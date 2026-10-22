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
from . import ai as AI

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


def history(symbol: str, days: int):
    """5-minute candles for the last `days` sessions + ~20 warm-up sessions, plus India VIX.
    Upstox's public history (no login needed) covers years; Yahoo (60 days max) is the fallback."""
    meta = D.instrument(symbol)
    cal_days = int((days + 22) * 1.5) + 5
    try:
        key = meta.get("upstox_key") or meta.get("upstox")
        c = D.cached(f"hist:{symbol}:{cal_days}", 600, lambda: D.upstox_public_history(key, cal_days))
        v = D.cached(f"hist:VIX:{cal_days}", 600, lambda: D.upstox_public_history(C.INDIA_VIX_UPSTOX, cal_days))
        if c is not None and len(c) > 75 * 15:
            return c, (v["close"] if v is not None and len(v) else None), "Upstox history"
    except Exception:
        pass
    c = D.yahoo_candles(meta["yahoo"], period="60d", interval="5m")
    return c, None, "Yahoo (60 days max)"


def run(symbol: str, capital: float = C.DEFAULT_CAPITAL, mult: float = C.NOISE_MULT, candles: pd.DataFrame | None = None,
        vix_intraday: pd.Series | None = None, vix_daily: pd.Series | None = None, lot: int | None = None,
        sizing: str = "risk", otm: int = -C.STRIKE_ITM, strategy: str = "both", days: int | None = None,
        risk_pct: float | None = None, live_rules: bool = True):
    """sizing: "risk" = lots so that 1% of capital is at risk (1 lot allowed up to 1.5%);
    "one_lot" = always 1 lot if the premium fits in the cash (small accounts - much riskier).
    otm: -1 = 1 strike in the money (the rule), 0 = ATM, 1/2 = that many strikes out of the money (cheaper).
    strategy: "noise", "camarilla" or "both" (one open trade per instrument at a time, like the live app), "trend" (trend-day
    catcher, round 23) or "all" (both + trend in its own slot).
    days: only trade the last N sessions (earlier sessions are used to warm up the indicators).
    live_rules (strategy "autopilot" only): replay the trades in time order with the rules live Autopilot applies and a plain
    backtest does not (round 38): trend slot from Rs 40k equity, Fibonacci from Rs 2 lakh, all open premiums together within
    the equity, a 1-lot trade only while it risks <= 15% of equity, the weekly loss limit, and the small-account 1-OTM strike."""
    meta = D.instrument(symbol)
    lot = lot or meta["lot"]
    source = "given"
    if candles is None:
        candles, vix_hist, source = history(symbol, days or 45)
        if vix_intraday is None and vix_daily is None and vix_hist is not None and len(vix_hist) > 100:
            vix_intraday = vix_hist
    if candles is None or candles.empty:
        return {"error": "No historical candles returned."}
    if vix_intraday is None and vix_daily is None:
        vix_intraday, vix_daily = _vix_series()
    df = I.add_indicators(candles)
    frames = [(d, g) for d, g in df.groupby(df.index.date)]
    trade_from = frames[-days][0] if days and days < len(frames) else None
    trades, equity = [], capital
    skips = {"risk": 0, "cash": 0}
    if strategy == "ai":
        return _run_ai(symbol, meta, candles, vix_intraday, vix_daily, lot, capital, sizing, otm, days, risk_pct, frames,
                       trade_from, source, skips)
    step = meta["step"]
    traded_days = 0
    vx = vix_intraday.sort_index() if vix_intraday is not None and len(vix_intraday) else None
    budget_pct = (risk_pct or C.RISK_PER_TRADE * 100) / 100
    size_fn = None
    try:
        from . import store as _st
        conv_on = bool((_st.get("change_flags") or {}).get("conviction", True))
    except Exception:
        conv_on = True
    if strategy == "autopilot" and symbol in C.CONVICTION_SYMS and sizing != "one_lot" and conv_on:   # round 33 conviction sizing
        mtf = S.mtf_frames(candles)
        size_fn = lambda ts, sg: S.conviction_mult(S.mtf_score_at(mtf, ts + timedelta(minutes=C.CANDLE_MIN), sg))  # noqa: E731
    for n, (d, day) in enumerate(frames):
        if n == 0 or len(day) < 60 or (trade_from and d < trade_from):
            continue
        hist = pd.concat([g for _, g in frames[max(0, n - 15):n]])
        sigma = S.noise_sigma(hist)
        if not sigma:
            continue
        traded_days += 1
        bands = S.noise_bands(day, float(hist["close"].iloc[-1]), sigma, mult)

        def vix_at(ts):
            if vx is not None:
                i = vx.index.searchsorted(ts, side="right") - 1
                if i >= 0:
                    return float(vx.iloc[i])
            if vix_daily is not None and len(vix_daily):
                v = vix_daily[vix_daily.index < pd.Timestamp(d)]
                if len(v):
                    return float(v.iloc[-1])
            return 14.0

        if vix_at(day.index[0]) < C.VIX_MIN:
            continue
        day_exps = I.demo_expiries(meta["expiry"], d, 3, meta.get("exchange", "NSE"))
        if d in C.BUDGET_DAYS or (symbol in C.SKIP_OWN_EXPIRY and day_exps and day_exps[0] == d):
            continue                                  # calendar rules (research round 6)
        if meta["iv_mult"]:
            # VIX corrected by the real-option-price table (days to expiry, moneyness)
            iv_of = lambda ts, s=None, k=None, opt=None: PR.iv(symbol, vix_at(ts), s, k, opt, (exp - d).days,  # noqa: E731
                                                               meta["step"], meta["iv_mult"]) if s else \
                min(max(vix_at(ts) / 100 * meta["iv_mult"], 0.06), 0.9)
        else:
            rv = D.realised_vol(hist.tail(375)) or 0.25
            iv_of = lambda ts, s=None, k=None, opt=None: min(max(rv, 0.08), 0.9)  # noqa: E731
        exp = I.pick_trading_expiry(day_exps, meta["expiry"], d)
        idx = list(day.index)
        lv = S.camarilla_levels(hist)
        day_trades = []
        sfn = size_fn if strategy == "autopilot" else None
        if strategy == "autopilot":                       # round 27: exactly what Autopilot trades on this index
            ap = C.AUTOPILOT_SYM_STRATEGIES.get(symbol, C.AUTOPILOT_STRATEGIES)
            main_strats = [x for x in ("noise", "camarilla") if x in ap]
            do_trend = "trend" in ap
            do_fib = "fib" in ap
        else:
            do_fib = strategy == "fib"
            main_strats = ["noise", "camarilla"] if strategy in ("both", "all") else ([] if strategy in ("trend", "fib") else [strategy])
            do_trend = strategy in ("trend", "all")
        if do_trend:                   # round 23: own slot, not blocked by an open Noise / Camarilla trade
            res = _trend_trade(day, idx, bands, exp, iv_of, step, lot, capital, d, sizing, otm, budget_pct, I.exch_rate(symbol),
                               size_fn=sfn)
            if isinstance(res, tuple):
                skips[res[2]] += 1
            elif res:
                trades.append(res)
        if do_fib and vix_at(day.index[0]) <= C.CAM_VIX_MAX:   # round 29: Fibonacci 0.618 breakout, own slot, Camarilla exits
            flv, start = S.fib_levels(hist), 0
            while flv:
                res = _one_trade(day, idx, bands, start, exp, iv_of, step, lot, capital, equity, d, mult, sizing, otm,
                                 "camarilla", flv, budget_pct, I.exch_rate(symbol), size_fn=sfn)
                if res is None:
                    break
                if isinstance(res, tuple):
                    skips[res[2]] += 1
                    start = res[1] + 1
                    continue
                trades.append({**res, "strategy": "Fibonacci 0.618"})
                break
        for strat in main_strats:
            if strat == "camarilla" and (not lv or vix_at(day.index[0]) > C.CAM_VIX_MAX):
                continue
            if strat == "noise" and vix_at(day.index[0]) >= C.NOISE_VIX_MAX:
                continue
            start, day_why = 0, set()
            while True:
                res = _one_trade(day, idx, bands, start, exp, iv_of, step, lot, capital, equity, d, mult, sizing, otm,
                                 strat, lv, budget_pct, I.exch_rate(symbol), size_fn=sfn)
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
    trades.sort(key=lambda t: (t["date"], t["entry_time"]))
    live = None
    if strategy == "autopilot" and live_rules:
        alt = None
        if _flag("small_otm") and otm == -C.STRIKE_ITM and capital < C.SMALL_OTM_BELOW * 3:
            alt = run(symbol, capital, mult, candles, vix_intraday, vix_daily, lot, sizing, C.SMALL_OTM_STRIKES, strategy, days,
                      risk_pct, live_rules=False).get("trades")
            alt = alt.to_dict("records") if isinstance(alt, pd.DataFrame) else alt
        trades, live = _autopilot_replay(trades, capital, lot, alt)
    eq = capital
    for t in trades:
        eq += t["pnl"]
        t["equity"] = round(eq, 0)
    out = _summary(trades, capital, traded_days)
    out["summary"]["skipped"] = skips
    if live is not None:
        out["summary"]["live_rules"] = live
    out["summary"]["sizing"] = sizing
    out["summary"]["source"] = source
    out["summary"]["period"] = [str(frames[-min(days or len(frames), len(frames))][0]), str(frames[-1][0])] if frames else None
    if trades:
        t = pd.DataFrame(trades)
        t["month"] = t["date"].str[:7]
        out["summary"]["by_month"] = [{"month": m, "trades": int(len(g)), "net": float(g.pnl.sum()), "win_rate": float((g.pnl > 0).mean() * 100)}
                                      for m, g in t.groupby("month")]
        out["summary"]["by_strategy"] = [{"strategy": k, "trades": int(len(g)), "net": float(g.pnl.sum()),
                                          "win_rate": float((g.pnl > 0).mean() * 100),
                                          "profit_factor": float(g.pnl[g.pnl > 0].sum() / -g.pnl[g.pnl <= 0].sum()) if (g.pnl <= 0).any() and g.pnl[g.pnl <= 0].sum() < 0 else None}
                                         for k, g in t.groupby("strategy")]
    return out


def _run_ai(symbol, meta, candles, vix_intraday, vix_daily, lot, capital, sizing, otm, days, risk_pct, frames, trade_from,
            source, skips):
    """The AI strategy on the same history and pricing as the rules."""
    if not AI.available():
        return {"error": "AI model file missing on the server."}
    step = meta["step"]
    budget_pct = (risk_pct or C.RISK_PER_TRADE * 100) / 100
    vx = vix_intraday.sort_index() if vix_intraday is not None and len(vix_intraday) else None
    state = {"equity": capital}

    def vix_at(ts, d):
        if vx is not None:
            i = vx.index.searchsorted(ts, side="right") - 1
            if i >= 0:
                return float(vx.iloc[i])
        if vix_daily is not None and len(vix_daily):
            v = vix_daily[vix_daily.index < pd.Timestamp(d)]
            if len(v):
                return float(v.iloc[-1])
        return 14.0

    def exp_for_day(d):
        if d in C.BUDGET_DAYS:
            return None
        return I.pick_trading_expiry(I.demo_expiries(meta["expiry"], d, 3, meta.get("exchange", "NSE")), meta["expiry"], d)

    def price_trade(day, i_in, opt, sl, tgt, exp, i_out, x_spot, reason, best):
        d = day.index[0].date()
        if exp is None or vix_at(day.index[0], d) < C.VIX_MIN:
            return None
        idx = list(day.index)
        ts_in, x_bar = idx[i_in], idx[i_out]
        spot = float(day["close"].iloc[i_in])
        sign = 1 if opt == "CE" else -1
        t_in = ts_in + timedelta(minutes=C.CANDLE_MIN)
        x_ts = x_bar + timedelta(minutes=C.CANDLE_MIN)
        k = round(spot / step) * step + sign * otm * step

        def iv_of(ts, s_, k_, o_):
            return PR.iv(symbol, vix_at(ts, d), s_, k_, o_, (exp - d).days, step, meta["iv_mult"]) if meta["iv_mult"] else 0.25

        def prem(s_, when, ts):
            t = I.years_to_expiry(exp, when.to_pydatetime().replace(tzinfo=None))
            return I.bs_price(s_, k, max(t, 1 / (365 * 24 * 12)), iv_of(ts, s_, k, opt), opt)

        p_in = prem(spot, t_in, ts_in) * (1 + SLIP)
        risk_unit = max(p_in - prem(sl, t_in, ts_in), p_in * 0.05)
        cost = p_in * lot
        if cost > capital:
            return ("skip", i_in, "cash")
        if sizing == "one_lot":
            lots = 1
        else:
            budget = capital * budget_pct
            lots = int(budget // (risk_unit * lot))
            if lots == 0 and risk_unit * lot <= 1.5 * budget:
                lots = 1
            if lots == 0:
                return ("skip", i_in, "risk")
            lots = min(lots, int(capital // cost))
        p_raw = prem(float(x_spot), x_ts, x_bar)
        p_out = PR.real_adjust(p_in / (1 + SLIP), p_raw, (exp - d).days, (x_ts - t_in).total_seconds() / 3600) * (1 - SLIP)
        qty = lots * lot
        pnl = (p_out - p_in) * qty - I.charges(p_in, p_out, qty, exch_pct=I.exch_rate(symbol))
        state["equity"] += pnl
        risk = abs(spot - sl)
        return {"date": d.isoformat(), "entry_time": t_in.strftime("%H:%M"), "exit_time": x_ts.strftime("%H:%M"),
                "side": "CALL" if opt == "CE" else "PUT", "type": "ATM option" if not otm else (f"{otm} OTM option" if otm > 0 else f"{-otm} ITM option"),
                "band": None, "spot_in": round(spot, 1), "spot_out": round(float(x_spot), 1), "R": round(sign * (x_spot - spot) / risk, 2),
                "prem_in": round(p_in, 2), "prem_out": round(p_out, 2), "lots": lots, "pnl": round(pnl, 0), "reason": reason,
                "equity": round(state["equity"], 0), "strategy": "AI model", "exp_r": round(best["exp_r"], 2), "p_win": round(best["p_win"], 2)}

    def log_skip(w):
        skips[w] += 1

    trades = AI.backtest_days(symbol, candles, vix_intraday, vix_daily, trade_from, exp_for_day, price_trade, log_skip)
    cutoff = AI.train_until()
    traded_days = len([1 for d, g in frames if (not trade_from or d >= trade_from) and (not cutoff or d >= cutoff) and len(g) >= 60])
    out = _summary(trades, capital, max(traded_days, 1))
    out["summary"]["train_until"] = cutoff.isoformat() if cutoff else None
    out["summary"]["note"] = (f"The AI model was trained on data up to {cutoff:%d %b %Y}. Only days after that are shown here - "
                              f"earlier days would be in-sample and look far better than they are. {traded_days} such session(s) in this period.") if cutoff else None
    out["summary"]["skipped"] = skips
    out["summary"]["sizing"] = sizing
    out["summary"]["source"] = source
    out["summary"]["period"] = [str(frames[-min(days or len(frames), len(frames))][0]), str(frames[-1][0])] if frames else None
    if trades:
        t = pd.DataFrame(trades)
        t["month"] = t["date"].str[:7]
        out["summary"]["by_month"] = [{"month": m, "trades": int(len(g)), "net": float(g.pnl.sum()), "win_rate": float((g.pnl > 0).mean() * 100)}
                                      for m, g in t.groupby("month")]
        out["summary"]["by_strategy"] = [{"strategy": "AI model", "trades": int(len(t)), "net": float(t.pnl.sum()),
                                          "win_rate": float((t.pnl > 0).mean() * 100),
                                          "profit_factor": float(t.pnl[t.pnl > 0].sum() / -t.pnl[t.pnl <= 0].sum()) if (t.pnl <= 0).any() and t.pnl[t.pnl <= 0].sum() < 0 else None}]
    return out


def _one_trade(day, idx, bands, start, exp, iv_of, step, lot, capital, equity, d, mult, sizing="risk", otm=0,
               strat="noise", lv=None, budget_pct=C.RISK_PER_TRADE, exch_pct=None, size_fn=None):
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
            budget = capital * budget_pct * (size_fn(ts_in, sign) if size_fn else 1.0)   # round 33 conviction (Autopilot)
            lots = int(budget // (risk_unit * lot))
            if lots == 0 and risk_unit * lot <= 1.5 * capital * budget_pct:   # conviction resizes, never skips
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
        pnl = (p_out - p_in) * qty - I.charges(p_in, p_out, qty, exch_pct=exch_pct)
        equity += pnl
        return {
            "date": d.isoformat(), "entry_time": t_in.strftime("%H:%M"), "exit_time": x_ts.strftime("%H:%M"),
            "side": "CALL" if opt == "CE" else "PUT", "type": "ATM option" if not otm else (f"{otm} OTM option" if otm > 0 else f"{-otm} ITM option"), "band": mult,
            "spot_in": round(spot, 1), "spot_out": round(float(x_spot), 1), "R": round(sign * (x_spot - spot) / risk, 2),
            "prem_in": round(p_in, 2), "prem_out": round(p_out, 2), "lots": lots, "pnl": round(pnl, 0),
            "reason": reason, "equity": round(equity, 0), "strategy": "Noise band" if strat == "noise" else "Camarilla",
            "risk_lot": round(risk_unit * lot, 0),
        }


def _flag(name):
    try:
        from . import store as _st
        return bool((_st.get("change_flags") or {}).get(name, True))
    except Exception:
        return True


def _autopilot_replay(trades, capital, lot, alt=None):
    """Round 38: what live Autopilot would actually have taken from these signals, in time order with running equity.
    alt: the same signals priced on the 1-OTM strike, used while equity is below SMALL_OTM_BELOW."""
    alt_of = {(t["date"], t["entry_time"], t["strategy"]): t for t in (alt or [])}
    eq = float(capital); kept, open_ = [], []
    why = {"trend_gate": 0, "fib_gate": 0, "cash_open": 0, "risk15": 0, "weekly": 0, "otm_swaps": 0, "plain_backtest_trades": len(trades)}
    wk = None; wk_pnl = 0.0; wk_start = eq
    for t in trades:
        swapped = False
        if eq < C.SMALL_OTM_BELOW and alt is not None:
            a = alt_of.get((t["date"], t["entry_time"], t["strategy"]))
            if a is not None:
                t, swapped = a, True
        ts_in, ts_out = f'{t["date"]} {t["entry_time"]}', f'{t["date"]} {t["exit_time"]}'
        open_ = [p for p in open_ if p[0] > ts_in]
        w = pd.Timestamp(t["date"]).to_period("W")
        if w != wk:
            wk, wk_pnl, wk_start = w, 0.0, eq
        lim = (C.AUTOPILOT_WEEKLY_SMALL if wk_start < C.AUTOPILOT_SMALL_ACCOUNT else C.MAX_WEEKLY_LOSS) * wk_start
        cost = t["prem_in"] * lot * t["lots"]
        if wk_pnl <= -lim:
            why["weekly"] += 1; continue
        if t["strategy"] == "Trend-day" and eq < C.TREND_MIN_EQUITY:
            why["trend_gate"] += 1; continue
        if t["strategy"].startswith("Fibonacci") and eq < C.FIB_MIN_EQUITY:
            why["fib_gate"] += 1; continue
        if t["lots"] == 1 and t.get("risk_lot") and t["risk_lot"] > C.AUTOPILOT_MAX_TRADE_RISK * eq:
            why["risk15"] += 1; continue
        if cost + sum(p[1] for p in open_) > eq:
            why["cash_open"] += 1; continue
        open_.append((ts_out, cost)); wk_pnl += t["pnl"]; eq += t["pnl"]
        why["otm_swaps"] += swapped
        kept.append(t)
    why["kept"] = len(kept)
    return kept, why


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


def _trend_trade(day, idx, bands, exp, iv_of, step, lot, capital, d, sizing="risk", otm=0, budget_pct=C.RISK_PER_TRADE,
                 exch_pct=None, size_fn=None):
    """Trend-day catcher (research round 23): first half-hour check 10:45-14:15 where S.trend_signal fires; stop = VWAP
    distance + 0.5 ATR (1-3 ATR), stop to entry at +1R, no target, square-off 15:15."""
    entry_i = opt = None
    for i, ts in enumerate(idx):
        end = ts + timedelta(minutes=C.CANDLE_MIN)
        if end.time() < C.TREND_FIRST_CHECK or end.time() > C.TREND_LAST_CHECK or not S._is_check(ts):
            continue
        o = S.trend_signal(day, bands, i)
        if o:
            entry_i, opt = i, o
            break
    if entry_i is None:
        return None
    ts_in = idx[entry_i]
    bar = day.loc[ts_in]
    sign = 1 if opt == "CE" else -1
    spot, a = float(bar["close"]), float(bar["atr"])
    risk = S.trend_risk(bar, a)
    sl = spot - sign * risk
    t_in = ts_in + timedelta(minutes=C.CANDLE_MIN)
    k = round(spot / step) * step + sign * otm * step

    def prem(s, when, ts):
        t = I.years_to_expiry(exp, when.to_pydatetime().replace(tzinfo=None))
        return I.bs_price(s, k, max(t, 1 / (365 * 24 * 12)), iv_of(ts, s, k, opt), opt)

    p_in = prem(spot, t_in, ts_in) * (1 + SLIP)
    risk_unit = max(p_in - prem(sl, t_in, ts_in), p_in * 0.05)
    cost = p_in * lot
    if cost > capital:
        return ("skip", entry_i, "cash")
    if sizing == "one_lot":
        lots = 1
    else:
        budget = capital * budget_pct * (size_fn(ts_in, sign) if size_fn else 1.0)
        lots = int(budget // (risk_unit * lot))
        if lots == 0 and risk_unit * lot <= 1.5 * capital * budget_pct:
            lots = 1
        if lots == 0:
            return ("skip", entry_i, "risk")
        lots = min(lots, int(capital // cost))
    live_sl, reason, x_spot, x_ts, x_bar = sl, None, None, None, None
    for ts in idx[entry_i + 1:]:
        c = day.loc[ts]
        end = ts + timedelta(minutes=C.CANDLE_MIN)
        adverse, favour = (c["low"], c["high"]) if sign > 0 else (c["high"], c["low"])
        if sign * (adverse - live_sl) <= 0:
            reason = "Breakeven stop" if live_sl == spot else "Stop-loss (VWAP)"
            x_spot = c["open"] if sign * (c["open"] - live_sl) < 0 else live_sl
        elif sign * (favour - (spot + sign * C.TREND_BE_R * risk)) >= 0 and sign * (live_sl - spot) < 0:
            live_sl = spot
        if reason is None and end.time() >= C.SQUARE_OFF:
            reason, x_spot = "Square-off", float(c["close"])
        if reason:
            x_ts, x_bar = end, ts
            break
    if reason is None:
        x_bar = idx[-1]
        reason, x_spot, x_ts = "Square-off", float(day["close"].iloc[-1]), idx[-1] + timedelta(minutes=5)
    p_out = PR.real_adjust(p_in / (1 + SLIP), prem(x_spot, x_ts, x_bar), (exp - d).days, (x_ts - t_in).total_seconds() / 3600) * (1 - SLIP)
    qty = lots * lot
    pnl = (p_out - p_in) * qty - I.charges(p_in, p_out, qty, exch_pct=exch_pct)
    return {
        "date": d.isoformat(), "entry_time": t_in.strftime("%H:%M"), "exit_time": x_ts.strftime("%H:%M"),
        "side": "CALL" if opt == "CE" else "PUT", "type": "ATM option" if not otm else (f"{otm} OTM option" if otm > 0 else f"{-otm} ITM option"),
        "band": C.NOISE_MULT, "spot_in": round(spot, 1), "spot_out": round(float(x_spot), 1), "R": round(sign * (x_spot - spot) / risk, 2),
        "prem_in": round(p_in, 2), "prem_out": round(p_out, 2), "lots": lots, "pnl": round(pnl, 0),
        "reason": reason, "equity": 0, "strategy": "Trend-day", "risk_lot": round(risk_unit * lot, 0),
    }
