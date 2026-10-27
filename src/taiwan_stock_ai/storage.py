"""Append-only observations and forecasts behind a replaceable SQLite boundary."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def report_fingerprint(report: dict) -> str:
    """Identify research content independently of when the same inputs were fetched.

    Raw response bytes and acquisition timestamps remain in the immutable first
    report and in the raw archive. They are not new research versions. Values,
    model rules, data dates, availability/quality statuses and errors still are.
    """
    def content(value):
        if isinstance(value, dict):
            return {key: content(item) for key, item in value.items()
                    if key not in {"fetched_at", "raw_path", "sha256"}}
        if isinstance(value, list):
            return [content(item) for item in value]
        return value

    meaningful = {key: value for key, value in report.items()
                  if key not in {"generated_at", "history"}}
    meaningful.setdefault("phase", "manual")
    return hashlib.sha256(("report-content-v1:" + canonical(content(meaningful))).encode()).hexdigest()


class Store:
    """Raw revisions are immutable; queries choose the latest known revision."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, market TEXT NOT NULL,
                symbol TEXT NOT NULL, date TEXT NOT NULL, fetched_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS obs_lookup ON observations(kind,date,market,symbol,fetched_at);
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, as_of TEXT NOT NULL, generated_at TEXT NOT NULL,
                status TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS signals (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                date TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL,
                available_at TEXT NOT NULL, payload TEXT NOT NULL,
                UNIQUE(date,market,symbol)
            );
            CREATE TABLE IF NOT EXISTS report_versions (
                as_of TEXT NOT NULL, phase TEXT NOT NULL, fingerprint TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(id),
                PRIMARY KEY(as_of,phase,fingerprint)
            );
            CREATE TABLE IF NOT EXISTS phase_heads (
                phase TEXT PRIMARY KEY, as_of TEXT NOT NULL, selected_at TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(id)
            );
        """)
        self._migrate_report_versions()

    def _migrate_report_versions(self) -> None:
        """Index old immutable reports once, retaining every old row and signal."""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version >= 3:
                return
            if version < 2:
                # The first known report wins for duplicate legacy content. Later
                # legacy copies remain readable; migration never rewrites evidence.
                for row in self.db.execute("SELECT id,payload FROM runs ORDER BY generated_at,rowid"):
                    report = json.loads(row["payload"])
                    self.db.execute("INSERT OR IGNORE INTO report_versions VALUES (?,?,?,?)",
                                    (report["as_of"], report.get("phase", "manual"),
                                     report_fingerprint(report), row["id"]))
            # Before v3 no selection pointer existed. Seed the latest report per
            # phase, with trading date taking precedence over a later backfill.
            for row in self.db.execute("SELECT id,payload FROM runs ORDER BY as_of,generated_at,rowid"):
                self._select_phase_report(json.loads(row["payload"]), row["id"])
            self.db.execute("PRAGMA user_version=3")

    def _select_phase_report(self, report: dict, run_id: str) -> None:
        """Advance the display pointer using the invocation, not first publication."""
        phase = report.get("phase", "manual")
        self.db.execute("""
            INSERT INTO phase_heads (phase,as_of,selected_at,run_id) VALUES (?,?,?,?)
            ON CONFLICT(phase) DO UPDATE SET
                as_of=excluded.as_of, selected_at=excluded.selected_at, run_id=excluded.run_id
            WHERE excluded.as_of > phase_heads.as_of
               OR (excluded.as_of = phase_heads.as_of AND excluded.selected_at >= phase_heads.selected_at)
        """, (phase, report["as_of"], report["generated_at"], run_id))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    def ingest(self, kind: str, records: list[dict], as_of: str) -> int:
        if kind not in {"quotes", "fundamentals", "institutions"}:
            raise ValueError("Unknown observation kind")
        before = self.db.total_changes
        with self.db:
            for record in records:
                if not record.get("date") or record["date"] > as_of:
                    continue
                payload = canonical(record)
                digest = hashlib.sha256((kind + payload).encode()).hexdigest()
                self.db.execute(
                    "INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?)",
                    (digest, kind, record["market"], record["symbol"], record["date"],
                     record["fetched_at"], payload),
                )
        return self.db.total_changes - before

    def records(self, kind: str, as_of: str, known_at: str | None = None) -> list[dict]:
        rows = self.db.execute("""
            SELECT payload FROM (
                SELECT payload,date,market,symbol,
                       ROW_NUMBER() OVER (
                         PARTITION BY market,symbol,date ORDER BY fetched_at DESC,rowid DESC
                       ) AS revision
                FROM observations WHERE kind=? AND date<=? AND (? IS NULL OR fetched_at<=?)
            ) WHERE revision=1 ORDER BY date,market,symbol
        """, (kind, as_of, known_at, known_at))
        return [json.loads(row["payload"]) for row in rows]

    def save_report(self, report: dict, record_signals: bool = True) -> str:
        payload = canonical(report)
        run_id = hashlib.sha256(payload.encode()).hexdigest()
        key = (report["as_of"], report.get("phase", "manual"), report_fingerprint(report))
        with self.db:
            # Serialize the lookup and insert across processes. The unique key
            # also protects this invariant from other writers of this schema.
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute(
                "SELECT run_id FROM report_versions WHERE as_of=? AND phase=? AND fingerprint=?", key,
            ).fetchone()
            if existing:
                # Do not attach forecasts retroactively to an earlier display-
                # only report. A new report and its signals commit atomically.
                # An A -> B -> A recovery selects the original A without adding
                # duplicate evidence or pretending it was first published now.
                self._select_phase_report(report, existing["run_id"])
                return existing["run_id"]
            self.db.execute("INSERT OR IGNORE INTO runs VALUES (?,?,?,?,?)",
                            (run_id, report["as_of"], report["generated_at"], report["status"], payload))
            self.db.execute("INSERT INTO report_versions VALUES (?,?,?,?)", (*key, run_id))
            if record_signals:
                for stock in report["analysis"]["candidates"]:
                    if stock["date"] != report["as_of"]:
                        continue  # Never backdate a newly published historical recommendation.
                    key = f'{stock["date"]}:{stock["market"]}:{stock["symbol"]}'
                    self.db.execute("INSERT OR IGNORE INTO signals VALUES (?,?,?,?,?,?,?)",
                                    (key, run_id, stock["date"], stock["market"], stock["symbol"],
                                     report["generated_at"], canonical(stock)))
            self._select_phase_report(report, run_id)
        return run_id

    def get_report(self, run_id: str) -> dict | None:
        row = self.db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def latest_phase_reports(self) -> dict[str, dict]:
        """Return independently retained phase reports without rewriting payloads."""
        rows = self.db.execute("""
            SELECT phase_heads.phase,runs.payload FROM phase_heads
            JOIN runs ON runs.id=phase_heads.run_id
            WHERE phase_heads.phase IN ('preopen','postclose','final')
        """)
        return {row["phase"]: json.loads(row["payload"]) for row in rows}

    def phase_selection_times(self) -> dict[str, str]:
        """Expose current selection times separately from immutable publication times."""
        rows = self.db.execute("SELECT phase,selected_at FROM phase_heads")
        return {row["phase"]: row["selected_at"] for row in rows}

    def signals(self, as_of: str) -> list[dict]:
        rows = self.db.execute("SELECT * FROM signals WHERE date<=? ORDER BY date,market,symbol", (as_of,))
        return [{**json.loads(r["payload"]), "signal_date": r["date"], "available_at": r["available_at"]}
                for r in rows]

    def history(self, limit: int = 30) -> list[dict]:
        rows = self.db.execute("SELECT id,payload FROM runs ORDER BY generated_at DESC LIMIT ?", (limit,))
        results = []
        for row in rows:
            report = json.loads(row["payload"])
            results.append({"id": row["id"], "as_of": report["as_of"],
                            "generated_at": report["generated_at"], "status": report["status"],
                            "phase": report.get("phase", "manual"),
                            "candidate_count": len(report["analysis"]["candidates"])})
        return results

    def latest_sources(self) -> list[dict]:
        for row in self.db.execute("""
            SELECT runs.payload FROM phase_heads JOIN runs ON runs.id=phase_heads.run_id
            ORDER BY phase_heads.as_of DESC,phase_heads.selected_at DESC
        """):
            sources = json.loads(row["payload"]).get("sources", [])
            if sources:
                return sources
        # Legacy reports without a usable source-bearing head remain available.
        for row in self.db.execute("SELECT payload FROM runs ORDER BY generated_at DESC"):
            sources = json.loads(row["payload"]).get("sources", [])
            if sources:
                return sources
        return []

    def export_history(self, directory: Path, limit: int = 30) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for row in self.db.execute("SELECT id,payload FROM runs ORDER BY generated_at DESC LIMIT ?", (limit,)):
            (directory / f'{row["id"]}.json').write_text(row["payload"], encoding="utf-8")

    def check(self) -> bool:
        return self.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def backup(self, destination: str | Path) -> None:
        destination = Path(destination)
        if destination.resolve() == self.path.resolve():
            raise ValueError("Backup destination must differ from database")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(destination) as target:
            self.db.backup(target)
