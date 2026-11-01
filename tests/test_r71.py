"""Offline test: round-71 CPR-width sizing (1.2x narrow, 0.85x middle, wide days skipped) and its switch. Run: python tests/test_r71.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(port=6427)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)
os.environ.pop("RENDER", None)

import pandas as pd  # noqa: E402
from fno import config as C, portfolio_bt as PB, service as SV, strategy as S  # noqa: E402


def day(d, h, l, c):
    ix = pd.date_range(f"{d} 09:15", periods=3, freq="5min")
    return pd.DataFrame({"open": [c, c, c], "high": [h, c, c], "low": [l, c, c], "close": [c, c, c], "volume": [0, 0, 0]}, index=ix)


# 1. width and multiplier
narrow = day("2026-10-29", 24020, 23980, 24000)          # tiny range -> narrow CPR
wide = day("2026-10-29", 24600, 23400, 24550)            # 5% range, close at the top -> wide CPR
w_n, w_w = S.cpr_width(narrow), S.cpr_width(wide)
assert w_n < C.CPR_NARROW and w_w > C.CPR_WIDE, (w_n, w_w)
assert S.cpr_tilt(w_n) == 1.2 and S.cpr_tilt(w_w) == 0.0 and S.cpr_tilt(0.3) == 0.85 and S.cpr_tilt(None) is None
# only the LAST session in history counts
hist = pd.concat([wide, day("2026-10-30", 24020, 23980, 24000)])
assert S.cpr_width(hist) < C.CPR_NARROW
# 2. backtest helper gives the previous session's width to the next date
cw = PB.cpr_widths(pd.concat([day("2026-10-29", 24600, 23400, 24550), day("2026-10-30", 24020, 23980, 24000)]))
assert pd.isna(cw["2026-10-29"]) and cw["2026-10-30"] > C.CPR_WIDE
# 3. live helper: autopilot only, switchable
g = {"candles": pd.concat([wide, day("2026-10-30", 24100, 24000, 24050)]), "now": pd.Timestamp("2026-10-30 10:00")}
r = SV._cpr_size("NIFTY", g, {"autopilot": True})
assert r and r["mult"] == 0.0, r
assert SV._cpr_size("NIFTY", g, {"autopilot": False}) is None
row = next(c for c in SV.changes() if c["id"] == "cpr_size")
assert row["on"] is True and row["kind"] == "switch"
SV.revert_change("cpr_size", False)
assert SV._cpr_size("NIFTY", g, {"autopilot": True}) is None
SV.revert_change("cpr_size", True)
assert SV._cpr_size("NIFTY", g, {"autopilot": True})["mult"] == 0.0
# 4. replay: a wide day is skipped, a narrow day gets 1.2x
T = pd.DataFrame([{"date": "2026-10-30", "entry_time": "10:00", "exit_time": "11:00", "sym": "BANKNIFTY", "strategy": "Noise", "side": "CALL",
                   "prem_in": 100.0, "prem_out": 120.0, "lot": 30, "risk_lot": 600.0, "risk_lot_capped": 600.0, "score": 3, "cpr_w": 0.8,
                   "pnl": 0, "R": 1.0, "spot_in": 1, "spot_out": 1, "reason": "x", "pieces": 1}])
R = PB.replay(T, 200_000, "compound", 1.0, 1, "standard", True, 50, False, None, cpr=True)
assert R["skipped"].iloc[0] == "wide CPR day (round 71)"
T.loc[0, "cpr_w"] = 0.1
R1 = PB.replay(T, 200_000, "compound", 1.0, 1, "standard", True, 50, False, None, cpr=True)
R0 = PB.replay(T, 200_000, "compound", 1.0, 1, "standard", True, 50, False, None, cpr=False)
assert int(R1["lots"].iloc[0]) == 4 and int(R0["lots"].iloc[0]) == 3, (R1["lots"].iloc[0], R0["lots"].iloc[0])
assert PB.BASELINE["cpr_size"] is True and "CPR-width sizing: False" in PB.differences({**PB.BASELINE, "cpr_size": False})
print("round-71 CPR sizing tests passed")
