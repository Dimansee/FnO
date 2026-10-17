"""Price data for the crypto and US books (the Indian book keeps using fno/market.py).

Crypto: Binance public market-data API (no key), with Bybit as a fallback.  US: Yahoo Finance via yfinance.
Everything returns a DataFrame with open/high/low/close/volume and a tz-aware index in the market's own clock
(crypto UTC, US New York), because strategy time windows and the US square-off are written in market time.
"""
from __future__ import annotations

import time as _t
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from . import market as M

TZ = {"IN": ZoneInfo("Asia/Kolkata"), "US": ZoneInfo("America/New_York"), "CRYPTO": ZoneInfo("UTC")}
MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}

CATALOG = {
    "CRYPTO": {
        "BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum", "SOLUSDT": "Solana", "BNBUSDT": "BNB", "XRPUSDT": "XRP",
        "DOGEUSDT": "Dogecoin", "ADAUSDT": "Cardano", "AVAXUSDT": "Avalanche", "LINKUSDT": "Chainlink", "TONUSDT": "Toncoin",
    },
    "US": {
        "SPY": "S&P 500 ETF", "QQQ": "Nasdaq 100 ETF", "IWM": "Russell 2000 ETF", "DIA": "Dow ETF", "AAPL": "Apple", "MSFT": "Microsoft",
        "NVDA": "Nvidia", "AMZN": "Amazon", "GOOGL": "Alphabet", "META": "Meta", "TSLA": "Tesla", "AMD": "AMD", "JPM": "JPMorgan", "GLD": "Gold ETF",
    },
}
DEFAULTS = {"CRYPTO": ["BTCUSDT", "ETHUSDT", "SOLUSDT"], "US": ["SPY", "QQQ", "AAPL", "NVDA"]}
CURRENCY = {"CRYPTO": "USDT", "US": "USD", "IN": "INR"}
BINANCE = ["https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com"]
BYBIT = "https://api.bybit.com"
_s = requests.Session()


def default_symbols(market: str) -> list[str]:
    return list(DEFAULTS.get(market, []))


def valid_symbol(market: str, sym: str) -> bool:
    return sym in CATALOG.get(market, {})


def catalog_ids(market: str) -> list[str]:
    return list(CATALOG.get(market, {}))


def catalog(market: str) -> list[dict]:
    return [{"id": k, "label": v} for k, v in CATALOG.get(market, {}).items()]


# ------------------------------------------------------------------ crypto
def _binance(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    rows, last_err = [], None
    for host in BINANCE:
        rows, cur = [], start_ms
        try:
            while cur < end_ms:
                r = _s.get(f"{host}/api/v3/klines", params={"symbol": symbol, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": 1000}, timeout=15)
                if r.status_code != 200:
                    raise RuntimeError(f"{host} {r.status_code}: {r.text[:80]}")
                batch = r.json()
                if not batch:
                    break
                rows += batch
                nxt = batch[-1][0] + 1
                if nxt <= cur or len(batch) < 1000:
                    break
                cur = nxt
            if rows:
                return _frame(rows)
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise RuntimeError(f"Binance unreachable: {last_err}")
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def _frame(rows) -> pd.DataFrame:
    df = pd.DataFrame([r[:6] for r in rows], columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts").astype(float)
    return df[~df.index.duplicated()].sort_index()


def _bybit(symbol: str, tf: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    iv = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D"}[tf]
    rows, end = [], end_ms
    for _ in range(40):
        r = _s.get(f"{BYBIT}/v5/market/kline", params={"category": "spot", "symbol": symbol, "interval": iv, "start": start_ms, "end": end, "limit": 1000}, timeout=15)
        lst = ((r.json() or {}).get("result") or {}).get("list") or []
        if not lst:
            break
        rows += [[int(x[0]), x[1], x[2], x[3], x[4], x[5]] for x in lst]
        oldest = int(lst[-1][0])
        if oldest <= start_ms:
            break
        end = oldest - 1
    return _frame(rows) if rows else pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def _crypto(symbol: str, tf: str, days: int) -> pd.DataFrame:
    end = int(_t.time() * 1000)
    start = end - int(days * 86400 * 1000)
    try:
        df = _binance(symbol, tf, start, end)
    except Exception:
        df = pd.DataFrame()
    if df.empty:
        df = _bybit(symbol, tf, start, end)
    return df


# ------------------------------------------------------------------ US
_YF = {"1m": ("1m", 7), "5m": ("5m", 59), "15m": ("15m", 59), "30m": ("30m", 59), "1h": ("1h", 720), "4h": ("1h", 720), "1d": ("1d", 3650)}


def _us(symbol: str, tf: str, days: int) -> pd.DataFrame:
    import yfinance as yf
    iv, cap = _YF[tf]
    d = min(int(days), cap)
    period = f"{d}d" if iv != "1d" else (f"{max(d, 30)}d" if d <= 700 else "max")
    df = yf.Ticker(symbol).history(period=period, interval=iv, auto_adjust=False)
    if df.empty:
        return df
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
    idx = df.index
    df.index = idx.tz_localize(TZ["US"]) if idx.tz is None else idx.tz_convert(TZ["US"])
    if iv == "1d":
        df.index = df.index.normalize()
    if tf == "4h":
        from . import custom as CU
        df = CU.resample(df, "4h", "US")
    return df


# ------------------------------------------------------------------ public
def candles(market: str, symbol: str, tf: str, days: int, ttl: int | None = None) -> pd.DataFrame:
    """OHLCV in the market's clock. `days` is calendar days of history wanted (the source may give less)."""
    if tf not in MINUTES:
        raise ValueError("Unknown timeframe " + tf)
    if not valid_symbol(market, symbol):
        raise ValueError(f"{symbol} is not in the {market} list")
    ttl = ttl if ttl is not None else (45 if days <= 10 else 1800)
    key = f"mk:{market}:{symbol}:{tf}:{days}"

    def load():
        df = _crypto(symbol, tf, days) if market == "CRYPTO" else _us(symbol, tf, days)
        if df.empty:
            raise RuntimeError(f"No {market} price data for {symbol} right now.")
        df.index = df.index.tz_convert(TZ[market]) if df.index.tz is not None else df.index.tz_localize(TZ[market])
        return df
    return M.cached(key, ttl, load)


def bar_ends(index, tf: str, market: str = "CRYPTO"):
    """When each bar is finished. US intraday bars end at 16:00 New York at the latest: the last bar of a session
    (e.g. the 13:30 4-hour bar) is complete at the close, not hours later when the market is shut."""
    ends = index + timedelta(minutes=MINUTES[tf])
    if market == "US" and MINUTES[tf] < 1440 and len(index):
        close = index.normalize() + timedelta(hours=16)
        ends = ends.where(ends <= close, close)
    return ends


def completed(df: pd.DataFrame, tf: str, now: datetime | None = None, market: str = "CRYPTO") -> pd.DataFrame:
    """Drop the bar still forming."""
    now = now or datetime.now(timezone.utc)
    if df.empty:
        return df
    return df[bar_ends(df.index, tf, market) <= now]


def history_days(market: str, tf: str, bars: int = 320) -> int:
    """Calendar days of history that give about `bars` bars (US trades ~6.5 h a day, 5 days a week)."""
    mins = MINUTES[tf]
    if market == "US":
        per_day = 1 if mins >= 1440 else max(1, int(np.ceil(390 / mins)))
        return int(bars / per_day * 7 / 5) + 5
    return int(mins * bars / 1440) + 3


def last_price(market: str, symbol: str) -> float:
    if market == "CRYPTO":
        try:
            def f():
                for host in BINANCE[:2]:
                    r = _s.get(f"{host}/api/v3/ticker/price", params={"symbol": symbol}, timeout=8)
                    if r.status_code == 200:
                        return float(r.json()["price"])
                raise RuntimeError("no ticker")
            return M.cached(f"px:{symbol}", 10, f)
        except Exception:
            pass
    df = candles(market, symbol, "5m", 3, ttl=20)
    return float(df["close"].iloc[-1])


def us_session_open(now: datetime | None = None, pad_before=5, pad_after=20) -> bool:
    """Mon-Fri 09:30-16:00 New York, with a little padding so the first and last bars are processed."""
    n = (now or datetime.now(timezone.utc)).astimezone(TZ["US"])
    if n.weekday() >= 5:
        return False
    t = n.time()
    return (datetime.combine(n.date(), dtime(9, 30)) - timedelta(minutes=pad_before)).time() <= t <= (datetime.combine(n.date(), dtime(16, 0)) + timedelta(minutes=pad_after)).time()
