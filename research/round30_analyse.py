"""Round 30 analysis: reads the grid trades (python round30_grid.py round30_trades.pkl) and writes round30_results.json."""
import os, sys, pickle, json
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "round30_trades.pkl")
D = pickle.load(open(src, "rb")); G = D["grid"]; T = D["trades"]; T["y"] = T.date.str[:4]
def pf(u): l = -u[u <= 0].sum(); return float(u[u > 0].sum() / l) if l else None
rows = []
for k, g in T.groupby("k"):
    tr, te = g[g.y < "2026"], g[g.y == "2026"]; yrs = tr.groupby("y").u.sum(); idx = tr.groupby("sym").u.sum()
    rows.append(dict(k=int(k), **{kk: str(v) for kk, v in G[k].items()}, n_tr=len(tr), tr=float(tr.u.sum()), pf_tr=pf(tr.u),
                     y23=float(yrs.get("2023", 0)), y24=float(yrs.get("2024", 0)), y25=float(yrs.get("2025", 0)), idx_pos=int((idx > 0).sum()),
                     n_26=len(te), y26=float(te.u.sum()), pf_26=pf(te.u)))
R = pd.DataFrame(rows)
rob = R[(R.tr > 0) & (R.y23 > 0) & (R.y24 > 0) & (R.y25 > 0) & (R.pf_tr >= 1.1) & (R.idx_pos >= 2) & (R.n_tr >= 100)]
out = {"settings": len(R), "profitable_2023_25": int((R.tr > 0).sum()), "profitable_2026": int((R.y26 > 0).sum()),
       "profitable_both": int(((R.tr > 0) & (R.y26 > 0)).sum()), "robust_2023_25": int(len(rob)), "robust_and_2026_positive": int((rob.y26 > 0).sum()),
       "robust": rob.sort_values("tr", ascending=False).round(2).to_dict("records"),
       "best_5_on_2023_25": R.sort_values("tr", ascending=False).head(5).round(2).to_dict("records")}
for d in ("entry", "tf", "vol", "adx", "vwap", "exit"):
    out[f"median_by_{d}"] = R.groupby(d)[["tr", "y26"]].median().round(0).to_dict("index")
json.dump(out, open(os.path.join(HERE, "round30_results.json"), "w"), indent=1, default=str)
print({k: v for k, v in out.items() if not isinstance(v, (list, dict))})
