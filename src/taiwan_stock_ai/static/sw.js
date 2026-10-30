"use strict";

// Replaced by the renderer with a content-derived application version.
const VERSION = "__PWA_VERSION__";
const SCOPE = new URL(self.registration.scope);
const PREFIX = `taiwan-stock-research:${encodeURIComponent(SCOPE.pathname)}:`;
const SHELL_CACHE = `${PREFIX}shell-${VERSION}`;
// Saved public reports remain available when a user accepts an app update.
const REPORT_CACHE = `${PREFIX}reports-v1`;
const REPORT_PAGES = new Set(["index.html", "preopen.html", "postclose.html", "final.html"]);
const REPORT_JSON = new Set(["assets/report.json", "assets/preopen.json", "assets/postclose.json", "assets/final.json"]);
const SHELL_FILES = [
  "offline.html", "manifest.webmanifest", "assets/style.css", "assets/app.js",
  "assets/pwa.js", "assets/ai-view.js", "assets/theme.js", "assets/plotly.min.js", "assets/icon-192.png",
  "assets/icon-512.png", "assets/icon-maskable-512.png",
];
const SHELL_PATHS = new Set(SHELL_FILES);

async function fetchBounded(request, timeout = 10000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(request, {signal: controller.signal});
    if (response.redirected || response.type === "opaque") return response;
    // Include body transfer in the timeout: mobile links can stall after the
    // response headers arrive. Only complete public files enter the cache.
    const bytes = await response.arrayBuffer();
    const body = [204, 205, 304].includes(response.status) ? null : bytes;
    const headers = new Headers(response.headers);
    headers.delete("content-length");
    headers.delete("content-encoding");
    return new Response(body, {status: response.status, statusText: response.statusText, headers});
  } finally {
    clearTimeout(timer);
  }
}

function relativePath(url) {
  if (url.origin !== SCOPE.origin || !url.pathname.startsWith(SCOPE.pathname)) return null;
  return url.pathname.slice(SCOPE.pathname.length);
}

function reportPath(path) {
  return path === "" ? "index.html" : path;
}

function canonical(path) {
  // Paths come exclusively from the fixed allowlists above. Queries never
  // create additional cache entries or retain user-supplied URL values.
  return new URL(path, SCOPE).href;
}

async function cached(cacheName, path) {
  try {
    const cache = await caches.open(cacheName);
    return await cache.match(canonical(path));
  } catch (_) {
    return undefined;
  }
}

async function remember(cacheName, path, response) {
  try {
    const cache = await caches.open(cacheName);
    await cache.put(canonical(path), response.clone());
    return true;
  } catch (_) {
    // Quota/private-mode failures must not prevent online reading.
    return false;
  }
}

function usable(response, path) {
  if (!response || response.status !== 200 || response.redirected || response.type === "opaque") return false;
  const type = (response.headers.get("content-type") || "").toLowerCase();
  if (REPORT_PAGES.has(path) || path === "offline.html") return type.includes("text/html");
  if (REPORT_JSON.has(path)) return type.includes("json");
  // Do not save an HTML error page as an application script or image.
  return !type.includes("text/html");
}

function escapeText(value) {
  return String(value).replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[char]);
}

function datesFromHtml(html) {
  try {
    const match = html.match(/<script id="report-data" type="application\/json">([\s\S]*?)<\/script>/);
    const report = match ? JSON.parse(match[1]) : {};
    const date = new Date(report.generated_at || "");
    const generated = Number.isNaN(date.getTime()) ? "尚無報告時間" : `${date.toLocaleString("zh-TW", {timeZone: "Asia/Taipei", hour12: false})} 台灣時間`;
    return `行情日期：${report.data_date || "尚無可驗證資料"}；原報告時間：${generated}`;
  } catch (_) {
    return "請以本頁原始行情日期及報告時間判讀。";
  }
}

async function markedFallback(response, html) {
  const headers = new Headers(response.headers);
  headers.set("X-Research-Cache", "offline");
  headers.set("Cache-Control", "no-store");
  headers.delete("content-length");
  headers.delete("content-encoding");
  if (!html) return new Response(await response.arrayBuffer(), {status: response.status, headers});
  const original = await response.text();
  const note = `<div id="pwa-worker-cache-notice" role="status" style="padding:14px 20px;border-bottom:2px solid var(--amber,#edc27b);background:var(--notice-bg,#332a18);color:var(--notice-text,#ffe1a3);font-size:16px;line-height:1.7">` +
    `<strong>目前顯示已儲存的離線副本。</strong> 無法確認網站最新內容，並非即時行情。<br>${escapeText(datesFromHtml(original))}</div>`;
  const body = original.replace(/<body\b([^>]*)>/i, `<body$1 data-report-cache="offline">${note}`);
  return new Response(body, {status: response.status, headers});
}

async function notifyFallback(event, path, reason) {
  const ids = [event.resultingClientId, event.clientId].filter(Boolean);
  await Promise.all(ids.map(async id => {
    try {
      const client = await self.clients.get(id);
      if (client) client.postMessage({type: "REPORT_CACHE_FALLBACK", pathname: new URL(path, SCOPE).pathname, reason});
    } catch (_) {
      // A closing/navigation client must not prevent serving the saved page.
    }
  }));
}

async function offlinePage() {
  const fallback = await cached(SHELL_CACHE, "offline.html");
  if (fallback) {
    const headers = new Headers(fallback.headers);
    headers.set("X-Research-Cache", "offline");
    headers.set("Cache-Control", "no-store");
    headers.delete("content-length");
    headers.delete("content-encoding");
    const html = (await fallback.text()).replace(/<head>/i, `<head><base href="${escapeText(SCOPE.href)}">`);
    return new Response(html, {status: 503, headers});
  }
  return new Response(`<!doctype html><html lang=zh-Hant><meta charset=utf-8><base href="${escapeText(SCOPE.href)}"><meta name=viewport content='width=device-width,initial-scale=1'><title>無法取得報告</title><body data-report-cache=offline><h1>目前無法取得報告</h1><p>此頁尚無可用的離線副本。請連線後重試；這裡沒有即時行情。</p></body></html>`, {
    status: 503, headers: {"Content-Type": "text/html; charset=utf-8", "X-Research-Cache": "offline", "Cache-Control": "no-store"},
  });
}

async function researchResponse(event, path) {
  let reason = "network-unavailable";
  try {
    const response = await fetchBounded(new Request(event.request, {cache: "no-store", credentials: "omit"}));
    if (usable(response, path)) {
      await remember(REPORT_CACHE, path, response);
      return response;
    }
    reason = `http-${response.status}`;
  } catch (_) {
    // A previously viewed report can be useful, but it must be visibly dated.
  }
  const saved = await cached(REPORT_CACHE, path);
  // A new navigation client is not execution-ready until its response is
  // delivered. Waiting for clients.get(resultingClientId) here would deadlock
  // the cached navigation. The HTML marker supplies immediate offline state;
  // notify the ready client independently of delivering that response.
  event.waitUntil(notifyFallback(event, path, reason));
  if (saved) return markedFallback(saved, REPORT_PAGES.has(path));
  if (REPORT_PAGES.has(path)) return offlinePage();
  return new Response(JSON.stringify({status: "unavailable", error: "No saved public report is available."}), {
    status: 503, headers: {"Content-Type": "application/json", "X-Research-Cache": "offline", "Cache-Control": "no-store"},
  });
}

async function shellResponse(request, path) {
  try {
    // Revalidate scripts/styles so fresh HTML never receives an indefinitely
    // cached old UI while a new worker waits for the user's update action.
    const response = await fetchBounded(new Request(request, {cache: "no-cache", credentials: "omit"}));
    if (usable(response, path)) {
      await remember(SHELL_CACHE, path, response);
      return response;
    }
    return await cached(SHELL_CACHE, path) || response;
  } catch (_) {
    return await cached(SHELL_CACHE, path) || new Response("Offline asset unavailable", {status: 503});
  }
}

self.addEventListener("install", event => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    await Promise.all(SHELL_FILES.map(async path => {
      const response = await fetchBounded(new Request(canonical(path), {cache: "reload", credentials: "omit"}), 15000);
      if (!usable(response, path)) throw new Error(`PWA asset unavailable: ${path}`);
      await cache.put(canonical(path), response);
    }));
    // Never skipWaiting here: a running page/watchlist must not be reloaded.
  })());
});

self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(name => name.startsWith(PREFIX) && name !== SHELL_CACHE && name !== REPORT_CACHE).map(name => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  const path = relativePath(new URL(event.request.url));
  if (path === null) return;
  const report = reportPath(path);
  if (REPORT_PAGES.has(report) || REPORT_JSON.has(report)) {
    event.respondWith(researchResponse(event, report));
  } else if (SHELL_PATHS.has(path)) {
    event.respondWith(shellResponse(event.request, path));
  } else if (event.request.mode === "navigate") {
    // Unknown routes are never stored. If the network is unavailable, keep
    // installation launches inside a truthful, useful offline screen.
    event.respondWith(fetchBounded(event.request).catch(() => offlinePage()));
  }
  // Publication metadata, APIs, unknown paths and external links are never
  // answered from this cache. Only fixed public files may be stored.
});

self.addEventListener("message", event => {
  let clientPath;
  try { clientPath = relativePath(new URL(event.source.url)); } catch (_) { return; }
  if (clientPath === null) return;
  if (event.data?.type === "UPDATE_APP") {
    event.waitUntil(self.skipWaiting());
    return;
  }
  if (event.data?.type !== "CACHE_REPORT_PATH") return;
  let path;
  try {
    const value = event.data.pathname ?? event.data.path;
    if (typeof value !== "string") return;
    const url = new URL(value, SCOPE);
    const relative = relativePath(url);
    if (relative === null) return;
    path = reportPath(relative);
  } catch (_) { return; }
  if (!REPORT_PAGES.has(path)) return;
  event.waitUntil((async () => {
    try {
      const response = await fetchBounded(new Request(canonical(path), {cache: "no-store", credentials: "omit"}));
      if (usable(response, path)) {
        const saved = await remember(REPORT_CACHE, path, response);
        if (saved) event.source.postMessage({type: "REPORT_CACHE_SAVED", pathname: new URL(path, SCOPE).pathname});
      }
    } catch (_) {
      // Saving an offline copy is optional and never changes the visible data.
    }
  })());
});
