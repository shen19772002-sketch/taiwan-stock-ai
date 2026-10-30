"""Versioned, evidence-linked AI research membership.

Membership is curated per security, not inferred from an exchange industry name.
Only verified primary business evidence known by the research day can activate a member.
The original all-market data and historical forecasts are never rewritten here.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable
from urllib.parse import urlsplit

REGISTRY_PATH = Path(__file__).parent / "data" / "ai_universe.json"
SCOPE_ID = "ai_supply_chain"
LIMITATIONS = [
    "AI 分類是附有公司業務來源的研究名單，不是交易所官方產業分類，也不表示 AI 營收占比。",
    "業務證據採公司官網、官方申報或技術合作方官方公告；來源種類逐筆保留，不採社群傳聞。",
    "採明確名單而非全產業關鍵字；未納入不代表公司沒有 AI 業務。",
    "未驗證、尚未生效或晚於研究日才確認的關聯不納入；歷史原始報告與預測保持不變。",
    "每檔以一個主要分類計算產業統計，次要標籤不重複計入成交額。",
    "矽晶圓為 AI 上游材料及集團轉投資的間接題材，業務證據不表示直接 AI 營收或訂單；中美晶與環球晶具有共同集團曝險。",
]


def _day(value: str, field: str = "as_of") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO calendar date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO calendar date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must be an ISO calendar date")
    return value


def _https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return bool(parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password)
    except ValueError:
        return False


def _validate_registry(registry: dict) -> None:
    if registry.get("id") != SCOPE_ID or not isinstance(registry.get("version"), str):
        raise ValueError("Invalid AI registry identifier/version")
    reviewed_at = _day(registry.get("reviewed_at"), "reviewed_at")
    categories = registry.get("categories", [])
    category_ids = [entry.get("id") for entry in categories]
    if (not category_ids or len(category_ids) != len(set(category_ids))
            or any(not isinstance(value, str) or not value for value in category_ids)):
        raise ValueError("AI categories must have unique nonempty identifiers")
    if any(not entry.get("name") for entry in categories):
        raise ValueError("AI categories must have display names")
    seen = set()
    for member in registry.get("members", []):
        key = member.get("market"), member.get("symbol")
        if key in seen or key[0] not in {"TWSE", "TPEX"} or not re.fullmatch(r"\d{4}", str(key[1])):
            raise ValueError("AI membership requires a unique exact market/security key")
        seen.add(key)
        if not member.get("name") or not member.get("rationale"):
            raise ValueError("AI membership requires a name and business rationale")
        member_day = _day(member.get("known_since"), "known_since")
        if member_day > reviewed_at:
            raise ValueError("AI membership cannot be known after its registry review")
        labels = member.get("category_ids", [])
        if (not labels or len(labels) != len(set(labels)) or not set(labels).issubset(category_ids)
                or member.get("primary_category") not in labels):
            raise ValueError("AI member categories must refer to registered categories")
        evidence = member.get("evidence", [])
        if not evidence:
            raise ValueError("AI membership requires issuer evidence")
        for item in evidence:
            if not _https_url(item.get("url")) or not item.get("title"):
                raise ValueError("AI evidence requires an HTTPS public source and title")
            if item.get("kind", "issuer") not in {"issuer", "official_filing", "technology_partner"}:
                raise ValueError("AI evidence must identify a primary issuer, filing or technology-partner source")
            known = _day(item.get("known_since"), "evidence.known_since")
            checked = _day(item.get("reviewed_at"), "evidence.reviewed_at")
            if known > checked or checked > reviewed_at or known < member_day:
                raise ValueError("AI evidence dates must preserve publication knowledge order")
            if item.get("verification_status") not in {"verified", "pending", "unavailable"}:
                raise ValueError("AI evidence needs an explicit verification state")
            if item.get("verification_status") == "verified":
                if not re.fullmatch(r"[a-f0-9]{64}", item.get("source_sha256", "")) or not item.get("excerpt"):
                    raise ValueError("Verified AI evidence must preserve an observed source digest and excerpt")


@lru_cache(maxsize=1)
def _load_registry() -> dict:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    _validate_registry(registry)
    canonical = json.dumps(registry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    registry["registry_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return registry


def get_universe(as_of: str) -> dict:
    """Return verified membership available by the research day, with evidence.

    ``as_of`` is the report research day, not necessarily its latest quote day.
    Undated issuer pages first verified today are not retroactively available to
    yesterday's forecasts. Returned values are copies, safe for render metadata.
    """
    research_day = _day(as_of)
    registry = _load_registry()
    names = {entry["id"]: entry["name"] for entry in registry["categories"]}
    members = []
    for stored in registry["members"]:
        if stored["known_since"] > research_day:
            continue
        evidence = [item for item in stored["evidence"]
                    if item["verification_status"] == "verified" and item["known_since"] <= research_day]
        if not evidence:
            continue
        member = deepcopy(stored)
        member["evidence"] = deepcopy(evidence)
        member["category"] = names[member["primary_category"]]
        member["categories"] = [names[identifier] for identifier in member["category_ids"]]
        members.append(member)
    return {
        "id": SCOPE_ID,
        "label": "AI 核心與供應鏈",
        "version": registry["version"],
        "registry_hash": registry["registry_hash"],
        "classification_as_of": research_day,
        "reviewed_at": registry["reviewed_at"],
        "categories": deepcopy(registry["categories"]),
        "members": members,
        "limitations": deepcopy(LIMITATIONS),
    }


def filter_records(records: Iterable[dict], as_of: str) -> list[dict]:
    """Copy only exact active market/security records; preserve official fields."""
    members = {(item["market"], item["symbol"]) for item in get_universe(as_of)["members"]}
    return [deepcopy(row) for row in records if (row.get("market"), str(row.get("symbol"))) in members]


def annotate_stocks(stocks: Iterable[dict], as_of: str) -> list[dict]:
    """Return active AI stocks with primary category, secondary tags and sources."""
    members = {(item["market"], item["symbol"]): item for item in get_universe(as_of)["members"]}
    annotated = []
    for stock in stocks:
        member = members.get((stock.get("market"), str(stock.get("symbol"))))
        if member is None:
            continue
        annotated.append({
            **deepcopy(stock),
            "ai_category": member["category"],
            "ai_category_id": member["primary_category"],
            "ai_categories": deepcopy(member["categories"]),
            "ai_category_ids": deepcopy(member["category_ids"]),
            "ai_evidence": deepcopy(member["evidence"]),
            "ai_rationale": member["rationale"],
            "ai_classification_as_of": _day(as_of),
        })
    return annotated


def scope_metadata(as_of: str) -> dict:
    """Compact immutable context for a new report or a labeled derived view."""
    universe = get_universe(as_of)
    categories = []
    for category in universe["categories"]:
        category_members = [member for member in universe["members"]
                            if member["primary_category"] == category["id"]]
        categories.append({**category, "member_count": len(category_members)})
    return {
        key: deepcopy(value) for key, value in {
            **universe,
            "member_count": len(universe["members"]),
            "categories": categories,
            "membership_rule": "依精確市場及代號、已驗證公司業務證據與研究日納入；非整個電子產業",
        }.items() if key != "members"
    }
