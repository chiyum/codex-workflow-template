#!/bin/bash
# 用法: verify-deploy.sh <version_url> <expected_commit> [timeout_seconds] [interval_seconds] [jq_path]
# 依據: 輪詢產品的 version endpoint，直到回傳的 commit 與本次 push 的 commit 一致
# 退出碼: 0=新版本已上線, 1=超時（部署問題，非測試失敗）
#
# 說明:
#   - 參數來自各產品 products/<product>.md 的「部署驗證」章節（version_url / jq_path / timeout / interval）
#   - 多 repo 產品: expected_commit 應取「擁有此 version endpoint 的那個 repo」的 HEAD，
#     不要混用其他 repo 的 HEAD

VERSION_URL="$1"
EXPECTED_COMMIT="$2"
TIMEOUT="${3:-600}"      # 預設 10 分鐘
INTERVAL="${4:-10}"      # 預設每 10 秒輪詢一次
JQ_PATH="${5:-.commit}"  # 預設回應格式 {"commit": "..."}

if [ -z "$VERSION_URL" ] || [ -z "$EXPECTED_COMMIT" ]; then
  echo "❌ 參數不足: verify-deploy.sh <version_url> <expected_commit> [timeout] [interval] [jq_path]"
  exit 1
fi

ELAPSED=0
while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
  DEPLOYED=$(curl -s --max-time 10 "$VERSION_URL" | jq -r "$JQ_PATH" 2>/dev/null)
  if [ "$DEPLOYED" = "$EXPECTED_COMMIT" ]; then
    echo "✅ 新版本已上線 (commit: ${EXPECTED_COMMIT:0:8})，耗時 ${ELAPSED}s"
    exit 0
  fi
  # 顯示目前線上 commit 前 8 碼；無回應時顯示「無回應」
  SHOWN="${DEPLOYED:0:8}"
  [ -z "$DEPLOYED" ] && SHOWN="無回應"
  echo "⏳ 等待部署... 目前線上: ${SHOWN}，期望: ${EXPECTED_COMMIT:0:8} (${ELAPSED}s/${TIMEOUT}s)"
  sleep "$INTERVAL"
  ELAPSED=$((ELAPSED + INTERVAL))
done

echo "❌ 超時: ${TIMEOUT}s 內未偵測到新版本上線。此為【部署問題】，請勿退回 architect，應回報使用者檢查部署狀態。"
exit 1
