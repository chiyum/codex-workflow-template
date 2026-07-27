# Safe Publishing Playbook

## 決策流程

1. 先定義來源 allowlist，不以 `.gitignore` 當來源選擇器。
2. 公開版在 `git init` 前完成獨立 staging、身份脫敏、機密掃描與引用驗證。
3. tree 與 history 都掃；掃描結果只輸出檔名與規則，不印命中值。
4. 安裝器預設只接受空 target；覆寫必須先做可識別備份。
5. fresh clone 重跑 install、validate 與 scan，才算可發布。

## 必過清單

- [ ] 無 auth、env、secret、session、history、log、database、cache、plugin runtime。
- [ ] Public 內容無個人姓名、家目錄、email、內部 repo、domain、IP、port 或部署識別。
- [ ] `.gitignore` 路徑錨定，不用寬鬆 glob 吞同名 source directory。
- [ ] Public 與 private root commit 不同且無共同祖先。
- [ ] `manifest.tsv` 與 tracked files 完全一致。

