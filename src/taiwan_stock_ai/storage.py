"""Append-only observations and forecasts behind a replaceable SQLite boundary."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


class Store:
    """Raw revisions are immutable; queries choose the latest known revision."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
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
            PRAGMA user_version=1;
        """)

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
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO runs VALUES (?,?,?,?,?)",
                            (run_id, report["as_of"], report["generated_at"], report["status"], payload))
            if record_signals:
                for stock in report["analysis"]["candidates"]:
                    if stock["date"] != report["as_of"]:
                        continue  # Never backdate a newly published historical recommendation.
                    key = f'{stock["date"]}:{stock["market"]}:{stock["symbol"]}'
                    self.db.execute("INSERT OR IGNORE INTO signals VALUES (?,?,?,?,?,?,?)",
                                    (key, run_id, stock["date"], stock["market"], stock["symbol"],
                                     report["generated_at"], canonical(stock)))
        return run_id

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
                            "candidate_count": len(report["analysis"]["candidates"])})
        return results

    def latest_sources(self) -> list[dict]:
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
