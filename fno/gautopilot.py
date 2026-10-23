"""Autopilot for the crypto, US and gold paper books.

When a market has NO strategy of yours on "Auto paper-trade", these built-in rules trade instead. Your own Auto
strategies always take priority: switch one on for a market and the autopilot steps aside there.

Where the rules come from (research/global_rules_search.py, 17 Oct 2026; write-up in the Project docs):
10 textbook rule families x timeframes x long/both = 100 tests on 10 crypto pairs (Binance spot, Jan 2023 - Oct 2026)
and 14 US stocks/ETFs (Yahoo 1h, ~2 years), same engine and costs as the app. Rules were chosen on the earlier part
of the data only and judged on a later part they never saw. Mean-reversion rules and everything on 1-hour bars lost
money after costs; slow trend-following (breakouts / Supertrend on 4-hour and daily bars) was the only family that
held up on the unseen part, in both markets. Those are the candidates below. None is individually "proven"
(t-stats 0.7-1.7 on the unseen data), so this is paper money testing the best evidence available, not a promise.

Live check (weekly): every candidate is re-backtested on its recent history, pooled over its symbols. A rule trades only
while that recent result is still positive (>= MIN_TRADES trades, avg >= MIN_AVG_R after costs, PF >= MIN_PF), and only on
symbols where it has not been clearly losing (own avg >= SYMBOL_FLOOR_R, or too few trades to tell). If nothing passes,
the market stays flat and the Markets tab says why.

Gold (23 Oct 2026, research/gold_sessions.py on COMEX gold 1h 2024-26, daily 2010-26, PAXG 1h 2020-26): no rule
passed the full design + holdout bar. Every session rule lost after costs (London breakout of the Asian range
-0.29R / -0.02R, Asian-hours drift -0.09R / -0.11R, NY opening range, fades, overlap-only Supertrend). Supertrend
4h / daily had no edge. Only the two long breakout rules below were positive in both halves, on few trades and in a
strong gold bull market, so they are kept as candidates and still face the weekly gate. Only XAUUSD is traded by the
autopilot (PAXG is the same ounce, so trading both would double one bet).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import numpy as np

from . import custom as CU
from . import markets as MK
from . import store

QUAL_TTL = 7 * 86400          # re-test every week
RETRY_TTL = 3 * 3600          # a market whose test could not run (data feed down) is retried sooner
MIN_TRADES, MIN_AVG_R, MIN_PF = 30, 0.03, 1.05
SYMBOL_FLOOR_R, SYMBOL_MIN_N = -0.10, 6
CHECK_DAYS = {("CRYPTO", "4h"): 365, ("CRYPTO", "1d"): 1095, ("US", "4h"): 720, ("US", "1h"): 720,
              ("GOLD", "1h"): 720, ("GOLD", "4h"): 720, ("GOLD", "1d"): 2555}   # gold is one symbol: longer windows so there are enough trades to judge
SYMBOLS = {"CRYPTO": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT"],
           "US": ["SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "JPM", "GLD"],
           "GOLD": ["XAUUSD"]}


def _c(l, op, r):
    return {"l": l, "op": op, "r": r}


_P = {"price": "close"}
_EX = {"target_r": None}
_SUPERTREND = {"long": {"all": [_c({"ind": "supertrend", "n": 10, "mult": 3.0, "out": "dir"}, "crosses_above", {"value": 0})]},
               "short": {"all": [_c({"ind": "supertrend", "n": 10, "mult": 3.0, "out": "dir"}, "crosses_below", {"value": 0})]},
               "exit": {**_EX, "stop_atr": 2.0, "exit_on_opposite": True}}


def _donchian(n, trail):
    return {"long": {"all": [_c(_P, "crosses_above", {"ind": "donchian", "n": n, "out": "upper"})]},
            "short": {"all": [_c(_P, "crosses_below", {"ind": "donchian", "n": n, "out": "lower"})]},
            "exit": {**_EX, "stop_atr": 2.0, "trail_atr": trail}}


_KELTNER = {"long": {"all": [_c(_P, "crosses_above", {"ind": "keltner", "n": 20, "mult": 2.0, "out": "upper"})]},
            "short": {"all": [_c(_P, "crosses_below", {"ind": "keltner", "n": 20, "mult": 2.0, "out": "lower"})]},
            "exit": {**_EX, "stop_atr": 2.0, "trail_atr": 3.0}}

# Fixed ids so open positions always find their exit rules again, even after a re-test.
# 'research' = pooled result on the unseen part of the history (avg R per trade after costs, trades).
CANDIDATES = {
    "CRYPTO": [
        {"id": "s_a10001", "name": "Supertrend 10/3 (4h)", "timeframe": "4h", "direction": "both", **_SUPERTREND, "research": "+0.15R over 487 unseen trades, every year positive",
         "about": "Buy when a 4-hour bar closes above the Supertrend line (trend flips up); sell short when one closes below it (flips down). Stop 2 x ATR from entry; exit on the opposite flip. No fixed target: it rides the trend."},
        {"id": "s_a10002", "name": "Donchian 55 breakout, long (4h)", "timeframe": "4h", "direction": "long", **_donchian(55, 4.0), "research": "+0.14R over 298 unseen trades",
         "about": "Buy when a 4-hour bar closes above the highest high of the previous 55 bars (about 9 days). Stop 2 x ATR; then a trailing stop 4 x ATR below the best price. Longs only."},
        {"id": "s_a10003", "name": "Donchian 20 breakout, long (daily)", "timeframe": "1d", "direction": "long", **_donchian(20, 3.0), "research": "+0.22R over 95 unseen trades, every year positive",
         "about": "Buy when a daily close (00:00 UTC, 05:30 IST) is above the highest high of the previous 20 days. Stop 2 x ATR; trailing stop 3 x ATR below the best price. Longs only."},
        {"id": "s_a10004", "name": "Supertrend 10/3 (daily)", "timeframe": "1d", "direction": "both", **_SUPERTREND, "research": "+0.33R over 73 unseen trades, depends on a few big winners",
         "about": "Same as the 4-hour Supertrend rule on daily bars: buy on an up-flip, short on a down-flip, stop 2 x ATR, exit on the opposite flip. Few trades, large winners."},
    ],
    "US": [
        {"id": "s_a10011", "name": "Donchian 55 breakout, long (4h)", "timeframe": "4h", "direction": "long", **_donchian(55, 4.0), "research": "+0.40R over 76 unseen trades",
         "about": "Buy when a 4-hour bar (two per US session) closes above the highest high of the previous 55 bars. Stop 2 x ATR, trailing stop 4 x ATR. Longs only, whole shares."},
        {"id": "s_a10012", "name": "Keltner breakout, long (4h)", "timeframe": "4h", "direction": "long", **_KELTNER, "research": "+0.32R over 115 unseen trades",
         "about": "Buy when a 4-hour bar closes above the upper Keltner band (20-bar EMA + 2 x ATR). Stop 2 x ATR, trailing stop 3 x ATR. Longs only, whole shares."},
    ],
    "GOLD": [
        {"id": "s_a10022", "name": "Donchian 55 breakout, long (4h)", "timeframe": "4h", "direction": "long", **_donchian(55, 4.0),
         "research": "gold 1h 2024-26: +0.23R over 29 earlier trades, +0.90R over 12 later ones; few trades, gold's bull run helps longs",
         "about": "Buy when a 4-hour bar (London clock: 00, 04, 08, 12, 16, 20) closes above the highest high of the previous 55 bars (about two weeks of gold trading). Stop 2 x ATR, trailing stop 4 x ATR. Longs only."},
        {"id": "s_a10023", "name": "Donchian 20 breakout, long (daily)", "timeframe": "1d", "direction": "long", **_donchian(20, 3.0),
         "research": "gold daily 2010-26: +0.40R over 54 trades to 2021, +0.48R over 26 since 2022 (t 1.6 / 1.2)",
         "about": "Buy when a COMEX day (ends 17:00 New York, 02:30-03:30 IST) closes above the highest high of the previous 20 days. Stop 2 x ATR, trailing stop 3 x ATR. Longs only."},
    ],
}


def _spec(market: str, cand: dict, symbols: list[str]) -> dict:
    raw = {k: v for k, v in cand.items() if k not in ("research", "about")}
    raw.update(name="Autopilot · " + cand["name"], market=market, symbols=symbols or SYMBOLS[market][:1], hold=cand.get("hold", "swing"),
               filters={"max_trades_day": 3, **(cand.get("filters") or {})}, enabled=True, auto=True, version=1)
    spec = CU.validate(raw, existing_id=cand["id"])
    spec["autopilot"] = True
    return spec


def all_specs(market: str) -> list[dict]:
    """Every candidate on every symbol. Used to find exit rules for positions the autopilot already holds."""
    return [_spec(market, c, SYMBOLS[market]) for c in CANDIDATES.get(market, [])]


def _stats(rs):
    r = np.asarray(rs, float)
    if not len(r):
        return {"n": 0, "avg_R": 0.0, "pf": None}
    lo = -r[r <= 0].sum()
    return {"n": int(len(r)), "avg_R": round(float(r.mean()), 3), "pf": round(float(r[r > 0].sum() / lo), 2) if lo > 0 else None}


def _rule_ok(s: dict) -> tuple[bool, str]:
    if s["n"] < MIN_TRADES:
        return False, f"only {s['n']} trades in the recent check"
    if s["avg_R"] < MIN_AVG_R:
        return False, f"recent average {s['avg_R']:+.2f}R per trade"
    if s["pf"] is not None and s["pf"] < MIN_PF:
        return False, f"recent profit factor {s['pf']}"
    return True, f"{s['n']} recent trades, {s['avg_R']:+.2f}R avg, PF {s['pf'] or 'no losses'}"


def qualify(market: str, force: bool = False) -> dict:
    """Re-test every candidate on recent history and store which rules / symbols may trade. Slow (network): runs off the request path."""
    cur = store.get(f"g_ap_qual:{market}") or {}
    fresh_ids = {c["id"] for c in CANDIDATES.get(market, [])}
    stale_set = set(cur.get("ids") or []) != fresh_ids          # candidate list changed (new research): re-test now
    age = time.time() - float(cur.get("t", 0))
    if not force and not stale_set and age < (RETRY_TTL if cur.get("data_errors") else QUAL_TTL):
        return cur
    rows, ok, errors = [], {}, 0
    for cand in CANDIDATES.get(market, []):
        spec = _spec(market, cand, SYMBOLS[market])
        days = CHECK_DAYS.get((market, spec["timeframe"]), 365)
        pooled, per = [], {}
        for sym in SYMBOLS[market]:
            try:
                df = MK.candles(market, sym, spec["timeframe"], days, ttl=3600)
                rs = [t["R"] for t in CU.simulate(spec, df)]
            except Exception as e:
                errors += 1
                per[sym] = {"error": str(e)[:80]}
                continue
            pooled += rs
            per[sym] = _stats(rs)
        s = _stats(pooled)
        good, why = _rule_ok(s)
        syms = [k for k, v in per.items() if "error" not in v and (v["n"] < SYMBOL_MIN_N or v["avg_R"] >= SYMBOL_FLOOR_R)] if good else []
        dropped = [k for k in per if k not in syms and "error" not in per[k]] if good else []
        if good and syms:
            ok[cand["id"]] = syms
        rows.append({"id": cand["id"], "name": spec["name"], "symbol": ", ".join(syms) if good else "all", "ok": bool(good and syms),
                     "why": why + (f"; skipping {', '.join(dropped)} (losing there lately)" if dropped else ""), "research": cand.get("research"), "per_symbol": per})
    out = {"t": time.time(), "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": ok, "rows": rows,
           "data_errors": errors, "ids": sorted(fresh_ids)}
    store.put(f"g_ap_qual:{market}", out)
    return out


def has_result(market: str) -> bool:
    q = store.get(f"g_ap_qual:{market}") or {}
    return bool(q.get("rows")) and set(q.get("ids") or []) == {c["id"] for c in CANDIDATES.get(market, [])}


def active_specs(market: str) -> list[dict]:
    """The autopilot strategies allowed to open trades now (empty until the weekly check has run)."""
    q = store.get(f"g_ap_qual:{market}") or {}
    cands = {c["id"]: c for c in CANDIDATES.get(market, [])}
    return [_spec(market, cands[cid], syms) for cid, syms in (q.get("ok") or {}).items() if cid in cands and syms]


def status(market: str, in_use: bool) -> dict:
    q = store.get(f"g_ap_qual:{market}") or {}
    rows = [{k: v for k, v in r.items() if k != "per_symbol"} for r in q.get("rows", [])]
    return {"in_use": in_use, "tested_at": q.get("at"), "passed": [r for r in rows if r["ok"]], "failed": [r for r in rows if not r["ok"]],
            "tested": len(rows), "rules": f"each rule needs ≥{MIN_TRADES} recent trades, ≥{MIN_AVG_R}R average and profit factor ≥{MIN_PF}"}


def rulebook(market: str) -> list[dict]:
    """Every built-in rule with its plain-English text, latest weekly check and the strategy itself (for the Backtest tab)."""
    q = store.get(f"g_ap_qual:{market}") or {}
    rows = {r["id"]: r for r in q.get("rows", [])}
    out = []
    for c in CANDIDATES.get(market, []):
        r = rows.get(c["id"], {})
        spec = _spec(market, c, SYMBOLS[market])
        spec.pop("autopilot", None)
        out.append({"id": c["id"], "name": c["name"], "timeframe": c["timeframe"], "direction": c["direction"], "about": c.get("about"),
                    "research": c.get("research"), "active": bool(r.get("ok")), "symbols": r.get("symbol") if r.get("ok") else None,
                    "check": r.get("why"), "spec": spec})
    return out
