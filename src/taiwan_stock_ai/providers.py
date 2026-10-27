"""Official end-of-day sources. Missing dates/data fail closed; never simulate prices.

Each response is retained byte-for-byte with a digest. API compatibility must be
verified in the deployment environment; blocked or changed schemas are surfaced.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests


ENDPOINTS = {
    "twse_companies": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    "tpex_companies": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
    "twse_quotes": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "twse_dated_quotes": "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=ALLBUT0999",
    "tpex_quotes": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
    "twse_valuations": "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL",
    "twse_dated_valuations": "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?response=json&selectType=ALL",
    "tpex_valuations": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis",
    "twse_institutions": "https://www.twse.com.tw/rwd/zh/fund/T86?response=json&selectType=ALLBUT0999",
    "tpex_institutions": "https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading",
    "twse_calendar": "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule",
    "twse_market_notices": "https://www.twse.com.tw/rwd/zh/news/newsList?response=json",
    "tpex_market_notices": "https://www.tpex.org.tw/data/home/news_zh.json",
}

CALENDAR_SOURCE_IDS = ("twse_calendar", "twse_market_notices", "tpex_market_notices")

INDUSTRIES = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業",
    "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "14": "建材營造",
    "15": "航運業", "16": "觀光餐旅", "17": "金融保險", "18": "貿易百貨",
    "19": "綜合", "20": "其他", "21": "化學工業", "22": "生技醫療",
    "23": "油電燃氣", "24": "半導體", "25": "電腦及週邊設備", "26": "光電",
    "27": "通信網路", "28": "電子零組件", "29": "電子通路", "30": "資訊服務",
    "31": "其他電子", "32": "文化創意", "33": "農業科技", "34": "電子商務",
    "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活",
}


def _first(row: dict, *names: str) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def number(value: Any) -> float | None:
    """Parse official numeric strings, preserving unknown as None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", "").replace("%", "").strip())
        return parsed if math.isfinite(parsed) else None
    except (ValueError, TypeError):
        return None


def parse_date(value: Any) -> str | None:
    """Accept explicit Gregorian or ROC calendar dates, never infer today's date."""
    if value is None:
        return None
    text = str(value).strip()
    text = text.replace("年", "/").replace("月", "/").replace("日", "")
    parts = re.fullmatch(r"(\d{2,4})[/-](\d{1,2})[/-](\d{1,2})", text)
    if parts:
        year, month, day = map(int, parts.groups())
    elif re.fullmatch(r"\d{7,8}", text):
        year, month, day = int(text[:-4]), int(text[-4:-2]), int(text[-2:])
    else:
        return None
    if year < 1911:
        year += 1911
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def rows(payload: Any) -> list[dict]:
    if isinstance(payload, list) and all(isinstance(x, dict) for x in payload):
        return payload
    if isinstance(payload, dict):
        tables = payload.get("tables")
        if isinstance(tables, list):
            for table in tables:
                if isinstance(table, dict) and "證券代號" in table.get("fields", []):
                    return rows(table)
        if isinstance(payload.get("fields9"), list) and isinstance(payload.get("data9"), list):
            return rows({"fields": payload["fields9"], "data": payload["data9"]})
        data = payload.get("data")
        if isinstance(data, list) and all(isinstance(x, dict) for x in data):
            return data
        fields = payload.get("fields")
        if isinstance(fields, list) and isinstance(data, list):
            return [dict(zip(fields, x)) for x in data if isinstance(x, list) and len(x) == len(fields)]
    raise ValueError("Unsupported official response schema")


def parse_companies(payload: Any) -> dict[str, dict]:
    companies = {}
    for row in rows(payload):
        symbol = str(_first(row, "公司代號", "公司代碼", "SecuritiesCompanyCode", "Code") or "").strip()
        # Common shares only. Membership in the official operating-company list
        # excludes ETFs, warrants, bonds and other non-company securities.
        if not re.fullmatch(r"[1-9]\d{3}", symbol):
            continue
        name = _first(row, "公司簡稱", "公司名稱", "CompanyAbbreviation", "CompanyName", "Name")
        industry = str(_first(row, "產業別", "SecuritiesIndustryCode", "IndustryCode", "Industry") or "").strip()
        companies[symbol] = {
            "name": str(name or symbol).strip(),
            "industry": INDUSTRIES.get(industry.zfill(2), industry or None),
            "source_date": parse_date(_first(row, "出表日期", "Date", "資料日期")),
        }
    return companies


def parse_quotes(payload: Any, market: str, companies: dict, source: str,
                 fetched_at: str, as_of: date) -> tuple[list[dict], int]:
    parsed, rejected = [], 0
    envelope_date = parse_date(payload.get("date")) if isinstance(payload, dict) else None
    for row in rows(payload):
        symbol = str(_first(row, "Code", "SecuritiesCompanyCode", "證券代號", "代號") or "").strip()
        if symbol not in companies:
            continue
        trading_date = parse_date(_first(row, "Date", "日期", "資料日期")) or envelope_date
        values = {
            "open": number(_first(row, "OpeningPrice", "Open", "開盤價", "開盤")),
            "high": number(_first(row, "HighestPrice", "High", "最高價", "最高")),
            "low": number(_first(row, "LowestPrice", "Low", "最低價", "最低")),
            "close": number(_first(row, "ClosingPrice", "Close", "收盤價", "收盤")),
            "volume": number(_first(row, "TradeVolume", "TradingShares", "成交股數")),
            "turnover": number(_first(row, "TradeValue", "TransactionAmount", "成交金額")),
        }
        if not trading_date or trading_date > as_of.isoformat() or any(v is None for v in values.values()):
            rejected += 1
            continue
        if (min(values[k] for k in ("open", "high", "low", "close")) <= 0
                or values["low"] > min(values["open"], values["close"])
                or values["high"] < max(values["open"], values["close"])
                or values["volume"] < 0 or not values["volume"].is_integer()
                or values["turnover"] < 0):
            rejected += 1
            continue
        parsed.append({
            "market": market, "symbol": symbol,
            "name": str(_first(row, "Name", "CompanyName", "證券名稱", "名稱") or companies[symbol]["name"]),
            "industry": companies[symbol]["industry"], "date": trading_date,
            **values, "volume": int(values["volume"]), "source": source,
            "fetched_at": fetched_at, "complete": True,
        })
    return parsed, rejected


def parse_fundamentals(payload: Any, market: str, companies: dict, source: str,
                       fetched_at: str, as_of: date) -> tuple[list[dict], int]:
    parsed, rejected = [], 0
    envelope_date = parse_date(payload.get("date")) if isinstance(payload, dict) else None
    for row in rows(payload):
        symbol = str(_first(row, "Code", "SecuritiesCompanyCode", "證券代號", "股票代號") or "").strip()
        if symbol not in companies:
            continue
        data_date = parse_date(_first(row, "Date", "日期", "資料日期")) or envelope_date
        if not data_date or data_date > as_of.isoformat():
            rejected += 1
            continue
        pe = number(_first(row, "PEratio", "PriceEarningRatio", "PERatio", "本益比"))
        pb = number(_first(row, "PBratio", "PriceBookRatio", "PBRatio", "股價淨值比"))
        dividend_yield = number(_first(row, "DividendYield", "YieldRatio", "Yield", "殖利率(%)", "殖利率"))
        parsed.append({"market": market, "symbol": symbol, "date": data_date,
                       "pe": pe, "pb": pb, "dividend_yield": dividend_yield,
                       "source": source, "fetched_at": fetched_at,
                       "complete": all(x is not None for x in (pe, pb, dividend_yield))})
    return parsed, rejected


def parse_calendar(payload: Any, as_of: date, source: str) -> dict:
    """Only explicit dated holiday rows count; ambiguous annual text stays unknown."""
    holiday_dates, trading_dates = [], []
    for row in rows(payload):
        day = parse_date(_first(row, "Date", "日期"))
        name = str(_first(row, "Name", "名稱", "Holiday", "Description") or "")
        description = str(_first(row, "Description", "說明") or "")
        if day and any(text in name for text in ("開始交易日", "最後交易日")):
            trading_dates.append(day)
        elif day and any(text in name + description for text in ("休市", "無交易", "放假", "補假", "紀念日", "節")):
            holiday_dates.append(day)
    if as_of.isoformat() in holiday_dates:
        return {"status": "verified", "trading_day": False, "source": source,
                "date": as_of.isoformat(), "holiday_dates": holiday_dates,
                "reason": "官方休市日資料"}
    if as_of.isoformat() in trading_dates:
        return {"status": "verified", "trading_day": True, "source": source,
                "date": as_of.isoformat(), "holiday_dates": holiday_dates,
                "reason": "官方行事曆明列開始／最後交易日"}
    # A partial holiday list is insufficient to certify an arbitrary weekday.
    return {"status": "unknown", "trading_day": None, "source": source,
            "date": as_of.isoformat(), "holiday_dates": holiday_dates,
            "reason": "無法由已取得資料確認完整交易日曆"}


def parse_institutions(payload: Any, market: str, companies: dict, source: str,
                       fetched_at: str, as_of: date) -> tuple[list[dict], int]:
    """Official institutional net purchases in shares, never turnover proxies."""
    parsed, rejected = [], 0
    envelope_date = parse_date(payload.get("date")) if isinstance(payload, dict) else None
    for row in rows(payload):
        symbol = str(_first(row, "SecuritiesCompanyCode", "證券代號") or "").strip()
        if symbol not in companies:
            continue
        day = parse_date(_first(row, "Date", "日期")) or envelope_date
        values = {
            "foreign_net": number(_first(row, "外陸資買賣超股數(不含外資自營商)",
                "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference")),
            "investment_trust_net": number(_first(row, "投信買賣超股數", "SecuritiesInvestmentTrustCompanies-Difference")),
            "dealer_net": number(_first(row, "自營商買賣超股數", "Dealers-Difference")),
            "net_buy": number(_first(row, "三大法人買賣超股數", "TotalDifference")),
        }
        if (not day or day > as_of.isoformat()
                or any(value is None or not value.is_integer() for value in values.values())):
            rejected += 1
            continue
        parsed.append({"market": market, "symbol": symbol, "date": day,
                       **{key: int(value) for key, value in values.items()},
                       "unit": "shares", "source": source, "fetched_at": fetched_at,
                       "complete": True})
    return parsed, rejected


def _notice_rows(payload: Any, source_id: str) -> list[dict]:
    """Read only known official schemas; article URLs are never followed."""
    if not isinstance(payload, dict):
        raise ValueError("Unsupported market notice schema")
    if source_id == "twse_market_notices":
        items = payload.get("data")
        if not isinstance(items, list):
            raise ValueError("Unsupported TWSE notice schema")
        return [{"Date": item[2], "title": str(item[1]), "id": str(item[3])}
                for item in items if isinstance(item, list) and len(item) >= 4]
    items = payload.get("release")
    if not isinstance(items, list):
        raise ValueError("Unsupported TPEx notice schema")
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", ""))
        prefix = re.match(r"^(\d{2,4}/\d{1,2}/\d{1,2})\s+(.+)$", title)
        if prefix:
            result.append({"Date": prefix[1], "title": prefix[2], "id": str(item.get("id", ""))})
    return result


def parse_market_calendar(responses: dict, as_of: date) -> dict:
    """Combine annual holidays with dated, full-market emergency announcements.

    An isolated company suspension, partial-day closure, cancelled event or an
    announcement from just one exchange cannot establish a national holiday.
    """
    calendar = parse_calendar(responses.get("twse_calendar", []), as_of, ENDPOINTS["twse_calendar"])
    if as_of.weekday() >= 5:
        calendar.update(status="verified", trading_day=False, reason="台股例行週末休市")
        return calendar
    if calendar["trading_day"] is False:
        return calendar
    closures = {}
    for source_id, market in (("twse_market_notices", "TWSE"), ("tpex_market_notices", "TPEX")):
        if source_id not in responses:
            continue
        for item in _notice_rows(responses[source_id], source_id):
            published = parse_date(item["Date"])
            title = item["title"]
            if not published or published > as_of.isoformat():
                continue
            if any(word in title for word in ("不休市", "取消休市", "照常交易", "下午", "上午", "半日")):
                continue
            scope = ("集中交易市場" in title if market == "TWSE"
                     else "櫃檯買賣有價證券市場" in title or "櫃檯買賣市場" in title)
            if not scope or not re.search(r"休市(?:一日|一天)|全日休市", title):
                continue
            explicit = re.search(r"(\d{2,4}年\d{1,2}月\d{1,2}日)", title)
            if explicit:
                closed_day = parse_date(explicit[1])
            else:
                short = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日", title)
                closed_day = parse_date(f"{published[:4]}/{short[1]}/{short[2]}") if short else None
            # A short date is anchored only to a nearby, explicitly dated notice.
            if not closed_day or not 0 <= (date.fromisoformat(closed_day) - date.fromisoformat(published)).days <= 7:
                continue
            if closed_day == as_of.isoformat():
                closures[market] = {"source": ENDPOINTS[source_id], "published_date": published,
                                    "date": closed_day, "title": title, "article_id": item["id"]}
    if len(closures) == 2:
        calendar.update(status="verified", trading_day=False, market_closures=closures,
                        source=ENDPOINTS["twse_market_notices"],
                        reason="證交所及櫃買中心官方公告當日全市場臨時休市")
    elif closures:
        calendar.update(status="unknown", trading_day=None, market_closures=closures,
                        reason="僅取得部分市場休市公告，尚無法確認全市場交易日")
    return calendar


def _error_text(exc: Exception) -> str:
    # Exception strings can include proxy credentials, so return bounded metadata.
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, requests.exceptions.ProxyError):
        return "ProxyError: HTTP 403" if "403" in str(exc) else "ProxyError"
    if isinstance(exc, requests.exceptions.SSLError):
        return "TLS verification failed"
    return type(exc).__name__


def fetch_response(client, url: str, attempts: int = 2):
    """Retry a transient transport failure once, preserving TLS and access policy."""
    for attempt in range(attempts):
        try:
            response = client.get(url, timeout=(5, 20), headers={
                "User-Agent": "taiwan-stock-ai/1.0 (public market research)"})
            if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
                time.sleep(1)
                continue
            return response
        except (requests.exceptions.SSLError, requests.exceptions.ProxyError):
            raise  # Repeating an access/trust failure cannot fix its cause.
        except (requests.Timeout, requests.ConnectionError, requests.exceptions.ChunkedEncodingError):
            if attempt + 1 >= attempts:
                raise
            time.sleep(1)
    raise RuntimeError("No fetch attempt configured")


def _run_directory(raw_dir: Path) -> Path:
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    run_dir = raw_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir.mkdir()
    return run_dir


def _download_sources(client, source_ids, run_dir: Path, fetched_at: str, attempts: int) -> tuple[dict, list]:
    responses, sources = {}, []
    for source_id in source_ids:
        url = ENDPOINTS[source_id]
        source = {"id": source_id, "url": url, "status": "error", "fetched_at": fetched_at,
                  "data_date": None, "row_count": 0, "error": None}
        try:
            response = fetch_response(client, url, attempts=attempts)
            raw = response.content
            raw_file = run_dir / f"{source_id}.json"
            raw_file.write_bytes(raw)
            source.update(raw_path=str(raw_file), sha256=hashlib.sha256(raw).hexdigest(), http_status=response.status_code)
            response.raise_for_status()
            payload = response.json()
            items = (_notice_rows(payload, source_id) if source_id.endswith("_market_notices")
                     else rows(payload))
            responses[source_id] = payload
            dates = [parse_date(_first(r, "Date", "日期", "資料日期", "出表日期")) for r in items]
            dates = [d for d in dates if d]
            if isinstance(payload, dict) and parse_date(payload.get("date")):
                dates.append(parse_date(payload["date"]))
            source.update(status="ok" if items else "waiting", row_count=len(items),
                          data_date=max(dates) if dates else None)
        except (requests.RequestException, ValueError, TypeError) as exc:
            source["error"] = _error_text(exc)
        sources.append(source)
    return responses, sources


def collect_calendar(as_of: date, raw_dir: Path, session: requests.Session | None = None) -> dict:
    """Small official-calendar preflight before the expensive market collection."""
    run_dir = _run_directory(raw_dir)
    fetched_at = datetime.now(timezone.utc).isoformat()
    client = session or requests.Session()
    try:
        responses, sources = _download_sources(client, CALENDAR_SOURCE_IDS, run_dir,
                                               fetched_at, 1 if session is not None else 2)
        result = {"calendar": parse_market_calendar(responses, as_of), "sources": sources}
        (run_dir / "manifest.json").write_text(json.dumps({"as_of": as_of.isoformat(), **result},
            ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    finally:
        if session is None:
            client.close()


def collect(as_of: date, raw_dir: Path, session: requests.Session | None = None,
            calendar_result: dict | None = None) -> dict:
    """Download latest official datasets; retain their true observation dates.

    This is not a historical backfill API. as_of prevents future observations
    entering a report; it never relabels latest data as historical data.
    Calendar preflight evidence can be reused without downloading it twice.
    """
    run_dir = _run_directory(raw_dir)
    fetched_at = datetime.now(timezone.utc).isoformat()
    client = session or requests.Session()
    source_ids = [source_id for source_id in ENDPOINTS
                  if calendar_result is None or source_id not in CALENDAR_SOURCE_IDS]
    responses, sources = _download_sources(client, source_ids, run_dir, fetched_at,
                                          1 if session is not None else 2)
    if calendar_result is not None:
        sources.extend(dict(source) for source in calendar_result.get("sources", []))

    source_map = {s["id"]: s for s in sources}
    quotes, fundamentals, institutions = [], [], []
    for prefix, market in (("twse", "TWSE"), ("tpex", "TPEX")):
        companies = parse_companies(responses.get(f"{prefix}_companies", []))
        for kind, parser, target in (("quotes", parse_quotes, quotes), ("valuations", parse_fundamentals, fundamentals),
                                     ("institutions", parse_institutions, institutions)):
            candidates = [f"{prefix}_{kind}"]
            if prefix == "twse" and kind in {"quotes", "valuations"}:
                candidates.append(f"twse_dated_{kind}")
            selected, selected_date = [], ""
            for source_id in candidates:
                if source_id not in responses:
                    continue
                parsed, rejected = parser(responses[source_id], market, companies, ENDPOINTS[source_id], fetched_at, as_of)
                info = source_map[source_id]
                info.update(accepted_rows=len(parsed), rejected_rows=rejected)
                if parsed:
                    info["data_date"] = max(row["date"] for row in parsed)
                if not companies:
                    info.update(status="waiting", error="缺少官方公司名單，無法驗證普通股範圍")
                elif not parsed:
                    info.update(status="waiting", error="無可驗證日期及必要欄位的資料")
                elif rejected:
                    info.update(status="waiting", error=f"{rejected} 筆資料日期或必要欄位不完整")
                if parsed and info["data_date"] > selected_date:
                    selected, selected_date = parsed, info["data_date"]
            # OpenAPI can lag its dated official counterpart. Pick the freshest
            # verified source as a whole; ties retain the preferred first source.
            # Never append both sources or backfill missing bars from stale data.
            target.extend(selected)

    calendar = (dict(calendar_result["calendar"]) if calendar_result is not None
                else parse_market_calendar(responses, as_of))
    if calendar.get("trading_day") is not False and any(q["date"] == as_of.isoformat() for q in quotes):
        calendar.update(status="verified", trading_day=True, reason="官方當日成交行情已確認交易")
    result = {"quotes": quotes, "fundamentals": fundamentals, "institutions": institutions,
              "calendar": calendar, "sources": sources}
    (run_dir / "manifest.json").write_text(json.dumps({"as_of": as_of.isoformat(), "sources": sources,
        "calendar": calendar}, ensure_ascii=False, indent=2), encoding="utf-8")
    if session is None:
        client.close()
    return result
