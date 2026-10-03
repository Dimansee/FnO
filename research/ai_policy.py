"""Evaluate decision policies on saved out-of-sample predictions (fast: no retraining).
usage: python ai_policy.py [tag]   (reads ai_er_oos{tag}.npy / ai_pw_oos{tag}.npy)"""
import json, os, sys
from datetime import date
import numpy as np
import pandas as pd

os.environ.setdefault("FNO_CALIB", "1")
import engine as E
import ai_train as T
from ai_data import SYMS, STOPS, RR

TAG = sys.argv[1] if len(sys.argv) > 1 else ""
df = pd.read_parquet("ai_rows.parquet")
df["date"] = pd.to_datetime(df["date"]).dt.date
er = np.load(f"ai_er_oos{TAG}.npy")
pw = np.load(f"ai_pw_oos{TAG}.npy")
for s in SYMS:
    for d in E.load_days(s):
        T.DAYS[(s, d.d)] = d
dates = np.array(df["date"])
q = np.array([T.quarter(d) for d in dates])
quarters = sorted(set(q[q >= 20241]))
oos = q >= 20241
uniq = sorted(set(dates))
last120 = uniq[-120]


def summarise(tr, name):
    if not tr:
        print(f"{name:48s} no trades"); return None
    p = np.array([t["pnl"] for t in tr])
    pos, neg = p[p > 0].sum(), -p[p <= 0].sum()
    daily = pd.Series(p, index=[t["date"] for t in tr]).groupby(level=0).sum()
    eq = daily.cumsum().values
    dd = float((eq - np.maximum.accumulate(np.concatenate([[0], eq]))[1:]).min())
    yrs = {y: round(float(sum(t["pnl"] for t in tr if t["date"].year == y))) for y in (2024, 2025, 2026)}
    u = round(float(sum(t["pnl"] for t in tr if t["date"] >= last120)))
    print(f"{name:48s} n{len(p):4d} net ₹{p.sum()/1000:+7.1f}k win {100*(p>0).mean():4.1f}% pf {pos/neg if neg else 99:.2f} "
          f"maxDD ₹{dd/1000:.0f}k  {yrs}  last120 ₹{u/1000:+.1f}k", flush=True)
    return {"name": name, "trades": len(p), "net": round(float(p.sum())), "win": round(float((p > 0).mean() * 100), 1),
            "pf": round(float(pos / neg), 2) if neg else None, "max_dd": round(dd), "years": yrs, "last120": u}


def adj(er_, pw_, min_pw=None, bars15=False, smooth=False):
    e = er_.copy()
    if min_pw is not None:
        e[pw_ < min_pw] = np.nan
    if bars15:
        tod = df["tod"].values
        e[(tod - 555) % 15 != 10] = np.nan                 # only bars ending on the quarter hour
    if smooth:
        prev = np.roll(e, 1, axis=0)
        same_day = np.roll(dates, 1) == dates
        e = np.where(same_day[:, None] & ~np.isnan(prev), (e + prev) / 2, e)
    return e


def run_policy(name, e, thr_fn):
    tr = []
    past = []
    for Q in quarters:
        m = q == Q
        thr = thr_fn(Q, past)
        tr += T.simulate(df, e, thr, T.DAYS, price=True, sel=m)
        past.append(e[m])
    return summarise(tr, name)


def fixed(v):
    return lambda Q, past: v


def quantile(pct, first=0.2):
    def f(Q, past):
        if not past:
            return first
        allp = np.concatenate([x[~np.isnan(x).all(axis=1)].max(axis=1) for x in past])
        return float(np.percentile(allp, 100 - pct)) if len(allp) else first
    return f


if __name__ == "__main__":
    res = []
    wf = json.load(open("ai_walkforward.json")) if os.path.exists("ai_walkforward.json") else None
    if wf:
        res.append(run_policy("validation-picked threshold (as trained)", er, lambda Q, past: wf["thr"][str(Q)]))
    for v in (0.1, 0.2, 0.3, 0.4):
        res.append(run_policy(f"fixed threshold {v}", er, fixed(v)))
    for pct in (2, 5, 10):
        res.append(run_policy(f"top {pct}% of past scores", er, quantile(pct)))
    res.append(run_policy("fixed 0.2 + P(win) ≥ 0.45", adj(er, pw, min_pw=0.45), fixed(0.2)))
    res.append(run_policy("fixed 0.2 + 15-min bars only", adj(er, pw, bars15=True), fixed(0.2)))
    res.append(run_policy("fixed 0.2 + smoothed over 2 bars", adj(er, pw, smooth=True), fixed(0.2)))
    res.append(run_policy("top 5% + P(win) ≥ 0.45 + smoothed", adj(er, pw, min_pw=0.45, smooth=True), quantile(5)))
    json.dump([r for r in res if r], open(f"ai_policies{TAG}.json", "w"), indent=1, default=str)
