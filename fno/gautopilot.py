"""Autopilot for the crypto and US paper books.

Why this exists: the crypto / US books only traded strategies you had built and switched to "Auto paper-trade"
in the Strategy lab. With none switched on, those books never opened a position. You asked for the app to trade
by itself with no settings, so when a market has NO strategy of yours on auto, these built-in candidates run instead.

Nothing is switched on blindly. Once a week every candidate is backtested on that symbol's own recent history
(same engine, same fees and slippage as the Strategy lab) and only the (strategy, symbol) pairs that pass ALL of
these checks may trade:
  - at least MIN_TRADES trades in the test
  - average trade above MIN_AVG_R (after costs)
  - profit factor at least MIN_PF
  - the most recent 30% of the history (the part a fitted rule usually fails on) was profitable too
If nothing passes, the market stays flat and the Markets tab says why, instead of trading a losing rule.
Your own strategies always take priority: switch one to Auto for a market and the autopilot steps aside there.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from . import custom as CU
from . import markets as MK
from . import store

QUAL_TTL = 7 * 86400          # re-test every week
RETRY_TTL = 3 * 3600          # a market whose test could not run (data feed down) is retried sooner
MIN_TRADES, MIN_AVG_R, MIN_PF = 25, 0.05, 1.10
HISTORY_DAYS = {"5m": 58, "15m": 58, "30m": 58, "1h": 365, "4h": 730, "1d": 1500}   # free feeds: US intraday stops at ~60 days
SYMBOLS = {"CRYPTO": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"], "US": ["SPY", "QQQ", "AAPL", "NVDA", "MSFT"]}


def _c(l, op, r):
    return {"l": l, "op": op, "r": r}


_EMA_ADX = {"long": {"all": [_c({"ind": "ema", "n": 9}, "crosses_above", {"ind": "ema", "n": 21}), _c({"ind": "adx", "n": 14, "out": "adx"}, "gt", {"value": 22})]},
            "short": {"all": [_c({"ind": "ema", "n": 9}, "crosses_below", {"ind": "ema", "n": 21}), _c({"ind": "adx", "n": 14, "out": "adx"}, "gt", {"value": 22})]}}
_RSI_DIP = {"long": {"all": [_c({"price": "close"}, "gt", {"ind": "ema", "n": 50}), _c({"ind": "rsi", "n": 7}, "crosses_above", {"value": 30})]},
            "short": {"all": [_c({"price": "close"}, "lt", {"ind": "ema", "n": 50}), _c({"ind": "rsi", "n": 7}, "crosses_below", {"value": 70})]}}

# Fixed ids (s_a + 5 hex) so open positions always find their exit rules again, even after a re-test.
CANDIDATES = {
    "CRYPTO": [
        {"id": "s_a00001", "preset": "Bollinger mean reversion", "timeframe": "1h"},
        {"id": "s_a00002", "preset": "Donchian breakout (trend following)", "timeframe": "4h"},
        {"id": "s_a00003", "name": "EMA 9/21 trend + ADX (1h)", "timeframe": "1h", "hold": "swing", **_EMA_ADX,
         "exit": {"stop_atr": 2.0, "target_r": 3.0, "trail_atr": 2.5, "exit_on_opposite": True}, "filters": {"max_trades_day": 3}},
        {"id": "s_a00004", "name": "RSI dip inside an uptrend (1h)", "timeframe": "1h", "hold": "swing", **_RSI_DIP,
         "exit": {"stop_atr": 1.5, "target_r": 1.5, "time_stop_bars": 24}, "filters": {"max_trades_day": 3}},
    ],
    "US": [
        {"id": "s_a00011", "preset": "Opening-range breakout + volume", "timeframe": "5m"},
        {"id": "s_a00012", "name": "EMA 9/21 trend + ADX (15m)", "timeframe": "15m", "hold": "intraday", **_EMA_ADX,
         "exit": {"stop_atr": 1.5, "target_r": 2.0}, "filters": {"start": "10:00", "end": "15:00", "max_trades_day": 2}},
        {"id": "s_a00013", "name": "RSI dip inside an uptrend (1h swing)", "timeframe": "1h", "hold": "swing", **_RSI_DIP,
         "exit": {"stop_atr": 1.5, "target_r": 1.5, "time_stop_bars": 14}, "filters": {"max_trades_day": 1}},
    ],
}


def _spec(market: str, cand: dict, symbols: list[str]) -> dict:
    base = next((p for p in CU.PRESETS if p["name"] == cand.get("preset")), {}) if cand.get("preset") else {}
    raw = {**base, **{k: v for k, v in cand.items() if k not in ("preset",)}}
    raw["name"] = "Autopilot · " + (cand.get("name") or base.get("name", cand["id"]))
    raw.update(market=market, symbols=symbols or SYMBOLS[market][:1], direction=raw.get("direction", "both"), enabled=True, auto=True, version=1)
    spec = CU.validate(raw, existing_id=cand["id"])
    spec["autopilot"] = True
    return spec


def all_specs(market: str) -> list[dict]:
    """Every candidate (all symbols). Used to find exit rules for positions the autopilot already holds."""
    return [_spec(market, c, SYMBOLS[market]) for c in CANDIDATES.get(market, [])]


def _passes(res: dict) -> tuple[bool, str]:
    t, u = res.get("total") or {}, res.get("unseen_30") or {}
    if res.get("error"):
        return False, res["error"]
    if t.get("n", 0) < MIN_TRADES:
        return False, f"only {t.get('n', 0)} trades in the test"
    if (t.get("avg_R") or 0) < MIN_AVG_R:
        return False, f"average trade {t.get('avg_R', 0):+.2f}R"
    if t.get("profit_factor") is not None and t["profit_factor"] < MIN_PF:   # None = no losing trade at all
        return False, f"profit factor {t.get('profit_factor')}"
    if u.get("n", 0) < 8 or (u.get("avg_R") or 0) <= 0:
        return False, f"recent 30% of history {u.get('avg_R', 0):+.2f}R over {u.get('n', 0)} trades"
    return True, f"{t['n']} trades, {t['avg_R']:+.2f}R avg, PF {t['profit_factor'] or 'no losses'}, recent {u['avg_R']:+.2f}R"


def qualify(market: str, force: bool = False) -> dict:
    """Backtest every candidate on every symbol and store which pairs may trade. Slow (network); run it off the request path."""
    cur = store.get(f"g_ap_qual:{market}") or {}
    age = time.time() - float(cur.get("t", 0))
    if not force and age < (RETRY_TTL if cur.get("data_errors") else QUAL_TTL):
        return cur
    rows, ok, errors = [], {}, 0
    for cand in CANDIDATES.get(market, []):
        spec = _spec(market, cand, SYMBOLS[market])
        days = HISTORY_DAYS[spec["timeframe"]]
        for sym in SYMBOLS[market]:
            try:
                df = MK.candles(market, sym, spec["timeframe"], days, ttl=3600)
                res = CU.backtest(spec, df, sym)
                good, why = _passes(res)
            except Exception as e:
                good, why = False, f"data error: {str(e)[:80]}"
                errors += 1
            rows.append({"id": cand["id"], "name": spec["name"], "symbol": sym, "ok": good, "why": why})
            if good:
                ok.setdefault(cand["id"], []).append(sym)
    out = {"t": time.time(), "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": ok, "rows": rows, "data_errors": errors}
    store.put(f"g_ap_qual:{market}", out)
    return out


def active_specs(market: str) -> list[dict]:
    """The autopilot strategies allowed to open trades now (empty until the weekly test has run)."""
    q = store.get(f"g_ap_qual:{market}") or {}
    cands = {c["id"]: c for c in CANDIDATES.get(market, [])}
    return [_spec(market, cands[cid], syms) for cid, syms in (q.get("ok") or {}).items() if cid in cands and syms]


def status(market: str, in_use: bool) -> dict:
    q = store.get(f"g_ap_qual:{market}") or {}
    return {"in_use": in_use, "tested_at": q.get("at"), "passed": [r for r in q.get("rows", []) if r["ok"]],
            "tested": len(q.get("rows", [])), "rules": f"needs ≥{MIN_TRADES} trades, ≥{MIN_AVG_R}R average, profit factor ≥{MIN_PF} and a profitable recent 30%"}
