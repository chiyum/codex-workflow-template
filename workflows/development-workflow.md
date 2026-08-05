# Development Workflow

## 0. 風險路由與凍結需求

先執行 `scripts/workflow-profile.py parse`、`lane` 與 `plan`，不得只憑文字感覺決定流程。L1 必須同時滿足：1–3 條 acceptance、單 repo、可回滾、目標 local/dev，且未命中高風險 deny list；auth、權限、租戶、DB、migration、資料一致性、跨 repo contract、基礎設施、金流、不可逆、prod、重大架構或全新視覺任一命中即 L3，其餘為 L2。

L1 可保留 Lite/Standard/Full，L2 至少 Standard，L3 強制 Full。requested profile 只可保留或升級。所有 code 新任務都先由使用者確認凍結；`auto` 不略過步驟 0，只表示凍結後在已確認範圍內自主執行，不改 profile。

Full 由 PM 產出 acceptance，Standard 由 verifier 產出，Lite 由主 Codex 產出。凍結前先把細節分成「需求內必要細節／必要安全或正確性條件／可選改善」呈現；可選改善未獲使用者明確同意，不得進 acceptance、code 或 blocker。必要安全或正確性條件須說明原因與影響後一起確認。

清單呈現給使用者確認後凍結，開發期間任何 agent 不得修改；純讀取／非 code 修改可略過。大型自主工作再加任務憲章，列出範圍、非目標、細節決策、預授權決策與必問白名單。`$rapid` 只有明確觸發才使用，不能由 L1 或 Lite 自動切換。

## 1. architect 實作

architect 先讀產品配置與 knowledge 索引，再判斷：

- 小／中改：說明思路後直接實作。
- 大改：比較三個真正不同的方案，包含架構、範圍、優缺點、風險、回滾與測試。凍結後的自主模式只有在推薦方案不改使用者可見行為、範圍、驗收、風險或成本時才可採用並記錄理由；超出邊界須回到步驟 0 確認。

實作必須同批更新測試、受影響規格與達 ADR 門檻的決策。只有 architect 寫產品程式碼，完成後以清楚的 commit message 提交。

凍結後純內部且不改使用者可見行為、範圍、驗收、風險或成本的實作選擇可自主決定。新想到的可選改善只記錄，不得實作或設為 blocker；從需求推衍、且會改變資料可見範圍、權限／篩選、預設值或 UI 行為的決策，必須另經使用者確認。

每次派遣前用 `scripts/workflow-profile.py resolve` 取得 model、effort 與 bounded/no-history fork。main 預設 Sol/medium；test/QA 採 Terra low/medium；`xhigh` 只限先說明理由的 L2/L3 單一 architect/reviewer 葉。Luna/max 不自動啟用。同任務退修優先 follow-up 重用原 agent，只傳 finding、diff 與新證據。

## 2. pre-review 與核心審查

L1 執行受影響範圍的 targeted lint、build、test 與 secret/diff scope gate；L2/L3 執行完整 `scripts/pre-review.sh`。確定性檢查失敗直接回 architect，不占 reviewer 回合。

Full/Standard 再由 reviewer 唯讀檢查正確性、安全、資料一致性、邊界與測試鑑別力；Lite 不開 reviewer，architect 完成自測與預檢後直接進 verifier。嚴重／一般問題退修，建議不阻擋。

三次停止規則只適用於沒有新證據、沒有狀態改變而原樣重做的同一操作或方法。architect 依 reviewer、QA、PM 或 verifier 意見做實質修正後重新送審，是正常開發迭代，不設輪數上限。

## 3. 本地驗證 gate

- Full：獨立 QA 實測，再由獨立 PM 只用凍結清單驗收。
- Standard：follow-up 同一 verifier 合併實測與驗收，不開 QA/PM。
- Lite：verifier 在 architect 自測後實測與驗收，不開 reviewer/QA/PM。

每條 `A<n>` 至少一份落地證據，主 Codex 執行 evidence gate 並抽驗關鍵證據。security/design 等命中式專項 gate 始終保留，不受 profile 角色精簡影響。

## 4. 發布與環境驗證

只有 plan 指定的所有本地 gate 全綠才可依產品配置 push。部署後 follow-up 原 QA 或 verifier 驗證環境。部署逾時分類為部署問題；正式環境操作必須有明確授權。

## 5. 收尾

回報前執行 `collect-run-metrics.py`。多 transcript tree 只保留逐 session raw observations，不將不相容 counters 做 sum/max 或分帳；只有單一 no-history transcript 的 exact run 才可比較 token。回報驗收結果、證據位置、commit、push／部署狀態、已知限制，以及三步內的一分鐘複驗。任一 gate 未過就回到 architect；不可用「大致完成」代替通過。

## 流程圖

```text
Full:     PM → architect → reviewer → QA → PM
Standard: verifier → architect → reviewer → verifier
Lite:     main → architect 自測 → verifier
                       ↓
             evidence gate → push → 環境驗證 → 回報
```
