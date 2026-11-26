"""Static Kustomize manifest tests for components/redis and examples/redis/overlays/dev.

Run standalone (no container runtime required):
    python3 -m unittest tests/redis/test_manifests.py -v
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import unittest
from typing import Any

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BASE_DIR = REPO_ROOT / "components" / "redis"
DEV_OVERLAY_DIR = REPO_ROOT / "examples" / "redis" / "overlays" / "dev"


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
    raise AssertionError(f"Resource {kind}/{name} not found in rendered manifests")


def find_configmap_by_prefix(docs: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    for doc in docs:
        name = doc.get("metadata", {}).get("name", "")
        if doc.get("kind") == "ConfigMap" and name.startswith(prefix):
            return doc
    raise AssertionError(f"ConfigMap with prefix '{prefix}' not found")


def parse_memory_mi(value: str) -> float:
    """Parse Kubernetes (Mi/Gi) or Redis (mb/gb) memory strings into MiB."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([A-Za-z]+)", value.strip())
    if not match:
        raise ValueError(f"Unsupported memory value: {value!r}")
    amount = float(match.group(1))
    unit = match.group(2).lower()
    if unit in ("mi", "mb", "m"):
        return amount
    if unit in ("gi", "gb", "g"):
        return amount * 1024.0
    raise ValueError(f"Unsupported memory unit: {unit!r}")


def env_map(container: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["name"]: entry for entry in container.get("env", [])}


class TestRedisBaseManifests(unittest.TestCase):
    docs: list[dict[str, Any]]
    kustomization: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        cls.docs = kustomize_build(BASE_DIR)
        cls.kustomization = yaml.safe_load((BASE_DIR / "kustomization.yaml").read_text())

    def test_version_label_matches_container_images(self) -> None:
        version = str(self.kustomization["labels"][0]["pairs"]["app.kubernetes.io/version"])
        expected_tag = self.kustomization["images"][0]["newTag"]
        self.assertTrue(
            expected_tag.startswith(f"{version}-") or expected_tag == version,
            f"Image tag {expected_tag!r} should match app.kubernetes.io/version {version!r}",
        )
        expected_image = f"docker.io/library/redis:{expected_tag}"

        for sts_name in ("redis", "redis-sentinel"):
            sts = find_doc(self.docs, "StatefulSet", sts_name)
            pod_spec = sts["spec"]["template"]["spec"]
            for container in pod_spec.get("initContainers", []) + pod_spec.get("containers", []):
                self.assertEqual(
                    container["image"],
                    expected_image,
                    f"{sts_name}/{container['name']} has mismatched image",
                )

    def test_headless_services_publish_not_ready_addresses(self) -> None:
        for svc_name in ("redis-headless", "redis-sentinel-headless"):
            svc = find_doc(self.docs, "Service", svc_name)
            self.assertEqual(svc["spec"].get("clusterIP"), "None")
            self.assertTrue(
                svc["spec"].get("publishNotReadyAddresses"),
                f"Service/{svc_name} must set publishNotReadyAddresses: true",
            )

    def test_statefulsets_parallel_and_scoped_anti_affinity(self) -> None:
        for sts_name, role in (("redis", "server"), ("redis-sentinel", "sentinel")):
            sts = find_doc(self.docs, "StatefulSet", sts_name)
            self.assertEqual(sts["spec"].get("podManagementPolicy"), "Parallel")

            terms = sts["spec"]["template"]["spec"]["affinity"]["podAntiAffinity"][
                "preferredDuringSchedulingIgnoredDuringExecution"
            ]
            self.assertTrue(terms, f"{sts_name} missing podAntiAffinity")
            match_labels = terms[0]["podAffinityTerm"]["labelSelector"].get("matchLabels", {})
            self.assertEqual(match_labels.get("app.kubernetes.io/name"), "redis")
            self.assertEqual(match_labels.get("app.kubernetes.io/role"), role)

    def test_pod_disruption_budgets(self) -> None:
        for pdb_name, role in (("redis", "server"), ("redis-sentinel", "sentinel")):
            pdb = find_doc(self.docs, "PodDisruptionBudget", pdb_name)
            self.assertEqual(pdb["spec"].get("maxUnavailable"), 1)
            match_labels = pdb["spec"]["selector"]["matchLabels"]
            self.assertEqual(match_labels.get("app.kubernetes.io/name"), "redis")
            self.assertEqual(match_labels.get("app.kubernetes.io/role"), role)

    def test_pod_security_restricted_and_resources(self) -> None:
        expected_uids = {"redis": 35534, "redis-sentinel": 65534}
        for sts_name, uid in expected_uids.items():
            sts = find_doc(self.docs, "StatefulSet", sts_name)
            pod_spec = sts["spec"]["template"]["spec"]

            self.assertIs(pod_spec.get("automountServiceAccountToken"), False)

            pod_sc = pod_spec.get("securityContext", {})
            self.assertIs(pod_sc.get("runAsNonRoot"), True)
            self.assertEqual(pod_sc.get("runAsUser"), uid)
            self.assertEqual(pod_sc.get("runAsGroup"), uid)
            self.assertEqual(pod_sc.get("fsGroup"), uid)
            self.assertEqual(pod_sc.get("fsGroupChangePolicy"), "OnRootMismatch")
            self.assertEqual(pod_sc.get("seccompProfile", {}).get("type"), "RuntimeDefault")

            for container in pod_spec.get("initContainers", []) + pod_spec.get("containers", []):
                c_sc = container.get("securityContext", {})
                self.assertIs(c_sc.get("readOnlyRootFilesystem"), True, container["name"])
                self.assertIs(c_sc.get("allowPrivilegeEscalation"), False, container["name"])
                self.assertEqual(c_sc.get("capabilities", {}).get("drop"), ["ALL"], container["name"])

                resources = container.get("resources", {})
                self.assertIn("requests", resources, f"{container['name']} missing resource requests")
                self.assertIn("limits", resources, f"{container['name']} missing resource limits")

    def test_probes_and_prestop_hooks(self) -> None:
        for sts_name in ("redis", "redis-sentinel"):
            sts = find_doc(self.docs, "StatefulSet", sts_name)
            main = sts["spec"]["template"]["spec"]["containers"][0]

            for probe_name in ("startupProbe", "readinessProbe", "livenessProbe"):
                probe = main.get(probe_name)
                self.assertIsNotNone(probe, f"{sts_name} missing {probe_name}")
                cmd = " ".join(probe["exec"]["command"])
                self.assertIn("PONG", cmd, f"{sts_name} {probe_name} must validate PONG output")
                self.assertGreaterEqual(probe.get("timeoutSeconds", 1), 3, f"{sts_name} {probe_name} timeout too tight")

            prestop_cmd = " ".join(main["lifecycle"]["preStop"]["exec"]["command"])
            self.assertNotIn("save", prestop_cmd.lower(), f"{sts_name} preStop should not run blocking/unauth SAVE")

        redis_main = find_doc(self.docs, "StatefulSet", "redis")["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(redis_main["command"], ["redis-server", "/etc/redis/redis.conf"])

    def test_flat_configs_and_tuning(self) -> None:
        redis_cm = find_configmap_by_prefix(self.docs, "redis-config-")
        redis_conf = redis_cm["data"]["redis.conf"]
        self.assertNotRegex(redis_conf, r"(?m)^\s*include\s+", "redis.conf must not use include directives")
        self.assertRegex(redis_conf, r"(?m)^repl-diskless-sync\s+yes\b")
        self.assertRegex(redis_conf, r"(?m)^client-output-buffer-limit\s+replica\b")

        sentinel_cm = find_configmap_by_prefix(self.docs, "sentinel-config-")
        sentinel_conf = sentinel_cm["data"]["sentinel.conf"]
        self.assertNotRegex(sentinel_conf, r"(?m)^\s*include\s+", "sentinel.conf must not use include directives")
        self.assertRegex(sentinel_conf, r"(?m)^dir\s+/etc/redis\b")
        self.assertRegex(sentinel_conf, r"(?m)^sentinel\s+resolve-hostnames\s+yes\b")
        self.assertRegex(sentinel_conf, r"(?m)^sentinel\s+announce-hostnames\s+yes\b")
        failover_match = re.search(r"(?m)^sentinel\s+failover-timeout\s+mymaster\s+(\d+)", sentinel_conf)
        self.assertIsNotNone(failover_match)
        self.assertGreaterEqual(int(failover_match.group(1)), 30000)

    def test_storage_and_memory_headroom(self) -> None:
        redis_cm = find_configmap_by_prefix(self.docs, "redis-config-")
        mm_match = re.search(r"(?m)^maxmemory\s+(\S+)", redis_cm["data"]["redis.conf"])
        self.assertIsNotNone(mm_match)
        maxmemory_mi = parse_memory_mi(mm_match.group(1))

        redis_sts = find_doc(self.docs, "StatefulSet", "redis")
        pvc_storage = redis_sts["spec"]["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"]
        pvc_mi = parse_memory_mi(pvc_storage)
        self.assertGreaterEqual(pvc_mi, maxmemory_mi * 4, "Base PVC should be >= 4x maxmemory")

        mem_limit = redis_sts["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["memory"]
        self.assertGreaterEqual(parse_memory_mi(mem_limit), maxmemory_mi * 2, "Memory limit should be >= 2x maxmemory")

    def test_no_dead_config_and_optional_sentinel_password(self) -> None:
        redis_sts = find_doc(self.docs, "StatefulSet", "redis")
        redis_pod = redis_sts["spec"]["template"]["spec"]
        vol_names = {v["name"] for v in redis_pod["volumes"]}
        self.assertNotIn("redis-secret", vol_names, "Unused redis-secret volume should remain removed")

        init_redis = redis_pod["initContainers"][0]
        init_redis_env = env_map(init_redis)
        self.assertTrue(init_redis_env["SENTINEL_PASSWORD"]["valueFrom"]["secretKeyRef"]["optional"])
        self.assertIn("REDIS_ACL_DIR", init_redis_env)
        self.assertIn("REDIS_PEER_SCAN_COUNT", init_redis_env)

        sent_sts = find_doc(self.docs, "StatefulSet", "redis-sentinel")
        sent_pod = sent_sts["spec"]["template"]["spec"]
        init_sent = sent_pod["initContainers"][0]
        init_sent_env = env_map(init_sent)
        self.assertNotIn("REDIS_HEADLESS_SERVICE", init_sent_env)
        self.assertIn("SENTINEL_HEADLESS_SERVICE", init_sent_env)
        self.assertTrue(init_sent_env["SENTINEL_PASSWORD"]["valueFrom"]["secretKeyRef"]["optional"])

        sent_main_env = env_map(sent_pod["containers"][0])
        self.assertTrue(sent_main_env["SENTINEL_PASSWORD"]["valueFrom"]["secretKeyRef"]["optional"])


class TestRedisDevOverlay(unittest.TestCase):
    docs: list[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.docs = kustomize_build(DEV_OVERLAY_DIR)

    def test_dev_overlay_resources_and_suffixes(self) -> None:
        redis_sts = find_doc(self.docs, "StatefulSet", "redis-dev")
        sent_sts = find_doc(self.docs, "StatefulSet", "redis-sentinel-dev")
        find_doc(self.docs, "PodDisruptionBudget", "redis-dev")
        find_doc(self.docs, "PodDisruptionBudget", "redis-sentinel-dev")

        self.assertEqual(redis_sts["spec"]["replicas"], 3)
        self.assertEqual(sent_sts["spec"]["replicas"], 1)

        init_redis_env = env_map(redis_sts["spec"]["template"]["spec"]["initContainers"][0])
        self.assertEqual(init_redis_env["SENTINEL_HOST"]["value"], "redis-sentinel-dev")
        self.assertEqual(init_redis_env["REDIS_HEADLESS_SERVICE"]["value"], "redis-headless-dev")

        init_sent_env = env_map(sent_sts["spec"]["template"]["spec"]["initContainers"][0])
        self.assertEqual(init_sent_env["SENTINEL_HEADLESS_SERVICE"]["value"], "redis-sentinel-headless-dev")

        sent_custom = find_configmap_by_prefix(self.docs, "sentinel-custom-config-dev-")
        self.assertIn("sentinel monitor mymaster redis-dev-0.redis-headless-dev 6379 1", sent_custom["data"]["sentinel-custom.conf"])

    def test_dev_overlay_memory_headroom(self) -> None:
        redis_custom = find_configmap_by_prefix(self.docs, "redis-custom-config-dev-")
        mm_match = re.search(r"(?m)^maxmemory\s+(\S+)", redis_custom["data"]["redis-custom.conf"])
        self.assertIsNotNone(mm_match)
        maxmemory_mi = parse_memory_mi(mm_match.group(1))

        redis_sts = find_doc(self.docs, "StatefulSet", "redis-dev")
        mem_limit = redis_sts["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["memory"]
        self.assertGreaterEqual(parse_memory_mi(mem_limit), maxmemory_mi * 2)


if __name__ == "__main__":
    unittest.main()
