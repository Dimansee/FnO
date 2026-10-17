"""Forward-test lab for the crypto book: rules being tried live WITHOUT touching the paper money.

A lab rule records what it would have done, trade by trade, in R (risk units), in its own ledger. It never takes cash,
position slots or risk from the crypto / US books, and the Indian options engine never sees it. When a lab rule has
enough live trades and beats its live counterpart, it can be promoted into the autopilot.

Current lab rule (from the 17 Oct 2026 crypto research):
  "Supertrend 10/3 (4h) + BTC 200-day switch": the same signals as the live autopilot Supertrend 4h rule, but longs
  only while BTC's last daily close is above its 200-day average, shorts only while below. On the held-out
  Apr 2025 - Oct 2026 data it made +0.27R per trade (262 trades) against +0.15R unfiltered, but it was worse
  before 2025, so it has to prove itself live first.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from . import custom as CU
from . import gautopilot as AP
from . import market as M
from . import markets as MK
from . import store

POS, TRD = "g_lab_pos", "g_lab_trades"
MAX_TRADES_KEPT = 1000
LAB = [{"id": "s_b20001", "base": "s_a10001", "market": "CRYPTO", "name": "Supertrend 10/3 (4h) + BTC 200-day switch", "regime": "btc200"}]


def _now():
    return datetime.now(timezone.utc)


def _spec(rule: dict) -> dict:
    base = next(c for c in AP.CANDIDATES[rule["market"]] if c["id"] == rule["base"])
    spec = AP._spec(rule["market"], base, AP.SYMBOLS[rule["market"]])
    spec.update(id=rule["id"], name="Lab · " + rule["name"], lab=True)
    return spec


def btc_regime() -> dict:
    """{'bull': bool, 'close': last daily close, 'sma200': ...} from completed daily BTC bars."""
    def load():
        d = MK.completed(MK.candles("CRYPTO", "BTCUSDT", "1d", 320, ttl=3600), "1d")
        c = d["close"]
        sma = float(c.iloc[-200:].mean())
        return {"bull": bool(float(c.iloc[-1]) > sma), "close": round(float(c.iloc[-1]), 2), "sma200": round(sma, 2), "as_of": str(d.index[-1].date())}
    return M.cached("lab:btc200", 900, load)


def _allowed(rule: dict, side: int) -> bool:
    if rule.get("regime") == "btc200":
        bull = btc_regime()["bull"]
        return (side > 0 and bull) or (side < 0 and not bull)
    return True


def positions() -> dict:
    return store.get(POS) or {}


def trades() -> list[dict]:
    return store.get(TRD) or []


def _bars(spec, sym):
    from . import gbook as GB
    return GB._bars_for(spec, sym)


def _close(pos: dict, px: float, reason: str):
    st = pos["state"]
    side = st["side"]
    r = side * (px - st["entry"]) / st["risk"] - CU.trade_cost_R(pos["market"], st["entry"], st["risk"])
    rec = {k: pos[k] for k in ("id", "rule_id", "rule", "market", "symbol", "side", "opened")}
    rec.update(closed=_now().isoformat(timespec="seconds"), entry=round(st["entry"], 6), exit=round(float(px), 6), R=round(float(r), 3), reason=reason)
    tr = trades()
    tr.append(rec)
    store.put(TRD, tr[-MAX_TRADES_KEPT:])
    return rec


def tick() -> list[dict]:
    out = []
    rules = {r["id"]: r for r in LAB}
    specs = {r["id"]: _spec(r) for r in LAB}
    pos = positions()
    changed = False
    # 1. manage open lab trades: every completed bar since the last look, then the live price for an intrabar stop
    for pid, p in list(pos.items()):
        spec = specs.get(p["rule_id"])
        if not spec:
            continue
        try:
            bars = _bars(spec, p["symbol"])
        except Exception as e:
            out.append({"lab": p["symbol"], "error": str(e)[:80]})
            continue
        ex, st = spec["exit"], p["state"]
        last_ts = datetime.fromisoformat(p.get("last_bar_ts") or p["opened"])
        lg = sh = None
        if ex.get("exit_on_opposite"):
            lg, sh, _ = CU.signals(spec, bars)
        mins = CU.TIMEFRAMES[spec["timeframe"]]
        done = None
        for ts, b in bars[bars.index.tz_convert(timezone.utc) > last_ts].iterrows():
            opp = bool(sh.loc[ts]) if (lg is not None and st["side"] > 0) else bool(lg.loc[ts]) if lg is not None else False
            res = CU.step_pos(st, float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]), ts.hour * 60 + ts.minute + mins, ex, None, opp)
            p["last_bar_ts"] = ts.tz_convert(timezone.utc).isoformat()
            changed = True
            if res:
                done = res
                break
        if not done:
            try:
                px = MK.last_price(p["market"], p["symbol"])
                if st["side"] * (px - st["sl"]) <= 0:
                    done = (px if st["side"] * (px - st["sl"]) < 0 else st["sl"], "Stop-loss (live price)")
            except Exception:
                pass
        if done:
            rec = _close(p, done[0], done[1])
            pos.pop(pid, None)
            changed = True
            out.append({"lab_closed": rec["symbol"], "R": rec["R"], "reason": rec["reason"]})
    # 2. new signals
    for rid, spec in specs.items():
        rule = rules[rid]
        for sym in spec["symbols"]:
            if any(x["rule_id"] == rid and x["symbol"] == sym for x in pos.values()):
                continue
            try:
                bars = _bars(spec, sym)
                if len(bars) < 80:
                    continue
                lg, sh, _ = CU.signals(spec, bars)
                end = MK.bar_ends(bars.index[-1:], spec["timeframe"], spec["market"])[0].astimezone(timezone.utc)
                key = f"g_lab_seen:{rid}:{sym}"
                if store.get(key) == end.isoformat():
                    continue
                store.put(key, end.isoformat())
                side = 1 if lg.iloc[-1] else -1 if sh.iloc[-1] else 0
                if side == 0 or (_now() - end) > timedelta(minutes=20):
                    continue
                if not _allowed(rule, side):
                    out.append({"lab_skipped": f"{sym} {'long' if side > 0 else 'short'}: BTC regime says no"})
                    continue
                px = MK.last_price(spec["market"], sym)
                a = float(CU.I_atr(bars, spec["exit"]["atr_n"]).iloc[-1])
                slip = CU.COSTS_BPS[spec["market"]][0]
                st = CU.new_pos(side, px * (1 + side * slip / 1e4), a, spec["exit"])
                pid = secrets.token_hex(4)
                pos[pid] = {"id": pid, "rule_id": rid, "rule": rule["name"], "market": spec["market"], "symbol": sym, "side": "long" if side > 0 else "short",
                            "opened": _now().isoformat(timespec="seconds"), "last_bar_ts": end.isoformat(), "state": st}
                changed = True
                out.append({"lab_opened": sym, "side": pos[pid]["side"]})
            except Exception as e:
                out.append({"lab": sym, "error": str(e)[:80]})
    if changed:
        store.put(POS, pos)
    return out


def _stats(rs):
    if not rs:
        return {"n": 0}
    w = [r for r in rs if r > 0]
    lo = -sum(r for r in rs if r <= 0)
    return {"n": len(rs), "avg_R": round(sum(rs) / len(rs), 3), "total_R": round(sum(rs), 2), "win_rate": round(len(w) / len(rs) * 100, 1),
            "pf": round(sum(w) / lo, 2) if lo > 0 else None}


def summary() -> dict:
    """What the Markets tab shows: each lab rule's live record next to its live counterpart since the lab started."""
    from . import gbook as GB
    tr, pos = trades(), positions()
    out = []
    started = store.get("g_lab_started")
    if not started:
        started = _now().isoformat(timespec="seconds")
        store.put("g_lab_started", started)
    for r in LAB:
        mine = [t["R"] for t in tr if t["rule_id"] == r["id"]]
        base = [t.get("R") or 0 for t in GB.trades(r["market"]) if t.get("strategy_id") == r["base"] and t.get("opened", "") >= started]
        try:
            reg = btc_regime() if r.get("regime") == "btc200" else None
        except Exception:
            reg = None
        out.append({"id": r["id"], "name": r["name"], "since": started[:10], "lab": _stats(mine), "live_counterpart": _stats(base),
                    "open": [{"symbol": p["symbol"], "side": p["side"], "opened": p["opened"]} for p in pos.values() if p["rule_id"] == r["id"]],
                    "regime": reg, "promote_when": "at least 30 lab trades and a better average R than the live rule"})
    return {"rules": out}
