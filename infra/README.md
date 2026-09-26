# Infrastructure

NixOS and Kubernetes cluster configuration.

- `local/` - Local KinD dev cluster configuration (Flux GitOps + vigil-app)
- `overlays/` - Kubernetes manifests for cluster provisioning
  - `hetzner/` - Full Hetzner Cloud deployment (Flux, Rancher, Longhorn, Prometheus, etc.)
  - `local/` - Minimal KinD overlay for local development and thesis testing
- `terraform/` - Hetzner Cloud eval cluster Terraform scripts
- `nixos/` - NixOS host configurations for Hetzner nodes
- `packer/` - VM image building
- `policy/` - OPA/Gatekeeper policies
