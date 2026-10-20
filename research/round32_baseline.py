"""Round 32 baseline: the current Autopilot rules' trades (round 27 set + the Fibonacci breakout on Nifty / Sensex from Rs 2 lakh,
round 29), same harnesses and costs as rounds 27-29. Writes /tmp/round32_live.pkl (one row per trade, with "u" = Rs 2,000 risk)."""
import os, sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import time
import pandas as pd
import round25_harness as H25, round25_trend as TB25, round29_harness as H29
from round29_levels import levels

VIX = H25.load("INDIAVIX")["close"]; BIG = 1e9
_B = dict(cam_tstop=45, noise_trail="band_vwap", overlap=False, reentry=0, sizing="one_lot")
_T = dict(t0=time(10, 45), share=0.8, stop="vwap", mult=1.75, be=1.0, sizing="one_lot")
rows = []
for s, cam in [("NIFTY", True), ("BANKNIFTY", True), ("SENSEX", False)]:
    rows += [{**r, "sym": s, "slot": "main"} for r in H25.run(s, H25.load(s), VIX, {**_B, **({} if cam else {"cam_vix_max": -1})}, capital=BIG)]
for s in ("NIFTY", "SENSEX"):
    rows += [{**r, "sym": s, "slot": "trend"} for r in TB25.run(s, H25.load(s), VIX, _T, capital=BIG)]


def prev(hist):
    last = hist[hist.index.date == hist.index[-1].date()]
    return pd.Series(dict(o=float(last.open.iloc[0]), h=float(last.high.max()), l=float(last.low.min()), c=float(last.close.iloc[-1])))


def fib(hist):
    L = levels(prev(hist))["fibonacci"]; return dict(h4=L["R2"], l4=L["S2"], h3=L["R1"], l3=L["S1"])


FB = dict(cam_tstop=45, noise_trail="band_vwap", overlap=True, reentry=0, sizing="one_lot", noise_vix_max=-1,
          strats=("camarilla",), levels_fn=fib)
for s in ("NIFTY", "SENSEX"):
    rows += [{**r, "sym": s, "slot": "fib"} for r in H29.run(s, H29.load(s), VIX, FB, capital=BIG)]
T = pd.DataFrame(rows); T["u"] = T.pnl / T.risk_lot * 2000
T.to_pickle("/tmp/round32_live.pkl")
print(T.groupby(["slot", T.date.str[:4]]).u.sum().round().unstack())
