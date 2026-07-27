# Codex Workflow Template

可公開重用的 Codex 多 agent 開發工作流範本。它展示主 Codex → PM 凍結 → architect 唯一寫入 → reviewer → QA／PM 證據 gate → 發布驗證的完整鏈，且不含任何個人、客戶、內部產品或執行期資料。

## 收錄與排除

`manifest.tsv` 是 machine-checkable 清單：`install` 會進目標 `CODEX_HOME`，`support` 只服務此 repo。範本包含 AGENTS、七個子 agent、L1–L3 resolver、effort routing、standard/rapid workflow、匿名 product template/example、通用 knowledge router、dev/discover/rapid/retro skills、metrics v2、aiuse Codex profile 隔離、驗收／state 制度、rules 與安全腳本。

永遠排除 auth、secrets、env、webhook、sessions、history、logs、SQLite、cache、MCP runtime、shell snapshots、models cache、installation id、plugin cache、OpenAI `.system` skills，以及 Memories 生成內容、database 與 runtime state。第三方 MCP 或插件屬選配，請自行安裝並讓所有相依元件使用一致版本。

## Memories feature

`config.example.toml` 的 `[features] memories = true` 只同步 Codex 內建 experimental feature toggle，不分享任何生成記憶。要停用時把值改成 `false` 或移除該設定；`memories/`、其 SQLite/WAL/SHM、runtime state 與生成內容一律不得加入 Git。

## 先決條件

- macOS 或 Linux
- Bash、Git、Python 3、ripgrep (`rg`)
- Codex CLI；其他工具依產品 repo 自行準備

## 安裝與 smoke test

```bash
target="$(python3 -c 'import os,tempfile; print(os.path.realpath(tempfile.mkdtemp(prefix="codex-home-")))')"
bash scripts/validate.sh
bash scripts/install.sh --target "$target"
bash scripts/validate.sh --installed "$target"
test -f "$target/AGENTS.md"
test -f "$target/products/sample_product.md"
test -f "$target/config.toml"
test -f "$target/scripts/workflow-profile.py"
test -f "$target/scripts/aiuse"
```

安裝器把安全的 `config.example.toml` 映射成目標的真正 `config.toml`，並把 `manifest.tsv` 一併安裝作為 provenance；其自我宣告必須恰有一筆且精確為 `install manifest.tsv manifest.tsv`。它會在路徑展開前拒絕空白 target，也拒絕 symlink ancestor、危險根目錄、source/target 重疊與非正規 manifest path。`--force` 只允許替換可由本次 trusted source 完整重建的現行 Codex workflow home：installed manifest 必須逐 byte 相同、全部 install destination 必須是 no-follow regular file 且 SHA-256 相同，檔案與目錄 inventory 也不得缺漏或多出 runtime payload。唯一 legacy fallback 會以 `policy/legacy-releases.tsv` 的已知 release tree fingerprint，精確核對全部目錄、檔案路徑與每檔 SHA-256，不拿 current source bytes 冒充舊版。僅偽造 marker／manifest、未知內容、hash drift、缺漏、symlink 或帶任意 extras 的 project 不會被替換。完整 staging 驗證後才建立時間戳備份並原子安裝；不符合已知 fingerprint 的舊 target 請改用全新 target。

## 客製

- Products：複製 `products/TEMPLATE.md`，在 INDEX 註冊 repo、規格、環境、Git owner 與驗證。
- Agents：`agents/*.toml` 註冊角色，`agent-guides/` 放完整責任；維持 architect 是唯一產品 code 寫入者。
- Skills：以獨立目錄加 `SKILL.md`；產品專屬部署／測試 skill 留在私人 repo。
- Knowledge：新增可跨產品複用的卡片，回寫 INDEX 與 playbook。
- 制度：每次任務從 acceptance／state／run metrics template 複製到本機執行區；公開前再次匿名化。

## aiuse Codex profiles

把安裝後的 `scripts/aiuse` 放到自己的 `PATH`，再以 `aiuse codex <profile> [args...]` 啟動。每個 profile 使用獨立 `CODEX_HOME`、auth、sessions、history、memories、logs 與 state；只有 `AGENTS.md`、config、agents、guides、knowledge、scripts、workflows 與 manifest 等非帳號 surfaces 會在全量 preflight 無衝突後建立 managed symlink。既有 local item、auth symlink/hard-link、runtime state symlink 或 credential-store override 都會 fail closed，不覆寫也不留下半套設定。`--yolo` 等一般 Codex 參數原樣轉交。

Runtime preflight 會從 pinned directory fd 遞迴檢查 regular file、精確 control socket 與 link count，並在 command 啟動前做有 entry/depth 上限的雙次一致性 recheck；`auth.json` 也會在每個實際 launch boundary 以 pinned parent、`O_NOFOLLOW`、file identity 與 link count 再驗一次。同一 profile 的 `aiuse` 程序另以 profile directory advisory lock 協作互斥。此 gate 防既有／意外共享、遵守同一協作鎖的 `aiuse` 程序，以及 preflight 期間的非協作競態；它不宣稱阻止同一 OS uid 在 command 已啟動後持續惡意改寫 `CODEX_HOME`，也不能強迫繞過 `aiuse` 的程序遵守 advisory lock，因為該 uid 原本就具有相同檔案權限。

## 成本路由與 metrics

主 session 預設 Sol/medium；機械測試與 QA 依 lane 使用 Terra low/medium，agent TOML 的 high 僅是 resolver 未介入時的 fail-safe。Luna/max 不自動啟用；`xhigh` 只允許事前說明理由的 L2/L3 單一 architect/reviewer 葉。run metrics 對多 transcript tree 只保存 raw observations，不能把不同 counter 假 sum/max；只有單一 no-history transcript 的 exact run 可進 token A/B，且每組至少五筆才可判讀。

## 更新、備份與復原

1. 在獨立 staging 編輯通用內容，不從私人 repo clone、fork 或攜帶 history。
2. 先更新 manifest，再跑 `bash scripts/scan-secrets.sh --history`、`bash scripts/validate-public.sh` 與 `bash scripts/validate.sh`。
3. 安裝更新到全新 target 比對；確認後才能用 `--force`，並記下輸出的 backup 路徑。
4. 復原時把新 target 改名，再把時間戳 backup 改回；不要用廣域 Git 還原或清理命令。

## 發布前檢查

```bash
bash scripts/scan-secrets.sh --history
bash scripts/validate-public.sh
bash scripts/validate.sh
git status --short
```

secret scanner 只輸出脫敏檔案標記／commit id 與規則名稱，不會列出完整命中值。`validate-public.sh` 會對 current tree 與每個 history revision 的 repository-relative path／filename 套用完整 Public 通用規則，並拒絕 Unicode `Cc`／`Cf` 控制或格式字元；path 命中只回報 `<redacted-path>`、commit ID（history）與規則名。`--history` 逐 commit 掃描完整 raw commit object，而不只格式化作者與訊息欄位，因此 signature／未知 header、NUL、非 UTF-8、空或不可讀 history 都不能通過。Public repo 必須用自己的 root commit 建立，任何私人內容都不可先 commit 再刪。

`policy/deny-paths.tsv` 是 tree、history 與 `.gitignore` 一致性檢查的單一 deny-path 規格，`policy/deny-path-fixtures.tsv` 逐條驗證 pattern 與 ignore entry。Public 只保留 personal-home、IPv4 等通用脫敏規則；私人識別 denylist 與 frozen fixtures 必須由發布者保存在 Public repo 外，並在發布前對 fresh clone 執行外部 gate。Public validator 會正規化 case/Unicode，還原 percent/Base64 包裝，拒絕 binary、symlink、hard-link、非正規或含控制字元的 path，並掃 current/untracked、所有 reachable commit tree/blob/raw metadata 與 annotated tag metadata。`acceptance/`、`state/`、`run-metrics/` 只允許制度 README／模板，生成 evidence、checkpoint、run 與 Memories state 不得提交。`scripts/test-security.sh` 以 raw header、current/history 控制字元路徑、current provenance drift 與 installer backup／rollback 等 mutation 逐項證明 gate 會正確變紅。

## 與私人版差異

私人版可保留內部產品與完整工程知識，但仍不含真實 secrets 或 runtime data；本版只保留匿名可用模板與少量通用示例。
