"""Part E2: Camarilla on Sensex done exactly (the rule shares a slot with the others on that index, so the whole Sensex set is rebuilt)."""
import sys, json, pickle; sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from lib import *
T0, A0, H0 = base_sets(); vix = O.H.load("INDIAVIX")["close"]
old = dict(C.AUTOPILOT_SYM_STRATEGIES)
C.AUTOPILOT_SYM_STRATEGIES = {**old, "SENSEX": ["noise", "camarilla", "trend", "fib"]}
sets = {}
try:
    for name, key, ref in (("T", PB._trade_key(B0), T0), ("A", PB._alt_key(B0), A0), ("H", PB._hi_key(B0), H0)):
        ex, otm, brs, mll = key
        X = PB._trades("SENSEX", O.candles("SENSEX"), vix, otm, PB.EXITS[ex]["ov"], brs, mll); X["score"] = PB._scores(X, O.candles("SENSEX"))
        sets[name] = pd.concat([ref[ref.sym != "SENSEX"], X], ignore_index=True)
finally:
    C.AUTOPILOT_SYM_STRATEGIES = old
X = sets["T"][sets["T"].sym == "SENSEX"]; print(X.groupby("strategy").size().to_dict(), "| baseline:", T0[T0.sym == "SENSEX"].groupby("strategy").size().to_dict())
ref = {(cap, p): replay(cap, a, b) for cap in (200_000, 20_000) for p, (a, b) in {**PER, **YEARS}.items()}
row = {(cap, p): replay(cap, a, b, T=sets["T"], A=sets["A"], H=sets["H"]) for cap in (200_000, 20_000) for p, (a, b) in {**PER, **YEARS}.items()}
for cap in (200_000, 20_000):
    print(cap, "today   ", {p: (round(ref[(cap, p)]["final"] / 1e5, 2), round(ref[(cap, p)]["dd"], 1)) for p in {**PER, **YEARS}})
    print(cap, "+CamSens", {p: (round(row[(cap, p)]["final"] / 1e5, 2), round(row[(cap, p)]["dd"], 1)) for p in {**PER, **YEARS}})
ok = all(row[(c, p)]["final"] > ref[(c, p)]["final"] and row[(c, p)]["dd"] >= ref[(c, p)]["dd"] - 2 for c in (200_000, 20_000) for p in PER)
print("PASS" if ok else "no pass")
json.dump({"today": {f"{c}|{p}": v for (c, p), v in ref.items()}, "cam_sensex": {f"{c}|{p}": v for (c, p), v in row.items()}, "pass": ok}, open(OUT + "/e2.json", "w"), indent=1)
pickle.dump(sets, open(OUT + "/camsens.pkl", "wb"))
