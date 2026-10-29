"""Verify installed-web-app behavior against public report bytes at a project subpath.

Fault injection changes HTTP delivery/publication metadata, never market data.
The browser is desktop Chromium with mobile emulation, not a physical Android.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import mimetypes
import socket
import struct
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from playwright.sync_api import sync_playwright


PREFIX = "/taiwan-stock-ai/"


class State:
    def __init__(self, site):
        self.site = site
        self.manifest_mode = "normal"
        self.manifest_requests = 0
        self.failing_pages = set()
        self.sw_suffix = ""
        self.network_down = False
        self.requests = []


def server_for(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            state.requests.append({"method": "GET", "path": self.path})
            if state.network_down:
                # Some Chromium builds do not apply the page offline override
                # to an already-running SW target. Make the origin unavailable
                # too, so a passing check proves use of CacheStorage.
                with contextlib.suppress(OSError):
                    self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if not parsed.path.startswith(PREFIX):
                self.send_error(404)
                return
            relative = parsed.path[len(PREFIX):] or "index.html"
            file = (state.site / relative).resolve()
            if state.site.resolve() not in file.parents or not file.is_file():
                self.send_error(404)
                return
            if relative in state.failing_pages:
                self.send_error(503, "Injected public-page outage")
                return
            data = file.read_bytes()
            if relative == "assets/publication.json":
                state.manifest_requests += 1
                mode = state.manifest_mode
                if mode == "error":
                    self.send_error(503, "Injected publication outage")
                    return
                if mode == "malformed":
                    data = b'{"schema_version": 1, "pages": {}}'
                if mode == "slow":
                    time.sleep(10)
                if mode == "new":
                    metadata = json.loads(data)
                    for item in metadata["pages"].values():
                        item["revision"] = "f" * 64
                    data = json.dumps(metadata).encode()
            if relative == "sw.js":
                data += state.sw_suffix.encode()
            self.send_response(200)
            content_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
            if relative.endswith(".webmanifest"):
                content_type = "application/manifest+json"
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(data)

        def do_POST(self):
            state.requests.append({"method": "POST", "path": self.path})
            self.send_error(405)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def report_data(page):
    return json.loads(page.locator("#report-data").text_content())


def wait_cached(page, path):
    page.wait_for_function("""async path => {
      for (const name of await caches.keys()) {
        const cache = await caches.open(name);
        if (await cache.match(new URL(path, location.origin))) return true;
      }
      return false;
    }""", arg=path, timeout=30000)


def wait_controlled(page):
    page.wait_for_function("() => navigator.serviceWorker.controller !== null", timeout=30000)


def set_pwa_tools(page, opened):
    """Exercise the compact install menu while supporting older saved pages."""
    tools = page.locator("#pwa-tools")
    if tools.count() and tools.evaluate("node => node.open") != opened:
        tools.locator(":scope > summary").click()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("/tmp/taiwan-pwa/browser"))
    parser.add_argument("--chromium", help="Optional local Chromium; omit to use Playwright's installed browser")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    state = State(args.site)
    server = server_for(state)
    base = f"http://127.0.0.1:{server.server_port}{PREFIX}"
    evidence = {"checked_at": datetime.now(timezone.utc).isoformat(),
                "base_url": base, "browser": "Chromium desktop/mobile emulation; not physical Android",
                "report_inputs": "existing rendered report files; no simulated market prices",
                "checks": [], "viewports": []}
    manifest = json.loads((args.site / "manifest.webmanifest").read_text())
    assert urljoin(base, manifest["scope"]) == base
    assert urljoin(base, manifest["start_url"]).startswith(base)
    assert manifest["display"] in {"standalone", "minimal-ui"}
    assert manifest["lang"].startswith("zh")
    sizes = set()
    for icon in manifest["icons"]:
        url = urljoin(base, icon["src"])
        assert url.startswith(base)
        data = (args.site / urlsplit(url).path[len(PREFIX):]).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", data[16:24])
        assert f"{width}x{height}" == icon["sizes"]
        sizes.add(width)
    assert {192, 512}.issubset(sizes)
    evidence["checks"].append("manifest scope, standalone display and actual 192/512 PNG dimensions")
    try:
        with sync_playwright() as p:
            # Playwright's new_context is an incognito profile, which cannot
            # install apps. Check installability in a real temporary profile
            # separately from isolated viewport/fault-injection contexts.
            with tempfile.TemporaryDirectory(prefix="taiwan-pwa-install-") as profile:
                install_context = p.chromium.launch_persistent_context(
                    profile, executable_path=args.chromium, channel="chromium", headless=True,
                )
                try:
                    install_page = install_context.new_page()
                    install_page.goto(base + "index.html", wait_until="networkidle")
                    wait_controlled(install_page)
                    cdp = install_context.new_cdp_session(install_page)
                    installability = cdp.send("Page.getInstallabilityErrors")
                    assert installability.get("installabilityErrors") == [], installability
                    evidence["persistent_profile_installability"] = installability
                    cdp.detach()
                finally:
                    install_context.close()
            browser = p.chromium.launch(executable_path=args.chromium, channel="chromium", headless=True)
            for label, width, height in (("desktop", 1440, 1000), ("android", 390, 844)):
                context = browser.new_context(viewport={"width": width, "height": height},
                                              is_mobile=label == "android", has_touch=label == "android")
                page = context.new_page()
                js_errors = []
                page.on("pageerror", lambda err: js_errors.append(str(err)))
                page.goto(base + "index.html", wait_until="networkidle")
                wait_controlled(page)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator("#report-refresh").is_visible()
                assert page.locator("#pwa-install-help").text_content().strip()
                scope = page.evaluate("async () => (await navigator.serviceWorker.ready).scope")
                assert scope == base
                wait_cached(page, PREFIX + "index.html")
                print(f"{label}: real service worker registered; public index cached", flush=True)
                installability = None
                try:
                    cdp = context.new_cdp_session(page)
                    installability = cdp.send("Page.getInstallabilityErrors")
                    cdp.detach()
                except Exception as exc:
                    installability = {"unavailable": str(exc).splitlines()[0]}
                if "installabilityErrors" in installability:
                    assert all(item["errorId"] == "in-incognito"
                               for item in installability["installabilityErrors"]), installability
                current = report_data(page)
                old_report_hash = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
                before = state.manifest_requests
                page.locator("#report-refresh").click()
                page.wait_for_function("() => !document.getElementById('report-refresh').disabled")
                assert state.manifest_requests == before + 1
                assert "最新" in page.locator("#report-refresh-feedback").inner_text()
                assert urlsplit(page.url).path == PREFIX + "index.html"
                for mode in ("error", "malformed", "slow"):
                    state.manifest_mode = mode
                    before = state.manifest_requests
                    # Exercise two attempts in the same event-loop turn. A
                    # later separate click after a fast 503 has completed is
                    # a legitimate retry, not a duplicate in-flight request.
                    page.evaluate("""() => {
                      const button = document.getElementById('report-refresh');
                      button.click(); button.click();
                    }""")
                    page.wait_for_function("() => !document.getElementById('report-refresh').disabled", timeout=14000)
                    assert state.manifest_requests == before + 1, f"Duplicate in-flight request: {mode}"
                    feedback = page.locator("#report-refresh-feedback").inner_text()
                    assert any(word in feedback for word in ("無法", "失敗", "稍後", "逾時", "檢查未完成")), feedback
                    assert hashlib.sha256(json.dumps(report_data(page), sort_keys=True).encode()).hexdigest() == old_report_hash
                state.manifest_mode = "normal"
                print(f"{label}: publication same/error/malformed/timeout and double-tap checks passed", flush=True)
                page.locator("#watch-symbol").fill("2330")
                page.locator("#watch-quantity").fill("10")
                page.locator("#watch-cost").fill("100")
                page.locator("#watch-form button").click()
                saved = page.evaluate("localStorage.getItem('taiwan-stock-ai-watchlist-v1')")
                assert saved and "2330" in saved
                page.goto(base + "preopen.html", wait_until="networkidle")
                wait_controlled(page)
                wait_cached(page, PREFIX + "preopen.html")
                preopen = report_data(page)
                state.network_down = True
                context.set_offline(True)
                page.reload(wait_until="domcontentloaded", timeout=45000)
                assert report_data(page) == preopen
                page.wait_for_selector("#pwa-offline:not([hidden])")
                assert "2330" in page.locator("#watch-body").inner_text()
                if preopen.get("data_date"):
                    assert preopen["data_date"] in page.locator(".data-strip").inner_text()
                page.screenshot(path=str(args.out / f"{label}-offline.png"))
                page.goto(base + "final.html", wait_until="domcontentloaded", timeout=45000)
                assert page.locator("#report-data").count() == 0, "Unvisited offline page borrowed another report"
                assert any(word in page.locator("body").inner_text() for word in ("離線", "無法連線"))
                state.network_down = False
                context.set_offline(False)
                print(f"{label}: offline cached report and unvisited-page fallback passed", flush=True)
                page.goto(base + "preopen.html", wait_until="networkidle")
                state.failing_pages.add("preopen.html")
                page.reload(wait_until="networkidle")
                assert page.evaluate("navigator.onLine") is True
                assert report_data(page) == preopen
                page.wait_for_selector("#pwa-offline:not([hidden])", timeout=10000)
                state.failing_pages.clear()
                page.reload(wait_until="networkidle")
                state.manifest_mode = "new"
                page.locator("#report-refresh").click()
                page.wait_for_url(
                    lambda url: parse_qs(urlsplit(url).query).get("v") == ["f" * 64],
                    wait_until="domcontentloaded", timeout=15000,
                )
                assert page.evaluate("localStorage.getItem('taiwan-stock-ai-watchlist-v1')") == saved
                state.manifest_mode = "normal"
                page.goto(base + "index.html", wait_until="networkidle")
                # Installation handling: actual mobile install UI requires a real device.
                page.evaluate("""() => {
                  window.__installCalled = 0;
                  const event = new Event('beforeinstallprompt');
                  event.prompt = async () => { window.__installCalled += 1; };
                  event.userChoice = Promise.resolve({outcome: 'accepted', platform: 'web'});
                  window.dispatchEvent(event);
                }""")
                set_pwa_tools(page, True)
                page.wait_for_selector("#pwa-install:not([hidden])")
                page.locator("#pwa-install").click()
                assert page.evaluate("window.__installCalled") == 1
                page.evaluate("window.dispatchEvent(new Event('appinstalled'))")
                set_pwa_tools(page, False)
                # A changed SW script waits for the user's explicit update choice.
                page.evaluate("caches.open('unrelated-other-app').then(c => c.put('/other-app/item', new Response('keep'))) ")
                state.sw_suffix += f"\n// verification update {label}\n"
                old_controller = page.evaluate("navigator.serviceWorker.controller.scriptURL")
                page.evaluate("async () => (await navigator.serviceWorker.getRegistration()).update()")
                page.wait_for_selector("#pwa-update:not([hidden])", timeout=30000)
                assert page.evaluate("navigator.serviceWorker.controller.scriptURL") == old_controller
                assert page.evaluate("async () => Boolean((await navigator.serviceWorker.getRegistration()).waiting)")
                assert page.evaluate("localStorage.getItem('taiwan-stock-ai-watchlist-v1')") == saved
                with page.expect_navigation(wait_until="networkidle", timeout=30000):
                    page.locator("#pwa-update").click()
                assert page.evaluate("localStorage.getItem('taiwan-stock-ai-watchlist-v1')") == saved
                assert page.evaluate("async () => (await caches.keys()).includes('unrelated-other-app')")
                page_checks = []
                for slug in ("index", "preopen", "postclose", "final"):
                    page.goto(base + f"{slug}.html", wait_until="networkidle")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    assert page.locator("main > section").count() >= 7
                    data = report_data(page)
                    page_checks.append({"page": slug, "data_date": data.get("data_date"),
                                        "candidate_count": len(data.get("analysis", {}).get("candidates", []))})
                page.goto(base + "index.html", wait_until="networkidle")
                page.screenshot(path=str(args.out / f"{label}-online.png"))
                assert not js_errors, js_errors
                evidence["viewports"].append({"viewport": label, "width": width, "height": height,
                                               "scope": scope, "pages": page_checks, "js_errors": js_errors,
                                               "chromium_installability": installability,
                                               "checks": ["same/new publication", "HTTP/error/malformed/timeout feedback",
                                                          "duplicate tap suppression", "real offline cached report",
                                                          "unvisited offline fallback", "HTTP outage while browser online",
                                                          "install event handler simulation", "real waiting-worker update",
                                                          "watchlist persistence", "other-app cache isolation", "layout"]})
                context.close()
            browser.close()
        assert all(item["method"] == "GET" for item in state.requests), state.requests
        evidence["checks"].append("No POST/dispatch or watchlist upload; public content GET requests only")
        (args.out / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
