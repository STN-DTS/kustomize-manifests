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

- **redis**: Redis cluster with Sentinel for high availability.

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

## Resources

- [Kustomize
  Documentation](https://kubectl.docs.kubernetes.io/references/kustomize/)
- [Kubernetes Best
  Practices](https://kubernetes.io/docs/concepts/configuration/overview/)
- [Kustomize GitOps
  Guide](https://kubectl.docs.kubernetes.io/guides/config_management/)
