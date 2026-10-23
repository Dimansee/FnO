"""Round 41 (24 Oct 2026): target 1 / target 2 / target 3 - book part of the position at each target, on top of the live exits.

Different from round 26 (which REPLACED the live exits with a fixed stop + T1/T2/T3): here the stop, breakeven at +1R,
Noise band/VWAP trail, Camarilla/Fib 45-min time stop and 15:15 square-off all stay; at each target a share of the lots
is sold and the rest keeps following the live exits. 3 lots on every trade so thirds are whole lots (one buy order,
one sell order per piece, real brokerage per order). Same data / engine / split as round 40: fno.backtest.run strategy
"autopilot", Nifty + Bank Nifty + Sensex, Jan 2023 - 23 Oct 2026, choose on 2023-25, judge on 2026, 1 ITM and 1 OTM.
Run: python round41.py -> round41_results.json"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import pandas as pd
import round40 as R40

T3 = 1 / 3
V = {
    "P0 current exits (3 lots, no partials)": {},
    "P1 T1/T2/T3 = 1R/2R/3R, a third each (fully out at 3R)": {"partials": [(1, T3), (2, T3), (3, T3)]},
    "P2 T1 1R, T2 2R (a third each), last third runs": {"partials": [(1, T3), (2, T3)]},
    "P3 1R/2R/3R thirds + stop to entry after T1": {"partials": [(1, T3), (2, T3), (3, T3)], "be_after_t1": True},
    "P4 T1 1.5R, T2 3R (thirds), last third runs": {"partials": [(1.5, T3), (3, T3)]},
    "P5 T1 0.5R, T2 1R, T3 2R (thirds)": {"partials": [(0.5, T3), (1, T3), (2, T3)]},
    "P6 half at 1R, half runs": {"partials": [(1, 0.5)]},
    "P7 half at 2R, half runs": {"partials": [(2, 0.5)]},
    "P8 a third at 2R, a third at 4R, last third runs": {"partials": [(2, T3), (4, T3)]},
}

if __name__ == "__main__":
    out = {}
    for otm, lab in R40.OTMS.items():
        for nm, ov in V.items():
            T = R40.run_all({**ov, "lots_x": 3}, otm)
            s = R40.summarise(T); s["today"] = int(T[T.date == "2026-10-23"].pnl.sum())
            s["exit_reasons"] = T.reason.value_counts().head(8).to_dict()
            out.setdefault(nm, {})[lab] = s
            print(f"{lab[:6]} {nm[:56]:<56} train {s['train']['net']:>8} pf {s['train']['pf']} fall {s['train']['fall']:>8} | 2026 {s['y2026']['net']:>7} pf {s['y2026']['pf']} | 23 Oct {s['today']:>6}", flush=True)
    json.dump(out, open(os.path.join(HERE, "round41_results.json"), "w"), indent=1, default=str)
