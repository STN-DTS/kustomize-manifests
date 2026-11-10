# Redis `dev` environment overlay

This overlay provides a Redis primary/replica deployment with Sentinel for
development and testing. It uses include-based configuration so overlays can
change environment-specific settings without copying the base configuration.
This is not Redis Cluster mode.

## Overview

- **Redis**: 3 pods (one initial primary and two replicas).
- **Sentinel**: 1 replica with quorum 1. It can authorize failover, but there is
  no Sentinel redundancy.
- **Authentication**: Redis requires a password from `redis-secret-dev`, key
  `password`. Sentinel uses the same password to authenticate to Redis, but
  Sentinel clients do not need a password.
- **Resources**: Minimal CPU and memory requests/limits for development.
- **Storage**: 1Gi PVC per Redis pod.

The overlay includes a committed example password for disposable testing only.
Replace it with a secret-management solution before using this pattern outside
an isolated development environment.

## Quick start

Run these commands from the repository root:

```bash
# Deploy to the default namespace
kubectl apply --kustomize examples/redis/overlays/dev/

# Create a namespace before deploying to it
kubectl create namespace my-dev
kubectl apply --kustomize examples/redis/overlays/dev/ --namespace my-dev

# Cleanup
kubectl delete --kustomize examples/redis/overlays/dev/ --namespace my-dev
```

Without `--namespace`, Kubernetes uses the current default namespace. The
development password is not suitable for production.

## Resources

### Redis StatefulSet: `redis-dev`

- **Replicas**: 3.
- **Initial primary**: `redis-dev-0`.
- **Initial replicas**: `redis-dev-1` and `redis-dev-2`.
- **Resources**: 50m CPU request, 200m limit; 128Mi memory request, 256Mi
  limit.
- **Redis maxmemory**: 128mb.

### Sentinel StatefulSet: `redis-sentinel-dev`

- **Replicas**: 1.
- **Monitor target**: `redis-dev-0.redis-headless-dev`, quorum 1.
- **Client authentication**: Not enabled.
- **Resources**: 25m CPU request, 100m limit; 64Mi memory request, 128Mi
  limit.

### Services

- `redis-headless-dev`: Headless service for Redis pod DNS.
- `redis-dev`: ClusterIP service targeting `redis-dev-0`. It does not follow a
  Sentinel promotion; use a Sentinel-aware client to discover the current
  primary.
- `redis-sentinel-headless-dev`: Headless service for Sentinel peer discovery.
- `redis-sentinel-dev`: ClusterIP service for unauthenticated Sentinel access.

### ConfigMaps and Secret

ConfigMaps are generated with a content hash in their names. Their names follow
these patterns and the hash changes when the content changes:

- `redis-config-dev-<hash>`
- `redis-custom-config-dev-<hash>`
- `sentinel-config-dev-<hash>`
- `sentinel-custom-config-dev-<hash>`

The `redis-secret-dev` Secret contains the Redis password under the `password`
key. There is no separate Sentinel Secret.

## Configuration

The base Redis and Sentinel config files include separate custom config files.
The overlay patches those custom ConfigMaps instead of copying the full base
configuration:

- `redis-custom-patch.yaml` changes environment-specific Redis settings.
- `sentinel-custom-patch.yaml` sets the monitor target and quorum.
- `statefulset-patch.yaml` sets development resources, storage, and Redis
  startup-discovery service names.
- `redis-service-patch.yaml` adjusts the static Service selector for the
  `-dev` name suffix.

The ConfigMap patch resource names use the unsuffixed base names; Kustomize
applies `-dev` to the rendered resources.

### Creating a `staging` overlay

From the repository root:

1. Create `examples/redis/overlays/staging/`.
2. Create `kustomization.yaml` with the current tagged base and required
   name-suffix patches:

   ```yaml
   apiVersion: kustomize.config.k8s.io/v1beta1
   kind: Kustomization
   nameSuffix: -staging

   labels:
     - includeSelectors: true
       pairs:
         app.kubernetes.io/instance: staging

   resources:
     - github.com/stn-dts/kustomize-manifests/components/redis?ref=redis-8.2.5.000

   patches:
     - path: redis-custom-patch.yaml
     - path: redis-secret-patch.yaml
     - path: sentinel-custom-patch.yaml
     - path: statefulset-patch.yaml
     - path: redis-service-patch.yaml
   ```

3. Create `redis-custom-patch.yaml` and `sentinel-custom-patch.yaml` from the
   examples below. For 3 Sentinels, use quorum 2. The base already configures 3
   Sentinel replicas.
4. Create `statefulset-patch.yaml` to use the suffixed discovery services:

   ```yaml
   apiVersion: apps/v1
   kind: StatefulSet
   metadata:
     name: redis
   spec:
     template:
       spec:
         initContainers:
           - name: init-redis
             env:
               - name: SENTINEL_HOST
                 value: redis-sentinel-staging
               - name: REDIS_HEADLESS_SERVICE
                 value: redis-headless-staging
   ```

5. Create `redis-service-patch.yaml` so the static Service selects the
   suffixed initial primary:

   ```yaml
   apiVersion: v1
   kind: Service
   metadata:
     name: redis
   spec:
     selector:
       statefulset.kubernetes.io/pod-name: redis-staging-0
   ```

6. Supply `redis-secret-patch.yaml` through your secret-management workflow.
   It must replace the base `redis-secret` password; do not commit plaintext
   credentials. The base contains an all-zero placeholder.
7. Render the overlay before applying it:

   ```bash
   kubectl kustomize examples/redis/overlays/staging/
   ```

The `redis-staging` ClusterIP Service still targets ordinal 0 and does not
follow Sentinel promotion. Use a Sentinel-aware client when clients must find
the current primary.

### Redis custom configuration

For example, `redis-custom-patch.yaml` can set a development memory limit:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: redis-custom-config
data:
  redis-custom.conf: |
    maxmemory 128mb
```

### Sentinel custom configuration

For the `dev` overlay, `sentinel-custom-patch.yaml` uses:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: sentinel-custom-config
data:
  sentinel-custom.conf: |
    sentinel monitor mymaster redis-dev-0.redis-headless-dev 6379 1
```

For `staging` with the base's 3 Sentinels, change the monitor address to
`redis-staging-0.redis-headless-staging` and use quorum 2.

### Changing replica counts

Edit `statefulset-patch.yaml` to change the Redis pod count:

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis
spec:
  replicas: 5
```

Edit `sentinel-patch.yaml` to change the Sentinel count. Keep the monitor quorum
in `sentinel-custom-patch.yaml` consistent with the number of Sentinels; for
example, use quorum 2 with 3 Sentinels. A single Sentinel with quorum 1 can
authorize failover, but provides no Sentinel redundancy.

### Adjusting resources

Edit `statefulset-patch.yaml`:

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis
spec:
  template:
    spec:
      containers:
        - name: redis
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
```

### Changing storage size

Edit `statefulset-patch.yaml`:

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis
spec:
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes:
          - ReadWriteOnce
        resources:
          requests:
            storage: 10Gi
```

The base requests 256Mi and this dev overlay requests 1Gi per pod. Changing the
claim template does not resize existing PVCs and may be rejected as an update to
an existing StatefulSet; follow your storage class's PVC expansion procedure
for existing data.

## Testing your deployment

Set `NAMESPACE` to the namespace where the overlay is deployed:

```bash
NAMESPACE=default
```

To retrieve the Redis password for a client:

```bash
kubectl get secret redis-secret-dev --namespace "$NAMESPACE" \
  -o jsonpath='{.data.password}' | base64 --decode
```

The Redis container already has the password in `REDIS_PASSWORD`. Use
`REDISCLI_AUTH` for commands inside the pod so the password is not included in
the `redis-cli` argument list:

```bash
kubectl exec --namespace "$NAMESPACE" redis-dev-0 -- \
  sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli ping'
# Should return: PONG
```

Sentinel client connections are not password-protected:

```bash
kubectl exec --namespace "$NAMESPACE" redis-sentinel-dev-0 -- \
  redis-cli -p 26379 ping
# Should return: PONG
```

Check Redis and Sentinel configuration:

```bash
kubectl exec --namespace "$NAMESPACE" redis-dev-0 -- \
  sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli config get maxmemory'

kubectl exec --namespace "$NAMESPACE" redis-sentinel-dev-0 -- \
  redis-cli -p 26379 sentinel masters
```

Test replication after the pods are ready:

```bash
kubectl exec --namespace "$NAMESPACE" redis-dev-0 -- \
  sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli set test-key "Hello World"'

kubectl exec --namespace "$NAMESPACE" redis-dev-1 -- \
  sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli get test-key'
# Should return: Hello World
```

## Troubleshooting

- **Pods not starting**: Check pod events and generated ConfigMap contents.
- **Replication not working**: Check Redis logs and verify the Sentinel-reported
  primary.
- **Sentinel not monitoring**: Check the monitor directive and Sentinel logs.
- **Authentication failures**: Confirm `redis-secret-dev` has the `password`
  key and that Redis commands use authentication.

```bash
kubectl logs --namespace "$NAMESPACE" redis-dev-0
kubectl logs --namespace "$NAMESPACE" redis-sentinel-dev-0

# ConfigMap names include a content hash; list them by the overlay label.
kubectl get configmaps --namespace "$NAMESPACE" \
  --selector app.kubernetes.io/instance=dev --output yaml

kubectl describe statefulset redis-dev --namespace "$NAMESPACE"
```

## Limitations and production use

- The single Sentinel has no redundancy. A quorum of 1 can authorize failover,
  but failover was not tested by this development example.
- The `redis-dev` ClusterIP Service targets the initial primary pod only. It
  will not follow a Sentinel promotion; use a Sentinel-aware client to discover
  the current primary.
- Replace the committed example password and development-sized resources
  before using this configuration outside disposable testing.
- Use Kustomize `replacements` rather than deprecated `vars` for dynamic values.
- `BGSAVE` requests a snapshot inside the Redis pod, but is not an off-cluster
  backup. Use a backup job or storage snapshot process for recoverable backups:

  ```bash
  kubectl exec --namespace "$NAMESPACE" redis-dev-0 -- \
    sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli BGSAVE'
  ```

## Contributing

When modifying this overlay:

1. Test changes in a development namespace first.
2. Update this README when customization options or generated names change.
3. Keep patches targeted and validate the rendered overlay with
   `kubectl kustomize examples/redis/overlays/dev/`.
