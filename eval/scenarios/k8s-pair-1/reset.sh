#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
"$DIR/os-drift-sysctl/reset.sh" "${1:-1}"