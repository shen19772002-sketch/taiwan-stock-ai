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


def render_site(report: dict, output_dir: Path) -> None:
    """Write an offline-capable dashboard; no external CDN or API key required.

    ``report`` must contain only public research data. Personal watchlists and
    position costs are stored by the browser, never included in report.json.
    """
    root = Path(__file__).parent
    output_dir = Path(output_dir)
    assets = output_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
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
    environment = Environment(
        loader=FileSystemLoader(root / "templates"),
        autoescape=select_autoescape(("html", "xml")),
    )
    environment.filters["number"] = _number
    environment.filters["taipei"] = _local_time
    template = environment.get_template("index.html")
    (output_dir / "index.html").write_text(template.render(report=clean_report), encoding="utf-8")
    (assets / "report.json").write_text(
        json.dumps(clean_report, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8"
    )
    for filename in ("style.css", "app.js"):
        shutil.copyfile(root / "static" / filename, assets / filename)
    (assets / "plotly.min.js").write_text(get_plotlyjs(), encoding="utf-8")
    (output_dir / ".nojekyll").touch()
