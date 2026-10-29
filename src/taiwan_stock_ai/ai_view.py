"""Read-only presentation context; never rewrite a stored report or prediction."""
from __future__ import annotations

from copy import deepcopy

from .ai_universe import annotate_stocks, get_universe


def build_ai_view(report: dict) -> dict:
    analysis = report.get("analysis", {})
    scope = report.get("research_scope", {})
    scoped = scope.get("id") == "ai_supply_chain"
    day = report.get("as_of") or "1970-01-01"
    universe = get_universe(day)
    # An AI report already contains its point-in-time classification/evidence.
    # Later registry edits must not relabel its stocks or historical ranking.
    observations = deepcopy(analysis.get("stocks", [])) if scoped else annotate_stocks(
        analysis.get("stocks", []), day)
    observations.sort(key=lambda row: (row.get("symbol", ""), row.get("market", "")))
    categories = deepcopy(scope.get("categories", universe["categories"]))
    members = universe["members"]
    industries = {item.get("industry"): item for item in analysis.get("industries", [])}
    for category in categories:
        category["count"] = sum(category["id"] in stock.get("ai_category_ids", [])
                                for stock in observations)
        category["member_count"] = category.get("member_count", sum(
            member["primary_category"] == category["id"] for member in members))
        category["current_count"] = sum(
            category["id"] == stock.get("ai_category_id")
            and stock.get("date") == report.get("data_date") for stock in observations)
        category["strength"] = industries.get(category["name"], {}).get("score") if scoped else None
    required_date = report.get("data_date")
    fresh = [stock for stock in observations if stock.get("date") == required_date]
    valuations = [stock for stock in fresh if stock.get("fundamental_status") == "current"]
    history_ready = [stock for stock in fresh if stock.get("indicators")]
    candidates = deepcopy(analysis.get("candidates", []))
    missing = []
    if not scoped:
        missing.append({"label": "原報告範圍", "detail": "此為舊版全市場報告；AI 卡片僅作附來源的範圍檢視，不重算原始排名或績效。"})
    if not observations:
        missing.append({"label": "AI 觀察行情", "detail": "尚無本研究日可驗證的 AI 公司行情或業務關聯；不補入示範股票。"})
    if observations and len(fresh) < len(observations):
        missing.append({"label": "行情日期", "detail": f"{len(fresh)} / {len(observations)} 檔觀察股票與行情日 {required_date} 對齊；其餘仍保留原日期。"})
    if fresh and len(valuations) < len(fresh):
        missing.append({"label": "同日估值", "detail": f"{len(valuations)} / {len(fresh)} 檔已取得同日估值；落後的本益比不沿用為今日篩選依據。"})
    if fresh and len(history_ready) < len(fresh):
        missing.append({"label": "技術歷史", "detail": f"{len(history_ready)} / {len(fresh)} 檔具備至少 60 筆技術歷史；不代表其餘股票需要重新累積全部日期。"})
    institutions = report.get("institutional_status", {})
    if institutions.get("status") != "ready":
        dates = institutions.get("market_dates", {})
        missing.append({"label": "法人資料", "detail": f"行情基準 {required_date or '待確認'}；上市 {dates.get('TWSE') or '待取得'}、上櫃 {dates.get('TPEX') or '待取得'}。缺值不視為買賣超零。"})
    bad_sources = [source for source in report.get("sources", []) if source.get("status") != "ok"]
    if bad_sources:
        missing.append({"label": "來源品質", "detail": f"{len(bad_sources)} 個來源有待公布、拒收列或錯誤；完整細節保留在官方來源表。"})
    if report.get("status") == "non_trading_day":
        title, text = "休市日，保留最近研究", "今天不產生新的交易訊號；每檔行情仍以原日期判讀。"
    elif not scoped:
        title, text = "舊版全市場報告", "保留原始分析與排名；本頁 AI 觀察不代表已重算為 AI 專屬報告。"
    elif candidates:
        title = f"AI 研究名單已更新 · {len(candidates)} 檔"
        text = "已依 AI 供應鏈範圍與既有量化規則篩選；各股另列資料覆蓋率、價位依據與風險。"
    elif not observations:
        title, text = "等待 AI 研究資料", "業務關聯與行情可驗證後才納入；未取得資料不冒充完整排名。"
    elif len(valuations) < len(fresh) or len(fresh) < len(observations):
        title, text = "今日排名等待同日資料", "先查看已取得的 AI 行情；同日估值或行情未齊，尚不產生完整選股名單。"
    else:
        title, text = "目前無符合條件的 AI 標的", "資料可供觀察，但趨勢、正本益比或風險條件尚未通過；不為湊足名單降低規則。"
    return {
        "scope_label": "AI 核心與供應鏈",
        "report_scope_label": "AI 供應鏈專屬報告" if scoped else "舊版全市場報告",
        "is_ai_report": scoped,
        "data_date": required_date,
        "universe_version": scope.get("version", universe["version"]),
        "registry_hash": scope.get("registry_hash", universe["registry_hash"]),
        "classification_as_of": scope.get("classification_as_of", day),
        "categories": categories, "observations": observations, "candidates": candidates,
        "missing_items": missing, "summary_title": title, "summary_text": text,
        "constituents_count": scope.get("member_count", len(members)),
        "performance_scope": report.get("tracking_scope", {}).get("label", "保留原始歷史訊號，包含先前全市場研究；不是 AI 專屬歷史回測。"),
    }
