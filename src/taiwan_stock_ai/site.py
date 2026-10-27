"""Render a self-contained, read-only Traditional Chinese research dashboard."""

from __future__ import annotations

import json
import math
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
from plotly.offline import get_plotlyjs


def _clean(value: Any) -> Any:
    """Produce strict JSON, treating non-finite statistics as unavailable."""
    if isinstance(value, dict):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _number(value: Any, places: int = 2) -> str:
    try:
        numeric = float(value)
        if not math.isfinite(numeric):
            return "—"
        return f"{numeric:,.{places}f}"
    except (TypeError, ValueError):
        return "—"


def _local_time(value: Any) -> str:
    if not value:
        return "尚未更新"
    try:
        date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if date.tzinfo is None:
            return str(value)
        return date.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d %H:%M 台灣時間")
    except ValueError:
        return str(value)


def _safe_url(value: Any) -> str:
    url = str(value or "")
    try:
        parsed = urlsplit(url)
        return url if parsed.scheme in {"http", "https"} and parsed.hostname else ""
    except ValueError:
        return ""


def _prepare_report(report: dict) -> dict:
    """Copy and normalize render data without changing the stored report."""
    clean_report = _clean(report)
    analysis = clean_report.setdefault("analysis", {})
    for field in ("candidates", "stocks", "industries", "themes", "limitations"):
        analysis.setdefault(field, [])
    analysis.setdefault("market", {})
    analysis.setdefault("methodology", {})
    clean_report.setdefault("sources", [])
    clean_report.setdefault("performance", {})
    clean_report.setdefault("history", [])
    clean_report.setdefault("status", "waiting")
    clean_report.setdefault("phase", "manual")
    clean_report.setdefault("data_mode", "盤後資料")
    for source in clean_report["sources"]:
        source["safe_url"] = _safe_url(source.get("url"))
    return clean_report


def _pending_phase(phase: str) -> dict:
    label = {"preopen": "盤前研究", "postclose": "盤後研究", "final": "完整資料更新"}[phase]
    return {
        "schema_version": 1,
        "phase": phase,
        "status": "waiting",
        "as_of": None,
        "data_date": None,
        "generated_at": None,
        "notices": [f"尚未產生{label}報告。排程或手動執行完成後會更新此頁。"],
    }


def _report_order(report: dict, selected_at: str | None = None) -> tuple[str, float]:
    """Prefer the most recently selected phase head, preserving report timestamps."""
    try:
        timestamp = datetime.fromisoformat(str(selected_at or report.get("generated_at", "")).replace("Z", "+00:00"))
        seconds = timestamp.timestamp() if timestamp.tzinfo is not None else 0.0
    except (ValueError, OverflowError):
        seconds = 0.0
    return str(report.get("as_of") or ""), seconds


def render_site(
    report: dict,
    output_dir: Path,
    phase_reports: dict[str, dict] | None = None,
    phase_selection_times: dict[str, str] | None = None,
) -> None:
    """Write the latest report and independently retained phase dashboards.

    ``phase_reports`` contains each phase's latest stored report. Postclose also
    shows newer final supplements under their original phase label. A missing
    phase is shown as waiting; preopen never borrows postclose market analysis.
    ``phase_selection_times`` orders deduplicated phase heads by last selection,
    so a reused older report can correctly supersede an intervening supplement.
    All reports contain public research data only. Personal watchlists and
    position costs stay in the browser across these same-origin pages.
    """
    root = Path(__file__).parent
    output_dir = Path(output_dir)
    assets = output_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    retained = dict(phase_reports or {})
    selected_times = phase_selection_times or {}
    if report.get("phase") in {"preopen", "postclose", "final"}:
        retained.setdefault(report["phase"], report)
    environment = Environment(
        loader=FileSystemLoader(root / "templates"),
        autoescape=select_autoescape(("html", "xml")),
    )
    environment.filters["number"] = _number
    environment.filters["taipei"] = _local_time
    template = environment.get_template("index.html")
    for page in ("index", "preopen", "postclose", "final"):
        selected = report if page == "index" else retained.get(page)
        if page != "index" and selected is not None and selected.get("phase") != page:
            selected = None
        supplement = retained.get("final")
        if (
            page == "postclose" and supplement is not None and supplement.get("phase") == "final"
            and (
                selected is None
                or _report_order(supplement, selected_times.get("final"))
                > _report_order(selected, selected_times.get("postclose"))
            )
        ):
            selected = supplement
        if selected is None:
            selected = _pending_phase(page)
        clean_report = _prepare_report(selected)
        data_file = "report.json" if page == "index" else f"{page}.json"
        (output_dir / f"{page}.html").write_text(
            template.render(
                report=clean_report,
                page=page,
                data_file=data_file,
                is_postclose_supplement=page == "postclose" and clean_report.get("phase") == "final",
                latest_as_of=report.get("as_of"),
                phase_is_stale=(
                    page != "index" and bool(clean_report.get("as_of"))
                    and bool(report.get("as_of"))
                    and clean_report["as_of"] < report["as_of"]
                ),
            ),
            encoding="utf-8",
        )
        (assets / data_file).write_text(
            json.dumps(clean_report, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8"
        )
    for filename in ("style.css", "app.js"):
        shutil.copyfile(root / "static" / filename, assets / filename)
    (assets / "plotly.min.js").write_text(get_plotlyjs(), encoding="utf-8")
    (output_dir / ".nojekyll").touch()
