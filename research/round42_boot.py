"""Round 42, part E: what one year from Rs 2 lakh can look like. Block bootstrap by calendar month of the backtest trades
(1 ITM, stress impact k=6%), 2,000 random 12-month paths per sizing plan, each replayed with the round42 rules
(compounding, cash, gates, weekly loss limit). Also with the filter "skip trades against a 3-day run >= 2 ATR"."""
import os, sys, json, pickle, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round42 as R
from fno import config as C
T = pickle.load(open(R.PKL, "rb"))[-1].copy()
sg = np.where(T.side == "CALL", 1, -1)
T["against2"] = (np.sign(T.move3) != sg) & (T.move3.abs() >= 2.0 * T.atr_d)
T["m"] = T.date.str[:7]
months = sorted(T.m.unique())
conv = lambda r: (C.CONVICTION_HIGH if r.score >= 5 else C.CONVICTION_LOW) if r.sym in C.CONVICTION_SYMS else 1.0
strong = lambda r: 2.0 if r.score >= 5 else (1.0 if r.score >= 3 else 0.5)
PLANS = {"C 1% risk, no confidence": (dict(), False), "D 1% risk + live confidence (today's Autopilot)": (dict(mult=conv), False),
         "E 1% risk + strong confidence": (dict(mult=strong), False), "D + skip trades against a big run": (dict(mult=conv), True),
         "E + skip trades against a big run": (dict(mult=strong), True), "F 2% risk + live confidence": (dict(risk=0.02, mult=conv), False)}
rng = np.random.default_rng(42)
paths = [rng.choice(months, 12, replace=True) for _ in range(int(sys.argv[1]) if len(sys.argv) > 1 else 2000)]
out = {}
for nm, (kw, filt) in PLANS.items():
    finals, dds = [], []
    for p in paths:
        parts = []
        for i, m in enumerate(p):
            g = T[T.m == m].copy()
            if filt:
                g = g[~g.against2]
            g["date"] = [(pd.Timestamp("2030-01-01") + pd.Timedelta(days=31 * i + int(x[8:10]) - 1)).date().isoformat() for x in g.date]   # stitch months in order
            parts.append(g)
        X = pd.concat(parts, ignore_index=True)
        Rp = R.replay(X, k=0.06, one_order=True, **kw)
        if Rp.empty:
            finals.append(200000); dds.append(0); continue
        eq = pd.concat([pd.Series([200000.0]), Rp["eq"]], ignore_index=True)
        finals.append(float(eq.iloc[-1])); dds.append(float((eq / eq.cummax() - 1).min()))
    f, d = np.array(finals), np.array(dds)
    out[nm] = {"median_end": int(np.median(f)), "p10_end": int(np.percentile(f, 10)), "p90_end": int(np.percentile(f, 90)),
               "loss_year_pct": round(float((f < 200000).mean() * 100), 1), "dd_worse_30_pct": round(float((d < -0.30).mean() * 100), 1),
               "dd_worse_50_pct": round(float((d < -0.50).mean() * 100), 1), "median_dd_pct": round(float(np.median(d) * 100), 1)}
    print(nm, out[nm], flush=True)
json.dump(out, open(os.path.join(HERE, "round42_boot_results.json"), "w"), indent=1)
