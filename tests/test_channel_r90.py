"""Offline test: round-90 15-min channel slot - bars, entry, stop-and-reverse exit, equity gate / switch, replay gate.
Run: python tests/test_channel_r90.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(port=6433)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)
os.environ.pop("RENDER", None)

from datetime import timedelta  # noqa: E402
import numpy as np, pandas as pd  # noqa: E402
from fno import config as C, strategy as S, service as SV, portfolio_bt as PB, indicators as I  # noqa: E402

TZ = "Asia/Kolkata"


def days(n, start="2026-10-01", drift=0.0, seed=3):
    rng = np.random.default_rng(seed)
    out, px = [], 24000.0
    for d in pd.bdate_range(start, periods=n):
        ix = pd.date_range(f"{d.date()} 09:15", periods=75, freq="5min", tz=TZ)
        c = px + np.cumsum(rng.normal(drift, 6, 75))
        o = np.r_[px, c[:-1]]
        out.append(pd.DataFrame({"open": o, "high": np.maximum(o, c) + 3, "low": np.minimum(o, c) - 3, "close": c, "volume": 0.0}, index=ix))
        px = c[-1]
    return I.add_indicators(pd.concat(out))


# 1. 15-min bars: 25 a session, channel = rolling max / min of 20 bars, RSI in 0-100
c5 = days(6)
b = S.channel_frame(c5)
assert len(b) == 6 * 25 and (b["n"] == 3).all()
assert abs(b["hi"].iloc[40] - b["high"].iloc[21:41].max()) < 1e-9 and abs(b["lo"].iloc[40] - b["low"].iloc[21:41].min()) < 1e-9
assert b["rsi"].dropna().between(0, 100).all()

# 2. live entry: a strong rally on the last day -> CALL above the channel high, sized at half risk, the next one must reverse
up = days(6, drift=0.0)
last = up.index[-1].normalize()
k = up.index >= last + timedelta(hours=11)
up.loc[k, ["open", "high", "low", "close"]] += np.linspace(0, 400, k.sum())[:, None]     # breakout after 11:00
now = up.index[up.index.get_indexer([last + timedelta(hours=12)])[0]] + timedelta(minutes=5)
cd = up[up.index < now]
st = S.channel_state(cd, now)
assert st and st["same_day"] and st["rsi"] > 50
ev = S.evaluate_channel(cd, {"level": 15}, now)
assert ev["plan"] and ev["plan"]["opt"] == "CE" and ev["plan"]["strategy"] == "channel", ev
assert abs(ev["plan"]["risk_pts"] - st["atr"] / C.CHANNEL_SIZE) < 1e-6 and ev["plan"]["sl"] == st["lo"]
assert S.evaluate_channel(cd, {"level": 15}, now, last_side="CE")["plan"] is None              # stop-and-reverse only

# 3. exit: none while the channel holds; out when price breaks the low with RSI < 50; never after 14:45 (held to 15:15)
pos = {"opened": now.isoformat(), "plan": ev["plan"], "sl_prem": 1, "target_prem": 1e9}
spot = float(cd["close"].iloc[-1])
assert S.exit_check(pos, spot, 100, now, st)[0] is None
rev = {**st, "rsi": 40.0, "cur_lo": st["lo"] - 1}
assert "Channel reversed" in (S.exit_check(pos, spot, 100, now, rev)[0] or "")
late = {**rev, "bar_end": rev["bar_end"].replace(hour=15, minute=0)}
assert S.exit_check(pos, spot, 100, now.replace(hour=15, minute=5), late)[0] is None
assert S.exit_check(pos, spot, 100, now.replace(hour=15, minute=16), st)[0] == "Square-off 15:15"
assert not C.CHANNEL_HARD_STOP and S.exit_check(pos, st["lo"] - 50, 100, now, st)[0] is None   # no fixed stop, as tested

# 4. gate + switch: Autopilot only, Nifty / Sensex only, from Rs 5L, switchable
pf = {"autopilot": True}
assert C.CHANNEL_MIN_EQUITY == 500_000
assert SV.channel_allowed(pf, 600_000, "NIFTY") and SV.channel_allowed(pf, 600_000, "SENSEX")
assert not SV.channel_allowed(pf, 600_000, "BANKNIFTY") and not SV.channel_allowed(pf, 499_000, "NIFTY")
assert not SV.channel_allowed({"autopilot": False}, 500_000, "NIFTY")
assert any(c["id"] == "channel_slot" and c["kind"] == "switch" for c in SV.changes())
SV.revert_change("channel_slot", False)
assert not SV.channel_allowed(pf, 600_000, "NIFTY")
SV.revert_change("channel_slot", True)
assert SV.channel_allowed(pf, 600_000, "NIFTY")

# 5. portfolio replay: channel trades skipped below Rs 5L and when switched off
row = dict(date="2026-10-05", entry_time="10:00", exit_time="11:00", sym="NIFTY", side="CALL", prem_in=100.0, prem_out=120.0, lot=65,
           lots=1, risk_lot=1300.0, risk_lot_capped=1300.0, R=1.0, score=3, cpr_w=0.3, pieces=1, spot_in=24000.0, spot_out=24050.0, reason="x")
T = pd.DataFrame([{**row, "strategy": "Channel 15m"}])
r_small = PB.replay(T, 50_000, "compound", 1.0, 1, "standard", True, 50, False, None, cpr=False)
r_big = PB.replay(T, 600_000, "compound", 1.0, 1, "standard", True, 50, False, None, cpr=False)
r_off = PB.replay(T, 600_000, "compound", 1.0, 1, "standard", True, 50, False, None, cpr=False, channel=False)
assert r_small["skipped"].iloc[0] == "channel slot below Rs 5 lakh"
assert pd.isna(r_big["skipped"].iloc[0]) if "skipped" in r_big else True
assert r_off["skipped"].iloc[0] == "channel slot off"
assert PB.BASELINE["channel"] is True and PB._validate({"channel": False})["channel"] is False
assert "channel" in C.SLOTS
print("round-90 channel slot tests passed")
