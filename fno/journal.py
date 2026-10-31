"""Session journal: what the app recommended, on what basis, and what then happened.

Written by the every-minute scheduler tick, so it runs even with no page open.
  09:16-09:25   context for the day (levels, VIX, cues, OI, expiries)             fno:jr:<date> field ctx:<sym>
  every 5 min   each strategy's recommendation (rules + AI) with its checklist,    fno:jr:<date> field reco:<id>
                order and reasons - once per signal candle; plus the AI's score
                for the candle even when it does not trade                          fno:jr:<date> field ai:<sym>  (list)
  15:16+        outcome of every recommendation on the index path (stop / target /
                square-off, R, best and worst excursion) and on the option premium
                from the recorder's 5-minute chain snapshots                        reco fields "result"
Research copy: the nightly GitHub job fetches /api/journal/day?date=... into research/data/live/<date>/journal.json.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pandas as pd

from . import config as C
from . import market as M
from . import paper as P
from . import store
from . import strategy as S

SYMS = ("NIFTY", "BANKNIFTY", "SENSEX")
KEEP_DAYS = 60
MAX_EPISODES = 80

# why an automatic entry did not happen -> (category, plain label). Order matters: first match wins.
AUTO_CATS = [
    ("weekly_limit", ("weekly loss limit",), "Weekly loss limit reached"),
    ("daily_limit", ("daily loss limit", "two losing", "max ", "daily risk limit"), "Daily limit reached"),
    ("shield", ("capital shield",), "Capital shield (account protection)"),
    ("money", ("not enough", "cash", "position size is zero", "quantity is zero"), "Not enough money"),
    ("risk_cap", ("skip (autopilot)", "even 1 lot risks"), "1 lot risks too much of the account"),
    ("calendar", ("holiday", "budget day", "expiry day"), "Calendar rule (holiday / budget / expiry)"),
    ("data", ("vix is unavailable", "candle is", "no price candles", "stale"), "Market data not reliable"),
    ("slot_busy", ("already have an open",), "A trade was already open in this slot"),
    ("learn_veto", ("learning", "veto", "half size"), "Learning engine veto"),
    ("big_run", ("round 42",), "Against a big 3-day run (round 42)"),
    ("spread", ("bid-ask spread",), "Option spread too wide"),
    ("custom_off", ("custom strategy",), "Custom strategy not set to auto"),
    ("equity_gate", ("equity reaches", "once equity"), "Rule waits for a bigger account"),
    ("lock", ("another order",), "Another order was being placed"),
    ("scalp_gov", ("stopped for today", "paused after", "lost twice today"), "Scalp governor stop"),
    ("scalp_filter", ("confidence", "too little room", "setup d:"), "Scalp filter not met"),
]


def auto_cat(msg: str) -> tuple[str, str]:
    m = (msg or "").lower()
    for cat, keys, label in AUTO_CATS:
        if any(k in m for k in keys):
            return cat, label
    return "other", "Other reason"


def note_auto(symbol: str, slot: str, ok: bool, msg: str, now, plan: dict | None = None, trade_id: str | None = None):
    """Every automatic entry attempt that had a reason to exist (a signal, or a block on one). Consecutive attempts with the
    same outcome are folded into one episode (from / to / attempts), so the every-minute retry does not flood the day."""
    if not msg or str(msg).startswith("No valid signal") or msg == "trend only" or "is off for this instrument" in str(msg) \
            or "only the own-slot rules" in str(msg).lower():
        return None
    day = now.date().isoformat()
    cat, label = ("executed", "Executed") if ok else auto_cat(str(msg))
    pl = plan or {}
    ep = {"from": now.strftime("%H:%M"), "to": now.strftime("%H:%M"), "n": 1, "ok": bool(ok), "cat": cat, "label": label, "msg": str(msg)[:260],
          "strategy": pl.get("strategy") or (slot if slot not in ("main", "scalp") else None), "signal_bar": pl.get("signal_bar"),
          "opt": pl.get("opt"), "entry": pl.get("entry"), "sl": pl.get("sl"), "target": pl.get("target"), "trade_id": trade_id}
    field = f"auto:{symbol}:{slot}"
    lst = _get(day, field) or []
    last = lst[-1] if lst else None
    same = last and last["cat"] == ep["cat"] and last.get("strategy") == ep["strategy"] and last.get("signal_bar") == ep["signal_bar"] \
        and (ok is False) and last["ok"] is False
    if same:
        last["to"], last["n"] = ep["to"], last["n"] + 1
        last["msg"] = ep["msg"]
    else:
        lst.append(ep)
    _put(day, field, lst[-MAX_EPISODES:])
    # a skipped signal with an index plan becomes a recommendation too, so the close settles what it would have done
    if not ok and pl.get("entry") is not None and pl.get("sl") is not None and slot != "scalp":
        stamp = pl.get("signal_bar") or now.strftime("%H:%M")
        rid = f"{symbol}:{pl.get('strategy')}:{stamp}"
        if _get(day, f"reco:{rid}") is None:
            _put(day, f"reco:{rid}", {"id": rid, "symbol": symbol, "time": now.strftime("%H:%M:%S"), "signal_bar": stamp, "strategy": pl.get("strategy"),
                                     "signal": pl.get("signal"), "opt": pl.get("opt"), "source": "autopilot",
                                     "index": {"entry": pl.get("entry"), "sl": pl.get("sl"), "target": pl.get("target"),
                                               "risk_pts": pl.get("risk_pts") or abs(float(pl["entry"]) - float(pl["sl"])), "rr": pl.get("rr")},
                                     "option": {}, "basis": {"checks": [], "score": None, "factors": [], "ai": None, "exp_r": pl.get("exp_r"),
                                                             "p_win": pl.get("p_win"), "trail": pl.get("trail")},
                                     "auto_enabled": True, "result": None})
    return ep


def _key(day):
    return f"fno:jr:{day}"


_seen: dict = {}            # (day, field) -> raw value found or written by this process (single writer: stays true)
_days_ready: set = set()    # days whose expiry + index entry were already set today


def _get(day, field):
    k = (day, field)
    if k in _seen:
        v = _seen[k]
    else:
        v = store._cmd("HGET", _key(day), field)
        if v:                                    # only remember fields that exist (a missing one may be written soon)
            if len(_seen) > 2000:
                _seen.clear()
            _seen[k] = v
    return json.loads(v) if v else None


def _put(day, field, value):
    raw = json.dumps(value, default=str, separators=(",", ":"))
    store._cmd("HSET", _key(day), field, raw)
    _seen[(day, field)] = raw
    if day not in _days_ready:                   # expiry and the day index only need setting once per day
        store._cmd("EXPIRE", _key(day), KEEP_DAYS * 86400)
        store._cmd("SADD", "fno:jr:days", day)
        _days_ready.add(day)


# ---------------------------------------------------------------- context at the open
def write_context(symbol, g, gc, fi, prefs):
    from . import service as SV
    today, hist = S.session_frames(g["candles"], g["now"])
    sigma = S.noise_sigma(hist)
    lv = S.camarilla_levels(hist)
    prev = hist[hist.index.date == hist.index[-1].date()] if not hist.empty else hist
    pc, ph, pl = (float(prev["close"].iloc[-1]), float(prev["high"].max()), float(prev["low"].min())) if len(prev) else (None, None, None)
    pv = (ph + pl + pc) / 3 if pc else None
    bc = (ph + pl) / 2 if pc else None
    tc = 2 * pv - bc if pc else None
    o0 = float(today["open"].iloc[0]) if len(today) else g["spot"]
    pcr = None
    try:                                                   # yesterday's last chain snapshot from the recorder
        from . import recorder
        days = sorted(store._cmd("SMEMBERS", "fno:rec:days") or [])
        prev_days = [d for d in days if d < g["now"].date().isoformat()]
        if prev_days:
            snaps = recorder.day(prev_days[-1])["snapshots"]
            mine = [s for s in snaps if s["sym"] == symbol]
            if mine:
                last = mine[-1]
                ce = sum((r[4] or 0) for r in last["rows"]); pe = sum((r[10] or 0) for r in last["rows"])
                pcr = round(pe / ce, 2) if ce else None
    except Exception:
        pass
    ctx = {"symbol": symbol, "time": g["now"].strftime("%H:%M"), "open": o0,
           "prev_close": pc, "prev_high": ph, "prev_low": pl, "gap_pct": round((g["spot"] / pc - 1) * 100, 2) if pc else None,
           "pivot": pv, "cpr": {"tc": tc, "bc": bc, "width_pct": round(abs(tc - bc) / pc * 100, 3) if pc else None},
           "camarilla": lv, "noise_sigma_known": bool(sigma),
           "noise_band_at": {f"{(int(k) + 5) // 60:02d}:{(int(k) + 5) % 60:02d}": (round(min(o0, pc) * (1 - C.NOISE_MULT * v), 1), round(max(o0, pc) * (1 + C.NOISE_MULT * v), 1))
                             for k, v in (sigma or {}).items() if (int(k) + 5) in (585, 615, 675, 735, 795, 855)} if sigma and pc else None,
           "atr_5m": float(today["atr"].iloc[-1]) if len(today) and "atr" in today else None,
           "vix": g["vix"], "global": {k: v for k, v in (gc or {}).items() if k in ("avg_equity_chg", "notes")}, "fii": fi,
           "pcr_prev_close": pcr, "expiry": str(g["trade_exp"]), "expiries": [str(e) for e in g["exps"][:3]], "lot": g["lot"],
           "prefs": {k: prefs.get(k) for k in ("strategies", "auto", "auto_syms", "sizing", "risk_pct")},
           "calendar": SV.calendar_guard(symbol, g["exps"], g["now"].date())}
    _put(g["now"].date().isoformat(), f"ctx:{symbol}", ctx)
    return ctx


# ---------------------------------------------------------------- recommendations
def record(symbol, ev, order, g, prefs):
    """Save the current recommendation(s) once per signal candle. Returns the number of new records."""
    day = g["now"].date().isoformat()
    n = 0
    plans = [(ev.get("plan"), order)] + [((ev.get(sl) or {}).get("plan"), None) for sl in C.SLOTS]
    for plan, od in plans:
        if plan:
            n += _record_one(symbol, ev, plan, od, g, prefs, day)
    ai = ev.get("ai")
    if ai and ai.get("candidates"):
        lst = _get(day, f"ai:{symbol}") or []
        if not lst or lst[-1]["bar"] != ai["bar_end"]:
            b = ai["candidates"][ai["best"]]
            lst.append({"bar": ai["bar_end"], "side": b["side"], "stop_k": b["stop_k"], "exp_r": round(b["exp_r"], 3), "p_win": round(b["p_win"], 3),
                        "spot": round(g["spot"], 1), "trade": b["exp_r"] >= ai.get("threshold", 0.3)})
            _put(day, f"ai:{symbol}", lst[-90:])
    return n


def _record_one(symbol, ev, plan, order, g, prefs, day) -> int:
    order = order or {}
    n = 0
    if plan:
        stamp = plan.get("signal_bar") or g["now"].strftime("%H:%M")
        rid = f"{symbol}:{plan.get('strategy')}:{stamp}"
        # one record per signal candle; the rule strategies also allow only one trade per day, so one record per day for them
        existing = [f for f in (store._cmd("HKEYS", _key(day)) or []) if f.startswith(f"reco:{symbol}:{plan.get('strategy')}:")]
        if _get(day, f"reco:{rid}") is None and not (plan.get("strategy") in ("noise", "camarilla", "trend", "fib") and existing):
            rec = {"id": rid, "symbol": symbol, "time": g["now"].strftime("%H:%M:%S"), "signal_bar": stamp, "strategy": plan.get("strategy"),
                   "signal": plan.get("signal") or ev.get("signal"), "opt": plan.get("opt"), "index": {"entry": plan.get("entry"), "sl": plan.get("sl"), "target": plan.get("target"),
                                                                               "risk_pts": plan.get("risk_pts"), "rr": plan.get("rr")},
                   "option": {"strike": order.get("strike"), "expiry": str(g["trade_exp"]), "ltp": order.get("ltp"), "entry_prem": order.get("entry_prem"),
                              "sl_prem": order.get("sl_prem"), "target_prem": order.get("target_prem"), "lots": order.get("lots"), "cost_per_lot": order.get("cost_per_lot"),
                              "delta": order.get("delta")},
                   "basis": {"checks": [[ok, t] for ok, t in ev.get("checks", [])], "score": ev.get("score"),
                             "factors": [{"factor": f["factor"], "value": f["value"], "score": f["score"]} for f in ev.get("factors", [])][:12],
                             "ai": {k: (ev.get("ai") or {}).get(k) for k in ("candidates", "best", "reasons", "threshold")} if plan.get("strategy") == "ai" else None,
                             "exp_r": plan.get("exp_r"), "p_win": plan.get("p_win"), "trail": plan.get("trail")},
                   "spot": g["spot"], "vix": g["vix"].get("level"), "lot": g["lot"], "auto_enabled": bool(prefs.get("auto")) and symbol in (prefs.get("auto_syms") or []),
                   "result": None}
            _put(day, f"reco:{rid}", rec)
            n += 1
    return n


# ---------------------------------------------------------------- outcomes after the close
def _premium_path(day, symbol, expiry, strike, opt, after_hhmm):
    """5-minute premium path of one contract from the recorder's chain snapshots."""
    try:
        from . import recorder
        snaps = recorder.day(day)["snapshots"]
    except Exception:
        return []
    out = []
    col = 1 if opt == "CE" else 7                         # ltp column in recorder.FIELDS
    for s in snaps:
        if s["sym"] != symbol or s["exp"] != expiry or s["t"] < after_hhmm:
            continue
        row = next((r for r in s["rows"] if r[0] == strike), None)
        if row and row[col] is not None:
            out.append((s["t"], row[col]))
    return out


def settle(day, candles_by_sym):
    """Fill in 'result' for every recommendation of the day. candles_by_sym: symbol -> today's 5-min bars."""
    fields = store._cmd("HKEYS", _key(day)) or []
    done = 0
    for f in fields:
        if not f.startswith("reco:"):
            continue
        rec = _get(day, f)
        if not rec or rec.get("result"):
            continue
        c = candles_by_sym.get(rec["symbol"])
        if c is None or c.empty:
            continue
        t0 = rec["time"][:5]
        after = c[c.index.strftime("%H:%M") >= t0]
        if after.empty:
            continue
        sign = 1 if rec["opt"] == "CE" else -1
        e, sl, tgt, risk = rec["index"]["entry"], rec["index"]["sl"], rec["index"]["target"], rec["index"]["risk_pts"] or 1
        hit, hit_t, x = None, None, None
        for ts, b in after.iterrows():
            adverse, favour = (b["low"], b["high"]) if sign > 0 else (b["high"], b["low"])
            end = (ts + timedelta(minutes=C.CANDLE_MIN)).strftime("%H:%M")
            if sign * (adverse - sl) <= 0:
                hit, hit_t, x = "stop", end, sl; break
            if tgt is not None and sign * (favour - tgt) >= 0:
                hit, hit_t, x = "target", end, tgt; break
            if end >= "15:15":
                hit, hit_t, x = "square-off", end, float(b["close"]); break
        if hit is None:
            hit, hit_t, x = "square-off", after.index[-1].strftime("%H:%M"), float(after["close"].iloc[-1])
        mfe = float(((after["high"] if sign > 0 else after["low"]) - e).max() * sign) if sign > 0 else float((e - after["low"]).max())
        mae = float((e - after["low"]).max()) if sign > 0 else float((after["high"] - e).max())
        opt_ = rec.get("option") or {}
        prem = _premium_path(day, rec["symbol"], opt_.get("expiry"), opt_.get("strike"), rec["opt"], t0) if opt_.get("strike") else []
        p_in = opt_.get("entry_prem") or opt_.get("ltp")
        res = {"index_outcome": hit, "at": hit_t, "exit_spot": round(x, 1), "R": round(sign * (x - e) / risk, 2), "mfe_pts": round(mfe, 1), "mae_pts": round(mae, 1),
               "close_15_15": float(after["close"].iloc[-1])}
        if prem and p_in:
            px = [p for _, p in prem]
            at_exit = next((p for t, p in prem if t >= hit_t), px[-1])
            res["premium"] = {"entry": p_in, "high": max(px), "low": min(px), "at_exit": at_exit, "last": px[-1],
                              "pnl_1lot_if_followed": round((at_exit - p_in) * (rec.get("lot") or 0), 0) if rec.get("lot") else None,
                              "max_gain_pct": round((max(px) / p_in - 1) * 100, 1), "max_loss_pct": round((min(px) / p_in - 1) * 100, 1)}
        rec["result"] = res
        _put(day, f, rec)
        done += 1
    lesson = None
    try:
        lesson = reflect(day, candles_by_sym)
    except Exception as e:
        lesson = f"(reflection failed: {str(e)[:80]})"
    _put(day, "settled", {"at": C.now_ist().isoformat(timespec="seconds"), "recos": done, "lesson": lesson})
    if lesson and done:
        try:
            from . import push as PUSH
            PUSH.send("Session wrap-up", lesson[:220], event_id=f"lesson:{day}", url="/#journal")
        except Exception:
            pass
    return done


def reflect(day, candles_by_sym) -> str | None:
    """A short, rule-based 'what happened today' note written after the close (no AI, no advice).
    It names what the recommendations did, what the context said, and the one thing to carry forward."""
    fields = store._cmd("HKEYS", _key(day)) or []
    recos = [r for r in (_get(day, f) for f in fields if f.startswith("reco:")) if r and r.get("result")]
    if not recos:
        return None
    parts = []
    outs = [r["result"]["index_outcome"] for r in recos]
    rs = [r["result"]["R"] for r in recos]
    parts.append(f"{len(recos)} recommendation{'s' if len(recos) != 1 else ''}: {outs.count('target')} target, {outs.count('stop')} stop, "
                 f"{outs.count('square-off')} squared off; {sum(rs):+.1f}R in total.")
    best = max(recos, key=lambda r: r["result"]["R"]); worst = min(recos, key=lambda r: r["result"]["R"])
    k_ = lambda r: f"{(r.get('option') or {}).get('strike'):g}" if (r.get("option") or {}).get("strike") else ""   # noqa: E731
    if best["result"]["R"] > 0:
        parts.append(f"Best: {best['symbol']} {best['opt']} {k_(best)} at {best['time'][:5]} ({best['result']['R']:+.1f}R, "
                     f"ran {best['result']['mfe_pts']:.0f} pts in favour).")
    if worst["result"]["R"] < 0 and worst is not best:
        pr = worst["result"].get("premium") or {}
        gain = pr.get("max_gain_pct")
        parts.append(f"Worst: {worst['symbol']} {worst['opt']} {k_(worst)} at {worst['time'][:5]} ({worst['result']['R']:+.1f}R"
                     + (f"; the premium was up {gain:.0f}% at one point before the stop" if gain and gain > 10 else "") + ").")
    sq = [r for r in recos if r["result"]["index_outcome"] == "square-off"]
    if sq:
        left = [r for r in sq if r["result"]["mfe_pts"] >= 2 * (r["index"].get("risk_pts") or 1)]
        if left:
            parts.append(f"{len(left)} squared-off trade{'s' if len(left) != 1 else ''} reached 2R during the day and gave part of it back - the rules hold to 15:15 by design; the backtest says trailing earlier did not help.")
    ctx = {s: _get(day, f"ctx:{s}") for s in SYMS}
    vix = next((c.get("vix", {}).get("level") for c in ctx.values() if c and c.get("vix")), None)
    if vix is not None:
        parts.append(f"India VIX was {vix:.1f} at the open" + (" - below 11, where the rules stand aside." if vix < 11 else
                     " - the band where the rules did best historically (50-75th percentile) is roughly 13-16." if 13 <= vix <= 16 else "."))
    scores = [r.get("basis", {}).get("score") for r in recos if r.get("basis", {}).get("score") is not None]
    if scores:
        hi = [r for r in recos if (r.get("basis", {}).get("score") or 0) >= 3]
        if hi:
            parts.append(f"{sum(1 for r in hi if r['result']['R'] > 0)} of {len(hi)} high-score (3+) ideas ended positive.")
    # paper trades that left early (trail / time stop / breakeven) vs the plan held to stop / target / 15:15
    try:
        tds = [t for t in store.trades() if t.get("opened", "")[:10] == day and t.get("mode") == "signal"]
    except Exception:
        tds = []
    for t in tds:
        pl = t.get("plan") or {}
        why = (t.get("reason") or "").lower()
        if not any(w in why for w in ("trail", "time stop", "breakeven", "band", "vwap")):
            continue
        rec = next((r for r in recos if r["symbol"] == t["symbol"] and r["strategy"] == pl.get("strategy") and r.get("signal_bar") == pl.get("signal_bar")), None)
        pr = (rec or {}).get("result", {}).get("premium") if rec else None
        if not pr or pr.get("pnl_1lot_if_followed") is None:
            continue
        held = pr["pnl_1lot_if_followed"] * (t.get("lots") or 1)
        parts.append(f"{t['symbol']} {pl.get('strategy')} trade left early ({t.get('reason')}) for ₹{t['pnl']:+,.0f}; held to stop / target / 15:15 it would have made ₹{held:+,.0f} "
                     + ("- the early exit cost money today, but over 3 years it saves more than it costs." if held > t["pnl"] else "- the early exit saved money today."))
    parts.append("Carry forward: one trade per rule per day, the index-level stop decides, premium paths are shown so you can see where the plan and the option diverged.")
    return " ".join(parts)


# ---------------------------------------------------------------- read
def days():
    out = sorted(store._cmd("SMEMBERS", "fno:jr:days") or [])
    return out[-KEEP_DAYS:]


def _hm(x) -> int:
    try:
        return int(str(x)[:2]) * 60 + int(str(x)[3:5])
    except Exception:
        return -1


def _trade_view(t, is_open=False) -> dict:
    pl = t.get("plan") or {}
    return {"id": t.get("id"), "opened": (t.get("opened") or "")[11:16], "closed": (t.get("closed") or "")[11:16] if not is_open else None,
            "open": is_open, "pnl": None if is_open else t.get("pnl"), "reason": None if is_open else t.get("reason"), "lots": t.get("lots"),
            "entry_prem": t.get("entry_prem"), "exit_prem": t.get("exit_prem"), "by": "autopilot" if pl.get("auto") else ("you (signal)" if t.get("mode") == "signal" else "you (manual)"),
            "legs": [f"{l.get('side')} {l.get('strike'):g}{l.get('opt')}" for l in (t.get("legs") or []) if l.get("strike") is not None]}


def decisions(d: str, h: dict, recos: list, closed: list, open_: list) -> dict:
    """Every recommendation / automatic attempt of the day with what the system did about it: executed (and how it
    ended), or not executed and why, plus the blocks that stopped Autopilot without a specific signal."""
    eps = {k[5:]: v for k, v in h.items() if k.startswith("auto:")}
    sys_trades = [(t, False) for t in closed if t.get("mode") == "signal"] + [(p, True) for p in open_ if p.get("mode") == "signal"]
    used, rows = set(), []

    def match_trade(r):
        for t, op in sys_trades:
            pl = t.get("plan") or {}
            if t.get("symbol") == r["symbol"] and pl.get("strategy") == r.get("strategy") and pl.get("signal_bar") and pl.get("signal_bar") == r.get("signal_bar"):
                return t, op
        rt = _hm(r.get("time"))
        for t, op in sys_trades:
            pl = t.get("plan") or {}
            if t.get("symbol") == r["symbol"] and pl.get("strategy") == r.get("strategy") and 0 <= _hm((t.get("opened") or "")[11:16]) - rt <= 30 \
                    and t.get("id") not in used:
                return t, op
        return None, None

    def match_episode(r):
        mine = [(k, e) for k, lst in eps.items() if k.split(":")[0] == r["symbol"] and not k.endswith(":scalp") for e in lst if not e.get("ok")]
        exact = [e for _, e in mine if e.get("strategy") == r.get("strategy") and e.get("signal_bar") and e.get("signal_bar") == r.get("signal_bar")]
        if exact:
            return exact[0]
        rt = _hm(r.get("time"))
        slot = r.get("strategy") if r.get("strategy") in C.SLOTS else "main"
        near = [e for k, e in mine if k.endswith(":" + slot) and (_hm(e["from"]) - 15 <= rt <= _hm(e["to"]) + 1 or 0 <= _hm(e["from"]) - rt <= 15)]
        near.sort(key=lambda e: (e["cat"] == "slot_busy", abs(_hm(e["from"]) - rt)))
        return near[0] if near else None

    for r in recos:
        t, op = match_trade(r)
        if t is not None:
            used.add(t.get("id"))
            st = {"state": "executed", "label": "Executed", "trade": _trade_view(t, op)}
        else:
            e = match_episode(r)
            if e:
                st = {"state": "skipped", "cat": e["cat"], "label": e["label"], "msg": e["msg"], "attempts": e.get("n"), "from": e["from"], "to": e["to"]}
            elif not r.get("auto_enabled"):
                st = {"state": "skipped", "cat": "auto_off", "label": "Autopilot off for this instrument",
                      "msg": "Automatic trading was off (or this index was not ticked in Settings → Auto-trade), so the signal was only recorded."}
            elif _hm(r.get("time")) > C.LAST_ENTRY_ANY.hour * 60 + C.LAST_ENTRY_ANY.minute:
                st = {"state": "skipped", "cat": "late", "label": "After the last entry time",
                      "msg": f"Automatic entries stop at {C.LAST_ENTRY_ANY.strftime('%H:%M')}."}
            else:
                st = {"state": "skipped", "cat": "not_attempted", "label": "No automatic attempt logged",
                      "msg": "The minute check did not try this signal (it had changed or expired before the check, or this was before decisions were logged)."}
        res = r.get("result") or {}
        if st["state"] == "executed":
            tv = st["trade"]
            what = "Still open." if tv["open"] else f"Closed {tv['closed']} · {tv['reason']}"
        elif res:
            pr = res.get("premium") or {}
            what = (f"If taken: {res.get('index_outcome')} at {res.get('at')} ({res.get('R'):+.1f}R on the index)"
                    + (f", about ₹{pr['pnl_1lot_if_followed']:+,.0f} per lot" if pr.get("pnl_1lot_if_followed") is not None else ""))
        else:
            what = "Outcome is worked out after 15:15."
        rows.append({"time": r.get("time", "")[:5], "symbol": r["symbol"], "strategy": r.get("strategy"), "opt": r.get("opt"),
                     "index": r.get("index"), "option": r.get("option"), "reco_id": r.get("id"), "status": st, "what_happened": what,
                     "result": res or None, "source": r.get("source") or "scan"})
    for t, op in sys_trades:                                 # executed system trades with no recorded recommendation (own-slot, Sensex...)
        if t.get("id") in used:
            continue
        pl = t.get("plan") or {}
        tv = _trade_view(t, op)
        rows.append({"time": tv["opened"], "symbol": t.get("symbol"), "strategy": pl.get("strategy"), "opt": pl.get("opt"),
                     "index": {"entry": pl.get("entry"), "sl": pl.get("sl"), "target": pl.get("target")}, "option": None, "reco_id": None,
                     "status": {"state": "executed", "label": "Executed", "trade": tv},
                     "what_happened": "Still open." if op else f"Closed {tv['closed']} · {tv['reason']}",
                     "result": None, "source": "trade"})
    rows.sort(key=lambda x: x["time"] or "")
    blocks = [{"symbol": k.split(":")[0], "slot": k.split(":", 1)[1], **e} for k, lst in eps.items() for e in lst
              if not e.get("ok") and not e.get("signal_bar") and not k.endswith(":scalp")]
    blocks.sort(key=lambda e: e["from"])
    scalp_eps = [{"symbol": k.split(":")[0], **e} for k, lst in eps.items() if k.endswith(":scalp") for e in lst]
    scalp_eps.sort(key=lambda e: e["from"])
    by_cat: dict = {}
    for r in rows:
        if r["status"]["state"] == "skipped":
            c = r["status"]["label"]
            by_cat[c] = by_cat.get(c, 0) + 1
    return {"rows": rows, "blocks": blocks, "scalp": scalp_eps,
            "summary": {"signals": len(rows), "executed": sum(1 for r in rows if r["status"]["state"] == "executed"),
                        "skipped": sum(1 for r in rows if r["status"]["state"] == "skipped"), "skipped_by_reason": by_cat}}


def day(d: str) -> dict:
    datetime.fromisoformat(d)
    raw = store._cmd("HGETALL", _key(d)) or []
    if isinstance(raw, dict):                                # fakeredis (tests) returns a dict, Upstash a flat list
        h = {k: json.loads(v) for k, v in raw.items()}
    else:
        h = {raw[i]: json.loads(raw[i + 1]) for i in range(0, len(raw), 2)}
    recos = sorted((v for k, v in h.items() if k.startswith("reco:")), key=lambda r: r["time"])
    seen_ids = set()
    trades = []
    for t in store.trades() + P.archived():                     # archived = trades from before a P&L reset (history kept)
        if t.get("opened", "")[:10] == d and t.get("id") not in seen_ids:
            seen_ids.add(t.get("id")); trades.append(t)
    open_ = [p for p in store.positions() if p.get("opened", "")[:10] == d]
    ai = {s: h.get(f"ai:{s}") or [] for s in SYMS}
    res = [r["result"] for r in recos if r.get("result")]
    try:
        from . import scalp as SC
        scalp_tr = sorted([t for t in SC.trades() if (t.get("opened") or "")[:10] == d], key=lambda t: t.get("opened") or "")
        scalp_open = [p for p in SC.positions() if (p.get("opened") or "")[:10] == d]
    except Exception:
        scalp_tr, scalp_open = [], []
    dec = decisions(d, h, recos, trades, open_)
    summary = {"recommendations": len(recos), "settled": len(res),
               "by_outcome": {k: sum(1 for r in res if r["index_outcome"] == k) for k in ("target", "stop", "square-off")},
               "sum_R": round(sum(r["R"] for r in res), 2) if res else None,
               "by_strategy": {st: {"n": sum(1 for r in recos if r["strategy"] == st),
                                    "R": round(sum(r["result"]["R"] for r in recos if r["strategy"] == st and r.get("result")), 2)} for st in {r["strategy"] for r in recos}},
               "paper_trades": len(trades), "paper_pnl": round(sum(t["pnl"] for t in trades), 0),
               "open_positions": len(open_), "executed": dec["summary"]["executed"], "skipped": dec["summary"]["skipped"],
               "scalp_trades": len(scalp_tr), "scalp_pnl": round(sum(t.get("pnl") or 0 for t in scalp_tr), 0)}
    return {"date": d, "context": {s: h.get(f"ctx:{s}") for s in SYMS}, "recommendations": recos, "ai": ai, "paper_trades": trades,
            "open_positions": open_, "decisions": dec, "scalp_trades": scalp_tr, "scalp_open": scalp_open,
            "settled": h.get("settled"), "summary": summary}
