"""Round 52b: are the crypto-1h FVG / breaker + trend + 3R winners an SMC effect, or just trend-following?
Control: for every trade, a twin in the SAME (trend) direction entered at the close of a random bar of the same UTC day
(where the trend filter agrees), with the same stop distance in ATR and the same 3R target. Costs as round 52."""
import sys, random, json
from pathlib import Path
import numpy as np
import round52_crypto_gold_smc as Q
DATA = Path(sys.argv[1])
rng = random.Random(7)
out = {}
for coin in Q.COINS1H:
    df = Q.load(DATA / f"crypto_{coin}_1h.csv.gz")
    days, offs = Q.split_days(df)
    O, H, L, C, A, E = (df[k].values for k in ("open", "high", "low", "close", "atr", "ema"))
    T = df.index; half = T[len(T) // 2]
    for setup in ("fvg", "breaker"):
        rows = []; busy = -1; per = {}
        sig = []
        for di, (d, off) in enumerate(zip(days, offs)):
            y = days[di - 1] if di else d
            cx = Q.Ctx(d, y)
            for (i, s, px, stop, hit) in Q.signals(setup, cx, None):
                sig.append((off + i, d.d, s, px, stop, hit, off, len(d.c)))
        for (g, dd, s, px, stop, hit, off, n) in sorted(sig):
            if g <= busy or per.get(dd, 0) >= 2 or s * (C[g] - E[g]) <= 0:
                continue
            risk = abs(px - stop)
            if risk < 0.3 * A[g] or risk > 4 * A[g]:
                continue
            k, ex = Q.walk(H, L, C, O, g, s, px, stop, px + s * 3 * risk, hit, 24)
            R = s * (ex - px) / risk - 0.0016 * px / risk
            cands = [j for j in range(off, off + n) if s * (C[j] - E[j]) > 0]
            j = rng.choice(cands)
            rk = risk / A[g] * A[j]
            k2, ex2 = Q.walk(H, L, C, O, j, s, C[j], C[j] - s * rk, C[j] + s * 3 * rk, False, 24)
            R2 = s * (ex2 - C[j]) / rk - 0.0016 * C[j] / rk
            busy = k; per[dd] = per.get(dd, 0) + 1
            rows.append((T[g] < half, R, R2))
        a = np.array(rows, float)
        out[f"{coin} {setup}"] = {"n": len(a), "netR": round(float(a[:, 1].mean()), 3), "trend_random_netR": round(float(a[:, 2].mean()), 3),
                                  "diff": round(float((a[:, 1] - a[:, 2]).mean()), 3), "se_diff": round(float((a[:, 1] - a[:, 2]).std() / np.sqrt(len(a))), 3)}
        print(coin, setup, out[f"{coin} {setup}"], flush=True)
d = np.array([[v["diff"], v["n"]] for v in out.values()])
print("pooled diff (trade-weighted):", round(float(np.average(d[:, 0], weights=d[:, 1])), 3))
json.dump(out, open(Path(__file__).resolve().parent / "round52b_results.json", "w"), indent=1)
