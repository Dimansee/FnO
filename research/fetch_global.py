"""Download crypto + US price history for the global-book research (runs on GitHub Actions, which has open internet).

Crypto: Binance spot klines from data.binance.vision monthly files (the same exchange the app trades on paper), 1h,
        Jan 2023 to the last full month, then the current month from the public API (falls back silently if blocked).
US:     Yahoo Finance 1h (~730 days), 1d (max), 5m (~60 days).
Writes gzip CSVs (ts in UTC, open, high, low, close, volume) to <out>/crypto_<SYM>_1h.csv.gz etc.
Usage: python research/fetch_global.py <out_dir>
"""
import io
import os
import sys
import zipfile
from datetime import date

import pandas as pd
import requests

OUT = sys.argv[1] if len(sys.argv) > 1 else "research/data/global"
os.makedirs(OUT, exist_ok=True)
CRYPTO = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT"]
US = ["SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "JPM", "GLD"]
S = requests.Session()


def months(start=date(2023, 1, 1)):
    d, today = start, date.today()
    while (d.year, d.month) < (today.year, today.month):
        yield d
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def crypto(sym):
    frames = []
    for m in months():
        url = f"https://data.binance.vision/data/spot/monthly/klines/{sym}/1h/{sym}-1h-{m:%Y-%m}.zip"
        r = S.get(url, timeout=60)
        if r.status_code != 200:
            continue
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            df = pd.read_csv(z.open(z.namelist()[0]), header=None, usecols=range(6))
        frames.append(df)
    for host in ("https://data-api.binance.vision", "https://api.binance.com"):   # current month (best effort)
        try:
            start = int(pd.Timestamp(date.today().replace(day=1)).tz_localize("UTC").timestamp() * 1000)
            rows = []
            while True:
                r = S.get(f"{host}/api/v3/klines", params={"symbol": sym, "interval": "1h", "startTime": start, "limit": 1000}, timeout=30)
                if r.status_code != 200:
                    break
                b = r.json()
                if not b:
                    break
                rows += [x[:6] for x in b]
                if len(b) < 1000:
                    break
                start = b[-1][0] + 1
            if rows:
                frames.append(pd.DataFrame(rows))
                break
        except Exception:
            continue
    if not frames:
        print("no data", sym)
        return
    df = pd.concat(frames)
    df.columns = ["ts", "open", "high", "low", "close", "volume"]
    ts = pd.to_numeric(df["ts"])
    ts = ts.where(ts < 1e14, ts // 1000)          # Binance switched some files to microseconds in 2025
    df["ts"] = pd.to_datetime(ts, unit="ms", utc=True)
    df = df.drop_duplicates("ts").sort_values("ts")
    df.to_csv(f"{OUT}/crypto_{sym}_1h.csv.gz", index=False)
    print(sym, len(df), df["ts"].iloc[0], df["ts"].iloc[-1])


def us(sym):
    import yfinance as yf
    for iv, period in (("1h", "729d"), ("1d", "max"), ("5m", "59d")):
        try:
            df = yf.Ticker(sym).history(period=period, interval=iv, auto_adjust=False)
        except Exception as e:
            print("fail", sym, iv, e)
            continue
        if df.empty:
            print("empty", sym, iv)
            continue
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
        df.index = (df.index.tz_localize("America/New_York") if df.index.tz is None else df.index).tz_convert("UTC")
        df.index.name = "ts"
        df.reset_index().to_csv(f"{OUT}/us_{sym}_{iv}.csv.gz", index=False)
        print(sym, iv, len(df))


for s in CRYPTO:
    try:
        crypto(s)
    except Exception as e:
        print("crypto fail", s, e)
for s in US:
    us(s)
