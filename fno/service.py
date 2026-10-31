"""Glue between market data, the rulebook and the paper ledger. Every public
function returns plain JSON-safe dicts for the API."""
from __future__ import annotations

import copy

import math
import threading
import uuid
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from . import ai_judge as AJ
from . import backtest as BT
from . import brokers as B
from . import config as C
from . import context as X
from . import indicators as I
from . import market as M
from . import push as PUSH
from . import paper as P
from . import store
from . import strategy as S
from . import ai as AI
from . import custom as CU
from . import learn as LN

ALL_SYMBOLS = list(C.INDICES) + list(C.STOCKS)


def clean(o):
    """Make numpy / pandas / NaN values JSON-safe."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, (date, pd.Timestamp)):
        return o.isoformat()
    return o


# ---------------------------------------------------------------------------
# Outside context (cached)
# ---------------------------------------------------------------------------
def gcues():
    try:
        return M.cached_soft("gcues", 600, X.global_cues, fail_ttl=300)
    except Exception:
        return {"markets": {}, "avg_equity_chg": None, "notes": ["Global data unavailable"]}


def news(symbol):
    meta = M.instrument(symbol)
    q = None if meta["kind"] == "index" else symbol
    try:
        return M.cached_soft(f"news:{q}", 300, lambda: X.fetch_news(q), fail_ttl=300)
    except Exception:
        return {"items": [], "score": 0, "events": [], "notes": ["News unavailable"]}


def fii():
    auto = M.cached("fii", 3600, X.fii_dii)
    if auto:
        auto["source"] = "NSE"
        return auto
    man = store.get("fii_manual")
    if man and man.get("value") is not None:
        return {"fii": float(man["value"]), "date": man.get("date"), "source": "manual"}
    return None


# ---------------------------------------------------------------------------
# Position management (also called every minute by the scheduler)
# ---------------------------------------------------------------------------
PREF_DEFAULTS = {"autopilot": False, "sizing": "risk", "risk_pct": 1.0, "strategies": ["noise", "camarilla"], "auto": False, "auto_syms": ["NIFTY"],
                 "enforce_limits": False, "fills": "realistic",
                 # capital shield (31 Oct 2026): hard caps on automatic entries so no run of losses or gap can empty the account
                 "shield_on": True, "shield_floor_pct": 60.0, "shield_open_pct": 50.0, "shield_trade_pct": 15.0}   # demo account: the daily risk limits are shown as a tip unless switched on


def prefs() -> dict:
    return {**PREF_DEFAULTS, **(store.get("prefs") or {})}


def eprefs() -> dict:
    """The settings the engine actually trades with. Autopilot ON overrides position size, risk, strategies and instruments
    with the tested rules; OFF = exactly what is saved in Settings."""
    pf = prefs()
    if not pf.get("autopilot"):
        return pf
    return {**pf, "auto": True, "auto_syms": list(C.AUTOPILOT_SYMS), "strategies": list(C.AUTOPILOT_STRATEGIES),
            "sizing": "compound", "risk_pct": C.AUTOPILOT_RISK_PCT, "max_trade_risk": C.AUTOPILOT_MAX_TRADE_RISK}


def sym_strategies(pf, symbol) -> list:
    """Strategies this instrument trades with: Autopilot narrows some indices to what held up in their own backtest (round 24)."""
    st = list(pf.get("strategies") or [])
    if pf.get("autopilot") and symbol in C.AUTOPILOT_SYM_STRATEGIES:
        st = [x for x in st if x in C.AUTOPILOT_SYM_STRATEGIES[symbol]]
    return st


def fib_allowed(pf, equity: float) -> bool:
    """Autopilot adds the Fibonacci breakout once equity reaches FIB_MIN_EQUITY (round 29: tested at Rs 2 lakh)."""
    return not pf.get("autopilot") or equity >= C.FIB_MIN_EQUITY


def _trend_only(pf, symbol) -> bool:
    """Autopilot trades this index with the trend-day slot only (no Noise / Camarilla / custom main-slot entries)."""
    st = C.AUTOPILOT_SYM_STRATEGIES.get(symbol)
    return bool(pf.get("autopilot")) and bool(st) and set(st) <= set(C.SLOTS)


def trend_allowed(pf, equity: float) -> bool:
    """Autopilot adds the trend-day slot only once the account has grown to TREND_MIN_EQUITY (round 24 small-account test)."""
    return not pf.get("autopilot") or equity >= C.TREND_MIN_EQUITY


def _traded_today(symbol, positions, trades) -> dict:
    """Which strategies already traded this instrument today (one trade per strategy per instrument)."""
    today = C.today_ist().isoformat()
    done = {"noise": False, "camarilla": False, "trend": False, "fib": False}
    for x in list(positions) + list(trades):
        if x["symbol"] == symbol and x.get("mode") == "signal" and x["opened"][:10] == today:
            st = ((x.get("plan") or {}).get("strategy")) or "noise"
            if str(st).startswith("custom:"):
                done[st] = int(done.get(st, 0)) + 1
            else:
                done[st] = True
    return done


def manage_positions(m: M.Market | None = None) -> list[dict]:
    pos = store.positions()
    if not pos:
        return []
    m = m or M.Market()
    now = C.now_ist()
    vix = m.vix()
    spots = {s: m.spot(s) for s in {p["symbol"] for p in pos}}
    prices = m.leg_prices(pos, spots, vix["level"])
    states = {}
    for sym in {p["symbol"] for p in pos if (p.get("plan") or {}).get("strategy") == "noise"}:
        try:
            states[sym] = S.noise_state(m.candles(sym), now)
        except Exception as e:
            m.errors.append(f"Band check failed for {sym}: {e}")
    cspecs = {x["id"]: x for x in _safe_specs()}
    cstates = {}
    for p in pos:
        st = (p.get("plan") or {}).get("strategy", "")
        if str(st).startswith("custom:") and (p["plan"].get("exit_on_opposite")) and st[7:] in cspecs:
            try:
                cstates[p["id"]] = CU.live_state(cspecs[st[7:]], m.candles(p["symbol"]), now)
            except Exception as e:
                m.errors.append(f"Custom strategy state failed for {p['symbol']}: {e}")
    exts = {}
    for p in pos:
        try:                                                 # high / low of the 5-minute bars after the entry bar: what happened between checks
            if (p.get("plan") or {}).get("sl") is not None:
                bars = m.candles(p["symbol"])
                o = datetime.fromisoformat(p["opened"]).replace(tzinfo=None)
                first = o.replace(minute=o.minute - o.minute % 5, second=0, microsecond=0) + timedelta(minutes=5)
                sub = bars[[ts.replace(tzinfo=None) >= first for ts in bars.index]]
                if not sub.empty:
                    exts[p["id"]] = (float(sub["high"].max()), float(sub["low"].min()))
        except Exception:
            pass
    closed = []
    for p in pos:
        if spots.get(p["symbol"]) is None or not all(l["key"] in prices for l in p["legs"]):
            due = now.time() >= C.SQUARE_OFF or p["opened"][:10] < now.date().isoformat()
            if due:                                          # square-off is guaranteed: close at the last price we saw
                last = p.get("last_px") or {}
                fills = {lg["key"]: float(prices.get(lg["key"], last.get(lg["key"], lg["price"]))) for lg in p["legs"]}
                rec = P.close_position(p["id"], fills, "Square-off 15:15 (live price unavailable - closed at the last known price)",
                                       extra={"spot_exit": spots.get(p["symbol"]) or p.get("last_spot"), "price_missing": True})
                if rec:
                    closed.append({"symbol": rec["symbol"], "reason": rec["reason"], "pnl": rec["pnl"], "id": p["id"]})
                continue
            if now.time() >= time(15, 0):                    # near square-off with no price: tell the user, do not stay silent
                _push_safe(f"{p['symbol']}: no price near square-off",
                           "A position is open but its live price is unavailable. Check it manually before 15:15.",
                           f"noprice:{p['id']}:{now.strftime('%H%M')[:3]}", "/#portfolio")
            continue
        prem = P.net_price(p["legs"], prices)
        sp = spots[p["symbol"]]
        is_c = str((p.get("plan") or {}).get("strategy", "")).startswith("custom:")
        reason, upd = S.exit_check(p, sp, prem, now, cstates.get(p["id"]) if is_c else states.get(p["symbol"]), exts.get(p["id"]))
        level = upd.pop("_level", None)                      # stop / target touched between two checks (price already moved away)
        hi, lo = max(p.get("hi", sp), sp), min(p.get("lo", sp), sp)      # best / worst index level seen: feeds the lesson written when the trade closes
        if hi != p.get("hi") or lo != p.get("lo"):
            upd = {**upd, "hi": hi, "lo": lo}
        lp = {lg["key"]: round(float(prices[lg["key"]]), 2) for lg in p["legs"]}
        if lp != p.get("last_px"):                       # last seen leg prices: the square-off fallback when the feed drops
            upd = {**upd, "last_px": lp, "last_spot": round(float(sp), 2)}
        if upd:
            p.update(upd)
            store.update_position(p)
        if reason and (p["mode"] == "signal" or reason.startswith("Square-off")):
            fills = m.exit_fills([p], prices, eprefs().get("fills", "realistic"))
            if level is not None and p.get("delta") and (p.get("plan") or {}).get("opt"):
                sg = 1 if p["plan"]["opt"] == "CE" else -1  # premium when the index was at the level, from the option's delta
                est = max(0.05, prem + float(p["delta"]) * sg * (level - sp))
                if "stop" in reason.lower():
                    est = max(0.05, est * (1 - M.slip_pct(est)))
                for lg in p["legs"]:
                    if lg["side"] == "BUY":
                        fills[lg["key"]] = round(est, 2)
            rec = P.close_position(p["id"], fills, reason, extra={"spot_exit": sp, "exit_ltp": round(prem, 2)})
            if rec:
                closed.append({"symbol": rec["symbol"], "reason": reason, "pnl": rec["pnl"], "id": p["id"]})
    return closed


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
def _gather(symbol, m: M.Market):
    meta = M.instrument(symbol)
    now = C.now_ist()
    candles = m.candles(symbol)
    if candles is None or candles.empty:
        raise RuntimeError("No price data available right now. Try again in a minute.")
    vix = m.vix()
    spot = float(candles["close"].iloc[-1])
    exps, lot = m.expiries_and_lot(symbol)
    trade_exp = I.pick_trading_expiry(exps, meta["expiry"], now.date()) or exps[0]
    chain = m.chain(symbol, trade_exp, spot, vix["level"], candles)
    return dict(meta=meta, now=now, candles=candles, vix=vix, spot=spot, exps=exps, lot=lot,
                trade_exp=trade_exp, chain=chain)


def _vix_prev_chg() -> float:
    """Yesterday's India VIX change vs the day before (%), a model input."""
    def f():
        d = M.yahoo_daily(C.INDIA_VIX_YAHOO, "10d")["close"].dropna()
        if len(d) < 3:
            return 0.0
        last = d.index[-1].date() if hasattr(d.index[-1], "date") else d.index[-1]
        if last == C.today_ist():
            d = d.iloc[:-1]
        return float((d.iloc[-1] / d.iloc[-2] - 1) * 100) if len(d) >= 2 else 0.0
    try:
        return M.cached("vix:prevchg", 900, f)
    except Exception:
        return 0.0


def _with_ai(symbol, g, ev, pf, traded):
    """Runs the AI model when enabled; its plan is used only when the rules have none (the rules keep priority)."""
    if "ai" not in (pf.get("strategies") or []):
        return ev
    if ev["signal"] in ("STOP FOR TODAY",):
        return ev
    try:
        res = AI.evaluate(symbol, g["candles"], g["vix"], gcues(), g["now"], g["trade_exp"],
                          trades_today=traded.get("ai", 0) if isinstance(traded, dict) else 0, vix_chg_prev=_vix_prev_chg())
    except Exception as e:                                        # the model must never break the page
        ev["checks"].append((False, f"AI model error: {str(e)[:120]}"))
        return ev
    ev["ai"] = res.get("ai")
    ev["ai_enabled"] = True
    ev["checks"].append((None, "AI model:"))
    ev["checks"] += res["checks"]
    if ev["signal"] == "MARKET CLOSED":
        return ev
    if not ev["plan"] and res["plan"]:
        ev["signal"], ev["plan"], ev["strategy"] = res["signal"], res["plan"], "AI model"
    elif ev["signal"] in ("NO TRADE TODAY", "NO NEW ENTRIES") and res["signal"] == "WAIT":
        ev["signal"] = "WAIT"
    return ev


def _safe_specs() -> list[dict]:
    try:
        return CU.load_all(store)
    except Exception:
        return []


def _with_custom(symbol, g, ev, pf, traded):
    """Runs the user's enabled custom strategies for this instrument; used only when the built-in strategies have no plan."""
    specs = [x for x in _safe_specs() if x.get("enabled") and x.get("market") == "IN" and symbol in x.get("symbols", [])]
    if not specs or ev["signal"] in ("STOP FOR TODAY", "MARKET CLOSED"):
        return ev
    for sp in specs:
        key = f"custom:{sp['id']}"
        try:
            res = CU.evaluate_live(sp, symbol, g["candles"], g["vix"], g["now"], int(traded.get(key, 0)) if isinstance(traded, dict) else 0)
        except Exception as e:                                   # a broken custom rule must never break the page
            ev["checks"].append((False, f"Custom strategy '{sp['name']}' error: {str(e)[:100]}"))
            continue
        ev["checks"] += res["checks"]
        ev.setdefault("custom", []).append({"id": sp["id"], "name": sp["name"], "signal": res["signal"], "auto": sp.get("auto", False)})
        if not ev["plan"] and res["plan"]:
            ev["signal"], ev["plan"], ev["strategy"] = res["signal"], res["plan"], sp["name"]
    return ev


def _learn_tags(g, plan) -> list[str]:
    cx = _entry_ctx(g)
    return LN.tags_for({"market": "IN", "side": plan.get("opt"), "time": g["now"].strftime("%H:%M"), "vix": cx.get("vix"), "trend": cx.get("trend"),
                        "gap_pct": cx.get("gap_pct"), "dte": cx.get("dte"), "wd": g["now"].weekday()})


def _learn_gate(g, plan):
    """(veto reason or None, size multiplier) from the learning engine's active rules."""
    try:
        st = plan.get("strategy") or "noise"
        return LN.veto("IN", st, _learn_tags(g, plan)), LN.size_mult("IN", st)
    except Exception:
        return None, 1.0


def _custom_auto_ok(plan) -> bool:
    st = str((plan or {}).get("strategy", ""))
    if not st.startswith("custom:"):
        return True
    sp = next((x for x in _safe_specs() if x["id"] == st[7:]), None)
    return bool(sp and sp.get("auto"))


def _entry_ctx(g) -> dict:
    """What the market looked like at entry. The learning engine groups closed trades by these."""
    try:
        today, hist = S.session_frames(g["candles"], g["now"])
        gap = (float(today["open"].iloc[0]) / float(hist["close"].iloc[-1]) - 1) * 100 if len(today) and len(hist) else None
        last = today.iloc[-1] if len(today) else None
        up = bool(last is not None and last["ema9"] > last["ema21"] and last["st"] > 0)
        dn = bool(last is not None and last["ema9"] < last["ema21"] and last["st"] < 0)
        return {"vix": round(float(g["vix"].get("level") or 0), 2), "vix_chg": round(float(g["vix"].get("chg_pct") or 0), 2),
                "gap_pct": None if gap is None else round(gap, 2), "trend": "up" if up else "down" if dn else "mixed",
                "dte": (g["trade_exp"] - g["now"].date()).days, "weekday": g["now"].weekday(), "minute_of_day": g["now"].hour * 60 + g["now"].minute,
                "atr_pct": None if last is None else round(float(last["atr"]) / float(last["close"]) * 100, 3)}
    except Exception:
        return {}


def _size_capital(acct, tr, pf) -> float:
    """Capital the position size is worked out from: the starting capital, or the current equity when compounding."""
    return P.equity(acct, tr) if pf.get("sizing") == "compound" else P.base(acct, tr)


def risk_guard(pos, tr, capital, pf, auto=False, equity=None) -> dict:
    """The daily risk limits (two losers / -2% / max trades). On the demo account they only advise unless enforced in Settings.
    Autopilot / automatic entries count only the system's own trades (signal trades): the limits were tested on the rules' trades
    (rounds 18 and 20), and a manual trade's loss must not switch the system off (19 Oct 2026: a manual -Rs 8,321 tripped the weekly
    limit and blocked Autopilot for three days although its own trades were well inside it)."""
    eq = float(equity) if equity is not None else float(capital) + sum(h["pnl"] for h in tr)   # account equity (all trades) - the size of the limit
    if (pf.get("autopilot") or auto) and flag("limits_system_only"):
        tr = [h for h in tr if h.get("mode") == "signal"]
        pos = [p for p in pos if p.get("mode") == "signal"]
    wl = pf.get("weekly_limit") or "auto"                         # Settings: auto (tested rule) / off / custom rupee amount
    if wl == "off":
        wg = {"blocked": False}
    elif wl == "custom" and float(pf.get("weekly_limit_rs") or 0) > 0:
        lim = float(pf["weekly_limit_rs"]); wk = P.week_pnl(tr)
        wg = {"blocked": wk <= -lim, "reason": f"Weekly loss limit hit (₹{wk:,.0f} this week, your limit ₹{lim:,.0f} in Settings). No new trades until Monday."}
    elif pf.get("autopilot"):
        wg = P.week_guard(tr, eq, C.AUTOPILOT_WEEKLY_SMALL if eq < C.AUTOPILOT_SMALL_ACCOUNT else None)
    else:
        wg = P.week_guard(tr, capital)
    if (pf.get("autopilot") or auto) and wg.get("blocked") and flag("limits_system_only"):
        wg = {**wg, "reason": wg["reason"].replace(" this week,", " this week on the system's own trades - manual trades not counted,")}
    if wg.get("blocked") and (auto or pf.get("enforce_limits")):   # the weekly limit always applies to automatic trades (round 18: it improved profit and the deepest fall)
        return wg
    g = P.day_guard(pos, tr, capital)
    if g.get("blocked") and not pf.get("enforce_limits"):
        return {"blocked": False, "tip": "Daily risk limit reached - on a real account you would stop now: " + g["reason"] +
                " (This demo account carries on; switch 'Enforce daily limits' on in Settings to stop for the day.)",
                "pnl_today": g.get("pnl_today"), "trades_today": g.get("trades_today")}
    return g


def risk_status() -> dict:
    """Everything that can stop or shrink automatic trading right now, in one place (banner, Autopilot → Status)."""
    acct, pos, tr, cash = P.snapshot()
    pf = eprefs()
    eq = P.equity(acct, tr)
    base = P.base(acct, tr)
    sys_only = bool(pf.get("autopilot")) and flag("limits_system_only")
    str_ = [h for h in tr if h.get("mode") == "signal"] if sys_only else tr
    wl = pf.get("weekly_limit") or "auto"
    if wl == "off":
        wk = {"blocked": False, "week_pnl": round(P.week_pnl(str_), 2), "limit_rs": None, "mode": "off"}
    elif wl == "custom" and float(pf.get("weekly_limit_rs") or 0) > 0:
        lim = float(pf["weekly_limit_rs"]); w = P.week_pnl(str_)
        wk = {"blocked": w <= -lim, "week_pnl": round(w, 2), "limit_rs": lim, "mode": "custom"}
    elif pf.get("autopilot"):
        wk = {**P.week_guard(str_, eq, C.AUTOPILOT_WEEKLY_SMALL if eq < C.AUTOPILOT_SMALL_ACCOUNT else None), "mode": "auto"}
    else:
        wk = {**P.week_guard(str_, base), "mode": "auto"}
    wk["system_only"] = sys_only
    wk["reset_at"] = P.week_reset_at()
    g = risk_guard(pos, tr, base, pf, auto=True, equity=eq)
    day = P.day_guard([p for p in pos if not sys_only or p.get("mode") == "signal"], str_, base)
    sh = shield_state(acct, pos, tr, pf)
    try:
        from . import scalp as SC
        scfg = SC.settings()
        now = C.now_ist()
        sc = {"on": bool(scfg.get("on")), "capital": scfg.get("capital"), "shield": SC.shield_block(scfg),
              "stopped": {s: (SC.governor(SC._today_trades(s, now), scfg["capital"]) or {}).get("stopped") for s in scfg.get("syms") or []}}
    except Exception as e:
        sc = {"error": str(e)[:120]}
    banners = []
    if pf.get("auto") or pf.get("autopilot"):
        if wk.get("blocked"):
            banners.append({"level": "bad", "kind": "weekly", "text": (g.get("reason") if g.get("blocked") else None) or
                            f"Weekly loss limit reached (₹{wk['week_pnl']:,.0f} this week). Autopilot takes no new trades until Monday."})
        elif g.get("blocked"):
            banners.append({"level": "bad", "kind": "daily", "text": g.get("reason")})
        elif wk.get("limit_rs") and wk["week_pnl"] <= -0.75 * wk["limit_rs"]:
            banners.append({"level": "warn", "kind": "weekly_near", "text": f"Close to the weekly loss limit: ₹{wk['week_pnl']:,.0f} of -₹{wk['limit_rs']:,.0f} used this week."})
        if sh["below_floor"]:
            banners.append({"level": "bad", "kind": "shield", "text": f"Capital shield: equity ₹{sh['equity']:,.0f} is below the floor ₹{sh['floor']:,.0f}. Autopilot is stopped."})
    if isinstance(sc, dict) and sc.get("on"):
        if sc.get("shield"):
            banners.append({"level": "bad", "kind": "scalp_shield", "text": sc["shield"]})
        for s_, why in (sc.get("stopped") or {}).items():
            if why:
                banners.append({"level": "warn", "kind": "scalp", "text": f"Scalp book {s_} stopped for today: {why}."})
    return {"autopilot": bool(pf.get("autopilot")), "auto": bool(pf.get("auto")), "auto_syms": pf.get("auto_syms"), "strategies": pf.get("strategies"),
            "equity": round(eq), "capital_start": acct.get("capital_start"), "cash": round(cash), "open_positions": len(pos),
            "weekly": wk, "daily": day, "blocked": bool(g.get("blocked")), "block_reason": g.get("reason"), "shield": sh, "scalp": sc,
            "banners": banners, "resets": (acct.get("resets") or [])[-5:], "time": C.now_ist().isoformat(timespec="seconds")}


def autopilot_overview() -> dict:
    """Autopilot tab: status, the settings it trades with (and what it overrides), live outcomes of its own trades,
    and the learning / research changes that are applied to it right now."""
    from . import learn as LN
    acct, pos, tr, cash = P.snapshot()
    pf, saved = eprefs(), prefs()
    auto_tr = [t for t in tr if P.origin(t) == "auto"]
    sig_tr = [t for t in tr if t.get("mode") == "signal"]

    def agg(rows):
        n = len(rows)
        pnl = sum(float(t.get("pnl") or 0) for t in rows)
        w = [t for t in rows if (t.get("pnl") or 0) > 0]
        lo = [t for t in rows if (t.get("pnl") or 0) <= 0]
        return {"n": n, "pnl": round(pnl), "win_rate": round(len(w) / n * 100, 1) if n else None,
                "avg_win": round(sum(t["pnl"] for t in w) / len(w)) if w else None, "avg_loss": round(sum(t["pnl"] for t in lo) / len(lo)) if lo else None,
                "best": round(max((t["pnl"] for t in rows), default=0)), "worst": round(min((t["pnl"] for t in rows), default=0))}
    by_st, by_sym, by_wk, curve = {}, {}, {}, []
    eq = float(acct.get("capital_start") or 0)
    for t in sorted(sig_tr, key=lambda x: x.get("closed") or ""):
        st = (t.get("plan") or {}).get("strategy") or "?"
        by_st.setdefault(st, []).append(t)
        by_sym.setdefault(t.get("symbol"), []).append(t)
        try:
            wk = datetime.fromisoformat(t["opened"]).strftime("%G-W%V")
        except Exception:
            wk = "?"
        by_wk.setdefault(wk, []).append(t)
        eq += float(t.get("pnl") or 0)
        curve.append({"t": (t.get("closed") or "")[:16], "equity": round(eq)})
    exits = {}
    for t in sig_tr:
        r = (t.get("reason") or "?").split("(")[0].strip()[:40]
        exits.setdefault(r, []).append(t)
    overrides = []
    if pf.get("autopilot"):
        for k, label in (("sizing", "Position size"), ("risk_pct", "Risk per trade %"), ("strategies", "Strategies"), ("auto_syms", "Instruments")):
            if saved.get(k) != pf.get(k):
                overrides.append({"setting": label, "saved": saved.get(k), "autopilot_uses": pf.get(k)})
    try:
        ov = LN.overlay()
        lchanges = LN.changes(15)
        lmode = LN.mode("IN")
    except Exception:
        ov, lchanges, lmode = {"rules": []}, [], None
    return {"status": risk_status(),
            "settings": {"autopilot": bool(pf.get("autopilot")), "auto": bool(pf.get("auto")), "auto_syms": pf.get("auto_syms"), "strategies": pf.get("strategies"),
                         "sym_strategies": {s_: sym_strategies(pf, s_) for s_ in (pf.get("auto_syms") or [])}, "sizing": pf.get("sizing"),
                         "risk_pct": pf.get("risk_pct"), "max_trade_risk": pf.get("max_trade_risk"), "confidence": pf.get("confidence", "standard"),
                         "weekly_limit": pf.get("weekly_limit") or "auto", "weekly_limit_rs": pf.get("weekly_limit_rs"), "enforce_limits": bool(pf.get("enforce_limits")),
                         "fills": pf.get("fills"), "entry_window": [C.FIRST_ENTRY_ANY.strftime("%H:%M"), C.LAST_ENTRY_ANY.strftime("%H:%M")],
                         "square_off": C.SQUARE_OFF.strftime("%H:%M"), "small_account": C.AUTOPILOT_SMALL_ACCOUNT, "weekly_small_pct": C.AUTOPILOT_WEEKLY_SMALL * 100,
                         "weekly_pct": C.MAX_WEEKLY_LOSS * 100, "daily_loss_pct": C.MAX_DAILY_LOSS * 100, "max_trades_day": C.MAX_TRADES_PER_DAY,
                         "shield": {k: saved.get(k, PREF_DEFAULTS.get(k)) for k in ("shield_on", "shield_floor_pct", "shield_open_pct", "shield_trade_pct")},
                         "overrides": overrides, "learn_mode": lmode},
            "outcomes": {"all_auto": agg(auto_tr), "all_system": agg(sig_tr), "by_strategy": {k: agg(v) for k, v in by_st.items()},
                         "by_symbol": {k: agg(v) for k, v in by_sym.items()}, "by_week": {k: agg(v) for k, v in sorted(by_wk.items())},
                         "exits": {k: agg(v) for k, v in exits.items()}, "curve": curve[-400:], "capital_start": acct.get("capital_start"),
                         "archived": len(P.archived())},
            "learning": {"mode": lmode, "overlay": ov.get("rules", []), "changes": lchanges, "switches": changes()}}


def calendar_guard(symbol, exps, today) -> dict | None:
    """Days the research says to sit out (round 6): Union Budget day, and Bank Nifty on its own expiry day."""
    if today in C.HOLIDAYS:
        return {"blocked": True, "reason": "NSE trading holiday - the market is closed today."}
    if today in C.BUDGET_DAYS:
        return {"blocked": True, "reason": "Union Budget day - no new signal trades (every Budget day in the 2023-26 test lost)."}
    if symbol in C.SKIP_OWN_EXPIRY and today in set(exps or []):
        return {"blocked": True, "reason": f"{M.instrument(symbol)['label']} expiry day - no new signals in it today "
                                           "(it lost in 3 of 4 years 2023-26 on these days). Nifty is still allowed."}
    return None


def _chart(today, hist):
    df = pd.concat([hist.tail(75), today]) if not hist.empty else today
    out = []
    for ts, r in df.iterrows():
        t = int(ts.timestamp()) + 19800  # shift so the chart's UTC axis reads IST
        out.append({"time": t, "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"],
                    "vwap": r.get("vwap"), "ema9": r.get("ema9"), "ema21": r.get("ema21")})
    return out


def _zones(hist: pd.DataFrame, today: pd.DataFrame, spot: float, n_days: int = 6) -> list[dict]:
    """Support / resistance zones: 5-minute swing highs and lows (2 bars each side) of the last sessions plus each day's
    high / low, clustered within 0.12% of price; strength = how many times price turned there."""
    df = pd.concat([hist[hist.index.normalize().isin(sorted(set(hist.index.normalize()))[-n_days:])] if not hist.empty else hist, today])
    if len(df) < 10:
        return []
    h, l = df["high"].values, df["low"].values
    pts = []
    for i in range(2, len(df) - 2):
        if h[i] >= max(h[i - 2:i + 3]):
            pts.append(float(h[i]))
        if l[i] <= min(l[i - 2:i + 3]):
            pts.append(float(l[i]))
    for _, g in df.groupby(df.index.date):
        pts += [float(g["high"].max())] * 2 + [float(g["low"].min())] * 2      # day extremes count double
    pts.sort()
    tol = spot * 0.0012
    zs, cur = [], [pts[0]]
    for x in pts[1:]:
        if x - cur[-1] <= tol and x - cur[0] <= 2 * tol:            # no chaining: a zone is at most ~0.25% wide
            cur.append(x)
        else:
            zs.append(cur); cur = [x]
    zs.append(cur)
    out = [{"price": round(sum(z) / len(z), 1), "lo": round(min(z), 1), "hi": round(max(z), 1), "touches": len(z)} for z in zs if len(z) >= 3]
    for z in out:
        z["kind"] = "resistance" if z["price"] > spot else "support"
        z["strength"] = 3 if z["touches"] >= 7 else 2 if z["touches"] >= 5 else 1
    above = sorted([z for z in out if z["price"] > spot], key=lambda z: z["price"])[:5]
    below = sorted([z for z in out if z["price"] <= spot], key=lambda z: -z["price"])[:5]
    return below[::-1] + above


def live_chart(symbol: str = "NIFTY", day: str | None = None) -> dict:
    """The working chart: a session's 5-minute candles with every level the rules use, the noise band, VWAP / EMAs,
    support-resistance zones, and every setup of that day (recommendations, autopilot decisions, paper and scalp trades)
    with entry, stop, trailing stop, target and exit - plus the risk of each open setup in rupees and % of equity."""
    from datetime import date as _date
    from . import journal as J
    m = M.Market()
    c = m.candles(symbol)
    if c is None or c.empty:
        raise ValueError("No candles available for this instrument right now.")
    days_avail = sorted({d.isoformat() for d in c.index.date})
    vault_days = []
    try:
        from . import datahub as DH
        vk = f"{DH.CK}IN:{symbol}"
        vault_days = DH._days(vk)
        want = day or days_avail[-1]
        if want not in days_avail and want in vault_days:            # older session: rebuild from the saved candles
            i = vault_days.index(want)
            rows = DH._candles_rows("IN", symbol, _date.fromisoformat(vault_days[max(0, i - 20)]), _date.fromisoformat(want))
            if rows:
                v = pd.DataFrame(rows)
                v.index = pd.to_datetime(v["datetime"]).dt.tz_localize(C.IST)
                c = v[["open", "high", "low", "close", "volume"]].astype(float)
    except Exception:
        pass
    all_days = sorted(set(days_avail) | set(vault_days))
    d = day or days_avail[-1]
    df = I.add_indicators(c[c.index.date <= _date.fromisoformat(d)])
    today = df[df.index.date == _date.fromisoformat(d)]
    hist = df[df.index.date < _date.fromisoformat(d)]
    if today.empty:
        raise ValueError(f"No candles saved for {d}.")
    spot = float(today["close"].iloc[-1])
    lv = {}
    if not hist.empty:
        last = hist[hist.index.date == hist.index[-1].date()]
        ph, pl_, pc = float(last["high"].max()), float(last["low"].min()), float(last["close"].iloc[-1])
        pv = (ph + pl_ + pc) / 3
        bc = (ph + pl_) / 2
        tc = 2 * pv - bc
        lv = {"prev_high": ph, "prev_low": pl_, "prev_close": pc, "pivot": pv, "r1": 2 * pv - pl_, "s1": 2 * pv - ph,
              "r2": pv + (ph - pl_), "s2": pv - (ph - pl_), "cpr_top": max(tc, bc), "cpr_bottom": min(tc, bc)}
        cam = S.camarilla_levels(hist) or {}
        lv.update({f"cam_{k}": v for k, v in cam.items() if k != "pivot"})
        fib = S.fib_levels(hist) or {}
        lv.update({"fib_up": fib.get("h4"), "fib_dn": fib.get("l4")})
        wk = hist[hist.index >= hist.index[-1] - pd.Timedelta(days=7)]
        lv.update({"week_high": float(wk["high"].max()), "week_low": float(wk["low"].min())})
    orr = I.opening_range(today)
    if orr:
        lv.update({"or_high": float(orr[0]) if isinstance(orr, (tuple, list)) else None, "or_low": float(orr[1]) if isinstance(orr, (tuple, list)) else None})
    lv = {k: (round(float(v), 1) if v is not None else None) for k, v in lv.items()}
    bands = []
    try:
        sigma = S.noise_sigma(hist)
        if sigma and lv.get("prev_close"):
            b = S.noise_bands(today, lv["prev_close"], sigma)
            bands = [{"time": int(ts.timestamp()) + 19800, "up": round(float(r["up"]), 1), "lo": round(float(r["lo"]), 1)}
                     for ts, r in b.iterrows() if pd.notna(r["up"])]
    except Exception:
        pass
    prev = hist[hist.index.date == hist.index[-1].date()] if not hist.empty else hist
    show = pd.concat([prev, today]) if len(prev) else today
    candles = [{"time": int(ts.timestamp()) + 19800, "open": round(float(r["open"]), 2), "high": round(float(r["high"]), 2), "low": round(float(r["low"]), 2),
                "close": round(float(r["close"]), 2), "vwap": None if ts.date() != today.index[0].date() or pd.isna(r.get("vwap")) else round(float(r["vwap"]), 2),
                "ema9": round(float(r["ema9"]), 2), "ema21": round(float(r["ema21"]), 2)} for ts, r in show.iterrows()]
    # ---- setups of the day
    acct, pos, tr, cash = P.snapshot()
    eq = P.equity(acct, tr)
    tsec = lambda iso: int(datetime.fromisoformat(str(iso)).timestamp()) + 19800 if iso else None   # noqa: E731
    try:
        jd = J.day(d)
    except Exception:
        jd = {"decisions": {"rows": []}, "recommendations": []}
    setups = []
    for r in (jd.get("decisions") or {}).get("rows", []):
        ix = r.get("index") or {}
        if ix.get("entry") is None:
            continue
        st = r["status"]
        tv = st.get("trade") or {}
        setups.append({"kind": "signal", "time": tsec(f"{d}T{r['time']}:00") if r.get("time") else None, "symbol": r["symbol"], "strategy": r.get("strategy"),
                       "side": r.get("opt"), "entry": ix.get("entry"), "sl": ix.get("sl"), "target": ix.get("target"), "state": st["state"],
                       "label": st.get("label"), "msg": st.get("msg"), "what": r.get("what_happened"), "trade_id": tv.get("id")})
    books = [(p, True, "paper") for p in pos] + [(t, False, "paper") for t in tr if (t.get("opened") or "")[:10] == d]
    try:
        from . import scalp as SC
        books += [(p, True, "scalp") for p in SC.positions()] + [(t, False, "scalp") for t in SC.trades() if (t.get("opened") or "")[:10] == d]
    except Exception:
        pass
    trades_out = []
    for t, op, book in books:
        if t.get("symbol") != symbol or (t.get("opened") or "")[:10] != d:
            continue
        if book == "scalp":
            side = 1 if t.get("side", 1) > 0 else -1
            e_ = t.get("entry") if op else t.get("entry_spot")
            x = {"book": "scalp", "id": t["id"], "open": op, "opened": tsec(t.get("opened")), "closed": tsec(t.get("closed")), "side": "CE" if side > 0 else "PE",
                 "entry": e_, "sl": t.get("stop") if op else None, "initial_sl": (e_ - side * t["risk"]) if (e_ and t.get("risk")) else None,
                 "target": t.get("nl") if op else None, "trail_on": t.get("trail_on"), "exit": t.get("exit_spot"), "pnl": t.get("pnl"),
                 "reason": t.get("reason"), "strategy": f"scalp {t.get('tag')}", "lots": t.get("lots"), "qty": t.get("qty"), "entry_prem": t.get("entry_prem")}
        else:
            pl = t.get("plan") or {}
            x = {"book": "paper", "id": t["id"], "open": op, "opened": tsec(t.get("opened")), "closed": tsec(t.get("closed")), "side": pl.get("opt"),
                 "entry": t.get("spot_at_entry"), "sl": t.get("live_sl", pl.get("sl")) if op else pl.get("sl"), "initial_sl": pl.get("sl"),
                 "target": pl.get("target"), "breakeven": bool(t.get("at_breakeven")), "target_hit": bool(t.get("target_hit")),
                 "exit": t.get("spot_exit"), "pnl": t.get("pnl"), "reason": t.get("reason"), "strategy": pl.get("strategy") or "manual",
                 "auto": bool(pl.get("auto")), "lots": t.get("lots"), "qty": t.get("qty"), "entry_prem": t.get("entry_prem"),
                 "sl_prem": t.get("sl_prem"), "target_prem": t.get("target_prem")}
        if op and x.get("entry_prem") and x.get("qty"):
            paid = float(x["entry_prem"]) * float(x["qty"])
            to_sl = (float(x["entry_prem"]) - float(x.get("sl_prem") or 0)) * float(x["qty"]) if x.get("sl_prem") else None
            x["risk"] = {"premium_paid": round(paid), "worst_case_pct": round(paid / eq * 100, 1) if eq else None,
                         "to_stop": round(to_sl) if to_sl is not None else None, "to_stop_pct": round(to_sl / eq * 100, 2) if to_sl and eq else None,
                         "sl_pts": round(abs(float(x["entry"]) - float(x["sl"])), 1) if x.get("entry") and x.get("sl") else None}
        trades_out.append(x)
    live = None
    if d == C.today_ist().isoformat() and C.MARKET_OPEN <= C.now_ist().time() <= C.MARKET_CLOSE:
        try:
            g, ev, order, _ = scan(symbol, m)
            pl = ev.get("plan")
            live = {"signal": ev.get("signal"), "checks": [[ok, t] for ok, t in (ev.get("checks") or [])][:10],
                    "plan": {k: pl.get(k) for k in ("strategy", "opt", "entry", "sl", "target", "risk_pts", "rr", "signal_bar")} if pl else None,
                    "order": {k: order.get(k) for k in ("strike", "lots", "entry_prem", "sl_prem", "target_prem", "cost_per_lot", "risk_per_lot")} if order else None,
                    "warnings": ev.get("warnings") or []}
            if live["order"] and eq:
                o_ = live["order"]
                live["risk"] = {"to_stop": round((o_.get("risk_per_lot") or 0) * (o_.get("lots") or 0)),
                                "to_stop_pct": round((o_.get("risk_per_lot") or 0) * (o_.get("lots") or 0) / eq * 100, 2),
                                "worst_case": round((o_.get("cost_per_lot") or 0) * (o_.get("lots") or 0)),
                                "worst_case_pct": round((o_.get("cost_per_lot") or 0) * (o_.get("lots") or 0) / eq * 100, 1)}
        except Exception as e:
            live = {"error": str(e)[:160]}
    try:
        from . import scalp as SC
        sz = SC.state(symbol, m).get("zones") if d == C.today_ist().isoformat() else None
    except Exception:
        sz = None
    return {"symbol": symbol, "label": M.instrument(symbol)["label"], "date": d, "days": all_days[-60:], "spot": spot,
            "session_start": int(today.index[0].timestamp()) + 19800, "candles": candles, "levels": lv, "bands": bands,
            "zones": _zones(hist, today, spot), "scalp_zones": sz, "setups": setups, "trades": trades_out, "live": live,
            "shield": shield_state(acct, pos, tr), "equity": round(eq), "live_day": d == C.today_ist().isoformat()}


def _mini_chain(ch, spot, width=6):
    atm_i = int((ch["strike"] - spot).abs().values.argmin())
    view = ch.iloc[max(0, atm_i - width): atm_i + width + 1]
    cols = ["strike"] + [f"{s}_{f}" for s in ("ce", "pe") for f in ("ltp", "oi", "chg_oi", "iv", "delta")]
    return float(ch["strike"].iloc[atm_i]), view.reindex(columns=cols).to_dict("records")


def _last_session_date(now):
    d = now.date() if now.time() >= C.MARKET_OPEN else now.date() - timedelta(days=1)
    while d.weekday() >= 5 or d in C.HOLIDAYS:
        d -= timedelta(days=1)
    return d


def health(m, candles, now) -> list[dict]:
    """A few honest lights: is the price data current, is the broker login healthy, are the schedules ticking."""
    out = []
    try:
        last = candles.index[-1]
        open_now = now.weekday() < 5 and now.date() not in C.HOLIDAYS and C.MARKET_OPEN <= now.time() <= C.MARKET_CLOSE
        if open_now:
            lag = (now - last).total_seconds() / 60
            st = "ok" if lag <= 12 else "warn" if lag <= 25 else "bad"
            out.append({"label": "Candles", "state": st, "text": f"latest bar {last:%H:%M} ({lag:.0f} min old)"})
        else:
            want = _last_session_date(now)
            st = "ok" if last.date() >= want else "bad"
            out.append({"label": "Candles", "state": st, "text": f"last session {last:%d %b}" + ("" if st == "ok" else f" - expected {want:%d %b}")})
    except Exception:
        out.append({"label": "Candles", "state": "bad", "text": "no data"})
    if not m.live:
        out.append({"label": "Broker", "state": "bad", "text": "not logged in - demo prices"})
    else:
        kind = (getattr(m, "token_kind", {}) or {}).get(m.br.name, "daily")
        if kind == "analytics":
            cfg = store.get("upstox") or {}
            left = B.analytics_days_left(cfg.get("analytics_created"))
            out.append({"label": "Broker", "state": "ok" if (left or 0) > 14 else "warn", "text": f"{m.br.name.title()} one-year data token, {left} days left"})
        else:
            cut = now.replace(hour=3, minute=30, second=0, microsecond=0)
            if now >= cut:
                cut += timedelta(days=1)
            hrs = (cut - now).total_seconds() / 3600
            out.append({"label": "Broker", "state": "ok" if hrs > 3 else "warn", "text": f"{m.br.name.title()} login runs out in {hrs:.0f}h (about 03:30)"})
    try:
        lt = store.get_raw("fno:last_tick")
        if lt:
            t = datetime.fromisoformat(lt)
            ago = (now - t).total_seconds() / 60
            expect = now.weekday() < 5 and now.date() not in C.HOLIDAYS and C.MARKET_OPEN <= now.time() <= C.MARKET_CLOSE
            st = "ok" if (not expect or ago <= 5) else "warn" if ago <= 15 else "bad"
            out.append({"label": "Auto-check", "state": st, "text": f"last ran {t:%a %H:%M}" + (f" ({ago:.0f} min ago)" if expect else "")})
        else:
            out.append({"label": "Auto-check", "state": "warn", "text": "no run recorded yet (starts at the next market open)"})
    except Exception:
        pass
    return out


def dashboard(symbol, strike=None):
    m = M.Market()
    closed = manage_positions(m)
    g = _gather(symbol, m)
    acct, pos, tr, cash = P.snapshot()
    cstats = M.chain_stats(g["chain"], g["spot"])
    gc, nw, fi = gcues(), news(symbol), fii()
    pf = eprefs()
    guard = risk_guard(pos, tr, P.base(acct, tr), pf, equity=P.equity(acct, tr))
    if not guard.get("blocked"):
        guard = calendar_guard(symbol, g["exps"], g["now"].date()) or guard
    traded = _traded_today(symbol, pos, tr)
    ev = S.evaluate(symbol, g["candles"], g["vix"], cstats, nw, gc, fi, g["now"], guard, traded, pf["strategies"])
    ev = _with_ai(symbol, g, ev, pf, traded)
    ev = _with_custom(symbol, g, ev, pf, traded)
    _untested_note(symbol, g, ev)
    today, hist = S.session_frames(g["candles"], g["now"])
    prev = float(hist["close"].iloc[-1]) if not hist.empty else g["spot"]

    order, alts = None, []
    if ev["plan"]:
        if any(p["symbol"] == symbol and p["mode"] == "signal" for p in pos):
            ev["warnings"].append("You already have an open signal trade in this instrument.")
        args = (ev["plan"], g["chain"], g["spot"], g["vix"]["level"], g["lot"], P.base(acct, tr))
        try:
            order = S.build_order(*args, strike=strike, cash=cash, prefs=pf)
        except ValueError:
            order = S.build_order(*args, cash=cash, prefs=pf)
        alts = S.strike_alternatives(*args, cash=cash, prefs=pf)
    atm, chain_rows = _mini_chain(g["chain"], g["spot"])
    ev.pop("today", None)
    if ev.get("bands"):
        ev["bands"]["series"] = [{"time": int(x["ts"].timestamp()) + 19800, "up": x["up"], "lo": x["lo"]}
                                 for x in ev["bands"]["series"]]
    return clean({
        "symbol": symbol, "label": g["meta"]["label"], "source": m.source, "live": m.live,
        "time": g["now"].strftime("%H:%M:%S"), "spot": g["spot"], "prev_close": prev, "health": health(m, g["candles"], g["now"]),
        "vix": g["vix"], "expiry": g["trade_exp"], "lot": g["lot"], "cash": cash,
        "chain_source": g["chain"].attrs.get("source", "live"),
        "signal": ev, "order": order, "alternatives": alts, "atm": atm, "chain": chain_rows,
        "chart": _chart(today, hist), "closed_now": closed,
        "errors": m.errors[-3:],
    })


def chain_view(symbol, expiry: str | None = None):
    m = M.Market()
    g = _gather(symbol, m)
    exp = date.fromisoformat(expiry) if expiry else g["trade_exp"]
    ch = g["chain"] if exp == g["trade_exp"] else m.chain(symbol, exp, g["spot"], g["vix"]["level"], g["candles"])
    spot = g["spot"]
    atm_i = int((ch["strike"] - spot).abs().values.argmin())
    view = ch.iloc[max(0, atm_i - 10): atm_i + 11]
    cols = ["strike"] + [f"{s}_{f}" for s in ("ce", "pe") for f in ("ltp", "bid", "ask", "oi", "chg_oi", "vol", "iv", "delta")]
    return clean({
        "symbol": symbol, "spot": spot, "expiry": exp, "expiries": g["exps"][:8], "lot": g["lot"],
        "atm": float(ch["strike"].iloc[atm_i]), "source": ch.attrs.get("source", "live"),
        "stats": M.chain_stats(ch, spot), "rows": view.reindex(columns=cols).to_dict("records"),
        "errors": m.errors[-3:],
    })


# ---------------------------------------------------------------------------
# Trading actions
# ---------------------------------------------------------------------------
_place_locks: dict = {}
_place_locks_guard = threading.Lock()


def _db_lock(name, ttl):
    """Cross-process lock in the database (SET NX EX): the QStash tick, a page click and a second worker during a deploy
    cannot run the same critical step at once. Returns a token (release with _db_unlock) or None if someone holds it."""
    tok = uuid.uuid4().hex
    try:
        return tok if store._cmd("SET", f"fno:lock:{name}", tok, "NX", "EX", int(ttl)) is not None else None
    except Exception:
        return tok                                   # database briefly unreachable: the process lock still protects us


def _db_unlock(name, tok):
    try:
        if store._cmd("GET", f"fno:lock:{name}") == tok:
            store._cmd("DEL", f"fno:lock:{name}")
    except Exception:
        pass                                         # it expires on its own


_LAST_PLAN: dict = {}          # (symbol, slot) -> the plan the last automatic attempt evaluated (for the journal)


def shield_state(acct=None, pos=None, tr=None, pf=None) -> dict:
    """Capital shield: the hard account-protection caps applied to every automatic entry (F&O book).
      floor  - equity below floor% of the starting capital: no new automatic trades until you reset or lower it
      open   - premium paid for all open positions (the most bought options can lose) capped at open% of equity
      trade  - one trade's planned loss (entry to stop) capped at trade% of equity
    With open <= 50% and floor at 60%, even every open option expiring worthless after the floor leaves >= 30% of the
    starting capital: the account cannot be emptied by the automatic system."""
    if acct is None:
        acct, pos, tr, _ = P.snapshot()
    pf = pf or prefs()
    eq = P.equity(acct, tr)
    start = float(acct.get("capital_start") or 0) or float(acct.get("capital_before") or 0) or eq
    floor = start * float(pf.get("shield_floor_pct") or 0) / 100
    used = sum(float(p.get("margin") or 0) for p in pos)
    cap = eq * float(pf.get("shield_open_pct") or 100) / 100
    return {"on": bool(pf.get("shield_on", True)), "equity": round(eq), "start": round(start), "floor": round(floor),
            "below_floor": bool(pf.get("shield_on", True)) and eq < floor, "open_premium": round(used), "open_cap": round(cap),
            "open_room": round(max(cap - used, 0)), "trade_cap": round(eq * float(pf.get("shield_trade_pct") or 100) / 100),
            "floor_pct": float(pf.get("shield_floor_pct") or 0), "open_pct": float(pf.get("shield_open_pct") or 0), "trade_pct": float(pf.get("shield_trade_pct") or 0)}


def _shield_lots(order, sh) -> tuple[int, str | None]:
    """Lots allowed by the shield for this order (0 = skip) and the reason when it cut or blocked the trade."""
    if not sh["on"]:
        return order["lots"], None
    if sh["below_floor"]:
        return 0, (f"Capital shield: equity ₹{sh['equity']:,.0f} is below the floor ₹{sh['floor']:,.0f} ({sh['floor_pct']:.0f}% of the "
                   f"starting ₹{sh['start']:,.0f}). Automatic trading is stopped - reset the account or lower the floor in Autopilot → Safety.")
    lots = int(order["lots"])
    cost = float(order.get("cost_per_lot") or 0)
    risk = float(order.get("risk_per_lot") or 0)
    why = []
    if cost > 0 and lots * cost > sh["open_room"]:
        lots = int(sh["open_room"] // cost)
        why.append(f"open premium ₹{sh['open_premium']:,.0f} + this trade would pass {sh['open_pct']:.0f}% of equity (₹{sh['open_cap']:,.0f})")
    if risk > 0 and lots * risk > sh["trade_cap"]:
        lots = min(lots, int(sh["trade_cap"] // risk))
        why.append(f"planned loss above {sh['trade_pct']:.0f}% of equity (₹{sh['trade_cap']:,.0f}; 1 lot risks ₹{risk:,.0f})")
    if lots <= 0:
        return 0, "Capital shield: skipped - " + "; ".join(why) + "."
    return lots, ("Capital shield: lots cut to %d - %s." % (lots, "; ".join(why))) if lots < order["lots"] else None


def place_signal(symbol, strike=None, auto=False, which=None):
    """One entry at a time per index (review 28 Oct): two overlapping ticks, or a tick and a click, could both pass the
    'already have an open trade' check and open the same trade twice."""
    with _place_locks_guard:
        lk = _place_locks.setdefault(symbol, threading.Lock())
    if not lk.acquire(timeout=40):
        return False, "Another order for this instrument is being placed - try again in a moment."
    try:
        tok = _db_lock(f"place:{symbol}", 45)
        if tok is None:
            return False, "Another order for this instrument is being placed - try again in a moment."
        try:
            return _place_signal(symbol, strike=strike, auto=auto, which=which)
        finally:
            _db_unlock(f"place:{symbol}", tok)
    finally:
        lk.release()


def _data_problem(g) -> str | None:
    """Automatic entries need real data: an assumed VIX (14.0 when the feed fails) or 5-minute candles older than 20
    minutes would size and filter trades on numbers that are not today's market."""
    if g["vix"].get("assumed"):
        return "Skipped: India VIX is unavailable (14.0 would be assumed), so the VIX filters cannot be checked."
    try:
        last = g["candles"].index[-1]
        age = (g["now"] - last.to_pydatetime().astimezone(g["now"].tzinfo)).total_seconds() / 60 - 5
        if age > 20:
            return f"Skipped: the latest 5-minute candle is {age:.0f} minutes old - waiting for fresh prices."
    except Exception:
        return "Skipped: no price candles available."
    return None


def _place_signal(symbol, strike=None, auto=False, which=None):
    """which=None: the main slot (Noise / Camarilla / AI / custom). which="trend": the trend-day catcher's own slot (round 23),
    which may hold one position while a main-slot trade is open in the same index."""
    m = M.Market()
    g = _gather(symbol, m)
    if auto:
        bad = _data_problem(g)
        if bad:
            return False, bad
    acct, pos, tr, cash = P.snapshot()
    pf = eprefs()
    guard = risk_guard(pos, tr, P.base(acct, tr), pf, auto=auto, equity=P.equity(acct, tr))
    if not guard.get("blocked"):
        guard = calendar_guard(symbol, g["exps"], g["now"].date()) or guard
    if guard.get("blocked"):
        return False, guard["reason"]
    slot_of = lambda p: (p.get("plan") or {}).get("strategy") if (p.get("plan") or {}).get("strategy") in C.SLOTS else None   # noqa: E731
    if any(p["symbol"] == symbol and p["mode"] == "signal" and slot_of(p) == which for p in pos):
        return False, ({"trend": "You already have an open trend-day trade in this instrument.",
                        "fib": "You already have an open Fibonacci-breakout trade in this instrument."}.get(which)
                       or "You already have an open signal trade in this instrument.")
    cstats = M.chain_stats(g["chain"], g["spot"])
    traded = _traded_today(symbol, pos, tr)
    strats = sym_strategies(pf, symbol)
    if which == "trend" and (("trend" not in strats) or not trend_allowed(pf, P.equity(acct, tr))):
        return False, ("Trend-day catcher is off for this instrument." if "trend" not in strats else
                       f"Autopilot adds the trend-day catcher once equity reaches ₹{C.TREND_MIN_EQUITY:,.0f}.")
    if which == "fib" and ("fib" not in strats or not fib_allowed(pf, P.equity(acct, tr))):
        return False, ("Fibonacci breakout is off for this instrument." if "fib" not in strats else
                       f"Autopilot adds the Fibonacci breakout once equity reaches ₹{C.FIB_MIN_EQUITY:,.0f}.")
    if which is None and _trend_only(pf, symbol):
        return False, "Only the own-slot rules trade this instrument."
    ev = _evaluate_once(symbol, g, cstats, guard, traded, strats)
    if which in C.SLOTS:
        tv = ev.get(which) or {}
        ev = {**ev, "plan": tv.get("plan"), "signal": tv.get("signal", "WAIT")}
    else:
        ev = _with_ai(symbol, g, ev, pf, traded)
        ev = _with_custom(symbol, g, ev, pf, traded)
    if not ev["plan"]:
        return False, f"No valid signal right now ({ev['signal']}). The setup may have changed - refresh."
    if auto:
        _LAST_PLAN[(symbol, which or "main")] = {k: ev["plan"].get(k) for k in ("strategy", "signal_bar", "opt", "entry", "sl", "target", "risk_pts", "rr", "exp_r", "p_win", "trail")}
        _LAST_PLAN[(symbol, which or "main")]["signal"] = ev.get("signal")
    if auto and not _custom_auto_ok(ev["plan"]):
        return False, "The signal comes from a custom strategy that is not switched to auto-trade (you can still place it by hand)."
    if auto:                                                        # the learning engine's guarded rules apply to automatic entries only
        why, mult = _learn_gate(g, ev["plan"])
        if why:
            return False, why
        if mult < 1:
            pf = {**pf, "risk_pct": float(pf.get("risk_pct") or 1.0) * mult}
        if pf.get("autopilot") and flag("big_run_skip") and ev["plan"].get("strategy") in ("noise", "camarilla", "trend", "fib"):
            try:
                run = S.big_run(S.session_frames(g["candles"], g["now"])[1])
            except Exception:
                run = None
            if S.against_big_run(run, ev["plan"].get("opt")):
                return False, (f"Skipped (round 42): this {('CALL' if ev['plan'].get('opt') == 'CE' else 'PUT')} bets against a "
                               f"{'rally' if run['dir'] > 0 else 'fall'} of {abs(run['move3']):,.0f} points in 3 sessions "
                               f"({run['k']:.1f}x the daily ATR). Such trades lost in every test period.")
    rp0 = float(pf.get("risk_pct") or 1.0)
    conv = _conviction(symbol, g, ev["plan"], pf)
    if conv:
        pf = {**pf, "risk_pct": rp0 * conv["mult"]}
        ev = {**ev, "plan": {**ev["plan"], "conviction": conv}}
    jd = None if pf.get("confidence") == "strong" else _ai_judge(symbol, g, ev["plan"], pf, which, m)   # strong mode was tested without the AI judge
    if jd:                                                          # round 39: on top of conviction, capped 0.5x - 1.6x
        lo, hi = AJ.meta()["clip"]
        jd["total_mult"] = round(min(max((conv["mult"] if conv else 1.0) * jd["mult"], lo), hi), 3)
        pf = {**pf, "risk_pct": rp0 * jd["total_mult"]}
        ev = {**ev, "plan": {**ev["plan"], "ai_judge": jd}}
    if pf.get("autopilot") and flag("dd_half"):                      # round 43: half risk while deep below the peak
        try:
            eq_now, pk = P.equity(acct, tr), P.peak_equity(acct, tr)
            if pk > 0 and eq_now < (1 - C.DD_HALF_AT) * pk:
                pf = {**pf, "risk_pct": float(pf.get("risk_pct") or 1.0) * 0.5}
                ev = {**ev, "plan": {**ev["plan"], "dd_half": {"equity": round(eq_now), "peak": round(pk)}}}
        except Exception:
            pass
    if strike is None:
        strike = _small_account_strike(ev["plan"], g["chain"], g["spot"], pf, P.equity(acct, tr))
        if strike is not None:
            ev = {**ev, "plan": {**ev["plan"], "small_account_otm": True}}
    try:
        order = S.build_order(ev["plan"], g["chain"], g["spot"], g["vix"]["level"], g["lot"], _size_capital(acct, tr, pf),
                              strike=strike, cash=cash, prefs=pf)
    except ValueError as e:
        return False, str(e)
    if order["lots"] <= 0:
        return False, order["note"] or "Position size is zero."
    if auto:
        lots_ok, why = _shield_lots(order, shield_state(acct, pos, tr, pf))
        if lots_ok <= 0:
            return False, why
        if lots_ok < order["lots"]:
            order = {**order, "lots": lots_ok, "note": ((order.get("note") or "") + " " + why).strip()}
    src = m.br.name if g["chain"].attrs.get("source") == "live" and m.br else "demo"
    legs = [{**lg, "src": src} for lg in order["legs"]]
    return P.open_position(cash, symbol, legs, order["lots"], g["lot"], g["trade_exp"], g["spot"], m.source,
                           clean(order), clean({**ev["plan"], "auto": auto, "ctx": _entry_ctx(g)}))


_EV_MEMO: dict = {}


def _evaluate_once(symbol, g, cstats, guard, traded, strats):
    """S.evaluate computes every rule at once (main, trend-day, Fibonacci). The tick asks for each slot separately within the
    same second on the same candles, so the result is reused while the inputs are identical (was: 3 evaluations a minute
    per index). Any change - a new candle, a trade placed (traded/guard change) - gives a fresh evaluation."""
    import json as _json
    key = (symbol, id(g["candles"]), len(g["candles"]), g["now"].strftime("%Y%m%d%H%M"), round(float(g["vix"].get("level") or 0), 2),
           _json.dumps(guard, sort_keys=True, default=str), _json.dumps(traded, sort_keys=True, default=str), tuple(strats or ()))
    hit = _EV_MEMO.get(key)
    if hit and _time.time() - hit[0] < 30:
        return copy.deepcopy(hit[1])
    ev = S.evaluate(symbol, g["candles"], g["vix"], cstats, news(symbol), gcues(), fii(), g["now"], guard, traded, strats)
    if len(_EV_MEMO) > 30:
        _EV_MEMO.clear()
    _EV_MEMO[key] = (_time.time(), copy.deepcopy(ev))
    return ev


def _small_account_strike(plan, chain, spot, pf, equity):
    """Round 38: below SMALL_OTM_BELOW equity, Autopilot buys 1 strike out of the money instead of 1 in the money. The cheaper lot
    fits the cash and the 15% one-lot risk check far more often, so a small account keeps trading through a dip instead of sitting
    out the big trend days. None = use the normal rule."""
    if not pf.get("autopilot") or equity >= C.SMALL_OTM_BELOW or not flag("small_otm"):
        return None
    try:
        strikes = chain["strike"].values
        atm_i = int(abs(strikes - spot).argmin())
        i = atm_i + (C.SMALL_OTM_STRIKES if plan["opt"] == "CE" else -C.SMALL_OTM_STRIKES)
        return float(strikes[i]) if 0 <= i < len(strikes) else None
    except Exception:
        return None


# ---------------- recent changes the user can switch back (Research tab -> Recent changes)
FLAGS_KEY = "change_flags"


def flag(name: str) -> bool:
    """True = the change is in use (default); False = switched back to how it worked before."""
    try:
        return bool((store.get(FLAGS_KEY) or {}).get(name, True))
    except Exception:
        return True


def set_flag(name: str, on: bool):
    f = store.get(FLAGS_KEY) or {}
    f[name] = bool(on)
    store.put(FLAGS_KEY, f)


def changes() -> list[dict]:
    """The recent changes, with their state, for the Research tab."""
    acct = P.account(); pf = prefs()
    try:
        removed = P.origin_summary()["removed_total"]
    except Exception:
        removed = 0
    po = float(acct.get("capital_start") or 0) <= 0 and acct.get("capital_before")
    return [
        {"id": "crypto_book_r64", "date": "2026-10-29", "title": "Crypto book: Donchian 55 4h retired, 2x leverage (crypto round 17)",
         "what": "The crypto autopilot no longer opens Donchian 55 breakout (4h) trades, and uses 2x leverage unless you set leverage yourself. Risk per trade stays 1%: leverage only frees cash, so fewer good signals are skipped. In the money replay with the weekly re-test (Rs 2L, 2023 - Oct 2026) this made Rs 2.65L vs 2.17L, better both before and after Apr 2025, worst fall -26% vs -30%. Open Donchian 55 trades are still managed to their exit.",
         "before": "Four crypto rules (incl. Donchian 55 4h) at 1x leverage.", "on": flag("crypto_book_r64"), "kind": "switch"},
        {"id": "dd_half", "date": "2026-10-25", "title": "Half risk in a deep drawdown (round 43)",
         "what": f"While the account is more than {C.DD_HALF_AT:.0%} below its highest value, Autopilot risks half as much per trade until it recovers. Same typical year, fewer deep falls.",
         "before": "Full risk whatever the drawdown.", "on": flag("dd_half"), "kind": "switch"},
        {"id": "big_run_skip", "date": "2026-10-24", "title": "Skip trades against a big 3-day run (round 42)",
         "what": f"Autopilot does not buy a CALL after a 3-session fall, or a PUT after a 3-session rally, of at least {C.BIG_RUN_ATR:g}x the daily ATR (~350-450 Nifty points). Those trades lost in 2023-25 on all three indices and in 2026.",
         "before": "Every signal traded, whatever the last 3 days did.", "on": flag("big_run_skip"), "kind": "switch"},
        {"id": "ai_judge", "date": "2026-10-23", "title": "AI judge sizes Nifty signals (round 39)",
         "what": "A small AI model scores each Noise / Camarilla / trend / Fibonacci signal on Nifty from 64 market features of the signal candle. Autopilot risks 1.33x on the better half and 0.67x on the weaker half, on top of conviction sizing (total kept between 0.5x and 1.6x).",
         "before": "Only conviction sizing (1.33x / 0.67x by the 5-min / 15-min / 1-hour charts).", "on": flag("ai_judge"), "kind": "switch"},
        {"id": "small_otm", "date": "2026-10-23", "title": "Small account: 1 strike out of the money (round 38)",
         "what": f"While equity is below ₹{C.SMALL_OTM_BELOW:,.0f}, Autopilot buys the strike 1 step out of the money. The cheaper lot passes the cash and 15% risk checks more often, so a small account keeps trading after a dip.",
         "before": "Always 1 strike in the money.", "on": flag("small_otm"), "kind": "switch"},
        {"id": "conviction", "date": "2026-10-21", "title": "Conviction sizing (round 33)",
         "what": "Autopilot risks 1.33% when the 5-min, 15-min and 1-hour charts agree with the trade (score 5-6) and 0.67% when they don't, on Nifty and Sensex.",
         "before": "Every trade risks 1% of equity.", "on": flag("conviction"), "kind": "switch"},
        {"id": "limits_system_only", "date": "2026-10-21", "title": "Loss limits count only the system's own trades",
         "what": "Autopilot's weekly and daily loss limits ignore your manual trades, so a manual loss can't stop the system.",
         "before": "Every trade in the account, manual ones included, counts towards the limits.", "on": flag("limits_system_only"), "kind": "switch"},
        {"id": "weekly_limit", "date": "2026-10-21", "title": "Your weekly loss limit choice",
         "what": {"auto": "Tested rule (4% of equity, 15% below ₹1 lakh).", "off": "Off - never stops for the week.",
                  "custom": f"Your own amount: ₹{float(pf.get('weekly_limit_rs') or 0):,.0f} a week."}[pf.get("weekly_limit") or "auto"],
         "before": "Tested rule (4% of equity, 15% below ₹1 lakh).", "on": (pf.get("weekly_limit") or "auto") != "auto", "kind": "action",
         "action": "Back to the tested rule"},
        {"id": "profit_only", "date": "2026-10-21", "title": "Capital 0 - trading only the profit",
         "what": f"The starting ₹{float(acct.get('capital_before') or 0):,.0f} is taken out; the account trades its profit only." if po else "Not in use.",
         "before": f"Capital back to ₹{float(acct.get('capital_before') or 0):,.0f}." if po else "", "on": bool(po), "kind": "action",
         "action": "Put the starting capital back"},
        {"id": "removed_trades", "date": "2026-10-21", "title": "Your own trades taken out of the P&L",
         "what": f"{removed} trade(s) kept aside, not counted in P&L or stats." if removed else "Not in use.",
         "before": "All trades counted.", "on": bool(removed), "kind": "action", "action": "Put them back"},
    ]


def revert_change(cid: str, on: bool | None = None) -> str:
    if cid in ("conviction", "limits_system_only", "small_otm", "ai_judge", "big_run_skip", "dd_half", "crypto_book_r64"):
        set_flag(cid, bool(on))
        return ("Switched on again." if on else "Switched back to how it worked before.")
    if cid == "weekly_limit":
        cur = store.get("prefs") or {}; cur["weekly_limit"] = "auto"; store.put("prefs", cur)
        return "Weekly loss limit back to the tested rule."
    if cid == "profit_only":
        a = P.account()
        if float(a.get("capital_start") or 0) > 0 or not a.get("capital_before"):
            return "Capital is not set to 0 - nothing to change."
        store.put("account", {**{k: v for k, v in a.items() if k not in ("capital_before", "profit_only_since")}, "capital_start": float(a["capital_before"])})
        return f"Starting capital back to ₹{float(a['capital_before']):,.0f}."
    if cid == "removed_trades":
        return f"Restored {P.restore_removed()['restored']} trade(s)."
    raise ValueError("Unknown change.")


def _conviction(symbol, g, plan, pf) -> dict | None:
    """Round 33: Autopilot sizes Nifty / Sensex trades by the 5-min / 15-min / 1-hour agreement score (1.33x risk on 5-6 of 6,
    0.67x below). None when it does not apply or the score cannot be worked out (the trade then keeps its normal size)."""
    strong = pf.get("confidence") == "strong"
    if not pf.get("autopilot") or (symbol not in C.CONVICTION_SYMS and not strong) or (plan or {}).get("strategy") not in C.CONVICTION_STRATS or not flag("conviction"):
        return None
    try:
        today, hist = S.session_frames(g["candles"], g["now"])
        c5 = pd.concat([hist, today])[["open", "high", "low", "close"]]
        if len(c5) < 100:
            return None
        if getattr(c5.index, "tz", None) is not None:
            c5.index = c5.index.tz_localize(None)
        side = 1 if plan.get("opt") == "CE" else -1
        sc = S.mtf_score_at(S.mtf_frames(c5), c5.index[-1] + pd.Timedelta(minutes=C.CANDLE_MIN), side)
        if strong:                                                  # round 42 opt-in: 2x / 1x / 0.5x on every index
            return {"score": int(sc), "mult": next(m for lo, m in C.STRONG_CONF if sc >= lo), "mode": "strong"}
        return {"score": int(sc), "mult": S.conviction_mult(sc)}
    except Exception:
        return None


def _ai_judge(symbol, g, plan, pf, which=None, m=None) -> dict | None:
    """Round 39: the AI judge scores the rule's own signal (features of the signal bar, turned to the trade's side). Autopilot,
    Nifty, Noise / Camarilla / trend / Fibonacci only. None when it does not apply (the trade keeps its conviction size)."""
    try:
        if not pf.get("autopilot") or not flag("ai_judge") or not AJ.available():
            return None
        st = (plan or {}).get("strategy")
        if symbol not in AJ.meta()["syms"] or st not in ("noise", "camarilla", "trend", "fib"):
            return None
        f = AJ.bar_features(symbol, g["candles"], g["vix"], g["now"], g["trade_exp"], plan.get("signal_bar"),
                            (gcues() or {}).get("avg_equity_chg"), _vix_prev_chg(), vix_series=m.vix_candles() if m else None)
        if f is None:
            return None
        return AJ.judge(f, 1 if plan.get("opt") == "CE" else -1, which if which in ("trend", "fib") else "main", st)
    except Exception:
        return None


def place_manual(symbol, expiry, strike, opt, side, lots):
    if side not in ("BUY", "SELL") or opt not in ("CE", "PE"):
        return False, "Invalid order."
    m = M.Market()
    g = _gather(symbol, m)
    exp = date.fromisoformat(expiry) if expiry else g["trade_exp"]
    ch = g["chain"] if exp == g["trade_exp"] else m.chain(symbol, exp, g["spot"], g["vix"]["level"], g["candles"])
    row = ch[ch.strike == float(strike)]
    if row.empty:
        return False, "Strike not found in the option chain."
    row = row.iloc[0]
    t = opt.lower()
    px = row.get(f"{t}_ask") if side == "BUY" else row.get(f"{t}_bid")
    if px is None or pd.isna(px) or px <= 0:
        px = row.get(f"{t}_ltp")
    if px is None or pd.isna(px) or px <= 0:
        return False, "No price available for this strike."
    src = m.br.name if ch.attrs.get("source") == "live" and m.br else "demo"
    leg = {"side": side, "strike": float(strike), "opt": opt, "key": row[f"{t}_key"], "price": float(px), "src": src}
    _, _, _, cash = P.snapshot()
    return P.open_position(cash, symbol, [leg], int(lots), g["lot"], exp, g["spot"], m.source)


def candles(symbol, kind="UND", strike=None, expiry=None):
    """Candles for one chart pane: the index/stock itself (kind UND) or one option (CE/PE).
    Also returns the strikes around the money so the pane can offer a strike picker."""
    kind = (kind or "UND").upper()
    if kind != "UND":
        r = option_candles(symbol, expiry, strike, kind)
    else:
        m = M.Market()
        g = _gather(symbol, m)
        today, hist = S.session_frames(g["candles"], g["now"])
        r = clean({"symbol": symbol, "label": g["meta"]["label"], "expiry": g["trade_exp"], "strike": None,
                   "opt": "UND", "source": "live" if m.live else "delayed", "candles": _chart(today, hist),
                   "ltp": g["spot"], "errors": m.errors[-3:]})
        r["strikes"], r["atm"] = _near(g["chain"], g["spot"])
    r["kind"] = kind
    return r


def _near(ch, spot, width=12):
    i = int((ch["strike"] - spot).abs().values.argmin())
    view = ch.iloc[max(0, i - width): i + width + 1]
    return [float(x) for x in view["strike"]], float(ch["strike"].iloc[i])


def option_candles(symbol, expiry=None, strike=None, opt="CE"):
    """5-minute candles for one option contract next to the underlying.
    Live broker candles when connected; otherwise a theoretical series priced
    from the underlying candles (Black-Scholes, same model as demo prices)."""
    if opt not in ("CE", "PE"):
        raise ValueError("Option type must be CE or PE")
    m = M.Market()
    g = _gather(symbol, m)
    exp = date.fromisoformat(expiry) if expiry else g["trade_exp"]
    ch = g["chain"] if exp == g["trade_exp"] else m.chain(symbol, exp, g["spot"], g["vix"]["level"], g["candles"])
    if strike is None:
        strike = float(ch["strike"].iloc[int((ch["strike"] - g["spot"]).abs().values.argmin())])
    row = ch[ch.strike == float(strike)]
    if row.empty:
        raise ValueError(f"Strike {strike:g} is not in the option chain.")
    key = row.iloc[0][f"{opt.lower()}_key"]
    today, hist = S.session_frames(g["candles"], g["now"])
    under = pd.concat([hist.tail(75), today]) if not hist.empty else today

    out, source = [], "theoretical"
    if m.br and ch.attrs.get("source") == "live" and key and not str(key).startswith("DEMO|"):
        try:
            oc = M.cached(f"oc:{m.br.name}:{key}", 25, lambda: m.br.candles(key))
            oc = oc[oc.index >= under.index[0]] if not oc.empty else oc
            if not oc.empty:
                source = "live"
                for ts, r in oc.iterrows():
                    out.append({"time": int(ts.timestamp()) + 19800, "open": r["open"], "high": r["high"],
                                "low": r["low"], "close": r["close"]})
        except Exception as e:
            m.errors.append(f"Live option candles unavailable ({e}); showing theoretical prices.")
    if not out:
        vix = g["vix"]["level"]
        for ts, r in under.iterrows():
            t = I.years_to_expiry(exp, ts.to_pydatetime().replace(tzinfo=None) + pd.Timedelta(minutes=5))
            def f(spot_px):
                iv = M.demo_iv(symbol, float(strike), float(spot_px), vix, g["candles"])
                return I.bs_price(float(spot_px), float(strike), t, iv, opt)
            o, c, a, b = f(r["open"]), f(r["close"]), f(r["high"]), f(r["low"])
            out.append({"time": int(ts.timestamp()) + 19800, "open": round(o, 2), "high": round(max(a, b, o, c), 2),
                        "low": round(min(a, b, o, c), 2), "close": round(c, 2)})
    strikes, atm = _near(ch, g["spot"])
    return clean({"symbol": symbol, "label": g["meta"]["label"], "expiry": exp, "strike": float(strike), "opt": opt,
                  "source": source, "candles": out, "strikes": strikes, "atm": atm,
                  "ltp": row.iloc[0][f"{opt.lower()}_ltp"], "errors": m.errors[-3:]})


def exit_position(pos_id):
    m = M.Market()
    pos = [p for p in store.positions() if p["id"] == pos_id]
    if not pos:
        return False, "Position not found (it may have been closed already)."
    sp = {pos[0]["symbol"]: m.spot(pos[0]["symbol"])}
    prices = m.leg_prices(pos, sp, m.vix()["level"])
    rec = P.close_position(pos_id, m.exit_fills(pos, prices, eprefs().get("fills", "realistic")), "Manual exit",
                           extra={"spot_exit": sp[pos[0]["symbol"]]})
    return (True, f"Closed. Net P&L after charges ₹{rec['pnl']:,.0f}") if rec else (False, "Already closed.")


def portfolio():
    m = M.Market()
    closed = manage_positions(m)
    acct, pos, tr, cash = P.snapshot()
    prices, out = {}, []
    if pos:
        spots = {s: m.spot(s) for s in {p["symbol"] for p in pos}}
        prices = m.leg_prices(pos, spots, m.vix()["level"])
    unreal = 0.0
    for p in pos:
        u = P.unrealised(p, prices)
        unreal += u
        out.append({**p, "current_prem": round(P.net_price(p["legs"], prices), 2), "unrealised": round(u, 2),
                    "price_src": "formula" if any(l.get("price_src") == "formula" for l in p["legs"]) or not all(l.get("src", "demo") in m.clients for l in p["legs"])
                                 else "chain" if any(l.get("price_src") == "chain" for l in p["legs"]) else "live"})
    return clean({
        "capital_start": acct["capital_start"], "capital_before": acct.get("capital_before"), "profit_only_since": acct.get("profit_only_since"),
        "cash": cash, "unrealised": unreal,
        "realised": sum(t["pnl"] for t in tr), "account_value": cash + sum(p["margin"] for p in pos) + unreal,
        "positions": out, "history": list(reversed(tr)), "stats": P.stats(tr, P.stats_capital(acct)),
        "closed_now": closed, "source": m.source, "errors": m.errors[-3:], "expected": _expected(),
    })


def _expected() -> dict:
    """Win rate / profit factor the 2023-26 backtest showed per strategy (both indices), for the journal comparison."""
    r = BT.research_summary().get("expected") or {}
    return r


def context(symbol):
    return clean({"global": gcues(), "news": news(symbol), "fii": fii()})


def backtest(symbol, mult, capital, sizing="risk", otm=-1, strategy="both", days=60):
    res = BT.run(symbol, float(capital), float(mult), lot=M.lot_size(symbol), sizing=sizing, otm=int(otm), strategy=strategy,
                 days=int(days), risk_pct=prefs()["risk_pct"])
    if res.get("error"):
        return {"error": res["error"]}
    t = res["trades"]
    return clean({"summary": res["summary"], "days": res.get("days"),
                  "trades": t.to_dict("records") if hasattr(t, "to_dict") else [],
                  "research": BT.research_summary()})


_MEMO: dict = {}
import time as _time


def _memo(key, data):
    _MEMO[key] = (_time.time(), data)
    return data


STOCKS_URL = "https://raw.githubusercontent.com/Dimansee/FnO/research-data/research/data/stock_picks.json"


def stock_picks(force=False) -> dict:
    """The nightly stock scan's output (see research/stock_scan.py), fetched from the public research branch and cached."""
    import json as _json
    import requests as _rq
    key = "fno:stocks:cache"
    if not force:
        hit = _MEMO.get(key)
        if hit and _time.time() - hit[0] < (120 if hit[1].get("error") else 600):   # memory first: no database read per page load
            return hit[1]
        try:
            raw = store._cmd("GET", key)
            if raw:
                return _memo(key, clean(_json.loads(raw)))
        except Exception:
            pass
    try:
        r = _rq.get(STOCKS_URL, timeout=15)
        r.raise_for_status()
        data = clean(_json.loads(r.text))                 # the scan can write NaN (not valid JSON): read it with the stdlib parser, turn NaN into null
    except Exception as e:
        return _memo(key, {"error": f"Stock picks unavailable: {str(e)[:100]}", "today": [], "history": []})
    try:
        store._cmd("SET", key, _json.dumps(data), "EX", 1200)
    except Exception:
        pass
    return _memo(key, data)


MARKET_URL = "https://raw.githubusercontent.com/Dimansee/FnO/stock-data/stock_market.json"
SERIES_URL = "https://raw.githubusercontent.com/Dimansee/FnO/stock-data/stock_series.json"
_SERIES = {}


def stock_market(force=False) -> dict:
    """Nightly per-stock market view (research/stock_market.py), fetched from the public research branch and cached."""
    import json as _json
    import requests as _rq
    key = "fno:stocks:market"
    if not force:
        hit = _MEMO.get(key)
        if hit and _time.time() - hit[0] < (120 if hit[1].get("error") else 600):   # memory first: no database read per page load
            return hit[1]
        try:
            raw = store._cmd("GET", key)
            if raw:
                return _memo(key, clean(_json.loads(raw)))
        except Exception:
            pass
    try:
        r = _rq.get(MARKET_URL, timeout=20)
        r.raise_for_status()
        data = clean(_json.loads(r.text))
    except Exception as e:
        return _memo(key, {"error": f"Market view unavailable: {str(e)[:100]}", "stocks": [], "industries": []})
    try:
        store._cmd("SET", key, _json.dumps(data, separators=(",", ":")), "EX", 1800)
    except Exception:
        pass
    return _memo(key, data)


def stock_series(sym: str) -> dict:
    """Last 250 sessions of close/volume for one stock (chart). The whole series file is fetched once per server instance."""
    import requests as _rq
    import time as _t
    if not _SERIES or _t.time() - _SERIES.get("_at", 0) > 1800:
        try:
            r = _rq.get(SERIES_URL, timeout=25)
            r.raise_for_status()
            import json as _json
            _SERIES.clear(); _SERIES.update(clean(_json.loads(r.text))); _SERIES["_at"] = _t.time()
        except Exception as e:
            if not _SERIES:
                return {"error": f"Chart data unavailable: {str(e)[:80]}"}
    x = (_SERIES.get("stocks") or {}).get(sym.upper())
    if not x:
        return {"error": "No chart data for this stock."}
    return {"sym": sym.upper(), "dates": _SERIES["dates"], "close": x["c"], "volume": x["v"]}



_LIVE = {}


def stock_live(syms) -> dict:
    """Live price check for a handful of picks: today's open, last price, previous close (Yahoo chart endpoint, cached 45 s)."""
    import time as _t
    from concurrent.futures import ThreadPoolExecutor
    import requests as _rq
    syms = [x for x in dict.fromkeys(str(x).upper() for x in syms) if x.replace("&", "").replace("-", "").isalnum()][:25]

    def one(sym):
        hit = _LIVE.get(sym)
        if hit and _t.time() - hit[0] < 45:
            return sym, hit[1]
        try:
            r = _rq.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym.replace('&', '%26')}.NS", params={"range": "1d", "interval": "5m"},
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            meta = res["meta"]
            q = (res.get("indicators", {}).get("quote") or [{}])[0]
            opens = [x for x in (q.get("open") or []) if x is not None]
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            v = {"last": meta.get("regularMarketPrice"), "open": opens[0] if opens else None, "prev_close": prev,
                 "low": meta.get("regularMarketDayLow"), "time": meta.get("regularMarketTime")}
            if v["open"] is not None and prev:
                v["gap_pct"] = round((v["open"] / prev - 1) * 100, 2)
        except Exception as e:
            v = {"error": str(e)[:60]}
        _LIVE[sym] = (_t.time(), v)
        return sym, v
    with ThreadPoolExecutor(max_workers=8) as ex:
        return {"quotes": dict(ex.map(one, syms)), "at": C.now_ist().isoformat(timespec="seconds")}


def _live_line(p, q):
    """One short phrase for a pick given its live quote: open/gap, and the levels re-based on the actual open."""
    if not q or q.get("error") or q.get("open") is None:
        return f"{p['sym']} ~₹{p.get('entry_ref', p['close'])} → ₹{p.get('target', round(p['close'] * 1.05, 2))}"
    o = q["open"]
    pc, lo, last = q.get("prev_close"), q.get("low"), q.get("last")
    if pc and lo and last and last <= lo * 1.0005 and any(abs(last / pc - 1 + b) <= 0.0015 for b in (0.02, 0.05, 0.10, 0.20)):
        return f"{p['sym']} is on its LOWER CIRCUIT (₹{last:.2f}) - cannot be sold, skip"
    return f"{p['sym']} opened ₹{o:.2f} ({q.get('gap_pct', 0):+.1f}%) → target ₹{o * 1.05:.2f}, risk ₹{p.get('risk_line', '-')}"


def stocks_preopen(now) -> dict:
    """09:00 IST: re-read the freshest picks (the 06:00 scan has finished) and send the list for the open."""
    try:
        data = stock_picks(force=True)
    except Exception as e:
        return {"error": str(e)[:80]}
    picks = [p for p in (data.get("today") or []) if p.get("direction") != "down"]
    if not picks:
        return {"picks": 0}
    stale = data.get("data_to", "") < (now.date() - __import__("datetime").timedelta(days=4)).isoformat()
    strong = [p for p in picks if p.get("tier", "rule") == "rule"]
    show = (strong or picks)[:4]
    body = "; ".join(f"{p['sym']} buy ~₹{p.get('entry_ref', p['close'])} → ₹{p.get('target', round(p['close'] * 1.05, 2))} ({p.get('setup', 'pick')})" for p in show)
    title = (f"Pre-open: {len(strong)} rule pick{'s' if len(strong) != 1 else ''}" if strong else f"Pre-open: no rule pick, {len(picks)} weaker") + (" (scan looks stale)" if stale else "")
    return _push_safe(title, body + " - buy at the open; I will re-check at 09:30.", f"stocks-pre:{data.get('data_to')}:{now.date()}", "/#recs")

def stocks_morning_push(now) -> dict:
    """Once a day, on the first tick after the open: push the night's stock picks (if any) to the devices."""
    try:
        data = stock_picks()
    except Exception as e:
        return {"error": str(e)[:80]}
    picks = data.get("today") or []
    if not picks or data.get("data_to", "") < (now.date() - __import__("datetime").timedelta(days=5)).isoformat():
        return {"picks": 0}
    strong = [p for p in picks if p.get("tier", "rule") == "rule"]
    show = (strong or picks)[:4]
    try:
        lq = stock_live([p["sym"] for p in show]).get("quotes", {})
    except Exception:
        lq = {}
    body = "; ".join(_live_line(p, lq.get(p["sym"])) + f" ({p.get('setup', 'pick')})" for p in show)
    title = f"{len(strong)} rule pick{'s' if len(strong) != 1 else ''} for today" if strong else f"No rule pick today - {len(picks)} weaker candidate{'s' if len(picks) != 1 else ''}"
    return _push_safe(title, body, f"stocks:{data.get('data_to')}:{now.date()}", "/#recs")


def _push_safe(title, body, event_id, url="/"):
    try:
        return PUSH.send(title, body, event_id=event_id, url=url)
    except Exception as e:                                   # notifications must never break the tick
        return {"error": str(e)[:120]}


def _push_signal(sym, ev, order, g):
    plan = ev["plan"]
    name = {"noise": "Noise-area", "camarilla": "Camarilla", "ai": "AI model", "trend": "Trend-day", "fib": "Fibonacci"}.get(plan.get("strategy"), plan.get("strategy"))
    tgt = f"target ₹{order.get('target_prem')}" if order.get("target_fixed", True) else "no fixed target, trails"
    itgt = f"T {plan['target']:.0f}" if plan.get("target") is not None else "no T"
    body = (f"{name}: {order.get('strike'):g} {plan.get('opt')} @ ₹{order.get('entry_prem')} (SL ₹{order.get('sl_prem')}, {tgt}) "
            f"| index {plan.get('entry'):.0f} SL {plan.get('sl'):.0f} {itgt} | score {ev.get('score')}")
    return _push_safe(f"{sym} {ev.get('signal')}", body, f"reco:{sym}:{plan.get('strategy')}:{plan.get('signal_bar') or g['now'].strftime('%H:%M')}:{g['now'].date()}", "/#journal")


def _note_skip_safe(sym, slot, msg, now):
    try:                                                     # round 25: why a valid signal was not placed (for the day-end lag review)
        from . import lag as LG
        LG.note_skip(sym, slot, str(msg or ""), now)
    except Exception:
        pass


def _note_auto_safe(sym, slot, ok, msg, now):
    """Journal: every automatic attempt with its outcome; the weekly / daily limit and the capital shield also send one phone alert."""
    try:
        from . import journal as J
        ep = J.note_auto(sym, slot, ok, str(msg or ""), now, plan=_LAST_PLAN.get((sym, slot)))
    except Exception as e:
        print("[journal] note_auto failed:", e, flush=True)
        return
    if not ep or ep.get("ok"):
        return
    if ep["cat"] == "weekly_limit":
        _push_safe("Weekly loss limit reached", str(msg)[:200] + " Open Autopilot → Status to see the week or reset the weekly counter.",
                   f"weekly:{now.strftime('%G-%V')}", "/#autopilot")
    elif ep["cat"] == "daily_limit" and slot != "scalp":
        _push_safe("Daily limit reached", str(msg)[:200], f"daily:{now.date()}", "/#autopilot")
    elif ep["cat"] == "shield" and "below the floor" in str(msg):
        _push_safe("Capital shield stopped Autopilot", str(msg)[:220], f"shield:{now.date()}", "/#autopilot")


_tick_lock = threading.Lock()


def tick():
    """Called every minute by the scheduler. Only one tick runs at a time (process lock + database lock): a slow tick
    overlapping the next one could manage the same positions twice or open duplicate trades."""
    if not _tick_lock.acquire(blocking=False):
        return {"skipped": "previous tick still running"}
    try:
        tok = _db_lock("tick", 110)
        if tok is None:
            return {"skipped": "another tick is running"}
        try:
            return _tick()
        finally:
            _db_unlock("tick", tok)
    finally:
        _tick_lock.release()


def _sweep_after_close(now) -> list:
    """After 15:30 nothing intraday may stay open: square off whatever the 15:15 check missed (feed down, app asleep)."""
    try:
        if not store.positions():
            return []
        out = manage_positions()
        for c in out:
            _push_safe(f"{c['symbol']} position closed", f"{c['reason']} - P&L ₹{c['pnl']:+,.0f}", f"close:{c.get('id')}:sweep", "/#portfolio")
        return out
    except Exception as e:
        print("[tick] after-close sweep failed:", e, flush=True)
        return [{"error": str(e)[:160]}]


def _tick():
    now = C.now_ist()
    if now.weekday() >= 5:
        return {"skipped": "weekend"}
    if now.date() in C.HOLIDAYS:
        return {"skipped": "NSE holiday"}
    if time(8, 55) <= now.time() < C.MARKET_OPEN:
        return {"pre_open": stocks_preopen(now)}
    if not (C.MARKET_OPEN <= now.time() <= C.MARKET_CLOSE):
        if now.time() > C.MARKET_CLOSE:
            swept = _sweep_after_close(now)
        else:
            swept = []
        if time(15, 31) <= now.time() <= time(20, 0):                  # after the close: the day's research note and lessons (runs once)
            try:
                from . import datahub as DH
                out = {"post_close": LN.post_close_in()}
                if swept:
                    out["swept"] = swept
                try:
                    if not store._cmd("SET", f"fno:data:in_saved:{now.date().isoformat()}", "1", "NX", "EX", 86400 * 2) is None:   # once a day
                        out["data_saved"] = DH.save_in(now)
                except Exception as e:
                    out["data_saved"] = {"error": str(e)[:120]}
                try:                                                    # weekly copy of everything to the user's Google Drive (Fridays)
                    if now.weekday() == 4 and not store._cmd("SET", f"fno:backup:week:{now.strftime('%G-%V')}", "1", "NX", "EX", 86400 * 10) is None:
                        from . import backup as BK
                        out["drive_backup"] = BK.run(now) if BK.cfg().get("url") else {"skipped": "Drive not connected"}
                except Exception as e:
                    out["drive_backup"] = {"error": str(e)[:120]}
                return out
            except Exception as e:
                return {"post_close": {"error": str(e)[:160]}}
        return {"skipped": "outside market hours", **({"swept": swept} if swept else {})}
    try:
        store.set_raw_lazy("fno:last_tick", now.isoformat(timespec="seconds"), 86400 * 4)
    except Exception:
        pass
    try:
        closed = manage_positions()
    except Exception as e:                                   # a failure here must not stop entries / scalp / journal - but say so
        import traceback
        traceback.print_exc()
        closed = []
        _push_safe("Position check failed", f"Open positions could not be checked this minute: {str(e)[:100]}",
                   f"mperr:{now.strftime('%H%M')[:3]}", "/#portfolio")
    for c in closed:
        _push_safe(f"{c['symbol']} position closed", f"{c['reason']} - P&L ₹{c['pnl']:+,.0f}", f"close:{c.get('id') or c['symbol']}:{now.strftime('%H%M')}", "/#portfolio")
    placed = []
    pf = eprefs()
    t = now.time()
    if pf.get("auto") and C.FIRST_ENTRY_ANY <= t <= C.LAST_ENTRY_ANY:
        for sym in pf.get("auto_syms") or []:
            try:
                strats = sym_strategies(pf, sym)
                _LAST_PLAN.pop((sym, "main"), None)
                ok, msg = place_signal(sym, auto=True) if not _trend_only(pf, sym) else (False, "trend only")
                _note_auto_safe(sym, "main", ok, msg, now)
                if not ok:
                    _note_skip_safe(sym, "main", msg, now)
                if ok:
                    placed.append({"symbol": sym, "message": msg})
                    _push_safe(f"{sym}: paper trade placed", str(msg)[:160], f"placed:{sym}:{now.strftime('%H%M')}", "/#portfolio")
                for slot in C.SLOTS:                      # own-slot rules (round 23 trend-day catcher, round 29 Fibonacci breakout)
                    if slot not in strats or (slot == "trend" and t < C.TREND_FIRST_CHECK):
                        continue
                    _LAST_PLAN.pop((sym, slot), None)
                    ok, msg = place_signal(sym, auto=True, which=slot)
                    _note_auto_safe(sym, slot, ok, msg, now)
                    if not ok:
                        _note_skip_safe(sym, slot, msg, now)
                    if ok:
                        placed.append({"symbol": sym, "message": msg, "strategy": slot})
                        _push_safe(f"{sym}: {'trend-day' if slot == 'trend' else 'Fibonacci'} paper trade placed", str(msg)[:160],
                                   f"placed{slot}:{sym}:{now.strftime('%H%M')}", "/#portfolio")
            except Exception as e:                       # one instrument failing must not stop the others
                placed.append({"symbol": sym, "error": str(e)[:120]})
    try:                                                     # scalp book (separate paper ledger, off unless switched on)
        from . import scalp as SC
        sc = SC.tick(now)
        for x in sc.get("opened") or []:
            _note_auto_safe(x["symbol"], "scalp", True, x.get("message"), now)
        for x in sc.get("skipped") or []:
            _note_auto_safe(x["symbol"], "scalp", False, x.get("why"), now)
        if sc.get("closed"):                                 # one phone alert when the governor stops an index for the day
            scfg = SC.settings()
            for s in {c["symbol"] for c in sc["closed"]}:
                gv = SC.governor(SC._today_trades(s, now), scfg["capital"])
                if gv.get("stopped"):
                    _push_safe(f"{s} scalp book stopped for today", gv["stopped"], f"scalpstop:{s}:{now.date()}", "/#scalp")
    except Exception as e:
        sc = {"error": str(e)[:160]}
    if t <= time(9, 45):
        try:
            stocks_morning_push(now)
        except Exception:
            pass
    try:                                                     # session journal: context at the open, recommendations, outcomes
        jr = journal_tick(now)
    except Exception as e:
        jr = {"error": str(e)[:160]}
    try:                                                     # market recorder (every RECORD_EVERY_MIN minutes)
        from . import recorder
        from .market import Market
        mk = Market()
        rec = recorder.snapshot(now, market=mk)
        if any("session expired" in x for x in getattr(mk, "errors", [])):
            _push_safe("Broker login expired", "Live prices are off (delayed Yahoo data is being used). Log in again in Settings.", f"authexp:{now.date()}", "/#settings")
    except Exception as e:
        rec = {"error": str(e)[:160]}
    try:                                                     # phone alerts for the things that quietly break trading
        mk2 = locals().get("mk")
        if mk2 is not None:
            for h in health(mk2, mk2.candles("NIFTY"), now):
                if h["state"] == "bad" and h["label"] in ("Candles", "Auto-check"):
                    _push_safe("Data check failed", f"{h['label']}: {h['text']}", f"health:{h['label']}:{now.strftime('%H')}", "/#signal")
                if h["label"] == "Broker" and "one-year" in h["text"] and h["state"] == "warn":
                    _push_safe("Upstox data token ending soon", h["text"] + ". Generate a new one in the Upstox developer page and paste it in Settings.", f"antoken:{now.date()}", "/#settings")
    except Exception:
        pass
    return {"closed": closed, "placed": placed, "scalp": sc, "recorded": rec, "journal": jr, "time": now.isoformat(timespec="seconds")}


def _untested_note(symbol, g, ev):
    if symbol in C.UNTESTED:
        ev.setdefault("warnings", []).append(C.UNTESTED_NOTE.get(symbol) or f"{g['meta']['label']}: the rules were researched on Nifty and Bank Nifty only.")


def scan(symbol, m=None, prefs_=None, want_order=True):
    """What the strategies say right now (no side effects): the same evaluation the Signal page shows."""
    from . import journal as J
    m = m or M.Market()
    g = _gather(symbol, m)
    acct, pos, tr, cash = P.snapshot()
    cstats = M.chain_stats(g["chain"], g["spot"])
    pf = prefs_ or eprefs()
    guard = risk_guard(pos, tr, P.base(acct, tr), pf, equity=P.equity(acct, tr))
    if not guard.get("blocked"):
        guard = calendar_guard(symbol, g["exps"], g["now"].date()) or guard
    traded = _traded_today(symbol, pos, tr)
    ev = S.evaluate(symbol, g["candles"], g["vix"], cstats, None, gcues(), fii(), g["now"], guard, traded, pf["strategies"])
    ev = _with_ai(symbol, g, ev, pf, traded)
    ev = _with_custom(symbol, g, ev, pf, traded)
    if ev.get("plan"):
        why, mult = _learn_gate(g, ev["plan"])
        ev["learn"] = {"veto": why, "size_mult": mult}
        if why:
            ev.setdefault("warnings", []).append(why + " (automatic entries only; you can still place it by hand)")
    order = None
    if ev["plan"] and want_order:
        try:
            order = S.build_order(ev["plan"], g["chain"], g["spot"], g["vix"]["level"], g["lot"], _size_capital(acct, tr, pf), cash=cash, prefs=pf)
        except ValueError as e:
            order = None
            if str(e).startswith("SKIP"):
                ev.setdefault("warnings", []).append(str(e))
    _untested_note(symbol, g, ev)
    if g["vix"].get("assumed"):
        ev.setdefault("warnings", []).append("India VIX is unavailable - 14.0 is being assumed, so the VIX filters (min 11, max 18/20) cannot be trusted right now. Check the VIX yourself before trading.")
    try:
        last = g["candles"].index[-1]
        age = (g["now"] - last.to_pydatetime().astimezone(g["now"].tzinfo)).total_seconds() / 60 - 5   # a 5-minute candle is stamped at its start
        if C.MARKET_OPEN <= g["now"].time() <= C.MARKET_CLOSE and age > 20:
            ev.setdefault("warnings", []).append(f"Price data looks stale: the latest 5-minute candle is about {age:.0f} minutes old. Do not trade off this signal until it updates.")
    except Exception:
        pass
    if C.today_ist().year > C.HOLIDAYS_YEAR:
        ev.setdefault("warnings", []).append(f"The built-in NSE holiday list only covers {C.HOLIDAYS_YEAR} - add this year's list (fno/config.py) so holidays are skipped.")
    return g, ev, order, pf


def journal_tick(now):
    """Runs inside tick(): writes the day's context once, recommendations at 5-minute boundaries, outcomes after the close."""
    from . import journal as J
    t = now.time()
    day = now.date().isoformat()
    out = {}
    if time(9, 16) <= t <= time(13, 0):                     # the scheduler may only start at 09:30: write it on the first tick that finds it missing
        m = None
        for sym in J.SYMS:
            if J._get(day, f"ctx:{sym}") is None:
                m = m or M.Market()
                g = _gather(sym, m)
                J.write_context(sym, g, gcues(), fii(), eprefs())
                out[f"ctx:{sym}"] = "written"
        if t < time(9, 30):
            return out or {"skipped": "context already written"}
    if time(9, 30) <= t <= time(14, 50) and now.minute % 5 == 1:
        m = M.Market()
        pf = eprefs()
        for sym in J.SYMS:
            try:
                g, ev, order, _ = scan(sym, m, pf)
                out[sym] = J.record(sym, ev, order, g, pf)
                if out[sym] and ev.get("plan") and order:
                    _push_signal(sym, ev, order, g)
            except Exception as e:
                out[sym] = f"error: {str(e)[:100]}"
        return out
    if t >= time(15, 16) and J._get(day, "settled") is None and (store._cmd("HLEN", J._key(day)) or 0) > 0:
        m = M.Market()
        cands = {}
        for sym in J.SYMS:
            try:
                c = m.candles(sym)
                cands[sym] = c[c.index.date == now.date()]
            except Exception:
                pass
        return {"settled": J.settle(day, cands)}
    return {"skipped": "nothing due"}


# ---------------------------------------------------------------------------
# Custom strategies: data + backtest + improvement
# ---------------------------------------------------------------------------
def _custom_data(spec: dict, symbol: str, days: int):
    """(candles in the base interval, vix series or None, source label) for a custom strategy backtest."""
    from . import markets as MK
    if spec["market"] == "IN":
        if symbol not in ALL_SYMBOLS:
            raise ValueError(f"{symbol} is not an Indian instrument in this app.")
        c, v, src = BT.history(symbol, int(days))
        return c, v, src
    if not MK.valid_symbol(spec["market"], symbol):
        raise ValueError(f"{symbol} is not in the {spec['market']} list.")
    cal = int(days)
    df = MK.candles(spec["market"], symbol, spec["timeframe"], cal, ttl=1800)
    return df, None, "Binance public data" if (spec["market"] == "CRYPTO" or symbol in MK.BINANCE_GOLD) else "Yahoo Finance (gold = COMEX GC=F)" if spec["market"] == "GOLD" else "Yahoo Finance"


def custom_backtest(raw: dict, symbol: str | None, days: int, capital: float, risk_pct: float) -> dict:
    spec = CU.validate(raw, existing_id=raw.get("id") if raw.get("id") else None)
    sym = symbol or spec["symbols"][0]
    if not 20 <= int(days) <= 1500:
        raise ValueError("Days must be between 20 and 1500.")
    c, v, src = _custom_data(spec, sym, int(days))
    res = CU.backtest(spec, c, sym, v, float(capital), float(risk_pct))
    res["source"] = src
    res["spec_name"] = spec["name"]
    return clean(res)


def custom_improve(sid: str, days: int = 250) -> dict:
    spec = next((x for x in CU.load_all(store) if x["id"] == sid), None)
    if not spec:
        raise ValueError("Strategy not found.")
    sym = spec["symbols"][0]
    c, v, src = _custom_data(spec, sym, int(days))
    res = LN.improve_custom(spec, c, sym, v)
    out = {k: val for k, val in res.items() if k != "_spec"}
    out["source"], out["symbol"] = src, sym
    if res.get("candidate"):
        ch = LN.apply_variant(sid, res, auto=(LN.mode(spec["market"]) == "full"))
        out["change"] = ch
        out["adopted"] = bool(ch and ch["status"] == "applied")
    return clean(out)
