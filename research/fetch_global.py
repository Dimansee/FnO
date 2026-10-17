"""Download crypto + US price history for the global-book research (runs on GitHub Actions, which has open internet).

Crypto: Binance spot klines from data.binance.vision monthly files (the same exchange the app trades on paper), 1h
        (plus 5m from Jan 2024 for BTC, ETH, SOL, BNB, XRP),
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
CRYPTO5 = CRYPTO[:5]   # 5-minute bars (Jan 2024 on) for the five main coins: intraday strategy tests and charts
S = requests.Session()


def months(start=date(2023, 1, 1)):
    d, today = start, date.today()
    while (d.year, d.month) < (today.year, today.month):
        yield d
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def crypto(sym, iv="1h", start_month=date(2023, 1, 1)):
    frames = []
    for m in months(start_month):
        url = f"https://data.binance.vision/data/spot/monthly/klines/{sym}/{iv}/{sym}-{iv}-{m:%Y-%m}.zip"
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
                r = S.get(f"{host}/api/v3/klines", params={"symbol": sym, "interval": iv, "startTime": start, "limit": 1000}, timeout=30)
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
    df.to_csv(f"{OUT}/crypto_{sym}_{iv}.csv.gz", index=False)
    print(sym, iv, len(df), df["ts"].iloc[0], df["ts"].iloc[-1])


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


def funding(sym):
    """Perpetual (USD-M) funding rate history, 8-hourly."""
    frames = []
    for m in months():
        url = f"https://data.binance.vision/data/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{m:%Y-%m}.zip"
        r = S.get(url, timeout=60)
        if r.status_code != 200:
            continue
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            frames.append(pd.read_csv(z.open(z.namelist()[0])))
    if not frames:
        print("no funding", sym)
        return
    df = pd.concat(frames)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = next(c for c in df.columns if "time" in c)
    rcol = next(c for c in df.columns if "rate" in c and "interval" not in c)
    out = pd.DataFrame({"ts": pd.to_datetime(pd.to_numeric(df[tcol]), unit="ms", utc=True), "rate": pd.to_numeric(df[rcol])})
    out.drop_duplicates("ts").sort_values("ts").to_csv(f"{OUT}/funding_{sym}.csv.gz", index=False)
    print("funding", sym, len(out))


for s in CRYPTO:
    try:
        crypto(s)
    except Exception as e:
        print("crypto fail", s, e)
    try:
        funding(s)
    except Exception as e:
        print("funding fail", s, e)
for s in CRYPTO5:
    try:
        crypto(s, "5m", date(2024, 1, 1))
    except Exception as e:
        print("crypto 5m fail", s, e)
for s in US:
    us(s)
