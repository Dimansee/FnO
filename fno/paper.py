"""Paper-trading engine: demo money, positions, journal and stats (in Supabase).
Cash is always derived (start capital - open margin + realised P&L), so there is
no running balance that can drift."""
from __future__ import annotations

import uuid

import numpy as np

from . import config as C
from . import indicators as I
from . import store

SIGN = {"BUY": 1, "SELL": -1}


def account() -> dict:
    a = store.get("account")
    if not a:
        a = {"capital_start": C.DEFAULT_CAPITAL, "created": C.now_ist().isoformat(timespec="seconds")}
        store.put("account", a)
    return a


def snapshot():
    """(account, positions, trades, cash)"""
    a = account()
    pos, tr = store.positions(), store.trades()
    cash = a["capital_start"] - sum(p["margin"] for p in pos) + sum(t["pnl"] for t in tr)
    return a, pos, tr, round(cash, 2)


def reset(capital: float):
    store.clear_positions()
    store.clear_trades()
    store.put("account", {"capital_start": float(capital), "created": C.now_ist().isoformat(timespec="seconds")})


def net_price(legs, prices: dict | None = None) -> float:
    tot = 0.0
    for lg in legs:
        p = prices.get(lg["key"], lg["price"]) if prices is not None else lg["price"]
        tot += SIGN[lg["side"]] * p
    return tot


def margin_needed(legs, qty, spot) -> float:
    debit = max(net_price(legs), 0) * qty
    naked = 0
    for opt in ("CE", "PE"):
        sells = sum(1 for l in legs if l["opt"] == opt and l["side"] == "SELL")
        buys = sum(1 for l in legs if l["opt"] == opt and l["side"] == "BUY")
        naked += max(sells - buys, 0)
    return debit + naked * 0.12 * spot * qty


def open_position(cash, symbol, legs, lots, lot, expiry, spot, source, order=None, plan=None):
    qty = int(lots) * int(lot)
    if qty <= 0:
        return False, "Quantity is zero."
    margin = margin_needed(legs, qty, spot)
    if margin > cash:
        return False, f"Not enough demo cash: need ₹{margin:,.0f}, have ₹{cash:,.0f}."
    entry = net_price(legs)
    pos = {
        "id": uuid.uuid4().hex[:8], "symbol": symbol, "legs": legs, "lots": int(lots), "lot": int(lot), "qty": qty,
        "expiry": str(expiry), "entry_prem": round(entry, 2), "margin": round(margin, 0),
        "opened": C.now_ist().isoformat(timespec="seconds"), "spot_at_entry": spot, "source": source,
        "mode": "signal" if plan else "manual", "plan": plan,
        "sl_prem": (order or {}).get("sl_prem", round(entry * (1 - C.PREMIUM_HARD_SL), 2) if entry > 0 else -1e9),
        "target_prem": (order or {}).get("target_prem", round(entry * 1.6, 2) if entry > 0 else 1e9),
    }
    store.insert_position(pos)
    desc = ", ".join("%s %g%s" % (l["side"], l["strike"], l["opt"]) for l in legs)
    return True, f"Paper trade opened: {symbol} {desc} × {lots} lot(s) at ₹{entry:.2f}."


def close_position(pos_id, prices: dict, reason: str) -> dict | None:
    pos = store.take_position(pos_id)
    if not pos:
        return None
    exit_net = net_price(pos["legs"], prices)
    gross = (exit_net - pos["entry_prem"]) * pos["qty"]
    ch = 0.0
    for lg in pos["legs"]:
        now = prices.get(lg["key"], lg["price"])
        b, s = (lg["price"], now) if lg["side"] == "BUY" else (now, lg["price"])
        ch += I.charges(b, s, pos["qty"])
    rec = {**pos, "closed": C.now_ist().isoformat(timespec="seconds"), "exit_prem": round(exit_net, 2),
           "gross_pnl": round(gross, 2), "charges": round(ch, 2), "pnl": round(gross - ch, 2), "reason": reason}
    store.insert_trade(rec)
    return rec


def unrealised(pos, prices) -> float:
    return (net_price(pos["legs"], prices) - pos["entry_prem"]) * pos["qty"]


def day_guard(positions, trades, capital) -> dict:
    today = C.today_ist().isoformat()
    closed_today = [h for h in trades if h["opened"][:10] == today]
    opened_today = closed_today + [p for p in positions if p["opened"][:10] == today]
    pnl = sum(h["pnl"] for h in closed_today)
    losses = sum(1 for h in closed_today if h["pnl"] < 0)
    if pnl <= -C.MAX_DAILY_LOSS * capital:
        return {"blocked": True, "reason": f"Daily loss limit hit (₹{pnl:,.0f}). Stop for today - this rule saves accounts."}
    if losses >= 2:
        return {"blocked": True, "reason": "Two losing trades today. Stop - revenge trading is the #1 account killer."}
    if len(opened_today) >= C.MAX_TRADES_PER_DAY:
        return {"blocked": True, "reason": f"Max {C.MAX_TRADES_PER_DAY} trades per day reached."}
    return {"blocked": False, "pnl_today": pnl, "trades_today": len(opened_today)}


def stats(trades: list[dict], capital: float) -> dict:
    if not trades:
        return {}
    pnl = np.array([h["pnl"] for h in trades], dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    eq = capital + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[capital], eq]))[1:]
    return {
        "trades": int(len(pnl)), "win_rate": float(len(wins) / len(pnl) * 100),
        "avg_win": float(wins.mean()) if len(wins) else 0.0, "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None,
        "net": float(pnl.sum()), "max_dd_pct": float(((eq - peak) / peak).min() * 100),
        "equity": [{"t": h["closed"], "v": float(v)} for h, v in zip(trades, eq)],
    }
