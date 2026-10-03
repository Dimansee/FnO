"""Adds the 'rule-style' trade labels to ai_rows.parquet: at every bar, a long and a short with the
noise strategy's exits (stop 2 ATR, target 4R, trailing exit at the half-hour checks on band/VWAP,
square-off 15:15). R_T_L / R_T_S in R units, X_T_L / X_T_S exit bar."""
import os
import numpy as np, pandas as pd
os.environ.setdefault("FNO_CALIB", "1")
import engine as E
from ai_data import SYMS

df = pd.read_parquet("ai_rows.parquet")
df["date"] = pd.to_datetime(df["date"]).dt.date
DAYS = {}
for s in SYMS:
    for d in E.load_days(s):
        DAYS[(s, d.d)] = d


def bands(d):
    sig = np.asarray(d.sig, dtype=float)
    o0, pc = d.o[0], d.prev_close
    return max(o0, pc) * (1 + 1.75 * sig), min(o0, pc) * (1 - 1.75 * sig)


RL, RS, XL, XS = [], [], [], []
for sym_id, dt, i in zip(df["sym"].values, df["date"].values, df["i"].values):
    d = DAYS[(SYMS[sym_id], dt)]
    up, lo = bands(d)
    a = d.atr[i]
    entry = d.c[i]
    meta = E.META[SYMS[sym_id]]
    for side, R, X in ((1, RL, XL), (-1, RS, XS)):
        s = E.Sig(int(i), side, entry - side * 2 * a, entry + side * 8 * a, "noise", 0, 0, "ai")
        k, px, why = E.simulate(d, s, meta, {}, up, lo)
        R.append(side * (px - entry) / (2 * a)); X.append(k)
df["R_T_L"], df["R_T_S"], df["X_T_L"], df["X_T_S"] = RL, RS, XL, XS
df.to_parquet("ai_rows.parquet", index=False)
print(df[["R_T_L", "R_T_S"]].describe().round(3))
