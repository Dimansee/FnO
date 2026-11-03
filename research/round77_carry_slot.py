"""Round 77: the overnight carry (round 75b) as a SEPARATE slot with its own budget, next to today's Autopilot.

Round 75c added the carry inside Autopilot's book, where carry losses counted toward the weekly loss limit and blocked
main trades (full period 35.9L vs 40.9L at Rs 2L). Here the main book is replayed exactly as today (BASELINE incl. CPR
sizing, Outcomes-tab replay) and is never touched by the carry; the carry slot runs beside it on the same account:
  - signal: round-75b 'strong' close (>=0.5% day move and close in the top/bottom 20% of the range at 15:15), buy ATM
    weekly option at 15:20, sell at next day's open; real NSE option prices (Nifty + Bank Nifty, Oct 2023 onward);
    slippage 1% or 2% each side + charges (already in round75b 'rs').
  - account guard: skip if one lot's premium > 50% of the combined equity (same as the live capital shield).
  - own weekly budget: after carry losses reach X% of equity in a week, no more carry that week.
  - sizing: fixed 1 lot, or lots = floor(K% of equity / premium of one lot), min 1.
Combined equity = main book equity + cumulative carry P&L. Worst fall is measured on the combined daily equity.
Usage: python research/round77_carry_slot.py
"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round73_regime_tilts as R73
import outcomes as O
from fno import portfolio_bt as PB

LOT = {"NIFTY": 65, "BANKNIFTY": 30}
PER = {"2023": ("2023-01-02", "2023-12-31"), "2024": ("2024-01-01", "2024-12-31"), "2025": ("2025-01-01", "2025-12-31"),
       "2026": ("2026-01-01", "2026-10-30"), "2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", "2026-10-30"),
       "full": ("2023-01-02", "2026-10-30")}
VARIANTS = {
    "1 lot, both sides":            dict(sides="both", size="fixed", k=0, wk=None),
    "1 lot, both, week budget 2%":  dict(sides="both", size="fixed", k=0, wk=0.02),
    "1 lot, calls only":            dict(sides="calls", size="fixed", k=0, wk=None),
    "5% of equity, both, wk 3%":    dict(sides="both", size="pct", k=0.05, wk=0.03),
}


def main_equity(base, alt, cap, a, b):
    q = {**O.B0, "capital": cap}
    T = base[(base.date >= a) & (base.date <= b)].assign(xm=1.0)
    A = alt[(alt.date >= a) & (alt.date <= b)].assign(xm=1.0) if alt is not None else None
    R = R73.replay_x(T, cap, q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"], q["dd_half"], A, cpr=True)
    if "skipped" in R:
        R = R[R["skipped"].isna()]
    return R.groupby("date")["equity"].last().astype(float)


def run(base, alt, carry, cap, a, b, v):
    eq_main = main_equity(base, alt, cap, a, b)
    C = carry[(carry.date >= a) & (carry.date <= b)]
    if v["sides"] != "both":
        C = C[C.side == (1 if v["sides"] == "calls" else -1)]
    days = sorted(set(eq_main.index) | set(C.date))
    main = eq_main.reindex(days).ffill().fillna(cap)
    byday = {d: g for d, g in C.groupby("date")}
    cum, wk_loss, wk_id, n, wins, pnl_list = 0.0, 0.0, None, 0, 0, []
    comb = []
    for d in days:
        w = pd.Timestamp(d).isocalendar()[:2]
        if w != wk_id:
            wk_id, wk_loss = w, 0.0
        eq = main[d] + cum
        if d in byday:
            for r in byday[d].itertuples(index=False):
                prem = r.pin * LOT[r.sym]
                if prem > 0.5 * eq:
                    continue
                if v["wk"] is not None and wk_loss >= v["wk"] * eq:
                    continue
                lots = 1 if v["size"] == "fixed" else max(1, int(v["k"] * eq // prem))
                p = r.rs * lots
                cum += p; n += 1; wins += p > 0; pnl_list.append(p)
                if p < 0:
                    wk_loss += -p
        comb.append(main[d] + cum)
    comb = pd.Series(comb, index=days)
    dd = float((comb / comb.cummax() - 1).min() * 100)
    mdd = float((main / main.cummax() - 1).min() * 100)
    return dict(final=round(float(comb.iloc[-1])), main_final=round(float(main.iloc[-1])), carry=round(cum), n=n,
                win=round(wins / n, 3) if n else None, dd=round(dd, 1), main_dd=round(mdd, 1),
                top10=round(float(np.sort(pnl_list)[-10:].sum())) if n else 0)


def main():
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    res = {}
    for slip in (0.01, 0.02):
        T = pd.read_csv(os.path.join(HERE, f"round75b_trades_{slip}.csv.gz"))
        carry = T[(T.sig == "strong") & (T.exit == "next_open")]
        for cap in (200_000, 20_000):
            for name, v in VARIANTS.items():
                rr = {p: run(base, alt, carry, cap, a, b, v) for p, (a, b) in PER.items()}
                res[f"{slip}|{cap}|{name}"] = rr
                print(f"slip {slip:.0%} Rs {cap:>7,} {name:<30} " + " ".join(
                    f"{p}:{rr[p]['final'] / 1e5:.2f}L(base {rr[p]['main_final'] / 1e5:.2f}L)" for p in ("2023-24", "2025-26", "full"))
                      + f" | fall {rr['full']['dd']:+.1f}% vs {rr['full']['main_dd']:+.1f}% | carry {rr['full']['n']} trades"
                      f" Rs {rr['full']['carry']:+,} top10 Rs {rr['full']['top10']:+,}", flush=True)
    json.dump(res, open(os.path.join(HERE, "round77_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
