#!/usr/bin/env bash
set -euo pipefail

SEED="${1:-1}"
TARGET_HOST="${TARGET_HOST:-vigil-worker-1}"
SSH_KEY="${SSH_KEY_PATH:-$HOME/.ssh/id_ed25519}"

ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "root@${TARGET_HOST}" \
  "systemctl stop rollback-gate.timer"

ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "root@${TARGET_HOST}" \
  "sysctl -w net.bridge.bridge-nf-call-iptables=1 || true"

ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "root@${TARGET_HOST}" \
  "flock /var/lock/vigil-nixos-rebuild nixos-rebuild switch --flake /opt/nixos-config#\$(cat /etc/vigil/flake-attr)"

echo "reset.sh: os-drift-sysctl seed=${SEED} - node at baseline"
