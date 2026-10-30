"use strict";

// Loaded before the stylesheet so a saved preference applies before painting.
// This preference is local to this browser and never changes research data.
(() => {
  const storageKey = "taiwan-stock-ai-theme-v1";
  const themes = new Set(["dark", "light", "sepia"]);
  const browserColors = {dark: "#0b131b", light: "#f4f7fa", sepia: "#f4eddf"};
  const root = document.documentElement;
  const normalize = value => themes.has(value) ? value : "dark";
  let current = "dark";
  try { current = normalize(localStorage.getItem(storageKey)); }
  catch (_) { /* Reading remains available when browser storage is disabled. */ }

  function apply(value, save = false) {
    current = normalize(value);
    root.dataset.theme = current;
    root.style.colorScheme = current === "dark" ? "dark" : "light";
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", browserColors[current]);
    const select = document.getElementById("theme-select");
    if (select) select.value = current;
    if (save) {
      try { localStorage.setItem(storageKey, current); }
      catch (_) { /* Keep the selected theme for this page even without storage. */ }
    }
    document.dispatchEvent(new CustomEvent("themechange", {detail: {theme: current}}));
  }

  apply(current);
  function bind() {
    const select = document.getElementById("theme-select");
    if (!select) return;
    select.value = current;
    select.addEventListener("change", () => apply(select.value, true));
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind, {once: true});
  else bind();

  window.addEventListener("storage", event => {
    if (event.key === storageKey || event.key === null) apply(event.newValue);
  });
})();
