"""Scope integration uses synthetic observations only, never published prices."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json

import pytest

from taiwan_stock_ai import ai_universe
from taiwan_stock_ai.cli import main
from taiwan_stock_ai.pipeline import _latest_watchlist_quotes, run
from taiwan_stock_ai.storage import Store, canonical


NOW = datetime(2026, 10, 29, 10, 30, tzinfo=timezone.utc)
DAY = "2026-10-29"


@pytest.fixture(autouse=True)
def synthetic_registry(monkeypatch):
    registry = {
        "id": "ai_supply_chain", "version": "synthetic-test-only", "reviewed_at": DAY,
        "registry_hash": "f" * 64,
        "categories": [{"id": identifier, "name": name, "description": "Synthetic category"}
                       for identifier, name in [("chip", "測試晶片"), ("packaging", "測試封裝"),
                                                ("server", "測試伺服器")]],
        "members": [{
            "market": "TWSE", "symbol": symbol, "name": "Synthetic security",
            "primary_category": primary, "category_ids": tags, "known_since": DAY,
            "rationale": "Synthetic classification used only to test pipeline boundaries",
            "evidence": [{"url": "https://example.invalid/test-fixture", "title": "Synthetic evidence",
                          "known_since": DAY, "reviewed_at": DAY, "verification_status": "verified",
                          "source_sha256": "0" * 64, "excerpt": "Synthetic test fixture, not issuer evidence"}],
        } for symbol, primary, tags in [("2330", "chip", ["chip", "packaging"]),
                                        ("2382", "server", ["server"])]]}
    ai_universe._validate_registry(registry)
    monkeypatch.setattr(ai_universe, "_load_registry", lambda: deepcopy(registry))
    return registry


def synthetic_quotes(end=DAY, symbols=("2330", "2382", "9999")):
    end_day = date.fromisoformat(end)
    rows = []
    for symbol in symbols:
        for index in range(65):
            close = 50 + index * .07 + (index % 5 - 2) * .1
            rows.append({"market": "TWSE", "symbol": symbol, "name": "合成測試資料",
                         "industry": "同一官方產業", "date": (end_day - timedelta(days=64-index)).isoformat(),
                         "open": close, "high": close + .5, "low": close - .5, "close": close,
                         "volume": 100000 + index * 500, "turnover": close * (100000 + index * 500),
                         "complete": True, "fetched_at": NOW.isoformat(), "source": "synthetic-test-only"})
    return rows


def synthetic_collection(end=DAY):
    return {
        "quotes": synthetic_quotes(end),
        "fundamentals": [{"market": "TWSE", "symbol": symbol, "date": end,
                          "pe": 12, "pb": 1.5, "dividend_yield": 4, "complete": True,
                          "fetched_at": NOW.isoformat()} for symbol in ("2330", "2382", "9999")],
        "institutions": [], "sources": [],
        "calendar": {"trading_day": True, "status": "verified"},
    }


def test_ai_filter_precedes_scoring_and_keeps_non_ai_holdings_prices(tmp_path):
    data = tmp_path / "data"
    collected = synthetic_collection()
    original = deepcopy(collected)
    report = run(data, tmp_path / "site", phase="postclose", now=NOW,
                 collector=lambda *_: collected, research_scope="ai")
    analysis = report["analysis"]
    assert report["research_scope"]["id"] == "ai_supply_chain"
    assert {stock["symbol"] for stock in analysis["stocks"]} == {"2330", "2382"}
    assert {stock["symbol"] for stock in analysis["candidates"]} == {"2330", "2382"}
    assert {industry["industry"] for industry in analysis["industries"]} == {"測試晶片", "測試伺服器"}
    assert "測試封裝" not in {industry["industry"] for industry in analysis["industries"]}
    assert sum(industry["turnover_share"] for industry in analysis["industries"]) == pytest.approx(1)
    expected = sum(row["turnover"] for row in collected["quotes"]
                   if row["date"] == DAY and row["symbol"] != "9999")
    assert analysis["market"]["turnover"] == pytest.approx(expected)
    assert analysis["market"]["covered_stocks"] == 2
    assert all(stock["official_industry"] == "同一官方產業" for stock in analysis["stocks"])
    assert all(stock["ai_evidence"] for stock in analysis["stocks"])
    assert analysis["scope_readiness"]["history_ready"] == 2
    assert analysis["scope_readiness"]["current_valuations"] == 2
    assert {stock["symbol"] for stock in report["watchlist_quotes"]} == {"2330", "2382", "9999"}
    assert all("score" not in stock and "plan" not in stock for stock in report["watchlist_quotes"])
    assert collected == original
    with Store(data / "market.sqlite3") as store:
        assert {q["symbol"] for q in store.records("quotes", DAY)} == {"2330", "2382", "9999"}
        assert {signal["symbol"] for signal in store.signals(DAY)} == {"2330", "2382"}


def test_preopen_classifies_on_research_day_without_using_same_day_prices(tmp_path):
    collected = synthetic_collection()
    collected["quotes"] += synthetic_quotes("2026-10-28")
    collected["fundamentals"] += [{**row, "date": "2026-10-28"} for row in collected["fundamentals"]]
    report = run(tmp_path / "data", tmp_path / "site", phase="preopen", now=NOW,
                 collector=lambda *_: collected, research_scope="ai")
    assert report["research_scope"]["classification_as_of"] == DAY
    assert report["analysis"]["as_of"] == "2026-10-28"
    assert {stock["date"] for stock in report["analysis"]["stocks"]} == {"2026-10-28"}
    assert {stock["symbol"] for stock in report["analysis"]["stocks"]} == {"2330", "2382"}
    assert all(quote["date"] == "2026-10-28" for quote in report["watchlist_quotes"])
    with Store(tmp_path / "data" / "market.sqlite3") as store:
        assert store.signals(DAY) == []


def test_new_classification_does_not_create_historical_ai_candidates(tmp_path):
    collected = synthetic_collection("2026-10-28")
    report = run(tmp_path / "data", tmp_path / "site", phase="postclose",
                 as_of=date(2026, 10, 28), now=NOW, collector=lambda *_: collected, research_scope="ai")
    assert report["analysis"]["stocks"] == []
    assert report["analysis"]["candidates"] == []
    assert report["research_scope"]["member_count"] == 0
    assert len(report["watchlist_quotes"]) == 3


def test_legacy_report_and_signal_remain_immutable_after_scope_change(tmp_path):
    data = tmp_path / "data"
    legacy = {"as_of": "2026-10-28", "generated_at": "2026-10-28T10:30:00+00:00",
              "phase": "postclose", "status": "ready", "analysis": {"candidates": [
                  {"market": "TWSE", "symbol": "9999", "name": "合成舊訊號", "close": 100,
                   "date": "2026-10-28"}], "stocks": []}}
    with Store(data / "market.sqlite3") as store:
        legacy_id = store.save_report(legacy)
        original_signal = store.signals(DAY)[0]
    original_path = data / "reports" / f"2026-10-28-postclose-{legacy_id[:16]}.json"
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_text(canonical(legacy))
    original_bytes = original_path.read_bytes()
    report = run(data, tmp_path / "site", phase="postclose", now=NOW,
                 collector=lambda *_: synthetic_collection(), research_scope="ai")
    assert original_path.read_bytes() == original_bytes
    with Store(data / "market.sqlite3") as store:
        assert store.get_report(legacy_id) == legacy
        signals = store.signals(DAY)
        assert next(signal for signal in signals if signal["symbol"] == "9999") == original_signal
        assert {signal["symbol"] for signal in signals if signal["signal_date"] == DAY} == {"2330", "2382"}
    assert report["performance"]["total_signals"] == 1
    assert report["tracking_scope"]["id"] == "historical_and_ai"
    assert "非 AI 專屬" in report["tracking_scope"]["label"]
    assert report["performance"]["limitations"][0] == report["tracking_scope"]["label"]


def test_ai_holiday_does_not_borrow_legacy_all_market_analysis(tmp_path):
    data = tmp_path / "data"
    with Store(data / "market.sqlite3") as store:
        store.ingest("quotes", synthetic_quotes(), DAY)
        store.save_report({"as_of": DAY, "generated_at": NOW.replace(hour=8).isoformat(),
                           "phase": "postclose", "status": "ready", "analysis": {
                               "as_of": DAY, "candidates": [], "stocks": [{"symbol": "9999"}]}})
    report = run(data, tmp_path / "site", phase="final", now=NOW,
                 collector=lambda *_: {**synthetic_collection(), "calendar": {
                     "status": "verified", "trading_day": False}}, research_scope="ai")
    assert report["status"] == "non_trading_day"
    assert report["analysis"]["stocks"] == []


def test_incomplete_new_watchlist_quote_does_not_revive_old_price():
    rows = synthetic_quotes(symbols=("9999",))
    assert _latest_watchlist_quotes(rows)[0]["date"] == DAY
    broken = {**rows[-1], "complete": False, "fetched_at": NOW.replace(minute=40).isoformat()}
    assert _latest_watchlist_quotes(rows + [broken]) == []


@pytest.mark.parametrize("extra,expected", [([], "ai"), (["--scope", "all"], "all")])
def test_cli_defaults_existing_actions_entry_to_ai(tmp_path, monkeypatch, capsys, extra, expected):
    seen = {}
    def fake_run(*args, **kwargs):
        seen.update(kwargs)
        return {"status": "waiting", "as_of": DAY, "phase": "manual", "generated_at": NOW.isoformat(),
                "data_date": None, "sources": [], "notices": [], "analysis": {"candidates": []},
                "research_scope": {"id": "ai_supply_chain" if kwargs["research_scope"] == "ai" else "all_market"}}
    monkeypatch.setattr("taiwan_stock_ai.cli.run", fake_run)
    assert main(["run", "--offline", "--data-dir", str(tmp_path / "data"),
                 "--output", str(tmp_path / "site"), *extra]) == 0
    assert seen["research_scope"] == expected
    assert json.loads(capsys.readouterr().out)["research_scope"]["id"] == (
        "ai_supply_chain" if expected == "ai" else "all_market")


def test_invalid_scope_rejected_before_collection(tmp_path):
    with pytest.raises(ValueError, match="research_scope"):
        run(tmp_path / "data", tmp_path / "site", research_scope="everything")
