"use strict";

(() => {
  const byId = id => document.getElementById(id);
  const pages = new Set(["index", "preopen", "postclose", "final"]);
  const page = document.body.dataset.page;
  const base = new URL("./", document.baseURI);
  if (!pages.has(page) || base.origin !== window.location.origin) return;

  const pageURL = new URL(`${page}.html`, base);
  const installButton = byId("pwa-install");
  const installHelp = byId("pwa-install-help");
  const updateButton = byId("pwa-update");
  const refreshButton = byId("report-refresh");
  const revision = document.body.dataset.pageRevision || "";
  const statusLabels = {ready: "分析完成", partial: "資料尚未齊全", waiting: "等待官方資料", non_trading_day: "非交易日"};
  let currentReport = {};
  try { currentReport = JSON.parse(byId("report-data")?.textContent || "{}"); } catch (_) { /* A static status panel still works without report JSON. */ }
  let deferredInstall = null;
  let registration = null;
  let updateRequested = false;
  let refreshing = false;
  // Controlled navigations are already cached by the worker. Only the first,
  // uncontrolled visit needs a separate request to retain its public report.
  let cacheRequested = Boolean(navigator.serviceWorker?.controller);
  let cachedReport = document.body.dataset.reportCache === "offline";
  let installed = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;

  function setText(id, message) {
    const node = byId(id);
    if (node) node.textContent = message;
  }

  function dateLabel(value) {
    return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : "尚無可驗證行情";
  }

  function syncOfflineNotice() {
    const notice = byId("pwa-offline");
    if (!notice) return;
    notice.hidden = window.navigator.onLine && !cachedReport;
    if (!notice.hidden) {
      const prefix = cachedReport ? "目前顯示本機儲存的報告。" : "目前離線，畫面保留已載入的報告。";
      notice.textContent = `${prefix}行情日：${dateLabel(currentReport.data_date)}。這不是即時行情；連線恢復後請按「檢查最新報告」。`;
      const workerNotice = byId("pwa-worker-cache-notice");
      if (workerNotice) workerNotice.hidden = true;
    }
  }

  function syncInstallButton() {
    if (!installButton) return;
    installButton.disabled = installed;
    installButton.textContent = installed ? "已加入主畫面" : "安裝到手機";
    if (installed && installHelp) installHelp.textContent = "可從手機主畫面開啟；分析仍由雲端排程執行。";
  }

  function manualInstallHelp() {
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent);
    if (installHelp) installHelp.textContent = ios
      ? "請用 Safari 開啟本頁，按分享按鈕，再選「加入主畫面」。"
      : "請用 Android Chrome 開啟本頁，按右上角「⋮」，選「安裝應用程式」或「加到主畫面」。若在其他 App 內開啟，請先改用瀏覽器。";
    setText("pwa-status", "若瀏覽器沒有提供安裝視窗，仍可直接使用本頁查看報告。");
  }

  window.addEventListener("beforeinstallprompt", event => {
    event.preventDefault();
    deferredInstall = event;
    syncInstallButton();
    if (installHelp) installHelp.textContent = "按「安裝到手機」，依瀏覽器提示加入主畫面，不用下載 APK。";
  });
  window.addEventListener("appinstalled", () => {
    installed = true;
    deferredInstall = null;
    syncInstallButton();
    setText("pwa-status", "已加入主畫面，可從「台股研究」圖示開啟。");
  });
  installButton?.addEventListener("click", async () => {
    if (installed) return;
    if (!deferredInstall) { manualInstallHelp(); return; }
    const prompt = deferredInstall;
    deferredInstall = null;
    installButton.disabled = true;
    try {
      await prompt.prompt();
      const choice = await prompt.userChoice;
      setText("pwa-status", choice.outcome === "accepted"
        ? "已接受安裝，完成後可從手機主畫面開啟。"
        : "已取消安裝；你仍可直接使用網站，也可以稍後再安裝。");
    } catch (_) {
      manualInstallHelp();
    } finally {
      syncInstallButton();
    }
  });

  function navigateToPublished(nextRevision) {
    const destination = new URL(pageURL);
    destination.searchParams.set("v", nextRevision);
    if (cachedReport) destination.searchParams.set("fresh", String(Date.now()));
    destination.hash = window.location.hash;
    window.location.assign(destination.href);
  }

  refreshButton?.addEventListener("click", async () => {
    if (refreshing) return;
    if (!window.navigator.onLine) {
      syncOfflineNotice();
      setText("report-refresh-feedback", "目前離線，無法檢查新報告。保留原報告，連線後再試。");
      return;
    }
    refreshing = true;
    refreshButton.disabled = true;
    refreshButton.setAttribute("aria-busy", "true");
    const previousText = refreshButton.textContent;
    refreshButton.textContent = "正在檢查…";
    setText("report-refresh-feedback", "正在檢查最新發布的報告，這個動作不會重新執行分析。");
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 8000);
    try {
      const url = new URL("assets/publication.json", base);
      url.searchParams.set("check", String(Date.now()));
      const response = await fetch(url.href, {method: "GET", cache: "no-store", credentials: "omit", signal: controller.signal});
      if (!response.ok || response.headers.get("X-Research-Cache") === "offline") throw new Error("publication unavailable");
      const publication = await response.json();
      const latest = publication?.pages?.[page];
      if (publication.schema_version !== 1 || !latest || typeof latest.revision !== "string"
          || !/^[a-f0-9]{64}$/.test(latest.revision)) throw new Error("invalid publication");
      if (latest.revision !== revision || cachedReport) {
        setText("report-refresh-feedback", "已取得最新發布資訊，正在載入報告…");
        navigateToPublished(latest.revision);
      } else {
        const status = statusLabels[latest.status] || "待確認";
        setText("report-refresh-feedback", `已是目前最新發布的報告。行情日：${dateLabel(latest.data_date)}；${status}。如需重新計算，請展開「需要重新分析？」。`);
      }
    } catch (_) {
      setText("report-refresh-feedback", "暫時無法確認最新報告，可能是網路或網站尚未更新。原報告仍保留，請稍後重試；這個動作沒有啟動分析。");
    } finally {
      window.clearTimeout(timer);
      refreshing = false;
      refreshButton.disabled = false;
      refreshButton.removeAttribute("aria-busy");
      refreshButton.textContent = previousText;
    }
  });

  function showWaitingWorker() {
    if (!registration?.waiting || !updateButton) return;
    updateButton.hidden = false;
    setText("pwa-status", "手機版有更新可安裝，按「更新手機版」後重新載入。報告日期仍以頁面標示為準。");
  }

  updateButton?.addEventListener("click", () => {
    if (!registration?.waiting) {
      updateButton.hidden = true;
      setText("pwa-status", "目前沒有等待安裝的手機版更新。");
      return;
    }
    updateRequested = true;
    updateButton.disabled = true;
    setText("pwa-status", "正在套用手機版更新，完成後會重新載入。");
    registration.waiting.postMessage({type: "UPDATE_APP"});
    window.setTimeout(() => {
      if (updateRequested) {
        updateButton.disabled = false;
        setText("pwa-status", "更新尚未完成，請稍後重試或重新開啟手機版。");
      }
    }, 10000);
  });

  function rememberCurrentReport(activeRegistration) {
    if (cacheRequested || !activeRegistration?.active || activeRegistration.scope !== base.href) return;
    cacheRequested = true;
    activeRegistration.active.postMessage({type: "CACHE_REPORT_PATH", pathname: pageURL.pathname});
  }

  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.addEventListener("message", event => {
      const message = event.data;
      if (!message || message.type !== "REPORT_CACHE_FALLBACK" || message.pathname !== pageURL.pathname) return;
      cachedReport = true;
      document.body.dataset.reportCache = "offline";
      syncOfflineNotice();
    });
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (!updateRequested) return;
      updateRequested = false;
      const destination = new URL(pageURL);
      destination.searchParams.set("app", String(Date.now()));
      destination.hash = window.location.hash;
      window.location.assign(destination.href);
    });
    const startWorker = async () => {
      try {
        registration = await navigator.serviceWorker.register(new URL("sw.js", base).href, {scope: base.href, updateViaCache: "none"});
        showWaitingWorker();
        registration.addEventListener("updatefound", () => {
          const worker = registration.installing;
          worker?.addEventListener("statechange", () => {
            if (worker.state === "installed") showWaitingWorker();
          });
        });
        navigator.serviceWorker.ready.then(rememberCurrentReport).catch(() => {});
      } catch (_) {
        setText("pwa-status", "目前無法啟用離線功能，仍可在線查看報告。請使用支援的瀏覽器並確認連線。");
      }
    };
    if (document.readyState === "complete") startWorker();
    else window.addEventListener("load", startWorker, {once: true});
  } else {
    setText("pwa-status", "目前瀏覽器未啟用離線功能，仍可在線查看報告。");
  }

  window.addEventListener("offline", syncOfflineNotice);
  window.addEventListener("online", () => {
    syncOfflineNotice();
    setText("report-refresh-feedback", "網路已連接。按「檢查最新報告」確認網站是否有更新。");
  });
  syncInstallButton();
  syncOfflineNotice();
})();
