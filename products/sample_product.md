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

## Git 帳號歸屬

- Owner：`<your-owner>`
- Branch：`main`

## 驗證

- 依 repo 既有 lint、test、build scripts。
- UI 行為需用瀏覽器實跑；API 契約需用實際請求驗證。

## 機密

測試角色為 admin 與 member；credential 只存在本機 excluded secrets，不放入產品配置。

