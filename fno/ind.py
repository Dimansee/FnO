"""Indicator library for the custom strategy builder (pure pandas, no network).

Every indicator is registered in INDICATORS with its parameters and outputs, so the same table
drives (a) the maths, (b) validation of a saved strategy and (c) the picker in the app.
All indicators are causal: the value on bar i only uses bars up to and including i.

Frames expect columns open, high, low, close, volume and a DatetimeIndex.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as I


# ------------------------------------------------------------------ basic maths
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wma(s: pd.Series, n: int) -> pd.Series:
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n, min_periods=n).apply(lambda x: float(np.dot(x, w) / w.sum()), raw=True)


def hma(s: pd.Series, n: int) -> pd.Series:
    half, root = max(n // 2, 1), max(int(round(n ** 0.5)), 1)
    return wma(2 * wma(s, half) - wma(s, n), root)


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift()
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def macd(s: pd.Series, fast=12, slow=26, sig=9) -> dict:
    line = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()
    signal = line.ewm(span=sig, adjust=False).mean()
    return {"line": line, "signal": signal, "hist": line - signal}


def bbands(s: pd.Series, n=20, k=2.0) -> dict:
    mid = sma(s, n)
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    up, lo = mid + k * sd, mid - k * sd
    return {"upper": up, "mid": mid, "lower": lo, "pctb": (s - lo) / (up - lo).replace(0, np.nan), "width": (up - lo) / mid}


def stoch(df: pd.DataFrame, k=14, d=3) -> dict:
    lo, hi = df["low"].rolling(k, min_periods=k).min(), df["high"].rolling(k, min_periods=k).max()
    kk = 100 * (df["close"] - lo) / (hi - lo).replace(0, np.nan)
    return {"k": kk, "d": kk.rolling(d, min_periods=d).mean()}


def adx(df: pd.DataFrame, n=14) -> dict:
    h, l = df["high"], df["low"]
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    pdi = 100 * pdm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    ndi = 100 * ndm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return {"adx": dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean(), "plus_di": pdi, "minus_di": ndi}


def cci(df: pd.DataFrame, n=20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    ma = tp.rolling(n, min_periods=n).mean()
    md = tp.rolling(n, min_periods=n).apply(lambda x: float(np.abs(x - x.mean()).mean()), raw=True)
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def willr(df: pd.DataFrame, n=14) -> pd.Series:
    hi, lo = df["high"].rolling(n, min_periods=n).max(), df["low"].rolling(n, min_periods=n).min()
    return -100 * (hi - df["close"]) / (hi - lo).replace(0, np.nan)


def mfi(df: pd.DataFrame, n=14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    raw = tp * df["volume"].fillna(0)
    d = tp.diff()
    pos = raw.where(d > 0, 0.0).rolling(n, min_periods=n).sum()
    neg = raw.where(d < 0, 0.0).rolling(n, min_periods=n).sum()
    return 100 - 100 / (1 + pos / neg.replace(0, np.nan))


def obv(df: pd.DataFrame) -> pd.Series:
    return (np.sign(df["close"].diff()).fillna(0) * df["volume"].fillna(0)).cumsum()


def donchian(df: pd.DataFrame, n=20) -> dict:
    """Highest high / lowest low of the PREVIOUS n bars (so 'close > upper' is a real breakout)."""
    return {"upper": df["high"].rolling(n, min_periods=n).max().shift(1), "lower": df["low"].rolling(n, min_periods=n).min().shift(1)}


def keltner(df: pd.DataFrame, n=20, mult=2.0) -> dict:
    mid = ema(df["close"], n)
    a = atr(df, n)
    return {"upper": mid + mult * a, "mid": mid, "lower": mid - mult * a}


def supertrend(df: pd.DataFrame, n=10, mult=3.0) -> dict:
    d = I.supertrend(df, n, mult)
    return {"dir": d}


def roc(s: pd.Series, n=10) -> pd.Series:
    return (s / s.shift(n) - 1) * 100


def vol_ratio(df: pd.DataFrame, n=20) -> pd.Series:
    v = df["volume"].replace(0, np.nan)
    return v / v.rolling(n, min_periods=n).mean()


def zscore(s: pd.Series, n=20) -> pd.Series:
    return (s - s.rolling(n, min_periods=n).mean()) / s.rolling(n, min_periods=n).std(ddof=0).replace(0, np.nan)


def psar(df: pd.DataFrame, step=0.02, max_step=0.2) -> dict:
    h, l = df["high"].values, df["low"].values
    n = len(df)
    sar, trend = np.full(n, np.nan), np.ones(n)
    if n < 3:
        return {"value": pd.Series(sar, index=df.index), "dir": pd.Series(trend, index=df.index)}
    up, af, ep, sar[0] = True, step, h[0], l[0]
    for i in range(1, n):
        s = sar[i - 1] + af * (ep - sar[i - 1])
        if up:
            s = min(s, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
            if l[i] < s:
                up, s, ep, af = False, ep, l[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, max_step)
        else:
            s = max(s, h[i - 1], h[i - 2] if i > 1 else h[i - 1])
            if h[i] > s:
                up, s, ep, af = True, ep, h[i], step
            elif l[i] < ep:
                ep, af = l[i], min(af + step, max_step)
        sar[i], trend[i] = s, 1 if up else -1
    return {"value": pd.Series(sar, index=df.index), "dir": pd.Series(trend, index=df.index)}


# ------------------------------------------------------------------ registry
# name -> label, params (name -> default), outputs, fn(df, **params) -> Series | dict[str, Series]
# "src" (close/open/high/low/hl2/hlc3) is accepted by indicators computed from one price series.
def _src(df, name):
    if name == "hl2":
        return (df["high"] + df["low"]) / 2
    if name == "hlc3":
        return (df["high"] + df["low"] + df["close"]) / 3
    return df[name if name in ("open", "high", "low", "close", "volume") else "close"]


INDICATORS = {
    "sma": {"label": "SMA", "params": {"n": 20}, "outs": ["value"], "src": True, "fn": lambda df, n, src="close": sma(_src(df, src), int(n))},
    "ema": {"label": "EMA", "params": {"n": 20}, "outs": ["value"], "src": True, "fn": lambda df, n, src="close": ema(_src(df, src), int(n))},
    "wma": {"label": "WMA", "params": {"n": 20}, "outs": ["value"], "src": True, "fn": lambda df, n, src="close": wma(_src(df, src), int(n))},
    "hma": {"label": "Hull MA", "params": {"n": 21}, "outs": ["value"], "src": True, "fn": lambda df, n, src="close": hma(_src(df, src), int(n))},
    "rsi": {"label": "RSI", "params": {"n": 14}, "outs": ["value"], "src": True, "fn": lambda df, n, src="close": rsi(_src(df, src), int(n))},
    "macd": {"label": "MACD", "params": {"fast": 12, "slow": 26, "sig": 9}, "outs": ["line", "signal", "hist"],
             "fn": lambda df, fast, slow, sig: macd(df["close"], int(fast), int(slow), int(sig))},
    "bb": {"label": "Bollinger Bands", "params": {"n": 20, "k": 2.0}, "outs": ["upper", "mid", "lower", "pctb", "width"],
           "fn": lambda df, n, k: bbands(df["close"], int(n), float(k))},
    "stoch": {"label": "Stochastic", "params": {"k": 14, "d": 3}, "outs": ["k", "d"], "fn": lambda df, k, d: stoch(df, int(k), int(d))},
    "adx": {"label": "ADX / DI", "params": {"n": 14}, "outs": ["adx", "plus_di", "minus_di"], "fn": lambda df, n: adx(df, int(n))},
    "atr": {"label": "ATR", "params": {"n": 14}, "outs": ["value"], "fn": lambda df, n: atr(df, int(n))},
    "cci": {"label": "CCI", "params": {"n": 20}, "outs": ["value"], "fn": lambda df, n: cci(df, int(n))},
    "willr": {"label": "Williams %R", "params": {"n": 14}, "outs": ["value"], "fn": lambda df, n: willr(df, int(n))},
    "mfi": {"label": "Money Flow Index", "params": {"n": 14}, "outs": ["value"], "fn": lambda df, n: mfi(df, int(n))},
    "obv": {"label": "On-Balance Volume", "params": {}, "outs": ["value"], "fn": lambda df: obv(df)},
    "donchian": {"label": "Donchian channel (previous n bars)", "params": {"n": 20}, "outs": ["upper", "lower"], "fn": lambda df, n: donchian(df, int(n))},
    "keltner": {"label": "Keltner channel", "params": {"n": 20, "mult": 2.0}, "outs": ["upper", "mid", "lower"], "fn": lambda df, n, mult: keltner(df, int(n), float(mult))},
    "supertrend": {"label": "Supertrend", "params": {"n": 10, "mult": 3.0}, "outs": ["dir"], "fn": lambda df, n, mult: supertrend(df, int(n), float(mult))},
    "psar": {"label": "Parabolic SAR", "params": {"step": 0.02, "max_step": 0.2}, "outs": ["value", "dir"], "fn": lambda df, step, max_step: psar(df, float(step), float(max_step))},
    "roc": {"label": "Rate of change %", "params": {"n": 10}, "outs": ["value"], "src": True, "fn": lambda df, n, src="close": roc(_src(df, src), int(n))},
    "zscore": {"label": "Z-score of price", "params": {"n": 20}, "outs": ["value"], "src": True, "fn": lambda df, n, src="close": zscore(_src(df, src), int(n))},
    "volratio": {"label": "Volume vs average", "params": {"n": 20}, "outs": ["value"], "fn": lambda df, n: vol_ratio(df, int(n))},
    "highest": {"label": "Highest high (previous n)", "params": {"n": 20}, "outs": ["value"], "fn": lambda df, n: df["high"].rolling(int(n)).max().shift(1)},
    "lowest": {"label": "Lowest low (previous n)", "params": {"n": 20}, "outs": ["value"], "fn": lambda df, n: df["low"].rolling(int(n)).min().shift(1)},
    "vwap": {"label": "VWAP (session)", "params": {}, "outs": ["value"], "fn": lambda df: I.vwap(df)},
}

# Fixed levels (no parameters). Intraday frames: daily levels come from the previous session.
LEVELS = {
    "pdh": "Previous day high", "pdl": "Previous day low", "pdc": "Previous day close",
    "day_open": "Today's open", "day_high": "Today's high so far", "day_low": "Today's low so far",
    "orb_high": "Opening range high (first 15 min)", "orb_low": "Opening range low (first 15 min)",
    "pivot": "Classic pivot (previous day)", "cam_h3": "Camarilla H3", "cam_h4": "Camarilla H4",
    "cam_l3": "Camarilla L3", "cam_l4": "Camarilla L4",
}
PRICES = ("open", "high", "low", "close", "volume", "hl2", "hlc3", "range_pct", "body_pct", "hour", "minute")
OPS = {"gt": ">", "lt": "<", "ge": ">=", "le": "<=", "crosses_above": "crosses above", "crosses_below": "crosses below",
       "rising": "is rising for", "falling": "is falling for"}


def meta() -> dict:
    """What the strategy builder can offer."""
    return {
        "indicators": {k: {"label": v["label"], "params": v["params"], "outs": v["outs"], "src": bool(v.get("src"))} for k, v in INDICATORS.items()},
        "levels": LEVELS, "prices": list(PRICES), "ops": OPS,
    }


# ------------------------------------------------------------------ levels
def _is_intraday(df: pd.DataFrame) -> bool:
    if len(df) < 3:
        return False
    return (df.index[1] - df.index[0]) < pd.Timedelta(hours=20)


def level_series(df: pd.DataFrame, name: str) -> pd.Series:
    out = pd.Series(np.nan, index=df.index)
    if not _is_intraday(df):                       # daily bars: "previous day" = previous bar
        h, l, c, o = df["high"], df["low"], df["close"], df["open"]
        if name == "pdh": return h.shift(1)
        if name == "pdl": return l.shift(1)
        if name == "pdc": return c.shift(1)
        if name == "day_open": return o
        if name == "day_high": return h
        if name == "day_low": return l
        if name in ("orb_high", "orb_low"): return h if name == "orb_high" else l
        base_h, base_l, base_c = h.shift(1), l.shift(1), c.shift(1)
    else:
        day = pd.Series(df.index.date, index=df.index)
        g = df.groupby(day)
        dh, dl, dc = g["high"].max(), g["low"].min(), g["close"].last()
        dates = list(dh.index)
        prev = {d: dates[i - 1] for i, d in enumerate(dates) if i > 0}
        pd_ = day.map(prev)
        mp = lambda s: pd_.map(s)
        if name == "pdh": return mp(dh).astype(float)
        if name == "pdl": return mp(dl).astype(float)
        if name == "pdc": return mp(dc).astype(float)
        if name == "day_open": return g["open"].transform("first")
        if name == "day_high": return g["high"].cummax()
        if name == "day_low": return g["low"].cummin()
        if name in ("orb_high", "orb_low"):
            first = df.index.to_series().groupby(day).transform("min")
            inside = (df.index - first) < pd.Timedelta(minutes=15)
            o = (df["high"] if name == "orb_high" else df["low"]).where(inside)
            agg = o.groupby(day).max() if name == "orb_high" else o.groupby(day).min()
            val = day.map(agg).astype(float)
            return val.where(~inside)               # known only after the opening range is complete
        base_h, base_l, base_c = mp(dh).astype(float), mp(dl).astype(float), mp(dc).astype(float)
    pv = (base_h + base_l + base_c) / 3
    r = (base_h - base_l) * 1.1
    if name == "pivot": return pv
    if name == "cam_h3": return base_c + r / 4
    if name == "cam_h4": return base_c + r / 2
    if name == "cam_l3": return base_c - r / 4
    if name == "cam_l4": return base_c - r / 2
    return out


# ------------------------------------------------------------------ operand resolver
def operand_series(df: pd.DataFrame, op: dict, cache: dict) -> pd.Series:
    """op is one of {"value": 30} | {"price": "close"} | {"level": "pdh"} | {"ind": "ema", "n": 9, "out": "value", "src": "close"}."""
    if "value" in op and op["value"] is not None:
        return pd.Series(float(op["value"]), index=df.index)
    if "price" in op:
        p = op["price"]
        if p in ("open", "high", "low", "close", "volume"):
            return df[p].astype(float)
        if p in ("hl2", "hlc3"):
            return _src(df, p)
        if p == "range_pct":
            return (df["high"] - df["low"]) / df["close"] * 100
        if p == "body_pct":
            return (df["close"] - df["open"]) / df["open"] * 100
        if p == "hour":
            return pd.Series(df.index.hour, index=df.index, dtype=float)
        if p == "minute":
            return pd.Series(df.index.hour * 60 + df.index.minute, index=df.index, dtype=float)
        raise ValueError(f"Unknown price field '{p}'")
    if "level" in op:
        if op["level"] not in LEVELS:
            raise ValueError(f"Unknown level '{op['level']}'")
        key = ("lvl", op["level"])
        if key not in cache:
            cache[key] = level_series(df, op["level"])
        return cache[key]
    if "ind" in op:
        name = op["ind"]
        spec = INDICATORS.get(name)
        if not spec:
            raise ValueError(f"Unknown indicator '{name}'")
        params = {k: op.get(k, dflt) for k, dflt in spec["params"].items()}
        extra = {"src": op["src"]} if spec.get("src") and op.get("src") else {}
        key = ("ind", name, tuple(sorted(params.items())), tuple(sorted(extra.items())))
        if key not in cache:
            cache[key] = spec["fn"](df, **params, **extra)
        res = cache[key]
        if isinstance(res, dict):
            out = op.get("out") or spec["outs"][0]
            if out not in res:
                raise ValueError(f"{spec['label']} has no output '{out}' (use {', '.join(spec['outs'])})")
            return res[out].astype(float)
        return res.astype(float)
    raise ValueError("A condition side needs a value, price, level or indicator.")


def describe(op: dict) -> str:
    if "value" in op and op["value"] is not None:
        return f"{op['value']:g}"
    if "price" in op:
        return op["price"]
    if "level" in op:
        return LEVELS.get(op["level"], op["level"])
    spec = INDICATORS.get(op.get("ind"), {"label": op.get("ind"), "params": {}, "outs": ["value"]})
    ps = ",".join(f"{op.get(k, d):g}" if isinstance(op.get(k, d), (int, float)) else str(op.get(k, d)) for k, d in spec["params"].items())
    out = op.get("out")
    return f"{spec['label']}({ps})" + (f".{out}" if out and out != spec["outs"][0] else "")
