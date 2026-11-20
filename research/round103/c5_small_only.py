"""Round 104 C5: the weekly-limit probe only for small accounts (week start below AUTOPILOT_SMALL_ACCOUNT, where the limit is 15%)."""
import sys, inspect, json; sys.path.insert(0, __file__.rsplit("/", 1)[0])
import r103lib as R3
from fno import portfolio_bt as PB, config as C
src = inspect.getsource(PB.replay)
src = src.replace('        if blocked:\n            why = "weekly loss limit"\n        elif gates', '        wk_probe = bool(blocked and WKP and r.entry_time >= WKT and week_start < C.AUTOPILOT_SMALL_ACCOUNT)\n        if blocked and not wk_probe:\n            why = "weekly loss limit"\n        elif gates')
b = "        lots, m = 0, _mult(conf, s, int(r.score))\n"; src = src.replace(b, b + "        if wk_probe:\n            m *= WKM\n")
PB.WKP, PB.WKT, PB.WKM = False, "11:30", 0.5
exec(src, PB.__dict__)
caps = (20_000, 40_000, 60_000, 100_000, 200_000)
pers = ("2023-24", "2025-26", "full", "fwd")
base = {(c, p): R3.run(c, p) for c in caps for p in pers}
for t, m in (("11:30", 0.5), ("11:30", 0.75), ("12:00", 0.5)):
    PB.WKP, PB.WKT, PB.WKM = True, t, m
    print(f"== small-only probe after {t} x{m}")
    for c in caps:
        row = []
        for p in pers:
            r = R3.run(c, p); b = base[(c, p)]
            row.append(f"{p} {R3.fmt(r)} vs {R3.fmt(b)}")
        print(f"  {c//1000}k: " + " | ".join(row), flush=True)
