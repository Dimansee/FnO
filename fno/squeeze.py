"""Squeeze-release SHADOW slot (round 101, 13 Nov 2026). ON by default - it only records.

It places NO orders and has no money: it never touches the paper account, the scalp book or the carry slot. Every minute it
checks the rule on Sensex and writes down what the rule would have done (index levels and the live price of the at-the-money
option at entry and exit), so the live signals, timing and fills can be compared with the backtest before any real use.

Rule (research/round101/l_squeeze.py, candidate 'SENSEX,14,2.0,1.5,signal,1'), on completed 15-minute bars anchored at 09:15:
  squeeze  = Bollinger band (14 bars, 2 sd) inside the Keltner channel (EMA 14 +/- 1.5 x ATR 10)
  release  = the first bar where the band is back outside the channel, after a squeeze bar
  momentum = close - average of (14-bar mid-range, 14-bar mean)
  entry    = at the open of the bar after a release: call if momentum > 0, put if < 0. Entry bars 09:30-14:45. One a day.
  stop     = 1.5 x ATR 14 (index points, at the signal bar)
  exit     = stop, else the close of the first bar where momentum has changed sign, else 15:15.
Backtest (Jan 2023 - 16 Oct 2026, model option prices): 569 trades, +Rs 1.95L per lot, positive every year; as a half-size slot
next to today's Autopilot it meets the bar. Not adopted: 2026 alone is worse, 85% of its trades open while Autopilot already
holds the same side, and with fills 1% worse on each side the edge at Rs 2L is gone. Hence this shadow first.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

from . import config as C
from . import indicators as I
from . import store

SETTINGS_KEY = "squeeze"
POS, TRADES, LOG = "fno:squeeze:positions", "fno:squeeze:trades", "squeeze_log"
SYM = "SENSEX"
DEFAULTS = {"on": True}
RULE = {"tf": 15, "n": 14, "bb": 2.0, "kc": 1.5, "atr_kc": 10, "atr_stop": 14, "stop_k": 1.5, "per_day": 1}
FIRST_BAR, LAST_BAR = time(9, 30), time(14, 45)      # entry bars (start times), as tested
SQUARE_OFF = time(15, 15)
GRACE_MIN = 3                                        # an entry may be recorded up to 3 minutes into its bar (a missed tick)
STARTED = "2026-11-16"                               # first session of the shadow
BACKTEST = {
    "period": "Jan 2023 - 16 Oct 2026, 1-min Sensex data, model option prices, slippage and charges; added to today's Autopilot at half size",
    "alone": {"trades": 569, "per_lot": 194_942, "wins_pct": 38, "avg_r": 0.26, "years": {"2023": 35_844, "2024": 74_242, "2025": 60_620, "2026": 24_236}},
    "rows": [
        {"name": "Rs 2L Autopilot alone", "y2324": 3_070_000, "y2526": 2_127_000, "full": 18_302_000, "fall": -25.9},
        {"name": "Rs 2L + squeeze slot (half size)", "y2324": 4_577_000, "y2526": 2_911_000, "full": 21_015_000, "fall": -28.0},
        {"name": "Rs 20k Autopilot alone", "y2324": 194_000, "y2526": 221_000, "full": 1_996_000, "fall": -66.0},
        {"name": "Rs 20k + squeeze slot (half size)", "y2324": 308_000, "y2526": 268_000, "full": 2_861_000, "fall": -56.0},
    ],
    "warning": "2026 on its own is worse with the slot (Rs 2L 6.80L -> 6.39L, Rs 20k 1.20L -> 0.60L). 85% of its trades open while "
               "Autopilot already holds the same side. With fills 1% worse on each side the edge at Rs 2L is gone, and Sensex "
               "option prices in the test were modelled. This shadow measures the real ones.",
}


# ----------------------------------------------------------------------------- settings / storage
def settings() -> dict:
    return {**DEFAULTS, **(store.get(SETTINGS_KEY) or {})}


def save_settings(new: dict) -> dict:
    cur = settings()
    if "on" in new:
        cur["on"] = bool(new["on"])
    store.put(SETTINGS_KEY, cur)
    return cur


def positions() -> list[dict]:
    return sorted((json.loads(x) for x in store.hvals(POS)), key=lambda p: p["opened"])


def trades() -> list[dict]:
    return sorted((json.loads(x) for x in store.hvals(TRADES)), key=lambda t: t["closed"])


def reset():
    store.hdrop(POS)
    store.hdrop(TRADES)
    store.put(LOG, [])


def _log(now, event: str, **kw):
    try:
        lst = store.get(LOG) or []
        lst.append({"t": now.isoformat(timespec="seconds"), "event": event, **kw})
        store.put(LOG, lst[-400:])
    except Exception:
        pass


# ----------------------------------------------------------------------------- the rule (pure functions, shared with the tests)
def _ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=n, adjust=False).mean()


def _atr(b: pd.DataFrame, n: int) -> pd.Series:
    pc = b["close"].shift(1)
    tr = pd.concat([b["high"] - b["low"], (b["high"] - pc).abs(), (b["low"] - pc).abs()], axis=1).max(axis=1)   # skips NaN: first bar = high - low
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def bars15(c5: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """5-minute candles -> completed 15-minute bars anchored at 09:15 each session. With `now`, the last bar is dropped while
    it is still forming or while one of its three 5-minute candles has not arrived yet."""
    if c5 is None or len(c5) == 0:
        return pd.DataFrame(columns=["open", "high", "low", "close", "n"])
    df = c5[["open", "high", "low", "close"]]
    parts = []
    for _d, g in df.groupby(df.index.date):
        r = g.resample(f"{RULE['tf']}min", origin=g.index[0].replace(hour=9, minute=15, second=0, microsecond=0), label="left", closed="left")
        b = r.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        b["n"] = r["close"].count()
        parts.append(b.dropna(subset=["close"]))
    b = pd.concat(parts)
    if now is not None and len(b):
        last = b.index[-1]
        if last + timedelta(minutes=RULE["tf"]) > now or int(b["n"].iloc[-1]) < RULE["tf"] // C.CANDLE_MIN:
            b = b.iloc[:-1]
    return b


def frame(c5: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """15-minute bars with the squeeze state at each bar's close: on, rel (release), mom, risk (index points to the stop),
    long / short (entry signal for the NEXT bar's open)."""
    b = bars15(c5, now)
    if len(b) == 0:
        return b
    n = RULE["n"]
    c, h, l = b["close"], b["high"], b["low"]
    e = _ema(c, n)
    a = _atr(b, RULE["atr_kc"])
    m = c.rolling(n).mean()
    s = c.rolling(n).std()
    on = (m + RULE["bb"] * s < e + RULE["kc"] * a) & (m - RULE["bb"] * s > e - RULE["kc"] * a)
    b = b.copy()
    b["on"] = on
    b["rel"] = (~on) & on.shift(1, fill_value=False)
    b["mom"] = c - (h.rolling(n).max() + l.rolling(n).min() + 2 * m) / 4
    b["risk"] = RULE["stop_k"] * _atr(b, RULE["atr_stop"])
    b["long"] = b["rel"] & (b["mom"] > 0)
    b["short"] = b["rel"] & (b["mom"] < 0)
    return b


def entry_check(b: pd.DataFrame, now: datetime) -> dict | None:
    """A signal to enter NOW: the last completed bar released the squeeze and the bar that has just started may trade."""
    if b is None or len(b) == 0:
        return None
    j0 = b.index[-1]
    start = j0 + timedelta(minutes=RULE["tf"])                  # the entry bar
    if start.date() != now.date() or j0.date() != now.date():
        return None
    if not (FIRST_BAR <= start.time() <= LAST_BAR):
        return None
    late = (now - start).total_seconds()
    if late < 0 or late >= GRACE_MIN * 60:
        return None
    r = b.iloc[-1]
    side = 1 if bool(r["long"]) else (-1 if bool(r["short"]) else 0)
    risk = float(r["risk"]) if r["risk"] == r["risk"] else 0.0
    if not side or not risk > 0:
        return None
    return {"side": side, "signal_bar": j0.isoformat(), "entry_bar": start.isoformat(), "risk_pts": round(risk, 2),
            "mom": round(float(r["mom"]), 2), "late_s": int(late)}


def exit_check(b: pd.DataFrame, c5: pd.DataFrame, p: dict, now: datetime, spot: float | None) -> tuple[str, float] | None:
    """(reason, index price of the rule's exit) for an open shadow trade, or None. Order as tested: stop, momentum turn, 15:15."""
    s, sl = int(p["side"]), float(p["sl"])
    eb = datetime.fromisoformat(p["entry_bar"])
    if now.date() > eb.date():                                  # the app was asleep at 15:15: close it, flagged, at the first price seen
        last = float(spot) if spot is not None else (float(c5["close"].iloc[-1]) if c5 is not None and len(c5) else float(p["entry_spot"]))
        return "Closed late: the app missed 15:15", last
    hit = lambda px: s * (float(px) - sl) <= 0
    done = b[b.index >= eb] if b is not None and len(b) else None
    nxt = eb                                                    # start of the bar now forming
    if done is not None and len(done):                          # completed bars, in order: stop inside the bar first, then its close
        for ts, r in done.iterrows():
            if hit(r["low"] if s > 0 else r["high"]):
                return "Stop (1.5 x ATR)", sl
            if (s > 0 and r["mom"] < 0) or (s < 0 and r["mom"] > 0):
                return "Momentum turned", float(r["close"])
        nxt = done.index[-1] + timedelta(minutes=RULE["tf"])
    forming = c5[c5.index >= nxt] if c5 is not None and len(c5) else None
    adv = [float(spot)] if spot is not None else []
    if forming is not None and len(forming):
        adv.append(float(forming["low"].min()) if s > 0 else float(forming["high"].max()))
    if adv and hit(min(adv) if s > 0 else max(adv)):
        return "Stop (1.5 x ATR)", sl
    if now.time() >= SQUARE_OFF:
        last = float(spot) if spot is not None else (float(c5["close"].iloc[-1]) if c5 is not None and len(c5) else float(p["entry_spot"]))
        return "15:15 square-off", last
    return None


# ----------------------------------------------------------------------------- recording (no orders, no money)
def _good(v) -> bool:
    return v is not None and v == v and float(v) > 0


def _open(sg: dict, b: pd.DataFrame, c5: pd.DataFrame, m, now) -> tuple[bool, str]:
    from .market import instrument, slip_pct
    exps, lot = m.expiries_and_lot(SYM)
    if not exps:
        return False, "no expiry list"
    exp = I.pick_trading_expiry(exps, instrument(SYM)["expiry"], now.date()) or exps[0]
    spot = m.spot(SYM)
    if spot is None:
        return False, "no index price"
    ch = m.chain(SYM, exp, spot, m.vix()["level"], c5)
    live = ch.attrs.get("source") == "live"
    step = C.INDICES[SYM]["step"]
    k = float(round(spot / step) * step)
    opt, tag = ("CE", "ce") if sg["side"] > 0 else ("PE", "pe")
    row = ch[ch["strike"] == k]
    if row.empty:
        return False, f"{k:g} {opt} is not in the option chain"
    r = row.iloc[0]
    ltp, ask, bid = r.get(f"{tag}_ltp"), r.get(f"{tag}_ask"), r.get(f"{tag}_bid")
    if not _good(ltp):
        return False, f"{k:g} {opt}: no price"
    buy = float(ask) if (live and _good(ask)) else round(float(ltp) * (1 + slip_pct(float(ltp))), 2)
    eb = datetime.fromisoformat(sg["entry_bar"])
    bar = c5[c5.index >= eb]
    p = {"id": uuid.uuid4().hex[:8], "symbol": SYM, "opened": now.isoformat(timespec="seconds"), "expiry": str(exp),
         "legs": [{"side": "BUY", "strike": k, "opt": opt, "key": r[f"{tag}_key"], "price": buy, "src": m.br.name if live and m.br else "demo"}],
         "lot": int(lot), "lots": 1, "qty": int(lot), "entry_prem": buy, "entry_ltp": round(float(ltp), 2),
         "entry_bid": round(float(bid), 2) if _good(bid) else None, "entry_ask": round(float(ask), 2) if _good(ask) else None,
         "entry_spot": round(float(spot), 2), "bar_open": round(float(bar["open"].iloc[0]), 2) if len(bar) else None,
         "side": sg["side"], "risk_pts": sg["risk_pts"], "sl": round(float(spot) - sg["side"] * sg["risk_pts"], 2),
         "signal_bar": sg["signal_bar"], "entry_bar": sg["entry_bar"], "late_s": sg["late_s"], "mom": sg["mom"],
         "price_src": "live" if live else "formula", "source": m.source, "shadow": True}
    store.hset(POS, p["id"], store._dump(p))
    return True, f"Shadow {SYM} {k:g}{opt} at ₹{buy:.2f}, index {spot:,.1f}, stop {p['sl']:,.1f} (no order placed)"


def _rec(p: dict, m, now, why: str, rule_px: float) -> dict | None:
    spot = m.spot(p["symbol"])
    if spot is None:
        return None
    prices = m.leg_prices([p], {p["symbol"]: spot}, m.vix()["level"])
    lg = p["legs"][0]
    if lg["key"] not in prices:
        return None
    raw = float(prices[lg["key"]])
    fills = m.exit_fills([p], {lg["key"]: raw}, "realistic")
    sell = float(fills.get(lg["key"], raw))
    s, risk = int(p["side"]), float(p["risk_pts"])
    gross = (sell - p["entry_prem"]) * p["qty"]
    chg = I.charges(p["entry_prem"], sell, p["qty"], exch_pct=I.exch_rate(p["symbol"]))
    ref = float(p.get("bar_open") or p["entry_spot"])
    return {"id": p["id"], "symbol": p["symbol"], "side": s, "opened": p["opened"], "closed": now.isoformat(timespec="seconds"),
            "strike": lg["strike"], "opt": lg["opt"], "expiry": p["expiry"], "qty": p["qty"], "lots": 1,
            "entry_prem": p["entry_prem"], "entry_ltp": p.get("entry_ltp"), "entry_bid": p.get("entry_bid"), "entry_ask": p.get("entry_ask"),
            "exit_prem": round(sell, 2), "exit_ltp": round(raw, 2),
            "entry_spot": p["entry_spot"], "bar_open": p.get("bar_open"), "exit_spot": round(float(spot), 2), "rule_exit": round(float(rule_px), 2),
            "sl": p["sl"], "risk_pts": risk, "signal_bar": p["signal_bar"], "entry_bar": p["entry_bar"], "late_s": p.get("late_s"),
            "r_live": round(s * (float(spot) - float(p["entry_spot"])) / risk, 2), "r_rule": round(s * (float(rule_px) - ref) / risk, 2),
            "gross": round(gross, 2), "charges": round(chg, 2), "pnl": round(gross - chg, 2), "reason": why,
            "price_src": "live" if (p.get("price_src") == "live" and lg.get("src", "demo") != "demo") else "formula", "shadow": True}


def _close(p: dict, m, now, why: str, rule_px: float) -> dict | None:
    rec = _rec(p, m, now, why, rule_px)
    if rec is None:
        return None
    return store.close_into(POS, TRADES, p["id"], lambda _cur: rec)


def _wants_entry(now) -> bool:
    t = now.time()
    return FIRST_BAR <= t <= (datetime.combine(now.date(), LAST_BAR) + timedelta(minutes=GRACE_MIN)).time() and (now.minute % RULE["tf"]) < GRACE_MIN


def tick(now=None, m=None) -> dict:
    """Every-minute job in market hours. Reads prices only when a shadow trade is open or a 15-minute bar has just closed."""
    cfg = settings()
    now = now or C.now_ist()
    open_ = positions()
    if not cfg["on"] and not open_:
        return {"off": True}
    today = now.date().isoformat()
    want = cfg["on"] and _wants_entry(now) and not any(p["opened"][:10] == today for p in open_) \
        and sum(1 for x in trades() if x["opened"][:10] == today) < RULE["per_day"]
    if not open_ and not want:
        return {}
    from .market import Market
    m = m or Market()
    out = {"opened": [], "closed": [], "skipped": []}
    c5 = m.candles(SYM)
    if c5 is None or len(c5) == 0:
        return {"skipped": [{"why": "no candles"}]}
    b = frame(c5, now)
    for p in open_:
        try:
            chk = exit_check(b, c5, p, now, m.spot(SYM))
            if chk:
                rec = _close(p, m, now, chk[0], chk[1])
                if rec:
                    out["closed"].append(rec)
                    _log(now, "closed", id=rec["id"], reason=rec["reason"], pnl=rec["pnl"], r_live=rec["r_live"], r_rule=rec["r_rule"])
                else:
                    out["skipped"].append({"why": "no option price for the exit yet - retrying next minute"})
        except Exception as e:
            out["skipped"].append({"why": f"error: {str(e)[:120]}"})
    if want:
        try:
            sg = entry_check(b, now)
            if sg:
                ok, msg = _open(sg, b, c5, m, now)
                (out["opened"] if ok else out["skipped"]).append({"symbol": SYM, ("message" if ok else "why"): msg})
                _log(now, "opened" if ok else "signal not recorded", side=sg["side"], signal_bar=sg["signal_bar"], late_s=sg["late_s"], note=msg)
        except Exception as e:
            out["skipped"].append({"why": f"error: {str(e)[:120]}"})
            _log(now, "error", note=str(e)[:160])
    return out


# ----------------------------------------------------------------------------- views
def stats(trs: list[dict] | None = None) -> dict:
    trs = trades() if trs is None else trs
    pnl = [float(t["pnl"]) for t in trs]
    rl = [float(t["r_live"]) for t in trs if t.get("r_live") is not None]
    rr = [float(t["r_rule"]) for t in trs if t.get("r_rule") is not None]
    sp = [(float(t["entry_ask"]) - float(t["entry_bid"])) / float(t["entry_ltp"]) * 100 for t in trs
          if _good(t.get("entry_ask")) and _good(t.get("entry_bid")) and _good(t.get("entry_ltp"))]
    return {"trades": len(trs), "wins": sum(x > 0 for x in pnl), "pnl": round(sum(pnl), 2),
            "avg_r_live": round(float(np.mean(rl)), 2) if rl else None, "avg_r_rule": round(float(np.mean(rr)), 2) if rr else None,
            "avg_spread_pct": round(float(np.mean(sp)), 2) if sp else None,
            "live_priced": sum(1 for t in trs if t.get("price_src") == "live"),
            "days": len({t["opened"][:10] for t in trs})}


def day(d: str) -> dict:
    """Everything the shadow did on one day (for the nightly journal and the reviews)."""
    return {"trades": [t for t in trades() if t["opened"][:10] == d], "open": [p for p in positions() if p["opened"][:10] == d],
            "log": [x for x in (store.get(LOG) or []) if x.get("t", "")[:10] == d], "on": settings()["on"], "rule": RULE}


def page() -> dict:
    trs = trades()
    return {"settings": settings(), "symbol": SYM, "started": STARTED, "positions": positions(), "trades": trs[-100:][::-1], "stats": stats(trs),
            "log": (store.get(LOG) or [])[-30:][::-1],
            "rule": "Sensex, 15-minute bars: when the Bollinger band has been inside the Keltner channel (a squeeze) and comes back out, "
                    "the rule buys the at-the-money call if momentum is up, the put if it is down, at the next bar's open. Stop 1.5 x ATR; "
                    "exit when momentum turns, else 15:15. One a day. Nothing is bought here - the app only writes down what would have happened.",
            "backtest": BACKTEST}
