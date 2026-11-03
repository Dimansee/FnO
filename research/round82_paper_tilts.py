"""Round 82: two untested findings from Zarattini, Aziz & Barbon (2025, 'Beat the market', SPY intraday momentum - the paper
behind our Noise band rule):
  NR4 / NR7   days after the narrowest daily range of the last 4 (or 7) sessions earned ~2x the average (22 bps vs 12, t 5.1)
  RSI5        the previous day's 5-day RSI predicts lower intraday-momentum returns when high (coefficient -3.25, p 0.001):
              read as dealer hedging damping trends after a run-up
Applied to today's Autopilot trades (BASELINE incl. CPR sizing, Jan 2023 - Oct 2026), per index's own daily candles:
per-lot P&L by year for each bucket; any bucket better / worse than the rest in EVERY year is replayed (1.3x / drop) at Rs 2L
and Rs 20k vs the same number of trades picked at random within each year.
Usage: python research/round82_paper_tilts.py
"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round73_regime_tilts as R73
import outcomes as O
from fno import portfolio_bt as PB

P = {**R73.PERIODS, "2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", "2026-10-30")}


def day_feats(sym):
    d = R73.daily(sym)
    rng = d.high - d.low
    f = pd.DataFrame(index=d.index)
    f["nr4"] = rng <= rng.rolling(4).min()
    f["nr7"] = rng <= rng.rolling(7).min()
    ch = d.close.diff()
    up, dn = ch.clip(lower=0).ewm(alpha=1 / 5, adjust=False).mean(), (-ch.clip(upper=0)).ewm(alpha=1 / 5, adjust=False).mean()
    f["rsi5"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    f = f.shift(1)                                    # known at today's open
    f.index = f.index.strftime("%Y-%m-%d")
    return f


def main():
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    base = base[base.date <= "2026-10-30"].reset_index(drop=True)
    F = {s: day_feats(s) for s in O.SYMS}
    for c in ("nr4", "nr7", "rsi5"):
        base[c] = [F[s][c].get(d, np.nan) for s, d in zip(base.sym, base.date)]
    base["rs"] = (base.prem_out - base.prem_in) * base.lot
    base["y"] = base.date.str[:4]
    sd = np.where(base.side == "CALL", 1, -1)
    labs = {
        "nr4": np.where(base.nr4 == True, "nr4", "rest"),
        "nr7": np.where(base.nr7 == True, "nr7", "rest"),
        "rsi5_hi70": np.where(base.rsi5 >= 70, "hi", "rest"),
        "rsi5_lo30": np.where(base.rsi5 <= 30, "lo", "rest"),
        "rsi5_stretched_with": np.where(((sd > 0) & (base.rsi5 >= 70)) | ((sd < 0) & (base.rsi5 <= 30)), "stretched_with", "rest"),
        "rsi5_stretched_against": np.where(((sd > 0) & (base.rsi5 <= 30)) | ((sd < 0) & (base.rsi5 >= 70)), "stretched_against", "rest"),
    }
    res = {"buckets": {}, "replays": {}}
    cand = []
    for name, lab in labs.items():
        g = base.assign(b=lab).groupby(["b", "y"]).rs.agg(["count", "mean"]).round(0)
        res["buckets"][name] = g.reset_index().to_dict("records")
        print(f"\n{name}:\n" + g.unstack("y").to_string())
        b = [x for x in set(lab) if x != "rest"][0]
        m = base[lab == b].groupby("y").rs.mean(); rest = base[lab != b].groupby("y").rs.mean()
        diff = (m - rest).reindex(["2023", "2024", "2025", "2026"])
        if diff.notna().all() and ((diff > 0).all() or (diff < 0).all()):
            kind = "better" if (diff > 0).all() else "worse"
            print(f"  -> {kind} than the rest in every year {diff.round(0).to_dict()}")
            cand.append((name, lab == b, kind))
    for name, hit, kind in cand:
        rng = np.random.default_rng(5)
        shit = pd.Series(hit).groupby(base.y.values).transform(lambda v: rng.permutation(v.values)).values.astype(bool)
        tag = f"{name}_{'x1.3' if kind == 'better' else 'skip'}"
        for cap in (200_000, 20_000):
            def run(mask):
                X = base.assign(xm=np.where(mask, 1.3, 1.0)) if kind == "better" else base[~mask].assign(xm=1.0)
                return {p: R73.run(X, alt.assign(xm=1.0) if alt is not None else None, cap, a, b_) for p, (a, b_) in P.items()}
            ref, new, shu = run(np.zeros(len(base), bool)), run(hit), run(shit)
            res["replays"][f"{tag}_{cap}"] = {"today": ref, "rule": new, "shuffled": shu}
            for lb, rr in (("today", ref), (tag, new), ("random same count", shu)):
                print(f"  Rs {cap:,} {lb:<26} " + " ".join(f"{p}:{rr[p]['final'] / 1e5:6.2f}L" for p in P) + f" fall {rr['full']['dd']:+.1f}%", flush=True)
    if not cand:
        print("\nno bucket better or worse in every year -> nothing to replay")
    json.dump(res, open(os.path.join(HERE, "round82_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
