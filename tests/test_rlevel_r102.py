"""Offline test: round-102 round-level break slot - events, entry timing, stop / trail exit, switch, done-today tracking, Outcomes trades.
Run: python tests/test_rlevel_r102.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(port=6434)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)
os.environ.pop("RENDER", None)

from datetime import timedelta  # noqa: E402
import numpy as np, pandas as pd  # noqa: E402
from fno import config as C, strategy as S, service as SV  # noqa: E402

TZ = "Asia/Kolkata"


def bars15(rows, day="2026-11-16"):
    """rows = (open, high, low, close) per 15-min bar from 09:15; returns 5-min candles (3 per bar, same OHLC path)."""
    out = []
    for n, (o, h, l, c) in enumerate(rows):
        t0 = pd.Timestamp(f"{day} 09:15", tz=TZ) + timedelta(minutes=15 * n)
        out += [(t0, o, h, max(l, min(o, c)), (o + c) / 2), (t0 + timedelta(minutes=5), (o + c) / 2, h, l, (o + c) / 2),
                (t0 + timedelta(minutes=10), (o + c) / 2, max(c, (o + c) / 2), min(c, (o + c) / 2), c)]
    df = pd.DataFrame(out, columns=["ts", "open", "high", "low", "close"]).set_index("ts")
    df["high"] = df[["open", "high", "close"]].max(axis=1); df["low"] = df[["open", "low", "close"]].min(axis=1)
    df["volume"] = 0.0
    return df


# 1. events: 25300 rejected (wick to 25280, close 25260), later broken (close 25340) -> break up with 1 prior rejection
day1 = [(25250, 25260, 25240, 25255), (25255, 25280, 25250, 25260), (25260, 25270, 25240, 25250), (25250, 25290, 25245, 25285),
        (25285, 25345, 25280, 25340)] + [(25340, 25350, 25330, 25345)] * 20
c5 = bars15(day1)
b = S.channel_frame(c5)
ev = S.rlevel_events(b, "NIFTY")
sg = S.rlevel_signals(b, ev)
i_brk = 4
assert ev["brk_u"].iloc[i_brk] == 25300 and ev["brk_u_w"].iloc[i_brk] >= 1, ev.head(6)
assert sg["sig"].iloc[i_brk] == 1 and abs(sg["stop"].iloc[i_brk] - (b["low"].iloc[i_brk] - b["close"].iloc[i_brk] * C.RLEVEL_BUF)) < 1e-6
assert (sg["sig"].iloc[:i_brk] == 0).all()

# a break of a level never rejected this week gives no signal
fresh = bars15([(25250, 25260, 25240, 25255), (25255, 25345, 25250, 25340)] + [(25340, 25350, 25330, 25345)] * 20)
fb = S.channel_frame(fresh)
assert (S.rlevel_signals(fb, S.rlevel_events(fb, "NIFTY"))["sig"] == 0).all()

# 2. live entry: only in the first 5 minutes of the bar after the signal (signal bar 10:15 -> entry bar 10:30)
sig_end = b.index[i_brk] + timedelta(minutes=15)                   # 10:30
now = sig_end + timedelta(minutes=2)
cd = c5[c5.index < now - timedelta(minutes=2)]                       # bars closed up to 10:30
cd = pd.concat([cd, c5[(c5.index >= sig_end) & (c5.index < sig_end + timedelta(minutes=1))]])
cd = cd[cd.index + timedelta(minutes=5) <= now + timedelta(minutes=5)]
e = S.evaluate_rlevel(cd, {"level": 15}, now, "NIFTY")
assert e["plan"] and e["plan"]["opt"] == "CE" and e["plan"]["strategy"] == "rlevel" and e["plan"]["signal_bar"] == "10:15", e
p = e["plan"]
assert abs(p["risk_pts"] - p["r_pts"] / C.RLEVEL_SIZE) < 1e-9 and p["level"] == 25300
late = S.evaluate_rlevel(cd, {"level": 15}, sig_end + timedelta(minutes=C.RLEVEL_ENTRY_MIN + 3), "NIFTY")
assert late["plan"] is None
used = S.evaluate_rlevel(cd, {"level": 15}, now, "NIFTY", {"n": 1, "bars": ["10:15"], "free_from": None})
assert used["plan"] is None
full = S.evaluate_rlevel(cd, {"level": 15}, now, "NIFTY", {"n": C.RLEVEL_PER_DAY, "bars": [], "free_from": None})
assert full["plan"] is None
busy = S.evaluate_rlevel(cd, {"level": 15}, now, "NIFTY", {"n": 1, "bars": [], "free_from": "10:45"})
assert busy["plan"] is None

# 3. exit: index stop; trail follows the best completed 15-min close 1R behind; no round-99 profit trail; square-off
pos = {"opened": now.isoformat(), "plan": p, "sl_prem": 1, "target_prem": 1e9}
st = S.rlevel_state(c5[c5.index < sig_end + timedelta(minutes=90)], sig_end + timedelta(minutes=90), "NIFTY")
r, upd = S.exit_check(pos, p["entry"] + 5, 100, sig_end + timedelta(minutes=90), st)
best = float(st["bars"]["close"][st["bars"].index >= sig_end].max())
if best > p["entry"]:
    assert upd.get("rl_trail") and abs(upd["live_sl"] - round(best - p["r_pts"], 2)) < 1e-6, (upd, best)
assert not upd.get("trail_on")
assert "stop" in (S.exit_check(pos, p["sl"] - 1, 100, now, None)[0] or "").lower()
pos2 = {**pos, "live_sl": p["sl"] + 10, "rl_trail": True}
assert "Trailing stop" in (S.exit_check(pos2, p["sl"] + 5, 100, now, None)[0] or "")
assert S.exit_check(pos, p["entry"], 100, now.replace(hour=15, minute=16), st)[0] == "Square-off 15:15"

# 4. switch: Autopilot only, Nifty only, any equity; listed in Research -> Changes
pf = {"autopilot": True}
assert SV.rlevel_allowed(pf, "NIFTY") and not SV.rlevel_allowed(pf, "SENSEX") and not SV.rlevel_allowed({"autopilot": False}, "NIFTY")
assert any(c["id"] == "rlevel_slot" and c["kind"] == "switch" for c in SV.changes())
SV.revert_change("rlevel_slot", False)
assert not SV.rlevel_allowed(pf, "NIFTY")
SV.revert_change("rlevel_slot", True)
assert SV.rlevel_allowed(pf, "NIFTY")
assert "rlevel" in C.SLOTS and "rlevel" in C.CONVICTION_STRATS and "rlevel" not in C.TRAIL_STRATS
assert C.RLEVEL_MIN_EQUITY == 100000
from fno import portfolio_bt as PB
_row = dict(sym="NIFTY", date="2026-11-16", entry_time="10:31", exit_time="11:30", strategy="Round-level break", side="CALL",
            prem_in=100.0, prem_out=150.0, lot=65, risk_lot=1000.0, risk_lot_capped=1000.0, R=1.0, spot_in=25300.0, spot_out=25350.0,
            reason="t", pieces=1, score=3, cpr_w=float("nan"))
_T = pd.DataFrame([_row])
_r = PB.replay(_T, 20000, "compound", 1.0, 1, "standard", True, 50, False, None)
assert _r["skipped"].iloc[0] == "round-level slot below its equity gate", _r
_r = PB.replay(_T, 200000, "compound", 1.0, 1, "standard", True, 50, False, None)
assert "skipped" not in _r or pd.isna(_r["skipped"].iloc[0]), _r

# 5. done-today: count, used signal bars and the bar the last trade ended in
today = C.today_ist().isoformat()
tr = [{"symbol": "NIFTY", "mode": "signal", "opened": f"{today}T10:31:00+05:30", "closed": f"{today}T10:52:10+05:30",
       "plan": {"strategy": "rlevel", "signal_bar": "10:15"}}]
d = SV._traded_today("NIFTY", [], tr)["rlevel_done"]
assert d == {"n": 1, "bars": ["10:15"], "free_from": "10:45"}, d

print("round-102 round-level slot tests passed")
