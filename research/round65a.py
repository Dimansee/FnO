"""Round 65a (29 Oct 2026): are Camarilla breakouts that come after a big run-up worse? (user's 29 Oct Nifty case:
H4 cross at 10:35 at the top of a ~150-point rally). Trades: round 53 (live Autopilot rules, 2023-01 -> 2026-10)."""
import os, sys, pickle, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round25_harness as H
from fno import indicators as I
T = pickle.load(open("/tmp/round53_trades.pkl", "rb"))
T = T[T.strategy == "Camarilla"].copy()
feat = []
for s in T.sym.unique():
    c = H.load(s).copy()
    c["atr"] = I.atr(c) if hasattr(I, "atr") else (c.high - c.low).rolling(14).mean()
    c["d"] = c.index.date.astype(str)
    c["dopen"] = c.groupby("d").open.transform("first")
    c["dlow"] = c.groupby("d").low.cummin(); c["dhigh"] = c.groupby("d").high.cummax()
    tp = (c.high + c.low + c.close) / 3
    c["vwap"] = (tp * 1).groupby(c.d).cumsum() / c.groupby("d").cumcount().add(1)
    for ix, r in T[T.sym == s].iterrows():
        t_end = pd.Timestamp(r.date + " " + r.entry_time)
        if c.index.tz is not None:
            t_end = t_end.tz_localize(c.index.tz)
        sig = t_end - pd.Timedelta(minutes=5)
        if sig not in c.index:
            continue
        i = c.index.get_loc(sig); sg = r.sgn; a = float(c.atr.iloc[i])
        w6 = c.iloc[max(0, i - 5): i + 1]
        run30 = sg * (c.close.iloc[i] - (w6.low.min() if sg > 0 else w6.high.max())) / a
        w12 = c.iloc[max(0, i - 11): i + 1]
        run60 = sg * (c.close.iloc[i] - (w12.low.min() if sg > 0 else w12.high.max())) / a
        from_open = sg * (c.close.iloc[i] - c.dopen.iloc[i]) / a
        from_ext = sg * (c.close.iloc[i] - (c.dlow.iloc[i] if sg > 0 else c.dhigh.iloc[i])) / a
        vw = sg * (c.close.iloc[i] - c.vwap.iloc[i]) / a
        feat.append((ix, run30, run60, from_open, from_ext, vw))
F = pd.DataFrame(feat, columns=["ix", "run30", "run60", "from_open", "from_dayext", "vwap_dist"]).set_index("ix")
T = T.join(F, how="inner")
T["per"] = np.where(T.date < "2026", "2023-25", "2026")
out = {"n": len(T)}
print("Camarilla trades with features:", len(T), " avg Rs/lot", round(T.pnl.mean()), " win%", round((T.pnl > 0).mean() * 100))
for f in ["run30", "run60", "from_open", "from_dayext", "vwap_dist"]:
    T["q"] = pd.qcut(T[f], 4, labels=["Q1 small", "Q2", "Q3", "Q4 big"])
    g = T.groupby("q", observed=True).agg(n=("pnl", "size"), lo=(f, "min"), hi=(f, "max"), avg=("pnl", "mean"), win=("pnl", lambda x: (x > 0).mean() * 100), R=("R", "mean"))
    g2 = T.groupby(["q", "per"], observed=True).pnl.mean().unstack()
    print(f"\n== {f} (in ATRs, our direction)"); print(pd.concat([g.round(2), g2.round(0)], axis=1).to_string())
    out[f] = pd.concat([g.round(2), g2.round(0)], axis=1).reset_index().astype(str).values.tolist()
T.to_pickle("/tmp/round65_cam.pkl")
json.dump(out, open(os.path.join(HERE, "round65a_results.json"), "w"), indent=1)
