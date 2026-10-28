# 台股 AI 智慧投資研究系統 V1.0

以 **TWSE／TPEx 官方盤後資料**建立的繁體中文研究網站。Python 收集與分析，GitHub Actions 每日更新，GitHub Pages 展示。V1 的「AI 選股」是可解釋規則模型，尚未啟用大型語言模型。

**研究網站：[開啟台股 AI 研究報告](https://shen19772002-sketch.github.io/taiwan-stock-ai/)**。2026-10-25 已完成正式 Pages 部署；實際行情日與研究日分開顯示，休市不產生當日新選股訊號。

每日自動更新：[盤前研究](https://shen19772002-sketch.github.io/taiwan-stock-ai/preopen.html)（08:20）、[盤後研究](https://shen19772002-sketch.github.io/taiwan-stock-ai/postclose.html)（15:30，18:30／20:30 補充）；時間皆為台灣平日排程，GitHub 實際啟動可能延遲。[手動執行、歷史保存與診斷方式](docs/daily-automation.md)。

免填參數手動更新：[執行盤前分析](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/workflows/preopen.yml)／[執行盤後分析](https://github.com/shen19772002-sketch/taiwan-stock-ai/actions/workflows/postclose.yml)。登入 GitHub，按 **Run workflow → 綠色 Run workflow**，完成後重新整理研究網站。

## 功能

- 上市／上櫃普通股行情、產業分類、PE／PB／殖利率；來源、日期與完整性可追溯。
- SMA20／60、RSI14、ATR14、動能、量比、市場廣度與產業強弱。
- 最多 15 檔符合條件的個股；每個評分有依據，缺資料不補造。
- ATR 觀察區間、突破、目標、停損及含費稅滑價的淨風報比。
- 繁中報告、Plotly 圖、Windows／Android 響應式頁面、本機自選與持股。
- SQLite 保存觀測修訂與原始預測；`market-data` 分支持續保存歷史。
- 已記錄訊號的 5／20 筆固定持有期追蹤，不冒充完整策略成交回測。

成交額占比是交易熱度代理，**不等於資金淨流入**。法人資料缺值不補分；新聞 AI、正式輪動預測及含公司行動的完整回測留待後續。系統僅供研究與模擬，不執行真實買賣。

## 安裝與執行

需要 Python 3.11 或 3.12。下載／clone 後，進入儲存庫目錄。

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock -e .
.venv\Scripts\python.exe -m taiwan_stock_ai run --phase manual
.venv\Scripts\python.exe -m http.server 8000 --directory site
```

Linux／macOS：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock -e .
.venv/bin/python -m taiwan_stock_ai run --phase manual
.venv/bin/python -m http.server 8000 --directory site
```

瀏覽本機服務的 8000 埠即可查看網站；停止按 Ctrl+C。下列指令假設已啟用虛擬環境，或將 `python` 換成 `.venv` 的 Python 路徑。網站位於 `site/`，資料位於 `data/`，均不提交至原始碼分支。

## 首次歷史暖機

只有一日行情不能計算 60 日策略。可逐日累積，或從官方指定日期端點回補約 100 個日曆日。請依執行當日調整日期；以下是開發驗證日期範例：

```bash
python -m taiwan_stock_ai backfill --start 2026-07-15 --end 2026-10-22
python -m taiwan_stock_ai run --phase manual
```

回補至少間隔一秒逐次請求，可能需要數分鐘；逐日保存，中斷不刪除已匯入資料。每份回應須有正確交易日期，缺資料不填值。回補使用**目前**公司名單暖機，不能宣稱無存活者偏差的歷史全市場回測，也不補造過去預測。

## 常用指令

```bash
python -m taiwan_stock_ai probe
python -m taiwan_stock_ai run --phase preopen
python -m taiwan_stock_ai run --phase postclose
python -m taiwan_stock_ai run --phase final
python -m taiwan_stock_ai run --offline
python -m taiwan_stock_ai check-db
python -m taiwan_stock_ai backup /path/to/backup.sqlite3
python -m pytest -q
python -m ruff check src tests scripts
```

`run` 在資料未公布時仍成功產生等待／部分資料頁，不能只以 exit 0 判定行情完整。`probe` 沒有可驗證行情時回傳 exit 2。透過網頁、`data/latest.json` 與原始 manifest 檢查來源結果。

`--as-of YYYY-MM-DD` 限制資料日期，不偽造當時已知資料，也不是完整歷史回測。最新行情日期與執行日期不同時，頁面清楚列示。

## GitHub 自動執行與展示

1. 將程式推送至 `main`。
2. **Settings → Pages → Source** 選擇 **GitHub Actions**。
3. 確認儲存庫／組織允許工作流程寫入 `market-data` 分支及部署 Pages。
4. **Actions → Daily Taiwan stock research → Run workflow**。若 `market-data` 已保存足夠歷史，填 `history_days=0`；只有尚未暖機的新副本才填 `100`。
5. research 與 deploy 均成功後，使用 GitHub 提供的 Pages 網址。

台灣時間：08:20 盤前、12:30 盤中排程（V1 無即時來源，保留盤後資訊）、15:30 初報、18:30／20:30 晚間重試。休市跳過新訊號；尚未公布顯示等待。GitHub 排程可能延遲，不是準時行情服務。

每次先還原 `market-data`，更新後保存 SQLite、原始回應與報告。SQLite 壓縮分塊並驗證雜湊，避免單檔限制；另保留 30 天復原 artifact。**不要刪除或重設資料分支**。詳細權限與失敗復原見[部署說明](docs/deployment.md)。

公開行情不需自行提供 API 金鑰。目前無 OpenAI 呼叫；未來金鑰只能放 GitHub Secrets／安全環境設定，不可放前端或提交 `.env`。

## 驗證與文件

- [系統架構與時序](docs/architecture.md)
- [官方 API 實測](docs/api-validation.md)
- [評分、價位與風險模型](docs/models.md)
- [每日盤前／盤後設定與驗證](docs/daily-automation.md)
- [部署與歷史保存](docs/deployment.md)
- [開發驗證紀錄](docs/validation.md)
- [下週交易日與啟用檢查](docs/market-readiness.md)
- [GitHub 遠端部署與正式網站驗證](docs/deployment-validation.md)

2026-10-24 已完成兩市場各 69 個交易日回補、134,446 筆去重行情、15 檔實際研究名單及桌面／手機圖表驗證。本次名單資料日為 2026-10-22；遠端部署狀態與測試證據見驗證文件。可用 `python scripts/validate_research.py` 重驗現有研究快照；瀏覽器完整檢查使用 `python scripts/browser_smoke.py --require-research`。

測試中的合成資料不會作為行情備援或網頁展示。瀏覽器驗證另安裝 `python -m pip install -e '.[browser]'`，啟動本機網站後執行 `python scripts/browser_smoke.py --chromium /path/to/chromium`。

已接入 TWSE T86／TPEx 三大法人明細，依實際日期與完整性使用。V1 尚無完整財報公布時點、除權息還原與下市資料，不宣稱完整獲利品質或歷史超額績效。先補齊這些資料，再擴充正式回測、樣本外驗證、新聞題材與深度 AI 研究。
