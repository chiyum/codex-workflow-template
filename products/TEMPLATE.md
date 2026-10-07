# <product_code>

## Repo 與規格

- Repo：`<relative-or-placeholder>`
- 主規格：`<path>`
- ADR 索引：`docs/adr/README.md`

## 環境

- 本地：`<command>`
- 測試環境：`<none-or-example-url>`
- 正式環境：是否存在；未經明確授權不得操作。
- 環境區分：`dev+prod 雙環境`／`單一線上環境`／`僅本機`；單一環境者 push main 即等同上線。

## Git 帳號歸屬

- Owner：`<owner>`
- Branch：`main`

## 驗證

- lint：`<command>`
- test：`<command>`
- build：`<command>`
- 部署版本探針：`<none-or-command>`

## 開發基準與 release routing

只做 routing，不覆蓋 repo code／規格／ADR 的行為真相；凍結前據此產生 baseline receipt。

- Repo：`<path>`；remote：`origin` → `<expected-url>`；branch：`main`
- 各目標環境版本查詢：`<version-url-or-command>`；查不到時的 unavailable 政策：`<policy>`
- Release gates：`<gates>`
- `release_profile_floor`：`lite|standard|full`

## 測試帳號與機密

僅列角色與用途。密碼、token、key 放在未追蹤的 `SECRETS.local.md` 或 `env/`。

