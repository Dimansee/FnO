"""Round 57: classic chart patterns on crypto (28 Oct 2026).

User question: do chart patterns (double top/bottom, head & shoulders, triangles, wedges, flags ...) make money on
5m / 15m / 1h / 4h crypto charts?

Pivots: ATR zigzag (a swing is confirmed only once price has reversed zz x ATR from it: the confirmation bar is when the
pattern becomes known, no look-ahead). Patterns are read from the last 3-5 confirmed pivots; the trade fires on the first
CLOSE through the pattern's breakout line within `width` bars of the last pivot's confirmation (and before the pattern is
invalidated on the other side). Entry next bar open, app exit order, 16 bps round trip (Binance taker + slippage).

Patterns (direction of the trade):
  double_top (short) / double_bottom (long): two equal swings (within 15% of the pattern height), break of the middle swing
  hs (short) / ihs (long): head & shoulders - head above both shoulders, shoulders level, break of the neckline line
  triple_top / triple_bottom: three equal swings, break of the lower / higher of the two middle swings
  asc_tri (long) / desc_tri (short): flat highs + rising lows / flat lows + falling highs, break of the flat side
  sym_tri (both): falling highs + rising lows, trade the side that breaks
  rect (both): flat highs and flat lows, trade the side that breaks
  rising_wedge (short) / falling_wedge (long): both lines the same way and converging, break against them
  bull_flag (long) / bear_flag (short): pole >= 4 ATR, pullback <= 50% of it, break of the pole's end
Stops: the other side of the pattern (textbook). Targets: measured move (pattern height from the break), or 1R / 2R.
Variants: zigzag 2 or 3 ATR, trend filter none / EMA 200, target mm / 1R / 2R, on 5m, 15m (BTC ETH SOL BNB XRP, 2024-),
1h and 4h (10 coins + PAXG, 2023-). Design < 1 Apr 2025 <= holdout.
Usage: python research/round57_patterns.py <data_dir>
"""
import itertools
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from numba import njit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import round55_engine as E  # noqa: E402

DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data/global"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "round57_patterns_results.json")
C11 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT", "PAXGUSDT"]
C5 = C11[:5]
TFS = {"5m": C5, "15m": C5, "1h": C11, "4h": C11}
CAP = {"5m": 144, "15m": 96, "1h": 48, "4h": 30}
PATTERNS = ["double_top", "double_bottom", "hs", "ihs", "triple_top", "triple_bottom", "asc_tri", "desc_tri", "sym_tri", "rect",
            "rising_wedge", "falling_wedge", "bull_flag", "bear_flag"]


def load(sym, tf):
    src = "5m" if tf in ("5m", "15m") else "1h"
    df = pd.read_csv(f"{DATA}/crypto_{sym}_{src}.csv.gz", parse_dates=["ts"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    if src == "1h":
        df = df[df.index >= "2023-01-01"]
    if tf in ("15m", "4h"):
        df = df.resample({"15m": "15min", "4h": "4h"}[tf], label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return df


@njit(cache=True)
def zigzag(h, l, a, k):
    """ATR zigzag. Returns pivot arrays: bar of the extreme, price, type (+1 high, -1 low), bar where it was confirmed."""
    n = len(h)
    pi = np.empty(n, np.int64)
    pp = np.empty(n)
    pt = np.empty(n, np.int64)
    pc = np.empty(n, np.int64)
    m = 0
    d = 0                   # 0 unknown, 1 looking for a high, -1 looking for a low
    ei = 0
    ep = h[0]
    lo_i, lo_p, hi_i, hi_p = 0, l[0], 0, h[0]
    for i in range(1, n):
        th = k * a[i] if np.isfinite(a[i]) else np.nan
        if not np.isfinite(th):
            continue
        if d == 0:
            if h[i] > hi_p:
                hi_i, hi_p = i, h[i]
            if l[i] < lo_p:
                lo_i, lo_p = i, l[i]
            if hi_p - l[i] >= th and hi_i < i:
                pi[m], pp[m], pt[m], pc[m] = hi_i, hi_p, 1, i
                m += 1
                d, ei, ep = -1, i, l[i]
            elif h[i] - lo_p >= th and lo_i < i:
                pi[m], pp[m], pt[m], pc[m] = lo_i, lo_p, -1, i
                m += 1
                d, ei, ep = 1, i, h[i]
            continue
        if d == 1:                      # rising leg: track the high
            if h[i] >= ep:
                ei, ep = i, h[i]
            elif ep - l[i] >= th:
                pi[m], pp[m], pt[m], pc[m] = ei, ep, 1, i
                m += 1
                d, ei, ep = -1, i, l[i]
        else:
            if l[i] <= ep:
                ei, ep = i, l[i]
            elif h[i] - ep >= th:
                pi[m], pp[m], pt[m], pc[m] = ei, ep, -1, i
                m += 1
                d, ei, ep = 1, i, h[i]
    return pi[:m], pp[:m], pt[:m], pc[:m]


def line_at(i1, p1, i2, p2, i):
    return p1 + (p2 - p1) * (i - i1) / (i2 - i1) if i2 != i1 else p1


def find_patterns(pi, pp, pt, pc, a):
    """Yield pattern instances: dict(name, side(+1/-1/0 both), ready bar, width, up_line(i), dn_line(i), stop_long, stop_short, height)."""
    out = []
    n = len(pi)
    for j in range(4, n):
        t = pt[j]
        ready = pc[j]
        A = a[pc[j]]
        if not np.isfinite(A) or A <= 0:
            continue
        # last pivots, oldest first
        P = [(pi[j - q], pp[j - q], pt[j - q]) for q in range(4, -1, -1)]   # 5 pivots
        (i0, p0, t0), (i1, p1, t1), (i2, p2, t2), (i3, p3, t3), (i4, p4, t4) = P
        width = max(i4 - i2, 3)

        def add(name, side, up, dn, stop_l, stop_s, height, w=width):
            if height < 2 * A:
                return
            out.append({"name": name, "side": side, "ready": int(ready), "end": int(ready + w), "up": up, "dn": dn,
                        "stop_l": stop_l, "stop_s": stop_s, "height": height})

        # ---- 3-pivot patterns ending at i4 (i2, i3, i4)
        if t4 == 1:     # ... high(i2) low(i3) high(i4)
            h = (p2 + p4) / 2 - p3
            if h > 0 and abs(p2 - p4) <= 0.15 * h and p1 < p3:
                add("double_top", -1, None, (lambda i, v=p3: v), None, max(p2, p4) + 0.1 * A, h)
            # bear flag: pole = high(i0)->? use low leg: pole from high i2? (handled on lows)
            pole = p2 - p1 if t2 == 1 else 0
        else:           # ... low(i2) high(i3) low(i4)
            h = p3 - (p2 + p4) / 2
            if h > 0 and abs(p2 - p4) <= 0.15 * h and p1 > p3:
                add("double_bottom", 1, (lambda i, v=p3: v), None, min(p2, p4) - 0.1 * A, None, h)
        # flags: pole = pivot i3 vs i2 (impulse), pullback = i4
        if t4 == -1:    # low(i2) -> high(i3) pole up, low(i4) pullback
            pole = p3 - p2
            if pole >= 4 * A and (p3 - p4) <= 0.5 * pole and (i4 - i3) <= 1.5 * max(i3 - i2, 1):
                add("bull_flag", 1, (lambda i, v=p3: v), None, p4 - 0.1 * A, None, pole, w=max(i4 - i3, 3) * 2)
        else:           # high(i2) -> low(i3) pole down, high(i4) pullback
            pole = p2 - p3
            if pole >= 4 * A and (p4 - p3) <= 0.5 * pole and (i4 - i3) <= 1.5 * max(i3 - i2, 1):
                add("bear_flag", -1, None, (lambda i, v=p3: v), None, p4 + 0.1 * A, pole, w=max(i4 - i3, 3) * 2)

        # ---- 5-pivot patterns (i0..i4)
        w5 = max(i4 - i0, 5)
        if t4 == 1:     # H L H L H
            Hs, Ls = (p0, p2, p4), (p1, p3)
            hh = max(Hs) - min(Ls)
            # head & shoulders
            sh = (p0 + p4) / 2
            if p2 > max(p0, p4) + 0.15 * (p2 - min(Ls)) and abs(p0 - p4) <= 0.2 * (p2 - min(Ls)):
                neck = (lambda i, a1=i1, b1=p1, a2=i3, b2=p3: line_at(a1, b1, a2, b2, i))
                add("hs", -1, None, neck, None, p4 + 0.1 * A, p2 - line_at(i1, p1, i3, p3, i2), w=w5 // 2)
            # triple top
            if max(Hs) - min(Hs) <= 0.15 * hh:
                add("triple_top", -1, None, (lambda i, v=min(Ls): v), None, max(Hs) + 0.1 * A, hh, w=w5 // 2)
        else:           # L H L H L
            Hs, Ls = (p1, p3), (p0, p2, p4)
            hh = max(Hs) - min(Ls)
            if p2 < min(p0, p4) - 0.15 * (max(Hs) - p2) and abs(p0 - p4) <= 0.2 * (max(Hs) - p2):
                neck = (lambda i, a1=i1, b1=p1, a2=i3, b2=p3: line_at(a1, b1, a2, b2, i))
                add("ihs", 1, neck, None, p4 - 0.1 * A, None, line_at(i1, p1, i3, p3, i2) - p2, w=w5 // 2)
            if max(Ls) - min(Ls) <= 0.15 * hh:
                add("triple_bottom", 1, (lambda i, v=max(Hs): v), None, min(Ls) - 0.1 * A, None, hh, w=w5 // 2)

        # ---- line patterns from the last 4 pivots (2 highs + 2 lows)
        q = [(pi[j - r], pp[j - r], pt[j - r]) for r in range(3, -1, -1)]
        hi = [(x, p) for x, p, tt in q if tt == 1]
        lo = [(x, p) for x, p, tt in q if tt == -1]
        if len(hi) == 2 and len(lo) == 2:
            (ha, hpa), (hb, hpb) = hi
            (la, lpa), (lb, lpb) = lo
            h0 = max(hpa, hpb) - min(lpa, lpb)
            if h0 <= 0:
                continue
            flat_h = abs(hpa - hpb) <= 0.1 * h0
            flat_l = abs(lpa - lpb) <= 0.1 * h0
            rise_h, fall_h = hpb > hpa + 0.1 * h0, hpb < hpa - 0.1 * h0
            rise_l, fall_l = lpb > lpa + 0.1 * h0, lpb < lpa - 0.1 * h0
            up = (lambda i, a1=ha, b1=hpa, a2=hb, b2=hpb: line_at(a1, b1, a2, b2, i))
            dn = (lambda i, a1=la, b1=lpa, a2=lb, b2=lpb: line_at(a1, b1, a2, b2, i))
            w4 = max(q[-1][0] - q[0][0], 4)
            last_hi, last_lo = hpb, lpb
            if flat_h and rise_l:
                add("asc_tri", 1, up, None, last_lo - 0.1 * A, None, h0, w=w4)
            if flat_l and fall_h:
                add("desc_tri", -1, None, dn, None, last_hi + 0.1 * A, h0, w=w4)
            if fall_h and rise_l:
                add("sym_tri", 0, up, dn, last_lo - 0.1 * A, last_hi + 0.1 * A, h0, w=w4)
            if flat_h and flat_l:
                add("rect", 0, up, dn, min(lpa, lpb) - 0.1 * A, max(hpa, hpb) + 0.1 * A, h0, w=w4)
            slope_h = (hpb - hpa) / max(hb - ha, 1)
            slope_l = (lpb - lpa) / max(lb - la, 1)
            if rise_h and rise_l and slope_l > slope_h:          # rising wedge: both up, lows faster -> converging
                add("rising_wedge", -1, None, dn, None, last_hi + 0.1 * A, h0, w=w4)
            if fall_h and fall_l and slope_h < slope_l:          # falling wedge: both down, highs faster
                add("falling_wedge", 1, up, None, last_lo - 0.1 * A, None, h0, w=w4)
    return out


def signals_for(df, a, zz, names):
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    n = len(c)
    pi, pp, pt, pc = zigzag(h, l, a, zz)
    pats = find_patterns(pi, pp, pt, pc, a)
    sig = {nm: (np.zeros(n, np.int64), np.full(n, np.nan), np.full(n, np.nan)) for nm in names}
    for P in pats:
        nm = P["name"]
        if nm not in sig:
            continue
        side_a, stop_a, mm_a = sig[nm]
        for i in range(P["ready"], min(P["end"], n - 1) + 1):
            up = P["up"](i) if P["up"] else None
            dn = P["dn"](i) if P["dn"] else None
            upp = P["up"](i - 1) if P["up"] else None
            dnp = P["dn"](i - 1) if P["dn"] else None
            # invalidation: price closes beyond the stop side before breaking out
            if P["side"] == -1 and P["stop_s"] is not None and c[i] > P["stop_s"]:
                break
            if P["side"] == 1 and P["stop_l"] is not None and c[i] < P["stop_l"]:
                break
            if up is not None and P["side"] in (1, 0) and c[i] > up and c[i - 1] <= upp:
                if side_a[i] == 0:
                    side_a[i], stop_a[i], mm_a[i] = 1, P["stop_l"], c[i] + P["height"]
                break
            if dn is not None and P["side"] in (-1, 0) and c[i] < dn and c[i - 1] >= dnp:
                if side_a[i] == 0:
                    side_a[i], stop_a[i], mm_a[i] = -1, P["stop_s"], c[i] - P["height"]
                break
    return sig


def main():
    t0 = time.time()
    res = {}
    for tf, coins in TFS.items():
        for sym in coins:
            df = load(sym, tf)
            o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
            a = E.atr(df)
            e200 = E.ema(c, 200)
            n = len(c)
            z = np.zeros(n, np.bool_)
            hold = df.index >= E.SPLIT
            for zz in (2.0, 3.0):
                sig = signals_for(df, a, zz, PATTERNS)
                for nm, (side, stop, mm) in sig.items():
                    for flt in ("none", "ema200"):
                        sd = side.copy()
                        if flt == "ema200":
                            sd[(sd == 1) & (c < e200)] = 0
                            sd[(sd == -1) & (c > e200)] = 0
                        for tg in ("mm", "1R", "2R"):
                            tgt = mm if tg == "mm" else np.full(n, np.nan)
                            tr = {"mm": 0.0, "1R": 1.0, "2R": 2.0}[tg]
                            en, s2, R, b, g = E.run(o, h, l, c, a, sd, stop, tgt, z, z, tr, 0.0, 0.0, 0.0, CAP[tf], 0.5 if tg == "mm" else 0.0)
                            key = json.dumps({"pat": nm, "tf": tf, "zz": zz, "flt": flt, "tgt": tg})
                            d = res.setdefault(key, {"R": [], "g": [], "h": [], "coin": [], "ts": []})
                            d["R"] += R.tolist()
                            d["g"] += g.tolist()
                            d["h"] += hold[en].tolist()
                            d["coin"] += [sym] * len(R)
                            d["ts"] += [x.isoformat() for x in df.index[en]]
        print(tf, "done", round(time.time() - t0), "s", flush=True)
    rows = []
    for key, d in res.items():
        R, g, hd, coin = np.array(d["R"]), np.array(d["g"]), np.array(d["h"], bool), np.array(d["coin"])
        per = {s: round(float(R[(coin == s) & hd].mean()), 3) for s in np.unique(coin) if ((coin == s) & hd).sum()}
        rows.append({"v": json.loads(key), "design": E.stats(R[~hd]), "holdout": E.stats(R[hd]),
                     "gross_design": round(float(g[~hd].mean()), 3) if (~hd).sum() else None,
                     "gross_holdout": round(float(g[hd].mean()), 3) if hd.sum() else None,
                     "coins_pos_holdout": sum(1 for x in per.values() if x > 0), "coins": len(per), "per_coin_holdout": per})
    json.dump(rows, open(OUT, "w"))
    print("variants", len(rows), "saved", round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
