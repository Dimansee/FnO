"""C: today's Autopilot trades, split by where the index stood in a run when the day began.
'with' = trade in the run's direction (CALL after up closes), 'against' = the reversal side. Rs per trade per lot, by year.
Then: drop / keep buckets in the house replay (six cells) + forward window."""
import json, numpy as np, pandas as pd, r105lib as X
R3 = X.R3; T = R3.T0.copy()
D = {s: X.streaks(X.daily_from_5m(s)) for s in X.SYMS}
def tag(F):
    F = F.copy(); dt = pd.to_datetime(F.date)
    F["p_run"] = [D[s].p_run.get(d, 0) for s, d in zip(F.sym, dt)]; F["p_cum"] = [D[s].p_cum.get(d, 0.0) for s, d in zip(F.sym, dt)]
    sd = np.where(F.side == "CALL", 1, -1); F["rel"] = np.sign(F.p_run) * sd          # +1 with the run, -1 against
    return F
T = tag(T); T["yr"] = T.date.str[:4]
out = {}
print("per-lot Rs a trade (n) by year; base all trades:", {y: (round(float(q.pnl.mean())), len(q)) for y, q in T.groupby("yr")})
B = {"with run>=2": (T.rel > 0) & (T.p_run.abs() >= 2), "against run>=2": (T.rel < 0) & (T.p_run.abs() >= 2),
     "with run>=3": (T.rel > 0) & (T.p_run.abs() >= 3), "against run>=3": (T.rel < 0) & (T.p_run.abs() >= 3),
     "with run 2-3": (T.rel > 0) & T.p_run.abs().between(2, 3), "against run 2-3": (T.rel < 0) & T.p_run.abs().between(2, 3),
     "with run>=4": (T.rel > 0) & (T.p_run.abs() >= 4), "against run>=4": (T.rel < 0) & (T.p_run.abs() >= 4),
     "with, run>=2 & >=1.5%": (T.rel > 0) & (T.p_run.abs() >= 2) & (T.p_cum.abs() >= 1.5), "against, run>=2 & >=1.5%": (T.rel < 0) & (T.p_run.abs() >= 2) & (T.p_cum.abs() >= 1.5),
     "against UP run>=2 (PUT)": (T.rel < 0) & (T.p_run >= 2), "against DOWN run>=2 (CALL)": (T.rel < 0) & (T.p_run <= -2),
     "with UP run>=2 (CALL)": (T.rel > 0) & (T.p_run >= 2), "with DOWN run>=2 (PUT)": (T.rel > 0) & (T.p_run <= -2),
     "no run (|run|<2)": T.p_run.abs() < 2}
for k, m in B.items():
    q = T[m]; rest = T[~m]
    yrs = {y: (round(float(g.pnl.mean())), len(g)) for y, g in q.groupby("yr")}
    ry = {y: round(float(g.pnl.mean())) for y, g in rest.groupby("yr")}
    better = sum(1 for y in yrs if yrs[y][0] > ry.get(y, 0))
    out[k] = {"years": yrs, "rest": ry, "n": int(len(q)), "avg": round(float(q.pnl.mean())), "win": round(float((q.pnl > 0).mean() * 100), 1)}
    print(f"{k:28s} n {len(q):4d} avg {q.pnl.mean():+6.0f} win {(q.pnl > 0).mean() * 100:4.1f}% | {yrs} | rest {ry} | better than rest in {better}/{len(yrs)} yrs")
# shuffle check: how often would a random bucket of the same size be better (or worse) than the rest in all 4 years?
rng = np.random.default_rng(5)
for k in ("against run>=2", "with run>=2", "against run>=3", "with run>=3"):
    m = B[k].values; obs = out[k]; hits_lo = hits_hi = 0
    for _ in range(2000):
        p = rng.permutation(m); ok_lo = ok_hi = True
        for y, g in T.groupby("yr"):
            mm = p[g.index.values] if False else p[T.index.get_indexer(g.index)]
            if mm.sum() < 5: continue
            a, b = g.pnl.values[mm].mean(), g.pnl.values[~mm].mean()
            ok_lo &= a < b; ok_hi &= a > b
        hits_lo += ok_lo; hits_hi += ok_hi
    print(f"shuffle {k}: P(worse than rest in every year by chance) {hits_lo / 2000:.3f}, P(better every year) {hits_hi / 2000:.3f}")
# house replay with buckets removed
base = R3.six(); fb = {c: R3.run(c, "fwd") for c in (200_000, 20_000)}
print("\nTODAY", {k: R3.fmt(v) for k, v in base.items()}, "| fwd", {c: R3.fmt(v) for c, v in fb.items()})
A0, H0 = tag(R3.A0) if R3.A0 is not None else None, tag(R3.H0) if R3.H0 is not None else None
def drop(F, fn):
    return None if F is None else F[~fn(F)].drop(columns=["p_run", "p_cum", "rel"])
TESTS = {"skip against run>=2": lambda F: (F.rel < 0) & (F.p_run.abs() >= 2), "skip against run>=3": lambda F: (F.rel < 0) & (F.p_run.abs() >= 3),
         "skip with run>=2": lambda F: (F.rel > 0) & (F.p_run.abs() >= 2), "skip with run>=3": lambda F: (F.rel > 0) & (F.p_run.abs() >= 3),
         "skip with run>=4": lambda F: (F.rel > 0) & (F.p_run.abs() >= 4),
         "skip with, run>=2 & >=1.5%": lambda F: (F.rel > 0) & (F.p_run.abs() >= 2) & (F.p_cum.abs() >= 1.5),
         "skip against, run>=2 & >=1.5%": lambda F: (F.rel < 0) & (F.p_run.abs() >= 2) & (F.p_cum.abs() >= 1.5),
         "only against (reversal side) after run>=2": lambda F: (F.rel > 0) & (F.p_run.abs() >= 2),
         "skip PUTs after UP run>=2": lambda F: (F.rel < 0) & (F.p_run >= 2), "skip CALLs after DOWN run>=2": lambda F: (F.rel < 0) & (F.p_run <= -2)}
rep = {"today": base}
for k, fn in TESTS.items():
    if k.startswith("only"): continue
    Tk, Ak, Hk = drop(T.drop(columns=["yr"]), fn), drop(A0, fn), drop(H0, fn)
    cells = {f"{cap}|{p}": R3.L.replay(cap, *R3.PER[p], T=Tk, A=Ak, H=Hk) for cap, p in R3.SIX}
    fw = {c: R3.L.replay(c, *R3.PER["fwd"], T=Tk, A=Ak, H=Hk) for c in (200_000, 20_000)}
    b_, d_ = R3.verdict(cells, base); rep[k] = {"cells": cells, "better": b_, "deeper": d_, "fwd": fw}
    print(f"{k:32s}: better {b_}/6, worst fall {d_:+.1f} pts deeper | " + " ".join(f"{kk.split('|')[0][:-3]}k {kk.split('|')[1]} {R3.fmt(v)}" for kk, v in cells.items()) + f" | fwd 2L {R3.fmt(fw[200000])} 20k {R3.fmt(fw[20000])}", flush=True)
json.dump({"buckets": out, "replay": rep}, open("r105_c.json", "w"), indent=1, default=str)
