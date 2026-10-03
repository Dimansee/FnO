import os, sys, json
import numpy as np, pandas as pd
os.environ.setdefault("FNO_CALIB", "1")
import engine as E
import ai_train as T
from ai_data import SYMS, STOPS
tags = sys.argv[1:] or [""]
df = pd.read_parquet("ai_rows.parquet"); df["date"] = pd.to_datetime(df["date"]).dt.date
for s in SYMS:
    for d in E.load_days(s):
        T.DAYS[(s, d.d)] = d
dates = np.array(df["date"]); q = np.array([T.quarter(d) for d in dates]); quarters = sorted(set(q[q >= 20241]))
uniq = sorted(set(dates)); last120 = uniq[-120]
ers = [np.load(f"ai_er_oos{t}.npy") for t in tags]
pws = [np.load(f"ai_pw_oos{t}.npy") for t in tags]
er = np.nanmean(np.stack(ers), axis=0); pw = np.nanmean(np.stack(pws), axis=0)
tod = df["tod"].values; is_check = ((tod + 5) % 30 == 15) & (tod + 5 >= 585)
res = []
def summ(tr, name):
    if not tr: print(f"{name:50s} no trades"); return
    p = np.array([t["pnl"] for t in tr]); pos, neg = p[p > 0].sum(), -p[p <= 0].sum()
    daily = pd.Series(p, index=[t["date"] for t in tr]).groupby(level=0).sum(); eq = daily.cumsum().values
    dd = float((eq - np.maximum.accumulate(np.concatenate([[0], eq]))[1:]).min())
    yrs = {y: round(float(sum(t["pnl"] for t in tr if t["date"].year == y))) for y in (2024, 2025, 2026)}
    u = round(float(sum(t["pnl"] for t in tr if t["date"] >= last120)))
    print(f"{name:50s} n{len(p):4d} net ₹{p.sum()/1000:+7.1f}k win {100*(p>0).mean():4.1f}% pf {pos/neg if neg else 99:.2f} maxDD ₹{dd/1000:.0f}k {yrs} last120 ₹{u/1000:+.1f}k", flush=True)
    res.append({"name": name, "trades": len(p), "net": round(float(p.sum())), "win": round(float((p > 0).mean()*100), 1), "pf": round(float(pos/neg), 2) if neg else None, "max_dd": round(dd), "years": yrs, "last120": u})
def run(name, e, thr):
    summ(sum((T.simulate(df, e, thr, T.DAYS, price=True, sel=(q == Q)) for Q in quarters), []), name)
lab = "+".join(t or "v1" for t in tags)
for thr in (0.2, 0.3, 0.4):
    run(f"[{lab}] fixed {thr}", er, thr)
    e = er.copy(); e[~is_check] = np.nan
    run(f"[{lab}] fixed {thr}, half-hour checks only", e, thr)
for pmin in (0.4, 0.45, 0.5):
    e = np.where(pw >= pmin, pw, np.nan)
    run(f"[{lab}] P(win) >= {pmin} (best combo by P)", e, -9)
    e2 = np.where((pw >= pmin) & (er >= 0.2), er, np.nan)
    run(f"[{lab}] P(win) >= {pmin} and E[R] >= 0.2", e2, -9)
json.dump(res, open(f"ai_policies3_{lab}.json", "w"), indent=1, default=str)
