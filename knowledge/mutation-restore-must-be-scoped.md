---
title: 突變工具只能還原自己修改的檔案
status: template
---

## 症狀

測試工具用廣域 Git 還原命令，連未提交實作一起消失；後續測試其實在舊版上執行。

## 根因

工具無法區分自己的 mutation 與使用者工作區變更。

## 對策

突變前先 commit；每輪把單一目標檔備份到暫存目錄，只用檔案副本還原。突變後先證明檔案真的變了，結束後驗工作區與全套測試回到基線。

## 適用／不適用

適用本機 mutation、fault injection 與自動修補測試；全新拋棄式 CI checkout 風險較低，但仍建議 scoped restore。

