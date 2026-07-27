# 現場急修工作流（rapid v1）

本流程只處理明確 `$rapid` 觸發、可回滾、可快速驗證、邊界唯一的 local/dev Bug 或小型修改。它使用獨立 `~/.codex/rapid/` state、acceptance、evidence，不繼承標準五步驟，也不得因「小改／緊急／L1」自動啟動。

## 適用性 gate

必須全部成立：單一主要行為；現況/期望/驗法唯一；可回滾；產品/repo/環境已知；最多 R1–R3；目標 local/dev。

以下任一命中即停止並建議 `$dev`：migration／資料回填／schema、登入／權限／租戶、金流／帳務、資安／機密、基礎設施／部署架構／port、跨 repo contract、新依賴／重大架構、從零頁面／全新視覺／複雜動效、不可逆／付費／prod、需求有多個行為解讀。

## Model／agent 政策

- 使用 `workflow-profile.py` 的 L1 profile：architect/reviewer `gpt-5.6-sol / medium / bounded`，QA `gpt-5.6-terra / medium / none`，PM evidence audit `gpt-5.6-terra / low / none`。
- architect 是唯一 code 寫入者；其他角色唯讀。
- 退修 follow-up 原 architect/reviewer，只傳 finding 與 delta；local→dev follow-up 同一 QA。agent 不可用或獨立反方維度才新 spawn。
- custom agent TOML 不固定 effort；explicit spawn 參數必須來自 resolver。

## 獨立產物

slug 為 `<YYYYMMDD>-rapid-<簡述>`：

```text
~/.codex/rapid/state/<slug>.json
~/.codex/rapid/acceptance/<slug>.md
~/.codex/rapid/evidence/<slug>/
```

state 至少記 task/product/cwd/kind/target_environment/current_stage/retry_scope/next_action/status/rollback/updated_at。status 只用 `running|awaiting_user|not_eligible|ready_for_release|done`，每次 gate 更新。

## 流程

### 0. 載入與凍結

讀產品配置、`knowledge/ROUTER.md` 與命中 playbook，跑適用性 gate。主 Codex依使用者原文直接凍結最多三條 rapid acceptance：R1 主要行為、R2 鄰近回歸、必要時 R3；記現況、期望、範圍、非目標、環境與 rollback。無法唯一凍結即 `not_eligible`。

### 1. 修改前基準

唯讀重現並把 screenshot/curl/test 輸出存入 rapid evidence。沒有可信基準就停止，不以 code 推論代替。

### 2. Architect 最小修補

交付原始需求、凍結卡、基準、repo 與範圍；只做最小可回滾修補、最近測試與必要規格同步，以繁中 commit。需要 ADR 代表 gate 判錯，停止轉 `$dev`。

### 3. Targeted pre-review

檢查 diff scope、secret/生成物、受影響檔案的 formatter/lint/build/最近測試；repo 已有必要完整 gate 時執行。失敗 follow-up 原 architect 實質修正。

### 4. Review、QA、PM audit

- 單一 reviewer：通用核心 + diff 命中模組，finding 嚴重/一般擋 gate。
- 單一 QA：實際重跑 R1–R3 並逐條存證。
- PM：只 audit 凍結卡與 QA evidence，不重跑相同 browser；證據不足 follow-up 同一 QA。

主 Codex 執行 evidence 存在檢查並親讀關鍵證據。失敗 follow-up 原 architect，修後重跑 targeted pre-review、受影響驗收與固定回歸哨兵。

### 5. Dev 發布與複驗

只在產品配置證明 remote/branch 會部署 dev 時依授權 push；push main 會碰 prod 或環境不明時只標 `ready_for_release`。版本等於本 commit 後 follow-up 同一 QA，傳 dev URL/版本/環境 delta，重跑主要行為與哨兵並存證。部署超時是部署問題，不退 architect。

### 6. 收尾

state 標 done；回報 R1–R3 證據、commit/push/dev/version、rollback 與三步複驗。只完成 local commit 必須寫「尚未發布」。收 metrics 時傳 `--risk-lane L1` 與 resolver 的 requested main profile。

## 三次保險絲

只有零新證據、零狀態變化、同一操作原樣三次仍同結果才停止該操作並換方法；實質修正後重驗不計次。使用者改需求時停止原任務並重新做 rapid eligibility，不改寫已凍結卡。

