#!/bin/bash
# pre-review: architect 完成後、reviewer 之前執行的確定性檢查
# 退出碼: 0=全部通過, 1=有問題（直接退回 architect，不計入 reviewer 3 回合）
# 注意: 本腳本在【目標產品 repo】的根目錄執行，而非本配置庫
#       用法示例（於產品 repo 根目錄）: bash ~/.codex/scripts/pre-review.sh
#
# 成長制度: 依 knowledge/README.md 先查相同根因與適用條件，優先合併既有卡；
#           確有必要的流程改善只先作提案，不會自動成為本腳本或產品的 gate。
#           只有經適用需求凍結與核准後，才修改 pre-review 規則或產品額外檢查。

FAIL=0

echo "=== [1/3] 靜態檢查 ==="
if ls *.go >/dev/null 2>&1 || [ -f go.mod ]; then
  go vet ./... || FAIL=1
  if command -v golangci-lint >/dev/null 2>&1; then
    golangci-lint run || FAIL=1
  fi
fi
if [ -f package.json ]; then
  # 前端: 偵測到 eslint 設定才跑，且讓其 exit code 決定成敗（與 Go 一致，會擋）
  # 排除 build 產物目錄：eslint flat config 預設只忽略 node_modules，不含 dist/build。
  # 若 architect 事前跑過 yarn build，工作區會留下 dist（bundled/minified），
  # 直接 eslint . 會去掃那包壓縮碼 → 極慢（曾實測 >10 分鐘）且對壓縮碼誤報 error，
  # 使整關假失敗。故顯式忽略常見 build 輸出目錄。詳見知識卡 pre-review-eslint-ignore-build-output。
  if ls .eslintrc* eslint.config.* >/dev/null 2>&1 || grep -q '"eslintConfig"' package.json 2>/dev/null; then
    npx --no-install eslint . \
      --ignore-pattern 'dist/**' \
      --ignore-pattern 'build/**' \
      --ignore-pattern '.output/**' \
      --ignore-pattern 'coverage/**' || FAIL=1
  fi
fi

echo "=== [2/3] Redis 殭屍 key 檢查 (SAdd/Set 後 5 行內未見 Expire/TTL) ==="
# 用 process substitution 讓迴圈在當前 shell 執行，FAIL 才能正確傳遞（避免 pipe 子 shell 陷阱）
while IFS=: read -r FILE LINE _; do
  [ -z "$FILE" ] && continue
  CONTEXT=$(sed -n "${LINE},$((LINE+5))p" "$FILE")
  if ! echo "$CONTEXT" | grep -q "Expire\|TTL\|SetEX\|SetNX.*time\."; then
    echo "⚠️ $FILE:$LINE 寫入 Redis 後未見 TTL 設定，請人工確認是否有清理機制"
    # 此項為警告，交由 reviewer 確認，不直接 FAIL；若要改為硬性失敗，取消下行註解
    # FAIL=1
  fi
done < <(grep -rn "\.SAdd(\|\.Set(\|\.HSet(\|\.HSetNX(\|\.RPush(\|\.LPush(\|\.ZAdd(\|\.Incr(\|\.IncrBy(" --include="*.go" . 2>/dev/null)

echo "=== [3/3] 產品自訂檢查 ==="
# 若目標產品 repo 根目錄存在 .pre-review-extra.sh，一併執行（各產品可自行擴充規則）
if [ -f .pre-review-extra.sh ]; then
  bash .pre-review-extra.sh || FAIL=1
fi

if [ "$FAIL" -eq 1 ]; then
  echo "❌ pre-review 未通過：請將上述輸出原樣附給 architect 修正後重跑。此輪【不計入】reviewer 3 回合。"
  exit 1
fi
echo "✅ pre-review 通過，進入 reviewer 審查"
exit 0
