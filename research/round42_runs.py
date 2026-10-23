"""Round 42, part D: "after a 250-400 point rally (or fall) in 2-3 days the market must come back". Daily closes, Nifty and
Bank Nifty since Sep 2007, Sensex since 1997 (Yahoo, research/data/idx_daily_*.csv.gz). A "big run" = the 3-day close-to-close
move is at least 1.5 x the 14-day ATR (about 250-400 Nifty points at today's levels), also checked at 1.0 / 2.0 ATR and as raw
Nifty points since 2021. For each run: next-day and next-3-day move in the run's direction (negative = it came back), and how
often half the run is retraced within 5 days, against the same numbers for all days (the base rate)."""
import os, json, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
out = {}
for s in ("NIFTY", "BANKNIFTY", "SENSEX"):
    d = pd.read_csv(os.path.join(HERE, "data", f"idx_daily_{s}.csv.gz"), parse_dates=["ts"]).set_index("ts")
    tr = pd.concat([d.high - d.low, (d.high - d.close.shift()).abs(), (d.low - d.close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    m3 = d.close - d.close.shift(3)
    f1, f3 = d.close.shift(-1) - d.close, d.close.shift(-3) - d.close
    # half retrace within 5 days: min (for up runs) / max (down runs) of the next 5 closes vs close - m3/2
    nxt_min = pd.concat([d.low.shift(-i) for i in range(1, 6)], axis=1).min(axis=1)
    nxt_max = pd.concat([d.high.shift(-i) for i in range(1, 6)], axis=1).max(axis=1)
    half_up = nxt_min <= d.close - m3 / 2
    half_dn = nxt_max >= d.close - m3 / 2
    df = pd.DataFrame({"m3": m3, "atr": atr, "f1": f1, "f3": f3, "half": np.where(m3 > 0, half_up, half_dn)}).dropna()
    df["dirn"] = np.sign(df.m3)
    df["f1d"], df["f3d"] = df.f1 * df.dirn / df.atr, df.f3 * df.dirn / df.atr          # in ATRs, + = continued, - = came back
    res = {}
    for era, g in (("all", df), ("to 2014", df[df.index < "2015"]), ("2015-2022", df[(df.index >= "2015") & (df.index < "2023")]), ("2023-26", df[df.index >= "2023"])):
        row = {"base": {"n": int(len(g)), "half_retrace_5d_pct": round(float(g.half.mean() * 100), 1), "next1_atr": round(float(g.f1d.mean()), 3), "next3_atr": round(float(g.f3d.mean()), 3)}}
        for k in (1.0, 1.5, 2.0):
            for side, q in (("up", g[(g.m3 >= k * g.atr)]), ("down", g[(g.m3 <= -k * g.atr)])):
                if len(q) < 10:
                    continue
                t3 = q.f3d.mean() / (q.f3d.std(ddof=1) / np.sqrt(len(q)))
                row[f"{side} run >= {k} ATR"] = {"n": int(len(q)), "half_retrace_5d_pct": round(float(q.half.mean() * 100), 1),
                                                 "next1_atr": round(float(q.f1d.mean()), 3), "next3_atr": round(float(q.f3d.mean()), 3),
                                                 "t_next3": round(float(t3), 2), "came_back_next3_pct": round(float((q.f3d < 0).mean() * 100), 1)}
        res[era] = row
    if s == "NIFTY":
        g = df[df.index >= "2021"].copy()
        for pts in (250, 400):
            for side, q in (("up", g[g.m3 >= pts]), ("down", g[g.m3 <= -pts])):
                res.setdefault("points since 2021", {})[f"{side} >= {pts} pts"] = {"n": int(len(q)), "half_retrace_5d_pct": round(float(q.half.mean() * 100), 1),
                    "next3_pts_in_run_dir": round(float((q.f3 * np.sign(q.m3)).mean()), 1), "came_back_next3_pct": round(float(((q.f3 * np.sign(q.m3)) < 0).mean() * 100), 1)}
    out[s] = res
json.dump(out, open(os.path.join(HERE, "round42_runs_results.json"), "w"), indent=1)
for s, r in out.items():
    print("==", s)
    for era, row in r.items():
        print(" ", era)
        for k, v in row.items():
            print("    ", k, v)
