"""Round 15 - data packs for an LLM trading agent (TradingAgents-style) with NO future information.

For each of the last N sessions and each index, writes what a trader could know at 09:30 IST:
last 10 daily bars, today's open and gap, yesterday's levels (PDH/PDL/close, Camarilla, CPR, pivot),
India VIX and its change, overnight global moves, previous-day put-call ratio from the NSE bhavcopy,
days to expiry, the first three 5-minute bars of the day (09:15-09:30), ATR and the noise-band levels.
Outcomes are NOT included. usage: python agent_pack.py 60   -> agent_packs.json
"""
import json, os, sys
from datetime import date, timedelta
import numpy as np
import pandas as pd

os.environ.setdefault("FNO_CALIB", "1")
import engine as E

N = int(sys.argv[1]) if len(sys.argv) > 1 else 60
SYMS = ("NIFTY", "BANKNIFTY")
D = {s: E.load_days(s) for s in SYMS}
dates = [d.d for d in D["NIFTY"]][-N:]


def daily(name):
    x = pd.read_csv(f"data/{name}_1d.csv.gz", parse_dates=["ts"]).set_index("ts").sort_index()
    return x


G = {k: daily(k) for k in ("SP500", "NASDAQ", "DOW", "NIKKEI", "HANGSENG", "CRUDE", "USDINR")}
B = pd.read_csv("data/OPT_bhavcopy.csv.gz", parse_dates=["date", "expiry"])


def global_cues(d):
    out = {}
    for k, x in G.items():
        prev = x[x.index.date < d]
        if len(prev) >= 2:
            out[k] = round(float(prev["close"].iloc[-1] / prev["close"].iloc[-2] - 1) * 100, 2)
    return out


def pcr_prev(sym, d):
    b = B[(B["sym"] == sym) & (B["date"].dt.date == d - timedelta(days=1))]
    if b.empty:
        prevd = B[(B["sym"] == sym) & (B["date"].dt.date < d)]["date"].max()
        b = B[(B["sym"] == sym) & (B["date"] == prevd)]
    if b.empty:
        return None
    e = b["expiry"].min()
    b = b[b["expiry"] == e]
    ce, pe = b[b.opt == "CE"]["oi"].sum(), b[b.opt == "PE"]["oi"].sum()
    maxce = b[b.opt == "CE"].sort_values("oi").iloc[-1]["strike"] if len(b[b.opt == "CE"]) else None
    maxpe = b[b.opt == "PE"].sort_values("oi").iloc[-1]["strike"] if len(b[b.opt == "PE"]) else None
    return {"pcr": round(pe / ce, 2) if ce else None, "max_call_oi_strike": maxce, "max_put_oi_strike": maxpe}


packs = []
for sym in SYMS:
    days = D[sym]
    idx = {d.d: i for i, d in enumerate(days)}
    for d in dates:
        i = idx.get(d)
        if i is None or i < 10:
            continue
        day = days[i]
        hist = [{"date": x.d.isoformat(), "open": round(x.o[0], 1), "high": round(max(x.h), 1), "low": round(min(x.l), 1), "close": round(x.c[-1], 1)} for x in days[i - 10:i]]
        bars = [{"end": f"{(day.t[k]+5)//60:02d}:{(day.t[k]+5)%60:02d}", "o": round(day.o[k], 1), "h": round(day.h[k], 1), "l": round(day.l[k], 1), "c": round(day.c[k], 1)} for k in range(3)]
        up, lo = E.noise_bands(day, 1.75)
        k945 = next((k for k in range(len(day.t)) if day.t[k] + 5 == 585), 6)
        packs.append({"symbol": sym, "date": d.isoformat(), "weekday": d.strftime("%A"), "expiry": day.expiry.isoformat(), "days_to_expiry": (day.expiry - d).days,
                      "prev": {"close": round(day.prev_close, 1), "high": round(day.prev_high, 1), "low": round(day.prev_low, 1)},
                      "today_open": round(day.o[0], 1), "gap_pct": round(day.gap, 2), "bars_0915_0930": bars,
                      "levels": {"camarilla": {k: round(v, 1) for k, v in day.cam.items()}, "pivot": round(day.pivot, 1), "cpr_width_pct": round(day.cpr_w, 2),
                                 "noise_band_at_0945": [round(lo[k945], 1), round(up[k945], 1)] if not np.isnan(up[k945]) else None,
                                 "atr_5min": round(day.atr[2], 1)},
                      "vix": {"level": round(day.vix[0], 2), "change_pct_vs_yesterday": round(day.vix_chg, 2)},
                      "global_prev_session_pct": global_cues(d), "options_prev_day": pcr_prev(sym, d), "last_10_days": hist})
json.dump(packs, open("agent_packs.json", "w"), indent=0)
print(len(packs), "packs", dates[0], "..", dates[-1])
