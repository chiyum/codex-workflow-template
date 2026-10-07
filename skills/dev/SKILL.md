---
name: dev
description: 執行明確要求的功能實作、修正或接續已授權開發；不因產品名稱、路徑或 URL 觸發，不用於純解說、唯讀診斷或盤點。支援 Full／Standard／Lite profile、獨立 auto 模式、lane 升級、凍結後自主決策與 state 接續。
---

# Dev Skill

本 skill 是標準開發流程入口。所有 gate 以 `scripts/workflow-profile.py` 的確定性輸出為準。

## 語法

- `$dev full <需求>`、`$dev standard <需求>`、`$dev lite <需求>`：選擇 requested profile。
- profile 後可加 `auto`；所有 code 新任務仍先確認凍結，`auto` 只讓凍結後的範圍內決策不停等，不改 profile。
- 既有 `$dev auto <需求>` 向後相容為 Standard + auto。
- `$dev <需求>` 與未指定 profile 的自然語言請求：先保留「未指定」，分 lane 後以 L1→Lite、L2→Standard、L3→Full 作 requested profile。
- `$dev 繼續 <slug>` 讀 state 的完整 plan identity、effective profile 與 `next_action` 接續，不重新套用預設值。

以 `workflow-profile.py parse` 解析 modifier。優先透過執行工具的 stdin 欄位把原始輸入直接傳給 `workflow-profile.py parse --stdin`；若呼叫端使用 argv，則把完整原文作為單一 `--input` argv。禁止把原文插入 shell command、shell 單引號或未受控 heredoc，避免 `$()`、`;` 與引號被 shell 解讀。fresh baseline 經使用者確認後，新任務執行 `workflow-profile.py plan --lane <lane> --mode <auto|standard> [--profile <profile>] --target <local|dev|prod|unknown> --baseline-receipt <receipt path>`；接續任務執行 `workflow-profile.py plan --lane <state lane> --mode continue --state <state path>`。receipt 缺失、不新鮮或 identity 漂移時 plan 保持 code 寫入 blocked。

## Fresh baseline gate

acceptance 討論前，主 Codex 依產品配置把 repo 與 routing 資訊經 stdin 交給 `scripts/development-baseline.py collect`，呈現每個 repo 的 path／branch／local HEAD／dirty／untracked／remote HEAD／擬採基準，以及目標環境 deployed version 與查詢時間；查不到寫 `unavailable_reason`，不得猜測。任一差異都在 acceptance 討論明示，等使用者確認擬採基準後才凍結；未凍結前不得寫 code。產品配置只提供 routing，選定 baseline 的 repo code／規格／ADR 才是行為真相，凍結 acceptance 是本次驗收 gate；三者衝突時明示，不批次改寫。

## Profile gate

- **Full**：PM 凍結 → architect → reviewer → 獨立 QA → 獨立 PM。
- **Standard**：同一 verifier 凍結 → architect → reviewer → follow-up verifier 合併實測與驗收；不開 QA/PM。
- **Lite**：主 Codex 凍結 → architect 修改與自測 → verifier 實測與驗收；不開 reviewer/QA/PM。

L1 可用三種 profile；L2 至少 Standard；L3 強制 Full。只能升級，不能降級。release risk 另列 overlay／gates，target 本身不改 change lane；effective floor 取 change lane floor 與產品 release floor 較高者；缺 release policy 的 direct prod fail-safe Full，且所有 direct prod 都要使用者確認。security/design 等命中式專項 gate 一律保留。

## 凍結與自主邊界

1. **凍結前以白話做需求來源對照**：先說「真正要解決 X／Y 維持不變／用什麼驗 A／Z 是額外推論」，每條 acceptance 標成原始要求、討論後明確確認、完成 A 不可缺少的正確性條件，或使用者已確認的推論／可選改善。最後一類未確認前不得進 acceptance、code 或 blocker；「必要」不得被拿來加入相鄰 B 或一般資安加固。`auto` 從凍結完成後才自主執行。
2. **小決策協作後執行**：凍結後依 `workflows/DECISION_LOG.md`。D1 由 PM 職責（Full 用 PM，Standard／Lite 用 verifier）與適用技術 agent 提供判斷，主 Codex 收斂、記錄後交 architect 實作及驗證；不改放行 gate。合理預設值與局部防護不因有可見效果就一律重凍結。
3. **待決按依賴續作**：D2 寫入 `state/<slug>-questions.md`，維持 `running` 並實際轉做獨立必做項；D3 立即提出證據與具體核准請求，只停止相依部分，未回覆不是同意。必要角色不可用標 `AGENT_UNAVAILABLE`，不偽造參與。
4. **語意與續作檢查**：每條功能路徑／重大退修核對原問題、需求、非目標與新假設；D2 已成主線前置或無獨立必做工作則升 D3。
5. **三次只停止無進展動作**：只有沒有新證據、沒有狀態改變、同一操作原樣連做三次且結果相同才換方法；architect 實質修正後重驗是正常退修，不限輪數。
6. **資安不自動擴張**：一般資安測試、掃描、加固與 security-auditor 預設不啟動；只有使用者明確要求，或既有硬 gate 被本次實際變更面命中且已在凍結前明示時才執行。
7. **發布授權分開**：涉及不可逆刪除、付費、主動掃描、正式環境或需求矛盾必須中斷確認；本次明確禁止的 commit／push／部署不得執行。

每個 gate 轉換更新 state；D2／D3 有獨立需求時以 `running`＋`next_action` 指向該需求與 decision log，只有無安全獨立工作時才用 `awaiting_user`。

## 最終回報

1. **改動前 vs. 改動後**：按功能路徑分組，白話＋專業術語，交代功能、效能、系統負荷、一致性／失敗模式與維運差異；量化成果附本次量測證據，未量測明標方向性影響。
2. 驗收結果逐條（A1…An，附證據路徑）。
3. **需求忠實度判定**：比較原始需求、使用者後續決定、凍結 acceptance 與實際 diff，回答有無少做、多做或未確認假設。
4. **非阻擋建議**：彙整去重各角色建議，逐項列來源、內容、與 A 的關係及本次不處理原因；沒有寫「無」。
5. 一分鐘複驗指引。
6. **我幫你做的決定**：決定、原因、未採方案、優點與缺點／代價；沒有寫「無」。
7. questions 與 blockers（若有）、commit 與發布狀態。
8. 必做需求及必要 gate 全部完成才標 `done`；否則回報部分完成並保存續作點。
