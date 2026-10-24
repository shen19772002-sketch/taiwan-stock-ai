"""Synthetic future observations test timestamp isolation and costs."""

from datetime import date, timedelta

import pytest

from taiwan_stock_ai.performance import evaluate


def signal(available="2025-01-01T08:00:00+00:00"):
    return {"market": "TWSE", "symbol": "9999", "name": "合成測試",
            "signal_date": "2025-01-01", "available_at": available, "close": 100}


def bars(count=20):
    return [{"market": "TWSE", "symbol": "9999", "date": (date(2025, 1, 2) + timedelta(days=i)).isoformat(),
             "open": 100, "close": 100, "complete": True} for i in range(count)]


def test_costs_and_horizons_separate():
    result = evaluate([signal()], bars(), "2025-02-01")
    assert result["evaluated"] == 1
    assert len(result["records"]) == 2
    expected = (100 * .999 * (1 - .001425 - .003)) / (100 * 1.001 * (1 + .001425)) - 1
    assert result["average_return"] == pytest.approx(expected)
    assert result["win_rate"] == 0


def test_future_and_publication_time_not_used():
    result = evaluate([signal()], bars(), "2025-01-04")
    assert all(row["status"] == "pending" for row in result["records"])
    delayed = evaluate([signal("2025-01-20T08:00:00+00:00")], bars(), "2025-02-01")
    assert all(row["entry_date"] == "2025-01-21" for row in delayed["records"])
    assert delayed["evaluated"] == 0


def test_unadjusted_action_excluded():
    quotes = bars()
    quotes[2]["close"] = 50
    result = evaluate([signal()], quotes, "2025-02-01")
    assert result["evaluated"] == 0
    assert all(row["status"] == "excluded_unadjusted" for row in result["records"])
