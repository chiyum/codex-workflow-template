# State 制度

長任務用 `<task-slug>.json` 保存 gate 與接續契約：

```json
{
  "task": "task-slug",
  "product": "sample_product",
  "cwd": "/absolute/path/to/repo",
  "lane": "L2",
  "mode": "auto",
  "requested_profile": "lite",
  "effective_profile": "standard",
  "profile_upgrade_reason": "change lane L2 floor=standard",
  "target": "dev",
  "product_policy_status": "known",
  "product_release_floor": "standard",
  "release_risk": {
    "target": "dev",
    "product_policy_status": "known",
    "gates": ["release_ancestor", "deployment_version"],
    "direct_prod_requires_confirmation": false,
    "fail_safe_full": false,
    "unavailable_reason": null
  },
  "effective_floor": "standard",
  "baseline_receipt_path": "/absolute/path/to/runtime-baseline-receipt.json",
  "baseline_receipt_identity": {
    "receipt_sha256": "<sha256>",
    "collector_sha256": "<sha256>",
    "identity_sha256": "<sha256>",
    "product": "sample_product",
    "target_environment": "dev",
    "repos": []
  },
  "current_step": "2",
  "next_action": "verifier 本地驗證 A3-A5",
  "status": "running",
  "updated_at": 1751800000
}
```

- `cwd`、`lane`、`requested_profile`、`effective_profile` 與 `profile_upgrade_reason` 是 change plan 接續契約；`requested_profile` 保存使用者選擇（未指定時為 lane floor），reason 必須等於 resolver 的確定性輸出，不能存自由字串，未升級時為 `null`。
- `target`、`product_policy_status`、`product_release_floor`、完整 `release_risk` gates 與 `effective_floor` 是 release overlay identity。接續時從 state 還原並重算比對，不得套用 CLI 預設或清空 release gates。
- `baseline_receipt_path` 指向 runtime receipt；`baseline_receipt_identity` 保存 receipt／collector／repo identity hash 與使用者確認 identity。receipt 不存在、不新鮮、hash 不符或 identity 漂移時，code 寫入 gate 一律保持 blocked。receipt 與機密不得寫進 repo。
- `$dev 繼續 <slug>` 執行 `plan --lane <state lane> --mode continue --state <path>` 載入完整 plan identity，沿用 effective profile 與 `next_action`；continue 禁止用 CLI 覆寫 profile、target、product policy/floor 或 baseline receipt，也不得降級。
- 狀態語義：
  - `running`：進行中；可同時有 D2／D3 待決，只要仍有可證明獨立的已核准工作，`next_action` 指向該工作與 decision log。
  - `awaiting_user`：無安全獨立工作、主要後續工作受阻，或平台只能整體暫停時才使用；不能只因一筆非阻塞待決就整案標記。
  - `awaiting_next_batch`：本批完成，等待接續。
  - `retry_exhausted`：同一階段的重試額度耗盡。
  - `done`：必做需求與必要 gate 全部完成；仍有必做待決或驗證缺口不得使用。
- `session_id` 若使用，只能來自 runtime 直接提供的穩定值；不得掃描 transcript 猜測。

## 配套檔（同目錄、同 slug）

- `<task>-handoff.md`：規劃與執行共用交接檔；規劃收斂即建立，凍結、交棒、換模型及批次結束時更新。保存需求上下文、前因後果、證據、決策理由、驗收來源（以絕對路徑與 A 編號引用凍結清單）與建議做法；不另建互相競爭的 plan，也不構成第二份 acceptance。尚未凍結時只存草案，不為填格式杜撰 state 的 lane 或 baseline confirmation。
- `<task>-questions.md`：決策與待決紀錄，格式見 `workflows/DECISION_LOG.md`；原範圍外建議另標，不混成必做。

## 非阻塞待決與恢復

D2 先保存紀錄，再把 `next_action` 設為具名獨立必做工作並實際執行；D3 即時詢問，能獨立的工作仍繼續，只有相依部分等核准。每次開始相依工作、階段結束、依賴／範圍改變或收到資訊與核准時重查；D2 成為主線前置、無法隔離或無其他獨立必做工作時升 D3。接續先讀原規格版本、state、questions 與必要 handoff，再核對程式、配置、核准及有效證據；不把過期待決當已決、不重問已核准、不重做已驗證項。平台必須整體暫停時保存可續作項目，不聲稱背景開發中。
