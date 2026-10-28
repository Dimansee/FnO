"""Round 57d: hold longer (28 Oct 2026). User: "we can hold more than 4 hr also, please test that".

Same entries as round 57 (chart patterns) and 57b (levels / pivots), but exits that let trades run:
  fixed target (measured move for patterns, 2R, 3R) with a time limit of 1 / 3 / 7 / 14 days or none,
  or a trailing stop 2 / 3 / 4 ATR (no target) with a 14-day limit or none.
Timeframes 5m, 15m, 1h, 4h for patterns; 15m, 1h for levels. Costs 16 bps. Design < 1 Apr 2025 <= holdout.
Usage: python research/round57d_hold.py <data_dir>
"""
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import round55_engine as E  # noqa: E402
import round57_patterns as P  # noqa: E402
import round57_levels_sessions as LS  # noqa: E402

DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data/global"
P.DATA = LS.DATA = DATA
OUT = os.path.join(HERE, "round57d_hold_results.json")
PER_DAY = {"5m": 288, "15m": 96, "1h": 24, "4h": 6}
SHORT = {"5m": "12h", "15m": "1d", "1h": "2d", "4h": "5d"}           # the limits used in rounds 55 / 57


def exits(tf):
    out = []
    caps = [("12h", PER_DAY[tf] // 2)] if tf in ("5m", "15m") else []
    caps += [("1d", PER_DAY[tf]), ("2d", 2 * PER_DAY[tf]), ("3d", 3 * PER_DAY[tf]), ("5d", 5 * PER_DAY[tf]), ("7d", 7 * PER_DAY[tf]),
             ("14d", 14 * PER_DAY[tf]), ("none", 0)]
    for tg in ("mm", "2R", "3R"):
        for cn, cb in caps:
            out.append((f"{tg} / {cn}", tg, 0.0, cb))
    for tr in (2.0, 3.0, 4.0):
        for cn, cb in (("14d", 14 * PER_DAY[tf]), ("none", 0)):
            out.append((f"trail {tr:g} ATR / {cn}", None, tr, cb))
    return out


def run_exit(o, h, l, c, a, sd, stop, mm, ex):
    name, tg, trail, cap = ex
    n = len(c)
    z = np.zeros(n, np.bool_)
    nan = np.full(n, np.nan)
    if tg == "mm":
        return E.run(o, h, l, c, a, sd, stop, mm, z, z, 0.0, trail, 0.0, 0.0, cap, 0.5)
    tr = {"2R": 2.0, "3R": 3.0, None: 0.0}[tg]
    return E.run(o, h, l, c, a, sd, stop, nan, z, z, tr, trail, 0.0, 0.0, cap, 0.0)


def add(res, key, R, g, hd, en, coin):
    d = res.setdefault(key, {"R": [], "g": [], "h": [], "coin": []})
    d["R"] += R.tolist(); d["g"] += g.tolist(); d["h"] += hd[en].tolist(); d["coin"] += [coin] * len(R)


def main():
    t0 = time.time()
    res = {}
    # chart patterns
    for tf, coins in P.TFS.items():
        exs = exits(tf)
        for sym in coins:
            df = P.load(sym, tf)
            o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
            a = E.atr(df); e200 = E.ema(c, 200); hd = df.index >= E.SPLIT
            for zz in (2.0, 3.0):
                for nm, (side, stop, mm) in P.signals_for(df, a, zz, P.PATTERNS).items():
                    for flt in ("none", "ema200"):
                        sd = side.copy()
                        if flt == "ema200":
                            sd[(sd == 1) & (c < e200)] = 0; sd[(sd == -1) & (c > e200)] = 0
                        for ex in exs:
                            en, _, R, b, g = run_exit(o, h, l, c, a, sd, stop, mm, ex)
                            add(res, json.dumps({"kind": "pattern", "setup": nm, "tf": tf, "zz": zz, "flt": flt, "exit": ex[0]}), R, g, hd, en, sym)
        print("patterns", tf, round(time.time() - t0), "s", flush=True)
    # levels / pivots
    for tf, coins in LS.TFS.items():
        exs = [e for e in exits(tf) if e[1] != "mm"]
        for sym in coins:
            df = LS.load(sym, tf)
            o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
            a = E.atr(df); e200 = E.ema(c, 200); hd = df.index >= E.SPLIT
            nan = np.full(len(c), np.nan)
            for fam, (side, stop, _) in LS.fams(df, o, h, l, c, a, tf):
                for flt in ("none", "ema200"):
                    sd = side.copy()
                    if flt == "ema200":
                        sd[(sd == 1) & (c < e200)] = 0; sd[(sd == -1) & (c > e200)] = 0
                    for ex in exs:
                        en, _, R, b, g = run_exit(o, h, l, c, a, sd, stop, nan, ex)
                        add(res, json.dumps({"kind": "level", "setup": fam, "tf": tf, "zz": None, "flt": flt, "exit": ex[0]}), R, g, hd, en, sym)
        print("levels", tf, round(time.time() - t0), "s", flush=True)
    rows = []
    for key, d in res.items():
        R, g, hd, coin = np.array(d["R"]), np.array(d["g"]), np.array(d["h"], bool), np.array(d["coin"])
        per = {s: float(R[(coin == s) & hd].mean()) for s in np.unique(coin) if ((coin == s) & hd).sum()}
        rows.append({"v": json.loads(key), "design": E.stats(R[~hd]), "holdout": E.stats(R[hd]),
                     "gross": round(float(g.mean()), 3) if len(g) else None, "coins_pos_holdout": sum(1 for x in per.values() if x > 0), "coins": len(per)})
    json.dump(rows, open(OUT, "w"))
    print("variants", len(rows), round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
