"""Rendering fixtures are synthetic and are never used by data collection."""

import json
import re
from copy import deepcopy

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


def test_phase_pages_retain_independent_reports_and_downloads(tmp_path):
    preopen = {
        "as_of": "2026-10-27", "phase": "preopen", "status": "ready",
        "data_date": "2026-10-22", "generated_at": "2026-10-27T00:20:00+00:00",
        "analysis": {"market": {"trend": "盤前測試觀察"}},
    }
    postclose = {
        "as_of": "2026-10-27", "phase": "postclose", "status": "partial",
        "data_date": "2026-10-27", "generated_at": "2026-10-27T07:30:00+00:00",
        "analysis": {"market": {"trend": "盤後測試觀察"}},
        "notices": ["法人資料尚未公布"],
    }
    phase_reports = {"preopen": preopen, "postclose": postclose}
    original = deepcopy(phase_reports)
    render_site(postclose, tmp_path, phase_reports)
    assert phase_reports == original
    preopen_html = (tmp_path / "preopen.html").read_text()
    postclose_html = (tmp_path / "postclose.html").read_text()
    assert "盤前測試觀察" in preopen_html
    assert "盤後測試觀察" not in preopen_html
    assert "盤後測試觀察" in postclose_html
    assert "盤前測試觀察" not in postclose_html
    assert 'href="assets/preopen.json"' in preopen_html
    assert 'href="assets/postclose.json"' in postclose_html
    for page in ("index", "preopen", "postclose", "final"):
        html = (tmp_path / f"{page}.html").read_text()
        assert f'href="{page}.html" aria-current="page"' in html
        assert "08:20" in html and "15:30" in html
        assert "記錄時間（台灣）" in html
        assert all(f'id="{section}"' in html for section in (
            "overview", "rotation", "themes", "ranking", "stock-detail", "watchlist", "reports"
        ))
    final = {
        **postclose, "phase": "final", "generated_at": "2026-10-27T10:30:00+00:00",
        "notices": ["晚間測試更新"],
    }
    render_site(final, tmp_path, {**phase_reports, "final": final})
    assert (tmp_path / "preopen.html").read_text() == preopen_html
    supplement_html = (tmp_path / "postclose.html").read_text()
    assert "最新盤後補充報告（完整資料更新）" in supplement_html
    assert "晚間測試更新" in supplement_html
    assert postclose["notices"] == ["法人資料尚未公布"]
    assert json.loads((tmp_path / "assets/report.json").read_text())["phase"] == "final"
    supplement = json.loads((tmp_path / "assets/postclose.json").read_text())
    assert supplement["phase"] == "final"
    assert supplement["notices"] == ["晚間測試更新"]


def test_missing_phase_is_explicitly_waiting_without_borrowed_data(tmp_path):
    report = {
        "as_of": "2026-10-27", "phase": "postclose", "data_date": "2026-10-27",
        "generated_at": "2026-10-27T07:30:00+00:00", "status": "ready",
        "analysis": {"market": {"trend": "不可借用的盤後觀察"}},
        "sources": [{"id": "TEST", "status": "ok", "row_count": 1}],
        "performance": {"total_signals": 10},
    }
    render_site(report, tmp_path)
    missing = json.loads((tmp_path / "assets/preopen.json").read_text())
    html = (tmp_path / "preopen.html").read_text()
    assert missing["phase"] == "preopen" and missing["status"] == "waiting"
    assert missing["as_of"] is None and missing["data_date"] is None
    assert missing["generated_at"] is None
    assert missing["analysis"]["stocks"] == []
    assert missing["analysis"]["market"] == {}
    assert missing["sources"] == [] and missing["performance"] == {}
    assert "尚未產生盤前研究報告" in html
    assert "不可借用的盤後觀察" not in html
    assert json.loads((tmp_path / "assets/postclose.json").read_text())["data_date"] == "2026-10-27"


def test_previous_phase_date_is_preserved_and_marked(tmp_path):
    earlier = {"as_of": "2026-10-22", "phase": "preopen", "data_date": "2026-10-21"}
    latest = {"as_of": "2026-10-27", "phase": "postclose", "data_date": "2026-10-27"}
    render_site(latest, tmp_path, {"preopen": earlier})
    html = (tmp_path / "preopen.html").read_text()
    assert "此頁保留最近一次盤前研究：2026-10-22" in html
    assert "最新排程基準日為 2026-10-27" in html
    assert "行情交易日：2026-10-21" in html
    data = json.loads((tmp_path / "assets/preopen.json").read_text())
    assert data["as_of"] == "2026-10-22"


def test_mismatched_phase_report_is_not_mislabeled(tmp_path):
    wrong = {"as_of": "2026-10-27", "phase": "final", "status": "ready"}
    render_site(wrong, tmp_path, {"preopen": wrong})
    data = json.loads((tmp_path / "assets/preopen.json").read_text())
    assert data["phase"] == "preopen" and data["status"] == "waiting"
    assert data["as_of"] is None


def test_older_final_does_not_replace_newer_postclose(tmp_path):
    old_final = {"as_of": "2026-10-26", "phase": "final", "generated_at": "2026-10-26T12:30:00Z"}
    postclose = {"as_of": "2026-10-27", "phase": "postclose", "generated_at": "2026-10-27T07:30:00Z"}
    render_site(postclose, tmp_path, {"final": old_final, "postclose": postclose})
    selected = json.loads((tmp_path / "assets/postclose.json").read_text())
    assert selected["phase"] == "postclose" and selected["as_of"] == "2026-10-27"
    same_day_final = {**postclose, "phase": "final", "generated_at": "2026-10-27T15:00:00+08:00"}
    render_site(postclose, tmp_path, {"final": same_day_final, "postclose": postclose})
    selected = json.loads((tmp_path / "assets/postclose.json").read_text())
    assert selected["phase"] == "postclose"


def test_institution_dates_and_missing_market_are_explicit_on_phase_page(tmp_path):
    report = {
        "phase": "postclose", "as_of": "2026-10-27", "data_date": "2026-10-27",
        "institutional_status": {
            "status": "waiting", "required_date": "2026-10-27",
            "market_dates": {"TWSE": "2026-10-22", "TPEX": None},
        },
    }
    render_site(report, tmp_path)
    html = (tmp_path / "postclose.html").read_text()
    assert "法人資料完整性" in html
    assert "行情基準：2026-10-27" in html
    assert "上市：2026-10-22" in html
    assert "上櫃：尚未取得" in html
    assert "缺漏資料不視為買賣超零" in html
    report["institutional_status"] = {
        "status": "ready", "required_date": "2026-10-27",
        "market_dates": {"TWSE": "2026-10-27", "TPEX": "2026-10-27"},
    }
    render_site(report, tmp_path)
    html = (tmp_path / "postclose.html").read_text()
    assert "與行情日對齊" in html
    assert "上櫃：2026-10-27" in html
    assert "缺漏資料不視為買賣超零" not in html


def test_reselected_deduplicated_postclose_supersedes_intervening_final(tmp_path):
    postclose = {
        "phase": "postclose", "as_of": "2026-10-27", "status": "partial",
        "generated_at": "2026-10-27T08:00:00Z", "notices": ["原始盤後報告 A"],
    }
    final = {
        "phase": "final", "as_of": "2026-10-27", "status": "partial",
        "generated_at": "2026-10-27T09:00:00Z", "notices": ["補充報告 B"],
    }
    phase_reports = {"postclose": postclose, "final": final}
    render_site(postclose, tmp_path, phase_reports, {
        "postclose": "2026-10-27T10:00:00Z", "final": "2026-10-27T09:00:00Z",
    })
    selected = json.loads((tmp_path / "assets/postclose.json").read_text())
    assert selected["phase"] == "postclose"
    assert selected["generated_at"] == "2026-10-27T08:00:00Z"
    assert selected["notices"] == ["原始盤後報告 A"]
    assert json.loads((tmp_path / "assets/final.json").read_text())["notices"] == ["補充報告 B"]
    render_site(final, tmp_path, phase_reports, {
        "postclose": "2026-10-27T10:00:00Z", "final": "2026-10-27T11:00:00Z",
    })
    selected = json.loads((tmp_path / "assets/postclose.json").read_text())
    assert selected["phase"] == "final"
    assert selected["generated_at"] == "2026-10-27T09:00:00Z"
