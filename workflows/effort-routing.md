# Model／effort 與風險路由（唯一政策來源）

主 Codex 先以 `python3 ~/.codex/scripts/workflow-profile.py lane ...` 決定 lane，再以 `plan --lane <lane> --mode <auto|standard> [--profile <lite|standard|full>] --target <local|dev|prod|unknown> --baseline-receipt <使用者已確認的 fresh receipt>` 取得 requested/effective profile、升級原因、release overlay 與 ordered gates；continue 改用 `--mode continue --state <path>`，並從 state 還原 exact receipt/release identity。任何 agent spawn 前再以 `resolve` 取得明確 `model`、`reasoning_effort` 與 `fork_turns`。不得以自由字串自我宣告 baseline confirmed，也不得只靠自然語言暗示降級。custom agent TOML 不固定 model/effort，避免覆蓋 explicit spawn；`config.toml [agents]` 的 `high` 只是不經 router 時的 fail-safe。

## 主對話與子代理的選擇來源

- main 尊重使用者當前 session 選定且實際生效的 model/effort，不因下方子代理政策切回其他模型或強度。
- `config.example.toml` 的 `gpt-5.6-sol / medium` 是初始化範例；`resolve --role main` 回傳的也只是參考值，不讀取或切換當前 session，不得用來覆蓋使用者的選擇。
- 子代理仍依下方矩陣與 resolver 明確派遣；main 的選擇不會自動改寫整組子代理政策。

## 風險 lane

- `L1 low-risk`：只有明確標成候選、1–3 條 acceptance、單 repo、可回滾，且未命中 deny list 才成立。target 不屬於 L1 資格，只進入 release overlay／gates。
- `L2 standard`：未命中高風險，但不滿足 L1 全部條件。
- `L3 high-risk`：auth、權限、租戶、DB、migration、資料一致性、跨 repo contract、基礎設施、金流、不可逆、重大架構、全新視覺任一命中即強制進入。目標環境 local/dev/prod 是 release risk，不改寫 change lane。
- `$rapid` 仍只接受明確觸發；L1 絕不自動切 rapid。

## Workflow profile floor 與 gate

| effective profile | 核心角色順序 | 禁用角色 |
|---|---|---|
| Full | PM acceptance → architect → reviewer → QA → 獨立 PM | 無 |
| Standard | verifier acceptance → architect → reviewer → follow-up 同一 verifier | QA、PM |
| Lite | architect 自測 → verifier | reviewer、QA、PM |

L1 的 change floor 是 Lite、L2 是 Standard、L3 是 Full。未指定 profile 就以該 floor 為 requested；明示 requested 只可保留或升級，plan 同時輸出 requested/effective 與升級原因。effective floor 是 change floor 與 product release floor 取高；未知／缺 policy 的 direct prod 再 fail-safe Full。`auto` 只改凍結後確認模式，continue 保留 state，都不改 profile。security auditor 只由 port/proxy/container/pipeline/DB-Redis 暴露面觸發；auth/permission/tenant/external input 只命中 reviewer security module，外部主動掃描先問。成對 design gate 仍按觸發條件附加，不受 profile 禁用角色影響。

所有 lane/profile 的 test scope 預設都是 diff/acceptance + 最近必要 sentinels。Full、L3、repo 指令名為 all/E2E 都不代表獲得全站授權；無法切分且具實質成本時先問。lint、build、staged-secret/diff scope、pre-review、release 與 product hard gates 仍為必做。

## Profile 矩陣

| lane／角色 | model | effort | history |
|---|---|---|---|
| main | 使用者當前 session 選定的 model | 使用者當前 session 選定的 effort | parent |
| L1 architect/reviewer | `gpt-5.6-sol` | `medium` | bounded 3 turns |
| L2 architect/reviewer | `gpt-5.6-sol` | `high` | bounded 3 turns |
| L1–L3 QA／test 執行 | `gpt-5.6-terra` | `medium` | none |
| L1–L2 verifier（Standard/Lite） | `gpt-5.6-terra` | `medium` | none |
| L1–L3 純 evidence 整理（L1 audit 仍由 main 直接完成） | `gpt-5.6-terra` | `low` | none |
| L3 architect/reviewer/PM/UI 判斷角色、security auditor | `gpt-5.6-sol` | `high` | bounded 3 turns |

子代理的 `xhigh` 只允許 L2/L3 的單一 architect/reviewer 葉，spawn 前必須告知使用者「為何需要深層單線推理」，並把理由傳給 `--xhigh-reason`。此限制不適用於使用者為 main 選擇的強度；不得因 main 使用 `xhigh` 就自動升級所有平行子代理。

## Luna route 邊界

預設 test/QA **保留 Terra low/medium**，不自動採 Luna/max。公開範本不附帶特定帳號或單次環境的 benchmark 結論；任何替換都必須先以 matched、可執行、相同 sandbox 的 A/B 證據驗證。

Luna low/medium 只列為未來可重新 benchmark 的 candidate；不得因 Terra 失敗、usage limit、unsupported 或 profile 登入狀態不明而自動切換。任何新增 Luna route 都必須有使用者拍板。collector 可記錄 `max|ultra` requested effort，僅代表遙測相容，不代表 router 允許自動使用。

## Reuse 與遙測

- 同任務退修用既有 architect/reviewer 的 follow-up，只傳 finding、diff/commit 與新增證據；Standard 的 acceptance→驗收重用同一 verifier，local→dev 沿用同一 QA/verifier，只傳環境與 delta。
- 只有 L3 獨立性、反方審查、不同責任維度或原 agent 已不可用才新 spawn。
- 收集 metrics 時傳 `--risk-lane`、main 的 `--requested-model/--requested-effort`；collector 會以 `turn_context` 記 effective 值並在漂移時告警。
- A/B 由 `gpt-5.6-terra / medium`、`fork_turns=none` 的機械 agent 執行，每個 before/after 至少 5 筆。未滿樣本不得宣稱節省百分比。

單純修改 `config.toml` 或 agent TOML 不代表已開啟的 session 會自動切換；磁碟預設供後續 session／agent 使用，main 仍以當前 session 中使用者的選擇與實際生效值為準。
