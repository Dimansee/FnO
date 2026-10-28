"""Round 55c: random-entry control for the level variants that looked best (28 Oct 2026).
For each coin: take the real signals of the variant, then place the same number of signals on random bars that pass the
same trend filter, same long/short mix, with (stop distance in ATR, target distance in R) pairs drawn from the real
signals. 30 seeds. If the level adds information, the real variant must beat the random ones.
Usage: python research/round55_control.py <data_dir>"""
import json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import round55_engine as E
DATA = sys.argv[1]
C10 = ["BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","ADAUSDT","AVAXUSDT","LINKUSDT","TONUSDT"]
VARS = [("swing-break k5 m3, EMA200, next-level target", 1, 5, 3, False, 1.0, None),
        ("swing-bounce k5 m3 rejection, EMA200, 1R", 0, 5, 3, True, 0.3, 1.0),
        ("swing-bounce k5 m3 rejection, EMA200, 2R", 0, 5, 3, True, 0.3, 2.0)]
out = {}
for name, mode, k, m, rej, buf, tr in VARS:
    real = {"d": [], "h": []}; rnd = {"d": [[] for _ in range(30)], "h": [[] for _ in range(30)]}
    for sym in C10:
        df = E.load(DATA, sym, "4h"); o, h, l, c = (df[x].values for x in ("open","high","low","close"))
        a = E.atr(df); e2 = E.ema(c, 200); n = len(c); z = np.zeros(n, np.bool_); nan = np.full(n, np.nan)
        side, stop, tgt = E.swing_levels(o, h, l, c, a, k, 300, 0.3, m, mode, rej, buf, 1)
        side[(side == 1) & (c < e2)] = 0; side[(side == -1) & (c > e2)] = 0
        hold = df.index >= E.SPLIT
        tg = tgt if tr is None else nan
        en, sd, R, b, g = E.run(o, h, l, c, a, side, stop, tg, z, z, tr or 0.0, 0.0, 0.0, 0.0, 30, 1.0 if tr is None else 0.0)
        real["d"] += R[~hold[en]].tolist(); real["h"] += R[hold[en]].tolist()
        idx = np.where(side != 0)[0]
        if not len(idx): continue
        sa = np.abs(c[idx] - stop[idx]) / a[idx]
        rr = np.abs(tgt[idx] - c[idx]) / np.abs(c[idx] - stop[idx]) if tr is None else np.full(len(idx), tr)
        fl = (c > e2) * 1 - (c < e2) * 1
        for sd_ in range(30):
            rg = np.random.default_rng(sd_)
            rs = np.zeros(n, np.int64); st2 = nan.copy(); tg2 = nan.copy()
            for i0 in idx:
                s0 = side[i0]
                cand = np.where(fl == s0)[0]; cand = cand[(cand > 250) & (cand < n - 2)]
                j = rg.choice(cand); p = rg.integers(len(idx))
                rs[j] = s0; st2[j] = c[j] - s0 * sa[p] * a[j]
                if tr is None: tg2[j] = c[j] + s0 * rr[p] * sa[p] * a[j]
            en2, _, R2, _, _ = E.run(o, h, l, c, a, rs, st2, tg2, z, z, tr or 0.0, 0.0, 0.0, 0.0, 30, 1.0 if tr is None else 0.0)
            rnd["d"][sd_] += R2[~hold[en2]].tolist(); rnd["h"][sd_] += R2[hold[en2]].tolist()
    rm = {p: [float(np.mean(x)) for x in rnd[p] if x] for p in ("d", "h")}
    out[name] = {"real_design": E.stats(real["d"]), "real_holdout": E.stats(real["h"]),
                 "random_design_avg": round(float(np.mean(rm["d"])), 3), "random_holdout_avg": round(float(np.mean(rm["h"])), 3),
                 "random_holdout_p_real_better": round(float(np.mean(np.array(rm["h"]) < np.mean(real["h"]))), 2),
                 "random_design_p_real_better": round(float(np.mean(np.array(rm["d"]) < np.mean(real["d"]))), 2)}
    print(name, json.dumps(out[name]))
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "round55_control_results.json"), "w"), indent=1)
