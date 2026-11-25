"""Round 107 C: the pre-open cue splits that held per lot, replayed as rules (realised replay, house bar, then 5 sizes + forward)."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/round106")
import numpy as np, pandas as pd
import r106lib as X
T, A, H = X.load_set("base"); out = {}
D = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/data"
gl = pd.read_csv(f"{D}/global_long_1d.csv.gz"); gl["date"] = pd.to_datetime(gl.ts.astype(str).str[:10])
G = {s: g.set_index("date").sort_index() for s, g in gl.groupby("sym")}
fx = pd.read_csv(f"{D}/USDINR_1d.csv.gz"); fx["date"] = pd.to_datetime(fx.ts.astype(str).str[:10]); fx = fx.set_index("date").sort_index()
def before(s, d):
    r = s.close.pct_change(); i = s.index.searchsorted(pd.Timestamp(d)) - 1
    return float(r.iloc[i]) if i >= 1 else np.nan
def nk_open(d):
    s = G["^N225"]; d = pd.Timestamp(d)
    if d not in s.index: return np.nan
    i = s.index.get_loc(d); return float(s.open.iloc[i] / s.close.iloc[i - 1] - 1) if i >= 1 else np.nan
days = sorted(T.date.unique())
spx = {d: before(G["^GSPC"], d) for d in days}; inr = {d: before(fx, d) for d in days}; nk = {d: nk_open(d) for d in days}
T = T.assign(spx=T.date.map(spx), inr=T.date.map(inr), nk=T.date.map(nk))
print("days with an S&P value:", sum(1 for v in spx.values() if v == v), "of", len(days))
sg = lambda r: 1 if r.side == "CALL" else -1
withus = lambda r, th=0.005: r.spx == r.spx and abs(r.spx) >= th and np.sign(r.spx) == sg(r)
against = lambda r, th=0.005: r.spx == r.spx and abs(r.spx) >= th and np.sign(r.spx) != sg(r)
R = lambda nm, **kw: X.report(nm, T, A, H, out, **kw)
print("BASE  " + " | ".join(f"{k} {X.fmt(v)}" for k, v in X.base_grid().items()), flush=True)
for th in (0.005, 0.0075, 0.01):
    R(f"skip trades WITH an S&P move >= {th*100:g}%", skip=lambda r, th=th: withus(r, th))
    R(f"0.5x on trades WITH an S&P move >= {th*100:g}%", mult=lambda r, th=th: 0.5 if withus(r, th) else 1.0)
for m in (1.25, 1.5):
    R(f"{m}x on trades AGAINST an S&P move >= 0.5%", mult=lambda r, m=m: m if against(r) else 1.0)
R("0.5x WITH + 1.25x AGAINST (S&P >= 0.5%)", mult=lambda r: 0.5 if withus(r) else (1.25 if against(r) else 1.0))
R("skip CALLs after rupee weakens >= 0.3%", skip=lambda r: r.inr == r.inr and r.inr >= 0.003 and r.side == "CALL")
R("skip trades WITH a Nikkei open >= 1%", skip=lambda r: r.nk == r.nk and abs(r.nk) >= 0.01 and np.sign(r.nk) == sg(r))
json.dump(out, open(os.path.dirname(os.path.abspath(__file__)) + "/r107_c.json", "w"), indent=1, default=str)
T.to_pickle("/tmp/r107_Tc.pkl")
