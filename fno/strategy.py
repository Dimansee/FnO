"""The rulebook, in code.

STRATEGY: "Noise-Area Momentum" (Zarattini, Aziz & Barbon, "Beat the Market", 2024),
adapted to Nifty / Bank Nifty options and chosen by a 3.75-year research loop
(2,700 settings x 9 strategy families, tuned on 2023 - Apr 2026, judged on the
last 30/60/90/120 days it never saw - see research/README.md).

Idea: most intraday wiggles are noise. Measure, for every time of day, how far the
index usually is from its open (average of the last 14 sessions). Only when price
travels well beyond that usual distance is a real trend likely - then ride it.

1. Bands: upper = max(today's open, yesterday's close) x (1 + 1.75 x usual move),
          lower = min(today's open, yesterday's close) x (1 - 1.75 x usual move).
2. Check only on the half hour (09:45, 10:15 ... 14:15), using the 5-min close.
3. CALL: close above the upper band AND above VWAP.  PUT: mirror image.
4. Skip the day if India VIX < 11 (moves too small to pay for the option).
5. Buy the option 1 strike IN-the-money of the nearest expiry (not on expiry day).
   Checked on real NSE option prices: ITM loses less to time decay on range days.
6. Stop-loss on the index: 2 x ATR(14, 5-min). Target: 4 x that risk.
7. Trailing exit, on each half hour: CALL exits if the close drops below
   max(upper band, VWAP); PUT exits if it rises above min(lower band, VWAP).
8. Square off by 15:15. One trade per instrument per day. Risk 1% of capital.
The market-context score (global cues, news, PCR, FII...) is shown for
information only: in the backtest, using it as a filter REDUCED profit.
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


def _is_check(bar_start) -> bool:
    """A 5-min bar whose close lands on :15 or :45 (09:45, 10:15 ...)."""
    end = bar_start + timedelta(minutes=C.CANDLE_MIN)
    return end.minute % C.CHECK_EVERY_MIN == C.FIRST_CHECK.minute % C.CHECK_EVERY_MIN


def noise_sigma(hist: pd.DataFrame, lookback: int = C.NOISE_LOOKBACK) -> dict:
    """Average |close / session open - 1| at each time of day over the last `lookback` sessions."""
    if hist.empty:
        return {}
    days = sorted(set(hist.index.date))[-lookback:]
    if len(days) < C.NOISE_MIN_SESSIONS:
        return {}
    h = hist[pd.Index(hist.index.date).isin(days)]
    op = h.groupby(h.index.date)["open"].transform("first")
    mv = (h["close"] / op - 1).abs()
    tod = h.index.hour * 60 + h.index.minute
    return mv.groupby(tod).mean().to_dict()


def noise_bands(today: pd.DataFrame, prev_close: float, sigma: dict, mult: float = C.NOISE_MULT) -> pd.DataFrame:
    if today.empty or not sigma:
        return pd.DataFrame(index=today.index, columns=["up", "lo"], dtype=float)
    o = float(today["open"].iloc[0])
    tod = today.index.hour * 60 + today.index.minute
    sg = pd.Series([sigma.get(t) for t in tod], index=today.index, dtype=float)
    return pd.DataFrame({"up": max(o, prev_close) * (1 + mult * sg), "lo": min(o, prev_close) * (1 - mult * sg)})


def noise_state(candles: pd.DataFrame, now: datetime) -> dict | None:
    """Latest completed bar with its bands and VWAP - used by the trailing exit."""
    today, hist = session_frames(candles, now)
    if today.empty or hist.empty:
        return None
    b = noise_bands(today, float(hist["close"].iloc[-1]), noise_sigma(hist))
    last = today.index[-1]
    return {"bar_start": last, "bar_end": last + timedelta(minutes=C.CANDLE_MIN), "is_check": _is_check(last),
            "close": float(today["close"].iloc[-1]), "vwap": float(today["vwap"].iloc[-1]),
            "up": None if b.empty or pd.isna(b["up"].iloc[-1]) else float(b["up"].iloc[-1]),
            "lo": None if b.empty or pd.isna(b["lo"].iloc[-1]) else float(b["lo"].iloc[-1])}


def evaluate(symbol, candles, vix, cstats, news, gcues, fii, now: datetime, day_guard: dict | None = None,
             traded_today=False, enabled=("noise", "camarilla")) -> dict:
    """Runs both strategies. Noise-area momentum has priority; Camarilla breakout is the second system."""
    tt = traded_today if isinstance(traded_today, dict) else {"noise": bool(traded_today), "camarilla": bool(traded_today)}
    res = _evaluate_noise(symbol, candles, vix, cstats, news, gcues, fii, now, day_guard, tt.get("noise", False))
    today, hist = res["today"], res.pop("hist")
    if "noise" not in enabled and res["signal"] not in ("STOP FOR TODAY", "MARKET CLOSED"):
        res["plan"], res["signal"] = None, "WAIT"
        res["checks"] = [(None, "Noise-band strategy is switched off in Settings")]
    cam = evaluate_camarilla(today, hist, vix, now, tt.get("camarilla", False))
    res["camarilla"] = {k: cam[k] for k in ("levels", "signal")}
    if res["signal"] in ("STOP FOR TODAY", "MARKET CLOSED"):
        return res
    if "camarilla" not in enabled:
        return res
    res["checks"].append((None, "Second strategy - Camarilla breakout:"))
    res["checks"] += cam["checks"]
    if not res["plan"] and cam["plan"]:
        res["signal"], res["plan"], res["strategy"] = cam["signal"], cam["plan"], "Camarilla breakout"
    elif res["signal"] in ("NO TRADE TODAY", "NO NEW ENTRIES") and cam["signal"] == "WAIT":
        res["signal"] = "WAIT"
    return res


def camarilla_levels(hist: pd.DataFrame) -> dict | None:
    if hist.empty:
        return None
    last = hist[hist.index.date == hist.index[-1].date()]
    h, l, c = float(last["high"].max()), float(last["low"].min()), float(last["close"].iloc[-1])
    r = (h - l) * 1.1
    return {"h4": c + r / 2, "h3": c + r / 4, "l3": c - r / 4, "l4": c - r / 2, "pivot": (h + l + c) / 3}


def evaluate_camarilla(today, hist, vix, now: datetime, traded_today=False) -> dict:
    """Camarilla breakout: a 5-min close crossing yesterday's H4 (buy CALL) or L4 (buy PUT), 09:20-13:00,
    India VIX 11-22. Stop at H3/L3 (1-3 x ATR), stop to entry after +1R, 45-min time stop, no fixed target."""
    out = {"levels": camarilla_levels(hist), "signal": "WAIT", "checks": [], "plan": None}
    lv = out["levels"]
    if not lv or today.empty or today.index[-1].date() != now.date():
        return out
    vlev = float(vix.get("level") or 0)
    ok = C.VIX_MIN <= vlev <= C.CAM_VIX_MAX
    out["checks"].append((ok, f"India VIX {vlev:.2f} between {C.VIX_MIN:g} and {C.CAM_VIX_MAX:g}"))
    if not ok:
        out["signal"] = "NO TRADE TODAY"
        return out
    if traded_today:
        out["signal"] = "NO NEW ENTRIES"
        out["checks"].append((False, "Camarilla trade already taken in this instrument today"))
        return out
    if now.time() > C.CAM_LAST_ENTRY:
        out["signal"] = "NO NEW ENTRIES"
        out["checks"].append((False, f"Camarilla entries only until {C.CAM_LAST_ENTRY:%H:%M}"))
        return out
    if len(today) < 2:
        out["checks"].append((None, f"Levels: H4 {lv['h4']:.1f} / L4 {lv['l4']:.1f} - waiting for the first candles"))
        return out
    spot, last = float(today["close"].iloc[-1]), today.iloc[-1]
    out["checks"].append((None, f"Yesterday's levels: H4 {lv['h4']:.1f} · H3 {lv['h3']:.1f} · L3 {lv['l3']:.1f} · L4 {lv['l4']:.1f}"))
    hit = None
    for k in (len(today) - 1, len(today) - 2):           # cross on the last bar, or the one before (10-min window)
        if k < 1:
            continue
        c0, c1 = float(today["close"].iloc[k - 1]), float(today["close"].iloc[k])
        end = today.index[k] + timedelta(minutes=C.CANDLE_MIN)
        if (now - end).total_seconds() / 60 > C.SIGNAL_VALID_MIN:
            continue
        if c0 <= lv["h4"] < c1 and spot > lv["h4"]:
            hit = ("CE", end)
        elif c0 >= lv["l4"] > c1 and spot < lv["l4"]:
            hit = ("PE", end)
        if hit:
            break
    a = float(last["atr"])
    if hit:
        opt, end = hit
        sign = 1 if opt == "CE" else -1
        ref = lv["h3"] if opt == "CE" else lv["l3"]
        risk = min(max(abs(spot - ref), C.CAM_RMIN * a), C.CAM_RMAX * a)
        out["signal"] = "BUY CALL" if opt == "CE" else "BUY PUT"
        out["checks"].append((True, f"{end:%H:%M} close crossed {'above H4' if opt == 'CE' else 'below L4'}"))
        out["plan"] = {"strategy": "camarilla", "opt": opt, "entry": spot, "risk_pts": risk, "sl": spot - sign * risk,
                       "target": None, "rr": None, "breakeven_at": spot + sign * C.CAM_BE_R * risk,
                       "time_stop_min": C.CAM_TIME_STOP, "trail": "stop to entry at +1R; 45-min time stop"}
    else:
        side = "above H4" if spot >= lv["pivot"] else "below L4"
        out["checks"].append((False, f"Waiting for a 5-min close {side} (now {spot:.1f})"))
    return out


def _evaluate_noise(symbol, candles, vix, cstats, news, gcues, fii, now: datetime, day_guard: dict | None = None,
                    traded_today: bool = False) -> dict:
    today, hist = session_frames(candles, now)
    factors = bias_factors(today, hist, vix, cstats, news, gcues, fii)
    score = sum(x["score"] for x in factors)
    res = {"factors": factors, "score": score, "signal": "WAIT", "checks": [], "warnings": [],
           "orb": None, "bands": None, "plan": None, "today": today, "hist": hist, "strategy": "Noise-area momentum"}

    # the band is useful on the chart even when no trade is possible
    sigma = noise_sigma(hist)
    prev_close = float(hist["close"].iloc[-1]) if not hist.empty else None
    bands = noise_bands(today, prev_close, sigma) if prev_close else pd.DataFrame()
    if not bands.empty and bands["up"].notna().any():
        res["bands"] = {"up": float(bands["up"].iloc[-1]), "lo": float(bands["lo"].iloc[-1]), "mult": C.NOISE_MULT,
                        "series": [{"ts": ts, "up": float(r.up), "lo": float(r.lo)} for ts, r in bands.dropna().iterrows()]}

    t = now.time()
    if symbol in C.STOCKS:
        res["warnings"].append("Stock signals are for practice: in the 2023-26 backtest these rules lost money on most "
                               "single stocks. Nifty was the most consistent - see the Backtest tab.")
    if (news or {}).get("events"):
        res["warnings"].append("Event in the news: " + ", ".join(news["events"]) +
                               ". On RBI/Budget/election-result days, skip or trade half size.")
    if day_guard and day_guard.get("blocked"):
        res["signal"] = "STOP FOR TODAY"
        res["checks"].append((False, day_guard["reason"]))
        return res
    if today.empty or today.index[-1].date() != now.date():
        res["signal"] = "MARKET CLOSED"
        res["checks"].append((None, "Showing the last session. Checks run every half hour from 09:45 on a trading day."))
        return res
    if not sigma:
        res["signal"] = "NO TRADE TODAY"
        res["checks"].append((False, f"Need {C.NOISE_MIN_SESSIONS}+ past sessions of 5-min data to measure the usual move"))
        return res
    vlev = float(vix.get("level") or 0)
    vix_ok = vlev >= C.VIX_MIN
    res["checks"].append((vix_ok, f"India VIX {vlev:.2f} ≥ {C.VIX_MIN:g} (enough movement to pay for an option)"))
    if not vix_ok:
        res["signal"] = "NO TRADE TODAY"
        return res
    if traded_today:
        res["signal"] = "NO NEW ENTRIES"
        res["checks"].append((False, "Already traded this instrument today - the rule is one trade per day"))
        return res
    if t < C.FIRST_CHECK:
        res["checks"].append((None, "First check at 09:45 - the noise band needs the first half hour"))
        return res
    if t > C.LAST_ENTRY:
        res["signal"] = "NO NEW ENTRIES"
        res["checks"].append((False, f"After {C.LAST_ENTRY:%H:%M} - only manage open trades"))
        return res

    # latest half-hour check bar
    checks = [ts for ts in today.index if _is_check(ts)]
    if not checks:
        res["checks"].append((None, "Waiting for the 09:45 check"))
        return res
    cb = checks[-1]
    bar = today.loc[cb]
    end = cb + timedelta(minutes=C.CANDLE_MIN)
    up, lo = float(bands.loc[cb, "up"]), float(bands.loc[cb, "lo"])
    last = today.iloc[-1]
    spot = float(last["close"])
    fresh = (now - end).total_seconds() / 60 <= C.SIGNAL_VALID_MIN
    nxt = end + timedelta(minutes=C.CHECK_EVERY_MIN)
    res["checks"].append((None, f"Last check {end:%H:%M} (next {nxt:%H:%M}) · band {lo:.1f} – {up:.1f}"))
    long_c = [(bar["close"] > up, f"{end:%H:%M} close {bar['close']:.1f} above upper band {up:.1f}"),
              (bar["close"] > bar["vwap"], f"Above VWAP {bar['vwap']:.1f}"),
              (fresh and spot > up, "Still above the band now (signal valid 10 min after the check)")]
    short_c = [(bar["close"] < lo, f"{end:%H:%M} close {bar['close']:.1f} below lower band {lo:.1f}"),
               (bar["close"] < bar["vwap"], f"Below VWAP {bar['vwap']:.1f}"),
               (fresh and spot < lo, "Still below the band now (signal valid 10 min after the check)")]
    a = float(last["atr"])
    risk = min(max(C.STOP_ATR * a, 1.0 * a), 2.5 * a)
    if all(c for c, _ in long_c):
        res["signal"], res["checks"] = "BUY CALL", res["checks"] + long_c
        res["plan"] = _plan("CE", spot, risk)
    elif all(c for c, _ in short_c):
        res["signal"], res["checks"] = "BUY PUT", res["checks"] + short_c
        res["plan"] = _plan("PE", spot, risk)
    else:
        lp, sp = sum(bool(c) for c, _ in long_c), sum(bool(c) for c, _ in short_c)
        side = long_c if lp >= sp else short_c
        res["checks"].append((None, f"Watching {'CALL' if lp >= sp else 'PUT'} side ({max(lp, sp)}/{len(side)} conditions met):"))
        res["checks"] += side
    return res


def _plan(opt, spot, risk):
    sign = 1 if opt == "CE" else -1
    return {
        "strategy": "noise", "opt": opt, "entry": spot, "risk_pts": risk,
        "sl": spot - sign * risk, "target": spot + sign * C.RR_NOISE * risk, "rr": C.RR_NOISE,
        "trail": "noise band / VWAP on the half hour",
    }


def build_order(plan, chain: pd.DataFrame, spot, vix_level, lot, capital, step=None,
                strike: float | None = None, cash: float | None = None, prefs: dict | None = None) -> dict:
    """Turn an underlying plan into concrete option legs, premiums and lots.

    strike: buy this strike instead of the ATM one (e.g. a cheaper OTM strike).
    cash:   demo cash available - lots are capped so the trade is affordable.
    """
    opt, tag = plan["opt"], plan["opt"].lower()
    strikes = chain["strike"].values
    atm_i = int(abs(strikes - spot).argmin())
    # the rule: 1 strike in-the-money (lower strike for a CALL, higher for a PUT)
    def_i = min(max(atm_i + (-C.STRIKE_ITM if opt == "CE" else C.STRIKE_ITM), 0), len(strikes) - 1)
    if strike is not None:
        hits = [i for i, k in enumerate(strikes) if abs(float(k) - float(strike)) < 1e-6]
        if not hits:
            raise ValueError(f"Strike {strike:g} is not in the option chain.")
        buy_i = hits[0]
    else:
        buy_i = def_i
    use_spread = False   # research: the tested rules buy the plain option (no spread)
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

    floor = 0.60 if use_spread else 0.15
    sl = max(entry - delta * plan["risk_pts"], entry * floor)
    tgt = entry + delta * (plan.get("rr") or 3.0) * plan["risk_pts"]   # no fixed target -> show a 3R estimate
    if max_value:
        tgt = min(tgt, max_value * 0.85)
    risk_per_lot = (entry - sl) * lot
    cost_per_lot = entry * lot
    prefs = prefs or {}
    rp = float(prefs.get("risk_pct") or C.RISK_PER_TRADE * 100) / 100
    allowed = capital * rp
    lots = int(allowed // risk_per_lot) if risk_per_lot > 0 else 0
    note = None
    if prefs.get("sizing") == "one_lot":
        lots = 1
        note = f"1-lot mode: this trade risks about ₹{risk_per_lot:,.0f} ({risk_per_lot / capital:.1%} of capital)."
    elif lots == 0:
        if risk_per_lot <= 1.5 * allowed:
            lots, note = 1, f"1 lot risks ₹{risk_per_lot:,.0f} ({risk_per_lot / capital:.1%}) - slightly above your {rp:.1%} rule."
        else:
            note = (f"SKIP: even 1 lot risks ₹{risk_per_lot:,.0f} ({risk_per_lot / capital:.1%} of capital). Pick a cheaper strike, "
                    "or switch Settings → Position size to 'Always 1 lot' if you accept the bigger risk.")
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
            "delta": round(delta, 2), "is_default": buy_i == def_i}


def strike_alternatives(plan, chain: pd.DataFrame, spot, vix_level, lot, capital, cash, itm=2, otm=6, prefs=None) -> list[dict]:
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
            o = build_order(plan, chain, spot, vix_level, lot, capital, strike=float(strikes[i]), cash=cash, prefs=prefs)
        except Exception:
            continue
        if o["entry_prem"] <= 0:
            continue
        out.append({k: o[k] for k in ("strike", "moneyness", "ltp", "entry_prem", "sl_prem", "target_prem",
                                      "cost_per_lot", "risk_per_lot", "lots", "affordable", "delta", "type", "is_default")})
    return out


def exit_check(pos: dict, spot: float, prem: float, now: datetime, state: dict | None = None) -> tuple[str | None, dict]:
    """Return (exit_reason or None, updates) for an open paper position.
    Signal trades exit on the INDEX levels (as backtested); manual trades on premium."""
    plan = pos.get("plan")
    upd = {}
    t = now.time()
    if t >= C.SQUARE_OFF or pos["opened"][:10] < now.date().isoformat():
        return "Square-off 15:15", upd
    if not plan or plan.get("strategy") not in ("noise", "camarilla", "ai"):
        if prem <= pos["sl_prem"]:
            return "Premium stop-loss hit", upd
        if prem >= pos["target_prem"]:
            return "Premium target hit", upd
        if not plan:
            return None, upd
    long_ = plan["opt"] == "CE"
    sl = pos.get("live_sl", plan["sl"])
    if (long_ and spot <= sl) or (not long_ and spot >= sl):
        if pos.get("at_breakeven"):
            return "Breakeven stop (moved to entry at +1R)", upd
        return ("Index stop-loss hit (2 × ATR)" if plan.get("strategy") == "noise" else
                f"Index stop-loss hit ({plan.get('stop_k', 1):g} × ATR)" if plan.get("strategy") == "ai" else "Index stop-loss hit"), upd
    if plan.get("target") is not None and ((long_ and spot >= plan["target"]) or (not long_ and spot <= plan["target"])):
        return f"Index target ({plan.get('rr', C.RR_TARGET):g}R) hit", upd
    if plan.get("strategy") == "noise" and state and state.get("is_check"):
        opened = datetime.fromisoformat(pos["opened"])
        if state["bar_end"].replace(tzinfo=None) > opened.replace(tzinfo=None) and pos.get("last_check") != state["bar_end"].isoformat():
            upd["last_check"] = state["bar_end"].isoformat()
            c, vw = state["close"], state["vwap"]
            if long_ and state.get("up") is not None and c < max(state["up"], vw):
                return f"Trailing exit at {state['bar_end']:%H:%M}: back inside the band / below VWAP", upd
            if not long_ and state.get("lo") is not None and c > min(state["lo"], vw):
                return f"Trailing exit at {state['bar_end']:%H:%M}: back inside the band / above VWAP", upd
    if plan.get("strategy") != "noise":
        if not pos.get("at_breakeven") and plan.get("breakeven_at") is not None and (
                (long_ and spot >= plan["breakeven_at"]) or (not long_ and spot <= plan["breakeven_at"])):
            upd.update({"at_breakeven": True, "live_sl": plan["entry"]})
        if plan.get("time_stop_min"):
            opened = datetime.fromisoformat(pos["opened"])
            half_r = plan["entry"] + (0.5 if long_ else -0.5) * plan["risk_pts"]
            reached = pos.get("reached_half_r") or (long_ and spot >= half_r) or (not long_ and spot <= half_r)
            if reached and not pos.get("reached_half_r"):
                upd["reached_half_r"] = True
            mins = (now.replace(tzinfo=None) - opened.replace(tzinfo=None)).total_seconds() / 60
            if not reached and mins >= plan["time_stop_min"]:
                return f"Time stop ({plan['time_stop_min']} min, no follow-through)", upd
    return None, upd
