#!/usr/bin/env bash

set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
"$DIR/k8s-3g/inject.sh" "${1:-1}"
"$DIR/os-drift-sysctl/inject.sh" "${1:-1}"