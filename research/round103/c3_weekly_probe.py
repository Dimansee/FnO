"""Round 104 C3: after the weekly loss limit is hit, still allow entries from a cut-off time at a fraction of the size.
Today (20 Nov) a Rs 2L account was blocked by the weekly limit (Mon-Wed losses), not by the CPR skip."""
import sys, inspect, json; sys.path.insert(0, __file__.rsplit("/", 1)[0])
import r103lib as R3
from fno import portfolio_bt as PB
src = inspect.getsource(PB.replay)
a = '        if blocked:\n            why = "weekly loss limit"\n        elif gates'
assert a in src
src = src.replace(a, '        wk_probe = bool(blocked and WKP and r.entry_time >= WKT)\n        if blocked and not wk_probe:\n            why = "weekly loss limit"\n        elif gates')
b = "        lots, m = 0, _mult(conf, s, int(r.score))\n"
assert b in src
src = src.replace(b, b + "        if wk_probe:\n            m *= WKM\n")
PB.WKP, PB.WKT, PB.WKM = False, "11:30", 0.5
ns = PB.__dict__; exec(src, ns)          # redefines PB.replay with the probe hook
L = R3.L
base = R3.six(); out = {"today": base}
print("TODAY", {k: R3.fmt(v) for k, v in base.items()})
for t in ("11:30", "10:30", "09:00"):
    for m in (0.25, 0.5, 1.0):
        PB.WKP, PB.WKT, PB.WKM = True, t, m
        cells = R3.six(); b_, d_ = R3.verdict(cells, base)
        fw = {c: R3.run(c, "fwd") for c in (200_000, 20_000)}
        r20 = L.replay(200_000, "2026-11-20", "2026-11-20", T=R3.T0, A=R3.A0, H=R3.H0, raw=True)
        t20 = r20[r20.skipped.isna()] if "skipped" in r20 else r20
        p20 = round(float(t20.pnl.sum())) if "pnl" in t20 else 0
        out[f"{t}|{m}"] = {"cells": cells, "better": b_, "deeper": d_, "fwd": fw, "today2L": p20}
        print(f"after {t} x{m}: better {b_}/6 deeper {d_} | " + " ".join(f"{k.split('|')[0][:3]}k {k.split('|')[1][:4]} {R3.fmt(v)}" for k, v in cells.items()), flush=True)
        print(f"     fwd 2L {R3.fmt(fw[200000])} 20k {R3.fmt(fw[20000])} | 20 Nov at 2L: {p20}", flush=True)
PB.WKP = False
json.dump(out, open("/tmp/r104_c3.json", "w"), indent=1, default=str)
