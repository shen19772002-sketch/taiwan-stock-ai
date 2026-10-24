"""Forward observation only: no retroactively reconstructed prediction history."""

from datetime import datetime
from zoneinfo import ZoneInfo


def evaluate(signals: list[dict], quotes: list[dict], as_of: str) -> dict:
    by_stock: dict[tuple, list] = {}
    for quote in quotes:
        if quote["date"] <= as_of and quote.get("complete"):
            by_stock.setdefault((quote["market"], quote["symbol"]), []).append(quote)
    records = []
    for signal in signals:
        publication_date = datetime.fromisoformat(signal["available_at"]).astimezone(
            ZoneInfo("Asia/Taipei")).date().isoformat()
        earliest = max(signal["signal_date"], publication_date)
        bars = sorted({q["date"]: q for q in by_stock.get((signal["market"], signal["symbol"]), [])
                       if q["date"] > earliest}.values(), key=lambda q: q["date"])
        for horizon in (5, 20):
            row = {"symbol": signal["symbol"], "name": signal["name"],
                   "market": signal["market"], "signal_date": signal["signal_date"],
                   "horizon": horizon, "entry_date": bars[0]["date"] if bars else None,
                   "exit_date": None, "net_return": None, "status": "pending"}
            if len(bars) >= horizon:
                window = bars[:horizon]
                # Corporate actions are not adjusted in V1. Do not publish misleading returns.
                closes = [signal["close"]] + [q["close"] for q in window]
                jumps = any(abs(b / a - 1) > .15 for a, b in zip(closes, closes[1:]) if a > 0)
                if jumps or any(q["open"] <= 0 or q["close"] <= 0 for q in window):
                    row["status"] = "excluded_unadjusted"
                else:
                    paid = window[0]["open"] * 1.001 * (1 + .001425)
                    received = window[-1]["close"] * .999 * (1 - .001425 - .003)
                    row.update(status="evaluated", exit_date=window[-1]["date"],
                               net_return=received / paid - 1)
            records.append(row)
    # Aggregate one fixed horizon; never mix 5-day and 20-day returns.
    completed = [r for r in records if r["status"] == "evaluated" and r["horizon"] == 20]
    return {"status": "available" if completed else "pending", "signals": len(signals),
            "total_signals": len(signals), "evaluated": len(completed), "summary_horizon": 20,
            "win_rate": sum(r["net_return"] > 0 for r in completed) / len(completed) if completed else None,
            "average_return": sum(r["net_return"] for r in completed) / len(completed) if completed else None,
            "records": records,
            "limitations": ["固定持有期追蹤，非策略成交回測；只統計真實保存過的訊號。",
                             "訊號記錄日之後第一筆完整開盤價模擬買入，第 5／20 筆完整交易資料收盤賣出。",
                             "摘要只統計 20 筆持有期；遇缺資料時觀察筆數不等於完整交易日數。",
                             "買賣各含 0.1425% 手續費與 0.1% 滑價，賣出含 0.3% 證交稅；未計最低手續費。",
                             "尚未接入除權息、拆併股調整與下市全歷史資料；異常跳空排除，績效不能視為總報酬。"]}
