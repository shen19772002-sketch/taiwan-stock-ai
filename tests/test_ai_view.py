"""Presentation preserves original research and diagnoses real missing inputs."""
from copy import deepcopy

import pytest

from taiwan_stock_ai.ai_view import build_ai_view
from taiwan_stock_ai.site import render_site


@pytest.fixture(autouse=True)
def fixed_universe(monkeypatch):
    # Explicit test-only registry, never copied into production classifications.
    monkeypatch.setattr("taiwan_stock_ai.ai_view.get_universe", lambda day: {
        "version": "fixture", "registry_hash": "new-registry", "members": [],
        "categories": [{"id": "chip", "name": "晶片", "description": "測試分類"}],
    })


def scoped_report():
    return {
        "as_of": "2026-10-29", "data_date": "2026-10-29", "status": "partial",
        "research_scope": {"id": "ai_supply_chain", "version": "saved-registry",
                           "registry_hash": "original-hash", "member_count": 1,
                           "classification_as_of": "2026-10-29",
                           "categories": [{"id": "chip", "name": "晶片", "member_count": 1}]},
        "analysis": {"stocks": [{"market": "TWSE", "symbol": "9999", "close": 100,
                                  "date": "2026-10-29", "bars": 80, "indicators": {"sma20": 99},
                                  "fundamental_status": "stale", "ai_category_id": "chip",
                                  "ai_category_ids": ["chip"], "ai_categories": ["晶片"],
                                  "ai_evidence": [{"title": "原始分類依據"}]}],
                     "candidates": [], "industries": []},
        "institutional_status": {"status": "waiting", "market_dates": {"TWSE": "2026-10-28"}},
    }


def test_same_day_valuation_wait_not_misdiagnosed_as_missing_history():
    report = scoped_report()
    view = build_ai_view(report)
    assert view["summary_title"] == "今日排名等待同日資料"
    labels = {item["label"] for item in view["missing_items"]}
    assert "同日估值" in labels
    assert "技術歷史" not in labels
    assert view["data_date"] == "2026-10-29"


def test_render_context_uses_saved_classification_not_a_later_registry():
    report = scoped_report()
    original = deepcopy(report)
    view = build_ai_view(report)
    assert view["registry_hash"] == "original-hash"
    assert view["observations"][0]["ai_evidence"][0]["title"] == "原始分類依據"
    view["observations"][0]["close"] = 1
    assert report == original


def test_no_eligible_stock_does_not_imply_missing_official_data():
    report = scoped_report()
    report["analysis"]["stocks"][0]["fundamental_status"] = "current"
    report["institutional_status"] = {"status": "ready"}
    view = build_ai_view(report)
    assert view["summary_title"] == "目前無符合條件的 AI 標的"
    assert not view["candidates"]


def test_legacy_rank_is_retained_under_its_original_scope(monkeypatch):
    report = scoped_report()
    report.pop("research_scope")
    report["analysis"]["candidates"] = [{"symbol": "8888", "market": "TWSE", "rank": 1}]
    monkeypatch.setattr("taiwan_stock_ai.ai_view.annotate_stocks", lambda stocks, day: [])
    view = build_ai_view(report)
    assert not view["is_ai_report"]
    assert view["report_scope_label"] == "舊版全市場報告"
    assert view["candidates"] == report["analysis"]["candidates"]
    assert not view["observations"]
    assert "不是 AI 專屬" in view["performance_scope"]


def test_previous_report_link_does_not_point_to_a_replaced_phase_page(tmp_path):
    old = scoped_report()
    old.update(phase="postclose", as_of="2026-10-28", data_date="2026-10-28",
               generated_at="2026-10-28T07:30:00Z")
    old["analysis"]["candidates"] = [{"market": "TWSE", "symbol": "9999", "name": "合成測試"}]
    current = scoped_report()
    current.update(phase="final", generated_at="2026-10-29T10:30:00Z")
    render_site(current, tmp_path, phase_reports={"postclose": old, "final": current})
    # postclose.html now resolves to current's final supplement. It must not be
    # advertised as the previous date's usable report merely from an old head.
    html = (tmp_path / "index.html").read_text()
    assert 'class="previous-report-link"' not in html
    assert "2026-10-29" in (tmp_path / "postclose.html").read_text()
