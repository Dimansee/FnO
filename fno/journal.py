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

SYMS = ("NIFTY", "BANKNIFTY")
KEEP_DAYS = 60


def _key(day):
    return f"fno:jr:{day}"


def _get(day, field):
    v = store._cmd("HGET", _key(day), field)
    return json.loads(v) if v else None


def _put(day, field, value):
    store._cmd("HSET", _key(day), field, json.dumps(value, default=str, separators=(",", ":")))
    store._cmd("EXPIRE", _key(day), KEEP_DAYS * 86400)
    store._cmd("SADD", "fno:jr:days", day)


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
    plan = ev.get("plan")
    if plan and order:
        stamp = plan.get("signal_bar") or g["now"].strftime("%H:%M")
        rid = f"{symbol}:{plan.get('strategy')}:{stamp}"
        # one record per signal candle; the rule strategies also allow only one trade per day, so one record per day for them
        existing = [f for f in (store._cmd("HKEYS", _key(day)) or []) if f.startswith(f"reco:{symbol}:{plan.get('strategy')}:")]
        if _get(day, f"reco:{rid}") is None and not (plan.get("strategy") in ("noise", "camarilla") and existing):
            rec = {"id": rid, "symbol": symbol, "time": g["now"].strftime("%H:%M:%S"), "signal_bar": stamp, "strategy": plan.get("strategy"),
                   "signal": ev.get("signal"), "opt": plan.get("opt"), "index": {"entry": plan.get("entry"), "sl": plan.get("sl"), "target": plan.get("target"),
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
    ai = ev.get("ai")
    if ai and ai.get("candidates"):
        lst = _get(day, f"ai:{symbol}") or []
        if not lst or lst[-1]["bar"] != ai["bar_end"]:
            b = ai["candidates"][ai["best"]]
            lst.append({"bar": ai["bar_end"], "side": b["side"], "stop_k": b["stop_k"], "exp_r": round(b["exp_r"], 3), "p_win": round(b["p_win"], 3),
                        "spot": round(g["spot"], 1), "trade": b["exp_r"] >= ai.get("threshold", 0.3)})
            _put(day, f"ai:{symbol}", lst[-90:])
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
        prem = _premium_path(day, rec["symbol"], rec["option"]["expiry"], rec["option"]["strike"], rec["opt"], t0)
        p_in = rec["option"].get("entry_prem") or rec["option"].get("ltp")
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
    _put(day, "settled", {"at": C.now_ist().isoformat(timespec="seconds"), "recos": done})
    return done


# ---------------------------------------------------------------- read
def days():
    out = sorted(store._cmd("SMEMBERS", "fno:jr:days") or [])
    return out[-KEEP_DAYS:]


def day(d: str) -> dict:
    datetime.fromisoformat(d)
    raw = store._cmd("HGETALL", _key(d)) or []
    if isinstance(raw, dict):                                # fakeredis (tests) returns a dict, Upstash a flat list
        h = {k: json.loads(v) for k, v in raw.items()}
    else:
        h = {raw[i]: json.loads(raw[i + 1]) for i in range(0, len(raw), 2)}
    recos = sorted((v for k, v in h.items() if k.startswith("reco:")), key=lambda r: r["time"])
    trades = [t for t in store.trades() if t.get("opened", "")[:10] == d]
    ai = {s: h.get(f"ai:{s}") or [] for s in SYMS}
    res = [r["result"] for r in recos if r.get("result")]
    summary = {"recommendations": len(recos), "settled": len(res),
               "by_outcome": {k: sum(1 for r in res if r["index_outcome"] == k) for k in ("target", "stop", "square-off")},
               "sum_R": round(sum(r["R"] for r in res), 2) if res else None,
               "by_strategy": {st: {"n": sum(1 for r in recos if r["strategy"] == st),
                                    "R": round(sum(r["result"]["R"] for r in recos if r["strategy"] == st and r.get("result")), 2)} for st in {r["strategy"] for r in recos}},
               "paper_trades": len(trades), "paper_pnl": round(sum(t["pnl"] for t in trades), 0)}
    return {"date": d, "context": {s: h.get(f"ctx:{s}") for s in SYMS}, "recommendations": recos, "ai": ai, "paper_trades": trades,
            "settled": h.get("settled"), "summary": summary}
