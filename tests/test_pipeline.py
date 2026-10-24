"""End-to-end pipeline failure handling with explicitly synthetic collectors."""

import json
from datetime import date, datetime, timezone

import pytest

from taiwan_stock_ai.cli import main
from taiwan_stock_ai.pipeline import resolve_phase, run
from taiwan_stock_ai.storage import Store


NOW = datetime(2025, 1, 6, 10, 30, tzinfo=timezone.utc)


def blocked_collector(as_of, raw_dir):
    return {"quotes": [], "fundamentals": [], "institutions": [],
            "calendar": {"status": "unknown", "trading_day": None},
            "sources": [{"id": "synthetic_error", "url": "https://example.invalid", "status": "error",
                         "error": "Synthetic test network error", "fetched_at": NOW.isoformat(),
                         "data_date": None, "row_count": 0}]}


def forbidden_collector(*_):
    raise AssertionError("collector should not run")


def test_failed_source_builds_honest_site_and_keeps_evidence(tmp_path):
    report = run(tmp_path / "data", tmp_path / "site", collector=blocked_collector, now=NOW)
    assert report["status"] == "waiting"
    assert report["analysis"]["candidates"] == []
    assert report["data_date"] is None
    assert (tmp_path / "site" / "index.html").is_file()
    assert json.loads((tmp_path / "data" / "latest.json").read_text())["sources"][0]["status"] == "error"
    with Store(tmp_path / "data" / "market.sqlite3") as store:
        assert store.check()
        assert len(store.history()) == 1
        assert store.signals("2025-01-06") == []


def test_offline_retains_source_dates_and_exports_historical_report(tmp_path):
    original = run(tmp_path / "data", tmp_path / "site", collector=blocked_collector, now=NOW)
    retained = run(tmp_path / "data", tmp_path / "site", offline=True,
                   collector=forbidden_collector, now=NOW.replace(minute=35))
    assert retained["sources"] == original["sources"]
    assert retained["source_mode"] == "retained"
    history_id = retained["history"][0]["id"]
    saved = json.loads((tmp_path / "site" / "history" / f"{history_id}.json").read_text())
    assert saved["generated_at"] == original["generated_at"]


@pytest.mark.parametrize("phase,offline,day,status", [
    ("intraday", False, date(2025, 1, 6), "waiting"),
    ("manual", True, date(2025, 1, 6), "waiting"),
    ("postclose", False, date(2025, 1, 5), "non_trading_day"),
])
def test_no_unauthorized_intraday_or_weekend_collection(tmp_path, phase, offline, day, status):
    result = run(tmp_path / "data", tmp_path / "site", phase, day, offline,
                 collector=forbidden_collector, now=NOW)
    assert result["status"] == status
    assert not result["analysis"]["candidates"]


def test_official_holiday_skips_selection(tmp_path):
    def holiday(as_of, raw_dir):
        result = blocked_collector(as_of, raw_dir)
        result["calendar"] = {"status": "verified", "trading_day": False}
        return result
    result = run(tmp_path / "data", tmp_path / "site", collector=holiday, now=NOW)
    assert result["status"] == "non_trading_day"
    assert not result["analysis"]["candidates"]


@pytest.mark.parametrize("hour,minute,phase", [(0,20,"preopen"), (4,30,"intraday"),
                                             (7,30,"postclose"), (10,30,"final"), (12,30,"final")])
def test_schedule_timezone(hour, minute, phase):
    assert resolve_phase("auto", NOW.replace(hour=hour, minute=minute)) == phase


def test_future_report_rejected(tmp_path):
    with pytest.raises(ValueError, match="未來"):
        run(tmp_path / "data", tmp_path / "site", as_of=date(2025, 1, 7), now=NOW)


def test_historical_research_never_registers_backdated_prediction(tmp_path, monkeypatch):
    synthetic_stock = {"market": "TWSE", "symbol": "9999", "name": "合成測試", "date": "2025-01-03",
                       "close": 100, "eligible": True, "score": 70}
    def historical(as_of, raw_dir):
        result = blocked_collector(as_of, raw_dir)
        result["quotes"] = [{**synthetic_stock, "fetched_at": NOW.isoformat()}]
        result["calendar"] = {"status": "verified", "trading_day": True}
        return result
    monkeypatch.setattr("taiwan_stock_ai.pipeline.analyze", lambda *args, **kwargs: {
        "market": {"technical_ready": 1}, "candidates": [synthetic_stock], "stocks": [],
        "industries": [], "themes": [], "limitations": [], "methodology": {}})
    run(tmp_path / "data", tmp_path / "site", as_of=date(2025, 1, 3), collector=historical, now=NOW)
    with Store(tmp_path / "data" / "market.sqlite3") as store:
        assert store.signals("2025-01-06") == []


def test_missing_database_check_fails(tmp_path):
    with pytest.raises(SystemExit) as error:
        main(["check-db", "--data-dir", str(tmp_path / "missing")])
    assert error.value.code == 2
