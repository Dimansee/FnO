"""Round 59: order blocks + fair value gaps + VOLUME on crypto (28 Oct 2026).

Round 52 found no edge in OB / FVG on crypto 5m / 1h (random trend entries did as well). Not tested there:
volume confirmation, 4h bars, trailing exits, and maker (limit-order) fees. This round adds all four.

Zones (decided on closed bars only; the zone is known at the close of the bar that completes it):
  FVG (bullish): low[i] > high[i-2], the middle bar i-1 is the impulse. Zone = [high[i-2], low[i]], entry at the top.
  Order block (bullish): the last bearish bar k before an impulse that within 3 bars closes above the highest high of
     the 10 bars before k and has moved >= 1.5 ATR above high[k]. Zone = [low[k], high[k]], entry at high[k].
  Bearish versions mirror these. Gap / zone must be >= 0.1 ATR.
Volume filters (on the impulse bar): none, volume >= 1.5x or >= 2x its 20-bar average; and optionally a "quiet
  pullback": the bar that touches the zone has volume below its 20-bar average.
Impulse strength: body >= 0.5 or >= 1.0 ATR.
Entry: limit order at the zone edge, valid 30 bars, cancelled if price closes beyond the far side first. A fill bar
  that also touches the stop counts as a full loss; a target on the fill bar is not counted (conservative).
Stop: beyond the far side of the zone - 0.1 ATR. Exit: 2R, 3R, or a 4 ATR trailing stop (no time limit beyond 30 days).
Trend filter: none / EMA 200 on the same bars.
Costs: 16 bps round trip (taker, as everywhere) and 6 bps (maker limit entry + 0.5 bps slippage on the exit side,
  closer to a real limit-order fill) reported side by side.
Timeframes: 15m (BTC ETH SOL BNB XRP, 2024-), 1h and 4h (10 coins + PAXG, 2023-). Design < 1 Apr 2025 <= holdout.
Usage: python research/round59_ob_fvg_volume.py <data_dir>
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
sys.path.insert(0, os.path.dirname(HERE))
import round55_engine as E  # noqa: E402
from fno import sessions as SS  # noqa: E402

DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data/global"
OUT = os.path.join(HERE, "round59_results.json")
C11 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT", "PAXGUSDT"]
TFS = {"15m": C11[:5], "1h": C11, "4h": C11}
PER_DAY = {"15m": 96, "1h": 24, "4h": 6}
VALID = 30


def load(sym, tf):
    src = "5m" if tf == "15m" else "1h"
    df = pd.read_csv(f"{DATA}/crypto_{sym}_{src}.csv.gz", parse_dates=["ts"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    if src == "1h":
        df = df[df.index >= "2022-09-01"]
    rule = {"15m": "15min", "4h": "4h"}.get(tf)
    if rule:
        df = df.resample(rule, label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return df


@njit(cache=True)
def zones(o, h, l, c, v, a, vavg, kind, min_body, vol_mult):
    """Returns per-bar arrays: side (+1/-1/0), entry price, far edge (for stop / invalidation), impulse index.
    kind 0 = FVG, 1 = order block. The zone is known at the close of bar i."""
    n = len(c)
    side = np.zeros(n, np.int64)
    ent = np.full(n, np.nan)
    far = np.full(n, np.nan)
    for i in range(12, n):
        A = a[i]
        if not np.isfinite(A) or A <= 0:
            continue
        if kind == 0:
            m = i - 1                                   # impulse bar
            body = abs(c[m] - o[m])
            if body < min_body * A or (vol_mult > 0 and not (v[m] >= vol_mult * vavg[m])):
                continue
            if l[i] > h[i - 2] and c[m] > o[m] and l[i] - h[i - 2] >= 0.1 * A:
                side[i], ent[i], far[i] = 1, l[i], h[i - 2]
            elif h[i] < l[i - 2] and c[m] < o[m] and l[i - 2] - h[i] >= 0.1 * A:
                side[i], ent[i], far[i] = -1, h[i], l[i - 2]
        else:
            # impulse completes at bar i; look for the opposite bar k in i-3..i-1 just before it
            for k in range(i - 1, i - 4, -1):
                if k < 11:
                    break
                hh = h[k - 10:k].max()
                ll = l[k - 10:k].min()
                imp_body = 0.0
                imp_vol_ok = vol_mult <= 0
                for m in range(k + 1, i + 1):
                    b = abs(c[m] - o[m])
                    if b > imp_body:
                        imp_body = b
                    if vol_mult > 0 and v[m] >= vol_mult * vavg[m]:
                        imp_vol_ok = True
                if imp_body < min_body * A or not imp_vol_ok:
                    continue
                if c[k] < o[k] and c[i] > hh and c[i] - h[k] >= 1.5 * A and (h[k] - l[k]) >= 0.1 * A:
                    side[i], ent[i], far[i] = 1, h[k], l[k]
                    break
                if c[k] > o[k] and c[i] < ll and l[k] - c[i] >= 1.5 * A and (h[k] - l[k]) >= 0.1 * A:
                    side[i], ent[i], far[i] = -1, l[k], h[k]
                    break
    return side, ent, far


@njit(cache=True)
def run_limit(o, h, l, c, a, v, vavg, side_z, ent_z, far_z, trend, valid, quiet, target_r, trail, cap, cost, through=0.0):
    """One position at a time. A zone becomes a resting limit order at its entry price for `valid` bars."""
    n = len(c)
    out_i = np.empty(n, np.int64)
    out_s = np.empty(n, np.int64)
    out_r = np.empty(n, np.float64)
    out_g = np.empty(n, np.float64)
    m = 0
    busy = -1
    for z in range(n):
        s = side_z[z]
        if s == 0 or z <= busy:
            continue
        if trend[z] != 0 and trend[z] != s:
            continue
        e = ent_z[z]
        A = a[z]
        stop = far_z[z] - s * 0.1 * A
        risk = s * (e - stop)
        if risk < 0.3 * A:
            stop = e - s * 0.3 * A
            risk = 0.3 * A
        fill = -1
        fpx = e
        for j in range(z + 1, min(n, z + 1 + valid)):
            if s * (c[j - 1] - far_z[z]) < 0 and j - 1 > z:      # closed beyond the far side: zone broken
                break
            touched = (l[j] <= e - through * A) if s > 0 else (h[j] >= e + through * A)    # through > 0: price must trade past the order
            if touched:
                if quiet and not (v[j] < vavg[j]):
                    break                                      # pullback not quiet: skip this zone
                fill = j
                fpx = min(o[j], e) if s > 0 else max(o[j], e)
                break
        if fill < 0:
            continue
        risk = s * (fpx - stop)
        if risk <= 0:
            continue
        tp = fpx + s * target_r * risk if target_r > 0 else np.nan
        sl = stop
        best = fpx
        exit_px = c[n - 1]
        k_exit = n - 1
        # fill bar: stop first (conservative), no target credit
        adv = l[fill] if s > 0 else h[fill]
        if s * (adv - sl) <= 0:
            exit_px, k_exit = sl, fill
        else:
            held = 0
            k = fill + 1
            done = False
            while k < n:
                held += 1
                adv = l[k] if s > 0 else h[k]
                fav = h[k] if s > 0 else l[k]
                if s * (o[k] - sl) <= 0:
                    exit_px, k_exit, done = o[k], k, True
                    break
                if s * (adv - sl) <= 0:
                    exit_px, k_exit, done = sl, k, True
                    break
                if np.isfinite(tp) and s * (fav - tp) >= 0:
                    exit_px, k_exit, done = tp, k, True
                    break
                if trail > 0:
                    best = max(best, fav) if s > 0 else min(best, fav)
                    cand = best - s * trail * A
                    if s * (cand - sl) > 0:
                        sl = cand
                if cap > 0 and held >= cap:
                    exit_px, k_exit, done = c[k], k, True
                    break
                k += 1
        g = s * (exit_px - fpx) / risk
        out_i[m] = fill
        out_s[m] = s
        out_g[m] = g
        out_r[m] = g - cost * fpx / risk
        m += 1
        busy = k_exit
    return out_i[:m], out_s[:m], out_r[:m], out_g[:m]


def main():
    t0 = time.time()
    res = {}
    grid = list(itertools.product(("fvg", "ob"), (0.0, 1.5, 2.0), (False, True), (0.5, 1.0), ("none", "ema200")))
    exits = [("2R", 2.0, 0.0), ("3R", 3.0, 0.0), ("trail 4 ATR", 0.0, 4.0)]
    for tf, coins in TFS.items():
        cap = 30 * PER_DAY[tf]
        for sym in coins:
            df = load(sym, tf)
            o, h, l, c, v = (df[k].values for k in ("open", "high", "low", "close", "volume"))
            a = E.atr(df)
            vavg = pd.Series(v).rolling(20, min_periods=5).mean().shift(1).values      # average of the 20 bars before
            e200 = E.ema(c, 200)
            tr_up = np.where(c > e200, 1, -1).astype(np.int64)
            no_tr = np.zeros(len(c), np.int64)
            ts = df.index
            valid_from = ts >= pd.Timestamp("2023-01-01", tz="UTC")
            hold = ts >= E.SPLIT
            sess = np.array([SS.label(t) for t in ts]) if tf != "4h" else None
            cache = {}
            for kind, vm, quiet, mb, flt in grid:
                kk = (kind, vm, mb)
                if kk not in cache:
                    sz, ez, fz = zones(o, h, l, c, v, a, np.nan_to_num(vavg, nan=np.inf), 0 if kind == "fvg" else 1, mb, vm)
                    sz = sz.copy(); sz[~valid_from] = 0
                    cache[kk] = (sz, ez, fz)
                sz, ez, fz = cache[kk]
                trend = tr_up if flt == "ema200" else no_tr
                for exn, trg, trl in exits:
                    for cost_name, cost in (("taker16", 0.0016), ("maker6", 0.0006)):
                        en, sd, R, g = run_limit(o, h, l, c, a, v, np.nan_to_num(vavg, nan=np.inf), sz, ez, fz, trend, VALID, quiet, trg, trl, cap, cost)
                        key = json.dumps({"tf": tf, "zone": kind, "vol": vm, "quiet_pullback": quiet, "body": mb, "flt": flt, "exit": exn, "cost": cost_name})
                        d = res.setdefault(key, {"R": [], "g": [], "h": [], "coin": [], "sess": []})
                        d["R"] += R.tolist(); d["g"] += g.tolist(); d["h"] += hold[en].tolist(); d["coin"] += [sym] * len(R)
                        d["sess"] += (sess[en].tolist() if sess is not None else ["-"] * len(R))
        print(tf, "done", round(time.time() - t0), "s", flush=True)
    rows = []
    for key, d in res.items():
        R, g, hd, coin, se = np.array(d["R"]), np.array(d["g"]), np.array(d["h"], bool), np.array(d["coin"]), np.array(d["sess"])
        per = {s: float(R[(coin == s) & hd].mean()) for s in np.unique(coin) if ((coin == s) & hd).sum()}
        by_s = {s: E.stats(R[se == s]) for s in np.unique(se) if s != "-"}
        rows.append({"v": json.loads(key), "design": E.stats(R[~hd]), "holdout": E.stats(R[hd]),
                     "gross_design": round(float(g[~hd].mean()), 3) if (~hd).sum() else None, "gross_holdout": round(float(g[hd].mean()), 3) if hd.sum() else None,
                     "coins_pos_holdout": sum(1 for x in per.values() if x > 0), "coins": len(per), "by_session": by_s})
    json.dump(rows, open(OUT, "w"))
    print("variants", len(rows), round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
