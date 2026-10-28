"""Round 55: support / resistance LEVELS on crypto (28 Oct 2026).

User question: pivots, levels, "price reverses from the same point again and again" - can these raise the profit ratio of
the crypto auto-trader, with better stop-loss and target?

Families (signal on the closed bar, entry next open, one position at a time per coin):
  swing-bounce  multi-touch level: prices of confirmed swing highs/lows (fractal k) inside a lookback, clustered within
                0.3 ATR; strength = how many swings sit on it (2 / 3 / 4+). Price comes down to support (or up to
                resistance), touches it and closes back on the same side; optional rejection candle. Stop beyond the level.
  swing-break   close through a multi-touch level (>= 0.1 ATR). Stop on the other side of the level.
  dpiv/wpiv     daily / weekly floor pivots (P, R1-2, S1-2 from the previous UTC day/week): bounce and breakout.
  cam           daily Camarilla H3/L3/H4/L4 on 15m/1h/4h (5m already failed in crypto_user_strategies).
  round         round numbers (BTC 1,000s, ETH 100s, SOL 10s, XRP 0.1s): bounce and breakout.
Targets: 1R, 1.5R, 2R, 3R, and (swing only) the next strong level. Time cap 1 day (15m/1h) or 5 days (4h).
Trend filter: none, or EMA 200 (longs only above it, shorts only below it).
Timeframes: 15m (BTC ETH SOL BNB XRP, 2024-), 1h and 4h (10 coins, 2023-). Costs 16 bps round trip.
Design < 2025-04-01 <= holdout.  Usage: python research/round55_levels.py <data_dir>
"""
import itertools
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import round55_engine as E  # noqa: E402

DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data/global"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "round55_levels_results.json")
C10 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT"]
C5 = C10[:5]
TFS = {"15m": C5, "1h": C10, "4h": C10}
CAP = {"15m": 96, "1h": 24, "4h": 30}
LOOK = {"15m": 400, "1h": 500, "4h": 300}
TARGETS = [("1R", 1.0), ("1.5R", 1.5), ("2R", 2.0), ("3R", 3.0), ("lvl", None)]


def families():
    f = []
    for k, m, rej in itertools.product((3, 5), (2, 3, 4), (False, True)):
        f.append({"fam": "swing-bounce", "k": k, "m": m, "rej": rej})
    for k, m in itertools.product((3, 5), (2, 3, 4)):
        f.append({"fam": "swing-break", "k": k, "m": m})
    for p, kind in (("D", "floor"), ("W", "floor"), ("D", "cam")):
        for mode in (0, 1):
            f.append({"fam": f"{'d' if p == 'D' else 'w'}{'piv' if kind == 'floor' else 'cam'}-{'bounce' if mode == 0 else 'break'}",
                      "p": p, "kind": kind, "mode": mode})
    for mode in (0, 1):
        f.append({"fam": f"round-{'bounce' if mode == 0 else 'break'}", "mode": mode})
    return f


def main():
    t0 = time.time()
    fams = families()
    res = {}
    for tf, coins in TFS.items():
        for sym in coins:
            df = E.load(DATA, sym, tf)
            o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
            a = E.atr(df)
            e200 = E.ema(c, 200)
            n = len(c)
            nanv = np.full(n, np.nan)
            z = np.zeros(n, np.bool_)
            hold = df.index >= E.SPLIT
            for F in fams:
                fam = F["fam"]
                if fam.startswith("swing"):
                    mode = 0 if fam == "swing-bounce" else 1
                    side, stop, tgt = E.swing_levels(o, h, l, c, a, F["k"], LOOK[tf], 0.3, F["m"], mode,
                                                     F.get("rej", False), 0.3 if mode == 0 else 1.0, 1)
                elif fam.startswith("round"):
                    lv, mask = E.round_levels(c)
                    side, stop, tgt = E.fixed_levels(o, h, l, c, a, lv, mask, F["mode"], True, 0.3 if F["mode"] == 0 else 1.0)
                else:
                    lv, mask = E.period_levels(df, F["p"], F["kind"])
                    if F["kind"] == "cam":       # textbook: fade H3/L3, break H4/L4
                        keep = [1, 2] if F["mode"] == 0 else [0, 3]
                        lv, mask = lv[:, keep], mask[keep]
                    side, stop, tgt = E.fixed_levels(o, h, l, c, a, np.ascontiguousarray(lv), mask, F["mode"], True,
                                                     0.3 if F["mode"] == 0 else 1.0)
                for flt in ("none", "ema200"):
                    sd = side.copy()
                    if flt == "ema200":
                        sd[(sd == 1) & (c < e200)] = 0
                        sd[(sd == -1) & (c > e200)] = 0
                    for tname, tr in TARGETS:
                        if tname == "lvl" and not fam.startswith("swing"):
                            continue
                        tg = tgt if tname == "lvl" else nanv
                        en, s2, R, b, g = E.run(o, h, l, c, a, sd, stop, tg, z, z, tr or 0.0, 0.0, 0.0, 0.0, CAP[tf],
                                                1.0 if tname == "lvl" else 0.0)
                        key = json.dumps({**F, "tf": tf, "flt": flt, "tgt": tname}, sort_keys=True)
                        d = res.setdefault(key, {"R": [], "g": [], "h": [], "coin": []})
                        d["R"] += R.tolist()
                        d["g"] += g.tolist()
                        d["h"] += hold[en].tolist()
                        d["coin"] += [sym] * len(R)
        print(tf, "done", round(time.time() - t0), "s", flush=True)

    rows = []
    for key, d in res.items():
        R, g, hd, coin = np.array(d["R"]), np.array(d["g"]), np.array(d["h"], bool), np.array(d["coin"])
        des, hol = E.stats(R[~hd]), E.stats(R[hd])
        per = {}
        for s in np.unique(coin):
            mk = (coin == s) & hd
            if mk.sum():
                per[s] = round(float(R[mk].mean()), 3)
        rows.append({"v": json.loads(key), "design": des, "holdout": hol, "gross_all": round(float(g.mean()), 3) if len(g) else None,
                     "coins_pos_holdout": sum(1 for x in per.values() if x > 0), "coins": len(per), "per_coin_holdout": per})
    json.dump(rows, open(OUT, "w"), indent=0)
    print("variants", len(rows), "saved", OUT, round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
