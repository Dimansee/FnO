"""Round 104 C2: instead of skipping wide-CPR days, trade them at a probe size (and only some slots / only after 11:30)."""
import sys; sys.path.insert(0, __file__.rsplit("/", 1)[0])
import r103lib as R3, numpy as np, pandas as pd, json
from fno import config as C, strategy as S
WM = {"v": 0.0}
_orig = S.cpr_tilt
def tilt(w):
    if w is None or w != w: return None
    if w >= 100: return 0.0
    if w < C.CPR_NARROW: return C.CPR_MULT_NARROW
    if w > C.CPR_WIDE: return WM["v"]
    return C.CPR_MULT_MID
S.cpr_tilt = tilt
T0, A0, H0 = R3.T0, R3.A0, R3.H0
def hhmm(X): return X.entry_time.str[:2].astype(int) * 60 + X.entry_time.str[3:5].astype(int)
def prep(which):
    def f(X):
        if X is None: return None
        X = X.copy(); wide = X.cpr_w > C.CPR_WIDE
        if which == "all": keep = wide
        elif which == "late": keep = wide & (hhmm(X) >= 11 * 60 + 30)
        elif which == "noNoise_RL": keep = wide & ~X.strategy.isin(["Noise band", "Round-level break"])
        elif which == "late_noNoise_RL": keep = wide & (hhmm(X) >= 11 * 60 + 30) & ~X.strategy.isin(["Noise band", "Round-level break"])
        X.loc[wide & ~keep, "cpr_w"] = 999.0
        return X
    return f
WM["v"] = 0.0
base = R3.six(); out = {"today": base}
print("TODAY", {k: R3.fmt(v) for k, v in base.items()})
def today_pnl(cap, f, wm):
    WM["v"] = wm
    R = R3.L.replay(cap, "2026-11-20", "2026-11-20", T=f(T0), A=f(A0), H=f(H0), raw=True)
    R = R[R["skipped"].isna()] if "skipped" in R else R
    return round(float(R.pnl.sum())), len(R)
for which in ("all", "late", "noNoise_RL", "late_noNoise_RL"):
    f = prep(which)
    for wm in (0.25, 0.5, 0.85):
        WM["v"] = wm
        cells = R3.six(f(T0), f(A0), f(H0))
        b, d = R3.verdict(cells, base)
        fw = {c: R3.run(c, "fwd", f(T0), f(A0), f(H0)) for c in (200_000, 20_000)}
        t20 = {c: today_pnl(c, f, wm) for c in (200_000, 20_000)}
        out[f"{which}|{wm}"] = {"cells": cells, "better": b, "deeper": d, "fwd": fw, "today": t20}
        print(f"{which:16s} x{wm}: better {b}/6, deeper {d} | " + " ".join(f"{k.split('|')[0][:3]}k {k.split('|')[1][:4]} {R3.fmt(v)}" for k, v in cells.items()), flush=True)
        print(f"     fwd 19Oct-20Nov: 200k {R3.fmt(fw[200000])} (today {R3.fmt(R3.run(200000,'fwd'))}) | 20k {R3.fmt(fw[20000])} | 20 Nov P&L: 2L {t20[200000]} 20k {t20[20000]}", flush=True)
json.dump(out, open("/tmp/r104_c2.json", "w"), indent=1, default=str)
