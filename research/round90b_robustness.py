"""Round 90b: is round 90's pick (15m 20-bar channel + RSI 50, Sensex, half size) real or a lucky outlier?

Baseline = today's live Autopilot INCLUDING round 88 (2 OTM from Rs 5L equity). Neighbours: channel 15 / 25 bars, no RSI,
full / quarter size, Nifty + Sensex, Nifty alone. Every year, both halves, full period at Rs 2L; at Rs 20k also with an
equity gate (the piece trades only once the account is >= Rs 1L / 2L / 5L, like the trend slot's Rs 40k gate).
Usage: python research/round90b_robustness.py [data_dir]
"""
import inspect, os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round89b_add_to_autopilot as B89
import outcomes as O
from fno import portfolio_bt as PB

_src = inspect.getsource(PB.replay).replace("def replay(", "def replay_gate(", 1)
_src = _src.replace("hi: pd.DataFrame | None = None) -> pd.DataFrame:", "hi: pd.DataFrame | None = None, gate: float = 0.0) -> pd.DataFrame:", 1)
_src = _src.replace('            why = "Fibonacci slot below Rs 2 lakh"\n',
                    '            why = "Fibonacci slot below Rs 2 lakh"\n'
                    '        elif "r89" in str(r.strategy) and eq < gate:\n'
                    '            why = "channel piece below its equity gate"\n', 1)
assert "channel piece below" in _src
_ns = dict(PB.__dict__); exec(compile(_src, "replay_gate", "exec"), _ns); replay_gate = _ns["replay_gate"]

PER = {"2023": ("2023-01-02", "2023-12-31"), "2024": ("2024-01-01", "2024-12-31"), "2025": ("2025-01-01", "2025-12-31"),
       "2026": ("2026-01-01", "2026-10-16"), "2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", "2026-10-16"),
       "full": ("2023-01-02", "2026-10-16")}
VARS = [("PICK: Sensex ch20+RSI50 half", [("SENSEX", "ch|20|50")], 2),
        ("Sensex ch20+RSI50 full", [("SENSEX", "ch|20|50")], 1),
        ("Sensex ch20+RSI50 quarter", [("SENSEX", "ch|20|50")], 4),
        ("Sensex ch20 no RSI half", [("SENSEX", "ch|20|none")], 2),
        ("Sensex ch15+RSI50 half", [("SENSEX", "ch|15|50")], 2),
        ("Sensex ch25+RSI50 half", [("SENSEX", "ch|25|50")], 2),
        ("Nifty ch20+RSI50 half", [("NIFTY", "ch|20|50")], 2),
        ("Nifty+Sensex ch20+RSI50 half", [("NIFTY", "ch|20|50"), ("SENSEX", "ch|20|50")], 2)]


def main():
    out = []
    log = lambda s: (print(s, flush=True), out.append(s))
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    hi = pd.concat([O.trades((k[0], 2, k[2], k[3]), s) for s in O.SYMS], ignore_index=True)

    def run(T, A, cap, per, gate=0.0):
        a, b = PER[per]; q = {**B0, "capital": cap}
        f = lambda X: X[(X.date >= a) & (X.date <= b)] if X is not None else None  # noqa: E731
        R = replay_gate(f(T), cap, q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"],
                        q["dd_half"] and q["sizing"] != "fixed", f(A), cpr=q.get("cpr_size", False), hi=f(hi), gate=gate)
        s = PB._summary(R, cap)
        tk = R[R["skipped"].isna()] if "skipped" in R else R
        new = tk[tk.strategy.str.contains("r89")]
        return s["final"], s["max_dd_pct"], len(new), float(new.pnl.sum())

    res = {}
    for cap in (200_000, 20_000):
        log(f"\n===== Rs {cap:,} (baseline = today's Autopilot incl. round 88)")
        b = {p: run(base, alt, cap, p) for p in PER}
        res[f"{cap}|today"] = b
        log(f"  {'Autopilot today':<34} " + "  ".join(f"{p} {v[0] / 1e5:6.2f}L({v[1]:+.0f}%)" for p, v in b.items()))
        gates = (0.0,) if cap == 200_000 else (0.0, 100_000.0, 200_000.0, 500_000.0)
        for lab, parts, div in VARS:
            P = pd.concat([B89.trades(s, "15m", nm, lab) for s, nm in parts], ignore_index=True)
            P["risk_lot"] *= div; P["risk_lot_capped"] *= div
            T = pd.concat([base, P], ignore_index=True); A = pd.concat([alt, P], ignore_index=True) if alt is not None else None
            for g in gates:
                if cap == 20_000 and not lab.startswith(("PICK", "Nifty+Sensex", "Sensex ch20 no")) and g:
                    continue
                r = {p: run(T, A, cap, p, g) for p in PER}
                res[f"{cap}|{lab}|{g}"] = r
                ok = all(r[p][0] > b[p][0] for p in ("2023-24", "2025-26", "full")) and r["full"][1] >= b["full"][1] - 2 \
                    and r["2023-24"][1] >= b["2023-24"][1] - 2 and r["2025-26"][1] >= b["2025-26"][1] - 2
                yrs = sum(r[p][0] > b[p][0] for p in ("2023", "2024", "2025", "2026"))
                log(f"  {lab + (f' gate {g / 1e5:.0f}L' if g else ''):<34} " + "  ".join(f"{p} {v[0] / 1e5:6.2f}L({v[1]:+.0f}%)" for p, v in r.items())
                    + f"  | years better {yrs}/4 | {'PASS' if ok else 'fail'} | piece trades {r['full'][2]} ({r['full'][3]:+,.0f})")
    json.dump(res, open(os.path.join(HERE, "round90b_results.json"), "w"), indent=1, default=str)
    open(os.path.join(HERE, "round90b_log.txt"), "w").write("\n".join(out))


if __name__ == "__main__":
    main()
