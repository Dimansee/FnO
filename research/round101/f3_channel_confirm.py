"""Part F3: the 15-min channel slot only when the other index (Nifty <-> Sensex) has the same channel signal at that bar or
earlier in the day. Unconfirmed channel trades: skip / half size. Year by year, both halves, Rs 2L / Rs 20k / Rs 5L / Rs 10L
(the slot only runs from Rs 5 lakh equity)."""
import sys, json; sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from lib import *
T0, A0, H0 = base_sets(); T0 = T0.sort_values(["date", "entry_time"]).reset_index(drop=True)
KEY = ["sym", "date", "entry_time", "strategy"]
cf = pd.read_pickle(OUT + "/confirm.pkl")
T0 = T0.merge(cf, on=KEY, how="left")
ch = T0.strategy.str.startswith("Channel")
un = ch & ~T0.conf
X = T0[ch].copy(); X["rs"] = (X.prem_out - X.prem_in) * X.lot; X["yr"] = X.date.str[:4]
print("channel trades by year: unconfirmed vs confirmed (avg R | Rs per lot | n)")
for y, g in X.groupby("yr"):
    u, c_ = g[~g.conf], g[g.conf]
    print(f"  {y}: unconfirmed {u.R.mean():+.3f}R  Rs {u.rs.mean():+7.0f}  n={len(u):3d}   |  confirmed {c_.R.mean():+.3f}R  Rs {c_.rs.mean():+7.0f}  n={len(c_)}")
for s, g in X.groupby("sym"):
    u, c_ = g[~g.conf], g[g.conf]; print(f"  {s}: unconfirmed {u.R.mean():+.3f}R n={len(u)} | confirmed {c_.R.mean():+.3f}R n={len(c_)}")
u, c_ = X[~X.conf].R.values, X[X.conf].R.values
se = np.sqrt(u.var(ddof=1) / len(u) + c_.var(ddof=1) / len(c_)); print(f"  difference {c_.mean() - u.mean():+.3f}R, t = {(c_.mean() - u.mean()) / se:.2f}")
rng = np.random.default_rng(1); allR = X.R.values; k = len(u)
perm = np.array([allR[rng.permutation(len(allR))[:k]].mean() for _ in range(20000)])
print(f"  permutation: P(a random {k}-trade subset averages <= {u.mean():+.3f}R) = {(perm <= u.mean()).mean():.4f}")
for st in ("alone (no partner all day)", "partner fires later (unknown at entry)", "partner fired earlier", "same time"):
    g = X[X.st == st]; print(f"    {st:<40} n={len(g):4d}  avg R {g.R.mean():+.3f}  2023-24 {g[g.date < '2025'].R.mean():+.3f}  2025-26 {g[g.date >= '2025'].R.mean():+.3f}")


def apply(mult):
    m = T0[KEY].assign(_m=mult.values); out = []
    for Y in (T0[[c for c in T0.columns if c not in ("st", "conf")]], A0, H0):
        Y = Y.merge(m, on=KEY, how="left"); Y["_m"] = Y["_m"].fillna(1.0); Y = Y[Y._m > 0].copy()
        Y["risk_lot"] = Y.risk_lot / Y._m; Y["risk_lot_capped"] = Y.risk_lot_capped / Y._m; out.append(Y.drop(columns="_m"))
    return out
CAPS = (200_000, 20_000, 500_000, 1_000_000)
P2 = {**PER, **YEARS}
ref = {(cap, p): replay(cap, a, b) for cap in CAPS for p, (a, b) in P2.items()}
res = {"today": {f"{c}|{p}": v for (c, p), v in ref.items()}}
one = pd.Series(1.0, index=T0.index)
for name, m in (("skip unconfirmed channel trades", one.where(~un, 0.0)), ("unconfirmed channel trades at half size", one.where(~un, 0.5))):
    T, A, H = apply(m)
    row = {(cap, p): replay(cap, a, b, T=T, A=A, H=H) for cap in CAPS for p, (a, b) in P2.items()}
    ok = all(row[(c, p)]["final"] >= ref[(c, p)]["final"] and row[(c, p)]["dd"] >= ref[(c, p)]["dd"] - 2 for c in (200_000, 20_000) for p in PER)
    strict = all(row[(c, p)]["final"] > ref[(c, p)]["final"] for c in (200_000, 20_000) for p in PER)
    res[name] = {f"{c}|{p}": v for (c, p), v in row.items()}; res[name]["pass_not_worse"] = ok; res[name]["strictly_better_everywhere"] = strict
    print(f"\n== {name}   (not worse anywhere + fall rule: {ok}; strictly better in all six cells: {strict})")
    for cap in CAPS:
        print(f"  Rs {cap:>9,}  " + "  ".join(f"{p}: {ref[(cap, p)]['final'] / 1e5:.2f}L -> {row[(cap, p)]['final'] / 1e5:.2f}L ({ref[(cap, p)]['dd']:+.0f}% -> {row[(cap, p)]['dd']:+.0f}%)" for p in P2))
json.dump(res, open(OUT + "/f3.json", "w"), indent=1, default=str)
