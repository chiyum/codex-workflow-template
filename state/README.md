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
  "profile_upgrade_reason": "L2 minimum profile is standard",
  "current_step": "2",
  "next_action": "run verifier",
  "status": "running",
  "updated_at": 1751800000
}
```

- `requested_profile` 保存使用者選擇；`effective_profile` 保存 lane 升級後實際 profile。未升級時 reason 是 `null`，升級時必須是非空字串。
- `$dev 繼續 <slug>` 執行 `plan --lane <state lane> --mode continue --state <path>`，沿用 effective profile 與 `next_action`；不得重新預設 Standard 或降級。
- 狀態可為 `running`、`awaiting_user`、`awaiting_next_batch`、`retry_exhausted`、`done`。跨批次另寫 handoff；白名單外但不阻擋的疑問寫 questions 檔。
- `session_id` 若使用，只能來自 runtime 直接提供的穩定值；不得掃描 transcript 猜測。
