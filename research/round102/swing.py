"""Round 102b: multi-timeframe EMA swing trades with the 200 EMA (Devansh, 19 Nov 2026).

Everything is decided at the 15:15 close (what is known then) and held for days; one position per index.
Timeframes: daily (closes at 15:15), 1-hour (09:15 ... 14:15 bars, the last one ends 15:15), 15-minute.
Trend gates (long; short is the mirror):
  g200   daily close above the daily 200 EMA
  gD     g200 and daily EMA 20 above EMA 50
  gDH    gD and 1-hour EMA 20 > EMA 50 > EMA 200
  gDHM   gDH and 15-min EMA 20 > EMA 50 > EMA 200 (all three timeframes stacked)
Triggers (on the signal day, candle open/close logic: the day's candle closes in the trade direction):
  pbD    today's low touched the daily EMA 20 (within 0.1%) and the close is back above it
  pbH    an hour today touched the 1-hour EMA 50 and the close is back above the 1-hour EMA 20
  xH     the 1-hour EMA 20 crossed above the 1-hour EMA 50 today
  brk    close above the previous day's high
Exits (checked at each later 15:15 close):
  eD20   close below the daily EMA 20
  eH50   close below the 1-hour EMA 50
  sw2R   stop at the lower of the last two days' lows (on a close), target 2R (on a close)
  t5     sw2R stop, otherwise out after 5 sessions
Every trade is also out after 10 sessions at the latest.
Options: ATM (and 1 ITM) of the first expiry at least 10 days away, so the hold never runs into expiry week.
  model = house model (round 34 IV table, Black-Scholes time value, extra decay per market hour held), 0.5% slippage a fill
  real  = NSE closing prices (bhavcopy, Nifty and Bank Nifty, Oct 2023 - Oct 2026), 1% slippage a fill
Charges as the app. The daily 200 EMA needs 200 sessions, so signals start in Oct 2023.
"""
import sys, os, json, itertools, time as _t
sys.path.insert(0, "/home/claude/dimansee/fno/research/round101")
import lib as L
import numpy as np, pandas as pd
from datetime import datetime, time, date, timedelta
M = L.M; R34 = M.R34
from fno import indicators as I, pricing as PR

SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
MIN_DTE = 10


def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


def frames(sym):
    a = R34.m1(sym)
    a = a[a.index.hour * 60 + a.index.minute < 15 * 60 + 15]          # up to the 15:14 bar -> "close at 15:15"
    d = a.groupby(a.index.normalize()).agg(o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last"))
    # hourly bars anchored 09:15
    hb = a.groupby(a.index.normalize(), group_keys=False).apply(lambda x: x.resample("60min", origin=x.index[0], label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna())
    mb = a.groupby(a.index.normalize(), group_keys=False).apply(lambda x: x.resample("15min", origin=x.index[0], label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna())
    d["e20"], d["e50"], d["e200"] = ema(d.c.values, 20), ema(d.c.values, 50), ema(d.c.values, 200)
    d.loc[d.index[:200], "e200"] = np.nan
    for f, pre in ((hb, "h"), (mb, "m")):
        f["e20"], f["e50"], f["e200"] = ema(f.close.values, 20), ema(f.close.values, 50), ema(f.close.values, 200)
    hb["day"] = hb.index.normalize(); mb["day"] = mb.index.normalize()
    hl = hb.groupby("day").last()                      # hourly values at the 15:15 close
    hb["touch50"] = hb.low <= hb.e50; hb["touch50s"] = hb.high >= hb.e50
    d["h20"], d["h50"], d["h200"] = hl.e20, hl.e50, hl.e200
    d["h_touch50"] = hb.groupby("day").touch50.any(); d["h_touch50s"] = hb.groupby("day").touch50s.any()
    # cross today: state at today's close differs from the state at yesterday's close
    st = (d.h20 > d.h50)
    d["xH_up"] = st & ~st.shift(1, fill_value=True)
    d["xH_dn"] = ~st & st.shift(1, fill_value=False)
    ml = mb.groupby("day").last()
    d["m20"], d["m50"], d["m200"] = ml.e20, ml.e50, ml.e200
    d["ph"], d["pl"] = d.h.shift(1), d.l.shift(1)
    d["low2"], d["high2"] = d.l.rolling(2).min(), d.h.rolling(2).max()
    return d


def entries(d, gate, trig):
    c, o = d.c, d.o
    up = c > d.e200; dn = c < d.e200
    if gate in ("gD", "gDH", "gDHM"):
        up &= d.e20 > d.e50; dn &= d.e20 < d.e50
    if gate in ("gDH", "gDHM"):
        up &= (d.h20 > d.h50) & (d.h50 > d.h200); dn &= (d.h20 < d.h50) & (d.h50 < d.h200)
    if gate == "gDHM":
        up &= (d.m20 > d.m50) & (d.m50 > d.m200); dn &= (d.m20 < d.m50) & (d.m50 < d.m200)
    if trig == "pbD":
        tu = (d.l <= d.e20 * 1.001) & (c > d.e20); td = (d.h >= d.e20 * 0.999) & (c < d.e20)
    elif trig == "pbH":
        tu = d.h_touch50 & (c > d.h20); td = d.h_touch50s & (c < d.h20)
    elif trig == "xH":
        tu = d.xH_up; td = d.xH_dn
    else:
        tu = c > d.ph; td = c < d.pl
    L_ = (up & tu & (c > o)).fillna(False).values; S_ = (dn & td & (c < o)).fillna(False).values
    return L_, S_


def trades(d, gate, trig, ex, maxhold=10):
    L_, S_ = entries(d, gate, trig)
    c = d.c.values; n = len(c); rows = []; i = 0
    while i < n - 1:
        s = 1 if L_[i] else (-1 if S_[i] else 0)
        if s == 0:
            i += 1; continue
        ep = c[i]; stop = d.low2.values[i] if s > 0 else d.high2.values[i]
        if not np.isfinite(stop) or s * (ep - stop) <= 0:
            stop = ep - s * ep * 0.01
        r = abs(ep - stop); j = i + 1; why = "max"
        while j < n:
            cj = c[j]
            if ex == "eD20" and s * (cj - d.e20.values[j]) < 0:
                why = "ema"; break
            if ex == "eH50" and s * (cj - d.h50.values[j]) < 0:
                why = "ema"; break
            if ex in ("sw2R", "t5") and s * (cj - stop) <= 0:
                why = "stop"; break
            if ex == "sw2R" and s * (cj - ep) >= 2 * r:
                why = "tgt"; break
            if ex == "t5" and j - i >= 5:
                why = "time"; break
            if j - i >= maxhold:
                break
            j += 1
        j = min(j, n - 1)
        rows.append({"i": i, "j": j, "d_in": d.index[i], "d_out": d.index[j], "side": s, "ep": ep, "xs": c[j], "sl": stop, "why": why,
                     "hold": j - i})
        i = j + 1 if j > i else i + 1
    return pd.DataFrame(rows)


_EXL = {}


def expiry_for(sym, dd):
    key = (sym, dd)
    if key not in _EXL:
        meta = R34.D.instrument(sym)
        ex = I.demo_expiries(meta["expiry"], dd, 6, meta.get("exchange", "NSE"))
        _EXL[key] = next((e for e in ex if (e - dd).days >= MIN_DTE), ex[-1])
    return _EXL[key]


def model_price(sym, T, itm=0):
    """House model with a far expiry; entry and exit at 15:15."""
    if T.empty:
        return T
    meta = R34.D.instrument(sym); step = meta["step"]; mult = meta["iv_mult"]
    vix = M.vix()
    t_in = pd.to_datetime([x + pd.Timedelta(hours=15, minutes=15) for x in T.d_in])
    t_out = pd.to_datetime([x + pd.Timedelta(hours=15, minutes=15) for x in T.d_out])
    exps = [expiry_for(sym, x.date()) for x in T.d_in]
    exp_dt = pd.to_datetime([datetime.combine(e, time(15, 30)) for e in exps])
    yin = np.maximum((exp_dt - t_in).total_seconds().values, 0) / (365 * 24 * 3600)
    yout = np.maximum((exp_dt - t_out).total_seconds().values, 0) / (365 * 24 * 3600)
    dte = np.array([(e - x.date()).days for e, x in zip(exps, T.d_in)])
    vx_in = vix.reindex(t_in, method="ffill").fillna(14.0).values
    vx_out = vix.reindex(t_out, method="ffill").fillna(14.0).values
    call = T.side.values > 0; s_in = T.ep.values
    k = np.round(s_in / step) * step - np.where(call, 1, -1) * itm * step
    dte_out = np.maximum(dte - (T.d_out - T.d_in).dt.days.values, 1)
    p_in = R34.bs(s_in, k, yin, R34.ivv(sym, vx_in, s_in, k, call, dte, step, mult), call)
    p_out = R34.bs(T["xs"].values, k, yout, R34.ivv(sym, vx_out, T["xs"].values, k, call, dte_out, step, mult), call)
    p_sl = R34.bs(T.sl.values, k, yin, R34.ivv(sym, vx_in, T.sl.values, k, call, dte, step, mult), call)
    mkt_hours = T.hold.values * 6.25
    rate = np.array([next(r for lim, r in PR.EXTRA_DECAY if x <= lim) for x in dte])
    p_out = np.maximum(p_in + PR.CHG_SCALE * (p_out - p_in) - p_in * rate * mkt_hours, 0.05)
    risk = np.maximum(p_in - p_sl, p_in * 0.05)
    rs, _, _ = R34.money(sym, p_in, p_out, risk, 0.005, "fixed", 1)
    T = T.copy(); T["prem_in"], T["prem_out"], T["risk"], T["rs"] = p_in, p_out, risk, rs
    T["lot"] = R34.D.lot_size(sym); T["date"] = T.d_in.dt.strftime("%Y-%m-%d"); T["pts"] = T.side * (T["xs"] - T.ep)
    return T


_CH = {}


def chain(sym):
    if sym not in _CH:
        b = pd.read_csv("/home/claude/dimansee/fno/research/data/OPT_bhavcopy.csv.gz")
        b = b[b.sym == sym]
        _CH[sym] = {k: g for k, g in b.groupby("date")}
    return _CH[sym]


def real_price(sym, T, itm=0):
    """NSE closing prices. Entry at the signal day's close, exit at the exit day's close (same contract)."""
    if sym not in ("NIFTY", "BANKNIFTY") or T.empty:
        return pd.DataFrame()
    ch = chain(sym); step = {"NIFTY": 50, "BANKNIFTY": 100}[sym]
    out = []
    for r in T.itertuples():
        d1, d2 = r.d_in.strftime("%Y-%m-%d"), r.d_out.strftime("%Y-%m-%d")
        g = ch.get(d1)
        if g is None:
            continue
        opt = "CE" if r.side > 0 else "PE"
        e_min = (r.d_in + pd.Timedelta(days=MIN_DTE)).strftime("%Y-%m-%d")
        gg = g[(g.opt == opt) & (g.expiry >= e_min)]
        if gg.empty:
            gg = g[(g.opt == opt) & (g.expiry > d2)]
        if gg.empty:
            continue
        e = gg.expiry.min(); gg = gg[gg.expiry == e]
        k0 = np.round(r.ep / step) * step - (1 if r.side > 0 else -1) * itm * step
        row = gg.iloc[np.argmin(np.abs(gg.strike.values - k0))]
        g2 = ch.get(d2)
        if g2 is None:
            continue
        x = g2[(g2.opt == opt) & (g2.expiry == e) & (g2.strike == row.strike)]
        if x.empty or row.close <= 0:
            continue
        po = float(x.close.iloc[0]); pi = float(row.close)
        rs, _, _ = R34.money(sym, np.array([pi]), np.array([max(po, 0.05)]), np.array([pi * 0.3]), 0.01, "fixed", 1)
        out.append({**r._asdict(), "prem_in": pi, "prem_out": po, "rs": float(rs[0]), "date": d1, "pts": r.side * (r.xs - r.ep)})
    return pd.DataFrame(out)


def twin(T, seed=7):
    T = T.copy(); f = np.random.default_rng(seed).choice([1, -1], len(T)); T["side"] = T.side * f
    T["sl"] = T.ep - T.side * (T.ep - T.sl).abs()
    return T


def main():
    res = {}; keep = {}
    for sym in SYMS:
        t = _t.time(); d = frames(sym); print(sym, "frames", round(_t.time() - t), flush=True)
        for gate, trig, ex in itertools.product(("g200", "gD", "gDH", "gDHM"), ("pbD", "pbH", "xH", "brk"), ("eD20", "eH50", "sw2R", "t5")):
            T = trades(d, gate, trig, ex)
            if len(T) < 8:
                continue
            for itm in (0, 1):
                key = f"{sym}|{gate}|{trig}|{ex}|{'ITM' if itm else 'ATM'}"
                P = model_price(sym, T, itm); W = model_price(sym, twin(T), itm)
                R = real_price(sym, T, itm); RW = real_price(sym, twin(T), itm)
                res[key] = {"model": L.summ(P), "model_twin": L.summ(W), "real": L.summ(R) if len(R) else None,
                            "real_twin": L.summ(RW) if len(RW) else None, "hold": float(T.hold.mean()),
                            "idx_pts": float((T.side * (T["xs"] - T.ep)).mean())}
                keep[key] = P
        json.dump(res, open("/home/claude/r102/out/swing.json", "w"))
        pd.to_pickle(keep, "/home/claude/r102/out/swing_trades.pkl")
    print("done")


if __name__ == "__main__":
    main()
