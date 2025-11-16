# Redis `dev` environment overlay

This overlay provides an example Redis deployment optimized for development and
testing environments, featuring an include-based configuration system that
eliminates copy/paste duplication.

## Overview

The Redis deployment includes:

- **Redis cluster**: 3 replicas (1 master + 2 replicas) for basic HA testing
- **Sentinel**: 1 replica for monitoring (no quorum, cannot perform failover)
- **Configuration**: Include-based system for clean customization
- **Authentication**:
  - Redis: Password required (stored in `redis-secret-dev`)
  - Sentinel: Password-protected (stored in `sentinel-secret-dev`)
- **Resources**: Minimal CPU/memory limits for development
- **Storage**: 1Gi PVC per Redis pod

## Architecture: `include`-based configuration

This overlay demonstrates a modern Kustomize pattern that avoids configuration
duplication:

### How it works

1. **Base configuration** (`components/redis/`):
   - Contains complete Redis/Sentinel configurations
   - Includes custom config files via `include` directives
   - Provides shared settings across all environments

2. **Custom config files**:
   - `redis-custom.conf`: Environment-specific Redis settings (maxmemory, etc.)
   - `sentinel-custom.conf`: Environment-specific Sentinel settings (monitor
     targets, quorum)

3. **Overlay patches**:
   - Only patch the custom config files
   - No duplication of base configuration
   - Minimal, targeted changes per environment

### Benefits

- **DRY principle**: No copy/paste of entire config files
- **Maintainability**: Base config updates automatically propagate
- **Clarity**: Environment-specific changes are isolated
- **Scalability**: Easy to create new overlays with minimal code

## Quick Start

```bash
# Deploy to default namespace
kubectl apply --kustomize examples/redis/overlays/dev/

# Deploy to specific namespace
kubectl apply --kustomize examples/redis/overlays/dev/ --namespace my-dev

# Cleanup
kubectl delete --kustomize examples/redis/overlays/dev/ --namespace my-dev
```

## Components

### Redis StatefulSet (`dev-redis`)

- **Replicas**: 3 (configurable via patches)
- **Master pod**: `dev-redis-0`
- **Replica pods**: `dev-redis-1`, `dev-redis-2`
- **Resources**: 50m CPU request, 200m limit; 128Mi memory request, 256Mi limit
- **Configuration**: Base config + dev customizations (128MB maxmemory)

### Sentinel StatefulSet (`dev-redis-sentinel`)

- **Replicas**: 1 (configurable via patches)
- **Password authentication**: Required for all operations
- **Resources**: 25m CPU request, 100m limit; 64Mi memory request, 128Mi limit
- **Configuration**: Monitors `dev-redis-0.dev-redis-headless` with quorum=1

### Services

- `dev-redis-headless`: Headless service for pod-to-pod communication
- `dev-redis`: ClusterIP service targeting the master pod (`dev-redis-0`)
- `dev-redis-sentinel-headless`: Headless service for Sentinel peer discovery
- `dev-redis-sentinel`: ClusterIP service for Sentinel access

### ConfigMaps

- `dev-redis-config`: Base Redis configuration (inherited from components)
- `dev-redis-custom-config`: Dev-specific Redis settings (maxmemory: 128MB)
- `dev-redis-sentinel-config`: Base Sentinel configuration
- `dev-sentinel-custom-config`: Dev-specific Sentinel settings (monitor target,
  quorum: 1)

### Secrets

- `dev-redis-sentinel-secret`: Contains Sentinel password and password.conf file

## Configuration customization

### Understanding the patch files

The overlay uses strategic patches to customize the base configuration:

#### `redis-custom-patch.yaml`

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: redis-custom-config
data:
  redis-custom.conf: |
    # Custom Redis configuration for environment-specific overrides
    maxmemory 128mb
```

#### `sentinel-custom-patch.yaml`

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: sentinel-custom-config
data:
  sentinel-custom.conf: |
    # Custom Sentinel configuration for environment-specific overrides
    sentinel monitor mymaster dev-redis-0.dev-redis-headless 6379 1
```

### Creating your own overlay

To create a new environment overlay (e.g., `staging`):

1. **Create directory structure**:

   ```bash
   mkdir --parents overlays/staging
   ```

2. **Create kustomization.yaml**:

   ```yaml
   apiVersion: kustomize.config.k8s.io/v1beta1
   kind: Kustomization
   nameSuffix: -staging

   labels:
     - includeSelectors: true
       pairs:
         app.kubernetes.io/instance: staging

   resources:
     - github.com/stn-dts/kustomize-manifests/components/redis?ref=redis-7.4.7.000

   patches:
     - path: redis-custom-patch.yaml
       target:
         kind: ConfigMap
         name: redis-custom-config
     - path: sentinel-custom-patch.yaml
       target:
         kind: ConfigMap
         name: sentinel-custom-config
     - path: statefulset-patch.yaml
       target:
         kind: StatefulSet
         name: redis
     - path: sentinel-patch.yaml
       target:
         kind: StatefulSet
         name: redis-sentinel
   ```

3. **Create custom config patches** with your environment settings.

## Customizing settings

### Changing replica counts

Edit `statefulset-patch.yaml`:

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis
spec:
  replicas: 5  # Change from default 3
```

Edit `sentinel-patch.yaml`:

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis-sentinel
spec:
  replicas: 3  # For HA, use odd numbers (1, 3, 5)
```

### Modifying redis configuration

Edit `redis-custom-patch.yaml`:

```yaml
data:
  redis-custom.conf: |
    # Your custom Redis settings
    maxmemory 512mb
    maxmemory-policy volatile-lru
    # Add any Redis directives you want to override
```

### Modifying sentinel configuration

Edit `sentinel-custom-patch.yaml`:

```yaml
data:
  sentinel-custom.conf: |
    # Your custom Sentinel settings
    sentinel monitor mymaster redis-staging-0.redis-staging-headless 6379 2
    sentinel down-after-milliseconds mymaster 3000
    # Add any Sentinel directives
```

### Setting custom passwords

The Sentinel password is managed via `redis-sentinel-secret-patch.yaml`:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: redis-sentinel-secret
stringData:
  password: <your-password>
  password.conf: |
    requirepass <your-password>
```

### Adjusting resources

Edit `statefulset-patch.yaml`:

```yaml
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
volumeClaimTemplates:
  - metadata:
    name: data
  spec:
    resources:
      requests:
        storage: 10Gi  # Change from default 1Gi
```

## Testing your deployment

### Redis connectivity (password required)

```bash
# Get the password
PASSWORD=$(kubectl get secret redis-secret-dev --namespace <namespace> -o jsonpath='{.data.redis-password}' | base64 -d)

kubectl exec dev-redis-0 --namespace <namespace> -- redis-cli -a $PASSWORD ping
# Should return: PONG
```

### Sentinel connectivity (password required)

```bash
# Get the password
PASSWORD=<your-password>

# Test Sentinel
kubectl exec dev-redis-sentinel-0 --namespace <namespace> -- redis-cli -p 26379 -a $PASSWORD ping
# Should return: PONG
```

### Verify configuration

```bash
# Check Redis maxmemory
kubectl exec dev-redis-0 --namespace <namespace> -- redis-cli config get maxmemory

# Check Sentinel masters
kubectl exec dev-redis-sentinel-0 --namespace <namespace> -- redis-cli -p 26379 -a $PASSWORD sentinel masters
```

### Test replication

```bash
# Write to master
kubectl exec dev-redis-0 --namespace <namespace> -- redis-cli set test-key "Hello World"

# Read from replica
kubectl exec dev-redis-1 --namespace <namespace> -- redis-cli get test-key
# Should return: Hello World
```

## Troubleshooting

### Common issues

1. **Pods not starting**: Check ConfigMap content for syntax errors
2. **Replication not working**: Verify master host in replica logs
3. **Sentinel not monitoring**: Check monitor directive in custom config
4. **Authentication failures**: Ensure password is set correctly

### Debugging commands

```bash
# Check pod logs
kubectl logs dev-redis-0 --namespace <namespace>

# Check ConfigMap content
kubectl get configmap dev-redis-custom-config --namespace <namespace> --output yaml

# Check StatefulSet status
kubectl describe statefulset dev-redis --namespace <namespace>
```

## Differences from Production

- **Sentinel count**: 1 (vs 3+ for HA failover)
- **Redis authentication**: Enabled (vs disabled in some dev setups)
- **Resource limits**: Reduced (vs production sizing)
- **Storage**: 1Gi (vs larger production volumes)
- **Quorum**: 1 (vs 2+ for production HA)

## Advanced usage

### Environment variables

The deployment supports environment-specific configuration via patches. For
complex setups, consider:

- Using Kustomize vars for dynamic values
- Creating base overlays for shared environment types
- Implementing CI/CD pipelines that generate custom patches

### Monitoring integration

For production-like monitoring in dev:
- Add Prometheus annotations
- Include metrics exporters
- Configure log aggregation

### Backup/restore

The PVC-based storage allows for:
- Manual snapshots via `kubectl exec` and `redis-cli SAVE`
- Automated backup jobs
- Data persistence across pod restarts

## Contributing

When modifying this overlay:
1. Test changes in a dev namespace first
2. Update this README with any new customization options
3. Ensure patches remain minimal and targeted
4. Validate that base config changes don't break overlays
