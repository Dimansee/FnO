"""Round 37 (Oct 2026): re-check round 21's "second trade only if the first did not lose" on the CURRENT Autopilot set
(Noise + Camarilla on Nifty / Bank Nifty, Noise on Sensex, plus the trend-day and Fibonacci slots, which stay one trade a day).
Variants for the Noise / Camarilla slot: one trade per rule per day (now) | a 2nd trade after a non-losing 1st, either direction |
the same, same direction only. Harness, prices and costs as rounds 25-33; 1 lot; Rs 2,000 risk a trade ("u").
Run: python round37.py -> round37_results.json"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import round25_harness as H25

VIX = H25.load("INDIAVIX")["close"]; BIG = 1e9
BASE = dict(cam_tstop=45, noise_trail="band_vwap", overlap=False, reentry=0, sizing="one_lot")
LIVE = pd.read_pickle("/tmp/round32_live.pkl")                 # current set (round 32 baseline): main + trend + fib slots
OTHER = LIVE[LIVE.slot != "main"].copy()
END = LIVE.date.max()
VARIANTS = {"one trade per rule per day (now)": {},
            "2nd trade after a non-losing 1st, either direction": dict(reentry_nonloss=True, reentry=1),
            "2nd trade after a non-losing 1st, same direction": dict(reentry_nonloss=True, reentry=1, reentry_same_side=True)}


def main_slot(P):
    rows = []
    for s, cam in [("NIFTY", True), ("BANKNIFTY", True), ("SENSEX", False)]:
        rows += [{**r, "sym": s, "slot": "main"} for r in H25.run(s, H25.load(s), VIX, {**BASE, **P, **({} if cam else {"cam_vix_max": -1})}, capital=BIG)]
    T = pd.DataFrame(rows); T["u"] = T.pnl / T.risk_lot * 2000
    return T[T.date <= END]


def stats(T):
    s = T.groupby("date").u.sum().sort_index(); eq = s.cumsum(); fall = float((eq - eq.cummax().clip(lower=0)).min())
    w = T.u[T.u > 0].sum(); l = -T.u[T.u <= 0].sum()
    return dict(n=int(len(T)), net=round(float(T.u.sum())), pf=round(float(w / l), 2), win=round(float((T.u > 0).mean() * 100), 1), fall=round(fall))


RES = {}
for nm, P in VARIANTS.items():
    M = main_slot(P); A = pd.concat([M, OTHER]); A["y"] = A.date.str[:4]
    RES[nm] = dict(all_years=stats(A), train=stats(A[A.y < "2026"]), y2026=stats(A[A.y == "2026"]),
                   years={y: round(float(v)) for y, v in A.groupby("y").u.sum().items()},
                   by_index={k: round(float(v)) for k, v in A.groupby("sym").u.sum().items()},
                   main_trades=int(len(M)), extra_trades=int(len(M)) - int(len(LIVE[LIVE.slot == "main"])),
                   by_year_main={y: round(float(v)) for y, v in M.groupby(M.date.str[:4]).u.sum().items()})
    if nm != list(VARIANTS)[0]:
        b = RES[list(VARIANTS)[0]]
        RES[nm]["vs_now_by_year"] = {y: RES[nm]["years"][y] - b["years"][y] for y in b["years"]}
        RES[nm]["vs_now_by_index"] = {k: RES[nm]["by_index"][k] - b["by_index"].get(k, 0) for k in RES[nm]["by_index"]}
    print(nm, json.dumps({k: v for k, v in RES[nm].items() if k in ("all_years", "train", "y2026", "years", "vs_now_by_year", "vs_now_by_index", "extra_trades")}), flush=True)
json.dump(RES, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "round37_results.json"), "w"), indent=1)
print("saved")
