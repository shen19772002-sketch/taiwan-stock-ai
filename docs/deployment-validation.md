# GitHub 部署驗證紀錄

核對日：2026-10-24（台灣時間）。

## 已完成

- `market-data` 首次上傳提交：`2b952b8216175273096fb86ba5862ab4f78d01af`。
- 保存兩市場各 69 個交易日、134,446 筆去重行情及官方原始回應、報告和本機驗證證據。
- 本機 140 項測試、Ruff、JavaScript 語法、依賴完整性及 actionlint 檢查通過。
- GitHub API 確認儲存庫為私人，保留現有可見性。

## 進行中的遠端驗證

原始碼上傳後須實際檢查 CI 的 Python 3.11／3.12 結果，並以手動研究流程驗證還原、產生報告及寫回歷史。成功推送不能取代這些驗證。

## Pages 啟用限制

GitHub API 回報 `has_pages=false`；目前整合在讀取與建立 Pages 時均回覆 HTTP 403 `Resource not accessible by integration`。管理者須在 [Settings → Pages](https://github.com/shen19772002-sketch/taiwan-stock-ai/settings/pages) 選擇 GitHub Actions，且私人儲存庫的 GitHub 方案須支援 Pages。系統不會因此自動變更儲存庫可見性。

完成設定後，重新執行 Daily Taiwan stock research 的部署工作；只有 deployment 成功且網站內容通過檢查後，才可宣稱線上網站已啟用。

10 月 26 日為官方補假，下一例行開市日預計為 10 月 27 日。排程與資料尚未公布時的行為見[開市檢查](market-readiness.md)。
