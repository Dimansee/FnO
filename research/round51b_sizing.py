"""Round 51b (28 Oct 2026): the two SMC features that separated good from bad Autopilot trades (entry inside an open FVG our way;
a >= 1 ATR displacement candle our way in the last 3 bars) used for SIZE instead of as filters.
Rs 2L account replay (round 43), full history, 2026 restart, and 1,000 one-year bootstrap paths."""
import os, sys, json, pickle
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round43 as R43, round51_smc_filters as F
T = pickle.load(open(R43.PKL, "rb")); T = F.feats(T)
T["disp"] = T.displacement.fillna(False).astype(bool); T["fvg"] = T.in_fvg_with.fillna(False).astype(bool)
pt = lambda X: (X.pnl.sum(), len(X))
for nm, g in (("displacement", T.disp), ("in FVG", T.fvg)):
    for per, m in (("2023-25", T.date < "2026"), ("2026", T.date >= "2026")):
        a, b = T[m & g], T[m & ~g]
        print(f"{nm:12s} {per}: with {len(a)} trades avg Rs {a.pnl.mean():.0f} | without {len(b)} avg Rs {b.pnl.mean():.0f}")
V = {"baseline": None,
     "1.5x on displacement": lambda r, e, p: 1.5 if r.disp else 1.0,
     "1.5x / 0.75x displacement": lambda r, e, p: 1.5 if r.disp else 0.75,
     "2x inside open FVG": lambda r, e, p: 2.0 if r.fvg else 1.0,
     "1.5x displacement + 2x FVG": lambda r, e, p: (1.5 if r.disp else 1.0) * (2.0 if r.fvg else 1.0)}
out = {}
for nm, fn in V.items():
    full = R43.replay(T, size_fn=fn); y26 = R43.replay(T[T.date >= "2026"], size_fn=fn); y25 = R43.replay(T[T.date < "2026"], size_fn=fn)
    bt = R43.boot(T, n=1000, size_fn=fn)
    out[nm] = {"full": full, "2023_25": y25, "2026": y26, "boot": bt}
    print(f"{nm:30s} full {full['final']:>9} {full['dd']:6} | 23-25 {y25['final']:>9} {y25['dd']:6} | 2026 {y26['final']:>8} {y26['dd']:6} | boot {bt}", flush=True)
json.dump(out, open(os.path.join(HERE, "round51b_results.json"), "w"), indent=1)
