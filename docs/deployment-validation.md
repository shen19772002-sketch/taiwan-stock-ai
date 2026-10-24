# GitHub 部署驗證紀錄

核對日：2026-10-24（台灣時間）。

## 已完成

- 程式、測試、工作流程與文件已上傳 `main`；沒有變更私人儲存庫的可見性。
- `market-data` 首次上傳提交：`2b952b8216175273096fb86ba5862ab4f78d01af`。
- 保存兩市場各 69 個交易日、134,446 筆去重行情及官方原始回應、報告和本機驗證證據。
- 本機 140 項測試、Ruff、JavaScript 語法、依賴完整性及 actionlint 檢查通過。
- GitHub API 確認儲存庫為私人，保留現有可見性。
- 從 GitHub 重新下載首份封存，220 個非資料庫檔案逐一核對 SHA-256 相同；SQLite integrity check 通過，完整 SQL dump 相同。快照有 152,307 筆觀測、4 份報告、0 筆前瞻訊號。SQLite 一致性備份的檔案標頭可能不同，資料庫以封存雜湊及完整邏輯內容驗證。

## 遠端實跑結果

程式版本：`fe548adf1b032f1dfcb7f3d92d0eb49468d942ed`。

| 驗證 | 結果與證據 |
| --- | --- |
| Python 3.11／3.12 CI | [40108465240](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/runs/40108465240) 成功；兩版本安裝、Ruff、pytest、離線網頁與 artifact 步驟均通過 |
| 每日研究 | [40108480764](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/runs/40108480764) 的 `research` job 成功；還原、官方 API 探測、產生報告、SQLite 檢查、歷史寫回與網頁 artifact 都通過 |
| 官方 API | GitHub runner 實際測試 9 個端點均 HTTP 200；接受 1,949 筆普通股行情及 1,975 筆估值，實際行情／估值日仍為 2026-10-22 |
| 歷史分支寫回 | `dcefd1c45d28668e071974d8d7cb8cdd6819a474`；新探測 manifest：`data/raw/20261024T154639301433Z/manifest.json` |
| Pages | `deploy` job 失敗於 `configure-pages`，`Get Pages site: Not Found`；尚未啟用，不能聲稱網站已上線 |

完整機器可讀證據見 [github-deployment-2026-10-24.json](evidence/github-deployment-2026-10-24.json)。CI 測試步驟成功由 GitHub API 核對；目前雲端下載完整日誌的網域受限，未從遠端 log 重數測試數量。本機完整測試為 140 項。

盤後來源仍有上市 7 筆、上櫃 24 筆不完整行情被排除，HTTP 200 不代表所有欄位完整。此次探測不匯入新預測；10 月 24 日為週末，遠端報告正確標示 `non_trading_day`、資料日 10 月 22 日、當日新選股 0 檔。此前驗證的 15 檔研究報告與原始資料仍保存於歷史分支。

## Pages 尚未啟用時查看報告

上述每日研究執行頁的 Artifacts 已有 `research-report-40108480764`（3,904,756 bytes），可下載、解壓後在該資料夾執行 `python -m http.server 8000`，以瀏覽器查看本機服務的 8000 埠。另有 Pages 打包檔與 30 天復原資料，三份 artifact 均由 GitHub API 確認存在且未過期。實際 15 檔報告先前已通過本機 HTTP 桌面／手機瀏覽器驗證；此環境的 artifact 下載及瀏覽器直接開啟本機檔案受到政策限制，因此不宣稱已下載檢查該 artifact，亦不宣稱遠端 Pages 網址已通過驗證。

另從遠端 `market-data` 讀取本次實際週末報告，使用相同渲染程式在本機 HTTP 服務檢查。1440×1000 與 390×844 視窗均通過版面、導覽、個股選單、搜尋與自選股持久化檢查，沒有 JavaScript／必要資產錯誤，正確保留資料日 2026-10-22 與 0 檔當日新選股。這項驗證不代表 Pages 已發布。

## Pages 啟用限制

GitHub API 回報 `has_pages=false`；目前整合在讀取與建立 Pages 時均回覆 HTTP 403 `Resource not accessible by integration`。管理者須在 [Settings → Pages](https://github.com/shen19772002-sketch/taiwan-stock-ai/settings/pages) 選擇 GitHub Actions，且私人儲存庫的 GitHub 方案須支援 Pages。系統不會因此自動變更儲存庫可見性。

完成設定後，在上述執行頁選 **Re-run failed jobs**，或重新執行 Daily Taiwan stock research（`phase=manual`、`history_days=0`）。只有 deployment 成功且網站內容通過檢查後，才可宣稱線上網站已啟用。既有歷史已暖機，無須重做 100 日回補。

此雲端環境另保存了 `results-receiver.actions.githubusercontent.com` 與 `productionresultssa6.blob.core.windows.net` 網域設定草稿，以便下載 Actions 日誌及 artifact；須在環境設定儲存並發布後才會生效，與 GitHub Pages 啟用是兩個不同設定。

10 月 26 日為官方補假，下一例行開市日預計為 10 月 27 日。排程與資料尚未公布時的行為見[開市檢查](market-readiness.md)。
