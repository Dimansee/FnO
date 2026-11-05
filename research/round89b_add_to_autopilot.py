"""Round 89b: Channel breakout + RSI versions that were positive in both halves on Nifty and Sensex (picked after looking,
a generous test) added to today's Autopilot - same replay as rounds 72b / 87b. Rs 2L and Rs 20k.
Usage: python research/round89b_add_to_autopilot.py [data_dir]"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round89_channel_rsi as R89
import round87b_add_to_autopilot as B87
import round72b_add_to_autopilot as B72
import outcomes as O
from fno import portfolio_bt as PB
M = R89.M
CANDS = [["5m channel 5 + RSI 60/40", "5m", "ch|5|60/40"], ["5m channel 20, no RSI", "5m", "ch|20|none"],
         ["5m channel 20 + RSI 60/40", "5m", "ch|20|60/40"], ["15m channel 20, no RSI", "15m", "ch|20|none"],
         ["15m channel 20 + RSI 50", "15m", "ch|20|50"]]
SYMS = ["NIFTY", "SENSEX"]


def trades(sym, tf, name, lab):
    T = M.run(sym, tf, name)
    lot = T["lot"].values
    pi = T.prem_in.values * 1.005; po = np.maximum(T.prem_out.values * 0.995, 0.05)
    risk = np.abs(T.ep.values - T.sl.values)
    out = pd.DataFrame({"date": T.date, "entry_time": T.t_in, "exit_time": [min(x, "15:20") for x in T.t_out], "sym": sym,
                        "strategy": f"{lab} r89", "side": np.where(T.side > 0, "CALL", "PUT"), "prem_in": pi, "prem_out": po, "lot": lot,
                        "risk_lot": np.maximum(T.risk.values * 1.005, pi * 0.05) * lot, "R": T.pts.values / np.maximum(risk, 1e-9),
                        "spot_in": T.ep.values, "spot_out": T["xs"].values, "reason": "r89", "pieces": 1})
    out["risk_lot_capped"] = out["risk_lot"]
    out["cpr_w"] = PB.cpr_widths(O.candles(sym)).reindex(out.date).values
    out["score"] = PB._scores(out, O.candles(sym))
    return out


def main():
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    cands = {lab: pd.concat([trades(s, tf, nm, lab) for s in SYMS], ignore_index=True) for lab, tf, nm in CANDS}
    P = {"2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", "2026-10-16"), "2026": ("2026-01-01", "2026-10-16"),
         "Jan 2023 - Oct 2026": ("2023-01-02", "2026-10-16")}
    res = {}
    for cap in (200_000, 20_000):
        for pn, (a, b) in P.items():
            q = {**B0, "capital": cap}
            rows = {"Autopilot today": B72.run(base, q, a, b, alt)}
            for lab, tr in cands.items():
                Tn = pd.concat([base, tr], ignore_index=True); An = pd.concat([alt, tr], ignore_index=True) if alt is not None else None
                R = PB.replay(Tn[(Tn.date >= a) & (Tn.date <= b)], q["capital"], q["sizing"], q["risk_pct"], q["lots"], q["confidence"],
                              q["gates"], q["max_lots"], q["dd_half"] and q["sizing"] != "fixed",
                              An[(An.date >= a) & (An.date <= b)] if An is not None else None, cpr=q.get("cpr_size", False))
                s = PB._summary(R, q["capital"])
                tk = R[R["skipped"].isna()] if "skipped" in R else R
                new = tk[tk.strategy.str.contains("r89")] if len(tk) else tk
                rows["+ " + lab] = {"final": s["final"], "max_dd_pct": s["max_dd_pct"], "trades": s["trades"],
                                    "new_trades": int(len(new)), "new_pnl": float(new.pnl.sum()) if len(new) else 0.0}
            res[f"{cap}|{pn}"] = rows
            print(f"\n== Rs {cap:,} | {pn}")
            for lab, r in rows.items():
                print(f"  {lab:<34} final Rs {r['final']:>12,.0f}  fall {r['max_dd_pct']:+.1f}%  trades {r['trades']:>5}  "
                      f"new {r.get('new_trades', 0):>4} ({r.get('new_pnl', 0):+,.0f})", flush=True)
    json.dump(res, open(os.path.join(HERE, "round89b_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
