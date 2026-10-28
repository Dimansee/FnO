"""Price data for the crypto, US and gold books (the Indian book keeps using fno/market.py).

Crypto: Binance public market-data API (no key), with Bybit as a fallback.  US: Yahoo Finance via yfinance.
Gold: XAUUSD = COMEX gold futures (Yahoo GC=F, nearly 24 h on weekdays, ~10 min delayed), PAXGUSDT = tokenised
gold on Binance (one token = one ounce in a London vault, trades 24/7).
Everything returns a DataFrame with open/high/low/close/volume and a tz-aware index in the market's own clock
(crypto UTC, US New York, gold London), because strategy time windows and the US square-off are written in market
time. Gold uses London time because the gold price is set there (LBMA auctions 10:30 and 15:00 London).
"""
from __future__ import annotations

import time as _t
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

from . import market as M

TZ = {"IN": ZoneInfo("Asia/Kolkata"), "US": ZoneInfo("America/New_York"), "CRYPTO": ZoneInfo("UTC"), "GOLD": ZoneInfo("Europe/London")}
MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}

CATALOG = {
    "CRYPTO": {
        "BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum", "SOLUSDT": "Solana", "BNBUSDT": "BNB", "XRPUSDT": "XRP",
        "DOGEUSDT": "Dogecoin", "ADAUSDT": "Cardano", "AVAXUSDT": "Avalanche", "LINKUSDT": "Chainlink", "TONUSDT": "Toncoin",
        "PAXGUSDT": "Gold (PAX Gold, 1 token = 1 oz)",   # tokenised gold on Binance, 24/7: traded in the crypto book like a coin
    },
    "US": {
        "SPY": "S&P 500 ETF", "QQQ": "Nasdaq 100 ETF", "IWM": "Russell 2000 ETF", "DIA": "Dow ETF", "AAPL": "Apple", "MSFT": "Microsoft",
        "NVDA": "Nvidia", "AMZN": "Amazon", "GOOGL": "Alphabet", "META": "Meta", "TSLA": "Tesla", "AMD": "AMD", "JPM": "JPMorgan", "GLD": "Gold ETF",
    },
    "GOLD": {
        "XAUUSD": "Gold spot (COMEX gold futures)", "PAXGUSDT": "PAX Gold (tokenised, 24/7)",
    },
}
DEFAULTS = {"CRYPTO": ["BTCUSDT", "ETHUSDT", "SOLUSDT"], "US": ["SPY", "QQQ", "AAPL", "NVDA"], "GOLD": ["XAUUSD"]}
CURRENCY = {"CRYPTO": "USDT", "US": "USD", "IN": "INR", "GOLD": "USD"}
YAHOO_ALIAS = {"XAUUSD": "GC=F"}         # our symbol -> Yahoo ticker
BINANCE_GOLD = {"PAXGUSDT"}              # gold symbols priced from Binance like the crypto book
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
_BN = {"down_until": 0.0}


def _binance(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    rows, last_err = [], None
    if _t.time() < _BN["down_until"]:                 # all Binance hosts failed a moment ago: don't try three hosts again per symbol
        raise RuntimeError("Binance unreachable (recently failed; using Bybit)")
    for host in list(BINANCE):
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
                if host != BINANCE[0]:                # remember the host that works: try it first next time
                    BINANCE.remove(host); BINANCE.insert(0, host)
                return _frame(rows)
        except Exception as e:
            last_err = e
            continue
    if last_err:
        _BN["down_until"] = _t.time() + 300
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


# Yahoo Finance tickers for the coins: third source when Binance and Bybit are both down or out of date
YAHOO_CRYPTO = {"BTCUSDT": "BTC-USD", "ETHUSDT": "ETH-USD", "SOLUSDT": "SOL-USD", "BNBUSDT": "BNB-USD", "XRPUSDT": "XRP-USD",
                "DOGEUSDT": "DOGE-USD", "ADAUSDT": "ADA-USD", "AVAXUSDT": "AVAX-USD", "LINKUSDT": "LINK-USD",
                "TONUSDT": "TON11419-USD", "PAXGUSDT": "PAXG-USD"}
SOURCE: dict = {}                 # (symbol, tf) -> source actually used for the last good fetch
_LOGGED: dict = {}


def _log_once(key: str, msg: str, every: int = 900):
    if _t.time() - _LOGGED.get(key, 0) > every:
        _LOGGED[key] = _t.time()
        print(f"[markets] {msg}", flush=True)


def fresh(df: pd.DataFrame, tf: str, now_ms: int | None = None) -> bool:
    """True when the newest candle is recent: a source that stopped updating (delisted pair, frozen feed) must never be used.
    Allowed age of the newest bar's start: 3 bars + 1 hour (daily: 2 days + 1 hour)."""
    if df is None or df.empty:
        return False
    now_ms = now_ms or int(_t.time() * 1000)
    last = int(pd.Timestamp(df.index[-1]).value // 1_000_000)
    return now_ms - last <= (3 * MINUTES[tf] + 60) * 60_000


def _yahoo_crypto(symbol: str, tf: str, days: int) -> pd.DataFrame:
    import yfinance as yf
    iv, cap = {"1m": ("1m", 7), "5m": ("5m", 59), "15m": ("15m", 59), "30m": ("30m", 59), "1h": ("1h", 720), "4h": ("1h", 720), "1d": ("1d", 3650)}[tf]
    d = min(int(days) + 1, cap)
    df = yf.Ticker(YAHOO_CRYPTO[symbol]).history(period=f"{d}d" if iv != "1d" else "max" if d > 700 else f"{d}d", interval=iv, auto_adjust=False)
    if df.empty:
        return df
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
    df.index = df.index.tz_convert("UTC") if df.index.tz is not None else df.index.tz_localize("UTC")
    if tf == "4h":
        df = df.resample("4h", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["close"])
    return df


def _crypto(symbol: str, tf: str, days: int) -> pd.DataFrame:
    """Binance, then Bybit, then Yahoo Finance; a source whose newest candle is old counts as failed (29 Oct 2026: on the server
    Binance failed and Bybit's PAXGUSDT had stopped in March 2025, so gold showed March prices)."""
    end = int(_t.time() * 1000)
    start = end - int(days * 86400 * 1000)
    tried = []
    for name, fn in (("Binance", lambda: _binance(symbol, tf, start, end)), ("Bybit", lambda: _bybit(symbol, tf, start, end)),
                     ("Yahoo Finance", lambda: _yahoo_crypto(symbol, tf, days) if symbol in YAHOO_CRYPTO else pd.DataFrame())):
        try:
            df = fn()
        except Exception as e:
            tried.append(f"{name}: {str(e)[:60]}")
            continue
        if fresh(df, tf, end):
            df = df[df.index >= pd.Timestamp(start, unit="ms", tz="UTC")]
            if tried:
                _log_once(f"{symbol}:{tf}:{name}", f"{symbol} {tf} from {name} ({'; '.join(tried)})")
            SOURCE[(symbol, tf)] = name
            return df
        tried.append(f"{name}: " + ("no data" if df is None or df.empty else f"out of date (last candle {pd.Timestamp(df.index[-1]):%Y-%m-%d %H:%M})"))
    _log_once(f"{symbol}:{tf}:none", f"no up-to-date {symbol} {tf} candles: {'; '.join(tried)}")
    raise RuntimeError(f"No up-to-date price data for {symbol} right now ({'; '.join(tried)}).")


def source(symbol: str, tf: str) -> str | None:
    return SOURCE.get((symbol, tf))


# ------------------------------------------------------------------ US
_YF = {"1m": ("1m", 7), "5m": ("5m", 59), "15m": ("15m", 59), "30m": ("30m", 59), "1h": ("1h", 720), "4h": ("1h", 720), "1d": ("1d", 3650)}


def _us(symbol: str, tf: str, days: int, market: str = "US") -> pd.DataFrame:
    import yfinance as yf
    iv, cap = _YF[tf]
    d = min(int(days), cap)
    period = f"{d}d" if iv != "1d" else (f"{max(d, 30)}d" if d <= 700 else "max")
    df = yf.Ticker(YAHOO_ALIAS.get(symbol, symbol)).history(period=period, interval=iv, auto_adjust=False)
    if df.empty:
        return df
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
    idx = df.index
    df.index = idx.tz_localize(TZ[market]) if idx.tz is None else idx.tz_convert(TZ[market])
    if iv == "1d":
        # daily bar labelled with its trading date (gold: the COMEX day that ends 17:00 New York), at midnight market time
        ny = idx.tz_localize(TZ["US"]) if idx.tz is None else idx.tz_convert(TZ["US"])
        df.index = pd.DatetimeIndex([pd.Timestamp(x.date(), tz=TZ[market]) for x in ny]) if market == "GOLD" else df.index.normalize()
        df = df[~df.index.duplicated(keep="last")]
    if tf == "4h":
        from . import custom as CU
        df = CU.resample(df, "4h", market)
    return df


def _gold(symbol: str, tf: str, days: int) -> pd.DataFrame:
    if symbol not in BINANCE_GOLD:
        return _us(symbol, tf, days, market="GOLD")
    if tf not in ("4h", "1d"):
        return _crypto(symbol, tf, days)
    # Build 4h / daily from 1h so PAXG bars line up with XAUUSD: 4h on the London clock, days = COMEX trading days
    # (ending 17:00 New York; Saturday/Sunday trading is folded into Monday, as COMEX's Sunday evening session is).
    from . import custom as CU
    h = _crypto(symbol, "1h", days + 3)
    if h.empty:
        return h
    h.index = h.index.tz_convert(TZ["GOLD"])
    if tf == "4h":
        return CU.resample(h, "4h", "GOLD")
    ny = h.index.tz_convert(TZ["US"])
    lab = (ny + timedelta(hours=7)).normalize()                    # 17:00 NY -> next trading date
    wd = lab.weekday
    lab = lab + pd.to_timedelta(np.where(wd == 5, 2, np.where(wd == 6, 1, 0)), unit="D")
    keys = pd.DatetimeIndex([pd.Timestamp(x.date(), tz=TZ["GOLD"]) for x in lab])
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return h.groupby(keys).agg(agg).dropna(subset=["close"])


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
        df = _crypto(symbol, tf, days) if market == "CRYPTO" else _gold(symbol, tf, days) if market == "GOLD" else _us(symbol, tf, days)
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
    if market == "GOLD" and MINUTES[tf] >= 1440 and len(index):
        # a gold daily bar is the COMEX trading day, which ends at 17:00 New York on its date
        ends = pd.DatetimeIndex([pd.Timestamp(datetime.combine(x.date(), dtime(17, 0)), tz=TZ["US"]) for x in index]).tz_convert(index.tz)
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
    if market == "GOLD":                 # ~23 h a day, 5 days a week
        per_day = 1 if mins >= 1440 else max(1, int(np.ceil(1380 / mins)))
        return int(bars / per_day * 7 / 5) + 5
    return int(mins * bars / 1440) + 3


def last_price(market: str, symbol: str) -> float:
    if market == "CRYPTO" or symbol in BINANCE_GOLD:
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


def gold_open(now: datetime | None = None, pad_after: int = 20) -> bool:
    """Gold (COMEX / spot XAUUSD) trades Sunday 18:00 to Friday 17:00 New York, with a one-hour break 17:00-18:00 every
    day (22:00-23:00 London, 03:30-04:30 IST in Indian winter). A little padding after the break so the last bar is processed."""
    n = (now or datetime.now(timezone.utc)).astimezone(TZ["US"])
    wd, m = n.weekday(), n.hour * 60 + n.minute
    if wd == 5 or (wd == 6 and m < 18 * 60) or (wd == 4 and m >= 17 * 60 + pad_after):
        return False
    return not (17 * 60 + pad_after <= m < 18 * 60)


def session_open(market: str, now: datetime | None = None) -> bool:
    """May the book open new trades now? Crypto always, US in its session, gold on weekdays outside the daily break
    (PAXG trades 24/7 but follows real gold's hours here, so weekend moves on thin volume don't fire entries)."""
    if market == "US":
        return us_session_open(now)
    if market == "GOLD":
        return gold_open(now)
    return True
