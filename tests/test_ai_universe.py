"""AI scope must be evidence-gated, exact-keyed and point-in-time."""
from copy import deepcopy
import hashlib
import json

import pytest

from taiwan_stock_ai import ai_universe as scope


@pytest.fixture
def registry():
    def member(market, symbol, known, status="verified"):
        return {
            "market": market, "symbol": symbol, "name": f"Company {symbol}",
            "primary_category": "server", "category_ids": ["server", "chip"],
            "known_since": known, "rationale": "Issuer discloses AI server products.",
            "evidence": [{"url": f"https://issuer.example/{symbol}/ai-server",
                          "title": "Issuer AI server products", "known_since": known,
                          "reviewed_at": "2026-10-29", "verification_status": status,
                          "source_sha256": "a" * 64, "excerpt": "AI servers for data centers."}],
        }
    return {
        "id": "ai_supply_chain", "version": "fixture.1", "registry_hash": "b" * 64,
        "reviewed_at": "2026-10-29",
        "categories": [{"id": "server", "name": "AI 伺服器", "description": "Systems"},
                       {"id": "chip", "name": "晶片與先進製程", "description": "Chips"}],
        "members": [member("TWSE", "2330", "2026-10-28"),
                    member("TPEX", "3324", "2026-10-29"),
                    member("TWSE", "9999", "2026-10-28", "pending")],
    }


def test_only_verified_members_known_by_research_day(registry, monkeypatch):
    monkeypatch.setattr(scope, "_load_registry", lambda: registry)
    before = scope.get_universe("2026-10-27")
    first = scope.get_universe("2026-10-28")
    after = scope.get_universe("2026-10-29")
    assert before["members"] == []
    assert [m["symbol"] for m in first["members"]] == ["2330"]
    assert [m["symbol"] for m in after["members"]] == ["2330", "3324"]
    assert first["classification_as_of"] == "2026-10-28"
    assert first["members"][0]["category"] == "AI 伺服器"
    assert len(first["members"][0]["categories"]) == 2


def test_exact_market_symbol_filter_not_company_name_or_industry(registry, monkeypatch):
    monkeypatch.setattr(scope, "_load_registry", lambda: registry)
    records = [
        {"market": "TWSE", "symbol": "2330", "industry": "半導體", "close": 100},
        {"market": "TPEX", "symbol": "2330", "industry": "半導體", "close": 101},
        {"market": "TWSE", "symbol": "8888", "name": "AI server Company 2330", "industry": "AI"},
        {"market": "TWSE", "symbol": "9999", "industry": "AI"},
        {"market": "TPEX", "symbol": "3324", "industry": "其他電子"},
    ]
    original = deepcopy(records)
    selected = scope.filter_records(records, "2026-10-28")
    assert selected == [records[0]]
    selected[0]["industry"] = "AI 伺服器"
    assert records == original  # Original official industry and raw observations survive.


def test_later_primary_evidence_does_not_backdate_an_earlier_candidate(registry, monkeypatch):
    registry["members"][0]["evidence"][0]["known_since"] = "2026-10-29"
    scope._validate_registry(registry)
    monkeypatch.setattr(scope, "_load_registry", lambda: registry)
    assert scope.get_universe("2026-10-28")["members"] == []
    assert len(scope.get_universe("2026-10-29")["members"]) == 2


def test_direct_technology_partner_source_is_explicitly_identified(registry, monkeypatch):
    registry["members"][0]["evidence"][0]["kind"] = "technology_partner"
    scope._validate_registry(registry)
    monkeypatch.setattr(scope, "_load_registry", lambda: registry)
    member = scope.get_universe("2026-10-28")["members"][0]
    assert member["evidence"][0]["kind"] == "technology_partner"
    assert member["evidence"][0]["source_sha256"] == "a" * 64


def test_stock_annotation_copies_business_sources_without_changing_scores(registry, monkeypatch):
    monkeypatch.setattr(scope, "_load_registry", lambda: registry)
    stock = {"market": "TWSE", "symbol": "2330", "date": "2026-10-27",
             "industry": "半導體", "score": 42.5, "plan": {"stop": 95}}
    original = deepcopy(stock)
    result = scope.annotate_stocks([stock], "2026-10-28")[0]
    assert result["ai_category"] == "AI 伺服器"
    assert result["ai_category_ids"] == ["server", "chip"]
    assert result["ai_classification_as_of"] == "2026-10-28"
    assert result["score"] == stock["score"]
    assert result["date"] == stock["date"]  # Membership day never relabels the quote day.
    result["plan"]["stop"] = 1
    result["ai_evidence"][0]["title"] = "changed"
    assert stock == original
    assert registry["members"][0]["evidence"][0]["title"] == "Issuer AI server products"


def test_primary_category_counts_do_not_double_count_secondary_tags(registry, monkeypatch):
    monkeypatch.setattr(scope, "_load_registry", lambda: registry)
    metadata = scope.scope_metadata("2026-10-29")
    counts = {entry["id"]: entry["member_count"] for entry in metadata["categories"]}
    assert metadata["member_count"] == 2
    assert counts == {"server": 2, "chip": 0}
    assert "members" not in metadata
    assert metadata["registry_hash"] == "b" * 64
    metadata["limitations"].clear()
    assert scope.LIMITATIONS


@pytest.mark.parametrize("bad", ["2026-02-30", "20261029", "2026-10-29T00:00:00", "", None, 20261029])
def test_invalid_research_dates_are_rejected(bad):
    with pytest.raises(ValueError, match="ISO calendar date"):
        scope.get_universe(bad)


@pytest.mark.parametrize("mutation", ["duplicate", "wrong_market", "wrong_category", "future_known", "future_review",
                                      "insecure_url", "missing_hash", "missing_excerpt", "unsupported_source_kind"])
def test_registry_rejects_ambiguous_or_unverifiable_membership(registry, mutation):
    member = registry["members"][0]
    if mutation == "duplicate":
        registry["members"].append(deepcopy(member))
    elif mutation == "wrong_market":
        member["market"] = "OTC"
    elif mutation == "wrong_category":
        member["primary_category"] = "unknown"
    elif mutation == "future_known":
        member["known_since"] = "2026-10-30"
    elif mutation == "future_review":
        member["evidence"][0]["reviewed_at"] = "2026-10-30"
    elif mutation == "insecure_url":
        member["evidence"][0]["url"] = "javascript:alert(1)"
    elif mutation == "missing_hash":
        member["evidence"][0].pop("source_sha256")
    elif mutation == "missing_excerpt":
        member["evidence"][0].pop("excerpt")
    elif mutation == "unsupported_source_kind":
        member["evidence"][0]["kind"] = "social_media_rumor"
    with pytest.raises(ValueError):
        scope._validate_registry(registry)


def test_packaged_registry_is_valid_and_evidence_hash_is_reproducible():
    registry = json.loads(scope.REGISTRY_PATH.read_text(encoding="utf-8"))
    scope._validate_registry(registry)
    expected = hashlib.sha256(json.dumps(registry, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")).encode()).hexdigest()
    assert scope._load_registry()["registry_hash"] == expected
    assert len(registry["categories"]) == 8
    assert len({(member["market"], member["symbol"]) for member in registry["members"]}) == len(registry["members"])
