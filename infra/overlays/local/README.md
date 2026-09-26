# Local Overlay

Minimal Kubernetes configuration for local KinD development and thesis testing.

## What's included

This overlay strips the full Hetzner deployment down to only what's needed for local
testing on KinD:

| Layer | Contents |
|---|---|
| `flux-system/` | Flux controller pods + GitRepository pointing at `lucawalz/vigil` |
| `namespaces/` | `default` namespace only |
| `sources/` | `bitnami` (OCI) + `ingress-nginx` Helm repositories |
| `infrastructure/` | `ingress-nginx` Helm release (NodePort service) |
| `apps/` | `vigil-app` (nginx + stress), `app-backend` (http-echo) |

## What was removed

The following Hetzner-specific resources were dropped because they don't work on KinD:

- **Rancher** — cluster management platform, not an app
- **Longhorn** — needs real block devices
- **cert-manager** — needs a real domain + public IP for Let's Encrypt
- **PostgreSQL** — depends on Longhorn storage class
- **Redis** — depends on Longhorn storage class
- **kube-prometheus-stack** — too heavy for small KinD nodes
- **monitoring/ingress/cert-manager namespaces** — not needed locally

## Dependency chain

```
gotk-sync.yaml (GitRepository)
    ↓
gotk-components.yaml (Flux controllers)
    ↓
config/namespaces.yaml  →  default namespace
    ↓
config/sources.yaml     →  bitnami + ingress-nginx Helm repos
    ↓
config/infrastructure.yaml  →  ingress-nginx (NodePort)
    ↓
config/apps.yaml        →  vigil-app + app-backend
```

## How to use

### Quick install (no Git bootstrap needed)

```powershell
kubectl config use-context kind-vigil-test

# Apply everything in dependency order
kubectl apply -k ./infra/overlays/local/kubernetes/clusters/local/flux-system/
kubectl apply -k ./infra/overlays/local/kubernetes/clusters/local/namespaces/
kubectl apply -k ./infra/overlays/local/kubernetes/clusters/local/sources/
kubectl apply -k ./infra/overlays/local/kubernetes/clusters/local/infrastructure/
kubectl apply -k ./infra/overlays/local/kubernetes/clusters/local/apps/

# Verify
kubectl get pods -n flux-system
kubectl get pods -n default
```

### Full GitOps (requires forking lucawalz/vigil)

1. Fork `lucawalz/vigil` to your GitHub account
2. Create a branch: `git checkout -b local-testing`
3. Commit this `local/` overlay to your fork
4. Edit `infra/overlays/local/kubernetes/clusters/local/flux-system/gotk-sync.yaml`:
   - Change `url` to `https://github.com/<your-username>/vigil.git`
   - Change `branch` to `local-testing`
5. Bootstrap:
   ```powershell
   flux bootstrap github \
     --owner=<your-username> \
     --repository=vigil \
     --branch=local-testing \
     --path=./infra/overlays/local/kubernetes/clusters/local \
     --personal
   ```

## Accessing services

```powershell
# vigil-app (nginx) — NodePort via ingress-nginx
# or direct port-forward:
kubectl port-forward service/ingress-nginx-controller -n ingress-nginx 8080:80
curl http://localhost:8080

# app-backend
kubectl port-forward service/app-backend 8081:80
curl http://localhost:8081
```
