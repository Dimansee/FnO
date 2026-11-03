"""Round 84: Autopilot v2.0 idea - trade only the best 5 rule x index pairs.

Universe: the 9 live pairs (Nifty: Noise, Camarilla, Trend-day, Fibonacci; Bank Nifty: Noise, Camarilla; Sensex: Noise,
Trend-day, Fibonacci) - the other pairs were dropped in rounds 27/29 for losing. Today's BASELINE trades (CPR sizing, small-
account OTM), Outcomes replay at Rs 2L and Rs 20k.
  static   rank pairs on 2023-24 only (per-lot P&L, or profit factor), keep the top 3 / 4 / 5 / 6; judge on 2025-26 (unseen)
  rolling  'live' version: every quarter, keep the top 5 pairs by per-lot P&L over the previous 12 months (no look-ahead)
  hindsight top 5 by the full period (for reference only - not a fair test)
Usage: python research/round84_top_pairs.py
"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round73_regime_tilts as R73
import outcomes as O
from fno import portfolio_bt as PB

P = {"2023": ("2023-01-02", "2023-12-31"), "2024": ("2024-01-01", "2024-12-31"), "2025": ("2025-01-01", "2025-12-31"),
     "2026": ("2026-01-01", "2026-10-30"), "2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", "2026-10-30"),
     "full": ("2023-01-02", "2026-10-30")}


def stats(T):
    g = T.groupby("pair").rs
    pf = T.groupby("pair").apply(lambda x: x.rs[x.rs > 0].sum() / max(-x.rs[x.rs <= 0].sum(), 1))
    return pd.DataFrame({"n": g.size(), "rs": g.sum(), "pf": pf})


def run(T, A, keep, cap, a, b):
    """keep: set of pairs, or a function(date) -> set of pairs."""
    f = (lambda X: X[X.pair.isin(keep)]) if not callable(keep) else (lambda X: X[[p in keep(d) for p, d in zip(X.pair, X.date)]])
    return R73.run(f(T).assign(xm=1.0), f(A).assign(xm=1.0) if A is not None else None, cap, a, b)


def main():
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    T = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    A = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    for X in (T, A):
        if X is not None:
            X["pair"] = X.sym + " " + X.strategy
    T = T[T.date <= "2026-10-30"]
    T["rs"] = (T.prem_out - T.prem_in) * T.lot
    s_in = stats(T[T.date < "2025"]).sort_values("rs", ascending=False)
    s_out = stats(T[T.date >= "2025"])
    s_all = stats(T).sort_values("rs", ascending=False)
    print("pairs ranked on 2023-24 (per-lot Rs), with 2025-26:\n", s_in.join(s_out, rsuffix="_2526").round(2).to_string())
    sets = {"all 9 (today)": set(T.pair)}
    for n in (3, 4, 5, 6):
        sets[f"top {n} by Rs, picked on 2023-24"] = set(s_in.index[:n])
    sets["top 5 by profit factor, picked on 2023-24"] = set(s_in.sort_values("pf", ascending=False).index[:5])
    sets["top 5 in hindsight (full period, not fair)"] = set(s_all.index[:5])
    # rolling quarterly re-pick from the previous 12 months
    qs = pd.date_range("2023-01-01", "2026-12-31", freq="QS")
    pick = {}
    for q in qs:
        lo, hi = (q - pd.DateOffset(months=12)).strftime("%Y-%m-%d"), q.strftime("%Y-%m-%d")
        h = T[(T.date >= lo) & (T.date < hi)]
        pick[hi] = set(stats(h).sort_values("rs", ascending=False).index[:5]) if len(h) > 50 else set(T.pair)
    keys = sorted(pick)
    roll = lambda d: pick[max(x for x in keys if x <= d)]
    sets["rolling: top 5 of the last 12 months, re-picked quarterly"] = roll
    res = {"rank_2023_24": s_in.round(2).reset_index().to_dict("records"), "rank_2025_26": s_out.round(2).reset_index().to_dict("records"),
           "sets": {k_: (sorted(v) if not callable(v) else {q: sorted(pick[q]) for q in keys}) for k_, v in sets.items()}, "replays": {}}
    for cap in (200_000, 20_000):
        print(f"\nRs {cap:,} (each period restarts from Rs {cap:,})")
        for name, keep in sets.items():
            rr = {p: run(T, A, keep, cap, a, b) for p, (a, b) in P.items()}
            res["replays"][f"{name}_{cap}"] = rr
            print(f"  {name:<58} " + " ".join(f"{p}:{rr[p]['final'] / 1e5:6.2f}L" for p in P) + f" | fall {rr['full']['dd']:+.1f}% trades {rr['full']['trades']}", flush=True)
    for name, v in sets.items():
        if not callable(v):
            print(f"{name}: {sorted(v)}")
    json.dump(res, open(os.path.join(HERE, "round84_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
