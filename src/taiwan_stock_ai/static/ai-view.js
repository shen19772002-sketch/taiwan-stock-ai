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
  let expanded = false;
  const initialLimit = 6;
  const listSummary = node("div", null, "ai-list-summary");
  const listCount = node("p", null, "ai-list-count");
  listCount.id = "ai-observation-count";
  const more = node("button", null, "secondary-button ai-list-more");
  more.id = "ai-observation-more"; more.type = "button";
  more.setAttribute("aria-controls", list.id);
  more.addEventListener("click", () => {
    const priorScroll = window.scrollY;
    expanded = !expanded;
    render();
    requestAnimationFrame(() => {
      if (expanded) window.scrollTo({top: priorScroll, behavior: "instant"});
      else listSummary.scrollIntoView({block: "center", behavior: "instant"});
    });
  });
  listSummary.append(listCount, more);
  list.after(listSummary);
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
    if (stock.date !== view.data_date) return "行情待更新";
    if (stock.fundamental_status !== "current") return "估值待補";
    if (!stock.indicators) return "歷史不足";
    if (candidates.has(key(stock)) && stock.plan) return "符合選股條件";
    if (!view.is_ai_report) return "舊報告觀察";
    return "未符合選股條件";
  }
  function render() {
    const query = (search?.value || "").trim().toLowerCase();
    const filtered = observations.filter(stock => (
      category === "all" || (stock.ai_category_ids || []).includes(category)
    ) && `${stock.symbol} ${stock.name} ${(stock.ai_categories || []).join(" ")}`.toLowerCase().includes(query));
    const isAll = category === "all" && !query;
    const limited = isAll && !expanded && filtered.length > initialLimit;
    const visible = limited ? filtered.slice(0, initialLimit) : filtered;
    listCount.textContent = limited
      ? `先顯示 ${visible.length} / ${filtered.length} 家 · 依代號排列`
      : `共 ${filtered.length} 家${isAll ? " · 依代號排列" : ""}`;
    more.hidden = !isAll || filtered.length <= initialLimit;
    more.textContent = expanded ? `收合為 ${initialLimit} 家` : `查看全部 ${filtered.length} 家`;
    more.setAttribute("aria-expanded", String(expanded));
    list.replaceChildren();
    visible.forEach(stock => {
      const card = node("article", null, "ai-stock-card");
      card.dataset.stockKey = key(stock);
      const main = node("div", null, "ai-stock-main");
      const heading = node("div", null, "ai-stock-heading");
      heading.append(node("strong", stock.name, "ai-stock-name"), node("span", stock.symbol, "ai-stock-code"));
      const tags = node("div", null, "ai-stock-tags");
      if (stock.ai_category) tags.append(node("span", stock.ai_category, "ai-stock-tag"));
      const meta = node("div", null, "ai-stock-meta");
      meta.append(node("span", `收盤 ${stock.date || "待取得"}`));
      main.append(heading, tags, meta);
      const price = node("div", null, "ai-stock-price");
      const change = stock.daily_return;
      const direction = change > 0 ? "up" : change < 0 ? "down" : "muted";
      price.append(node("strong", number(stock.close)), node("span", change === null || change === undefined
        ? "漲跌待比較" : `${change > 0 ? "+" : ""}${number(change * 100)}%`,
      `ai-stock-return ${direction}`));
      const footer = node("div", null, "ai-stock-footer");
      footer.append(node("span", stockState(stock), "ai-stock-state"));
      const actions = node("div", null, "ai-stock-actions");
      const detail = node("a", "看詳情 ›", "ai-stock-detail");
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
