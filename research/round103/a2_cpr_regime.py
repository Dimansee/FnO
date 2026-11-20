import sys, json
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import r103lib as R3
import numpy as np, pandas as pd
from fno import portfolio_bt as PB, config as C
import outcomes as O
W = C.CPR_WIDE
res = {}
for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
    c = O.candles(sym)
    w = PB.cpr_widths(c).dropna()
    s = pd.DataFrame({"w": w.values}, index=pd.to_datetime(w.index))
    s["wide"] = s.w > W
    print(sym, "sessions", len(s), "median width", round(s.w.median(), 3), "p90", round(s.w.quantile(.9), 3))
    print("  share of sessions wide by half-year:")
    g = s.groupby([s.index.year, (s.index.month - 1) // 6 + 1]).wide.agg(["mean", "size"])
    print("  ", {f"{y}H{h}": f"{m:.0%} of {n}" for (y, h), (m, n) in g.iterrows()})
    last = s.tail(25)
    print("  last 25 sessions wide:", f"{last.wide.mean():.0%}", " last 10:", f"{s.tail(10).wide.mean():.0%}", " widths last 10:", [round(x, 2) for x in s.tail(10).w])
    res[sym] = {"all": float(s.wide.mean()), "last25": float(last.wide.mean())}
json.dump(res, open("/tmp/r103_a2.json", "w"))
