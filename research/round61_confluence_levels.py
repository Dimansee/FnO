"""Round 61: level break with confluence (28 Oct 2026).

User: "what if we find levels like - 2-3 times reversal from the same point and then break of that point, higher high
creation, major point break, pivots, and other basic points like volume, liquidity".

Rounds 55 / 6 / 59 tested these one at a time (all flat or losing after fees). Here they are stacked on ONE event:
  Base event: a CLOSE through a level (>= 0.1 ATR) that price turned from 2+ (or 3+) times: confirmed fractal swings
              (5 bars each side, known only 5 bars later) within 0.3 ATR of each other in the last 300 bars.
  Entry: "break" = next open after the breakout candle; "retest" = within 10 bars price comes back to the level
         (low within 0.1 ATR of it) and closes on the breakout side -> next open. A close 0.5 ATR back through the
         level before that cancels the retest (failed break).
  Confluences (each on / off):
    structure  higher high AND higher low (last two confirmed swings) for longs; lower high + lower low for shorts
    major      the level is the highest high (lowest low) of the last 100 bars - a major point, not a minor one
    pivot      a daily or weekly floor pivot (P, R1, R2, S1, S2 of the previous day / week) within 0.5 ATR of the level
    volume     breakout candle volume >= 1.5x or 2x its 20-bar average
    sweep      liquidity grab first: in the last 20 bars a wick took out the last swing low (high) and closed back
    trend      EMA 200 (longs above, shorts below)
Stop: 1 ATR beyond the level. Exits: 2R, 3R, 3 ATR trailing stop. Time cap 2 days (15m), 5 days (1h), 10 days (4h).
Coins: 15m BTC ETH SOL BNB XRP (2024-), 1h / 4h 10 coins + PAXG (2023-). Costs 16 bps. Design < 1 Apr 2025 <= holdout.
Control: each variant's trades re-placed on random bars (same side, stop and target distance in ATR, same trend side), 5x.
Usage: python research/round61_confluence_levels.py <data_dir>
"""
import itertools
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from numba import njit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import round55_engine as E  # noqa: E402

DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data/global"
OUT = os.path.join(HERE, "round61_results.json")
C11 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT", "PAXGUSDT"]
TFS = {"15m": C11[:5], "1h": C11, "4h": C11}
CAP = {"15m": 192, "1h": 120, "4h": 60}
EXITS = (("2R", 2.0, 0.0), ("3R", 3.0, 0.0), ("trail 3 ATR", 0.0, 3.0))
REPS = 5
K, LOOK, TOL, MAJ = 5, 300, 0.3, 100


def load(sym, tf):
    src = "5m" if tf == "15m" else "1h"
    df = pd.read_csv(f"{DATA}/crypto_{sym}_{src}.csv.gz", parse_dates=["ts"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    if src == "1h":
        df = df[df.index >= "2022-06-01"]
    rule = {"15m": "15min", "4h": "4h"}.get(tf)
    if rule:
        df = df.resample(rule, label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return df


@njit(cache=True)
def track(h, l, c, a, k, look, tol, min_t):
    """Breaks and retests of multi-touch levels + swing structure. Everything at bar i uses only swings confirmed by i."""
    n = len(c)
    nan = np.nan
    bl, bs, rl, rs = np.full(n, nan), np.full(n, nan), np.full(n, nan), np.full(n, nan)
    sh1, sh2, sl1, sl2 = np.full(n, nan), np.full(n, nan), np.full(n, nan), np.full(n, nan)
    M = 400
    rp, rt, rlast, ra = np.zeros(M), np.zeros(M, np.int64), np.zeros(M, np.int64), np.zeros(M, np.bool_)
    sp, st, slast, sa = np.zeros(M), np.zeros(M, np.int64), np.zeros(M, np.int64), np.zeros(M, np.bool_)
    h1 = h2 = l1 = l2 = nan
    ret_l_px, ret_l_until, ret_s_px, ret_s_until = nan, -1, nan, -1
    for i in range(1, n):
        A = a[i]
        if not (A > 0):
            continue
        p = i - k
        if p >= k:
            ish, isl = True, True
            for q in range(p - k, p + k + 1):
                if h[q] > h[p]:
                    ish = False
                if l[q] < l[p]:
                    isl = False
            if ish:
                h2, h1 = h1, h[p]
                hit = -1
                for j in range(M):
                    if ra[j] and abs(rp[j] - h[p]) <= tol * A:
                        hit = j
                        break
                if hit >= 0:
                    rp[hit] = (rp[hit] * rt[hit] + h[p]) / (rt[hit] + 1)
                    rt[hit] += 1
                    rlast[hit] = p
                else:
                    for j in range(M):
                        if not ra[j]:
                            ra[j], rp[j], rt[j], rlast[j] = True, h[p], 1, p
                            break
            if isl:
                l2, l1 = l1, l[p]
                hit = -1
                for j in range(M):
                    if sa[j] and abs(sp[j] - l[p]) <= tol * A:
                        hit = j
                        break
                if hit >= 0:
                    sp[hit] = (sp[hit] * st[hit] + l[p]) / (st[hit] + 1)
                    st[hit] += 1
                    slast[hit] = p
                else:
                    for j in range(M):
                        if not sa[j]:
                            sa[j], sp[j], st[j], slast[j] = True, l[p], 1, p
                            break
        sh1[i], sh2[i], sl1[i], sl2[i] = h1, h2, l1, l2
        # retests of earlier breaks
        if ret_l_until >= i:
            if c[i] < ret_l_px - 0.5 * A:
                ret_l_until = -1
            elif l[i] <= ret_l_px + 0.1 * A and c[i] > ret_l_px:
                rl[i] = ret_l_px
                ret_l_until = -1
        if ret_s_until >= i:
            if c[i] > ret_s_px + 0.5 * A:
                ret_s_until = -1
            elif h[i] >= ret_s_px - 0.1 * A and c[i] < ret_s_px:
                rs[i] = ret_s_px
                ret_s_until = -1
        # breaks
        best, best_t = nan, 0
        for j in range(M):
            if not ra[j]:
                continue
            if i - rlast[j] > look:
                ra[j] = False
                continue
            if c[i] > rp[j] + 0.1 * A:
                if rt[j] >= min_t and c[i - 1] <= rp[j] + 0.1 * A and rt[j] > best_t:
                    best, best_t = rp[j], rt[j]
                ra[j] = False
        if best_t > 0:
            bl[i] = best
            ret_l_px, ret_l_until = best, i + 10
        best, best_t = nan, 0
        for j in range(M):
            if not sa[j]:
                continue
            if i - slast[j] > look:
                sa[j] = False
                continue
            if c[i] < sp[j] - 0.1 * A:
                if st[j] >= min_t and c[i - 1] >= sp[j] - 0.1 * A and st[j] > best_t:
                    best, best_t = sp[j], st[j]
                sa[j] = False
        if best_t > 0:
            bs[i] = best
            ret_s_px, ret_s_until = best, i + 10
    return bl, bs, rl, rs, sh1, sh2, sl1, sl2


def pivots(df):
    """Previous UTC day / week floor pivots (P, R1, R2, S1, S2) mapped onto each bar: array (n, 10)."""
    out = []
    for rule in ("1D", "W-SUN"):
        g = df.resample(rule, label="left", closed="left").agg({"high": "max", "low": "min", "close": "last"}).dropna()
        P = (g.high + g.low + g.close) / 3
        lv = pd.DataFrame({"P": P, "R1": 2 * P - g.low, "S1": 2 * P - g.high, "R2": P + (g.high - g.low), "S2": P - (g.high - g.low)})
        lv.index = lv.index + (pd.Timedelta(days=1) if rule == "1D" else pd.Timedelta(days=7))     # usable from the next period
        out.append(lv.reindex(df.index, method="ffill").values)
    return np.hstack(out)


def main():
    t0 = time.time()
    rng = np.random.default_rng(61)
    res = {}
    score_rows = {}
    for tf, coins in TFS.items():
        for sym in coins:
            df = load(sym, tf)
            o, h, l, c, v = (df[x].values for x in ("open", "high", "low", "close", "volume"))
            a = E.atr(df)
            e200 = E.ema(c, 200)
            n = len(c)
            z = np.zeros(n, np.bool_)
            nan = np.full(n, np.nan)
            valid = (df.index >= pd.Timestamp("2023-01-01", tz="UTC")) & np.isfinite(a) & (a > 0)
            valid[-2:] = False
            hold = df.index >= E.SPLIT
            years = df.index.year.values
            vavg = pd.Series(v).rolling(20, min_periods=5).mean().shift(1).values
            vr = v / np.where(vavg > 0, vavg, np.nan)
            hh = pd.Series(h).rolling(MAJ, min_periods=20).max().shift(1).values
            ll = pd.Series(l).rolling(MAJ, min_periods=20).min().shift(1).values
            PV = pivots(df)
            POOLS = {f: {(s_, hd_): np.flatnonzero(valid & (hold == hd_) & (True if not f else ((c >= e200) if s_ == 1 else (c <= e200))))
                         for s_ in (1, -1) for hd_ in (False, True)} for f in (False, True)}
            for min_t in (2, 3):
                bl, bs, rl, rs, sh1, sh2, sl1, sl2 = track(h, l, c, a, K, LOOK, TOL, min_t)
                ev_l = (l < sl1) & (c > sl1)                 # wick through the last swing low, close back above: sell-side liquidity taken
                ev_s = (h > sh1) & (c < sh1)
                sw_l = pd.Series(ev_l.astype(float)).rolling(20, min_periods=1).max().values > 0
                sw_s = pd.Series(ev_s.astype(float)).rolling(20, min_periods=1).max().values > 0
                struct_l, struct_s = (sh1 > sh2) & (sl1 > sl2), (sh1 < sh2) & (sl1 < sl2)
                for entry in ("break", "retest"):
                    Ll, Ls = (bl, bs) if entry == "break" else (rl, rs)
                    # carry the break bar's volume / major / pivot facts to the retest bar: evaluate them on the level itself
                    lvl_l, lvl_s = np.isfinite(Ll) & valid, np.isfinite(Ls) & valid
                    pvd_l = np.nanmin(np.abs(PV - np.nan_to_num(Ll)[:, None]), axis=1) <= 0.5 * a
                    pvd_s = np.nanmin(np.abs(PV - np.nan_to_num(Ls)[:, None]), axis=1) <= 0.5 * a
                    if entry == "break":
                        maj_l, maj_s = Ll >= hh - 0.3 * a, Ls <= ll + 0.3 * a
                        vol_now = vr
                    else:      # for a retest, "major" and "volume" refer to the breakout candle (up to 10 bars before)
                        bk_maj_l = pd.Series(np.where(np.isfinite(bl), (bl >= hh - 0.3 * a).astype(float), np.nan)).ffill(limit=10).fillna(0).values > 0
                        bk_maj_s = pd.Series(np.where(np.isfinite(bs), (bs <= ll + 0.3 * a).astype(float), np.nan)).ffill(limit=10).fillna(0).values > 0
                        maj_l, maj_s = bk_maj_l, bk_maj_s
                        bkv = pd.Series(np.where(np.isfinite(bl) | np.isfinite(bs), vr, np.nan)).ffill(limit=10).values
                        vol_now = bkv
                    stop_l, stop_s = Ll - a, Ls + a
                    for st_, mj, pv_, vm, sw, tr in itertools.product((0, 1), (0, 1), (0, 1), (0.0, 1.5, 2.0), (0, 1), (0, 1)):
                        L, S = lvl_l.copy(), lvl_s.copy()
                        if st_:
                            L &= struct_l; S &= struct_s
                        if mj:
                            L &= maj_l; S &= maj_s
                        if pv_:
                            L &= pvd_l; S &= pvd_s
                        if vm:
                            L &= np.nan_to_num(vol_now) >= vm; S &= np.nan_to_num(vol_now) >= vm
                        if sw:
                            L &= sw_l; S &= sw_s
                        if tr:
                            L &= c >= e200; S &= c <= e200
                        side = np.zeros(n, np.int64)
                        side[L] = 1
                        side[S & ~L] = -1
                        stop = np.where(side == 1, stop_l, np.where(side == -1, stop_s, np.nan))
                        idx = np.flatnonzero(side)
                        ds = np.abs(c[idx] - stop[idx]) / a[idx]
                        ss, hs_ = side[idx], hold[idx]
                        ctl = []
                        for _ in range(REPS if len(idx) else 0):
                            cs, cst = np.zeros(n, np.int64), np.full(n, np.nan)
                            for (s_, hd_), pool in POOLS[bool(tr)].items():
                                m = (ss == s_) & (hs_ == hd_)
                                if m.any() and len(pool):
                                    js = pool[rng.integers(len(pool), size=int(m.sum()))]
                                    cs[js], cst[js] = s_, c[js] - s_ * ds[m] * a[js]
                            ctl.append((cs, cst))
                        for exn, trt, trail in EXITS:
                            en, _, R, b, g = E.run(o, h, l, c, a, side, stop, nan, z, z, trt, trail, 0.0, 0.0, CAP[tf], 0.0)
                            key = json.dumps({"tf": tf, "touches": min_t, "entry": entry, "structure": st_, "major": mj, "pivot": pv_,
                                              "volume": vm, "sweep": sw, "trend": tr, "exit": exn})
                            d = res.setdefault(key, {"R": [], "h": [], "coin": [], "yr": [], "cR": [], "ch": []})
                            d["R"] += R.tolist(); d["h"] += hold[en].tolist(); d["coin"] += [sym] * len(R); d["yr"] += years[en].tolist()
                            for cs, cst in ctl:
                                en2, _, R2, _, _ = E.run(o, h, l, c, a, cs, cst, nan, z, z, trt, trail, 0.0, 0.0, CAP[tf], 0.0)
                                d["cR"] += R2.tolist(); d["ch"] += hold[en2].tolist()
                            # confluence count on the plain version (no filter applied): how many boxes each trade ticked
                            if not (st_ or mj or pv_ or vm or sw or tr) and exn in ("2R", "trail 3 ATR"):
                                sig = en - 1
                                isL = side[sig] == 1
                                sc = (np.where(isL, struct_l[sig], struct_s[sig]).astype(int) + np.where(isL, maj_l[sig], maj_s[sig]).astype(int)
                                      + np.where(isL, pvd_l[sig], pvd_s[sig]).astype(int) + (np.nan_to_num(vol_now[sig]) >= 1.5).astype(int)
                                      + np.where(isL, sw_l[sig], sw_s[sig]).astype(int) + np.where(isL, c[sig] >= e200[sig], c[sig] <= e200[sig]).astype(int))
                                kk = f"{tf}|{min_t}|{entry}|{exn}"
                                s2 = score_rows.setdefault(kk, {"R": [], "sc": [], "h": []})
                                s2["R"] += R.tolist(); s2["sc"] += sc.tolist(); s2["h"] += hold[en].tolist()
        print(tf, "done", round(time.time() - t0), "s", flush=True)
    rows = []
    for key, d in res.items():
        R, hd, coin, yr = np.array(d["R"]), np.array(d["h"], bool), np.array(d["coin"]), np.array(d["yr"])
        cR, ch = np.array(d["cR"]), np.array(d["ch"], bool)
        per = {s: round(float(R[(coin == s) & hd].mean()), 3) for s in np.unique(coin) if ((coin == s) & hd).sum()}
        rows.append({"v": json.loads(key), "design": E.stats(R[~hd]), "holdout": E.stats(R[hd]),
                     "ctl_design": round(float(cR[~ch].mean()), 3) if (~ch).sum() else None,
                     "ctl_holdout": round(float(cR[ch].mean()), 3) if ch.sum() else None,
                     "years": {str(y): round(float(R[yr == y].mean()), 3) for y in np.unique(yr)},
                     "coins_pos_holdout": sum(1 for x in per.values() if x > 0), "coins": len(per)})
    scores = {}
    for kk, s2 in score_rows.items():
        R, sc, hd = np.array(s2["R"]), np.array(s2["sc"]), np.array(s2["h"], bool)
        scores[kk] = {int(x): {"design": E.stats(R[(sc == x) & ~hd]), "holdout": E.stats(R[(sc == x) & hd])} for x in np.unique(sc)}
    json.dump({"rows": rows, "scores": scores}, open(OUT, "w"))
    print("variants", len(rows), round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
