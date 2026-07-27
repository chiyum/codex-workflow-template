# Model／effort 與風險路由（唯一政策來源）

主 Codex 先以 `python3 ~/.codex/scripts/workflow-profile.py lane ...` 決定 lane，再以 `plan --lane <lane> --mode <auto|standard|continue>` 取得 acceptance owner、PM spawn 數與 pre-review gate；任何 agent spawn 前再以 `resolve` 取得明確 `model`、`reasoning_effort` 與 `fork_turns`。不得只靠自然語言暗示降級。custom agent TOML 不固定 model/effort，避免覆蓋 explicit spawn；`config.toml [agents]` 的 `high` 只是不經 router 時的 fail-safe。

## 風險 lane

- `L1 low-risk`：只有明確標成候選、1–3 條 acceptance、單 repo、可回滾、目標為 local/dev，且未命中 deny list 才成立。
- `L2 standard`：未命中高風險，但不滿足 L1 全部條件。
- `L3 high-risk`：auth、權限、租戶、DB、migration、資料一致性、跨 repo contract、基礎設施、金流、不可逆、prod、重大架構、全新視覺任一命中即強制進入。
- `$rapid` 仍只接受明確觸發；L1 絕不自動切 rapid。

## Profile 矩陣

| lane／角色 | model | effort | history |
|---|---|---|---|
| main | `gpt-5.6-sol` | `medium` | parent |
| L1 architect/reviewer | `gpt-5.6-sol` | `medium` | bounded 3 turns |
| L2 architect/reviewer | `gpt-5.6-sol` | `high` | bounded 3 turns |
| L1–L3 QA／test 執行 | `gpt-5.6-terra` | `medium` | none |
| L1–L3 純 evidence 整理（L1 audit 仍由 main 直接完成） | `gpt-5.6-terra` | `low` | none |
| L3 architect/reviewer/PM/UI 判斷角色、security auditor | `gpt-5.6-sol` | `high` | bounded 3 turns |

`xhigh` 只允許 L2/L3 的單一 architect/reviewer 葉，spawn 前必須告知使用者「為何需要深層單線推理」，並把理由傳給 `--xhigh-reason`。不得讓整個 session 或平行 agents 一起升級。

## Luna route 邊界

預設 test/QA **保留 Terra low/medium**，不自動採 Luna/max。公開範本不附帶特定帳號或單次環境的 benchmark 結論；任何替換都必須先以 matched、可執行、相同 sandbox 的 A/B 證據驗證。

Luna low/medium 只列為未來可重新 benchmark 的 candidate；不得因 Terra 失敗、usage limit、unsupported 或 profile 登入狀態不明而自動切換。任何新增 Luna route 都必須有使用者拍板。collector 可記錄 `max|ultra` requested effort，僅代表遙測相容，不代表 router 允許自動使用。

## Reuse 與遙測

- 同任務退修用既有 architect/reviewer 的 follow-up，只傳 finding、diff/commit 與新增證據；local→dev 沿用同一 QA，只傳環境與 delta。
- 只有 L3 獨立性、反方審查、不同責任維度或原 agent 已不可用才新 spawn。
- 收集 metrics 時傳 `--risk-lane`、main 的 `--requested-model/--requested-effort`；collector 會以 `turn_context` 記 effective 值並在漂移時告警。
- A/B 由 `gpt-5.6-terra / medium`、`fork_turns=none` 的機械 agent 執行，每個 before/after 至少 5 筆。未滿樣本不得宣稱節省百分比。

目前已開啟的 session 不會因修改 `config.toml` 或 agent TOML 熱重載；新 main 預設與新的 agent profile 從下一個 session 起生效。
