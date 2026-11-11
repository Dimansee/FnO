"""Round 98 (11 Nov 2026): catch the reversal, or trail the stop once in profit?

Trigger (live paper, 11 Nov): Bank Nifty Noise-band CE 60900, bought 10:45 at Rs 863.2 (index 61,021, risk 209 pts). The index
ran to 61,378 (+1.7R, about +Rs 6,500-7,000 on the option), then fell back; the half-hour band/VWAP check closed it at 12:45
at 60,916: -Rs 496. The Noise rule has no breakeven or profit lock at all (only the 2-ATR stop, the 4R target and the
half-hour band/VWAP trail).

User's two ideas:
  1. find the reversal and exit the open trade on it
  2. once the trade is in profit, move the stop up so a reversal still closes it in profit
Both tested on today's Autopilot (rounds 88 + 90) with the trades re-run bar by bar under each exit (fno.backtest EXIT
profile; the channel slot keeps its own stop-and-reverse exit), Outcomes replay at Rs 2L and Rs 20k.
"N:" = the change applies to Noise-band trades only (EXIT["only"]), the others keep today's exits.
Bar (every round): better than today in 2023-24 AND 2025-26 AND the full period, at Rs 2L AND Rs 20k, and the worst fall
not deeper by more than 2 points.
Usage: python research/round98_reversal_and_trail.py
"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import pandas as pd
import outcomes as O
from fno import portfolio_bt as PB

END = "2026-10-16"
PER = {"2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", END), "full": ("2023-01-02", END),
       "2026": ("2026-01-01", END)}
NEW = {
    # ---- idea 2: move the stop up once in profit
    "n_be1":      ("N: stop to entry at +1R", {"noise_be_r": 1.0}),
    "n_be075":    ("N: stop to entry at +0.75R", {"noise_be_r": 0.75}),
    "n_be1_l25":  ("N: stop to +0.25R at +1R", {"noise_be_r": 1.0, "noise_be_lock": 0.25}),
    "n_be15_l5":  ("N: stop to +0.5R at +1.5R", {"noise_be_r": 1.5, "noise_be_lock": 0.5}),
    "n_lad":      ("N: ladder 1R->0, 1.5R->0.5R, 2R->1R, 3R->2R", {"only": "noise", "lock_steps": [(1.0, 0.0), (1.5, 0.5), (2.0, 1.0), (3.0, 2.0)]}),
    "n_gb_1_1":   ("N: trail 1R behind the best once +1R", {"only": "noise", "giveback": (1.0, 1.0)}),
    "n_gb_15_1":  ("N: trail 1R behind the best once +1.5R", {"only": "noise", "giveback": (1.5, 1.0)}),
    "n_gb_2_1":   ("N: trail 1R behind the best once +2R", {"only": "noise", "giveback": (2.0, 1.0)}),
    "all_lad":    ("All: ladder 1.5R->0.5R, 2R->1R, 3R->2R", {"lock_steps": [(1.5, 0.5), (2.0, 1.0), (3.0, 2.0)]}),
    "all_gb_2_15": ("All: trail 1.5R behind the best once +2R", {"giveback": (2.0, 1.5)}),
    "trail_25":   ("All: trail 1.5R behind the best once +2.5R (r40/r92)", None),
    "all_gb_3_15": ("All: trail 1.5R behind the best once +3R", {"giveback": (3.0, 1.5)}),
    "all_be075":  ("All: stop to entry at +0.75R (Noise too)", {"be_r": 0.75, "noise_be_r": 0.75}),
    # ---- idea 1: exit on a reversal signal (5-min close) once in profit
    "n_ema9_1":   ("N: exit on 5-min close back across 9-EMA once +1R", {"only": "noise", "rev_exit": "ema9", "rev_after_R": 1.0}),
    "n_vwap_1":   ("N: exit on 5-min close back across VWAP once +1R", {"only": "noise", "rev_exit": "vwap", "rev_after_R": 1.0}),
    "n_ev_1":     ("N: exit on 5-min close across 9-EMA AND VWAP once +1R", {"only": "noise", "rev_exit": "ema9_vwap", "rev_after_R": 1.0}),
    "n_ev_15":    ("N: same, once +1.5R", {"only": "noise", "rev_exit": "ema9_vwap", "rev_after_R": 1.5}),
    "n_candle_1": ("N: exit on a strong opposite candle once +1R", {"only": "noise", "rev_exit": "candle", "rev_after_R": 1.0}),
    "n_give50_1": ("N: exit when half the best move is given back, once +1R", {"only": "noise", "rev_exit": "give50", "rev_after_R": 1.0}),
    "all_ema9_1": ("All: exit on 5-min close across 9-EMA once +1R", {"rev_exit": "ema9", "rev_after_R": 1.0}),
    "all_ev_1":   ("All: exit on 5-min close across 9-EMA AND VWAP once +1R", {"rev_exit": "ema9_vwap", "rev_after_R": 1.0}),
    "all_give50_15": ("All: exit when half the best move is given back, once +1.5R", {"rev_exit": "give50", "rev_after_R": 1.5}),
    # ---- both together
    "n_be1_ev":   ("N: stop to entry at +1R + exit on 9-EMA-and-VWAP close", {"noise_be_r": 1.0, "only": "noise", "rev_exit": "ema9_vwap", "rev_after_R": 1.0}),
}


def run(ch, cap, a, b):
    r = O.run_cfg({**O.B0, **ch, "capital": cap}, a, b)
    return {"final": round(float(r["final"])), "dd": float(r["max_dd_pct"]), "trades": int(r.get("trades", 0))}


def passes(row, base):
    ok = True
    for cap in ("200000", "20000"):
        for p in ("2023-24", "2025-26", "full"):
            ok &= row[cap][p]["final"] > base[cap][p]["final"] and row[cap][p]["dd"] >= base[cap][p]["dd"] - 2.0
    return bool(ok)


def problem_size():
    """How often does a trade reach +1R / +1.5R and still close in a loss? (today's rules, 1 lot, 1 ITM)"""
    q = dict(O.B0)
    T = pd.concat([O.trades(PB._trade_key(q), s) for s in q["indices"]], ignore_index=True)
    out = {}
    for strat, g in T.groupby("strategy"):
        row = {"n": len(g), "pnl_1lot": round(float(g.pnl.sum()))}
        for lv in (1.0, 1.5, 2.0):
            h = g[g.mfe_R >= lv]
            bad = h[h.pnl < 0]
            row[f"reach{lv:g}R"] = len(h)
            row[f"reach{lv:g}R_lost"] = len(bad)
            row[f"reach{lv:g}R_lost_rs"] = round(float(bad.pnl.sum()))
        out[strat] = row
    return out


def main():
    for k, (lab, ov) in NEW.items():
        if ov is not None:
            PB.EXITS[k] = {"label": lab, "ov": ov}
    res = {"_problem": problem_size()}
    print("PROBLEM:", json.dumps(res["_problem"], indent=1), flush=True)
    V = {"today": ("Today's exits", {})}
    V.update({k: (lab, {"exit": k}) for k, (lab, _) in NEW.items()})
    for name, (lab, ch) in V.items():
        res[name] = {"label": lab}
        for cap in (200_000, 20_000):
            res[name][str(cap)] = {p: run(ch, cap, a, b) for p, (a, b) in PER.items()}
        r2, r20 = res[name]["200000"], res[name]["20000"]
        print(f"{name:<14} Rs2L " + " ".join(f"{p}:{r2[p]['final'] / 1e5:7.2f}L" for p in PER) + f" fall {r2['full']['dd']:+.1f}% | "
              f"Rs20k " + " ".join(f"{p}:{r20[p]['final'] / 1e5:6.2f}L" for p in PER) + f" fall {r20['full']['dd']:+.1f}%  {lab}",
              flush=True)
        json.dump(res, open(os.path.join(HERE, "round98_results.json"), "w"), indent=1)
    base = res["today"]
    res["_pass"] = {n: passes(r, base) for n, r in res.items() if n != "today" and not n.startswith("_")}
    print("PASS:", [n for n, v in res["_pass"].items() if v], flush=True)
    json.dump(res, open(os.path.join(HERE, "round98_results.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
