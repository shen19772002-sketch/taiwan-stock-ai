# 每日盤前與盤後自動分析

本設定沿用既有 `taiwan_stock_ai` 分析、SQLite、歷史資料分支及 GitHub Pages，不另建專案，不提供真實交易下單。

## 自動執行時間

工作流程為 `.github/workflows/research.yml` 的 **Daily Taiwan stock research**。執行環境設定 `TZ=Asia/Taipei`；GitHub cron 使用 UTC，因此以下時間已換算為 UTC+8。

| 台灣時間，週一至週五 | UTC cron | phase | 網頁 |
| --- | --- | --- | --- |
| 08:20 | `20 0 * * 1-5` | `preopen` | `preopen.html` |
| 12:30（保留既有功能） | `30 4 * * 1-5` | `intraday` | 首頁顯示既有盤後資料，不宣稱即時報價 |
| 15:30 | `30 7 * * 1-5` | `postclose` | `postclose.html` |
| 18:30、20:30 | `30 10 * * 1-5`、`30 12 * * 1-5` | `final` | 更新盤後頁及 `final.html` |

GitHub 排程可能延遲或漏跑，不能保證準點。排隊延遲不改變原 cron 所指定的研究階段。盤前只使用研究日之前的盤後行情；即使手動在收盤後執行，也不混入當日收盤價。手動執行時間會如實列示，不能視為當天 08:20 已發布的預測。

排程透過 `research_schedule.py` 讀取該 GitHub run 原始 `created_at`，推算建立時間之前最近一次符合 cron 的台灣研究日；跨午夜延遲與稍後重新執行不使用新的執行日期。GitHub 沒有提供精確的原訂觸發時間，因此這是明確的日期歸屬規則，並非準點保證。日期查詢失敗會記錄並重試，不能猜成當日。手動執行仍使用當日日期。

先查官方行事曆與兩交易所臨時休市公告；確認休市時不執行當日行情收集、選股模型或新增交易訊號，保留歷史資料並發布休市狀態。來源缺失時不將「沒有行情」當作已確認休市。

法人使用 TWSE T86 與 TPEx 三大法人明細，以實際交易日、股數與完整性判定。尚未公布、部分市場落後或行情／估值未齊全時，報告保留等待／部分資料狀態；18:30、20:30 再收集。超過最後一次排程才公布的資料會在次日或手動 `final` 更新，不保證公布後立即觸發。

## 手動執行

查看結果可直接開啟手機版的盤前／盤後研究，按 **檢查最新報告** 讀取已發布內容。需要重新計算時，展開 **需要重新分析？**，選擇 **到 GitHub 執行盤前／盤後**，也可以直接開啟：

- [手動盤前分析](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/workflows/preopen.yml)
- [手動盤後分析](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/workflows/postclose.yml)

登入有執行權限的 GitHub 帳號，按 **Run workflow → 綠色 Run workflow**。保留預設 `main` 分支即可，**不用選 phase、不用填 history_days、不用勾選其他參數**。完成後重新整理研究頁面。兩個入口共用原本的分析、歷史保存、重試及部署程序。

若顯示等待執行，請等待目前工作完成，不需重複按。自動與手動工作共用同一序列，採用 GitHub 的 `queue: max`，最多保留 100 筆等待工作，避免後送出的手動工作取代已排隊的自動工作；同時只允許一筆工作更新歷史與網站。

需要晚間補充、回補歷史或檢查來源時，仍可使用進階入口 [Daily Taiwan stock research](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/workflows/research.yml)，按 **Run workflow**：

1. Branch 選 `main`。
2. 盤前選 `phase=preopen`；盤後選 `phase=postclose`；補法人資料選 `phase=final`。
3. 本專案已保存歷史，`history_days=0`；一般更新不需勾選 `probe_sources`。
4. 按 Run workflow，等待 `research` 與 `deploy` 兩個工作完成。

實際分析入口是 `python -m taiwan_stock_ai run --phase preopen` 或 `--phase postclose`；工作流程經 `scripts/run_research.py` 執行此入口，沒有示範行情替代。

## 查看報告

- [最新研究首頁](https://shen19772002-sketch.github.io/taiwan-stock-ai/)
- [盤前研究](https://shen19772002-sketch.github.io/taiwan-stock-ai/preopen.html)
- [盤後研究及晚間補充](https://shen19772002-sketch.github.io/taiwan-stock-ai/postclose.html)
- [晚間完整資料更新](https://shen19772002-sketch.github.io/taiwan-stock-ai/final.html)

各頁顯示研究日、實際行情日、原始產生時間及來源狀態。晚間報告較新時，盤後頁顯示「盤後補充」，原始盤後報告仍在歷史中。沒有該階段報告時顯示等待，不借用其他階段內容。自選股仍只保存在瀏覽器，不上傳個人持股。

## 歷史、去重與重試

每次先還原遠端 `market-data`，完成分析及 SQLite 檢查後存回，再發布網頁。完整觀測、原始回應、歷史報告與最初預測持續保留；新增版本不刪除舊版。網站提供近期歷史及各時段頁面所引用的報告，完整封存留在資料分支。

相同研究日、階段及研究內容重跑時重用原報告 ID，保留原產生時間。擷取時間、原始檔路徑及歷史清單變化不製造新報告；行情、法人、分析或來源品質有實質變化才新增版本。當日預測從次日開始列入績效快照，不以重跑改寫初始預測。

官方網路暫時失敗最多請求兩次；分析程序、封存程序最多三次，間隔 20 秒並設超時。正常產生等待資料或休市頁的 exit 0 不會反覆重跑。還原失敗停止，不建立空歷史覆蓋遠端；應查看原因後重新執行整個 workflow。推送暫時失敗可重試同一已提交快照；遠端歷史衝突仍拒絕覆蓋，不使用 force push。

## 成功／失敗紀錄

在同一 Actions 頁面選一次執行：

- `research`／`deploy` 的綠色勾表示程序與部署成功；仍需看報告資料日期及來源完整性。
- 執行頁 Summary 顯示每個程序嘗試次數與最終 exit code；展開失敗步驟可讀錯誤。
- `research-logs-執行ID-嘗試次數`：90 天內保留每次嘗試 stdout／stderr 及 JSON 結果。初始化或還原失敗也會嘗試上傳診斷。
- `research-state-*`：還原成功後保留 30 天復原 artifact；`research-report-*`：分析成功後保留 30 天網頁 artifact。

Artifacts 是短期故障復原用途，永久歷史仍由 `market-data` 保存。不要刪除或重設此分支。

## 本次修改範圍

| 檔案 | 變更 |
| --- | --- |
| `.github/workflows/research.yml` | 明列台灣時區、真實入口、分階段排程、重試及診斷 artifact |
| `.github/workflows/preopen.yml`、`.github/workflows/postclose.yml` | 免填參數的手動入口，共用 `research.yml` |
| `scripts/run_research.py` | 保存各次程序輸出、exit code、超時與有限重試 |
| `scripts/research_schedule.py` | 以原始 GitHub run 推算跨午夜排程日期 |
| `scripts/persist_data.py` | 推送失敗後重試未推送提交，避免誤判為已保存 |
| `src/taiwan_stock_ai/pipeline.py` | 休市前置判斷、盤前日期限制、法人狀態、原始報告重用及各頁輸出 |
| `src/taiwan_stock_ai/providers.py` | 官方臨時休市公告、TWSE／TPEx 法人，以及最新有效來源選擇 |
| `src/taiwan_stock_ai/storage.py` | 保留歷史的 schema migration、語意去重、各階段最新選取指標 |
| `src/taiwan_stock_ai/cli.py` | 輸出階段、來源品質與法人日期供執行紀錄核對 |
| `src/taiwan_stock_ai/site.py`、`templates/index.html`、`static/app.js`、`static/style.css` | 獨立盤前／盤後／晚間頁、法人狀態與歷史階段標示；後三者同在套件目錄 |
| `tests/test_run_research.py`、`test_research_schedule.py`、`test_storage.py`、`test_site.py`、`test_pipeline.py`、`test_providers.py`、`test_persistence.py`、`test_market_readiness.py` | 重試、日期、去重、並行保存、來源、網頁及休市回歸測試；皆在 `tests/` |

安裝依賴與既有七個網頁功能保留。驗證結果見 [2026-10-28 每日排程驗證](daily-automation-validation.md)。
