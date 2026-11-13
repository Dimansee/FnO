"""Part H: are the sizing tilts that raised profit real, or just more leverage? Leverage-matched control: the same multipliers
are shuffled at random among the trades of each slot (rule x index) 120 times; the real tilt must beat most random ones."""
import sys, json, time; sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from lib import *
T0, A0, H0 = base_sets(); T0 = T0.sort_values(["date", "entry_time"]).reset_index(drop=True)
KEY = ["sym", "date", "entry_time", "strategy"]
cf = pd.read_pickle(OUT + "/confirm.pkl"); T0 = T0.merge(cf, on=KEY, how="left")
BASEC = [c for c in T0.columns if c not in ("st", "conf")]
def apply(mult):
    m = T0[KEY].assign(_m=np.asarray(mult)); out = []
    for Y in (T0[BASEC], A0, H0):
        Y = Y.merge(m, on=KEY, how="left"); Y["_m"] = Y["_m"].fillna(1.0); Y = Y[Y._m > 0].copy()
        Y["risk_lot"] = Y.risk_lot / Y._m; Y["risk_lot_capped"] = Y.risk_lot_capped / Y._m; out.append(Y.drop(columns="_m"))
    return out
CELLS = [(200_000, "2023-24"), (200_000, "2025-26"), (200_000, "full"), (20_000, "2023-24"), (20_000, "2025-26"), (20_000, "full")]
def run(mult):
    T, A, H = apply(mult)
    return {k: replay(k[0], *PER[k[1]], T=T, A=A, H=H) for k in CELLS}
ref = run(np.ones(len(T0)))
slot = (T0.strategy + "|" + T0.sym).values
one = np.ones(len(T0))
tilts = {}
for N in (20, 30, 40, 60):
    prev = T0.groupby(slot).R.transform(lambda r: r.shift(1).rolling(N, min_periods=N).sum()).values
    m = one.copy(); m[prev > 0] = 1.25; m[prev < 0] = 0.75; tilts[f"equity curve last {N}: 1.25x / 0.75x"] = m
    m = one.copy(); m[prev > 0] = 1.15; m[prev < 0] = 0.85; tilts[f"equity curve last {N}: 1.15x / 0.85x"] = m
ns = T0.sym.isin(["NIFTY", "SENSEX"]).values; conf = T0.conf.fillna(False).values
m = one.copy(); m[ns & conf] = 1.25; m[ns & ~conf] = 0.75; tilts["cross-index confirmed 1.25x / unconfirmed 0.75x (known at entry)"] = m
m = one.copy(); m[ns & conf] = 1.15; m[ns & ~conf] = 0.85; tilts["cross-index confirmed 1.15x / unconfirmed 0.85x"] = m
m = one.copy(); m[ns & ~conf & T0.strategy.str.startswith("Channel").values] = 0.0; m[ns & conf & T0.strategy.str.startswith("Fib").values] = 1.25; tilts["channel unconfirmed skipped + Fib confirmed 1.25x"] = m
rng = np.random.default_rng(11); out = {"today": {f"{c}|{p}": v for (c, p), v in ref.items()}}; t0 = time.time()
NP = int(sys.argv[1]) if len(sys.argv) > 1 else 120
for name, m in tilts.items():
    real = run(m)
    rnd = {k: [] for k in CELLS}; dd = []
    for _ in range(NP):
        p = m.copy()
        for s in np.unique(slot):
            ix = np.where(slot == s)[0]; p[ix] = m[ix][rng.permutation(len(ix))]
        r = run(p)
        for k in CELLS:
            rnd[k].append(r[k]["final"])
        dd.append(r[(200_000, "full")]["dd"])
    better = all(real[k]["final"] > ref[k]["final"] for k in CELLS); fall_ok = all(real[k]["dd"] >= ref[k]["dd"] - 2 for k in CELLS)
    pct = {k: float((np.array(rnd[k]) < real[k]["final"]).mean()) for k in CELLS}
    out[name] = {"real": {f"{c}|{p}": v for (c, p), v in real.items()}, "beats_random_share": {f"{c}|{p}": v for (c, p), v in pct.items()},
                 "random_median": {f"{c}|{p}": float(np.median(v)) for (c, p), v in rnd.items()}, "better_everywhere": better, "fall_ok": fall_ok}
    print(f"\n{name}   better in all six: {better} | fall within 2 pts: {fall_ok} | {round(time.time() - t0)}s")
    for k in CELLS:
        print(f"   Rs {k[0]:>7,} {k[1]:<8} today {ref[k]['final'] / 1e5:7.2f}L ({ref[k]['dd']:+.0f}%)  tilt {real[k]['final'] / 1e5:7.2f}L ({real[k]['dd']:+.0f}%)  random median {np.median(rnd[k]) / 1e5:7.2f}L  beats {pct[k] * 100:3.0f}% of random", flush=True)
    json.dump(out, open(OUT + "/h_perm.json", "w"), indent=1, default=str)
