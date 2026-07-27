# Codex 全域工作規範範本

## 基本原則

- 本檔與 `workflows/development-workflow.md` 是主 Codex 的確定性骨架；model/effort 與 L1–L3 只採 `workflows/effort-routing.md` 與 `scripts/workflow-profile.py` 輸出，子 agent 不得改 gate 或縮放範圍。
- 任何程式碼、測試、migration、build 或 deploy script 修改，只交給 `architect`。主 Codex、reviewer、QA、PM 與其他角色不直接寫產品程式碼。
- 純探索、診斷、文件、Codex 設定與 workflow 設定可由主 Codex 處理。
- 回覆、文件與 commit message 使用團隊約定語言；註解解釋 why。
- 不把 auth、token、password、env、runtime database、session、log 或 plugin cache 加入 Git。

## 標準鏈

1. 載入 `products/INDEX.md` 與命中的產品配置、規格及 knowledge playbook。
2. 先用 resolver 判定 L1–L3。L1 由主 Codex 凍結 1–3 條 acceptance；L2/L3 由 PM 產出並凍結。
3. architect 做方案、實作、測試、規格與必要 ADR，並 commit。
4. L1 跑 targeted pre-review；L2/L3 跑完整 pre-review，再交 reviewer。嚴重與一般問題退回 architect。architect 依 reviewer、QA 或 PM 意見做實質修正後重新送審是正常迭代，不設三輪上限。三次只限制沒有新證據、沒有狀態改變而原樣重做的同一操作；命中後停止該動作並重新分析或換方法，不凍結 finding 或失敗指紋。
5. reviewer 通過後由 QA 實測並保存逐條證據；L1 由主 Codex 稽核 QA 證據，L2/L3 保留獨立 PM 驗收。
6. 本地 gate 全綠後才依產品配置 push；是否碰測試環境或正式環境仍受產品政策與使用者授權限制。
7. 最終回報結果、證據、commit、發布狀態與最短複驗步驟。

## 子 agent 邊界

- `architect`：唯一產品程式碼寫入者。
- `reviewer`：唯讀審查 correctness、安全、效能、一致性與測試缺口。
- `qa`：執行實際測試與蒐證，不以推論代替測試。
- `pm`：只依凍結清單判斷完成與否。
- `ui_designer` / `design_reviewer`：視覺任務成對使用；前者先定規格，後者反方驗收。
- `security_auditor`：重大基礎設施變更時做防禦性唯讀審查；不主動掃描未授權目標。

平行 agent 必須維度互斥且唯讀，最後只由單一收斂步驟整合。所有 gate、無進展重複動作的三次保險絲與退回路徑由主 Codex 控制。

主 session 預設 `gpt-5.6-sol / medium`。test/QA 預設使用 Terra low/medium；`xhigh` 只限先說明理由的 L2/L3 單一 architect/reviewer 葉。Luna/max 不自動啟用，替換前必須有 matched A/B 證據與使用者決定。同任務退修與 local→dev 優先 follow-up 重用原 agent，只傳 delta。

## 權限護欄

可自動做安全、可逆、任務內的讀取、修改、測試、commit 與已授權發布。遇到不可逆刪除、付費、主動資安掃描、正式環境操作、需求矛盾或機密即將進入公開歷史，必須停下取得明確授權。

## 產品與機密

- 每個產品配置必須標示 repo、規格、環境、Git 帳號歸屬與部署驗證方式；未知就標 `待補`，不猜。
- 機密只放在安裝環境中未追蹤的 `SECRETS.local.md` 或 `env/`，文件只寫變數名稱與引用位置。
- 第三方 MCP／插件需自行安裝，版本、hooks 與 worker 必須一致；其 cache 與 state 不屬於 workflow source。
