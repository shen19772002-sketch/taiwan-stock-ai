"""Synthetic records test persistence; they never seed application data."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import sqlite3
from threading import Barrier

import pytest

from taiwan_stock_ai.storage import Store, canonical


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


def research_report(phase="postclose", day="2025-01-06"):
    """A report with meaningful values and deliberately volatile provenance."""
    return {
        "schema_version": 1, "as_of": day, "phase": phase,
        "generated_at": f"{day}T08:00:00+00:00", "data_date": day,
        "status": "ready", "data_mode": "after_close", "source_mode": "fetched",
        "sources": [{"id": "twse_quotes", "url": "https://example.test/official",
                     "status": "ok", "data_date": day, "row_count": 1,
                     "accepted_rows": 1, "rejected_rows": 0, "error": None,
                     "fetched_at": f"{day}T07:59:00+00:00", "raw_path": "raw/first.json",
                     "sha256": "original-raw-digest"}],
        "calendar": {"status": "verified", "trading_day": True, "date": day,
                     "fetched_at": f"{day}T07:59:00+00:00", "raw_path": "raw/calendar.json"},
        "analysis": {"model_version": "v1", "candidates": [
            {**observation(day=day), "name": "合成測試", "score": 70,
             "pe": 12, "institution_net": 500, "complete": True}
        ]},
        "performance": {"status": "pending", "signals": 1, "records": []},
        "notices": [], "history": [],
    }


def test_report_rerun_uses_first_payload_despite_fresh_acquisition_metadata(tmp_path):
    original = research_report()
    rerun = deepcopy(original)
    rerun["generated_at"] = "2025-01-06T10:00:00+00:00"
    rerun["history"] = [{"id": "previous-run", "phase": "postclose"}]
    rerun["sources"][0].update(fetched_at=rerun["generated_at"], raw_path="raw/retry.json",
                                sha256="new-raw-byte-order-digest")
    rerun["calendar"].update(fetched_at=rerun["generated_at"], raw_path="raw/calendar-retry.json")
    rerun["analysis"]["candidates"][0]["fetched_at"] = rerun["generated_at"]
    with Store(tmp_path / "db.sqlite3") as store:
        run_id = store.save_report(original)
        assert store.save_report(rerun) == run_id
        assert store.get_report(run_id) == original
        assert store.get_report("unknown") is None
        assert len(store.history()) == 1
        assert store.history()[0]["phase"] == "postclose"
        assert store.db.execute("SELECT count(*) FROM report_versions").fetchone()[0] == 1
        assert store.signals(original["as_of"])[0]["available_at"] == original["generated_at"]
        store.backup(tmp_path / "backup.sqlite3")
    with Store(tmp_path / "backup.sqlite3") as restored:
        assert restored.save_report(rerun) == run_id
        assert restored.get_report(run_id) == original


@pytest.mark.parametrize(("path", "value"), [
    (("analysis", "candidates", 0, "close"), 101),
    (("analysis", "candidates", 0, "pe"), 11),
    (("analysis", "candidates", 0, "institution_net"), 600),
    (("analysis", "candidates", 0, "score"), 80),
    (("analysis", "model_version"), "v2"),
    (("status",), "waiting_data"),
    (("sources", 0, "status"), "partial"),
    (("sources", 0, "error"), "official feed incomplete"),
    (("sources", 0, "data_date"), "2025-01-03"),
    (("sources", 0, "rejected_rows"), 2),
    (("calendar", "status"), "unknown"),
    (("calendar", "trading_day"), False),
    (("performance", "status"), "available"),
])
def test_material_changes_retain_distinct_report_versions(tmp_path, path, value):
    original = research_report()
    revised = deepcopy(original)
    node = revised
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    with Store(tmp_path / "db.sqlite3") as store:
        first_id = store.save_report(original)
        revised_id = store.save_report(revised)
        assert revised_id != first_id
        assert store.get_report(first_id) == original
        assert store.get_report(revised_id) == revised
        assert len(store.history()) == 2
        # A corrected report must not retroactively improve the original signal.
        assert len(store.signals(original["as_of"])) == 1
        assert store.signals(original["as_of"])[0]["score"] == 70


def test_phases_and_dates_have_independent_versions(tmp_path):
    with Store(tmp_path / "db.sqlite3") as store:
        assert store.latest_phase_reports() == {}
        assert store.phase_selection_times() == {}
        reports = {phase: research_report(phase) for phase in ("preopen", "postclose", "final")}
        run_ids = {store.save_report(report) for report in reports.values()}
        assert len(run_ids) == 3
        assert store.latest_phase_reports() == reports
        assert {row["phase"] for row in store.history()} == set(reports)
        later = research_report("preopen", "2025-01-07")
        store.save_report(later)
        reports["preopen"] = later
        # A manually rerun older date must not displace the latest trading date.
        historical = research_report("preopen", "2025-01-03")
        historical["generated_at"] = "2025-01-08T08:00:00+00:00"
        store.save_report(historical)
        store.save_report(research_report("manual", "2025-01-09"))
        assert store.latest_phase_reports() == reports
        updated = deepcopy(reports["final"])
        updated["analysis"]["candidates"][0]["institution_net"] = 700
        updated["generated_at"] = "2025-01-06T12:00:00+00:00"
        store.save_report(updated)
        assert store.latest_phase_reports()["final"] == updated


def test_display_only_report_cannot_acquire_backdated_forecasts(tmp_path):
    original = research_report()
    with Store(tmp_path / "db.sqlite3") as store:
        run_id = store.save_report(original, record_signals=False)
        assert store.save_report(original, record_signals=True) == run_id
        assert store.signals(original["as_of"]) == []


def test_recovered_content_reselects_original_report_without_duplicate_version(tmp_path):
    original = research_report()
    unavailable = deepcopy(original)
    unavailable["generated_at"] = "2025-01-06T09:00:00+00:00"
    unavailable["status"] = "waiting_data"
    unavailable["sources"][0]["status"] = "error"
    recovered = deepcopy(original)
    recovered["generated_at"] = "2025-01-06T10:00:00+00:00"
    recovered["sources"][0]["fetched_at"] = recovered["generated_at"]
    with Store(tmp_path / "db.sqlite3") as store:
        original_id = store.save_report(original)
        unavailable_id = store.save_report(unavailable)
        assert store.latest_phase_reports()["postclose"] == unavailable
        assert store.latest_sources() == unavailable["sources"]
        assert store.save_report(recovered) == original_id
        assert len(store.history()) == 2
        assert store.latest_phase_reports()["postclose"] == original
        assert store.phase_selection_times()["postclose"] == recovered["generated_at"]
        assert store.get_report(original_id)["generated_at"] == original["generated_at"]
        assert store.latest_sources() == original["sources"]
        assert store.get_report(unavailable_id) == unavailable
        # A slower invocation finishing afterwards cannot undo recovery, whether
        # its content is already known or is a distinct old snapshot.
        assert store.save_report(unavailable) == unavailable_id
        stale = deepcopy(unavailable)
        stale["analysis"]["candidates"][0]["score"] = 80
        store.save_report(stale)
        assert store.latest_phase_reports()["postclose"] == original
        assert store.latest_sources() == original["sources"]
        # Neither a newer invocation for an older date nor its retry replaces
        # the current trading date, even across different phase source heads.
        historical = research_report("postclose", "2025-01-03")
        historical["generated_at"] = "2025-01-07T08:00:00+00:00"
        store.save_report(historical)
        store.save_report(historical)
        historical["phase"] = "manual"
        store.save_report(historical)
        assert store.latest_phase_reports()["postclose"] == original
        assert store.latest_sources() == original["sources"]
    with Store(tmp_path / "db.sqlite3") as reopened:
        assert reopened.latest_phase_reports()["postclose"] == original
        assert reopened.latest_sources() == original["sources"]
        assert reopened.phase_selection_times()["postclose"] == recovered["generated_at"]


def test_v2_migration_seeds_phase_heads_without_rewriting_evidence(tmp_path):
    path = tmp_path / "v2.sqlite3"
    reports = {phase: research_report(phase) for phase in ("preopen", "postclose", "final", "manual")}
    reports["manual"]["generated_at"] = "2025-01-06T12:00:00+00:00"
    reports["manual"]["sources"][0]["status"] = "partial"
    with Store(path) as store:
        store.ingest("quotes", [observation()], "2025-01-06")
        for report in reports.values():
            store.save_report(report)
        historical = research_report("preopen", "2025-01-03")
        historical["generated_at"] = "2025-01-08T08:00:00+00:00"
        store.save_report(historical)
    # Simulate the immediately preceding schema: evidence and semantic index
    # already exist, but durable phase selection has not been introduced.
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE phase_heads")
        db.execute("PRAGMA user_version=2")
        before = {table: db.execute(f"SELECT * FROM {table}").fetchall()
                  for table in ("observations", "runs", "signals", "report_versions")}
    with Store(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 3
        for table, rows in before.items():
            assert [tuple(row) for row in store.db.execute(f"SELECT * FROM {table}")] == rows
        assert store.latest_phase_reports() == {phase: report for phase, report in reports.items()
                                                if phase != "manual"}
        assert store.latest_sources() == reports["manual"]["sources"]
        assert store.db.execute("SELECT count(*) FROM phase_heads").fetchone()[0] == 4
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_legacy_migration_retains_all_evidence_and_deduplicates_future_reruns(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    original = research_report()
    original.pop("phase")
    legacy_duplicate = deepcopy(original)
    legacy_duplicate["generated_at"] = "2025-01-06T09:00:00+00:00"
    reports = [original, legacy_duplicate]
    payloads = [canonical(report) for report in reports]
    run_ids = [hashlib.sha256(payload.encode()).hexdigest() for payload in payloads]
    stock = original["analysis"]["candidates"][0]
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE observations (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, market TEXT NOT NULL,
                symbol TEXT NOT NULL, date TEXT NOT NULL, fetched_at TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE runs (
                id TEXT PRIMARY KEY, as_of TEXT NOT NULL, generated_at TEXT NOT NULL,
                status TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE signals (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                date TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
                available_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(date,market,symbol)
            );
            PRAGMA user_version=1;
        """)
        db.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?)",
                   ("old-observation", "quotes", stock["market"], stock["symbol"], stock["date"],
                    stock["fetched_at"], canonical(stock)))
        for report, payload, run_id in zip(reports, payloads, run_ids):
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?)",
                       (run_id, report["as_of"], report["generated_at"], report["status"], payload))
        db.execute("INSERT INTO signals VALUES (?,?,?,?,?,?,?)",
                   ("old-signal", run_ids[0], stock["date"], stock["market"], stock["symbol"],
                    original["generated_at"], canonical(stock)))
        before = {table: db.execute(f"SELECT * FROM {table}").fetchall()
                  for table in ("observations", "runs", "signals")}
    with Store(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 3
        for table, rows in before.items():
            assert [tuple(row) for row in store.db.execute(f"SELECT * FROM {table}")] == rows
        assert store.save_report(legacy_duplicate) == run_ids[0]
        assert [store.get_report(run_id) for run_id in run_ids] == reports
        assert len(store.history()) == 2
        assert all(row["phase"] == "manual" for row in store.history())
        assert store.db.execute("SELECT count(*) FROM report_versions").fetchone()[0] == 1
        assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
    # Reopening must not rewrite the original migration mapping or legacy bytes.
    with Store(path) as reopened:
        assert reopened.save_report(original) == run_ids[0]
        assert reopened.get_report(run_ids[1]) == legacy_duplicate


def test_concurrent_store_connections_only_publish_one_semantic_report(tmp_path):
    path = tmp_path / "concurrent.sqlite3"
    workers = 6
    ready = Barrier(workers)

    def save(index):
        report = research_report()
        report["generated_at"] = f"2025-01-06T08:{index:02d}:00+00:00"
        report["sources"][0].update(fetched_at=report["generated_at"], raw_path=f"raw/{index}.json")
        with Store(path) as store:
            ready.wait(timeout=10)
            return store.save_report(report), report

    with ThreadPoolExecutor(max_workers=workers) as executor:
        published = list(executor.map(save, range(workers)))
    run_ids = {run_id for run_id, _ in published}
    assert len(run_ids) == 1
    with Store(path) as store:
        assert len(store.history()) == 1
        assert store.db.execute("SELECT count(*) FROM report_versions").fetchone()[0] == 1
        assert len(store.signals("2025-01-06")) == 1
        stored = store.get_report(run_ids.pop())
        assert stored in [report for _, report in published]
        assert store.signals("2025-01-06")[0]["available_at"] == stored["generated_at"]
        assert store.latest_phase_reports()["postclose"] == stored
        assert store.db.execute("SELECT selected_at FROM phase_heads").fetchone()[0] == (
            "2025-01-06T08:05:00+00:00"
        )


def test_report_and_forecast_write_are_atomic(tmp_path):
    report = research_report()
    invalid = deepcopy(report)
    del invalid["analysis"]["candidates"][0]["market"]
    with Store(tmp_path / "db.sqlite3") as store:
        with pytest.raises(KeyError):
            store.save_report(invalid)
        assert store.history() == []
        assert store.db.execute("SELECT count(*) FROM report_versions").fetchone()[0] == 0
        assert store.latest_phase_reports() == {}
        assert store.signals(report["as_of"]) == []
        run_id = store.save_report(report)
        assert json.loads(store.db.execute("SELECT payload FROM runs").fetchone()[0]) == report
        assert store.get_report(run_id) == report
