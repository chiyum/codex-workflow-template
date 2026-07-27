---
name: dev
description: 自主執行凍結、architect、review、QA/PM、發布驗證與一次性回報的完整開發管線。
---

# Dev Skill

1. 讀 `AGENTS.md`、產品配置與 `workflows/development-workflow.md`。
2. 依 `workflow-profile.py plan` 的 owner 凍結驗收：L1 由主 Codex 產出 1–3 條且不 spawn PM；L2/L3 才由 PM 產出。大型任務建立 state checkpoint。
3. 只委派 architect 寫產品程式碼。大改自主採 architect 推薦方案並記錄理由。
4. 跑 pre-review，交 reviewer；嚴重／一般 finding 退 architect。architect 有實質修正或新證據就持續完整 gate，不設退修輪數上限。只有沒有新證據、沒有狀態改變而原樣重做同一操作三次仍同結果，才停止該動作並重新分析或換方法，不凍結 finding。
5. reviewer 通過後交 QA；L1 由主 Codex audit QA evidence，L2/L3 再交獨立 PM 驗收；大型任務依 lane 加反方 PM。
6. 本地全綠才依產品配置 push 與驗證測試環境。正式環境需另有明確授權。
7. 一次回報清單結果、證據、commit、發布狀態、決策與最短複驗。

任何 agent 都不得改 gate、縮放需求或用推論取代測試。
