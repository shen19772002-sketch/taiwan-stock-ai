"""Synthetic records test persistence; they never seed application data."""

from taiwan_stock_ai.storage import Store


def observation(close=100, fetched="2025-01-06T08:00:00+00:00", day="2025-01-06"):
    return {"market": "TWSE", "symbol": "9999", "date": day, "close": close, "fetched_at": fetched}


def test_revision_history_and_known_at(tmp_path):
    with Store(tmp_path / "db.sqlite3") as store:
        original = observation()
        assert store.ingest("quotes", [original], "2025-01-06") == 1
        assert store.ingest("quotes", [original], "2025-01-06") == 0
        corrected = observation(101, "2025-01-07T08:00:00+00:00")
        store.ingest("quotes", [corrected], "2025-01-07")
        assert store.records("quotes", "2025-01-06")[0]["close"] == 101
        assert store.records("quotes", "2025-01-06", "2025-01-06T09:00:00+00:00")[0]["close"] == 100
        assert store.db.execute("SELECT count(*) FROM observations").fetchone()[0] == 2
        assert store.ingest("quotes", [observation(day="2025-01-08")], "2025-01-07") == 0
        store.backup(tmp_path / "backup.sqlite3")
    with Store(tmp_path / "backup.sqlite3") as restored:
        assert restored.check()
        assert restored.records("quotes", "2025-01-07")[0]["close"] == 101


def test_original_forecast_immutable_and_repeatable(tmp_path):
    stock = {**observation(), "name": "合成測試", "score": 70}
    report = {"as_of": "2025-01-06", "generated_at": "2025-01-06T08:00:00+00:00",
              "status": "ready", "analysis": {"candidates": [stock]}}
    with Store(tmp_path / "db.sqlite3") as store:
        store.save_report(report)
        store.save_report(report)
        report["generated_at"] = "2025-01-06T10:00:00+00:00"
        stock["score"] = 90
        store.save_report(report)
        assert len(store.history()) == 2
        assert len(store.signals("2025-01-06")) == 1
        assert store.signals("2025-01-06")[0]["score"] == 70
        report["as_of"] = "2025-01-07"
        store.save_report(report)
        assert len(store.signals("2025-01-07")) == 1
