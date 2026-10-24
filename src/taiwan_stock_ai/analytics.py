"""Explainable, point-in-time end-of-day research models.

Inputs contain observed daily bars, never synthetic replacements. This module
does not place trades and deliberately withholds strategies without warmup or
when unadjusted corporate-action-like moves make price comparisons unsafe.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math
from typing import Any

import numpy as np
import pandas as pd

MIN_BARS = 60
FEE_RATE = 0.001425
SELL_TAX_RATE = 0.003
SLIPPAGE_RATE = 0.001
PROXY_LABEL = "成交額占比及價格強弱代理指標；不代表法人資金淨流入"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _iso_day(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        return None


def tick_size(price: float) -> float:
    """TWSE/TPEx ordinary-share tick size; not ETF or warrant tick rules."""
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Price must be positive and finite")
    for ceiling, tick in ((10, .01), (50, .05), (100, .1), (500, .5), (1000, 1)):
        if price < ceiling:
            return tick
    return 5.0


def round_price(price: float, direction: str = "up") -> float:
    """Round conservatively on the official ordinary-share price lattice."""
    if direction not in {"up", "down"}:
        raise ValueError("direction must be up or down")
    step = Decimal(str(tick_size(price)))
    rounding = ROUND_CEILING if direction == "up" else ROUND_FLOOR
    result = (Decimal(str(price)) / step).to_integral_value(rounding=rounding) * step
    return float(result)


def _valid_bars(quotes: list[dict], as_of: str) -> pd.DataFrame:
    rows: dict[tuple[str, str, str], dict] = {}
    for quote in quotes:
        day = _iso_day(quote.get("date"))
        if not day or day > as_of or quote.get("complete") is not True:
            continue
        market, symbol = str(quote.get("market", "")), str(quote.get("symbol", ""))
        if market not in {"TWSE", "TPEX"} or not symbol:
            continue
        values = {key: _number(quote.get(key)) for key in (
            "open", "high", "low", "close", "volume", "turnover")}
        if any(value is None for value in values.values()):
            continue
        if min(values[key] for key in ("open", "high", "low", "close")) <= 0:
            continue
        if values["volume"] <= 0 or values["turnover"] <= 0:
            continue
        if not (values["low"] <= min(values["open"], values["close"])
                <= max(values["open"], values["close"]) <= values["high"]):
            continue
        # Identical-date corrections are resolved by the latest observed fetch.
        key = market, symbol, day
        previous = rows.get(key)
        if previous and str(previous.get("fetched_at", "")) > str(quote.get("fetched_at", "")):
            continue
        rows[key] = {**quote, **values, "date": day, "symbol": symbol, "market": market}
    return pd.DataFrame(list(rows.values()))


def _latest_observations(rows: list[dict], as_of: str) -> dict[tuple[str, str], dict]:
    """A newer incomplete observation supersedes an older complete one.

    Otherwise a newly unavailable PE (for example after reported losses) would
    silently revive yesterday's positive PE. Callers validate each field and
    its observation date rather than treating completeness as a revision filter.
    """
    latest = {}
    for row in rows:
        day = _iso_day(row.get("date"))
        if not day or day > as_of:
            continue
        key = str(row.get("market", "")), str(row.get("symbol", ""))
        old = latest.get(key)
        if old is None or (day, str(row.get("fetched_at", ""))) >= (
                old["date"], str(old.get("fetched_at", ""))):
            latest[key] = {**row, "date": day}
    return latest


def _indicators(bars: pd.DataFrame) -> dict:
    close = bars["close"]
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    if loss.iloc[-1] == 0:
        rsi = 100.0 if gain.iloc[-1] > 0 else 50.0
    else:
        rsi = 100 - 100 / (1 + gain.iloc[-1] / loss.iloc[-1])
    previous_close = close.shift(1)
    tr = pd.concat([bars["high"] - bars["low"],
                    (bars["high"] - previous_close).abs(),
                    (bars["low"] - previous_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().iloc[-1]
    volume_mean = bars["volume"].iloc[-21:-1].mean()
    return {
        "sma20": float(close.iloc[-20:].mean()),
        "sma60": float(close.iloc[-60:].mean()),
        "rsi14": float(rsi), "atr14": float(atr),
        "momentum20": float(close.iloc[-1] / close.iloc[-21] - 1),
        "volume_ratio": float(bars["volume"].iloc[-1] / volume_mean),
        "high20": float(bars["high"].iloc[-20:].max()),
        "low20": float(bars["low"].iloc[-20:].min()),
        "extension20": float(close.iloc[-1] / close.iloc[-20:].mean() - 1),
    }


def _net_profit(entry: float, exit_price: float) -> float:
    entry_cost = entry * (1 + SLIPPAGE_RATE) * (1 + FEE_RATE)
    exit_proceeds = exit_price * (1 - SLIPPAGE_RATE) * (1 - FEE_RATE - SELL_TAX_RATE)
    return exit_proceeds - entry_cost


def price_plan(close: float, indicators: dict) -> dict | None:
    """ATR observation zone and cost-aware target; conditional, not an order."""
    atr, sma20 = indicators["atr14"], indicators["sma20"]
    if not all(math.isfinite(x) and x > 0 for x in (close, atr, sma20)):
        return None
    entry_low = round_price(max(tick_size(close), min(close, sma20 - .25 * atr)), "down")
    entry_high = round_price(min(close, sma20 + .25 * atr), "up")
    if entry_low <= 0 or entry_high < entry_low:
        return None
    stop_raw = entry_low - 2 * atr
    if stop_raw <= 0:
        return None
    stop = round_price(stop_raw, "down")
    if stop <= 0 or stop >= entry_low:
        return None
    # Worst entry in the observation band; target solves the net 2R equation.
    risk = -_net_profit(entry_high, stop)
    if risk <= 0:
        return None
    entry_cost = entry_high * (1 + SLIPPAGE_RATE) * (1 + FEE_RATE)
    net_exit_factor = (1 - SLIPPAGE_RATE) * (1 - FEE_RATE - SELL_TAX_RATE)
    target = round_price((entry_cost + 2 * risk) / net_exit_factor, "up")
    breakout = round_price(indicators["high20"] + tick_size(indicators["high20"]), "up")
    return {
        "entry_low": entry_low, "entry_high": entry_high,
        "breakout": breakout, "stop": stop, "target": target,
        "risk_reward": round((target - entry_high) / (entry_high - stop), 3),
        "net_risk_reward": round(_net_profit(entry_high, target) / risk, 3),
        "entry_basis": "以觀察區間上緣計算報酬風險比；突破價不沿用本區間的目標及停損",
        "conditions": "盤後收盤突破前 20 日高點、成交量達前 20 日均量 1.2 倍後再重新計算策略；觀察區間需止跌確認",
        "invalidation": "跌破停損、收盤跌破 60 日均線、資料修正或公司行動未完成還原時，策略失效；跳空可能使損失超過估計",
        "costs": {"fee_per_side": FEE_RATE, "sell_tax": SELL_TAX_RATE,
                  "slippage_per_side": SLIPPAGE_RATE},
    }


def analyze(quotes: list[dict], fundamentals: list[dict] | None = None,
            institutions: list[dict] | None = None, as_of: str | None = None) -> dict:
    """Analyze only information dated at or before *as_of*.

    Fundamental dates must be observation/publication dates, never a future
    financial period relabeled as historical availability. This is a research
    screen, not a calibrated statistical prediction or historical backtest.
    """
    valid_days = [_iso_day(row.get("date")) for row in quotes]
    day = _iso_day(as_of) if as_of else max((d for d in valid_days if d), default=date.today().isoformat())
    if day is None:
        raise ValueError("as_of must be an ISO calendar date")
    frame = _valid_bars(quotes, day)
    valuation = _latest_observations(fundamentals or [], day)
    chips = _latest_observations(institutions or [], day)
    output = {
        "as_of": day, "market": {"status": "waiting", "trend": "等待足夠官方資料",
            "advancers": None, "decliners": None, "breadth": None,
            "covered_stocks": 0, "comparable_stocks": 0, "technical_ready": 0},
        "industries": [], "candidates": [], "stocks": [], "themes": [],
        "methodology": {
            "version": "1.0.0", "min_bars": MIN_BARS,
            "score": "技術面 45、基本面 25、產業強弱 20、法人籌碼 10；缺資料項目不計分、不補值，另顯示資料覆蓋率",
            "selection": "至少 60 筆完整交易日、當日行情及同日估值、正本益比、價格不高於 20 日均線 8%、20 日動能為正、20 日均線高於 60 日均線、淨報酬風險比至少 2",
            "industry": PROXY_LABEL,
            "market": "等權個股上漲家數與均線廣度，非官方加權指數",
            "costs": {"fee_per_side": FEE_RATE, "sell_tax": SELL_TAX_RATE,
                      "slippage_per_side": SLIPPAGE_RATE},
        },
        "limitations": [
            "採盤後資料；不提供即時報價或保證漲跌的預測。",
            "首次收集未滿 60 個交易日，僅展示可驗證行情，暫不產生選股及價位。",
            "題材以產業價格與成交額變化作探索性代理，尚未串接新聞語意或驗證預測命中率。",
            "正本益比僅表示資料來源的每股盈餘分母為正，不代表已查核獲利品質或未來獲利。",
            "原始行情未進行完整除權息還原；60 日內單日漲跌超過 15% 的股票排除策略，較小公司行動仍可能影響指標。",
            "回測引擎及含公司行動的總報酬回測尚未啟用；本次輸出是保存供後續驗證的研究訊號。",
        ],
    }
    if frame.empty:
        return output
    grouped = frame.sort_values("date").groupby(["market", "symbol"], sort=True)
    series: dict[tuple[str, str], pd.DataFrame] = {}
    for key, bars in grouped:
        bars = bars.reset_index(drop=True)
        last = bars.iloc[-1]
        fresh = last["date"] == day
        eligible_bars = bars.iloc[-MIN_BARS:]
        adjusted_risk = bool((eligible_bars["close"].pct_change().abs() > .15).any())
        ind = _indicators(bars) if len(bars) >= MIN_BARS else None
        fundamental = valuation.get(key, {})
        current_fundamental = fundamental if fundamental.get("date") == day else {}
        pe, pb, dividend = (_number(current_fundamental.get(field)) for field in ("pe", "pb", "dividend_yield"))
        stock = {"market": key[0], "symbol": key[1], "name": str(last.get("name") or key[1]),
                 "industry": str(last.get("industry") or "未分類"), "date": last["date"],
                 "close": float(last["close"]), "volume": int(last["volume"]),
                 "turnover": float(last["turnover"]), "source": str(last.get("source") or ""),
                 "fetched_at": str(last.get("fetched_at") or ""), "bars": len(bars),
                 "eligible": False, "exclusions": [], "indicators": ind,
                 "score": None, "score_components": [], "coverage": 0.0,
                 "available_score_max": 0, "pe": pe, "pb": pb, "dividend_yield": dividend,
                 "fundamental_date": fundamental.get("date"),
                 "fundamental_status": "current" if current_fundamental else "stale" if fundamental else "missing",
                 "plan": None,
                 "daily_return": float(last["close"] / bars.iloc[-2]["close"] - 1) if len(bars) >= 2 else None}
        if not fresh:
            stock["exclusions"].append("行情日期落後本次分析日")
        if len(bars) < MIN_BARS:
            stock["exclusions"].append(f"完整歷史僅 {len(bars)} 筆，需至少 {MIN_BARS} 筆")
        if adjusted_risk:
            stock["exclusions"].append("60 日內存在逾 15% 單日漲跌，待確認公司行動及還原")
        if fundamental and fundamental.get("date") != day:
            stock["exclusions"].append("估值日期落後分析日；等待同日基本面資料，不沿用舊本益比")
        if pe is None or pe <= 0:
            stock["exclusions"].append("缺少正本益比，無法確認正盈餘篩選條件")
        if ind:
            if ind["sma20"] <= ind["sma60"] or ind["momentum20"] <= 0:
                stock["exclusions"].append("中期趨勢或 20 日動能尚未轉強")
            if ind["extension20"] > .08:
                stock["exclusions"].append("股價高於 20 日均線 8%，不符合低乖離觀察條件")
            if ind["atr14"] <= 0:
                stock["exclusions"].append("波動為零，無法形成有效 ATR 風險價位")
        output["stocks"].append(stock)
        series[key] = bars

    fresh_stocks = [s for s in output["stocks"] if s["date"] == day]
    total_turnover = sum(s["turnover"] for s in fresh_stocks)
    industry_groups: dict[str, list[dict]] = {}
    for stock in fresh_stocks:
        industry_groups.setdefault(stock["industry"], []).append(stock)
    for industry, members in industry_groups.items():
        ready = [s for s in members if s["indicators"] is not None
                 and not any("公司行動" in reason for reason in s["exclusions"])]
        momentum = float(np.mean([s["indicators"]["momentum20"] for s in ready])) if ready else None
        prior_total, prior_sector = 0.0, 0.0
        # Compare each symbol's prior 20-session average with today's share.
        # Coverage counts are explicit because availability is not a net flow.
        for stock in fresh_stocks:
            history = series[(stock["market"], stock["symbol"])]
            if len(history) >= 21:
                amount = float(history["turnover"].iloc[-21:-1].mean())
                prior_total += amount
                if stock["industry"] == industry:
                    prior_sector += amount
        amount = sum(s["turnover"] for s in members)
        share = amount / total_turnover if total_turnover else 0.0
        prior_share = prior_sector / prior_total if prior_total and prior_sector else None
        output["industries"].append({
            "industry": industry, "score": None, "momentum20": momentum,
            "turnover": amount, "turnover_share": share,
            "share_change": share - prior_share if prior_share is not None else None,
            "stock_count": len(members), "technical_ready": len(ready),
            "proxy_label": PROXY_LABEL,
        })
    ranked_industries = sorted([i for i in output["industries"] if i["momentum20"] is not None and i["industry"] != "未分類"],
                               key=lambda i: (i["momentum20"], i["industry"]))
    relative_ranks = pd.Series([i["momentum20"] for i in ranked_industries], dtype=float).rank(method="average")
    for index, industry in enumerate(ranked_industries):
        strength = (relative_ranks.iloc[index] - 1) / (len(ranked_industries) - 1) if len(ranked_industries) > 1 else .5
        rotation = .5 if industry["share_change"] is None else float(np.clip(.5 + industry["share_change"] * 10, 0, 1))
        industry["score"] = round(100 * (.75 * strength + .25 * rotation), 2)
    output["industries"].sort(key=lambda i: (-(i["score"] if i["score"] is not None else -1), -i["turnover"]))
    industry_scores = {i["industry"]: i for i in output["industries"]}

    for stock in output["stocks"]:
        ind = stock["indicators"]
        if not ind or stock["date"] != day:
            continue
        components = []

        def component(name, value, maximum, explanation):
            components.append({"name": name, "score": round(float(value), 2) if value is not None else None,
                               "max_score": maximum, "available": value is not None,
                               "explanation": explanation})

        component("均線趨勢", 10 * (stock["close"] > ind["sma20"]) + 10 * (ind["sma20"] > ind["sma60"]), 20,
                  "收盤高於 SMA20 得 10 分，SMA20 高於 SMA60 得 10 分")
        component("20 日動能", float(np.clip(ind["momentum20"] / .10, 0, 1)) * 10, 10,
                  "20 日漲幅 0% 至 10% 線性給 0 至 10 分")
        component("RSI 區間", 10 if 45 <= ind["rsi14"] <= 70 else 5 if 30 <= ind["rsi14"] < 45 or 70 < ind["rsi14"] <= 80 else 0, 10,
                  "RSI14 45–70 得 10 分；30–45 或 70–80 得 5 分；其餘 0 分")
        component("量能確認", float(np.clip((ind["volume_ratio"] - .8) / .7, 0, 1)) * 5, 5,
                  "當日量 / 前 20 日均量，0.8 至 1.5 倍線性給 0 至 5 分")
        pe, pb, dividend = stock["pe"], stock["pb"], stock["dividend_yield"]
        component("正盈餘與估值", None if pe is None else 15 if 0 < pe <= 15 else 10 if 15 < pe <= 25 else 5 if pe > 25 else 0, 15,
                  "正 PE ≤15 得 15 分；15–25 得 10 分；>25 得 5 分；非正值 0 分")
        component("股價淨值比", None if pb is None else 5 if 0 < pb <= 2 else 2 if pb > 2 else 0, 5,
                  "0 < PB ≤2 得 5 分；PB >2 得 2 分；非正值 0 分")
        component("殖利率", None if dividend is None else float(np.clip(dividend / 5, 0, 1)) * 5, 5,
                  "官方百分比殖利率 0% 至 5% 線性給 0 至 5 分；不代表未來股利")
        sector = industry_scores.get(stock["industry"], {})
        component("產業相對強弱", None if sector.get("score") is None else sector["score"] * .2, 20,
                  "產業等權 20 日動能排名占 75%，成交額占比變化占 25%，乘以 20 分")
        institution = chips.get((stock["market"], stock["symbol"]), {})
        net_buy = (_number(institution.get("net_buy"))
                   if institution.get("date") == day and institution.get("complete") is True else None)
        component("法人買賣超", None if net_buy is None else float(np.clip(net_buy / stock["volume"] / .1, 0, 1)) * 10, 10,
                  "同日官方法人買超股數 / 成交股數，0% 至 10% 線性給 0 至 10 分；缺資料不給分")
        stock["score_components"] = components
        stock["score"] = round(sum(c["score"] for c in components if c["score"] is not None), 2)
        stock["available_score_max"] = sum(c["max_score"] for c in components if c["available"])
        stock["coverage"] = stock["available_score_max"] / 100
        if not stock["exclusions"]:
            stock["plan"] = price_plan(stock["close"], ind)
            if stock["plan"] is None:
                stock["exclusions"].append("無法形成有效且為正的風險價位")
            elif stock["plan"]["net_risk_reward"] < 2:
                stock["exclusions"].append("估計淨報酬風險比不足 2")
                stock["plan"] = None
        stock["eligible"] = not stock["exclusions"] and stock["plan"] is not None

    eligible = [stock for stock in output["stocks"] if stock["eligible"]]
    output["candidates"] = sorted(eligible, key=lambda s: (-s["score"], -s["coverage"], s["market"], s["symbol"]))[:15]
    for rank, stock in enumerate(output["candidates"], start=1):
        stock["rank"] = rank
    ready_stocks = [s for s in fresh_stocks if s["indicators"] is not None
                    and not any("公司行動" in reason for reason in s["exclusions"])]
    comparable = [s for s in fresh_stocks if s["daily_return"] is not None]
    advancing = sum(s["daily_return"] > 0 for s in comparable) if comparable else None
    declining = sum(s["daily_return"] < 0 for s in comparable) if comparable else None
    breadth = sum(s["close"] > s["indicators"]["sma60"] for s in ready_stocks) / len(ready_stocks) if ready_stocks else None
    output["market"] = {
        "status": "ok" if ready_stocks else "warming_up" if fresh_stocks else "stale",
        "trend": "偏多" if breadth is not None and breadth >= .6 else "偏弱" if breadth is not None and breadth <= .4 else "盤整" if breadth is not None else "等待歷史資料",
        "advancers": advancing, "decliners": declining, "breadth": breadth,
        "covered_stocks": len(fresh_stocks), "comparable_stocks": len(comparable),
        "comparison_label": "漲跌僅比較有前一筆已保存收盤價的股票；缺少歷史不視為平盤",
        "technical_ready": len(ready_stocks),
        "turnover": total_turnover,
        "label": "本系統涵蓋普通股之等權市場廣度；非官方指數",
    }
    for industry in output["industries"]:
        momentum, share_change = industry["momentum20"], industry["share_change"]
        if momentum is None or share_change is None or industry["industry"] == "未分類":
            continue
        lifecycle = ("擴張觀察" if momentum > 0 and share_change > 0 else
                     "高檔降溫觀察" if momentum > 0 else
                     "低檔升溫觀察" if share_change > 0 else "退潮觀察")
        output["themes"].append({"industry": industry["industry"], "stage": lifecycle,
                                 "hypothesis": "可能的輪動觀察方向，需後續價格及官方法人資料驗證",
                                 "momentum20": momentum, "share_change": share_change,
                                 "confidence": "未校準；不提供上漲機率", "proxy_label": PROXY_LABEL})
    return output
