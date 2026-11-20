import sys, json, time
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import r103lib as R3
import numpy as np, pandas as pd
from fno import portfolio_bt as PB
out = {}
CAPS = [200_000, 500_000, 1_000_000, 2_500_000, 5_000_000, 10_000_000]
for cap in CAPS:
    for ml in (50, 100, 200, 500):
        t = time.time()
        row = {}
        for per in ("2023-24", "2025-26", "full", "fwd"):
            a, b = R3.PER[per]
            R = R3.L.replay(cap, a, b, T=R3.T0, A=R3.A0, H=R3.H0, raw=True, max_lots=ml)
            s = PB._summary(R, cap)
            row[per] = {"final": s.get("final", cap), "dd": s.get("max_dd_pct", 0), "max_lots": s.get("max_lots", 0), "avg_lots": s.get("avg_lots", 0), "trades": s.get("trades", 0)}
        out[f"{cap}|{ml}"] = row
        r = row
        print(f"cap {cap/1e5:>6.1f}L  maxlots {ml:>3}: " + " | ".join(f"{p} {r[p]['final']/1e5:,.1f}L ({r[p]['dd']:.0f}%) lots max {r[p]['max_lots']}" for p in r) + f"  [{time.time()-t:.0f}s]", flush=True)
json.dump(out, open("/tmp/r103_b1.json", "w"), indent=1)
