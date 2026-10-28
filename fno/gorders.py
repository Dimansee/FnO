"""Order desk for the crypto / US paper books (Delta-style terminal).

Orders you place yourself:
  market  fills now at the live price (+ slippage)
  limit   buy below / sell above the price; fills when a 1-minute candle trades through it (at the limit, or better if
          the candle opens beyond it). A limit already on the wrong side of the price fills at once, like an exchange.
  stop    buy above / sell below the price (breakout / stop entry); fills at the trigger (+ slippage) when touched
Each order can carry a stop-loss, a take-profit and a trailing stop, and a leverage (isolated margin). Sizing: quantity,
amount (position value), margin (money you put up; value = margin x leverage), or % of free balance as margin.
Liquidation (isolated margin, 0.5% maintenance): long liq = entry x (1 - 1/lev + 0.5%), short = entry x (1 + 1/lev - 0.5%).
With no stop-loss the position is closed at its liquidation price, losing its margin; a stop beyond liquidation is
replaced by the liquidation price. Paper money only: nothing here talks to an exchange.
"""
from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timedelta, timezone

from . import gbook as GB
from . import markets as MK
from . import store

ORD = "fno:g:orders:"
HIST_KEEP = 300
MM = 0.005
MAX_LEV = {"CRYPTO": 25.0, "US": 2.0, "GOLD": 20.0}
TYPES = ("market", "limit", "stop")


def _now():
    return GB._now()


def _num(b, k):
    v = b.get(k)
    return None if v in (None, "") else float(v)


def liq_price(side: int, entry: float, lev: float) -> float | None:
    if lev <= 1 and side > 0:
        return None                                   # an unleveraged long cannot be liquidated
    return entry * (1 - side * (1 / lev - MM))


# ------------------------------------------------------------------ FX (display only)
def fx() -> dict:
    """USDT -> INR for showing values in rupees. USD/INR from Yahoo; USDT is treated as 1 USD."""
    def load():
        try:
            import yfinance as yf
            s = yf.Ticker("INR=X").history(period="5d", interval="1d")["Close"].dropna()
            if len(s):
                return {"USDINR": round(float(s.iloc[-1]), 4), "src": "Yahoo Finance USD/INR", "t": _now().isoformat(timespec="minutes")}
        except Exception:
            pass
        return {"USDINR": 88.0, "src": "fallback estimate (live rate unavailable)", "t": _now().isoformat(timespec="minutes")}
    from . import market as M
    return M.cached("g_fx_usdinr", 900, load)


# ------------------------------------------------------------------ sizing / checks shared by preview, placing and fills
def _ticket(market: str, b: dict) -> dict:
    sym = str(b.get("symbol") or "")
    if not MK.valid_symbol(market, sym):
        raise ValueError(f"{sym or 'Symbol'} is not in the {market} list.")
    side = {"long": 1, "buy": 1, "short": -1, "sell": -1}.get(str(b.get("side", "")).lower())
    if not side:
        raise ValueError("Choose Buy / Long or Sell / Short.")
    typ = str(b.get("type") or "market").lower()
    if typ not in TYPES:
        raise ValueError("Order type must be market, limit or stop.")
    pf = GB.prefs(market)
    lev = _num(b, "leverage") or pf["max_leverage"]
    if not 1 <= lev <= MAX_LEV[market]:
        raise ValueError(f"Leverage must be between 1x and {MAX_LEV[market]:g}x for {market}.")
    px = float(MK.last_price(market, sym))
    trig = _num(b, "price")
    if typ != "market":
        if not trig or trig <= 0:
            raise ValueError(f"Set the {typ} price.")
        if typ == "stop" and side * (trig - px) <= 0:
            raise ValueError(f"A stop {'buy' if side > 0 else 'sell'} triggers {'above' if side > 0 else 'below'} the price ({px:,.6g}); use a limit order to {'buy lower' if side > 0 else 'sell higher'}.")
    marketable = typ == "market" or (typ == "limit" and side * (trig - px) >= 0)
    slip, fee = GB._slip_fee(market)
    if marketable:
        entry = px * (1 + side * slip / 1e4)
    elif typ == "limit":
        entry = trig
    else:
        entry = trig * (1 + side * slip / 1e4)
    stop, target, trail = _num(b, "stop"), _num(b, "target"), _num(b, "trail_pct")
    if stop is not None and side * (entry - stop) <= 0:
        raise ValueError(f"Stop-loss must be {'below' if side > 0 else 'above'} the entry ({entry:,.6g}).")
    if target is not None and side * (target - entry) <= 0:
        raise ValueError(f"Take-profit must be {'above' if side > 0 else 'below'} the entry ({entry:,.6g}).")
    if trail is not None and not 0.1 <= trail <= 50:
        raise ValueError("Trailing stop must be between 0.1% and 50%.")
    eq, free = GB.equity(market), GB.cash(market)
    mode = b.get("size_mode") or "qty"
    if mode == "qty":
        qty = _num(b, "qty") or 0
    elif mode == "amount":
        qty = (_num(b, "amount") or 0) / entry
    elif mode == "margin":
        qty = (_num(b, "margin") or 0) * lev / entry
    elif mode == "pct":
        p = _num(b, "pct") or 0
        if not 0 < p <= 100:
            raise ValueError("Use 1-100% of the free balance.")
        qty = free * p / 100 * lev / entry * 0.998         # leave room for fees
    elif mode == "risk":
        rp = _num(b, "risk_pct") or pf["risk_pct"]
        if stop is None:
            raise ValueError("Sizing by risk needs a stop-loss.")
        qty = eq * rp / 100 / abs(entry - stop)
    else:
        raise ValueError("Size by quantity, amount, margin, % of balance or risk.")
    if market == "US":
        qty = float(int(qty))
    qty = round(qty, 6)
    if qty <= 0:
        raise ValueError("Size rounds to zero: enter a larger size.")
    notional = qty * entry
    margin = notional / lev
    fees = notional * fee / 1e4
    liq = liq_price(side, entry, lev)
    eff_stop, warn = stop, []
    if liq is not None and (stop is None or side * (stop - liq) < 0):
        if stop is not None:
            warn.append(f"Liquidation ({liq:,.6g}) comes before your stop-loss at {lev:g}x: lower the leverage or move the stop closer.")
        eff_stop = liq
    if lev > 10:
        warn.append(f"{lev:g}x leverage: a {100 / lev:.1f}% move against you wipes the margin.")
    risk_unit = abs(entry - eff_stop) if eff_stop is not None else entry          # unleveraged long, no stop: whole value at risk
    loss = qty * risk_unit + fees * 2
    out = {"symbol": sym, "side": "long" if side > 0 else "short", "type": typ, "price": round(px, 8), "trigger": trig, "fills_now": marketable,
           "entry": round(entry, 8), "qty": qty, "notional": round(notional, 2), "leverage": lev, "margin": round(margin, 2), "fees_est": round(fees * 2, 2),
           "free": round(free, 2), "equity": round(eq, 2), "liq": round(liq, 8) if liq else None, "stop": stop, "target": target, "trail_pct": trail,
           "loss_at_stop": round(loss, 2), "loss_pct": round(loss / eq * 100, 2) if eq else None, "stop_is_liq": stop is None or eff_stop != stop,
           "gain_at_target": round(qty * abs(target - entry) - fees * 2, 2) if target is not None else None,
           "rr": round(abs(target - entry) / risk_unit, 2) if target is not None and risk_unit else None,
           "currency": MK.CURRENCY[market], "warnings": warn, "note": str(b.get("note") or "")[:200], "_side": side, "_eff_stop": eff_stop}
    if margin + fees > free:
        out["error"] = f"Not enough free balance: needs {margin + fees:,.2f} (margin + fee), {free:,.2f} available."
    if market == "US" and not MK.us_session_open(pad_before=0, pad_after=0):
        out["error"] = "US market is closed (opens 9:30 am New York = 7:00 pm IST). Limit and stop orders can still be placed and wait for the open."
    if market == "GOLD" and sym not in MK.BINANCE_GOLD and not MK.gold_open(pad_after=0):
        out["error"] = "Gold is closed (daily break 17:00-18:00 New York, or the weekend). PAXG trades 24/7; limit and stop orders can still be placed."
    return out


def _public(t: dict) -> dict:
    return {k: v for k, v in t.items() if not k.startswith("_")}


def preview(market: str, b: dict) -> dict:
    return _public(_ticket(market, b))


def _open(market: str, t: dict, fill_px: float | None = None, why: str = "") -> dict:
    """Create the position from a checked ticket (fill_px overrides the entry for a triggered order)."""
    side = t["_side"]
    entry = fill_px if fill_px is not None else t["entry"]
    lev = t["leverage"]
    qty = t["qty"]
    _, fee = GB._slip_fee(market)
    liq = liq_price(side, entry, lev)
    stop = t["stop"]
    if stop is not None and side * (entry - stop) <= 0:
        raise ValueError(f"Filled at {entry:,.6g}, already beyond the stop-loss {stop:,.6g}.")
    eff = stop
    if liq is not None and (stop is None or side * (stop - liq) < 0):
        eff = liq
    if eff is None:                                     # 1x long without a stop: never liquidated; a far floor keeps R defined
        eff = entry * 0.01
    risk = abs(entry - eff)
    margin = qty * entry / lev
    fees = qty * entry * fee / 1e4
    if margin + fees > GB.cash(market) + 1e-9:
        raise ValueError(f"Not enough free balance at fill: needs {margin + fees:,.2f}, {GB.cash(market):,.2f} available.")
    target = t["target"]
    if target is not None and side * (target - entry) <= 0:
        target = None
    st = {"side": side, "entry": float(entry), "risk": float(risk), "sl": float(eff), "sl0": float(eff), "tp": target,
          "trail": float(entry * t["trail_pct"] / 100) if t.get("trail_pct") else None, "be": None, "after": None, "best": float(entry), "held": 0,
          "pend_opp": False, "mfe": 0.0, "mae": 0.0, "first": True, "atr": float(risk)}
    now = _now()
    pos = {"id": secrets.token_hex(4), "market": market, "symbol": t["symbol"], "side": t["side"], "qty": qty, "entry": round(entry, 8), "margin": round(margin, 2),
           "leverage": lev, "fees_in": round(fees, 4), "strategy_id": GB.MANUAL_ID, "strategy_name": "Manual", "spec_version": 1, "state": st,
           "opened": now.isoformat(timespec="seconds"), "from_bar_ts": now.isoformat(timespec="seconds"), "ctx": {"manual": True, "note": t.get("note", ""), "order": why or t["type"]},
           "size_mult": 1.0, "tf": "5m", "hold": "swing", "exit": GB.MANUAL_EXIT, "manual": True,
           "liq": round(liq, 8) if liq else None, "stop_is_liq": stop is None or eff != stop, "user_stop": stop}
    GB._save_pos(pos)
    return pos


def place(market: str, b: dict) -> dict:
    if market in GB.RETIRED:
        raise ValueError("The Gold account was merged into Crypto - trade gold there as PAXG (Gold).")
    t = _ticket(market, b)
    if t.get("error") and (t["fills_now"] or not t["error"].startswith("US market is closed")):
        raise ValueError(t["error"])
    if t["fills_now"]:
        pos = _open(market, t)
        _log(market, {**_order_rec(t), "status": "filled", "fill_price": pos["entry"], "filled": pos["opened"], "pos_id": pos["id"]})
        return {"ok": True, "filled": True, "position": pos["id"], "message": _msg(t, pos["entry"], "Filled")}
    o = {**_order_rec(t), "status": "open", "last_check": _now().isoformat(timespec="seconds")}
    store.hset(ORD + market, o["id"], json.dumps(o, default=str))
    return {"ok": True, "filled": False, "order": o["id"], "message": _msg(t, t["trigger"], "Order placed")}


def _order_rec(t: dict) -> dict:
    return {"id": secrets.token_hex(4), "created": _now().isoformat(timespec="seconds"), **_public(t)}


def _msg(t, px, head):
    return (f"{head}: {'buy' if t['side'] == 'long' else 'sell'} {t['symbol']} x {t['qty']:g} {t['type']} at {px:,.6g}" +
            (f" · SL {t['stop']:,.6g}" if t["stop"] else (f" · liquidation {t['liq']:,.6g}" if t["liq"] else "")) + (f" · TP {t['target']:,.6g}" if t["target"] else ""))


def open_orders(market: str) -> list[dict]:
    raw = store.hvals(ORD + market)
    return sorted((json.loads(x) for x in raw), key=lambda o: o["created"], reverse=True)


def history(market: str) -> list[dict]:
    return (store.get(f"g_ordhist:{market}") or [])[:HIST_KEEP]


def _log(market: str, o: dict):
    h = store.get(f"g_ordhist:{market}") or []
    h.insert(0, o)
    store.put(f"g_ordhist:{market}", h[:HIST_KEEP])


def cancel(market: str, oid: str, why: str = "Cancelled by you") -> dict:
    v = store.htake(ORD + market, oid)
    if not v:
        raise ValueError("Order not found (already filled or cancelled?).")
    o = json.loads(v)
    _log(market, {**o, "status": "cancelled", "reason": why, "ended": _now().isoformat(timespec="seconds")})
    return {"ok": True, "message": f"Cancelled {o['type']} {o['side']} {o['symbol']}"}


def process(market: str) -> list[dict]:
    """Fill open limit / stop orders from the 1-minute candles since they were last checked."""
    out = []
    for o in open_orders(market):
        try:
            fast = market == "CRYPTO" or o["symbol"] in MK.BINANCE_GOLD
            df = MK.candles(market, o["symbol"], "1m" if fast else "5m", 1, ttl=20)
        except Exception as e:
            out.append({"order": o["id"], "error": str(e)[:80]})
            continue
        since = datetime.fromisoformat(o.get("last_check") or o["created"])
        mins = 1 if fast else 5
        bars = df[df.index.tz_convert(timezone.utc) + timedelta(minutes=mins) > since]
        side, trig, fill = (1 if o["side"] == "long" else -1), float(o["trigger"]), None
        slip, _ = GB._slip_fee(market)
        for ts, r in bars.iterrows():
            hi, lo, op = float(r["high"]), float(r["low"]), float(r["open"])
            if o["type"] == "limit" and ((side > 0 and lo <= trig) or (side < 0 and hi >= trig)):
                fill = min(op, trig) if side > 0 else max(op, trig)
                break
            if o["type"] == "stop" and ((side > 0 and hi >= trig) or (side < 0 and lo <= trig)):
                fill = (max(op, trig) if side > 0 else min(op, trig)) * (1 + side * slip / 1e4)
                break
        if fill is None:
            o["last_check"] = (bars.index[-1].tz_convert(timezone.utc) if len(bars) else since).isoformat()
            store.hset(ORD + market, o["id"], json.dumps(o, default=str))
            continue
        v = store.htake(ORD + market, o["id"])       # take it first so two ticks never fill it twice
        if not v:
            continue
        t = {**o, "_side": side}
        try:
            pos = _open(market, t, fill, why=o["type"])
            _log(market, {**o, "status": "filled", "fill_price": round(fill, 8), "filled": pos["opened"], "pos_id": pos["id"]})
            out.append({"filled": o["id"], "symbol": o["symbol"], "price": fill})
        except ValueError as e:
            _log(market, {**o, "status": "rejected", "reason": str(e), "ended": _now().isoformat(timespec="seconds")})
            out.append({"rejected": o["id"], "reason": str(e)})
    return out


def close_part(market: str, pid: str, frac: float) -> dict:
    """Close part (or all) of a position at the live price."""
    if not 0 < frac <= 1:
        raise ValueError("Close between 1% and 100%.")
    pos = next((p for p in GB.positions(market) if p["id"] == pid), None)
    if not pos:
        raise ValueError("Position not found (already closed?).")
    px = float(MK.last_price(market, pos["symbol"]))
    if frac >= 0.999 or (market == "US" and int(pos["qty"] * frac) == pos["qty"]):
        rec = GB.close_position(market, pid, px, "Closed by you")
        return {"ok": True, "message": f"Closed {pos['symbol']}: {rec['pnl']:+,.2f}"}
    q = float(int(pos["qty"] * frac)) if market == "US" else round(pos["qty"] * frac, 6)
    if q <= 0:
        raise ValueError("That part rounds to zero.")
    part = {**pos, "id": secrets.token_hex(4), "qty": q, "margin": round(pos["margin"] * q / pos["qty"], 2), "fees_in": round(pos["fees_in"] * q / pos["qty"], 4)}
    pos["qty"] = round(pos["qty"] - q, 6)
    pos["margin"] = round(pos["margin"] - part["margin"], 2)
    pos["fees_in"] = round(pos["fees_in"] - part["fees_in"], 4)
    GB._save_pos(pos)
    GB._save_pos(part)
    rec = GB.close_position(market, part["id"], px, f"Partly closed by you ({frac * 100:.0f}%)")
    return {"ok": True, "message": f"Closed {frac * 100:.0f}% of {pos['symbol']}: {rec['pnl']:+,.2f}"}


def desk(market: str) -> dict:
    """Everything the terminal's bottom panel needs in one call."""
    pos = []
    for p in GB.positions(market):
        try:
            px = float(MK.last_price(market, p["symbol"]))
        except Exception:
            px = p["entry"]
        side = 1 if p["side"] == "long" else -1
        u = side * (px - p["entry"]) * p["qty"]
        pos.append({**{k: v for k, v in p.items() if k != "state"}, "mark": px, "unrealised": round(u, 4), "roe": round(u / p["margin"] * 100, 2) if p["margin"] else None,
                    "stop": p["state"]["sl"], "target": p["state"]["tp"], "trail": p["state"].get("trail"), "R_now": round(side * (px - p["entry"]) / p["state"]["risk"], 2)})
    tr = sorted(GB.trades(market), key=lambda t: t["closed"], reverse=True)[:200]
    eq, free = GB.equity(market), GB.cash(market)
    upnl = sum(p["unrealised"] for p in pos)
    return {"market": market, "currency": MK.CURRENCY[market], "equity": round(eq, 2), "free": round(free, 2), "upnl": round(upnl, 2),
            "used_margin": round(sum(p["margin"] for p in pos), 2), "positions": pos, "orders": open_orders(market), "order_history": history(market)[:100],
            "trades": [{k: t.get(k) for k in ("id", "symbol", "side", "strategy_name", "opened", "closed", "entry", "exit", "qty", "pnl", "fees", "R", "reason", "leverage")} for t in tr],
            "fx": fx(), "max_leverage": MAX_LEV[market], "t": time.time()}
