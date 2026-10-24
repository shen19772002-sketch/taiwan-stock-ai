"""Exercise a locally served real report on desktop and Android-sized screens."""

import argparse
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def research_contract(report):
    """Fail when a supposedly warmed real report lacks actionable evidence."""
    analysis = report["analysis"]
    candidates = analysis["candidates"]
    assert 0 < len(candidates) <= 15, "Expected a nonempty research shortlist"
    assert report["data_date"] == analysis["as_of"], "Analysis date differs from verified price date"
    assert len({(s["market"], s["symbol"]) for s in candidates}) == len(candidates)
    for stock in candidates:
        assert stock["date"] == report["data_date"], "Ranked stock has a stale price"
        assert stock["eligible"] and not stock["exclusions"]
        assert stock["bars"] >= 60
        assert stock["fundamental_date"] == stock["date"], "Stale valuation used for ranking"
        assert stock["pe"] > 0
        assert urlsplit(stock["source"]).hostname in {"openapi.twse.com.tw", "www.twse.com.tw", "www.tpex.org.tw"}
        plan = stock["plan"]
        for field in ("entry_low", "entry_high", "breakout", "target", "stop", "risk_reward", "net_risk_reward"):
            assert isinstance(plan[field], (float, int)) and math.isfinite(plan[field]) and plan[field] > 0
        assert plan["stop"] < plan["entry_low"] <= plan["entry_high"] < plan["target"]
        assert plan["net_risk_reward"] >= 2
        assert plan["conditions"] and plan["invalidation"]
    assert any(s.get("data_date") == report["data_date"] for s in report["sources"])
    return candidates


def check_chart(page, report):
    industries = [item for item in report["analysis"]["industries"]
                  if item.get("momentum20") is not None and math.isfinite(item["momentum20"])][:10][::-1]
    assert industries, "Expected industries with verified momentum history"
    page.wait_for_selector("#industry-chart .plot-container")
    actual = page.locator("#industry-chart").evaluate("node => ({x:Array.from(node.data[0].x), y:Array.from(node.data[0].y)})")
    assert actual["y"] == [industry["industry"] for industry in industries], "Chart industries differ from report"
    assert len(actual["x"]) == len(industries)
    for plotted, industry in zip(actual["x"], industries):
        assert math.isclose(plotted, industry["momentum20"] * 100, rel_tol=1e-10, abs_tol=1e-10)
    geometry = page.locator("#industry-chart").evaluate(
        "node => ({width:node.clientWidth,scroll:node.scrollWidth,plot:node._fullLayout.width})")
    assert abs(geometry["plot"] - geometry["width"]) <= 1, "Plotly canvas is clipped by its container"
    assert geometry["scroll"] <= geometry["width"] + 1, "Chart overflows its responsive container"
    assert not page.locator("#industry-chart-empty").is_visible()
    return len(industries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    parser.add_argument("--screenshots", type=Path, default=Path("/tmp/taiwan-stock-browser"))
    parser.add_argument("--require-research", action="store_true", help="Require real ranked stocks, plans, and plotted data")
    args = parser.parse_args()
    args.screenshots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, headless=True)
        for name, width, height in (("desktop", 1440, 1000), ("android", 390, 844)):
            context = browser.new_context(viewport={"width": width, "height": height},
                                          is_mobile=name == "android", has_touch=name == "android")
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append("console: " + message.text)
                    if message.type == "error" and not str(message.location.get("url", "")).endswith("/favicon.ico") else None)
            page.on("response", lambda response: errors.append(f"HTTP {response.status}: {urlsplit(response.url).path}")
                    if response.status >= 400 and not urlsplit(response.url).path.endswith("/favicon.ico") else None)
            page.goto(args.url, wait_until="networkidle")
            report = json.loads(page.locator("#report-data").text_content())
            assert page.title().startswith("台股 AI")
            assert page.locator("main > section").count() >= 7
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Horizontal overflow"
            chart_count = 0
            if args.require_research:
                candidates = research_contract(report)
                assert page.locator("#ranking-body tr").count() == len(candidates)
                chart_count = check_chart(page, report)
                assert report["data_date"] in page.locator("#data-sources").inner_text()
                # Verify each actual candidate can expose its own strategy, not
                # merely that one generic detail panel happened to render.
                for stock in candidates:
                    page.locator("#stock-select").select_option(f'{stock["market"]}:{stock["symbol"]}')
                    assert stock["symbol"] in page.locator("#detail-name").inner_text()
                    assert stock["date"] in page.locator("#detail-date").inner_text()
                    assert math.isclose(float(page.locator("#detail-close").inner_text().replace(",", "")),
                                        stock["close"], abs_tol=.005)
                    assert not page.locator("#detail-quality").is_visible()
                    displayed_prices = page.locator("#price-plan strong").all_text_contents()
                    assert len(displayed_prices) == 6 and all(value != "—" for value in displayed_prices)
                    for index, field in enumerate(("breakout", "target", "stop", "risk_reward", "net_risk_reward"), 1):
                        assert math.isclose(float(displayed_prices[index].replace(",", "")),
                                            stock["plan"][field], abs_tol=.005)
                    entry_prices = [float(value.replace(",", "")) for value in displayed_prices[0].split(" — ")]
                    assert entry_prices == [stock["plan"]["entry_low"], stock["plan"]["entry_high"]]
                    assert "策略失效" in page.locator("#detail-conditions").inner_text()
                    assert page.locator("#detail-scores .score-row").count() == len(stock["score_components"])
            page.locator("#watch-symbol").fill("2330")
            page.locator("#watch-quantity").fill("10")
            page.locator("#watch-cost").fill("100")
            page.locator("#watch-form button").click()
            assert "2330" in page.locator("#watch-body").inner_text()
            page.reload(wait_until="networkidle")
            assert "2330" in page.locator("#watch-body").inner_text(), "Watchlist did not persist"
            page.get_by_role("button", name="移除 2330").click()
            assert "尚未加入" in page.locator("#watch-body").inner_text()
            if page.locator("#stock-select option").count() > 1:
                page.locator("#stock-select").select_option(index=1)
                assert page.locator("#detail-content").is_visible()
            page.locator("#stock-search").fill("NO_MATCH_FOR_BROWSER_TEST")
            assert page.locator("#ranking-body tr").count() == 0
            page.locator("#stock-search").fill("")
            if args.require_research:
                candidates = report["analysis"]["candidates"]
                first = candidates[0]
                page.locator("#stock-select").select_option(f'{first["market"]}:{first["symbol"]}')
                assert page.locator("#ranking-body tr").count() == len(candidates)
                check_chart(page, report)
                page.locator("#rotation .panel").first.screenshot(path=str(args.screenshots / f"{name}-industry.png"))
                page.locator("#detail-content").screenshot(path=str(args.screenshots / f"{name}-strategy.png"))
            page.locator("#overview").scroll_into_view_if_needed()
            page.screenshot(path=str(args.screenshots / f"{name}-viewport.png"))
            page.screenshot(path=str(args.screenshots / f"{name}.png"), full_page=True)
            assert errors == [], errors
            print(json.dumps({"viewport": name, "width": width, "height": height,
                              "data_date": report.get("data_date"), "candidates": len(report["analysis"]["candidates"]),
                              "chart_industries": chart_count, "errors": errors,
                              "checks": ["navigation", "responsive layout", "persistent local watchlist", "stock details", "search"]
                              + (["shortlist provenance", "risk plans", "Plotly values"] if args.require_research else [])},
                             ensure_ascii=False))
            context.close()
        browser.close()


if __name__ == "__main__":
    main()
