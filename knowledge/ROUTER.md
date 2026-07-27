# 工程知識庫短路由

日常 agent 只讀本檔，再讀命中的 playbook；不要完整載入大型 `INDEX.md`。沒有命中 playbook 時，以 `rg -n '<技術|問題類別>' ~/.codex/knowledge/INDEX.md ~/.codex/knowledge/*.md` 找 1–3 張卡。`INDEX.md` 僅供 retro、全域盤點與新增卡更新索引。

| 技術域／訊號 | 先讀 |
|---|---|
| Git history、公開發布、脫敏、scanner | `playbooks/playbook-safe-publishing.md` |
| acceptance、evidence、gate | `acceptance-gate-must-assert-outcome.md` |
| mutation、rollback、fixture cleanup | `mutation-restore-must-be-scoped.md` |
| Bash、macOS、全形字元、unbound variable | `bash-variable-before-nonascii-needs-braces.md` |

新增知識卡或 playbook 時仍依 `INDEX.md` 開頭的成長規則同步索引；本 router 只在新增技術域 playbook 時加一列。
