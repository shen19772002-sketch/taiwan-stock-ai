"""Rendering fixtures are synthetic and are never used by data collection."""

import json
import re

from taiwan_stock_ai.site import render_site


def test_empty_report_has_no_manufactured_stocks(tmp_path):
    render_site({"as_of": "2026-10-23", "generated_at": "2026-10-23T01:00:00+00:00"}, tmp_path)
    html = (tmp_path / "index.html").read_text()
    data = json.loads((tmp_path / "assets/report.json").read_text())
    assert "等待官方資料" in html
    assert "研究名單正在累積依據" in html
    assert "2026/10/23 09:00 台灣時間" in html
    assert data["analysis"]["candidates"] == []
    assert data["analysis"]["stocks"] == []
    assert (tmp_path / "assets/plotly.min.js").stat().st_size > 100_000
    assert (tmp_path / ".nojekyll").exists()
    assert "https://cdn" not in html


def test_source_errors_and_unsafe_content_are_escaped(tmp_path):
    unsafe = "</script><img src=x onerror=alert(1)>"
    report = {
        "as_of": "2026-10-23", "phase": "intraday", "status": "waiting",
        "sources": [{"id": unsafe, "url": "javascript:alert(1)", "status": "error", "error": unsafe, "row_count": 0}],
        "analysis": {"limitations": [unsafe]},
    }
    render_site(report, tmp_path)
    html = (tmp_path / "index.html").read_text()
    assert unsafe not in html
    assert 'href="javascript:' not in html
    assert "尚未啟用授權盤中資料來源" in html
    assert "取得失敗" in html
    embedded = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S)
    assert embedded
    assert json.loads(embedded.group(1))["sources"][0]["id"] == unsafe


def test_finite_json_and_source_dates_preserved(tmp_path):
    report = {
        "as_of": "2026-10-23", "data_date": "2026-10-22", "status": "partial", "notices": ["資料未齊，停止新訊號"],
        "analysis": {"market": {"covered_stocks": 1}, "stocks": [{"market": "TWSE", "symbol": "9999", "name": "測試資料", "date": "2026-10-22", "close": 45.5, "score": float("nan")}], "industries": [{"industry": "測試產業", "score": 33.0, "turnover_share": 0.25, "stock_count": 1}]},
        "sources": [{"id": "TEST_FIXTURE", "url": "https://example.com/data", "status": "ok", "data_date": "2026-10-22", "fetched_at": "2026-10-23T01:00:00Z", "row_count": 1}],
    }
    render_site(report, tmp_path)
    html = (tmp_path / "index.html").read_text()
    data = json.loads((tmp_path / "assets/report.json").read_text())
    assert data["analysis"]["stocks"][0]["score"] is None
    assert data["analysis"]["stocks"][0]["date"] == "2026-10-22"
    assert "25.0%" in html
    assert "行情交易日：2026-10-22" in html
    assert "資料未齊，停止新訊號" in html
    assert 'href="https://example.com/data"' in html
    assert "成交額與成交額占比是交易熱度代理指標" in html


def test_browser_assets_keep_personal_data_local_and_render_text_safely(tmp_path):
    render_site({"as_of": "2026-10-23"}, tmp_path)
    javascript = (tmp_path / "assets/app.js").read_text()
    assert "localStorage.setItem" in javascript
    assert "textContent" in javascript
    assert "innerHTML" not in javascript
    assert "fetch(" not in javascript
    assert "Number.isInteger(item.quantity)" in javascript
