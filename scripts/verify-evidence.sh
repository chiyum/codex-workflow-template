#!/bin/bash
# shell 入口保留相容；實際規則由 Python 確定性驗證，避免只看檔名就假 PASS。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
if [[ $# -ne 2 ]]; then
  echo "evidence: blocked reason=usage"
  exit 1
fi
exec python3 "$SCRIPT_DIR/verify-evidence.py" "$1" --profile "$2"
