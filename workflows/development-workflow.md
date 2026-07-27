# Development Workflow

## 0. 風險路由與凍結需求

先執行 `scripts/workflow-profile.py lane` 與 `plan`，不得只憑文字感覺決定流程。L1 必須同時滿足：1–3 條 acceptance、單 repo、可回滾、目標 local/dev，且未命中高風險 deny list；auth、權限、租戶、DB、migration、資料一致性、跨 repo contract、基礎設施、金流、不可逆、prod、重大架構或全新視覺任一命中即 L3，其餘為 L2。

L1 由主 Codex 保存使用者原始需求並凍結 1–3 條「驗證步驟＋預期結果」；L2/L3 由 PM 產出並凍結。大型自主工作再加任務憲章：範圍、非目標、預授權決策、必問白名單。凍結後所有 agent 只能照清單做；新需求另開變更。`$rapid` 只有明確觸發才使用 `rapid-change-workflow.md`，不得從 L1 自動切換。

## 1. architect 實作

architect 先讀產品配置與 knowledge 索引，再判斷：

- 小／中改：說明思路後直接實作。
- 大改：比較三個真正不同的方案，包含架構、範圍、優缺點、風險、回滾與測試；自主模式採推薦方案並記錄理由。

實作必須同批更新測試、受影響規格與達 ADR 門檻的決策。只有 architect 寫產品程式碼，完成後以清楚的 commit message 提交。

每次派遣前用 `scripts/workflow-profile.py resolve` 取得 model、effort 與 bounded/no-history fork。main 預設 Sol/medium；test/QA 採 Terra low/medium；`xhigh` 只限先說明理由的 L2/L3 單一 architect/reviewer 葉。Luna/max 不自動啟用。同任務退修優先 follow-up 重用原 agent，只傳 finding、diff 與新證據。

## 2. pre-review 與 reviewer

L1 執行受影響範圍的 targeted lint、build、test 與 secret/diff scope gate；L2/L3 執行完整 `scripts/pre-review.sh`。確定性檢查失敗直接回 architect，不占 reviewer 回合。

reviewer 唯讀檢查：正確性、安全、資料一致性、併發、錯誤處理、邊界與測試是否真的能失敗。嚴重／一般問題退修，建議不阻擋。

三次停止規則只適用於沒有新證據、沒有狀態改變而原樣重做的同一操作或方法；連做三次仍得到相同結果時，停止該無進展動作並重新分析、增加觀測或換方法。失敗指紋只追蹤驗收結果、根因與證據，不是退修上限。architect 依 reviewer、QA 或 PM 意見做實質修正後重新送審，是正常開發迭代，不論幾輪都持續到 gate 通過，不得凍結整個 finding 或任務。

## 3. 本地 QA／PM gate

QA 實跑 API、UI、整合與高風險反例。每條 `A<n>` 至少一份落地證據，證據檔名含條目編號。L1 由主 Codex 只稽核 QA 證據；L2/L3 由獨立 PM 只用凍結清單判定。主 Codex 執行 evidence gate，並親自抽驗關鍵證據。大型改動再由反方 PM 嘗試證明功能未完成。

## 4. 發布與環境驗證

只有本地 reviewer、QA、PM 全綠才可依產品配置 push。部署驗證若逾時，分類為部署問題，不當作程式錯誤反覆改 code。正式環境操作必須有明確授權。

## 5. 收尾

回報前執行 `collect-run-metrics.py`。多 transcript tree 只保留逐 session raw observations，不將不相容 counters 做 sum/max 或分帳；只有單一 no-history transcript 的 exact run 才可比較 token。回報驗收結果、證據位置、commit、push／部署狀態、已知限制，以及三步內的一分鐘複驗。任一 gate 未過就回到 architect；不可用「大致完成」代替通過。

## 流程圖

```text
原始需求 → PM 凍結 → architect → pre-review → reviewer
                                      ↑              │
                                      └────退修──────┘
reviewer PASS → QA + PM + evidence gate → push → 環境驗證 → 回報
```
