"""Paper-trading engine: demo money, positions, journal and stats (in Supabase).
Cash is always derived (start capital - open margin + realised P&L), so there is
no running balance that can drift."""
from __future__ import annotations

import uuid
from datetime import timedelta

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


REMOVED = "fno:trades:removed"          # trades taken out of the P&L by the user (kept here so they can be restored)
ORIGINS = {"manual": "Manual orders (option chain / order ticket)", "signal": "Signals you placed yourself", "auto": "Automatic (Autopilot / auto-trade)"}


def origin(t: dict) -> str:
    """Who opened it: 'auto' = the system on its own, 'signal' = a system signal you placed by hand, 'manual' = your own order."""
    if t.get("mode") != "signal":
        return "manual"
    return "auto" if (t.get("plan") or {}).get("auto") else "signal"


def _group(rows, pnl=True) -> dict:
    out = {k: {"n": 0, "pnl": 0.0} for k in ORIGINS}
    for t in rows:
        g = out[origin(t)]; g["n"] += 1
        if pnl:
            g["pnl"] += float(t.get("pnl") or 0)
    return {k: {"label": ORIGINS[k], "n": v["n"], "pnl": round(v["pnl"], 2)} for k, v in out.items()}


def origin_summary() -> dict:
    """Closed trades and open positions by origin, plus what is parked in the removed list."""
    removed = _removed()
    return {"closed": _group(store.trades()), "open": _group(store.positions(), pnl=False), "removed": _group(removed),
            "removed_total": len(removed)}


def _removed() -> list[dict]:
    import json as _j
    return [_j.loads(v) for v in (store._cmd("HVALS", REMOVED) or [])]


def remove_trades(kinds) -> dict:
    """Take closed trades of the given origins out of the ledger (P&L, cash, equity and every stat are derived from it).
    Each one is copied to REMOVED first, then deleted, so restore_removed() can put it back. Open positions are not touched."""
    import json as _j
    kinds = [k for k in kinds if k in ("manual", "signal")]          # automatic trades can never be removed here
    if not kinds:
        raise ValueError("Pick manual orders and/or signals you placed yourself.")
    moved, pnl = 0, 0.0
    for t in store.trades():
        if origin(t) in kinds:
            store._cmd("HSET", REMOVED, t["id"], store._dump(t))
            store._cmd("HDEL", store.TRADES, t["id"])
            moved += 1; pnl += float(t.get("pnl") or 0)
    return {"removed": moved, "pnl_removed": round(pnl, 2)}


def restore_removed() -> dict:
    import json as _j
    n = 0
    for t in _removed():
        store._cmd("HSET", store.TRADES, t["id"], store._dump(t))
        store._cmd("HDEL", REMOVED, t["id"]); n += 1
    return {"restored": n}


def reset(capital: float):
    store.clear_positions()
    store.clear_trades()
    store._cmd("DEL", REMOVED)
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
        "mode": "signal" if plan else "manual", "plan": plan, "delta": (order or {}).get("delta"),
        "sl_prem": (order or {}).get("sl_prem", round(entry * (1 - C.PREMIUM_HARD_SL), 2) if entry > 0 else -1e9),
        "target_prem": (order or {}).get("target_prem", round(entry * 1.6, 2) if entry > 0 else 1e9),
        "target_fixed": (order or {}).get("target_fixed", True),
    }
    store.insert_position(pos)
    desc = ", ".join("%s %g%s" % (l["side"], l["strike"], l["opt"]) for l in legs)
    return True, f"Paper trade opened: {symbol} {desc} × {lots} lot(s) at ₹{entry:.2f}."


def close_position(pos_id, prices: dict, reason: str, extra: dict | None = None) -> dict | None:
    pos = store.take_position(pos_id)
    if not pos:
        return None
    exit_net = net_price(pos["legs"], prices)
    gross = (exit_net - pos["entry_prem"]) * pos["qty"]
    ch = 0.0
    for lg in pos["legs"]:
        now = prices.get(lg["key"], lg["price"])
        b, s = (lg["price"], now) if lg["side"] == "BUY" else (now, lg["price"])
        ch += I.charges(b, s, pos["qty"], exch_pct=I.exch_rate(pos.get("symbol")))
    rec = {**pos, "closed": C.now_ist().isoformat(timespec="seconds"), "exit_prem": round(exit_net, 2),
           "gross_pnl": round(gross, 2), "charges": round(ch, 2), "pnl": round(gross - ch, 2), "reason": reason, **(extra or {})}
    store.insert_trade(rec)
    try:                                                    # the learning engine writes a lesson for every closed trade; it must never block a close
        from . import learn
        learn.on_close_in(rec)
    except Exception:
        import traceback
        traceback.print_exc()
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


def equity(acct, trades) -> float:
    """Starting capital plus realised P&L - what compounding sizes from."""
    return float(acct["capital_start"]) + sum(h["pnl"] for h in trades)


def peak_equity(acct, trades) -> float:
    """Highest realised account value so far (trades in closing order)."""
    eq = pk = float(acct["capital_start"])
    for h in sorted(trades, key=lambda x: x.get("closed") or ""):
        eq += h["pnl"]
        pk = max(pk, eq)
    return pk


def base(acct, trades) -> float:
    """The capital the risk limits and fixed sizing are measured on: the starting capital, or - when it is set to 0 to trade
    only the profit already made - the current equity (never 0, so a 2% limit is never 'any loss')."""
    cs = float(acct["capital_start"])
    return cs if cs > 0 else max(equity(acct, trades), 1.0)


def stats_capital(acct) -> float:
    """Starting point of the equity curve in the stats: the capital before 'profit only' was switched on, if it was."""
    cs = float(acct["capital_start"])
    return cs if cs > 0 else float(acct.get("capital_before") or 0) or 1.0


def week_pnl(trades) -> float:
    today = C.today_ist()
    wk_start = (today - timedelta(days=today.weekday())).isoformat()
    return float(sum(h["pnl"] for h in trades if wk_start <= h["opened"][:10] <= today.isoformat()))


def week_guard(trades, capital, limit=None) -> dict:
    """Round 18: after losing 4% of capital in a week, no new trades until Monday."""
    today = C.today_ist()
    wk_start = (today - timedelta(days=today.weekday())).isoformat()
    wk_pnl = sum(h["pnl"] for h in trades if wk_start <= h["opened"][:10] <= today.isoformat())
    limit = C.MAX_WEEKLY_LOSS if limit is None else limit
    if wk_pnl <= -limit * capital:
        return {"blocked": True, "reason": f"Weekly loss limit hit (₹{wk_pnl:,.0f} this week, limit {limit:.0%}). No new trades until Monday - research round 18."}
    return {"blocked": False}


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
