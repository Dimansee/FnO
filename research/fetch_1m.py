"""1-minute index history (Upstox public API, no login) for the rally / scalping and stop-loss studies.
Runs on GitHub Actions. Output: research/data/<NAME>_1m_upstox.csv.gz"""
import os, time, urllib.parse
from datetime import date, timedelta

import pandas as pd
import requests

OUT = "research/data"
KEYS = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank", "INDIAVIX": "NSE_INDEX|India VIX"}
S = requests.Session(); S.headers.update({"Accept": "application/json"})
log = []
for name, key in KEYS.items():
    rows, y, m, today = [], 2023, 1, date.today()
    while (y, m) <= (today.year, today.month):
        fr = date(y, m, 1)
        to = min(date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1), today)
        url = f"https://api.upstox.com/v3/historical-candle/{urllib.parse.quote(key, safe='')}/minutes/1/{to}/{fr}"
        for attempt in range(5):
            r = S.get(url, timeout=40)
            if r.status_code == 429:
                time.sleep(3 + attempt * 3); continue
            break
        if r.status_code == 200:
            rows += r.json().get("data", {}).get("candles", [])
        else:
            log.append(f"{name} {fr}: {r.status_code} {r.text[:120]}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        time.sleep(0.4)
    df = pd.DataFrame([c[:6] for c in rows], columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df = df.drop_duplicates("ts").sort_values("ts")
    df.to_csv(f"{OUT}/{name}_1m_upstox.csv.gz", index=False, compression="gzip")
    log.append(f"{name}: {len(df)} bars {df.ts.min()} -> {df.ts.max()} days {df.ts.dt.date.nunique()}")
    print(log[-1], flush=True)
open("research/fetch_1m_log.txt", "w").write("\n".join(log) + "\n")
