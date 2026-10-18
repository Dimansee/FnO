"""Round 30 engine: EMA crossover / MACD / volume / ADX / VWAP / Supertrend / Donchian entries on 5- or 15-minute bars, exits on
5-minute bars, the option priced exactly like the app's backtester (1 strike ITM, calibrated Black-Scholes, real-price decay
adjustment, 0.5% slippage per fill, 2026 charges), 1 lot; P&L also scaled to Rs 2,000 risk a trade ("u")."""
import os, sys
from datetime import timedelta, time
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import round26_harness as H
from fno import indicators as I, config as C, pricing as PR, backtest as B
SLIP = B.SLIP
END = "2026-10-09"          # the heavyweight-turnover volume proxy ends here (research-data stock files)

def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def adx(df, n=14):
    h, l, c = df.high, df.low, df.close
    up, dn = h.diff(), -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0); ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    at = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(pdm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / at
    ndi = 100 * pd.Series(ndm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / at
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()

def volume_proxy():
    """Relative 5-min turnover of the 10 Nifty heavyweights vs the same time of day over the previous 20 sessions (round 9)."""
    import evr
    cwd = os.getcwd(); os.chdir(HERE)
    try:
        return evr.volume_proxy()
    finally:
        os.chdir(cwd)

def frame(sym, tf, rel):
    """Indicators on the signal timeframe, aligned to the 5-minute bar on which that signal bar closes."""
    c5 = H.load(sym); c5 = c5[c5.index < pd.Timestamp(END) + pd.Timedelta(days=1)]
    d5 = I.add_indicators(c5)
    if tf == 5:
        s = c5.copy()
    else:
        s = c5.resample(f"{tf}min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        s = s[[t.time() >= time(9, 15) and t.time() <= time(15, 25) for t in s.index]]
    x = pd.DataFrame(index=s.index)
    for n in (5, 9, 13, 20, 21, 50): x[f"e{n}"] = ema(s.close, n)
    m = ema(s.close, 12) - ema(s.close, 26); sig = ema(m, 9)
    x["macd"], x["msig"], x["hist"] = m, sig, m - sig
    x["adx"] = adx(s); x["adx_up"] = x["adx"] > x["adx"].shift(1)
    x["st"] = I.supertrend(s.assign(volume=0)) if hasattr(I, "supertrend") else np.nan
    x["dch"], x["dcl"] = s.high.rolling(20).max().shift(1), s.low.rolling(20).min().shift(1)
    x["close"] = s.close
    v = rel.reindex(c5.index)
    x["rel"] = v.rolling(tf // 5, min_periods=1).mean().reindex(s.index, method="ffill") if tf > 5 else v.reindex(s.index)
    # map each signal bar to the 5-minute bar where it closes
    x.index = [t + pd.Timedelta(minutes=tf - 5) for t in x.index]
    return d5, x

def price_trade(sym, d, day, i_in, i_out, x_spot, side, sl, vix, meta):
    idx = list(day.index); step = meta["step"]; lot = meta["lot"]
    ts_in = idx[i_in]; spot = float(day.close.iloc[i_in]); sign = 1 if side == "CE" else -1
    exp = I.pick_trading_expiry(I.demo_expiries(meta["expiry"], d, 3, meta.get("exchange", "NSE")), meta["expiry"], d)
    def vix_at(ts):
        k = vix.index.searchsorted(ts, side="right") - 1; return float(vix.iloc[k]) if k >= 0 else 14.0
    k = round(spot / step) * step - sign * C.STRIKE_ITM * step
    def prem(s, when, ts):
        t = I.years_to_expiry(exp, when.to_pydatetime().replace(tzinfo=None))
        return I.bs_price(s, k, max(t, 1 / (365 * 24 * 12)), PR.iv(sym, vix_at(ts), s, k, side, (exp - d).days, step, meta["iv_mult"]), side)
    t_in = ts_in + timedelta(minutes=5)
    p_in = prem(spot, t_in, ts_in) * (1 + SLIP)
    ru = max(p_in - prem(sl, t_in, ts_in), p_in * 0.05)
    x_bar = idx[i_out]; x_ts = x_bar + timedelta(minutes=5)
    p_out = PR.real_adjust(p_in / (1 + SLIP), prem(x_spot, x_ts, x_bar), (exp - d).days, (x_ts - t_in).total_seconds() / 3600) * (1 - SLIP)
    pnl = (p_out - p_in) * lot - I.charges(p_in, p_out, lot, exch_pct=I.exch_rate(sym))
    return pnl, ru * lot

def run(sym, d5, x, vix, P):
    """P: entry (ema:f,s | macd | macd0 | emamacd:f,s | st | donch), tf, vol (None or threshold), adx (bool), vwap (bool),
    exit (opp | trail | hold | t2), max_trades."""
    meta = B.D.instrument(sym)
    ent = P["entry"]
    if ent.startswith("ema") and ":" in ent:
        f, s_ = ent.split(":")[1].split(","); fa, sl_ = x[f"e{f}"], x[f"e{s_}"]
        long_sig = (fa > sl_) & (fa.shift(1) <= sl_.shift(1)); short_sig = (fa < sl_) & (fa.shift(1) >= sl_.shift(1))
        if ent.startswith("emamacd"):
            long_sig &= x["hist"] > 0; short_sig &= x["hist"] < 0
        trend_up, trend_dn = fa > sl_, fa < sl_; slow = sl_
    elif ent == "macd":
        long_sig = (x["hist"] > 0) & (x["hist"].shift(1) <= 0); short_sig = (x["hist"] < 0) & (x["hist"].shift(1) >= 0)
        trend_up, trend_dn = x["hist"] > 0, x["hist"] < 0; slow = x.e21
    elif ent == "macd0":
        long_sig = (x.macd > 0) & (x.macd.shift(1) <= 0); short_sig = (x.macd < 0) & (x.macd.shift(1) >= 0)
        trend_up, trend_dn = x.macd > 0, x.macd < 0; slow = x.e21
    elif ent == "donch":
        long_sig = (x.close > x.dch) & (x.close.shift(1) <= x.dch.shift(1)); short_sig = (x.close < x.dcl) & (x.close.shift(1) >= x.dcl.shift(1))
        trend_up, trend_dn = x.close > x.e21, x.close < x.e21; slow = x.e21
    else:
        raise ValueError(ent)
    if P.get("vol"):
        ok = x.rel >= P["vol"]; long_sig &= ok; short_sig &= ok
    if P.get("adx"):
        ok = (x.adx >= 20) & x.adx_up; long_sig &= ok; short_sig &= ok
    sig = pd.Series(0, index=x.index); sig[long_sig.fillna(False)] = 1; sig[short_sig.fillna(False)] = -1
    sig5 = sig.reindex(d5.index).fillna(0).astype(int)
    up5 = trend_up.reindex(d5.index).ffill().fillna(False); dn5 = trend_dn.reindex(d5.index).ffill().fillna(False)
    slow5 = slow.reindex(d5.index).ffill()
    is_tf_close = pd.Series(d5.index.isin(x.index), index=d5.index)
    out = []
    for d, day in d5.groupby(d5.index.date):
        if len(day) < 60 or d in C.BUDGET_DAYS: continue
        k0 = vix.index.searchsorted(day.index[0], side="right") - 1
        v0 = float(vix.iloc[k0]) if k0 >= 0 else 14.0
        if v0 < C.VIX_MIN: continue
        idx = list(day.index); n = 0; i = 0
        s_day = sig5.loc[day.index].values; vw = day.vwap.values
        while i < len(idx) and n < P.get("max_trades", 2):
            t_end = (idx[i] + timedelta(minutes=5)).time()
            if t_end < time(9, 30) or t_end > time(14, 30) or s_day[i] == 0:
                i += 1; continue
            side = 1 if s_day[i] > 0 else -1; c0 = float(day.close.iloc[i])
            if P.get("vwap") and side * (c0 - vw[i]) <= 0:
                i += 1; continue
            a = float(day.atr.iloc[i]); stop_k = 2.0 if P["exit"] in ("opp", "hold") else 1.5
            risk = stop_k * a; sl = c0 - side * risk; live = sl; tgt = c0 + side * 2 * risk if P["exit"] == "t2" else None
            j_exit, x_spot = None, None
            for j in range(i + 1, len(idx)):
                b = day.iloc[j]; adv, fav = (b.low, b.high) if side > 0 else (b.high, b.low)
                if side * (adv - live) <= 0:
                    j_exit, x_spot = j, (b.open if side * (b.open - live) < 0 else live); break
                if tgt is not None and side * (fav - tgt) >= 0:
                    j_exit, x_spot = j, (b.open if side * (b.open - tgt) > 0 else tgt); break
                if P["exit"] == "hold" and side * (fav - (c0 + side * risk)) >= 0 and side * (live - c0) < 0:
                    live = c0
                if is_tf_close.iloc[day.index.get_loc(idx[j]) + d5.index.get_loc(idx[0])] if False else (idx[j] in x.index):
                    if P["exit"] == "opp" and ((side > 0 and dn5.loc[idx[j]]) or (side < 0 and up5.loc[idx[j]])):
                        j_exit, x_spot = j, float(b.close); break
                    if P["exit"] == "trail" and side * (float(b.close) - float(slow5.loc[idx[j]])) < 0:
                        j_exit, x_spot = j, float(b.close); break
                if (idx[j] + timedelta(minutes=5)).time() >= C.SQUARE_OFF:
                    j_exit, x_spot = j, float(b.close); break
            if j_exit is None:
                j_exit, x_spot = len(idx) - 1, float(day.close.iloc[-1])
            pnl, rl = price_trade(sym, d, day, i, j_exit, float(x_spot), "CE" if side > 0 else "PE", sl, vix, meta)
            out.append({"sym": sym, "date": str(d), "entry": idx[i].strftime("%H:%M"), "exit": idx[j_exit].strftime("%H:%M"), "side": side,
                        "R": side * (float(x_spot) - c0) / risk, "pnl": pnl, "u": pnl / rl * 2000})
            n += 1; i = j_exit + 1
    return out
