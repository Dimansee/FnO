"""Round 83: why the Outcomes-tab row 'compound 2% · confidence strong · drawdown brake off · 2 OTM · loss cap Rs 2,200 ·
Nifty + Sensex' (Rs 2L -> Rs 2.18 Cr, rank 5 of 32,256) is not the live Autopilot.

Checks, all with the Outcomes engine (research/outcomes.run_cfg) and today's BASELINE as the reference:
  1. the user's bar: full period, 2023-24 and 2025-26 (each restarted from the capital), each year, Rs 2L and Rs 20k, worst fall
  2. which of the 6 changes does the work (each change alone on today's Autopilot)
  3. cost stress: extra slippage on every fill (cheap 2-OTM premiums have the widest spreads in % terms)
  4. how much of the profit comes from the best few trades
Usage: python research/round83_grid_top_row.py
"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import outcomes as O
from fno import portfolio_bt as PB

PER = {"2023": ("2023-01-02", "2023-12-31"), "2024": ("2024-01-01", "2024-12-31"), "2025": ("2025-01-01", "2025-12-31"),
       "2026": ("2026-01-01", "2026-10-30"), "2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", "2026-10-30"),
       "full": ("2023-01-02", "2026-10-30")}
CAND = {"risk_pct": 2.0, "confidence": "strong", "dd_half": False, "otm": 2, "max_loss_lot": 2200, "indices": ["NIFTY", "SENSEX"]}
ONE = {"risk 2%": {"risk_pct": 2.0}, "confidence strong": {"confidence": "strong"}, "brake off": {"dd_half": False},
       "2 OTM": {"otm": 2}, "loss cap 2,200": {"max_loss_lot": 2200}, "Nifty + Sensex": {"indices": ["NIFTY", "SENSEX"]}}


def run(q, a, b, slip=0.0, detail=False):
    k, ak = PB._trade_key(q), PB._alt_key(q)
    T = pd.concat([O.trades(k, s) for s in q["indices"]], ignore_index=True)
    T = T[(T.date >= a) & (T.date <= b)].copy()
    A = None
    if ak:
        A = pd.concat([O.trades(ak, s) for s in q["indices"]], ignore_index=True)
        A = A[(A.date >= a) & (A.date <= b)].copy()
    if slip:
        for X in (T, A):
            if X is None:
                continue
            X["prem_in"] = X.prem_in * (1 + slip)
            X["prem_out"] = np.maximum(X.prem_out * (1 - slip), 0.05)
            X["R"] = (X.prem_out - X.prem_in) / (X.risk_lot / X.lot)
    R = PB.replay(T, q["capital"], q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"],
                  q["dd_half"] and q["sizing"] != "fixed", A, cpr=q.get("cpr_size", False) and q["sizing"] != "fixed")
    s = PB._summary(R, q["capital"])
    out = {"final": round(s.get("final", q["capital"])), "dd": s.get("max_dd_pct", 0), "trades": s.get("trades", 0)}
    if detail and s.get("trades"):
        tk = R[R["skipped"].isna()] if "skipped" in R else R
        p = tk["pnl"].astype(float).sort_values(ascending=False)
        out["top10_share"] = round(float(p.head(10).sum() / max(p.sum(), 1)), 2)
        out["max_lots"] = int(tk["lots"].max())
        out["worst_trade"] = round(float(p.min()))
    return out


def table(q, cap, slip=0.0):
    return {p: run({**q, "capital": cap}, a, b, slip, detail=(p == "full")) for p, (a, b) in PER.items()}


def line(lab, rr):
    return f"  {lab:<34} " + " ".join(f"{p}:{rr[p]['final'] / 1e5:7.2f}L" for p in PER) + f" | fall {rr['full']['dd']:+.1f}%" + \
           (f" top10 {rr['full'].get('top10_share')} maxlots {rr['full'].get('max_lots')} worst {rr['full'].get('worst_trade')}" if 'top10_share' in rr['full'] else "")


def main():
    base = dict(O.B0)
    cand = {**base, **CAND}
    res = {}
    for cap in (200_000, 20_000):
        print(f"\nRs {cap:,} (each period restarts from Rs {cap:,})")
        res[f"base_{cap}"] = table(base, cap); print(line("today's Autopilot", res[f"base_{cap}"]), flush=True)
        res[f"cand_{cap}"] = table(cand, cap); print(line("grid row (all 6 changes)", res[f"cand_{cap}"]), flush=True)
        for nm, ch in ONE.items():
            res[f"one_{nm}_{cap}"] = table({**base, **ch}, cap); print(line("only " + nm, res[f"one_{nm}_{cap}"]), flush=True)
        for sl in (0.01, 0.02):
            res[f"base_slip{sl}_{cap}"] = table(base, cap, sl); print(line(f"today +{sl:.0%} slippage", res[f"base_slip{sl}_{cap}"]), flush=True)
            res[f"cand_slip{sl}_{cap}"] = table(cand, cap, sl); print(line(f"grid row +{sl:.0%} slippage", res[f"cand_slip{sl}_{cap}"]), flush=True)
    json.dump(res, open(os.path.join(HERE, "round83_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
