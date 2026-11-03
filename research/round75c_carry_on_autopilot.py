"""Round 75c: add the round-75b overnight carry (strong close -> sell at next open, real NSE option prices) to today's Autopilot.

Only Nifty + Bank Nifty from Oct 2023 have real option prices (bhavcopy); Sensex and Jan-Sep 2023 get no carry trades, so the
2023 column is mostly today's Autopilot. Each carry trade is put on day D at 15:20 and closed before D+1's first Autopilot entry,
risk per lot = the whole premium (no stop overnight), sized by Autopilot's own risk rule, CPR tilt and brake.
Usage: python research/round75c_carry_on_autopilot.py [slip=0.01] [sig=strong] [sides=both|calls|puts]
"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round73_regime_tilts as R73
import outcomes as O
from fno import portfolio_bt as PB

slip = float(sys.argv[1]) if len(sys.argv) > 1 else 0.01
sig = sys.argv[2] if len(sys.argv) > 2 else "strong"
sides = sys.argv[3] if len(sys.argv) > 3 else "both"


def carry_rows():
    T = pd.read_csv(os.path.join(HERE, f"round75b_trades_{slip}.csv.gz"))
    T = T[(T.sig == sig) & (T.exit == "next_open")]
    if sides != "both":
        T = T[T.side == (1 if sides == "calls" else -1)]
    lot = T.sym.map({"NIFTY": 65, "BANKNIFTY": 30}).values
    pi = T.pin.values * (1 + slip - 0.005)        # replay adds its own impact; keep total slippage at `slip`
    po = np.maximum(T.pout.values * (1 - slip + 0.005), 0.05)
    out = pd.DataFrame({"date": T.date.values, "entry_time": "15:20", "exit_time": "15:29", "sym": T.sym.values,
                        "strategy": f"Overnight carry (r75 {sig})", "side": np.where(T.side > 0, "CALL", "PUT"), "prem_in": pi, "prem_out": po,
                        "lot": lot, "risk_lot": pi * lot, "R": po / pi - 1, "spot_in": np.nan, "spot_out": np.nan, "reason": "r75c", "pieces": 1})
    out["risk_lot_capped"] = out["risk_lot"]
    parts = []
    for s, g in out.groupby("sym"):
        g = g.copy()
        g["cpr_w"] = PB.cpr_widths(O.candles(s)).reindex(g.date).values
        g["score"] = PB._scores(g, O.candles(s))
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def main():
    new = carry_rows()
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    res = {"slip": slip, "sig": sig, "sides": sides, "n_carry": len(new)}
    for cap in (200_000, 20_000):
        ref = {p: R73.run(base.assign(xm=1.0), alt.assign(xm=1.0) if alt is not None else None, cap, a, b) for p, (a, b) in R73.PERIODS.items()}
        Tn = pd.concat([base, new], ignore_index=True).assign(xm=1.0)
        An = pd.concat([alt, new], ignore_index=True).assign(xm=1.0) if alt is not None else None
        add = {p: R73.run(Tn, An, cap, a, b) for p, (a, b) in R73.PERIODS.items()}
        res[f"replay_{cap}"] = {"today": ref, "plus_carry": add}
        for lab, rr in (("Autopilot today", ref), ("+ overnight carry", add)):
            print(f"  Rs {cap:,} {lab:<18} " + " ".join(f"{p}:{rr[p]['final'] / 1e5:6.2f}L" for p in R73.PERIODS) + f" fall {rr['full']['dd']:+.1f}% trades {rr['full']['trades']}", flush=True)
    json.dump(res, open(os.path.join(HERE, f"round75c_results_{sig}_{sides}_{slip}.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
