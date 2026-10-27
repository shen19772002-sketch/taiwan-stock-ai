# GitHub 部署驗證紀錄

最新核對日：2026-10-28（台灣時間）。

每日盤前、盤後與法人晚間補充已完成實際 GitHub Actions／Pages 驗證，詳見[每日排程最終驗證](daily-automation-validation.md)。下方保留 10/25 首次正式網站啟用紀錄。

## 正式網站已啟用

網站：**https://shen19772002-sketch.github.io/taiwan-stock-ai/**。

擁有者已將本專案設為公開，並在 Pages 選擇 GitHub Actions。GitHub API 確認 `build_type=workflow`、`public=true`；本次只核對這個專案，沒有修改其他專案的可見性。

- [正式部署 40161425495](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/runs/40161425495)：`research`、`deploy` 兩個 job 均成功，程式版本為 `26f50c445c7a9117054a50fe695ffd12dde65d19`。
- [對應版本 CI 40161419830](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/runs/40161419830)：成功；Python 3.11／3.12 測試均通過。
- `market-data` 寫回提交：`bbb07d4f7dd6642448c1f6e8de0d8d008c5ea91d`。還原、SQLite 檢查與歷史保存步驟均成功。
- 正式 `assets/report.json` 經 HTTPS 驗證取得 HTTP 200；內容與上述資料分支封存報告經同一渲染器輸出的結果完全一致。網站只多出來源網址的 `safe_url` 衍生欄位，行情與分析資料未改寫。
- 正式報告 SHA-256：`8878a2ddb31f4554b0e71a436b81ff3133b06a9a7299ff890b5cff1f3c079ab3`。
- 報告研究日為 **2026-10-25**，行情日為 **2026-10-22**，狀態為 `non_trading_day`，當日新選股 0 檔，歷史清單 7 筆。這是週末控制流程的預期結果；既有 15 檔研究快照仍保留。

機器可讀證據見 [正式 Pages 驗證](evidence/pages-live-2026-10-25.json)。下方保留首次上傳與當時的阻礙，Pages 未啟用的狀態已由本次成功部署取代。

## 正式內容的畫面驗證

以預設 HTTPS 憑證驗證下載正式首頁、CSS、JavaScript、Plotly、報告與一份歷史 JSON，六個檔案全部 HTTP 200，逐檔保存 SHA-256。正式三個前端資產與已測試版本的原始位元組相同。

容器 Chromium 直接開啟正式網址時遇到 `ERR_CERT_AUTHORITY_INVALID`，因此本次把傳輸與畫面分開驗證：將已驗證下載的原始內容透過本機 HTTP 提供給瀏覽器，沒有停用 TLS 或忽略憑證錯誤。這項限制不影響已成功的 GitHub Pages 部署與正式 HTTPS 內容核對，但不能宣稱容器瀏覽器直接連線成功。

桌面 1440×1000、手機 390×844 均通過導覽、版面、搜尋、個股選單、自選股重新載入保存；沒有 JavaScript 或必要資產錯誤。兩尺寸各 10 個產業的 Plotly 數值與報告完全相同，畫布寬度符合容器；頁面內嵌資料等於正式 `report.json`，已抽查一份歷史報告連結可讀。

上述完整畫面檢查對應首次成功部署 [40111009704](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/runs/40111009704)。檢查另發現休市報告不應附加「目前無符合完整選股條件／需要至少 60 筆歷史」提示；已修正為只在非休市且無候選時顯示。本機 140 項測試與 Ruff 再次通過，最新部署已以正式 HTML／JSON 確認誤導提示移除、休市提示保留，且報告仍與歷史封存一致。前端資產沒有改動，完整畫面套件未重複擴大執行。

## 2026-10-24 首次上傳紀錄

- 程式、測試、工作流程與文件已上傳 `main`；首次上傳時沒有變更私人儲存庫的可見性。
- `market-data` 首次上傳提交：`2b952b8216175273096fb86ba5862ab4f78d01af`。
- 保存兩市場各 69 個交易日、134,446 筆去重行情及官方原始回應、報告和本機驗證證據。
- 本機 140 項測試、Ruff、JavaScript 語法、依賴完整性及 actionlint 檢查通過。
- 首次上傳時 GitHub API 確認儲存庫為私人。
- 從 GitHub 重新下載首份封存，220 個非資料庫檔案逐一核對 SHA-256 相同；SQLite integrity check 通過，完整 SQL dump 相同。快照有 152,307 筆觀測、4 份報告、0 筆前瞻訊號。SQLite 一致性備份的檔案標頭可能不同，資料庫以封存雜湊及完整邏輯內容驗證。

## 2026-10-24 遠端實跑結果

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

## 2026-10-24 尚未啟用時的替代報告驗證

上述每日研究執行頁的 Artifacts 已有 `research-report-40108480764`（3,904,756 bytes），可下載、解壓後在該資料夾執行 `python -m http.server 8000`，以瀏覽器查看本機服務的 8000 埠。另有 Pages 打包檔與 30 天復原資料，三份 artifact 均由 GitHub API 確認存在且未過期。實際 15 檔報告先前已通過本機 HTTP 桌面／手機瀏覽器驗證；此環境的 artifact 下載及瀏覽器直接開啟本機檔案受到政策限制，因此不宣稱已下載檢查該 artifact，亦不宣稱遠端 Pages 網址已通過驗證。

另從遠端 `market-data` 讀取本次實際週末報告，使用相同渲染程式在本機 HTTP 服務檢查。1440×1000 與 390×844 視窗均通過版面、導覽、個股選單、搜尋與自選股持久化檢查，沒有 JavaScript／必要資產錯誤，正確保留資料日 2026-10-22 與 0 檔當日新選股。這項驗證不代表 Pages 已發布。

## 已排除的 Pages 啟用限制

首次上傳時 GitHub API 回報 `has_pages=false`；當時整合在讀取與建立 Pages 時均回覆 HTTP 403 `Resource not accessible by integration`。此限制已由擁有者於 10 月 25 日完成公開及 Pages 設定後排除。

需要手動更新時，執行 Daily Taiwan stock research（`phase=manual`、`history_days=0`）。既有歷史已暖機，無須重做 100 日回補。未來某次部署失敗時，應先查看工作紀錄；成功部署與實際網站內容是不同的驗證項目。

此雲端環境另保存了 `results-receiver.actions.githubusercontent.com` 與 `productionresultssa6.blob.core.windows.net` 網域設定草稿，以便下載 Actions 日誌及 artifact；須在環境設定儲存並發布後才會生效，與 GitHub Pages 啟用是兩個不同設定。

10 月 26 日為官方補假，下一例行開市日預計為 10 月 27 日。排程與資料尚未公布時的行為見[開市檢查](market-readiness.md)。
