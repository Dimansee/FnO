"""Glue between market data, the rulebook and the paper ledger. Every public
function returns plain JSON-safe dicts for the API."""
from __future__ import annotations

import math
from datetime import date, time

import numpy as np
import pandas as pd

from . import backtest as BT
from . import config as C
from . import context as X
from . import indicators as I
from . import market as M
from . import paper as P
from . import store
from . import strategy as S
from . import ai as AI

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
PREF_DEFAULTS = {"sizing": "risk", "risk_pct": 1.0, "strategies": ["noise", "camarilla"], "auto": False, "auto_syms": ["NIFTY"]}


def prefs() -> dict:
    return {**PREF_DEFAULTS, **(store.get("prefs") or {})}


def _traded_today(symbol, positions, trades) -> dict:
    """Which strategies already traded this instrument today (one trade per strategy per instrument)."""
    today = C.today_ist().isoformat()
    done = {"noise": False, "camarilla": False}
    for x in list(positions) + list(trades):
        if x["symbol"] == symbol and x.get("mode") == "signal" and x["opened"][:10] == today:
            done[((x.get("plan") or {}).get("strategy")) or "noise"] = True
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
    closed = []
    for p in pos:
        if spots.get(p["symbol"]) is None or not all(l["key"] in prices for l in p["legs"]):
            continue
        prem = P.net_price(p["legs"], prices)
        reason, upd = S.exit_check(p, spots[p["symbol"]], prem, now, states.get(p["symbol"]))
        if upd:
            p.update(upd)
            store.update_position(p)
        if reason and (p["mode"] == "signal" or reason.startswith("Square-off")):
            rec = P.close_position(p["id"], prices, reason)
            if rec:
                closed.append({"symbol": rec["symbol"], "reason": reason, "pnl": rec["pnl"]})
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


def calendar_guard(symbol, exps, today) -> dict | None:
    """Days the research says to sit out (round 6): Union Budget day, and Bank Nifty on its own expiry day."""
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


def dashboard(symbol, strike=None):
    m = M.Market()
    closed = manage_positions(m)
    g = _gather(symbol, m)
    acct, pos, tr, cash = P.snapshot()
    cstats = M.chain_stats(g["chain"], g["spot"])
    gc, nw, fi = gcues(), news(symbol), fii()
    guard = P.day_guard(pos, tr, acct["capital_start"])
    if not guard.get("blocked"):
        guard = calendar_guard(symbol, g["exps"], g["now"].date()) or guard
    pf = prefs()
    traded = _traded_today(symbol, pos, tr)
    ev = S.evaluate(symbol, g["candles"], g["vix"], cstats, nw, gc, fi, g["now"], guard, traded, pf["strategies"])
    ev = _with_ai(symbol, g, ev, pf, traded)
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
        "time": g["now"].strftime("%H:%M:%S"), "spot": g["spot"], "prev_close": prev,
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
    guard = P.day_guard(pos, tr, acct["capital_start"])
    if not guard.get("blocked"):
        guard = calendar_guard(symbol, g["exps"], g["now"].date()) or guard
    if guard.get("blocked"):
        return False, guard["reason"]
    if any(p["symbol"] == symbol and p["mode"] == "signal" for p in pos):
        return False, "You already have an open signal trade in this instrument."
    cstats = M.chain_stats(g["chain"], g["spot"])
    pf = prefs()
    traded = _traded_today(symbol, pos, tr)
    ev = S.evaluate(symbol, g["candles"], g["vix"], cstats, news(symbol), gcues(), fii(), g["now"], guard, traded, pf["strategies"])
    ev = _with_ai(symbol, g, ev, pf, traded)
    if not ev["plan"]:
        return False, f"No valid signal right now ({ev['signal']}). The setup may have changed - refresh."
    try:
        order = S.build_order(ev["plan"], g["chain"], g["spot"], g["vix"]["level"], g["lot"], acct["capital_start"],
                              strike=strike, cash=cash, prefs=pf)
    except ValueError as e:
        return False, str(e)
    if order["lots"] <= 0:
        return False, order["note"] or "Position size is zero."
    src = m.br.name if g["chain"].attrs.get("source") == "live" and m.br else "demo"
    legs = [{**lg, "src": src} for lg in order["legs"]]
    return P.open_position(cash, symbol, legs, order["lots"], g["lot"], g["trade_exp"], g["spot"], m.source,
                           clean(order), clean({**ev["plan"], "auto": auto}))


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
    rec = P.close_position(pos_id, prices, "Manual exit")
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
        out.append({**p, "current_prem": round(P.net_price(p["legs"], prices), 2), "unrealised": round(u, 2)})
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


def tick():
    """Called every minute by the database scheduler during market hours."""
    now = C.now_ist()
    if now.weekday() >= 5:
        return {"skipped": "weekend"}
    if not (C.MARKET_OPEN <= now.time() <= C.MARKET_CLOSE):
        return {"skipped": "outside market hours"}
    closed = manage_positions()
    placed = []
    pf = prefs()
    t = now.time()
    if pf.get("auto") and C.FIRST_ENTRY_ANY <= t <= C.LAST_ENTRY_ANY:
        for sym in pf.get("auto_syms") or []:
            try:
                ok, msg = place_signal(sym, auto=True)
                if ok:
                    placed.append({"symbol": sym, "message": msg})
            except Exception as e:                       # one instrument failing must not stop the others
                placed.append({"symbol": sym, "error": str(e)[:120]})
    try:                                                     # session journal: context at the open, recommendations, outcomes
        jr = journal_tick(now)
    except Exception as e:
        jr = {"error": str(e)[:160]}
    try:                                                     # market recorder (every RECORD_EVERY_MIN minutes)
        from . import recorder
        from .market import Market
        rec = recorder.snapshot(now, market=Market())
    except Exception as e:
        rec = {"error": str(e)[:160]}
    return {"closed": closed, "placed": placed, "recorded": rec, "journal": jr, "time": now.isoformat(timespec="seconds")}


def scan(symbol, m=None, prefs_=None, want_order=True):
    """What the strategies say right now (no side effects): the same evaluation the Signal page shows."""
    from . import journal as J
    m = m or M.Market()
    g = _gather(symbol, m)
    acct, pos, tr, cash = P.snapshot()
    cstats = M.chain_stats(g["chain"], g["spot"])
    guard = P.day_guard(pos, tr, acct["capital_start"])
    if not guard.get("blocked"):
        guard = calendar_guard(symbol, g["exps"], g["now"].date()) or guard
    pf = prefs_ or prefs()
    traded = _traded_today(symbol, pos, tr)
    ev = S.evaluate(symbol, g["candles"], g["vix"], cstats, None, gcues(), fii(), g["now"], guard, traded, pf["strategies"])
    ev = _with_ai(symbol, g, ev, pf, traded)
    order = None
    if ev["plan"] and want_order:
        try:
            order = S.build_order(ev["plan"], g["chain"], g["spot"], g["vix"]["level"], g["lot"], acct["capital_start"], cash=cash, prefs=pf)
        except ValueError:
            order = None
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
                J.write_context(sym, g, gcues(), fii(), prefs())
                out[f"ctx:{sym}"] = "written"
        if t < time(9, 30):
            return out or {"skipped": "context already written"}
    if time(9, 30) <= t <= time(14, 50) and now.minute % 5 == 1:
        m = M.Market()
        pf = prefs()
        for sym in J.SYMS:
            try:
                g, ev, order, _ = scan(sym, m, pf)
                out[sym] = J.record(sym, ev, order, g, pf)
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
