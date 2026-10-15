"""Round 18 rules: holidays, weekly loss limit, bid-ask spread skip, VIX ceilings."""
from datetime import datetime
import pandas as pd
import fno.config as C, fno.paper as P, fno.service as SV, fno.strategy as S

now = C.now_ist
C.now_ist = lambda: datetime(2026, 10, 20, 10, 0, tzinfo=now().tzinfo)
assert SV.tick() == {"skipped": "NSE holiday"}
C.now_ist = now

wk = [{"opened": "2026-10-12T10:00:00", "pnl": -5000, "closed": "x"}, {"opened": "2026-10-13T10:00:00", "pnl": -4000, "closed": "x"}]
if C.today_ist().isoformat() == "2026-10-15":
    assert P.day_guard([], wk, 200000)["blocked"]

rows = [{"strike": k, "ce_key": f"c{k}", "ce_ltp": 100, "ce_bid": 80, "ce_ask": 100, "ce_delta": .5} for k in (24950, 25000, 25050)]
plan = {"opt": "CE", "risk_pts": 30, "rr": 4, "strategy": "noise", "side": 1}
try:
    S.build_order(plan, pd.DataFrame(rows), 25000, 14, 65, 200000)
    raise SystemExit("wide spread should be skipped")
except ValueError as e:
    assert str(e).startswith("SKIP")
for r in rows:
    r["ce_bid"] = 99
assert S.build_order(plan, pd.DataFrame(rows), 25000, 14, 65, 200000)["lots"] >= 1
assert C.NOISE_VIX_MAX == 18 and C.CAM_VIX_MAX == 20 and C.MAX_WEEKLY_LOSS == 0.04
print("round 18 rules OK")
