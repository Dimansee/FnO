"""Paper books for crypto (USDT) and US stocks / ETFs (USD).

Kept completely separate from the Indian options ledger: its own money, positions and trades, so nothing you do
here can touch the F&O results. Trades come from your custom strategies (market = CRYPTO or US) and follow exactly
the same bar-by-bar exit logic as the backtester (custom.step_pos), so what you saw in the backtest is what runs.

A tick (every 5 minutes from GitHub Actions, and whenever the app is open) does, per market:
  1. manage open positions: every bar completed since the last look, then the latest price
  2. look for new signals on the last completed bar of each enabled strategy / symbol
Sizing: risk_pct of the market's equity per trade (default 1%), position value capped by max_leverage.
Costs: the same slippage and fee assumptions the backtester uses (custom.COSTS_BPS).
Crypto shorts are perpetual-style (no funding modelled). US shorts are allowed on paper with full margin.
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import config as C
from . import custom as CU
from . import ind as X
from . import markets as MK
from . import store

DEFAULT_CAPITAL = {"CRYPTO": 10_000.0, "US": 10_000.0}
PREF_DEFAULTS = {"risk_pct": 1.0, "max_leverage": 1.0, "max_open": 4, "auto": True}
MAX_LEVERAGE = {"CRYPTO": 5.0, "US": 2.0}
POS, TRD = "fno:g:pos:", "fno:g:trades:"


def _dump(v):
    return json.dumps(v, default=str, separators=(",", ":"))


# ------------------------------------------------------------------ account
def prefs(market: str) -> dict:
    return {**PREF_DEFAULTS, **(store.get(f"g_prefs:{market}") or {})}


def set_prefs(market: str, p: dict) -> dict:
    cur = prefs(market)
    if "risk_pct" in p:
        v = float(p["risk_pct"])
        if not 0.1 <= v <= 5:
            raise ValueError("Risk per trade must be between 0.1% and 5%.")
        cur["risk_pct"] = v
    if "max_leverage" in p:
        v = float(p["max_leverage"])
        if not 1 <= v <= MAX_LEVERAGE[market]:
            raise ValueError(f"Leverage must be between 1 and {MAX_LEVERAGE[market]:g}x for {market}.")
        cur["max_leverage"] = v
    if "max_open" in p:
        v = int(p["max_open"])
        if not 1 <= v <= 12:
            raise ValueError("Max open positions must be between 1 and 12.")
        cur["max_open"] = v
    if "auto" in p:
        cur["auto"] = bool(p["auto"])
    store.put(f"g_prefs:{market}", cur)
    return cur


def account(market: str) -> dict:
    a = store.get(f"g_account:{market}")
    if not a:
        a = {"capital_start": DEFAULT_CAPITAL[market], "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        store.put(f"g_account:{market}", a)
    return a


def reset(market: str, capital: float):
    if not 100 <= capital <= 1e9:
        raise ValueError("Starting capital must be between 100 and 1,000,000,000.")
    store._cmd("DEL", POS + market)
    store._cmd("DEL", TRD + market)
    store.put(f"g_account:{market}", {"capital_start": float(capital), "created": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def positions(market: str) -> list[dict]:
    raw = store._cmd("HVALS", POS + market) or []
    return sorted((json.loads(x) for x in raw), key=lambda p: p["opened"])


def trades(market: str) -> list[dict]:
    raw = store._cmd("HVALS", TRD + market) or []
    return sorted((json.loads(x) for x in raw), key=lambda p: p["closed"])


def _save_pos(p):
    store._cmd("HSET", POS + p["market"], p["id"], _dump(p))


def _take_pos(market, pid):
    v = store._cmd("EVAL", store._TAKE, 1, POS + market, pid)
    return json.loads(v) if v else None


def equity(market: str) -> float:
    return account(market)["capital_start"] + sum(t["pnl"] for t in trades(market))


def cash(market: str) -> float:
    return equity(market) - sum(p["margin"] for p in positions(market))


# ------------------------------------------------------------------ opening and closing
def _slip_fee(market):
    return CU.COSTS_BPS[market]


def _spec_map():
    from . import gautopilot as AP
    out = {s["id"]: s for m in ("CRYPTO", "US") for s in AP.all_specs(m)}   # so autopilot positions keep their exit rules
    out.update({s["id"]: s for s in CU.load_all(store)})
    return out


def _own_auto(specs: dict, market: str) -> list[dict]:
    return [s for s in specs.values() if not s.get("autopilot") and s.get("enabled") and s.get("auto") and s["market"] == market]


def runners(market: str, specs: dict | None = None) -> tuple[list[dict], str]:
    """Strategies allowed to open trades in this market: yours if any are on Auto, otherwise the tested autopilot set."""
    from . import gautopilot as AP
    own = _own_auto(specs if specs is not None else _spec_map(), market)
    if own:
        return own, "yours"
    return AP.active_specs(market), "autopilot"


_TICK_LOCK = threading.Lock()   # one tick at a time per process (scheduler, background loop and an open page can overlap)


def _now():
    return datetime.now(timezone.utc)


def _local(market, dt=None):
    return (dt or _now()).astimezone(MK.TZ[market])


def _ctx(market, bars, side, mins):
    """Context stored with the trade (what the learning engine groups by)."""
    try:
        c = bars["close"]
        e20, e50 = X.ema(c, 20).iloc[-1], X.ema(c, 50).iloc[-1]
        last = float(c.iloc[-1])
        trend = "up" if last > e20 > e50 else "down" if last < e20 < e50 else "mixed"
        atrp = (X.atr(bars, 14) / bars["close"]).dropna()
        v = float(atrp.iloc[-1] / atrp.iloc[-100:].median()) if len(atrp) > 30 else 1.0
        loc = _local(market)
        return {"trend": trend, "vol": "low" if v < 0.8 else "high" if v > 1.25 else "mid", "local_time": loc.strftime("%H:%M"), "atr_pct": round(float(atrp.iloc[-1]) * 100, 3)}
    except Exception:
        return {"local_time": _local(market).strftime("%H:%M")}


def open_position(market: str, spec: dict, symbol: str, side: int, price: float, atr_sig: float, bars: pd.DataFrame, bar_end: datetime) -> tuple[bool, str]:
    pf = prefs(market)
    slip, fee = _slip_fee(market)
    ex = spec["exit"]
    from . import learn as LN
    strat = f"custom:{spec['id']}"
    # learning engine: skip this automatic entry? (never blocks a manual / backtest run)
    tags = LN.tags_for({"market": market, "side": "long" if side > 0 else "short", "time": _ctx(market, bars, side, 0).get("local_time"),
                        "wd": _local(market).weekday(), **{k: v for k, v in _ctx(market, bars, side, 0).items() if k in ("trend", "vol")}})
    why = LN.veto(market, strat, tags)
    if why:
        return False, why
    eq = equity(market)
    mult = LN.size_mult(market, strat)
    entry = price * (1 + side * slip / 1e4)
    st = CU.new_pos(side, entry, atr_sig, ex)
    risk_per_unit = st["risk"]
    if risk_per_unit <= 0:
        return False, "Zero risk distance"
    qty = (eq * pf["risk_pct"] / 100 * mult) / risk_per_unit
    lev = pf["max_leverage"] if market == "CRYPTO" else min(pf["max_leverage"], MAX_LEVERAGE["US"])
    max_notional = eq * lev / max(pf["max_open"], 1) * 1.5
    if qty * entry > max_notional:
        qty = max_notional / entry
    if market == "US":
        qty = float(int(qty))                      # whole shares
    if qty <= 0:
        return False, "Position size rounds to zero for this account size."
    margin = qty * entry / (lev if market == "CRYPTO" else (1 if side < 0 else lev))
    if margin > cash(market):
        return False, f"Not enough free cash: need {margin:,.2f}, have {cash(market):,.2f}."
    fees = qty * entry * fee / 1e4
    now = _now()
    pos = {"id": secrets.token_hex(4), "market": market, "symbol": symbol, "side": "long" if side > 0 else "short", "qty": round(qty, 6), "entry": round(entry, 6),
           "margin": round(margin, 2), "leverage": lev, "fees_in": round(fees, 4), "strategy_id": spec["id"], "strategy_name": spec["name"], "spec_version": spec.get("version", 1),
           "state": st, "opened": now.isoformat(timespec="seconds"), "from_bar_ts": bar_end.isoformat(), "ctx": _ctx(market, bars, side, 0), "size_mult": mult,
           "tf": spec["timeframe"], "hold": spec["hold"]}
    _save_pos(pos)
    return True, f"{spec['name']}: {pos['side']} {symbol} x {qty:g} at {entry:,.4f} (stop {st['sl']:,.4f}" + (f", target {st['tp']:,.4f}" if st["tp"] else "") + ")"


def close_position(market: str, pid: str, px: float, reason: str) -> dict | None:
    pos = _take_pos(market, pid)
    if not pos:
        return None
    slip, fee = _slip_fee(market)
    side = 1 if pos["side"] == "long" else -1
    exit_fill = px * (1 - side * slip / 1e4)
    gross = side * (exit_fill - pos["entry"]) * pos["qty"]
    fees = pos["fees_in"] + pos["qty"] * exit_fill * fee / 1e4
    st = pos["state"]
    R = (side * (exit_fill - pos["entry"]) - pos["fees_in"] / pos["qty"] - pos["qty"] * exit_fill * fee / 1e4 / pos["qty"]) / st["risk"]
    rec = {**pos, "closed": _now().isoformat(timespec="seconds"), "exit": round(exit_fill, 6), "gross_pnl": round(gross, 4), "fees": round(fees, 4), "pnl": round(gross - fees, 4),
           "R": round(R, 3), "mfe_R": round(st["mfe"], 2), "mae_R": round(st["mae"], 2), "reason": reason}
    store._cmd("HSET", TRD + market, rec["id"], _dump(rec))
    try:
        from . import learn as LN
        LN.on_close_g(rec)
    except Exception:
        pass
    return rec


# ------------------------------------------------------------------ the tick
def _bars_for(spec: dict, symbol: str, need_days: int = 14):
    tf = spec["timeframe"]
    days = max(need_days, MK.history_days(spec["market"], tf))
    return MK.completed(MK.candles(spec["market"], symbol, tf, days), tf, market=spec["market"])


def _sq_min(spec):
    sq = CU.SESSION[spec["market"]]["square_off"] if spec["hold"] == "intraday" else None
    return (sq[0] * 60 + sq[1]) if sq else None


def _manage(market: str, pos: dict, spec: dict | None, out: list):
    """Process every completed bar since the last look, then the latest price."""
    sym = pos["symbol"]
    ex = (spec or {}).get("exit") or {"stop_atr": 1.5, "target_r": None, "trail_atr": None, "breakeven_r": None, "time_stop_bars": None, "exit_on_opposite": False}
    mins = CU.TIMEFRAMES[pos["tf"]]
    st = pos["state"]
    side = 1 if pos["side"] == "long" else -1
    sq = _sq_min({"market": market, "hold": pos["hold"]})
    try:
        bars = _bars_for({"market": market, "symbol": sym, "timeframe": pos["tf"], "hold": pos["hold"], **(spec or {"exit": ex})}, sym)
    except Exception as e:
        out.append({"symbol": sym, "error": str(e)[:100]})
        return
    opened = datetime.fromisoformat(pos["opened"])
    last_ts = datetime.fromisoformat(pos.get("last_bar_ts") or pos["opened"])
    new = bars[(bars.index.tz_convert(timezone.utc) > last_ts) & (bars.index.tz_convert(timezone.utc) >= opened - timedelta(minutes=0))]
    lg = sh = None
    if ex.get("exit_on_opposite") and spec:
        lg, sh, _ = CU.signals(spec, bars)
    entry_day = _local(market, opened).date()
    done = None
    for ts, b in new.iterrows():
        if pos["hold"] == "intraday" and ts.date() != entry_day:
            done = (float(bars["close"].loc[:ts].iloc[-2]) if len(bars.loc[:ts]) > 1 else float(b["open"]), "End of session")
            break
        opp = False
        if lg is not None:
            opp = bool(sh.loc[ts]) if side > 0 else bool(lg.loc[ts])
        res = CU.step_pos(st, float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]), ts.hour * 60 + ts.minute + mins, ex, sq, opp)
        pos["last_bar_ts"] = (ts.tz_convert(timezone.utc)).isoformat()
        if res:
            done = res
            break
    if not done:
        try:                                   # the bar still forming: only an intrabar stop / target touch matters
            px = MK.last_price(market, sym)
            if side * (px - st["sl"]) <= 0:
                done = (px if side * (px - st["sl"]) < 0 else st["sl"], "Stop-loss (live price)")
            elif st["tp"] is not None and side * (px - st["tp"]) >= 0:
                done = (st["tp"], "Target (live price)")
        except Exception:
            pass
    if done:
        rec = close_position(market, pos["id"], done[0], done[1])
        if rec:
            out.append({"closed": rec["symbol"], "strategy": rec["strategy_name"], "pnl": rec["pnl"], "R": rec["R"], "reason": rec["reason"]})
    else:
        pos["state"] = st
        _save_pos(pos)


def _evaluate(market: str, spec: dict, symbol: str, out: list):
    bars = _bars_for(spec, symbol)
    if len(bars) < 80:
        return
    mins = CU.TIMEFRAMES[spec["timeframe"]]
    lg, sh, _ = CU.signals(spec, bars)
    ts = bars.index[-1]
    end = MK.bar_ends(bars.index[-1:], spec["timeframe"], market)[0].astimezone(timezone.utc)
    key = f"g_seen:{spec['id']}:{symbol}"
    seen = store.get(key)
    if seen == end.isoformat():
        return
    store.put(key, end.isoformat())
    side = 1 if lg.iloc[-1] else -1 if sh.iloc[-1] else 0
    if side == 0:
        return
    age = (_now() - end).total_seconds() / 60
    if age > max(10, min(mins, 20)):
        out.append({"skipped": f"{spec['name']} {symbol}: signal on the {_local(market, end):%H:%M} bar was {age:.0f} min old"})
        return
    if any(p["symbol"] == symbol and p["strategy_id"] == spec["id"] for p in positions(market)):
        return
    pf = prefs(market)
    opened = positions(market)
    if len(opened) >= pf["max_open"]:
        out.append({"skipped": f"{spec['name']} {symbol}: {pf['max_open']} positions already open"})
        return
    today = _local(market).date()
    n_today = sum(1 for p in opened + trades(market) if p["strategy_id"] == spec["id"] and _local(market, datetime.fromisoformat(p["opened"])).date() == today)
    if n_today >= spec["filters"]["max_trades_day"]:
        return
    px = MK.last_price(market, symbol)
    a = float(X.atr(bars, spec["exit"]["atr_n"]).iloc[-1])
    if abs(px - float(bars["close"].iloc[-1])) > 0.5 * a:
        out.append({"skipped": f"{spec['name']} {symbol}: price already ran more than half an ATR from the signal"})
        return
    ok, msg = open_position(market, spec, symbol, side, px, a, bars, end)
    out.append({"opened" if ok else "skipped": msg})


def tick(markets=("CRYPTO", "US"), manage_only=False) -> dict:
    """Run once. Safe to call from several places; every step is idempotent per bar."""
    if not _TICK_LOCK.acquire(timeout=50):
        return {"skipped": "another tick is still running"}
    try:
        return _tick(markets, manage_only)
    finally:
        _TICK_LOCK.release()


def _tick(markets, manage_only) -> dict:
    out: dict = {}
    try:
        store.put("g_last_tick", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    except Exception:
        pass
    specs = _spec_map()
    for market in markets:
        rows: list = []
        if market == "US" and not MK.us_session_open() and not positions("US"):
            out[market] = [{"skipped": "US market closed"}]
            continue
        for pos in positions(market):
            try:
                _manage(market, pos, specs.get(pos["strategy_id"]), rows)
            except Exception as e:
                rows.append({"error": f"manage {pos['symbol']}: {str(e)[:100]}"})
        if not manage_only and prefs(market)["auto"] and (market == "CRYPTO" or MK.us_session_open()):
            for spec in runners(market, specs)[0]:
                for sym in spec["symbols"]:
                    if not MK.valid_symbol(market, sym):
                        continue
                    try:
                        _evaluate(market, spec, sym, rows)
                    except Exception as e:
                        rows.append({"error": f"{spec['name']} {sym}: {str(e)[:100]}"})
        out[market] = rows
    try:
        out["research"] = _after_close()
    except Exception as e:
        out["research"] = {"error": str(e)[:100]}
    return out


def _after_close():
    from . import learn as LN
    done = {}
    now = _now()
    if now.hour == 0 and now.minute < 40:                                      # just after the UTC day rolled over
        done["CRYPTO"] = LN.post_close_g("CRYPTO", (now - timedelta(days=1)).date().isoformat())
    loc = _local("US", now)
    if loc.weekday() < 5 and (loc.hour, loc.minute) >= (16, 20) and loc.hour < 19:
        done["US"] = LN.post_close_g("US", loc.date().isoformat())
    try:
        from . import datahub as DH
        saved = DH.daily_us()
        if saved:
            done["US_data_saved"] = saved
    except Exception as e:
        done["US_data_saved"] = {"error": str(e)[:100]}
    return done


# ------------------------------------------------------------------ read side
def portfolio(market: str) -> dict:
    acct, pos, tr = account(market), positions(market), trades(market)
    out, unreal = [], 0.0
    for p in pos:
        try:
            px = MK.last_price(market, p["symbol"])
        except Exception:
            px = p["entry"]
        side = 1 if p["side"] == "long" else -1
        u = side * (px - p["entry"]) * p["qty"]
        unreal += u
        out.append({**{k: v for k, v in p.items() if k != "state"}, "price": px, "unrealised": round(u, 2), "stop": p["state"]["sl"], "target": p["state"]["tp"],
                    "R_now": round(side * (px - p["entry"]) / p["state"]["risk"], 2)})
    realised = sum(t["pnl"] for t in tr)
    pnl = np.array([t["pnl"] for t in tr], dtype=float)
    stats = {}
    if len(pnl):
        w, lo = pnl[pnl > 0], pnl[pnl <= 0]
        eq = acct["capital_start"] + np.cumsum(pnl)
        peak = np.maximum.accumulate(np.concatenate([[acct["capital_start"]], eq]))[1:]
        stats = {"trades": int(len(pnl)), "win_rate": round(float(len(w) / len(pnl) * 100), 1), "net": round(float(pnl.sum()), 2),
                 "profit_factor": round(float(w.sum() / -lo.sum()), 2) if lo.sum() < 0 else None, "max_dd_pct": round(float(((eq - peak) / peak).min() * 100), 1),
                 "avg_R": round(float(np.mean([t.get("R") or 0 for t in tr])), 3),
                 "equity": [{"t": t["closed"], "v": round(float(v), 2)} for t, v in zip(tr, eq)][-300:]}
    cap = acct["capital_start"]
    return {"market": market, "currency": MK.CURRENCY[market], "capital_start": cap, "realised": round(realised, 2), "unrealised": round(unreal, 2),
            "equity": round(cap + realised + unreal, 2), "cash": round(cap + realised - sum(p["margin"] for p in pos), 2),
            "positions": out, "history": list(reversed([{k: v for k, v in t.items() if k not in ("state",)} for t in tr]))[:150], "stats": stats, "prefs": prefs(market),
            "max_leverage": MAX_LEVERAGE[market], "autopilot": _ap_status(market), "last_tick": store.get("g_last_tick")}


def _ap_status(market: str) -> dict:
    from . import gautopilot as AP
    try:
        return AP.status(market, runners(market)[1] == "autopilot")
    except Exception as e:
        return {"error": str(e)[:120]}


def manual_exit(market: str, pid: str) -> tuple[bool, str]:
    pos = next((p for p in positions(market) if p["id"] == pid), None)
    if not pos:
        return False, "Position not found (already closed?)."
    rec = close_position(market, pid, MK.last_price(market, pos["symbol"]), "Manual exit")
    return (True, f"Closed: {rec['pnl']:+,.2f} {MK.CURRENCY[market]}") if rec else (False, "Already closed.")
