"""Round 59b: the round-59 OB / FVG / volume grid on 1h and 4h with realistic limit-order costs and fills.
Costs: 16 bps (taker, as the app) and 10 bps (Binance USD-M futures: 2 bps maker entry + 5 bps taker exit + 3 bps exit
slippage). Fills: 'touch' (price reaches the order) or 'through' (price trades 0.1 ATR past it - queue-safe).
Usage: python research/round59b_realistic.py <data_dir>"""
import itertools, json, os, sys, time
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import round55_engine as E
import round59_ob_fvg_volume as Z
Z.DATA = sys.argv[1]
OUT = os.path.join(HERE, "round59b_results.json")
t0 = time.time(); res = {}
grid = list(itertools.product(("fvg", "ob"), (0.0, 1.5, 2.0), (False, True), (0.5, 1.0), ("none", "ema200")))
exits = [("2R", 2.0, 0.0), ("3R", 3.0, 0.0), ("trail 4 ATR", 0.0, 4.0)]
for tf in ("1h", "4h"):
    cap = 30 * Z.PER_DAY[tf]
    for sym in Z.TFS[tf]:
        df = Z.load(sym, tf)
        o, h, l, c, v = (df[k].values for k in ("open", "high", "low", "close", "volume"))
        a = E.atr(df); vavg = np.nan_to_num(pd.Series(v).rolling(20, min_periods=5).mean().shift(1).values, nan=np.inf)
        e200 = E.ema(c, 200); tr_up = np.where(c > e200, 1, -1).astype(np.int64); no_tr = np.zeros(len(c), np.int64)
        ts = df.index; vf = ts >= pd.Timestamp("2023-01-01", tz="UTC"); hold = ts >= E.SPLIT
        cache = {}
        for kind, vm, quiet, mb, flt in grid:
            kk = (kind, vm, mb)
            if kk not in cache:
                sz, ez, fz = Z.zones(o, h, l, c, v, a, vavg, 0 if kind == "fvg" else 1, mb, vm); sz = sz.copy(); sz[~vf] = 0; cache[kk] = (sz, ez, fz)
            sz, ez, fz = cache[kk]
            for exn, trg, trl in exits:
                for fill, thr in (("touch", 0.0), ("through", 0.1)):
                    for cn, cost in (("taker16", 0.0016), ("limit10", 0.0010)):
                        en, sd, R, g = Z.run_limit(o, h, l, c, a, v, vavg, sz, ez, fz, tr_up if flt == "ema200" else no_tr, Z.VALID, quiet, trg, trl, cap, cost, thr)
                        key = json.dumps({"tf": tf, "zone": kind, "vol": vm, "quiet_pullback": quiet, "body": mb, "flt": flt, "exit": exn, "fill": fill, "cost": cn})
                        dd = res.setdefault(key, {"R": [], "g": [], "h": [], "coin": [], "ts": []})
                        dd["R"] += R.tolist(); dd["g"] += g.tolist(); dd["h"] += hold[en].tolist(); dd["coin"] += [sym] * len(R); dd["ts"] += [str(ts[i].year) for i in en]
    print(tf, "done", round(time.time() - t0), "s", flush=True)
rows = []
for key, d in res.items():
    R, g, hd, coin, yr = np.array(d["R"]), np.array(d["g"]), np.array(d["h"], bool), np.array(d["coin"]), np.array(d["ts"])
    per = {s: float(R[(coin == s) & hd].mean()) for s in np.unique(coin) if ((coin == s) & hd).sum()}
    rows.append({"v": json.loads(key), "design": E.stats(R[~hd]), "holdout": E.stats(R[hd]), "gross_holdout": round(float(g[hd].mean()), 3) if hd.sum() else None,
                 "years": {y: round(float(R[yr == y].mean()), 3) for y in sorted(set(yr))}, "coins_pos_holdout": sum(1 for x in per.values() if x > 0), "coins": len(per)})
json.dump(rows, open(OUT, "w")); print("variants", len(rows), round(time.time() - t0), "s")
