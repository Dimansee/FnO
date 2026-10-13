"""Quick test of mirrored DOWNSIDE setups on the Nifty 500 daily data (no shorting costs modelled - hit-rate statistics only)."""
import numpy as np, pandas as pd
d = pd.read_csv("data/stocks_1d.csv.gz", parse_dates=["date"]).sort_values(["sym", "date"])
f = (d["adj close"] / d["close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
for c in ("open", "high", "low", "close"): d[c] = d[c] * f
C = d.pivot(index="date", columns="sym", values="close"); O = d.pivot(index="date", columns="sym", values="open"); H = d.pivot(index="date", columns="sym", values="high"); L = d.pivot(index="date", columns="sym", values="low"); V = d.pivot(index="date", columns="sym", values="volume")
SMA200 = C.rolling(200, min_periods=200).mean(); R3 = C / C.shift(3) - 1; TURN = (C * V).rolling(50, min_periods=20).median(); LO252 = L.rolling(252, min_periods=252).min().shift(1); V50 = V.rolling(50, min_periods=50).mean()
split = pd.Timestamp("2025-07-01")
def stats(name, sig, tgt, risk, hold):
    rows = []
    idx = np.argwhere(sig.values)
    for i, j in idx:
        if i + 1 + hold >= len(C): continue
        s = C.columns[j]; e = O.iloc[i + 1, j]
        if pd.isna(e) or e <= 0: continue
        hit = None
        for k in range(i + 1, i + 1 + hold):
            lo, hi = L.iloc[k, j], H.iloc[k, j]
            if hi >= e * (1 + risk): hit = ("risk", k); break
            if lo <= e * (1 - tgt): hit = ("target", k); break
        ret = (e / O.iloc[i + 1 + hold, j] - 1) if hit is None else (tgt if hit[0] == "target" else -risk)
        rows.append({"date": C.index[i], "ret": ret - 0.002, "hit": hit[0] if hit else "time"})
    r = pd.DataFrame(rows)
    for lab, m in (("train", r.date < split), ("unseen", r.date >= split)):
        x = r[m]
        print(f"{name:55s} {lab:6s} n {len(x):5d} win {100*(x.ret>0).mean():4.0f}% target-hit {100*(x.hit=='target').mean():4.0f}% avg {100*x.ret.mean():+.2f}%")
liq = TURN >= 1e7
stats("UP-pop: 3-day rise >10% below 200-d avg -> fall 5% in 5d", (R3 > 0.10) & (C < SMA200) & liq, 0.05, 0.08, 5)
stats("UP-pop: 3-day rise >10% ABOVE 200-d avg -> fall 5% in 5d", (R3 > 0.10) & (C > SMA200) & liq, 0.05, 0.08, 5)
stats("UP-pop 7-10% below 200-d avg -> fall 5%", (R3 > 0.07) & (R3 <= 0.10) & (C < SMA200) & liq, 0.05, 0.08, 5)
stats("Breakdown: new 52-w low on 1.5x vol below 200-d -> fall 10% in 60d", (C < LO252) & (V >= 1.5 * V50) & (C < SMA200) & liq, 0.10, 0.10, 60)
stats("Breakdown: new 52-w low on 1.5x vol -> fall 15% in 60d (trail n/a)", (C < LO252) & (V >= 1.5 * V50) & (C < SMA200) & liq, 0.15, 0.10, 60)
stats("Below 200-d avg and 3-day rise >5% -> fall 5% in 5d", (R3 > 0.05) & (C < SMA200) & liq, 0.05, 0.08, 5)
# control: random days
rng = np.random.default_rng(1); rand = pd.DataFrame(rng.random(C.shape) < 0.003, index=C.index, columns=C.columns) & liq
stats("CONTROL: random stock-days -> fall 5% in 5d", rand, 0.05, 0.08, 5)
