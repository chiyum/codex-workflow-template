#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
command -v python3 >/dev/null 2>&1 || { echo "repo 驗證故障：找不到 python3" >&2; exit 2; }

if [ "${1:-}" = "--installed" ]; then
  [ "$#" -eq 2 ] || { echo "用法：validate.sh --installed <target>" >&2; exit 2; }
  exec python3 "$ROOT/scripts/validate_repo.py" --root "$ROOT" --installed "$2"
fi
[ "$#" -eq 0 ] || { echo "未知參數" >&2; exit 2; }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/codex-validate.XXXXXX")"
cleanup() { python3 - "$TMP" <<'PY'
from pathlib import Path
import shutil, sys
p=Path(sys.argv[1])
if p.name.startswith('codex-validate.') and p.is_dir(): shutil.rmtree(p)
PY
}
trap cleanup EXIT

python3 "$ROOT/scripts/validate_repo.py" --root "$ROOT" --list-shell "$TMP/shell" --list-mjs "$TMP/mjs"
while IFS= read -r rel; do [ -z "$rel" ] || bash -n "$ROOT/$rel"; done < "$TMP/shell"
if [ -s "$TMP/mjs" ]; then
  command -v node >/dev/null 2>&1 || { echo "repo 驗證故障：缺少 node" >&2; exit 2; }
  while IFS= read -r rel; do [ -z "$rel" ] || node --check "$ROOT/$rel"; done < "$TMP/mjs"
fi
python3 "$ROOT/scripts/check-links.py" "$ROOT"
python3 "$ROOT/scripts/test-aiuse-profile.py"
python3 "$ROOT/scripts/test-run-metrics.py"
python3 "$ROOT/scripts/test-workflow-profile.py"
bash "$ROOT/scripts/test-playwright-lock.sh"
bash "$ROOT/scripts/scan-secrets.sh"
if [ -x "$ROOT/scripts/validate-public.sh" ]; then bash "$ROOT/scripts/validate-public.sh"; fi
bash "$ROOT/scripts/test-security.sh"

TARGET="$(python3 - "$TMP/installed" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).resolve())
PY
)"
bash "$ROOT/scripts/install.sh" --target "$TARGET" >/dev/null
python3 "$ROOT/scripts/validate_repo.py" --root "$ROOT" --installed "$TARGET"
echo "repo 驗證通過"
