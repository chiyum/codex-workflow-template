# sample_product

匿名示範：一個前端與 API 組成的內部工具。

## Repo 與規格

- Repo：`projects/sample-product`
- 主規格：`specs/product.md`
- ADR：`docs/adr/README.md`

## 環境

- 本地：依 repo README 啟動。
- 測試：`https://app.example.com`
- 正式：此範例不定義正式環境操作。
- 環境區分：僅測試環境；push main 只部署測試環境。

## Git 帳號歸屬

- Owner：`<your-owner>`
- Branch：`main`

## 驗證

- 依 repo 既有 lint、test、build scripts。
- UI 行為需用瀏覽器實跑；API 契約需用實際請求驗證。

## 開發基準與 release routing

- Repo：`projects/sample-product`；remote：`origin` → `<your-remote-url>`；branch：`main`
- 測試環境版本查詢：`https://app.example.com/version`；查不到時標 unavailable 並等使用者決定。
- Release gates：`release_ancestor`、`deployment_version`
- `release_profile_floor`：`standard`

## 機密

測試角色為 admin 與 member；credential 只存在本機 excluded secrets，不放入產品配置。

