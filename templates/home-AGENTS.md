# Codex 專案路由

本目錄的唯一全域規則來源是 `~/.codex/AGENTS.md`；開始任何工作前先讀該檔，不在本檔複製流程或角色 SOP。

- 標準開發：依 `~/.codex/workflows/development-workflow.md` 命中段落執行，model/effort 與 L1–L3 由 `~/.codex/workflows/effort-routing.md` 及 `~/.codex/scripts/workflow-profile.py` 決定。
- 明確 `$rapid`：改讀 `~/.codex/workflows/rapid-change-workflow.md`；自然語言提到小改、Bug 或緊急不得自動切換。
- 產品：先讀 `~/.codex/products/INDEX.md` 與命中的產品配置。
- 工程知識：先讀 `~/.codex/knowledge/ROUTER.md`，再按需讀 playbook／卡片；不要完整載入大型 INDEX。
- 角色：只在派遣該角色時讀 `~/.codex/agent-guides/<role>.md`。
- `~/.claude/` 永遠唯讀；新增或更新只寫 `.codex` 或使用者指定 repo。

若本檔與 `~/.codex/AGENTS.md` 或命中 workflow 衝突，以後兩者為準並修正本 router，不建立第二份流程真相。

