"""Round 4 robustness of the breakout + market-regime exit: regime average length, hold, slot cap; and an out-of-universe check
on stocks OUTSIDE the Nifty 500 (never used in rounds 1-3)."""
import sys, json, numpy as np, pandas as pd
import stock_search as S
import stock_search_r2 as R
X = lambda sl, tg, mode, hold, **kw: dict(sl=sl, tg=tg, mode=mode, hold=hold, **kw)
S.HMAX = 91
lst = pd.read_csv("/home/claude/data6y/all_list.csv").fillna("")
n500 = set(lst[lst.Indices.str.contains("N500")].Symbol.astype(str))
outside = "--outside" in sys.argv
colmask = pd.Series([ (s not in n500) if outside else (s in n500) for s in S.C.columns], index=S.C.columns)
EWI = S.EWI
_base = R.sim_full
def make_sim(mk):
    def f(w, x):
        o, k = _base(w, x)
        if not x.get("regime"): return o, k
        for j in range(len(w["e"])):
            for kk in range(int(k[j])):
                t = w["ti"][j] + 1 + kk
                if t < len(mk) and not mk[t]:
                    px = w["o"][j, kk + 1] if np.isfinite(w["o"][j, kk + 1]) else w["c"][j, kk]
                    o[j], k[j] = px, kk + 1; break
        return o, k
    return f
res = {}
APP = X(("none", 0), ("pct", 0.05), "exit", 5)
cm = lambda m: m & pd.DataFrame(np.repeat(colmask.values[None, :], len(S.C), axis=0), index=S.C.index, columns=S.C.columns)
wd = S.windows(cm(S.E["drop3>=10% (current rule)"])); rk_d = S.R3.values[wd["ti"], wd["si"]]
e, tr = R.portfolio([("cur", wd, APP, rk_d, 10)]); res["dip only"] = R.report("dip only (app rule)", e, tr)
for ma in (50, 100, 150, 200):
    mkup = (EWI > EWI.rolling(ma).mean())
    MK = pd.DataFrame(np.repeat(mkup.values[:, None], S.C.shape[1], axis=1), index=S.C.index, columns=S.C.columns)
    R.sim_full = make_sim(mkup.values)
    wb = S.windows(cm(S.E["52-week high on 1.5x volume"] & MK)); rk_b = -S.VX.values[wb["ti"], wb["si"]]
    for hold in (40, 60, 90):
        x = X(("none", 0), ("none", 0), "exit", hold, regime=True)
        e, tr = R.portfolio([("brk", wb, x, rk_b, 10)]); k = f"breakout only, MA{ma}, hold {hold}"; res[k] = R.report(k, e, tr)
        for cap in (2, 3, 5):
            if hold != 60 and cap != 3: continue
            e, tr = R.portfolio([("cur", wd, APP, rk_d, 10), ("brk", wb, x, rk_b, cap)]); k = f"dip + breakout max {cap}, MA{ma}, hold {hold}"; res[k] = R.report(k, e, tr)
    R.sim_full = _base
json.dump(res, open("r4" + ("_outside" if outside else "") + ".json", "w"), indent=1, default=str)
