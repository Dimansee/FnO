"""Scalp book - a separate, paper-only ledger for the scalping rules (scalping-spec study, Oct 2026).

It never touches the main paper account: own positions, own journal, own capital (default Rs 20,000),
own Redis keys. OFF by default because the scalping-spec study (Oct 2026) found these rules lost money in every year
2023-2026 on both indices (see BACKTEST below and research/README.md, "Scalping spec").

The rules are the ones that study tested, line for line (research/scalp_spec.py):
  day type  6-point classifier, re-scored every 15 min from 09:30 -> Trend (4+), in-between (3), no trades (0-2)
  setups    A opening-range breakout 09:30-11:00 | B pullback to the 1-min EMA 9 (Trend) | C level break + retest (Trend)
  exits     stop at the trigger / pullback low, capped at 1 x ATR(14, 1-min); at +1R stop to entry + costs, then exit
            on a 1-min close through EMA 9; 5-bar time stop; close back through VWAP; next level (outside Trend); 15:15
  governor  -2% day, give-back lock, charges budget, 3-loss pause, 5 losses, setup off after 2 losses, minimum room
Index VWAP = time-weighted (indices have no volume), the same as the research data.
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

from . import config as C
from . import indicators as I
from . import store

SETTINGS_KEY = "scalp"
POS, TRADES = "fno:scalp:positions", "fno:scalp:trades"
DAY = "fno:scalp:day:{d}:{s}"

DEFAULTS = {"on": False, "syms": ["NIFTY"], "capital": 20_000, "conf": 65, "setups": "ABC", "levels_on_chart": True}

# Tested governor values (scalping-spec study). Kept here, not in Settings: changing them is a research question.
GOV = {"day_loss": 0.02, "peak": 0.015, "giveback": 0.40, "charges": 0.30, "charges_after": 5, "streak": 3,
       "pause_min": 30, "max_losses": 5, "setup_losses": 2, "room_cost_x": 3.0}
FIRST, LAST_ENTRY, ORB_END, SQUARE = 570, 885, 660, 915      # minutes from midnight (bar close): 09:30, 14:45, 11:00, 15:15

BACKTEST = {
    "period": "Jan 2023 - Oct 2026, 1-minute bars, 1 lot of the 1-strike-ITM option, 0.5% slippage per fill, fees at 0.15% STT",
    "rows": [
        {"name": "Nifty - the rules as set here", "trades_per_day": 1.7, "win_pct": 18.7, "pf": 0.45,
         "years": {"2023": -59284, "2024": -46686, "2025": -59833, "2026": -52895}, "unseen_120d": -28364},
        {"name": "Nifty - 0.1% slippage", "pf": 0.66, "years": {"2023": -36433, "2024": -21150, "2025": -28294, "2026": -32435}},
        {"name": "Bank Nifty - the rules as set here", "trades_per_day": 1.3, "win_pct": 16.7, "pf": 0.36,
         "years": {"2023": -67881, "2024": -90279, "2025": -102829, "2026": -82504}, "unseen_120d": -58580},
        {"name": "Nifty - no governor", "pf": 0.45, "years": {"2023": -245063, "2024": -225823, "2025": -290658, "2026": -256295}},
    ],
    "why": "Entries averaged +0.75 index points; 73% of trades hit the tight 1-minute stop; fees of ~Rs 72 a round trip "
           "roughly doubled the loss. The classifier found 84% of trend days in the right direction, and the governor cut "
           "losses about 4x versus no governor, but neither creates an edge. Matches round 7 (0 of 1,458 scalping rules profitable).",
}


# =============================================================================
# Settings and storage (own keys - never the main ledger)
# =============================================================================
def settings() -> dict:
    return {**DEFAULTS, **(store.get(SETTINGS_KEY) or {})}


def save_settings(new: dict) -> dict:
    cur = settings()
    if "on" in new:
        cur["on"] = bool(new["on"])
    if "syms" in new:
        cur["syms"] = [s for s in (new["syms"] or []) if s in C.INDICES] or ["NIFTY"]
    if "capital" in new:
        cur["capital"] = max(5_000.0, min(10_000_000.0, float(new["capital"])))
    if "conf" in new:
        cur["conf"] = max(0, min(100, int(new["conf"])))
    if "setups" in new:
        cur["setups"] = "".join(x for x in "ABC" if x in str(new["setups"]).upper()) or "ABC"
    if "levels_on_chart" in new:
        cur["levels_on_chart"] = bool(new["levels_on_chart"])
    store.put(SETTINGS_KEY, cur)
    return cur


def positions() -> list[dict]:
    raw = store._cmd("HVALS", POS) or []
    return sorted((json.loads(x) for x in raw), key=lambda p: p["opened"])


def trades() -> list[dict]:
    raw = store._cmd("HVALS", TRADES) or []
    return sorted((json.loads(x) for x in raw), key=lambda t: t["closed"])


def _save_pos(p):
    store._cmd("HSET", POS, p["id"], store._dump(p))


def _take_pos(pid):
    v = store._cmd("EVAL", store._TAKE, 1, POS, pid)
    return json.loads(v) if v else None


def reset():
    store._cmd("DEL", POS)
    store._cmd("DEL", TRADES)


def _day_state(d, sym) -> dict:
    v = store._cmd("GET", DAY.format(d=d, s=sym))
    return json.loads(v) if v else {"orb_done": {"1": False, "-1": False}}


def _save_day_state(d, sym, st):
    store._cmd("SET", DAY.format(d=d, s=sym), store._dump(st), "EX", 86400 * 3)


# =============================================================================
# Pure rule functions (same logic as research/scalp_spec.py)
# =============================================================================
def ema_arr(a, n):
    a = np.asarray(a, float)
    out = np.empty_like(a)
    if not len(a):
        return out
    k = 2 / (n + 1)
    out[0] = a[0]
    for i in range(1, len(a)):
        out[i] = out[i - 1] + k * (a[i] - out[i - 1])
    return out


def atr1_arr(h, l, c, n=14):
    h, l, c = (np.asarray(x, float) for x in (h, l, c))
    if not len(c):
        return np.array([])
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.empty_like(tr)
    out[0] = tr[0]
    for i in range(1, len(tr)):
        out[i] = out[i - 1] + (tr[i] - out[i - 1]) / n
    return out


def _mins(idx) -> np.ndarray:
    return np.array([t.hour * 60 + t.minute for t in idx])


def day5(c5: pd.DataFrame, today) -> dict | None:
    """5-minute context for `today`: indicators, yesterday's levels, narrow-CPR flag."""
    if c5 is None or c5.empty:
        return None
    df = c5[["open", "high", "low", "close"]].copy()
    tm = df.index.time
    df = df[(tm >= time(9, 15)) & (tm <= time(15, 25))]
    df["volume"] = 0.0                                    # index: time-weighted VWAP, as in the research data
    df["e9"], df["e21"], df["e50"] = I.ema(df["close"], 9), I.ema(df["close"], 21), I.ema(df["close"], 50)
    df["atr"], df["adx"], df["vwap"] = I.atr(df), I.adx(df), I.vwap(df)
    dates = sorted(set(df.index.date))
    if today not in dates or dates.index(today) == 0:
        return None
    k = dates.index(today)
    cpr = {}
    for j in range(1, len(dates)):
        p = df[df.index.date == dates[j - 1]]
        ph, pl, pc = float(p["high"].max()), float(p["low"].min()), float(p["close"].iloc[-1])
        pv, bc = (ph + pl + pc) / 3, (ph + pl) / 2
        cpr[dates[j]] = (abs((2 * pv - bc) - bc) / pc * 100, ph, pl, pc)
    w, ph, pl, pc = cpr[today]
    past = [cpr[d][0] for d in dates[max(1, k - 20):k]]
    narrow = len(past) >= 10 and w <= np.percentile(past, 30)
    t = df[df.index.date == today]
    return dict(t=_mins(t.index), c=t["close"].values, vwap=t["vwap"].values, e9=t["e9"].values, e21=t["e21"].values,
                e50=t["e50"].values, adx=t["adx"].values, atr=t["atr"].values, ph=ph, pl=pl, pc=pc, narrow=bool(narrow),
                cpr_w=round(w, 3))


def day1(c1: pd.DataFrame, now: datetime | None = None) -> dict | None:
    """Today's completed 1-minute bars (09:15-15:29) with EMA 9/21, ATR(14) and the session TWAP."""
    if c1 is None or c1.empty:
        return None
    d = (now or C.now_ist()).date()
    x = c1[c1.index.date == d]
    m = _mins(x.index)
    keep = (m >= 555) & (m <= 929)
    if now is not None:                                   # drop the bar still forming
        keep &= (m + 1) <= now.hour * 60 + now.minute
    x, m = x[keep], m[keep]
    if len(x) < 2:
        return None
    h, l, c, o = (x[k].values.astype(float) for k in ("high", "low", "close", "open"))
    tp = (h + l + c) / 3
    return dict(m=m, o=o, h=h, l=l, c=c, twap=np.cumsum(tp) / np.arange(1, len(tp) + 1), e9=ema_arr(c, 9),
                e21=ema_arr(c, 21), a1=atr1_arr(h, l, c), ts=[t.isoformat() for t in x.index])


def build(sym: str, d5: dict, d1: dict) -> dict:
    """Join 5-min and 1-min data and compute today's levels (as tested)."""
    j5 = np.searchsorted(d5["t"] + 5, d1["m"] + 1, side="right") - 1
    orm = d1["m"] < 570
    orh = float(d1["h"][orm].max()) if orm.any() else float("nan")
    orl = float(d1["l"][orm].min()) if orm.any() else float("nan")
    ph, pl, pc = d5["ph"], d5["pl"], d5["pc"]
    pv, bc = (ph + pl + pc) / 3, (ph + pl) / 2
    tc = 2 * pv - bc
    named = [(ph, "Prev high"), (pl, "Prev low"), (pc, "Prev close"), (pv, "Pivot"), (tc, "CPR top"), (bc, "CPR bottom"),
             (2 * pv - pl, "R1"), (2 * pv - ph, "S1"), (pv + (ph - pl), "R2"), (pv - (ph - pl), "S2")]
    if orm.any() and d1["m"][-1] + 1 >= 570:
        named += [(orh, "Opening-range high"), (orl, "Opening-range low")]
    rnd = 100 if sym == "NIFTY" else 500
    lo, hi = float(d1["c"].min()), float(d1["c"].max())
    base = math.floor(lo / rnd) * rnd
    named += [(float(v), "Round number") for v in np.arange(base - 2 * rnd, hi + 3 * rnd, rnd)]
    lv = np.array(sorted(set(round(p, 2) for p, _ in named)))
    return dict(sym=sym, d5=d5, d1=d1, j5=j5, orh=orh, orl=orl, levels=lv, named=named, narrow=d5["narrow"])


def classify(x, i):
    """(score 0-6, direction, parts) at 1-min bar i - same scoring as research/scalp_spec.py."""
    d1, d5, j = x["d1"], x["d5"], int(x["j5"][i])
    c = d1["c"][i]
    side = 1 if c >= d1["twap"][i] else -1
    parts = {"narrow_cpr": bool(x["narrow"]),
             "beyond_prev_day": bool((side > 0 and c > d5["ph"]) or (side < 0 and c < d5["pl"])),
             "outside_opening_range": bool((side > 0 and c > x["orh"]) or (side < 0 and c < x["orl"])),
             "vwap_side_80": False, "adx_rising_25": False, "ema_stack": False}
    if j >= 1:
        share = np.mean(np.sign(d5["c"][:j + 1] - d5["vwap"][:j + 1]) == side)
        parts["vwap_side_80"] = bool(share >= 0.8)
        a = d5["adx"]
        parts["adx_rising_25"] = bool(a[j] > 25 and j >= 3 and a[j] > a[j - 3])
        e9, e21, e50 = d5["e9"][j], d5["e21"][j], d5["e50"][j]
        parts["ema_stack"] = bool((side > 0 and e9 > e21 > e50) or (side < 0 and e9 < e21 < e50))
    return sum(parts.values()), side, parts


def classify_at(x, i):
    """The day type in force at bar i: re-scored on the 15-minute grid 09:30, 09:45 ... (first bar closing at/after it)."""
    m = x["d1"]["m"]
    g = 570 + 15 * ((m[i] + 1 - 570) // 15)
    k = int(np.argmax(m + 1 >= g)) if (m + 1 >= g).any() else i
    return classify(x, min(k, i))


def mode_of(score):
    return "trend" if score >= 4 else "mid" if score == 3 else "none"


def next_level(x, px, side):
    lv = x["levels"]
    if side > 0:
        a = lv[lv > px + 1e-6]
        return float(a[0]) if len(a) else None
    a = lv[lv < px - 1e-6]
    return float(a[-1]) if len(a) else None


def cost_points(sym, spot, lot):
    prem = 0.012 * spot
    return I.charges(prem, prem, lot) / lot / 0.6


def find_signal(x, i, mode, sdir, orb_done, setups="ABC"):
    """Setup on the close of 1-min bar i -> (tag, side, structure price) or None. orb_done is updated when A fires."""
    d1, d5 = x["d1"], x["d5"]
    m, c, e9, e21 = d1["m"][i], d1["c"][i], d1["e9"], d1["e21"]
    a1 = d1["a1"][i]
    if "A" in setups and m + 1 <= ORB_END and i >= 1:
        j = int(x["j5"][i])
        adx_up = j >= 1 and d5["adx"][j] > d5["adx"][j - 1]
        for sd in (1, -1):
            lvl = x["orh"] if sd > 0 else x["orl"]
            if not orb_done[str(sd)] and sd == sdir and adx_up and sd * (c - lvl) > 0 and sd * (d1["c"][i - 1] - lvl) <= 0:
                orb_done[str(sd)] = True
                return ("A", sd, d1["l"][i] if sd > 0 else d1["h"][i])
    if "B" in setups and mode == "trend":
        sd = sdir
        touched = any(sd * (e9[k] - (d1["l"][k] if sd > 0 else d1["h"][k])) >= 0 for k in range(max(0, i - 2), i + 1))
        if sd * (e9[i] - e21[i]) > 0 and touched and sd * (c - e9[i]) > 0 and sd * (c - d1["twap"][i]) > 0 \
                and sd * (c - d1["o"][i]) > 0:
            lo = min(d1["l"][max(0, i - 4):i + 1]) if sd > 0 else max(d1["h"][max(0, i - 4):i + 1])
            return ("B", sd, lo)
    if "C" in setups and mode == "trend" and i >= 11:
        sd = sdir
        for L in x["levels"]:
            if abs(c - L) > 3 * a1:
                continue
            broke = any(sd * (d1["c"][k] - L) > 0 and sd * (d1["c"][k - 1] - L) <= 0 for k in range(i - 10, i))
            probe = d1["l"][i] if sd > 0 else d1["h"][i]
            if broke and sd * (probe - L) <= 0.1 * a1 and sd * (c - L) > 0 and sd * (c - d1["o"][i]) > 0:
                return ("C", sd, probe)
    return None


def plan(x, i, sig, mode, sym, lot, scale=1.0):
    """Risk, stop, next level, room and confidence for a signal (as tested)."""
    d1 = x["d1"]
    tag, sd, sx = sig
    c, a1, m = d1["c"][i], d1["a1"][i], d1["m"][i]
    risk = max(min(abs(c - sx), a1), 0.3 * a1, 2.0 * scale)
    nl = next_level(x, c, sd)
    room = abs(nl - c) if nl is not None else 10 * risk
    conf = (25 if mode == "trend" else 12) + 20 + min(20, 20 * room / (2 * risk)) + 7.5 + 5 \
        + (10 if (570 <= m + 1 <= 690 or 810 <= m + 1 <= 885) else 0)
    return dict(tag=tag, side=sd, entry=float(c), risk=float(risk), stop=float(c - sd * risk), nl=nl, room=float(room),
                conf=round(float(conf), 1), cp=float(cost_points(sym, c, lot)))


def exit_step(p, d1, k):
    """Apply the tested exit rules to 1-min bar k for an open plan p (mutates p's stop / hi_fav / trail_on).
    Returns (index price, reason) or None."""
    sd, c, risk = p["side"], p["entry"], p["risk"]
    hk, lk, ck, ok = d1["h"][k], d1["l"][k], d1["c"][k], d1["o"][k]
    adverse = lk if sd > 0 else hk
    if sd * (adverse - p["stop"]) <= 0:
        return (p["stop"] if sd * (ok - p["stop"]) > 0 else float(ok)), "Stop"
    fav = hk if sd > 0 else lk
    if sd * (fav - p["hi_fav"]) > 0:
        p["hi_fav"] = float(fav)
    if p["mode"] != "trend" and p.get("nl") is not None and sd * (fav - p["nl"]) >= 0:
        return p["nl"], "Next level reached"
    if not p["trail_on"] and sd * (p["hi_fav"] - c) >= risk:
        p["trail_on"] = True
        be = c + sd * p["cp"]
        if sd * (be - p["stop"]) > 0:
            p["stop"] = float(be)
    if p["trail_on"] and sd * (ck - d1["e9"][k]) < 0:
        return float(ck), "Trail: closed through EMA 9"
    if d1["m"][k] - p["entry_m"] >= 5 and sd * (p["hi_fav"] - c) < 0.5 * risk:
        return float(ck), "Time stop: no +0.5R in 5 minutes"
    if sd * (ck - d1["twap"][k]) < 0:
        return float(ck), "Closed back through VWAP"
    if d1["m"][k] + 1 >= SQUARE:
        return float(ck), "Square-off 15:15"
    return None


def governor(day_trades: list[dict], capital: float) -> dict:
    """Today's governor state from today's closed scalp trades of one index (same order of checks as tested)."""
    g = dict(net=0.0, peak=0.0, gross=0.0, charges=0.0, n=0, losses=0, streak=0, pause_until=None, stopped=None,
             setup_losses={"A": 0, "B": 0, "C": 0})
    for t in sorted(day_trades, key=lambda t: t["closed"]):
        g["net"] += t["pnl"]
        g["charges"] += t["charges"]
        g["gross"] += t["gross"]
        g["n"] += 1
        g["peak"] = max(g["peak"], g["net"])
        if t["pnl"] < 0:
            g["losses"] += 1
            g["streak"] += 1
            g["setup_losses"][t["tag"]] = g["setup_losses"].get(t["tag"], 0) + 1
        else:
            g["streak"] = 0
        if g["net"] <= -GOV["day_loss"] * capital:
            g["stopped"] = g["stopped"] or f"Day loss limit ({GOV['day_loss']:.0%} of capital) reached"
        if g["losses"] >= GOV["max_losses"]:
            g["stopped"] = g["stopped"] or f"{GOV['max_losses']} losing trades today"
        if g["peak"] >= GOV["peak"] * capital and g["net"] <= (1 - GOV["giveback"]) * g["peak"]:
            g["stopped"] = g["stopped"] or f"Give-back lock: day peaked at ₹{g['peak']:,.0f}, {GOV['giveback']:.0%} given back"
        if g["n"] >= GOV["charges_after"] and (g["gross"] <= 0 or g["charges"] > GOV["charges"] * g["gross"]):
            g["stopped"] = g["stopped"] or f"Charges budget: fees over {GOV['charges']:.0%} of gross profit"
        if g["streak"] >= GOV["streak"]:
            g["pause_until"] = (datetime.fromisoformat(t["closed"]) + timedelta(minutes=GOV["pause_min"])).isoformat(timespec="seconds")
            g["streak"] = 0
    for k in ("net", "peak", "gross", "charges"):
        g[k] = round(g[k], 2)
    return g


def zones(x, spot, chain=None, n=3):
    """Levels merged into zones (within 0.1%), strongest sources first; plus option-chain OI walls. Top n above / below."""
    pts = list(x["named"])
    oi = {}
    if chain is not None and not chain.empty:
        try:
            ce, pe = pd.to_numeric(chain["ce_oi"], errors="coerce"), pd.to_numeric(chain["pe_oi"], errors="coerce")
            if ce.fillna(0).sum() > 0:
                above, below = chain["strike"] >= spot, chain["strike"] <= spot
                if above.any():
                    oi["call_wall"] = float(chain["strike"][above].iloc[int(ce[above].fillna(0).values.argmax())])
                    pts.append((oi["call_wall"], "Call OI wall"))
                if below.any():
                    oi["put_wall"] = float(chain["strike"][below].iloc[int(pe[below].fillna(0).values.argmax())])
                    pts.append((oi["put_wall"], "Put OI wall"))
                cc, pc = pd.to_numeric(chain["ce_chg_oi"], errors="coerce").fillna(0), pd.to_numeric(chain["pe_chg_oi"], errors="coerce").fillna(0)
                oi["call_build"] = float(chain["strike"].iloc[int(cc.values.argmax())]) if cc.max() > 0 else None
                oi["put_build"] = float(chain["strike"].iloc[int(pc.values.argmax())]) if pc.max() > 0 else None
                oi["pcr"] = round(float(pe.fillna(0).sum() / ce.fillna(0).sum()), 2)
        except Exception:
            pass
    pts = sorted((float(p), s) for p, s in pts if p == p)
    out, cur = [], []
    for p, s in pts:
        if cur and abs(p - cur[0][0]) > 0.001 * spot:
            out.append(cur)
            cur = []
        cur.append((p, s))
    if cur:
        out.append(cur)
    zs = []
    for grp in out:
        srcs = sorted({s for _, s in grp})
        if srcs == ["Round number"] and abs(grp[0][0] - spot) > 0.02 * spot:
            continue
        zs.append({"price": round(float(np.mean([p for p, _ in grp])), 2), "sources": srcs,
                   "strength": len(srcs) + (1 if any("OI" in s for s in srcs) else 0)})
    above = [z for z in zs if z["price"] > spot][:n]
    below = [z for z in zs if z["price"] <= spot][::-1][:n]
    return {"above": above, "below": below, "oi": oi}


# =============================================================================
# Live: state for the page, and the every-minute tick
# =============================================================================
PART_LABELS = {"narrow_cpr": "Narrow CPR (bottom 30% of 20 days)", "beyond_prev_day": "Beyond yesterday's high/low",
               "outside_opening_range": "Outside the 09:15-09:30 range", "vwap_side_80": "80%+ of 5-min closes on one side of VWAP",
               "adx_rising_25": "ADX(14, 5-min) above 25 and rising", "ema_stack": "EMA 9 / 21 / 50 stacked (5-min)"}


def _context(sym, m, now):
    c5 = m.candles(sym)
    d5 = day5(c5, now.date())
    c1 = m.candles1(sym)
    d1 = day1(c1, now)
    if d5 is None or d1 is None:
        return None, "Waiting for today's 1-minute and 5-minute candles."
    return build(sym, d5, d1), None


def _today_trades(sym, now, trs=None):
    d = now.date().isoformat()
    return [t for t in (trs if trs is not None else trades()) if t["symbol"] == sym and t["closed"][:10] == d]


def state(sym: str, m, now=None, cfg=None, chain=None) -> dict:
    """Everything the Scalp page shows for one index (no side effects)."""
    now = now or C.now_ist()
    cfg = cfg or settings()
    x, why = _context(sym, m, now)
    trs = _today_trades(sym, now)
    gov = governor(trs, cfg["capital"])
    out = {"symbol": sym, "time": now.isoformat(timespec="seconds"), "governor": gov, "trades_today": trs}
    if x is None:
        return {**out, "error": why}
    i = len(x["d1"]["c"]) - 1
    spot = float(x["d1"]["c"][i])
    score, sdir, parts = classify_at(x, i) if x["d1"]["m"][i] + 1 >= FIRST else (0, 0, {})
    mode = mode_of(score) if parts else "none"
    if chain is None:
        try:
            exps, _ = m.expiries_and_lot(sym)
            exp = I.pick_trading_expiry(exps, C.INDICES[sym]["expiry"], now.date()) or exps[0]
            chain = m.chain(sym, exp, spot, m.vix()["level"], m.candles(sym))
        except Exception:
            chain = None
    lot = C.INDICES[sym]["lot"]
    cand = None
    if parts:
        sig = find_signal(x, i, mode, sdir, {"1": True, "-1": True} if x["d1"]["m"][i] + 1 > ORB_END else
                          dict(_day_state(now.date().isoformat(), sym)["orb_done"]), cfg["setups"])
        if sig and mode != "none":
            cand = plan(x, i, sig, mode, sym, lot)
    return {**out, "spot": spot, "bar": x["d1"]["ts"][i], "score": score, "dir": sdir, "mode": mode,
            "parts": [{"key": k, "label": PART_LABELS[k], "ok": v} for k, v in parts.items()],
            "opening_range": [x["orh"], x["orl"]] if x["d1"]["m"][i] + 1 >= 570 else None,
            "cpr_width_pct": x["d5"]["cpr_w"], "zones": zones(x, spot, chain), "candidate": cand,
            "vwap": round(float(x["d1"]["twap"][i]), 2), "ema9": round(float(x["d1"]["e9"][i]), 2)}


def _open(sym, x, i, pl, m, now, cfg) -> tuple[bool, str]:
    from .market import slip_pct
    exps, lot = m.expiries_and_lot(sym)
    exp = I.pick_trading_expiry(exps, C.INDICES[sym]["expiry"], now.date()) or exps[0]
    spot = pl["entry"]
    ch = m.chain(sym, exp, spot, m.vix()["level"], m.candles(sym))
    step = C.INDICES[sym]["step"]
    strike = round(spot / step) * step - pl["side"] * step              # 1 strike in-the-money
    opt, tag = ("CE", "ce") if pl["side"] > 0 else ("PE", "pe")
    row = ch[ch["strike"] == float(strike)]
    if row.empty:
        return False, f"{strike:g} {opt} not in the option chain"
    r = row.iloc[0]
    ltp, ask, bid = (r.get(f"{tag}_{k}") for k in ("ltp", "ask", "bid"))
    ok = lambda v: v is not None and not (isinstance(v, float) and math.isnan(v)) and float(v) > 0
    if not ok(ltp):
        return False, "no option price"
    live = ch.attrs.get("source") == "live"
    price = float(ask) if ok(ask) else round(float(ltp) * (1 + (slip_pct(float(ltp)) if live else 0)), 2)
    if ok(ask) and ok(bid) and (float(ask) - float(bid)) / float(ask) > C.MAX_SPREAD_PCT:
        return False, f"{strike:g} {opt} spread too wide"
    if price * lot > cfg["capital"] * 0.95:
        return False, f"1 lot costs ₹{price * lot:,.0f}, more than the scalp book's capital"
    delta = abs(float(r.get(f"{tag}_delta"))) if ok(abs(float(r.get(f"{tag}_delta") or 0))) else 0.6
    pos = {"id": uuid.uuid4().hex[:8], "symbol": sym, "opened": now.isoformat(timespec="seconds"), "expiry": str(exp),
           "legs": [{"side": "BUY", "strike": float(strike), "opt": opt, "key": r[f"{tag}_key"], "price": price,
                     "src": m.br.name if live and m.br else "demo"}],
           "lot": int(lot), "qty": int(lot), "entry_prem": price, "delta": delta, "source": m.source,
           "entry_m": int(x["d1"]["m"][i]), "last_m": int(x["d1"]["m"][i]), "hi_fav": pl["entry"], "trail_on": False, **pl}
    _save_pos(pos)
    return True, f"Scalp {pl['tag']} {sym} {strike:g}{opt} at ₹{price:.2f} (stop {pos['stop']:.1f}, conf {pl['conf']:.0f})"


def _close(p, idx_px, why, m, now, spot=None) -> dict | None:
    from .market import slip_pct
    taken = _take_pos(p["id"])
    if not taken:
        return None
    prices = m.leg_prices([p], {p["symbol"]: spot or idx_px}, m.vix()["level"])
    lg = p["legs"][0]
    prem = prices.get(lg["key"], lg["price"])
    if spot is not None and abs(idx_px - spot) > 1e-9:          # level touched between checks: premium at the level, via delta
        sg = 1 if lg["opt"] == "CE" else -1
        prem = max(0.05, prem + p["delta"] * sg * (idx_px - spot))
    fills = m.exit_fills([p], {lg["key"]: prem}, "realistic")
    sell = float(fills.get(lg["key"], prem))
    if "Stop" in why and lg.get("src", "demo") == "demo":
        sell = max(0.05, sell * (1 - slip_pct(sell)))
    gross = (sell - p["entry_prem"]) * p["qty"]
    chg = I.charges(p["entry_prem"], sell, p["qty"])
    rec = {"id": p["id"], "symbol": p["symbol"], "tag": p["tag"], "side": p["side"], "mode": p["mode"], "opened": p["opened"],
           "closed": now.isoformat(timespec="seconds"), "strike": lg["strike"], "opt": lg["opt"], "expiry": p["expiry"],
           "qty": p["qty"], "entry_prem": p["entry_prem"], "exit_prem": round(sell, 2), "gross": round(gross, 2),
           "charges": round(chg, 2), "pnl": round(gross - chg, 2), "reason": why, "entry_spot": p["entry"],
           "exit_spot": round(float(idx_px), 2), "pts": round(p["side"] * (idx_px - p["entry"]), 2), "risk": p["risk"],
           "conf": p["conf"], "price_src": "live" if lg.get("src", "demo") != "demo" else "formula"}
    store._cmd("HSET", TRADES, rec["id"], store._dump(rec))
    return rec


def tick(now=None, m=None) -> dict:
    """Every-minute job: manage open scalp positions, then look for a new setup. Does nothing while the book is OFF."""
    cfg = settings()
    now = now or C.now_ist()
    open_ = positions()
    if not cfg["on"] and not open_:
        return {"off": True}
    from .market import Market
    m = m or Market()
    out = {"opened": [], "closed": [], "skipped": []}
    trs_all = trades()
    for sym in sorted(set(cfg["syms"]) | {p["symbol"] for p in open_}):
        try:
            x, why = _context(sym, m, now)
            if x is None:
                out["skipped"].append({"symbol": sym, "why": why})
                continue
            d1 = x["d1"]
            n = len(d1["c"])
            # ---- open position: walk the completed 1-min bars since the last check
            mine = [p for p in open_ if p["symbol"] == sym]
            for p in mine:
                res = None
                for k in range(n):
                    if d1["m"][k] <= p["last_m"]:
                        continue
                    res = exit_step(p, d1, k)
                    p["last_m"] = int(d1["m"][k])
                    if res:
                        break
                if res is None and now.hour * 60 + now.minute >= SQUARE:
                    res = (float(d1["c"][-1]), "Square-off 15:15")
                if res:
                    rec = _close(p, res[0], res[1], m, now, spot=float(d1["c"][-1]))
                    if rec:
                        out["closed"].append(rec)
                        trs_all.append(rec)
                else:
                    store._cmd("EVAL", store._UPDATE_IF_EXISTS, 1, POS, p["id"], store._dump(p))
            if not cfg["on"] or sym not in cfg["syms"] or any(p["symbol"] == sym for p in positions()):
                continue
            # ---- new entry on the latest completed bar
            i = n - 1
            mi = int(d1["m"][i]) + 1
            if mi < FIRST or mi > LAST_ENTRY:
                continue
            score, sdir, _ = classify_at(x, i)
            mode = mode_of(score)
            if mode == "none":
                continue
            today = now.date().isoformat()
            gov = governor(_today_trades(sym, now, trs_all), cfg["capital"])
            if gov["stopped"] or (gov["pause_until"] and now < datetime.fromisoformat(gov["pause_until"])):
                continue
            ds = _day_state(today, sym)
            sig = find_signal(x, i, mode, sdir, ds["orb_done"], cfg["setups"])
            _save_day_state(today, sym, ds)
            if not sig:
                continue
            last = [t for t in _today_trades(sym, now, trs_all)]
            if last:
                lt = last[-1]
                lm = datetime.fromisoformat(lt["closed"])
                if lt["side"] == sig[1] and (now - lm).total_seconds() < 180:
                    continue
            scale = 1.0                                       # minimum stop is 2 Nifty points, scaled to the index (as tested)
            if sym != "NIFTY":
                ns = m.spot("NIFTY")
                scale = float(d1["c"][i]) / ns if ns else 2.2
            pl = plan(x, i, sig, mode, sym, C.INDICES[sym]["lot"], scale)
            pl["mode"] = mode
            if pl["conf"] < cfg["conf"]:
                out["skipped"].append({"symbol": sym, "why": f"setup {sig[0]} confidence {pl['conf']:.0f} < {cfg['conf']}"})
                continue
            if gov["setup_losses"].get(sig[0], 0) >= GOV["setup_losses"]:
                out["skipped"].append({"symbol": sym, "why": f"setup {sig[0]} lost twice today"})
                continue
            if pl["room"] < GOV["room_cost_x"] * pl["cp"]:
                out["skipped"].append({"symbol": sym, "why": "too little room to the next level to pay the fees"})
                continue
            ok, msg = _open(sym, x, i, pl, m, now, cfg)
            (out["opened"] if ok else out["skipped"]).append({"symbol": sym, ("message" if ok else "why"): msg})
        except Exception as e:                                # one index failing must not stop the other
            out["skipped"].append({"symbol": sym, "why": f"error: {str(e)[:120]}"})
    return out


def exit_now(pid: str, m=None) -> dict | None:
    from .market import Market
    m = m or Market()
    p = next((x for x in positions() if x["id"] == pid), None)
    if not p:
        return None
    spot = m.spot(p["symbol"])
    if spot is None:
        raise ValueError("No index price right now - try again in a minute.")
    return _close(p, spot, "Closed by hand", m, C.now_ist(), spot=spot)


def summary(trs: list[dict]) -> dict:
    if not trs:
        return {"trades": 0}
    pnl = np.array([t["pnl"] for t in trs])
    pos, neg = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    days = {}
    for t in trs:
        days[t["closed"][:10]] = days.get(t["closed"][:10], 0) + t["pnl"]
    return {"trades": len(trs), "net": round(float(pnl.sum())), "charges": round(sum(t["charges"] for t in trs)),
            "gross": round(sum(t["gross"] for t in trs)), "win_pct": round(100 * float(np.mean(pnl > 0)), 1),
            "pf": round(float(pos / neg), 2) if neg > 0 else None, "days": len(days),
            "green_days": sum(1 for v in days.values() if v > 0), "per_day": round(len(trs) / len(days), 1)}


def page(m=None) -> dict:
    """GET /api/scalp: settings, live state per index, open positions, journal, backtest."""
    from .market import Market
    m = m or Market()
    cfg = settings()
    now = C.now_ist()
    open_ = positions()
    trs = trades()
    st = {}
    for s in C.INDICES:
        try:
            st[s] = state(s, m, now, cfg)
        except Exception as e:
            st[s] = {"symbol": s, "error": str(e)[:160], "governor": governor(_today_trades(s, now, trs), cfg["capital"])}
    if open_:
        spots = {p["symbol"]: (st.get(p["symbol"]) or {}).get("spot") or m.spot(p["symbol"]) for p in open_}
        prices = m.leg_prices(open_, spots, m.vix()["level"])
        for p in open_:
            lg = p["legs"][0]
            cur = prices.get(lg["key"])
            p["current_prem"] = cur
            p["unrealised"] = round((cur - p["entry_prem"]) * p["qty"], 2) if cur is not None else None
    return {"settings": cfg, "state": st, "positions": open_, "trades": trs[-300:][::-1], "summary": summary(trs),
            "equity": round(cfg["capital"] + sum(t["pnl"] for t in trs), 2), "backtest": BACKTEST, "gov_rules": GOV,
            "source": m.source, "errors": list(dict.fromkeys(m.errors))[:5]}
