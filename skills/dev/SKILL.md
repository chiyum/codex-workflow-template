---
name: dev
description: 支援 Full／Standard／Lite profile、獨立 auto 模式、lane 升級與 state 接續的一鍵開發管線。
---

# Dev Skill

本 skill 是標準開發流程入口。所有 gate 以 `scripts/workflow-profile.py` 的確定性輸出為準。

## 語法

- `$dev full <需求>`、`$dev standard <需求>`、`$dev lite <需求>`：選擇 requested profile。
- profile 後可加 `auto`；所有 code 新任務仍先確認凍結，`auto` 只讓凍結後的範圍內決策不停等，不改 profile。
- 既有 `$dev auto <需求>` 等於 Standard + auto。
- `$dev <需求>` 與未指定 profile 的自然語言請求預設 Standard。
- `$dev 繼續 <slug>` 讀 state 的 effective profile 與 `next_action` 接續，不重新套用預設值。

以 `workflow-profile.py parse` 解析 modifier。優先透過執行工具的 stdin 欄位把原始輸入直接傳給 `workflow-profile.py parse --stdin`；若呼叫端使用 argv，則把完整原文作為單一 `--input` argv。禁止把原文插入 shell command、shell 單引號或未受控 heredoc，避免 `$()`、`;` 與引號被 shell 解讀。新任務再執行 `workflow-profile.py plan --lane <lane> --mode <auto|standard> --profile <profile>`；接續任務執行 `workflow-profile.py plan --lane <state lane> --mode continue --state <state path>`。

## Profile gate

- **Full**：PM 凍結 → architect → reviewer → 獨立 QA → 獨立 PM。
- **Standard**：同一 verifier 凍結 → architect → reviewer → follow-up verifier 合併實測與驗收；不開 QA/PM。
- **Lite**：主 Codex 凍結 → architect 修改與自測 → verifier 實測與驗收；不開 reviewer/QA/PM。

L1 可用三種 profile；L2 至少 Standard；L3 強制 Full。只能升級，不能降級。security/design 等命中式專項 gate 一律保留。

## 凍結與自主邊界

所有 code 新任務都先把需求內必要細節、必要安全或正確性條件、可選改善分開呈現，由使用者確認後凍結。可選改善未獲明確同意，不得進 acceptance、code 或 blocker；`auto` 從凍結完成後才自主執行。

凍結後純內部且不改使用者可見行為、範圍、驗收、風險或成本的選擇可自主決定。新想到的可選改善只記錄；若完成任務必須改已凍結邊界，或涉及不可逆刪除、付費、資安、正式環境、需求矛盾，必須中斷並重新確認。每個 gate 轉換更新 state；最終回報逐條證據、commit、發布狀態、決策與最短複驗。
