"""Round 60: every pattern on the user's chart-pattern cheat sheet, on every timeframe (28 Oct 2026).

User: "can we map these patterns on chart ... what is the logic, what should be the ideal time frame".
Detector: fno/patterns.py (the SAME code that draws the patterns on the app chart). 26 patterns: flags, pennants,
channels, range, megaphone, symmetrical / ascending / descending triangles, double / triple tops and bottoms,
head & shoulders (and inverse), diamond, cup & handle (and inverted), Livermore cylinder, rising / falling wedge,
Gartley and Cypher (bull and bear).
Timeframes: 5m and 15m (BTC ETH SOL BNB XRP, 2024-), 1h, 4h and 1d (10 coins + PAXG, 2023-).
Variants: zigzag 2 / 3 ATR; trend filter none / EMA 200; exit measured move / 2R / 3 ATR trailing stop.
Costs 16 bps round trip (market order on the break). Design < 1 Apr 2025 <= holdout.
Control: for each variant, the same trades (same side, same stop and target distance in ATR, same filter) placed on
10 sets of random bars of the same coin; "edge" = pattern avg R - random avg R. A pattern only counts if it beats
its own random control in BOTH periods.
Usage: python research/round60_chart_patterns_all.py <data_dir>
"""
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
OUT = os.path.join(HERE, "round60_results.json")
C11 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT", "PAXGUSDT"]
TFS = {"5m": C11[:5], "15m": C11[:5], "1h": C11, "4h": C11, "1d": C11}
CAP = {"5m": 288, "15m": 192, "1h": 120, "4h": 60, "1d": 30}
EXITS = (("mm", 0.0, 0.0), ("2R", 2.0, 0.0), ("trail 3 ATR", 0.0, 3.0))
REPS = 10


def load(sym, tf):
    src = "5m" if tf in ("5m", "15m") else "1h"
    df = pd.read_csv(f"{DATA}/crypto_{sym}_{src}.csv.gz", parse_dates=["ts"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    if src == "1h":
        df = df[df.index >= "2022-06-01"]
    rule = {"15m": "15min", "4h": "4h", "1d": "1D"}.get(tf)
    if rule:
        df = df.resample(rule, label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return df


def main():
    t0 = time.time()
    rng = np.random.default_rng(60)
    res = {}
    for tf, coins in TFS.items():
        for sym in coins:
            df = load(sym, tf)
            o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
            a = E.atr(df)
            e200 = E.ema(c, 200)
            n = len(c)
            z = np.zeros(n, np.bool_)
            nan = np.full(n, np.nan)
            valid = (df.index >= pd.Timestamp("2023-01-01", tz="UTC")) & np.isfinite(a) & (a > 0)
            valid[-2:] = False
            hold = df.index >= E.SPLIT
            years = df.index.year.values
            POOLS = {f: {(s_, hd_): np.flatnonzero(valid & (hold == hd_) & (True if f == "none" else ((c >= e200) if s_ == 1 else (c <= e200))))
                         for s_ in (1, -1) for hd_ in (False, True)} for f in ("none", "ema200")}
            for zz in (2.0, 3.0):
                sig = PT.signals(h, l, c, a, zz)
                for nm, (side, stop, tgt) in sig.items():
                    for flt in ("none", "ema200"):
                        sd = side.copy()
                        sd[~valid] = 0
                        if flt == "ema200":
                            sd[(sd == 1) & (c < e200)] = 0
                            sd[(sd == -1) & (c > e200)] = 0
                        idx = np.flatnonzero(sd)
                        # random control: same side and stop / target distance in ATR, on random bars allowed by the filter
                        ctl = []
                        if len(idx):
                            ds = np.abs(c[idx] - stop[idx]) / a[idx]
                            dt = np.abs(tgt[idx] - c[idx]) / a[idx]
                            ss = sd[idx]
                            hs_ = hold[idx]
                            for _ in range(REPS):
                                cs, cst, ctg = np.zeros(n, np.int64), np.full(n, np.nan), np.full(n, np.nan)
                                for (s_, hd_), pool in POOLS[flt].items():
                                    m = (ss == s_) & (hs_ == hd_)
                                    if not m.any() or not len(pool):
                                        continue
                                    js = pool[rng.integers(len(pool), size=int(m.sum()))]
                                    cs[js], cst[js], ctg[js] = s_, c[js] - s_ * ds[m] * a[js], c[js] + s_ * dt[m] * a[js]
                                ctl.append((cs, cst, ctg))
                        for exn, tr, trail in EXITS:
                            use_t = tgt if exn == "mm" else nan
                            en, _, R, b, g = E.run(o, h, l, c, a, sd, stop, use_t, z, z, tr, trail, 0.0, 0.0, CAP[tf], 0.5 if exn == "mm" else 0.0)
                            key = json.dumps({"pat": nm, "tf": tf, "zz": zz, "flt": flt, "exit": exn})
                            d = res.setdefault(key, {"R": [], "h": [], "coin": [], "yr": [], "bars": [], "cR": [], "ch": []})
                            d["R"] += R.tolist(); d["h"] += hold[en].tolist(); d["coin"] += [sym] * len(R); d["yr"] += years[en].tolist(); d["bars"] += b.tolist()
                            for cs, cst, ctg in ctl:
                                en2, _, R2, _, _ = E.run(o, h, l, c, a, cs, cst, ctg if exn == "mm" else nan, z, z, tr, trail, 0.0, 0.0, CAP[tf], 0.5 if exn == "mm" else 0.0)
                                d["cR"] += R2.tolist(); d["ch"] += hold[en2].tolist()
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
                     "coins_pos_holdout": sum(1 for x in per.values() if x > 0), "coins": len(per),
                     "avg_bars": round(float(np.mean(d["bars"])), 1) if d["bars"] else None})
    json.dump(rows, open(OUT, "w"))
    print("variants", len(rows), round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
