"""Offline test: round-88 big-account strike (2 OTM from BIG_OTM_FROM equity), its switch, and the portfolio replay tier.
Run: python tests/test_r88.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(port=6428)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)
os.environ.pop("RENDER", None)

import pandas as pd  # noqa: E402
from fno import config as C, portfolio_bt as PB, service as SV, aprules as AR  # noqa: E402

ch = pd.DataFrame({"strike": [22350.0 + 50 * i for i in range(7)]})       # 22350 .. 22650, ATM 22500
ap = {"autopilot": True}
f = SV._small_account_strike
# 1. tiers: small -> 1 OTM, middle -> normal rule (None), big -> 2 OTM; calls up, puts down
assert f({"opt": "CE"}, ch, 22502, ap, 25_000) == 22550.0
assert f({"opt": "CE"}, ch, 22502, ap, 200_000) is None
assert f({"opt": "CE"}, ch, 22502, ap, C.BIG_OTM_FROM - 1) is None
assert f({"opt": "CE"}, ch, 22502, ap, C.BIG_OTM_FROM) == 22600.0
assert f({"opt": "PE"}, ch, 22502, ap, 900_000) == 22400.0
assert f({"opt": "CE"}, ch, 22502, {}, 900_000) is None                    # Autopilot off: untouched
# 2. switch: listed, on by default, off -> normal rule; small-account rule unaffected
row = next(c for c in SV.changes() if c["id"] == "big_otm")
assert row["on"] is True and row["kind"] == "switch"
assert "2 steps out of the money" in str(AR.rulebook())
SV.revert_change("big_otm", False)
assert f({"opt": "CE"}, ch, 22502, ap, 900_000) is None
assert f({"opt": "CE"}, ch, 22502, ap, 25_000) == 22550.0
assert "2 steps out of the money" not in str(AR.rulebook())
SV.revert_change("big_otm", True)
# 3. portfolio replay: hi rows only once equity >= BIG_OTM_FROM; baseline has the rule on
assert PB.BASELINE["big_otm"] is True
assert PB._hi_key({**PB.BASELINE}) == ("current", C.BIG_OTM_STRIKES, True, 0)
assert PB._hi_key({**PB.BASELINE, "big_otm": False}) is None
assert PB._hi_key({**PB.BASELINE, "sizing": "fixed"}) is None


def tr(prem_out):
    return pd.DataFrame([{"date": "2026-10-01", "entry_time": "10:00", "exit_time": "11:00", "sym": "NIFTY", "strategy": "Noise", "side": "CALL",
                          "prem_in": 100.0, "prem_out": prem_out, "lot": 65, "risk_lot": 1300.0, "risk_lot_capped": 1300.0, "R": 1.0,
                          "spot_in": 1, "spot_out": 1, "reason": "x", "score": 3, "cpr_w": 0.3, "pieces": 1}])


base, hi = tr(110.0), tr(150.0)                  # the 2-OTM copy of the same signal is marked by a different exit premium
small = PB.replay(base, 200_000, "compound", 1.0, 1, "none", hi=hi)
big = PB.replay(base, C.BIG_OTM_FROM, "compound", 1.0, 1, "none", hi=hi)
assert float(small.prem_out.iloc[0]) < 120 and float(big.prem_out.iloc[0]) > 140, (small.prem_out.iloc[0], big.prem_out.iloc[0])
print("round 88 big-account strike: OK")
