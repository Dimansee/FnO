"""Round 99b (11 Nov 2026): full backtest of today's live Autopilot (profit trail included) on all data, 2 Jan 2023 - 5 Nov 2026,
against the rules before round 99. Outcomes engine, Rs 2L and Rs 20k: every year and both halves (each restarts from the
capital) and one continuous run over the whole period.
Usage: python research/round99b_full_backtest.py
"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import outcomes as O

END = "2026-11-05"
PER = {"2023": ("2023-01-02", "2023-12-31"), "2024": ("2024-01-01", "2024-12-31"), "2025": ("2025-01-01", "2025-12-31"),
       "2026": ("2026-01-01", END), "2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", END), "full": ("2023-01-02", END)}
KEEP = ("final", "max_dd_pct", "trades", "win_rate", "profit_factor", "losing_months", "months", "worst_month", "best_month", "cagr_pct")


def table(cache):
    O.CACHE = cache; O._cand.clear() if hasattr(O._cand, "clear") else None
    out = {}
    for cap in (200_000, 20_000):
        out[str(cap)] = {}
        for p, (a, b) in PER.items():
            r = O.run_cfg({**O.B0, "capital": cap}, a, b)
            out[str(cap)][p] = {k: r.get(k) for k in KEEP}
    return out


def main():
    res = {"before_r99": table("/tmp/outcomes_trades_pre_r99"), "today_live": table("/tmp/outcomes_trades")}
    for cap in ("200000", "20000"):
        print(f"\nRs {int(cap):,}")
        for p in PER:
            a, b = res["before_r99"][cap][p], res["today_live"][cap][p]
            print(f"  {p:<8} before {a['final'] / 1e5:9.2f}L ({a['max_dd_pct']:+.1f}%)  now {b['final'] / 1e5:9.2f}L ({b['max_dd_pct']:+.1f}%) "
                  f"trades {b['trades']} win {b['win_rate']}% PF {b['profit_factor']} losing months {b['losing_months']}/{b['months']} "
                  f"worst month {b['worst_month']} cagr {b['cagr_pct']}", flush=True)
    json.dump(res, open(os.path.join(HERE, "round99b_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
