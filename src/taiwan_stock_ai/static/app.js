"use strict";

(() => {
  const report = JSON.parse(document.getElementById("report-data").textContent);
  const analysis = report.analysis || {};
  const candidates = analysis.candidates || [];
  const stocks = analysis.stocks || [];
  const byKey = new Map([...stocks, ...candidates].map(stock => [key(stock), stock]));
  const $ = id => document.getElementById(id);
  const number = (value, digits = 2) => value !== null && value !== undefined && Number.isFinite(Number(value)) ? Number(value).toLocaleString("zh-TW", {minimumFractionDigits: digits, maximumFractionDigits: digits}) : "—";
  const percent = (value, digits = 1) => value !== null && value !== undefined && Number.isFinite(Number(value)) ? `${number(Number(value) * 100, digits)}%` : "—";
  const labels = {ready: "分析完成", partial: "資料尚未齊全", waiting: "等待資料", non_trading_day: "非交易日", pending: "待後續驗證", completed: "已完成觀察", evaluated: "已完成觀察", excluded_unadjusted: "未調整異常，排除"};
  const phaseLabels = {preopen: "盤前研究", intraday: "盤中排程", postclose: "盤後初報", final: "完整資料更新", manual: "手動研究"};
  const storageKey = "taiwan-stock-ai-watchlist-v1";
  let watchlist = readWatchlist();

  function key(stock) { return `${stock.market}:${stock.symbol}`; }
  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function cell(row, text, className) { const node = el("td", text, className); row.append(node); return node; }
  function asLines(value) {
    if (!value) return [];
    if (Array.isArray(value)) return value.map(item => typeof item === "object" ? JSON.stringify(item) : String(item));
    if (typeof value === "object") return Object.entries(value).map(([name, item]) => `${name}：${typeof item === "object" ? JSON.stringify(item) : item}`);
    return [String(value)];
  }
  function validWatch(item) {
    return item && /^(TWSE|TPEX)$/.test(item.market) && /^[0-9]{4,6}$/.test(item.symbol) && Number.isFinite(item.quantity) && item.quantity >= 0 && Number.isInteger(item.quantity) && Number.isFinite(item.cost) && item.cost >= 0;
  }
  function readWatchlist() {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || "[]");
      return Array.isArray(saved) ? saved.filter(validWatch).slice(0, 200) : [];
    } catch (_) { return []; }
  }
  function saveWatchlist() {
    try { localStorage.setItem(storageKey, JSON.stringify(watchlist)); $("watch-feedback").textContent = "已儲存在目前瀏覽器。"; }
    catch (_) { $("watch-feedback").textContent = "瀏覽器無法儲存資料；此次觀察清單只保留到頁面關閉。"; }
    renderWatchlist();
    renderRanking();
  }
  function renderRanking() {
    const body = $("ranking-body");
    body.replaceChildren();
    const search = $("stock-search").value.trim().toLowerCase();
    const filtered = candidates.filter(stock => `${stock.symbol} ${stock.name} ${stock.industry}`.toLowerCase().includes(search));
    filtered.forEach(stock => {
      const row = el("tr");
      cell(row, String(candidates.indexOf(stock) + 1).padStart(2, "0"), "rank");
      const nameCell = cell(row);
      const button = el("button", null, "stock-button");
      button.type = "button";
      button.append(el("span", `${stock.symbol} ${stock.name}`, "stock-name"), el("span", `${stock.market === "TPEX" ? "上櫃" : "上市"} · ${stock.industry || "未分類"}`, "stock-meta"));
      button.addEventListener("click", () => { $("stock-select").value = key(stock); renderDetail(stock); $("stock-detail").scrollIntoView({behavior: "smooth"}); });
      nameCell.append(button);
      cell(row, number(stock.close));
      cell(row).append(el("span", number(stock.score, 1), "score-pill"));
      cell(row, number(stock.indicators?.rsi14, 1));
      cell(row, number(stock.plan?.net_risk_reward));
      cell(row, stock.date || "—");
      const saved = watchlist.some(item => key(item) === key(stock));
      const watch = el("button", saved ? "★" : "☆", `icon-button${saved ? " selected" : ""}`);
      watch.type = "button";
      watch.setAttribute("aria-label", `${saved ? "已觀察" : "加入觀察"} ${stock.symbol}`);
      watch.addEventListener("click", () => {
        if (!saved && watchlist.length < 200) { watchlist.push({market: stock.market, symbol: stock.symbol, quantity: 0, cost: 0}); saveWatchlist(); }
        else { $("watchlist").scrollIntoView({behavior: "smooth"}); }
      });
      cell(row).append(watch);
      body.append(row);
    });
    $("search-empty").hidden = !candidates.length || Boolean(filtered.length);
  }

  function renderDetail(stock) {
    $("detail-empty").hidden = Boolean(stock);
    $("detail-content").hidden = !stock;
    if (!stock) return;
    $("detail-market").textContent = `${stock.market === "TPEX" ? "上櫃" : "上市"} · ${stock.industry || "未分類"}`;
    $("detail-name").textContent = `${stock.symbol} ${stock.name}`;
    $("detail-close").textContent = number(stock.close);
    $("detail-date").textContent = `交易日 ${stock.date || "待確認"} · 官方盤後資料 · 新臺幣`;
    $("detail-quality").hidden = Boolean(stock.eligible && stock.plan);
    $("detail-quality").textContent = asLines(stock.exclusions).join("；") || "歷史、基本面或風險條件尚未齊全，暫不產生操作價位。";
    const plan = stock.plan;
    const priceGrid = $("price-plan");
    priceGrid.replaceChildren();
    const prices = [
      ["建議觀察區間", plan ? `${number(plan.entry_low)} — ${number(plan.entry_high)}` : "等待條件", ""],
      ["突破觀察價", number(plan?.breakout), ""],
      ["目標價", number(plan?.target), "target"],
      ["停損價", number(plan?.stop), "stop"],
      ["風險報酬比", number(plan?.risk_reward), ""],
      ["扣除成本後風報比", number(plan?.net_risk_reward), "target"],
    ];
    prices.forEach(([name, value, className]) => { const card = el("div", null, `price-item ${className}`); card.append(el("span", name), el("strong", value)); priceGrid.append(card); });
    $("detail-conditions").replaceChildren();
    const conditions = [...asLines(plan?.conditions), ...asLines(plan?.invalidation).map(item => `策略失效：${item}`)];
    (conditions.length ? conditions : ["等待價格歷史與基本面條件完成後提供策略。"]).forEach(item => $("detail-conditions").append(el("li", item)));
    $("detail-indicators").replaceChildren();
    const indicators = [["20 日均線", stock.indicators?.sma20], ["60 日均線", stock.indicators?.sma60], ["RSI 14", stock.indicators?.rsi14], ["ATR 14", stock.indicators?.atr14], ["20 日動能", stock.indicators?.momentum20, true], ["量比", stock.indicators?.volume_ratio], ["本益比", stock.pe], ["股價淨值比", stock.pb], ["殖利率（%）", stock.dividend_yield]];
    indicators.forEach(([name, value, isPercent]) => { const item = el("div"); item.append(el("dt", name), el("dd", isPercent ? percent(value) : number(value))); $("detail-indicators").append(item); });
    $("detail-scores").replaceChildren();
    (stock.score_components || []).forEach(component => {
      const item = el("div", null, "score-row");
      const title = el("div", null, "score-title");
      title.append(el("span", component.name), el("span", component.available ? `${number(component.score, 1)} / ${number(component.max_score, 0)}` : "缺少資料"));
      const bar = el("div", null, "score-bar");
      const fill = el("span");
      fill.style.width = `${component.available && component.max_score > 0 ? Math.max(0, Math.min(100, 100 * component.score / component.max_score)) : 0}%`;
      bar.append(fill);
      item.append(title, bar, el("p", component.explanation || ""));
      $("detail-scores").append(item);
    });
    $("detail-coverage").textContent = `資料覆蓋：${typeof stock.coverage === "number" ? percent(stock.coverage) : asLines(stock.coverage).join("；") || "待確認"}。正本益比僅作為歷史獲利代理，不代表未來獲利。`;
  }

  function renderWatchlist() {
    const body = $("watch-body");
    body.replaceChildren();
    if (!watchlist.length) { const row = el("tr"); const empty = cell(row, "尚未加入觀察股票。可從研究名單加入，或輸入代號與持股資料。", "empty-cell"); empty.colSpan = 7; body.append(row); return; }
    watchlist.forEach(item => {
      const stock = byKey.get(key(item));
      const row = el("tr");
      cell(row, `${item.symbol} ${stock?.name || (item.market === "TPEX" ? "上櫃" : "上市")}`);
      cell(row, number(stock?.close));
      cell(row, number(item.quantity, 0));
      cell(row, item.cost > 0 ? number(item.cost) : "未輸入");
      const pnl = stock?.close != null && item.quantity > 0 && item.cost > 0 ? (stock.close - item.cost) * item.quantity : null;
      cell(row, number(pnl), pnl === null ? "muted" : pnl >= 0 ? "up" : "down");
      cell(row, stock?.date || "無可驗證行情");
      const remove = el("button", "移除", "icon-button");
      remove.type = "button";
      remove.setAttribute("aria-label", `移除 ${item.symbol}`);
      remove.addEventListener("click", () => { watchlist = watchlist.filter(watch => key(watch) !== key(item)); saveWatchlist(); });
      cell(row).append(remove);
      body.append(row);
    });
  }

  function renderThemes() {
    const themes = analysis.themes || [];
    const grid = $("theme-grid");
    if (!themes.length) { grid.append(el("div", "尚無足夠資料辨識產業階段。待動能與成交歷史累積後，才會提出下一階段觀察假說。", "empty-theme")); return; }
    themes.forEach((theme, index) => {
      const card = el("article", null, "theme-card");
      card.append(el("span", String(index + 1).padStart(2, "0"), "theme-number"), el("span", theme.stage || theme.lifecycle || theme.status || "研究假說", "badge"), el("h3", theme.industry || theme.name || theme.theme || "產業觀察"));
      asLines(theme.reason || theme.explanation || theme.hypothesis || theme.rationale).forEach(line => card.append(el("p", line)));
      asLines(theme.conditions || theme.confirmation || theme.invalidation).forEach(line => card.append(el("p", line)));
      grid.append(card);
    });
  }

  function renderIndustries() {
    const industries = (analysis.industries || []).filter(item => item.momentum20 != null && Number.isFinite(Number(item.momentum20))).slice(0, 10).reverse();
    const chart = $("industry-chart");
    chart.hidden = !industries.length;
    if (!industries.length) { $("industry-chart-empty").hidden = false; return; }
    if (!window.Plotly) { $("industry-chart-empty").hidden = false; $("industry-chart-empty").textContent = "互動圖表載入失敗，產業數據仍可於右側表格查看。"; return; }
    $("industry-chart-empty").hidden = true;
    const values = industries.map(item => Number(item.momentum20) * 100);
    Plotly.newPlot(chart, [{type: "bar", orientation: "h", x: values, y: industries.map(item => item.industry), marker: {color: values.map(value => value >= 0 ? "#62d3b3" : "#edc27b")}, hovertemplate: "%{y}<br>20 日動能 %{x:.2f}%<extra></extra>"}], {autosize: true, height: 240, paper_bgcolor: "transparent", plot_bgcolor: "transparent", font: {family: "Microsoft JhengHei, sans-serif", color: "#8da3b1", size: 10}, margin: {l: 105, r: 15, t: 4, b: 30}, xaxis: {ticksuffix: "%", gridcolor: "#243540", zerolinecolor: "#42606c"}, yaxis: {automargin: true}, bargap: 0.45}, {responsive: true, displayModeBar: false});
  }

  function renderPerformance() {
    const performance = report.performance || {};
    const container = $("performance-summary");
    const total = performance.total_signals ?? performance.signals ?? performance.signal_count ?? 0;
    const evaluated = performance.evaluated ?? performance.completed ?? performance.completed_count ?? 0;
    const grid = el("div", null, "performance-grid");
    [["保留原始訊號", number(total, 0)], ["完成 20 筆觀察", number(evaluated, 0)], ["20 筆觀察勝率", evaluated > 0 ? percent(performance.win_rate) : "等待驗證"], ["20 筆平均淨報酬", evaluated > 0 ? percent(performance.average_return ?? performance.mean_net_return) : "等待驗證"]].forEach(([name, value]) => { const item = el("div"); item.append(el("span", name), el("strong", value)); grid.append(item); });
    container.append(grid);
    asLines(performance.limitations || performance.note || performance.notes).forEach(line => container.append(el("p", line, "method-note")));
    const history = report.history || [];
    const body = $("history-body");
    history.slice(0, 30).forEach(item => {
      const row = el("tr");
      const dateCell = cell(row);
      if (/^[a-f0-9]{64}$/.test(item.id || "")) {
        const link = el("a", `${item.as_of} ↓`);
        link.href = `history/${item.id}.json`;
        link.setAttribute("download", `research-${item.as_of}-${item.phase || "manual"}.json`);
        dateCell.append(link);
      } else dateCell.textContent = item.as_of || item.date || "—";
      cell(row, phaseLabels[item.phase] || item.phase || "未記錄時段");
      const recorded = item.generated_at ? new Date(item.generated_at) : null;
      cell(row, recorded && Number.isFinite(recorded.getTime()) ? recorded.toLocaleString("zh-TW", {timeZone: "Asia/Taipei", hour12: false}) : "—");
      cell(row, labels[item.status] || item.status || "已儲存");
      cell(row, number(item.candidate_count ?? item.candidates_count, 0));
      body.append(row);
    });
    if (!history.length) { const row = el("tr"); const empty = cell(row, "尚無歷史研究紀錄。", "empty-cell"); empty.colSpan = 5; body.append(row); }
    const recordsBody = $("performance-records");
    const records = performance.records || [];
    records.slice(-100).reverse().forEach(item => {
      const row = el("tr");
      cell(row, `${item.symbol} ${item.name || ""}`);
      cell(row, item.signal_date || "—");
      cell(row, item.entry_date || "等待交易日");
      cell(row, item.exit_date || "尚未結束");
      cell(row, item.horizon ? `${item.horizon} 筆交易資料` : "—");
      cell(row, ["completed", "evaluated"].includes(item.status) ? percent(item.net_return, 2) : item.status === "excluded_unadjusted" ? "排除計算" : "尚未完成");
      cell(row, labels[item.status] || item.status || "待後續驗證");
      recordsBody.append(row);
    });
    if (!records.length) { const row = el("tr"); const empty = cell(row, "尚無可追蹤的原始訊號；不產生模擬績效數字。", "empty-cell"); empty.colSpan = 7; recordsBody.append(row); }
  }

  $("stock-search").addEventListener("input", renderRanking);
  $("stock-select").replaceChildren();
  if (!byKey.size) { const option = el("option", "尚無可驗證股票"); option.value = ""; $("stock-select").append(option); }
  [...byKey.entries()].sort((left, right) => left[1].symbol.localeCompare(right[1].symbol)).forEach(([id, stock]) => { const option = el("option", `${stock.symbol} ${stock.name}`); option.value = id; $("stock-select").append(option); });
  const firstStock = candidates[0] || stocks[0];
  if (firstStock) $("stock-select").value = key(firstStock);
  $("stock-select").addEventListener("change", event => renderDetail(byKey.get(event.target.value)));
  $("watch-form").addEventListener("submit", event => {
    event.preventDefault();
    const item = {market: $("watch-market").value, symbol: $("watch-symbol").value.trim(), quantity: Number($("watch-quantity").value || 0), cost: Number($("watch-cost").value || 0)};
    if (!validWatch(item)) { $("watch-feedback").textContent = "請輸入有效股票代號、非負整數股數及非負成本。"; return; }
    if (watchlist.length >= 200 && !watchlist.some(existing => key(existing) === key(item))) { $("watch-feedback").textContent = "觀察清單上限為 200 檔。"; return; }
    watchlist = watchlist.filter(existing => key(existing) !== key(item));
    watchlist.push(item);
    saveWatchlist();
    $("watch-form").reset();
  });
  const methodology = $("methodology-content");
  const methodLabels = {technical: "技術模型", fundamentals: "基本面模型", institutions: "法人資料", industry: "產業模型", scoring: "綜合評分", costs: "成本假設", limitations: "模型限制", eligibility: "入選條件", data: "資料規則", version: "模型版本"};
  const methods = analysis.methodology || {};
  if (Array.isArray(methods)) methods.forEach(item => methodology.append(el("p", typeof item === "object" ? JSON.stringify(item) : item)));
  else Object.entries(methods).forEach(([name, value]) => { methodology.append(el("strong", methodLabels[name] || name)); asLines(value).forEach(line => methodology.append(el("p", line))); });
  if (!methodology.childElementCount) methodology.append(el("p", "選股至少需要 60 個有效交易日。技術趨勢、動能、估值與風險報酬條件均須具備可驗證資料；缺漏項目不冒充已知資料。"));
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(entries => entries.forEach(entry => { if (entry.isIntersecting) document.querySelectorAll(".nav-link").forEach(link => link.classList.toggle("active", link.getAttribute("href") === `#${entry.target.id}`)); }), {rootMargin: "-140px 0px -55% 0px"});
    document.querySelectorAll("main > section[id]").forEach(section => observer.observe(section));
  }
  renderRanking();
  renderDetail(firstStock);
  renderWatchlist();
  renderThemes();
  renderPerformance();
  renderIndustries();
})();
