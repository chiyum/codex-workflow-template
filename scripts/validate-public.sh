#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
command -v python3 >/dev/null 2>&1 || { echo "Public 驗證器故障：找不到 python3" >&2; exit 2; }
command -v git >/dev/null 2>&1 || { echo "Public 驗證器故障：找不到 git" >&2; exit 2; }
exec python3 "$ROOT/scripts/validate_public.py" --root "$ROOT"

