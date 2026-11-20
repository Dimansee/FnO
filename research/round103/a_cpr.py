"""Round 103 A: is the wide-CPR skip (round 71) right for every slot? Today (20 Nov) it blocked every trade at every account size.
Round 71 judged Noise / Camarilla / Fibonacci / trend-day trades pooled; the trend-day catcher, the channel slot (r90) and the
round-level slot (r102) were added AFTER r71 and were never tested under the skip."""
import json, sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import r103lib as R3
import numpy as np, pandas as pd
from fno import config as C

T, A, H = R3.T0, R3.A0, R3.H0
W = C.CPR_WIDE
print("CPR_WIDE", W, "CPR_NARROW", C.CPR_NARROW)
# 1. raw trades (1 lot, after costs): by strategy, wide vs not, by year
T = T.copy(); T["wide"] = T.cpr_w > W; T["yr"] = T.date.str[:4]
out = {}
rows = []
for st, g in T.groupby("strategy"):
    for yr, gy in list(g.groupby("yr")) + [("all", g)]:
        for w, gw in gy.groupby("wide"):
            rows.append({"strategy": st, "year": yr, "wide": bool(w), "n": len(gw), "pnl_per_trade": round(gw.pnl.mean()), "R": round(gw.R.mean(), 3),
                         "win": round((gw.pnl > 0).mean(), 3), "total": round(gw.pnl.sum())})
df = pd.DataFrame(rows)
print(df[df.year == "all"].to_string())
df.to_csv("/tmp/r103_cpr_by_strategy.csv", index=False)
out["by_strategy"] = df.to_dict("records")

# 2. portfolio variants: exempt some strategies from the wide-CPR skip
def variant(exempt, how):
    def f(X):
        if X is None: return None
        X = X.copy()
        m = X.strategy.isin(exempt) | X.strategy.str.startswith(tuple(e for e in exempt if e.endswith("*")))
        if how == "none":      # no CPR logic at all for these (no narrow boost either)
            X.loc[m, "cpr_w"] = np.nan
        else:                  # 'mid': on wide days they trade at the middle multiplier (0.85x) instead of being skipped
            X.loc[m & (X.cpr_w > W), "cpr_w"] = (C.CPR_NARROW + W) / 2
        return X
    return f

base = R3.six()
print("TODAY", {k: R3.fmt(v) for k, v in base.items()})
out["today"] = base
VARS = {
    "trend-day exempt (mid)": (["Trend-day"], "mid"),
    "trend-day exempt (none)": (["Trend-day"], "none"),
    "trend + Fibonacci exempt (mid)": (["Trend-day", "Fibonacci 0.618"], "mid"),
    "new slots exempt (channel, round-level) (mid)": (["Channel 15m", "Round-level break"], "mid"),
    "trend + fib + channel + round-level exempt (mid)": (["Trend-day", "Fibonacci 0.618", "Channel 15m", "Round-level break"], "mid"),
    "trend + fib + channel + round-level exempt (none)": (["Trend-day", "Fibonacci 0.618", "Channel 15m", "Round-level break"], "none"),
    "everything exempt (no wide-CPR skip, mid)": (["Noise band", "Camarilla", "Trend-day", "Fibonacci 0.618", "Channel 15m", "Round-level break"], "mid"),
}
print(sorted(T.strategy.unique()))
for name, (ex, how) in VARS.items():
    f = variant(ex, how)
    cells = R3.six(f(T), f(A), f(H))
    b, d = R3.verdict(cells, base)
    fw = {c: (R3.run(c, "fwd", f(T), f(A), f(H)), R3.run(c, "fwd")) for c in (200_000, 20_000, 40_000)}
    print(f"\n{name}: better in {b}/6, fall deeper by up to {d} pts")
    print("   " + " | ".join(f"{k.split('|')[0][:3]}k {k.split('|')[1]} {R3.fmt(v)} vs {R3.fmt(base[k])}" for k, v in cells.items()))
    print("   forward 19Oct-20Nov: " + " | ".join(f"{c//1000}k {R3.fmt(a)} vs today {R3.fmt(b_)}" for c, (a, b_) in fw.items()))
    out[name] = {"cells": cells, "better": b, "deeper": d, "fwd": {str(c): [a, b_] for c, (a, b_) in fw.items()}}
json.dump(out, open("/tmp/r103_a_cpr.json", "w"), indent=1, default=str)
