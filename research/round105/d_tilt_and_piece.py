"""D: (1) the run buckets in R terms; (2) size tilts by run state inside the house replay (hook on the size multiplier);
(3) the one stand-alone version that was positive in both periods (WITH the run: 30-min range break, 1R trail) as an extra slot."""
import json, inspect, numpy as np, pandas as pd, r105lib as X
R3 = X.R3; PB = R3.PB; L = X.L
src = inspect.getsource(PB.replay)
b = "        lots, m = 0, _mult(conf, s, int(r.score))\n"
assert b in src
exec(src.replace(b, b + "        m *= RT.get((r.date, r.sym, r.side), 1.0)\n"), PB.__dict__)
PB.RT = {}
D = {s: X.streaks(X.daily_from_5m(s)) for s in X.SYMS}
T = R3.T0.copy(); dt = pd.to_datetime(T.date)
T["p_run"] = [D[s].p_run.get(d, 0) for s, d in zip(T.sym, dt)]; T["p_cum"] = [D[s].p_cum.get(d, 0.0) for s, d in zip(T.sym, dt)]
T["rel"] = np.sign(T.p_run) * np.where(T.side == "CALL", 1, -1); T["yr"] = T.date.str[:4]; T["Rm"] = T.pnl / T.risk_lot_capped
print("R per trade by year (money / risk):")
for k, m in {"all": T.rel > -9, "against run>=2": (T.rel < 0) & (T.p_run.abs() >= 2), "against run>=4": (T.rel < 0) & (T.p_run.abs() >= 4),
             "against, run>=2 & >=1.5%": (T.rel < 0) & (T.p_run.abs() >= 2) & (T.p_cum.abs() >= 1.5), "with run>=2": (T.rel > 0) & (T.p_run.abs() >= 2),
             "with run 2-3": (T.rel > 0) & T.p_run.abs().between(2, 3)}.items():
    q = T[m]; print(f"  {k:26s} n {len(q):4d} | " + " ".join(f"{y} {g.Rm.mean():+.3f}" for y, g in q.groupby("yr")) + " | by strategy: " + " ".join(f"{s[:9]} {g.Rm.mean():+.2f}({len(g)})" for s, g in q.groupby("strategy")))
def keyset(mask_fn):
    ks = {}
    for s in X.SYMS:
        d = D[s]
        for day, r, c in zip(d.index, d.p_run, d.p_cum):
            for side, sd in (("CALL", 1), ("PUT", -1)):
                v = mask_fn(int(r), float(c), int(np.sign(r) * sd))
                if v != 1.0: ks[(day.strftime("%Y-%m-%d"), s, side)] = v
    return ks
base = R3.six(); fb = {c: R3.run(c, "fwd") for c in (200_000, 20_000)}
print("\nTODAY", {k: R3.fmt(v) for k, v in base.items()}, "| fwd", {c: R3.fmt(v) for c, v in fb.items()})
TILTS = {}
for x in (0.0, 0.5, 0.75):
    TILTS[f"against run>=2 x{x}"] = lambda r, c, rel, x=x: x if rel < 0 and abs(r) >= 2 else 1.0
    TILTS[f"against run>=3 x{x}"] = lambda r, c, rel, x=x: x if rel < 0 and abs(r) >= 3 else 1.0
    TILTS[f"against run>=4 x{x}"] = lambda r, c, rel, x=x: x if rel < 0 and abs(r) >= 4 else 1.0
    TILTS[f"against, run>=2 & >=1.5% x{x}"] = lambda r, c, rel, x=x: x if rel < 0 and abs(r) >= 2 and abs(c) >= 1.5 else 1.0
    TILTS[f"against, run>=2 & >=2.5% x{x}"] = lambda r, c, rel, x=x: x if rel < 0 and abs(r) >= 2 and abs(c) >= 2.5 else 1.0
for x in (1.2, 1.5):
    TILTS[f"with run 2-3 x{x}"] = lambda r, c, rel, x=x: x if rel > 0 and 2 <= abs(r) <= 3 else 1.0
    TILTS[f"with run>=2 x{x}"] = lambda r, c, rel, x=x: x if rel > 0 and abs(r) >= 2 else 1.0
TILTS["reversal side x1.5 after run 2-3 (the user's idea as size)"] = lambda r, c, rel: 1.5 if rel < 0 and 2 <= abs(r) <= 3 else 1.0
out = {"today": base, "fwd": fb}
for k, fn in TILTS.items():
    PB.RT = keyset(fn)
    cells = R3.six(); fw = {c: R3.run(c, "fwd") for c in (200_000, 20_000)}; yrs = {y: L.replay(200_000, *L.YEARS[y], T=R3.T0, A=R3.A0, H=R3.H0) for y in L.YEARS}
    b_, d_ = R3.verdict(cells, base); out[k] = {"cells": cells, "better": b_, "deeper": d_, "fwd": fw, "years2L": yrs}
    print(f"{k:34s}: better {b_}/6, fall {d_:+.1f} deeper | " + " ".join(f"{kk.split('|')[0][:-3]}k {kk.split('|')[1]} {R3.fmt(v)}" for kk, v in cells.items()) + f" | fwd 2L {R3.fmt(fw[200000])} 20k {R3.fmt(fw[20000])}", flush=True)
PB.RT = {}
yb = {y: L.replay(200_000, *L.YEARS[y], T=R3.T0, A=R3.A0, H=R3.H0) for y in L.YEARS}; out["years2L_today"] = yb
print("years at 2L today:", {y: R3.fmt(v) for y, v in yb.items()})
for k in out:
    if isinstance(out[k], dict) and "years2L" in out[k]:
        out[k]["years_better"] = sum(1 for y in yb if out[k]["years2L"][y]["final"] > yb[y]["final"])
print("single-year check (Rs 2L restarted each year), years better of 4:", {k: v["years_better"] for k, v in out.items() if isinstance(v, dict) and "years_better" in v})
json.dump(out, open("r105_d.json", "w"), indent=1, default=str)
