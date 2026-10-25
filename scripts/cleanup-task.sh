#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
command -v python3 >/dev/null 2>&1 || {
  echo "cleanup 故障：找不到 python3" >&2
  exit 2
}
exec python3 "$SCRIPT_DIR/cleanup-task.py" "$@"
