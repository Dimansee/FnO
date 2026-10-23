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
MINS = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}


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
MK.last_price = lambda m, s: float(fake_candles(m, s, "4h", 365)["close"].iloc[-1])
MK.completed = lambda df, tf, now=None, market="CRYPTO": df[df.index + timedelta(minutes=MK.MINUTES[tf]) <= clock[0]]
MK.us_session_open = lambda now=None: False

# 1. every candidate is a valid strategy
for m in ("CRYPTO", "US"):
    for s in AP.all_specs(m):
        assert s["enabled"] and s["auto"] and s["autopilot"] and s["market"] == m and s["id"].startswith("s_a"), s
print("candidates valid:", sum(len(AP.all_specs(m)) for m in ("CRYPTO", "US")))

# 2. qualification tests every pair and stores the result
q = AP.qualify("CRYPTO", force=True)
assert len(q["rows"]) == len(AP.CANDIDATES["CRYPTO"]), len(q["rows"])
assert all(len(r["per_symbol"]) == len(AP.SYMBOLS["CRYPTO"]) for r in q["rows"])
print("qualify:", [(r["name"][12:40], r["ok"], r["why"][:60]) for r in q["rows"]])
assert AP.qualify("CRYPTO")["t"] == q["t"]          # cached for a week
assert len(AP.active_specs("CRYPTO")) == len(q["ok"])
# a changed candidate list forces a re-test even inside the week
store.put("g_ap_qual:CRYPTO", {**q, "ids": ["s_old"]})
q = AP.qualify("CRYPTO")
assert q["ids"] == sorted(c["id"] for c in AP.CANDIDATES["CRYPTO"])

# force a known pass so the trade path is exercised whatever the synthetic data gave
store.put("g_ap_qual:CRYPTO", {**q, "ok": {"s_a10001": ["BTCUSDT"]}})
specs, who = GB.runners("CRYPTO")
assert who == "autopilot" and specs and specs[0]["id"] == "s_a10001" and specs[0]["symbols"] == ["BTCUSDT"]

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
assert len(pos) == 1 and pos[0]["strategy_id"] == "s_a10001" and pos[0]["strategy_name"].startswith("Autopilot"), pos
assert "s_a10001" in GB._spec_map()                 # exit rules found for the open position
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
store.put("g_ap_qual:US", {"t": 9e18, "at": "2026-10-16T00:00:00+00:00", "ok": {}, "rows": [{"id": "s_a10011", "name": "Autopilot · x", "symbol": "all", "ok": False, "why": "recent profit factor 0.8", "per_symbol": {}}], "data_errors": 0})
assert GB.runners("US") == ([], "autopilot")
st = GB.portfolio("US")["autopilot"]
assert st["in_use"] and st["tested"] == 1 and st["passed"] == [] and len(st["failed"]) == 1 and "per_symbol" not in st["failed"][0]

# 5b. US: the last bar of a session is complete at the 16:00 close, not 4 hours later
import pandas as _pd
ix = _pd.DatetimeIndex([_pd.Timestamp("2026-10-16 09:30", tz=MK.TZ["US"]), _pd.Timestamp("2026-10-16 13:30", tz=MK.TZ["US"])])
ends = MK.bar_ends(ix, "4h", "US")
assert ends[0].hour == 13 and ends[0].minute == 30 and ends[1].hour == 16 and ends[1].minute == 0, ends
assert MK.history_days("US", "4h") > 150 and MK.history_days("CRYPTO", "4h") < 60

# 5c. lab: separate ledger, BTC regime switch, never touches the crypto book
from fno import glab, learn as LN, market as Mmod
Mmod._cache.clear() if hasattr(Mmod, "_cache") else None
before_pos = len(GB.positions("CRYPTO"))
reg = glab.btc_regime()
_cu.signals = sig                                       # fresh long signal on the last bar again
clock[0] = clock[0]
print("lab part of the earlier book tick:", res.get("lab"))
assert "lab" in res
store.put("g_lab_pos", {})
for sym_ in AP.SYMBOLS["CRYPTO"]:
    store.put(f"g_lab_seen:s_b20001:{sym_}", None)
import fno.glab as _gl
_gl._now = lambda: clock[0]
res = glab.tick()
print("lab tick:", res[:4], "regime bull:", reg["bull"])
lp = glab.positions()
if reg["bull"]:
    assert lp and all(p["side"] == "long" for p in lp.values()), res
else:
    assert not lp and any("BTC regime" in (x.get("lab_skipped") or "") for x in res), res
assert len(GB.positions("CRYPTO")) == before_pos                     # lab never opens book positions
_cu.signals = orig
sm = glab.summary()["rules"][0]
assert sm["id"] == "s_b20001" and "lab" in sm and "live_counterpart" in sm
assert GB.portfolio("CRYPTO")["lab"]["rules"] and GB.portfolio("US")["lab"] is None

# 5d. markets keep separate settings
LN.set_mode("off", "CRYPTO")
assert LN.mode("CRYPTO") == "off" and LN.mode("IN") != "off" and LN.mode("US") != "off"
LN.set_mode("safe")
assert LN.modes() == {"IN": "safe", "CRYPTO": "safe", "US": "safe", "GOLD": "safe"}
base = {"timeframe": "1h", "direction": "long", "enabled": False, "hold": "swing", "long": {"all": [{"l": {"price": "close"}, "op": "gt", "r": {"value": 1}}]}, "exit": {"stop_atr": 1.0, "target_r": 1.5}}
store.put("custom_strategies", [])
for i in range(CU.MAX_STRATEGIES):
    CU.upsert(store, {**base, "name": f"c{i}", "market": "CRYPTO", "symbols": ["BTCUSDT"]})
try:
    CU.upsert(store, {**base, "name": "one too many", "market": "CRYPTO", "symbols": ["BTCUSDT"]}); raise SystemExit("limit not enforced")
except ValueError:
    pass
CU.upsert(store, {"name": "india ok", "market": "IN", "symbols": ["NIFTY"], "timeframe": "15m", "direction": "long", "enabled": False,
                  "long": {"all": [{"l": {"price": "close"}, "op": "gt", "r": {"value": 1}}]}, "exit": {"stop_atr": 1.0, "target_r": 1.5}})
print("separate settings OK")

# 6. runner stays off outside Render; health accepts the POST wake-up call
assert RN.enabled() is False and RN.start() is False
import app as A
c = A.app.test_client()
r = c.post("/api/health")
assert r.status_code == 200 and "global_runner" in r.get_json(), r.data

# 7. slow rules may enter up to 4h after the signal bar; fast rules keep the 10-20 min window
assert GB._max_age(240) == 240 and GB._max_age(1440) == 240 and GB._max_age(60) == 20 and GB._max_age(5) == 10

# 8. live scanner: one row per watched coin (rule rows or a 'watched only' note), with trigger text for flat rules
sc = GB.scanner("CRYPTO")
assert {r["symbol"] for r in sc} == set(GB.WATCH["CRYPTO"]), sc
for r in sc:
    assert "error" not in r, r
    if r.get("rule") and not r["in_position"]:
        assert r["next"] and all(isinstance(x, str) and ("if a bar closes" in x or "already" in x or " when" in x) for x in r["next"]), r
assert "scanner" in GB.portfolio("CRYPTO")
print("scanner OK:", len(sc), "rows")

# 9. chart endpoint: candles + rule lines on the rule's own timeframe, bad input refused
ch = GB.chart("CRYPTO", "BTCUSDT", "4h")
assert len(ch["candles"]) > 50 and ch["lines"] and all(p["time"] and p["value"] for p in ch["lines"][0]["points"][:5]), {k: (len(v) if isinstance(v, list) else v) for k, v in ch.items()}
assert "1m" in ch["timeframes"] and {"id": "BTCUSDT", "label": "Bitcoin"} in ch["symbols"]
for bad in (("CRYPTO", "BTCUSDT", "7m"), ("CRYPTO", "NOPE", "5m")):
    try:
        GB.chart(*bad); raise SystemExit("bad chart input accepted")
    except ValueError:
        pass
r = c.get("/api/gbook/chart?market=CRYPTO&symbol=BTCUSDT&tf=1d")
assert r.status_code in (200, 401), r.status_code
print("chart OK:", len(ch["candles"]), "candles,", len(ch["lines"]), "lines,", len(ch["markers"]), "markers")

# 10. journal, rulebook and market context for the Crypto workspace
j = GB.journal("CRYPTO")
assert {"trades", "by_day", "by_strategy", "by_symbol", "by_reason", "open"} <= set(j) and len(j["open"]) == len(GB.positions("CRYPTO"))
rb = GB.rules("CRYPTO")
assert len(rb["built_in"]) == len(AP.CANDIDATES["CRYPTO"]) and all(r["about"] and r["spec"]["market"] == "CRYPTO" for r in rb["built_in"])
for r in rb["built_in"]:
    CU.validate(r["spec"], existing_id=r["id"])            # the Backtest tab posts these specs back unchanged
ctx = GB.context("CRYPTO")
assert ctx["rows"] and all("error" not in r for r in ctx["rows"]), ctx["rows"][:2]
assert ctx["breadth"]["of"] == len(ctx["rows"])
print("journal/rules/context OK")

# 11. manual paper trades: preview, checks, open, edit, managed exit, kept out of learning
px = MK.last_price("CRYPTO", "ETHUSDT")
pv = GB.manual_preview("CRYPTO", {"symbol": "ETHUSDT", "side": "buy", "stop": px * 0.98, "target": px * 1.04, "size_mode": "risk", "risk_pct": 1})
assert pv["qty"] > 0 and abs(pv["loss_pct"] - 1.0) < 0.3 and pv["rr"] and "id" not in pv, pv
for bad in ({"side": "buy", "stop": px * 1.01}, {"side": "sell", "stop": px * 0.99}, {"side": "buy"}, {"side": "buy", "stop": px * 0.99, "target": px * 0.98},
            {"side": "buy", "stop": px * 0.99999}, {"side": "x", "stop": px * 0.99}):
    try:
        GB.manual_open("CRYPTO", {"symbol": "ETHUSDT", **bad}); raise SystemExit(f"bad manual trade accepted: {bad}")
    except ValueError:
        pass
n0 = len(GB.positions("CRYPTO"))
o = GB.manual_open("CRYPTO", {"symbol": "ETHUSDT", "side": "buy", "stop": px * 0.98, "target": px * 1.04, "trail_pct": 3, "size_mode": "amount", "amount": 500, "note": "test"})
mp = next(p for p in GB.positions("CRYPTO") if p["id"] == o["id"])
assert len(GB.positions("CRYPTO")) == n0 + 1 and mp["strategy_name"] == "Manual" and mp["manual"] and abs(mp["qty"] * mp["entry"] - 500) < 2, mp
m = GB.modify_position("CRYPTO", o["id"], {"stop": px * 0.97, "target": ""})
mp = next(p for p in GB.positions("CRYPTO") if p["id"] == o["id"])
assert abs(mp["state"]["sl"] - px * 0.97) < 1e-6 and mp["state"]["tp"] is None, mp["state"]
try:
    GB.modify_position("CRYPTO", o["id"], {"stop": px * 1.05}); raise SystemExit("stop above price accepted")
except ValueError:
    pass
GB.modify_position("CRYPTO", o["id"], {"stop": px * 0.9999})   # tighten: the next tick's live-price check may stop it
orig = MK.last_price
MK.last_price = lambda m, s: px * 0.99                           # price falls through the stop
GB.tick(("CRYPTO",), manage_only=True)
MK.last_price = orig
assert not any(p["id"] == o["id"] for p in GB.positions("CRYPTO"))
rec = next(t for t in GB.trades("CRYPTO") if t["id"] == o["id"])
assert rec["strategy_name"] == "Manual" and rec["reason"].startswith("Stop") and rec["pnl"] < 0, rec
print("manual trades OK:", o["message"])

# 12. order desk: market / limit / stop orders, leverage + liquidation, partial close, cancel
from fno import gorders as GO
GO.fx = lambda: {"USDINR": 88.0, "src": "test"}
px = MK.last_price("CRYPTO", "SOLUSDT")
pv = GO.preview("CRYPTO", {"symbol": "SOLUSDT", "side": "buy", "type": "market", "size_mode": "margin", "margin": 200, "leverage": 10})
assert abs(pv["notional"] - 2000) < 5 and abs(pv["margin"] - 200) < 1 and pv["liq"] and pv["liq"] < px and pv["stop_is_liq"], pv
assert abs(pv["liq"] - pv["entry"] * (1 - 0.1 + 0.005)) < 1e-6
pv2 = GO.preview("CRYPTO", {"symbol": "SOLUSDT", "side": "buy", "type": "market", "size_mode": "qty", "qty": 1, "leverage": 20, "stop": px * 0.9})
assert pv2["warnings"] and pv2["stop_is_liq"], pv2                  # 20x: liquidation (~-4.5%) before a -10% stop
for bad in ({"type": "stop", "price": px * 0.99}, {"type": "limit"}, {"leverage": 40}, {"type": "market", "stop": px * 1.01}):
    try:
        GO.place("CRYPTO", {"symbol": "SOLUSDT", "side": "buy", "size_mode": "qty", "qty": 1, **bad}); raise SystemExit(f"bad order accepted {bad}")
    except ValueError:
        pass
r = GO.place("CRYPTO", {"symbol": "SOLUSDT", "side": "sell", "type": "market", "size_mode": "pct", "pct": 10, "leverage": 5, "target": px * 0.9})
pos = next(p for p in GB.positions("CRYPTO") if p["id"] == r["position"])
assert pos["side"] == "short" and pos["leverage"] == 5 and pos["liq"] > px and abs(pos["state"]["sl"] - pos["liq"]) < 1e-6, pos
out = GO.close_part("CRYPTO", pos["id"], 0.5)
left = next(p for p in GB.positions("CRYPTO") if p["id"] == pos["id"])
assert abs(left["qty"] - pos["qty"] / 2) < 1e-5 and "Partly closed" in next(t for t in GB.trades("CRYPTO") if t["symbol"] == "SOLUSDT" and t["side"] == "short")["reason"]
GO.close_part("CRYPTO", pos["id"], 1)
assert not any(p["id"] == pos["id"] for p in GB.positions("CRYPTO"))
# limit buy below the market waits, then fills when a 1-minute candle trades through it
lim = px * 0.95
o = GO.place("CRYPTO", {"symbol": "SOLUSDT", "side": "buy", "type": "limit", "price": lim, "size_mode": "qty", "qty": 2, "stop": lim * 0.97, "target": lim * 1.05})
assert not o["filled"] and len(GO.open_orders("CRYPTO")) == 1
m1 = fake_candles("CRYPTO", "SOLUSDT", "4h", 365).tail(5).copy()
idx = pd.date_range(end=clock[0] + timedelta(minutes=3), periods=5, freq="1min", tz="UTC")
m1.index = idx
m1[["open", "high", "low", "close"]] = px
m1.iloc[-1, m1.columns.get_loc("low")] = lim * 0.999
_c = MK.candles
MK.candles = lambda m, s, tf, d, ttl=None: m1 if tf == "1m" else _c(m, s, tf, d, ttl)
res = GO.process("CRYPTO")
MK.candles = _c
assert res and res[0].get("filled"), res
fp = next(p for p in GB.positions("CRYPTO") if p["symbol"] == "SOLUSDT" and p["side"] == "long")
assert abs(fp["entry"] - lim) < 1e-6 and fp["state"]["tp"] and GO.history("CRYPTO")[0]["status"] == "filled"
# stop order placed then cancelled
so = GO.place("CRYPTO", {"symbol": "BTCUSDT", "side": "buy", "type": "stop", "price": MK.last_price("CRYPTO", "BTCUSDT") * 1.05, "size_mode": "amount", "amount": 100})
GO.cancel("CRYPTO", so["order"])
assert not GO.open_orders("CRYPTO") and GO.history("CRYPTO")[0]["status"] == "cancelled"
dk = GO.desk("CRYPTO")
assert {"positions", "orders", "order_history", "trades", "fx", "equity", "free", "upnl"} <= set(dk) and all("roe" in p for p in dk["positions"])
GO.close_part("CRYPTO", fp["id"], 1)
print("order desk OK")
print("ALL GLOBAL AUTOPILOT OK")
