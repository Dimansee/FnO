"""Round 55b: stop-loss and target study on the LIVE crypto trend rules (28 Oct 2026).

Rules (as in fno/gautopilot.py): Supertrend 10/3 4h and daily (both directions, exit on the opposite flip),
Donchian 55 4h long (trail 4 ATR), Donchian 20 daily long (trail 3 ATR). All stop 2 ATR today.
Grid: initial stop 1.5/2/2.5/3/4 ATR x trailing 0/2/3/4/5/6 ATR x fixed target none/2R/3R/5R x break-even at
0/1R/2R x 50% partial at 0/1R/2R. Same engine as round55_levels (app exit order, 16 bps).
Choose on the design period only (before 1 Apr 2025), judge on the holdout. Risk per trade is fixed, so the score is
total R (avg R x trades); drawdown is the worst peak-to-trough of the time-ordered pooled R curve.
Usage: python research/round55_exits.py <data_dir>
"""
import itertools
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import round55_engine as E  # noqa: E402

DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data/global"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "round55_exits_results.json")
C10 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT"]
RULES = {"st4": ("4h", 0.0), "st1d": ("1d", 0.0), "dc55": ("4h", 4.0), "dc20": ("1d", 3.0)}
LIVE = {"st4": (2.0, 0.0, 0.0, 0.0, 0.0), "st1d": (2.0, 0.0, 0.0, 0.0, 0.0), "dc55": (2.0, 4.0, 0.0, 0.0, 0.0), "dc20": (2.0, 3.0, 0.0, 0.0, 0.0)}
GRID = list(itertools.product((1.5, 2.0, 2.5, 3.0, 4.0), (0.0, 2.0, 3.0, 4.0, 5.0, 6.0), (0.0, 2.0, 3.0, 5.0), (0.0, 1.0, 2.0), (0.0, 1.0, 2.0)))


def dd(R):
    if not len(R):
        return 0.0
    eq = np.cumsum(R)
    return round(float((eq - np.maximum.accumulate(np.r_[0, eq])[1:]).min()), 1)


def prep(rule, sym):
    tf = RULES[rule][0]
    df = E.load(DATA, sym, tf)
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    a = E.atr(df)
    n = len(c)
    side = np.zeros(n, np.int64)
    ol = np.zeros(n, np.bool_)
    osr = np.zeros(n, np.bool_)
    if rule.startswith("st"):
        d = E.supertrend_dir(h, l, c, a, 3.0)
        fl = np.r_[False, (d[1:] == 1) & (d[:-1] == -1)]
        fs = np.r_[False, (d[1:] == -1) & (d[:-1] == 1)]
        side[fl], side[fs] = 1, -1
        ol, osr = fl, fs
    else:
        N = 55 if rule == "dc55" else 20
        up = pd.Series(h).rolling(N).max().shift(1).values
        side[(c > up) & np.r_[False, c[:-1] <= up[:-1]]] = 1
    return df, o, h, l, c, a, side, ol, osr


def main():
    out = {}
    for rule in RULES:
        P = {s: prep(rule, s) for s in C10}
        rows = []
        for st, tr, tg, be, pt in GRID:
            Rs, ts = [], []
            for s, (df, o, h, l, c, a, side, ol, osr) in P.items():
                n = len(c)
                nanv = np.full(n, np.nan)       # stop = st x ATR from the entry price, as in the app
                en, sd, R, b, g = E.run(o, h, l, c, a, side, nanv, nanv, ol, osr, tg, tr, be, pt, 0, 0.0, st)
                Rs.append(R)
                ts.append(df.index[en].values)
            R = np.concatenate(Rs)
            T = np.concatenate(ts)
            o_ = np.argsort(T)
            R, T = R[o_], T[o_]
            hd = T >= np.datetime64(E.SPLIT.tz_convert(None))
            rows.append({"stop": st, "trail": tr, "tgt": tg, "be": be, "part": pt,
                         "design": {**E.stats(R[~hd]), "dd": dd(R[~hd])}, "holdout": {**E.stats(R[hd]), "dd": dd(R[hd])}})
        live = next(r for r in rows if (r["stop"], r["trail"], r["tgt"], r["be"], r["part"]) == LIVE[rule])
        best = sorted(rows, key=lambda r: -r["design"]["tot"])[:15]
        out[rule] = {"live": live, "top_design": best, "all": rows}
        print(rule, "LIVE", live["design"], live["holdout"])
        for r in best[:5]:
            print("   ", r["stop"], r["trail"], r["tgt"], r["be"], r["part"], "D", r["design"]["tot"], r["design"]["avg"], "H", r["holdout"]["tot"], r["holdout"]["avg"], r["holdout"]["dd"])
    json.dump(out, open(OUT, "w"))


if __name__ == "__main__":
    main()
