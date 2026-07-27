#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/playwright-lock-test.XXXXXX")"
cleanup() {
  rm -f "$TEST_ROOT/playwright-mcp.lock.d/owner" "$TEST_ROOT/playwright-mcp.lock.d/acquired_at"
  rmdir "$TEST_ROOT/playwright-mcp.lock.d" 2>/dev/null || true
  rm -f "$TEST_ROOT/user-root/.codex/locks/playwright-mcp.lock.d/owner" "$TEST_ROOT/user-root/.codex/locks/playwright-mcp.lock.d/acquired_at"
  rmdir "$TEST_ROOT/user-root/.codex/locks/playwright-mcp.lock.d" 2>/dev/null || true
  rmdir "$TEST_ROOT/user-root/.codex/locks" 2>/dev/null || true
  rmdir "$TEST_ROOT/user-root/.codex" 2>/dev/null || true
  rmdir "$TEST_ROOT/home" 2>/dev/null || true
  rmdir "$TEST_ROOT" 2>/dev/null || true
}
trap cleanup EXIT
export PLAYWRIGHT_LOCK_ROOT="$TEST_ROOT"
export PLAYWRIGHT_LOCK_WAIT_SECONDS=0
export PLAYWRIGHT_LOCK_POLL_SECONDS=0

LOCK="$ROOT/scripts/playwright-lock.sh"
"$LOCK" status | grep -q "LOCK FREE"
"$LOCK" acquire --owner qa-test | grep -q "LOCK ACQUIRED"
"$LOCK" status | grep -q "owner=qa-test"
if "$LOCK" acquire --owner pm-test >/dev/null 2>&1; then
  echo "第二持有者不應取得鎖" >&2
  exit 1
fi
if "$LOCK" release --owner pm-test >/dev/null 2>&1; then
  echo "非 owner 不應釋放鎖" >&2
  exit 1
fi
"$LOCK" release --owner qa-test | grep -q "LOCK RELEASED"

export PLAYWRIGHT_LOCK_STALE_SECONDS=1
"$LOCK" acquire --owner stale-test >/dev/null
printf '0\n' > "$TEST_ROOT/playwright-mcp.lock.d/acquired_at"
"$LOCK" acquire --owner replacement 2>/dev/null | grep -q "owner=replacement"
"$LOCK" release --owner replacement >/dev/null

# 不同 CODEX_HOME/profile 必須競爭同一把 HOME 級全域鎖。
unset PLAYWRIGHT_LOCK_ROOT
export PLAYWRIGHT_LOCK_STALE_SECONDS=1200
GLOBAL_HOME="$TEST_ROOT/user-root"
mkdir -p "$GLOBAL_HOME"
HOME="$GLOBAL_HOME" CODEX_HOME="$TEST_ROOT/profile-a" "$LOCK" acquire --owner profile-a >/dev/null
if HOME="$GLOBAL_HOME" CODEX_HOME="$TEST_ROOT/profile-b" "$LOCK" acquire --owner profile-b >/dev/null 2>&1; then
  echo "不同 CODEX_HOME 不應繞過全域鎖" >&2
  exit 1
fi
HOME="$GLOBAL_HOME" CODEX_HOME="$TEST_ROOT/profile-a" "$LOCK" release --owner profile-a >/dev/null
echo "playwright lock 測試通過"
