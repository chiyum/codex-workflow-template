# Development Workflow

## 任務入口

先辨識使用者要的是解說、唯讀診斷、文件修改或已授權實作。產品名稱、repo、URL 或 port 只協助定位，不等於修改授權；引文、歷史或測試案例中的產品也不自動成為目標。只讀本次命中的產品配置、規格與 playbook，不展開無關產品與完整歷史。

## 0. 風險路由、基準與凍結需求

先執行 `scripts/workflow-profile.py parse`、`lane` 與 `plan --target <local|dev|prod|unknown>`，不得只憑文字感覺決定流程。L1 必須同時滿足：使用者需求邊界明確、1–3 條 acceptance、單 repo、可回滾，且未命中高風險 deny list；auth、權限、租戶、DB、migration、資料一致性、跨 repo contract、基礎設施、金流、不可逆、重大架構或全新視覺任一命中即 L3，其餘為 L2。local/dev/prod 是 release risk，只增加 release overlay 與 gate，不改寫 change lane；缺少或未知產品 release policy 的 direct prod fail-safe Full，且仍須使用者確認。

未明示 profile 時依 L1→Lite、L2→Standard、L3→Full；明示 profile 只可保留或升級，effective floor 取 change lane floor 與產品 release floor 較高者。既有 `$dev auto <需求>` 向後相容為 Standard。所有 code 新任務都先由使用者確認凍結；`auto` 不略過步驟 0，只表示凍結後在已確認範圍內自主執行，不改 profile。

開始討論前先用 `scripts/development-baseline.py` 產生 fresh baseline receipt：命中產品、每個 repo 的 branch／local HEAD／dirty／untracked／remote HEAD／擬採基準，以及目標環境 deployed version 或 unavailable 原因。receipt 綁定 collector、時間與內容 hash，使用者確認逐 hash 綁定；任一差異都要明示並等使用者確認，在此之前 code 寫入一律 blocked，不能以自由字串宣告 confirmed。

Full 由 PM 產出 acceptance，Standard 由 verifier 產出，Lite 由主 Codex 產出。技術 receipt 之前先給使用者白話凍結摘要：「真正要解決的是 X；Y 維持不變；用 Z 驗收；以下是額外推論／可選改善，是否納入？」每條 acceptance 標明來源：`原始要求`、`討論後明確確認`、`完成 A 不可缺少的正確性條件`，或 `Codex 推論／可選改善（使用者已確認）`。未確認的推論只放待確認區，不得進 acceptance、code 或 blocker；「必要」不得被拿來加入相鄰功能、一般資安加固或其他 B。build／lint／本次 diff 測試、pre-review 與部署檢查是工程 gate，不冒充產品 acceptance。

清單呈現給使用者確認後凍結，開發期間任何 agent 不得修改；純讀取／非 code 修改可略過。大型自主工作再加任務憲章，列出範圍、非目標、細節決策、預授權決策與必問白名單。`$rapid` 只有明確觸發才使用，不能由 L1 或 Lite 自動切換。

規劃收斂、凍結交棒、換模型或跨 session 續作時，主動在 `state/<task>-handoff.md` 保存需求上下文、前因後果、證據、決策理由、驗收來源與建議做法；接手者先讀取、核對基準並回述目標／邊界／第一步。尚未凍結者只保存草案，不偽造已確認 acceptance 或 state。

## 1. architect 實作

architect 先讀產品配置與 `knowledge/ROUTER.md`（查找與合併規則見 `knowledge/README.md`），再判斷：

- 小／中改：說明思路後直接實作。
- 大改：比較三個真正不同的方案，包含架構、範圍、優缺點、風險、回滾與測試。分析深度不決定人工核准：凍結後方案依 `workflows/DECISION_LOG.md` 的 D1／D2／D3 分流。

實作必須同批更新測試、受影響的可維護實作文件與達 ADR 門檻的決策；凍結的外部規格本體不因同步實作文件而改寫。缺少適用規格、來源不可讀或版本衝突時回報缺口與受影響需求，只停相依部分，不自造規格。只有 architect 寫產品程式碼，完成後以清楚的 commit message 提交。

凍結後依 D1／D2／D3 推進：D1（局部、可回退、不衝突凍結承諾的錯誤處理、防護與合理預設值）由相關角色協作、主 Codex 收斂記錄後實作；D2 記錄依賴並轉做其他已核准的獨立需求；D3（核心架構或凍結承諾變更、越權、主線受阻）提出證據與方案詢問，只停相依工作。資料／租戶／權限邊界與核准的核心、介面、流程承諾不得自主改動；相鄰可選改善只記錄，不實作也不設 blocker。

**語意偏移檢查點**：architect 每完成一條功能路徑或重大退修，固定自問「現在解的仍是原始問題嗎？每個新行為對應哪條需求？有沒有碰到非目標？有沒有新增未確認的假設？」答案交主 Codex 依 D1／D2／D3 處理，不得放寬驗收或順修相鄰問題。

每次派遣前用 `scripts/workflow-profile.py resolve` 取得 model、effort 與 bounded/no-history fork。main 使用使用者當前 session 選定且實際生效的 model/effort；子 agent 依路由矩陣派遣：test/QA 採 Terra low/medium；`xhigh` 只限先說明理由的 L2/L3 單一 architect/reviewer 葉。Luna/max 不自動啟用。同任務退修優先 follow-up 重用原 agent，只傳 finding、diff 與新證據。

## 2. pre-review 與核心審查

所有 lane/profile 都以 diff/acceptance＋最近必要 sentinels 切分 formatter、lint、build 與 test，再執行 staged secret/diff scope、`scripts/pre-review.sh`、release 與產品硬 gate。Full/L3 不等於獲准跑全站測試；工具無法切分且廣泛執行有實質成本時先問使用者。確定性檢查失敗直接回 architect，不占 reviewer 回合。

Full/Standard 再由 reviewer 唯讀檢查正確性、安全、資料一致性、邊界與測試鑑別力；Lite 不開 reviewer，architect 完成自測與預檢後直接進 verifier。reviewer 全報並分級，但只有本次 diff 引入、凍結 A 未完成或 A 直接路徑不正確者可列嚴重／一般並退修；既存且未被惡化的問題、相鄰 B、未命中的資安加固或廣泛測試一律列建議，不阻擋 A。

一般資安測試、掃描、加固與 security-auditor 預設關閉；只有使用者明確要求，或產品／工作流既有硬 gate 被本次實際變更面（對外 port、反向代理、container、部署 pipeline、DB/Redis 暴露面）命中時才啟動，且須在步驟 0 白話列入。auth／permission／tenant diff 由 reviewer 檢查 A 直接路徑的功能正確性，不因此啟動獨立資安稽核；任何對外主動掃描都須先取得授權。

**推理衛生**：使用者反駁、測試轉紅或工具失敗使方向可能改變時，先分類為 `evidence_changed`（新 log／重現／反證）、`state_changed`（程式／環境／設定改變）、`decision_changed`（使用者明確改需求、優先順序、風險或成本選擇）或 `pressure_only`（只有語氣、催促或重複詢問）。前兩者可更新技術判斷；`decision_changed` 影響凍結內容時回步驟 0；`pressure_only` 可觸發一次有目的的重查，但不得單獨翻轉結論、放寬 gate 或改寫 acceptance。每次失敗標記 `closer／same／worse`，前兩次皆為 `same／worse` 時，下一次必須先寫明要取得的新資訊。

三次停止規則只適用於沒有新證據、沒有狀態改變而原樣重做的同一操作或方法。architect 依 reviewer、QA、PM 或 verifier 意見做實質修正後重新送審，是正常開發迭代，不設輪數上限。

## 3. 本地驗證 gate

- Full：獨立 QA 實測，再由獨立 PM 只用凍結清單驗收。
- Standard：follow-up 同一 verifier 合併實測與驗收，不開 QA/PM。
- Lite：verifier 在 architect 自測後實測與驗收，不開 reviewer/QA/PM。

測試範圍預設 targeted：只測凍結 A 的正常／直接失敗流程、本次 diff 與實際改到的共用面最近必要哨兵；只有使用者明確要求或產品硬 gate 逐字規定時才跑全站回歸。

每條 `A<n>` 依 profile 落地各自角色證據；條目宣告不可替代的 environment／viewport／interaction／data／execution path 時，角色 receipt 必須記錄實際值與 delta，任一 delta 即 FAIL/BLOCKED。主 Codex 執行 `scripts/verify-evidence.sh <清單> <effective-profile>`，未知 owner、零證據或不符 receipt 都不放行；通過後優先抽驗限制最多或失敗代價最高的 1–2 條。security/design 等命中式專項 gate 始終保留，不受 profile 角色精簡影響。

PM／verifier 判定 A 的 PASS／FAIL 後，另做**需求忠實度判定**：獨立比較原始需求、使用者後續明確決定、凍結 acceptance 與實際 diff／使用者流程，明答原始問題是否解決、有無少做或多做、有無未確認假設。acceptance 本身已偏離原始需求時必須 FAIL／BLOCKED 並回步驟 0，不得驗收後改清單合理化。

## 4. 發布與環境驗證

發布依本次適用授權執行；本次明確限本地或禁止 commit／push／部署時，保留必要本地 gate、交付可檢閱 diff，不得宣稱環境驗收完成。只有 plan 指定的所有本地 gate 全綠才可依產品配置 push。部署後 follow-up 原 QA 或 verifier 驗證環境。部署逾時分類為部署問題；正式環境操作必須有明確授權。

## 5. 收尾

回報前執行 `collect-run-metrics.py`。多 transcript tree 只保留逐 session raw observations，不將不相容 counters 做 sum/max 或分帳；只有單一 no-history transcript 的 exact run 才可比較 token。回報驗收結果、證據位置、commit、push／部署狀態、已知限制，以及三步內的一分鐘複驗。任一 gate 未過就回到 architect；不可用「大致完成」代替通過。

回報已完成功能時另附：

- **改動前 vs. 改動後**：按功能路徑分組，每側先白話再專業術語，交代使用者流程或結果、資料／控制流程、效能與延遲、系統負荷，以及一致性、失敗模式或維運影響；不適用者可略過但要說明。數字只能引用本次實測證據，未量測就標「方向性影響（未量測）」。
- **我幫你做的決定**：凍結後在授權範圍內自主作成、有實質影響的決策，逐項寫決定、原因、未採方案、優點與缺點／代價；沒有則寫「無」。
- **非阻擋建議**：彙整去重各角色的建議，逐項列來源、內容、與 A 的關係及本次未處理原因；沒有則寫「無」。建議不得靜默實作或省略不報。

## 流程圖

```text
Full:     PM → architect → reviewer → QA → PM
Standard: verifier → architect → reviewer → verifier
Lite:     main → architect 自測 → verifier
                       ↓
             evidence gate → push → 環境驗證 → 回報
```
