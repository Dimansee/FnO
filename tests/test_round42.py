"""Offline test: round 42 big-run skip and strong confidence sizing. Run: python tests/test_round42.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np, pandas as pd
from fno import config as C, strategy as S

# 15 sessions of 5-minute bars: quiet days, then a 3-day rally of ~3 daily ATRs
days = pd.bdate_range("2026-09-01", periods=16)
rows, px = [], 22000.0
for i, d in enumerate(days):
    drift = 180.0 if i >= 13 else 0.0
    for k in range(75):
        ts = pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=15 + 5 * k)
        o = px; px = px + drift / 75 + (5 if k % 2 else -5)
        rows.append((ts, o, max(o, px) + 20, min(o, px) - 20, px))
h = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"]).set_index("ts")
run = S.big_run(h)
assert run and run["dir"] == 1 and run["k"] >= C.BIG_RUN_ATR, run
assert S.against_big_run(run, "PE") and not S.against_big_run(run, "CE")
assert not S.against_big_run({**run, "k": 1.2}, "PE") and not S.against_big_run(None, "PE")
assert S.big_run(h[h.index.date < days[5].date()]) is None          # not enough history
print("big run OK:", run)
assert [next(m for lo, m in C.STRONG_CONF if sc >= lo) for sc in range(7)] == [0.5, 0.5, 0.5, 1.0, 1.0, 2.0, 2.0]
print("strong confidence OK")
print("ALL ROUND 42 OK")

# round 43: peak equity for the drawdown half-risk rule
from fno import paper as P  # noqa: E402
tr = [{"pnl": 50000, "closed": "2026-01-02"}, {"pnl": -60000, "closed": "2026-01-05"}, {"pnl": 5000, "closed": "2026-01-06"}]
assert P.peak_equity({"capital_start": 200000}, tr) == 250000 and P.equity({"capital_start": 200000}, tr) == 195000
assert 195000 < (1 - C.DD_HALF_AT) * 250000          # 22% below the peak -> half risk
print("dd half OK")
