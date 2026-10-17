"""Round 3: 52-week-high breakout book with a market-regime exit (sell at the next open once the equal-weight market index closes
below its 100-day average), longer holds, fewer slots; and plateau checks around the app's dip-rule exit."""
import json, sys, numpy as np, pandas as pd
import stock_search as S
import stock_search_r2 as R
MK = S.MKT_UP.values

def sim_regime(w, x):
    out, kout = R.sim_full(w, x)
    if not x.get("regime"): return out, kout
    n = len(w["e"]); ti = w["ti"]
    for j in range(n):
        for k in range(int(kout[j])):
            t = ti[j] + 1 + k
            if t < len(MK) and not MK[t]:
                px = w["o"][j, k + 1] if k + 1 < w["o"].shape[1] and np.isfinite(w["o"][j, k + 1]) else w["c"][j, k]
                out[j], kout[j] = px, k + 1; break
    return out, kout
R.sim_full_orig = R.sim_full
_base = R.sim_full
def patched(w, x):
    if x.get("regime"):
        o, k = _base(w, x)
        n = len(w["e"]); ti = w["ti"]
        for j in range(n):
            for kk in range(int(k[j])):
                t = ti[j] + 1 + kk
                if t < len(MK) and not MK[t]:
                    px = w["o"][j, kk + 1] if np.isfinite(w["o"][j, kk + 1]) else w["c"][j, kk]
                    o[j], k[j] = px, kk + 1; break
        return o, k
    return _base(w, x)
R.sim_full = patched
X = lambda sl, tg, mode, hold, **kw: dict(sl=sl, tg=tg, mode=mode, hold=hold, **kw)
S.HMAX = 61
APP = X(("none", 0), ("pct", 0.05), "exit", 5)
wd = S.windows(S.E["drop3>=10% (current rule)"]); rk_d = S.R3.values[wd["ti"], wd["si"]]
wb = S.windows(S.E["52-week high on 1.5x volume"] & S.MKTUP); rk_b = -S.VX.values[wb["ti"], wb["si"]]
res = {}
print("== dip rule exit plateau (portfolio, 10 slots)")
for tgp in (0.03, 0.04, 0.05, 0.06, 0.07):
    for h in (3, 4, 5, 6, 7):
        e, tr = R.portfolio([("cur", wd, X(("none", 0), ("pct", tgp), "exit", h), rk_d, 10)])
        k = f"dip target {tgp*100:.0f}% hold {h}"; res[k] = R.report(k, e, tr)
print("== breakout books")
for nm, x, cap in [("brk hold 40", X(("none",0),("none",0),"exit",40), 10),
                   ("brk hold 60 + regime exit", X(("none",0),("none",0),"exit",60, regime=True), 10),
                   ("brk hold 40 + regime exit", X(("none",0),("none",0),"exit",40, regime=True), 10),
                   ("brk SL12% hold 60 + regime exit", X(("pct",0.12),("none",0),"exit",60, regime=True), 10),
                   ("brk SL12% +20% trail, 60 + regime", X(("pct",0.12),("pct",0.20),"trail",60, regime=True), 10)]:
    e, tr = R.portfolio([("brk", wb, x, rk_b, cap)]); res[nm] = R.report(nm, e, tr)
for nm, x, cap in [("dip first + brk 60 regime (max 3)", X(("none",0),("none",0),"exit",60, regime=True), 3),
                   ("dip first + brk 60 regime (max 5)", X(("none",0),("none",0),"exit",60, regime=True), 5),
                   ("dip first + brk SL12% 60 regime (max 3)", X(("pct",0.12),("none",0),"exit",60, regime=True), 3)]:
    e, tr = R.portfolio([("cur", wd, APP, rk_d, 10), ("brk", wb, x, rk_b, cap)]); res[nm] = R.report(nm, e, tr)
json.dump(res, open("r3.json", "w"), indent=1, default=str)
