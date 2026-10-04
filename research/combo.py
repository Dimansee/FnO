"""Round 10 — VWAP + EMA 20/50 + RSI + ADX + volume + ATR + CPR/pivot + OI confirmation, in every combination.

Ingredients (long form; shorts mirrored), each usable as a confirmation filter:
  vwap    close above VWAP
  ema     close > EMA20 > EMA50
  rsi     RSI(14) > 55
  adx     ADX(14) > 20 and +DI > -DI (via Supertrend direction as the DI proxy already in the engine: st == side)
  vol     relative volume >= 1.3 (10-heavyweight 5-min turnover vs its usual level at that time of day)
  atr     volatility expanding: ATR now > 1.1 x ATR 12 bars ago
  cpr     above yesterday's CPR top (TC) for longs / below BC for shorts; CPR narrower than 0.5% of price
  oi      yesterday's put/call OI ratio (nearest expiry, +-4% strikes, NSE bhavcopy) >= 1.0 for longs / <= 1.0 for shorts
Triggers (what fires the entry, then the chosen filters must all be true on that bar):
  ema_x   EMA20 crosses EMA50        vwap_x  close crosses VWAP       cpr_x   close crosses TC / BC
  mom     momentum candle (range > 1.2 ATR, closes in the top/bottom 25%)   state   the first bar where all chosen filters turn true
Votes: trigger 'state' with 'at least k of 8' instead of a fixed subset.
Exits: stop 1.5 x ATR (1-2.5 clip), target 2R; or no target and trail on VWAP (exit when a close crosses back). Square-off 15:15.
Period: 2023-10-03 .. 2026-10-01 (OI data starts Oct 2023); tuned before 2026-04-09, judged on the unseen last 120 days.
"""
import itertools, json, os, sys, time
from datetime import date, timedelta
from multiprocessing import Pool
import numpy as np
import pandas as pd

os.environ.setdefault("FNO_CALIB", "1")
import engine as E
import evr

SYMS = ("NIFTY", "BANKNIFTY")
ING = ("vwap", "ema", "rsi", "adx", "vol", "atr", "cpr", "oi")
START = date(2023, 10, 3)


def oi_table():
    b = pd.read_csv("data/OPT_bhavcopy.csv.gz", parse_dates=["date", "expiry"])
    b["date"], b["expiry"] = b["date"].dt.date, b["expiry"].dt.date
    out = {}
    for (d, sym), g in b.groupby(["date", "sym"]):
        exps = sorted(e for e in g["expiry"].unique() if (e - d).days >= 1)
        if not exps:
            continue
        g = g[g["expiry"] == exps[0]]
        spot = float(g["und"].dropna().iloc[0]) if g["und"].notna().any() else float(g["strike"].median())
        g = g[(g["strike"] / spot - 1).abs() <= 0.04]
        ce, pe = g[g["opt"] == "CE"]["oi"].sum(), g[g["opt"] == "PE"]["oi"].sum()
        if ce > 0:
            out[(sym, d)] = pe / ce
    return out


def load(sym, rel, oi):
    days = evr.load(sym, rel)
    dates = [d.d for d in days]
    for n, d in enumerate(days):
        prev = dates[n - 1] if n else None
        d.pcr = oi.get((sym, prev)) if prev else None
        ph, pl, pc = d.prev_high, d.prev_low, d.prev_close
        pv = (ph + pl + pc) / 3
        bc = (ph + pl) / 2
        d.tc, d.bc = 2 * pv - bc, bc
        if d.tc < d.bc:
            d.tc, d.bc = d.bc, d.tc
        x = d.evr
        x["e20"], x["e50"] = x["EMA20"], x["EMA50"]
    return [d for d in days if d.d >= START]


def cond(d, i, side, name):
    x = d.evr
    c = d.c[i]
    if name == "vwap":
        return side * (c - d.vwap[i]) > 0
    if name == "ema":
        return side * (c - x["e20"][i]) > 0 and side * (x["e20"][i] - x["e50"][i]) > 0
    if name == "rsi":
        return side * (x["rsi"][i] - 50) > 5
    if name == "adx":
        return d.adx[i] > 20 and d.st[i] == side
    if name == "vol":
        return not np.isnan(x["rel"][i]) and x["rel"][i] >= 1.3
    if name == "atr":
        return i >= 12 and d.atr[i] > 1.1 * d.atr[i - 12]
    if name == "cpr":
        return d.cpr_w < 0.5 and (c > d.tc if side > 0 else c < d.bc)
    if name == "oi":
        return d.pcr is not None and (d.pcr >= 1.0 if side > 0 else d.pcr <= 1.0)
    raise ValueError(name)


def s_combo(d, j, p, st):
    x = d.evr
    filt, trig, k = p["cfilters"], p["trigger"], p.get("k")
    for i in range(max(j, 13), len(d.t)):
        if d.t[i] + 5 > 870:
            return None
        if d.t[i] + 5 < 585 or np.isnan(x["e50"][i]) or np.isnan(x["rsi"][i]):
            continue
        for side in (1, -1):
            c0, c1 = d.c[i - 1], d.c[i]
            if trig == "ema_x":
                fired = side * (x["e20"][i - 1] - x["e50"][i - 1]) <= 0 < side * (x["e20"][i] - x["e50"][i])
            elif trig == "vwap_x":
                fired = side * (c0 - d.vwap[i - 1]) <= 0 < side * (c1 - d.vwap[i])
            elif trig == "cpr_x":
                lv = d.tc if side > 0 else d.bc
                fired = side * (c0 - lv) <= 0 < side * (c1 - lv)
            elif trig == "mom":
                rng = d.h[i] - d.l[i]
                pos = (c1 - d.l[i]) / rng if rng > 0 else 0.5
                fired = rng > 1.2 * d.atr[i] and (pos >= 0.75 if side > 0 else pos <= 0.25) and side * (c1 - d.o[i]) > 0
            else:                                              # state: filters newly all true (or k of 8)
                names = ING if k else filt
                now_ok = sum(cond(d, i, side, n) for n in names)
                prev_ok = sum(cond(d, i - 1, side, n) for n in names)
                need = k if k else len(names)
                fired = now_ok >= need > prev_ok
                if fired and not names:
                    fired = False
            if not fired:
                continue
            if trig != "state" and not all(cond(d, i, side, n) for n in filt):
                continue
            a = d.atr[i]
            r = min(max(1.5 * a, a), 2.5 * a)
            stop = d.c[i] - side * r
            tgt = d.c[i] + side * 2.0 * r if p["exit"] == "2R" else None
            return E.Sig(i, side, stop, tgt, "vwap" if p["exit"] == "vwap" else "", 0, 0, "combo")
    return None


E.STRATS["combo"] = s_combo
E.simulate = evr._orig_sim


def ev(tr, dates, split):
    p = np.array([t["pnl"] for t in tr]) if tr else np.zeros(0)
    tr_ = [t for t in tr if t["date"] < split]; te = [t for t in tr if t["date"] >= split]
    pos, neg = p[p > 0].sum(), -p[p <= 0].sum()
    return {"n": len(tr), "train": round(sum(t["pnl"] for t in tr_)), "unseen120": round(sum(t["pnl"] for t in te)), "n_unseen": len(te),
            "win": round(float((p > 0).mean() * 100), 1) if len(p) else 0, "pf": round(float(pos / neg), 2) if neg else None,
            "years": {y: round(sum(t["pnl"] for t in tr if t["date"].year == y)) for y in (2023, 2024, 2025, 2026)}}


D, DATES, SPLIT = {}, [], None


def init():
    global D, DATES, SPLIT
    rel = evr.volume_proxy()
    oi = oi_table()
    D = {s: load(s, rel, oi) for s in SYMS}
    DATES = [d.d for d in D["NIFTY"]]
    SPLIT = DATES[-120]


def one(p):
    base = dict(max_trades=2, vix_min=11, otm=-1)
    rec = {"params": {"trigger": p["trigger"], "filters": sorted(p["cfilters"]), "k": p.get("k"), "exit": p["exit"]}}
    both = []
    for s in SYMS:
        tr = E.run(D[s], s, "combo", {**base, **p})
        rec[s] = ev(tr, DATES, SPLIT)
        both += tr
    rb = ev(both, DATES, SPLIT)
    rec["both"] = rb
    half = sum(t["pnl"] for t in both if t["date"] < DATES[len(DATES) // 2])
    rec["robust"] = all(rec[s]["train"] > 0 for s in SYMS) and half > 0 and (rb["train"] - half) > 0 and all(rb["years"][y] > 0 for y in (2024, 2025))
    return rec


if __name__ == "__main__":
    t0 = time.time()
    init()
    print("days", len(DATES), DATES[0], "->", DATES[-1], flush=True)
    jobs = []
    for trig in ("ema_x", "vwap_x", "cpr_x", "mom", "state"):
        for r in range(0, 9):
            for sub in itertools.combinations(ING, r):
                if trig == "state" and r == 0:
                    continue
                for ex in ("2R", "vwap"):
                    jobs.append({"trigger": trig, "cfilters": frozenset(sub), "exit": ex})
    for k in range(2, 9):
        for ex in ("2R", "vwap"):
            jobs.append({"trigger": "state", "cfilters": frozenset(), "k": k, "exit": ex})
    print("runs", len(jobs), flush=True)
    with Pool(2, initializer=init) as pool:
        rows = []
        for n, rec in enumerate(pool.imap_unordered(one, jobs, chunksize=8)):
            rows.append(rec)
            if n % 200 == 0:
                print(n, round(time.time() - t0), "s", flush=True)
    json.dump(rows, open("combo_results.json", "w"), default=str)
    df = pd.DataFrame([{"trigger": r["params"]["trigger"], "filters": "+".join(r["params"]["filters"]) or "(none)", "nf": len(r["params"]["filters"]),
                        "k": r["params"]["k"], "exit": r["params"]["exit"], "robust": r["robust"],
                        **{f"b_{k}": v for k, v in r["both"].items() if k != "years"},
                        **{f"y{y}": r["both"]["years"][y] for y in (2024, 2025, 2026)}} for r in rows])
    df.to_pickle("combo_results.pkl")
    print("\nruns", len(df), "| profitable on training:", int((df.b_train > 0).sum()), "| robust:", int(df.robust.sum()),
          "| robust and profitable unseen:", int((df.robust & (df.b_unseen120 > 0)).sum()))
    cols = ["trigger", "filters", "k", "exit", "b_n", "b_train", "b_unseen120", "b_pf", "b_win", "y2024", "y2025", "y2026"]
    print("\ntop 12 by training profit:"); print(df.sort_values("b_train", ascending=False).head(12)[cols].to_string(index=False))
    print("\nrobust ones:"); print(df[df.robust].sort_values("b_train", ascending=False)[cols].to_string(index=False))
    print("\nby trigger:"); print(df.groupby("trigger").b_train.agg(["median", lambda x: round((x > 0).mean() * 100)]).round(0).to_string())
    print("\nby number of confirmations:"); print(df[df.k.isna()].groupby("nf").agg(median=("b_train", "median"), profitable_pct=("b_train", lambda x: round((x > 0).mean() * 100)), trades=("b_n", "median")).round(0).to_string())
    print("\neach ingredient: median training profit with it vs without (same trigger/exit):")
    for ing in ING:
        w = df[df.k.isna() & df.filters.str.contains(ing)]; wo = df[df.k.isna() & ~df.filters.str.contains(ing)]
        print(f"  {ing:5s} with ₹{w.b_train.median()/1000:+7.1f}k ({round((w.b_train>0).mean()*100)}% profitable, median {int(w.b_n.median())} trades) | without ₹{wo.b_train.median()/1000:+7.1f}k ({round((wo.b_train>0).mean()*100)}%)")
    print("\nvotes (at least k of 8):"); print(df[df.k.notna()][cols].to_string(index=False))
    print("total", round(time.time() - t0), "s")
