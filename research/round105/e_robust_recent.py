"""E: (1) neighbours of the best filter from C (skip reversal-side trades after a run) at several starting sizes;
(2) 'nowadays': the last 3 months only - what the day after a run did, and how Autopilot's with / against trades did."""
import json, numpy as np, pandas as pd, r105lib as X
R3 = X.R3; L = X.L
D = {s: X.streaks(X.daily_from_5m(s)) for s in X.SYMS}
def tag(F):
    if F is None: return None
    F = F.copy(); dt = pd.to_datetime(F.date)
    F["p_run"] = [D[s].p_run.get(d, 0) for s, d in zip(F.sym, dt)]; F["p_cum"] = [D[s].p_cum.get(d, 0.0) for s, d in zip(F.sym, dt)]
    F["rel"] = np.sign(F.p_run) * np.where(F.side == "CALL", 1, -1); return F
T, A, H = tag(R3.T0), tag(R3.A0), tag(R3.H0)
CAPS = (20_000, 40_000, 100_000, 200_000, 500_000); PERS = ("2023-24", "2025-26", "full", "fwd")
def grid(Tk, Ak, Hk): return {f"{c}|{p}": L.replay(c, *R3.PER[p], T=Tk, A=Ak, H=Hk) for c in CAPS for p in PERS}
base = grid(R3.T0, R3.A0, R3.H0); out = {"today": base}
print("TODAY  " + " ".join(f"{k.split('|')[0][:-3]}k/{k.split('|')[1]} {R3.fmt(v)}" for k, v in base.items()))
cut = lambda F, fn: None if F is None else F[~fn(F)].drop(columns=["p_run", "p_cum", "rel"])
for rn in (2, 3):
    for cm in (1.0, 1.5, 2.0, 2.5):
        fn = lambda F, rn=rn, cm=cm: (F.rel < 0) & (F.p_run.abs() >= rn) & (F.p_cum.abs() >= cm)
        g = grid(cut(T, fn), cut(A, fn), cut(H, fn)); k = f"skip against, run>={rn} & >={cm}%"; out[k] = g
        main = [x for x in base if not x.endswith("fwd")]
        better = sum(1 for x in main if g[x]["final"] > base[x]["final"]); deeper = max(base[x]["dd"] - g[x]["dd"] for x in main)
        print(f"{k:32s} better {better}/{len(main)} fall {deeper:+.1f} | " + " ".join(f"{x.split('|')[0][:-3]}k/{x.split('|')[1]} {R3.fmt(v)}" for x, v in g.items()), flush=True)
json.dump(out, open("r105_e.json", "w"), indent=1, default=str)
print("\nNOWADAYS (25 Aug - 20 Nov 2026), day after a run, signed with the run:")
for s in X.SYMS:
    d = D[s]; q = d[(d.index >= "2026-08-25")]
    for nm, m in (("after 2+ up closes", q.p_run >= 2), ("after 2-3 up closes", q.p_run.between(2, 3)), ("after 2+ down closes", q.p_run <= -2)):
        z = q[m]; sg = np.sign(z.p_run); cc = sg * (z.close / z.pc - 1) * 100; oc = sg * (z.close / z.open - 1) * 100; gp = sg * (z.open / z.pc - 1) * 100
        print(f"  {s:9s} {nm:22s} n {len(z):2d} | closed with the run {int((cc > 0).sum())}, against {int((cc < 0).sum())} | avg close-to-close {cc.mean():+.2f}% gap {gp.mean():+.2f}% open-to-close {oc.mean():+.2f}% | gap against run {int((gp < 0).sum())}, of which closed above the open {int(((gp < 0) & (oc > 0)).sum())}")
q = T[T.date >= "2026-08-25"]
for nm, m in (("with run>=2", (q.rel > 0) & (q.p_run.abs() >= 2)), ("against run>=2 (reversal side)", (q.rel < 0) & (q.p_run.abs() >= 2)), ("no run", q.p_run.abs() < 2)):
    z = q[m]; print(f"  Autopilot per-lot trades {nm:32s} n {len(z):3d} avg Rs {z.pnl.mean():+7.0f} win {(z.pnl > 0).mean() * 100:.0f}% total {z.pnl.sum():+.0f}")
d = D["NIFTY"].tail(16)[["open", "close", "run", "cum"]].round(2); print(d.to_string())
