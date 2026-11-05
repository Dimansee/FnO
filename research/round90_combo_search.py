"""Round 90 (5 Nov 2026): is there ANY combination of today's new pieces that beats today's Autopilot? (user request)

Pieces = strategy x index from rounds 87 / 89 (TradingView built-ins on 15m, the 15m 20-bar channel with / without RSI 50,
the 5m 5-bar channel + RSI 60/40), each at full or half size (half = Autopilot's risk sizing gives it half the lots).
Honest search: pieces are pre-filtered and the combination is chosen on 2023-24 ONLY (greedy forward selection inside
the Autopilot replay, Rs 2L: add the piece that raises the 2023-24 final most without deepening the worst fall by more
than 2 points; stop when nothing helps; max 5 pieces). Then judged on 2025-26 (never seen by the search), the full
period, and Rs 20k. A second search does the same on 2025-26 to show how much the pick depends on the period.
Usage: python research/round90_combo_search.py [data_dir]
"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round89_channel_rsi as R89
import round89b_add_to_autopilot as B89
import outcomes as O
from fno import portfolio_bt as PB
M = R89.M
PER = {"2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", "2026-10-16"), "full": ("2023-01-02", "2026-10-16")}
SPECS = [("15m", n) for n in ("barupdn", "bb", "channel", "consec", "greedy", "inside", "momentum", "ma2", "volexp", "ch|20|none", "ch|20|50")] + \
        [("5m", "ch|5|60/40")]


def piece(sym, tf, name):
    return B89.trades(sym, tf, name, f"{tf} {name} {sym}")


def replay(T, A, cap, per, B0):
    a, b = PER[per]
    q = {**B0, "capital": cap}
    R = PB.replay(T[(T.date >= a) & (T.date <= b)], q["capital"], q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"],
                  q["max_lots"], q["dd_half"] and q["sizing"] != "fixed", A[(A.date >= a) & (A.date <= b)] if A is not None else None,
                  cpr=q.get("cpr_size", False))
    s = PB._summary(R, q["capital"])
    return {"final": float(s["final"]), "fall": float(s["max_dd_pct"])}


def build(base, alt, chosen, pieces):
    add = [pieces[k].assign(risk_lot=pieces[k].risk_lot * (2 if half else 1), risk_lot_capped=pieces[k].risk_lot_capped * (2 if half else 1))
           for k, half in chosen]
    T = pd.concat([base] + add, ignore_index=True)
    A = pd.concat([alt] + add, ignore_index=True) if alt is not None else None
    return T, A


def search(base, alt, pieces, keys, per, B0, log):
    chosen, best = [], replay(base, alt, 200_000, per, B0)
    floor = best["fall"] - 2.0
    log(f"\n--- search on {per}: Autopilot today {best['final']:,.0f} fall {best['fall']:+.1f}%")
    for step in range(5):
        top = None
        for k in keys:
            if any(k == c[0] for c in chosen):
                continue
            for half in (False, True):
                T, A = build(base, alt, chosen + [(k, half)], pieces)
                r = replay(T, A, 200_000, per, B0)
                if r["fall"] >= floor and r["final"] > best["final"] and (top is None or r["final"] > top[1]["final"]):
                    top = ((k, half), r)
        if top is None:
            log(f"  step {step + 1}: nothing improves {per} without a deeper fall - stop")
            break
        chosen.append(top[0]); best = top[1]
        log(f"  step {step + 1}: + {top[0][0]}{' (half size)' if top[0][1] else ''} -> {best['final']:,.0f} fall {best['fall']:+.1f}%")
    return chosen


def main():
    out = []
    log = lambda s: (print(s, flush=True), out.append(s))
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    pieces = {}
    for tf, name in SPECS:
        for sym in M.SYMS:
            pieces[f"{tf} {name} {sym}"] = piece(sym, tf, name)
    own = {key: (float(((p.prem_out - p.prem_in) * p.lot)[p.date < "2025"].sum()), float(((p.prem_out - p.prem_in) * p.lot)[p.date >= "2025"].sum()))
           for key, p in pieces.items()}
    res = {"own": own}
    for per, other in (("2023-24", "2025-26"), ("2025-26", "2023-24")):
        idx = 0 if per == "2023-24" else 1
        keys = [key for key, v in own.items() if v[idx] > 0]            # pre-filter on the search period only
        log(f"\n{len(keys)} of {len(own)} pieces make money on their own in {per}: {', '.join(keys)}")
        chosen = search(base, alt, pieces, keys, per, B0, log)
        res[f"pick_{per}"] = chosen
        if not chosen:
            continue
        T, A = build(base, alt, chosen, pieces)
        log(f"  judged ({' + '.join(c[0] + (' half' if c[1] else '') for c in chosen)}):")
        for cap in (200_000, 20_000):
            for p in PER:
                b, n = replay(base, alt, cap, p, B0), replay(T, A, cap, p, B0)
                res[f"{per}|{cap}|{p}"] = {"today": b, "combo": n}
                tag = "SEARCHED" if p == per else ("UNSEEN" if p == other else "")
                log(f"    Rs {cap:>7,} {p:<8} today {b['final']:>12,.0f} ({b['fall']:+.1f}%)  combo {n['final']:>12,.0f} ({n['fall']:+.1f}%)  {tag}")
    json.dump(res, open(os.path.join(HERE, "round90_results.json"), "w"), indent=1, default=str)
    open(os.path.join(HERE, "round90_log.txt"), "w").write("\n".join(out))


if __name__ == "__main__":
    main()
