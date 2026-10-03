"""Glue between market data, the rulebook and the paper ledger. Every public
function returns plain JSON-safe dicts for the API."""
from __future__ import annotations

import math
from datetime import date

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
def _traded_today(symbol, positions, trades) -> bool:
    today = C.today_ist().isoformat()
    return any(x["symbol"] == symbol and x.get("mode") == "signal" and x["opened"][:10] == today
               for x in list(positions) + list(trades))


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
    ev = S.evaluate(symbol, g["candles"], g["vix"], cstats, nw, gc, fi, g["now"], guard, _traded_today(symbol, pos, tr))
    today, hist = S.session_frames(g["candles"], g["now"])
    prev = float(hist["close"].iloc[-1]) if not hist.empty else g["spot"]

    order, alts = None, []
    if ev["plan"]:
        if any(p["symbol"] == symbol and p["mode"] == "signal" for p in pos):
            ev["warnings"].append("You already have an open signal trade in this instrument.")
        args = (ev["plan"], g["chain"], g["spot"], g["vix"]["level"], g["lot"], acct["capital_start"])
        try:
            order = S.build_order(*args, strike=strike, cash=cash)
        except ValueError:
            order = S.build_order(*args, cash=cash)
        alts = S.strike_alternatives(*args, cash=cash)
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
def place_signal(symbol, strike=None):
    m = M.Market()
    g = _gather(symbol, m)
    acct, pos, tr, cash = P.snapshot()
    guard = P.day_guard(pos, tr, acct["capital_start"])
    if guard.get("blocked"):
        return False, guard["reason"]
    if any(p["symbol"] == symbol and p["mode"] == "signal" for p in pos):
        return False, "You already have an open signal trade in this instrument."
    cstats = M.chain_stats(g["chain"], g["spot"])
    ev = S.evaluate(symbol, g["candles"], g["vix"], cstats, news(symbol), gcues(), fii(), g["now"], guard,
                    _traded_today(symbol, pos, tr))
    if not ev["plan"]:
        return False, f"No valid signal right now ({ev['signal']}). The setup may have changed - refresh."
    try:
        order = S.build_order(ev["plan"], g["chain"], g["spot"], g["vix"]["level"], g["lot"], acct["capital_start"],
                              strike=strike, cash=cash)
    except ValueError as e:
        return False, str(e)
    if order["lots"] <= 0:
        return False, order["note"] or "Position size is zero."
    src = m.br.name if g["chain"].attrs.get("source") == "live" and m.br else "demo"
    legs = [{**lg, "src": src} for lg in order["legs"]]
    return P.open_position(cash, symbol, legs, order["lots"], g["lot"], g["trade_exp"], g["spot"], m.source,
                           clean(order), clean(ev["plan"]))


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
        "closed_now": closed, "source": m.source, "errors": m.errors[-3:],
    })


def context(symbol):
    return clean({"global": gcues(), "news": news(symbol), "fii": fii()})


def backtest(symbol, mult, capital, sizing="risk", otm=0):
    res = BT.run(symbol, float(capital), float(mult), lot=M.lot_size(symbol), sizing=sizing, otm=int(otm))
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
    return {"closed": manage_positions(), "time": now.isoformat(timespec="seconds")}
