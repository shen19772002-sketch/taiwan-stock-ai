"""Audit a generated research report against stored real observations and raw evidence."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

from taiwan_stock_ai.analytics import tick_size
from taiwan_stock_ai.storage import Store


def validate_plan(plan: dict) -> None:
    for name in ("entry_low", "entry_high", "breakout", "target", "stop"):
        price = plan[name]
        assert isinstance(price, (int, float)) and math.isfinite(price) and price > 0, name
        ticks = price / tick_size(price)
        assert math.isclose(ticks, round(ticks), abs_tol=1e-7), f"Invalid tick: {name}"
    assert 0 < plan["stop"] < plan["entry_low"] <= plan["entry_high"] < plan["target"]
    costs = plan["costs"]
    paid = plan["entry_high"] * (1 + costs["slippage_per_side"]) * (1 + costs["fee_per_side"])
    exit_factor = (1 - costs["slippage_per_side"]) * (1 - costs["fee_per_side"] - costs["sell_tax"])
    risk = paid - plan["stop"] * exit_factor
    reward = plan["target"] * exit_factor - paid
    assert risk > 0 and reward / risk >= 2 - 1e-9, "Cost-adjusted reward/risk is below 2"
    assert math.isclose(reward / risk, plan["net_risk_reward"], abs_tol=.00051)
    assert plan["conditions"] and plan["invalidation"]


def audit(report_path: Path, data_dir: Path) -> dict:
    raw_report = report_path.read_bytes()
    report = json.loads(raw_report)
    analysis = report["analysis"]
    day = analysis["as_of"]
    selected = analysis["candidates"]
    assert 1 <= len(selected) <= 15, "This audit requires a real, nonempty research shortlist"
    assert day == report["data_date"]
    market_coverage = {}
    for market in ("TWSE", "TPEX"):
        current = [s for s in analysis["stocks"] if s["market"] == market and s["date"] == day]
        classified = [s for s in current if s.get("industry") and s["industry"] != "未分類"]
        scored = [s for s in current if any(c["name"] == "產業相對強弱" and c["available"]
                                           for c in s["score_components"])]
        assert current and classified and scored, f"{market} is missing usable industry coverage"
        market_coverage[market] = {"stocks": len(current), "classified": len(classified),
                                   "industry_scored": len(scored),
                                   "eligible": sum(s["eligible"] for s in current),
                                   "selected": sum(s["market"] == market for s in selected)}
    with Store(data_dir / "market.sqlite3") as store:
        assert store.check(), "SQLite integrity failed"
        quotes = store.records("quotes", day, known_at=report["generated_at"])
        fundamentals = store.records("fundamentals", day, known_at=report["generated_at"])
    grouped: dict[tuple, list] = {}
    for row in quotes:
        if row.get("complete") and row["volume"] > 0 and row["turnover"] > 0:
            grouped.setdefault((row["market"], row["symbol"]), []).append(row)
    valuation = {(r["market"], r["symbol"]): r for r in fundamentals if r["date"] == day}
    checked_sources = {}
    stocks = []
    for stock in selected:
        key = stock["market"], stock["symbol"]
        bars = sorted(grouped[key], key=lambda row: row["date"])
        assert len(bars) >= 60 and len({bar["date"] for bar in bars}) == len(bars)
        assert stock["date"] == day == bars[-1]["date"]
        assert stock["eligible"] and not stock["exclusions"]
        assert math.isclose(stock["close"], bars[-1]["close"])
        for period in (20, 60):
            expected = math.fsum(row["close"] for row in bars[-period:]) / period
            assert math.isclose(expected, stock["indicators"][f"sma{period}"], abs_tol=1e-8)
        assert stock["fundamental_date"] == day
        assert stock["pe"] == valuation[key]["pe"] and stock["pe"] > 0
        assert all(row["date"] <= day for row in bars)
        observed_at = datetime.fromisoformat(report["generated_at"])
        assert all(datetime.fromisoformat(row["fetched_at"]) <= observed_at for row in bars)
        assert stock["source"] == bars[-1]["source"]
        source = next(s for s in report["sources"] if s["url"] == stock["source"])
        if source["url"] not in checked_sources:
            path = Path(source["raw_path"]).resolve()
            assert path.is_relative_to(data_dir.resolve()), "Raw evidence outside data directory"
            original = path.read_bytes()
            if path.suffix == ".gz":
                original = gzip.decompress(original)
            assert hashlib.sha256(original).hexdigest() == source["sha256"], "Source hash mismatch"
            json.loads(original)
            checked_sources[source["url"]] = {"sha256": source["sha256"], "raw_path": str(path)}
        validate_plan(stock["plan"])
        stocks.append({"rank": stock["rank"], "market": stock["market"], "symbol": stock["symbol"],
                       "name": stock["name"], "date": stock["date"], "close": stock["close"],
                       "bars": len(bars), "score": stock["score"], "coverage": stock["coverage"],
                       "net_risk_reward": stock["plan"]["net_risk_reward"]})
    assert [s["rank"] for s in stocks] == list(range(1, len(stocks) + 1))
    assert [s["score"] for s in stocks] == sorted((s["score"] for s in stocks), reverse=True)
    return {"status": "passed", "report_sha256": hashlib.sha256(raw_report).hexdigest(),
            "generated_at": report["generated_at"], "as_of": report["as_of"], "data_date": day,
            "candidate_count": len(stocks), "market": analysis["market"], "market_coverage": market_coverage,
            "checks": ["stored real prices", "same-date positive PE", "distinct 60-bar history",
                       "independent SMA20/SMA60", "no future dates or unavailable observations",
                       "legal price ticks", "net reward/risk including fees/tax/slippage",
                       "raw response SHA-256", "ranking order", "TWSE and TPEx industry coverage"],
            "sources": checked_sources, "candidates": stocks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("data/latest.json"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("data/validation/research-audit.json"))
    args = parser.parse_args()
    result = audit(args.report, args.data_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
