"""Backtester for the live rulebook (noise-area momentum) on recent 5-minute data.

The big study behind these rules (3.75 years, 9 strategy families, 2,700+ settings,
judged on days the search never saw) lives in research/ and its headline numbers are
in research_summary.json. This module is the quick re-check you can run in the app
on the latest ~60 days of Yahoo data.

Limits (stated honestly):
- Yahoo keeps only ~60 days of 5-minute candles; the first 14 sessions are used to
  measure the "usual move", so ~45 sessions get traded. Small sample.
- Option prices are modelled with Black-Scholes (IV from India VIX), plus 0.5%
  slippage per fill and full charges. Real fills will differ somewhat.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C
from . import indicators as I
from . import market as D
from . import pricing as PR
from . import strategy as S

SLIP = 0.005


def research_summary() -> dict:
    p = Path(__file__).with_name("research_summary.json")
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _vix_series():
    try:
        v = D.yahoo_candles(C.INDIA_VIX_YAHOO, period="60d", interval="5m")["close"]
        if len(v) > 100 and 5 < float(v.median()) < 100:     # sanity: India VIX lives roughly in 8-90
            return v, None
    except Exception:
        pass
    try:
        d = D.yahoo_daily(C.INDIA_VIX_YAHOO, "6mo")["close"].dropna()
        d.index = (d.index.tz_localize(None) if d.index.tz is not None else d.index).normalize()
        return None, d
    except Exception:
        return None, pd.Series(dtype=float)


def run(symbol: str, capital: float = C.DEFAULT_CAPITAL, mult: float = C.NOISE_MULT, candles: pd.DataFrame | None = None,
        vix_intraday: pd.Series | None = None, vix_daily: pd.Series | None = None, lot: int | None = None,
        sizing: str = "risk", otm: int = -C.STRIKE_ITM, strategy: str = "both"):
    """sizing: "risk" = lots so that 1% of capital is at risk (1 lot allowed up to 1.5%);
    "one_lot" = always 1 lot if the premium fits in the cash (small accounts - much riskier).
    otm: -1 = 1 strike in the money (the rule), 0 = ATM, 1/2 = that many strikes out of the money (cheaper).
    strategy: "noise", "camarilla" or "both" (one open trade per instrument at a time, like the live app)."""
    meta = D.instrument(symbol)
    lot = lot or meta["lot"]
    if candles is None:
        candles = D.yahoo_candles(meta["yahoo"], period="60d", interval="5m")
    if candles is None or candles.empty:
        return {"error": "No historical candles returned."}
    if vix_intraday is None and vix_daily is None:
        vix_intraday, vix_daily = _vix_series()
    df = I.add_indicators(candles)
    days = sorted(set(df.index.date))
    trades, equity = [], capital
    skips = {"risk": 0, "cash": 0}
    step = meta["step"]
    traded_days = 0
    for n, d in enumerate(days):
        hist = df[df.index.date < d]
        day = df[df.index.date == d]
        if len(day) < 60 or hist.empty:
            continue
        sigma = S.noise_sigma(hist)
        if not sigma:
            continue
        traded_days += 1
        bands = S.noise_bands(day, float(hist["close"].iloc[-1]), sigma, mult)

        def vix_at(ts):
            if vix_intraday is not None and len(vix_intraday):
                v = vix_intraday[vix_intraday.index <= ts]
                if len(v):
                    return float(v.iloc[-1])
            if vix_daily is not None and len(vix_daily):
                v = vix_daily[vix_daily.index < pd.Timestamp(d)]
                if len(v):
                    return float(v.iloc[-1])
            return 14.0

        if vix_at(day.index[0]) < C.VIX_MIN:
            continue
        if meta["iv_mult"]:
            # VIX corrected by the real-option-price table (days to expiry, moneyness)
            iv_of = lambda ts, s=None, k=None, opt=None: PR.iv(symbol, vix_at(ts), s, k, opt, (exp - d).days,  # noqa: E731
                                                               meta["step"], meta["iv_mult"]) if s else \
                min(max(vix_at(ts) / 100 * meta["iv_mult"], 0.06), 0.9)
        else:
            rv = D.realised_vol(hist.tail(375)) or 0.25
            iv_of = lambda ts, s=None, k=None, opt=None: min(max(rv, 0.08), 0.9)  # noqa: E731
        exp = I.pick_trading_expiry(I.demo_expiries(meta["expiry"], d, 3), meta["expiry"], d)
        idx = list(day.index)
        lv = S.camarilla_levels(hist)
        day_trades = []
        for strat in (["noise", "camarilla"] if strategy == "both" else [strategy]):
            if strat == "camarilla" and (not lv or vix_at(day.index[0]) > C.CAM_VIX_MAX):
                continue
            start, day_why = 0, set()
            while True:
                res = _one_trade(day, idx, bands, start, exp, iv_of, step, lot, capital, equity, d, mult, sizing, otm,
                                 strat, lv)
                if res is None:
                    break
                if isinstance(res, tuple):          # ("skip", bar, why): too risky/costly for this capital, keep looking
                    day_why.add(res[2])
                    start = res[1] + 1
                    continue
                day_trades.append(res)
                day_why.clear()
                break
            for w in day_why:                         # days whose signal(s) couldn't be taken
                skips[w] += 1
        # one open trade per instrument: if the second strategy's trade overlaps the first, skip it
        day_trades.sort(key=lambda t: t["entry_time"])
        busy_until = ""
        for t in day_trades:
            if t["entry_time"] < busy_until:
                continue
            trades.append(t)
            busy_until = t["exit_time"]
    eq = capital
    for t in trades:
        eq += t["pnl"]
        t["equity"] = round(eq, 0)
    out = _summary(trades, capital, traded_days)
    out["summary"]["skipped"] = skips
    out["summary"]["sizing"] = sizing
    return out


def _one_trade(day, idx, bands, start, exp, iv_of, step, lot, capital, equity, d, mult, sizing="risk", otm=0,
               strat="noise", lv=None):
        entry_i = None
        for i, ts in enumerate(idx):
            if i < start:
                continue
            if strat == "camarilla":
                end = ts + timedelta(minutes=C.CANDLE_MIN)
                if end.time() > C.CAM_LAST_ENTRY:
                    break
                if i < 1:
                    continue
                c0, c1 = float(day["close"].iloc[i - 1]), float(day["close"].iloc[i])
                if c0 <= lv["h4"] < c1:
                    entry_i, opt = i, "CE"
                    break
                if c0 >= lv["l4"] > c1:
                    entry_i, opt = i, "PE"
                    break
                continue
            end = ts + timedelta(minutes=C.CANDLE_MIN)
            if end.time() > C.LAST_ENTRY:
                break
            if end.time() < C.FIRST_CHECK or not S._is_check(ts):
                continue
            b, up, lo = day.loc[ts], bands.loc[ts, "up"], bands.loc[ts, "lo"]
            if pd.isna(up):
                continue
            if b["close"] > up and b["close"] > b["vwap"]:
                entry_i, opt = i, "CE"
                break
            if b["close"] < lo and b["close"] < b["vwap"]:
                entry_i, opt = i, "PE"
                break
        if entry_i is None:
            return None
        ts_in = idx[entry_i]
        bar = day.loc[ts_in]
        sign = 1 if opt == "CE" else -1
        spot = float(bar["close"])
        a = float(bar["atr"])
        if strat == "camarilla":
            risk = min(max(abs(spot - (lv["h3"] if sign > 0 else lv["l3"])), C.CAM_RMIN * a), C.CAM_RMAX * a)
            sl, tgt = spot - sign * risk, None
        else:
            risk = min(max(C.STOP_ATR * a, a), 2.5 * a)
            sl, tgt = spot - sign * risk, spot + sign * C.RR_NOISE * risk
        t_in = ts_in + timedelta(minutes=C.CANDLE_MIN)
        k = round(spot / step) * step + sign * otm * step

        def prem(s, when, ts):
            t = I.years_to_expiry(exp, when.to_pydatetime().replace(tzinfo=None))
            return I.bs_price(s, k, max(t, 1 / (365 * 24 * 12)), iv_of(ts, s, k, opt), opt)

        p_in = prem(spot, t_in, ts_in) * (1 + SLIP)
        risk_unit = max(p_in - prem(sl, t_in, ts_in), p_in * 0.05)
        cost = p_in * lot
        if cost > capital:
            return ("skip", entry_i, "cash")       # can't pay for even 1 lot
        if sizing == "one_lot":
            lots = 1
        else:
            budget = capital * C.RISK_PER_TRADE
            lots = int(budget // (risk_unit * lot))
            if lots == 0 and risk_unit * lot <= 1.5 * budget:
                lots = 1
            if lots == 0:
                return ("skip", entry_i, "risk")   # 1 lot would risk more than 1.5% of capital
            lots = min(lots, int(capital // cost))
        reason, x_spot, x_ts = None, None, None
        live_sl, reached = sl, False
        for ts in idx[entry_i + 1:]:
            c = day.loc[ts]
            end = ts + timedelta(minutes=C.CANDLE_MIN)
            adverse, favour = (c["low"], c["high"]) if sign > 0 else (c["high"], c["low"])
            if sign * (adverse - live_sl) <= 0:
                why = "Breakeven stop" if live_sl == spot else ("Stop-loss (2 ATR)" if strat == "noise" else "Stop-loss (H3/L3)")
                reason, x_spot = why, (c["open"] if sign * (c["open"] - live_sl) < 0 else live_sl)
            elif tgt is not None and sign * (favour - tgt) >= 0:
                reason, x_spot = "Target 4R", (c["open"] if sign * (c["open"] - tgt) > 0 else tgt)
            elif strat == "camarilla":
                if sign * (favour - (spot + sign * C.CAM_BE_R * risk)) >= 0 and sign * (live_sl - spot) < 0:
                    live_sl = spot
                if sign * (favour - (spot + sign * 0.5 * risk)) >= 0:
                    reached = True
                if not reached and (end - t_in).total_seconds() / 60 >= C.CAM_TIME_STOP:
                    reason, x_spot = "Time stop (45 min)", float(c["close"])
            elif S._is_check(ts):
                lvl = max(bands.loc[ts, "up"], c["vwap"]) if sign > 0 else min(bands.loc[ts, "lo"], c["vwap"])
                if not pd.isna(lvl) and sign * (c["close"] - lvl) < 0:
                    reason, x_spot = "Trailing exit (band/VWAP)", float(c["close"])
            if reason is None and end.time() >= C.SQUARE_OFF:
                reason, x_spot = "Square-off", float(c["close"])
            if reason:
                x_ts, x_bar = end, ts
                break
        if reason is None:
            x_bar = idx[-1]
            reason, x_spot, x_ts = "Square-off", float(day["close"].iloc[-1]), idx[-1] + timedelta(minutes=5)
        p_raw = prem(x_spot, x_ts, x_bar)
        p_out = PR.real_adjust(p_in / (1 + SLIP), p_raw, (exp - d).days, (x_ts - t_in).total_seconds() / 3600) * (1 - SLIP)
        qty = lots * lot
        pnl = (p_out - p_in) * qty - I.charges(p_in, p_out, qty)
        equity += pnl
        return {
            "date": d.isoformat(), "entry_time": t_in.strftime("%H:%M"), "exit_time": x_ts.strftime("%H:%M"),
            "side": "CALL" if opt == "CE" else "PUT", "type": "ATM option" if not otm else (f"{otm} OTM option" if otm > 0 else f"{-otm} ITM option"), "band": mult,
            "spot_in": round(spot, 1), "spot_out": round(float(x_spot), 1), "R": round(sign * (x_spot - spot) / risk, 2),
            "prem_in": round(p_in, 2), "prem_out": round(p_out, 2), "lots": lots, "pnl": round(pnl, 0),
            "reason": reason, "equity": round(equity, 0), "strategy": "Noise band" if strat == "noise" else "Camarilla",
        }


def _summary(trades, capital, traded_days):
    t = pd.DataFrame(trades)
    if t.empty:
        return {"trades": t, "summary": {"trades": 0, "days": traded_days}, "days": traded_days}
    w, l = t[t.pnl > 0], t[t.pnl <= 0]
    eq = np.concatenate([[capital], t["equity"].values])
    peak = np.maximum.accumulate(eq)
    summary = {
        "trades": len(t), "days": traded_days, "win_rate": len(w) / len(t) * 100,
        "avg_win": w.pnl.mean() if len(w) else 0, "avg_loss": l.pnl.mean() if len(l) else 0,
        "profit_factor": w.pnl.sum() / -l.pnl.sum() if l.pnl.sum() < 0 else None,
        "net": t.pnl.sum(), "return_pct": t.pnl.sum() / capital * 100,
        "max_dd_pct": ((eq - peak) / peak).min() * 100, "avg_R": t.R.mean(),
    }
    return {"trades": t, "summary": summary, "days": traded_days}
