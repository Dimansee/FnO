"""Overnight carry slot - a separate, paper-only ledger (round 77, 3 Nov 2026). OFF by default.

Rule (research/round75b_overnight_carry.py signal 'strong', tested as its own slot in research/round77_carry_slot.py):
  at 15:20 on day D, for Nifty / Bank Nifty: if D moved >= 0.5% open -> 15:15 close AND the 15:15 close is in the top 20%
  of D's range (09:15-15:15), buy 1 lot of the ATM call (mirror: >= 0.5% down and bottom 20% -> ATM put) of the nearest
  expiry that is still alive tomorrow. Sell it at the next trading day's open (first check from 09:15). No stop overnight:
  the most it can lose is the premium.
  Guards: premium of 1 lot <= 50% of the slot's equity; the slot's own weekly budget (no new carry once this week's carry
  losses reach 2% of its equity); the capital-shield floor from Autopilot -> Safety.
Backtest (Oct 2023 - Oct 2026, real NSE option prices, next to today's Autopilot): Rs 2L -> 43.5L vs 40.9L, better in
2023-24 and 2025-26, worst fall -29.8% vs -31.3%. BUT about all the profit came from the 10 biggest gap nights; without
them it is about Rs 0, and at 2% slippage it loses. Hence a separate opt-in paper slot, never part of Autopilot.
It never touches the main paper account or the scalp book: own capital, own positions, own journal, own keys.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time

from . import config as C
from . import indicators as I
from . import store

SETTINGS_KEY = "carry"
POS, TRADES = "fno:carry:positions", "fno:carry:trades"
SYMS = ("NIFTY", "BANKNIFTY")                     # the only indices with real option prices in the test
DEFAULTS = {"on": False, "syms": list(SYMS), "capital": 50_000}
RULE = {"move": 0.005, "pos": 0.80, "week_budget": 0.02, "max_prem": 0.50}
ENTRY_FROM, ENTRY_TO = time(15, 20), time(15, 28)   # the 15:15 5-min bar has closed; before the 15:30 close
SIGNAL_BAR_LAST = time(15, 15)                       # bars that start at or before 15:15 (as tested)
BACKTEST = {
    "period": "Oct 2023 - Oct 2026, real NSE option prices (bhavcopy), Nifty + Bank Nifty, 1 lot, 1% slippage each side + fees",
    "rows": [
        {"name": "Rs 2L Autopilot alone", "y2324": 888_000, "y2526": 1_028_000, "full": 4_093_000, "fall": -31.3},
        {"name": "Rs 2L + carry slot (1 lot)", "y2324": 936_000, "y2526": 1_234_000, "full": 4_346_000, "fall": -29.8},
        {"name": "Rs 20k Autopilot alone", "y2324": 196_000, "y2526": 175_000, "full": 625_000, "fall": -66.0},
        {"name": "Rs 20k + carry slot (1 lot)", "y2324": 244_000, "y2526": 390_000, "full": 879_000, "fall": -50.3},
    ],
    "warning": "426 carry nights made Rs +2.54L, but the 10 biggest gap nights made Rs +2.52L (e.g. 2 Feb 2026, 4 Apr 2025). "
               "Without them it is about Rs 0; the median night is +Rs 89, and at 2% slippage the carry loses money. "
               "It is a bet on rare big gaps - keep it small and on paper.",
}


# ----------------------------------------------------------------------------- settings / storage
def settings() -> dict:
    return {**DEFAULTS, **(store.get(SETTINGS_KEY) or {})}


def save_settings(new: dict) -> dict:
    cur = settings()
    if "on" in new:
        cur["on"] = bool(new["on"])
    if "syms" in new:
        cur["syms"] = [s for s in (new["syms"] or []) if s in SYMS]
    if "capital" in new:
        cur["capital"] = max(5_000.0, min(10_000_000.0, float(new["capital"])))
    store.put(SETTINGS_KEY, cur)
    return cur


def positions() -> list[dict]:
    return sorted((json.loads(x) for x in store.hvals(POS)), key=lambda p: p["opened"])


def trades() -> list[dict]:
    return sorted((json.loads(x) for x in store.hvals(TRADES)), key=lambda t: t["closed"])


def reset():
    store.hdrop(POS)
    store.hdrop(TRADES)


def equity(cfg, trs=None) -> float:
    trs = trades() if trs is None else trs
    return float(cfg["capital"]) + sum(float(t["pnl"]) for t in trs)


# ----------------------------------------------------------------------------- the rule
def signal(c5, today: date) -> dict:
    """Strong-close check on today's 5-minute bars that start at or before 15:15. side +1 call, -1 put, 0 none."""
    if c5 is None or len(c5) == 0:
        return {"side": 0, "why": "no candles"}
    idx = c5.index
    d = c5[[ts.date() == today for ts in idx]]
    d = d[[ts.time() <= SIGNAL_BAR_LAST for ts in d.index]]
    if len(d) < 10 or d.index[-1].time() < SIGNAL_BAR_LAST:
        return {"side": 0, "why": "today's candles up to 15:15 are not complete"}
    o, h, l, c = float(d["open"].iloc[0]), float(d["high"].max()), float(d["low"].min()), float(d["close"].iloc[-1])
    mv = c / o - 1
    pos = (c - l) / max(h - l, 1e-9)
    out = {"move_pct": round(mv * 100, 2), "close_in_range": round(pos, 2), "close": c}
    if mv >= RULE["move"] and pos >= RULE["pos"]:
        return {**out, "side": 1, "why": f"strong close up: {mv:+.2%} on the day, close in the top {1 - pos:.0%} of the range"}
    if mv <= -RULE["move"] and pos <= 1 - RULE["pos"]:
        return {**out, "side": -1, "why": f"strong close down: {mv:+.2%} on the day, close in the bottom {pos:.0%} of the range"}
    return {**out, "side": 0, "why": f"no strong close ({mv:+.2%}, close at {pos:.0%} of the range; needs ±0.5% and the outer 20%)"}


def _week_loss(trs, now) -> float:
    wk = now.isocalendar()[:2]
    return -sum(min(0.0, float(t["pnl"])) for t in trs if datetime.fromisoformat(t["closed"]).isocalendar()[:2] == wk)


def _shield_floor(cfg, eq) -> str | None:
    try:
        from . import service as SV
        pf = SV.prefs()
    except Exception:
        return None
    if not pf.get("shield_on", True):
        return None
    floor = float(cfg["capital"]) * float(pf.get("shield_floor_pct") or 0) / 100
    if eq < floor:
        return f"Capital shield: carry equity ₹{eq:,.0f} is below the floor ₹{floor:,.0f}"
    return None


def _open(sym, sg, m, now, cfg, trs) -> tuple[bool, str]:
    from .market import slip_pct
    eq = equity(cfg, trs)
    sb = _shield_floor(cfg, eq)
    if sb:
        return False, sb
    wl = _week_loss(trs, now)
    if wl >= RULE["week_budget"] * eq:
        return False, f"carry weekly budget used: lost ₹{wl:,.0f} this week (limit {RULE['week_budget']:.0%} of ₹{eq:,.0f})"
    exps, lot = m.expiries_and_lot(sym)
    alive = sorted(e for e in exps if e > now.date())
    if not alive:
        return False, "no expiry alive tomorrow"
    exp = alive[0]
    spot = m.spot(sym) or sg["close"]
    vix = m.vix()["level"]
    ch = m.chain(sym, exp, spot, vix, m.candles(sym))
    live = ch.attrs.get("source") == "live"
    step = C.INDICES[sym]["step"]
    k = float(round(spot / step) * step)
    opt, tag = ("CE", "ce") if sg["side"] > 0 else ("PE", "pe")
    row = ch[ch["strike"] == k]
    if row.empty:
        return False, f"{k:g} {opt} is not in the option chain"
    r = row.iloc[0]
    ltp, ask = r.get(f"{tag}_ltp"), r.get(f"{tag}_ask")
    good = lambda v: v is not None and v == v and float(v) > 0
    if not good(ltp):
        return False, f"{k:g} {opt}: no price"
    buy = float(ask) if (live and good(ask)) else round(float(ltp) * (1 + slip_pct(float(ltp))), 2)
    prem = buy * int(lot)
    if prem > RULE["max_prem"] * eq:
        return False, f"1 lot of {k:g} {opt} costs ₹{prem:,.0f}, more than {RULE['max_prem']:.0%} of the carry equity ₹{eq:,.0f}"
    p = {"id": uuid.uuid4().hex[:8], "symbol": sym, "opened": now.isoformat(timespec="seconds"), "expiry": str(exp),
         "legs": [{"side": "BUY", "strike": k, "opt": opt, "key": r[f"{tag}_key"], "price": buy, "src": m.br.name if live and m.br else "demo"}],
         "lot": int(lot), "lots": 1, "qty": int(lot), "entry_prem": buy, "entry_spot": round(float(spot), 2), "side": sg["side"],
         "why": sg["why"], "source": m.source}
    store.hset(POS, p["id"], store._dump(p))
    return True, f"Carry {sym} {k:g}{opt} × 1 lot at ₹{buy:.2f} (₹{prem:,.0f}) - {sg['why']}; sells at tomorrow's open"


def _rec(p, m, now, why):
    spot = m.spot(p["symbol"])
    prices = m.leg_prices([p], {p["symbol"]: spot}, m.vix()["level"]) if spot is not None else {}
    lg = p["legs"][0]
    if lg["key"] not in prices:
        return None
    fills = m.exit_fills([p], {lg["key"]: prices[lg["key"]]}, "realistic")
    sell = float(fills.get(lg["key"], prices[lg["key"]]))
    gross = (sell - p["entry_prem"]) * p["qty"]
    chg = I.charges(p["entry_prem"], sell, p["qty"], exch_pct=I.exch_rate(p["symbol"]))
    return {"id": p["id"], "symbol": p["symbol"], "side": p["side"], "opened": p["opened"], "closed": now.isoformat(timespec="seconds"),
            "strike": lg["strike"], "opt": lg["opt"], "expiry": p["expiry"], "qty": p["qty"], "lots": 1,
            "entry_prem": p["entry_prem"], "exit_prem": round(sell, 2), "entry_spot": p["entry_spot"], "exit_spot": round(float(spot), 2),
            "gap_pct": round((float(spot) / p["entry_spot"] - 1) * 100, 2), "gross": round(gross, 2), "charges": round(chg, 2),
            "pnl": round(gross - chg, 2), "reason": why, "why": p.get("why"),
            "price_src": "live" if lg.get("src", "demo") != "demo" else "formula"}


def _close(p, m, now, why) -> dict | None:
    rec = _rec(p, m, now, why)
    if rec is None:
        return None
    return store.close_into(POS, TRADES, p["id"], lambda _cur: rec)


def tick(now=None, m=None) -> dict:
    """Every-minute job (market hours): sell yesterday's carry at the open; at 15:20-15:28 look for today's strong close."""
    cfg = settings()
    now = now or C.now_ist()
    open_ = positions()
    t = now.time()
    due = [p for p in open_ if p["opened"][:10] < now.date().isoformat()]
    want = cfg["on"] and cfg["syms"] and ENTRY_FROM <= t <= ENTRY_TO
    if not due and not want:
        return {"off": True} if not cfg["on"] and not open_ else {}
    from .market import Market
    m = m or Market()
    out = {"opened": [], "closed": [], "skipped": []}
    if t >= C.MARKET_OPEN:
        for p in due:
            try:
                rec = _close(p, m, now, "Sold at the next open")
                if rec:
                    out["closed"].append(rec)
                else:
                    out["skipped"].append({"symbol": p["symbol"], "why": "no option price yet - retrying next minute"})
            except Exception as e:
                out["skipped"].append({"symbol": p["symbol"], "why": f"error: {str(e)[:120]}"})
    if want:
        trs = trades()
        today = now.date().isoformat()
        for sym in cfg["syms"]:
            try:
                if any(p["symbol"] == sym and p["opened"][:10] == today for p in positions()) or \
                        any(x["symbol"] == sym and x["opened"][:10] == today for x in trs):
                    continue
                sg = signal(m.candles(sym), now.date())
                if not sg["side"]:
                    out["skipped"].append({"symbol": sym, "why": sg["why"]})
                    continue
                ok, msg = _open(sym, sg, m, now, cfg, trs)
                (out["opened"] if ok else out["skipped"]).append({"symbol": sym, ("message" if ok else "why"): msg})
            except Exception as e:
                out["skipped"].append({"symbol": sym, "why": f"error: {str(e)[:120]}"})
    return out


def exit_now(pid: str, m=None) -> dict | None:
    from .market import Market
    m = m or Market()
    p = next((x for x in positions() if x["id"] == pid), None)
    if not p:
        return None
    return _close(p, m, C.now_ist(), "Closed by hand")


def page() -> dict:
    cfg = settings()
    trs = trades()
    pnl = [float(t["pnl"]) for t in trs]
    return {"settings": cfg, "positions": positions(), "trades": trs[-100:][::-1], "equity": round(equity(cfg, trs), 2),
            "stats": {"trades": len(trs), "wins": sum(x > 0 for x in pnl), "pnl": round(sum(pnl), 2),
                      "best": round(max(pnl), 2) if pnl else 0, "worst": round(min(pnl), 2) if pnl else 0},
            "rule": "At 15:20, after a ±0.5% day that closes in the outer 20% of its range: buy 1 lot of the ATM option that way "
                    "(nearest expiry alive tomorrow), sell at the next open. Own weekly budget 2%, premium ≤ 50% of the slot's equity.",
            "backtest": BACKTEST}
