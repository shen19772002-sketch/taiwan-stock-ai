"""Next-week readiness uses copied official calendar facts, no network requests.

The holiday row is copied verbatim from TWSE's 2026 official calendar response.
The market-price rows below are explicitly synthetic control-flow fixtures.
"""

from datetime import date, datetime, timezone
import hashlib
import json

import pytest

from taiwan_stock_ai.pipeline import run
from taiwan_stock_ai.providers import parse_calendar
from taiwan_stock_ai.storage import Store


CALENDAR_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
CALENDAR_SHA256 = "7644c1a8af784c09f54670fd7413f536b13eb76c54d658058e8873d1aee32117"
OFFICIAL_ROWS = [{
    "Name": "臺灣光復暨金門古寧頭大捷紀念日", "Date": "1151026", "Weekday": "一",
    "Description": "臺灣光復暨金門古寧頭大捷紀念日為10月25日適逢星期日，於10月26日（星期一）補假。",
}]


def test_next_monday_is_explicit_official_holiday():
    calendar = parse_calendar(OFFICIAL_ROWS, date(2026, 10, 26), CALENDAR_URL)
    assert calendar["status"] == "verified"
    assert calendar["trading_day"] is False
    assert "2026-10-26" in calendar["holiday_dates"]


def test_unlisted_tuesday_is_not_falsely_certified_open():
    calendar = parse_calendar(OFFICIAL_ROWS, date(2026, 10, 27), CALENDAR_URL)
    assert calendar["status"] == "unknown"
    assert calendar["trading_day"] is None


@pytest.mark.parametrize("phase", ["preopen", "postclose", "final", "manual"])
def test_october_26_holiday_suppresses_new_selections(tmp_path, phase):
    now = datetime(2026, 10, 26, 12, 30, tzinfo=timezone.utc)
    def official_calendar_collector(as_of, raw_dir):
        return {"quotes": [], "fundamentals": [], "institutions": [], "sources": [],
                "calendar": parse_calendar(OFFICIAL_ROWS, as_of, CALENDAR_URL)}
    report = run(tmp_path / "data", tmp_path / "site", phase=phase,
                 collector=official_calendar_collector, now=now)
    assert report["status"] == "non_trading_day"
    assert report["analysis"]["candidates"] == []
    with Store(tmp_path / "data" / "market.sqlite3") as store:
        assert store.signals("2026-10-26") == []


def synthetic_collector(quote_day, now):
    def collect(as_of, raw_dir):
        stock = {"market": "TWSE", "symbol": "9999", "name": "合成控制流程測試",
                 "industry": "測試產業", "date": quote_day, "open": 100, "high": 101,
                 "low": 99, "close": 100, "volume": 1000, "turnover": 100000,
                 "source": "https://example.invalid/test", "fetched_at": now.isoformat(), "complete": True}
        return {"quotes": [stock], "fundamentals": [], "institutions": [], "sources": [],
                "calendar": {"status": "verified" if quote_day == as_of.isoformat() else "unknown",
                             "trading_day": True if quote_day == as_of.isoformat() else None}}
    return collect


def test_tuesday_preopen_keeps_actual_previous_quote_date(tmp_path):
    now = datetime(2026, 10, 27, 0, 20, tzinfo=timezone.utc)
    report = run(tmp_path / "data", tmp_path / "site", phase="preopen",
                 collector=synthetic_collector("2026-10-22", now), now=now)
    assert report["as_of"] == "2026-10-27"
    assert report["data_date"] == "2026-10-22"
    assert report["analysis"]["stocks"][0]["date"] == "2026-10-22"
    assert any("最新可驗證交易日期為 2026-10-22" in text for text in report["notices"])
    with Store(tmp_path / "data" / "market.sqlite3") as store:
        assert store.signals("2026-10-27") == []


def test_tuesday_intraday_never_fetches_unlicensed_live_prices(tmp_path):
    def forbidden(*args):
        raise AssertionError("No authorized intraday provider exists")
    report = run(tmp_path / "data", tmp_path / "site", phase="intraday", collector=forbidden,
                 now=datetime(2026, 10, 27, 4, 30, tzinfo=timezone.utc))
    assert report["status"] == "waiting"
    assert report["data_date"] is None
    assert any("尚未接入有授權的盤中來源" in text for text in report["notices"])


def save_cached_calendar(data_dir, rows=OFFICIAL_ROWS, *, change=None):
    """Archive a copied official calendar fixture, never production market data."""
    archive = data_dir / "raw" / "calendar-fixture" / "twse_calendar.json"
    archive.parent.mkdir(parents=True)
    raw = json.dumps(rows, ensure_ascii=False).encode()
    archive.write_bytes(raw)
    source = {"id": "twse_calendar", "url": CALENDAR_URL, "status": "ok",
              "fetched_at": "2026-10-23T09:00:00+00:00", "raw_path": str(archive),
              "sha256": hashlib.sha256(raw).hexdigest(), "row_count": len(rows),
              "data_date": "2026-10-26", "error": None}
    if change:
        change(source, archive)
    with Store(data_dir / "market.sqlite3") as store:
        store.save_report({"as_of": "2026-10-23", "generated_at": "2026-10-23T09:01:00+00:00",
                           "status": "waiting", "sources": [source], "analysis": {"candidates": []}})
    return source


@pytest.mark.parametrize("phase,offline", [("intraday", False), ("manual", True)])
def test_cached_verified_holiday_skips_research_without_network(tmp_path, phase, offline):
    data_dir = tmp_path / "data"
    source = save_cached_calendar(data_dir)
    def forbidden(*args):
        raise AssertionError("Cached holiday must not request prices")
    report = run(data_dir, tmp_path / "site", phase=phase, offline=offline, collector=forbidden,
                 now=datetime(2026, 10, 26, 4, 30, tzinfo=timezone.utc))
    assert report["status"] == "non_trading_day"
    assert report["calendar"]["source_mode"] == "retained"
    assert report["calendar"]["sha256"] == source["sha256"]
    assert report["calendar"]["fetched_at"] == source["fetched_at"]
    assert report["analysis"]["candidates"] == []
    with Store(data_dir / "market.sqlite3") as store:
        assert store.signals("2026-10-26") == []


def test_cached_calendar_relative_archive_survives_restore(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    save_cached_calendar(data_dir, change=lambda source, archive: source.update(
        raw_path=str(archive.relative_to(tmp_path))))
    report = run(data_dir, tmp_path / "site", phase="intraday",
                 now=datetime(2026, 10, 26, 4, 30, tzinfo=timezone.utc))
    assert report["status"] == "non_trading_day"


@pytest.mark.parametrize("fault", ["missing", "tampered", "outside", "wrong_url", "future"])
def test_unverified_cached_calendar_keeps_holiday_unknown(tmp_path, fault):
    def change(source, archive):
        if fault == "missing":
            archive.unlink()
        elif fault == "tampered":
            archive.write_bytes(b"[]")
        elif fault == "outside":
            outside = tmp_path / "outside.json"
            outside.write_bytes(archive.read_bytes())
            source["raw_path"] = str(outside)
        elif fault == "wrong_url":
            source["url"] = "https://example.invalid/calendar"
        elif fault == "future":
            source["fetched_at"] = "2026-10-27T09:00:00+00:00"
    data_dir = tmp_path / "data"
    save_cached_calendar(data_dir, change=change)
    report = run(data_dir, tmp_path / "site", phase="intraday",
                 now=datetime(2026, 10, 26, 4, 30, tzinfo=timezone.utc))
    assert report["status"] == "waiting"
    assert report["calendar"]["status"] == "unknown"
    assert report["calendar"]["trading_day"] is None


@pytest.mark.parametrize("calendar_rows", [OFFICIAL_ROWS, [{
    "Date": "1151027", "Name": "開始交易日", "Description": "合成測試：快取不確認開市",
}]])
def test_cached_calendar_never_certifies_tuesday_open(tmp_path, calendar_rows):
    data_dir = tmp_path / "data"
    save_cached_calendar(data_dir, calendar_rows)
    report = run(data_dir, tmp_path / "site", phase="intraday",
                 now=datetime(2026, 10, 27, 4, 30, tzinfo=timezone.utc))
    assert report["status"] == "waiting"
    assert report["calendar"]["trading_day"] is None


@pytest.mark.parametrize("quote_day,expected_signals", [("2026-10-22", 0), ("2026-10-27", 1)])
def test_tuesday_postclose_records_only_verified_same_day_signal(tmp_path, monkeypatch, quote_day, expected_signals):
    now = datetime(2026, 10, 27, 7, 30, tzinfo=timezone.utc)
    def synthetic_analysis(quotes, *args, **kwargs):
        return {"market": {"technical_ready": 1}, "candidates": [{**quotes[-1], "score": 70}],
                "stocks": [], "industries": [], "themes": [], "limitations": [], "methodology": {}}
    monkeypatch.setattr("taiwan_stock_ai.pipeline.analyze", synthetic_analysis)
    run(tmp_path / "data", tmp_path / "site", phase="postclose",
        collector=synthetic_collector(quote_day, now), now=now)
    with Store(tmp_path / "data" / "market.sqlite3") as store:
        assert len(store.signals("2026-10-27")) == expected_signals
