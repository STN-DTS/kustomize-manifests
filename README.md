# Kustomize Manifests

## Intro

This repository contains Kustomize configurations for deploying various
applications on Kubernetes. These manifests provide reusable, customizable
templates for common application deployments.

## Prerequisites

- [kubectl](https://kubernetes.io/docs/tasks/tools/) installed and configured
- [Kustomize](https://kustomize.io/) support for the `labels` and `patches`
  fields used here. Check the version bundled with kubectl using
  `kubectl version --client`, or use a recent standalone Kustomize release.

## Components

- **keel**: A simple, robust, background service that automatically updates
  Kubernetes workloads. It is namespace-scoped; deploy it into the namespace
  whose workloads it should manage.
- **opensearch**: OpenSearch cluster and an application read/write role
  (`app-readwrite` on `app-*` indices) as OpenSearch Operator custom resources.
  Requires the operator and its CRDs; the cluster is incomplete on its own, so
  overlays must supply the image, node pools, and environment-specific secret
  and service names, and should rename the role and its index pattern.
  The optional `opensearch/auditlog-retention` add-on deletes security audit
  log indices after 3 days.
- **redis**: Redis primary/replica StatefulSets with Sentinel. This is not
  Redis Cluster mode. The ClusterIP Redis service targets the initial primary
  pod and does not follow a Sentinel failover; use a Sentinel-aware client when
  clients must discover the current primary.
- **reloader**: Reloader controller that automatically triggers rolling updates
  on Deployments, StatefulSets, DaemonSets, and CronJobs when referenced
  ConfigMaps or Secrets are updated. Its Role and namespace setting scope it to
  the namespace where it is deployed.

## Versioning

This repository uses Git tags to manage versions for the different components.
The tagging convention follows this format:

`<component-name>-<product-version>.<manifest-version>`

For example, the current tag `redis-8.2.5.000` breaks down as follows:
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
   directory (e.g., `components/redis/`). For product upgrades, update the
   image tag and version labels in the component's Kustomization as well.
2. **Commit:** Commit and push your changes to the main branch.
3. **Tag the release:** Create a new git tag following the versioning convention.
   - *Product upgrade:* If upgrading the base product (e.g., from Redis 7.4.7 to
     8.2.5), use the new product version and reset the manifest version to `000`
     (e.g., `redis-8.2.5.000`).
   - *Manifest update:* If fixing or updating the manifests for an existing
     product version, increment the manifest version (e.g., from
     `redis-7.4.7.000` to `redis-7.4.7.001`).
4. **Push the tag:** Push the new tag to the remote repository:
   ```bash
   git tag redis-8.2.5.000
   git push origin redis-8.2.5.000
   ```

## Examples

The `examples/` directory contains sample overlays demonstrating how to
customize the base components for different environments:

- **redis/dev**: Development environment with one initial Redis primary, two
  replicas, and one Sentinel. Redis requires a password; Sentinel client access
  is unauthenticated. The overlay contains an example password and is only for
  disposable development/testing, not production.

## Usage

Components are Kustomize bases; use an environment-specific overlay where
required configuration and credentials are supplied. For example, to deploy
the Redis development overlay:

```bash
kubectl apply --kustomize examples/redis/overlays/dev/
```

Do not apply `components/redis/` directly without replacing its all-zero
placeholder password and supplying the configuration required by your
environment. If your overlay uses `nameSuffix`, patch the Redis Service's
`statefulset.kubernetes.io/pod-name` selector to match the suffixed pod name;
Kustomize does not rewrite that label value. See the [Redis development
overlay guide](examples/redis/overlays/dev/README.md) for its limitations and
setup.

## Using as Base Manifests

These manifests can serve as base configurations for your applications. To
create an overlay:

1. Create a new directory for your application, e.g., `my-app/`.
2. Inside `my-app/`, create a `kustomization.yaml` that references a tagged
   component version:

   ```yaml
   apiVersion: kustomize.config.k8s.io/v1beta1
   kind: Kustomization
   resources:
     - github.com/stn-dts/kustomize-manifests/components/redis?ref=redis-8.2.5.000
   ```

3. Before applying, replace the Redis base's all-zero placeholder password
   through a secure secret-management workflow and add any
   environment-specific patches your deployment needs. Do not commit real
   credentials.
4. Render the overlay to check it: `kubectl kustomize my-app/`.
5. Apply it only after supplying real credentials and required
   environment-specific settings: `kubectl apply --kustomize my-app/`.

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
