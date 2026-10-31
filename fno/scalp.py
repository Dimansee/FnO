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
Setup D (round 36, research/round36.py, opt-in): on a 5-min close - EMA 9 vs 15 + candle colour, breakout of the 30-min range or a
  key level (yesterday's high/low, floor pivots, opening range, OI walls, 5-min swings, trend lines), strong candle, one-way last
  30 min, PCR at the open agrees, room >= 1.5 ATR; stop 1 ATR (5-min), exit on a 5-min close through EMA 15; spread <= 0.5%.
  Not gated by the day-type classifier. It lost money in 2025 and on recorded real prices - paper only, re-check after 60 sessions.
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

DEFAULTS = {"on": False, "syms": ["NIFTY"], "capital": 20_000, "conf": 65, "setups": "ABC", "levels_on_chart": True,
            "sizing": "max", "strike": "best"}   # max lots the cash buys; best of 2 ITM .. 2 OTM (as asked; see BACKTEST)

# Tested governor values (scalping-spec study). Kept here, not in Settings: changing them is a research question.
GOV = {"day_loss": 0.02, "peak": 0.015, "giveback": 0.40, "charges": 0.30, "charges_after": 5, "streak": 3,
       "pause_min": 30, "max_losses": 5, "setup_losses": 2, "room_cost_x": 3.0}
FIRST, LAST_ENTRY, ORB_END, SQUARE = 570, 885, 660, 915      # minutes from midnight (bar close): 09:30, 14:45, 11:00, 15:15

# Setup D = research/round36.py, the best of 768 settings picked on 2023-24 plus the two filters that helped in both halves:
#   5-min breakout (15-min high/low or a key level) + strong candle + 5-min trend + one-way 30 min + PCR agrees + room >= 1.5 ATR,
#   stop 1 x ATR(14, 5-min), exit on a 5-min close through the 5-min EMA 15, square-off 15:15, option spread <= 0.5%.
R36 = {"brk_n": 6, "body": 0.6, "wick": 0.25, "avg_n": 10, "er_n": 30, "er_min": 0.30, "room_atr": 1.5, "pcr_n": 10,
       "max_spread": 0.005, "first": 560, "last": 900, "swing_days": 4}
R36_PF = {"base": 1.02, "full": 1.17}
D_CHECKS = {"ema": "EMA 9 vs 15 (5-min) and a close beyond EMA 9 with the candle's colour", "breakout": "Breakout: 30-min high/low or a key level",
            "strong": "Strong candle (body ≥ 60%, close in the outer 25%, range ≥ 10-candle average)",
            "oneway": "One-way: last 30 min efficiency ≥ 0.30, same direction", "pcr": "PCR at the open agrees (≥ 1 for calls, < 1 for puts)",
            "room": "Room to the next key level ≥ 1.5 × ATR (5-min)", "window": "Entry window 09:20-15:00"}

BACKTEST = {
    "period": "Jan 2023 - Oct 2026, 1-minute bars, 1 lot of the 1-strike-ITM option, 0.5% slippage per fill, fees at 0.15% STT",
    "rows": [
        {"name": "Nifty - the rules as set here", "trades_per_day": 1.7, "win_pct": 18.7, "pf": 0.45,
         "years": {"2023": -59284, "2024": -46686, "2025": -59833, "2026": -52895}, "unseen_120d": -28364},
        {"name": "Nifty - 0.1% slippage", "pf": 0.66, "years": {"2023": -36433, "2024": -21150, "2025": -28294, "2026": -32435}},
        {"name": "Bank Nifty - the rules as set here", "trades_per_day": 1.3, "win_pct": 16.7, "pf": 0.36,
         "years": {"2023": -67881, "2024": -90279, "2025": -102829, "2026": -82504}, "unseen_120d": -58580},
        {"name": "Nifty - no governor", "pf": 0.45, "years": {"2023": -245063, "2024": -225823, "2025": -290658, "2026": -256295}},
        {"name": "Nifty - max lots of ₹20,000, best of 2 ITM..2 OTM (your sizing)", "pf": 0.57,
         "years": {"2023": -85144, "2024": -50797, "2025": -65532, "2026": -60601}, "unseen_120d": -35196},
        {"name": "Bank Nifty - max lots, best strike", "pf": 0.36, "years": {"2023": -71222, "2024": -45357, "2025": -68441, "2026": -38644}},
        {"name": "Round 34: EMA 9/15 + volume, more lots - best of 96 on Nifty (5-min pullback, 0.25% slippage)", "trades_per_day": 2.0, "win_pct": 36.3,
         "pf": 0.96, "years": {"2023": 8043, "2024": 51129, "2025": -41113, "2026": -59703}},
        {"name": "Round 36: D without the PCR / room filters - best of 768 picked on 2023-24, 3 indices, ATM, ₹2,000 risk, 0.25% slippage",
         "trades_per_day": 1.6, "win_pct": 28.0, "pf": R36_PF["base"], "years": {"2023": 249889, "2024": 98423, "2025": -336420, "2026": 69797}},
        {"name": "Round 36: setup D as here (+ PCR agrees + room ≥ 1.5 ATR), Nifty + Bank Nifty from Oct 2023 - filters chosen on the same data",
         "trades_per_day": 0.45, "win_pct": 28.0, "pf": R36_PF["full"], "years": {"2023": 16872, "2024": 27664, "2025": -34423, "2026": 53769}},
    ],
    "compound": "Max lots + best strike on one real ₹20,000 account that compounds: Nifty never got above ₹20,000, fell under "
                "₹10,000 by 17 Feb 2023 and could no longer buy a lot after 27 Jun 2023 (₹1,359 left); Bank Nifty ran out by "
                "23 Oct 2023. The chooser mostly picked 2-OTM strikes (most lots).",
    "why": "Round 34 (EMA 9/15 + volume + liquidity, 1-min / 5-min, many lots): 0 of 96 settings profitable on any index even at "
           "0.1% slippage; on 11 recorded sessions real option prices lost 28% more than the model and the median ATM bid-ask spread was "
           "0.79% of the premium; trading only when the spread is <= 0.5% cut the loss from -Rs 324 to -Rs 14 a trade (Nifty about "
           "break-even) - collecting more sessions. Earlier: entries averaged +0.75 index points; 73% of trades hit the tight 1-minute stop; fees of ~Rs 72 a round trip "
           "roughly doubled the loss. The classifier found 84% of trend days in the right direction, and the governor cut "
           "losses about 4x versus no governor, but neither creates an edge. Matches round 7 (0 of 1,458 scalping rules profitable). "
           "Sensex was not tested (no 1-minute Sensex history yet; the nightly data job now collects it).",
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
        cur["setups"] = "".join(x for x in "ABCD" if x in str(new["setups"]).upper()) or "ABC"
    if new.get("sizing") in ("max", "1lot"):
        cur["sizing"] = new["sizing"]
    if new.get("strike") in ("best", "itm1"):
        cur["strike"] = new["strike"]
    if "levels_on_chart" in new:
        cur["levels_on_chart"] = bool(new["levels_on_chart"])
    store.put(SETTINGS_KEY, cur)
    return cur


def positions() -> list[dict]:
    raw = store.hvals(POS)
    return sorted((json.loads(x) for x in raw), key=lambda p: p["opened"])


def trades() -> list[dict]:
    raw = store.hvals(TRADES)
    return sorted((json.loads(x) for x in raw), key=lambda t: t["closed"])


def _save_pos(p):
    store.hset(POS, p["id"], store._dump(p))


def _take_pos(pid):
    v = store.htake(POS, pid)
    return json.loads(v) if v else None


def reset():
    store.hdrop(POS)
    store.hdrop(TRADES)


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
    return I.charges(prem, prem, lot, exch_pct=I.exch_rate(sym)) / lot / 0.6


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
# Setup D (round 36): same logic as research/round36.py, chart = 5 minutes
# =============================================================================
def five_d(c5: pd.DataFrame, d1: dict, today) -> dict | None:
    """5-minute bars: the broker's 5-min candles for earlier days + today's candles rebuilt from today's completed 1-minute
    bars (a 5-min candle counts once its last minute has closed). EMA 9 / 15 and ATR(14) run over the whole series."""
    if c5 is None or c5.empty or d1 is None or not len(d1["m"]):
        return None
    h5 = c5[["open", "high", "low", "close"]].copy()
    tm = h5.index.time
    h5 = h5[(tm >= time(9, 15)) & (tm <= time(15, 25)) & (h5.index.date < today)]
    keep = sorted(set(h5.index.date))[-30:]
    h5 = h5[np.isin(h5.index.date, keep)]
    t, dy, o, h, l, c, m1 = [], [], [], [], [], [], []
    for ts, r in h5.iterrows():
        t.append(ts.hour * 60 + ts.minute); dy.append(ts.date().toordinal())
        o.append(r["open"]); h.append(r["high"]); l.append(r["low"]); c.append(r["close"]); m1.append(-1)
    k0 = len(c)
    mm = d1["m"]
    for st in range(555, 930, 5):
        sel = np.where((mm >= st) & (mm <= st + 4))[0]
        if not len(sel) or mm[sel[-1]] != st + 4:
            continue
        t.append(st); dy.append(today.toordinal()); o.append(d1["o"][sel[0]]); h.append(float(d1["h"][sel].max()))
        l.append(float(d1["l"][sel].min())); c.append(d1["c"][sel[-1]]); m1.append(int(sel[-1]))
    if len(c) - k0 <= 0 or k0 == 0:
        return None
    o, h, l, c = (np.asarray(v, float) for v in (o, h, l, c))
    return dict(t=np.asarray(t), day=np.asarray(dy), o=o, h=h, l=l, c=c, e9=ema_arr(c, 9), e15=ema_arr(c, 15),
                at=atr1_arr(h, l, c), k0=k0, m1=np.asarray(m1), by_close={int(t[k]) + 4: k for k in range(k0, len(c))})


def d_static(F: dict, d1: dict, walls=None) -> list:
    """Per-day levels as in round 36: yesterday's high / low, floor pivots, the 09:15-09:30 opening range, OI walls."""
    pd_ = F["day"] == F["day"][F["k0"] - 1]
    ph, pl, pc = float(F["h"][:F["k0"]][pd_[:F["k0"]]].max()), float(F["l"][:F["k0"]][pd_[:F["k0"]]].min()), float(F["c"][F["k0"] - 1])
    pv = (ph + pl + pc) / 3
    lv = [ph, pl, pv, 2 * pv - pl, 2 * pv - ph, pv + (ph - pl), pv - (ph - pl)]
    orm = (d1["m"] >= 555) & (d1["m"] <= 569)
    orr = (float(d1["h"][orm].max()), float(d1["l"][orm].min())) if orm.any() else None
    return [lv, orr, [w for w in (walls or []) if w is not None and w == w], pc]


def d_levels(F: dict, statics: list, upto: int) -> dict:
    """round36.level_kernel on the 5-min chart for bar `upto`: crossed a level up / down, room to the next level above / below.
    Levels = statics + 5-min swing highs / lows (2 candles each side) of the last trading day or two + the trend lines through
    the last two swing highs (falling) / swing lows (rising)."""
    h, l, c, day = F["h"], F["l"], F["c"], F["day"]
    K = 8
    sh, sl_ = [(np.nan, np.nan, -1)] * K, [(np.nan, np.nan, -1)] * K
    nsh = nsl = 0
    ev_h, ev_l = {}, {}
    for k in range(2, upto - 1):                                  # swing at k is confirmed at the close of k + 2 (<= upto)
        if k + 2 > upto or day[k - 2] != day[k + 2]:
            continue
        if h[k] > max(h[k - 2], h[k - 1], h[k + 1], h[k + 2]):
            ev_h[k + 2] = (h[k], float(k))
        if l[k] < min(l[k - 2], l[k - 1], l[k + 1], l[k + 2]):
            ev_l[k + 2] = (l[k], float(k))
    for i in range(upto + 1):
        if i in ev_h:
            sh = sh[1:] + [(ev_h[i][0], ev_h[i][1], day[i])]; nsh = min(nsh + 1, K)
        if i in ev_l:
            sl_ = sl_[1:] + [(ev_l[i][0], ev_l[i][1], day[i])]; nsl = min(nsl + 1, K)
    i = upto
    lv, orr, walls, _ = statics
    lev = list(lv) + list(walls)
    if orr is not None and F["t"][i] + 4 >= 569:
        lev += list(orr)
    dmin = day[i] - R36["swing_days"]
    lev += [v for v, _, d in sh if d >= dmin and v == v] + [v for v, _, d in sl_ if d >= dmin and v == v]
    if nsh >= 2 and sh[-2][2] >= dmin and sh[-1][0] < sh[-2][0] and sh[-1][1] > sh[-2][1]:
        lev.append(sh[-1][0] + (sh[-1][0] - sh[-2][0]) / (sh[-1][1] - sh[-2][1]) * (i - sh[-1][1]))
    if nsl >= 2 and sl_[-2][2] >= dmin and sl_[-1][0] > sl_[-2][0] and sl_[-1][1] > sl_[-2][1]:
        lev.append(sl_[-1][0] + (sl_[-1][0] - sl_[-2][0]) / (sl_[-1][1] - sl_[-2][1]) * (i - sl_[-1][1]))
    same = i > 0 and day[i - 1] == day[i]
    up = dn = False
    ru = rd = math.inf
    for v in lev:
        if same and c[i - 1] <= v < c[i]:
            up = True
        if same and c[i - 1] >= v > c[i]:
            dn = True
        if v > c[i]:
            ru = min(ru, v - c[i])
        if v < c[i]:
            rd = min(rd, c[i] - v)
    return dict(brk_up=up, brk_dn=dn, room_up=ru, room_dn=rd, levels=sorted(lev))


def d_signal(F: dict, d1: dict, k: int, statics: list, pcr) -> dict:
    """Setup D on the close of 5-min bar k (round 36). Returns side, every check, entry, 1-ATR stop and room."""
    o, h, l, c, e9, e15, at = (F[x][k] for x in ("o", "h", "l", "c", "e9", "e15", "at"))
    side = 1 if (e9 > e15 and c > e9 and c > o) else -1 if (e9 < e15 and c < e9 and c < o) else 0
    N = R36["brk_n"]
    L = d_levels(F, statics, k)
    rng_up = k - N >= 0 and F["day"][k - N] == F["day"][k] and c > F["h"][k - N:k].max()
    rng_dn = k - N >= 0 and F["day"][k - N] == F["day"][k] and c < F["l"][k - N:k].min()
    rg = h - l
    avg = float(np.mean(F["h"][k - R36["avg_n"]:k] - F["l"][k - R36["avg_n"]:k])) if k >= R36["avg_n"] else math.inf
    body = abs(c - o)
    strong_up = c > o and body >= R36["body"] * rg and (h - c) <= R36["wick"] * rg and rg >= avg
    strong_dn = c < o and body >= R36["body"] * rg and (c - l) <= R36["wick"] * rg and rg >= avg
    j, n = int(F["m1"][k]), R36["er_n"]
    er, mv = 0.0, 0.0
    if j >= n:
        cc = np.asarray(d1["c"][j - n:j + 1], float)
        mv = float(cc[-1] - cc[0])
        tot = float(np.abs(np.diff(cc)).sum())
        er = abs(mv) / tot if tot > 0 else 0.0
    room = (L["room_up"] if side > 0 else L["room_dn"]) if side else math.inf
    close_m = int(F["t"][k]) + 5
    checks = {"ema": side != 0,
              "breakout": bool(side > 0 and (rng_up or L["brk_up"]) or side < 0 and (rng_dn or L["brk_dn"])),
              "strong": bool(side > 0 and strong_up or side < 0 and strong_dn),
              "oneway": bool(side and er >= R36["er_min"] and mv * side > 0),
              "pcr": bool(side and pcr is not None and (pcr >= 1 if side > 0 else pcr < 1)),
              "room": bool(side and at > 0 and room / at >= R36["room_atr"]),
              "window": R36["first"] <= close_m <= R36["last"]}
    nl = (c + side * room) if side and math.isfinite(room) else None
    return dict(side=side, checks=checks, ok=all(checks.values()) and at > 0, entry=float(c), risk=float(at),
                stop=float(c - side * at) if side else None, room=float(room) if math.isfinite(room) else None, nl=nl,
                er=round(er, 2), pcr=pcr, bar_close=f"{close_m // 60:02d}:{close_m % 60:02d}")


def plan_d(sig: dict, sym: str, lot: int) -> dict:
    room = sig["room"] if sig["room"] is not None else 10 * sig["risk"]
    return dict(tag="D", side=sig["side"], entry=sig["entry"], risk=sig["risk"], stop=sig["stop"], nl=sig["nl"], room=float(room),
                conf=100.0, cp=float(cost_points(sym, sig["entry"], lot)), mode="d")


def open_oi(chain, spot, sym) -> dict | None:
    """PCR within 10 strikes of spot and the biggest call / put OI strikes - read once near the open, when OI still mostly
    reflects yesterday's close (round 36 used the previous day's bhavcopy)."""
    if chain is None or chain.empty or "ce_oi" not in chain:
        return None
    ce, pe = pd.to_numeric(chain["ce_oi"], errors="coerce").fillna(0), pd.to_numeric(chain["pe_oi"], errors="coerce").fillna(0)
    near = (chain["strike"] - spot).abs() <= R36["pcr_n"] * C.INDICES[sym]["step"]
    if ce[near].sum() <= 0:
        return None
    above, below = chain["strike"] > spot, chain["strike"] < spot
    cw = float(chain["strike"][above].iloc[int(ce[above].values.argmax())]) if above.any() and ce[above].max() > 0 else None
    pw = float(chain["strike"][below].iloc[int(pe[below].values.argmax())]) if below.any() and pe[below].max() > 0 else None
    return {"pcr": round(float(pe[near].sum() / ce[near].sum()), 3), "walls": [cw, pw]}


def _ensure_open_oi(sym, m, now, ds, spot) -> None:
    if ds.get("oi_open"):
        return
    try:
        exps, _ = m.expiries_and_lot(sym)
        exp = I.pick_trading_expiry(exps, C.INDICES[sym]["expiry"], now.date()) or exps[0]
        oi = open_oi(m.chain(sym, exp, spot, m.vix()["level"], m.candles(sym)), spot, sym)
        if oi:
            ds["oi_open"] = {**oi, "at": now.strftime("%H:%M")}
    except Exception:
        pass


def d_context(sym, m, x, now, ds):
    """(5-min chart, today's statics, PCR at the open) for setup D, or (None, reason)."""
    F = five_d(m.candles(sym), x["d1"], now.date())
    if F is None:
        return None, "Setup D waits for a completed 5-minute candle and earlier days' 5-minute history."
    oi = ds.get("oi_open") or {}
    return (F, d_static(F, x["d1"], oi.get("walls")), oi.get("pcr")), None


def exit_step_d(p, d1, F, k):
    """Setup D exits on 1-min bar k: 1-ATR stop (intrabar), a 5-min close through the 5-min EMA 15, 15:15 square-off."""
    sd = p["side"]
    adverse = d1["l"][k] if sd > 0 else d1["h"][k]
    if sd * (adverse - p["stop"]) <= 0:
        ok = d1["o"][k]
        return (p["stop"] if sd * (ok - p["stop"]) > 0 else float(ok)), "Stop"
    fav = d1["h"][k] if sd > 0 else d1["l"][k]
    if sd * (fav - p["hi_fav"]) > 0:
        p["hi_fav"] = float(fav)
    j = F["by_close"].get(int(d1["m"][k])) if F is not None else None
    if j is not None and sd * (F["c"][j] - F["e15"][j]) < 0:
        return float(d1["c"][k]), "Closed through EMA 15 (5-min)"
    if d1["m"][k] + 1 >= SQUARE:
        return float(d1["c"][k]), "Square-off 15:15"
    return None


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
            try:
                exps, lot_now = m.expiries_and_lot(sym)
                exp = I.pick_trading_expiry(exps, C.INDICES[sym]["expiry"], now.date()) or exps[0]
                ch2 = chain if chain is not None else m.chain(sym, exp, spot, m.vix()["level"], m.candles(sym))
                pick, why = pick_strike(ch2, sym, cand["side"], spot, min(cand["room"], 2 * cand["risk"]), exp, now, cash(cfg),
                                        {**cfg, "lot": lot_now}, ch2.attrs.get("source") == "live", m.vix()["level"])
                cand["pick"] = {**pick, "moneyness": moneyness(pick["itm"])} if pick else {"error": why}
            except Exception as e:
                cand["pick"] = {"error": str(e)[:120]}
    setup_d = None
    try:
        ds = _day_state(now.date().isoformat(), sym)
        oi = ds.get("oi_open") or (open_oi(chain, spot, sym) if chain is not None else None) or {}
        F5 = five_d(m.candles(sym), x["d1"], now.date())
        if F5 is None:
            setup_d = {"error": "Waiting for a completed 5-minute candle today."}
        else:
            k5 = len(F5["c"]) - 1
            sg = d_signal(F5, x["d1"], k5, d_static(F5, x["d1"], oi.get("walls")), oi.get("pcr"))
            setup_d = {"bar": sg["bar_close"], "side": sg["side"], "ok": sg["ok"], "er": sg["er"], "pcr": sg["pcr"],
                       "pcr_src": f"at {ds['oi_open']['at']}" if ds.get("oi_open") else ("now" if oi else None),
                       "room_atr": round(sg["room"] / sg["risk"], 2) if sg["room"] is not None and sg["risk"] > 0 else None,
                       "checks": [{"key": k, "label": D_CHECKS[k], "ok": v} for k, v in sg["checks"].items()]}
            if sg["ok"] and cand is None and "D" in cfg["setups"]:
                cand = plan_d(sg, sym, lot)
                try:
                    exps, lot_now = m.expiries_and_lot(sym)
                    exp = I.pick_trading_expiry(exps, C.INDICES[sym]["expiry"], now.date()) or exps[0]
                    ch2 = chain if chain is not None else m.chain(sym, exp, spot, m.vix()["level"], m.candles(sym))
                    pick, why = pick_strike(ch2, sym, cand["side"], spot, min(cand["room"], 2 * cand["risk"]), exp, now, cash(cfg),
                                            {**cfg, "lot": lot_now}, ch2.attrs.get("source") == "live", m.vix()["level"], R36["max_spread"])
                    cand["pick"] = {**pick, "moneyness": moneyness(pick["itm"])} if pick else {"error": why}
                except Exception as e:
                    cand["pick"] = {"error": str(e)[:120]}
    except Exception as e:
        setup_d = {"error": str(e)[:120]}
    return {**out, "spot": spot, "bar": x["d1"]["ts"][i], "score": score, "dir": sdir, "mode": mode, "setup_d": setup_d,
            "parts": [{"key": k, "label": PART_LABELS[k], "ok": v} for k, v in parts.items()],
            "opening_range": [x["orh"], x["orl"]] if x["d1"]["m"][i] + 1 >= 570 else None,
            "cpr_width_pct": x["d5"]["cpr_w"], "zones": zones(x, spot, chain), "candidate": cand,
            "vwap": round(float(x["d1"]["twap"][i]), 2), "ema9": round(float(x["d1"]["e9"][i]), 2)}


STRIKES_BEST = (2, 1, 0, -1, -2)      # steps in-the-money: 2 ITM, 1 ITM, ATM, 1 OTM, 2 OTM


def cash(cfg, trs=None, open_=None) -> float:
    """Scalp book cash: capital + all realised scalp P&L - premium tied up in open scalp positions (it compounds)."""
    trs = trades() if trs is None else trs
    open_ = positions() if open_ is None else open_
    return cfg["capital"] + sum(t["pnl"] for t in trs) - sum(p["entry_prem"] * p["qty"] for p in open_)


def pick_strike(ch, sym, side, spot, tgt, exp, now, cash_, cfg, live, vix=14.0, max_spread=None):
    """Choose the strike and quantity. sizing 'max' buys as many lots as the cash allows; strike 'best' compares
    2 ITM .. 2 OTM and keeps the one with the highest expected net profit (after fees and spread) if the index moves
    `tgt` points in the next 10 minutes - the same rule as research/scalp_spec.py. Returns (choice dict, None) or
    (None, reason)."""
    from .market import slip_pct, demo_iv
    meta = C.INDICES[sym]
    step, lot = meta["step"], int(cfg.get("lot") or meta["lot"])
    opt, tag = ("CE", "ce") if side > 0 else ("PE", "pe")
    atm = round(spot / step) * step
    ok = lambda v: v is not None and not (isinstance(v, float) and math.isnan(v)) and float(v) > 0
    itms = STRIKES_BEST if cfg.get("strike", "best") == "best" else (1,)
    t_later = I.years_to_expiry(exp, now + timedelta(minutes=10))
    best, why = None, "no strike in the option chain"
    for itm in itms:
        k = atm - side * itm * step
        row = ch[ch["strike"] == float(k)]
        if row.empty:
            continue
        r = row.iloc[0]
        ltp, ask, bid = (r.get(f"{tag}_{f}") for f in ("ltp", "ask", "bid"))
        if not ok(ltp):
            continue
        lim = max_spread if (max_spread is not None and live) else C.MAX_SPREAD_PCT   # 0.5% applies to real quotes only
        if ok(ask) and ok(bid) and (float(ask) - float(bid)) / float(ask) > lim:
            why = f"{k:g} {opt} spread too wide (over {lim:.1%})"
            continue
        if max_spread is not None and live and not (ok(ask) and ok(bid)):
            why = f"{k:g} {opt}: no live bid / ask to check the spread"
            continue
        buy = float(ask) if ok(ask) else round(float(ltp) * (1 + (slip_pct(float(ltp)) if live else 0)), 2)
        if cfg.get("sizing", "max") == "max":
            lots = int(cash_ * 0.95 // (buy * lot))
        else:
            lots = 1 if buy * lot <= cash_ * 0.95 else 0
        if lots <= 0:
            why = f"1 lot of {k:g} {opt} costs ₹{buy * lot:,.0f}, more than the scalp book's cash ₹{cash_:,.0f}"
            continue
        ivv = r.get(f"{tag}_iv")
        iv = float(ivv) / 100 if ok(ivv) else demo_iv(sym, k, spot, vix)
        later = I.bs_price(spot + side * tgt, k, t_later, iv, opt)
        sell = later * (1 - slip_pct(later))
        q = lots * lot
        val = (sell - buy) * q - I.charges(buy, sell, q, exch_pct=I.exch_rate(sym))
        dl = r.get(f"{tag}_delta")
        cand = {"strike": float(k), "itm": itm, "opt": opt, "key": r[f"{tag}_key"], "price": buy, "lots": lots, "qty": q,
                "expected": round(val, 2), "delta": abs(float(dl)) if ok(abs(float(dl or 0))) else 0.5}
        if best is None or val > best["expected"]:
            best = cand
    return (best, None) if best else (None, why)


def moneyness(itm):
    return "ATM" if itm == 0 else f"{abs(itm)} {'ITM' if itm > 0 else 'OTM'}"


def shield_block(cfg) -> str | None:
    """Capital shield for the scalp book: below floor% of its capital the book stops opening trades (same floor as the
    main book's shield, Autopilot → Safety). Sizing stays 'max lots' as chosen; the 2% day-loss governor still applies."""
    try:
        from . import service as SV
        pf = SV.prefs()
    except Exception:
        return None
    if not pf.get("shield_on", True):
        return None
    cap = float(cfg.get("capital") or 0)
    eq = cap + sum(float(t.get("pnl") or 0) for t in trades())
    floor = cap * float(pf.get("shield_floor_pct") or 0) / 100
    if cap > 0 and eq < floor:
        return (f"Capital shield: scalp book equity ₹{eq:,.0f} is below the floor ₹{floor:,.0f} ({pf.get('shield_floor_pct'):.0f}% of "
                f"₹{cap:,.0f}). Stopped for today and every day until you reset the scalp book or lower the floor.")
    return None


def _open(sym, x, i, pl, m, now, cfg, max_spread=None) -> tuple[bool, str]:
    sb = shield_block(cfg)
    if sb:
        return False, sb
    exps, lot = m.expiries_and_lot(sym)
    exp = I.pick_trading_expiry(exps, C.INDICES[sym]["expiry"], now.date()) or exps[0]
    spot = pl["entry"]
    vix = m.vix()["level"]
    ch = m.chain(sym, exp, spot, vix, m.candles(sym))
    live = ch.attrs.get("source") == "live"
    c_, why = pick_strike(ch, sym, pl["side"], spot, min(pl["room"], 2 * pl["risk"]), exp, now, cash(cfg), {**cfg, "lot": lot}, live, vix,
                          max_spread)
    if c_ is None:
        return False, why
    pos = {"id": uuid.uuid4().hex[:8], "symbol": sym, "opened": now.isoformat(timespec="seconds"), "expiry": str(exp),
           "legs": [{"side": "BUY", "strike": c_["strike"], "opt": c_["opt"], "key": c_["key"], "price": c_["price"],
                     "src": m.br.name if live and m.br else "demo"}],
           "lot": int(lot), "lots": c_["lots"], "qty": c_["qty"], "itm": c_["itm"], "expected": c_["expected"],
           "entry_prem": c_["price"], "delta": c_["delta"], "source": m.source,
           "entry_m": int(x["d1"]["m"][i]), "last_m": int(x["d1"]["m"][i]), "hi_fav": pl["entry"], "trail_on": False, **pl}
    _save_pos(pos)
    return True, (f"Scalp {pl['tag']} {sym} {c_['strike']:g}{c_['opt']} ({moneyness(c_['itm'])}) × {c_['lots']} lot(s) at ₹{c_['price']:.2f} "
                  f"(stop {pos['stop']:.1f}, " + ("all setup D checks passed)" if pl["tag"] == "D" else f"conf {pl['conf']:.0f})"))


def _close(p, idx_px, why, m, now, spot=None) -> dict | None:
    from .market import slip_pct
    return store.close_into(POS, TRADES, p["id"], lambda _cur: _scalp_rec(p, idx_px, why, m, now, spot, slip_pct))   # one atomic step


def _scalp_rec(p, idx_px, why, m, now, spot, slip_pct):
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
    chg = I.charges(p["entry_prem"], sell, p["qty"], exch_pct=I.exch_rate(p["symbol"]))
    rec = {"id": p["id"], "symbol": p["symbol"], "tag": p["tag"], "side": p["side"], "mode": p["mode"], "opened": p["opened"],
           "closed": now.isoformat(timespec="seconds"), "strike": lg["strike"], "opt": lg["opt"], "expiry": p["expiry"],
           "qty": p["qty"], "lots": p.get("lots", 1), "itm": p.get("itm", 1), "entry_prem": p["entry_prem"], "exit_prem": round(sell, 2), "gross": round(gross, 2),
           "charges": round(chg, 2), "pnl": round(gross - chg, 2), "reason": why, "entry_spot": p["entry"],
           "exit_spot": round(float(idx_px), 2), "pts": round(p["side"] * (idx_px - p["entry"]), 2), "risk": p["risk"],
           "conf": p["conf"], "price_src": "live" if lg.get("src", "demo") != "demo" else "formula"}
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
            today = now.date().isoformat()
            mine = [p for p in open_ if p["symbol"] == sym]
            F5 = five_d(m.candles(sym), d1, now.date()) if ("D" in cfg["setups"] or any(p["tag"] == "D" for p in mine)) else None
            # ---- open position: walk the completed 1-min bars since the last check
            for p in mine:
                res = None
                for k in range(n):
                    if d1["m"][k] <= p["last_m"]:
                        continue
                    res = exit_step_d(p, d1, F5, k) if p["tag"] == "D" else exit_step(p, d1, k)
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
                    store.hupdate(POS, p["id"], store._dump(p))
            if not cfg["on"] or sym not in cfg["syms"] or any(p["symbol"] == sym for p in positions()):
                continue
            # ---- new entry on the latest completed bar
            i = n - 1
            mi = int(d1["m"][i]) + 1
            gov = governor(_today_trades(sym, now, trs_all), cfg["capital"])
            blocked = gov["stopped"] or (gov["pause_until"] and now < datetime.fromisoformat(gov["pause_until"]))
            ds = _day_state(today, sym)
            if "D" in cfg["setups"]:
                # setup D (round 36): acts only when this minute closes a 5-minute candle
                _ensure_open_oi(sym, m, now, ds, float(d1["c"][i]))
                _save_day_state(today, sym, ds)
                k5 = F5["by_close"].get(int(d1["m"][i])) if F5 is not None else None
                if k5 is not None and not blocked:
                    oi = ds.get("oi_open") or {}
                    sg = d_signal(F5, d1, k5, d_static(F5, d1, oi.get("walls")), oi.get("pcr"))
                    if sg["ok"] and gov["setup_losses"].get("D", 0) < GOV["setup_losses"]:
                        pl = plan_d(sg, sym, C.INDICES[sym]["lot"])
                        ok, msg = _open(sym, x, i, pl, m, now, cfg, R36["max_spread"])
                        (out["opened"] if ok else out["skipped"]).append({"symbol": sym, ("message" if ok else "why"): msg})
                        if ok:
                            continue
                    elif sg["side"] and sg["checks"]["breakout"]:
                        miss = [D_CHECKS[c] for c, v in sg["checks"].items() if not v]
                        out["skipped"].append({"symbol": sym, "why": "setup D: " + ("; ".join(miss) or "lost twice today")})
            if not ("A" in cfg["setups"] or "B" in cfg["setups"] or "C" in cfg["setups"]):
                continue
            if mi < FIRST or mi > LAST_ENTRY:
                continue
            score, sdir, _ = classify_at(x, i)
            mode = mode_of(score)
            if mode == "none":
                continue
            if blocked:
                if find_signal(x, i, mode, sdir, ds["orb_done"], cfg["setups"]):
                    out["skipped"].append({"symbol": sym, "why": ("Scalp book stopped for today: " + gov["stopped"]) if gov["stopped"]
                                           else f"Scalp book paused after losses until {str(gov['pause_until'])[11:16]}"})
                continue
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
            "equity": round(cfg["capital"] + sum(t["pnl"] for t in trs), 2), "cash": round(cash(cfg, trs, open_), 2), "backtest": BACKTEST, "gov_rules": GOV,
            "source": m.source, "errors": list(dict.fromkeys(m.errors))[:5]}
