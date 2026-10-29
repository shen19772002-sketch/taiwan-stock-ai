"""Daily collection, immutable research records and static publication."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .analytics import analyze
from . import ai_universe
from .performance import evaluate
from .providers import CALENDAR_SOURCE_IDS, ENDPOINTS, collect, collect_calendar, parse_market_calendar
from .site import render_site
from .storage import Store

TAIPEI = ZoneInfo("Asia/Taipei")


def _latest_watchlist_quotes(quotes: list[dict]) -> list[dict]:
    """Keep verified all-market prices without expanding the research universe.

    Choose the latest observation before checking validity: a newer invalid
    observation must not silently revive an older price as if it were current.
    The query that supplies these records already enforces date/known-at bounds.
    """
    latest = {}
    for quote in quotes:
        key = quote.get("market"), quote.get("symbol")
        prior = latest.get(key)
        if prior is None or (quote.get("date", ""), quote.get("fetched_at", "")) >= (
                prior.get("date", ""), prior.get("fetched_at", "")):
            latest[key] = quote
    output = []
    for key in sorted(latest):
        quote = latest[key]
        try:
            close = float(quote.get("close"))
        except (TypeError, ValueError):
            continue
        if quote.get("complete") is not True or not math.isfinite(close) or close <= 0:
            continue
        output.append({field: quote.get(field) for field in (
            "market", "symbol", "name", "close", "date", "source", "fetched_at", "industry",
        )})
    return output


def _ai_analysis(quotes: list[dict], fundamentals: list[dict], institutions: list[dict],
                 research_date: str, data_date: str) -> dict:
    """Run the existing model on the versioned AI universe, not a top-15 filter.

    Current membership may use older bars for warmup, but it cannot be used for
    research dates before its evidence became available. One primary category
    supplies each member's turnover exactly once; secondary tags do not inflate
    the AI-sector denominator or relative-strength scores.
    """
    universe = ai_universe.get_universe(research_date)
    membership = {(m["market"], m["symbol"]): m for m in universe["members"]}
    original_industries = {}
    scoped_quotes = []
    for quote in ai_universe.filter_records(quotes, research_date):
        key = quote["market"], quote["symbol"]
        original_industries[key] = quote.get("industry")
        scoped_quotes.append({**quote, "industry": membership[key]["category"]})
    analysis = analyze(scoped_quotes,
                       ai_universe.filter_records(fundamentals, research_date),
                       ai_universe.filter_records(institutions, research_date), as_of=data_date)
    analysis["stocks"] = ai_universe.annotate_stocks(analysis["stocks"], research_date)
    analysis["candidates"] = ai_universe.annotate_stocks(analysis["candidates"], research_date)
    for stock in (*analysis["stocks"], *analysis["candidates"]):
        stock["official_industry"] = original_industries.get((stock["market"], stock["symbol"]))
    analysis.setdefault("market", {})["label"] = "已納入 AI 供應鏈普通股之等權廣度；非全市場或官方指數"
    methodology = analysis.setdefault("methodology", {})
    methodology.update({
        "version": "1.1.0-ai", "universe": ai_universe.scope_metadata(research_date),
        "industry": "AI 主分類內等權 20 日動能占 75%、成交額占比變化占 25%；分母僅含同日 AI 股票，非資金淨流入",
        "market": "僅以有效 AI 成分股計算漲跌家數與均線廣度；非全市場或官方加權指數",
    })
    for industry in analysis["industries"]:
        industry["denominator_label"] = "同日已取得且通過檢查的 AI 成分股成交額"
        industry["classification_as_of"] = research_date
    limitations = analysis.setdefault("limitations", [])
    limitations.extend(universe["limitations"])
    limitations.append("分類以研究日已知證據為準；目前成分股的回看歷史不代表無倖存者偏誤的歷史成分股回測。")
    analysis["scope_readiness"] = {
        "observed_stocks": len(analysis["stocks"]),
        "current_quotes": sum(stock.get("date") == data_date for stock in analysis["stocks"]),
        "history_ready": sum(stock.get("indicators") is not None for stock in analysis["stocks"]),
        "current_valuations": sum(stock.get("fundamental_status") == "current" for stock in analysis["stocks"]),
        "eligible": len(analysis["candidates"]),
    }
    return analysis


def resolve_phase(phase: str, now: datetime) -> str:
    if phase != "auto":
        return phase
    local = now.astimezone(TAIPEI)
    minutes = local.hour * 60 + local.minute
    if minutes < 9 * 60:
        return "preopen"
    if minutes < 14 * 60:
        return "intraday"
    if minutes < 18 * 60:
        return "postclose"
    return "final"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def _cached_holiday(sources: list[dict], data_dir: Path, as_of: date, now: datetime) -> dict | None:
    """Reuse only an explicit holiday from an intact, locally archived official response."""
    responses, evidence = {}, []
    for source in sources:
        if (source.get("id") not in CALENDAR_SOURCE_IDS or source.get("status") != "ok"
                or source.get("url") != ENDPOINTS[source["id"]]):
            continue
        try:
            if datetime.fromisoformat(source["fetched_at"]) > now:
                continue
            archive = Path(source["raw_path"]).resolve()
            if not archive.is_relative_to((data_dir / "raw").resolve()):
                continue
            raw = archive.read_bytes()
            if hashlib.sha256(raw).hexdigest() != source.get("sha256"):
                continue
            responses[source["id"]] = json.loads(raw)
            evidence.append({key: source[key] for key in ("id", "fetched_at", "raw_path", "sha256")})
        except (KeyError, OSError, TypeError, ValueError):
            continue  # Missing or unverifiable evidence cannot establish a holiday.
    calendar = parse_market_calendar(responses, as_of)
    if calendar.get("trading_day") is False and evidence:
        primary = next((item for item in evidence if item["id"] == "twse_calendar"), evidence[0])
        calendar.update(source_mode="retained", evidence=evidence,
                        **{key: primary[key] for key in ("fetched_at", "raw_path", "sha256")})
        return calendar
    return None


def run(data_dir: Path, output: Path, phase: str = "manual", as_of: date | None = None,
        offline: bool = False, collector=None, now: datetime | None = None,
        research_scope: str = "all") -> dict:
    # Keep direct library callers compatible; the public CLI explicitly defaults
    # to AI, so every existing scheduled and manual Actions entry uses AI scope.
    if research_scope not in {"ai", "all"}:
        raise ValueError("research_scope must be ai or all")
    clock_supplied = now is not None
    now = now or datetime.now(timezone.utc)
    as_of = as_of or now.astimezone(TAIPEI).date()
    if as_of > now.astimezone(TAIPEI).date():
        raise ValueError("不可產生未來日期的研究報告")
    scope = (ai_universe.scope_metadata(as_of.isoformat()) if research_scope == "ai" else
             {"id": "all_market", "label": "全市場普通股研究", "version": "1.0.0"})
    phase = resolve_phase(phase, now)
    notices = []
    result = {"quotes": [], "fundamentals": [], "institutions": [], "sources": [],
              "calendar": {"status": "unknown", "trading_day": None}}
    if as_of.weekday() >= 5:
        result["calendar"] = {"status": "verified", "trading_day": False,
                              "date": as_of.isoformat(), "reason": "例行週末休市"}
        notices.append("非交易日：跳過當日交易分析，保留最近可驗證的歷史行情。")
    elif phase == "intraday":
        notices.append("V1.0 尚未接入有授權的盤中來源；12:30 僅呈現已保存的盤後資料，不更新即時行情。")
    elif offline:
        notices.append("離線模式：僅使用本機已保存資料，沒有發出行情更新請求。")
    elif collector is not None:
        result = collector(as_of, Path(data_dir) / "raw")
    else:
        preflight = collect_calendar(as_of, Path(data_dir) / "raw")
        if preflight["calendar"].get("trading_day") is False:
            result.update(preflight)
        else:
            result = collect(as_of, Path(data_dir) / "raw", calendar_result=preflight)

    if not clock_supplied:
        now = datetime.now(timezone.utc)  # Include observations obtained during this run.

    data_dir, output = Path(data_dir), Path(output)
    with Store(data_dir / "market.sqlite3") as store:
        source_mode = "fetched" if result["sources"] else "none"
        if not result["sources"]:
            result["sources"] = store.latest_sources()
            if result["sources"]:
                source_mode = "retained"
                notices.append("來源表保留最後一次擷取狀態與時間；本次沒有重新驗證官方來源。")
        if source_mode == "retained" and result["calendar"].get("status") == "unknown":
            cached_calendar = _cached_holiday(result["sources"], data_dir, as_of, now)
            if cached_calendar:
                result["calendar"] = cached_calendar
                notices.append("依已保存且雜湊驗證通過的官方行事曆判定休市；本次未重新擷取行事曆。")
        for kind in ("quotes", "fundamentals", "institutions"):
            store.ingest(kind, result[kind], as_of.isoformat())
        # A delayed/manual preopen run must not mix in the same day's closing prices.
        cutoff = (as_of - timedelta(days=1)) if phase == "preopen" else as_of
        quotes = store.records("quotes", cutoff.isoformat(), known_at=now.isoformat())
        fundamentals = store.records("fundamentals", cutoff.isoformat(), known_at=now.isoformat())
        institutions = store.records("institutions", cutoff.isoformat(), known_at=now.isoformat())
        data_date = max((q["date"] for q in quotes), default=None)
        calendar = result["calendar"]
        if calendar.get("trading_day") is False:
            # Display retained context without executing a new trading-day model.
            prior = None
            for previous in store.history():
                saved = store.get_report(previous["id"])
                if (data_date and saved.get("analysis", {}).get("as_of")
                        and saved["analysis"]["as_of"] <= data_date
                        and saved.get("research_scope", {}).get("id", "all_market") == scope["id"]
                        and (research_scope != "ai" or saved.get("research_scope", {}).get("registry_hash")
                             == scope.get("registry_hash"))
                        and datetime.fromisoformat(saved["generated_at"]) <= now):
                    prior = saved
                    break
            analysis = deepcopy(prior["analysis"]) if prior else {
                "as_of": data_date, "market": {"status": "non_trading_day", "technical_ready": 0,
                                             "trend": "休市，暫停當日交易分析"},
                "candidates": [], "stocks": [], "industries": [], "themes": [],
                "limitations": [], "methodology": {},
            }
            analysis["candidates"] = []
            notices.append("官方行事曆或休市公告已確認休市；未執行當日選股模型，畫面保留歷史研究。")
        else:
            analysis = (_ai_analysis(quotes, fundamentals, institutions, as_of.isoformat(),
                                     data_date or as_of.isoformat()) if research_scope == "ai" else
                        analyze(quotes, fundamentals, institutions, as_of=data_date or as_of.isoformat()))
        institution_dates = {
            market: max((row["date"] for row in institutions if row["market"] == market
                         and row.get("complete") and row["date"] <= (data_date or cutoff.isoformat())),
                        default=None) for market in ("TWSE", "TPEX")
        }
        institution_ready = bool(data_date) and all(day == data_date for day in institution_dates.values())
        institutional_status = {
            "status": "ready" if institution_ready else "waiting",
            "required_date": data_date, "market_dates": institution_dates,
        }
        current_markets = {q["market"] for q in quotes if q["date"] == data_date}
        source_errors = any(s["status"] != "ok" for s in result["sources"])
        if calendar.get("trading_day") is False:
            status = "non_trading_day"
            analysis["candidates"] = []
        elif not quotes or phase == "intraday":
            status = "waiting"
        elif (source_errors or current_markets != {"TWSE", "TPEX"}
              or calendar.get("status") != "verified" or offline
              or (phase != "preopen" and data_date != as_of.isoformat())):
            status = "partial"
        elif not analysis["market"].get("technical_ready"):
            status = "waiting"
        else:
            status = "ready"
        if data_date and data_date != as_of.isoformat():
            notices.append(f"最新可驗證交易日期為 {data_date}；本報告日期為 {as_of.isoformat()}。")
        if calendar.get("status") != "verified":
            notices.append("交易日曆尚未完整驗證；不把缺少行情當成已確認休市。")
        if current_markets != {"TWSE", "TPEX"}:
            notices.append("上市／上櫃行情尚未完整涵蓋，排行僅能代表已取得且通過檢查的範圍。")
        if phase == "preopen":
            notices.append("盤前分析只採用報告日期之前的盤後資料；手動或延遲執行也不混入當日收盤價。")
        if not institution_ready:
            notices.append("對應行情日期的上市／上櫃法人明細尚未完整取得；缺值不補零，18:30、20:30 再更新。")
            if status == "ready":
                status = "partial"
        if phase in {"postclose", "final"} and data_date != as_of.isoformat() and status != "non_trading_day":
            notices.append("當日盤後行情尚未完整公布，以下是最後可驗證資料；晚間排程會補充更新。")
        if phase == "final":
            notices.append("晚間更新會重試官方資料；final 是排程階段，並不表示所有資料已公布。")
        if not analysis["candidates"] and status != "non_trading_day":
            if research_scope == "ai":
                readiness = analysis.get("scope_readiness", {})
                notices.append("目前無符合完整條件的 AI 標的；"
                               f"可觀察 {readiness.get('observed_stocks', 0)} 檔、"
                               f"技術歷史就緒 {readiness.get('history_ready', 0)} 檔、"
                               f"同日估值 {readiness.get('current_valuations', 0)} 檔。"
                               "資料缺漏與未符合趨勢／風險條件分開判讀，不代表必須重新累積 60 日。")
            else:
                notices.append("目前無符合完整選股條件的標的；需要至少 60 筆完整歷史、正本益比及技術條件。")
        report = {
            "schema_version": 1, "generated_at": now.isoformat(), "as_of": as_of.isoformat(),
            "data_date": data_date, "phase": phase, "status": status, "data_mode": "盤後資料",
            "sources": result["sources"], "source_mode": source_mode,
            "calendar": calendar, "notices": notices,
            "research_scope": scope,
            "watchlist_quotes": _latest_watchlist_quotes(quotes),
            "tracking_scope": (
                {"id": "historical_and_ai", "label": "保留原始範圍的歷史訊號與新 AI 訊號；非 AI 專屬回測"}
                if research_scope == "ai" else
                {"id": "original_research_scope", "label": "保留各次原始研究範圍的訊號；非 AI 專屬回測"}
            ),
            "institutional_status": institutional_status,
            "analysis": analysis, "history": store.history(),
            # Today's first forecast cannot yet have forward performance. Keep
            # it out of this snapshot so saving it does not change a rerun.
            "performance": evaluate([signal for signal in store.signals(as_of.isoformat())
                                     if signal["signal_date"] < as_of.isoformat()],
                                    quotes, as_of.isoformat()),
        }
        report["performance"].setdefault("limitations", []).insert(0, report["tracking_scope"]["label"])
        record_signals = (phase in {"postclose", "final", "manual"} and not offline
                          and calendar.get("trading_day") is True
                          and as_of == now.astimezone(TAIPEI).date()
                          and data_date == as_of.isoformat())
        run_id = store.save_report(report, record_signals=record_signals)
        # Repeated content resolves to the original immutable report. Never
        # overwrite that filename with a new acquisition time/history payload.
        report = store.get_report(run_id)
        phase_reports = store.latest_phase_reports()
        phase_selection_times = store.phase_selection_times()
        if not store.check():
            raise RuntimeError("SQLite integrity check failed")
        store.export_history(output / "history", limit=31)
        # Older phase pages may refer to history outside the homepage's last 31.
        for saved in (report, *phase_reports.values()):
            for item in saved.get("history", []):
                historical = store.get_report(item["id"])
                if historical is not None:
                    _write_json(output / "history" / f'{item["id"]}.json', historical)
    _write_json(data_dir / "reports" / f'{as_of.isoformat()}-{phase}-{run_id[:16]}.json', report)
    _write_json(data_dir / "latest.json", report)
    render_site(report, output, phase_reports=phase_reports, phase_selection_times=phase_selection_times)
    return report
