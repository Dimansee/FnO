"""B: stand-alone trades the day after a 2-3 day run. Fade = against the run; 'dip' = gap against the run, then with it.
5-min bars 2023 - 20 Nov 2026, priced as 1 lot ATM option with slippage and charges (round-87 pricing). Each version is
compared with its random-direction twin (same entries, coin-flip side)."""
import json, sys, numpy as np, pandas as pd, r105lib as X
L = X.L
CONDS = {"run>=2": lambda r, c: abs(r) >= 2, "run>=3": lambda r, c: abs(r) >= 3, "run 2-3": lambda r, c: 2 <= abs(r) <= 3,
         "run>=2 & >=1.5%": lambda r, c: abs(r) >= 2 and abs(c) >= 1.5}
EXITS = {"hold": dict(), "2R": dict(tp_R=2.0), "trail1R": dict(trail_R=1.0)}
res = []
def prep(sym):
    df = X.O.candles(sym)[["open", "high", "low", "close"]]
    d = X.streaks(X.daily_from_5m(sym))
    o, h, l, c = (df[k].values.astype(float) for k in ("open", "high", "low", "close"))
    hm = np.asarray(df.index.hour * 60 + df.index.minute); dn = df.index.normalize()
    day = np.asarray(dn.astype("int64"))
    run = d.p_run.reindex(dn).values; cum = d.p_cum.reindex(dn).values; pc = d.pc.reindex(dn).values
    ph = d.high.shift(1).reindex(dn).values; pl = d.low.shift(1).reindex(dn).values
    g = pd.DataFrame({"h": h, "l": l, "o": o, "day": day, "hm": hm})
    dopen = g.groupby("day").o.transform("first").values
    or_h = g[g.hm < 9 * 60 + 45].groupby("day").h.max().reindex(day).values; or_l = g[g.hm < 9 * 60 + 45].groupby("day").l.min().reindex(day).values
    runhi = g.groupby("day").h.cummax().values; runlo = g.groupby("day").l.cummin().values
    return dict(df=df, o=o, h=h, l=l, c=c, hm=hm, day=day, run=run, cum=cum, pc=pc, ph=ph, pl=pl, dopen=dopen, or_h=or_h, or_l=or_l, runhi=runhi, runlo=runlo)

def signals(P, entry, ok):
    """returns L, S, risk for one entry type; ok = boolean per bar (the day qualifies)."""
    c, hm, s = P["c"], P["hm"], np.sign(np.nan_to_num(P["run"]))
    up, dn = ok & (s > 0), ok & (s < 0)
    px = c; clamp = lambda r: np.clip(r, 0.002 * px, 0.006 * px)
    pcl = np.r_[np.nan, c[:-1]]; z = np.zeros(len(c), bool)
    if entry == "fade open":            # 09:20, against the run
        t = hm == 9 * 60 + 15; return dn & t, up & t, 0.0035 * px
    if entry == "fade OR break":        # close beyond the first 30-min range against the run
        t = (hm >= 9 * 60 + 45) & (hm <= 14 * 60)
        return dn & t & (c > P["or_h"]), up & t & (c < P["or_l"]), clamp(P["or_h"] - P["or_l"])
    if entry == "fade prev-close break":
        t = (hm >= 9 * 60 + 30) & (hm <= 14 * 60)
        return dn & t & (c > P["pc"]) & (pcl <= P["pc"]), up & t & (c < P["pc"]) & (pcl >= P["pc"]), 0.003 * px
    if entry == "fade failed new high":  # went past yesterday's extreme with the run, then closed back inside
        t = (hm >= 9 * 60 + 30) & (hm <= 14 * 60)
        Sx = up & t & (P["runhi"] > P["ph"]) & (c < P["ph"]) & (pcl >= P["ph"])
        Lx = dn & t & (P["runlo"] < P["pl"]) & (c > P["pl"]) & (pcl <= P["pl"])
        return Lx, Sx, clamp(np.where(Sx, P["runhi"] - c, c - P["runlo"]) + 0.0005 * px)
    gap = (P["dopen"] / P["pc"] - 1) * 100
    if entry == "dip: gap against, OR break with run":
        t = (hm >= 9 * 60 + 45) & (hm <= 14 * 60)
        return up & t & (gap <= -0.1) & (c > P["or_h"]), dn & t & (gap >= 0.1) & (c < P["or_l"]), clamp(P["or_h"] - P["or_l"])
    if entry == "dip: gap against, back over prev close":
        t = (hm >= 9 * 60 + 20) & (hm <= 14 * 60)
        return up & t & (gap <= -0.1) & (c > P["pc"]) & (pcl <= P["pc"]), dn & t & (gap >= 0.1) & (c < P["pc"]) & (pcl >= P["pc"]), 0.003 * px
    if entry == "with run: OR break":   # control: plain continuation
        t = (hm >= 9 * 60 + 45) & (hm <= 14 * 60)
        return up & t & (c > P["or_h"]), dn & t & (c < P["or_l"]), clamp(P["or_h"] - P["or_l"])
ENTRIES = ["fade open", "fade OR break", "fade prev-close break", "fade failed new high", "dip: gap against, OR break with run",
           "dip: gap against, back over prev close", "with run: OR break"]
PP = {s: prep(s) for s in X.SYMS}
def stats(T):
    if T is None or len(T) == 0: return None
    a, b = T[T.date < "2025"], T[(T.date >= "2025") & (T.date <= "2026-10-16")]; f = T[T.date >= "2026-10-19"]
    k = lambda q: (int(len(q)), round(float(q.rs.sum())), round(float(q.rs.mean())) if len(q) else 0)
    return {"n": int(len(T)), "a": k(a), "b": k(b), "fwd": k(f), "win": round(float((T.rs > 0).mean() * 100), 1), "pts": round(float(T.pts.mean()), 1),
            "years": {y: round(float(v)) for y, v in T.groupby(T.date.str[:4]).rs.sum().items()}}
for cn, cf in CONDS.items():
    for en in ENTRIES:
        for xn, xk in EXITS.items():
            parts, twins = [], []
            for sym in X.SYMS:
                P = PP[sym]
                ok = np.array([cf(r, c) if np.isfinite(r) else False for r, c in zip(P["run"], P["cum"])])
                Lx, Sx, risk = signals(P, en, ok)
                first = 9 * 60 + 20 if en == "fade open" else 9 * 60 + 25
                rows = L.simulate(P["o"], P["h"], P["l"], P["c"], P["hm"], P["day"], Lx, Sx, risk, first=first, **xk)
                if not rows: continue
                T0 = pd.DataFrame(rows, columns=["i", "j", "ep", "xs", "side", "sl"]).astype({"i": int, "j": int})
                T0["date"] = P["df"].index[T0.i.values].strftime("%Y-%m-%d"); T0["t_in"] = P["df"].index[T0.i.values].strftime("%H:%M")
                T0["t_out"] = (P["df"].index[T0.j.values] + pd.Timedelta(minutes=5)).strftime("%H:%M"); T0["pts"] = T0.side * (T0["xs"] - T0.ep)
                t = L.M.price(sym, P["df"], T0); t["sym"] = sym; parts.append(t)
                for seed in (1, 2, 3):
                    tw = L.M.price(sym, P["df"], T0, twin=seed); tw["seed"] = seed; twins.append(tw)
            if not parts: continue
            T = pd.concat(parts, ignore_index=True); TW = pd.concat(twins, ignore_index=True)
            st = stats(T); tw = {"a": round(float(TW[TW.date < "2025"].rs.sum() / 3)), "b": round(float(TW[TW.date >= "2025"].rs.sum() / 3))}
            st.update(cond=cn, entry=en, exit=xn, twin=tw, by_sym={s: (round(float(q[q.date < "2025"].rs.sum())), round(float(q[q.date >= "2025"].rs.sum()))) for s, q in T.groupby("sym")})
            res.append(st)
            print(f"{cn:16s} | {en:38s} | {xn:7s} n {st['n']:4d} win {st['win']:4.1f}% | 23-24 {st['a'][1]:+8d} ({st['a'][2]:+5d}/tr) | 25-26 {st['b'][1]:+8d} ({st['b'][2]:+5d}/tr) | fwd {st['fwd'][0]:2d} {st['fwd'][1]:+7d} | twin {tw['a']:+8d} {tw['b']:+8d} | yrs {st['years']}", flush=True)
json.dump(res, open("r105_b.json", "w"), indent=1)
both = [r for r in res if r["a"][1] > 0 and r["b"][1] > 0]
print(f"\n{len(res)} versions; positive in both periods: {len(both)}; of those beating the twin in both: {sum(1 for r in both if r['a'][1] > r['twin']['a'] and r['b'][1] > r['twin']['b'])}")
