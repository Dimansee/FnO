"""Round 32 analysis: which one-direction settings hold up on 2023-25, how they did in 2026, which ingredients matter, and the
comparison with the current Autopilot rules (year by year and month by month). Writes round32_results.json."""
import os, sys, json, pickle
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
G = pickle.load(open(sys.argv[1] if len(sys.argv) > 1 else "/tmp/round32_trades.pkl", "rb"))
GRID, T = G["grid"], G["trades"]
LIVE = pd.read_pickle("/tmp/round32_live.pkl")
END = T.date.max(); LIVE = LIVE[LIVE.date <= END]
T["y"] = T.date.str[:4]; LIVE["y"] = LIVE.date.str[:4]
SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]


def name(k, mx):
    P = GRID[k]; return f"{P['bias']} | {P['trig']} | trail {P['trail']} | {P['tf']}m | {mx}/day"


def st(df):
    if len(df) == 0: return dict(n=0, net=0, pf=None, win=None, fall=0)
    df = df.sort_values(["date", "entry"] if "entry" in df else ["date"])
    w = df.u[df.u > 0].sum(); l = -df.u[df.u <= 0].sum(); eq = df.u.cumsum()
    return dict(n=int(len(df)), net=round(float(df.u.sum())), pf=round(float(w / l), 2) if l else None,
                win=round(float((df.u > 0).mean() * 100)), fall=round(float((eq - eq.cummax().clip(lower=0)).min())))


variants = {}
for mx in (1, 3):
    sub = T if mx == 3 else T[T.n == 0]
    for k, g in sub.groupby("k"):
        variants[(k, mx)] = g
rows = []
for (k, mx), g in variants.items():
    tr, te = g[g.y < "2026"], g[g.y == "2026"]
    yrs = tr.groupby("y").u.sum(); idx = tr.groupby("sym").u.sum()
    s_tr, s_te = st(tr), st(te)
    rows.append(dict(k=k, mx=mx, **GRID[k], train=s_tr["net"], pf=s_tr["pf"], n=s_tr["n"], fall=s_tr["fall"],
                     y23=yrs.get("2023", 0), y24=yrs.get("2024", 0), y25=yrs.get("2025", 0),
                     idx_pos=int((idx > 0).sum()), y26=s_te["net"], pf26=s_te["pf"], n26=s_te["n"]))
R = pd.DataFrame(rows)
R["robust"] = (R[["y23", "y24", "y25"]].min(axis=1) > 0) & (R.pf >= 1.1) & (R.idx_pos >= 2) & (R.n >= 100)


# neighbour robustness: settings differing in one ingredient
def neigh(r):
    m = (R.mx == r.mx) & (((R.bias != r.bias).astype(int) + (R.trig != r.trig) + (R.trail != r.trail) + (R.tf != r.tf)) == 1)
    return round(float((R[m].train > 0).mean() * 100))


R["neigh_pos_pct"] = [neigh(r) for r in R.itertuples()]
RES = {"settings": int(len(R)), "end": END,
       "profitable_train": int((R.train > 0).sum()), "profitable_2026": int((R.y26 > 0).sum()),
       "profitable_both": int(((R.train > 0) & (R.y26 > 0)).sum()), "robust": int(R.robust.sum()),
       "robust_positive_2026": int((R.robust & (R.y26 > 0)).sum())}
print(RES)
cols = ["bias", "trig", "trail", "tf", "mx", "train", "pf", "n", "y23", "y24", "y25", "idx_pos", "neigh_pos_pct", "y26", "pf26", "n26"]
rob = R[R.robust].sort_values("train", ascending=False)
print("\nROBUST on 2023-25:\n", rob[cols].head(30).to_string())
RES["robust_list"] = rob[cols].head(40).to_dict("records")
print("\nTop 15 by training (any):\n", R.sort_values("train", ascending=False)[cols].head(15).to_string())
RES["top_train"] = R.sort_values("train", ascending=False)[cols].head(15).to_dict("records")
print("\nTop 10 by 2026 (hindsight, for reference only):\n", R.sort_values("y26", ascending=False)[cols].head(10).to_string())
RES["top_2026_hindsight"] = R.sort_values("y26", ascending=False)[cols].head(10).to_dict("records")

# ingredients: median training / 2026 result by each choice
ing = {}
for c in ("bias", "trig", "trail", "tf", "mx"):
    t = R.groupby(c)[["train", "y26"]].median().round()
    t["pos_train_%"] = (R.groupby(c).train.apply(lambda s: (s > 0).mean() * 100)).round()
    print("\n", t.to_string()); ing[c] = {str(i): v for i, v in t.to_dict("index").items()}
RES["ingredients_median"] = ing

# one-direction effect: each bias vs "free" on the same trigger / trail / tf / trades a day
base = R[R.bias == "free"].set_index(["trig", "trail", "tf", "mx"])[["train", "y26"]]
eff = {}
for b in ["first", "orb15", "pivot", "cpr", "orb_pivot"]:
    x = R[R.bias == b].set_index(["trig", "trail", "tf", "mx"])[["train", "y26"]]
    dlt = (x - base.reindex(x.index))
    eff[b] = dict(better_train_pct=round(float((dlt.train > 0).mean() * 100)), better_2026_pct=round(float((dlt.y26 > 0).mean() * 100)),
                  median_change_train=round(float(dlt.train.median())), median_change_2026=round(float(dlt.y26.median())))
print("\none-direction vs free:", json.dumps(eff, indent=0))
RES["one_direction_vs_free"] = eff

# ---- comparison with the current rules
alld = sorted(set(LIVE.date) | set(T.date))
def daily(df): return df.groupby("date").u.sum().reindex(alld).fillna(0)
def dstat(s):
    eq = s.cumsum()
    return dict(net=round(float(s.sum())), fall=round(float((eq - eq.cummax().clip(lower=0)).min())),
                sharpe=round(float(s.mean() / s.std() * np.sqrt(250)), 2) if s.std() else None)
picks = {}
# (a) the single best robust setting on 2023-25 (chosen before looking at 2026)
if len(rob):
    r0 = rob.iloc[0]; picks["new: best robust setting (all 3 indices)"] = variants[(r0.k, r0.mx)]
# (b) per index: best setting on that index's own 2023-25 (every year positive, PF >= 1.1, >= 40 trades)
per = []
for s in SYMS:
    best = None
    for (k, mx), g in variants.items():
        gg = g[(g.sym == s) & (g.y < "2026")]
        if len(gg) < 40: continue
        yy = gg.groupby("y").u.sum()
        if len(yy) < 3 or yy.min() <= 0: continue
        pf = gg.u[gg.u > 0].sum() / max(-gg.u[gg.u <= 0].sum(), 1)
        if pf < 1.1: continue
        if best is None or gg.u.sum() > best[0]: best = (gg.u.sum(), k, mx)
    if best:
        per.append(variants[(best[1], best[2])].query("sym == @s"))
        RES.setdefault("per_index_pick", {})[s] = dict(setting=name(best[1], best[2]), train=round(float(best[0])))
if per: picks["new: per-index picks"] = pd.concat(per)
cmp = {"current rules (Autopilot at Rs 2 lakh)": LIVE}
cmp.update(picks)
for nm, df in list(picks.items()):
    cmp[f"current + {nm[5:]}"] = pd.concat([LIVE, df])
table = {}
for nm, df in cmp.items():
    s = daily(df)
    table[nm] = dict(years={y: round(float(s[[d[:4] == y for d in s.index]].sum())) for y in ("2023", "2024", "2025", "2026")},
                     train=dstat(s[[d < "2026" for d in s.index]]), y2026=dstat(s[[d >= "2026" for d in s.index]]),
                     trades_2026=int((df.date >= "2026").sum()),
                     months_2026={m: round(float(s[[d[:7] == m for d in s.index]].sum())) for m in sorted({d[:7] for d in s.index if d >= "2026"})},
                     by_index_2026={k: round(float(v)) for k, v in df[df.date >= "2026"].groupby("sym").u.sum().items()})
    print("\n", nm, json.dumps({k: v for k, v in table[nm].items() if k != "months_2026"}))
RES["comparison"] = table
m = pd.DataFrame({k: v["months_2026"] for k, v in table.items()})
print("\n2026 month by month (Rs, Rs 2,000 risk a trade):\n", m.to_string())
json.dump(RES, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "round32_results.json"), "w"), indent=1, default=str)
print("saved round32_results.json")
