"""Round 107 B: Autopilot's own trades (today's rules, 1 ITM per lot) split by the pre-open cues: with / against the overnight
US move, with / against the walk-forward cue model's predicted day direction, crude / gold / USD-INR moves, previous day.
Then the best-looking split replayed as a skip and a size tilt on the realised replay (house bar)."""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/round106")
import numpy as np, pandas as pd
import r106lib as X
T, A, H = X.load_set("base")
T = T[(T.date >= "2023-11-06") & (T.date <= "2026-10-30")].copy()
Z = {s: pd.read_csv(f"/tmp/r107_{s}.csv", index_col=0, parse_dates=True) for s in ("NIFTY", "BANKNIFTY", "SENSEX")}
cols = ["SP500", "NASDAQ", "DOW", "CRUDE", "USDINR", "GOLD", "NIKKEI_open", "HSI_open", "VIX_chg", "prev_day"]
# walk-forward predicted day direction per index (monthly refit, training only on earlier months)
P = {}
for s, z in Z.items():
    pr = pd.Series(index=z.index, dtype=float)
    for m in sorted(set(z.index.to_period("M"))):
        tr = z[z.index.to_period("M") < m]
        if len(tr) < 200: continue
        te = z[z.index.to_period("M") == m]
        b = np.linalg.lstsq(np.c_[np.ones(len(tr)), tr[cols].values], tr.day.values, rcond=None)[0]
        pr.loc[te.index] = np.c_[np.ones(len(te)), te[cols].values] @ b
    P[s] = pr
def feat(r, k):
    z = Z[r.sym]; d = pd.Timestamp(r.date)
    return z[k].get(d, np.nan) if k in z else np.nan
sg = np.where(T.side == "CALL", 1, -1)
for k in cols + ["gap"]:
    T[k] = [Z[s][k].get(pd.Timestamp(d), np.nan) for s, d in zip(T.sym, T.date)]
T["pred"] = [P[s].get(pd.Timestamp(d), np.nan) for s, d in zip(T.sym, T.date)]
T["y"] = T.date.str[:4]
out = {}
def split(name, cond_with, cond_against):
    rows = []
    for lab, m in (("with", cond_with), ("against", cond_against), ("neither", ~(cond_with | cond_against))):
        g = T[m]
        rows.append((lab, len(g), g.pnl.mean(), {y: round(g[g.y == y].pnl.mean(), 0) for y in ("2023", "2024", "2025", "2026")}, g.R.mean()))
    print(f"\n{name}")
    for lab, n, avg, yy, rr in rows:
        print(f"   {lab:8s} {n:5d} trades  avg Rs {avg:+6.0f}/lot  R {rr:+.3f}  by year {yy}")
    out[name] = [(l, n, round(a, 1), yy) for l, n, a, yy, _ in rows]
us = T.SP500
split("Overnight S&P 500 move >= 0.5% (trade with / against it)", (np.abs(us) >= 0.005) & (np.sign(us) == sg), (np.abs(us) >= 0.005) & (np.sign(us) != sg))
split("Overnight Nasdaq move >= 1%", (np.abs(T.NASDAQ) >= 0.01) & (np.sign(T.NASDAQ) == sg), (np.abs(T.NASDAQ) >= 0.01) & (np.sign(T.NASDAQ) != sg))
split("Walk-forward cue model's predicted day direction (|pred| above median)", (np.abs(T.pred) >= T.pred.abs().median()) & (np.sign(T.pred) == sg), (np.abs(T.pred) >= T.pred.abs().median()) & (np.sign(T.pred) != sg))
split("Crude moved >= 2% overnight (trade with crude's direction / against)", (np.abs(T.CRUDE) >= 0.02) & (np.sign(T.CRUDE) == sg), (np.abs(T.CRUDE) >= 0.02) & (np.sign(T.CRUDE) != sg))
split("Crude moved >= 2% overnight: CALL after crude DOWN (good for India) / CALL after crude UP", (np.abs(T.CRUDE) >= 0.02) & (np.sign(T.CRUDE) == -sg), (np.abs(T.CRUDE) >= 0.02) & (np.sign(T.CRUDE) == sg))
split("Gold moved >= 1.5% overnight (with / against)", (np.abs(T.GOLD) >= 0.015) & (np.sign(T.GOLD) == sg), (np.abs(T.GOLD) >= 0.015) & (np.sign(T.GOLD) != sg))
split("Rupee weakened >= 0.3% (USD/INR up): PUT / CALL", (T.USDINR >= 0.003) & (sg == -1), (T.USDINR >= 0.003) & (sg == 1))
split("Asian open (Nikkei) >= 1% (with / against)", (np.abs(T.NIKKEI_open) >= 0.01) & (np.sign(T.NIKKEI_open) == sg), (np.abs(T.NIKKEI_open) >= 0.01) & (np.sign(T.NIKKEI_open) != sg))
split("Previous day >= 1% (with / against)", (np.abs(T.prev_day) >= 0.01) & (np.sign(T.prev_day) == sg), (np.abs(T.prev_day) >= 0.01) & (np.sign(T.prev_day) != sg))
split("Gap >= 0.75% (with / against the gap)", (np.abs(T.gap) >= 0.0075) & (np.sign(T.gap) == sg), (np.abs(T.gap) >= 0.0075) & (np.sign(T.gap) != sg))
T.to_pickle("/tmp/r107_T.pkl")
json.dump(out, open(os.path.dirname(os.path.abspath(__file__)) + "/r107_b.json", "w"), indent=1, default=str)
