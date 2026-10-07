# run-metrics v2

任務結束時以本地解析 Codex JSONL，量測 token、reasoning、agent spawn/reuse、fork、risk lane、驗收與 verdict。collector 同時支援現行 `event_msg.token_count`／`turn_context`／`sub_agent_activity`／`response_item.custom_tool_call` 與 legacy `message.usage`。

## 收集

```bash
python3 ~/.codex/scripts/collect-run-metrics.py \
  --slug <slug> --sessions <session-id> --risk-lane L1 \
  --requested-model gpt-5.6-sol --requested-effort medium \
  --acceptance-result pass --repair-count 0
```

路徑優先序是 `--codex-home`、`CODEX_HOME`、`~/.codex`，因此 `aiuse` profile 會從自己的 sessions/state 收集；可再用 `--sessions-root`／`--runs-dir` 覆寫。synthetic fixture 可直接重複傳 `--transcript`。`workflow_identity` 只讀取當前 `CODEX_HOME` Git repo 的 branch、HEAD、origin、managed dirty 與 fingerprint；不讀其他 checkout 或 source override，Git ignored runtime 不會污染身分。`CODEX_HOME` 不是 Git repo 或 origin 不可用時會寫入 `unavailable_reason`，不猜測版本；origin 只記錄去除 credential 的 canonical URL。

若要量測一個明確的 no-history child，而它的 transcript 仍帶有其他 agent activity event，需加 `--single-session-only`。這個選項只接受「精確一份、`history_mode=none`、非 main」的 transcript；不符合就 fail closed，避免把 parent tree counter 冒充單 agent 成本。

## Schema v2 重點

- `collector_status`: `ok|unsupported_schema|unsupported_token_attribution|incomplete_runtime`。任何 discovered transcript 缺 usage、混合 schema 都整筆 fail closed；spawn mismatch、missing child 或 failed spawn 也不會顯示成成功。多 transcript 的 agent/runtime 非 token 欄位仍保留，但 status 為 `unsupported_token_attribution`。報表逐筆顯示，只有 `ok + exact_single_transcript` 能進 token 平均；舊 v1 或缺 attribution 的舊 v2 run 都排除。
- `runtime.requested` 與 `runtime.effective_by_session`: requested 由 collector args 明記，effective 來自 turn context；main 不一致會寫 `runtime.warnings`。每個 spawn 另以 `call_id → started event → child transcript` 對應，保存 requested/effective model/effort 與 `matched|mismatch|missing_child|failed_spawn`。
- `--requested-effort` 可記 `low|medium|high|xhigh|max|ultra`；記錄能力不等於 routing 授權。此範本不啟用 Luna/max 作為預設 QA/test，Luna low/medium 也只是不自動啟用的未來候選。
- `tokens`: input、uncached/cached input、cache write、output、reasoning output。單一可解析 transcript 採最後 cumulative counter，標 `token_attribution.status=exact_single_transcript`。真實 parent/child counters 既不共享同一基準，也不能假設互相獨立，因此 discovered 或 explicit 多 transcript **不做 sum、max 或分帳推算**：`tokens.total=null`、`tokens_by_*={}`、status=`unsupported_token_attribution`。
- `token_observations`: 每份 transcript 的 path、session id、role、agent path、parent/history、effective context、時間範圍，以及原始 first/final counter。這些值只供稽核，不代表可互相比較或聚合。
- `agent_calls`／`agent_spawn_total`: 去重後的 started event；`reuse.follow_up_events` 與 reused thread；`fork.max_depth` 與 history mode 計數。取不到明確資料寫 `unknown`，不猜值。
- `risk_lane`、`acceptance_result`、`repair_count`、`outcome`、`verdict`: 共同判斷品質與流程成本。

## A/B

before/after 應各自派一個 `fork_turns=none` 的獨立量測 child，並各收它自己的 transcript：

```bash
--sessions <no-history-child-session> --single-session-only \
--experiment context-router --variant before
```

after variant 同樣另收另一個獨立 child。執行 `python3 ~/.codex/scripts/report-runs.py`；報表對非 exact run 的 token 顯示 `—` 並排除平均。exact run 才比較 input/cached/out/reasoning、agent spawn/reuse、repair、失敗率、驗收失敗率與 verdict；每個 variant 少於 5 筆會標「不可判讀」，不得宣稱節省百分比。A/B 執行 agent 使用 `gpt-5.6-terra / medium / fork_turns=none`，不繼承目前 main 的完整歷史。

## 限制

- 不同任務不可逐筆硬比，應用相似 low-risk fixture/任務與相同環境做 before/after。
- parent + child tree 可用來稽核 agent 派遣、reuse 與 requested/effective profile，不可用來宣稱總 token 或 agent 分帳。
- transcript schema 漂移時應新增 synthetic fixture，不能把真 session 內容 commit 進 repo。
- runtime 未保存 requested spawn 參數、call id 或 child transcript 時會 fail closed；orchestrator 應依 effort router 明確傳 model/effort，且不可移除 spawn/event correlation 資料。
