"""Round 64: the signals on another AI's crypto "priority list" that earlier rounds had not tested (29 Oct 2026).

Already tested (see rounds 1-63): double top/bottom, H&S, flags, pennants, triangles, wedges, Gartley (r57, r60), candlesticks
(round 2), BOS / CHoCH / HH-HL / liquidity sweeps / equal highs-lows (r6 / r52 / r61), MACD and EMA crossovers and the EMA trend
filter (r1, r58, r62), Bollinger squeeze (crypto candles round). New here:
  A. Standalone on 1h / 4h / daily, 11 coins (incl. PAXG), 2023 - Oct 2026, 16 bps, entry next open:
     rsi_div      regular RSI(14) divergence at a confirmed 5-bar swing (price lower low + RSI higher low within 60 bars ->
                  long; mirror short); signal when the swing is confirmed; stop 0.5 ATR beyond the swing
     atr_expand   a bar with range >= 2x (or 1.5x) ATR(14) closing in its top / bottom quarter -> trade its direction; stop
                  at the far end of the bar
     bat / butterfly / crab / abcd   harmonic XABCD on an ATR zigzag (2 or 3 ATR), entered when D is confirmed;
                  stop 1 ATR beyond D (beyond X for the bat); target 61.8% of AD (or a 3 ATR trail)
     Exits 2R / 3 ATR trailing stop (harmonics: 0.618 AD / trail); trend filter none / EMA 200. Random-entry control.
  B. As filters on the LIVE crypto rules (Supertrend 10/3 4h + daily, Donchian 55 4h long, Donchian 20 daily long, live
     exits): take the live trade only when
     adx20       ADX(14) >= 20 (trend detected, not a range)
     chop        Choppiness(14) < 61.8 (not choppy)
     vol_notlow  ATR% not in the lowest third of its past year;  vol_nothigh: not in the highest third
     btc200      BTC above its 200-day average for longs, below for shorts
     squeeze     Bollinger width in its lowest 20% (1y) at some bar of the last 20 (breakout from a squeeze)
     no_div      no RSI divergence against the trade in the last 20 bars
     expand      the signal bar itself is an ATR expansion bar (range >= 1.5 ATR)
     The user's rule: a filter only counts if it ADDS profit - total R higher than the live rule in BOTH the design period
     (before 1 Apr 2025) and the later period - and beats skipping the same share of trades at random (200 shuffles).
Usage: python research/round64_untested_signals.py <global_data_dir>
"""
import itertools
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import round55_engine as E  # noqa: E402
from fno import patterns as PT  # noqa: E402

DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data/global"
OUT = os.path.join(HERE, "round64_results.json")
C11 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT", "PAXGUSDT"]
CAP = {"1h": 120, "4h": 60, "1d": 30}
START = pd.Timestamp("2023-01-01", tz="UTC")


def load(sym, tf):
    df = E.load(DATA, sym, tf)
    return df[df.index >= "2022-01-01"]


def wilder(x, n):
    return pd.Series(x).ewm(alpha=1 / n, adjust=False).mean().values


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    up, dn = wilder(np.where(d > 0, d, 0.0), n), wilder(np.where(d < 0, -d, 0.0), n)
    return 100 - 100 / (1 + up / np.where(dn == 0, 1e-12, dn))


def adx(h, l, c, n=14):
    up, dn = np.diff(h, prepend=h[0]), -np.diff(l, prepend=l[0])
    pdm, ndm = np.where((up > dn) & (up > 0), up, 0.0), np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    at = wilder(tr, n)
    pdi, ndi = 100 * wilder(pdm, n) / at, 100 * wilder(ndm, n) / at
    dx = 100 * abs(pdi - ndi) / np.where(pdi + ndi == 0, 1e-12, pdi + ndi)
    return wilder(dx, n)


def chop(h, l, c, n=14):
    pc = np.r_[c[0], c[:-1]]
    tr = pd.Series(np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc))))
    rng = pd.Series(h).rolling(n).max() - pd.Series(l).rolling(n).min()
    return (100 * np.log10(tr.rolling(n).sum() / rng) / np.log10(n)).values


def swings(h, l, k=5):
    n = len(h)
    sh, sl = np.zeros(n, bool), np.zeros(n, bool)
    for p in range(k, n - k):
        if h[p] >= h[p - k:p + k + 1].max():
            sh[p] = True
        if l[p] <= l[p - k:p + k + 1].min():
            sl[p] = True
    return sh, sl


def divergence(h, l, c, r, a, k=5, look=60):
    """Signals at the confirmation bar (p + k) of a swing that forms a regular divergence with the previous swing."""
    n = len(c)
    sh, sl = swings(h, l, k)
    bull, bear = np.zeros(n, bool), np.zeros(n, bool)
    stop_l, stop_s = np.full(n, np.nan), np.full(n, np.nan)
    last_l = last_h = None
    for p in range(n):
        if sl[p]:
            if last_l is not None and p - last_l <= look and l[p] < l[last_l] and r[p] > r[last_l] and p + k < n:
                bull[p + k], stop_l[p + k] = True, l[p] - 0.5 * a[p + k]
            last_l = p
        if sh[p]:
            if last_h is not None and p - last_h <= look and h[p] > h[last_h] and r[p] < r[last_h] and p + k < n:
                bear[p + k], stop_s[p + k] = True, h[p] + 0.5 * a[p + k]
            last_h = p
    return bull, bear, stop_l, stop_s


def harmonics(h, l, c, a, zz):
    """XABCD harmonics (bat, butterfly, crab) and AB=CD on the ATR zigzag; signal when D is confirmed."""
    n = len(c)
    out = {nm: (np.zeros(n, np.int64), np.full(n, np.nan), np.full(n, np.nan)) for nm in ("bat", "butterfly", "crab", "abcd")}
    piv = PT.zigzag(h, l, a, zz)
    near = lambda x, lo, hi, t=0.05: lo - t <= x <= hi + t      # noqa: E731
    for j in range(4, len(piv)):
        (i0, X, _, _), (i1, A_, _, _), (i2, B, _, _), (i3, C_, _, _), (i4, D, t4, rd) = piv[j - 4:j + 1]
        if rd >= n - 1 or not (a[rd] > 0):
            continue
        s = 1 if t4 == -1 else -1                             # D is a low -> long
        XA, AB, BC, CD = abs(A_ - X), abs(A_ - B), abs(C_ - B), abs(C_ - D)
        if XA <= 0 or AB <= 0 or BC <= 0:
            continue
        ab, bc, xd = AB / XA, BC / AB, abs(A_ - D) / XA
        AD = abs(A_ - D)
        tgt = D + s * 0.618 * AD

        def put(nm, stop):
            side, st, tg = out[nm]
            if side[rd] == 0 and s * (c[rd] - stop) > 0 and s * (tgt - c[rd]) > 0:
                side[rd], st[rd], tg[rd] = s, stop, tgt
        if (A_ - X) * s > 0:                                  # proper XA leg for the side
            if near(ab, 0.382, 0.5) and near(bc, 0.382, 0.886, 0) and near(xd, 0.886, 0.886, 0.03):
                put("bat", X - s * 0.5 * a[rd])
            if near(ab, 0.786, 0.786) and near(bc, 0.382, 0.886, 0) and near(xd, 1.27, 1.27, 0.07):
                put("butterfly", D - s * a[rd])
            if near(ab, 0.382, 0.618, 0) and near(bc, 0.382, 0.886, 0) and near(xd, 1.618, 1.618, 0.08):
                put("crab", D - s * a[rd])
        # AB=CD: A -> B leg, C retraces 61.8-78.6% of it, C -> D equals A -> B (+-10%) and D goes past B
        if near(bc, 0.618, 0.786, 0.02) and 0.9 <= CD / AB <= 1.1 and (A_ - B) * s > 0 and (B - D) * s > 0:
            put("abcd", D - s * a[rd])
    return out


# ------------------------------------------------------------------ part A
def part_a():
    rng = np.random.default_rng(64)
    res = {}
    for tf in ("1h", "4h", "1d"):
        for sym in C11:
            df = load(sym, tf)
            o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
            a = E.atr(df)
            e200 = E.ema(c, 200)
            n = len(c)
            z = np.zeros(n, np.bool_)
            nanv = np.full(n, np.nan)
            valid = (df.index >= START) & np.isfinite(a) & (a > 0)
            valid[-2:] = False
            hold = df.index >= E.SPLIT
            yrs = df.index.year.values
            r = rsi(c)
            sigs = {}
            bu, be, sl_, ss_ = divergence(h, l, c, r, a)
            side = np.zeros(n, np.int64); side[bu] = 1; side[be & ~bu] = -1
            sigs["rsi_div"] = (side, np.where(side == 1, sl_, np.where(side == -1, ss_, np.nan)), nanv)
            pc = np.r_[c[0], c[:-1]]
            rngb = h - l
            pa = np.r_[a[0], a[:-1]]
            for m in (1.5, 2.0):
                big = rngb >= m * pa
                upc, dnc = (c - l) >= 0.75 * rngb, (h - c) >= 0.75 * rngb
                side = np.zeros(n, np.int64); side[big & upc] = 1; side[big & dnc & ~upc] = -1
                sigs[f"atr_expand_{m}"] = (side, np.where(side == 1, l, np.where(side == -1, h, np.nan)), nanv)
            for zz in (2.0, 3.0):
                for nm, v in harmonics(h, l, c, a, zz).items():
                    sigs[f"{nm}_zz{int(zz)}"] = v
            pools = {(f, s_, hd): np.flatnonzero(valid & (hold == hd) & (True if not f else ((c >= e200) if s_ == 1 else (c <= e200))))
                     for f in (False, True) for s_ in (1, -1) for hd in (False, True)}
            for nm, (side0, stop, tgt) in sigs.items():
                exits = [("2R", 2.0, 0.0, nanv), ("trail 3 ATR", 0.0, 3.0, nanv)]
                if np.isfinite(tgt).any():
                    exits.insert(0, ("harmonic target", 0.0, 0.0, tgt))
                for flt in (False, True):
                    sd = side0.copy()
                    sd[~valid] = 0
                    if flt:
                        sd[(sd == 1) & (c < e200)] = 0; sd[(sd == -1) & (c > e200)] = 0
                    idx = np.flatnonzero(sd)
                    ds = np.abs(c[idx] - stop[idx]) / a[idx]
                    dt = np.abs(tgt[idx] - c[idx]) / a[idx]
                    ctl = []
                    for _ in range(10 if len(idx) else 0):
                        cs, cst, ctg = np.zeros(n, np.int64), np.full(n, np.nan), np.full(n, np.nan)
                        for q, (s_, hd) in enumerate(zip(sd[idx], hold[idx])):
                            pool = pools[(flt, s_, hd)]
                            if len(pool):
                                jj = pool[rng.integers(len(pool))]
                                cs[jj], cst[jj], ctg[jj] = s_, c[jj] - s_ * ds[q] * a[jj], c[jj] + s_ * dt[q] * a[jj]
                        ctl.append((cs, cst, ctg))
                    for exn, tr, trail, tg in exits:
                        en, _, R, b, g = E.run(o, h, l, c, a, sd, stop, tg, z, z, tr, trail, 0.0, 0.0, CAP[tf], 0.3 if exn == "harmonic target" else 0.0)
                        key = json.dumps({"signal": nm.rsplit("_zz", 1)[0], "zz": nm.rsplit("_zz", 1)[1] if "_zz" in nm else None, "tf": tf, "ema200": flt, "exit": exn})
                        d = res.setdefault(key, {"R": [], "h": [], "yr": [], "sym": [], "cR": [], "ch": []})
                        d["R"] += R.tolist(); d["h"] += hold[en].tolist(); d["yr"] += yrs[en].tolist(); d["sym"] += [sym] * len(R)
                        for cs, cst, ctg in ctl:
                            en2, _, R2, _, _ = E.run(o, h, l, c, a, cs, cst, ctg if exn == "harmonic target" else nanv, z, z, tr, trail, 0.0, 0.0,
                                                     CAP[tf], 0.3 if exn == "harmonic target" else 0.0)
                            d["cR"] += R2.tolist(); d["ch"] += hold[en2].tolist()
        print("A", tf, flush=True)
    rows = []
    for key, d in res.items():
        R, hd, yr, sy = np.array(d["R"]), np.array(d["h"], bool), np.array(d["yr"]), np.array(d["sym"])
        cR, ch = np.array(d["cR"]), np.array(d["ch"], bool)
        if not len(R):
            continue
        rows.append({"v": json.loads(key), "design": E.stats(R[~hd]), "holdout": E.stats(R[hd]),
                     "ctl_design": round(float(cR[~ch].mean()), 3) if (~ch).any() else None,
                     "ctl_holdout": round(float(cR[ch].mean()), 3) if ch.any() else None,
                     "years": {str(y): round(float(R[yr == y].mean()), 3) for y in np.unique(yr)},
                     "coins_pos_holdout": int(sum(1 for s in np.unique(sy) if (hd & (sy == s)).any() and R[hd & (sy == s)].mean() > 0))})
    return rows


# ------------------------------------------------------------------ part B
LIVE = {"Supertrend 4h": ("4h", "st", 0.0), "Supertrend daily": ("1d", "st", 0.0), "Donchian 55 4h": ("4h", 55, 4.0), "Donchian 20 daily": ("1d", 20, 3.0)}


def part_b():
    rng = np.random.default_rng(65)
    btc = load("BTCUSDT", "1d")
    btc_up = pd.Series(btc["close"].values > pd.Series(btc["close"].values).rolling(200).mean().values, index=btc.index + pd.Timedelta(days=1))
    out = {}
    for rule, (tf, kind, trail) in LIVE.items():
        rows = []
        for sym in C11:
            df = load(sym, tf)
            o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
            a = E.atr(df)
            n = len(c)
            side = np.zeros(n, np.int64)
            ol, osr = np.zeros(n, np.bool_), np.zeros(n, np.bool_)
            if kind == "st":
                dr = E.supertrend_dir(h, l, c, a, 3.0)
                fl = np.r_[False, (dr[1:] == 1) & (dr[:-1] == -1)]
                fs = np.r_[False, (dr[1:] == -1) & (dr[:-1] == 1)]
                side[fl], side[fs] = 1, -1
                ol, osr = fl, fs
            else:
                up = pd.Series(h).rolling(kind).max().shift(1).values
                side[(c > up) & np.r_[False, c[:-1] <= up[:-1]]] = 1
            side[df.index < START] = 0
            per = 365 * (6 if tf == "4h" else 1)
            atrp = pd.Series(a / c)
            q_lo = atrp.rolling(per, min_periods=per // 2).quantile(1 / 3).values
            q_hi = atrp.rolling(per, min_periods=per // 2).quantile(2 / 3).values
            bbw = (4 * pd.Series(c).rolling(20).std() / pd.Series(c).rolling(20).mean())
            sq = (bbw <= bbw.rolling(per, min_periods=per // 2).quantile(0.2)).astype(float).rolling(20, min_periods=1).max().values > 0
            r = rsi(c)
            bu, be, _, _ = divergence(h, l, c, r, a)
            bu20 = pd.Series(bu.astype(float)).rolling(20, min_periods=1).max().values > 0
            be20 = pd.Series(be.astype(float)).rolling(20, min_periods=1).max().values > 0
            bt = btc_up.reindex(df.index, method="ffill").fillna(False).values.astype(bool)
            F = {"adx20": (adx(h, l, c) >= 20,) * 2, "chop": (chop(h, l, c) < 61.8,) * 2,
                 "vol_notlow": (atrp.values > q_lo,) * 2, "vol_nothigh": (atrp.values < q_hi,) * 2,
                 "btc200": (bt, ~bt), "squeeze": (sq, sq), "no_div": (~be20, ~bu20), "expand": ((h - l) >= 1.5 * np.r_[a[0], a[:-1]],) * 2}
            nanv = np.full(n, np.nan)
            en, sd, R, b, g = E.run(o, h, l, c, a, side, nanv, nanv, ol, osr, 0.0, trail, 0.0, 0.0, 0, 0.0, 2.0)
            sig = en - 1
            flags = {f: np.where(sd == 1, lo[sig], sh_[sig]) for f, (lo, sh_) in F.items()}
            rows.append(pd.DataFrame({"R": R, "h": df.index[en] >= E.SPLIT, "sym": sym, **flags}))
        T = pd.concat(rows, ignore_index=True)
        base = {"design": round(float(T.R[~T.h].sum()), 1), "holdout": round(float(T.R[T.h].sum()), 1), "n": len(T),
                "avg_d": round(float(T.R[~T.h].mean()), 3), "avg_h": round(float(T.R[T.h].mean()), 3)}
        res = {"live": base, "filters": {}}
        for f in F:
            k = T[f].astype(bool).values
            tot_d, tot_h = float(T.R[k & ~T.h].sum()), float(T.R[k & T.h].sum())
            # shuffle: keep the same number of trades at random in each period
            sims_d = [T.R[~T.h].sample(int((k & ~T.h).sum()), random_state=int(rng.integers(1e9))).sum() for _ in range(200)]
            sims_h = [T.R[T.h].sample(int((k & T.h).sum()), random_state=int(rng.integers(1e9))).sum() for _ in range(200)]
            res["filters"][f] = {"kept": round(float(k.mean() * 100), 1), "design": round(tot_d, 1), "holdout": round(tot_h, 1),
                                 "avg_d": round(float(T.R[k & ~T.h].mean()), 3), "avg_h": round(float(T.R[k & T.h].mean()), 3),
                                 "p_d": round(float(np.mean(np.array(sims_d) >= tot_d)), 3), "p_h": round(float(np.mean(np.array(sims_h) >= tot_h)), 3),
                                 "adds_profit": bool(tot_d > base["design"] and tot_h > base["holdout"])}
        out[rule] = res
        print("B", rule, base, flush=True)
    return out


def main():
    t0 = time.time()
    b = part_b()
    a = part_a()
    json.dump({"standalone": a, "filters": b}, open(OUT, "w"))
    print("done", round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
