"""Offline test: round-106 realised replay. A trade's result counts for sizing, the drawdown brake and the weekly loss limit
only once it has CLOSED (live Autopilot sizes from realised P&L). Run: python tests/test_r106_realised.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from fno import portfolio_bt as PB


def row(date, t_in, t_out, prem_in, prem_out, sym="NIFTY", strategy="Noise band", side="CALL"):
    return {"date": date, "entry_time": t_in, "exit_time": t_out, "sym": sym, "strategy": strategy, "side": side, "score": 3,
            "lot": 65, "risk_lot": 500.0, "risk_lot_capped": 500.0, "prem_in": prem_in, "prem_out": prem_out,
            "spot_in": 25000.0, "spot_out": 25100.0, "R": 1.0, "reason": "Square-off", "pieces": 1, "cpr_w": None}


def lots_of(R, t_in):
    return int(R[(R.entry_time == t_in) & R.skipped.isna()].lots.iloc[0]) if "skipped" in R else int(R[R.entry_time == t_in].lots.iloc[0])


# 1. a big winner still open at the second entry: realised sizes the second trade on the starting capital
T = pd.DataFrame([row("2026-01-05", "09:30", "15:10", 200.0, 600.0),                 # +26k a lot, closes 15:10
                  row("2026-01-05", "10:00", "10:30", 200.0, 210.0, sym="SENSEX", strategy="Camarilla"),
                  row("2026-01-06", "10:00", "10:30", 200.0, 210.0, sym="SENSEX", strategy="Camarilla")])
args = (500_000, "compound", 1.0, 1, "none", False, 0, False, None)
new = PB.replay(T, *args)
old = PB.replay(T, *args, realised=False)
a_new, a_old = new[new.entry_time == "10:00"].lots.tolist(), old[old.entry_time == "10:00"].lots.tolist()
assert a_new[0] < a_old[0], (a_new, a_old)            # same-day entry: old replay already counted the open winner
assert a_new[1] == a_old[1], (a_new, a_old)           # next day the winner has closed: both agree
assert abs(new.equity.iloc[-1] - old.equity.iloc[-1]) < 0.6 * abs(old.equity.iloc[-1] - 500_000)   # totals reported either way
print("open winner not used for sizing until it closes OK")

# 2. weekly limit: a loser still open does not block; once closed it does
L = pd.DataFrame([row("2026-01-05", "09:30", "15:10", 300.0, 10.0),                  # big loser, closes 15:10 Monday
                  row("2026-01-05", "11:00", "11:30", 200.0, 210.0, sym="SENSEX", strategy="Camarilla"),
                  row("2026-01-06", "11:00", "11:30", 200.0, 210.0, sym="SENSEX", strategy="Camarilla")])
args2 = (200_000, "compound", 1.0, 1, "none", False, 0, False, None)
n2, o2 = PB.replay(L, *args2), PB.replay(L, *args2, realised=False)
sk = lambda R, d: R[(R.date == d) & (R.entry_time == "11:00")].get("skipped").iloc[0] if "skipped" in R else None
assert pd.isna(sk(n2, "2026-01-05")), n2                  # realised: Monday's loser is still open at 11:00 -> trade taken
assert sk(o2, "2026-01-05") == "weekly loss limit", o2     # old replay blocked it on a result not yet known
assert sk(n2, "2026-01-06") == "weekly loss limit", n2     # Tuesday: the loss is realised -> blocked
print("weekly limit counts only closed trades OK")
print("ALL R106 REALISED OK")
