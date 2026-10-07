# Products 索引

| 代號 | 說明 | 配置 |
|---|---|---|
| `sample_product` | 匿名示範產品 | [sample_product.md](sample_product.md) |

新增產品時複製 [TEMPLATE.md](TEMPLATE.md)，補上 repo、規格、環境、測試入口、Git 歸屬與部署驗證。機密只寫本機變數名稱，不寫值。

## 配置檔必填區塊

1. **規格書與文件路徑**：該產品所有規格書、設計文件與 ADR 索引位置。
2. **測試環境**：服務位置、啟動方式與健康檢查指令。
3. **測試帳號**：只列角色、權限等級與適用情境；credential 只寫本機引用位置。
4. **重要規約**：API 回應格式、權限模型、業務規則等驗收時必須對照的條目。
5. **環境區分**：`dev+prod 雙環境`／`單一線上環境`／`僅本機` 等；單一環境者 push main 即等同上線，必須明確註明。
6. **開發基準與 release routing**：每個 repo 的路徑、remote 名與預期 URL、Git 帳號、branch；每個目標環境的 version 查詢方式與 unavailable 政策；release gates 與 `release_profile_floor = lite|standard|full`。這些欄位只做 routing，不覆蓋 repo code／規格／ADR 的當前行為真相。

每次 code 任務凍結前用上述資訊經 `scripts/development-baseline.py` 產生 fresh baseline receipt；產品配置與選定 baseline repo 的行為不一致時，在 acceptance 明示衝突等使用者決定，不批次改寫產品規則。

