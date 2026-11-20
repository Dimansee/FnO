"""Round 104 C4: neighbourhood of the weekly-limit probe (is 11:30 x0.5 a spike?) and the combination with a wide-CPR probe."""
import sys, inspect, json; sys.path.insert(0, __file__.rsplit("/", 1)[0])
import r103lib as R3, numpy as np
from fno import portfolio_bt as PB, config as C, strategy as S
src = inspect.getsource(PB.replay)
src = src.replace('        if blocked:\n            why = "weekly loss limit"\n        elif gates', '        wk_probe = bool(blocked and WKP and r.entry_time >= WKT)\n        if blocked and not wk_probe:\n            why = "weekly loss limit"\n        elif gates')
b = "        lots, m = 0, _mult(conf, s, int(r.score))\n"; src = src.replace(b, b + "        if wk_probe:\n            m *= WKM\n")
PB.WKP, PB.WKT, PB.WKM = False, "11:30", 0.5
exec(src, PB.__dict__)
WM = {"v": 0.0}
def tilt(w):
    if w is None or w != w: return None
    if w >= 100: return 0.0
    if w < C.CPR_NARROW: return C.CPR_MULT_NARROW
    if w > C.CPR_WIDE: return WM["v"]
    return C.CPR_MULT_MID
S.cpr_tilt = tilt
def late_wide(cut):
    def f(X):
        if X is None: return None
        X = X.copy(); wide = X.cpr_w > C.CPR_WIDE
        X.loc[wide & (X.entry_time < cut), "cpr_w"] = 999.0
        return X
    return f
base = R3.six(); out = {}
def report(name, cells, f=None):
    b_, d_ = R3.verdict(cells, base)
    fw = {c: R3.run(c, "fwd", *(f(R3.T0), f(R3.A0), f(R3.H0)) if f else (None, None, None)) for c in (200_000, 20_000)} if f else {c: R3.run(c, "fwd") for c in (200_000, 20_000)}
    print(f"{name}: better {b_}/6 deeper {d_} | " + " ".join(f"{k.split('|')[0][:3]}k {k.split('|')[1][:4]} {R3.fmt(v)}" for k, v in cells.items()) + f" || fwd 2L {R3.fmt(fw[200000])} 20k {R3.fmt(fw[20000])}", flush=True)
    out[name] = {"cells": cells, "better": b_, "deeper": d_, "fwd": fw}
PB.WKP = True
for t in ("11:00", "11:30", "12:00", "13:00"):
    for m in (0.35, 0.5, 0.75):
        PB.WKT, PB.WKM = t, m
        report(f"weekly probe after {t} x{m}", R3.six())
PB.WKT, PB.WKM = "11:30", 0.5
for cut in ("11:30", "12:00"):
    for wm in (0.25, 0.5):
        WM["v"] = wm; f = late_wide(cut)
        report(f"weekly probe 11:30 x0.5 + wide-CPR after {cut} x{wm}", R3.six(f(R3.T0), f(R3.A0), f(R3.H0)), f)
json.dump(out, open("/tmp/r104_c4.json", "w"), indent=1, default=str)
