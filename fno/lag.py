"""Day-end "where did we lag" review (research round 25).

For every index after the close:
  - how big the day's move was and how much of it the book caught (index points, rupees),
  - why a trend day was missed (VIX ceiling, no signal / slow grind, opening drive, a skipped signal and the reason it was skipped),
  - which trades left points on the table (trailing exit / time stop before the move continued) or went the wrong way,
  - how the capital was used (lots, share of equity in premium, risk per trade, the equity at which lots step up).
A 20-session tally of the causes is kept, and each cause is linked to the research that already tested its fix, so the
review says plainly whether a change is due (and why not, when the tested fix lost money). Rule numbers are never changed
from here: a cause that keeps costing money and has no recent test is flagged for research instead.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pandas as pd

from . import config as C
from . import store

KEY_DAYS = "fno:lag:days"
WINDOW = 20                     # sessions in the rolling tally
TREND_PCT = 0.8                 # |open -> close| >= 0.8% = trend day (same definition as the research)
FLAG_MIN = 3                    # a cause seen this many times in the window is listed under "improvement watch"

# cause -> (label, research cards that tested the fix, what they found)
CAUSES = {
    "vix_ceiling": ("VIX above the Noise / Camarilla ceiling", "r18a, r23, r24",
                    "Trading those days lost money with bought options (r23: 2026 +91k vs +105k) and with debit spreads (r24: every year negative). Ceilings stay."),
    "no_signal": ("No signal: price never cleared the noise band or H4/L4 (slow grind)", "r23",
                  "The trend-day catcher was added for this (10:45-14:15, own slot). Wider bands / earlier checks earned less (r24)."),
    "opening_drive": ("Opening drive: most of the move was done before the first check", "r24",
                      "A 09:45 / 10:15 catcher start would catch these but earned less overall (r24). No change."),
    "trail_exit": ("Trailing exit (back inside the band / VWAP) before the move continued", "r23",
                   "Holding to 15:15 instead wins on days like these but lost overall (r23: 2026 +96k vs +105k, deeper fall). No change."),
    "time_stop": ("Camarilla 45-minute time stop before the move came", "r23",
                  "No time stop or a 90-minute stop earned less (r23: train +72k / +102k vs +131k). No change."),
    "blocked_open": ("Signal blocked because another trade was open in the index", "r23",
                     "Allowing both rules at once: more profit in training, 2025 negative, deeper fall (r23). No change; the catcher has its own slot."),
    "wrong_way": ("First trade went against the day's direction", "r21d, r14a",
                  "Re-entering after a loss was worse (r21d); re-entry after a non-losing trade is on the watch-list (p-reentry)."),
    "risk_skip": ("Signal skipped: one lot risked more than the per-trade limit for this equity", "r20b, r25",
                  "Small-account cap (15%). Bigger size or a tighter cap did not help over rolling 12-month windows (r25)."),
    "cash_skip": ("Signal skipped: not enough free cash", "r25", "Happens when several positions are open; lots shrink to fit the cash."),
    "weekly_cap": ("Signal skipped: weekly loss limit reached", "r18b, r20c", "Adopted rule (it improved profit and the deepest fall)."),
    "day_limit": ("Signal skipped: daily limit (2 losers / -2% / 4 trades)", "r19c", "Only advisory on paper unless enforced in Settings."),
    "catcher_gate": ("Trend-day catcher waiting for Rs 40k equity (Autopilot)", "r24", "Small-account gate; removes the catcher's extra risk below Rs 40k."),
    "learn_veto": ("Skipped by the learning engine (veto / half size)", "learning", "Guarded change with its own evidence; see Changes in this tab."),
    "other_skip": ("Signal skipped for another reason", "-", "See the message."),
}


def _skip_cause(msg: str) -> str:
    m = (msg or "").lower()
    if "already have an open" in m:
        return "blocked_open"
    if "skip (autopilot)" in m or "risks" in m and "limit" in m or "even 1 lot risks" in m:
        return "risk_skip"
    if "cash" in m:
        return "cash_skip"
    if "week" in m:
        return "weekly_cap"
    if "two losing" in m or "daily loss" in m or "max " in m and "trades" in m:
        return "day_limit"
    if "equity reaches" in m:
        return "catcher_gate"
    if "learning" in m or "veto" in m or "half size" in m:
        return "learn_veto"
    return "other_skip"


def note_skip(symbol: str, slot: str, msg: str, now: datetime):
    """Called by the scheduler when a valid signal could not be placed. One record per index / slot / reason per day."""
    if not msg or msg.startswith("No valid signal") or msg == "trend only" or "is off for this instrument" in msg:
        return
    key = f"fno:skips:{now.date().isoformat()}"
    field = f"{symbol}|{slot}|{_skip_cause(msg)}"
    store._cmd("HSETNX", key, field, json.dumps({"time": now.strftime("%H:%M"), "msg": msg[:240]}))
    store._cmd("EXPIRE", key, 14 * 86400)


def _skips(day: str) -> list[dict]:
    raw = store._cmd("HGETALL", f"fno:skips:{day}") or []
    h = raw if isinstance(raw, dict) else {raw[i]: raw[i + 1] for i in range(0, len(raw), 2)}
    out = []
    for k, v in h.items():
        sym, slot, cause = k.split("|", 2)
        out.append({"symbol": sym, "slot": slot, "cause": cause, **json.loads(v)})
    return sorted(out, key=lambda x: x["time"])


def _px_at(c: pd.DataFrame, t: datetime) -> float | None:
    """Index close of the last bar that ended at or before t."""
    if c.empty:
        return None
    end = c.index + timedelta(minutes=C.CANDLE_MIN)
    tt = pd.Timestamp(t)
    if tt.tzinfo is None and c.index.tz is not None:
        tt = tt.tz_localize(c.index.tz)
    elif tt.tzinfo is not None and c.index.tz is None:
        tt = tt.tz_localize(None)
    sel = c[end <= tt]
    return float(sel["close"].iloc[-1]) if len(sel) else float(c["close"].iloc[0])


def _ceilings(vix: float | None) -> list[str]:
    if vix is None:
        return []
    out = []
    if vix >= C.NOISE_VIX_MAX:
        out.append(f"Noise (ceiling {C.NOISE_VIX_MAX:g})")
    if vix > C.CAM_VIX_MAX:
        out.append(f"Camarilla (ceiling {C.CAM_VIX_MAX:g})")
    if vix < C.VIX_MIN:
        out.append(f"all rules (floor {C.VIX_MIN:g})")
    return out


def review_in(day: str, candles: dict, stats: dict, jd: dict, trades: list[dict], equity: float | None, prefs: dict | None,
              vix_open: float | None) -> dict:
    """candles: {sym: today's 5-min bars}; stats: {sym: learn._day_stats(...)}; jd: journal day; trades: signal trades opened today."""
    skips = _skips(day)
    recos = jd.get("recommendations", []) if jd else []
    per, causes = [], []
    for sym, st in stats.items():
        c = candles.get(sym)
        if c is None or c.empty:
            continue
        o, cl = float(c["open"].iloc[0]), float(c["close"].iloc[-1])
        sq = c[[ (t + timedelta(minutes=C.CANDLE_MIN)).time() <= C.SQUARE_OFF for t in c.index]]
        close_1515 = float(sq["close"].iloc[-1]) if len(sq) else cl
        move = cl - o
        mv_pct = move / o * 100
        trend = abs(mv_pct) >= TREND_PCT
        up = move > 0
        mine = [t for t in trades if t.get("symbol") == sym]
        rows, caught_pts, rupees = [], 0.0, 0.0
        for t in mine:
            pl = t.get("plan") or {}
            sign = 1 if pl.get("opt", "CE") == "CE" else -1
            entry = float(pl.get("entry") or t.get("spot_at_entry") or o)
            closed = t.get("closed")
            xs = _px_at(c, datetime.fromisoformat(closed)) if closed else None
            got = sign * ((xs if xs is not None else close_1515) - entry)
            hold = sign * (close_1515 - entry)
            reason = t.get("reason") or ("open" if not closed else "")
            left = hold - got if closed else 0.0
            delta = abs(float(t.get("delta") or 0.55))
            left_rs = left * delta * float(t.get("qty") or 0)
            tag = None
            if trend and ((sign > 0) != up):
                tag = "wrong_way"
            elif left > 0.5 * float(pl.get("risk_pts") or 1e9) and "trailing" in reason.lower():
                tag = "trail_exit"
            elif left > 0.5 * float(pl.get("risk_pts") or 1e9) and "time stop" in reason.lower():
                tag = "time_stop"
            if tag:
                causes.append({"symbol": sym, "cause": tag, "pts": round(max(left, 0) if tag != "wrong_way" else abs(got), 1),
                               "rs": round(max(left_rs, 0)) if tag != "wrong_way" else round(-float(t.get("pnl") or 0))})
            caught_pts += got
            rupees += float(t.get("pnl") or 0)
            rows.append({"strategy": pl.get("strategy"), "side": "CALL" if sign > 0 else "PUT", "opened": (t.get("opened") or "")[11:16],
                         "closed": (closed or "")[11:16], "reason": reason, "lots": t.get("lots"), "pnl": round(float(t.get("pnl") or 0)),
                         "index_pts": round(got, 1), "if_held_to_1515": round(hold, 1), "left_pts": round(max(left, 0), 1),
                         "capital_used_pct": round(float(t.get("margin") or 0) / equity * 100, 1) if equity else None,
                         "risk_pct": round(delta * float(pl.get("risk_pts") or 0) * float(t.get("qty") or 0) / equity * 100, 2) if equity else None,
                         "tag": tag})
        missed = []
        with_dir = [t for t in mine if ((1 if (t.get("plan") or {}).get("opt", "CE") == "CE" else -1) > 0) == up]
        if trend and not with_dir:
            sk = [s for s in skips if s["symbol"] == sym]
            ceil = _ceilings(vix_open)
            if sk:
                for s in sk:
                    missed.append({"cause": s["cause"], "time": s["time"], "msg": s["msg"]})
            elif ceil and len(ceil) >= 2 or (ceil and "all rules" in ceil[0]):
                missed.append({"cause": "vix_ceiling", "msg": f"India VIX {vix_open:.1f} at the open: " + ", ".join(ceil) + " off"})
            else:
                first = c[[(t + timedelta(minutes=C.CANDLE_MIN)).time() <= C.FIRST_CHECK for t in c.index]]
                done = (float(first["close"].iloc[-1]) - o) / move if len(first) and move else 0
                if done >= 0.6:
                    missed.append({"cause": "opening_drive", "msg": f"{done:.0%} of the day's move was done by {C.FIRST_CHECK:%H:%M}"})
                else:
                    sigs = [r for r in recos if r.get("symbol") == sym]
                    missed.append({"cause": "no_signal" if not sigs else "other_skip",
                                   "msg": "no rule fired in the trade's direction" if not sigs else f"{len(sigs)} signal(s) logged but none placed"})
            for x in missed:
                causes.append({"symbol": sym, "cause": x["cause"], "pts": round(abs(move), 1), "rs": None})
        if trend and with_dir:                                   # a signal skipped on a trend day still cost a chance
            for sk in (x for x in skips if x["symbol"] == sym):
                causes.append({"symbol": sym, "cause": sk["cause"], "pts": None, "rs": None})
        per.append({"symbol": sym, "regime": st.get("regime"), "move_pts": round(move, 1), "move_pct": round(mv_pct, 2), "trend_day": trend,
                    "caught_pts": round(caught_pts, 1), "caught_share": round(caught_pts / abs(move), 2) if trend and move else None,
                    "pnl": round(rupees), "trades": rows, "missed": missed})
    cap = _capital(equity, prefs, trades)
    rec = {"day": day, "causes": causes, "trend_days": sum(1 for p in per if p["trend_day"]),
           "trend_caught": sum(1 for p in per if p["trend_day"] and (p["caught_share"] or 0) > 0.2)}
    try:
        store._cmd("HSET", KEY_DAYS, day, json.dumps(rec, separators=(",", ":")))
    except Exception:
        pass
    return {"indices": per, "capital": cap, "skips": skips, "rolling": rolling(day), "headline": _headline(per, causes)}


def _capital(equity, prefs, trades) -> dict | None:
    if not equity:
        return None
    prefs = prefs or {}
    rp = float(prefs.get("risk_pct") or C.RISK_PER_TRADE * 100) / 100
    risk_lot = []
    for t in trades:
        pl = t.get("plan") or {}
        lots = max(int(t.get("lots") or 1), 1)
        rl = abs(float(t.get("delta") or 0.55)) * float(pl.get("risk_pts") or 0) * float(t.get("qty") or 0) / lots
        if rl > 0:
            risk_lot.append(rl)
    typical = sum(risk_lot) / len(risk_lot) if risk_lot else 2000.0
    used = [float(t.get("margin") or 0) for t in trades]
    return {"equity": round(equity), "risk_pct": rp * 100, "risk_budget": round(equity * rp), "typical_risk_per_lot": round(typical),
            "lots_today": [int(t.get("lots") or 0) for t in trades], "max_capital_in_one_trade_pct": round(max(used) / equity * 100, 1) if used else 0,
            "two_lots_from_equity": round(2 * typical / rp, -3), "three_lots_from_equity": round(3 * typical / rp, -3),
            "note": ("Lots = risk budget ÷ risk of one lot, re-worked from equity before every trade (Autopilot compounds). "
                     "Using more of the cash was tested (r25): bigger risk per trade lifts good years but brought 40-80% falls.")}


def _headline(per, causes) -> str:
    bits = []
    for p in per:
        if p["trend_day"]:
            share = p["caught_share"]
            bits.append(f"{p['symbol']} trend {p['move_pct']:+.1f}%: caught {share:.0%}" if share is not None else f"{p['symbol']} trend {p['move_pct']:+.1f}%")
    if causes:
        top = max(causes, key=lambda x: (x.get("rs") or 0, x.get("pts") or 0))
        bits.append("main lag: " + CAUSES.get(top["cause"], (top["cause"],))[0].split(":")[0].lower())
    return " | ".join(bits) or "No trend day: nothing missed worth noting."


def rolling(day: str | None = None, n: int = WINDOW) -> dict:
    raw = store._cmd("HGETALL", KEY_DAYS) or []
    h = raw if isinstance(raw, dict) else {raw[i]: raw[i + 1] for i in range(0, len(raw), 2)}
    days = sorted(k for k in h if not day or k <= day)[-n:]
    tally, tdays, tcaught = {}, 0, 0
    for d in days:
        r = json.loads(h[d])
        tdays += r.get("trend_days", 0)
        tcaught += r.get("trend_caught", 0)
        for x in r.get("causes", []):
            t = tally.setdefault(x["cause"], {"n": 0, "pts": 0.0, "rs": 0.0, "days": set()})
            t["n"] += 1
            t["pts"] += float(x.get("pts") or 0)
            t["rs"] += float(x.get("rs") or 0)
            t["days"].add(d)
    rows = []
    for k, t in sorted(tally.items(), key=lambda kv: (-kv[1]["rs"], -kv[1]["n"])):
        label, cards, verdict = CAUSES.get(k, (k, "-", "Not researched yet."))
        rows.append({"cause": k, "label": label, "times": t["n"], "days": len(t["days"]), "index_pts": round(t["pts"]),
                     "rs_left": round(t["rs"]), "research": cards, "verdict": verdict, "watch": t["n"] >= FLAG_MIN})
    watch = [r for r in rows if r["watch"]]
    return {"sessions": len(days), "trend_days": tdays, "trend_days_caught": tcaught, "causes": rows,
            "improvement_watch": watch,
            "summary": (f"Last {len(days)} sessions: {tcaught} of {tdays} trend-day index moves caught. "
                        + ("Most frequent lag: " + watch[0]["label"].lower() + f" ({watch[0]['times']}×). " + watch[0]["verdict"] if watch else
                           "No lag cause has repeated enough to act on."))}
