"""Round 63b: does adding the round-63 rule (RMI + Supertrend 15-min, MACD + RMI agreeing) to TODAY's Autopilot add profit?

User (29 Oct): "if there is any profit from that rule only then it is applicable otherwise not."
Same harness as the Outcomes tab (research/outcomes.py): fno.portfolio_bt.replay with today's BASELINE (compounding 1% risk,
standard confidence, big-run skip, half risk in a >15% drawdown, gates, weekly limit, free cash, market impact, real charges).
The new rule's trades are priced like round 63 (ATM option, app Black-Scholes calibration + decay adjustment, 0.5% slippage per
fill as the app's backtest) and given the same conviction score, then merged into the baseline trade list.
Runs: Rs 2L and Rs 20k; full history (2 Jan 2023 - 23 Oct 2026) and 2026 alone; new rule on Nifty + Sensex (Bank Nifty lost in
round 63) and on all three.
Usage: python research/round63b_add_to_autopilot.py
"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import outcomes as O
import round62_scalping7 as S
import round63_scalp_combos as C
import round34 as R34
from fno import portfolio_bt as PB

S.DATA = os.path.join(HERE, "data")
SLIP = 0.005


def rmi_trades(sym):
    df = C.load("index", sym, "15m")
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    day, dayend, _, _, _ = S.session_info("index", df.index)
    up, dn = C.states(df)
    for name, side, stop, tgt, exl, exs, cap, allow in S.strategies("index", "5m", df, None):
        if name.startswith("S5"):
            break
    sd = side.copy()
    for f in ("macd", "rmi"):
        sd[(sd == 1) & ~up[f]] = 0
        sd[(sd == -1) & ~dn[f]] = 0
    en, s2, R, b = S.sim(o, h, l, c, sd, stop, tgt, exl, exs, cap, dayend, allow)
    e = o[en]
    xs = e * (1 + s2 * R)
    j = np.minimum(en + b - 1, len(o) - 1)
    T = pd.DataFrame({"i": en, "j": j, "xs": xs, "side": s2, "sl": stop[en - 1]})
    vix1 = pd.read_csv(os.path.join(S.DATA, "INDIAVIX_1m_upstox.csv.gz"), parse_dates=["ts"]).set_index("ts").close
    vix1 = vix1[~vix1.index.duplicated()].sort_index()
    p_in, p_out, risk, dte = R34.price(sym, {"idx": df.index, "c": o}, T, vix1)
    lot = O.D.lot_size(sym)
    idx = df.index
    exit_ts = idx[j] + pd.Timedelta(minutes=15)
    out = pd.DataFrame({
        "date": [x.strftime("%Y-%m-%d") for x in idx[en]], "entry_time": [x.strftime("%H:%M") for x in idx[en]],
        "exit_time": [min(x, x.normalize() + pd.Timedelta(hours=15, minutes=20)).strftime("%H:%M") for x in exit_ts],
        "sym": sym, "strategy": "RMI+Supertrend 15m", "side": np.where(s2 > 0, "CALL", "PUT"),
        "prem_in": p_in * (1 + SLIP), "prem_out": np.maximum(p_out * (1 - SLIP), 0.05), "lot": lot,
        "risk_lot": np.maximum(risk * (1 + SLIP), p_in * 0.05) * lot, "R": R / np.maximum(np.abs(e - stop[en - 1]) / e, 1e-9),
        "spot_in": e, "spot_out": xs, "reason": "rmi15", "pieces": 1})
    out["risk_lot_capped"] = out["risk_lot"]
    out["score"] = PB._scores(out, O.candles(sym))
    return out


def run(T, q, start, end, A=None):
    T = T[(T.date >= start) & (T.date <= end)]
    if A is not None:
        A = A[(A.date >= start) & (A.date <= end)]
    R = PB.replay(T, q["capital"], q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"],
                  q["dd_half"] and q["sizing"] != "fixed", A)
    s = PB._summary(R, q["capital"])
    tk = R[R["skipped"].isna()] if "skipped" in R else R
    new = tk[tk.strategy == "RMI+Supertrend 15m"] if len(tk) else tk
    return {"final": s["final"], "net": s["net"], "max_dd_pct": s["max_dd_pct"], "trades": s["trades"], "pf": s.get("profit_factor"),
            "years": {y["year"]: y["ret_pct"] for y in s["years"]}, "new_trades": int(len(new)),
            "new_pnl": float(new.pnl.sum()) if len(new) else 0.0,
            "skipped": int(R["skipped"].notna().sum()) if "skipped" in R else 0}


def main():
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = {s: O.trades(k, s) for s in O.SYMS}
    alt = {s: O.trades(ak, s) for s in O.SYMS} if ak else None
    new = {s: rmi_trades(s) for s in O.SYMS}
    for s in O.SYMS:
        print(s, "new-rule trades", len(new[s]), flush=True)
    res = {}
    for cap in O.CAPS:
        for per, (a, b) in O.PERIODS.items():
            q = {**B0, "capital": cap}
            Tb = pd.concat(list(base.values()), ignore_index=True)
            Ab = pd.concat(list(alt.values()), ignore_index=True) if alt else None
            rows = {"Autopilot today": run(Tb, q, a, b, Ab)}
            for lab, syms in (("+ RMI 15m on Nifty + Sensex", ["NIFTY", "SENSEX"]), ("+ RMI 15m on all three", O.SYMS)):
                Tn = pd.concat([Tb] + [new[s] for s in syms], ignore_index=True)
                An = pd.concat([Ab] + [new[s] for s in syms], ignore_index=True) if Ab is not None else None
                rows[lab] = run(Tn, q, a, b, An)
            res[f"{cap}|{per}"] = rows
            print(f"\n== start Rs {cap:,} | {per} ({a} to {b})")
            for lab, r in rows.items():
                print(f"  {lab:<30} final Rs {r['final']:>12,.0f}  net {r['net']:>+12,.0f}  worst fall {r['max_dd_pct']:+.1f}%  trades {r['trades']:>4}"
                      f"  new-rule trades {r['new_trades']:>3} (their P&L {r['new_pnl']:+,.0f})  years {r['years']}")
    json.dump(res, open(os.path.join(HERE, "round63b_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
