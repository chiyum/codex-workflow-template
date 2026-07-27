#!/usr/bin/env bash
set -euo pipefail

COMMAND="${1:-}"
shift || true
# 瀏覽器資源跨 aiuse/CODEX_HOME profile 共用；只有測試可用明確 override 隔離。
LOCK_ROOT="${PLAYWRIGHT_LOCK_ROOT:-$HOME/.codex/locks}"
LOCK_DIR="$LOCK_ROOT/playwright-mcp.lock.d"
STALE_SECONDS="${PLAYWRIGHT_LOCK_STALE_SECONDS:-1200}"
WAIT_SECONDS="${PLAYWRIGHT_LOCK_WAIT_SECONDS:-3600}"
POLL_SECONDS="${PLAYWRIGHT_LOCK_POLL_SECONDS:-7}"
OWNER=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --owner) OWNER="${2:-}"; shift 2 ;;
    *) echo "未知參數：$1" >&2; exit 2 ;;
  esac
done

case "$STALE_SECONDS:$WAIT_SECONDS:$POLL_SECONDS" in
  *[!0-9:]*|::*|:|*:) echo "鎖定時間參數必須是非負整數" >&2; exit 2 ;;
esac

read_owner() { [ -f "$LOCK_DIR/owner" ] && sed -n '1p' "$LOCK_DIR/owner" || true; }
read_acquired() { [ -f "$LOCK_DIR/acquired_at" ] && sed -n '1p' "$LOCK_DIR/acquired_at" || true; }

case "$COMMAND" in
  acquire)
    [ -n "$OWNER" ] || { echo "acquire 必須提供 --owner" >&2; exit 2; }
    mkdir -p "$LOCK_ROOT"
    waited=0
    while true; do
      if mkdir "$LOCK_DIR" 2>/dev/null; then
        date +%s > "$LOCK_DIR/acquired_at"
        printf '%s\n' "$OWNER" > "$LOCK_DIR/owner"
        echo "LOCK ACQUIRED owner=$OWNER"
        exit 0
      fi
      now=$(date +%s)
      acquired=$(read_acquired)
      case "$acquired" in ''|*[!0-9]*) acquired="$now" ;; esac
      age=$((now - acquired))
      if [ "$age" -gt "$STALE_SECONDS" ]; then
        quarantine="$LOCK_ROOT/playwright-mcp.stale.$$.d"
        if mv "$LOCK_DIR" "$quarantine" 2>/dev/null; then
          rm -f "$quarantine/owner" "$quarantine/acquired_at"
          rmdir "$quarantine" 2>/dev/null || true
          echo "STALE LOCK RECLAIMED age=${age}s" >&2
        fi
        continue
      fi
      if [ "$waited" -ge "$WAIT_SECONDS" ]; then
        echo "LOCK WAIT TIMEOUT owner=$(read_owner)" >&2
        exit 1
      fi
      sleep "$POLL_SECONDS"
      waited=$((waited + POLL_SECONDS))
    done
    ;;
  release)
    [ -n "$OWNER" ] || { echo "release 必須提供 --owner" >&2; exit 2; }
    [ -d "$LOCK_DIR" ] || { echo "LOCK ALREADY RELEASED"; exit 0; }
    current=$(read_owner)
    [ "$current" = "$OWNER" ] || { echo "LOCK OWNER MISMATCH current=$current requested=$OWNER" >&2; exit 1; }
    rm -f "$LOCK_DIR/owner" "$LOCK_DIR/acquired_at"
    if rmdir "$LOCK_DIR" 2>/dev/null; then
      echo "LOCK RELEASED owner=$OWNER"
    else
      echo "LOCK RELEASE FAILED：鎖目錄含非預期內容" >&2
      exit 1
    fi
    ;;
  status)
    if [ -d "$LOCK_DIR" ]; then
      echo "LOCK HELD owner=$(read_owner) acquired_at=$(read_acquired)"
      exit 0
    fi
    echo "LOCK FREE"
    ;;
  *)
    echo "用法：playwright-lock.sh acquire|release|status [--owner <id>]" >&2
    exit 2
    ;;
esac

