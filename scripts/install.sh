#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
command -v python3 >/dev/null 2>&1 || { echo "安裝失敗：找不到 python3" >&2; exit 2; }
exec python3 "$ROOT/scripts/install_impl.py" "$@"

