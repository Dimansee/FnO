"""Round 93 (7 Nov 2026): book at 1:2, and after 1:2 increase lot size on the next signal if signals are strong.

User: "what if we do 1:2 ratio wise profit and after 1:2 we will increase lot size according to the other signal and if the
signals are strong". Earlier: a fixed 2R target lost to the current exits (r21c, r26 - older rules); pyramiding on re-entry
deepened the fall to -57% (r14a); conviction sizing by the 0-6 agreement score is live (r33: 1.33x on 5-6, 0.67x below).
Versions on today's Autopilot (rounds 88 + 90 included), Outcomes engine:
  A    take the whole position off at +2R on every trade (stop, trail and time rules otherwise unchanged)
  B    'house money': a new trade gets m x size when another trade in the same index and direction is already >= +2R
       at this entry (open, judged on the index price at entry) - strong only (score >= 5) or any score
  C    after a trade CLOSES at >= +2R, the next strong (score >= 5) trade that session gets m x size
  D    A + C (the literal reading: book 1:2, then size up the next strong signal)
  m = 1.5 / 2.0. Size is set by scaling that trade's risk per lot, so lot caps, cash checks and slippage still apply.
Bar: better than today in 2023-24, 2025-26 and the full period, at Rs 2L and Rs 20k, worst fall not deeper by > 2 points.
Usage: python research/round93_2r_then_size_up.py
"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import outcomes as O
from fno import portfolio_bt as PB
import round92_outcome_combos as R92

PB.EXITS["tp_2r"] = {"label": "Book everything at 2R", "ov": {"partials": [(2, 1.0)]}}
PER = {p: R92.PER[p] for p in ("2023-24", "2025-26", "full", "2026")}
KEY = ["sym", "date", "entry_time", "strategy"]


def frames(q):
    k, ak, hk = PB._trade_key(q), PB._alt_key(q), PB._hi_key(q)
    T = pd.concat([O.trades(k, s) for s in q["indices"]], ignore_index=True)
    A = pd.concat([O.trades(ak, s) for s in q["indices"]], ignore_index=True) if ak else None
    H = pd.concat([O.trades(hk, s) for s in q["indices"]], ignore_index=True) if hk else None
    return T, A, H


def risk_pts(T):
    """Index-point risk of each trade: from its result where |R| >= 0.1, else from option risk / (lot x 0.6 delta)."""
    mv = (T.spot_out - T.spot_in).abs()
    r = np.where(T.R.abs() >= 0.1, mv / T.R.abs().clip(lower=1e-9), T.risk_lot / (T.lot * 0.6))
    return pd.Series(r, index=T.index)


def mult_house(T, m, strong_only):
    """B: m when another same-index, same-direction trade is open at this entry and >= +2R on the index price at entry."""
    T = T.copy(); T["rp"] = risk_pts(T)
    T["sg"] = np.where(T.side == "CALL", 1, -1)
    out = {}
    for (s, d), g in T.groupby(["sym", "date"]):
        g = g.sort_values("entry_time")
        for r in g.itertuples(index=False):
            if strong_only and r.score < 5:
                continue
            opn = g[(g.entry_time < r.entry_time) & (g.exit_time > r.entry_time) & (g.sg == r.sg)]
            if len(opn) and ((r.spot_in - opn.spot_in) * opn.sg / opn.rp >= 2.0).any():
                out[(r.sym, r.date, r.entry_time, r.strategy)] = m
    return out


def mult_after_win(T, m):
    """C: after a trade closes at >= +2R, the next strong trade that session (any index) gets m."""
    out = {}
    for d, g in T.groupby("date"):
        wins = g[g.R >= 2.0].exit_time.sort_values().tolist()
        if not wins:
            continue
        used = set()
        for w in wins:
            nxt = g[(g.entry_time > w) & (g.score >= 5)].sort_values("entry_time")
            for r in nxt.itertuples(index=False):
                k = (r.sym, r.date, r.entry_time, r.strategy)
                if k not in used:
                    out[k] = m; used.add(k); break
    return out


def apply(X, mult):
    if X is None or not mult:
        return X
    X = X.copy()
    f = np.array([mult.get(k, 1.0) for k in zip(X.sym, X.date, X.entry_time, X.strategy)])
    X["risk_lot"] = X.risk_lot / f
    X["risk_lot_capped"] = X.risk_lot_capped / f
    return X


def replay(q, frs, mult, a, b):
    T, A, H = (apply(x, mult) for x in frs)
    cut = lambda X: X[(X.date >= a) & (X.date <= b)] if X is not None else None  # noqa: E731
    R = PB.replay(cut(T), q["capital"], q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"],
                  q["dd_half"] and q["sizing"] != "fixed", cut(A), cpr=q.get("cpr_size", False) and q["sizing"] != "fixed",
                  hi=cut(H), channel=q.get("channel", True))
    s = PB._summary(R, q["capital"])
    return {"final": round(float(s["final"])), "dd": float(s["max_dd_pct"]), "trades": int(s["trades"])}


def main():
    q0 = dict(O.B0)
    base = frames(q0)
    tp = frames({**q0, "exit": "tp_2r"})
    V = {"today": (base, {}), "A book all at 2R": (tp, {})}
    for m in (1.5, 2.0):
        V[f"B up 2R -> next signal {m}x (strong only)"] = (base, mult_house(base[0], m, True))
        V[f"B up 2R -> next signal {m}x (any)"] = (base, mult_house(base[0], m, False))
        V[f"C after a 2R win -> next strong {m}x"] = (base, mult_after_win(base[0], m))
        V[f"D book at 2R + next strong {m}x"] = (tp, mult_after_win(tp[0], m))
    res = {}
    for name, (frs, mult) in V.items():
        res[name] = {"sized_trades": len(mult)}
        for cap in (200_000, 20_000):
            res[name][str(cap)] = {p: replay({**q0, "capital": cap}, frs, mult, a, b) for p, (a, b) in PER.items()}
        r2, r20 = res[name]["200000"], res[name]["20000"]
        print(f"{name:<40} sized {len(mult):>4} | Rs2L 23-24 {r2['2023-24']['final'] / 1e5:6.2f}L 25-26 {r2['2025-26']['final'] / 1e5:6.2f}L "
              f"full {r2['full']['final'] / 1e5:7.2f}L ({r2['full']['dd']:+.1f}%) 2026 {r2['2026']['final'] / 1e5:5.2f}L | "
              f"Rs20k 23-24 {r20['2023-24']['final'] / 1e5:5.2f}L 25-26 {r20['2025-26']['final'] / 1e5:5.2f}L full {r20['full']['final'] / 1e5:6.2f}L "
              f"({r20['full']['dd']:+.1f}%)", flush=True)
    b = res["today"]
    res["_pass"] = {n: R92.passes(r, b) for n, r in res.items() if n != "today" and not n.startswith("_")}
    print("PASS:", [n for n, v in res["_pass"].items() if v], flush=True)
    json.dump(res, open(os.path.join(HERE, "round93_results.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
