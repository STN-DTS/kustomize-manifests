# Kustomize Manifests

## Intro

This repository contains Kustomize configurations for deploying various
applications on Kubernetes. These manifests provide reusable, customizable
templates for common application deployments.

## Prerequisites

- [kubectl](https://kubernetes.io/docs/tasks/tools/) installed and configured
- [Kustomize](https://kustomize.io/) (built into kubectl as of v1.14, or install
  separately)

## Components

- **keel**: A simple, robust, background service that automatically updates
  Kubernetes workloads.
- **redis**: Redis cluster with Sentinel for high availability.
- **reloader**: Reloader controller that automatically triggers rolling updates
  on Deployments, StatefulSets, DaemonSets, and CronJobs when referenced
  ConfigMaps or Secrets are updated.

## Versioning

This repository uses Git tags to manage versions for the different components.
The tagging convention follows this format:

`<component-name>-<product-version>.<manifest-version>`

For example, the tag `redis-7.4.7.000` breaks down as follows:
- `redis`: The component or product name.
- `7.4.7`: The upstream version of the product being deployed.
- `000`: The internal version number for our manifests (starting at `000` and
  incrementing for any updates to the manifests for that specific product
  version).

Other examples include:
- `keel-0.21.1.000`
- `reloader-1.4.10.000`

## Releasing a New Version

To release a new version of a component or to publish an update to its manifests:

1. **Make changes:** Update the manifests within the specific component
   directory (e.g., `components/redis/`).
1. **Commit:** Commit and push your changes to the main branch.
1. **Tag the release:** Create a new git tag following the versioning convention.
   - *Product upgrade:* If upgrading the base product (e.g., from Redis 7.4.7 to
     8.2.5), use the new product version and reset the manifest version to `000`
     (e.g., `redis-8.2.5.000`).
   - *Manifest update:* If fixing or updating the manifests for an existing
     product version, increment the manifest version (e.g., from
     `redis-7.4.7.000` to `redis-7.4.7.001`).
1. **Push the tag:** Push the new tag to the remote repository:
   ```bash
   git tag redis-8.2.5.000
   git push origin redis-8.2.5.000
   ```

## Examples

The `examples/` directory contains sample overlays demonstrating how to
customize the base components for different environments:

- **redis/dev**: Development environment with 3 Redis replicas
  (password-protected) and 1 Sentinel replica (password-protected). Uses minimal
  resources suitable for development/testing.

To deploy an example:

```
kubectl apply --kustomize examples/redis/overlays/dev/
```

## Usage

Use `kubectl apply --kustomize <component>/` to deploy the components.

For example:
```
kubectl apply --kustomize components/redis/
```

## Using as Base Manifests

These manifests can serve as base configurations for your applications. To
create an overlay:

1. Create a new directory for your application, e.g., `my-app/`
2. Inside `my-app/`, create a `kustomization.yaml` that references this repo as
   a base:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
bases:
  - github.com/stn-dts/kustomize-manifests/components/redis?ref=redis-7.4.7
patchesStrategicMerge:
  - patch.yaml
#
# Add your customizations here
#
```

3. Apply with: `kubectl apply --kustomize my-app/`

## Best Practices

- Always use overlays for environment-specific customizations
- Store secrets securely using Kubernetes secrets or external secret managers
- Validate manifests with `kubectl apply --dry-run=client --kustomize <dir>/`
- Use resource limits and requests to prevent resource starvation
- Enable Pod Disruption Budgets for high availability

## Structure

- `components/`: Directory containing Kustomize bases for different
  applications.
- `examples/`: Sample overlays showing how to customize components for specific
  environments.

## Resources

- [Kustomize
  Documentation](https://kubectl.docs.kubernetes.io/references/kustomize/)
- [Kubernetes Best
  Practices](https://kubernetes.io/docs/concepts/configuration/overview/)
- [Kustomize GitOps
  Guide](https://kubectl.docs.kubernetes.io/guides/config_management/)
