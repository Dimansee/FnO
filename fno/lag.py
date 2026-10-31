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
    "catcher_gate": ("An own-slot rule waiting for its equity level (catcher Rs 40k, Fibonacci Rs 2 lakh)", "r24, r29",
                     "Small-account gates: below these levels the extra rule added more risk than profit."),
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


# ---------------------------------------------------------------- weekly improvement review (31 Oct 2026)
# The user: "improvement fixes recorded after the market - where it lagged - or from the learning of the whole week at once".
# Every session's lag review, journal decisions and trades of one ISO week are read together and turned into a short list
# of fixes. Like the daily review, nothing here changes a rule by itself: each fix says what the research already found and
# whether it is "keep as is", "research next" or "your action" (e.g. capital, a setting, a broker login).
FIX_TEXT = {
    "weekly_limit": ("Weekly loss limit blocked entries", "r18b, r20c",
                     "Adopted rule: in the tests stopping for the week improved profit and the deepest fall. Keep; you can restart the weekly counter yourself in Autopilot → Status."),
    "daily_limit": ("Daily limit blocked entries", "r19c", "Tested rule; keep."),
    "money": ("Not enough cash for the signal", "r25", "Your action: more capital, or fewer positions open at once. Sizing up beyond cash was tested and brought 40-80% falls."),
    "risk_cap": ("1 lot risked too much for the account size", "r20b, r25", "Small-account cap. Grows away as equity grows; a bigger cap did not help in rolling 12-month windows."),
    "shield": ("Capital shield cut or blocked a trade", "safety", "Your safety rule (Autopilot → Safety). Loosen it only knowing the worst case grows with it."),
    "data": ("Market data was not reliable", "ops", "Your action: keep the broker logged in (live candles / VIX) - Yahoo fallback is delayed."),
    "slot_busy": ("Signal blocked by a trade already open", "r23", "Allowing both rules at once lost in 2025 and deepened the fall (r23). Keep."),
    "learn_veto": ("Learning-engine veto", "learning", "Guarded change with its own evidence (Learning tab); it expires on its own."),
    "big_run": ("Against a big 3-day run", "r42", "Adopted rule; such trades lost in every test period."),
    "spread": ("Option spread too wide", "r34", "Keep: wide spreads cost more than the signal earns."),
    "calendar": ("Calendar rule", "r6", "Keep."),
    "auto_off": ("Autopilot was off for the instrument", "settings", "Your action: tick the instrument in Settings → Auto-trade if you want it traded."),
    "not_attempted": ("Signal changed before the minute check", "ops", "Watch: if this repeats on winners, research an entry on the signal bar itself."),
}


def weekly(week: str | None = None) -> dict:
    """week = 'YYYY-Www' (ISO); default the current week (or last week on a weekend before any trade)."""
    from . import journal as J
    from . import paper as P
    today = C.today_ist()
    if week:
        y, w = week.split("-W")
        monday = date.fromisocalendar(int(y), int(w), 1)
    else:
        monday = today - timedelta(days=today.weekday())
    days = [(monday + timedelta(days=i)).isoformat() for i in range(5)]
    wk = f"{monday.isocalendar()[0]}-W{monday.isocalendar()[1]:02d}"
    raw = store._cmd("HGETALL", KEY_DAYS) or []
    lagh = raw if isinstance(raw, dict) else {raw[i]: raw[i + 1] for i in range(0, len(raw), 2)}
    sessions, skip_tally, cause_tally = [], {}, {}
    exec_n = skip_n = 0
    missed_r = {}
    for d in days:
        if d > today.isoformat():
            break
        try:
            jd = J.day(d)
        except Exception:
            jd = None
        dec = (jd or {}).get("decisions") or {"rows": [], "blocks": []}
        rows = dec.get("rows", [])
        if not rows and not dec.get("blocks") and d not in lagh:
            continue
        e = sum(1 for r in rows if r["status"]["state"] == "executed")
        s = [r for r in rows if r["status"]["state"] == "skipped"]
        exec_n += e
        skip_n += len(s)
        for r in s:
            cat = r["status"].get("cat") or "other"
            t = skip_tally.setdefault(cat, {"n": 0, "R": 0.0, "won": 0, "label": r["status"].get("label"), "examples": []})
            t["n"] += 1
            res = r.get("result") or {}
            if res.get("R") is not None:
                t["R"] += float(res["R"])
                t["won"] += 1 if res["R"] > 0 else 0
            if len(t["examples"]) < 3:
                t["examples"].append(f"{d[5:]} {r['time']} {r['symbol']} {r.get('strategy') or ''}: {r['what_happened']}")
        for b in dec.get("blocks", []):
            cat = b["cat"]
            t = skip_tally.setdefault(cat, {"n": 0, "R": 0.0, "won": 0, "label": b.get("label"), "examples": []})
            t.setdefault("blocked_minutes", 0)
            t["blocked_minutes"] = t.get("blocked_minutes", 0) + max(1, (int(b["to"][:2]) * 60 + int(b["to"][3:])) - (int(b["from"][:2]) * 60 + int(b["from"][3:])))
        lr = json.loads(lagh[d]) if d in lagh else {}
        for x in lr.get("causes", []):
            ct = cause_tally.setdefault(x["cause"], {"n": 0, "rs": 0.0, "pts": 0.0})
            ct["n"] += 1
            ct["rs"] += float(x.get("rs") or 0)
            ct["pts"] += float(x.get("pts") or 0)
        sm = (jd or {}).get("summary") or {}
        sessions.append({"date": d, "executed": e, "skipped": len(s), "paper_pnl": sm.get("paper_pnl"), "scalp_pnl": sm.get("scalp_pnl"),
                         "sum_R": sm.get("sum_R"), "trend_days": lr.get("trend_days"), "trend_caught": lr.get("trend_caught"),
                         "lesson": ((jd or {}).get("settled") or {}).get("lesson")})
    tr = [t for t in store.trades() + P.archived() if days[0] <= (t.get("opened") or "")[:10] <= days[-1]]
    sys_tr = [t for t in tr if t.get("mode") == "signal"]
    by_st = {}
    for t in sys_tr:
        k = (t.get("plan") or {}).get("strategy") or "?"
        b = by_st.setdefault(k, {"n": 0, "pnl": 0.0, "wins": 0})
        b["n"] += 1; b["pnl"] += float(t.get("pnl") or 0); b["wins"] += 1 if (t.get("pnl") or 0) > 0 else 0
    try:
        from . import scalp as SC
        sc = [t for t in SC.trades() if days[0] <= (t.get("opened") or "")[:10] <= days[-1]]
    except Exception:
        sc = []
    exits = {}
    for t in sys_tr:
        r = (t.get("reason") or "?").split("(")[0].strip()[:40]
        x = exits.setdefault(r, {"n": 0, "pnl": 0.0}); x["n"] += 1; x["pnl"] += float(t.get("pnl") or 0)
    fixes = []
    for cat, t in sorted(skip_tally.items(), key=lambda kv: -(kv[1]["R"] if kv[1]["R"] > 0 else 0) - kv[1]["n"] * 0.01):
        lab, cards, verdict = FIX_TEXT.get(cat, (t.get("label") or cat, "-", "Not researched yet - flagged for research if it keeps costing."))
        status = "your action" if verdict.startswith("Your action") else "keep" if any(w in verdict for w in ("Keep", "keep", "Adopted", "Tested rule")) else "research next"
        if t["R"] > 1.5 and t["won"] >= 2 and status == "keep":
            status = "watch"
        fixes.append({"area": "entries", "cat": cat, "title": lab, "times": t["n"], "blocked_minutes": t.get("blocked_minutes"),
                      "missed_R": round(t["R"], 2) if t["n"] else None, "would_have_won": t["won"], "research": cards, "verdict": verdict,
                      "status": status, "examples": t["examples"]})
    for cause, ct in sorted(cause_tally.items(), key=lambda kv: -kv[1]["rs"]):
        label, cards, verdict = CAUSES.get(cause, (cause, "-", "Not researched yet."))
        fixes.append({"area": "lag", "cat": cause, "title": label, "times": ct["n"], "rs_left": round(ct["rs"]), "pts": round(ct["pts"]),
                      "research": cards, "verdict": verdict, "status": "research next" if ct["n"] >= FLAG_MIN and "No change" not in verdict else "keep"})
    for r, x in sorted(exits.items(), key=lambda kv: kv[1]["pnl"]):
        if x["n"] >= 2 and x["pnl"] < 0:
            fixes.append({"area": "exits", "cat": "exit:" + r, "title": f"Exits by '{r}' lost ₹{-x['pnl']:,.0f} over {x['n']} trades", "times": x["n"],
                          "research": "r23, r40-r41", "verdict": "Exit rules were tested (trail / time stop / targets). One week is far too few trades to change them; it is logged for the 60-session forward check.",
                          "status": "keep"})
    pnl = round(sum(float(t.get("pnl") or 0) for t in sys_tr))
    spnl = round(sum(float(t.get("pnl") or 0) for t in sc))
    head = (f"Week {wk}: {len(sessions)} session(s), Autopilot {exec_n} executed / {skip_n} skipped, system P&L ₹{pnl:+,.0f}"
            + (f", scalp ₹{spnl:+,.0f}" if sc else "") + ".")
    top = next((f for f in fixes if f["status"] in ("your action", "research next", "watch")), None)
    if top:
        head += f" First thing to look at: {top['title'].lower()} ({top['times']}×) - {top['status']}."
    return {"week": wk, "monday": days[0], "headline": head, "sessions": sessions, "executed": exec_n, "skipped": skip_n,
            "system_pnl": pnl, "scalp_pnl": spnl, "scalp_trades": len(sc), "by_strategy": {k: {**v, "pnl": round(v["pnl"])} for k, v in by_st.items()},
            "exits": {k: {**v, "pnl": round(v["pnl"])} for k, v in exits.items()}, "fixes": fixes,
            "rolling": rolling(), "note": "Nothing here changes a rule on its own. 'Keep' = already tested; 'research next' = flagged for a research round; "
                                          "'watch' = a tested rule that cost money this week - judged over 60 sessions, not one week; 'your action' = a setting, capital or login."}
