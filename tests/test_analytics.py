"""Synthetic fixtures are test inputs only and are never report fallbacks."""

import copy
import json
from datetime import date, timedelta

import pytest

from taiwan_stock_ai.analytics import analyze, price_plan, round_price, tick_size


def bars(count=65, symbol="TEST", offset=0, flat=False):
    start = date(2025, 1, 1)
    rows = []
    for index in range(count):
        close = 50 + offset if flat else 50 + offset + index * .07 + (index % 5 - 2) * .1
        rows.append({"market": "TWSE", "symbol": symbol, "name": "測試用合成資料",
                     "industry": "測試產業", "date": (start + timedelta(days=index)).isoformat(),
                     "open": close, "high": close if flat else close + .5,
                     "low": close if flat else close - .5, "close": close,
                     "volume": 100000 + index * 500, "turnover": close * (100000 + index * 500),
                     "source": "test-fixture", "fetched_at": "2025-05-01T00:00:00Z", "complete": True})
    return rows


def valuation(day, symbol="TEST", **extra):
    return {"market": "TWSE", "symbol": symbol, "date": day,
            "pe": 12, "pb": 1.5, "dividend_yield": 4, "complete": True, **extra}


def test_warmup_is_real_and_duplicates_do_not_add_history():
    quotes = bars(59)
    report = analyze(quotes + quotes, [valuation(quotes[-1]["date"])])
    assert report["candidates"] == []
    assert report["stocks"][0]["bars"] == 59
    assert report["stocks"][0]["plan"] is None
    assert report["market"]["status"] == "warming_up"


def test_eligible_strategy_costs_and_json_finite():
    quotes = bars()
    report = analyze(quotes, [valuation(quotes[-1]["date"])])
    assert len(report["candidates"]) == 1
    stock = report["candidates"][0]
    plan = stock["plan"]
    assert plan["stop"] < plan["entry_low"] <= plan["entry_high"] < plan["target"]
    assert plan["net_risk_reward"] >= 2
    assert plan["risk_reward"] > plan["net_risk_reward"]
    assert stock["coverage"] == .9
    assert stock["score_components"][-1]["score"] is None
    json.dumps(report, allow_nan=False)


def test_no_lookahead_in_prices_or_fundamentals():
    history = bars(65)
    cutoff = history[59]["date"]
    initial = analyze(history[:60], [valuation(cutoff)], as_of=cutoff)
    future = valuation(history[-1]["date"], pe=-10)
    combined = analyze(history, [valuation(cutoff), future], as_of=cutoff)
    assert combined == initial
    # A future-only positive PE cannot qualify the historical signal.
    assert analyze(history, [valuation(history[-1]["date"])], as_of=cutoff)["candidates"] == []


@pytest.mark.parametrize("pe", [None, 0, -5])
def test_missing_or_negative_earnings_never_qualifies(pe):
    quotes = bars()
    result = analyze(quotes, [valuation(quotes[-1]["date"], pe=pe)])
    assert result["candidates"] == []
    assert result["stocks"][0]["plan"] is None


def test_stale_prices_never_rank_as_current():
    quotes = bars()
    report = analyze(quotes, [valuation(quotes[-1]["date"])], as_of="2025-03-10")
    assert report["candidates"] == []
    assert report["market"]["status"] == "stale"
    assert "落後" in report["stocks"][0]["exclusions"][0]


def test_corporate_action_like_gap_withholds_strategy():
    quotes = bars()
    for row in quotes[30:]:
        for field in ("open", "high", "low", "close"):
            row[field] /= 2
    report = analyze(quotes, [valuation(quotes[-1]["date"])])
    assert report["candidates"] == []
    assert any("公司行動" in reason for reason in report["stocks"][0]["exclusions"])
    assert report["market"]["technical_ready"] == 0


def test_flat_prices_have_no_strategy_or_infinite_indicators():
    quotes = bars(flat=True)
    report = analyze(quotes, [valuation(quotes[-1]["date"])])
    assert report["candidates"] == []
    assert report["stocks"][0]["indicators"]["rsi14"] == 50
    assert report["stocks"][0]["plan"] is None
    json.dumps(report, allow_nan=False)
    assert price_plan(50, {"atr14": 0, "sma20": 50, "high20": 50}) is None


@pytest.mark.parametrize("price,tick,up,down", [
    (9.999, .01, 10, 9.99), (10.01, .05, 10.05, 10),
    (49.99, .05, 50, 49.95), (50.01, .1, 50.1, 50),
    (99.99, .1, 100, 99.9), (100.1, .5, 100.5, 100),
    (500.1, 1, 501, 500), (1000.1, 5, 1005, 1000)])
def test_tw_stock_tick_boundaries(price, tick, up, down):
    assert tick_size(price) == tick
    assert round_price(price, "up") == up
    assert round_price(price, "down") == down


def test_bad_ohlc_incomplete_and_future_rows_do_not_create_history():
    quotes = bars(60)
    broken = copy.deepcopy(quotes[-1])
    broken["high"] = 1
    quotes[-1] = broken
    report = analyze(quotes, [valuation(quotes[-1]["date"])])
    assert report["stocks"][0]["bars"] == 59
    assert not report["candidates"]


def test_max_fifteen_and_industry_proxy_label():
    quotes, fundamentals = [], []
    for index in range(20):
        symbol = f"TEST{index:02d}"
        rows = bars(symbol=symbol, offset=index)
        quotes.extend(rows)
        fundamentals.append(valuation(rows[-1]["date"], symbol))
    report = analyze(quotes, fundamentals)
    assert len(report["candidates"]) == 15
    assert sum(industry["turnover_share"] for industry in report["industries"]) == pytest.approx(1)
    assert "不代表" in report["industries"][0]["proxy_label"]
    assert all(theme["confidence"] == "未校準；不提供上漲機率" for theme in report["themes"])


def test_institution_data_must_be_same_day():
    quotes = bars()
    current_day = quotes[-1]["date"]
    institution = {"market": "TWSE", "symbol": "TEST", "date": current_day,
                   "net_buy": 20000, "complete": True}
    current = analyze(quotes, [valuation(current_day)], [institution])
    stale = analyze(quotes, [valuation(current_day)], [{**institution, "date": quotes[-2]["date"]}])
    assert current["candidates"][0]["coverage"] == 1
    assert stale["candidates"][0]["coverage"] == .9


def test_empty_is_waiting_and_does_not_invent_a_market():
    result = analyze([], as_of="2025-01-01")
    assert result["market"]["status"] == "waiting"
    assert result["stocks"] == result["candidates"] == result["themes"] == []


def test_unknown_industry_does_not_manufacture_sector_strength():
    quotes = bars()
    for row in quotes:
        row["industry"] = None
    report = analyze(quotes, [valuation(quotes[-1]["date"])])
    stock = report["stocks"][0]
    assert report["industries"][0]["score"] is None
    assert report["themes"] == []
    assert stock["coverage"] == .7


def test_equal_sector_momentum_receives_equal_rank():
    first, second = bars(symbol="A"), bars(symbol="B")
    for row in second:
        row["industry"] = "另一測試產業"
    report = analyze(first + second)
    assert report["industries"][0]["score"] == report["industries"][1]["score"]


def test_new_incomplete_valuation_does_not_revive_stale_positive_pe():
    quotes = bars()
    old = valuation(quotes[-2]["date"])
    current = valuation(quotes[-1]["date"], pe=None, complete=False)
    report = analyze(quotes, [old, current])
    stock = report["stocks"][0]
    assert report["candidates"] == []
    assert stock["pe"] is None
    assert stock["pb"] == 1.5
    assert stock["fundamental_date"] == current["date"]
    assert stock["plan"] is None


def test_partial_current_valuation_scores_only_observed_fields():
    quotes = bars()
    current = valuation(quotes[-1]["date"], dividend_yield=None, complete=False)
    report = analyze(quotes, [current])
    stock = report["candidates"][0]
    assert stock["pe"] == 12
    assert stock["dividend_yield"] is None
    assert stock["coverage"] == .85


def test_stale_valuation_excluded_instead_of_reused():
    quotes = bars()
    report = analyze(quotes, [valuation(quotes[-2]["date"])])
    stock = report["stocks"][0]
    assert stock["fundamental_status"] == "stale"
    assert stock["pe"] is None
    assert report["candidates"] == []
    assert any("估值日期落後" in reason for reason in stock["exclusions"])


def test_same_day_revision_can_invalidate_previously_positive_pe():
    quotes = bars()
    old = valuation(quotes[-1]["date"], fetched_at="2025-03-06T08:00:00+00:00")
    correction = valuation(quotes[-1]["date"], pe=None, complete=False,
                           fetched_at="2025-03-06T09:00:00+00:00")
    report = analyze(quotes, [correction, old])
    assert report["stocks"][0]["pe"] is None
    assert report["candidates"] == []


def test_first_observation_does_not_invent_zero_advancers():
    report = analyze(bars(1))
    assert report["market"]["advancers"] is None
    assert report["market"]["decliners"] is None
    assert report["market"]["comparable_stocks"] == 0
    comparable = analyze(bars(2))
    assert comparable["market"]["comparable_stocks"] == 1
    assert comparable["market"]["advancers"] == 1
