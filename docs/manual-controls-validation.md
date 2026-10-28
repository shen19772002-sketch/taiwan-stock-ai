# 自動執行核對與簡化手動操作

驗證日：2026-10-28，以下時間均為 Asia/Taipei。

## 自動執行的實際狀態

`Daily Taiwan stock research` 已啟用，保留平日 08:20 盤前、15:30 盤後及 18:30／20:30 補充資料排程，也保留既有 12:30 可用性檢查。工作流程啟用、排程觸發與報告資料完整是三件事，分開核對。

- [40557421175](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/runs/40557421175) 是 `schedule` 自動觸發且成功的紀錄，建立於今天 02:10；屬前一天晚間工作延遲，不能當成今天 08:20 已準時完成。
- 13:46 查詢時，尚未出現今天早上／中午新建立的排程紀錄。06:30 的盤前報告是手動驗證。
- 今天盤前 [40640531976](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/runs/40640531976) 原訂 08:20，GitHub 於 **13:52:49** 才建立。第一次嘗試在排隊時被本次手動驗證取代，尚未開始任何工作便取消。
- 已明確以人工要求重新執行同一筆工作：第 2 次嘗試於 **15:28:09** 開始、**15:30:16** 完成，研究、SQLite 檢查、歷史保存及網站部署皆成功。排程解析器仍採原排程的 `preopen`／10/28，行情只使用 10/27，產生 15 檔候選。這是成功補跑，不冒稱準時自動完成。

GitHub 排程已觀察到延遲，不能保證分鐘級準時。原因沒有足夠證據判定；未將手動紀錄當作自動成功。

## 簡化後的操作

研究網站各頁頂端新增 **手動盤前分析**、**手動盤後分析**，並提供 **查看自動執行紀錄** 連結。

1. 開啟 [盤前入口](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/workflows/preopen.yml) 或 [盤後入口](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/workflows/postclose.yml)。
2. 登入有執行權限的 GitHub 帳號，按 **Run workflow → 綠色 Run workflow**；保留 `main`，不用填參數。
3. 工作完成後重新整理[研究網站](https://shen19772002-sketch.github.io/taiwan-stock-ai/)。

兩個入口呼叫原本 `research.yml`，沿用真實官方資料、分析引擎、歷史保存、重試及部署；原進階入口仍保留。網頁只導向 GitHub 的登入與執行介面，不在公開前端保存憑證。

## 實跑與網頁驗證

功能提交 `1120041`，兩個免填參數入口實際執行成功：

| 入口 | 執行紀錄 | 研究資料 |
| --- | --- | --- |
| 盤前 | [40640428790](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/runs/40640428790) | 研究日 10/28、行情日 10/27，15 檔候選，法人與行情日對齊 |
| 盤後 | [40640580217](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/runs/40640580217) | 研究日及最新行情日 10/28，當時來源未齊，0 檔候選、法人等待 10/28 資料 |

兩者研究與部署均成功，資料庫完整性通過。報告 `partial` 如實保留；程序成功不表示行情與法人已全部公布。

預設 TLS 驗證下載正式網站 4 個 HTML、4 個報告 JSON 與 3 個共享資源，皆 HTTP 200。各頁內嵌資料與 JSON 一致，兩個手動入口及自動紀錄連結均正確。以下載原始位元組在 localhost 提供給 Chromium，桌機 1440×1000、Android 390×844 的 4 頁均通過：保留 9 個內容區塊，按鈕高度至少 44px，無橫向溢位或 JavaScript 錯誤。沒有停用 TLS 驗證。

## 排隊修正

修正提交 `f9a81d0` 在共用 concurrency group 加入 `queue: max`，保留 `cancel-in-progress: false`。最多保留 100 筆等待工作，同時只有一筆可以寫回歷史並部署；滿 100 筆時 GitHub 仍會取消額外工作。使用者看到等待狀態時不必重複按執行。

依據 [GitHub 官方 concurrency 文件原始碼](https://github.com/github/docs/blob/3869faed240317911b720c484aef61931b8474ec/data/reusables/actions/actions-group-concurrency.md)及[功能版本設定](https://github.com/github/docs/blob/3869faed240317911b720c484aef61931b8474ec/data/features/actions-nga.yml)，此設定適用 GitHub.com。直接讀取文件網站遭環境代理拒絕，改由官方 `github/docs` 儲存庫讀取，沒有將第三方推測作為設定依據。

本機既有 220 項測試與 Ruff 通過；[功能 CI 40640410810](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/runs/40640410810)及[排隊修正 CI 40650607195](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/runs/40650607195)成功。加入新排隊語法前 actionlint 通過；最新 actionlint v1.7.12 尚不識別 `queue`，檢查僅回報此一未知欄位。GitHub 已接受工作流程並實際啟動，不能把舊 linter 的結果宣稱為全部通過。

三筆實際工作驗證了「一筆執行、兩筆同時等待」，之後全部完成，沒有相互取代取消：

| 執行 | 模式 | 研究與部署 |
| --- | --- | --- |
| [40650629242](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/runs/40650629242) | 盤前 | 成功，部署於 15:34:59 完成 |
| [40650666743](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/runs/40650666743) | 盤前 | 15:35:01 才開始研究，部署於 15:37:44 完成 |
| [40650670348](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/runs/40650670348) | 盤後 | 15:37:47 才開始研究，部署於 15:40:02 完成 |

15:40 再次下載並檢查最後部署：11 個公開檔案皆 HTTP 200，4 頁在桌機／Android 的 8 組畫面檢查通過。最新盤後報告產生於 15:38:40，取得 10/28 上市有效行情 1,088 檔，但來源尚未全部齊全、法人仍為 10/27，保留 `partial`、等待法人及 0 檔候選，沒有用昨日法人補成今日資料。

最後比對 `market-data`：原有 **19 份歷史報告全部存在、Git blob SHA 相同**；目前共 23 份。兩筆盤前排隊測試重用同一份 15:29:06 原報告，沒有把重新執行時間改寫成新的預測時間。資料分支驗證提交為 `75e38cc31e07d8fa6f465633b9bd00995b3c28b7`。

機器可讀證據：[manual-controls-2026-10-28.json](evidence/manual-controls-2026-10-28.json)，包含自動觸發與人工補跑的區分、三筆排隊狀態快照、遠端 CI、部署後網頁驗證及歷史檔案比對。

詳細使用及錯誤紀錄入口見[每日自動分析說明](daily-automation.md)。
