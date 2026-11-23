"""E: sizing and regime - VIX-scaled size, VIX spike filter, confluence, trend-strength gate on Noise."""
import json
import numpy as np, pandas as pd
import r106lib as X
T0, A, H = X.load_set("base")
T = X.features(T0); out = {}
print("BASE  " + " | ".join(f"{k} {X.fmt(v)}" for k, v in X.base_grid().items()), flush=True)
R = lambda nm, **kw: X.report(nm, T, A, H, out, **kw)
print("\nVIX at entry - per-lot rupees by VIX bucket and strategy")
T["vb"] = pd.cut(T.vix, [0, 12, 13, 14, 15, 16, 17, 18, 20, 40])
print(T.pivot_table(index="vb", columns="strategy", values="pnl", aggfunc="sum", observed=True).round(0).to_string())
print(T.pivot_table(index="vb", columns="strategy", values="pnl", aggfunc="count", observed=True).to_string())
cap = lambda r: 18.0 if r.strategy == "Noise band" else 20.0
R("VIX within 2 of the strategy's cap: 0.67x", mult=lambda r: 0.67 if r.vix >= cap(r) - 2 else 1.0)
R("VIX within 2 of the strategy's cap: 0.5x", mult=lambda r: 0.5 if r.vix >= cap(r) - 2 else 1.0)
R("VIX >= 16: 0.67x (all strategies)", mult=lambda r: 0.67 if r.vix >= 16 else 1.0)
R("linear: 1x at VIX 14 -> 0.5x at VIX 20", mult=lambda r: float(np.clip(1 - 0.5 * (r.vix - 14) / 6, 0.5, 1.0)))
print("\nVIX spike (VIX at entry vs previous close)")
for th in (5, 8, 10, 15):
    z = T[T.vix_chg > th]; print(f"  VIX up > {th}%: {len(z)} trades, per-lot {z.pnl.sum():+.0f}")
    R(f"block entries when VIX up > {th}% on the day", skip=lambda r, th=th: r.vix_chg > th)
print("\nConfluence (another strategy, same index, same / opposite way earlier today)")
for nm, m in (("agree", T.agree == 1), ("conflict", T.conflict == 1)):
    z = T[m]; print(f"  {nm}: {len(z)} trades per-lot {z.pnl.sum():+.0f} avg {z.pnl.mean():+.0f} (others avg {T[~m].pnl.mean():+.0f})")
R("agree: 1.5x", mult=lambda r: 1.5 if r.agree else 1.0)
R("agree: 1.25x", mult=lambda r: 1.25 if r.agree else 1.0)
R("conflict: skip", skip=lambda r: bool(r.conflict))
R("agree 1.25x + conflict skip", mult=lambda r: 1.25 if r.agree else 1.0, skip=lambda r: bool(r.conflict))
print("\nTrend-strength gate on Noise (previous day's daily ADX14, previous day's range / ATR)")
nz = T[T.strategy == "Noise band"].copy()
nz["ab"] = pd.cut(nz.adx_prev, [0, 15, 20, 25, 30, 100]); nz["rb"] = pd.cut(nz.prev_range_atr, [0, 0.6, 0.8, 1.0, 1.3, 10])
print(nz.assign(y=nz.date.str[:4]).pivot_table(index="ab", columns="y", values="pnl", aggfunc="sum", observed=True).round(0).to_string())
print(nz.assign(y=nz.date.str[:4]).pivot_table(index="rb", columns="y", values="pnl", aggfunc="sum", observed=True).round(0).to_string())
for a in (15, 20, 25):
    R(f"Noise only when ADX >= {a}", skip=lambda r, a=a: r.strategy == "Noise band" and not (r.adx_prev >= a))
for q in (0.6, 0.8):
    R(f"Noise only when prev range >= {q} ATR", skip=lambda r, q=q: r.strategy == "Noise band" and not (r.prev_range_atr >= q))
R("Noise: 0.5x when ADX < 20", mult=lambda r: 0.5 if r.strategy == "Noise band" and r.adx_prev < 20 else 1.0)
json.dump(out, open("r106_e.json", "w"), indent=1, default=str)
