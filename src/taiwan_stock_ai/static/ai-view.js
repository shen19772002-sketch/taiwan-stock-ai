"use strict";

(() => {
  const data = document.getElementById("ai-view-data");
  const list = document.getElementById("ai-observation-list");
  if (!data || !list) return;
  const view = JSON.parse(data.textContent);
  const observations = view.observations || [];
  const candidates = new Set((view.is_ai_report ? view.candidates || [] : []).map(key));
  const search = document.getElementById("ai-observation-search");
  const empty = document.getElementById("ai-observation-empty");
  let category = "all";
  function key(stock) { return `${stock.market}:${stock.symbol}`; }
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined && text !== null) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function number(value, digits = 2) {
    return value !== null && value !== undefined && Number.isFinite(Number(value))
      ? Number(value).toLocaleString("zh-TW", {maximumFractionDigits: digits}) : "—";
  }
  function isWatched(stock) {
    try {
      const saved = JSON.parse(localStorage.getItem("taiwan-stock-ai-watchlist-v1") || "[]");
      return Array.isArray(saved) && saved.some(item => key(item) === key(stock));
    } catch (_) { return false; }
  }
  function stockState(stock) {
    if (candidates.has(key(stock)) && stock.plan) return "符合本次條件 · 查看策略與風險";
    if (stock.date !== view.data_date) return "保留原行情日 · 資料日期尚待對齊";
    if (stock.fundamental_status !== "current") return "等待同日估值 · 暫不產生策略";
    if (!stock.indicators) return "技術歷史不足 · 保留已驗證行情";
    return "尚未符合趨勢或風險條件 · 可查看依據";
  }
  function render() {
    const query = (search?.value || "").trim().toLowerCase();
    const filtered = observations.filter(stock => (
      category === "all" || (stock.ai_category_ids || []).includes(category)
    ) && `${stock.symbol} ${stock.name} ${(stock.ai_categories || []).join(" ")}`.toLowerCase().includes(query));
    list.replaceChildren();
    filtered.forEach(stock => {
      const card = node("article", null, "ai-stock-card");
      card.dataset.stockKey = key(stock);
      const main = node("div", null, "ai-stock-main");
      const heading = node("div", null, "ai-stock-heading");
      heading.append(node("strong", stock.name, "ai-stock-name"), node("span", stock.symbol, "ai-stock-code"));
      const tags = node("div", null, "ai-stock-tags");
      (stock.ai_categories || []).forEach(name => tags.append(node("span", name, "ai-stock-tag")));
      const meta = node("div", null, "ai-stock-meta");
      meta.append(node("span", `收盤 ${stock.date || "待取得"}`), node("span", `${stock.bars || 0} 筆完整歷史`));
      main.append(heading, tags, meta);
      const price = node("div", null, "ai-stock-price");
      const change = stock.daily_return;
      price.append(node("strong", number(stock.close)), node("span", change === null || change === undefined
        ? "漲跌待比較" : `${change > 0 ? "+" : ""}${number(change * 100)}%`,
      `ai-stock-return ${change > 0 ? "up" : "down"}`));
      const footer = node("div", null, "ai-stock-footer");
      footer.append(node("span", stockState(stock), "ai-stock-state"));
      const actions = node("div", null, "ai-stock-actions");
      const detail = node("a", "查看依據 ›", "ai-stock-detail");
      detail.href = "#stock-detail"; detail.dataset.aiDetail = key(stock);
      detail.addEventListener("click", event => {
        event.preventDefault();
        document.dispatchEvent(new CustomEvent("ai:select", {detail: {key: key(stock)}}));
      });
      const watched = isWatched(stock);
      const watch = node("button", watched ? "★" : "☆", "ai-stock-watch");
      watch.type = "button"; watch.dataset.aiWatch = key(stock);
      watch.setAttribute("aria-label", `${watched ? "已觀察" : "加入觀察"} ${stock.symbol}`);
      watch.disabled = watched;
      watch.addEventListener("click", () => document.dispatchEvent(new CustomEvent("ai:watch", {detail: {key: key(stock)}})));
      actions.append(detail, watch); footer.append(actions);
      card.append(main, price, footer); list.append(card);
    });
    empty.hidden = filtered.length > 0;
    if (!filtered.length) empty.textContent = observations.length
      ? "沒有符合這個類別或搜尋條件的觀察股票。未驗證的公司不自動補入。"
      : "目前沒有本研究日可驗證的 AI 觀察行情；不補入示範股票。";
  }
  document.querySelectorAll("#ai-category-filters [data-ai-category]").forEach(button => {
    button.addEventListener("click", () => {
      category = button.dataset.aiCategory;
      document.querySelectorAll("#ai-category-filters [data-ai-category]").forEach(item => {
        item.classList.toggle("active", item === button);
        item.setAttribute("aria-pressed", String(item === button));
      });
      render();
    });
  });
  search?.addEventListener("input", render);
  document.querySelectorAll("[data-ai-category-link]").forEach(link => {
    link.addEventListener("click", () => {
      const target = [...document.querySelectorAll("#ai-category-filters [data-ai-category]")]
        .find(button => button.dataset.aiCategory === link.dataset.aiCategoryLink);
      target?.click();
    });
  });
  document.addEventListener("ai:watch-updated", render);
  window.addEventListener("storage", render);
  render();
})();
