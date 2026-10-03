"""Entry delay measured with real 1-minute prices: the app sees the 5-minute candle close and buys on
its next minute tick, so the real fill is ~the 1-minute close one (or two) minutes after the signal."""
import os; os.environ.setdefault("FNO_CALIB","1")
exec(open("round6.py").read().split("# ------------------------------------------------------------------ 1. expiry")[0].replace('show("BASE','pass # show("BASE'))
import numpy as np, pandas as pd
M1 = {}
for s in SYMS:
    x = pd.read_csv(f"data/{s}_1m_upstox.csv.gz", parse_dates=["ts"])
    M1[s] = dict(zip(zip(x.ts.dt.date, x.ts.dt.hour * 60 + x.ts.dt.minute), x.close))
ORIG = dict(E.STRATS)
CUR = {"sym": None}
def delayed(fn, lag):
    def g(d, j, p, st):
        while True:
            s = fn(d, j, p, st)
            if s is None:
                return s
            m = d.t[s.i] + 5 + lag - 1       # 1-min bar starting here closes `lag` minutes after the 5-min close
            px = M1[CUR["sym"]].get((d.d, m))
            if px is None:
                return s
            if s.side * (px - s.stop) <= 0:  # already through the stop: skip this signal, keep scanning
                j = s.i + 1
                continue
            s.px = px
            return s
    return g
res = {}
for lag in (1, 2, 3):
    E.STRATS.update({k: delayed(ORIG[k], lag) for k in ("noise", "camarilla")})
    tr = []
    for s in SYMS:
        CUR["sym"] = s
        tr += both(s)
    res[lag] = stats(tr)
    show(f"buy {lag} min after the candle closes", res[lag])
E.STRATS.update(ORIG)
# how far price moved in that minute, in the trade's direction (points)
mv = []
for t in BASE:
    m = t["in"]
    s = t["sym"]
    p1 = M1[s].get((t["date"], m))
    if p1 is not None:
        mv.append((s, t["side"] * (p1 - t["spot_in"])))
mv = pd.DataFrame(mv, columns=["sym", "pts"])
print(mv.groupby("sym")["pts"].describe(percentiles=[.1, .5, .9]).round(1))
json.dump({"lag": res, "move_1min": mv.groupby("sym")["pts"].describe(percentiles=[.1, .5, .9]).round(1).to_dict()}, open("round6_delay.json", "w"), default=str, indent=1)
