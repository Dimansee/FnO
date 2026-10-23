"""Round 42, part C (24 Oct 2026): T1/T2/T3 booking with the user's split (e.g. 50 lots -> 10 / 15 / 25) and early exits
when the market reverses, on top of the live exits. 10 lots per trade so 20/30/50% are whole lots (2/3/5).
Reversal signals (objective, checked on each finished 5-minute bar once the trade has been +1R): 5-min close back across
the 9-EMA; close back across VWAP; half of the best open move given back; a strong opposite 5-min candle (body >= 60% of
range, range >= 0.5 ATR). Same engine/data/split as rounds 40-41. Run: python round42_exits.py -> round42_exits_results.json"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import round40 as R40

P = [(1.0, 0.2), (2.0, 0.3), (3.0, 0.5)]
V = {
    "R0 current exits (10 lots)": {},
    "R1 book 20/30/50% at 1R/2R/3R": {"partials": P},
    "R2 R1 + exit rest on 5-min close across 9-EMA": {"partials": P, "rev_exit": "ema9"},
    "R3 R1 + exit rest on 5-min close across VWAP": {"partials": P, "rev_exit": "vwap"},
    "R4 R1 + exit rest when half the best move is given back": {"partials": P, "rev_exit": "give50"},
    "R5 R1 + exit rest on a strong opposite candle": {"partials": P, "rev_exit": "candle"},
    "R6 no targets, exit on 9-EMA cross after +1R": {"rev_exit": "ema9"},
    "R7 no targets, exit on strong opposite candle after +1R": {"rev_exit": "candle"},
    "R8 no targets, exit on 9-EMA cross after +2R": {"rev_exit": "ema9", "rev_after_R": 2.0},
    "R9 no targets, exit on strong opposite candle after +2R": {"rev_exit": "candle", "rev_after_R": 2.0},
}

if __name__ == "__main__":
    out = {}
    otms = {-1: R40.OTMS[-1]} if "--itm" in sys.argv else R40.OTMS
    for otm, lab in otms.items():
        for nm, ov in V.items():
            T = R40.run_all({**ov, "lots_x": 10}, otm)
            s = R40.summarise(T); s["today"] = int(T[T.date == "2026-10-23"].pnl.sum())
            s["exit_reasons"] = T.reason.value_counts().head(10).to_dict()
            out.setdefault(nm, {})[lab] = s
            print(f"{lab[:6]} {nm[:58]:<58} train {s['train']['net']:>9} pf {s['train']['pf']} fall {s['train']['fall']:>9} | 2026 {s['y2026']['net']:>8} pf {s['y2026']['pf']} | 23 Oct {s['today']:>7}", flush=True)
            json.dump(out, open(os.path.join(HERE, "round42_exits_results.json"), "w"), indent=1, default=str)
