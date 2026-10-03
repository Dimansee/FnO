"""The rulebook, in code.

STRATEGY: "ORB-VWAP Trend Breakout with Market-Bias Filter"
-----------------------------------------------------------
A widely taught intraday index/stock-option method (opening-range breakout,
confirmed by VWAP and trend), plus a scoring filter that only allows trades
when the wider context (global markets, India VIX, option-chain OI, news, FII
flows) agrees with the direction. Every rule is fixed in advance so the
decision is mechanical, not emotional.

1. Mark the 09:15-09:30 opening range (ORB).
2. Skip the day if the range is tiny (<0.15% - dead) or huge (>1.2% - chaos).
3. Score the market from -8 to +8 (one point per factor, see `bias_factors`).
4. CALL entry (09:30-14:30): a 5-min candle CLOSES above ORB high, price is
   above VWAP, EMA9 > EMA21, Supertrend is up, and total score >= +3.
   PUT entry is the mirror image with score <= -3.
5. Instrument: India VIX <= 16 -> buy the ATM option.
               India VIX  > 16 -> buy a debit spread (ATM / 2 strikes OTM)
               because options are expensive and premium decays fast.
6. Stop-loss on the underlying: ORB midpoint (bounded to 0.5-1.5 x ATR).
   Option hard stop: never lose more than 30% of premium.
7. Target: 2 x risk. At +1R move stop to entry (breakeven).
8. Time stop: if +0.5R is not reached within 45 minutes, exit (theta).
9. Square off everything by 15:15. Max 2 trades/day; stop at -2% day loss.
10. Position size: risk 1% of capital per trade.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import pandas as pd

from . import config as C
from . import indicators as I


def _f(name, score, value, why):
    return {"factor": name, "score": int(score), "value": value, "why": why}


def session_frames(candles: pd.DataFrame, now: datetime):
    """Split candles into today's session (only completed bars) and history."""
    if candles.empty:
        return candles, candles
    df = I.add_indicators(candles)
    last_day = df.index[-1].date()
    today = df[df.index.date == last_day]
    # drop the bar still forming
    if not today.empty and now.tzinfo is not None:
        if today.index[-1] + timedelta(minutes=C.CANDLE_MIN) > now:
            today = today.iloc[:-1]
    hist = df[df.index.date < last_day]
    return today, hist


def bias_factors(today, hist, vix, cstats, news, gcues, fii) -> list[dict]:
    f = []
    # 1 Global markets
    g = (gcues or {}).get("avg_equity_chg")
    if g is None:
        f.append(_f("Global markets", 0, "n/a", "No global data"))
    else:
        s = 1 if g > 0.4 else -1 if g < -0.4 else 0
        f.append(_f("Global markets", s, f"{g:+.2f}% avg", "US + Asia indices & US futures, latest session"))
    # 2 Opening gap
    if not today.empty and not hist.empty:
        gap = (today["open"].iloc[0] / hist["close"].iloc[-1] - 1) * 100
        s = 1 if gap > 0.25 else -1 if gap < -0.25 else 0
        why = "Gap > 1% - avoid chasing the first move" if abs(gap) > 1 else "Today's open vs yesterday's close"
        f.append(_f("Opening gap", s, f"{gap:+.2f}%", why))
    else:
        f.append(_f("Opening gap", 0, "n/a", "Session not open yet"))
    # 3 India VIX change
    vc = vix.get("chg_pct", 0)
    s = -1 if vc > 5 else 1 if vc < -5 else 0
    f.append(_f("India VIX", s, f"{vix.get('level', 0):.2f} ({vc:+.1f}%)", "Rising VIX = fear (bearish), falling = calm"))
    # 4 Option chain PCR
    pcr = (cstats or {}).get("pcr")
    if pcr is None:
        f.append(_f("Put-Call Ratio (OI)", 0, "n/a", "Needs a broker connection (Settings)"))
    else:
        s = 1 if pcr > 1.2 else -1 if pcr < 0.7 else 0
        f.append(_f("Put-Call Ratio (OI)", s, f"{pcr:.2f}", ">1.2 heavy put writing (support), <0.7 call writing"))
    # 5 News
    ns = (news or {}).get("score")
    if ns is None:
        f.append(_f("News sentiment", 0, "n/a", "No headlines"))
    else:
        s = 1 if ns >= 3 else -1 if ns <= -3 else 0
        f.append(_f("News sentiment", s, f"{ns:+.1f}", "Keyword score of recent headlines"))
    # 6 FII flows
    if fii and fii.get("fii") is not None:
        v = fii["fii"]
        s = 1 if v > 1000 else -1 if v < -1000 else 0
        f.append(_f("FII cash flow", s, f"₹{v:,.0f} cr", "Previous day net FII buying/selling"))
    else:
        f.append(_f("FII cash flow", 0, "n/a", "Enter it in Settings if NSE blocks the fetch"))
    # 7 + 8 Price structure
    if not today.empty:
        last = today.iloc[-1]
        s = 1 if last["close"] > last["vwap"] else -1
        f.append(_f("Price vs VWAP", s, f"{last['close']:.1f} vs {last['vwap']:.1f}", "Above VWAP = buyers in control"))
        up = last["ema9"] > last["ema21"] and last["st"] > 0
        dn = last["ema9"] < last["ema21"] and last["st"] < 0
        f.append(_f("Trend (EMA9/21 + Supertrend)", 1 if up else -1 if dn else 0,
                    "Up" if up else "Down" if dn else "Mixed", "Both must agree"))
    else:
        f.append(_f("Price vs VWAP", 0, "n/a", "Session not open yet"))
        f.append(_f("Trend (EMA9/21 + Supertrend)", 0, "n/a", "Session not open yet"))
    return f


def evaluate(symbol, candles, vix, cstats, news, gcues, fii, now: datetime, day_guard: dict | None = None) -> dict:
    today, hist = session_frames(candles, now)
    factors = bias_factors(today, hist, vix, cstats, news, gcues, fii)
    score = sum(x["score"] for x in factors)
    res = {"factors": factors, "score": score, "signal": "WAIT", "checks": [], "warnings": [],
           "orb": None, "plan": None, "today": today}

    t = now.time()
    if (news or {}).get("events"):
        res["warnings"].append("Event in the news: " + ", ".join(news["events"]) +
                               ". On RBI/Budget/election-result days, skip or trade half size.")
    if day_guard and day_guard.get("blocked"):
        res["signal"] = "STOP FOR TODAY"
        res["checks"].append((False, day_guard["reason"]))
        return res

    if today.empty or today.index[-1].date() != now.date():
        res["signal"] = "MARKET CLOSED"
        res["checks"].append((None, "Showing the last session. Live signals start 09:30 on a trading day."))
        return res
    if t < C.ORB_END:
        res["signal"] = "WAIT"
        res["checks"].append((False, "Opening range forms 09:15-09:30. Plan with the score; no trades yet."))
        return res

    orb = I.opening_range(today)
    if orb is None:
        res["checks"].append((False, "Opening range not available yet"))
        return res
    hi, lo = orb
    mid = (hi + lo) / 2
    spot = float(today["close"].iloc[-1])
    width = (hi - lo) / spot * 100
    res["orb"] = {"high": hi, "low": lo, "mid": mid, "width_pct": width}

    width_ok = C.ORB_MIN_PCT <= width <= C.ORB_MAX_PCT
    res["checks"].append((width_ok, f"Opening range {width:.2f}% (allowed {C.ORB_MIN_PCT}-{C.ORB_MAX_PCT}%)"))
    if not width_ok:
        res["signal"] = "NO TRADE TODAY"
        return res
    if t > C.LAST_ENTRY:
        res["signal"] = "NO NEW ENTRIES"
        res["checks"].append((False, f"After {C.LAST_ENTRY:%H:%M} - only manage open trades"))
        return res

    last = today.iloc[-1]
    long_c = [
        (last["close"] > hi, f"5-min close {last['close']:.1f} above ORB high {hi:.1f}"),
        (last["close"] > last["vwap"], "Price above VWAP"),
        (last["ema9"] > last["ema21"], "EMA9 above EMA21"),
        (last["st"] > 0, "Supertrend up"),
        (score >= C.MIN_BIAS_SCORE, f"Market score {score:+d} ≥ +{C.MIN_BIAS_SCORE}"),
        (vix.get("chg_pct", 0) <= C.VIX_SPIKE_PCT, f"No VIX spike (>{C.VIX_SPIKE_PCT}%)"),
    ]
    short_c = [
        (last["close"] < lo, f"5-min close {last['close']:.1f} below ORB low {lo:.1f}"),
        (last["close"] < last["vwap"], "Price below VWAP"),
        (last["ema9"] < last["ema21"], "EMA9 below EMA21"),
        (last["st"] < 0, "Supertrend down"),
        (score <= -C.MIN_BIAS_SCORE, f"Market score {score:+d} ≤ -{C.MIN_BIAS_SCORE}"),
    ]
    a = float(last["atr"])
    if all(c for c, _ in long_c):
        res["signal"], res["checks"] = "BUY CALL", res["checks"] + long_c
        risk = min(max(spot - mid, 0.5 * a), 1.5 * a)
        res["plan"] = _plan("CE", spot, risk)
    elif all(c for c, _ in short_c):
        res["signal"], res["checks"] = "BUY PUT", res["checks"] + short_c
        risk = min(max(mid - spot, 0.5 * a), 1.5 * a)
        res["plan"] = _plan("PE", spot, risk)
    else:
        # show the side that is closer to triggering
        lp, sp = sum(c for c, _ in long_c), sum(c for c, _ in short_c)
        side = long_c if lp >= sp else short_c
        res["checks"].append((None, f"Watching {'CALL' if lp >= sp else 'PUT'} setup ({max(lp, sp)}/{len(side)} conditions met):"))
        res["checks"] += side
    return res


def _plan(opt, spot, risk):
    sign = 1 if opt == "CE" else -1
    return {
        "opt": opt, "entry": spot, "risk_pts": risk,
        "sl": spot - sign * risk,
        "breakeven_at": spot + sign * risk,
        "target": spot + sign * C.RR_TARGET * risk,
        "time_stop_min": C.TIME_STOP_MIN,
    }


def build_order(plan, chain: pd.DataFrame, spot, vix_level, lot, capital, step=None,
                strike: float | None = None, cash: float | None = None) -> dict:
    """Turn an underlying plan into concrete option legs, premiums and lots.

    strike: buy this strike instead of the ATM one (e.g. a cheaper OTM strike).
    cash:   demo cash available - lots are capped so the trade is affordable.
    """
    opt, tag = plan["opt"], plan["opt"].lower()
    strikes = chain["strike"].values
    atm_i = int(abs(strikes - spot).argmin())
    if strike is not None:
        hits = [i for i, k in enumerate(strikes) if abs(float(k) - float(strike)) < 1e-6]
        if not hits:
            raise ValueError(f"Strike {strike:g} is not in the option chain.")
        buy_i = hits[0]
    else:
        buy_i = atm_i
    use_spread = vix_level > C.VIX_SPREAD_LEVEL
    buy = chain.iloc[buy_i]

    def px(row, side):
        v = row.get(f"{tag}_{side}")
        return float(v) if v and not (isinstance(v, float) and math.isnan(v)) and v > 0 else float(row[f"{tag}_ltp"])

    def dlt(row):
        v = row.get(f"{tag}_delta")
        return abs(float(v)) if v is not None and not pd.isna(v) else 0.5

    ltp = float(buy[f"{tag}_ltp"]) if buy.get(f"{tag}_ltp") is not None and not pd.isna(buy.get(f"{tag}_ltp")) else None
    legs = [{"side": "BUY", "strike": float(buy["strike"]), "opt": opt, "key": buy[f"{tag}_key"], "price": px(buy, "ask")}]
    delta = dlt(buy)
    entry = legs[0]["price"]
    max_value = None
    if use_spread:
        j = buy_i + (C.SPREAD_WIDTH_STRIKES if opt == "CE" else -C.SPREAD_WIDTH_STRIKES)
        if 0 <= j < len(chain):
            sell = chain.iloc[j]
            legs.append({"side": "SELL", "strike": float(sell["strike"]), "opt": opt, "key": sell[f"{tag}_key"],
                         "price": px(sell, "bid")})
            entry = legs[0]["price"] - legs[1]["price"]
            delta = max(delta - dlt(sell), 0.1)
            max_value = abs(legs[1]["strike"] - legs[0]["strike"])
        else:
            use_spread = False

    floor = 0.60 if use_spread else 1 - C.PREMIUM_HARD_SL
    sl = max(entry - delta * plan["risk_pts"], entry * floor)
    tgt = entry + delta * C.RR_TARGET * plan["risk_pts"]
    if max_value:
        tgt = min(tgt, max_value * 0.85)
    risk_per_lot = (entry - sl) * lot
    cost_per_lot = entry * lot
    allowed = capital * C.RISK_PER_TRADE
    lots = int(allowed // risk_per_lot) if risk_per_lot > 0 else 0
    note = None
    if lots == 0:
        if risk_per_lot <= 1.5 * allowed:
            lots, note = 1, f"1 lot risks ₹{risk_per_lot:,.0f} ({risk_per_lot / capital:.1%}) - slightly above the 1% rule."
        else:
            note = f"SKIP: even 1 lot risks ₹{risk_per_lot:,.0f} ({risk_per_lot / capital:.1%} of capital). Pick a cheaper strike or underlying."
    affordable = True
    if cash is not None and cost_per_lot > 0:
        max_lots_cash = int(cash // cost_per_lot)
        if max_lots_cash < 1:
            lots, affordable = 0, False
            note = (f"Not enough cash: 1 lot needs ₹{cost_per_lot:,.0f}, you have ₹{cash:,.0f}. "
                    "Pick a cheaper strike below.")
        elif lots > max_lots_cash:
            lots = max_lots_cash
            note = (note + " " if note else "") + f"Lots reduced to {lots} to fit your cash."
    # how far from ATM, in the option's own terms
    step_n = buy_i - atm_i
    otm_steps = step_n if opt == "CE" else -step_n
    moneyness = "ATM" if otm_steps == 0 else (f"{otm_steps} OTM" if otm_steps > 0 else f"{-otm_steps} ITM")
    return {"type": "Debit spread" if use_spread else "Buy option", "legs": legs, "strike": float(buy["strike"]),
            "opt": opt, "moneyness": moneyness, "ltp": ltp, "entry_prem": round(entry, 2),
            "sl_prem": round(sl, 2), "target_prem": round(tgt, 2), "lots": lots, "lot": lot, "qty": lots * lot,
            "risk_rs": round(risk_per_lot * lots, 0), "reward_rs": round((tgt - entry) * lot * lots, 0),
            "capital_used": round(cost_per_lot * lots, 0), "cost_per_lot": round(cost_per_lot, 0),
            "risk_per_lot": round(risk_per_lot, 0), "affordable": affordable, "note": note,
            "delta": round(delta, 2), "is_default": buy_i == atm_i}


def strike_alternatives(plan, chain: pd.DataFrame, spot, vix_level, lot, capital, cash, itm=2, otm=6) -> list[dict]:
    """The same trade on nearby strikes (2 ITM to 6 OTM), cheapest last."""
    strikes = chain["strike"].values
    atm_i = int(abs(strikes - spot).argmin())
    sign = 1 if plan["opt"] == "CE" else -1  # OTM direction in the chain
    out = []
    for n in range(-itm, otm + 1):
        i = atm_i + sign * n
        if not 0 <= i < len(chain):
            continue
        try:
            o = build_order(plan, chain, spot, vix_level, lot, capital, strike=float(strikes[i]), cash=cash)
        except Exception:
            continue
        if o["entry_prem"] <= 0:
            continue
        out.append({k: o[k] for k in ("strike", "moneyness", "ltp", "entry_prem", "sl_prem", "target_prem",
                                      "cost_per_lot", "risk_per_lot", "lots", "affordable", "delta", "type")})
    return out


def exit_check(pos: dict, spot: float, prem: float, now: datetime) -> tuple[str | None, dict]:
    """Return (exit_reason or None, updates). Applied to open paper positions
    that were opened from a signal (positions with a `plan`)."""
    plan = pos.get("plan")
    upd = {}
    t = now.time()
    if t >= C.SQUARE_OFF or pos["opened"][:10] < now.date().isoformat():
        return "Square-off 15:15", upd
    if prem <= pos["sl_prem"]:
        return "Premium stop-loss hit", upd
    if prem >= pos["target_prem"]:
        return "Premium target hit", upd
    if not plan:
        return None, upd
    long_ = plan["opt"] == "CE"
    sl = pos.get("live_sl", plan["sl"])
    if (long_ and spot <= sl) or (not long_ and spot >= sl):
        return ("Breakeven stop" if pos.get("at_breakeven") else "Underlying stop-loss hit"), upd
    if (long_ and spot >= plan["target"]) or (not long_ and spot <= plan["target"]):
        return "Underlying target (2R) hit", upd
    if not pos.get("at_breakeven") and ((long_ and spot >= plan["breakeven_at"]) or (not long_ and spot <= plan["breakeven_at"])):
        upd.update({"at_breakeven": True, "live_sl": plan["entry"]})
    opened = datetime.fromisoformat(pos["opened"])
    half_r = plan["entry"] + (0.5 if long_ else -0.5) * plan["risk_pts"]
    reached = pos.get("reached_half_r") or (long_ and spot >= half_r) or (not long_ and spot <= half_r)
    if reached and not pos.get("reached_half_r"):
        upd["reached_half_r"] = True
    mins = (now.replace(tzinfo=None) - opened.replace(tzinfo=None)).total_seconds() / 60
    if not reached and mins >= plan["time_stop_min"]:
        return f"Time stop ({plan['time_stop_min']} min, no follow-through)", upd
    return None, upd
