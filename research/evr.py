"""Round 9 — EMA + volume + RSI combinations (the way creators teach them), tested like everything else:
option P&L with corrected premiums and costs, tuned on 2023-01 .. 2026-04-09, judged on the unseen last 120 days.

Volume: the index has none, so the proxy is the 5-minute rupee turnover of the 10 Nifty heavyweights (about half the
index weight), compared with the usual turnover at that time of day over the previous 20 sessions ("relative volume").

Entry families (long shown; short mirrored), all requiring an RSI and a volume condition:
  cross   fast EMA crosses above slow EMA on this candle
  stack   price above fast EMA, fast above slow (and above the 3rd EMA if given), after being below the fast EMA
  pullback price in an uptrend (fast > slow) touches / dips to the fast EMA and closes back above it
  bounce  price above EMA-fast and above the 200 EMA; RSI turns up from below 50
EMA types: EMA, SMA, DEMA, TEMA, HMA.  RSI: > 50 / > 55 / > 60, or RSI crossing 50 / 60.  Volume: relative volume >= 1.0 /
1.3 / 1.6 / 2.0 (or none).  Exits: stop 1.5-2 ATR, target 2R/3R or trailing on the fast EMA, 15:15 square-off.
"""
import itertools, json, os, sys, time
from datetime import date, timedelta
import numpy as np
import pandas as pd

os.environ.setdefault("FNO_CALIB", "1")
import engine as E
from fno import indicators as I

SYMS = ("NIFTY", "BANKNIFTY")
STOCKS = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK", "BHARTIARTL", "LT", "ITC"]


# ---------------------------------------------------------------- moving averages
def _ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def ma(s: pd.Series, n: int, kind: str) -> pd.Series:
    if kind == "EMA":
        return _ema(s, n)
    if kind == "SMA":
        return s.rolling(n).mean()
    if kind == "DEMA":
        e = _ema(s, n)
        return 2 * e - _ema(e, n)
    if kind == "TEMA":
        e1 = _ema(s, n); e2 = _ema(e1, n); e3 = _ema(e2, n)
        return 3 * e1 - 3 * e2 + e3
    if kind == "HMA":
        w = lambda x, m: x.rolling(m).apply(lambda v: np.dot(v, np.arange(1, m + 1)) / (m * (m + 1) / 2), raw=True)  # noqa: E731
        return w(2 * w(s, max(n // 2, 1)) - w(s, n), max(int(np.sqrt(n)), 1))
    raise ValueError(kind)


# ---------------------------------------------------------------- volume proxy
def volume_proxy() -> pd.Series:
    tot = None
    for s in STOCKS:
        d = pd.read_csv(f"data/{s}_5m_upstox.csv.gz", parse_dates=["ts"]).set_index("ts").sort_index()
        v = d["close"] * d["volume"]
        tot = v if tot is None else tot.add(v, fill_value=0)
    tot = tot[(tot.index.hour * 60 + tot.index.minute >= 555) & (tot.index.hour * 60 + tot.index.minute <= 925)]
    day = tot.index.date
    tod = tot.index.hour * 60 + tot.index.minute
    piv = pd.DataFrame({"v": tot.values, "d": day, "t": tod}).pivot_table(index="d", columns="t", values="v")
    usual = piv.rolling(20, min_periods=10).mean().shift(1)
    rel = pd.Series([float(piv.loc[d_, t_] / usual.loc[d_, t_]) if (d_ in usual.index and t_ in usual.columns and usual.loc[d_, t_] > 0) else np.nan
                     for d_, t_ in zip(day, tod)], index=tot.index)
    return rel


# ---------------------------------------------------------------- attach to Day objects
def load(sym, rel):
    days = E.load_days(sym)
    df = E._read(f"{sym}_5m_upstox")
    df = df[(df.index.time >= pd.Timestamp("09:15").time()) & (df.index.time <= pd.Timestamp("15:25").time())]
    c = df["close"]
    cols = {}
    for kind in ("EMA", "SMA", "DEMA", "TEMA", "HMA"):
        for n in (5, 8, 9, 13, 20, 21, 34, 50, 200):
            cols[f"{kind}{n}"] = ma(c, n, kind)
    cols["rsi"] = E._rsi(c)
    cols["rsi9"] = E._rsi(c, 9)
    cols["rel"] = rel.reindex(df.index)
    X = pd.DataFrame(cols, index=df.index)
    by_day = {d_: g for d_, g in X.groupby(X.index.date)}
    for d in days:
        g = by_day.get(d.d)
        if g is None or len(g) != len(d.t):
            d.evr = None
            continue
        d.evr = {k: g[k].values.astype(float) for k in g.columns}
    return [d for d in days if d.evr is not None]


# ---------------------------------------------------------------- the strategy
def s_evr(d: E.Day, j, p, st):
    x = d.evr
    f, s_, kind = p["fast"], p["slow"], p["kind"]
    F, S = x[f"{kind}{f}"], x[f"{kind}{s_}"]
    L = x[f"{kind}{p['third']}"] if p.get("third") else None
    rsi = x["rsi9"] if p.get("rsi_n") == 9 else x["rsi"]
    rel = x["rel"]
    for i in range(max(j, 3), len(d.t)):
        if d.t[i] + 5 > p.get("last_entry", 870):
            return None
        if d.t[i] + 5 < p.get("first_entry", 585):
            continue
        if np.isnan(F[i]) or np.isnan(S[i]) or np.isnan(rsi[i]):
            continue
        for side in (1, -1):
            c0, c1 = d.c[i - 1], d.c[i]
            mode = p["mode"]
            ok = False
            if mode == "cross":
                ok = side * (F[i - 1] - S[i - 1]) <= 0 < side * (F[i] - S[i])
            elif mode == "stack":
                ok = side * (c1 - F[i]) > 0 and side * (F[i] - S[i]) > 0 and side * (c0 - F[i - 1]) <= 0
                if L is not None:
                    ok = ok and side * (S[i] - L[i]) > 0
            elif mode == "pullback":
                touched = (d.l[i] <= F[i] if side > 0 else d.h[i] >= F[i])
                ok = side * (F[i] - S[i]) > 0 and touched and side * (c1 - F[i]) > 0 and side * (c0 - F[i - 1]) > 0
            elif mode == "bounce":
                ok = side * (c1 - F[i]) > 0 and (L is None or side * (c1 - L[i]) > 0) and \
                     side * (rsi[i] - 50) > 0 >= side * (rsi[i - 1] - 50)
            if not ok:
                continue
            # RSI condition
            rc = p["rsi"]
            if rc == "gt50" and side * (rsi[i] - 50) <= 0:
                continue
            if rc == "gt55" and side * (rsi[i] - 50) <= 5:
                continue
            if rc == "gt60" and side * (rsi[i] - 50) <= 10:
                continue
            if rc == "x50" and not (side * (rsi[i - 1] - 50) <= 0 < side * (rsi[i] - 50)):
                continue
            if rc == "x60" and not (side * (rsi[i - 1] - 50) <= 10 < side * (rsi[i] - 50)):
                continue
            # volume condition
            vmin = p.get("vol", 0)
            if vmin and (np.isnan(rel[i]) or rel[i] < vmin):
                continue
            if p.get("vwap", 0) and side * (d.c[i] - d.vwap[i]) <= 0:
                continue
            stop = _risk(d, i, side, p)
            r = abs(d.c[i] - stop)
            tgt = d.c[i] + side * p["rr"] * r if p.get("rr") else None
            st[("evr_fast", d.d)] = (kind, f)
            return E.Sig(i, side, stop, tgt, p.get("trail", ""), p.get("be", 0), p.get("tstop", 0), "evr")
    return None


def _risk(d, i, side, p):
    a = d.atr[i]
    r = min(max(p.get("stop_atr", 1.5) * a, 1.0 * a), 2.5 * a)
    return d.c[i] - side * r


# trailing on the fast EMA: patch engine.simulate's "ema" trail
_orig_sim = E.simulate


def simulate_evr(d, s, meta, p, upper=None, lower=None):
    if s.trail != "ema":
        return _orig_sim(d, s, meta, p, upper, lower)
    kind, f = p["kind"], p["fast"]
    F = d.evr[f"{kind}{f}"]
    side, entry = s.side, d.c[s.i]
    stop = s.stop
    for k in range(s.i + 1, len(d.t)):
        hi, lo, cl, op = d.h[k], d.l[k], d.c[k], d.o[k]
        adverse, favour = (lo, hi) if side > 0 else (hi, lo)
        if side * (adverse - stop) <= 0:
            return k, (op if side * (op - stop) < 0 else stop), "stop"
        if s.target is not None and side * (favour - s.target) >= 0:
            return k, (op if side * (op - s.target) > 0 else s.target), "target"
        if side * (cl - F[k]) < 0 and k > s.i + 1:
            return k, cl, "ema exit"
        if d.t[k] + 5 >= E.SQUARE_OFF:
            return k, cl, "square-off"
    return len(d.t) - 1, d.c[-1], "square-off"


E.simulate = simulate_evr
E.STRATS["evr"] = s_evr

GRID = []
for kind in ("EMA", "SMA", "DEMA", "TEMA", "HMA"):
    for fast, slow, third in ((5, 13, None), (9, 21, None), (9, 21, 50), (8, 34, None), (20, 50, None), (5, 20, 200), (13, 50, 200)):
        for mode in ("cross", "stack", "pullback", "bounce"):
            if mode == "bounce" and third is None:
                continue
            for rsi in ("gt50", "gt55", "gt60", "x50", "x60"):
                for vol in (0, 1.0, 1.3, 1.6, 2.0):
                    for rr, trail in ((2.0, ""), (3.0, ""), (None, "ema")):
                        GRID.append(dict(kind=kind, fast=fast, slow=slow, third=third, mode=mode, rsi=rsi, vol=vol, rr=rr, trail=trail,
                                         stop_atr=1.5, be=0, tstop=0, max_trades=2, vix_min=11, otm=-1, last_entry=870, first_entry=585))
rng = np.random.default_rng(5)
if len(GRID) > 1200:
    GRID = [GRID[i] for i in sorted(rng.choice(len(GRID), 1200, replace=False))]


def ev(tr, dates, split):
    p = np.array([t["pnl"] for t in tr]) if tr else np.zeros(0)
    tr_ = [t for t in tr if t["date"] < split]; te = [t for t in tr if t["date"] >= split]
    pos, neg = p[p > 0].sum(), -p[p <= 0].sum()
    return {"n": len(tr), "train": round(sum(t["pnl"] for t in tr_)), "unseen120": round(sum(t["pnl"] for t in te)),
            "n_unseen": len(te), "win": round(float((p > 0).mean() * 100), 1) if len(p) else 0, "pf": round(float(pos / neg), 2) if neg else None,
            "years": {y: round(sum(t["pnl"] for t in tr if t["date"].year == y)) for y in (2023, 2024, 2025, 2026)}}


if __name__ == "__main__":
    t0 = time.time()
    rel = volume_proxy()
    D = {s: load(s, rel) for s in SYMS}
    dates = [d.d for d in D["NIFTY"]]
    split = dates[-120]
    print("days", len(dates), "settings", len(GRID), "volume proxy coverage", round(rel.notna().mean() * 100), "%", flush=True)
    rows = []
    for n, p in enumerate(GRID):
        rec = {"params": {k: p[k] for k in ("kind", "fast", "slow", "third", "mode", "rsi", "vol", "rr", "trail")}}
        tot_tr, ok = 0, True
        both = []
        for s in SYMS:
            tr = E.run(D[s], s, "evr", p)
            r = ev(tr, dates, split)
            rec[s] = r
            both += tr
            if r["train"] <= 0:
                ok = False
        rb = ev(both, dates, split)
        rec["both"] = rb
        half = sum(t["pnl"] for t in both if t["date"] < dates[len(dates) // 2])
        rec["robust"] = ok and half > 0 and (rb["train"] - half) > 0 and all(rb["years"][y] > 0 for y in (2023, 2024, 2025))
        rows.append(rec)
        if n % 100 == 0:
            print(n, round(time.time() - t0), "s", flush=True)
    json.dump(rows, open("evr_results.json", "w"), default=str)
    df = pd.DataFrame([{**r["params"], **{f"b_{k}": v for k, v in r["both"].items() if k != "years"}, "robust": r["robust"],
                        "y2023": r["both"]["years"][2023], "y2024": r["both"]["years"][2024], "y2025": r["both"]["years"][2025], "y2026": r["both"]["years"][2026]} for r in rows])
    df.to_pickle("evr_results.pkl")
    print("\nprofitable on training after costs:", int((df.b_train > 0).sum()), "of", len(df), "| robust (both indices, both halves, every year):", int(df.robust.sum()))
    print("robust and profitable on the unseen 120 days:", int((df.robust & (df.b_unseen120 > 0)).sum()))
    print("\ntop 10 by training profit:")
    print(df.sort_values("b_train", ascending=False).head(10)[["kind", "fast", "slow", "third", "mode", "rsi", "vol", "rr", "trail", "b_n", "b_train", "b_unseen120", "b_pf", "b_win", "y2023", "y2024", "y2025", "y2026"]].to_string(index=False))
    print("\nby volume filter (median training profit / share profitable):")
    print(df.groupby("vol").b_train.agg(["median", lambda x: round((x > 0).mean() * 100)]).round(0).to_string())
    print("\nby EMA type:"); print(df.groupby("kind").b_train.agg(["median", lambda x: round((x > 0).mean() * 100)]).round(0).to_string())
    print("\nby RSI rule:"); print(df.groupby("rsi").b_train.agg(["median", lambda x: round((x > 0).mean() * 100)]).round(0).to_string())
    print("\nby entry mode:"); print(df.groupby("mode").b_train.agg(["median", lambda x: round((x > 0).mean() * 100)]).round(0).to_string())
    print("total", round(time.time() - t0), "s")
