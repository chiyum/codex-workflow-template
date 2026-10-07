# Acceptance 制度

每個任務一份凍結清單，每條格式如下：

```markdown
### A1 行為名稱
- 需求來源：原始要求
- 驗證步驟：使用者可重現的操作或命令
- 預期結果：可觀察且可判定的結果
```

- 標題行必須以 `### A<n> ` 開頭，`verify-evidence.sh` 靠此解析。
- `需求來源` 只可填：`原始要求`、`討論後明確確認`、`完成 A 不可缺少的正確性條件`、`Codex 推論／可選改善（使用者已確認）`。未確認的推論或可選改善放白話摘要的待確認區，不得寫成 `A<n>`。
- 驗證步驟寫明 URL、帳號引用（不寫明文）與具體操作；驗法在凍結時就定案。
- environment、viewport、interaction、data 或 execution path 不可替代時，在條目內加一行 `- 不可替代驗法: environment=<...>; viewport=<...>; interaction=<...>; data=<...>; execution_path=<...>`，只寫真正必要的欄位。

## 白話凍結摘要（呈現技術 receipt 前先給使用者看）

```markdown
## 白話凍結摘要
- 真正要解決的問題: <X>
- 這次要完成: <A>
- 維持不變／這次不做: <Y；含相鄰 B、一般資安稽核或未要求的廣泛回歸>
- 驗收方式: <A 的正常流程、直接失敗流程、build，以及實際改到的共用面最近必要哨兵>
- 額外推論／可選改善: <無／逐項列出，標明待使用者決定>
```

正式 acceptance 只收本次產品需求 A。build／lint／本次 diff 測試、pre-review 與部署檢查另列工程 gate；相鄰 B、一般資安測試／掃描／加固與未命中的廣泛回歸不得加入，除非使用者明確要求，或既有硬 gate 已在凍結前白話揭露並由實際變更面命中。

## 任務憲章

大型或自主工作再加入以下任務憲章，並與 acceptance 放在同一份凍結清單：

```markdown
## 任務憲章（凍結）
- 範圍: <這次要動的>
- 非目標: <明確不做的；相鄰但未確認的對外行為也列入>
- 細節決策:
  - 需求內必要細節: <已確認內容>
  - 完成 A 不可缺少的正確性條件: <原因、影響與已確認內容；不得放相鄰功能或一般資安加固>
  - 使用者已同意的可選改善: <無／逐項列出>
- 預授權決策: <依 workflows/DECISION_LOG.md 分流；D1 可補全不衝突凍結承諾的局部錯誤處理、防護與合理預設值，不改資料／租戶／權限邊界或已核准的核心、介面、流程承諾>
- 必問白名單: <未授權不可逆刪除、付費、主動資安掃描、正式環境、核心架構或凍結承諾衝突、越過權限或資料邊界、待決已阻塞主線>
- 提問政策: <questions 檔記 D1/D2/D3、角色引用、依賴與升級條件；範圍外可選改善不實作、不列 blocker>
- 語意偏移檢查: <每條功能路徑／重大退修核對原問題、需求、非目標與新增假設>
```

## 證據

- QA／PM／verifier 每驗一條都把證據放在任務 evidence 目錄，檔名以 `<role>-A<n>-` 開頭，例如 `qa-A1-畫面.png`、`pm-A1-對照.md`、`verifier-A2-curl.txt`。
- 主 Codex 放行前執行 `bash scripts/verify-evidence.sh <清單檔> <effective-profile>`：Full 每條都須有 QA 與 PM 各自 artifact；Standard/Lite 每條由 verifier 提供。未知 owner 前綴或任一缺件都不放行。
- 命中「不可替代驗法」時，角色另寫 `<role>-A<n>-receipt.json`（schema `evidence-receipt/v1`），記錄 `expected`、`actual`、`deltas` 與 `PASS|FAIL|BLOCKED`；任一 delta 必須 FAIL/BLOCKED。receipt 不得包含 token、password、cookie 或原始機密 payload。
- 腳本通過後，主 Codex 仍親自抽驗限制最多或失敗代價最高的 1–2 條；抽驗不符則該輪驗收整批作廢重驗。

## 凍結規則

所有 code 新任務都先由使用者確認凍結；`auto` 只從凍結後開始自主執行。凍結後任何 agent 不得修改清單本體，evidence 目錄可隨時新增。清單用於判定 A 的 PASS／FAIL；architect 更新的規格只供參考。PM／verifier 另做需求忠實度判定，清單若已偏離原始需求必須 FAIL／BLOCKED 並回步驟 0，不得用錯誤清單合理化多做的 B。需求中途變更由使用者明示後重新凍結，舊清單標記 superseded。規劃上下文與建議做法存於 `state/<task>-handoff.md`，不構成第二份 acceptance。
