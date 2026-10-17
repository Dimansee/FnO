"""Offline test: crypto/US autopilot + background runner wiring. Run: python tests/test_global_autopilot.py"""
import os, sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(port=6421)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)
os.environ.pop("RENDER", None)

import numpy as np
import pandas as pd

from fno import custom as CU, gautopilot as AP, gbook as GB, markets as MK, runner as RN, store  # noqa: E402

rng = np.random.default_rng(11)
UTC = timezone.utc
T0 = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
clock = [T0]
GB._now = lambda: clock[0]
MINS = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}


def bars(tf, n, start=100.0, seed=0):
    r = np.random.default_rng(seed)
    idx = pd.date_range(end=T0 - timedelta(minutes=MINS[tf]), periods=n, freq=f"{MINS[tf]}min", tz="UTC")
    # strong regime-switching trend: breakouts and trend rules should pass, so at least one pair qualifies
    drift = np.repeat(r.choice([0.004, -0.004], size=n // 60 + 1), 60)[:n]
    px = start * np.cumprod(1 + drift + r.normal(0, 0.003, n))
    o = np.concatenate([[start], px[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, px) * 1.002, "low": np.minimum(o, px) * 0.998, "close": px, "volume": r.uniform(80, 200, n)}, index=idx)


CACHE = {}


def fake_candles(market, symbol, tf, days, ttl=None):
    key = (market, symbol, tf)
    if key not in CACHE:
        n = min(int(days * 1440 / MINS[tf]), 3000)
        df = bars(tf, n, seed=hash(key) % 1000)
        if market == "US":
            df.index = df.index.tz_convert(MK.TZ["US"])
        CACHE[key] = df
    return CACHE[key]


MK.candles = fake_candles
MK.last_price = lambda m, s: float(fake_candles(m, s, "1h", 30)["close"].iloc[-1])
MK.completed = lambda df, tf, now=None: df[df.index + timedelta(minutes=MK.MINUTES[tf]) <= clock[0]]
MK.us_session_open = lambda now=None: False

# 1. every candidate is a valid strategy
for m in ("CRYPTO", "US"):
    for s in AP.all_specs(m):
        assert s["enabled"] and s["auto"] and s["autopilot"] and s["market"] == m and s["id"].startswith("s_a"), s
print("candidates valid:", sum(len(AP.all_specs(m)) for m in ("CRYPTO", "US")))

# 2. qualification tests every pair and stores the result
q = AP.qualify("CRYPTO", force=True)
assert len(q["rows"]) == len(AP.CANDIDATES["CRYPTO"]) * len(AP.SYMBOLS["CRYPTO"]), len(q["rows"])
print("qualify:", [(r["name"][12:30], r["symbol"], r["ok"], r["why"]) for r in q["rows"]][:6])
assert AP.qualify("CRYPTO")["t"] == q["t"]          # cached for a week
ok_pairs = sum(len(v) for v in q["ok"].values())
assert ok_pairs == len(AP.active_specs("CRYPTO")[0]["symbols"]) if len(q["ok"]) == 1 else True

# force a known pass so the trade path is exercised whatever the synthetic data gave
store.put("g_ap_qual:CRYPTO", {**q, "ok": {"s_a00003": ["BTCUSDT"]}})
specs, who = GB.runners("CRYPTO")
assert who == "autopilot" and specs and specs[0]["id"] == "s_a00003" and specs[0]["symbols"] == ["BTCUSDT"]

# 3. a tick with NO user strategies uses the autopilot; make the last bar a fresh long signal
import fno.custom as _cu
orig = _cu.signals
def sig(spec, df):
    lg, sh, info = orig(spec, df)
    lg = lg.copy(); sh = sh.copy(); lg.iloc[-1] = True; sh.iloc[-1] = False
    return lg, sh, info
_cu.signals = sig
clock[0] = T0 + timedelta(minutes=1)
res = GB.tick(("CRYPTO",))
print("tick:", res["CRYPTO"])
pos = GB.positions("CRYPTO")
assert len(pos) == 1 and pos[0]["strategy_id"] == "s_a00003" and pos[0]["strategy_name"].startswith("Autopilot"), pos
assert "s_a00003" in GB._spec_map()                 # exit rules found for the open position
res2 = GB.tick(("CRYPTO",))                         # same bar never traded twice
assert len(GB.positions("CRYPTO")) == 1, res2

# 4. a user strategy on Auto makes the autopilot step aside
CU.upsert(store, {"name": "Mine", "market": "CRYPTO", "symbols": ["ETHUSDT"], "timeframe": "1h", "direction": "long", "enabled": True, "auto": True, "hold": "swing",
                  "long": {"all": [{"l": {"price": "close"}, "op": "gt", "r": {"value": 1}}]}, "exit": {"stop_atr": 1.0, "target_r": 1.5}})
specs, who = GB.runners("CRYPTO")
assert who == "yours" and [s["name"] for s in specs] == ["Mine"]
pf = GB.portfolio("CRYPTO")
assert pf["autopilot"]["in_use"] is False and pf["last_tick"]
_cu.signals = orig

# 5. nothing passes -> no autopilot trades, status explains
store.put("g_ap_qual:US", {"t": 9e18, "at": "2026-10-16T00:00:00+00:00", "ok": {}, "rows": [{"id": "s_a00011", "name": "Autopilot · x", "symbol": "SPY", "ok": False, "why": "profit factor 0.8"}], "data_errors": 0})
assert GB.runners("US") == ([], "autopilot")
st = GB.portfolio("US")["autopilot"]
assert st["in_use"] and st["tested"] == 1 and st["passed"] == []

# 6. runner stays off outside Render; health accepts the POST wake-up call
assert RN.enabled() is False and RN.start() is False
import app as A
c = A.app.test_client()
r = c.post("/api/health")
assert r.status_code == 200 and "global_runner" in r.get_json(), r.data
print("ALL GLOBAL AUTOPILOT OK")
