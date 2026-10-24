"""Official dated history for warming up today's company universe.

Historical observations are acquired now, never treated as forecasts published
in the past. Current company membership introduces survivorship bias and is not
a complete historical universe. Every response must match its requested date.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

from .providers import ENDPOINTS, _error_text, fetch_response, parse_companies, parse_date, parse_quotes, rows


HISTORICAL_ENDPOINTS = {
    "TWSE": "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX",
    "TPEX": "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes",
}


def historical_url(market: str, day: date) -> str:
    if market == "TWSE":
        params = {"response": "json", "type": "ALLBUT0999", "date": day.strftime("%Y%m%d")}
    elif market == "TPEX":
        # The empty id parameter is part of the tested all-securities request.
        params = {"date": day.strftime("%Y/%m/%d"), "id": "", "response": "json"}
    else:
        raise ValueError("Unsupported market")
    return HISTORICAL_ENDPOINTS[market] + "?" + urlencode(params)


def normalize_history(payload: dict, market: str, day: date) -> dict:
    """Verify response-level dates before mapping a known official table."""
    if not isinstance(payload, dict) or parse_date(payload.get("date")) != day.isoformat():
        raise ValueError("Historical response date does not match requested date")
    if market == "TWSE":
        # providers.rows selects the official table containing 證券代號.
        rows(payload)
        return payload
    for table in payload.get("tables", []):
        if not isinstance(table, dict):
            continue
        fields = table.get("fields", [])
        if "代號" not in fields or "成交金額(元)" not in fields:
            continue
        if table.get("date") and parse_date(table["date"]) != day.isoformat():
            raise ValueError("Historical table date does not match requested date")
        normalized = {"date": payload["date"], "fields": [
            "成交金額" if field == "成交金額(元)" else field for field in fields
        ], "data": table.get("data")}
        rows(normalized)
        return normalized
    raise ValueError("Unsupported TPEx historical response schema")


def backfill(start: date, end: date, raw_dir: Path, delay: float = 1.0,
             *, session: requests.Session | None = None,
             progress: Callable[[dict], None] | None = None,
             on_batch: Callable[[list[dict]], None] | None = None) -> dict:
    """Collect complete dated bars, storing gzip originals and a run manifest.

    No more than one request per second is made, including metadata requests.
    Three consecutive network/schema failures pause that market for this run.
    Holidays or unpublished dates remain waiting; a response for a different
    trading date is never accepted. This does not backfill fundamental records.
    """
    if start > end:
        raise ValueError("start must be on or before end")
    if (end - start).days > 366:
        raise ValueError("One backfill run is limited to 367 calendar dates")
    if end > datetime.now(ZoneInfo("Asia/Taipei")).date():
        raise ValueError("Cannot backfill a future calendar date")
    if not math.isfinite(delay) or delay < 1:
        raise ValueError("Official-source request interval must be at least one second")
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    run_dir = raw_dir / ("backfill-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    run_dir.mkdir()
    client = session or requests.Session()
    sources, quotes = [], []
    last_request = None

    def request(source_id: str, url: str, requested_date: str | None = None):
        nonlocal last_request
        if last_request is not None:
            time.sleep(max(0, delay - (time.monotonic() - last_request)))
        last_request = time.monotonic()
        fetched_at = datetime.now(timezone.utc).isoformat()
        source = {"id": source_id, "url": url, "status": "error", "fetched_at": fetched_at,
                  "requested_date": requested_date, "data_date": None, "row_count": 0,
                  "accepted_rows": 0, "rejected_rows": 0, "error": None}
        payload = None
        try:
            response = fetch_response(client, url)
            original = response.content
            archive = run_dir / f"{source_id}.json.gz"
            # Deterministic gzip; SHA-256 refers to the uncompressed exact bytes.
            archive.write_bytes(gzip.compress(original, mtime=0))
            source.update(raw_path=str(archive), sha256=hashlib.sha256(original).hexdigest(),
                          http_status=response.status_code)
            response.raise_for_status()
            payload = response.json()
            source["status"] = "ok"
            if isinstance(payload, dict):
                source["data_date"] = parse_date(payload.get("date"))
        except (requests.RequestException, ValueError, TypeError) as exc:
            source["error"] = _error_text(exc)
        finally:
            last_request = time.monotonic()
        sources.append(source)
        return payload, source

    try:
        company_lists = {}
        for prefix, market in (("twse", "TWSE"), ("tpex", "TPEX")):
            payload, source = request(prefix + "_companies", ENDPOINTS[prefix + "_companies"])
            try:
                companies = parse_companies(payload) if payload is not None else {}
                source["row_count"] = len(companies)
                dates = [v["source_date"] for v in companies.values() if v.get("source_date")]
                source["data_date"] = max(dates) if dates else None
            except (ValueError, TypeError):
                companies = {}
                source.update(status="error", error="Unsupported company response schema")
            if not companies:
                source.update(status="error", error=source["error"] or "No verified current company universe")
            company_lists[market] = companies

        failures = {"TWSE": 0, "TPEX": 0}
        day = start
        while day <= end:
            if day.weekday() < 5:
                for market in ("TWSE", "TPEX"):
                    if not company_lists[market] or failures[market] >= 3:
                        continue
                    source_id = market.lower() + "_history_" + day.isoformat()
                    payload, source = request(source_id, historical_url(market, day), day.isoformat())
                    matching = []
                    if payload is None:
                        failures[market] += 1
                    else:
                        try:
                            normalized = normalize_history(payload, market, day)
                            source["row_count"] = len(rows(normalized))
                            parsed, rejected = parse_quotes(normalized, market, company_lists[market],
                                                            source["url"], source["fetched_at"], day)
                            matching = [q for q in parsed if q["date"] == day.isoformat()]
                            rejected += len(parsed) - len(matching)
                            for quote in matching:
                                quote["history_purpose"] = "current_universe_indicator_warmup"
                                quote["universe_date"] = company_lists[market][quote["symbol"]]["source_date"]
                            quotes.extend(matching)
                            source.update(accepted_rows=len(matching), rejected_rows=rejected,
                                          status="ok" if matching and not rejected else "waiting")
                            if rejected:
                                source["error"] = f"{rejected} current-universe rows lack valid complete OHLC"
                            elif not matching:
                                source["error"] = "No complete current-universe bars for requested date"
                            failures[market] = 0
                        except (ValueError, TypeError):
                            source.update(status="waiting", error="No verified date-matching historical table")
                            # Holidays can legitimately have no date/table. They
                            # are not evidence of exchange trading or an outage.
                            if source["data_date"]:
                                failures[market] += 1
                            else:
                                failures[market] = 0
                    if matching and on_batch:
                        on_batch(matching)
                    if progress:
                        progress(dict(source))
                # Preserve completed work's provenance even if interrupted.
                (run_dir / "manifest.json").write_text(json.dumps({
                    "start": start.isoformat(), "end": end.isoformat(),
                    "purpose": "current_universe_indicator_warmup", "sources": sources,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
            day += timedelta(days=1)
        result = {"quotes": quotes, "sources": sources,
                  "limitations": ["只回補現存公司清單的價格歷史，非無存活者偏差的全市場回測資料。",
                                   "原始價格尚未還原除權息；未回填估值或建立過去的預測訊號。"],
                  "paused_markets": [market for market, count in failures.items() if count >= 3]}
        return result
    finally:
        (run_dir / "manifest.json").write_text(json.dumps({
            "start": start.isoformat(), "end": end.isoformat(),
            "purpose": "current_universe_indicator_warmup", "sources": sources,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        if session is None:
            client.close()
