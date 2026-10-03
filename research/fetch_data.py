"""Download intraday history for strategy research (runs on GitHub Actions,
which has open internet). Output: research/data/*.csv.gz + fetch_log.txt"""
import gzip, io, json, os, time, urllib.parse
from datetime import date, timedelta

import pandas as pd
import requests
import yfinance as yf

OUT = "research/data"
os.makedirs(OUT, exist_ok=True)
log = []
def L(*a):
    s = " ".join(str(x) for x in a); print(s); log.append(s)

UP = {
    "NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank", "INDIAVIX": "NSE_INDEX|India VIX",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "RELIANCE": "NSE_EQ|INE002A01018", "HDFCBANK": "NSE_EQ|INE040A01034", "ICICIBANK": "NSE_EQ|INE090A01021",
    "INFY": "NSE_EQ|INE009A01021", "TCS": "NSE_EQ|INE467B01029", "SBIN": "NSE_EQ|INE062A01020",
    "AXISBANK": "NSE_EQ|INE238A01034", "BHARTIARTL": "NSE_EQ|INE397D01024", "LT": "NSE_EQ|INE018A01030",
    "ITC": "NSE_EQ|INE154A01025",
}
S = requests.Session()
S.headers.update({"Accept": "application/json", "User-Agent": "Mozilla/5.0"})

def upstox(name, key, start=date(2023, 1, 1)):
    """5-minute candles month by month (the API wants each request inside one calendar month)."""
    rows, y, m = [], start.year, start.month
    today = date.today()
    while (y, m) <= (today.year, today.month):
        fr = date(y, m, 1)
        to = min(date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1), today)
        url = (f"https://api.upstox.com/v3/historical-candle/{urllib.parse.quote(key, safe='')}"
               f"/minutes/5/{to.isoformat()}/{fr.isoformat()}")
        for attempt in range(4):
            r = S.get(url, timeout=30)
            if r.status_code == 429:
                time.sleep(3 + attempt * 3); continue
            break
        if r.status_code == 200:
            rows += r.json().get("data", {}).get("candles", [])
        else:
            L(f"upstox {name} {fr}..{to}: HTTP {r.status_code} {r.text[:120]}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        time.sleep(0.35)
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "oi"][: len(rows[0])])
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.drop_duplicates("ts").sort_values("ts")
    return df[["ts", "open", "high", "low", "close", "volume"]]

def yahoo(ticker, period, interval):
    try:
        d = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=False, threads=False)
    except Exception as e:
        L("yahoo fail", ticker, e); return None
    if d is None or d.empty:
        L("yahoo empty", ticker, interval); return None
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d.rename(columns=str.lower).reset_index()
    d = d.rename(columns={d.columns[0]: "ts"})
    d["ts"] = pd.to_datetime(d["ts"])
    if d["ts"].dt.tz is not None:
        d["ts"] = d["ts"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return d[["ts", "open", "high", "low", "close", "volume"]]

def save(df, fname):
    df.to_csv(f"{OUT}/{fname}.csv.gz", index=False, compression="gzip")
    L(f"saved {fname}: {len(df)} rows {df.ts.min()} -> {df.ts.max()}")

YF = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "INDIAVIX": "^INDIAVIX"}
for n, k in UP.items():
    df = upstox(n, k)
    if df is not None and len(df):
        if df["ts"].dt.tz is not None:
            df["ts"] = df["ts"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        save(df, f"{n}_5m_upstox")
    else:
        t = YF.get(n, f"{n}.NS")
        y = yahoo(t, "60d", "5m")
        if y is not None:
            save(y, f"{n}_5m_yahoo")

# daily context series (no look-ahead use is the backtester's job)
for n, t in {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "INDIAVIX": "^INDIAVIX", "SP500": "^GSPC", "NASDAQ": "^IXIC",
             "NIKKEI": "^N225", "HANGSENG": "^HSI", "DOW": "^DJI", "USDINR": "INR=X", "CRUDE": "CL=F"}.items():
    y = yahoo(t, "3y", "1d")
    if y is not None:
        save(y, f"{n}_1d")
# Yahoo 5m too, as a cross-check of the Upstox data
for n, t in {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}.items():
    y = yahoo(t, "60d", "5m")
    if y is not None:
        save(y, f"{n}_5m_yahoo_check")
open("research/fetch_log.txt", "w").write("\n".join(log) + "\n")
