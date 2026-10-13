"""Nightly market view for the app's Stocks tab -> research/data/stock_market.json (one compact row per Nifty 500 stock).

Per stock: industry, close, 1-day / 3-day / 1-month / 3-month change, distance from the 200-day average and the 52-week
high, RSI(14), median daily turnover, and - when Yahoo Finance has it - the analyst consensus (mean / high / low 12-month
target, number of analysts, rating). The consensus is third-party context, NOT a tested signal; it is refreshed at most
once a week per stock (research/data/stock_analyst.json) because it needs one request per name.
"""
import json, os, time
from datetime import date, datetime
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
d = pd.read_csv(os.path.join(DATA, "stocks_1d.csv.gz"), parse_dates=["date"])
f = d["adj close"] / d["close"]
for c in ("open", "high", "low", "close"):
    d[c] = d[c] * f
C = d.pivot(index="date", columns="sym", values="close")
H = d.pivot(index="date", columns="sym", values="high")
V = d.pivot(index="date", columns="sym", values="volume")
RAWC = d.pivot(index="date", columns="sym", values="close")   # unadjusted = adjusted on the last day
names = pd.read_csv(os.path.join(DATA, "stocks_list.csv")).set_index("Symbol")
last = C.index[-1]


def rsi(s, n=14):
    ch = s.diff()
    up = ch.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-ch.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


sma200 = C.rolling(200, min_periods=120).mean().iloc[-1]
hi52 = H.rolling(252, min_periods=120).max().iloc[-1]
turn = (C * V).rolling(50, min_periods=20).median().iloc[-1]
r14 = C.apply(rsi).iloc[-1]
c0, c1, c3, c21, c63 = C.iloc[-1], C.iloc[-2], C.iloc[-4], C.iloc[-22], C.iloc[-64]

# analyst consensus cache (refreshed weekly per name; needs the open internet of GitHub Actions)
AN = os.path.join(DATA, "stock_analyst.json")
an = json.load(open(AN)) if os.path.exists(AN) else {}
if os.environ.get("FETCH_ANALYST", "1") == "1":
    try:
        import yfinance as yf
        todo = [s for s in C.columns if (datetime.now() - datetime.fromisoformat(an.get(s, {}).get("at", "2000-01-01"))).days >= 7]
        print(f"analyst consensus: refreshing {len(todo)} names")
        for i, s in enumerate(todo):
            try:
                info = yf.Ticker(s + ".NS").info or {}
                an[s] = {"at": datetime.now().isoformat(timespec="seconds"), "target_mean": info.get("targetMeanPrice"), "target_high": info.get("targetHighPrice"),
                         "target_low": info.get("targetLowPrice"), "analysts": info.get("numberOfAnalystOpinions"), "rating": info.get("recommendationKey"),
                         "market_cap_cr": round(info["marketCap"] / 1e7) if info.get("marketCap") else None, "sector": info.get("sector")}
            except Exception as e:
                an[s] = {"at": datetime.now().isoformat(timespec="seconds"), "error": str(e)[:60]}
            if i % 25 == 0:
                json.dump(an, open(AN, "w"))
            time.sleep(0.3)
        json.dump(an, open(AN, "w"))
    except Exception as e:
        print("analyst fetch skipped:", e)

rows = []
for s in C.columns:
    if pd.isna(c0[s]):
        continue
    a = an.get(s, {})
    tgt = a.get("target_mean")
    cap = a.get("market_cap_cr")
    rows.append({"sym": s, "name": str(names["Company Name"].get(s, s)), "industry": str(names["Industry"].get(s, "Other")),
                 "close": round(float(c0[s]), 2),
                 "chg_1d": round(float(c0[s] / c1[s] - 1) * 100, 2) if not pd.isna(c1[s]) else None,
                 "chg_3d": round(float(c0[s] / c3[s] - 1) * 100, 2) if not pd.isna(c3[s]) else None,
                 "chg_1m": round(float(c0[s] / c21[s] - 1) * 100, 1) if not pd.isna(c21[s]) else None,
                 "chg_3m": round(float(c0[s] / c63[s] - 1) * 100, 1) if not pd.isna(c63[s]) else None,
                 "vs_200dma": round(float(c0[s] / sma200[s] - 1) * 100, 1) if not pd.isna(sma200[s]) else None,
                 "from_52w_high": round(float(c0[s] / hi52[s] - 1) * 100, 1) if not pd.isna(hi52[s]) else None,
                 "rsi14": round(float(r14[s]), 0) if not pd.isna(r14[s]) else None,
                 "turnover_cr": round(float(turn[s] / 1e7), 1) if not pd.isna(turn[s]) else None,
                 "cap_bucket": "Large" if cap and cap >= 100000 else "Mid" if cap and cap >= 30000 else "Small" if cap else None, "market_cap_cr": cap,
                 "analyst": {"target": tgt, "upside_pct": round((tgt / float(c0[s]) - 1) * 100, 1) if tgt else None, "high": a.get("target_high"), "low": a.get("target_low"),
                             "n": a.get("analysts"), "rating": a.get("rating")} if tgt else None})
ind = {}
for r in rows:
    g = ind.setdefault(r["industry"], {"n": 0, "chg_1d": [], "chg_1m": []})
    g["n"] += 1
    if r["chg_1d"] is not None: g["chg_1d"].append(r["chg_1d"])
    if r["chg_1m"] is not None: g["chg_1m"].append(r["chg_1m"])
industries = sorted([{"industry": k, "n": v["n"], "avg_1d": round(float(np.mean(v["chg_1d"])), 2) if v["chg_1d"] else None,
                      "avg_1m": round(float(np.mean(v["chg_1m"])), 1) if v["chg_1m"] else None} for k, v in ind.items()], key=lambda x: -(x["avg_1d"] or 0))
out = {"generated": date.today().isoformat(), "data_to": last.date().isoformat(), "stocks": rows, "industries": industries,
       "analyst_note": "Analyst targets are the Yahoo Finance consensus (12-month, mean of the covering analysts) shown for context - the same kind of number apps show as a 'target'. "
                       "They are not tested and not a signal here; the only tested stock rule is the Stocks tab's 10%-drop pick.",
       "analyst_coverage": sum(1 for r in rows if r["analyst"])}
json.dump(out, open(os.path.join(DATA, "stock_market.json"), "w"), separators=(",", ":"))
print(f"{len(rows)} stocks, {len(industries)} industries, analyst coverage {out['analyst_coverage']}, data to {last.date()}")
