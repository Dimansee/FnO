"""Round 7a — what happens around 80-120 point intraday rallies (Nifty; Bank Nifty in the same % size).

1. Find every swing ("leg") with a zigzag on 1-minute highs/lows: a leg ends when price comes back
   REV points from its extreme. Legs of 80+ Nifty points (Bank Nifty: same % of price) are rallies.
2. Describe them: how often, what time, how long, where they start (day low/high, yesterday's
   high/low, Camarilla, round numbers, after a quiet spell, after an opposite move) compared with
   ordinary minutes.
3. Real time: when price is already TRIG points off a swing low, how often does it go on to make it a
   80+ leg before falling back to the low? Which conditions raise / lower those odds?
4. After: once a rally has done +80, how much more does it usually run, and how much does it give back
   after it tops?
"""
import json, sys
import numpy as np
import pandas as pd
from numba import njit

import m1

REV = 25.0          # Nifty points of pull-back that end a leg
SIZES = (80, 120, 200)
TRIG = 30.0


@njit(cache=True)
def zigzag(h, l, rev):
    """Swings on 1-minute highs/lows: a swing ends when price comes back `rev` from its extreme.
    Returns (start_i, end_i, direction, size). The day's last swing counts too."""
    n = len(h)
    out = []
    d = 0
    hi, lo, hi_i, lo_i = h[0], l[0], 0, 0
    start, sp, ext, ext_i = 0, 0.0, 0.0, 0
    for i in range(1, n):
        if d == 0:
            if h[i] > hi:
                hi, hi_i = h[i], i
            if l[i] < lo:
                lo, lo_i = l[i], i
            if hi - lo >= rev:
                if hi_i > lo_i:
                    d, start, sp, ext, ext_i = 1, lo_i, lo, hi, hi_i
                else:
                    d, start, sp, ext, ext_i = -1, hi_i, hi, lo, lo_i
        elif d == 1:
            if h[i] > ext:
                ext, ext_i = h[i], i
            elif ext - l[i] >= rev:
                out.append((start, ext_i, 1, ext - sp))
                d, start, sp, ext, ext_i = -1, ext_i, ext, l[i], i
        else:
            if l[i] < ext:
                ext, ext_i = l[i], i
            elif h[i] - ext >= rev:
                out.append((start, ext_i, -1, sp - ext))
                d, start, sp, ext, ext_i = 1, ext_i, ext, h[i], i
    if d != 0:
        out.append((start, ext_i, d, abs(ext - sp)))
    return out


def legs(days):
    rows = []
    for dd in days:
        rev = REV * dd.scale
        for s, e, side, size in zigzag(dd.h, dd.l, rev):
            pts = size / dd.scale                       # in Nifty-equivalent points
            rows.append({"date": dd.d, "sym": dd.sym, "s": s, "e": e, "side": side, "pts": pts, "start_m": int(dd.m[s]),
                         "mins": int(e - s), **context(dd, s, side)})
    return pd.DataFrame(rows)


def context(dd, i, side):
    """Conditions at minute i (only information known at that minute)."""
    sc = dd.scale
    px = dd.c[i]
    lo_so_far, hi_so_far = dd.l[: i + 1].min(), dd.h[: i + 1].max()
    j = max(0, i - 30)
    rng30 = (dd.h[j: i + 1].max() - dd.l[j: i + 1].min()) / sc
    prior30 = side * (dd.c[i] - dd.c[j]) / sc
    near = lambda lv, tol=15: abs(px - lv) <= tol * sc  # noqa: E731
    cams = [dd.cam["h3"], dd.cam["h4"], dd.cam["l3"], dd.cam["l4"]]
    rnd = 100 if dd.sym == "NIFTY" else 500
    return {
        "tod": "09:15-09:45" if dd.m[i] < 585 else "09:45-11:00" if dd.m[i] < 660 else "11:00-13:00" if dd.m[i] < 780 else "13:00-14:30" if dd.m[i] < 870 else "14:30-15:30",
        "at_day_extreme": bool((side > 0 and px - lo_so_far <= 10 * sc) or (side < 0 and hi_so_far - px <= 10 * sc)),
        "near_prev_hl": bool(near(dd.ph) or near(dd.pl) or near(dd.pc)),
        "near_camarilla": bool(any(near(x) for x in cams)),
        "near_round": bool(abs(px - round(px / rnd) * rnd) <= 10 * sc),
        "quiet_30m": bool(rng30 < 0.12 * dd.atr_d / sc) if dd.atr_d else False,
        "after_opposite_40": bool(prior30 <= -40),
        "with_trend_vs_open": bool(side * (px - dd.o[0]) > 0),
        "vs_twap": "with" if side * (px - dd.twap[i]) > 0 else "against",
        "gap": "gap up" if dd.gap > 0.3 else "gap down" if dd.gap < -0.3 else "flat open",
        "vix_30m_falling": bool((dd.vix[i] - dd.vix[j]) * side < 0),
        "vix": "<13" if dd.vix[i] < 13 else "13-16" if dd.vix[i] < 16 else "16+",
    }


def describe(L, days):
    out = {}
    ndays = len({(r.date) for r in L.itertuples()}) or 1
    big = L[L.pts >= SIZES[0]]
    out["days"] = len(days)
    out["legs_per_day_80plus"] = round(len(big) / len(days), 2)
    by_day = big.groupby("date").size()
    out["pct_days_with_80plus"] = round(len(by_day) / len(days) * 100)
    out["pct_days_with_80plus_up"] = round(big[big.side > 0].date.nunique() / len(days) * 100)
    out["pct_days_with_80plus_down"] = round(big[big.side < 0].date.nunique() / len(days) * 100)
    out["size_buckets_per_100_days"] = {f"{a}-{b}" if b else f"{a}+": round(len(L[(L.pts >= a) & ((L.pts < b) if b else True)]) / len(days) * 100, 1)
                                        for a, b in ((25, 50), (50, 80), (80, 120), (120, 200), (200, None))}
    out["duration_min_median"] = {k: int(g.mins.median()) for k, g in ((f"80-120", big[big.pts < 120]), ("120+", big[big.pts >= 120]))}
    out["duration_min_p25_p75"] = [int(big.mins.quantile(.25)), int(big.mins.quantile(.75))]
    out["start_time_share"] = (big.tod.value_counts(normalize=True) * 100).round(1).to_dict()
    return out


def lift_table(L, base):
    """Share of 80+ rally starts with each condition vs the share among all minutes (base)."""
    big = L[L.pts >= SIZES[0]]
    rows = []
    for col in ("at_day_extreme", "near_prev_hl", "near_camarilla", "near_round", "quiet_30m", "after_opposite_40",
                "with_trend_vs_open", "vix_30m_falling"):
        a, b = big[col].mean() * 100, base[col].mean() * 100
        rows.append({"condition": col, "rally_starts_pct": round(a, 1), "all_minutes_pct": round(b, 1), "lift": round(a / b, 2) if b else None})
    for col in ("tod", "vs_twap", "gap", "vix"):
        va, vb = big[col].value_counts(normalize=True), base[col].value_counts(normalize=True)
        for k in vb.index:
            rows.append({"condition": f"{col}={k}", "rally_starts_pct": round(va.get(k, 0) * 100, 1), "all_minutes_pct": round(vb[k] * 100, 1),
                         "lift": round(va.get(k, 0) / vb[k], 2)})
    return rows


def baseline(days, every=7):
    rng = np.random.default_rng(1)
    rows = []
    for dd in days:
        for i in range(30, len(dd.c) - 30, every):
            rows.append(context(dd, i, 1 if rng.random() < 0.5 else -1))
    return pd.DataFrame(rows)


@njit(cache=True)
def _trig_events(h, l, c, rev, trig, goal):
    """Real-time: track the running swing; when price is `trig` off the swing extreme, record whether it
    reaches `goal` (from the extreme) before coming back to the extreme. Returns (i, side, ok, mfe)."""
    n = len(c)
    out = []
    lo, hi = l[0], h[0]
    lo_i, hi_i = 0, 0
    armed_up, armed_dn = True, True
    for i in range(1, n):
        if l[i] < lo:
            lo, lo_i, armed_up = l[i], i, True
        if h[i] > hi:
            hi, hi_i, armed_dn = h[i], i, True
        if armed_up and c[i] - lo >= trig and lo_i < i:
            armed_up = False
            ok, mfe = 0, 0.0
            for k in range(i + 1, n):
                if l[k] <= lo:
                    break
                mfe = max(mfe, h[k] - lo)
                if h[k] - lo >= goal:
                    ok = 1
                    break
            out.append((i, 1, ok, mfe))
        if armed_dn and hi - c[i] >= trig and hi_i < i:
            armed_dn = False
            ok, mfe = 0, 0.0
            for k in range(i + 1, n):
                if h[k] >= hi:
                    break
                mfe = max(mfe, hi - l[k])
                if hi - l[k] >= goal:
                    ok = 1
                    break
            out.append((i, -1, ok, mfe))
        # a fresh swing starts after a REV pull-back
        if hi - l[i] >= rev and hi_i < i:
            lo, lo_i = l[i], i
            hi, hi_i = h[i], i
            armed_up = armed_dn = True
    return out


def realtime(days, trig=TRIG, goal=80.0):
    rows = []
    for dd in days:
        for i, side, ok, mfe in _trig_events(dd.h, dd.l, dd.c, REV * dd.scale, trig * dd.scale, goal * dd.scale):
            if dd.m[i] > 900:
                continue
            rows.append({"date": dd.d, "sym": dd.sym, "i": i, "side": side, "ok": ok, "mfe": mfe / dd.scale, **context(dd, i, side)})
    return pd.DataFrame(rows)


def realtime_table(R):
    base = R.ok.mean() * 100
    rows = [{"condition": "all", "n": len(R), "reach_80_pct": round(base, 1), "lift": 1.0}]
    for col in ("at_day_extreme", "near_prev_hl", "near_camarilla", "near_round", "quiet_30m", "after_opposite_40",
                "with_trend_vs_open", "vix_30m_falling", "tod", "vs_twap", "gap", "vix"):
        for k, g in R.groupby(col):
            if len(g) >= 40:
                rows.append({"condition": f"{col}={k}", "n": len(g), "reach_80_pct": round(g.ok.mean() * 100, 1), "lift": round(g.ok.mean() * 100 / base, 2)})
    return rows


def after(days, L):
    """Once a rally has done +80 in real time: extra run, and give-back after the top."""
    big = L[L.pts >= 80]
    dmap = {(dd.sym, dd.d): dd for dd in days}
    ext, give30, give60, back_to_start = [], [], [], 0
    for r in big.itertuples():
        dd = dmap[(r.sym, r.date)]
        ext.append(r.pts - 80)
        e = r.e
        top = dd.h[e] if r.side > 0 else dd.l[e]
        for mins, arr in ((30, give30), (60, give60)):
            seg = slice(e + 1, min(len(dd.c), e + 1 + mins))
            if dd.c[seg].size:
                worst = (top - dd.l[seg].min()) if r.side > 0 else (dd.h[seg].max() - top)
                arr.append(worst / dd.scale / r.pts * 100)
        start = dd.l[r.s] if r.side > 0 else dd.h[r.s]
        rest = slice(e + 1, len(dd.c))
        if dd.c[rest].size and ((r.side > 0 and dd.l[rest].min() <= start) or (r.side < 0 and dd.h[rest].max() >= start)):
            back_to_start += 1
    ext = np.array(ext)
    return {"rallies": len(big), "extra_after_80_median": round(float(np.median(ext)), 1),
            "p_reach_100": round(float((ext >= 20).mean() * 100), 1), "p_reach_120": round(float((ext >= 40).mean() * 100), 1),
            "p_reach_160": round(float((ext >= 80).mean() * 100), 1),
            "giveback_30m_median_pct": round(float(np.median(give30)), 1), "giveback_60m_median_pct": round(float(np.median(give60)), 1),
            "pct_fully_reversed_same_day": round(back_to_start / len(big) * 100, 1)}


if __name__ == "__main__":
    res = {}
    for sym in ("NIFTY", "BANKNIFTY"):
        days = m1.load(sym)
        L = legs(days)
        B = baseline(days)
        R = realtime(days)
        res[sym] = {"describe": describe(L, days), "lift": lift_table(L, B), "after": after(days, L),
                    "realtime": realtime_table(R),
                    "realtime_goal": {g: round(realtime(days, TRIG, g).ok.mean() * 100, 1) for g in (60, 80, 100, 120)}}
        L.to_pickle(f"r7_legs_{sym}.pkl"); R.to_pickle(f"r7_rt_{sym}.pkl")
        print(sym, json.dumps(res[sym]["describe"], default=str))
        print(sym, "after:", res[sym]["after"], "| goal odds when +30 off the low:", res[sym]["realtime_goal"])
        print(pd.DataFrame(res[sym]["lift"]).to_string(index=False))
        print(pd.DataFrame(res[sym]["realtime"]).to_string(index=False), flush=True)
    json.dump(res, open("r7_rally.json", "w"), default=str, indent=1)
