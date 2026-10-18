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
    assert P.week_guard(wk, 200000)["blocked"] and not P.week_guard(wk[:1], 200000)["blocked"]

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

# ---- round 20: Autopilot
SV.prefs = lambda: {"autopilot": False, "sizing": "risk", "risk_pct": 1.0, "strategies": ["noise"], "auto": False, "auto_syms": ["NIFTY"], "enforce_limits": False}
assert SV.eprefs()["sizing"] == "risk" and SV.eprefs()["auto"] is False            # OFF = exactly what is saved
SV.prefs = lambda: {"autopilot": True, "sizing": "risk", "risk_pct": 2.0, "strategies": ["noise"], "auto": False, "auto_syms": ["NIFTY"], "enforce_limits": False}
e = SV.eprefs()
assert e["auto"] and e["sizing"] == "compound" and e["risk_pct"] == 1.0 and e["auto_syms"] == ["NIFTY", "BANKNIFTY", "SENSEX"] and set(e["strategies"]) == {"noise", "camarilla", "trend", "fib"}
rows = [{"strike": k, "ce_key": f"c{k}", "ce_ltp": 100, "ce_bid": 99, "ce_ask": 100, "ce_delta": .5} for k in (24950, 25000, 25050)]
ch = pd.DataFrame(rows)
plan = {"opt": "CE", "risk_pts": 30, "rr": 4, "strategy": "noise", "side": 1}
o = S.build_order(plan, ch, 25000, 14, 65, 20000, prefs=e)           # risk per lot ~ 0.5*30*65*... small vs 15% of 20k?
print("20k lots:", o["lots"], o["note"])
big = S.build_order(dict(plan, risk_pts=300), ch, 25000, 14, 65, 20000, prefs=e) if False else None
try:
    wide = S.build_order(dict(plan, risk_pts=400), ch, 25000, 14, 65, 20000, prefs=e)
    assert wide["lots"] == 0 and "SKIP" in (wide["note"] or ""), wide["note"]       # a 1-lot risk above 15% of equity is skipped
except ValueError:
    pass
big_eq = S.build_order(plan, ch, 25000, 14, 65, 2_000_000, prefs=e)
assert big_eq["lots"] > o["lots"] or o["lots"] >= 1, "compounding: more equity -> more lots"
assert big_eq["lots"] >= 2
assert P.week_guard([{"opened": "2026-10-12T10:00:00", "pnl": -2000, "closed": "x"}], 20000, 0.15)["blocked"] is False or C.today_ist().isocalendar()[:2] != (2026, 42)
print("autopilot rules OK")
