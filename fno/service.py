"""Glue between market data, the rulebook and the paper ledger. Every public
function returns plain JSON-safe dicts for the API."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

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
        return M.cached("gcues", 600, X.global_cues)
    except Exception:
        return {"markets": {}, "avg_equity_chg": None, "notes": ["Global data unavailable"]}


def news(symbol):
    meta = M.instrument(symbol)
    q = None if meta["kind"] == "index" else symbol
    try:
        return M.cached(f"news:{q}", 300, lambda: X.fetch_news(q))
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
                 "enforce_limits": False, "fills": "realistic"}   # demo account: the daily risk limits are shown as a tip unless switched on


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


def _traded_today(symbol, positions, trades) -> dict:
    """Which strategies already traded this instrument today (one trade per strategy per instrument)."""
    today = C.today_ist().isoformat()
    done = {"noise": False, "camarilla": False}
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
    return P.equity(acct, tr) if pf.get("sizing") == "compound" else acct["capital_start"]


def risk_guard(pos, tr, capital, pf, auto=False) -> dict:
    """The daily risk limits (two losers / -2% / max trades). On the demo account they only advise unless enforced in Settings."""
    if pf.get("autopilot"):
        eq = float(capital) + sum(h["pnl"] for h in tr)
        wg = P.week_guard(tr, eq, C.AUTOPILOT_WEEKLY_SMALL if eq < C.AUTOPILOT_SMALL_ACCOUNT else None)
    else:
        wg = P.week_guard(tr, capital)
    if wg.get("blocked") and (auto or pf.get("enforce_limits")):   # the weekly limit always applies to automatic trades (round 18: it improved profit and the deepest fall)
        return wg
    g = P.day_guard(pos, tr, capital)
    if g.get("blocked") and not pf.get("enforce_limits"):
        return {"blocked": False, "tip": "Daily risk limit reached - on a real account you would stop now: " + g["reason"] +
                " (This demo account carries on; switch 'Enforce daily limits' on in Settings to stop for the day.)",
                "pnl_today": g.get("pnl_today"), "trades_today": g.get("trades_today")}
    return g


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
        lt = store._cmd("GET", "fno:last_tick")
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
    guard = risk_guard(pos, tr, acct["capital_start"], pf)
    if not guard.get("blocked"):
        guard = calendar_guard(symbol, g["exps"], g["now"].date()) or guard
    traded = _traded_today(symbol, pos, tr)
    ev = S.evaluate(symbol, g["candles"], g["vix"], cstats, nw, gc, fi, g["now"], guard, traded, pf["strategies"])
    ev = _with_ai(symbol, g, ev, pf, traded)
    ev = _with_custom(symbol, g, ev, pf, traded)
    today, hist = S.session_frames(g["candles"], g["now"])
    prev = float(hist["close"].iloc[-1]) if not hist.empty else g["spot"]

    order, alts = None, []
    if ev["plan"]:
        if any(p["symbol"] == symbol and p["mode"] == "signal" for p in pos):
            ev["warnings"].append("You already have an open signal trade in this instrument.")
        args = (ev["plan"], g["chain"], g["spot"], g["vix"]["level"], g["lot"], acct["capital_start"])
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
def place_signal(symbol, strike=None, auto=False):
    m = M.Market()
    g = _gather(symbol, m)
    acct, pos, tr, cash = P.snapshot()
    pf = eprefs()
    guard = risk_guard(pos, tr, acct["capital_start"], pf, auto=auto)
    if not guard.get("blocked"):
        guard = calendar_guard(symbol, g["exps"], g["now"].date()) or guard
    if guard.get("blocked"):
        return False, guard["reason"]
    if any(p["symbol"] == symbol and p["mode"] == "signal" for p in pos):
        return False, "You already have an open signal trade in this instrument."
    cstats = M.chain_stats(g["chain"], g["spot"])
    traded = _traded_today(symbol, pos, tr)
    ev = S.evaluate(symbol, g["candles"], g["vix"], cstats, news(symbol), gcues(), fii(), g["now"], guard, traded, pf["strategies"])
    ev = _with_ai(symbol, g, ev, pf, traded)
    ev = _with_custom(symbol, g, ev, pf, traded)
    if not ev["plan"]:
        return False, f"No valid signal right now ({ev['signal']}). The setup may have changed - refresh."
    if auto and not _custom_auto_ok(ev["plan"]):
        return False, "The signal comes from a custom strategy that is not switched to auto-trade (you can still place it by hand)."
    if auto:                                                        # the learning engine's guarded rules apply to automatic entries only
        why, mult = _learn_gate(g, ev["plan"])
        if why:
            return False, why
        if mult < 1:
            pf = {**pf, "risk_pct": float(pf.get("risk_pct") or 1.0) * mult}
    try:
        order = S.build_order(ev["plan"], g["chain"], g["spot"], g["vix"]["level"], g["lot"], _size_capital(acct, tr, pf),
                              strike=strike, cash=cash, prefs=pf)
    except ValueError as e:
        return False, str(e)
    if order["lots"] <= 0:
        return False, order["note"] or "Position size is zero."
    src = m.br.name if g["chain"].attrs.get("source") == "live" and m.br else "demo"
    legs = [{**lg, "src": src} for lg in order["legs"]]
    return P.open_position(cash, symbol, legs, order["lots"], g["lot"], g["trade_exp"], g["spot"], m.source,
                           clean(order), clean({**ev["plan"], "auto": auto, "ctx": _entry_ctx(g)}))


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
                    "price_src": "live" if all(l.get("src", "demo") in m.clients for l in p["legs"]) else "formula"})
    return clean({
        "capital_start": acct["capital_start"], "cash": cash, "unrealised": unreal,
        "realised": sum(t["pnl"] for t in tr), "account_value": cash + sum(p["margin"] for p in pos) + unreal,
        "positions": out, "history": list(reversed(tr)), "stats": P.stats(tr, acct["capital_start"]),
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


STOCKS_URL = "https://raw.githubusercontent.com/Dimansee/FnO/research-data/research/data/stock_picks.json"


def stock_picks(force=False) -> dict:
    """The nightly stock scan's output (see research/stock_scan.py), fetched from the public research branch and cached."""
    import json as _json
    import requests as _rq
    key = "fno:stocks:cache"
    if not force:
        try:
            raw = store._cmd("GET", key)
            if raw:
                return clean(_json.loads(raw))
        except Exception:
            pass
    try:
        r = _rq.get(STOCKS_URL, timeout=15)
        r.raise_for_status()
        data = clean(_json.loads(r.text))                 # the scan can write NaN (not valid JSON): read it with the stdlib parser, turn NaN into null
    except Exception as e:
        return {"error": f"Stock picks unavailable: {str(e)[:100]}", "today": [], "history": []}
    try:
        store._cmd("SET", key, _json.dumps(data), "EX", 1200)
    except Exception:
        pass
    return data


MARKET_URL = "https://raw.githubusercontent.com/Dimansee/FnO/stock-data/stock_market.json"
SERIES_URL = "https://raw.githubusercontent.com/Dimansee/FnO/stock-data/stock_series.json"
_SERIES = {}


def stock_market(force=False) -> dict:
    """Nightly per-stock market view (research/stock_market.py), fetched from the public research branch and cached."""
    import json as _json
    import requests as _rq
    key = "fno:stocks:market"
    if not force:
        try:
            raw = store._cmd("GET", key)
            if raw:
                return clean(_json.loads(raw))
        except Exception:
            pass
    try:
        r = _rq.get(MARKET_URL, timeout=20)
        r.raise_for_status()
        data = clean(_json.loads(r.text))
    except Exception as e:
        return {"error": f"Market view unavailable: {str(e)[:100]}", "stocks": [], "industries": []}
    try:
        store._cmd("SET", key, _json.dumps(data, separators=(",", ":")), "EX", 1800)
    except Exception:
        pass
    return data


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
    if pc and lo and last and last <= lo * 1.0005 and any(abs(last / pc - 1 + b) <= 0.0015 for b in (0.05, 0.10, 0.20)):
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
    name = {"noise": "Noise-area", "camarilla": "Camarilla", "ai": "AI model"}.get(plan.get("strategy"), plan.get("strategy"))
    body = (f"{name}: {order.get('strike'):g} {plan.get('opt')} @ ₹{order.get('entry_prem')} (SL ₹{order.get('sl_prem')}, target ₹{order.get('target_prem')}) "
            f"| index {plan.get('entry'):.0f} SL {plan.get('sl'):.0f} T {plan.get('target'):.0f} | score {ev.get('score')}")
    return _push_safe(f"{sym} {ev.get('signal')}", body, f"reco:{sym}:{plan.get('strategy')}:{plan.get('signal_bar') or g['now'].strftime('%H:%M')}:{g['now'].date()}", "/#journal")


def tick():
    """Called every minute by the database scheduler during market hours."""
    now = C.now_ist()
    if now.weekday() >= 5:
        return {"skipped": "weekend"}
    if now.date() in C.HOLIDAYS:
        return {"skipped": "NSE holiday"}
    if time(8, 55) <= now.time() < C.MARKET_OPEN:
        return {"pre_open": stocks_preopen(now)}
    if not (C.MARKET_OPEN <= now.time() <= C.MARKET_CLOSE):
        if time(15, 31) <= now.time() <= time(20, 0):                  # after the close: the day's research note and lessons (runs once)
            try:
                from . import datahub as DH
                out = {"post_close": LN.post_close_in()}
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
        return {"skipped": "outside market hours"}
    try:
        store._cmd("SET", "fno:last_tick", now.isoformat(timespec="seconds"), "EX", 86400 * 4)
    except Exception:
        pass
    closed = manage_positions()
    for c in closed:
        _push_safe(f"{c['symbol']} position closed", f"{c['reason']} - P&L ₹{c['pnl']:+,.0f}", f"close:{c.get('id') or c['symbol']}:{now.strftime('%H%M')}", "/#portfolio")
    placed = []
    pf = eprefs()
    t = now.time()
    if pf.get("auto") and C.FIRST_ENTRY_ANY <= t <= C.LAST_ENTRY_ANY:
        for sym in pf.get("auto_syms") or []:
            try:
                ok, msg = place_signal(sym, auto=True)
                if ok:
                    placed.append({"symbol": sym, "message": msg})
                    _push_safe(f"{sym}: paper trade placed", str(msg)[:160], f"placed:{sym}:{now.strftime('%H%M')}", "/#portfolio")
            except Exception as e:                       # one instrument failing must not stop the others
                placed.append({"symbol": sym, "error": str(e)[:120]})
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
    return {"closed": closed, "placed": placed, "recorded": rec, "journal": jr, "time": now.isoformat(timespec="seconds")}


def scan(symbol, m=None, prefs_=None, want_order=True):
    """What the strategies say right now (no side effects): the same evaluation the Signal page shows."""
    from . import journal as J
    m = m or M.Market()
    g = _gather(symbol, m)
    acct, pos, tr, cash = P.snapshot()
    cstats = M.chain_stats(g["chain"], g["spot"])
    pf = prefs_ or eprefs()
    guard = risk_guard(pos, tr, acct["capital_start"], pf)
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
    return df, None, "Binance public data" if spec["market"] == "CRYPTO" else "Yahoo Finance"


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
        ch = LN.apply_variant(sid, res, auto=(LN.mode() == "full"))
        out["change"] = ch
        out["adopted"] = bool(ch and ch["status"] == "applied")
    return clean(out)
