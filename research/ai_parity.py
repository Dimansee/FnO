"""Does the app's AI path (fno/ai.py + fno/backtest.py) reproduce the research simulation on the same days?"""
import os, sys, time
os.environ.setdefault("FNO_CALIB", "1")
import numpy as np, pandas as pd
sys.path.insert(0, "..")
from fno import backtest as BT, config as C, ai as AI, ai_model as AM
import engine as E, ai_train as T
from ai_data import SYMS
sym = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
days = int(sys.argv[2]) if len(sys.argv) > 2 else 120
c = pd.read_csv(f"data/{sym}_5m_upstox.csv.gz", parse_dates=["ts"]).set_index("ts")
c.index = c.index.tz_localize("Asia/Kolkata") if c.index.tz is None else c.index
v = pd.read_csv("data/INDIAVIX_5m_upstox.csv.gz", parse_dates=["ts"]).set_index("ts")["close"]
v.index = v.index.tz_localize("Asia/Kolkata")
vd = pd.read_csv("data/INDIAVIX_1d.csv.gz", parse_dates=["ts"]).set_index("ts")["close"]
vd.index = vd.index.normalize()
sessions = sorted(set(c.index.date))
start = sessions[-(days + 22)]
c = c[c.index.date >= start]
t0 = time.time()
app = BT.run(sym, 200000, candles=c, vix_intraday=v[v.index >= c.index[0]], vix_daily=vd, lot=E.META[sym]["lot"], strategy="ai", days=days)
print("app AI backtest:", round(time.time() - t0, 1), "s |", {k: app["summary"].get(k) for k in ("trades", "net", "win_rate", "profit_factor")})
# research simulation with the exported model's predictions on the same days
df = pd.read_parquet("ai_rows.parquet"); df["date"] = pd.to_datetime(df["date"]).dt.date
for s in SYMS:
    for d in E.load_days(s):
        T.DAYS[(s, d.d)] = d
sub = df[(df["sym"] == SYMS.index(sym)) & (df["date"] >= sessions[-days])]
X = AI._matrix([r for r in sub.drop(columns=[c_ for c_ in sub.columns if c_.startswith(("R_", "X_", "fwd"))]).to_dict("records")])
er = np.full((len(df), 6), np.nan); er[sub.index.values] = AM.expected_r(X).reshape(len(sub), 6)
tr = T.simulate(df, er, AI.threshold(), T.DAYS, price=True, sel=(df["sym"] == SYMS.index(sym)) & (df["date"] >= sessions[-days]))
p = np.array([t["pnl"] for t in tr])
print("research sim     :", {"trades": len(tr), "net": round(p.sum()), "win_rate": round((p > 0).mean() * 100, 1)})
a = app["trades"]
print("app trades sample:", a[["date", "entry_time", "side", "spot_in", "spot_out", "reason", "pnl"]].head(5).to_string(index=False) if len(a) else "none")
print("research sample  :", [(t["date"], t["in"], t["side"], round(t["pnl"])) for t in tr[:5]])
