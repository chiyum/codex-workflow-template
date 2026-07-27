---
title: macOS Bash 3.2 中變數後接非 ASCII 字元需使用大括號
tech: [shell, bash, macos]
problem-class: [shell-portability, i18n-script]
source: codex-workflow-template
status: validated
---

## 症狀

啟用 `set -u` 的 Bash 腳本在 `$VAR` 後緊接全形標點或 CJK 字元時，macOS Bash 3.2 可能把部分多位元組位元誤併入變數名，產生帶亂碼的 `unbound variable`。`bash -n` 不會發現這種執行期問題。

## 根因

舊版 Bash 對變數名後方多位元組字元的掃描存在相容性問題；同一段在較新 Bash 或其他 shell 可能正常，因此容易只在 macOS 使用者環境出現。

## 對策

變數後若直接接非 ASCII 字元，一律寫成 `${VAR}`。另以 `rg -n '\$[A-Za-z_][A-Za-z0-9_]*[^[:ascii:]]' <script>` 檢查同檔類似位置，並真實執行受影響分支。

## 程式碼參考

- `scripts/aiuse`：使用 `${profile_home}（獨立）`，避免 `$profile_home（獨立）` 在 Bash 3.2 被錯誤解析。

## 適用／不適用

適用於需跨 macOS/Linux 執行、且 shell 訊息含非 ASCII 文字的腳本。變數後接空白或半形標點時風險較低，但大括號仍是清楚且可攜的寫法。

## 關聯

- `scripts/test-aiuse-profile.py` 會真實執行該輸出分支，補足純語法檢查的盲點。
