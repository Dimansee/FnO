"""Nightly market view for the app's Stocks tab.

Input: research/work/all_1y.csv.gz + all_list.csv (every investable NSE cash stock, 1 year; written by fetch_stocks.py) -
falls back to the Nifty 500 research file when the broad file is missing.
Output (research/work/out/, published to the `stock-data` branch by the workflow, so the daily churn never enters history):
  stock_market.json  one compact row per stock: name, industry, index membership, close, 1-day/3-day/1-month/3-month/1-year
                     change, vs 200-day average, from 52-week high, RSI(14), median daily turnover, market-cap bucket, and the
                     Yahoo Finance analyst consensus (12-month target, upside, rating, number of analysts) where it exists
  stock_series.json  last 250 sessions of close + volume per stock, for the charts
Analyst consensus is third-party context, NOT a tested signal; it is refreshed at most once a week per stock, up to
ANALYST_PER_RUN names a night (research/data/stock_analyst.json keeps the cache; committed to research-data).
"""
import json, os, time
from datetime import date, datetime
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA, WORK = os.path.join(HERE, "data"), os.path.join(HERE, "work")
OUTD = os.path.join(WORK, "out"); os.makedirs(OUTD, exist_ok=True)
ANALYST_PER_RUN = int(os.environ.get("ANALYST_PER_RUN", "450"))

src = os.path.join(WORK, "all_1y.csv.gz")
if os.path.exists(src):
    d = pd.read_csv(src, parse_dates=["date"]); lst = pd.read_csv(os.path.join(WORK, "all_list.csv")).set_index("Symbol")
    universe = "all NSE cash stocks (EQ series)"
else:
    d = pd.read_csv(os.path.join(DATA, "stocks_1d.csv.gz"), parse_dates=["date"]); lst = pd.read_csv(os.path.join(DATA, "stocks_list.csv")).set_index("Symbol")
    lst["Indices"] = "N500"; universe = "Nifty 500"
d = d.sort_values(["sym", "date"])
f = (d["adj close"] / d["close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
for c in ("open", "high", "low", "close"):
    d[c] = d[c] * f
C = d.pivot(index="date", columns="sym", values="close"); H = d.pivot(index="date", columns="sym", values="high"); V = d.pivot(index="date", columns="sym", values="volume")
# yfinance sometimes has the latest session for only a handful of names (partial day): use the last date that at least half the universe has
# ... or a placeholder row that just repeats yesterday's close and volume for everyone: drop trailing dates like that too
cover = C.notna().mean(axis=1)
fresh = ((C != C.shift(1)) | (V != V.shift(1))).where(C.notna()).mean(axis=1)
ok = (cover >= 0.5) & (fresh >= 0.2)
while len(C) > 1 and not ok.iloc[len(C) - 1]:
    C, H, V = C.iloc[:-1], H.iloc[:-1], V.iloc[:-1]
last = C.index[-1]
# drop names with no trade in the last 5 sessions (suspended / dead) - you cannot buy those today
alive = C.iloc[-5:].notna().any()
C, H, V = C.loc[:, alive], H.loc[:, alive], V.loc[:, alive]


def rsi(s, n=14):
    ch = s.diff(); up = ch.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(); dn = (-ch.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


sma200 = C.rolling(200, min_periods=120).mean().iloc[-1]
hi52 = H.rolling(252, min_periods=60).max().iloc[-1]
turn = (C * V).rolling(50, min_periods=10).median().iloc[-1]
r14 = C.apply(rsi).iloc[-1]
Cf = C.ffill()
c0 = Cf.iloc[-1]
lag = lambda n: Cf.iloc[-1 - n] if len(Cf) > n else pd.Series(np.nan, index=Cf.columns)
c1, c3, c21, c63, c250 = lag(1), lag(3), lag(21), lag(63), lag(min(250, len(Cf) - 1))

AN = os.path.join(DATA, "stock_analyst.json")
an = json.load(open(AN)) if os.path.exists(AN) else {}
if os.environ.get("FETCH_ANALYST", "1") == "1":
    try:
        import yfinance as yf
        stale = [s for s in C.columns if (datetime.now() - datetime.fromisoformat(an.get(s, {}).get("at", "2000-01-01"))).days >= 7]
        stale.sort(key=lambda s: -float(turn.get(s, 0) or 0))               # most traded first
        todo = stale[:ANALYST_PER_RUN]
        print(f"analyst consensus: {len(stale)} stale, refreshing {len(todo)}")
        for i, s in enumerate(todo):
            try:
                info = yf.Ticker(s + ".NS").info or {}
                an[s] = {"at": datetime.now().isoformat(timespec="seconds"), "target_mean": info.get("targetMeanPrice"), "target_high": info.get("targetHighPrice"),
                         "target_low": info.get("targetLowPrice"), "analysts": info.get("numberOfAnalystOpinions"), "rating": info.get("recommendationKey"),
                         "market_cap_cr": round(info["marketCap"] / 1e7) if info.get("marketCap") else None, "sector": info.get("sector"), "industry": info.get("industry"),
                         "pe": info.get("trailingPE"), "div_yield": info.get("dividendYield")}
            except Exception as e:
                an[s] = {"at": datetime.now().isoformat(timespec="seconds"), "error": str(e)[:60]}
            if i % 50 == 0:
                json.dump(an, open(AN, "w"))
            time.sleep(0.25)
        json.dump(an, open(AN, "w"))
    except Exception as e:
        print("analyst fetch skipped:", e)

def pct(a, b):
    return round(float(a / b - 1) * 100, 2) if not (pd.isna(a) or pd.isna(b) or b == 0) else None

rows = []
for s in C.columns:
    if pd.isna(c0[s]):
        continue
    a = an.get(s, {}); tgt = a.get("target_mean"); cap = a.get("market_cap_cr")
    ind = str(lst["Industry"].get(s, "") or "") if s in lst.index else ""
    if not ind or ind == "nan":
        ind = a.get("sector") or "Other"
    rows.append({"sym": s, "name": str(lst["Company Name"].get(s, s)) if s in lst.index else s, "industry": ind,
                 "idx": str(lst["Indices"].get(s, "") or "") if s in lst.index and "Indices" in lst.columns else "",
                 "close": round(float(c0[s]), 2), "chg_1d": pct(c0[s], c1[s]), "chg_3d": pct(c0[s], c3[s]), "chg_1m": pct(c0[s], c21[s]), "chg_3m": pct(c0[s], c63[s]), "chg_1y": pct(c0[s], c250[s]),
                 "vs_200dma": pct(c0[s], sma200[s]), "from_52w_high": pct(c0[s], hi52[s]),
                 "rsi14": round(float(r14[s])) if not pd.isna(r14[s]) else None,
                 "turnover_cr": round(float(turn[s] / 1e7), 2) if not pd.isna(turn[s]) else None,
                 "cap_bucket": "Large" if cap and cap >= 100000 else "Mid" if cap and cap >= 30000 else "Small" if cap else None, "market_cap_cr": cap,
                 "pe": round(a["pe"], 1) if a.get("pe") else None,
                 "analyst": {"target": tgt, "upside_pct": round((tgt / float(c0[s]) - 1) * 100, 1) if tgt else None, "high": a.get("target_high"), "low": a.get("target_low"),
                             "n": a.get("analysts"), "rating": a.get("rating")} if tgt else None})
rows = [r for r in rows if r["idx"] == "" or True]
ind = {}
for r in rows:
    g = ind.setdefault(r["industry"], {"n": 0, "a": [], "b": []}); g["n"] += 1
    if r["chg_1d"] is not None: g["a"].append(r["chg_1d"])
    if r["chg_1m"] is not None: g["b"].append(r["chg_1m"])
industries = sorted([{"industry": k, "n": v["n"], "avg_1d": round(float(np.mean(v["a"])), 2) if v["a"] else None, "avg_1m": round(float(np.mean(v["b"])), 1) if v["b"] else None}
                     for k, v in ind.items()], key=lambda x: -(x["avg_1d"] or 0))
adv = sum(1 for r in rows if (r["chg_1d"] or 0) > 0); dec = sum(1 for r in rows if (r["chg_1d"] or 0) < 0)
out = {"generated": date.today().isoformat(), "data_to": last.date().isoformat(), "universe": universe, "count": len(rows), "advances": adv, "declines": dec,
       "stocks": rows, "industries": industries, "analyst_coverage": sum(1 for r in rows if r["analyst"]),
       "analyst_note": "Analyst targets are the Yahoo Finance consensus (12-month, mean of the covering analysts) - the same kind of number apps show as a 'target'. "
                       "They are context, not a tested signal; the only tested stock rule is the Picks view."}
json.dump(out, open(os.path.join(OUTD, "stock_market.json"), "w"), separators=(",", ":"))
tail = Cf.iloc[-250:]
Vt = V.ffill().iloc[-250:]
series = {"dates": [t.date().isoformat() for t in tail.index],
          "stocks": {s: {"c": [None if pd.isna(x) else round(float(x), 2) for x in tail[s]], "v": [0 if pd.isna(x) else int(x) for x in Vt[s]]} for s in C.columns if not pd.isna(c0[s])}}
json.dump(series, open(os.path.join(OUTD, "stock_series.json"), "w"), separators=(",", ":"))
print(f"{len(rows)} stocks ({universe}), {len(industries)} industries, analyst coverage {out['analyst_coverage']}, data to {last.date()}, "
      f"series {os.path.getsize(os.path.join(OUTD, 'stock_series.json'))//1024} KB")
