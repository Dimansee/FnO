"""Custom strategies: rules you build from indicators, run by the same live loop and the same
honest backtester as the built-in strategies.

A strategy is plain JSON (no code is ever executed):

  {"id": "...", "name": "EMA trend", "market": "IN", "symbols": ["NIFTY"], "timeframe": "15m",
   "direction": "both",                     # long | short | both
   "long":  {"all": [cond, ...], "any": [cond, ...]},
   "short": {"all": [...],       "any": [...]},
   "exit":  {"stop_atr": 1.5, "atr_n": 14, "target_r": 2.0, "trail_atr": null, "breakeven_r": null,
             "time_stop_bars": null, "exit_on_opposite": false},
   "filters": {"start": "09:30", "end": "14:30", "max_trades_day": 2, "vix_min": null, "vix_max": null,
               "session": null},          # asia | london | newyork | overlap (world sessions, DST-aware; see fno/sessions.py)
   "hold": "intraday" | "swing", "enabled": true, "auto": false}

  cond = {"l": operand, "op": "gt|lt|ge|le|crosses_above|crosses_below|rising|falling", "r": operand}
  operand = {"ind": "ema", "n": 9} | {"price": "close"} | {"level": "pdh"} | {"value": 30}

Backtest rules (kept deliberately conservative): the signal is read at the close of a bar and the trade
starts at the NEXT bar's open; stop and target are checked inside each bar and a bar that touches both
counts as a stop; a gap through the stop fills at the open; intraday strategies are flat by the
market's square-off time.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, time as dtime, timedelta

import numpy as np
import pandas as pd

from . import config as C
from . import ind as X
from . import indicators as I

MARKETS = ("IN", "CRYPTO", "US", "GOLD")
TIMEFRAMES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
MAX_CONDS, MAX_STRATEGIES = 8, 20
DEFAULT_EXIT = {"stop_atr": 1.5, "atr_n": 14, "target_r": 2.0, "trail_atr": None, "breakeven_r": None,
                "time_stop_bars": None, "exit_on_opposite": False, "after_target_trail": None}
DEFAULT_FILTERS = {"start": None, "end": None, "max_trades_day": 2, "vix_min": None, "vix_max": None, "session": None}
SESSION_FILTERS = ("asia", "london", "newyork", "overlap")
SESSION = {  # market -> (offset used when resampling so bars start on the session clock, square-off hh:mm, last entry hh:mm)
    "IN": {"offset": "15min", "square_off": (15, 15), "open": (9, 15), "close": (15, 30)},
    "US": {"offset": "30min", "square_off": (15, 50), "open": (9, 30), "close": (16, 0)},
    "CRYPTO": {"offset": "0min", "square_off": None, "open": None, "close": None},
    # gold, London clock: intraday trades are flat at 21:45 London, before the 17:00 New York daily break
    "GOLD": {"offset": "0min", "square_off": (21, 45), "open": None, "close": None},
}
# (slippage per side, fee per side) in bps of price. Gold: 2 + 3 bps a side = about $4 an ounce round trip at $4,000,
# above a typical XAUUSD spread (~$0.3) plus commission, and close to Binance's spot fee on PAXG.
COSTS_BPS = {"IN": (1.0, 0.0), "US": (2.0, 0.0), "CRYPTO": (3.0, 5.0), "GOLD": (2.0, 3.0)}


# ------------------------------------------------------------------ presets
def _c(l, op, r):
    return {"l": l, "op": op, "r": r}


PRESETS = [
    {"name": "EMA 9/21 trend + ADX", "about": "Trade the 9/21 EMA cross only when ADX says the market is trending.", "market": "IN", "symbols": ["NIFTY"], "timeframe": "15m", "direction": "both",
     "long": {"all": [_c({"ind": "ema", "n": 9}, "crosses_above", {"ind": "ema", "n": 21}), _c({"ind": "adx", "n": 14, "out": "adx"}, "gt", {"value": 22})]},
     "short": {"all": [_c({"ind": "ema", "n": 9}, "crosses_below", {"ind": "ema", "n": 21}), _c({"ind": "adx", "n": 14, "out": "adx"}, "gt", {"value": 22})]},
     "exit": {**DEFAULT_EXIT, "stop_atr": 1.5, "target_r": 2.0}, "filters": {**DEFAULT_FILTERS, "start": "09:45", "end": "14:15"}, "hold": "intraday"},
    {"name": "Supertrend + VWAP", "about": "Supertrend flips with price on the right side of VWAP.", "market": "IN", "symbols": ["NIFTY", "BANKNIFTY"], "timeframe": "5m", "direction": "both",
     "long": {"all": [_c({"ind": "supertrend", "n": 10, "mult": 3.0, "out": "dir"}, "crosses_above", {"value": 0}), _c({"price": "close"}, "gt", {"ind": "vwap"})]},
     "short": {"all": [_c({"ind": "supertrend", "n": 10, "mult": 3.0, "out": "dir"}, "crosses_below", {"value": 0}), _c({"price": "close"}, "lt", {"ind": "vwap"})]},
     "exit": {**DEFAULT_EXIT, "stop_atr": 2.0, "target_r": 3.0, "exit_on_opposite": True}, "filters": {**DEFAULT_FILTERS, "start": "09:30", "end": "14:30"}, "hold": "intraday"},
    {"name": "RSI dip inside an uptrend", "about": "Buy a short-term RSI dip while price is above the 50 EMA (and the mirror for shorts).", "market": "IN", "symbols": ["NIFTY"], "timeframe": "15m", "direction": "both",
     "long": {"all": [_c({"price": "close"}, "gt", {"ind": "ema", "n": 50}), _c({"ind": "rsi", "n": 7}, "crosses_above", {"value": 30})]},
     "short": {"all": [_c({"price": "close"}, "lt", {"ind": "ema", "n": 50}), _c({"ind": "rsi", "n": 7}, "crosses_below", {"value": 70})]},
     "exit": {**DEFAULT_EXIT, "stop_atr": 1.2, "target_r": 1.5, "time_stop_bars": 8}, "filters": {**DEFAULT_FILTERS, "start": "09:45", "end": "14:15"}, "hold": "intraday"},
    {"name": "Opening-range breakout + volume", "about": "Break of the first-15-minute range, confirmed by above-average volume (use on markets with real volume).", "market": "US", "symbols": ["SPY", "QQQ"], "timeframe": "5m", "direction": "both",
     "long": {"all": [_c({"price": "close"}, "crosses_above", {"level": "orb_high"}), _c({"ind": "volratio", "n": 20}, "gt", {"value": 1.3})]},
     "short": {"all": [_c({"price": "close"}, "crosses_below", {"level": "orb_low"}), _c({"ind": "volratio", "n": 20}, "gt", {"value": 1.3})]},
     "exit": {**DEFAULT_EXIT, "stop_atr": 1.5, "target_r": 2.0}, "filters": {**DEFAULT_FILTERS, "start": "09:50", "end": "15:00"}, "hold": "intraday"},
    {"name": "Bollinger mean reversion", "about": "Fade a close outside the Bollinger band when RSI is stretched; exit near the middle band.", "market": "CRYPTO", "symbols": ["BTCUSDT", "ETHUSDT"], "timeframe": "1h", "direction": "both",
     "long": {"all": [_c({"price": "close"}, "lt", {"ind": "bb", "n": 20, "k": 2.0, "out": "lower"}), _c({"ind": "rsi", "n": 14}, "lt", {"value": 30})]},
     "short": {"all": [_c({"price": "close"}, "gt", {"ind": "bb", "n": 20, "k": 2.0, "out": "upper"}), _c({"ind": "rsi", "n": 14}, "gt", {"value": 70})]},
     "exit": {**DEFAULT_EXIT, "stop_atr": 1.5, "target_r": 1.0, "time_stop_bars": 12}, "filters": {**DEFAULT_FILTERS, "max_trades_day": 3}, "hold": "swing"},
    {"name": "Donchian breakout (trend following)", "about": "Buy a 20-bar high, sell a 20-bar low, trail with 3 ATR. Classic, low win-rate, big winners.", "market": "CRYPTO", "symbols": ["BTCUSDT"], "timeframe": "4h", "direction": "both",
     "long": {"all": [_c({"price": "close"}, "crosses_above", {"ind": "donchian", "n": 20, "out": "upper"})]},
     "short": {"all": [_c({"price": "close"}, "crosses_below", {"ind": "donchian", "n": 20, "out": "lower"})]},
     "exit": {**DEFAULT_EXIT, "stop_atr": 2.0, "target_r": None, "trail_atr": 3.0}, "filters": {**DEFAULT_FILTERS, "max_trades_day": 2}, "hold": "swing"},
    {"name": "Gold: London breakout of the Asian range", "about": "Classic gold setup, for your own experiments. Tested 23 Oct 2026 on 2 years of COMEX gold: lost after costs (-0.29R then -0.02R a trade on 1h). Buy the first close above the Asian high (or short below the low) between London open and 11:00 London.",
     "market": "GOLD", "symbols": ["XAUUSD"], "timeframe": "15m", "direction": "both",
     "long": {"all": [_c({"price": "close"}, "crosses_above", {"level": "asia_high"})]},
     "short": {"all": [_c({"price": "close"}, "crosses_below", {"level": "asia_low"})]},
     "exit": {**DEFAULT_EXIT, "stop_atr": 1.5, "target_r": 2.0}, "filters": {**DEFAULT_FILTERS, "session": "london", "end": "11:00", "max_trades_day": 1}, "hold": "intraday"},
    {"name": "Gold: New York opening-range breakout", "about": "For your own experiments. Tested 23 Oct 2026 on 59 days of 15m COMEX gold: no edge (-0.58R then +0.03R a trade, few trades). Trades the break of the 08:30-09:00 New York range during the overlap, with volume.",
     "market": "GOLD", "symbols": ["XAUUSD"], "timeframe": "5m", "direction": "both",
     "long": {"all": [_c({"price": "close"}, "crosses_above", {"level": "ny_or_high"}), _c({"ind": "volratio", "n": 20}, "gt", {"value": 1.2})]},
     "short": {"all": [_c({"price": "close"}, "crosses_below", {"level": "ny_or_low"}), _c({"ind": "volratio", "n": 20}, "gt", {"value": 1.2})]},
     "exit": {**DEFAULT_EXIT, "stop_atr": 1.5, "target_r": 2.0}, "filters": {**DEFAULT_FILTERS, "session": "overlap", "max_trades_day": 1}, "hold": "intraday"},
]


# ------------------------------------------------------------------ validation
def _num(v, lo, hi, what, allow_none=True, integer=False):
    if v is None or v == "":
        if allow_none:
            return None
        raise ValueError(f"{what} is required.")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number.")
    if not lo <= f <= hi:
        raise ValueError(f"{what} must be between {lo:g} and {hi:g}.")
    return int(f) if integer else f


def _operand(o, side):
    if not isinstance(o, dict):
        raise ValueError(f"A condition is missing its {side} side.")
    if "value" in o and o["value"] not in (None, ""):
        return {"value": _num(o["value"], -1e9, 1e9, "A number in a condition", allow_none=False)}
    if "price" in o:
        if o["price"] not in X.PRICES:
            raise ValueError(f"Unknown price field '{o['price']}'.")
        return {"price": o["price"]}
    if "level" in o:
        if o["level"] not in X.LEVELS:
            raise ValueError(f"Unknown level '{o['level']}'.")
        return {"level": o["level"]}
    if "ind" in o:
        spec = X.INDICATORS.get(o["ind"])
        if not spec:
            raise ValueError(f"Unknown indicator '{o['ind']}'.")
        out = {"ind": o["ind"]}
        for k, d in spec["params"].items():
            v = o.get(k, d)
            out[k] = _num(v, 0.1 if isinstance(d, float) else 1, 500 if isinstance(d, int) else 50, f"{spec['label']} {k}", allow_none=False, integer=isinstance(d, int))
        if len(spec["outs"]) > 1:
            out["out"] = o.get("out") if o.get("out") in spec["outs"] else spec["outs"][0]
        if spec.get("src") and o.get("src") in ("open", "high", "low", "close", "hl2", "hlc3"):
            out["src"] = o["src"]
        return out
    raise ValueError(f"A condition's {side} side needs a number, price, level or indicator.")


def _cond(c):
    if not isinstance(c, dict) or c.get("op") not in X.OPS:
        raise ValueError("Each condition needs an operator.")
    out = {"l": _operand(c.get("l"), "left"), "op": c["op"]}
    if c["op"] in ("rising", "falling"):
        out["r"] = {"value": _num((c.get("r") or {}).get("value", 3), 1, 50, "Number of bars", allow_none=False, integer=True)}
    else:
        out["r"] = _operand(c.get("r"), "right")
    return out


def _side(s, name):
    s = s or {}
    allc = [_cond(c) for c in (s.get("all") or [])]
    anyc = [_cond(c) for c in (s.get("any") or [])]
    if len(allc) + len(anyc) > MAX_CONDS:
        raise ValueError(f"Use at most {MAX_CONDS} conditions per side ({name}).")
    return {"all": allc, "any": anyc}


def _hhmm(v, what):
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
        raise ValueError(f"{what} must look like 09:30.")
    return v


def validate(raw: dict, existing_id: str | None = None) -> dict:
    """Return the cleaned, complete strategy or raise ValueError with a plain-English reason."""
    if not isinstance(raw, dict):
        raise ValueError("Strategy must be an object.")
    name = str(raw.get("name") or "").strip()[:60]
    if not name:
        raise ValueError("Give the strategy a name.")
    market = raw.get("market", "IN")
    if market not in MARKETS:
        raise ValueError("Market must be IN, CRYPTO, US or GOLD.")
    tf = raw.get("timeframe", "15m")
    if tf not in TIMEFRAMES:
        raise ValueError("Timeframe must be one of " + ", ".join(TIMEFRAMES))
    if market == "IN" and TIMEFRAMES[tf] > 60:
        raise ValueError("Indian intraday strategies can use 5m, 15m, 30m or 1h bars.")
    direction = raw.get("direction", "both")
    if direction not in ("long", "short", "both"):
        raise ValueError("Direction must be long, short or both.")
    syms = [str(s).strip().upper() for s in (raw.get("symbols") or []) if str(s).strip()][:10]
    if not syms:
        raise ValueError("Pick at least one symbol.")
    long_, short_ = _side(raw.get("long"), "long"), _side(raw.get("short"), "short")
    if direction in ("long", "both") and not (long_["all"] or long_["any"]):
        raise ValueError("Add at least one entry condition for the long side.")
    if direction in ("short", "both") and not (short_["all"] or short_["any"]):
        raise ValueError("Add at least one entry condition for the short side.")
    ex = {**DEFAULT_EXIT, **(raw.get("exit") or {})}
    exit_ = {
        "stop_atr": _num(ex["stop_atr"], 0.3, 10, "Stop distance (ATR)", allow_none=False),
        "atr_n": _num(ex["atr_n"], 2, 100, "ATR length", allow_none=False, integer=True),
        "target_r": _num(ex["target_r"], 0.3, 20, "Target (R)"),
        "trail_atr": _num(ex["trail_atr"], 0.3, 20, "Trailing distance (ATR)"),
        "breakeven_r": _num(ex["breakeven_r"], 0.3, 10, "Break-even trigger (R)"),
        "time_stop_bars": _num(ex["time_stop_bars"], 1, 500, "Time stop (bars)", integer=True),
        "exit_on_opposite": bool(ex["exit_on_opposite"]),
        "after_target_trail": _num(ex.get("after_target_trail"), 0.3, 20, "Trail after target (ATR)"),
    }
    if exit_["after_target_trail"] is not None and exit_["target_r"] is None:
        raise ValueError("'Trail after target' needs a target: set a Target (R) too.")
    if exit_["target_r"] is None and exit_["trail_atr"] is None and not exit_["exit_on_opposite"] and exit_["time_stop_bars"] is None and raw.get("hold", "intraday") == "swing":
        raise ValueError("A swing strategy needs a target, trailing stop, time stop or opposite-signal exit, otherwise trades never close.")
    fl = {**DEFAULT_FILTERS, **(raw.get("filters") or {})}
    filters = {"start": _hhmm(fl["start"], "Start time"), "end": _hhmm(fl["end"], "End time"),
               "max_trades_day": _num(fl["max_trades_day"], 1, 20, "Max trades per day", allow_none=False, integer=True),
               "vix_min": _num(fl["vix_min"], 5, 90, "VIX minimum"), "vix_max": _num(fl["vix_max"], 5, 90, "VIX maximum"),
               "session": fl.get("session") or None}
    if filters["session"] not in (None,) + SESSION_FILTERS:
        raise ValueError("Session must be asia, london, newyork or overlap.")
    if filters["session"] and market == "IN":
        raise ValueError("World-session filters are for the crypto, US and gold books.")
    hold = raw.get("hold", "intraday" if market in ("IN",) else "swing")
    if hold not in ("intraday", "swing"):
        raise ValueError("Hold must be intraday or swing.")
    if market == "IN" and hold == "swing":
        raise ValueError("Indian index options strategies here are intraday only (they close by 15:15).")
    out = {"id": existing_id or raw.get("id") or "s_" + secrets.token_hex(3), "name": name, "about": str(raw.get("about") or "")[:300],
           "market": market, "symbols": syms, "timeframe": tf, "direction": direction, "long": long_, "short": short_,
           "exit": exit_, "filters": filters, "hold": hold, "enabled": bool(raw.get("enabled", False)), "auto": bool(raw.get("auto", False)), "watch": bool(raw.get("watch", False)),
           "created": raw.get("created") or C.now_ist().isoformat(timespec="seconds"), "version": int(raw.get("version") or 1)}
    if not re.fullmatch(r"s_[0-9a-f]{6}", out["id"]):
        out["id"] = "s_" + secrets.token_hex(3)
    return out


# ------------------------------------------------------------------ data helpers
def resample(df: pd.DataFrame, tf: str, market: str) -> pd.DataFrame:
    """Aggregate 5-minute (or finer) bars into the strategy timeframe on the session clock."""
    mins = TIMEFRAMES[tf]
    if df.empty:
        return df
    base = int(round(pd.Series(df.index).diff().dropna().median().total_seconds() / 60)) if len(df) > 2 else 5
    if mins == base or mins < base:
        return df
    rule = "1D" if mins == 1440 else f"{mins}min"
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    kw = {} if mins == 1440 else {"offset": SESSION[market]["offset"]}
    out = df.resample(rule, label="left", closed="left", **kw).agg(agg).dropna(subset=["open", "close"])
    return out


def bar_minutes(tf: str) -> int:
    return TIMEFRAMES[tf]


# ------------------------------------------------------------------ signals
def _cond_series(df, c, cache):
    l = X.operand_series(df, c["l"], cache)
    op = c["op"]
    if op in ("rising", "falling"):
        n = int(c["r"]["value"])
        d = l.diff()
        s = ((d > 0) if op == "rising" else (d < 0)).rolling(n, min_periods=n).sum() == n
        return s.fillna(False)
    r = X.operand_series(df, c["r"], cache)
    if op == "gt": s = l > r
    elif op == "lt": s = l < r
    elif op == "ge": s = l >= r
    elif op == "le": s = l <= r
    elif op == "crosses_above": s = (l.shift(1) <= r.shift(1)) & (l > r)
    else: s = (l.shift(1) >= r.shift(1)) & (l < r)
    return s.fillna(False)


def _side_series(df, side, cache):
    mask = pd.Series(True, index=df.index)
    any_ = None
    if not side["all"] and not side["any"]:
        return pd.Series(False, index=df.index)
    for c in side["all"]:
        mask &= _cond_series(df, c, cache)
    for c in side["any"]:
        s = _cond_series(df, c, cache)
        any_ = s if any_ is None else (any_ | s)
    if any_ is not None:
        mask &= any_
    return mask


def _window(df, filters):
    ok = pd.Series(True, index=df.index)
    if filters.get("session") and df.index.tz is not None:
        from . import sessions as SS
        ok &= pd.Series(SS.mask(df.index, filters["session"]), index=df.index)
    if filters.get("start") or filters.get("end"):
        t = pd.Series(df.index.hour * 60 + df.index.minute, index=df.index)
        bar = None
        if filters.get("start"):
            h, m = map(int, filters["start"].split(":"))
            ok &= t >= h * 60 + m
        if filters.get("end"):
            h, m = map(int, filters["end"].split(":"))
            ok &= t <= h * 60 + m
    return ok


def signals(spec: dict, df: pd.DataFrame) -> tuple[pd.Series, pd.Series, dict]:
    """(long, short, cache) boolean series on the strategy's own bars; filters (time window) applied."""
    cache: dict = {}
    d = spec["direction"]
    lg = _side_series(df, spec["long"], cache) if d in ("long", "both") else pd.Series(False, index=df.index)
    sh = _side_series(df, spec["short"], cache) if d in ("short", "both") else pd.Series(False, index=df.index)
    w = _window(df, spec["filters"])
    both = lg & sh
    return (lg & w & ~both), (sh & w & ~both), cache


# ------------------------------------------------------------------ position stepping (shared by the backtest and the live books)
def new_pos(side: int, entry: float, atr_sig: float, ex: dict) -> dict:
    """State of one open trade. side: +1 long, -1 short. Everything is fixed at entry, as in the live app."""
    risk = ex["stop_atr"] * atr_sig
    return {"side": side, "entry": float(entry), "risk": float(risk), "sl": float(entry - side * risk),
            "sl0": float(entry - side * risk),
            "tp": float(entry + side * ex["target_r"] * risk) if ex["target_r"] else None,
            "trail": float(ex["trail_atr"] * atr_sig) if ex["trail_atr"] else None,
            "be": float(entry + side * ex["breakeven_r"] * risk) if ex["breakeven_r"] else None,
            "after": float(ex["after_target_trail"] * atr_sig) if ex.get("after_target_trail") else None,
            "best": float(entry), "held": 0, "pend_opp": False, "mfe": 0.0, "mae": 0.0, "first": True, "atr": float(atr_sig)}


def step_pos(p: dict, o: float, h: float, l: float, c: float, end_min: int | None, ex: dict, sq_min: int | None,
             opp_signal: bool) -> tuple[float, str] | None:
    """Advance one finished bar. Returns (exit price, reason) when the trade ends on this bar, else None (state updated in place)."""
    side = p["side"]
    p["held"] += 1
    if p["pend_opp"]:
        return o, "Opposite signal"
    adverse, fav = (l, h) if side > 0 else (h, l)
    if not p["first"] and side * (o - p["sl"]) <= 0:
        return o, "Stop (gap)"
    if side * (adverse - p["sl"]) <= 0:
        if p["sl"] == p["entry"]:
            return p["sl"], "Break-even stop"
        return p["sl"], "Trailing stop" if p["sl"] != p["sl0"] and p["trail"] else "Stop-loss"
    if p["tp"] is not None and side * (fav - p["tp"]) >= 0:
        if not p.get("after"):
            return p["tp"], "Target"
        # target reached: do not exit. Lock in profit behind the target and let the stop trail from here on.
        lock = p["tp"] - side * p["after"]
        p["sl"] = max(p["sl"], lock) if side > 0 else min(p["sl"], lock)
        p["tp"], p["trail"], p["best"] = None, p["after"], float(fav)
    p["mfe"] = max(p["mfe"], side * (fav - p["entry"]) / p["risk"])
    p["mae"] = max(p["mae"], side * (p["entry"] - adverse) / p["risk"])
    if p["be"] is not None and side * (fav - p["be"]) >= 0 and side * (p["sl"] - p["entry"]) < 0:
        p["sl"] = p["entry"]
    if p["trail"]:
        p["best"] = max(p["best"], fav) if side > 0 else min(p["best"], fav)
        cand = p["best"] - side * p["trail"]
        if side * (cand - p["sl"]) > 0:
            p["sl"] = cand
    p["first"] = False
    if sq_min is not None and end_min is not None and end_min >= sq_min:
        return c, "Square-off"
    if ex["time_stop_bars"] and p["held"] >= ex["time_stop_bars"]:
        return c, "Time stop"
    if ex["exit_on_opposite"] and opp_signal:
        p["pend_opp"] = True
    return None


def trade_cost_R(market: str, entry: float, risk: float) -> float:
    slip, fee = COSTS_BPS[market]
    return (2 * slip + 2 * fee) / 1e4 * entry / risk


# ------------------------------------------------------------------ simulation (underlying, R multiples)
def simulate(spec: dict, df: pd.DataFrame, vix: pd.Series | None = None) -> list[dict]:
    """Walk the bars and return trades with R multiples on the underlying (costs in bps already deducted)."""
    market = spec["market"]
    ex, fl = spec["exit"], spec["filters"]
    if len(df) < 60:
        return []
    lg, sh, cache = signals(spec, df)
    a = I_atr(df, ex["atr_n"]).values
    o, h, l, c = (df[k].values.astype(float) for k in ("open", "high", "low", "close"))
    idx = df.index
    n = len(df)
    mins = bar_minutes(spec["timeframe"])
    sq = SESSION[market]["square_off"] if spec["hold"] == "intraday" else None
    sq_min = (sq[0] * 60 + sq[1]) if sq else None
    day = np.array([d.toordinal() for d in idx.date])
    start_min = idx.hour.values * 60 + idx.minute.values
    vixv = None
    if vix is not None and len(vix):
        try:
            vixv = vix.reindex(idx, method="ffill").values
        except Exception:
            vixv = None
    trades, per_day = [], {}
    i, last_exit_bar = 0, -1
    lgv, shv = lg.values, sh.values
    intraday = spec["hold"] == "intraday"
    while i < n - 1:
        side = 1 if lgv[i] else -1 if shv[i] else 0
        if side == 0 or i <= last_exit_bar:
            i += 1
            continue
        j = i + 1
        if (intraday and day[j] != day[i]) or per_day.get(day[j], 0) >= fl["max_trades_day"]:
            i += 1
            continue
        if vixv is not None and not np.isnan(vixv[i]):
            if (fl["vix_min"] and vixv[i] < fl["vix_min"]) or (fl["vix_max"] and vixv[i] > fl["vix_max"]):
                i += 1
                continue
        ai = a[i]
        if not np.isfinite(ai) or ai <= 0:
            i += 1
            continue
        p = new_pos(side, o[j], ai, ex)
        res, k_exit = None, None
        for k in range(j, n):
            if intraday and day[k] != day[j]:                              # data gap: out at the previous close
                res, k_exit = (c[k - 1], "End of session"), k - 1
                break
            opp = bool(shv[k]) if side > 0 else bool(lgv[k])
            res = step_pos(p, o[k], h[k], l[k], c[k], start_min[k] + mins, ex, sq_min, opp)
            if res:
                k_exit = k
                break
        if res is None:
            k_exit, res = n - 1, (c[n - 1], "Open at end of data")
        exit_px, reason = res
        gross = side * (exit_px - p["entry"])
        r_mult = gross / p["risk"] - trade_cost_R(market, p["entry"], p["risk"])
        trades.append({"side": "long" if side > 0 else "short", "entry_i": j, "exit_i": k_exit, "entry_ts": idx[j], "exit_ts": idx[k_exit],
                       "entry": float(p["entry"]), "exit": float(exit_px), "sl0": float(p["sl0"]), "tp0": p["tp"],
                       "risk": float(p["risk"]), "R": float(r_mult), "gross_R": float(gross / p["risk"]), "mfe_R": float(p["mfe"]), "mae_R": float(p["mae"]),
                       "bars": int(k_exit - j + 1), "reason": reason, "vix": None if vixv is None or np.isnan(vixv[i]) else float(vixv[i]),
                       "atr": float(ai)})
        per_day[day[j]] = per_day.get(day[j], 0) + 1
        last_exit_bar = k_exit
        i = k_exit + 1
    return trades


def I_atr(df, n):
    return X.atr(df, int(n))


# ------------------------------------------------------------------ statistics
def stats_R(rs) -> dict:
    r = np.asarray(list(rs), dtype=float)
    if len(r) == 0:
        return {"n": 0}
    w, lo = r[r > 0], r[r <= 0]
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    streak = cur = 0
    for x in r:
        cur = cur + 1 if x <= 0 else 0
        streak = max(streak, cur)
    out = {"n": int(len(r)), "win_rate": round(float(len(w) / len(r) * 100), 1), "avg_R": round(float(r.mean()), 3),
           "total_R": round(float(r.sum()), 2), "avg_win_R": round(float(w.mean()), 2) if len(w) else 0.0,
           "avg_loss_R": round(float(lo.mean()), 2) if len(lo) else 0.0,
           "profit_factor": round(float(w.sum() / -lo.sum()), 2) if lo.sum() < 0 else None,
           "max_dd_R": round(float((eq - peak).min()), 2), "longest_losing_streak": int(streak)}
    if len(r) >= 8:
        rng = np.random.default_rng(7)
        boots = rng.choice(r, size=(2000, len(r)), replace=True).mean(axis=1)
        out["avg_R_ci95"] = [round(float(np.percentile(boots, 2.5)), 3), round(float(np.percentile(boots, 97.5)), 3)]
        sd = r.std(ddof=1)
        out["t_stat"] = round(float(r.mean() / (sd / np.sqrt(len(r)))), 2) if sd > 0 else None
    return out


def verdict(total: dict, train: dict, unseen: dict, days: int) -> list[str]:
    """Plain-English reading of a backtest. These are warnings first, praise last."""
    msgs = []
    n = total.get("n", 0)
    if n == 0:
        return ["The rules never produced a trade on this data. Loosen a condition or widen the time window."]
    if n < 30:
        msgs.append(f"Only {n} trades: far too few to say anything reliable. Treat the numbers as a sketch.")
    elif n < 100:
        msgs.append(f"{n} trades is a small sample: results can swing a lot by chance.")
    ci = total.get("avg_R_ci95")
    if ci and ci[0] <= 0 <= ci[1]:
        msgs.append(f"The average trade ({total['avg_R']:+.2f}R) is not statistically different from zero (95% range {ci[0]:+.2f} to {ci[1]:+.2f}R).")
    if train.get("n", 0) >= 15 and unseen.get("n", 0) >= 10:
        if unseen["avg_R"] < 0 <= train["avg_R"]:
            msgs.append("It made money on the first 70% of the history and lost on the last 30%: the classic sign of a rule that was fitted to the past.")
        elif train["avg_R"] > 0 and unseen["avg_R"] < 0.4 * train["avg_R"]:
            msgs.append("The last 30% of the history was much weaker than the first 70%: expect the live edge to be smaller than the headline.")
        elif unseen["avg_R"] > 0 and train["avg_R"] > 0:
            msgs.append("Both the first 70% and the last 30% of the history were profitable, which is what a real edge looks like.")
    if total.get("profit_factor") and total["profit_factor"] < 1.0:
        msgs.append("Profit factor below 1: it lost money after costs.")
    if total.get("longest_losing_streak", 0) >= 8:
        msgs.append(f"Longest losing streak was {total['longest_losing_streak']} trades; be sure you could sit through that.")
    msgs.append("Tested rules were not tuned on this data by the app, but if YOU tried many variants and kept the best, the best-looking one is partly luck. Tracking it live on paper is the real test.")
    return msgs


# ------------------------------------------------------------------ option P&L for Indian index trades
def option_pnl(spec_symbol: str, trades: list[dict], vix: pd.Series | None, capital: float, risk_pct: float, otm: int = -C.STRIKE_ITM) -> dict:
    """Translate underlying trades into the ITM option the live app would buy, with slippage, charges and compounding."""
    from . import market as D
    from . import pricing as PR
    SLIP = 0.005
    meta = D.instrument(spec_symbol)
    try:
        lot = D.lot_size(spec_symbol)
    except Exception:                                  # database unreachable: fall back to the built-in lot size
        lot = meta["lot"]
    step = meta["step"]
    kind = meta["expiry"]
    eq, out, skipped = capital, [], 0
    vx = vix if (vix is not None and len(vix)) else None
    for t in sorted(trades, key=lambda x: x["entry_ts"]):
        ts_in, ts_out = t["entry_ts"].to_pydatetime(), t["exit_ts"].to_pydatetime()
        d = ts_in.date()
        exps = I.demo_expiries(kind, d, 4, meta.get("exchange", "NSE"))
        exp = I.pick_trading_expiry(exps, kind, d)
        if exp is None:
            skipped += 1
            continue
        sign = 1 if t["side"] == "long" else -1
        opt = "CE" if sign > 0 else "PE"
        spot = t["entry"]
        k = round(spot / step) * step + sign * otm * step
        v_in = t["vix"]
        if v_in is None and vx is not None:
            try:
                v_in = float(vx.asof(t["entry_ts"]))
            except Exception:
                v_in = None
        v_in = v_in or 14.0
        dte = (exp - d).days

        def prem(s, when, v):
            ty = max(I.years_to_expiry(exp, when.replace(tzinfo=None)), 1 / (365 * 24 * 12))
            return I.bs_price(s, k, ty, PR.iv(spec_symbol, v, s, k, opt, dte, step, meta["iv_mult"] or 1.0), opt)

        p_in = prem(spot, ts_in, v_in) * (1 + SLIP)
        p_sl = prem(t["sl0"], ts_in, v_in)
        risk_unit = max(p_in - p_sl, p_in * 0.05)
        lots = int((eq * risk_pct / 100) // (risk_unit * lot))
        if lots == 0 and risk_unit * lot <= 0.15 * eq:
            lots = 1
        if lots == 0 or p_in * lot > eq:
            skipped += 1
            continue
        lots = min(lots, int(eq // (p_in * lot)))
        hours = max((ts_out - ts_in).total_seconds() / 3600, 0.08)
        p_raw = prem(t["exit"], ts_out + timedelta(minutes=0), v_in)
        p_out = PR.real_adjust(p_in / (1 + SLIP), p_raw, dte, hours) * (1 - SLIP)
        qty = lots * lot
        pnl = (p_out - p_in) * qty - I.charges(p_in, p_out, qty, exch_pct=I.exch_rate(meta.get("symbol")))
        eq += pnl
        out.append({"ts": t["entry_ts"], "pnl": round(pnl, 0), "equity": round(eq, 0), "lots": lots, "prem_in": round(p_in, 2), "prem_out": round(p_out, 2)})
    pnls = np.array([x["pnl"] for x in out], dtype=float)
    res = {"capital": capital, "final_equity": round(eq, 0), "net": round(float(pnls.sum()), 0) if len(pnls) else 0, "trades": len(out), "skipped": skipped}
    if len(pnls):
        w, lo = pnls[pnls > 0], pnls[pnls <= 0]
        res.update({"win_rate": round(float(len(w) / len(pnls) * 100), 1), "profit_factor": round(float(w.sum() / -lo.sum()), 2) if lo.sum() < 0 else None,
                    "max_dd_pct": round(float(((np.array([capital] + [x["equity"] for x in out]) - np.maximum.accumulate(np.array([capital] + [x["equity"] for x in out]))) / np.maximum.accumulate(np.array([capital] + [x["equity"] for x in out]))).min() * 100), 1)})
    res["curve"] = [{"t": x["ts"].isoformat(), "v": x["equity"]} for x in out][-400:]
    return res


# ------------------------------------------------------------------ backtest wrapper
def backtest(spec: dict, df: pd.DataFrame, symbol: str, vix: pd.Series | None = None, capital: float = C.DEFAULT_CAPITAL, risk_pct: float = 1.0) -> dict:
    """df: raw candles in the base interval (5m for Indian data). Returns everything the app shows."""
    bars = resample(df, spec["timeframe"], spec["market"])
    if len(bars) < 80:
        return {"error": f"Only {len(bars)} bars of data for {symbol} - need at least 80. Try a shorter timeframe."}
    trades = simulate(spec, bars, vix)
    for t in trades:
        t["symbol"] = symbol
    res = summarize(spec, trades, symbol, len(bars), len(set(df.index.date)) if len(df) else 0,
                    str(df.index[0].date()), str(df.index[-1].date()))
    if spec["market"] == "IN" and trades:
        res["option"] = option_pnl(symbol, trades, vix, capital, risk_pct)
    return res


def summarize(spec: dict, trades: list[dict], symbol: str, n_bars: int, days: int, d_from: str, d_to: str) -> dict:
    """R statistics the backtest screens show, for one symbol or for several pooled (trades in entry-time order)."""
    rs = [t["R"] for t in trades]
    cut = int(len(rs) * 0.7)
    train, unseen = stats_R(rs[:cut]), stats_R(rs[cut:])
    total = stats_R(rs)
    hours = {}
    for t in trades:
        hh = t["entry_ts"].strftime("%H:00")
        hours.setdefault(hh, []).append(t["R"])
    years = {}
    for t in trades:
        years.setdefault(str(t["entry_ts"].year), []).append(t["R"])
    reasons = {}
    for t in trades:
        reasons.setdefault(t["reason"], []).append(t["R"])
    sides = {s: stats_R([t["R"] for t in trades if t["side"] == s]) for s in ("long", "short")}
    eq = np.cumsum(rs) if rs else np.array([])
    px = lambda v: round(float(v), 2 if abs(v) >= 100 else 4 if abs(v) >= 1 else 6)    # keep cheap coins (DOGE, XRP) readable
    res = {"symbol": symbol, "bars": n_bars, "days": days, "from": d_from, "to": d_to,
           "total": total, "train_70": train, "unseen_30": unseen, "by_side": sides,
           "by_hour": {k: stats_R(v) for k, v in sorted(hours.items())}, "by_year": {k: stats_R(v) for k, v in sorted(years.items())},
           "by_exit": {k: stats_R(v) for k, v in reasons.items()},
           "curve_R": [round(float(x), 2) for x in eq[-600:]],
           "trades": [{"ts": t["entry_ts"].isoformat(), "exit_ts": t["exit_ts"].isoformat(), "symbol": t.get("symbol", symbol), "side": t["side"],
                       "entry": px(t["entry"]), "exit": px(t["exit"]), "R": round(t["R"], 2), "reason": t["reason"], "bars": t["bars"]} for t in trades[-200:]],
           "verdict": verdict(total, train, unseen, days),
           "assumptions": ["Signal read at a bar's close, trade starts at the next bar's open.", "A bar touching both stop and target counts as a stop.",
                           f"Costs: {COSTS_BPS[spec['market']][0]:g} bps slippage + {COSTS_BPS[spec['market']][1]:g} bps fees per side on the underlying."
                           if spec["market"] != "IN" else "R is measured on the index, before option costs; money figures below price the ITM option with slippage and charges."]}
    return res


# ------------------------------------------------------------------ live evaluation
def _fmt(v):
    return "n/a" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:,.2f}"


def _describe_cond(c, df, cache):
    l = X.operand_series(df, c["l"], cache)
    if c["op"] in ("rising", "falling"):
        return f"{X.describe(c['l'])} {X.OPS[c['op']]} {c['r']['value']} bars (now {_fmt(l.iloc[-1])})"
    r = X.operand_series(df, c["r"], cache)
    return f"{X.describe(c['l'])} = {_fmt(l.iloc[-1])} {X.OPS[c['op']]} {X.describe(c['r'])} = {_fmt(r.iloc[-1])}"


def live_state(spec: dict, candles: pd.DataFrame, now: datetime) -> dict | None:
    """Latest COMPLETED bar's flags (used for the opposite-signal exit)."""
    bars = resample(candles, spec["timeframe"], spec["market"])
    mins = bar_minutes(spec["timeframe"])
    bars = bars[bars.index + timedelta(minutes=mins) <= now] if now.tzinfo is not None else bars
    if len(bars) < 60:
        return None
    lg, sh, _ = signals(spec, bars)
    return {"bar_end": bars.index[-1] + timedelta(minutes=mins), "long": bool(lg.iloc[-1]), "short": bool(sh.iloc[-1])}


def evaluate_live(spec: dict, symbol: str, candles: pd.DataFrame, vix: dict | None, now: datetime, trades_today: int = 0) -> dict:
    """Same shape the rule strategies return: {"signal", "checks", "plan"}. Used for Indian index/stock options."""
    out = {"signal": "WAIT", "checks": [], "plan": None}
    ex, fl = spec["exit"], spec["filters"]
    mins = bar_minutes(spec["timeframe"])
    bars = resample(candles, spec["timeframe"], spec["market"])
    bars = bars[bars.index + timedelta(minutes=mins) <= now] if now.tzinfo is not None else bars
    if len(bars) < 60 or bars.index[-1].date() != now.date():
        out["signal"] = "MARKET CLOSED"
        out["checks"].append((None, f"{spec['name']}: waiting for today's {spec['timeframe']} bars"))
        return out
    lg, sh, cache = signals(spec, bars)
    last = bars.index[-1]
    end = last + timedelta(minutes=mins)
    if trades_today >= fl["max_trades_day"]:
        out["signal"] = "NO NEW ENTRIES"
        out["checks"].append((False, f"{spec['name']}: already took {trades_today} trade(s) today (limit {fl['max_trades_day']})"))
        return out
    vlev = float((vix or {}).get("level") or 0)
    for key, bad in (("vix_min", lambda v, x: v < x), ("vix_max", lambda v, x: v > x)):
        if fl.get(key) and vlev and bad(vlev, fl[key]):
            out["signal"] = "NO TRADE TODAY"
            out["checks"].append((False, f"{spec['name']}: India VIX {vlev:.1f} outside your {fl['vix_min'] or '-'}–{fl['vix_max'] or '-'} range"))
            return out
    fresh = (now - end).total_seconds() / 60 <= C.SIGNAL_VALID_MIN
    side = 1 if lg.iloc[-1] else -1 if sh.iloc[-1] else 0
    for nm, sd, sg in (("LONG (CALL)", spec["long"], lg), ("SHORT (PUT)", spec["short"], sh)):
        if (nm.startswith("LONG") and spec["direction"] == "short") or (nm.startswith("SHORT") and spec["direction"] == "long"):
            continue
        out["checks"].append((None, f"{spec['name']} - {nm}:"))
        for c in sd["all"]:
            out["checks"].append((bool(_cond_series(bars, c, cache).iloc[-1]), _describe_cond(c, bars, cache)))
        if sd["any"]:
            ok_any = any(bool(_cond_series(bars, c, cache).iloc[-1]) for c in sd["any"])
            out["checks"].append((ok_any, "At least one of: " + "; ".join(_describe_cond(c, bars, cache) for c in sd["any"])))
    w = bool(_window(bars, fl).iloc[-1])
    if not w:
        out["checks"].append((False, f"Outside your trading window {fl['start'] or '-'}–{fl['end'] or '-'}"))
    if side == 0:
        return out
    if not fresh:
        out["checks"].append((False, f"Signal older than {C.SIGNAL_VALID_MIN} minutes - wait for the next bar"))
        return out
    a = float(X.atr(bars, ex["atr_n"]).iloc[-1])
    spot = float(bars["close"].iloc[-1])
    risk = ex["stop_atr"] * a
    opt = "CE" if side > 0 else "PE"
    plan = {"strategy": f"custom:{spec['id']}", "custom_name": spec["name"], "opt": opt, "entry": spot, "risk_pts": risk,
            "sl": spot - side * risk, "target": spot + side * ex["target_r"] * risk if ex["target_r"] else None, "rr": ex["target_r"],
            "signal_bar": end.strftime("%H:%M"), "spec_version": spec.get("version", 1),
            "trail_pts": ex["trail_atr"] * a if ex["trail_atr"] else None,
            "breakeven_at": spot + side * ex["breakeven_r"] * risk if ex["breakeven_r"] else None,
            "time_stop_bars": ex["time_stop_bars"], "bar_min": mins, "exit_on_opposite": ex["exit_on_opposite"],
            "after_trail_pts": ex["after_target_trail"] * a if ex.get("after_target_trail") else None,
            "trail": ", ".join(x for x in (f"trail {ex['trail_atr']:g} ATR" if ex["trail_atr"] else "", f"break-even at {ex['breakeven_r']:g}R" if ex["breakeven_r"] else "",
                                          f"time stop {ex['time_stop_bars']} bars" if ex["time_stop_bars"] else "", "exit on opposite signal" if ex["exit_on_opposite"] else "",
                                          f"after the target the stop trails {ex['after_target_trail']:g} ATR" if ex.get("after_target_trail") else "") if x) or "fixed stop and target, square-off 15:15"}
    out["signal"] = "BUY CALL" if side > 0 else "BUY PUT"
    out["plan"] = plan
    out["checks"].append((True, f"{spec['name']}: signal on the {end:%H:%M} bar - {'LONG' if side > 0 else 'SHORT'}"))
    return out


def custom_exit(pos: dict, spot: float, now: datetime, state: dict | None) -> tuple[str | None, dict]:
    """Extra exit rules for custom plans (trail, break-even, time stop, opposite signal). Index stop/target are in strategy.exit_check."""
    plan, upd = pos["plan"], {}
    long_ = plan["opt"] == "CE"
    sign = 1 if long_ else -1
    sl = pos.get("live_sl", plan["sl"])
    if plan.get("breakeven_at") is not None and not pos.get("at_breakeven") and sign * (spot - plan["breakeven_at"]) >= 0:
        sl = max(sl, plan["entry"]) if long_ else min(sl, plan["entry"])
        upd.update({"at_breakeven": True, "live_sl": sl})
    trail_pts = plan.get("after_trail_pts") if pos.get("target_hit") and plan.get("after_trail_pts") else plan.get("trail_pts")
    if trail_pts:
        best = pos.get("best_spot", plan["entry"])
        best = max(best, spot) if long_ else min(best, spot)
        cand = best - sign * trail_pts
        if sign * (cand - sl) > 0:
            sl = cand
            upd["live_sl"] = sl
        upd["best_spot"] = best
    if plan.get("time_stop_bars"):
        opened = datetime.fromisoformat(pos["opened"])
        mins = (now.replace(tzinfo=None) - opened.replace(tzinfo=None)).total_seconds() / 60
        if mins >= plan["time_stop_bars"] * plan.get("bar_min", 5):
            return f"Time stop ({plan['time_stop_bars']} bars)", upd
    if plan.get("exit_on_opposite") and state and state.get("bar_end"):
        opened = datetime.fromisoformat(pos["opened"])
        if state["bar_end"].replace(tzinfo=None) > opened.replace(tzinfo=None) and ((long_ and state["short"]) or (not long_ and state["long"])):
            return "Opposite signal on the strategy's bars", upd
    return None, upd


# ------------------------------------------------------------------ storage
def load_all(store) -> list[dict]:
    return store.get("custom_strategies") or []


def save_all(store, items: list[dict]):
    store.put("custom_strategies", items)


def upsert(store, raw: dict) -> dict:
    items = load_all(store)
    rid = raw.get("id")
    old = next((s for s in items if s["id"] == rid), None) if rid else None
    spec = validate({**(old or {}), **raw} if old else raw, existing_id=old["id"] if old else None)
    same_market = [s for s in items if s.get("market", "IN") == spec["market"] and (not old or s["id"] != old["id"])]
    if len(same_market) >= MAX_STRATEGIES:            # the limit is per market: Indian, crypto and US strategies never crowd each other out
        raise ValueError(f"You can keep up to {MAX_STRATEGIES} {spec['market']} strategies; delete one first.")
    if old:
        spec["created"] = old.get("created", spec["created"])
        changed = {k: spec[k] for k in ("long", "short", "exit", "filters", "timeframe", "direction", "symbols", "hold")} != \
                  {k: old.get(k) for k in ("long", "short", "exit", "filters", "timeframe", "direction", "symbols", "hold")}
        spec["version"] = int(old.get("version", 1)) + (1 if changed else 0)
        items = [spec if s["id"] == old["id"] else s for s in items]
    else:
        items.append(spec)
    save_all(store, items)
    return spec


def delete(store, sid: str) -> bool:
    items = load_all(store)
    keep = [s for s in items if s["id"] != sid]
    if len(keep) == len(items):
        return False
    save_all(store, keep)
    return True
