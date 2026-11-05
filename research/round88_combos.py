"""Round 88: combine the parts that passed on their own but were never put together.

Rounds 1-87 tested ~90 ideas. Two passed the Rs 2L bar on their own and were not adopted for a reason that a
combination could fix:
  - r83: the 2-OTM strike alone (Rs 2L full 61.0L vs 40.9L, both halves better, shallower fall) but worse at Rs 20k
    -> "candidate for an equity-gated rule (like the trend slot)". Never tested as a gate.
  - r77: overnight carry as its own slot (1 lot + 2% weekly budget) beat Autopilot in both halves; edge sits on ~10 gap nights.
Here:
  A. Strike tier by equity: below Rs 60k 1 OTM (today's small-account rule), Rs 60k..G 1 ITM (today), from G 2 OTM.
     G = Rs 60k / 1L / 2L / 5L. Same signals, same sizing; only the strike changes with equity (path dependent replay).
  B. A + the r77 carry slot (1 lot, both sides, 2% weekly budget) on the same account.
  C. Stress: +1% slippage per fill on every Autopilot trade, and the 2-OTM gain without its 10 best trades.
Periods 2023-24 and 2025-26 restart from the capital (the bar: better in both halves, worst fall not deeper by > 2 pts,
at Rs 2L AND at Rs 20k, the live account size).
Usage: python research/round88_combos.py
"""
import inspect, json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import outcomes as O
from fno import portfolio_bt as PB
from fno import config as C

PER = {"2023": ("2023-01-02", "2023-12-31"), "2024": ("2024-01-01", "2024-12-31"), "2025": ("2025-01-01", "2025-12-31"),
       "2026": ("2026-01-01", "2026-10-16"), "2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", "2026-10-16"),
       "full": ("2023-01-02", "2026-10-16")}

# --- PB.replay with a third strike tier: rows from `hi` (2 OTM) once equity >= HI_FROM -------------------------------
_src = inspect.getsource(PB.replay)
_src = _src.replace("def replay(", "def replay_tier(", 1)
_src = _src.replace("cpr: bool = False) -> pd.DataFrame:", "cpr: bool = False, hi=None, hi_from: float = 0, slip_add: float = 0) -> pd.DataFrame:", 1)
_src = _src.replace("    eq = peak = float(capital)",
                    "    hi_by = {(a.sym, a.date, a.entry_time, a.strategy): a for a in hi.itertuples(index=False)} if hi is not None and len(hi) else {}\n"
                    "    eq = peak = float(capital)", 1)
_src = _src.replace("            r = alt_by.get((r.sym, r.date, r.entry_time, r.strategy), r)\n",
                    "            r = alt_by.get((r.sym, r.date, r.entry_time, r.strategy), r)\n"
                    "        elif hi_by and eq >= hi_from:\n"
                    "            r = hi_by.get((r.sym, r.date, r.entry_time, r.strategy), r)\n", 1)
_src = _src.replace("imp = IMPACT_K * math.sqrt(qty / VOL5[s])", "imp = IMPACT_K * math.sqrt(qty / VOL5[s]) + slip_add", 1)
assert _src.count("hi_by") == 3 and "slip_add" in _src.split("imp =")[1][:80]
_ns = dict(PB.__dict__)
exec(compile(_src, "replay_tier", "exec"), _ns)
replay_tier = _ns["replay_tier"]


def load():
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    k2 = (k[0], 2, k[2], k[3])
    hi = pd.concat([O.trades(k2, s) for s in O.SYMS], ignore_index=True)
    return B0, base, alt, hi


def replay(base, alt, hi, cap, a, b, hi_from=None, slip_add=0.0):
    q = {**O.B0, "capital": cap}
    f = lambda X: X[(X.date >= a) & (X.date <= b)] if X is not None else None  # noqa: E731
    R = replay_tier(f(base), cap, q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"],
                    q["dd_half"] and q["sizing"] != "fixed", f(alt), cpr=q.get("cpr_size", False),
                    hi=f(hi) if hi_from is not None else None, hi_from=hi_from or 0, slip_add=slip_add)
    return R


def summ(R, cap):
    s = PB._summary(R, cap)
    tk = R[R["skipped"].isna()] if "skipped" in R else R
    return {"final": s["final"], "dd": s["max_dd_pct"], "trades": s["trades"], "top10": float(np.sort(tk.pnl.values)[-10:].sum()) if len(tk) else 0.0,
            "net_ex_top10": float(s["net"] - (np.sort(tk.pnl.values)[-10:].sum() if len(tk) else 0.0))}


# --- carry slot (round 77 variant '1 lot, both, week budget 2%') on top of a main-book equity curve -------------------
LOT = {"NIFTY": 65, "BANKNIFTY": 30}


def with_carry(R, carry, cap, a, b, wk=0.02):
    tk = R[R["skipped"].isna()] if "skipped" in R else R
    eq_main = tk.groupby("date")["equity"].last().astype(float)
    Cc = carry[(carry.date >= a) & (carry.date <= b)]
    days = sorted(set(eq_main.index) | set(Cc.date))
    main = eq_main.reindex(days).ffill().fillna(cap)
    byday = {d: g for d, g in Cc.groupby("date")}
    cum, wk_loss, wk_id, comb, n = 0.0, 0.0, None, [], 0
    for d in days:
        w = pd.Timestamp(d).isocalendar()[:2]
        if w != wk_id:
            wk_id, wk_loss = w, 0.0
        eq = main[d] + cum
        for r in (byday[d].itertuples(index=False) if d in byday else []):
            prem = r.pin * LOT[r.sym]
            if prem > 0.5 * eq or wk_loss >= wk * eq:
                continue
            cum += r.rs; n += 1
            wk_loss += max(-r.rs, 0)
        comb.append(main[d] + cum)
    comb = pd.Series(comb, index=days)
    return {"final": round(float(comb.iloc[-1])), "dd": round(float((comb / comb.cummax() - 1).min() * 100), 1), "carry": round(cum), "carry_n": n}


def main():
    B0, base, alt, hi = load()
    print("trades: base", len(base), "1otm", 0 if alt is None else len(alt), "2otm", len(hi), flush=True)
    T = pd.read_csv(os.path.join(HERE, "round75b_trades_0.01.csv.gz"))
    carry = T[(T.sig == "strong") & (T.exit == "next_open")]
    VERS = {"Autopilot today": None, "2 OTM from Rs 60k": 60_000, "2 OTM from Rs 1L": 100_000, "2 OTM from Rs 2L": 200_000,
            "2 OTM from Rs 5L": 500_000}
    res = {}
    for slip in (0.0, 0.01):
        for cap in (200_000, 20_000):
            for name, g in VERS.items():
                row = {}
                for pn, (a, b) in PER.items():
                    R = replay(base, alt, hi, cap, a, b, hi_from=g, slip_add=slip)
                    s = summ(R, cap)
                    s["carry"] = with_carry(R, carry, cap, a, b) if a >= "2023-01-02" else None
                    row[pn] = s
                res[f"{slip}|{cap}|{name}"] = row
                f = row["full"]
                print(f"slip+{slip:.0%} Rs {cap:>7,} {name:<20} " +
                      " ".join(f"{p}:{row[p]['final'] / 1e5:6.2f}L" for p in ("2023", "2024", "2025", "2026", "2023-24", "2025-26", "full")) +
                      f" | fall {f['dd']:+.1f}% | ex-top10 {f['net_ex_top10'] / 1e5:+.2f}L"
                      f" || +carry 23-24 {row['2023-24']['carry']['final'] / 1e5:.2f}L 25-26 {row['2025-26']['carry']['final'] / 1e5:.2f}L"
                      f" full {f['carry']['final'] / 1e5:.2f}L fall {f['carry']['dd']:+.1f}%", flush=True)
    json.dump(res, open(os.path.join(HERE, "round88_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
