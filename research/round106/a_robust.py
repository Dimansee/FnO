"""A: robustness. (0) parity: xreplay with no hooks == today's replay; (1) slippage stress; (2) per-strategy ablation;
(3) year by year (account restarted each 1 Jan) and per-strategy rupees per year; (4) parameter sensitivity (from regen sets)."""
import json, sys, os
import r106lib as X
import pandas as pd
T, A, H = X.load_set("base")
out = {}
plain = X.grid(T, A, H, strict_eq=False, strict=False)
ref = {f"{c}|{p}": X.R3.run(c, p) for c, p in X.SIX}
print("parity (house replay):", all(abs(plain[k]["final"] - ref[k]["final"]) < 1 and plain[k]["dd"] == ref[k]["dd"] for k in ref))
print("HOUSE " + " | ".join(f"{k} {X.fmt(v)}" for k, v in plain.items()))
base = X.base_grid()
out["base"] = base
print("BASE  " + " | ".join(f"{k} {X.fmt(v)}" for k, v in base.items()), flush=True)

print("\n1. SLIPPAGE STRESS")
for nm, kw in (("slip x2 (1.0%/fill) + impact x2", dict(slip_add=0.005, impact_k=0.12)),
               ("slip x3 (1.5%/fill) + impact x3", dict(slip_add=0.010, impact_k=0.18)),
               ("slip x2 only", dict(slip_add=0.005)), ("impact x2 only", dict(impact_k=0.12))):
    g = X.grid(T, A, H, **kw); out[nm] = g
    print(f"  {nm:34s} " + " | ".join(f"{k} {X.fmt(v)}" for k, v in g.items()), flush=True)

print("\n2. ABLATION (one strategy off)")
for st in sorted(T.strategy.unique()):
    k = lambda F: None if F is None else F[F.strategy != st]
    g = X.grid(k(T), k(A), k(H)); out[f"off {st}"] = g
    b, n, d = X.verdict(g, base)
    print(f"  without {st:18s} better {b}/{n} fall {d:+.1f} | " + " | ".join(f"{kk} {X.fmt(v)}" for kk, v in g.items()), flush=True)

print("\n3. YEAR BY YEAR (restart at Rs 2L and Rs 20k on 1 Jan)")
yrs = X.grid(T, A, H, pers=tuple(X.YEARS)); out["years"] = yrs
out["years_house"] = X.grid(T, A, H, pers=tuple(X.YEARS), strict_eq=False, strict=False)
for k, v in yrs.items():
    print(f"  {k:14s} {X.fmt(v)}  trades {v['trades']}   (house replay {X.fmt(out['years_house'][k])})")
print("  per-lot rupees by strategy and year (1 ITM, 1 lot, no sizing):")
pv = T.assign(y=T.date.str[:4]).pivot_table(index="strategy", columns="y", values="pnl", aggfunc="sum").round(0)
pn = T.assign(y=T.date.str[:4]).pivot_table(index="strategy", columns="y", values="pnl", aggfunc="count")
print(pv.to_string()); print(pn.to_string())
out["per_lot"] = pv.to_dict()

print("\n4. PARAMETER SENSITIVITY (one change, new trades)")
for nm in ("band150", "band200", "lb10", "lb20", "nvix16", "nvix20", "cvix18", "cvix22"):
    f = f"/tmp/r106/{nm}.pkl"
    if not os.path.exists(f):
        print("  missing", nm); continue
    Tv, Av, Hv = X.load_set(nm)
    g = X.grid(Tv, Av, Hv); out[nm] = g; b, n, d = X.verdict(g, base)
    gy = X.grid(Tv, Av, Hv, pers=tuple(X.YEARS), caps=(200_000,)); out[nm + " years"] = gy
    nz = Tv[Tv.strategy == "Noise band"]
    print(f"  {nm:9s} better {b}/{n} fall {d:+.1f} noise trades {len(nz)} per-lot {nz.pnl.sum():+.0f} | " + " | ".join(f"{kk} {X.fmt(v)}" for kk, v in g.items()) + " || years 2L " + " ".join(f"{kk.split('|')[1]} {X.fmt(v)}" for kk, v in gy.items()), flush=True)
json.dump(out, open("r106_a.json", "w"), indent=1, default=str)
