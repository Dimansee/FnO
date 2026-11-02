"""Round 72b: the round-72 opening-candle retest -> top (the user's 2 Nov trade) added to today's Autopilot.
Same Outcomes-tab replay as rounds 63b / 67b / 69b / 70b: today's BASELINE (compounding 1%, conviction, big-run skip,
drawdown brake, gates, CPR-width sizing), new trades priced as ATM options at 0.5% slippage per fill.
Usage: python research/round72b_add_to_autopilot.py [data_dir]
"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data")
import numpy as np, pandas as pd
import round72_opening_candle_retest as M
import round67b_add_to_autopilot as B67
import outcomes as O
from fno import portfolio_bt as PB
M.DATA = M.S.DATA = M.R34.DATA = DATA
SLIP = 0.005

# candidates: (label, signal params, exit, symbols)
CANDS = json.loads(os.environ.get("R72_CANDS", "[]")) or [
    ["user's rule: 5m candle, run 0.15%, exit at top", {"level": "5m", "run": 0.0015, "tol": 0.0005, "green": False, "room": 1.0}, "top", O.SYMS],
    ["user's rule, exit top then trail", {"level": "5m", "run": 0.0015, "tol": 0.0005, "green": False, "room": 1.0}, "top+trail", O.SYMS],
]


def trades(sym, p, ex):
    sg = M.signals(sym, **p)
    T = M.build(sym, sg, ex)
    if T.empty:
        return T
    df = M.load(sym)
    lot = T["lot"].values
    pi = T["prem_in"].values / (1 + M.SLIP) * (1 + SLIP)
    po = np.maximum(T["prem_out"].values / (1 - M.SLIP) * (1 - SLIP), 0.05)
    out = pd.DataFrame({"date": T.date, "entry_time": T.t_in, "exit_time": [min(x, "15:20") for x in T.t_out], "sym": sym,
                        "strategy": "Level break (opening-candle retest r72)", "side": np.where(T.side > 0, "CALL", "PUT"),
                        "prem_in": pi, "prem_out": po, "lot": lot, "risk_lot": np.maximum(T["risk_lot"].values / lot / (1 + M.SLIP) * (1 + SLIP), pi * 0.05) * lot,
                        "R": T["R"].values, "spot_in": T["e"].values, "spot_out": T["xs"].values, "reason": "r72", "pieces": 1})
    out["risk_lot_capped"] = out["risk_lot"]
    cw = PB.cpr_widths(O.candles(sym))
    out["cpr_w"] = cw.reindex(out.date).values
    out["score"] = PB._scores(out, O.candles(sym))
    return out


def run(T, q, start, end, A=None):
    """round 67b's run() plus round 71's CPR-width sizing, so the baseline matches today's Autopilot."""
    T = T[(T.date >= start) & (T.date <= end)]
    if A is not None:
        A = A[(A.date >= start) & (A.date <= end)]
    R = PB.replay(T, q["capital"], q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"],
                  q["dd_half"] and q["sizing"] != "fixed", A, cpr=q.get("cpr_size", False))
    s = PB._summary(R, q["capital"])
    tk = R[R["skipped"].isna()] if "skipped" in R else R
    new = tk[tk.strategy.str.contains("r72")] if len(tk) else tk
    return {"final": s["final"], "net": s["net"], "max_dd_pct": s["max_dd_pct"], "trades": s["trades"],
            "years": {y["year"]: y["ret_pct"] for y in s["years"]}, "new_trades": int(len(new)),
            "new_pnl": float(new.pnl.sum()) if len(new) else 0.0, "skipped": int(R["skipped"].notna().sum()) if "skipped" in R else 0}


def main():
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    cands = {}
    for lab, p, ex, syms in CANDS:
        cands[lab] = pd.concat([trades(s, p, ex) for s in syms], ignore_index=True)
        print(lab, "trades", len(cands[lab]), "own Rs@0.5%slip/lot", round(float(((cands[lab].prem_out - cands[lab].prem_in) * cands[lab].lot).sum())), flush=True)
    P = {"2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", "2026-10-16"), "2023": ("2023-01-02", "2023-12-31"),
         "2024": ("2024-01-01", "2024-12-31"), "2025": ("2025-01-01", "2025-12-31"), "2026": ("2026-01-01", "2026-10-16"),
         "Jan 2023 - Oct 2026": ("2023-01-02", "2026-10-16")}
    res = {}
    for cap in (200_000, 20_000):
        for pn, (a, b) in P.items():
            q = {**B0, "capital": cap}
            rows = {"Autopilot today": run(base, q, a, b, alt)}
            for lab, tr in cands.items():
                Tn = pd.concat([base, tr], ignore_index=True)
                An = pd.concat([alt, tr], ignore_index=True) if alt is not None else None
                rows["+ " + lab] = run(Tn, q, a, b, An)
            res[f"{cap}|{pn}"] = rows
            print(f"\n== Rs {cap:,} | {pn}")
            for lab, r in rows.items():
                print(f"  {lab:<52} final Rs {r['final']:>12,.0f}  fall {r['max_dd_pct']:+.1f}%  trades {r['trades']:>4}  new {r['new_trades']:>3} ({r['new_pnl']:+,.0f})  skipped {r['skipped']}", flush=True)
    json.dump(res, open(os.path.join(HERE, "round72b_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
