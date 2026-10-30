"""Verify the AI dashboard against existing rendered reports in real Chromium.

No synthetic prices or classifications are injected. Empty-state runs verify the
same controls and document the data-dependent checks that could not be exercised.
Mobile checks use viewport emulation, not a physical Android installation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from pwa_smoke import PREFIX, State, report_data, server_for


STORAGE_KEY = "taiwan-stock-ai-watchlist-v1"
REQUIRED_SECTIONS = (
    "overview", "rotation", "themes", "ranking", "stock-detail",
    "watchlist", "reports", "data-sources", "methodology",
)


def stock_key(stock):
    return f"{stock['market']}:{stock['symbol']}"


def category_ids(stock):
    categories = stock.get("ai_category_ids", stock.get("ai_categories", []))
    return {value.get("id") if isinstance(value, dict) else value for value in categories}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def displayed_number(text):
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", text)
    return float(match.group().replace(",", "")) if match else None


def assert_original_prices(page, observations):
    """Prove visible card values refer to the same report observations."""
    index = {stock_key(stock): stock for stock in observations}
    checked = []
    for card in page.locator("#ai-observation-list .ai-stock-card").all():
        identity = card.get_attribute("data-stock-key")
        assert identity in index, f"Observation card outside the embedded universe: {identity}"
        source = index[identity]
        assert source["symbol"] in card.inner_text()
        price = card.locator(".ai-stock-price strong")
        assert price.count(), f"Missing visible quote for {identity}"
        actual = displayed_number(price.inner_text())
        assert actual is not None and math.isclose(actual, source["close"], abs_tol=0.005), identity
        day = str(source.get("date") or "")
        assert day and (day in card.inner_text() or day[5:].replace("-", "/") in card.inner_text()), identity
        tags = card.locator(".ai-stock-tag").all_text_contents()
        assert tags == [source["ai_category"]], (identity, tags)
        assert "筆完整歷史" not in card.inner_text(), identity
        checked.append(identity)
    return checked


def inspect_simple_surface(page, view, report, *, first_screen=False):
    """Require the simplified surface to retain the dates and missing-data signals.

    Technical details may be folded, but data age, non-live status and incomplete
    source status must remain readable before a user opens those details.
    """
    date = page.locator(".report-date-summary")
    assert date.is_visible(), "Market-data date must remain visible in the compact header"
    if report.get("data_date"):
        assert report["data_date"] in date.inner_text()
    assert "非即時" in page.locator(".topbar").inner_text()
    status = page.locator(".readiness-status")
    assert status.is_visible(), "Source completeness must remain visible when details are folded"
    if report.get("status") == "partial":
        assert "待補" in status.inner_text(), status.inner_text()
    assert page.locator(".phase-nav a").count() == 4
    result = page.evaluate("""() => {
      const font = selector => {const node=document.querySelector(selector);
        return node ? parseFloat(getComputedStyle(node).fontSize) : null;};
      const first = document.querySelector('.ai-stock-card');
      const price = first?.querySelector('.ai-stock-price strong');
      const name = first?.querySelector('.ai-stock-name');
      const rect = node => {if (!node) return null;const r=node.getBoundingClientRect();
        return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom};};
      return {body_font:font('body'), date_font:font('.report-date-summary'),
        status_font:font('.readiness-status'), name_font:font('.ai-stock-name'),
        price_font:font('.ai-stock-price strong'), meta_font:font('.ai-stock-meta'),
        first_card:rect(first), first_name:rect(name), first_price:rect(price),
        viewport_width:innerWidth,viewport_height:innerHeight};
    }""")
    assert result["body_font"] >= 18, result
    assert result["date_font"] >= 16 and result["status_font"] >= 16, result
    if view.get("observations"):
        assert result["name_font"] >= 26 and result["price_font"] >= 34, result
        assert result["meta_font"] >= 16, result
        if first_screen:
            limit = 680 if result["viewport_width"] >= 390 else 700
            assert result["first_card"]["y"] <= limit, result
            bottom_nav = page.locator(".mobile-quick-nav")
            nav_top = bottom_nav.bounding_box()["y"] if bottom_nav.is_visible() else result["viewport_height"]
            assert result["first_name"]["bottom"] <= nav_top, result
            assert result["first_price"]["bottom"] <= nav_top, result
    return result


def exercise_observations(page, view, report):
    observations = view.get("observations", [])
    cards = page.locator("#ai-observation-list .ai-stock-card")
    checked = assert_original_prices(page, observations)
    if not observations:
        assert cards.count() == 0
        assert page.locator("#ai-observation-empty").is_visible()
        return {"state": "empty", "checks": ["no manufactured observations", "honest empty state"],
                "skipped": ["priced cards", "category membership", "stock detail link", "card watch action"]}
    assert cards.count() > 0
    by_key = {stock_key(stock): stock for stock in observations}
    candidate_keys = {stock_key(stock) for stock in view.get("candidates", [])}
    short_states = {}
    for card in cards.all():
        identity = card.get_attribute("data-stock-key")
        stock = by_key[identity]
        if stock.get("date") != view.get("data_date"):
            expected = "行情待更新"
        elif stock.get("fundamental_status") != "current":
            expected = "估值待補"
        elif not stock.get("indicators"):
            expected = "歷史不足"
        elif view.get("is_ai_report") and identity in candidate_keys and stock.get("plan"):
            expected = "符合選股條件"
        elif not view.get("is_ai_report"):
            expected = "舊報告觀察"
        else:
            expected = "未符合選股條件"
        assert card.locator(".ai-stock-state").inner_text() == expected, identity
        short_states[identity] = expected
    original_count = cards.count()
    assert original_count == min(6, len(observations)), original_count
    more = page.locator("#ai-observation-more")
    pagination = "all observations fit the compact list"
    if len(observations) > 6:
        assert more.is_visible()
        assert "6" in page.locator("#ai-observation-count").inner_text()
        more.click()
        assert cards.count() == len(observations)
        checked_all = assert_original_prices(page, observations)
        assert set(checked_all) == {stock_key(stock) for stock in observations}
        pagination = {"initial_count": original_count, "expanded_count": cards.count()}
    search = page.locator("#ai-observation-search")
    search.fill("__no_matching_ai_symbol__")
    assert cards.count() == 0
    assert page.locator("#ai-observation-empty").is_visible()
    search.fill("")
    expanded_count = len(observations) if len(observations) > 6 else original_count
    assert cards.count() == expanded_count
    # A stock outside the initial six must remain discoverable by search.
    last = observations[-1]
    search.fill(last["symbol"])
    assert stock_key(last) in assert_original_prices(page, observations)
    search.fill("")
    all_button = page.locator('#ai-category-filters [data-ai-category="all"]')
    buttons = page.locator("#ai-category-filters [data-ai-category]").all()
    category_checks = []
    for button in buttons:
        category = button.get_attribute("data-ai-category")
        if category == "all":
            continue
        button.click()
        assert button.get_attribute("aria-pressed") == "true"
        keys = assert_original_prices(page, observations)
        source_index = {stock_key(stock): stock for stock in observations}
        expected_keys = {stock_key(stock) for stock in observations if category in category_ids(stock)}
        assert set(keys) == expected_keys, (category, keys, expected_keys)
        for identity in keys:
            assert category in category_ids(source_index[identity]), (category, identity)
        if not keys:
            assert page.locator("#ai-observation-empty").is_visible()
        category_checks.append({"category": category, "visible_stocks": len(keys)})
    all_button.click()
    assert cards.count() == expanded_count
    if len(observations) > 6:
        more.click()
        assert cards.count() == original_count
    first = cards.first
    identity = first.get_attribute("data-stock-key")
    detail = first.locator("[data-ai-detail]")
    assert detail.count(), "Card must expose a real detail action"
    detail.click()
    assert page.locator("#stock-detail").is_visible()
    assert page.locator("#stock-select").input_value() == identity
    assert identity.split(":", 1)[1] in page.locator("#detail-name").inner_text()
    assert page.locator("#detail-ai-context").is_visible()
    assert page.locator("#detail-ai-evidence").is_visible()
    stock = next(item for item in observations if stock_key(item) == identity)
    if isinstance(stock.get("bars"), int):
        assert str(stock["bars"]) in page.locator("#detail-coverage").inner_text()
    for category in stock.get("ai_categories", []):
        assert category in page.locator("#detail-ai-context").inner_text()
    if not stock.get("plan"):
        # Missing plans may be explained in prose, but never turned into numbers.
        price_values = page.locator("#price-plan strong").all_text_contents()
        assert all(displayed_number(value) is None for value in price_values), price_values
    evidence_links = page.locator("#detail-ai-evidence a").all()
    assert evidence_links, "AI association must expose evidence, not only a theme label"
    assert all(link.get_attribute("href").startswith("https://") for link in evidence_links)
    watch = first.locator("[data-ai-watch]")
    assert watch.count(), "Card must offer the preserved local watchlist action"
    watch.click()
    saved = page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY)
    parsed = json.loads(saved or "[]")
    assert any(stock_key(item) == identity for item in parsed), parsed
    assert identity.split(":", 1)[1] in page.locator("#watch-body").inner_text()
    page.reload(wait_until="networkidle")
    assert page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY) == saved
    # The AI research scope must not erase the user's existing non-AI holdings
    # or manufacture a price for them. Use an actual preserved quote if present.
    research_keys = {stock_key(item) for item in report.get("analysis", {}).get("stocks", [])}
    non_ai_quote = next((item for item in report.get("watchlist_quotes", [])
                         if stock_key(item) not in research_keys and item.get("close") is not None), None)
    non_ai_check = "no non-AI quote in this input"
    if non_ai_quote:
        page.locator("#watch-symbol").fill(non_ai_quote["symbol"])
        page.locator("#watch-market").select_option(non_ai_quote["market"])
        page.locator("#watch-quantity").fill("1")
        page.locator("#watch-cost").fill("1")
        page.locator("#watch-form button").click()
        match = page.locator("#watch-body tr").filter(has_text=non_ai_quote["symbol"]).first
        assert match.count()
        actual = displayed_number(match.locator("td").nth(1).inner_text())
        assert actual is not None and math.isclose(actual, non_ai_quote["close"], abs_tol=0.005)
        assert page.locator(f'#stock-select option[value="{stock_key(non_ai_quote)}"]').count() == 0
        non_ai_check = stock_key(non_ai_quote)
    return {"state": "populated", "initial_visible_stocks": checked,
            "categories": category_checks,
            "progressive_list": pagination,
            "short_state_semantics": short_states,
            "non_ai_watchlist_quote": non_ai_check,
            "checks": ["original price and date", "search miss and reset", "category membership",
                       "detail navigation", "evidence links", "missing plans remain unavailable",
                       "six-card overview, complete expanded list and late-symbol search",
                       "local watchlist add and reload persistence", "non-AI holding quote isolation"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("ai-ui-evidence"))
    parser.add_argument("--chromium", help="Optional local Chromium; otherwise use Playwright's installed browser")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    state = State(args.site)
    server = server_for(state)
    base = f"http://127.0.0.1:{server.server_port}{PREFIX}"
    evidence = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "delivery": "existing rendered files served at the GitHub Pages project subpath",
        "browser": "Chromium desktop / mobile viewport emulation; not a physical Android",
        "market_data_injected": False, "viewports": [],
    }
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=args.chromium, channel="chromium", headless=True,
            )
            for label, width, height in (
                ("small-phone", 360, 800), ("phone", 390, 844),
                ("landscape", 844, 390), ("desktop", 1440, 1100),
            ):
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    is_mobile=width < 900, has_touch=width < 900,
                )
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                pages = []
                for slug in ("index", "preopen", "postclose", "final"):
                    page.goto(base + f"{slug}.html", wait_until="networkidle")
                    report = report_data(page)
                    original_digest = digest(report)
                    view = json.loads(page.locator("#ai-view-data").text_content())
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (label, slug)
                    assert all(page.locator(f"main > section#{section}").count() for section in REQUIRED_SECTIONS)
                    assert page.locator("#ai-observation").count()
                    assert page.locator("#report-refresh").is_visible()
                    assert page.locator("#ai-observation-search").is_visible()
                    assert page.locator(".research-readiness").is_visible()
                    page.evaluate("""() => {document.documentElement.style.scrollBehavior='auto';
                      window.scrollTo(0,0);}""")
                    surface = inspect_simple_surface(
                        page, view, report, first_screen=slug == "index" and width <= 390,
                    )
                    details = page.locator(".readiness-details")
                    assert details.count() == 1
                    assert not details.evaluate("node => node.open")
                    details.locator(":scope > summary").click()
                    assert details.evaluate("node => node.open")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (label, slug, "expanded readiness")
                    details.locator(":scope > summary").click()
                    # Scope selection must occur in analysis, not by pretending
                    # old full-market reports were already AI-only predictions.
                    scope = report.get("research_scope", {})
                    if isinstance(scope, dict) and scope.get("id") == "ai_supply_chain":
                        observations = {stock_key(stock) for stock in view.get("observations", [])}
                        candidates = report.get("analysis", {}).get("candidates", [])
                        assert all(stock_key(stock) in observations for stock in candidates)
                        research_keys = {stock_key(stock) for stock in report.get("analysis", {}).get("stocks", [])}
                        option_keys = set(page.locator("#stock-select option").evaluate_all(
                            "options => options.map(option => option.value).filter(Boolean)"))
                        assert option_keys <= research_keys, "Non-AI watchlist quotes leaked into research detail"
                    if slug == "index":
                        interactions = exercise_observations(page, view, report)
                        page.goto(base + "index.html", wait_until="networkidle")
                        assert digest(report_data(page)) == original_digest
                        page.evaluate("() => window.scrollTo(0, 0)")
                        page.screenshot(path=str(args.out / f"{label}-overview.png"))
                    else:
                        assert_original_prices(page, view.get("observations", []))
                        interactions = {"state": "independent phase page", "checks": ["original prices / phase metadata"]}
                    pages.append({"page": slug, "report_phase": report.get("phase"),
                                  "as_of": report.get("as_of"), "data_date": report.get("data_date"),
                                  "scope": scope.get("id") if isinstance(scope, dict) else scope,
                                  "surface": surface, "checks": interactions})
                    assert not errors, errors
                evidence["viewports"].append({"name": label, "width": width, "height": height,
                                               "pages": pages, "js_errors": errors})
                print(f"{label}: AI cards, data progress, original prices and preserved sections passed", flush=True)
                context.close()
            browser.close()
        assert all(request["method"] == "GET" for request in state.requests)
        evidence["network"] = "public GET only; no analysis dispatch / watchlist upload / order submission"
        evidence["result"] = "passed"
        (args.out / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"result": "passed", "evidence": str(args.out / 'evidence.json')}, ensure_ascii=False))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
