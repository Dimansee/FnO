"""The learning engine: a lesson for every closed trade, a research note after every close, and guarded self-improvement.

What it does (all rule-based and deterministic - no AI call is made, so every conclusion can be traced):
  1. on_close_in / on_close_g   a lesson for each closed paper trade (what happened, what kind of trade it was, how
                                trades like it have done), and the evidence tables are updated.
  2. post_close_in / _g         after the close: what the market did today (range, regime, VIX, global cues), what
                                every strategy signalled and how those signals ended, what to watch tomorrow.
  3. review                     turns the evidence into guarded changes ("overlay"):
                                  veto  - skip a setup bucket (e.g. 'midday entries') only when it is significantly
                                          negative over many signals, in both halves of the sample
                                  size  - trade a strategy at half size while its recent results are clearly bad
                                Every change is logged with its evidence, expires on its own and can be reverted.
  4. improve_custom             tries small variants of one of YOUR custom strategies, adopts one only if it is better
                                in every slice of the history, and rolls back if live results fall short.

Modes (Settings -> Learning):  off | safe (vetoes and half-size only, the default) | full (also adopts tested
variants of custom strategies).  Changes to the built-in rules' numbers are never made automatically: they are
proposed with their evidence and wait for your approval.

Evidence sets:  "T" = trades actually taken on paper, "S" = every signal the strategies gave (the journal), including
signals that were skipped.  Vetoes use S (more data, not biased by skipping); size changes use T.
"""
from __future__ import annotations

import json
import math
import secrets
import time as _t
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from . import config as C
from . import store

KEEP_LESSONS = 500
R_KEEP = 120                  # most recent R values kept per evidence bucket
MIN_N_VETO = 25               # signals in a bucket before it can be vetoed
Z_VETO = 2.8                  # strict, because many buckets are looked at (multiple-comparison protection)
MAX_VETOES_PER_STRATEGY = 3
MAX_SHARE_VETOED = 0.40       # a veto may not remove more than 40% of a strategy's signals
VETO_DAYS = 45
SIZE_MIN_N = 20
K_LEARN = "fno:ls:"
MODES = ("off", "safe", "full")

_cache: dict = {}


# ------------------------------------------------------------------ small redis helpers
def _hget(key, field):
    v = store._cmd("HGET", K_LEARN + key, field)
    return json.loads(v) if v else None


def _hset(key, field, val):
    store._cmd("HSET", K_LEARN + key, field, json.dumps(val, default=str, separators=(",", ":")))


def _hgetall(key) -> dict:
    raw = store._cmd("HGETALL", K_LEARN + key) or []
    if isinstance(raw, dict):
        return {k: json.loads(v) for k, v in raw.items()}
    return {raw[i]: json.loads(raw[i + 1]) for i in range(0, len(raw), 2)}


def _index_add(idx, member, score):
    store._cmd("ZADD", K_LEARN + idx, score, member)


def _index_recent(idx, n):
    return store._cmd("ZREVRANGE", K_LEARN + idx, 0, n - 1) or []


def _ts(s) -> float:
    try:
        return datetime.fromisoformat(str(s)).timestamp()
    except Exception:
        return _t.time()


MARKET_KEYS = ("IN", "CRYPTO", "US")


def mode(market: str | None = None) -> str:
    """Learning mode. Each market (Indian options, crypto, US) has its own switch so one never changes the others;
    a market without its own setting follows the overall one."""
    if market in MARKET_KEYS:
        m = store.get(f"learn_mode:{market}")
        if m in MODES:
            return m
    m = store.get("learn_mode")
    return m if m in MODES else "safe"


def modes() -> dict:
    return {k: mode(k) for k in MARKET_KEYS}


def set_mode(m: str, market: str | None = None):
    if m not in MODES:
        raise ValueError("Learning mode must be off, safe or full.")
    if market in MARKET_KEYS:
        store.put(f"learn_mode:{market}", m)
    else:                                   # "all markets": set each one so they stay independent afterwards
        store.put("learn_mode", m)
        for k in MARKET_KEYS:
            store.put(f"learn_mode:{k}", m)
    _cache.pop("overlay", None)


# ------------------------------------------------------------------ tags
def _tod_in(hhmm: str) -> str:
    return "open" if hhmm < "10:15" else "morning" if hhmm < "12:00" else "midday" if hhmm < "13:30" else "afternoon"


def _tod_utc(h: int) -> str:
    return "asia" if h < 7 else "europe" if h < 13 else "us" if h < 21 else "late"


def _tod_us(hhmm: str) -> str:
    return "open" if hhmm < "10:30" else "midday" if hhmm < "14:30" else "close"


def tags_for(d: dict) -> list[str]:
    """d: {market, time 'HH:MM' (local to the market), side 'CE'/'PE'/'long'/'short', vix, trend 'up'/'down'/'mixed', gap_pct, dte, wd (0=Mon), vol 'low'/'mid'/'high'}"""
    m = d.get("market", "IN")
    tags = []
    side = d.get("side")
    bull = side in ("CE", "long")
    if side:
        tags.append("side:" + ("long" if bull else "short"))
    t = d.get("time")
    if t:
        tags.append("tod:" + (_tod_in(t) if m == "IN" else _tod_us(t) if m == "US" else _tod_utc(int(t[:2]))))
    if d.get("wd") is not None:
        tags.append("dow:" + ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[int(d["wd"])])
    tr = d.get("trend")
    if tr in ("up", "down"):
        tags.append("trend:" + ("with" if (tr == "up") == bull else "against"))
    elif tr == "mixed":
        tags.append("trend:mixed")
    if m == "IN":
        v = d.get("vix")
        if v:
            tags.append("vix:" + ("low" if v < 13 else "mid" if v <= 17 else "high"))
        g = d.get("gap_pct")
        if g is not None:
            tags.append("gap:" + ("big" if abs(g) >= 0.5 else "small"))
        if d.get("dte") is not None:
            tags.append("dte:" + ("0-1" if d["dte"] <= 1 else "2-3" if d["dte"] <= 3 else "4+"))
    elif d.get("vol"):
        tags.append("vol:" + d["vol"])
    return tags


# ------------------------------------------------------------------ evidence tables
def _bump(sets: str, market: str, strategy: str, tags: list[str], R: float):
    fields = [f"{sets}|{market}|{strategy}|{t}" for t in ["all"] + tags]
    cur = store._cmd("HMGET", K_LEARN + "st", *fields) or []
    args = []
    for f, v in zip(fields, cur):
        o = json.loads(v) if v else {"n": 0, "s": 0.0, "r": []}
        o["n"] += 1
        o["s"] = round(o["s"] + R, 4)
        o["r"] = (o["r"] + [round(R, 3)])[-R_KEEP:]
        args += [f, json.dumps(o, separators=(",", ":"))]
    store._cmd("HSET", K_LEARN + "st", *args)


def _stat(sets, market, strategy, tag="all") -> dict | None:
    return _hget("st", f"{sets}|{market}|{strategy}|{tag}")


def _mean_se(r):
    r = np.asarray(r, dtype=float)
    n = len(r)
    if n == 0:
        return 0.0, 0.0, 0
    sd = float(r.std(ddof=1)) if n > 1 else 1.0
    return float(r.mean()), sd / math.sqrt(n), n


def all_stats(sets="S") -> dict:
    return {k: v for k, v in _hgetall("st").items() if k.startswith(sets + "|")}


# ------------------------------------------------------------------ lessons
def _held_text(mins):
    if mins is None:
        return "?"
    return f"{mins:.0f} min" if mins < 120 else f"{mins / 60:.1f} h"


def classify(R, mfe, mae, held_min, reason) -> str:
    if R is None:
        return "unknown"
    if mfe is not None and mfe >= 1.0 and R < 0.3:
        return "gave_back"
    if R <= -0.8 and held_min is not None and held_min <= 10:
        return "fast_stop"
    if R <= -0.8:
        return "stopped"
    if R >= 1.0 and (mae is None or mae < 0.6):
        return "clean_win"
    if R > 0.3:
        return "win"
    if R < -0.3:
        return "small_loss"
    return "scratch"


KIND_TEXT = {
    "clean_win": "A clean winner: it went mostly your way without a deep dip first.",
    "win": "A win, though not a full-size one.",
    "gave_back": "It reached +{mfe:.1f}R and then gave most of it back. This is the pattern a break-even or trailing rule is meant to catch.",
    "fast_stop": "Stopped out within minutes of entry: the entry was probably early or into a spike. Look at the entry candle.",
    "stopped": "A full stop-loss. These are part of the plan; what matters is whether this setup keeps losing (see 'similar trades').",
    "small_loss": "A small loss: exited by a rule before the stop.",
    "scratch": "Roughly break-even.",
    "unknown": "The exit price was not recorded for this trade, so R could not be measured.",
}


def _lesson(t: dict, tags: list[str]) -> dict:
    kind = classify(t["R"], t.get("mfe_R"), t.get("mae_R"), t.get("held_min"), t.get("reason"))
    money = f"₹{t['pnl']:+,.0f}" if t.get("pnl_label") else f"{t['pnl']:+,.2f} {t.get('ccy', '')}".strip()
    parts = [f"{t['strategy_name']} {t['side_label']} on {t['symbol']}: " + (f"{t['R']:+.2f}R" if t["R"] is not None else "R n/a")
             + f" ({money}), {t['reason']}, held {_held_text(t.get('held_min'))}."]
    parts.append(KIND_TEXT[kind].format(mfe=t.get("mfe_R") or 0))
    # what similar trades have done: the least-comfortable bucket with enough data, else the best
    best = worst = None
    for tg in tags:
        s = _stat("S", t["market"], t["strategy"], tg) or _stat("T", t["market"], t["strategy"], tg)
        if not s or s["n"] < 8:
            continue
        mean, se, n = _mean_se(s["r"])
        if worst is None or mean < worst[1]:
            worst = (tg, mean, n)
        if best is None or mean > best[1]:
            best = (tg, mean, n)
    if worst and worst[1] < -0.1:
        parts.append(f"Watch: '{worst[0]}' setups for this strategy average {worst[1]:+.2f}R over {worst[2]} signals.")
    elif best and best[1] > 0.1:
        parts.append(f"In its favour: '{best[0]}' setups average {best[1]:+.2f}R over {best[2]} signals.")
    flags = []
    if kind == "gave_back":
        s = _stat("T", t["market"], t["strategy"], "kind:gave_back")
        tot = _stat("T", t["market"], t["strategy"], "all")
        if s and tot and tot["n"] >= 10 and s["n"] / tot["n"] >= 0.25:
            parts.append(f"{s['n']} of {tot['n']} trades of this strategy reached +1R and gave it back - a break-even or trailing rule is worth testing on it.")
            flags.append("gave_back_pattern")
    return {"kind": kind, "text": " ".join(parts), "flags": flags}


def _record(t: dict, extra_tags: list[str]):
    """Common path for a normalised closed trade (market-independent)."""
    tags = tags_for(t["ctx"]) + extra_tags
    kind = classify(t["R"], t.get("mfe_R"), t.get("mae_R"), t.get("held_min"), t.get("reason"))
    les = _lesson(t, tags)
    if t["R"] is not None:
        _bump("T", t["market"], t["strategy"], tags + [f"kind:{kind}"], t["R"])
    out = {"id": t["id"], "market": t["market"], "symbol": t["symbol"], "strategy": t["strategy"], "strategy_name": t["strategy_name"],
           "side": t["side_label"], "opened": t["opened"], "closed": t["closed"], "R": t["R"], "pnl": t["pnl"], "reason": t["reason"],
           "mfe_R": t.get("mfe_R"), "mae_R": t.get("mae_R"), "held_min": t.get("held_min"), "tags": tags, **les}
    _hset("lessons", t["id"], out)
    _index_add("lessons:idx", t["id"], _ts(t["closed"]))
    n = store._cmd("ZCARD", K_LEARN + "lessons:idx") or 0
    if n > KEEP_LESSONS:
        old = store._cmd("ZRANGE", K_LEARN + "lessons:idx", 0, n - KEEP_LESSONS - 1) or []
        for oid in old:
            store._cmd("HDEL", K_LEARN + "lessons", oid)
            store._cmd("ZREM", K_LEARN + "lessons:idx", oid)
    _cache.pop("summary", None)
    try:
        review(t["market"])
    except Exception:
        pass
    return out


def _strategy_name(st: str) -> str:
    if st.startswith("custom:"):
        from . import custom as CU
        try:
            sp = next((x for x in CU.load_all(store) if x["id"] == st[7:]), None)
            return sp["name"] if sp else st
        except Exception:
            return st
    return {"noise": "Noise-area", "camarilla": "Camarilla", "ai": "AI model", "trend": "Trend-day", "fib": "Fibonacci"}.get(st, st)


def on_close_in(rec: dict):
    """A closed Indian options paper trade (called by paper.close_position)."""
    plan = rec.get("plan") or {}
    if rec.get("mode") != "signal" or not plan or mode("IN") == "off":
        return None
    sign = 1 if plan.get("opt") == "CE" else -1
    entry, risk, xs = plan.get("entry"), plan.get("risk_pts"), rec.get("spot_exit")
    R = round(sign * (xs - entry) / risk, 3) if entry is not None and risk and xs is not None else None
    hi, lo = rec.get("hi"), rec.get("lo")
    mfe = mae = None
    if entry is not None and risk and hi is not None and lo is not None:
        mfe = max(((hi - entry) if sign > 0 else (entry - lo)) / risk, 0.0)
        mae = max(((entry - lo) if sign > 0 else (hi - entry)) / risk, 0.0)
    o, c = datetime.fromisoformat(rec["opened"]), datetime.fromisoformat(rec["closed"])
    ctx = {**(plan.get("ctx") or {}), "market": "IN", "side": plan.get("opt"), "time": o.strftime("%H:%M"), "wd": o.weekday()}
    t = {"id": rec["id"], "market": "IN", "symbol": rec["symbol"], "strategy": plan.get("strategy") or "noise", "side_label": "CALL" if sign > 0 else "PUT",
         "opened": rec["opened"], "closed": rec["closed"], "R": R, "pnl": rec["pnl"], "pnl_label": "inr", "reason": rec.get("reason") or "",
         "mfe_R": None if mfe is None else round(mfe, 2), "mae_R": None if mae is None else round(mae, 2),
         "held_min": (c - o).total_seconds() / 60, "ctx": ctx}
    t["strategy_name"] = _strategy_name(t["strategy"])
    return _record(t, [])


def on_close_g(rec: dict):
    """A closed crypto / US paper trade (called by gbook)."""
    if mode(rec.get("market")) == "off":
        return None
    o, c = datetime.fromisoformat(rec["opened"]), datetime.fromisoformat(rec["closed"])
    ctx = {**(rec.get("ctx") or {}), "market": rec["market"], "side": rec["side"], "wd": o.weekday(),
           "time": (rec.get("ctx") or {}).get("local_time") or o.strftime("%H:%M")}
    t = {"id": rec["id"], "market": rec["market"], "symbol": rec["symbol"], "strategy": f"custom:{rec['strategy_id']}", "side_label": rec["side"].upper(),
         "opened": rec["opened"], "closed": rec["closed"], "R": rec.get("R"), "pnl": rec["pnl"], "ccy": "USD" if rec["market"] == "US" else "USDT",
         "reason": rec.get("reason") or "", "mfe_R": rec.get("mfe_R"), "mae_R": rec.get("mae_R"), "held_min": (c - o).total_seconds() / 60, "ctx": ctx}
    t["strategy_name"] = rec.get("strategy_name") or _strategy_name(t["strategy"])
    return _record(t, [])


# ------------------------------------------------------------------ overlay (the guarded changes)
def overlay() -> dict:
    hit = _cache.get("overlay")
    if hit and _t.time() - hit[0] < 45:
        return hit[1]
    ov = store.get("learn_overlay") or {"rules": []}
    today = C.today_ist().isoformat()
    ov["rules"] = [r for r in ov.get("rules", []) if not r.get("expires") or r["expires"] >= today]
    _cache["overlay"] = (_t.time(), ov)
    return ov


def _save_overlay(ov):
    store.put("learn_overlay", ov)
    _cache["overlay"] = (_t.time(), ov)
    _cache.pop("summary", None)


def veto(market: str, strategy: str, tags: list[str]) -> str | None:
    """Reason to skip an AUTOMATIC entry now, or None. Only in safe/full mode."""
    if mode(market) == "off":
        return None
    for r in overlay().get("rules", []):
        if r["type"] == "veto" and r["market"] == market and r["strategy"] == strategy and r["tag"] in tags:
            return r["reason"]
    return None


def size_mult(market: str, strategy: str) -> float:
    if mode(market) == "off":
        return 1.0
    m = 1.0
    for r in overlay().get("rules", []):
        if r["type"] == "size" and r["market"] == market and r["strategy"] == strategy:
            m = min(m, float(r["mult"]))
    return m


def _log_change(kind, title, evidence, rule=None, status="applied", before=None, after=None, market=None, strategy=None) -> dict:
    cid = "c_" + secrets.token_hex(3)
    ch = {"id": cid, "ts": C.now_ist().isoformat(timespec="seconds"), "kind": kind, "title": title, "evidence": evidence, "status": status,
          "market": market, "strategy": strategy, "before": before, "after": after, "rule": rule}
    _hset("changes", cid, ch)
    _index_add("changes:idx", cid, _t.time())
    _cache.pop("summary", None)
    return ch


def _set_change_status(cid, status, note=None):
    ch = _hget("changes", cid)
    if ch:
        ch["status"] = status
        if note:
            ch["note"] = note
        ch["updated"] = C.now_ist().isoformat(timespec="seconds")
        _hset("changes", cid, ch)
        _cache.pop("summary", None)


def review(market: str) -> list[dict]:
    """Look at the evidence for every strategy in this market and add / remove guarded rules. Safe to call often."""
    if mode(market) == "off":
        return []
    ov = overlay()
    rules = list(ov.get("rules", []))
    made = []
    today = C.today_ist()
    st_all = _hgetall("st")
    strategies = sorted({k.split("|")[2] for k in st_all if k.split("|")[1] == market})
    for strat in strategies:
        sname = _strategy_name(strat)
        tot_s = st_all.get(f"S|{market}|{strat}|all")
        # ---- veto buckets (signals evidence)
        if tot_s and tot_s["n"] >= MIN_N_VETO:
            active = [r for r in rules if r["type"] == "veto" and r["market"] == market and r["strategy"] == strat]
            for key, v in st_all.items():
                p = key.split("|", 3)
                if p[0] != "S" or p[1] != market or p[2] != strat or p[3] in ("all",) or p[3].startswith("kind:") or p[3].startswith("dow:") or p[3].startswith("side:"):
                    continue
                tag = p[3]
                if any(r["tag"] == tag for r in active) or len(active) >= MAX_VETOES_PER_STRATEGY:
                    continue
                r = v["r"]
                mean, se, n = _mean_se(r)
                if v["n"] < MIN_N_VETO or n < 20 or se <= 0:
                    continue
                half = n // 2
                m1, m2 = float(np.mean(r[:half])), float(np.mean(r[half:]))
                z = mean / se
                share = v["n"] / max(tot_s["n"], 1)
                if mean <= -0.15 and z <= -Z_VETO and m1 < 0 and m2 < 0 and share <= MAX_SHARE_VETOED:
                    reason = (f"Learning engine: '{tag}' setups for {sname} have averaged {mean:+.2f}R over {v['n']} signals "
                              f"(negative in both halves), so this entry is skipped.")
                    rule = {"id": "r_" + secrets.token_hex(3), "type": "veto", "market": market, "strategy": strat, "tag": tag, "reason": reason,
                            "since": today.isoformat(), "expires": (today + timedelta(days=VETO_DAYS)).isoformat(),
                            "evidence": {"n": v["n"], "mean_R": round(mean, 3), "z": round(z, 2), "first_half": round(m1, 3), "second_half": round(m2, 3)}}
                    if mode(market) in ("safe", "full"):
                        rules.append(rule)
                        active.append(rule)
                        made.append(_log_change("veto", f"Skip '{tag}' entries for {sname} ({market})", rule["evidence"], rule, "applied", market=market, strategy=strat))
        # ---- fade vetoes whose evidence recovered (the skipped signals keep being tracked in the S set)
        for r in [x for x in rules if x["type"] == "veto" and x["market"] == market and x["strategy"] == strat]:
            v = st_all.get(f"S|{market}|{strat}|{r['tag']}")
            if v:
                mean, se, n = _mean_se(v["r"])
                if n >= 20 and mean > -0.05:
                    rules.remove(r)
                    made.append(_log_change("veto_lifted", f"Lifted the skip on '{r['tag']}' for {sname}", {"n": v["n"], "mean_R": round(mean, 3)}, r, "applied", market=market, strategy=strat))
        # ---- size down while recent live trades are clearly bad (trades evidence)
        t_all = st_all.get(f"T|{market}|{strat}|all")
        cur = next((r for r in rules if r["type"] == "size" and r["market"] == market and r["strategy"] == strat), None)
        if t_all and t_all["n"] >= SIZE_MIN_N:
            last = t_all["r"][-20:]
            mean, se, n = _mean_se(last)
            if not cur and n >= SIZE_MIN_N and mean <= -0.25 and mean / max(se, 1e-9) <= -2.0:
                rule = {"id": "r_" + secrets.token_hex(3), "type": "size", "market": market, "strategy": strat, "mult": 0.5, "since": today.isoformat(),
                        "reason": f"{sname}: last {n} trades averaged {mean:+.2f}R, so new positions are half size until it recovers.",
                        "evidence": {"n": n, "mean_R": round(mean, 3), "z": round(mean / se, 2)}}
                rules.append(rule)
                made.append(_log_change("size_down", f"Half size for {sname} ({market})", rule["evidence"], rule, "applied", market=market, strategy=strat))
            elif cur:
                m15 = t_all["r"][-15:]
                if len(m15) >= 15 and np.mean(m15) >= 0:
                    rules.remove(cur)
                    made.append(_log_change("size_restored", f"Full size restored for {sname} ({market})", {"last15_mean_R": round(float(np.mean(m15)), 3)}, cur, "applied", market=market, strategy=strat))
    if made:
        ov["rules"] = rules
        _save_overlay(ov)
        try:
            from . import push as PUSH
            PUSH.send("Learning engine", made[0]["title"], event_id=f"learn:{made[0]['id']}", url="/#learning")
        except Exception:
            pass
    return made


def revert(change_id: str) -> bool:
    ch = _hget("changes", change_id)
    if not ch or ch.get("status") not in ("applied", "pending"):
        return False
    rule = ch.get("rule") or {}
    ov = overlay()
    if rule.get("id"):
        ov["rules"] = [r for r in ov.get("rules", []) if r.get("id") != rule["id"]]
        _save_overlay(ov)
    if ch["kind"] == "variant" and ch.get("before") and ch.get("strategy"):
        from . import custom as CU
        items = CU.load_all(store)
        for i, s in enumerate(items):
            if s["id"] == ch["strategy"][7:]:
                items[i] = {**ch["before"], "version": s.get("version", 1) + 1}
        CU.save_all(store, items)
    _set_change_status(change_id, "reverted")
    return True


# ------------------------------------------------------------------ improving a custom strategy
def _neighbours(spec: dict) -> list[tuple[str, dict]]:
    ex = spec["exit"]
    out = []
    for k in (0.8, 1.25):
        out.append((f"stop {ex['stop_atr'] * k:.2f} ATR", {"stop_atr": round(ex["stop_atr"] * k, 2)}))
    if ex["target_r"]:
        for k in (0.75, 1.5):
            out.append((f"target {ex['target_r'] * k:.2f}R", {"target_r": round(ex["target_r"] * k, 2)}))
    if ex["breakeven_r"] is None:
        out.append(("break-even at 1R", {"breakeven_r": 1.0}))
    if ex["trail_atr"] is None and ex["target_r"]:
        out.append(("trail 2 ATR", {"trail_atr": 2.0, "target_r": None}))
    out.append(("time stop 10 bars", {"time_stop_bars": 10}) if not ex["time_stop_bars"] else ("no time stop", {"time_stop_bars": None}))
    return out[:9]


def improve_custom(spec: dict, df: pd.DataFrame, symbol: str, vix=None) -> dict:
    """Backtest the strategy and a handful of small exit variants on the same data; return a verdict, and adopt a variant only in 'full' mode
    when it beats the current version in every third of the history."""
    from . import custom as CU
    base_trades = CU.simulate(spec, CU.resample(df, spec["timeframe"], spec["market"]), vix)
    if len(base_trades) < 60:
        return {"ok": False, "why": f"only {len(base_trades)} historical trades (need 60) - not enough to judge changes"}
    def folds(tr):
        n = len(tr)
        return [[x["R"] for x in tr[i * n // 3:(i + 1) * n // 3]] for i in range(3)]
    b_f = folds(base_trades)
    b_r = [x["R"] for x in base_trades]
    b_mean, b_se, _ = _mean_se(b_r)
    best = None
    for label, change in _neighbours(spec):
        v = {**spec, "exit": {**spec["exit"], **change}}
        try:
            tr = CU.simulate(v, CU.resample(df, v["timeframe"], v["market"]), vix)
        except Exception:
            continue
        if len(tr) < 0.7 * len(base_trades):
            continue
        r = [x["R"] for x in tr]
        mean, se, _ = _mean_se(r)
        f = folds(tr)
        wins = sum(1 for a, b in zip(f, b_f) if a and b and np.mean(a) > np.mean(b))
        delta = mean - b_mean
        need = 1.65 * math.sqrt(se ** 2 + b_se ** 2) + 0.03
        if delta > need and wins == 3 and (best is None or delta > best["delta"]):
            best = {"label": label, "change": change, "delta": delta, "mean": mean, "base_mean": b_mean, "n": len(tr), "folds": wins, "spec": v}
    if not best:
        return {"ok": True, "adopted": False, "why": f"no variant was better than the current version in all three slices of {len(base_trades)} trades (current {b_mean:+.2f}R per trade)"}
    return {"ok": True, "adopted": False, "candidate": {k: best[k] for k in ("label", "change", "delta", "mean", "base_mean", "n", "folds")}, "_spec": best["spec"]}


def apply_variant(spec_id: str, result: dict, auto: bool) -> dict | None:
    """Store a tested variant. auto=True (full mode) writes it now and leaves a monitor; otherwise it waits for approval."""
    from . import custom as CU
    cand = result.get("candidate")
    if not cand:
        return None
    items = CU.load_all(store)
    cur = next((s for s in items if s["id"] == spec_id), None)
    if not cur:
        return None
    new = {**cur, "exit": {**cur["exit"], **cand["change"]}}
    ev = {"change": cand["label"], "gain_R_per_trade": round(cand["delta"], 3), "from": round(cand["base_mean"], 3), "to": round(cand["mean"], 3),
          "trades": cand["n"], "better_in_all_3_slices": True}
    rule = None
    ch = _log_change("variant", f"{cur['name']}: {cand['label']}", ev, rule, "applied" if auto else "pending", before=cur, after=new, market=cur["market"], strategy=f"custom:{spec_id}")
    if auto:
        CU.upsert(store, {**new, "id": spec_id})
        store.put(f"learn_monitor:{spec_id}", {"change": ch["id"], "since": C.now_ist().isoformat(timespec="seconds"), "expect": cand["mean"], "n0": (_stat("T", cur["market"], f"custom:{spec_id}") or {"n": 0})["n"]})
    return ch


def apply_pending(change_id: str) -> bool:
    ch = _hget("changes", change_id)
    if not ch or ch.get("status") != "pending" or ch["kind"] != "variant":
        return False
    from . import custom as CU
    CU.upsert(store, {**ch["after"], "id": ch["strategy"][7:]})
    _set_change_status(change_id, "applied", "approved by you")
    return True


def monitor_variants():
    """Roll back an adopted variant if the next live trades fall clearly short of what the backtest promised."""
    from . import custom as CU
    for s in CU.load_all(store):
        mon = store.get(f"learn_monitor:{s['id']}")
        if not mon:
            continue
        t = _stat("T", s["market"], f"custom:{s['id']}")
        if not t:
            continue
        new = t["r"][-(t["n"] - mon["n0"]):] if t["n"] > mon["n0"] else []
        if len(new) >= 15:
            mean, se, n = _mean_se(new)
            if mean < mon["expect"] - 1.5 * se - 0.1:
                revert(mon["change"])
                _set_change_status(mon["change"], "reverted", f"auto-reverted: {n} live trades averaged {mean:+.2f}R vs {mon['expect']:+.2f}R expected")
            store.delete_setting(f"learn_monitor:{s['id']}")


# ------------------------------------------------------------------ read side
def lessons(n=40, market=None) -> list[dict]:
    ids = _index_recent("lessons:idx", n * 2 if market else n)
    if not ids:
        return []
    raw = store._cmd("HMGET", K_LEARN + "lessons", *ids) or []
    out = [json.loads(x) for x in raw if x]
    if market:
        out = [x for x in out if x["market"] == market]
    return out[:n]


def changes(n=30) -> list[dict]:
    ids = _index_recent("changes:idx", n)
    if not ids:
        return []
    raw = store._cmd("HMGET", K_LEARN + "changes", *ids) or []
    return [json.loads(x) for x in raw if x]


def research_list(market=None, n=14) -> list[dict]:
    ids = _index_recent("research:idx", n * 2)
    if not ids:
        return []
    raw = store._cmd("HMGET", K_LEARN + "research", *ids) or []
    out = [json.loads(x) for x in raw if x]
    if market:
        out = [x for x in out if x["market"] == market]
    return out[:n]


def leaderboard(market: str | None = None, limit=8) -> dict:
    """Best and worst setup buckets by signal evidence (n >= 10)."""
    rows = []
    for k, v in all_stats("S").items():
        _, m, strat, tag = k.split("|", 3)
        if market and m != market or tag == "all" or v["n"] < 10 or tag.startswith("kind:"):
            continue
        mean, se, n = _mean_se(v["r"])
        rows.append({"market": m, "strategy": strat, "name": _strategy_name(strat), "tag": tag, "n": v["n"], "mean_R": round(mean, 3), "z": round(mean / se, 2) if se else 0})
    rows.sort(key=lambda r: r["mean_R"])
    return {"worst": rows[:limit], "best": list(reversed(rows[-limit:]))}


def strategy_health(market: str | None = None) -> list[dict]:
    out = []
    st = _hgetall("st")
    for k, v in st.items():
        s, m, strat, tag = k.split("|", 3)
        if tag != "all" or (market and m != market):
            continue
        out.append({"set": s, "market": m, "strategy": strat, "name": _strategy_name(strat), "n": v["n"], "avg_R": round(v["s"] / v["n"], 3) if v["n"] else 0,
                    "last20_R": round(float(np.mean(v["r"][-20:])), 3) if v["r"] else 0, "total_R": round(v["s"], 2)})
    return sorted(out, key=lambda x: (x["market"], x["strategy"], x["set"]))


def summary(market: str | None = None) -> dict:
    ov = overlay()
    return {"mode": mode(market), "modes": modes(), "rules": ov.get("rules", []), "changes": changes(30), "lessons": lessons(30, market), "research": research_list(market, 10),
            "leaderboard": leaderboard(market), "health": strategy_health(market),
            "limits": {"min_signals_veto": MIN_N_VETO, "z_veto": Z_VETO, "veto_days": VETO_DAYS, "max_vetoes_per_strategy": MAX_VETOES_PER_STRATEGY}}


# ------------------------------------------------------------------ after the close: Indian market
def _day_stats(day_df: pd.DataFrame, hist: pd.DataFrame) -> dict:
    o, h, l, c = float(day_df["open"].iloc[0]), float(day_df["high"].max()), float(day_df["low"].min()), float(day_df["close"].iloc[-1])
    rng = h - l
    moves = day_df["close"].diff().abs().sum()
    er = abs(c - o) / moves if moves else 0.0
    pc = float(hist["close"].iloc[-1]) if len(hist) else None
    ranges = []
    if len(hist):
        g = hist.groupby(hist.index.date)
        ranges = [(x["high"].max() - x["low"].min()) / x["close"].iloc[-1] * 100 for _, x in list(g)[-14:]]
    avg_rng = float(np.mean(ranges)) if ranges else None
    body = abs(c - o) / rng if rng else 0
    rng_pct = rng / c * 100
    vs_avg = rng_pct / avg_rng if avg_rng else None
    first_hour = day_df.iloc[:12]
    reversal = bool(len(first_hour) and rng and ((first_hour["high"].max() - o) / rng > 0.7 and c < o) or ((o - first_hour["low"].min()) / rng > 0.7 and c > o))
    if body >= 0.55 and (vs_avg or 1) >= 0.8:
        regime = "trend day (up)" if c > o else "trend day (down)"
    elif body <= 0.25:
        regime = "range / chop day"
    elif reversal:
        regime = "reversal day"
    else:
        regime = "mixed day"
    gap = (o / pc - 1) * 100 if pc else None
    return {"open": round(o, 1), "high": round(h, 1), "low": round(l, 1), "close": round(c, 1), "chg_pct": round((c / pc - 1) * 100, 2) if pc else None,
            "range_pct": round(rng_pct, 2), "range_vs_14d": round(vs_avg, 2) if vs_avg else None, "efficiency": round(er, 2), "body_share": round(body, 2),
            "close_in_range": round((c - l) / rng, 2) if rng else None, "gap_pct": round(gap, 2) if gap is not None else None, "regime": regime}


def _insights_in(sym_stats: dict, signals: list[dict], trades: list[dict]) -> list[str]:
    out = []
    for sym, s in sym_stats.items():
        bits = [f"{sym}: {s['regime']}, {s['chg_pct']:+.2f}%" if s["chg_pct"] is not None else f"{sym}: {s['regime']}"]
        if s["range_vs_14d"]:
            bits.append(f"range {s['range_vs_14d']:.1f}× the 14-day average")
        if s["gap_pct"] is not None and abs(s["gap_pct"]) >= 0.5:
            bits.append(f"opened with a {s['gap_pct']:+.2f}% gap")
        out.append(", ".join(bits) + ".")
    if signals:
        by = {}
        for x in signals:
            by.setdefault(x["name"], []).append(x["R"])
        for nm, rs in by.items():
            out.append(f"{nm}: {len(rs)} signal(s), {sum(rs):+.1f}R in total on the index path.")
    else:
        out.append("No strategy signalled today: nothing to learn from entries, only from the market's behaviour.")
    if trades:
        out.append(f"Paper trades closed today: {len(trades)}, net ₹{sum(t['pnl'] for t in trades):+,.0f}.")
    return out


def post_close_in(day: str | None = None, force: bool = False) -> dict:
    """The after-market note for Nifty and Bank Nifty. Idempotent: runs once per day unless force."""
    from . import journal as J
    from . import market as M
    from . import service as SV
    from . import strategy as S
    day = day or C.today_ist().isoformat()
    key = f"IN:{day}"
    if not force and _hget("research", key):
        return {"skipped": "already done", "day": day}
    d = date.fromisoformat(day)
    if d.weekday() >= 5 or d in C.HOLIDAYS:
        return {"skipped": "not a trading day"}
    m = M.Market()
    syms, levels, regs, todays = {}, {}, {}, {}
    for sym in tuple(J.SYMS) + tuple(x for x in C.AUTOPILOT_SYMS if x not in J.SYMS):
        try:
            c = m.candles(sym)
        except Exception:
            continue
        today = c[c.index.date == d]
        hist = c[c.index.date < d]
        if len(today) < 30:
            continue
        syms[sym] = _day_stats(today, hist)
        todays[sym] = today
        regs[sym] = syms[sym]["regime"]
        full = pd.concat([hist, today])
        try:
            full = full.copy()
            lv = S.camarilla_levels(__import__("fno.indicators", fromlist=["x"]).add_indicators(full))
        except Exception:
            lv = None
        ph, pl, pc = float(today["high"].max()), float(today["low"].min()), float(today["close"].iloc[-1])
        levels[sym] = {"prev_high": round(ph, 1), "prev_low": round(pl, 1), "prev_close": round(pc, 1), "pivot": round((ph + pl + pc) / 3, 1),
                       "cam_h3": round(lv["h3"], 1) if lv else None, "cam_h4": round(lv["h4"], 1) if lv else None,
                       "cam_l3": round(lv["l3"], 1) if lv else None, "cam_l4": round(lv["l4"], 1) if lv else None}
    if not syms:
        return {"skipped": "no candles for the day"}
    # signals the strategies gave today (journal) and how they ended
    jd = {}
    try:
        jd = J.day(day)
    except Exception:
        pass
    sig_rows, n_new = [], 0
    for r in jd.get("recommendations", []):
        res = r.get("result")
        if not res:
            continue
        ctx = (jd.get("context") or {}).get(r["symbol"]) or {}
        trend = None
        for f in (r.get("basis") or {}).get("factors", []):
            if f.get("factor", "").startswith("Trend"):
                trend = {"Up": "up", "Down": "down"}.get(f.get("value"), "mixed")
        exp = r.get("option", {}).get("expiry")
        dte = (date.fromisoformat(exp) - d).days if exp else None
        tags = tags_for({"market": "IN", "side": r["opt"], "time": r["time"][:5], "vix": r.get("vix"), "trend": trend, "gap_pct": ctx.get("gap_pct"),
                         "dte": dte, "wd": d.weekday()})
        regime = regs.get(r["symbol"])
        sets_tags = tags + ([f"regime:{regime}"] if regime else [])
        flag = f"fno:ls:seen:{day}"
        if store._cmd("SADD", flag, r["id"]):                     # each signal is counted exactly once
            _bump("S", "IN", r["strategy"], sets_tags, float(res["R"]))
            n_new += 1
            store._cmd("EXPIRE", flag, 7 * 86400)
        sig_rows.append({"id": r["id"], "symbol": r["symbol"], "strategy": r["strategy"], "name": _strategy_name(r["strategy"]), "R": float(res["R"]),
                         "outcome": res["index_outcome"], "time": r["time"][:5]})
    # watch-list custom strategies: replayed on today's bars so they keep learning without trading
    watch = []
    try:
        from . import custom as CU
        for sp in CU.load_all(store):
            if sp.get("market") != "IN" or sp.get("enabled") or not sp.get("watch"):
                continue
            for sym in sp["symbols"][:2]:
                if sym not in syms:
                    continue
                c = m.candles(sym)
                trs = [t for t in CU.simulate(sp, CU.resample(c, sp["timeframe"], "IN")) if t["entry_ts"].date() == d]
                for t in trs:
                    rid = f"watch:{sp['id']}:{sym}:{t['entry_ts']:%H%M}"
                    if store._cmd("SADD", f"fno:ls:seen:{day}", rid):
                        tg = tags_for({"market": "IN", "side": "CE" if t["side"] == "long" else "PE", "time": t["entry_ts"].strftime("%H:%M"), "vix": t.get("vix"), "wd": d.weekday()})
                        _bump("S", "IN", f"custom:{sp['id']}", tg + [f"regime:{regs.get(sym)}"], float(t["R"]))
                    watch.append({"strategy": sp["name"], "symbol": sym, "R": round(t["R"], 2), "side": t["side"]})
    except Exception:
        pass
    trades_today = [t for t in store.trades() if t.get("closed", "")[:10] == day and t.get("mode") == "signal"]
    gc = {}
    try:
        gc = SV.gcues()
    except Exception:
        pass
    vix_now = None
    try:
        vix_now = m.vix()
    except Exception:
        pass
    report = {"market": "IN", "date": day, "headline": " | ".join(f"{s} {v['chg_pct']:+.2f}% ({v['regime']})" for s, v in syms.items() if v["chg_pct"] is not None),
              "symbols": syms, "levels_for_tomorrow": levels, "vix": vix_now, "global": {k: v for k, v in (gc or {}).items() if k in ("avg_equity_chg", "notes")},
              "signals": sig_rows, "watchlist": watch, "trades": [{"symbol": t["symbol"], "pnl": t["pnl"], "reason": t.get("reason")} for t in trades_today],
              "insights": _insights_in(syms, sig_rows, trades_today), "new_evidence": n_new, "generated": C.now_ist().isoformat(timespec="seconds")}
    # regime memory: how each strategy has done on each kind of day, so far
    reg = []
    for k, v in all_stats("S").items():
        _, mk, strat, tag = k.split("|", 3)
        if mk == "IN" and tag.startswith("regime:") and v["n"] >= 5:
            mean, se, n = _mean_se(v["r"])
            reg.append({"strategy": _strategy_name(strat), "regime": tag[7:], "n": v["n"], "mean_R": round(mean, 2)})
    report["regime_memory"] = sorted(reg, key=lambda x: (x["strategy"], x["regime"]))
    try:                                                         # round 25: where the book lagged today, and the 20-session tally
        from . import lag as LG
        from . import paper as P
        acct, _, tr_all, _ = P.snapshot()
        opened = [t for t in tr_all if t.get("opened", "")[:10] == day and t.get("mode") == "signal"]
        vo = None
        for cx in ((jd.get("context") or {}).values() if jd else []):
            if cx and isinstance(cx.get("vix"), dict) and cx["vix"].get("level") is not None:
                vo = float(cx["vix"]["level"]); break
        if vo is None and isinstance(vix_now, dict) and vix_now.get("level") is not None:
            vo = float(vix_now["level"])
        report["lag"] = LG.review_in(day, todays, syms, jd, opened, P.equity(acct, tr_all), SV.eprefs(), vo)
        report["insights"].append("Where it lagged: " + report["lag"]["headline"])
    except Exception as e:
        report["lag"] = {"error": str(e)[:160]}
    new = review("IN")
    report["changes"] = [{"id": c["id"], "title": c["title"]} for c in new]
    monitor_variants()
    _hset("research", key, report)
    _index_add("research:idx", key, d.toordinal() * 10 + 1)
    _cache.pop("summary", None)
    try:
        from . import push as PUSH
        PUSH.send("After-close research", report["headline"][:200], event_id=f"research:{key}", url="/#learning")
    except Exception:
        pass
    return {"ok": True, "day": day, "signals_added": n_new, "changes": len(new)}


# ------------------------------------------------------------------ after the close: crypto / US
def post_close_g(market: str, day: str | None = None, force: bool = False) -> dict:
    """Daily note for the crypto / US book: what the markets did, what the strategies did, and what was learned."""
    from . import markets as MK
    from . import gbook as G
    day = day or (datetime.utcnow().date() if market == "CRYPTO" else C.now_ist().astimezone(MK.TZ["US"]).date()).isoformat()
    key = f"{market}:{day}"
    if not force and _hget("research", key):
        return {"skipped": "already done", "day": day}
    syms = {}
    for sym in MK.default_symbols(market)[:8]:
        try:
            df = MK.candles(market, sym, "1d", 40)
        except Exception:
            continue
        if len(df) < 5:
            continue
        last, prev = df.iloc[-1], df.iloc[-2]
        rng = (df["high"] - df["low"]) / df["close"] * 100
        syms[sym] = {"close": round(float(last["close"]), 4), "chg_pct": round((float(last["close"]) / float(prev["close"]) - 1) * 100, 2),
                     "range_pct": round(float(rng.iloc[-1]), 2), "range_vs_20d": round(float(rng.iloc[-1] / rng.iloc[-21:-1].mean()), 2) if len(rng) > 21 else None,
                     "above_50d": bool(len(df) >= 50 and last["close"] > df["close"].iloc[-50:].mean())}
    trades = [t for t in G.trades(market) if t.get("closed", "")[:10] == day]
    ins = [f"{s}: {v['chg_pct']:+.2f}% on the day" + (f", range {v['range_vs_20d']:.1f}× the 20-day average" if v.get("range_vs_20d") else "") + "." for s, v in syms.items()]
    if trades:
        ins.append(f"Closed trades today: {len(trades)}, net {sum(t['pnl'] for t in trades):+,.2f}.")
    new = review(market)
    report = {"market": market, "date": day, "headline": " | ".join(f"{s} {v['chg_pct']:+.1f}%" for s, v in list(syms.items())[:4]), "symbols": syms,
              "trades": [{"symbol": t["symbol"], "pnl": t["pnl"], "R": t.get("R"), "reason": t.get("reason")} for t in trades], "insights": ins,
              "changes": [{"id": c["id"], "title": c["title"]} for c in new], "generated": datetime.utcnow().isoformat(timespec="seconds") + "Z"}
    _hset("research", key, report)
    _index_add("research:idx", key, date.fromisoformat(day).toordinal() * 10 + (2 if market == "US" else 3))
    _cache.pop("summary", None)
    return {"ok": True, "day": day, "changes": len(new)}
