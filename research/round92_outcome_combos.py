"""Round 92 (7 Nov 2026): what is left to re-check after rounds 88 and 90 changed the baseline?

The Outcomes table (regenerated 6 Nov with round 88 + round 90 in the baseline) has three rows that beat today's Autopilot at
BOTH Rs 2L and Rs 20k over the full period and in 2026 alone, without a deeper worst fall:
  T  trail 1.5R behind the best move once +2.5R   (round 40; pending p-r40-exits, waiting on live trades)
  S  2 OTM always                                (round 88 kept 1 OTM < Rs 60k, 1 ITM, 2 OTM from Rs 5L - decided BEFORE round 90)
  L  close a trade at Rs 1,500 loss per lot       (loss caps tested in rounds 23-40 before the current rules)
The table only reports the full period and 2026, so here: each alone, in pairs and all three, on the same engine
(outcomes.run_cfg), every year, both halves (each restarts from the capital), full period, Rs 2L and Rs 20k.
Bar (as in every round): better than today in 2023-24 AND 2025-26 AND the full period, at Rs 2L AND Rs 20k, and the worst
fall not deeper by more than 2 points.
Then a cost stress (+0.5% slippage per fill) and a block bootstrap of the passing versions.
Usage: python research/round92_outcome_combos.py
"""
import itertools, json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import outcomes as O
from fno import portfolio_bt as PB

END = "2026-10-16"   # local research data end
PER = {"2023": ("2023-01-02", "2023-12-31"), "2024": ("2024-01-01", "2024-12-31"), "2025": ("2025-01-01", "2025-12-31"),
       "2026": ("2026-01-01", END), "2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", END), "full": ("2023-01-02", END)}
PIECES = {"T": {"exit": "trail_25"}, "S": {"otm": 2}, "L": {"max_loss_lot": 1500}}


def versions():
    out = {"today": {}}
    for r in range(1, 4):
        for combo in itertools.combinations("TSL", r):
            ch = {}
            for c in combo:
                ch.update(PIECES[c])
            out["+".join(combo)] = ch
    # the Rs 5L / 2L gate kept but trail and loss cap added (in case S only helps large accounts)
    out["T+L (strike rules as today)"] = {**PIECES["T"], **PIECES["L"]}
    return out


def run(ch, cap, a, b):
    q = {**O.B0, **ch, "capital": cap}
    r = O.run_cfg(q, a, b)
    return {"final": round(float(r["final"])), "dd": float(r["max_dd_pct"]), "trades": int(r.get("trades", 0))}


def passes(row, base):
    ok = True
    for cap in ("200000", "20000"):
        for p in ("2023-24", "2025-26", "full"):
            ok &= row[cap][p]["final"] > base[cap][p]["final"]
            ok &= row[cap][p]["dd"] >= base[cap][p]["dd"] - 2.0
    return bool(ok)


def main():
    V = versions()
    res = {}
    for name, ch in V.items():
        res[name] = {}
        for cap in (200_000, 20_000):
            res[name][str(cap)] = {p: run(ch, cap, a, b) for p, (a, b) in PER.items()}
        r2, r20 = res[name]["200000"], res[name]["20000"]
        print(f"{name:<28} Rs2L " + " ".join(f"{p}:{r2[p]['final'] / 1e5:7.2f}L" for p in PER) + f" fall {r2['full']['dd']:+.1f}% | "
              f"Rs20k " + " ".join(f"{p}:{r20[p]['final'] / 1e5:6.2f}L" for p in ("2023-24", "2025-26", "full")) + f" fall {r20['full']['dd']:+.1f}%",
              flush=True)
    base = res["today"]
    res["_pass"] = {n: passes(r, base) for n, r in res.items() if n != "today" and not n.startswith("_")}
    print("PASS:", [n for n, v in res["_pass"].items() if v], flush=True)
    json.dump(res, open(os.path.join(HERE, "round92_results.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
