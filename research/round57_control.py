"""Round 57c: random-entry control, years, overlap with the live trend rules, for the best chart-pattern variants.
Random entries: same coin, same number of signals, same long/short mix, same trend state (EMA 200 filter when the
variant uses it), stop distance (in ATR) and target distance (in R) drawn from the real signals; 30 seeds.
Usage: python research/round57_control.py <data_dir>"""
import json, os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import round55_engine as E  # noqa: E402
import round57_patterns as P  # noqa: E402

DATA = sys.argv[1]
P.DATA = DATA
VARS = [("bull_flag", "4h", 2.0, "ema200", "mm"), ("bull_flag", "4h", 2.0, "ema200", "2R"), ("asc_tri", "4h", 2.0, "ema200", "2R"),
        ("asc_tri", "4h", 2.0, "none", "2R"), ("double_bottom", "4h", 3.0, "ema200", "2R"), ("ihs", "1h", 3.0, "none", "2R"),
        ("desc_tri", "4h", 2.0, "ema200", "1R")]
SEEDS = 30


def live_positions(o, h, l, c, a):
    """Bars where Supertrend 4h or Donchian 55 4h (live rules) hold a position, by side."""
    n = len(c)
    z = np.zeros(n, np.bool_); nan = np.full(n, np.nan)
    d = E.supertrend_dir(h, l, c, a, 3.0)
    fl = np.r_[False, (d[1:] == 1) & (d[:-1] == -1)]; fs = np.r_[False, (d[1:] == -1) & (d[:-1] == 1)]
    sg = np.zeros(n, np.int64); sg[fl] = 1; sg[fs] = -1
    e1, s1, _, b1, _ = E.run(o, h, l, c, a, sg, nan, nan, fl, fs, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 2.0)
    up = pd.Series(h).rolling(55).max().shift(1).values
    sg2 = np.zeros(n, np.int64); sg2[(c > up) & np.r_[False, c[:-1] <= up[:-1]]] = 1
    e2, s2, _, b2, _ = E.run(o, h, l, c, a, sg2, nan, nan, z, z, 0.0, 4.0, 0.0, 0.0, 0, 0.0, 2.0)
    pos = np.zeros(n, np.int64)
    for e, s, b in list(zip(e1, s1, b1)) + list(zip(e2, s2, b2)):
        seg = pos[e:e + b]
        pos[e:e + b] = np.where(seg == 0, s, seg)
    return pos


out = {}
for pat, tf, zz, flt, tg in VARS:
    real = {"d": [], "h": []}; rnd = {"d": [[] for _ in range(SEEDS)], "h": [[] for _ in range(SEEDS)]}
    years, overlap, tot = {}, 0, 0
    for sym in P.TFS[tf]:
        df = P.load(sym, tf); o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
        a = E.atr(df); e2 = E.ema(c, 200); n = len(c); z = np.zeros(n, np.bool_); nan = np.full(n, np.nan)
        side, stop, mm = P.signals_for(df, a, zz, [pat])[pat]
        if flt == "ema200":
            side[(side == 1) & (c < e2)] = 0; side[(side == -1) & (c > e2)] = 0
        hold = df.index >= E.SPLIT
        tgt = mm if tg == "mm" else nan
        tr = {"mm": 0.0, "1R": 1.0, "2R": 2.0}[tg]
        en, sd, R, b, g = E.run(o, h, l, c, a, side, stop, tgt, z, z, tr, 0.0, 0.0, 0.0, P.CAP[tf], 0.5 if tg == "mm" else 0.0)
        real["d"] += R[~hold[en]].tolist(); real["h"] += R[hold[en]].tolist()
        for t_, r_ in zip(df.index[en], R):
            years.setdefault(t_.year, []).append(r_)
        if tf == "4h":
            pos = live_positions(o, h, l, c, a)
            for e, s in zip(en, sd):
                tot += 1; overlap += int(pos[e - 1] == s)
        idx = np.where(side != 0)[0]
        if not len(idx):
            continue
        sa = np.maximum(np.abs(c[idx] - stop[idx]) / a[idx], 0.5)
        rr = np.abs(mm[idx] - c[idx]) / np.abs(c[idx] - stop[idx]) if tg == "mm" else np.full(len(idx), tr)
        fl = (c > e2) * 1 - (c < e2) * 1
        for k in range(SEEDS):
            rg = np.random.default_rng(k)
            rs = np.zeros(n, np.int64); st2 = nan.copy(); tg2 = nan.copy()
            for i0 in idx:
                s0 = side[i0]
                cand = np.where(fl == s0)[0] if flt == "ema200" else np.arange(n)
                cand = cand[(cand > 250) & (cand < n - 2)]
                j = rg.choice(cand); p = rg.integers(len(idx))
                rs[j] = s0; st2[j] = c[j] - s0 * sa[p] * a[j]
                if tg == "mm":
                    tg2[j] = c[j] + s0 * rr[p] * sa[p] * a[j]
            e_, _, R2, _, _ = E.run(o, h, l, c, a, rs, st2, tg2, z, z, tr, 0.0, 0.0, 0.0, P.CAP[tf], 0.5 if tg == "mm" else 0.0)
            rnd["d"][k] += R2[~hold[e_]].tolist(); rnd["h"][k] += R2[hold[e_]].tolist()
    rm = {p: np.array([np.mean(x) for x in rnd[p] if x]) for p in ("d", "h")}
    name = f"{pat} {tf} zz{zz:g} {flt} {tg}"
    out[name] = {"real_design": E.stats(real["d"]), "real_holdout": E.stats(real["h"]),
                 "random_design": round(float(rm["d"].mean()), 3), "random_holdout": round(float(rm["h"].mean()), 3),
                 "beats_random_design": round(float((rm["d"] < np.mean(real["d"])).mean()), 2),
                 "beats_random_holdout": round(float((rm["h"] < np.mean(real["h"])).mean()), 2),
                 "years": {int(y): round(float(np.mean(v)), 3) for y, v in sorted(years.items())},
                 "share_inside_live_trade": round(overlap / tot * 100, 0) if tot else None}
    r = out[name]
    print(f"{name:<38} real D {r['real_design']['avg']:+.3f} H {r['real_holdout']['avg']:+.3f} | random D {r['random_design']:+.3f} H {r['random_holdout']:+.3f} | "
          f"beats D {r['beats_random_design']} H {r['beats_random_holdout']} | years {r['years']} | inside live trade {r['share_inside_live_trade']}%", flush=True)
json.dump(out, open(os.path.join(HERE, "round57_control_results.json"), "w"), indent=1)
