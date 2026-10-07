---
name: rapid
description: 明確觸發的 local/dev 現場急修快線；不適用 production、migration、權限、金流、資安、基礎設施、跨 repo contract 或重大架構。
---

# rapid

只有使用者明確輸入 `$rapid`、`$rapid 繼續 <slug>` 或從面板選擇時使用。完整讀取 `~/.codex/workflows/rapid-change-workflow.md`，以使用者原文為需求來源，所有 state/acceptance/evidence 只寫 `~/.codex/rapid/`。

- 不套 `$dev` 五步驟，也不因自然語言中的「緊急／Bug／小改」自動觸發。
- architect 唯一 code 寫入；先基準、再最小修補、targeted pre-review、單一 reviewer/QA、PM evidence audit。
- model/effort 由 `workflow-profile.py` 明確解析；退修與 local→dev 優先 follow-up 原 agent，只傳 delta。
- 任一 deny 條件命中就標 `not_eligible`，說明原因並停止，不靜默切 workflow。
- architect 以一至兩行如實標記 `root_fix／containment／workaround／external_limitation`；不得把暫時控制或繞路宣稱為根因已修復。
- 完成回報 slug、逐條證據、commit、發布/版本、rollback 與三步複驗；另交代改動前／後的白話與技術差異、實際影響及自主決策利弊，量化成果只引用本次實測；未發布必須直說。

