"""Round 53b (28 Oct 2026): does 'displacement' also mark the better trades of the crypto / gold autopilot rules?

Each candidate of fno.gautopilot (crypto Supertrend 4h / daily, Donchian 55 4h, Donchian 20 daily; gold Donchian 55 4h,
Donchian 20 daily) is run with the app's own engine (fno.custom.simulate, its costs) on its symbols. A trade "has
displacement" if one of the 3 bars up to and including the signal bar (the bar before entry) has a body >= 1 ATR(14) of
that timeframe in the trade's direction. Compare average R with vs without, first half vs second half of each series.
Data: research-data branch research/data/global (crypto 1h Jan 2023 - Oct 2026 resampled to 4h / 1d, UTC; gold 1h
Jun 2024 - Oct 2026 -> 4h, gold daily 2000 - 2026).
Run: python round53b_global.py -> round53b_results.json
"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
from fno import gautopilot as G, custom as CU

DATA = os.path.join(HERE, "data", "global")


def load(f):
    df = pd.read_csv(os.path.join(DATA, f), parse_dates=["ts"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float).sort_index()


def rs(df, tf):
    if tf == "1h":
        return df
    return df.resample({"4h": "4h", "1d": "1D"}[tf], label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def frame(market, sym, tf):
    if market == "CRYPTO":
        return rs(load(f"crypto_{sym}_1h.csv.gz"), tf)
    if tf == "1d":
        return load("gold_XAUUSD_1d.csv.gz")
    return rs(load("gold_XAUUSD_1h.csv.gz"), tf)


out, rows = {}, []
for market in ("CRYPTO", "GOLD"):
    for cand in G.CANDIDATES[market]:
        for sym in G.SYMBOLS[market]:
            spec = G._spec(market, cand, [sym])
            df = frame(market, sym, spec["timeframe"])
            tr = CU.simulate(spec, df)
            if not tr:
                continue
            o, c = df.open.values, df.close.values
            hl = pd.concat([df.high - df.low, (df.high - df.close.shift()).abs(), (df.low - df.close.shift()).abs()], axis=1).max(axis=1)
            atr = hl.ewm(alpha=1 / 14, adjust=False).mean().values
            half = df.index[len(df) // 2]
            for t in tr:
                j = t["entry_i"]; s = 1 if t["side"] == "long" else -1
                bars = range(max(0, j - 3), j)
                disp = any(s * (c[b] - o[b]) >= atr[b] for b in bars)
                rows.append({"market": market, "rule": cand["name"], "sym": sym, "R": t["R"], "disp": disp, "first_half": t["entry_ts"] < half})
R = pd.DataFrame(rows)
for (m, rule), g in R.groupby(["market", "rule"]):
    rec = {}
    for lab, x in (("all", g), ("first half", g[g.first_half]), ("second half", g[~g.first_half])):
        a, b = x[x.disp], x[~x.disp]
        rec[lab] = {"n_with": len(a), "R_with": round(float(a.R.mean()), 3) if len(a) else None,
                    "n_without": len(b), "R_without": round(float(b.R.mean()), 3) if len(b) else None}
    out[f"{m} | {rule}"] = rec
    print(f"{m:6s} {rule:36s} " + "  ".join(f"{k}: {v['R_with']}({v['n_with']}) vs {v['R_without']}({v['n_without']})" for k, v in rec.items()), flush=True)
for m, g in R.groupby("market"):
    a, b = g[g.disp], g[~g.disp]
    d = []
    rng = np.random.default_rng(1)
    for _ in range(4000):
        x, y = rng.choice(a.R.values, len(a)), rng.choice(b.R.values, len(b))
        d.append(x.mean() - y.mean())
    out[f"{m} pooled"] = {"R_with": round(float(a.R.mean()), 3), "n_with": len(a), "R_without": round(float(b.R.mean()), 3), "n_without": len(b),
                          "diff_ci90": [round(float(np.percentile(d, 5)), 3), round(float(np.percentile(d, 95)), 3)], "p_le_0": round(float((np.array(d) <= 0).mean()), 4)}
    print(m, "pooled", out[f"{m} pooled"])
json.dump(out, open(os.path.join(HERE, "round53b_results.json"), "w"), indent=1)
