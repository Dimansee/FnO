"""Round 57b: do support/resistance and pivot setups work inside ONE session? (28 Oct 2026)

Round 55 found every level setup flat before costs over the whole day. Here the same setups only take signals from bars
that start in one session (Asia, London, New York, London+NY overlap, late US), on 15m (BTC ETH SOL BNB XRP, 2024-) and
1h (10 coins + PAXG, 2023-). Same engine and costs (16 bps). Design < 1 Apr 2025 <= holdout.
Usage: python research/round57_levels_sessions.py <data_dir>
"""
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import round55_engine as E  # noqa: E402
from fno import sessions as SS  # noqa: E402

DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data/global"
OUT = os.path.join(HERE, "round57_levels_sessions_results.json")
C11 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT", "PAXGUSDT"]
TFS = {"15m": C11[:5], "1h": C11}
CAP = {"15m": 96, "1h": 24}
LOOK = {"15m": 400, "1h": 500}
SESS = ["asia", "london", "newyork", "overlap", "late"]


def load(sym, tf):
    src = "5m" if tf == "15m" else "1h"
    df = pd.read_csv(f"{DATA}/crypto_{sym}_{src}.csv.gz", parse_dates=["ts"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    if src == "1h":
        df = df[df.index >= "2023-01-01"]
    else:
        df = df.resample("15min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return df


def fams(df, o, h, l, c, a, tf):
    yield "swing-bounce k5 m3", E.swing_levels(o, h, l, c, a, 5, LOOK[tf], 0.3, 3, 0, True, 0.3, 0)
    yield "swing-break k5 m3", E.swing_levels(o, h, l, c, a, 5, LOOK[tf], 0.3, 3, 1, False, 1.0, 0)
    for p, kind, nm in (("D", "floor", "daily pivots"), ("W", "floor", "weekly pivots"), ("D", "cam", "camarilla")):
        lv, mask = E.period_levels(df, p, kind)
        for mode in (0, 1):
            L, M = lv, mask
            if kind == "cam":
                keep = [1, 2] if mode == 0 else [0, 3]
                L, M = lv[:, keep], mask[keep]
            yield f"{nm} {'bounce' if mode == 0 else 'break'}", E.fixed_levels(o, h, l, c, a, np.ascontiguousarray(L), M, mode, True, 0.3 if mode == 0 else 1.0)
    lv, mask = E.round_levels(c)
    for mode in (0, 1):
        yield f"round numbers {'bounce' if mode == 0 else 'break'}", E.fixed_levels(o, h, l, c, a, lv, mask, mode, True, 0.3 if mode == 0 else 1.0)


def main():
    res = {}
    for tf, coins in TFS.items():
        for sym in coins:
            df = load(sym, tf)
            o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
            a = E.atr(df)
            e200 = E.ema(c, 200)
            n = len(c)
            z = np.zeros(n, np.bool_)
            nanv = np.full(n, np.nan)
            hold = df.index >= E.SPLIT
            masks = {s: SS.mask(df.index, s) for s in SESS}
            for fam, (side, stop, _) in fams(df, o, h, l, c, a, tf):
                for flt in ("none", "ema200"):
                    sd0 = side.copy()
                    if flt == "ema200":
                        sd0[(sd0 == 1) & (c < e200)] = 0
                        sd0[(sd0 == -1) & (c > e200)] = 0
                    for s in SESS:
                        sd = sd0.copy()
                        sd[~masks[s]] = 0
                        for tr in (1.5, 2.0):
                            en, s2, R, b, g = E.run(o, h, l, c, a, sd, stop, nanv, z, z, tr, 0.0, 0.0, 0.0, CAP[tf], 0.0)
                            key = json.dumps({"fam": fam, "tf": tf, "flt": flt, "session": s, "tgt": tr})
                            d = res.setdefault(key, {"R": [], "g": [], "h": [], "coin": []})
                            d["R"] += R.tolist(); d["g"] += g.tolist(); d["h"] += hold[en].tolist(); d["coin"] += [sym] * len(R)
        print(tf, "done", flush=True)
    rows = []
    for key, d in res.items():
        R, g, hd, coin = np.array(d["R"]), np.array(d["g"]), np.array(d["h"], bool), np.array(d["coin"])
        per = {s: float(R[(coin == s) & hd].mean()) for s in np.unique(coin) if ((coin == s) & hd).sum()}
        rows.append({"v": json.loads(key), "design": E.stats(R[~hd]), "holdout": E.stats(R[hd]),
                     "gross": round(float(g.mean()), 3) if len(g) else None, "coins_pos_holdout": sum(1 for x in per.values() if x > 0), "coins": len(per)})
    json.dump(rows, open(OUT, "w"))
    print("variants", len(rows))


if __name__ == "__main__":
    main()
