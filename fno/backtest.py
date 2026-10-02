"""Backtester: replays the exact rulebook on past 5-minute candles so you can see
the strategy's REAL historical win rate, profit factor and drawdown - instead of
trusting anyone's accuracy claim.

Limits (stated honestly):
- Yahoo gives only ~60 days of 5-minute data. That is a small sample.
- News sentiment, PCR and FII flows can't be reconstructed historically for
  free, so the backtest scores only: global markets, opening gap, India VIX,
  VWAP and trend. Use the threshold slider to see how filtering changes results.
- Option prices are modelled with Black-Scholes (IV from India VIX / realised
  vol), plus slippage and charges. Real fills will differ somewhat.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from . import config as C
from . import market as D
from . import indicators as I


def _daily_change(ticker, period="6mo"):
    try:
        d = D.yahoo_daily(ticker, period)["close"].dropna()
        d.index = d.index.tz_localize(None).normalize() if d.index.tz is not None else d.index.normalize()
        return d
    except Exception:
        return pd.Series(dtype=float)


def run(symbol: str, capital: float = C.DEFAULT_CAPITAL, min_score: int = 2, candles: pd.DataFrame | None = None,
        vix_daily: pd.Series | None = None, global_daily: dict | None = None, slippage=0.005, lot: int | None = None):
    meta = D.instrument(symbol)
    lot_override = lot or meta["lot"]
    if candles is None:
        candles = D.yahoo_candles(meta["yahoo"], period="60d", interval="5m")
    if candles.empty:
        return {"error": "No historical candles returned."}
    df = I.add_indicators(candles)
    if vix_daily is None:
        vix_daily = _daily_change(C.INDIA_VIX_YAHOO)
    if global_daily is None:
        global_daily = {n: _daily_change(t) for n, t in
                        {"S&P 500": "^GSPC", "Nasdaq": "^IXIC", "Nikkei 225": "^N225", "Hang Seng": "^HSI"}.items()}
    gchg = pd.DataFrame({k: v.pct_change() * 100 for k, v in global_daily.items() if len(v)})
    vchg = vix_daily.pct_change() * 100

    days = sorted(set(df.index.date))
    trades, equity = [], capital
    prev_close = None
    for d in days:
        day = df[df.index.date == d]
        if prev_close is None or len(day) < 20:
            prev_close = float(day["close"].iloc[-1]) if len(day) else prev_close
            continue
        dts = pd.Timestamp(d)
        # --- context known at the open (no look-ahead) ---
        g_prev = gchg[gchg.index < dts].tail(1)  # US closes before India opens; Asia - use prior session to be safe
        g_avg = float(g_prev.mean(axis=1).iloc[0]) if not g_prev.empty and not g_prev.isna().all(axis=None) else 0.0
        v_prev = vix_daily[vix_daily.index < dts]
        vix_level = float(v_prev.iloc[-1]) if len(v_prev) else 14.0
        vix_chg = float(vchg[vchg.index < dts].iloc[-1]) if len(vchg[vchg.index < dts]) else 0.0
        gap = (day["open"].iloc[0] / prev_close - 1) * 100
        ctx = (1 if g_avg > 0.4 else -1 if g_avg < -0.4 else 0) + (1 if gap > 0.25 else -1 if gap < -0.25 else 0) \
            + (-1 if vix_chg > 5 else 1 if vix_chg < -5 else 0)

        orb = I.opening_range(day)
        prev_close = float(day["close"].iloc[-1])
        if not orb:
            continue
        hi, lo = orb
        mid = (hi + lo) / 2
        width = (hi - lo) / day["open"].iloc[0] * 100
        if not (C.ORB_MIN_PCT <= width <= C.ORB_MAX_PCT):
            continue

        if meta["iv_mult"]:
            iv = vix_level / 100 * meta["iv_mult"]
        else:
            iv = D.realised_vol(df[df.index.date < d].tail(375)) or 0.25
        iv = min(max(iv, 0.08), 0.9)
        exp = I.pick_trading_expiry(I.demo_expiries(meta["expiry"], d, 3), meta["expiry"], d)

        bars = day[(day.index.time >= C.ORB_END)]
        n_trades, losses, i = 0, 0, 0
        idx = list(bars.index)
        while i < len(idx) and n_trades < C.MAX_TRADES_PER_DAY and losses < 2:
            ts = idx[i]
            b = bars.loc[ts]
            bar_end = ts + timedelta(minutes=C.CANDLE_MIN)
            if bar_end.time() > C.LAST_ENTRY:
                break
            vw = 1 if b["close"] > b["vwap"] else -1
            tr = 1 if (b["ema9"] > b["ema21"] and b["st"] > 0) else -1 if (b["ema9"] < b["ema21"] and b["st"] < 0) else 0
            score = ctx + vw + tr
            opt = None
            if b["close"] > hi and vw > 0 and tr > 0 and score >= min_score and vix_chg <= C.VIX_SPIKE_PCT:
                opt = "CE"
            elif b["close"] < lo and vw < 0 and tr < 0 and score <= -min_score:
                opt = "PE"
            if not opt:
                i += 1
                continue
            sign = 1 if opt == "CE" else -1
            spot = float(b["close"])
            a = float(b["atr"])
            risk = min(max(sign * (spot - mid), 0.5 * a), 1.5 * a)
            sl, be_at, tgt = spot - sign * risk, spot + sign * risk, spot + sign * C.RR_TARGET * risk
            half_r = spot + sign * 0.5 * risk
            step = meta["step"]
            k_buy = round(spot / step) * step
            spread = vix_level > C.VIX_SPREAD_LEVEL
            k_sell = k_buy + sign * C.SPREAD_WIDTH_STRIKES * step

            def prem(s, when):
                t = I.years_to_expiry(exp, when.to_pydatetime().replace(tzinfo=None))
                p = I.bs_price(s, k_buy, t, iv, opt)
                if spread:
                    p -= I.bs_price(s, k_sell, t, iv * (1 + 2.5 * abs(k_sell / s - 1)), opt)
                return p

            entry_p = prem(spot, bar_end) * (1 + slippage)
            g = I.bs_greeks(spot, k_buy, I.years_to_expiry(exp, bar_end.to_pydatetime().replace(tzinfo=None)), iv, opt)
            delta = abs(g["delta"])
            if spread:
                delta = max(delta - abs(I.bs_greeks(spot, k_sell, I.years_to_expiry(exp, bar_end.to_pydatetime().replace(tzinfo=None)), iv, opt)["delta"]), 0.1)
            floor = 0.60 if spread else 1 - C.PREMIUM_HARD_SL
            sl_p = max(entry_p - delta * risk, entry_p * floor)
            lot = lot_override
            rpl = (entry_p - sl_p) * lot
            lots = int((equity * C.RISK_PER_TRADE) // rpl) if rpl > 0 else 0
            if lots == 0 and rpl <= 1.5 * equity * C.RISK_PER_TRADE:
                lots = 1
            if lots == 0:
                i += 1
                continue

            # --- walk forward ---
            live_sl, reached, reason, exit_spot, exit_ts = sl, False, None, None, None
            j = i + 1
            while j < len(idx):
                t2 = idx[j]
                c = bars.loc[t2]
                t2_end = t2 + timedelta(minutes=C.CANDLE_MIN)
                adverse = c["low"] if opt == "CE" else c["high"]
                favour = c["high"] if opt == "CE" else c["low"]
                if sign * (adverse - live_sl) <= 0:
                    reason, exit_spot = ("Breakeven stop" if live_sl == spot else "Stop-loss"), live_sl
                elif sign * (favour - tgt) >= 0:
                    reason, exit_spot = "Target 2R", tgt
                if reason:
                    exit_ts = t2_end
                    break
                if sign * (favour - be_at) >= 0:
                    live_sl = spot
                if sign * (favour - half_r) >= 0:
                    reached = True
                mins = (t2_end - bar_end).total_seconds() / 60
                if not reached and mins >= C.TIME_STOP_MIN:
                    reason, exit_spot, exit_ts = "Time stop", float(c["close"]), t2_end
                    break
                if t2_end.time() >= C.SQUARE_OFF:
                    reason, exit_spot, exit_ts = "Square-off", float(c["close"]), t2_end
                    break
                j += 1
            if reason is None:
                last_ts = idx[-1]
                reason, exit_spot, exit_ts = "Square-off", float(bars.loc[last_ts]["close"]), last_ts + timedelta(minutes=5)

            exit_p = prem(exit_spot, exit_ts) * (1 - slippage)
            qty = lots * lot
            gross = (exit_p - entry_p) * qty
            chg = I.charges(entry_p, exit_p, qty, orders=4 if spread else 2)
            pnl = gross - chg
            equity += pnl
            n_trades += 1
            losses += pnl < 0
            trades.append({
                "date": d.isoformat(), "entry_time": bar_end.strftime("%H:%M"), "exit_time": exit_ts.strftime("%H:%M"),
                "side": "CALL" if opt == "CE" else "PUT", "type": "Spread" if spread else "Naked",
                "score": score, "spot_in": round(spot, 1), "spot_out": round(exit_spot, 1),
                "R": round(sign * (exit_spot - spot) / risk, 2), "prem_in": round(entry_p, 2),
                "prem_out": round(exit_p, 2), "lots": lots, "pnl": round(pnl, 0), "reason": reason,
                "equity": round(equity, 0),
            })
            i = j + 1

    t = pd.DataFrame(trades)
    if t.empty:
        return {"trades": t, "summary": {"trades": 0}, "days": len(days)}
    w, l = t[t.pnl > 0], t[t.pnl <= 0]
    eq = np.concatenate([[capital], t["equity"].values])
    peak = np.maximum.accumulate(eq)
    summary = {
        "trades": len(t), "days": len(days), "win_rate": len(w) / len(t) * 100,
        "avg_win": w.pnl.mean() if len(w) else 0, "avg_loss": l.pnl.mean() if len(l) else 0,
        "profit_factor": w.pnl.sum() / -l.pnl.sum() if l.pnl.sum() < 0 else float("inf"),
        "net": t.pnl.sum(), "return_pct": t.pnl.sum() / capital * 100,
        "max_dd_pct": ((eq - peak) / peak).min() * 100, "avg_R": t.R.mean(),
    }
    return {"trades": t, "summary": summary, "days": len(days)}
