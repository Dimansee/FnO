"""Part F: portfolio overlays never tested: (1) correlation cap - Nifty and Sensex move together (r ~ 0.99), so the same rule
firing on both is one bet taken twice; (2) equity-curve filter - trade a slot smaller / not at all while its own last N trades are
under water. Decisions use only information known at entry. Same replay, both halves, Rs 2L and Rs 20k."""
import sys, json; sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from lib import *
T0, A0, H0 = base_sets()
KEY = ["sym", "date", "entry_time", "strategy"]
T0 = T0.sort_values(["date", "entry_time"]).reset_index(drop=True)


def apply(mult):
    """mult: Series aligned to T0 rows (0 = drop, 0.5 = half the lots ...)."""
    m = T0[KEY].assign(_m=mult.values)
    out = []
    for X in (T0, A0, H0):
        Y = X.merge(m, on=KEY, how="left"); Y["_m"] = Y["_m"].fillna(1.0)
        Y = Y[Y._m > 0].copy()
        Y["risk_lot"] = Y.risk_lot / Y._m; Y["risk_lot_capped"] = Y.risk_lot_capped / Y._m
        out.append(Y.drop(columns="_m"))
    return out


ref = {(cap, p): replay(cap, a, b) for cap in (200_000, 20_000) for p, (a, b) in PER.items()}
res = {"today": {f"{c}|{p}": v for (c, p), v in ref.items()}}
def test(name, mult):
    T, A, H = apply(mult)
    row = {(cap, p): replay(cap, a, b, T=T, A=A, H=H) for cap in (200_000, 20_000) for p, (a, b) in PER.items()}
    ok = all(row[k]["final"] > ref[k]["final"] and row[k]["dd"] >= ref[k]["dd"] - 2 for k in ref)
    res[name] = {f"{c}|{p}": v for (c, p), v in row.items()}; res[name]["pass"] = ok; res[name]["changed"] = int((mult != 1).sum())
    print(f"{name[:57]:<58}" + "".join(f"{row[(c, p)]['final'] / 1e5:>10.2f}{'*' if row[(c, p)]['final'] > ref[(c, p)]['final'] else ' '}" for c in (200_000, 20_000) for p in PER) +
          f"  {row[(200_000, 'full')]['dd']:+.1f}%  n={int((mult != 1).sum())} {'PASS' if ok else ''}", flush=True)
print(f"{'version':<58}" + "".join(f"{'2L ' + p:>11}" for p in PER) + "".join(f"{'20k ' + p:>11}" for p in PER) + "  fall 2L")
print(f"{'(today)':<58}" + "".join(f"{ref[(c, p)]['final'] / 1e5:>11.2f}" for c in (200_000, 20_000) for p in PER) + f"  {ref[(200_000, 'full')]['dd']:+.1f}%")

# ---- 1. correlation cap: same rule, same side, same day, entries within 30 minutes on Nifty and Sensex
mins = lambda s: int(s[:2]) * 60 + int(s[3:])
NS = T0[T0.sym.isin(["NIFTY", "SENSEX"])]
twin = {}
for (d, st, sd), g in NS.groupby(["date", "strategy", "side"]):
    if g.sym.nunique() == 2:
        n, s = g[g.sym == "NIFTY"], g[g.sym == "SENSEX"]
        for i in n.index:
            for j in s.index:
                if abs(mins(T0.entry_time[i]) - mins(T0.entry_time[j])) <= 30:
                    twin[i] = j; twin[j] = i
is_twin = T0.index.isin(list(twin)); print(f"twin trades (same rule + side on Nifty and Sensex within 30 min): {int(is_twin.sum())} of {int(T0.sym.isin(['NIFTY', 'SENSEX']).sum())}")
tw = T0[is_twin]; print("  twin avg R", round(tw.R.mean(), 3), "| non-twin Nifty/Sensex avg R", round(T0[T0.sym.isin(['NIFTY', 'SENSEX']) & ~is_twin].R.mean(), 3),
                        "| twin avg R 2023-24", round(tw[tw.date < '2025'].R.mean(), 3), "2025-26", round(tw[tw.date >= '2025'].R.mean(), 3))
one = pd.Series(1.0, index=T0.index)
m = one.copy(); m[is_twin] = 0.5; test("corr cap: both twins at half size", m)
m = one.copy(); m[is_twin] = 0.75; test("corr cap: both twins at 0.75x", m)
m = one.copy(); m[is_twin & (T0.sym == "SENSEX")] = 0; test("corr cap: drop the Sensex twin", m)
m = one.copy(); m[is_twin & (T0.sym == "NIFTY")] = 0; test("corr cap: drop the Nifty twin", m)
later = pd.Series(False, index=T0.index)
for i, j in twin.items():
    if (mins(T0.entry_time[i]), T0.sym[i] == "SENSEX") > (mins(T0.entry_time[j]), T0.sym[j] == "SENSEX"):
        later[i] = True
m = one.copy(); m[later] = 0; test("corr cap: drop the twin that enters second", m)
m = one.copy(); m[later] = 0.5; test("corr cap: second twin at half size", m)
m = one.copy(); m[is_twin] = 1.25; test("opposite: twins 1.25x (agreement as confirmation)", m)

# ---- 2. equity-curve filter per slot (rule x index): based on the R of that slot's last N CLOSED trades before this entry
T0["slot"] = T0.strategy + "|" + T0.sym
for N in (10, 20, 30):
    prev = T0.groupby("slot").R.transform(lambda r: r.shift(1).rolling(N, min_periods=N).sum())
    for act, lab in ((0.5, "half size"), (0.0, "skip")):
        m = one.copy(); m[prev < 0] = act; test(f"equity curve: slot's last {N} trades < 0R -> {lab}", m)
    m = one.copy(); m[prev > 0] = 1.25; m[prev < 0] = 0.75; test(f"equity curve: last {N} trades >0R 1.25x, <0R 0.75x", m)
print("pass:", [k for k, v in res.items() if isinstance(v, dict) and v.get("pass")])
json.dump(res, open(OUT + "/f_overlays.json", "w"), indent=1, default=str)
