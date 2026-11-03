"""Round 74: the capital shield (31 Oct defaults) on today's Autopilot, at the account sizes it actually runs at (3 Nov 2026).

Why: since 31 Oct the shield refuses any automatic trade whose premium would take open premium above 50% of equity.
At the live equity (Rs 24,130 on 2 Nov) one lot of a Bank Nifty 1-ITM monthly option (~Rs 13-18k) is always refused and
Nifty/Sensex weeklies often are. The Outcomes backtest never had this cap (only free cash, the 15% planned-loss cap and the
weekly limit), so its numbers did not describe the live system. This round replays today's rules with the shield added.

Shield variants (premium cap = open premium incl. this trade <= x% of equity; floor = no entries below y% of the start):
  none            today's backtest (free cash only)
  live            open 50%, floor 60%            <- what runs since 31 Oct
  open 70 / 100   open 70% / 100% (=cash), floor 60%
  floor only      no premium cap, floor 60%
  1-lot exception open 50%, but 1 lot is allowed while its planned loss fits the 15% cap (the cap only trims extra lots)
Capitals: Rs 20k, 25k, 50k, 1L, 2L. Periods: each year 2023-2026 and the full Jan 2023 - Oct 2026 (compounding, all
of today's rules incl. CPR sizing, 1 OTM below Rs 60k).
Usage: python research/round74_capital_shield.py [data_dir]
"""
import inspect, json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import pandas as pd
import round73_regime_tilts as R73          # offline lot sizes, PERIODS
import outcomes as O
from fno import portfolio_bt as PB

_src = inspect.getsource(PB.replay).replace("def replay(", "def replay_sh(", 1)
_src = _src.replace("alt: pd.DataFrame | None = None, cpr: bool = False)", "alt: pd.DataFrame | None = None, cpr: bool = False, sh: dict | None = None)", 1)
# floor: no new entries below floor% of the starting capital
_src = _src.replace("        if blocked:\n            why = \"weekly loss limit\"",
                    "        if blocked:\n            why = \"weekly loss limit\"\n"
                    "        elif sh and sh.get('floor') and eq < sh['floor'] / 100 * float(capital):\n            why = 'shield floor'", 1)
# premium cap: open premium (this trade included) <= open% of equity; optional 1-lot exception
_src = _src.replace("            free = eq - sum(p[1] for p in open_pos)",
                    "            if sh and sh.get('open'):\n"
                    "                room = sh['open'] / 100 * eq - sum(p[1] for p in open_pos)\n"
                    "                cap_l = int(max(room, 0) // max(r.prem_in * lot, 1e-9))\n"
                    "                if cap_l < 1 and sh.get('one_lot'):\n"
                    "                    cap_l = 1\n"
                    "                lots = min(lots, cap_l)\n"
                    "                if lots <= 0:\n"
                    "                    why = 'shield premium cap'\n"
                    "            free = eq - sum(p[1] for p in open_pos)", 1)
assert "shield floor" in _src and "shield premium cap" in _src
exec(compile(_src, "replay_sh", "exec"), PB.__dict__)

VARIANTS = {
    "none (backtest so far)": None,
    "live: open 50%, floor 60%": {"open": 50, "floor": 60},
    "open 70%, floor 60%": {"open": 70, "floor": 60},
    "open 100% (cash), floor 60%": {"open": 100, "floor": 60},
    "floor 60% only": {"floor": 60},
    "open 50% + 1-lot exception, floor 60%": {"open": 50, "floor": 60, "one_lot": True},
}
CAPS = (20_000, 25_000, 50_000, 100_000, 200_000)


def run(T, A, cap, a, b, sh):
    q = {**O.B0, "capital": cap}
    T = T[(T.date >= a) & (T.date <= b)]
    A = A[(A.date >= a) & (A.date <= b)] if A is not None else None
    R = PB.replay_sh(T, cap, q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"], q["dd_half"], A, cpr=True, sh=sh)
    s = PB._summary(R, cap)
    sk = R["skipped"].value_counts().to_dict() if "skipped" in R else {}
    return {"final": round(s.get("final", cap)), "dd": s.get("max_dd_pct", 0), "trades": s.get("trades", 0),
            "shield_skips": int(sk.get("shield premium cap", 0)) + int(sk.get("shield floor", 0)), "skips": {k: int(v) for k, v in sk.items()}}


def main():
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    T = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    A = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    res = {}
    for cap in CAPS:
        print(f"\n== start Rs {cap:,}")
        for lab, sh in VARIANTS.items():
            r = {p: run(T, A, cap, a, b, sh) for p, (a, b) in R73.PERIODS.items()}
            res[f"{cap}|{lab}"] = r
            print(f"  {lab:<40} " + " ".join(f"{p}:{r[p]['final']/1e5:6.2f}L" for p in R73.PERIODS) +
                  f" | fall {r['full']['dd']:+.1f}% trades {r['full']['trades']} shield-skips {r['full']['shield_skips']}", flush=True)
    json.dump(res, open(os.path.join(HERE, "round74_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
