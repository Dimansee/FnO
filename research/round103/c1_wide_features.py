"""Round 104 C1: on wide-CPR days, what at entry separates winners from losers? (all 6 slots, 1-lot raw trades, 2023-01 .. 2026-11-20)"""
import sys; sys.path.insert(0, __file__.rsplit("/", 1)[0])
import r103lib as R3, numpy as np, pandas as pd
from fno import config as C
L = R3.L
T = R3.T0.copy()
rows = []
for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
    df, o, h, l, c, hm, day = L.bar_of(sym, "5m")
    d = pd.DataFrame({"o": o, "h": h, "l": l, "c": c, "hm": hm, "day": df.index.normalize()}, index=df.index)
    daily = d.groupby("day").agg(O=("o", "first"), H=("h", "max"), L=("l", "min"), C=("c", "last"))
    daily["pH"] = daily.H.shift(1); daily["pL"] = daily.L.shift(1); daily["pC"] = daily.C.shift(1); daily["pR"] = (daily.H - daily.L).shift(1)
    daily["atr"] = (daily.H - daily.L).rolling(14).mean().shift(1)
    daily.index = daily.index.strftime("%Y-%m-%d")
    t = T[T.sym == sym].join(daily, on="date")
    rows.append(t)
X = pd.concat(rows)
X["dir"] = np.where(X.side == "CALL", 1, -1)
X["hh"] = X.entry_time.str[:2].astype(int) * 60 + X.entry_time.str[3:5].astype(int)
X["move_atr"] = X.dir * (X.spot_in - X.O) / X.atr                    # day's move so far in direction, in ATRs
X["gap_atr"] = X.dir * (X.O - X.pC) / X.atr                           # opening gap in trade direction
X["beyond_prev"] = np.where(X.dir > 0, X.spot_in > X.pH, X.spot_in < X.pL)
X["wide"] = X.cpr_w > C.CPR_WIDE
X["late"] = X.hh >= 11 * 60 + 30
X.to_pickle("/tmp/r104_wide_X.pkl")
W = X[X.wide]
print(len(W), "wide-day trades;", W.date.nunique(), "days")
for col, fn in [("late", lambda s: s), ("beyond_prev", lambda s: s), ("move>0.5ATR", lambda s: W.move_atr > 0.5), ("move>0.8ATR", lambda s: W.move_atr > 0.8), ("gap against (<-0.3)", lambda s: W.gap_atr < -0.3)]:
    m = fn(W[col] if col in W else None)
    for v in (False, True):
        g = W[m == v]
        print(f"{col:22s} {v!s:5s} n={len(g):3d} R={g.R.mean():+.3f} pnl/trade={g.pnl.mean():+7.0f} win={(g.pnl>0).mean():.2f} total={g.pnl.sum():+8.0f}")
print()
for name, m in [("late & beyond prev", W.late & W.beyond_prev), ("late & move>0.8ATR", W.late & (W.move_atr > 0.8)), ("move>1.0ATR", W.move_atr > 1.0), ("beyond prev & move>0.5", W.beyond_prev & (W.move_atr > 0.5))]:
    g = W[m]; print(f"{name:26s} n={len(g):3d} R={g.R.mean():+.3f} pnl/trade={g.pnl.mean():+7.0f} win={(g.pnl>0).mean():.2f} total={g.pnl.sum():+8.0f} years={g.date.str[:4].value_counts().sort_index().to_dict()}")
print("\nper-year of 'late & beyond prev':"); g = W[W.late & W.beyond_prev]; print(g.groupby(g.date.str[:4]).agg(n=("R","size"), R=("R","mean"), pnl=("pnl","sum")))
