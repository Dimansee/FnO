"""Offline test (19 Nov 2026): Muhurat / special short sessions are not "yesterday" in the backtester's CPR widths, and the
journal labels a signal from a rule Autopilot does not use on that index. Run: python tests/test_muhurat_levels_r102b.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from datetime import date
import pandas as pd
from fno import config as C, portfolio_bt as PB

C.MUHURAT_DAYS = set(C.MUHURAT_DAYS) | {date(2023, 11, 12)}
rows = []
for d, n, base in (("2023-11-10", 75, 19400.0), ("2023-11-12", 12, 19520.0), ("2023-11-13", 75, 19450.0)):
    ts = pd.date_range(f"{d} 09:15" if n == 75 else f"{d} 18:15", periods=n, freq="5min", tz="Asia/Kolkata")
    for i, t in enumerate(ts):
        rows.append((t, base, base + 30 + (i % 3), base - 30 - (i % 2), base + 1))
c = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"]).set_index("ts")
w = PB.cpr_widths(c)
assert "2023-11-12" not in w.index, w
fri = c[c.index.date == date(2023, 11, 10)]
H, L, Cl = fri.high.max(), fri.low.min(), fri.close.iloc[-1]
p = (H + L + Cl) / 3; bc = (H + L) / 2
assert abs(w["2023-11-13"] - abs(2 * p - 2 * bc) / p * 100) < 1e-9, w      # Monday's CPR comes from Friday, not the Muhurat hour

src = open(os.path.join(os.path.dirname(__file__), "..", "fno", "journal.py")).read()
assert "Rule not used on this index (round 24/29)" in src and "_rule_not_used" in src
assert C.AUTOPILOT_SYM_STRATEGIES["SENSEX"] and "camarilla" not in C.AUTOPILOT_SYM_STRATEGIES["SENSEX"]
print("Muhurat prior-day and relabel tests passed")
