"""Nightly stock scan (round 16 rule) -> research/data/stock_picks.json, read by the app's Stocks tab.

Rule (long-only, delivery, paper): close above its 200-day average AND down more than 10% over the last three closes,
among the 150 most liquid Nifty 500 names (median 50-day turnover). Buy at the next open with equity/10 per name,
+5% limit sell at once, sell at the open after the 5th close if not filled. No stop.
The file also settles earlier picks from the data, so the app can show what each recommendation did.
"""
import json, os
from datetime import date
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "stock_picks.json")
DROP, MAXPOS, HOLD, TARGET, LIQ_N, LOOKBACK_DAYS = 0.10, 10, 5, 0.05, 150, 60

d = pd.read_csv(os.path.join(HERE, "data", "stocks_1d.csv.gz"), parse_dates=["date"])
f = d["adj close"] / d["close"]
for c in ("open", "high", "low", "close"):
    d[c] = d[c] * f
C = d.pivot(index="date", columns="sym", values="close"); O = d.pivot(index="date", columns="sym", values="open")
H = d.pivot(index="date", columns="sym", values="high"); V = d.pivot(index="date", columns="sym", values="volume")
RAW = d.pivot(index="date", columns="sym", values="close")           # unadjusted close for display (same as adjusted at the end)
names = pd.read_csv(os.path.join(HERE, "data", "stocks_list.csv")).set_index("Symbol")
SMA200 = C.rolling(200, min_periods=200).mean()
R3 = C / C.shift(3) - 1
TURN = (C * V).rolling(50, min_periods=20).median()
dates = C.index


def picks_on(i):
    """Signals at close of dates[i] (to be bought at the open of dates[i+1])."""
    t = TURN.iloc[i]
    liquid = set(t.nlargest(LIQ_N).index)
    sig = (R3.iloc[i] < -DROP) & (C.iloc[i] > SMA200.iloc[i])
    rows = []
    for s in sig[sig].index:
        if s not in liquid or pd.isna(C.iloc[i][s]):
            continue
        rows.append({"sym": s, "name": str(names["Company Name"].get(s, s)), "industry": str(names["Industry"].get(s, "")),
                     "close": round(float(C.iloc[i][s]), 2), "drop_3d_pct": round(float(R3.iloc[i][s] * 100), 1),
                     "above_200dma_pct": round(float((C.iloc[i][s] / SMA200.iloc[i][s] - 1) * 100), 1),
                     "turnover_cr": round(float(t[s] / 1e7), 1)})
    rows.sort(key=lambda r: r["drop_3d_pct"])
    return rows[:MAXPOS]


def settle(sym, i):
    """Outcome of a pick signalled at close i: entry = open i+1, +5% limit, else open after the 5th close."""
    if i + 1 >= len(dates):
        return None
    entry = float(O.iloc[i + 1][sym])
    if pd.isna(entry):
        return None
    tgt = entry * (1 + TARGET)
    for k in range(i + 1, min(i + 1 + HOLD, len(dates))):
        hi = H.iloc[k][sym]
        if not pd.isna(hi) and hi >= tgt:
            px = max(tgt, float(O.iloc[k][sym])) if k > i + 1 else tgt
            return {"entry": round(entry, 2), "exit": round(px, 2), "exit_date": dates[k].date().isoformat(), "how": "target +5%", "ret_pct": round((px / entry - 1) * 100 - 0.2, 2)}
    k = i + 1 + HOLD
    if k >= len(dates):
        return {"entry": round(entry, 2), "open": True, "last": round(float(C.iloc[-1][sym]), 2), "ret_pct": round((float(C.iloc[-1][sym]) / entry - 1) * 100 - 0.2, 2),
                "days_held": len(dates) - (i + 1)}
    px = float(O.iloc[k][sym])
    return {"entry": round(entry, 2), "exit": round(px, 2), "exit_date": dates[k].date().isoformat(), "how": "time exit (6th open)", "ret_pct": round((px / entry - 1) * 100 - 0.2, 2)}


last = len(dates) - 1
today = picks_on(last)
history = []
for i in range(max(0, last - LOOKBACK_DAYS), last):
    ps = picks_on(i)
    if not ps:
        continue
    for p in ps:
        p["result"] = settle(p["sym"], i)
    history.append({"signal_date": dates[i].date().isoformat(), "buy_date": dates[i + 1].date().isoformat() if i + 1 < len(dates) else None, "picks": ps})
closed = [p["result"]["ret_pct"] for h in history for p in h["picks"] if p.get("result") and not p["result"].get("open")]
rec = {"n": len(closed), "win_pct": round(100 * float(np.mean([r > 0 for r in closed])), 0) if closed else None,
       "avg_ret_pct": round(float(np.mean(closed)), 2) if closed else None, "target_hit_pct": round(100 * sum(1 for h in history for p in h["picks"] if p.get("result") and p["result"].get("how", "").startswith("target")) / len(closed), 0) if closed else None}
out = {"generated": date.today().isoformat(), "data_to": dates[last].date().isoformat(), "buy_on": "next trading day's open",
       "rule": "Close above the 200-day average and down more than 10% over the last three closes, among the 150 most liquid Nifty 500 names. "
               "Buy at the next open (equity ÷ 10 per name), +5% limit sell at once, sell at the open after the 5th close if not filled. No stop. Paper only.",
       "expected": "Backtest 2021-26: 64-69% of trades hit +5%, average +1.4% a trade after costs, ~7 signals a month, 1-3 names open at a time; "
                   "plan on 8-12% a year on the allotted capital. Survivorship-exposed (today's index list), so treat the record as an upper bound.",
       "today": today, "record_last_60_sessions": rec, "history": history}
json.dump(out, open(OUT, "w"), indent=1)
print(f"{dates[last].date()}: {len(today)} pick(s) {[p['sym'] for p in today]}; last-60-session record {rec}")
