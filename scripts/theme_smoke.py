"""Verify readable themes using real report files and Chromium, without market fixtures.

Only browser preference failures, network outages, and an isolated watchlist
fixture are simulated. Prices, report dates and Plotly observations stay intact.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from ai_ui_smoke import digest
from pwa_smoke import PREFIX, State, report_data, server_for, wait_cached, wait_controlled


THEME_KEY = "taiwan-stock-ai-theme-v1"
WATCH_KEY = "taiwan-stock-ai-watchlist-v1"
THEMES = ("dark", "light", "sepia")


def assert_theme(page, theme):
    page.wait_for_function("theme => document.documentElement.dataset.theme === theme", arg=theme)
    assert page.locator("#theme-select").input_value() == theme


def inspect_readability(page, mobile):
    result = page.evaluate("""() => {
      const rgba = value => (value.match(/[\\d.]+/g) || []).map(Number);
      const background = node => {
        if (!node) return [255, 255, 255, 1];
        const own = rgba(getComputedStyle(node).backgroundColor);
        if (own.length === 3) own.push(1);
        const parent = background(node.parentElement), alpha = own[3] || 0;
        return [0, 1, 2].map(i => own[i] * alpha + parent[i] * (1-alpha)).concat(1);
      };
      const luminance = color => color.slice(0, 3).reduce((sum, component, i) => {
        const linear = component / 255;
        return sum + (linear <= 0.04045 ? linear / 12.92 : ((linear + 0.055) / 1.055) ** 2.4)
          * [0.2126, 0.7152, 0.0722][i];
      }, 0);
      const selectors = ['body', '.research-head p', '.research-readiness > p',
        '.report-meta', '.report-date strong', '.ai-stock-meta', '.ai-stock-state', '.method-note'];
      const text = selectors.flatMap(selector => [...document.querySelectorAll(selector)].slice(0, 3)
        .filter(node => node.getClientRects().length).map(node => {
          const style = getComputedStyle(node), bg = background(node), fg = rgba(style.color);
          const a = luminance(bg), b = luminance(fg);
          return {selector, size: parseFloat(style.fontSize), color: style.color,
            background: bg, contrast: (Math.max(a,b)+0.05)/(Math.min(a,b)+0.05)};
        }));
      const controls = ['#theme-select', '#report-refresh', '.phase-nav a', '.ai-category-button']
        .flatMap(selector => [...document.querySelectorAll(selector)].slice(0, 3)
        .filter(node => node.getClientRects().length).map(node => ({selector,
          height: node.getBoundingClientRect().height, width: node.getBoundingClientRect().width})));
      return {text, controls, background: background(document.body),
        overflow: document.documentElement.scrollWidth > innerWidth};
    }""")
    assert not result["overflow"], result
    assert result["text"], "Readable report text must exist"
    for item in result["text"]:
        assert item["contrast"] >= 4.5, item
        if mobile:
            minimum = 16 if item["selector"] in {"body", ".research-head p", ".research-readiness > p"} else 14
            assert item["size"] >= minimum, item
    if mobile:
        for item in result["controls"]:
            assert item["height"] >= 44 and item["width"] >= 44, item
    return result


def inspect_plotly(page):
    """Compare the actual Plotly layout with the currently rendered CSS palette."""
    page.wait_for_function("""() => [...document.querySelectorAll('.js-plotly-plot')].every(chart => {
      const root = getComputedStyle(document.documentElement);
      const normalize = value => {const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d'); ctx.fillStyle = value; return ctx.fillStyle;};
      const layout = chart._fullLayout;
      return layout && normalize(layout.paper_bgcolor) === normalize(root.getPropertyValue('--surface').trim())
        && normalize(layout.font.color) === normalize(root.getPropertyValue('--text').trim());
    })""", timeout=10000)
    result = page.evaluate("""() => {
      const css = getComputedStyle(document.documentElement);
      const normalize = value => {const ctx=document.createElement('canvas').getContext('2d');
        ctx.fillStyle=value; return ctx.fillStyle;};
      const palette = Object.fromEntries(['surface','text','muted','line','red','green']
        .map(key=>[key,normalize(css.getPropertyValue('--'+key).trim())]));
      return {palette, charts: [...document.querySelectorAll('.js-plotly-plot')].map(chart => {
        const layout = chart._fullLayout;
        return {id: chart.id, paper: normalize(layout.paper_bgcolor), plot: normalize(layout.plot_bgcolor),
          font: normalize(layout.font.color), font_size: layout.font.size,
          legend_visible: Boolean(layout.legend),
          legend: normalize(layout.legend?.font?.color || layout.font.color),
          x_tick: normalize(layout.xaxis.tickfont.color), y_tick: normalize(layout.yaxis.tickfont.color),
          x_grid_visible: layout.xaxis.showgrid, y_grid_visible: layout.yaxis.showgrid,
          x_grid: normalize(layout.xaxis.gridcolor), y_grid: normalize(layout.yaxis.gridcolor),
          traces: chart.data.map(trace=>({x: trace.x, y: trace.y,
            colors: (Array.isArray(trace.marker?.color) ? trace.marker.color : []).map(normalize)}))};
      })};
    }""")
    palette = result["palette"]
    for chart in result["charts"]:
        assert chart["paper"] == chart["plot"] == palette["surface"], chart
        assert chart["font"] == chart["legend"] == palette["text"], chart
        assert chart["x_tick"] == chart["y_tick"] == palette["muted"], chart
        for axis in ("x", "y"):
            if chart[axis + "_grid_visible"]:
                assert chart[axis + "_grid"] == palette["line"], chart
        assert chart["font_size"] >= 14, chart
        for trace in chart["traces"]:
            for value, color in zip(trace["x"], trace["colors"], strict=True):
                assert color == palette["red" if value >= 0 else "green"], trace
    if not result["charts"]:
        result["skipped"] = "No valid industry observations in this report; no chart data fabricated"
    return result


def chart_values(result):
    return [{"id": chart["id"], "traces": [{"x": trace["x"], "y": trace["y"]}
             for trace in chart["traces"]]} for chart in result["charts"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("theme-evidence"))
    parser.add_argument("--chromium")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    state = State(args.site)
    server = server_for(state)
    base = f"http://127.0.0.1:{server.server_port}{PREFIX}"
    evidence = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "browser": "Chromium viewport emulation; not physical Android",
                "market_data_injected": False, "viewports": []}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.chromium, channel="chromium", headless=True)
            for name, width, height in (("small-phone", 360, 800), ("phone", 390, 844), ("desktop", 1440, 1000)):
                context = browser.new_context(viewport={"width": width, "height": height},
                                              is_mobile=width < 600, has_touch=width < 600)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "index.html", wait_until="networkidle")
                wait_controlled(page)
                wait_cached(page, PREFIX + "index.html")
                original = digest(report_data(page))
                # Isolated browser-state fixture; never inserted into market/report data.
                holdings = json.dumps([{"market": "TWSE", "symbol": "1101", "quantity": 7, "cost": 12.5}])
                page.evaluate("([key,value])=>localStorage.setItem(key,value)", [WATCH_KEY, holdings])
                page.reload(wait_until="networkidle")
                assert_theme(page, "dark")
                themes = []
                original_chart = None
                for theme in THEMES:
                    page.locator("#theme-select").select_option(theme)
                    assert_theme(page, theme)
                    assert page.evaluate("key=>localStorage.getItem(key)", THEME_KEY) == theme
                    readability = inspect_readability(page, width < 600)
                    chart = inspect_plotly(page)
                    if original_chart is None:
                        original_chart = chart_values(chart)
                    assert chart_values(chart) == original_chart
                    assert digest(report_data(page)) == original
                    assert page.evaluate("key=>localStorage.getItem(key)", WATCH_KEY) == holdings
                    page.evaluate("window.scrollTo(0,0)")
                    page.screenshot(path=str(args.out / f"{name}-{theme}.png"))
                    page.reload(wait_until="networkidle")
                    assert_theme(page, theme)
                    assert chart_values(inspect_plotly(page)) == original_chart
                    themes.append({"theme": theme, "readability": readability, "plotly": chart})
                assert len({tuple(item["readability"]["background"]) for item in themes}) == 3
                phases = []
                for slug in ("preopen", "postclose", "final"):
                    page.goto(base + slug + ".html", wait_until="networkidle")
                    assert_theme(page, "sepia")
                    assert page.evaluate("key=>localStorage.getItem(key)", WATCH_KEY) == holdings
                    inspect_plotly(page)
                    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                    phases.append(slug)
                context.set_offline(True)
                state.network_down = True
                page.goto(base + "index.html", wait_until="networkidle")
                assert_theme(page, "sepia")
                # The worker marks the saved document. pwa.js replaces that
                # raw banner with one dated notice to avoid duplicate alerts.
                assert page.locator("body").get_attribute("data-report-cache") == "offline"
                page.wait_for_selector("#pwa-offline:not([hidden])")
                assert digest(report_data(page)) == original
                assert page.evaluate("key=>localStorage.getItem(key)", WATCH_KEY) == holdings
                inspect_plotly(page)
                # Verify an unsaved phase uses the themed offline explanation,
                # rather than borrowing another phase's prices.
                await_delete = """async path=>{for(const name of await caches.keys()){
                  const cache=await caches.open(name);await cache.delete(new URL(path,location.origin));}}"""
                page.evaluate(await_delete, PREFIX + "final.html")
                page.goto(base + "final.html", wait_until="networkidle")
                assert_theme(page, "sepia")
                assert page.locator("#report-data").count() == 0
                assert "尚未儲存" in page.locator("main").inner_text()
                page.screenshot(path=str(args.out / f"{name}-offline-explanation.png"))
                state.network_down = False
                context.set_offline(False)
                assert not errors, errors
                evidence["viewports"].append({"name": name, "width": width, "height": height,
                    "themes": themes, "persisted_phase_pages": phases, "offline_preference": "passed",
                    "unvisited_page_fallback": "passed", "watchlist_unchanged": True, "js_errors": errors})
                context.close()
                print(f"{name}: three themes, readable text, charts and persisted offline preferences passed", flush=True)
            blocked = browser.new_context()
            blocked.add_init_script(f"""(() => {{
              const get = Storage.prototype.getItem, set = Storage.prototype.setItem;
              Storage.prototype.getItem = function(key) {{if(key==={json.dumps(THEME_KEY)})
                throw new DOMException('Injected preference read failure','SecurityError');return get.call(this,key);}};
              Storage.prototype.setItem = function(key,value) {{if(key==={json.dumps(THEME_KEY)})
                throw new DOMException('Injected preference write failure','QuotaExceededError');return set.call(this,key,value);}};
            }})();""")
            page = blocked.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "index.html", wait_until="networkidle")
            assert_theme(page, "dark")
            page.locator("#theme-select").select_option("light")
            assert_theme(page, "light")
            inspect_plotly(page)
            assert not errors, errors
            blocked.close()
            evidence["preference_storage_failure"] = "safe default and live switching still work; persistence unavailable"
            browser.close()
        assert all(request["method"] == "GET" for request in state.requests)
        evidence["network"] = "GET only; no report dispatch, watchlist upload, or trading request"
        evidence["result"] = "passed"
        (args.out / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"result": "passed", "evidence": str(args.out / 'evidence.json')}))
    finally:
        state.network_down = False
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
