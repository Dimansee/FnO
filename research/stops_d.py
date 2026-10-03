import json, numpy as np, pandas as pd, glob
import engine as E
def study(opt, spot_fn, bar):
    res = []
    for (sym, d, exp, k, typ), g in opt.groupby(["sym", "d", "expiry", "strike", "opt"]):
        dte = (exp - d).days
        if dte < 1 or dte > 14:
            continue
        sp = spot_fn(sym, d)
        if sp is None:
            continue
        mm, cc = sp
        step = E.META[sym]["step"]
        g = g.sort_values("m")
        for t_in in range(570, 871, 30):
            x = g[g.m == t_in - bar]
            j = int(np.searchsorted(mm, t_in - 1))
            if x.empty or j >= len(cc):
                continue
            atm = round(cc[j] / step) * step
            mny = (k - atm) / step * (1 if typ == "CE" else -1)
            if not -1 <= mny <= 0:
                continue
            e = float(x.close.iloc[0])
            nxt = g[(g.m >= t_in) & (g.m < 915)]
            if e < 20 or nxt.empty:
                continue
            lo, hi = nxt.low.values / e - 1, nxt.high.values / e - 1
            for slp in (0.2, 0.3, 0.4):
                for tp in (0.2, 0.4, 0.6, 1.0):
                    s_hit = np.nonzero(lo <= -slp)[0]; t_hit = np.nonzero(hi >= tp)[0]
                    if len(t_hit) and (not len(s_hit) or t_hit[0] < s_hit[0]):
                        code, depth = 0, 0.0
                    elif len(s_hit):
                        later = t_hit[t_hit > s_hit[0]]
                        code = 1 if len(later) else 2
                        upto = later[0] if len(later) else len(lo)
                        depth = float(-lo[s_hit[0]:upto].min() - slp)
                    else:
                        code, depth = 3, 0.0
                    res.append((sym, slp, tp, code, depth, dte, d))
    return pd.DataFrame(res, columns=["sym", "sl", "tp", "code", "depth", "dte", "d"])
def table(P):
    out = {}
    for (slp, tp), g in P.groupby(["sl", "tp"]):
        st = g[g.code >= 1]
        out[f"SL-{int(slp*100)}% TP+{int(tp*100)}%"] = {"entries": len(g), "target_first": round((g.code == 0).mean()*100, 1),
            "stop_then_target": round((g.code == 1).mean()*100, 1), "stop_never_target": round((g.code == 2).mean()*100, 1),
            "neither": round((g.code == 3).mean()*100, 1),
            "of_stopped_then_target": round((st.code == 1).mean()*100, 1) if len(st) else None,
            "depth_below_stop_median_pct": round(float(g[g.code == 1].depth.median()*100), 1) if (g.code == 1).any() else None,
            "depth_within_2pct_share": round(float((g[g.code == 1].depth <= 0.02).mean()*100), 1) if (g.code == 1).any() else None}
    return out
# 5-minute real candles (still-listed contracts, May-Oct 2026)
o5 = pd.read_csv("data/OPT_live5m.csv.gz", parse_dates=["ts", "expiry"])
o5 = o5[(o5.ts.dt.hour*60 + o5.ts.dt.minute).between(555, 925) & (o5.volume > 0)]
o5["d"], o5["m"], o5["expiry"] = o5.ts.dt.date, o5.ts.dt.hour*60 + o5.ts.dt.minute, o5.expiry.dt.date
idx = {}
for s in ("NIFTY", "BANKNIFTY"):
    x = pd.read_csv(f"data/{s}_1m_upstox.csv.gz", parse_dates=["ts"])
    for d, g in x.groupby(x.ts.dt.date):
        idx[(s, d)] = ((g.ts.dt.hour*60 + g.ts.dt.minute).values, g.close.values)
P5 = study(o5, lambda s, d: idx.get((s, d)), 5)
# 1-minute real candles recorded by the new nightly job
fr = []
for f in sorted(glob.glob("data/live/*/candles_1m.csv.gz")):
    c = pd.read_csv(f, parse_dates=["ts"]); fr.append(c)
c = pd.concat(fr); c = c[c.sym.isin(["NIFTY", "BANKNIFTY"])]
ix = c[c.kind == "IDX"]
for (s, d), g in ix.groupby([ix.sym, ix.ts.dt.date]):
    g = g.sort_values("ts"); idx[(s, d)] = ((g.ts.dt.hour*60 + g.ts.dt.minute).values, g.close.values)
o1 = c[c.kind.isin(["CE", "PE"])].rename(columns={"kind": "opt"}).copy()
o1["d"], o1["m"], o1["expiry"] = o1.ts.dt.date, o1.ts.dt.hour*60 + o1.ts.dt.minute, pd.to_datetime(o1.expiry).dt.date
o1 = o1[o1.m.between(555, 929)]
P1 = study(o1, lambda s, d: idx.get((s, d)), 1)
out = {"real_5min": {"days": int(P5.d.nunique()), "table": table(P5)}, "real_1min": {"days": int(P1.d.nunique()), "table": table(P1)}}
json.dump(out, open("r7_stops_premium.json", "w"), default=str, indent=1)
for k in ("real_5min", "real_1min"):
    print(k, out[k]["days"], "days")
    print(pd.DataFrame(out[k]["table"]).T.to_string())
