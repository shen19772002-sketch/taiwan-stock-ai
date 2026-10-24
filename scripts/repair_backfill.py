"""Retry only failed historical source-days from a saved backfill manifest.

The original, checksum-verified company snapshot is reused. Successful days and
empty holiday responses are not requested again. No forecasts are backdated.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

import requests

from taiwan_stock_ai.backfill import historical_url, normalize_history
from taiwan_stock_ai.providers import _error_text, fetch_response, parse_companies, parse_quotes, rows
from taiwan_stock_ai.storage import Store


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def repair(manifest: Path, data_dir: Path, *, session=None, progress=None) -> dict:
    manifest, data_dir = Path(manifest), Path(data_dir)
    content = manifest.read_bytes()
    original = json.loads(content)
    database = data_dir / "market.sqlite3"
    if not database.is_file():
        raise ValueError("Existing database is required for repair")
    failures = []
    seen = set()
    for source in original["sources"]:
        match = re.fullmatch(r"(twse|tpex)_history_(\d{4}-\d{2}-\d{2})", source["id"])
        if source["status"] != "error" or not match:
            continue
        market, day = match[1].upper(), date.fromisoformat(match[2])
        if day > datetime.now(ZoneInfo("Asia/Taipei")).date():
            raise ValueError("Cannot repair future history")
        if (market, day) not in seen:
            failures.append((market, day))
            seen.add((market, day))
    metadata = {source["id"]: source for source in original["sources"]}
    company_lists = {}
    for market in {market for market, _ in failures}:
        source_id = market.lower() + "_companies"
        raw = gzip.decompress((manifest.parent / f"{source_id}.json.gz").read_bytes())
        if hashlib.sha256(raw).hexdigest() != metadata[source_id]["sha256"]:
            raise ValueError("Company snapshot checksum mismatch")
        company_lists[market] = parse_companies(json.loads(raw))
        if not company_lists[market]:
            raise ValueError("No verified company universe")

    run_dir = data_dir / "raw" / ("repair-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    run_dir.mkdir(parents=True)
    report = {"source_manifest": str(manifest), "source_manifest_sha256": hashlib.sha256(content).hexdigest(),
              "purpose": "current_universe_indicator_warmup_repair", "sources": [], "inserted": 0}
    client = session or requests.Session()
    try:
        with Store(database) as store:
            for index, (market, day) in enumerate(failures):
                if index:
                    time.sleep(1)
                url = historical_url(market, day)
                source_id = market.lower() + "_history_" + day.isoformat()
                fetched_at = datetime.now(timezone.utc).isoformat()
                source = {"id": source_id, "url": url, "requested_date": day.isoformat(),
                          "data_date": None, "fetched_at": fetched_at, "status": "error",
                          "accepted_rows": 0, "rejected_rows": 0, "error": None}
                try:
                    response = fetch_response(client, url)
                    raw = response.content
                    archive = run_dir / f"{source_id}.json.gz"
                    temporary = archive.with_suffix(".tmp")
                    temporary.write_bytes(gzip.compress(raw, mtime=0))
                    temporary.replace(archive)
                    source.update(raw_path=str(archive), sha256=hashlib.sha256(raw).hexdigest(),
                                  http_status=response.status_code)
                    response.raise_for_status()
                    normalized = normalize_history(response.json(), market, day)
                    source.update(data_date=day.isoformat(), row_count=len(rows(normalized)))
                    parsed, rejected = parse_quotes(normalized, market, company_lists[market], url, fetched_at, day)
                    matching = [quote for quote in parsed if quote["date"] == day.isoformat()]
                    rejected += len(parsed) - len(matching)
                    for quote in matching:
                        quote["history_purpose"] = "current_universe_indicator_warmup"
                        quote["universe_date"] = company_lists[market][quote["symbol"]]["source_date"]
                    source.update(accepted_rows=len(matching), rejected_rows=rejected,
                                  status="ok" if matching and not rejected else "waiting")
                    if rejected:
                        source["error"] = f"{rejected} current-universe rows lack valid complete OHLC"
                    elif not matching:
                        source["error"] = "No complete verified bars for requested date"
                except (requests.RequestException, ValueError, TypeError) as exc:
                    source["error"] = _error_text(exc)
                    matching = []
                if matching:
                    report["inserted"] += store.ingest("quotes", matching, day.isoformat())
                report["sources"].append(source)
                _write_json(run_dir / "manifest.json", report)
                if progress:
                    progress(source)
            report["integrity"] = store.check()
    finally:
        _write_json(run_dir / "manifest.json", report)
        if session is None:
            client.close()
    report["manifest"] = str(run_dir / "manifest.json")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    report = repair(args.manifest, args.data_dir, progress=lambda source: print(json.dumps(
        {key: source.get(key) for key in ("id", "status", "accepted_rows", "error")}, ensure_ascii=False), flush=True))
    print(json.dumps({key: report[key] for key in ("inserted", "integrity", "manifest")}, ensure_ascii=False))
    return 0 if all(source["accepted_rows"] for source in report["sources"]) and report["integrity"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
