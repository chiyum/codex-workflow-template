#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/playwright-lock-test.XXXXXX")"
cleanup() {
  rm -f "$TEST_ROOT/same-owner.out" "$TEST_ROOT/contender.out" "$TEST_ROOT/cross-profile.out"
  rm -f "$TEST_ROOT/stale-a.out" "$TEST_ROOT/stale-b.out"
  rm -f "$TEST_ROOT/playwright-mcp.lock.d/unexpected"
  rm -f "$TEST_ROOT/playwright-mcp.lock.d/owner" "$TEST_ROOT/playwright-mcp.lock.d/acquired_at"
  rmdir "$TEST_ROOT/playwright-mcp.lock.d" 2>/dev/null || true
  rm -f "$TEST_ROOT/global-home/.codex/locks/playwright-mcp.lock.d/owner" "$TEST_ROOT/global-home/.codex/locks/playwright-mcp.lock.d/acquired_at"
  rmdir "$TEST_ROOT/global-home/.codex/locks/playwright-mcp.lock.d" 2>/dev/null || true
  rmdir "$TEST_ROOT/global-home/.codex/locks" 2>/dev/null || true
  rmdir "$TEST_ROOT/global-home/.codex" 2>/dev/null || true
  rmdir "$TEST_ROOT/global-home" 2>/dev/null || true
  rmdir "$TEST_ROOT" 2>/dev/null || true
}
trap cleanup EXIT
export PLAYWRIGHT_LOCK_ROOT="$TEST_ROOT"
# 舊環境變數即使殘留也不得重新啟用背景 waiter。
export PLAYWRIGHT_LOCK_WAIT_SECONDS=3600
export PLAYWRIGHT_LOCK_POLL_SECONDS=7

LOCK="$ROOT/scripts/playwright-lock.sh"
assert_fast_acquire_failure() {
  local owner="$1" expected="$2" output="$3"
  python3 - "$LOCK" "$owner" "$expected" "$output" <<'PY'
from pathlib import Path
import subprocess, sys
try:
    completed = subprocess.run(
        [sys.argv[1], "acquire", "--owner", sys.argv[2]],
        text=True, capture_output=True, timeout=2, check=False,
    )
except subprocess.TimeoutExpired:
    print(f"{sys.argv[2]} 超過 2 秒仍未 fail-fast", file=sys.stderr)
    raise SystemExit(1)
combined = completed.stdout + completed.stderr
Path(sys.argv[4]).write_text(combined, encoding="utf-8")
if completed.returncode != 1 or sys.argv[3] not in combined:
    print(f"{sys.argv[2]} acquire 結果錯誤：rc={completed.returncode}", file=sys.stderr)
    raise SystemExit(1)
PY
}

"$LOCK" status | grep -q "LOCK FREE"
"$LOCK" acquire --owner qa-test | grep -q "LOCK ACQUIRED"
"$LOCK" status | grep -q "owner=qa-test"
acquired_at=$(sed -n '1p' "$TEST_ROOT/playwright-mcp.lock.d/acquired_at")
assert_fast_acquire_failure qa-test "LOCK ALREADY HELD owner=qa-test" "$TEST_ROOT/same-owner.out"
[ "$acquired_at" = "$(sed -n '1p' "$TEST_ROOT/playwright-mcp.lock.d/acquired_at")" ] || {
  echo "同 owner acquire 不應刷新 acquired_at" >&2
  exit 1
}

assert_fast_acquire_failure pm-test "LOCK BUSY owner=qa-test" "$TEST_ROOT/contender.out"
if "$LOCK" release --owner pm-test >/dev/null 2>&1; then
  echo "非 owner 不應釋放鎖" >&2
  exit 1
fi
touch "$TEST_ROOT/playwright-mcp.lock.d/unexpected"
if "$LOCK" release --owner qa-test >/dev/null 2>&1; then
  echo "含非預期內容時不得部分釋放鎖" >&2
  exit 1
fi
grep -q "qa-test" "$TEST_ROOT/playwright-mcp.lock.d/owner"
rm -f "$TEST_ROOT/playwright-mcp.lock.d/unexpected"
"$LOCK" release --owner qa-test | grep -q "LOCK RELEASED"

export PLAYWRIGHT_LOCK_STALE_SECONDS=1
"$LOCK" acquire --owner stale-test >/dev/null
printf '0\n' > "$TEST_ROOT/playwright-mcp.lock.d/acquired_at"
python3 - "$LOCK" "$TEST_ROOT/stale-a.out" "$TEST_ROOT/stale-b.out" <<'PY'
from pathlib import Path
import subprocess, sys, time
processes = [
    subprocess.Popen(
        [sys.argv[1], "acquire", "--owner", owner],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    for owner in ("replacement-a", "replacement-b")
]
deadline = time.monotonic() + 2
results = []
try:
    for process in processes:
        stdout, stderr = process.communicate(timeout=max(0.01, deadline - time.monotonic()))
        results.append((process.returncode, stdout + stderr))
except subprocess.TimeoutExpired:
    for process in processes:
        process.kill()
    for process in processes:
        process.communicate()
    print("stale 競爭者超過 2 秒仍未結束", file=sys.stderr)
    raise SystemExit(1)
for output, (_, content) in zip(sys.argv[2:], results):
    Path(output).write_text(content, encoding="utf-8")
if sorted(returncode for returncode, _ in results) != [0, 1]:
    print(f"stale 競爭 exit code 錯誤：{[item[0] for item in results]}", file=sys.stderr)
    raise SystemExit(1)
PY
replacement_owner=$(sed -n '1p' "$TEST_ROOT/playwright-mcp.lock.d/owner")
case "$replacement_owner" in
  replacement-a|replacement-b) ;;
  *) echo "stale reclaim owner 錯誤：$replacement_owner" >&2; exit 1 ;;
esac
"$LOCK" release --owner "$replacement_owner" >/dev/null

# 不同 CODEX_HOME/profile 必須競爭同一把 HOME 級全域鎖。
unset PLAYWRIGHT_LOCK_ROOT
export PLAYWRIGHT_LOCK_STALE_SECONDS=1200
GLOBAL_HOME="$TEST_ROOT/global-home"
mkdir -p "$GLOBAL_HOME"
HOME="$GLOBAL_HOME" CODEX_HOME="$TEST_ROOT/profile-a" "$LOCK" acquire --owner profile-a >/dev/null
(
  export HOME="$GLOBAL_HOME" CODEX_HOME="$TEST_ROOT/profile-b"
  assert_fast_acquire_failure profile-b "LOCK BUSY owner=profile-a" "$TEST_ROOT/cross-profile.out"
)
HOME="$GLOBAL_HOME" CODEX_HOME="$TEST_ROOT/profile-a" "$LOCK" release --owner profile-a >/dev/null
echo "playwright lock 測試通過"
