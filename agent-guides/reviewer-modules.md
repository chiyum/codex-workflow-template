# Reviewer 領域模組（只按 diff 命中載入）

## security-and-tenancy

- 外部輸入在系統邊界驗證；SQL injection、XSS、SSRF、path traversal 與 command injection 有防護。
- JWT/token 的簽章、期限、撤銷與敏感 log 正確；權限不能只靠前端隱藏。
- 每個 query/write/cache key/event 都保留 tenant scope；換帳號、租戶或 instance 的反例必須失敗關閉。
- 機密不進 repo、輸出、錯誤或 metrics；不可逆與付費操作具備確認、冪等與 terminal-state audit。
- 本模組由 auth、permission、tenant 或 external input 程式碼 diff 命中，只是 reviewer 的 code-security 審查；不因此單獨啟動 security-auditor。

## database-and-consistency

- migration 序列、up/down、既有資料、nullable/default/backfill 與部署順序相容；不得假設 db push 會執行 data migration。
- 多步寫入在同一 transaction；unique/FK/lock/deadlock/重試後語義正確，部分成功不被宣稱全失敗。
- 批次修改、排序或 replace 不會刪掉 concurrent update；資料 identity 不從 row order 或模糊欄位猜測。
- 讀寫來源一致；replica lag、timestamp/timezone 與 driver type map 有明確處理。

## redis-cache-multi-instance

- `SADD/HSET` 的主資料有 TTL 或 active cleanup；`KEYS *` 不允許，集合全量讀與 N+1 有界。
- cache 同時具備 TTL 與主動 invalidation；所有改變列表結果的入口一致清除，多 instance 不只清本機。
- Pub/Sub subscriber 有 reconnect；重複、遺漏、倒序與 subscriber 中斷有補償或安全語義。
- distributed lock 有 owner/token、期限與 compare-and-release；worker 並行不破壞順序。

## async-race-push

- await 前 capture session/user/room/epoch，回來後比對 current；cancel/teardown 不會清掉新 owner 的 state。
- fire-and-forget 有 durable/observable 補償；queue per-item 失敗不毒化整批，也不把失敗誤標成功。
- WebSocket buffer/backpressure、重連、重播、dedupe、ordering 與多 instance broadcast 有證據。
- timeout 預算由外到內遞減；goroutine/task 有生命週期、panic/error 可達能處理的角色。

## frontend-state-and-environment

- source of truth 唯一；async response 不覆蓋更新後 draft/session，cache/visibility/重整後重新同步。
- 表單保留 raw draft 與提交時 normalize；未知 option、分頁外 id、IME、空字串/null 有測試。
- DOM locator、popup/autoplay、iOS/viewport/overflow 與 browser security 邊界符合真實使用路徑。
- 隨環境選值預設 map + fallback；第三環境不需修改多處，build artifact/version 已驗證。

## contract-and-field-semantics

- API/event/JSON 欄位、錯誤碼、序列化與所有 producer/consumer 同步；跨 repo 版本與回滾順序明確。
- 一欄位一語義；view-only metadata 放 view DTO，不污染多消費者共用 model。
- 共用 params 的新載重欄位已 grep 所有 producer；零值/缺值不會靜默改變策略。
- simulator/dry-run/rewrite route 使用真實 dispatch/contract，不維護容易漂移的鏡像。

## infrastructure

- 對外 port、bind address、TLS、proxy header、origin lock、secret/env 與 log 暴露面最小。
- image/tag、volume、migration、health/readiness、rollback 與 version endpoint 對應本次 commit。
- Docker/防火牆/反代不互相繞過；pipeline 的檢查確實接線且失敗會阻擋部署。
- 只有 port、proxy、container、pipeline 或 DB/Redis 暴露面變更才命中獨立 security auditor；小 UI 即使 target=prod 也不命中。主動對外掃描必須先取得使用者確認。
- 命中上述基礎設施暴露面時工作流為 L3，獨立 security auditor 做環境審查；reviewer 不代替該 gate。

