"""Live container integration tests for components/redis.

Spins up temporary podman (or docker) containers using the exact image tag,
ConfigMaps, init scripts, and probe commands rendered by `kubectl kustomize`.

Run:
    python3 -m unittest tests/redis/test_integration.py -v

Optional environment variables:
    CONTAINER_RUNTIME   "podman" or "docker" (auto-detected if unset)
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from typing import Any

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BASE_DIR = REPO_ROOT / "components" / "redis"

# Special-character passwords exercise shell/printf escaping and SHA-256 ACL hashing.
REDIS_PASS = 'a"b\\c #d'
SENTINEL_PASS = 's"p\\2 #b'
WORKLOAD_USER = "indexer"
WORKLOAD_PASS = "idx-secret-123"


def detect_runtime() -> str:
    explicit = os.environ.get("CONTAINER_RUNTIME")

    if explicit:
        return explicit

    for candidate in ("podman", "docker"):
        if shutil.which(candidate):
            return candidate

    raise RuntimeError("Neither 'podman' nor 'docker' found in PATH")


def kustomize_build(path: pathlib.Path) -> list[dict[str, Any]]:
    proc = subprocess.run(
        ["kubectl", "kustomize", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )

    return [doc for doc in yaml.safe_load_all(proc.stdout) if doc]


def find_doc(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    for doc in docs:
        if doc.get("kind") == kind and doc.get("metadata", {}).get("name") == name:
            return doc

    raise RuntimeError(f"Resource {kind}/{name} not found")


def run_cmd(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)

    if check and proc.returncode != 0:
        raise AssertionError(
            f"Command failed (rc={proc.returncode}): {' '.join(cmd)}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )

    return proc


def vol_opt(src: str, dst: str, *, ro: bool = False, runtime: str = "podman") -> str:
    opts = []

    if ro:
        opts.append("ro")

    if runtime == "podman":
        opts.append("Z")

    return f"{src}:{dst}:{','.join(opts)}" if opts else f"{src}:{dst}"


class RedisClusterHarness:
    """Manages a temporary multi-container Redis + Sentinel cluster."""

    def __init__(self, runtime: str, docs: list[dict[str, Any]], *, sentinel_pass: str = "") -> None:
        self.runtime = runtime
        self.sentinel_pass = sentinel_pass
        self.run_id = uuid.uuid4().hex[:8]
        self.network = f"redis-kustomize-test-{self.run_id}"
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(prefix=f"redis-test-{self.run_id}-"))
        self.containers: list[str] = []

        redis_sts = find_doc(docs, "StatefulSet", "redis")
        sent_sts = find_doc(docs, "StatefulSet", "redis-sentinel")

        redis_pod = redis_sts["spec"]["template"]["spec"]
        sent_pod = sent_sts["spec"]["template"]["spec"]

        self.image: str = redis_pod["containers"][0]["image"]
        self.redis_uid = f"{redis_pod['securityContext']['runAsUser']}:{redis_pod['securityContext']['runAsGroup']}"
        self.sent_uid = f"{sent_pod['securityContext']['runAsUser']}:{sent_pod['securityContext']['runAsGroup']}"

        self.init_redis_script: str = redis_pod["initContainers"][0]["command"][2]
        self.init_sent_script: str = sent_pod["initContainers"][0]["command"][2]

        self.redis_startup_cmd: str = redis_pod["containers"][0]["startupProbe"]["exec"]["command"][2]
        self.redis_ready_cmd: str = redis_pod["containers"][0]["readinessProbe"]["exec"]["command"][2]
        self.redis_live_cmd: str = redis_pod["containers"][0]["livenessProbe"]["exec"]["command"][2]
        self.sent_ready_cmd: str = sent_pod["containers"][0]["readinessProbe"]["exec"]["command"][2]

        self.cfg_src = BASE_DIR / "config"
        self.acl_dir = self.tmpdir / "acl"
        self.acl_dir.mkdir()

        acl_file = self.acl_dir / "users-dev.conf"
        acl_file.write_text(
            "# Comment line to verify init-redis strips comments from aclfile\n\n"
            f"user {WORKLOAD_USER} on >{WORKLOAD_PASS} ~idx:* +@read +ping\n"
        )

        os.chmod(acl_file, 0o644)

        run_cmd([self.runtime, "network", "create", self.network])

    def cleanup(self) -> None:
        for cname in reversed(self.containers):
            subprocess.run([self.runtime, "kill", cname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run([self.runtime, "rm", "--force", cname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        subprocess.run([self.runtime, "network", "rm", "--force", self.network], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def force_rm(self, cname: str) -> None:
        """Immediately kill and remove a container without waiting for the default stop timeout."""
        subprocess.run([self.runtime, "kill", cname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        run_cmd([self.runtime, "rm", "--force", cname])

    def _ensure_pod_dirs(self, kind: str, idx: int, *, wipe_config: bool = False) -> pathlib.Path:
        pod_dir = self.tmpdir / f"{kind}-{idx}"
        cfg_dir = pod_dir / "config"

        if wipe_config and cfg_dir.exists():
            shutil.rmtree(cfg_dir)

        cfg_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(cfg_dir, 0o777)

        if kind == "redis":
            data_dir = pod_dir / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(data_dir, 0o777)

        return pod_dir

    def _make_hosts(self, short_name: str, fqdn: str, ip: str) -> pathlib.Path:
        hosts_path = self.tmpdir / f"hosts-{short_name}-{uuid.uuid4().hex[:6]}"
        hosts_path.write_text(f"127.0.0.1 localhost\n{ip} {fqdn} {short_name}\n")
        os.chmod(hosts_path, 0o644)

        return hosts_path

    def _hosts_flag(self, hosts_path: pathlib.Path) -> list[str]:
        if self.runtime == "podman":
            return ["--hosts-file", str(hosts_path)]

        return ["--volume", vol_opt(str(hosts_path), "/etc/hosts", ro=True, runtime=self.runtime)]

    def redis_cname(self, idx: int) -> str:
        return f"rt-{self.run_id}-redis-{idx}"

    def sent_cname(self, idx: int) -> str:
        return f"rt-{self.run_id}-sentinel-{idx}"

    def run_init_redis(self, idx: int, *, pod_ip: str = "10.89.0.10", sentinel_host: str = "redis-sentinel", wipe_config: bool = False) -> pathlib.Path:
        pod_dir = self._ensure_pod_dirs("redis", idx, wipe_config=wipe_config)
        fqdn = f"redis-{idx}.redis-headless.default.svc.cluster.local"
        hosts = self._make_hosts(f"redis-{idx}", fqdn, pod_ip)

        run_cmd([
            self.runtime, "run", "--rm", "--network", self.network,
            "--hostname", f"redis-{idx}",
            *self._hosts_flag(hosts),
            "--read-only", "--user", self.redis_uid, "--entrypoint", "",
            "--env", f"POD_NAME=redis-{idx}",
            "--env", "POD_NAMESPACE=default",
            "--env", f"POD_IP={pod_ip}",
            "--env", f"REDIS_PASSWORD={REDIS_PASS}",
            "--env", f"SENTINEL_HOST={sentinel_host}",
            "--env", f"SENTINEL_PASSWORD={self.sentinel_pass}",
            "--env", "REDIS_HEADLESS_SERVICE=redis-headless",
            "--env", "REDIS_ACL_DIR=/etc/redis-acl",
            "--env", "REDIS_PEER_SCAN_COUNT=3",
            "--volume", vol_opt(str(pod_dir / "config"), "/etc/redis", runtime=self.runtime),
            "--volume", vol_opt(str(self.cfg_src / "redis.conf"), "/mnt/redis.conf", ro=True, runtime=self.runtime),
            "--volume", vol_opt(str(self.cfg_src / "redis-custom.conf"), "/mnt/redis-custom.conf", ro=True, runtime=self.runtime),
            "--volume", vol_opt(str(self.acl_dir), "/etc/redis-acl", ro=True, runtime=self.runtime),
            self.image, "bash", "-c", self.init_redis_script,
        ])

        return pod_dir / "config" / "redis.conf"

    def start_redis(self, idx: int) -> str:
        pod_dir = self._ensure_pod_dirs("redis", idx)
        cname = self.redis_cname(idx)

        if cname not in self.containers:
            self.containers.append(cname)

        fqdn = f"redis-{idx}.redis-headless.default.svc.cluster.local"
        short = f"redis-{idx}.redis-headless"

        run_cmd([
            self.runtime, "run", "--detach", "--name", cname, "--network", self.network,
            "--hostname", f"redis-{idx}",
            "--network-alias", fqdn,
            "--network-alias", short,
            "--read-only", "--user", self.redis_uid, "--entrypoint", "",
            "--env", f"REDIS_PASSWORD={REDIS_PASS}",
            "--volume", vol_opt(str(pod_dir / "config"), "/etc/redis", runtime=self.runtime),
            "--volume", vol_opt(str(pod_dir / "data"), "/data", runtime=self.runtime),
            self.image, "redis-server", "/etc/redis/redis.conf",
        ])

        return cname

    def run_init_sentinel(self, idx: int, *, pod_ip: str = "10.89.0.20", wipe_config: bool = False) -> pathlib.Path:
        pod_dir = self._ensure_pod_dirs("sentinel", idx, wipe_config=wipe_config)
        fqdn = f"redis-sentinel-{idx}.redis-sentinel-headless.default.svc.cluster.local"
        hosts = self._make_hosts(f"redis-sentinel-{idx}", fqdn, pod_ip)

        run_cmd([
            self.runtime, "run", "--rm", "--network", self.network,
            "--hostname", f"redis-sentinel-{idx}",
            *self._hosts_flag(hosts),
            "--read-only", "--user", self.sent_uid, "--entrypoint", "",
            "--env", f"POD_NAME=redis-sentinel-{idx}",
            "--env", "POD_NAMESPACE=default",
            "--env", f"POD_IP={pod_ip}",
            "--env", f"REDIS_PASSWORD={REDIS_PASS}",
            "--env", f"SENTINEL_PASSWORD={self.sentinel_pass}",
            "--env", "SENTINEL_HEADLESS_SERVICE=redis-sentinel-headless",
            "--volume", vol_opt(str(pod_dir / "config"), "/etc/redis", runtime=self.runtime),
            "--volume", vol_opt(str(self.cfg_src / "sentinel.conf"), "/mnt/sentinel.conf", ro=True, runtime=self.runtime),
            "--volume", vol_opt(str(self.cfg_src / "sentinel-custom.conf"), "/mnt/sentinel-custom.conf", ro=True, runtime=self.runtime),
            self.image, "bash", "-c", self.init_sent_script,
        ])

        return pod_dir / "config" / "sentinel.conf"

    def start_sentinel(self, idx: int) -> str:
        pod_dir = self._ensure_pod_dirs("sentinel", idx)
        cname = self.sent_cname(idx)

        if cname not in self.containers:
            self.containers.append(cname)

        fqdn = f"redis-sentinel-{idx}.redis-sentinel-headless.default.svc.cluster.local"
        short = f"redis-sentinel-{idx}.redis-sentinel-headless"

        run_cmd([
            self.runtime, "run", "--detach", "--name", cname, "--network", self.network,
            "--hostname", f"redis-sentinel-{idx}",
            "--network-alias", fqdn,
            "--network-alias", short,
            "--network-alias", "redis-sentinel",
            "--read-only", "--user", self.sent_uid, "--entrypoint", "",
            "--env", f"SENTINEL_PASSWORD={self.sentinel_pass}",
            "--volume", vol_opt(str(pod_dir / "config"), "/etc/redis", runtime=self.runtime),
            self.image, "redis-sentinel", "/etc/redis/sentinel.conf",
        ])

        return cname

    def exec_redis_cli(self, idx: int, *args: str, auth: str | None = REDIS_PASS, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = [self.runtime, "exec"]

        if auth is not None:
            cmd += ["--env", f"REDISCLI_AUTH={auth}"]

        cmd += [self.redis_cname(idx), "redis-cli", "--no-auth-warning", *args]

        return run_cmd(cmd, check=check)

    def exec_sentinel_cli(self, idx: int, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = [self.runtime, "exec"]

        if self.sentinel_pass:
            cmd += ["--env", f"REDISCLI_AUTH={self.sentinel_pass}"]

        cmd += [self.sent_cname(idx), "redis-cli", "-p", "26379", "--no-auth-warning", *args]

        return run_cmd(cmd, check=check)

    def wait_until(self, predicate, *, timeout: float = 30.0, interval: float = 0.5, desc: str = "condition") -> Any:
        deadline = time.monotonic() + timeout
        last_err: Exception | None = None

        while time.monotonic() < deadline:
            try:
                result = predicate()

                if result:
                    return result
            except Exception as exc:  # noqa: BLE001
                last_err = exc

            time.sleep(interval)

        raise AssertionError(f"Timed out waiting for {desc}" + (f" (last error: {last_err})" if last_err else ""))


class TestRedisHAIntegration(unittest.TestCase):
    runtime: str
    docs: list[dict[str, Any]]
    cluster: RedisClusterHarness

    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime = detect_runtime()
        cls.docs = kustomize_build(BASE_DIR)
        cls.cluster = RedisClusterHarness(cls.runtime, cls.docs)

        try:
            # Bootstrap redis-0 first, then redis-1 & redis-2 (also exercises peer-probe bootstrap)
            cls.cluster.run_init_redis(0, pod_ip="10.89.10.10")
            cls.cluster.start_redis(0)
            cls.cluster.wait_until(
                lambda: cls.cluster.exec_redis_cli(0, "ping", check=False).stdout.strip() == "PONG",
                desc="redis-0 ready",
            )

            for i in (1, 2):
                cls.cluster.run_init_redis(i, pod_ip=f"10.89.10.{10 + i}")
                cls.cluster.start_redis(i)

            for i in (1, 2):
                cls.cluster.wait_until(
                    lambda idx=i: "master_link_status:up" in cls.cluster.exec_redis_cli(idx, "info", "replication", check=False).stdout,
                    desc=f"redis-{i} replicating from redis-0",
                )

            # Bootstrap 3 Sentinels
            for i in range(3):
                cls.cluster.run_init_sentinel(i, pod_ip=f"10.89.10.{20 + i}")
                cls.cluster.start_sentinel(i)

            cls.cluster.wait_until(
                lambda: cls.cluster.exec_sentinel_cli(0, "sentinel", "ckquorum", "mymaster", check=False).stdout.strip().startswith("OK 3 usable Sentinels"),
                desc="Sentinel 3-node quorum",
            )
        except Exception:
            cls.cluster.cleanup()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls.cluster.cleanup()

    def test_01_basic_read_write_replication_and_workload_acl(self) -> None:
        self.assertEqual(self.cluster.exec_redis_cli(0, "set", "idx:hello", "world").stdout.strip(), "OK")

        for replica in (1, 2):
            val = self.cluster.wait_until(
                lambda idx=replica: self.cluster.exec_redis_cli(idx, "get", "idx:hello", check=False).stdout.strip() == "world",
                desc=f"replication to redis-{replica}",
            )

            self.assertTrue(val)

        # Workload ACL user can read idx:* and PING, but cannot write
        acl_get = run_cmd([
            self.runtime, "exec", self.cluster.redis_cname(0),
            "redis-cli", "--user", WORKLOAD_USER, "--pass", WORKLOAD_PASS, "--no-auth-warning", "get", "idx:hello",
        ])

        self.assertEqual(acl_get.stdout.strip(), "world")

        acl_set = run_cmd([
            self.runtime, "exec", self.cluster.redis_cname(0),
            "redis-cli", "--user", WORKLOAD_USER, "--pass", WORKLOAD_PASS, "--no-auth-warning", "set", "idx:denied", "1",
        ])

        self.assertIn("NOPERM", acl_set.stdout + acl_set.stderr)

    def test_02_probes_validate_pong_and_reject_bad_auth(self) -> None:
        run_cmd([self.runtime, "exec", self.cluster.redis_cname(0), "sh", "-c", self.cluster.redis_startup_cmd])
        run_cmd([self.runtime, "exec", self.cluster.redis_cname(0), "sh", "-c", self.cluster.redis_ready_cmd])
        run_cmd([self.runtime, "exec", self.cluster.redis_cname(0), "sh", "-c", self.cluster.redis_live_cmd])
        run_cmd([self.runtime, "exec", self.cluster.sent_cname(0), "sh", "-c", self.cluster.sent_ready_cmd])

        bad_pass = run_cmd(
            [self.runtime, "exec", "--env", "REDIS_PASSWORD=wrong", self.cluster.redis_cname(0), "sh", "-c", self.cluster.redis_ready_cmd],
            check=False,
        )

        self.assertNotEqual(bad_pass.returncode, 0, "readinessProbe must fail on WRONGPASS")

        no_pass = run_cmd(
            [self.runtime, "exec", "--env", "REDIS_PASSWORD=", self.cluster.redis_cname(0), "sh", "-c", self.cluster.redis_ready_cmd],
            check=False,
        )

        self.assertNotEqual(no_pass.returncode, 0, "readinessProbe must fail on NOAUTH")

    def test_03_failover_masterauth_restarts_and_pod_recreation(self) -> None:
        # 1. Trigger failover away from redis-0
        self.cluster.exec_sentinel_cli(0, "sentinel", "failover", "mymaster")

        def get_promoted_master() -> str | None:
            lines = self.cluster.exec_sentinel_cli(
                0, "--raw", "sentinel", "get-master-addr-by-name", "mymaster", check=False
            ).stdout.strip().splitlines()

            if lines and not lines[0].startswith("redis-0."):
                return lines[0]

            return None

        new_master_host = self.cluster.wait_until(get_promoted_master, timeout=30.0, desc="failover promotion")
        new_idx = int(new_master_host.split(".")[0].split("-")[1])

        # 2. Verify demoted redis-0 replicates from the new primary (verifies unconditional masterauth)
        self.cluster.wait_until(
            lambda: (
                "role:slave" in (info := self.cluster.exec_redis_cli(0, "info", "replication", check=False).stdout)
                and "master_link_status:up" in info
            ),
            timeout=25.0,
            desc="demoted redis-0 replicating with master_link_status:up",
        )

        # Write through promoted primary and verify it replicates back to demoted redis-0
        self.assertEqual(self.cluster.exec_redis_cli(new_idx, "set", "idx:after-failover", "yes").stdout.strip(), "OK")

        self.cluster.wait_until(
            lambda: self.cluster.exec_redis_cli(0, "get", "idx:after-failover", check=False).stdout.strip() == "yes",
            desc="replication from promoted primary to demoted redis-0",
        )

        # 3. In-place container restarts (no initContainer re-run; preserves /etc/redis emptyDir)
        run_cmd([self.runtime, "restart", self.cluster.sent_cname(0)])
        run_cmd([self.runtime, "restart", self.cluster.redis_cname(new_idx)])

        self.cluster.wait_until(
            lambda: self.cluster.exec_sentinel_cli(0, "ping", check=False).stdout.strip() == "PONG",
            desc="sentinel-0 healthy after in-place restart (no Duplicate master name)",
        )

        self.cluster.wait_until(
            lambda: "role:master" in self.cluster.exec_redis_cli(new_idx, "info", "replication", check=False).stdout,
            desc=f"promoted redis-{new_idx} stays role:master after in-place restart",
        )

        acl_ping = run_cmd([
            self.runtime, "exec", self.cluster.redis_cname(new_idx),
            "redis-cli", "--user", WORKLOAD_USER, "--pass", WORKLOAD_PASS, "--no-auth-warning", "ping",
        ])

        self.assertEqual(acl_ping.stdout.strip(), "PONG", "Workload ACL user must work after CONFIG REWRITE + restart")

        # 4. Pod recreation of promoted non-0 primary (fresh /etc/redis, new IP -> FQDN self-check)
        # Use force_rm (instant kill + rm) so Sentinel still reports new_idx as primary during init-redis.
        self.cluster.force_rm(self.cluster.redis_cname(new_idx))
        conf_path = self.cluster.run_init_redis(new_idx, pod_ip="10.89.10.99", wipe_config=True)
        self.assertNotIn("replicaof ", conf_path.read_text(), "Recreated promoted primary must not get replicaof")
        self.cluster.start_redis(new_idx)

        self.cluster.wait_until(
            lambda: self.cluster.exec_redis_cli(new_idx, "ping", check=False).stdout.strip() == "PONG",
            desc=f"recreated redis-{new_idx} ready",
        )

        # Determine whichever non-0 pod Sentinel currently considers primary.
        def get_current_non_zero_master() -> int | None:
            lines = self.cluster.exec_sentinel_cli(
                0, "--raw", "sentinel", "get-master-addr-by-name", "mymaster", check=False
            ).stdout.strip().splitlines()

            if lines and not lines[0].startswith("redis-0."):
                return int(lines[0].split(".")[0].split("-")[1])

            return None

        active_idx = self.cluster.wait_until(get_current_non_zero_master, timeout=15.0, desc="active non-0 primary")

        # 5. Pod recreation of demoted redis-0 with unreachable Sentinel -> peer probe must prevent split-brain
        self.cluster.force_rm(self.cluster.redis_cname(0))

        r0_conf = self.cluster.run_init_redis(
            0, pod_ip="10.89.10.97", sentinel_host="unreachable-sentinel", wipe_config=True
        )

        expected_replicaof = f'replicaof "redis-{active_idx}.redis-headless.default.svc.cluster.local" 6379'

        self.assertIn(expected_replicaof, r0_conf.read_text(), "redis-0 peer probe must find active primary")
        self.cluster.start_redis(0)
        self.cluster.wait_until(
            lambda: "master_link_status:up" in self.cluster.exec_redis_cli(0, "info", "replication", check=False).stdout,
            desc="recreated redis-0 replicating via peer-probe discovery",
        )

        # 6. Sentinel pod recreation with new IP -> num-other-sentinels must remain 2 (no stale voters)
        self.cluster.force_rm(self.cluster.sent_cname(2))
        self.cluster.run_init_sentinel(2, pod_ip="10.89.10.98", wipe_config=True)
        self.cluster.start_sentinel(2)

        def other_sentinels_is_two() -> bool:
            lines = self.cluster.exec_sentinel_cli(0, "--raw", "sentinel", "master", "mymaster", check=False).stdout.splitlines()
            info = dict(zip(lines[0::2], lines[1::2]))
            return info.get("num-other-sentinels") == "2"

        self.cluster.wait_until(other_sentinels_is_two, timeout=20.0, desc="stable num-other-sentinels == 2")


class TestRedisSentinelAuthIntegration(unittest.TestCase):
    def test_optional_sentinel_password_auth(self) -> None:
        runtime = detect_runtime()
        docs = kustomize_build(BASE_DIR)
        cluster = RedisClusterHarness(runtime, docs, sentinel_pass=SENTINEL_PASS)

        try:
            cluster.run_init_redis(0, pod_ip="10.89.11.10")
            cluster.start_redis(0)
            cluster.wait_until(
                lambda: cluster.exec_redis_cli(0, "ping", check=False).stdout.strip() == "PONG",
                desc="redis-0 ready",
            )

            cluster.run_init_sentinel(0, pod_ip="10.89.11.20")
            cluster.start_sentinel(0)
            cluster.wait_until(
                lambda: cluster.exec_sentinel_cli(0, "ping", check=False).stdout.strip() == "PONG",
                desc="authenticated sentinel-0 ready",
            )

            # Sentinel readinessProbe passes with SENTINEL_PASSWORD and fails without it
            run_cmd([runtime, "exec", cluster.sent_cname(0), "sh", "-c", cluster.sent_ready_cmd])
            unauth = run_cmd(
                [runtime, "exec", "--env", "SENTINEL_PASSWORD=", cluster.sent_cname(0), "sh", "-c", cluster.sent_ready_cmd],
                check=False,
            )

            self.assertNotEqual(unauth.returncode, 0, "Sentinel readinessProbe must fail when auth is enabled and password omitted")

            # init-redis authenticates to Sentinel and discovers redis-0
            r1_conf = cluster.run_init_redis(1, pod_ip="10.89.11.11")
            self.assertIn("replicaof ", r1_conf.read_text())
        finally:
            cluster.cleanup()


if __name__ == "__main__":
    unittest.main()
