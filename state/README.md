# State 制度

長任務用 `<task-slug>.json` 記錄 `task`、`product`、`current_step`、`next_action`、`status` 與 `updated_at`。狀態可為 `running`、`awaiting_user`、`awaiting_next_batch`、`done`。跨批次另寫 handoff，白名單外但不阻擋的疑問累積在 questions 檔。

