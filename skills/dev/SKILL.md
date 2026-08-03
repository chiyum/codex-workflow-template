---
name: dev
description: 支援 Full／Standard／Lite profile、獨立 auto 模式、lane 升級與 state 接續的一鍵開發管線。
---

# Dev Skill

本 skill 是標準開發流程入口。所有 gate 以 `scripts/workflow-profile.py` 的確定性輸出為準。

## 語法

- `$dev full <需求>`、`$dev standard <需求>`、`$dev lite <需求>`：選擇 requested profile。
- profile 後可加 `auto`；它只略過步驟 0 的確認，不改 profile。
- 既有 `$dev auto <需求>` 等於 Standard + auto。
- `$dev <需求>` 與未指定 profile 的自然語言請求預設 Standard。
- `$dev 繼續 <slug>` 讀 state 的 effective profile 與 `next_action` 接續，不重新套用預設值。

以 `workflow-profile.py parse --input '<原始輸入>'` 解析 modifier。新任務再執行 `workflow-profile.py plan --lane <lane> --mode <auto|standard> --profile <profile>`；接續任務執行 `workflow-profile.py plan --lane <state lane> --mode continue --state <state path>`。

## Profile gate

- **Full**：PM 凍結 → architect → reviewer → 獨立 QA → 獨立 PM。
- **Standard**：同一 verifier 凍結 → architect → reviewer → follow-up verifier 合併實測與驗收；不開 QA/PM。
- **Lite**：主 Codex 凍結 → architect 修改與自測 → verifier 實測與驗收；不開 reviewer/QA/PM。

L1 可用三種 profile；L2 至少 Standard；L3 強制 Full。只能升級，不能降級。security/design 等命中式專項 gate 一律保留。

auto 與一般模式都只能在不可逆刪除、付費、資安、正式環境或需求矛盾時中斷。每個 gate 轉換更新 state；最終回報逐條證據、commit、發布狀態、決策與最短複驗。
