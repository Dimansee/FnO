"""Paper books for crypto (USDT), US stocks / ETFs (USD) and gold (USD per ounce).

Kept completely separate from the Indian options ledger: its own money, positions and trades, so nothing you do
here can touch the F&O results. Trades come from your custom strategies (market = CRYPTO, US or GOLD) and follow exactly
the same bar-by-bar exit logic as the backtester (custom.step_pos), so what you saw in the backtest is what runs.

A tick (every 5 minutes from GitHub Actions, and whenever the app is open) does, per market:
  1. manage open positions: every bar completed since the last look, then the latest price
  2. look for new signals on the last completed bar of each enabled strategy / symbol
Sizing: risk_pct of the market's equity per trade (default 1%), position value capped by max_leverage.
Costs: the same slippage and fee assumptions the backtester uses (custom.COSTS_BPS).
Crypto shorts are perpetual-style (no funding modelled). US shorts are allowed on paper with full margin.
Gold is sized in ounces (fractions allowed, like a spot / CFD account) and opens new trades only while gold is open.
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

DEFAULT_CAPITAL = {"CRYPTO": 10_000.0, "US": 10_000.0, "GOLD": 10_000.0}
MARKETS = ("CRYPTO", "US", "GOLD")
PREF_DEFAULTS = {"risk_pct": 1.0, "max_leverage": 1.0, "max_open": 4, "auto": True}
MAX_LEVERAGE = {"CRYPTO": 5.0, "US": 2.0, "GOLD": 10.0}   # gold: MCX / spot margins are ~5-10%
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
    store._cmd("DEL", "fno:g:orders:" + market)
    store.put(f"g_ordhist:{market}", [])
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
    out = {s["id"]: s for m in MARKETS for s in AP.all_specs(m)}   # so autopilot positions keep their exit rules
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
    lev = min(pf["max_leverage"], MAX_LEVERAGE[market])
    max_notional = eq * lev / max(pf["max_open"], 1) * 1.5
    if qty * entry > max_notional:
        qty = max_notional / entry
    if market == "US":
        qty = float(int(qty))                      # whole shares
    if qty <= 0:
        return False, "Position size rounds to zero for this account size."
    margin = qty * entry / (1 if (market == "US" and side < 0) else lev)
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


MANUAL_ID = "manual"
MANUAL_EXIT = {"stop_atr": 1.0, "atr_n": 14, "target_r": None, "trail_atr": None, "breakeven_r": None, "after_target_trail": None,
               "time_stop_bars": None, "exit_on_opposite": False}


def manual_preview(market: str, b: dict) -> dict:
    from . import gorders as GO
    return GO.preview(market, {"type": "market", "size_mode": "risk", **b})


def manual_open(market: str, b: dict) -> dict:
    from . import gorders as GO
    r = GO.place(market, {"type": "market", "size_mode": "risk", **b})
    return {**r, "id": r.get("position")}


def modify_position(market: str, pid: str, b: dict) -> dict:
    """Move the stop / target of an open position (any position, yours or a rule's). The stop may only be moved, never removed."""
    pos = next((p for p in positions(market) if p["id"] == pid), None)
    if not pos:
        raise ValueError("Position not found (already closed?).")
    st, side = pos["state"], (1 if pos["side"] == "long" else -1)
    px = float(MK.last_price(market, pos["symbol"]))
    if b.get("stop") not in (None, ""):
        sl = float(b["stop"])
        if side * (px - sl) <= 0:
            raise ValueError(f"The stop must be {'below' if side > 0 else 'above'} the current price ({px:,.6g}).")
        if pos.get("liq") and side * (sl - pos["liq"]) < 0:
            raise ValueError(f"That stop is beyond the liquidation price ({pos['liq']:,.6g}); the position would be liquidated first.")
        st["sl"] = sl
        pos["stop_is_liq"], pos["user_stop"] = False, sl
    if "target" in b:
        tp = None if b["target"] in (None, "") else float(b["target"])
        if tp is not None and side * (tp - px) <= 0:
            raise ValueError(f"The target must be {'above' if side > 0 else 'below'} the current price ({px:,.6g}).")
        st["tp"] = tp
    pos["state"] = st
    pos.setdefault("edits", []).append({"t": _now().isoformat(timespec="seconds"), "stop": st["sl"], "target": st["tp"]})
    _save_pos(pos)
    return {"ok": True, "message": f"{pos['symbol']}: stop {st['sl']:,.6g}" + (f", target {st['tp']:,.6g}" if st["tp"] is not None else ", no target")}


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
    if pos.get("stop_is_liq") and reason.startswith("Stop") and abs(st["sl"] - st["sl0"]) < 1e-12:
        reason = "Liquidated (no stop-loss)" if pos.get("user_stop") is None else "Liquidated (before your stop-loss)"
    R = (side * (exit_fill - pos["entry"]) - pos["fees_in"] / pos["qty"] - pos["qty"] * exit_fill * fee / 1e4 / pos["qty"]) / st["risk"]
    rec = {**pos, "closed": _now().isoformat(timespec="seconds"), "exit": round(exit_fill, 6), "gross_pnl": round(gross, 4), "fees": round(fees, 4), "pnl": round(gross - fees, 4),
           "R": round(R, 3), "mfe_R": round(st["mfe"], 2), "mae_R": round(st["mae"], 2), "reason": reason}
    store._cmd("HSET", TRD + market, rec["id"], _dump(rec))
    if not rec.get("manual"):                  # the learning engine studies the rules, not your own manual trades
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
    ex = (spec or {}).get("exit") or pos.get("exit") or {"stop_atr": 1.5, "target_r": None, "trail_atr": None, "breakeven_r": None, "time_stop_bars": None, "exit_on_opposite": False}
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


CATCHUP_MIN = 240   # slow rules (4h / daily bars) may still enter up to 4 hours after the signal bar closed


def _max_age(mins: int) -> float:
    """How old a signal may be and still be taken. Fast bars: 10-20 min. 4h and daily bars: up to 4 hours, so a signal is
    not lost when the host was asleep or a tick was missed (research 17 Oct 2026: on the live crypto trend rules, entering
    one 4h bar late kept the edge, +0.08R vs +0.085R; 2+ bars late it faded). The half-ATR price guard below still applies."""
    return CATCHUP_MIN if mins >= 240 else max(10, min(mins, 20))


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
    if age > _max_age(mins):
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


def tick(markets=MARKETS, manage_only=False) -> dict:
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
        try:
            from . import gorders as GO
            filled = GO.process(market)
        except Exception as e:
            filled = [{"error": f"orders: {str(e)[:80]}"}]
        if market in ("US", "GOLD") and not MK.session_open(market, _now()) and not positions(market):
            out[market] = filled + [{"skipped": f"{'US market' if market == 'US' else 'Gold'} closed"}]
            continue
        for pos in positions(market):
            try:
                _manage(market, pos, specs.get(pos["strategy_id"]), rows)
            except Exception as e:
                rows.append({"error": f"manage {pos['symbol']}: {str(e)[:100]}"})
        if not manage_only and prefs(market)["auto"] and MK.session_open(market, _now()):
            for spec in runners(market, specs)[0]:
                for sym in spec["symbols"]:
                    if not MK.valid_symbol(market, sym):
                        continue
                    try:
                        _evaluate(market, spec, sym, rows)
                    except Exception as e:
                        rows.append({"error": f"{spec['name']} {sym}: {str(e)[:100]}"})
        out[market] = filled + rows
    if "CRYPTO" in markets:
        try:                                   # forward-test lab: own ledger, never touches the books' cash or slots
            from . import glab
            out["lab"] = glab.tick()
        except Exception as e:
            out["lab"] = [{"error": str(e)[:100]}]
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
    if loc.weekday() < 5 and (loc.hour, loc.minute) >= (17, 5) and (loc.hour, loc.minute) < (17, 50):   # gold's daily break
        done["GOLD"] = LN.post_close_g("GOLD", loc.date().isoformat())
    try:
        from . import datahub as DH
        saved = DH.daily_us()
        if saved:
            done["US_data_saved"] = saved
    except Exception as e:
        done["US_data_saved"] = {"error": str(e)[:100]}
    return done


# ------------------------------------------------------------------ scanner (what every coin / rule is waiting for)
WATCH = {"CRYPTO": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"], "US": ["SPY", "QQQ", "AAPL", "NVDA", "MSFT"], "GOLD": ["XAUUSD", "PAXGUSDT"]}


def _px(v: float) -> str:
    return f"{v:,.0f}" if v >= 1000 else f"{v:,.2f}" if v >= 1 else f"{v:.4f}"


def _trigger(spec: dict, bars: pd.DataFrame, cache: dict) -> list[str]:
    """Plain words for the price that would fire the next entry, read from the rule's own first condition."""
    from . import indicators as IND
    close = float(bars["close"].iloc[-1])
    out = []
    for side_name, word in (("long", "buy"), ("short", "sell short")):
        if spec["direction"] not in (side_name, "both"):
            continue
        conds = (spec.get(side_name) or {}).get("all") or []
        if not conds:
            continue
        c = conds[0]
        l, r = c["l"], c["r"]
        if l.get("ind") == "supertrend" and len(conds) == 1:
            tr, up, lo = IND.supertrend_bands(bars, int(l.get("n", 10)), float(l.get("mult", 3.0)))
            if side_name == "long" and tr.iloc[-1] < 0:
                lvl = float(up.iloc[-1])
                out.append(f"{word} if a bar closes above {_px(lvl)} ({(lvl / close - 1) * 100:+.1f}%)")
            elif side_name == "short" and tr.iloc[-1] > 0:
                lvl = float(lo.iloc[-1])
                out.append(f"{word} if a bar closes below {_px(lvl)} ({(lvl / close - 1) * 100:+.1f}%)")
            else:
                out.append(f"trend already {'up' if tr.iloc[-1] > 0 else 'down'} — no new {word} until it flips")
        elif "price" in l and "ind" in r and len(conds) == 1 and c["op"] in ("crosses_above", "crosses_below", "gt", "lt"):
            lvl = float(X.operand_series(bars, r, cache).iloc[-1])
            if np.isfinite(lvl):
                above = c["op"] in ("crosses_above", "gt")
                out.append(f"{word} if a bar closes {'above' if above else 'below'} {_px(lvl)} ({(lvl / close - 1) * 100:+.1f}%)")
                continue
        else:                                                   # your own rules: show where each condition stands now
            try:
                out.append(f"{word} when: " + "; ".join(CU._describe_cond(x, bars, cache) for x in conds))
            except Exception:
                out.append(f"{word} when its conditions line up")
    return out


def scanner(market: str) -> list[dict]:
    """One row per watched symbol and active rule: last signal, open position, and the level that triggers the next entry."""
    def build():
        rows = []
        specs, held = runners(market)[0], positions(market)
        for sym in WATCH[market]:
            for spec in specs:
                if sym not in spec["symbols"]:
                    continue
                try:
                    bars = _bars_for(spec, sym)
                    if len(bars) < 80:
                        continue
                    lg, sh, cache = CU.signals(spec, bars)
                    sig = lg.astype(int) - sh.astype(int)
                    hits = np.flatnonzero(sig.values)
                    last = None
                    if len(hits):
                        k = int(hits[-1])
                        last = {"side": "long" if sig.iloc[k] > 0 else "short", "at": MK.bar_ends(bars.index[k:k + 1], spec["timeframe"], market)[0].isoformat(timespec="minutes"),
                                "bars_ago": int(len(bars) - 1 - k)}
                    pos = next((p for p in held if p["symbol"] == sym and p["strategy_id"] == spec["id"]), None)
                    rows.append({"symbol": sym, "rule": spec["name"].replace("Autopilot · ", ""), "tf": spec["timeframe"], "price": float(bars["close"].iloc[-1]),
                                 "last_signal": last, "in_position": pos["side"] if pos else None, "next": [] if pos else _trigger(spec, bars, cache)})
                except Exception as e:
                    rows.append({"symbol": sym, "rule": spec["name"], "error": str(e)[:80]})
        not_traded = [s for s in WATCH[market] if not any(r["symbol"] == s for r in rows)]
        for s in not_traded:
            rows.append({"symbol": s, "rule": None, "note": "no active rule is working on this symbol lately, so it is only watched"})
        return rows
    from . import market as M
    return M.cached(f"g_scan:{market}:{len(positions(market))}", 120, build)


# ------------------------------------------------------------------ 24h ticker (terminal header)
def ticker(market: str, symbol: str) -> dict:
    if not MK.valid_symbol(market, symbol):
        raise ValueError(f"{symbol} is not in the {market} list")

    def load():
        if market == "CRYPTO" or symbol in MK.BINANCE_GOLD:
            for host in MK.BINANCE[:2]:
                try:
                    r = MK._s.get(f"{host}/api/v3/ticker/24hr", params={"symbol": symbol}, timeout=8)
                    if r.status_code == 200:
                        j = r.json()
                        return {"price": float(j["lastPrice"]), "chg_pct": float(j["priceChangePercent"]), "high": float(j["highPrice"]), "low": float(j["lowPrice"]),
                                "vol_quote": float(j["quoteVolume"])}
                except Exception:
                    continue
        df = MK.candles(market, symbol, "1h", 3, ttl=60)
        last = df.tail(7 if market == "US" else 24)
        px = float(MK.last_price(market, symbol))
        return {"price": px, "chg_pct": round((px / float(last["open"].iloc[0]) - 1) * 100, 2), "high": float(last["high"].max()), "low": float(last["low"].min()),
                "vol_quote": float((last["close"] * last["volume"]).sum())}
    from . import market as M
    return M.cached(f"g_tk:{market}:{symbol}", 10, load)


# ------------------------------------------------------------------ chart
CHART_TF = {"1m": 1, "5m": 3, "15m": 8, "1h": 30, "4h": 120, "1d": 600}     # timeframe -> calendar days of history shown


def chart(market: str, symbol: str, tf: str) -> dict:
    """Candles for the Markets chart, plus what the book did on this symbol: trade arrows, open position lines, and the
    Supertrend / channel lines of the active rules that run on this same timeframe."""
    if tf not in CHART_TF:
        raise ValueError("Unknown timeframe " + tf)
    if not MK.valid_symbol(market, symbol):
        raise ValueError(f"{symbol} is not in the {market} list")
    df = MK.candles(market, symbol, tf, CHART_TF[tf], ttl=20 if tf in ("1m", "5m") else 60)
    df = df.tail(1500)
    t = lambda ix: int(pd.Timestamp(ix).tz_convert("UTC").timestamp())     # noqa: E731
    candles = [{"time": t(i), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close), "volume": float(r.volume)}
               for i, r in zip(df.index, df.itertuples())]
    lines = []
    from . import indicators as IND
    for spec in runners(market)[0]:
        if spec["timeframe"] != tf or symbol not in spec["symbols"]:
            continue
        for side_name in ("long", "short"):
            for c in ((spec.get(side_name) or {}).get("all") or [])[:1]:
                l, r = c["l"], c["r"]
                name = spec["name"].replace("Autopilot · ", "")
                if l.get("ind") == "supertrend" and side_name == "long":
                    tr, up, lo = IND.supertrend_bands(df, int(l.get("n", 10)), float(l.get("mult", 3.0)))
                    pts = [{"time": t(i), "value": float(lo.iloc[k] if tr.iloc[k] > 0 else up.iloc[k]), "color": "#3fbf8f" if tr.iloc[k] > 0 else "#e2675a"}
                           for k, i in enumerate(df.index) if k >= 20]
                    lines.append({"name": f"{name}: Supertrend", "points": pts})
                elif "price" in l and "ind" in r:
                    ser = X.operand_series(df, r, {})
                    pts = [{"time": t(i), "value": float(v)} for i, v in ser.items() if np.isfinite(v)]
                    lines.append({"name": f"{name}: {X.describe(r)}", "points": pts})
    t0 = candles[0]["time"] if candles else 0
    markers = []
    for tr_ in trades(market):
        if tr_["symbol"] != symbol:
            continue
        for key, kind in (("opened", "in"), ("closed", "out")):
            ts = tr_.get(key)
            if ts and t(datetime.fromisoformat(ts)) >= t0:
                markers.append({"time": t(datetime.fromisoformat(ts)), "kind": kind, "side": tr_["side"], "text": (f"{tr_['side']} " if kind == "in" else f"exit {tr_.get('R', 0):+.1f}R")})
    pos = []
    for p in positions(market):
        if p["symbol"] != symbol:
            continue
        markers.append({"time": t(datetime.fromisoformat(p["opened"])), "kind": "in", "side": p["side"], "text": p["side"]})
        pos.append({"side": p["side"], "entry": p["entry"], "stop": p["state"]["sl"], "target": p["state"]["tp"], "strategy": p["strategy_name"]})
    # snap markers onto bar times so they always draw
    times = [c["time"] for c in candles]
    import bisect
    for m in markers:
        k = bisect.bisect_right(times, m["time"]) - 1
        m["time"] = times[max(k, 0)] if times else m["time"]
    markers.sort(key=lambda m: m["time"])
    return {"symbol": symbol, "tf": tf, "candles": candles, "lines": lines, "markers": markers, "positions": pos,
            "symbols": MK.catalog(market), "timeframes": list(CHART_TF)}


# ------------------------------------------------------------------ journal, rulebook, market context
def _grp(rows, key):
    out = {}
    for t in rows:
        out.setdefault(key(t), []).append(t)
    res = []
    for k, ts in out.items():
        pnl = np.array([x["pnl"] for x in ts], float)
        lo = -pnl[pnl <= 0].sum()
        res.append({"key": k, "trades": len(ts), "win_rate": round(float((pnl > 0).mean() * 100), 1), "net": round(float(pnl.sum()), 2),
                    "avg_R": round(float(np.mean([x.get("R") or 0 for x in ts])), 3), "profit_factor": round(float(pnl[pnl > 0].sum() / lo), 2) if lo > 0 else None})
    return res


def journal(market: str) -> dict:
    tr = sorted(trades(market), key=lambda t: t["closed"], reverse=True)
    day = lambda t: _local("IN", datetime.fromisoformat(t["closed"])).date().isoformat()     # noqa: E731  (days in Indian time)
    rows = [{k: t.get(k) for k in ("id", "symbol", "side", "strategy_name", "tf", "opened", "closed", "entry", "exit", "qty", "pnl", "fees", "R", "mfe_R", "mae_R", "reason")} | {"day": day(t)}
            for t in tr]
    return {"market": market, "currency": MK.CURRENCY[market], "trades": rows,
            "by_day": sorted(_grp(tr, day), key=lambda x: x["key"], reverse=True),
            "by_strategy": sorted(_grp(tr, lambda t: t["strategy_name"].replace("Autopilot · ", "")), key=lambda x: -x["trades"]),
            "by_symbol": sorted(_grp(tr, lambda t: t["symbol"]), key=lambda x: -x["trades"]),
            "by_reason": sorted(_grp(tr, lambda t: t.get("reason") or "?"), key=lambda x: -x["trades"]),
            "open": [{k: p.get(k) for k in ("symbol", "side", "strategy_name", "opened", "entry", "qty")} for p in positions(market)]}


def rules(market: str) -> dict:
    from . import gautopilot as AP
    specs = _spec_map()
    own = [{"id": s["id"], "name": s["name"], "timeframe": s["timeframe"], "symbols": s["symbols"], "auto": bool(s.get("auto")), "enabled": bool(s.get("enabled")), "spec": s}
           for s in specs.values() if not s.get("autopilot") and s["market"] == market]
    slip, fee = _slip_fee(market)
    return {"market": market, "built_in": AP.rulebook(market), "own": own, "who_trades": runners(market, specs)[1], "prefs": prefs(market),
            "costs": {"slippage_bps": slip, "fee_bps": fee}, "tested_at": (store.get(f"g_ap_qual:{market}") or {}).get("at"),
            "gate": AP.status(market, True)["rules"], "symbols": MK.catalog(market)}


def context(market: str) -> dict:
    """Market overview: per symbol price, 1/7/30-day change, trend (200-day average, 4h Supertrend), daily volatility."""
    def build():
        from . import indicators as IND
        rows = []
        for item in MK.catalog(market):
            sym = item["id"]
            try:
                d = MK.completed(MK.candles(market, sym, "1d", 420, ttl=900), "1d", market=market)
                h4 = MK.completed(MK.candles(market, sym, "4h", 60, ttl=900), "4h", market=market)
                px = float(MK.last_price(market, sym))
                c = d["close"]
                ch = lambda n: round((px / float(c.iloc[-n]) - 1) * 100, 2) if len(c) > n else None    # noqa: E731
                sma200 = float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else None
                a = float(X.atr(d, 14).iloc[-1])
                st4 = int(IND.supertrend(h4).iloc[-1]) if len(h4) > 30 else None
                vol = d["volume"]
                rows.append({"symbol": sym, "label": item["label"], "price": px, "d1": ch(1), "d7": ch(7), "d30": ch(30),
                             "vs_sma200": round((px / sma200 - 1) * 100, 1) if sma200 else None, "trend4h": st4, "atr_pct": round(a / px * 100, 2),
                             "vol_vs_30d": round(float(vol.iloc[-1] / vol.iloc[-31:-1].mean()), 2) if len(vol) > 31 and vol.iloc[-31:-1].mean() > 0 else None})
            except Exception as e:
                rows.append({"symbol": sym, "label": item["label"], "error": str(e)[:80]})
        good = [r for r in rows if "error" not in r]
        out = {"market": market, "rows": rows, "at": _now().isoformat(timespec="minutes"),
               "breadth": {"above_200d": sum(1 for r in good if (r["vs_sma200"] or 0) > 0), "up_4h": sum(1 for r in good if r["trend4h"] == 1), "of": len(good)}}
        lead = next((r for r in good if r["symbol"] == {"CRYPTO": "BTCUSDT", "US": "SPY", "GOLD": "XAUUSD"}[market]), None)
        if lead and lead["vs_sma200"] is not None:
            out["regime"] = {"symbol": lead["symbol"], "bull": lead["vs_sma200"] > 0, "vs_sma200": lead["vs_sma200"]}
        try:
            if market == "GOLD":
                import yfinance as yf
                dx = yf.Ticker("DX-Y.NYB").history(period="10d", interval="1d")["Close"].dropna()
                if len(dx):
                    chg = (dx.iloc[-1] / dx.iloc[0] - 1) * 100
                    out["sentiment"] = {"name": "US dollar index (gold usually moves the other way)", "value": round(float(dx.iloc[-1]), 2),
                                        "label": "dollar rising" if chg > 0.5 else "dollar falling" if chg < -0.5 else "dollar flat", "week_ago": round(float(dx.iloc[0]), 2)}
            elif market == "CRYPTO":
                r = MK._s.get("https://api.alternative.me/fng/?limit=8", timeout=8).json()["data"]
                out["sentiment"] = {"name": "Crypto Fear & Greed", "value": int(r[0]["value"]), "label": r[0]["value_classification"], "week_ago": int(r[-1]["value"])}
            else:
                import yfinance as yf
                vx = yf.Ticker("^VIX").history(period="10d", interval="1d")["Close"].dropna()
                if len(vx):
                    out["sentiment"] = {"name": "VIX (fear gauge)", "value": round(float(vx.iloc[-1]), 2), "label": "calm" if vx.iloc[-1] < 15 else "normal" if vx.iloc[-1] < 22 else "stressed",
                                        "week_ago": round(float(vx.iloc[0]), 2)}
        except Exception:
            pass
        return out
    from . import market as M
    return M.cached(f"g_ctx:{market}", 600, build)


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
            "max_leverage": MAX_LEVERAGE[market], "autopilot": _ap_status(market), "last_tick": store.get("g_last_tick"), "lab": _lab(market),
            "scanner": _scan_safe(market)}


def _scan_safe(market: str):
    try:
        return scanner(market)
    except Exception as e:
        return [{"error": str(e)[:120]}]


def _lab(market: str):
    if market != "CRYPTO":
        return None
    try:
        from . import glab
        return glab.summary()
    except Exception as e:
        return {"error": str(e)[:120]}


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
