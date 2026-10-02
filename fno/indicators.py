"""Technical indicators and option maths. Pure functions, no network."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from . import config as C


# ---------------------------------------------------------------------------
# Indicators (expect a DataFrame with columns open, high, low, close, volume
# and a tz-aware DatetimeIndex in Asia/Kolkata)
# ---------------------------------------------------------------------------
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP, reset each day. Indices have no volume, so for them this
    falls back to a time-weighted average of typical price (TWAP)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].fillna(0)
    day = df.index.date
    out = pd.Series(index=df.index, dtype=float)
    for d in pd.unique(day):
        m = day == d
        v = vol[m]
        if v.sum() <= 0:
            v = pd.Series(1.0, index=v.index)
        out[m] = (tp[m] * v).cumsum() / v.cumsum()
    return out


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.Series:
    """Returns +1 (uptrend) / -1 (downtrend)."""
    a = atr(df, n)
    hl2 = (df["high"] + df["low"]) / 2
    upper = (hl2 + mult * a).values
    lower = (hl2 - mult * a).values
    close = df["close"].values
    fu, fl = upper.copy(), lower.copy()
    trend = np.ones(len(df))
    for i in range(1, len(df)):
        fu[i] = upper[i] if (upper[i] < fu[i - 1] or close[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lower[i] if (lower[i] > fl[i - 1] or close[i - 1] < fl[i - 1]) else fl[i - 1]
        if trend[i - 1] == 1:
            trend[i] = -1 if close[i] < fl[i] else 1
        else:
            trend[i] = 1 if close[i] > fu[i] else -1
    return pd.Series(trend, index=df.index)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema9"] = ema(df["close"], 9)
    df["ema21"] = ema(df["close"], 21)
    df["vwap"] = vwap(df)
    df["st"] = supertrend(df)
    df["atr"] = atr(df)
    return df


def opening_range(day_df: pd.DataFrame):
    """High/low of the first 15 minutes (09:15-09:30) of one session."""
    t = day_df.index.time
    m = (t >= C.MARKET_OPEN) & (t < C.ORB_END)
    if not m.any():
        return None
    w = day_df[m]
    return float(w["high"].max()), float(w["low"].min())


# ---------------------------------------------------------------------------
# Black-Scholes (used for demo-mode option prices and backtests)
# ---------------------------------------------------------------------------
def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _npdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_price(spot, strike, t_years, iv, opt="CE", r=C.RISK_FREE):
    t_years = max(t_years, 1 / (365 * 24 * 4))  # floor at ~15 minutes
    iv = max(iv, 0.01)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / (iv * math.sqrt(t_years))
    d2 = d1 - iv * math.sqrt(t_years)
    if opt == "CE":
        return spot * _ncdf(d1) - strike * math.exp(-r * t_years) * _ncdf(d2)
    return strike * math.exp(-r * t_years) * _ncdf(-d2) - spot * _ncdf(-d1)


def bs_greeks(spot, strike, t_years, iv, opt="CE", r=C.RISK_FREE):
    t_years = max(t_years, 1 / (365 * 24 * 4))
    iv = max(iv, 0.01)
    sq = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t_years) / (iv * sq)
    d2 = d1 - iv * sq
    gamma = _npdf(d1) / (spot * iv * sq)
    vega = spot * _npdf(d1) * sq / 100
    if opt == "CE":
        delta = _ncdf(d1)
        theta = (-spot * _npdf(d1) * iv / (2 * sq) - r * strike * math.exp(-r * t_years) * _ncdf(d2)) / 365
    else:
        delta = _ncdf(d1) - 1
        theta = (-spot * _npdf(d1) * iv / (2 * sq) + r * strike * math.exp(-r * t_years) * _ncdf(-d2)) / 365
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def years_to_expiry(expiry: date, now: datetime | None = None) -> float:
    now = now or C.now_ist()
    exp_dt = datetime.combine(expiry, datetime.min.time().replace(hour=15, minute=30))
    secs = (exp_dt - now.replace(tzinfo=None)).total_seconds()
    return max(secs, 0) / (365 * 24 * 3600)


# ---------------------------------------------------------------------------
# Expiry calendar (demo mode only - live mode reads expiries from Upstox)
# Nifty: weekly Tuesday. Bank Nifty & stocks: last Tuesday of the month.
# Exchange holidays are not modelled here (expiry would move a day earlier).
# ---------------------------------------------------------------------------
def _last_tuesday(y, m):
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    while d.weekday() != 1:
        d -= timedelta(days=1)
    return d


def demo_expiries(kind: str, today: date | None = None, n: int = 4) -> list[date]:
    today = today or C.today_ist()
    out = []
    if kind == "weekly":
        d = today
        while len(out) < n:
            if d.weekday() == 1 and d >= today:
                out.append(d)
            d += timedelta(days=1)
    else:
        y, m = today.year, today.month
        while len(out) < n:
            lt = _last_tuesday(y, m)
            if lt >= today:
                out.append(lt)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def pick_trading_expiry(expiries: list[date], kind: str, today: date | None = None) -> date:
    """Avoid expiry-day gamma: on weekly expiry day use next week; for monthly
    contracts roll when fewer than 3 calendar days remain."""
    today = today or C.today_ist()
    future = sorted(e for e in expiries if e >= today)
    if not future:
        return None
    min_days = 1 if kind == "weekly" else 3
    for e in future:
        if (e - today).days >= min_days:
            return e
    return future[-1]


def charges(premium_buy, premium_sell, qty, orders=2):
    """Approximate Indian options charges for one round trip (buy + sell),
    discount-broker model. VERIFY against your broker's calculator."""
    buy_val, sell_val = premium_buy * qty, premium_sell * qty
    brokerage = 20 * orders
    stt = 0.001 * sell_val                 # 0.1% on sell side premium
    exch = 0.0003503 * (buy_val + sell_val)
    sebi = 0.000001 * (buy_val + sell_val)
    stamp = 0.00003 * buy_val
    gst = 0.18 * (brokerage + exch + sebi)
    return round(brokerage + stt + exch + sebi + stamp + gst, 2)
