# GitHub 排程與 Pages 部署

## 首次啟用

1. 將程式碼推送至自己的 GitHub 儲存庫 `main`，並確認已保存的歷史資料位於 `market-data` 分支。
2. 在 **Settings → Actions → General → Workflow permissions** 允許此工作流程寫入儲存庫；組織政策若限制寫入，需由管理者開放。`research.yml` 只在資料工作使用 `contents: write`，部署工作使用 `pages: write` 與 `id-token: write`。
3. 在 **Settings → Pages → Build and deployment → Source** 選擇 **GitHub Actions**。若有 `github-pages` environment 審核規則，依自己的發布政策處理。
4. 在 **Actions → Tests and static report** 執行 CI，確認測試通過。
5. 在 **Actions → Daily Taiwan stock research → Run workflow** 選 `manual`。檢查資料來源結果、`market-data` 分支與 Pages deployment 結果。GitHub 會提供實際 Pages 網址。

首次遠端驗證可勾選 `probe_sources`，即使當天休市也會測試 runner 到官方 API 的連線並保存原始回應。這個探測不將休市日變成交易日，不回填預測。若沒有任何可驗證行情，探測步驟會失敗，先檢查來源再重試。一般排程不啟用額外探測。

每次研究另保留 30 天 `research-report-*` 網頁 artifact。Pages 尚未啟用時，可從 Actions 執行頁下載、解壓，在該資料夾執行 `python -m http.server 8000`，用瀏覽器查看本機服務的 8000 埠。網站所有必要圖表資產均已打包。自選股儲存仍依瀏覽器與來源網址區隔，不跨裝置同步。

官方公開資料不需要 API 金鑰。GitHub 自動提供工作流程的 `GITHUB_TOKEN`；不需要另貼個人權杖。程式透過短暫的 Git askpass 讀取環境權杖，Git remote URL 與檔案不含權杖。未實作的 AI API、授權即時行情不得以假的 token 或行情取代。

## 排程

| 台灣時間（UTC+8） | UTC cron | 工作 |
|---|---|---|
| 平日 08:20 | `20 0 * * 1-5` | 盤前研究，使用已公布的上一交易日資料 |
| 平日 12:30 | `30 4 * * 1-5` | V1 未接授權盤中來源，明確顯示無即時行情 |
| 平日 15:30 | `30 7 * * 1-5` | 盤後初步收集 |
| 平日 18:30 | `30 10 * * 1-5` | 晚間再次收集與報告更新 |
| 平日 20:30 | `30 12 * * 1-5` | 補抓晚公布資料 |

GitHub 排程可能延遲、取消或在儲存庫長期無活動後停用，因此不保證準點。排程會依觸發的 cron 時段固定研究階段，避免排隊延遲將盤前工作變成盤中工作；只有手動選擇 `auto` 才依真正執行時的台灣時間判斷。週末與可驗證的官方休市日跳過當日交易分析；官方日曆無法取得時不能假裝已確認交易日。資料還沒公布會留下等待／部分完成狀態，晚間排程不是法人資料已公布的保證。法人明細使用 TWSE T86／TPEx 三大法人每日資料，僅同日完整數據參與評分，不能將成交額當成法人淨買超。

## 歷史資料如何保存

每次執行先從同一儲存庫的 **`market-data` 分支** 還原 `data/`，再收集、檢查 SQLite 完整性並寫回。首次確認遠端分支不存在才建立新分支；授權或連線失敗會停止，不能錯誤地建立空資料庫覆蓋歷史。

- `data/market.sqlite3`：原始觀察版本、不可變的研究執行紀錄與原始預測。
- `data/raw/`：官方原始回應及來源 manifest。
- `data/reports/`：每次研究報告快照。
- `data/latest.json`：最近一次報告，可更新。

執行機器上的資料庫仍是一般 SQLite。資料分支將它保存為 **`.persistence/manifest.json` 與每份不超過 40 MiB 的 `part-*.gzpart`**，避免 SQLite 成長後撞上 GitHub 的單檔限制。封存時先建立一致性 SQLite 備份，再使用固定時間戳的 gzip 壓縮、依序切分；完全相同的資料封存不會製造新的資料提交。每次重新擷取的原始回應仍保留；語意相同的研究報告會重用原始版本，不因擷取時間而重複建立。還原時先驗證每個區塊的大小與 SHA-256、完整壓縮串流及解壓後資料庫的大小與 SHA-256，最後執行 SQLite integrity check，全部通過才放回 `data/market.sqlite3`。區塊遺失、損毀、次序錯誤或不安全路徑都會停止還原，不能退回空資料庫。舊版直接存放 SQLite 的分支仍可還原，下次保存時會轉成分塊格式；既有原始回應與報告不會刪除。

需要手動還原時，先準備空的目的目錄，執行 `python scripts/persist_data.py restore --repository OWNER/REPO --state-dir /tmp/research-restore --data-dir recovered-data`；其中 `state-dir` 必須尚不存在，`data-dir` 必須為空。請使用 helper，不要把單一壓縮區塊當成完整資料庫。這個讀取操作不會推送任何變更。

工作流程使用單一 concurrency group、`cancel-in-progress: false` 與 `queue: max`，不中止正在執行的工作，也不以新工作取代同組等待中的工作；最多保留 100 筆待執行工作，超過上限時 GitHub 會取消新增工作。日常資料完整性要以來源 manifest 為準。寫回不用 force push；若遠端意外已有其他更新，保留失敗紀錄與 recovery artifact，重新從最新歷史開始執行，不能以舊 SQLite 強制覆蓋新歷史。原始回應及報告的同名快照不可覆寫。資料分支更新不會觸發 `main` 的 CI，避免循環排程。

`research-state-*` artifact 保留 30 天供短期復原，內含一般 SQLite；它不是永久儲存的替代品。不要刪除或重設 `market-data`。分塊處理了資料庫的單檔大小，但 Git 歷史與原始回應仍會持續長大，應定期檢查分支大小並備份；接近儲存庫容量限制前，應將 `Store` 實作換成 PostgreSQL／物件儲存，將原始回應封存至具有持久保留政策的 bucket。V1 不宣稱 Git 分支適合無限量資料。

## 網路、公開資料與故障處理

私人儲存庫的 Pages 可用性取決於 GitHub 方案；啟用前請確認方案支援。若 GitHub App 回覆 `Resource not accessible by integration`，這是該整合未獲 Pages 設定權限，不代表沒有程式推送或 Actions 執行權限。管理者可在儲存庫的 Settings → Pages 選擇 GitHub Actions，再重跑部署。不要為了啟用 Pages 自動將私人儲存庫改成公開。

雲端開發環境的網路允許清單和 GitHub runner 是不同環境。至少需能存取 `openapi.twse.com.tw`、`www.twse.com.tw`、`www.tpex.org.tw` 與套件下載站；新增資料來源時再加入對應官方主機。已在雲端草稿保存網域不代表設定已發布，也不代表 GitHub 實際 API 請求已通過。

API 失敗時會保存來源錯誤並展示等待資料，不會生成行情。請使用 `python -m taiwan_stock_ai probe --data-dir data` 檢查；沒有可驗證行情時此命令會以非零狀態結束。正常 `run` 可以成功產生誠實的等待資料頁面，此退出碼表示管線完成，並不代表 API、選股或績效已可用。

Pages 只發布 `site/`，不發布 SQLite 或原始回應。若儲存庫本身公開，資料分支也公開；請勿將 Secrets、私人持股或個資寫入 `data/`。自選股與持股僅留在瀏覽器 localStorage，不隨 Pages 上傳。GitHub Pages 是靜態展示，沒有下單功能。

CI 使用隔離的測試資料及離線空資料頁面，不碰真實官方行情。來源連線、遠端寫入權限與 Pages 發布仍需由首次真實工作流程分別驗證。
