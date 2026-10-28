"""Offline test: gold book, world sessions, session levels, session stats, gold autopilot and the API. Run: python tests/test_gold.py"""
import os, sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(port=6431)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)
os.environ.pop("RENDER", None)

import numpy as np
import pandas as pd

from fno import custom as CU, gautopilot as AP, gbook as GB, gold as GLD, ind as X, learn as LN, markets as MK, sessions as SS, store  # noqa: E402

UTC, LON, NY = timezone.utc, MK.TZ["GOLD"], MK.TZ["US"]

# 1. gold market hours (COMEX: Sun 18:00 - Fri 17:00 New York, daily 17:00-18:00 break)
ny = lambda *a: datetime(*a, tzinfo=NY)   # noqa: E731
assert MK.gold_open(ny(2026, 10, 21, 10, 0)) and MK.gold_open(ny(2026, 10, 25, 18, 30))          # Wed morning; Sunday evening reopen
assert not MK.gold_open(ny(2026, 10, 24, 12, 0)) and not MK.gold_open(ny(2026, 10, 25, 17, 0))   # Saturday; Sunday before the open
assert not MK.gold_open(ny(2026, 10, 21, 17, 30)) and not MK.gold_open(ny(2026, 10, 23, 17, 30))  # daily break; Friday after the close
assert MK.session_open("CRYPTO", ny(2026, 10, 24, 12, 0)) and not MK.session_open("GOLD", ny(2026, 10, 24, 12, 0))
print("gold hours OK")

# 2. sessions follow each city's clock across the DST changes (UK 25 Oct, US 1 Nov 2026)
def is_(t, name):
    return bool(SS.mask(pd.DatetimeIndex([pd.Timestamp(t)]), name)[0])
assert is_("2026-10-23 07:30Z", "london") and not is_("2026-10-23 06:30Z", "london")     # BST: London opens 07:00 UTC
assert is_("2026-10-27 08:30Z", "london") and not is_("2026-10-27 07:30Z", "london")     # GMT: 08:00 UTC
assert is_("2026-10-27 07:30Z", "asia") and not is_("2026-10-23 07:30Z", "asia")          # Asia runs until London opens
assert is_("2026-10-27 12:30Z", "overlap") and not is_("2026-11-03 12:30Z", "overlap")   # NY opens 12:00 UTC (EDT) vs 13:00 UTC (EST)
assert SS.label(pd.Timestamp("2026-11-03 14:00Z")) == "overlap" and SS.label(pd.Timestamp("2026-11-03 20:00Z")) == "newyork"
assert SS.label(pd.Timestamp("2026-11-03 23:00Z")) == "late"
w = {x["id"]: x for x in SS.windows_ist(datetime(2026, 11, 4).date())}
assert (w["london"]["start_ist"], w["overlap"]["start_ist"], w["overlap"]["end_ist"], w["newyork"]["end_ist"]) == ("13:30", "18:30", "22:30", "03:30"), w
w = {x["id"]: x for x in SS.windows_ist(datetime(2026, 10, 22).date())}
assert (w["london"]["start_ist"], w["newyork"]["start_ist"], w["asia"]["start_ist"]) == ("12:30", "17:30", "05:30"), w
ck = SS.clock(datetime(2026, 10, 23, 12, 38, tzinfo=UTC))
assert ck["gold_open"] and ck["session_now"] == "overlap" and ck["next_events"][0]["id"] in ("comex_close", "lbma_pm"), ck["next_events"][:2]
assert SS.clock(datetime(2026, 10, 24, 12, 0, tzinfo=UTC))["session_now"] == "closed"
print("sessions OK")


# 3. synthetic gold: 15m bars on COMEX hours, more movement in London and the overlap
def gold_bars(start, end, freq="15min", seed=1):
    r = np.random.default_rng(seed)
    idx = pd.date_range(start, end, freq=freq, tz="UTC", inclusive="left")
    idx = idx[[MK.gold_open(t.to_pydatetime(), pad_after=0) for t in idx]]
    vol = np.where(SS.mask(idx, "overlap"), 0.0015, np.where(SS.mask(idx, "london"), 0.0009, 0.0003))
    px = 4000 * np.cumprod(1 + r.normal(0, vol))
    o = np.concatenate([[4000.0], px[:-1]])
    df = pd.DataFrame({"open": o, "close": px, "volume": r.uniform(500, 900, len(idx))}, index=idx.tz_convert(LON))
    df["high"] = np.maximum(df.open, df.close) * (1 + vol / 2)
    df["low"] = np.minimum(df.open, df.close) * (1 - vol / 2)
    return df[["open", "high", "low", "close", "volume"]]


m15 = gold_bars("2026-08-03", "2026-10-23")
st = GLD.stats_from_bars(m15)
rows = {r["session"]: r for r in st["rows"]}
assert set(rows) == {"asia", "london", "overlap", "newyork"} and st["days"] > 50, st["days"]
assert rows["overlap"]["avg_range"] > rows["asia"]["avg_range"] and rows["london"]["avg_range"] > rows["asia"]["avg_range"], rows
assert len(st["hours"]) >= 20 and st["busiest_ist"], st["busiest_ist"]
print("session stats OK:", {k: v["avg_range"] for k, v in rows.items()}, "busiest IST", st["busiest_ist"])

# 4. session levels: no look-ahead
day = m15[m15.index.tz_convert("UTC").date == pd.Timestamp("2026-10-21").date()]
ah = X.level_series(m15, "asia_high").loc[day.index]
asia = day[SS.mask(day.index, "asia")]
after = day.index[day.index > asia.index[-1]]
assert ah.loc[asia.index].isna().all() or (ah.loc[asia.index] != asia["high"].max()).all()    # never known inside the window
assert np.isclose(ah.loc[after[0]], asia["high"].max()), (ah.loc[after[0]], asia["high"].max())
nl = X.level_series(m15, "ny_or_low")
ny_day = m15[m15.index.tz_convert(NY).date == pd.Timestamp("2026-10-21").date()]
loc = ny_day.index.tz_convert(NY)
win = ny_day[(loc.hour * 60 + loc.minute >= 510) & (loc.hour * 60 + loc.minute < 540)]
first_after = ny_day.index[ny_day.index > win.index[-1]][0]
assert np.isnan(nl.loc[win.index]).all() or not np.isclose(nl.loc[win.index[0]], win["low"].min())
assert np.isclose(nl.loc[first_after], win["low"].min())
assert X.level_series(CU.resample(m15, "1d", "GOLD"), "asia_high").isna().all()               # daily bars: not available
print("session levels OK")

# 5. session filter in a strategy, gold costs, gold presets valid
spec = CU.validate({"name": "London only", "market": "GOLD", "symbols": ["XAUUSD"], "timeframe": "15m", "direction": "long", "hold": "intraday",
                    "long": {"all": [{"l": {"price": "close"}, "op": "gt", "r": {"value": 0}}]}, "exit": {"stop_atr": 1, "target_r": 1},
                    "filters": {"session": "london", "max_trades_day": 20}})
lg, sh, _ = CU.signals(spec, m15)
assert lg.any() and SS.mask(m15.index[lg.values], "london").all()
try:
    CU.validate({**spec, "filters": {"session": "tokyo"}}); raise SystemExit("bad session accepted")
except ValueError:
    pass
try:
    CU.validate({**spec, "market": "IN", "symbols": ["NIFTY"], "filters": {"session": "london"}}); raise SystemExit("session on IN accepted")
except ValueError:
    pass
assert CU.COSTS_BPS["GOLD"] == (2.0, 3.0) and CU.SESSION["GOLD"]["square_off"] == (21, 45)
golds = [p for p in CU.PRESETS if p["market"] == "GOLD"]
assert len(golds) == 2 and all(CU.validate(p) for p in golds)
t = CU.simulate(CU.validate(golds[0]), m15)
assert all(SS.mask(pd.DatetimeIndex([x["entry_ts"] - timedelta(minutes=15)]), "london")[0] for x in t), "London breakout fired outside London"
assert all(x["exit_ts"].tz_convert(LON).hour * 60 + x["exit_ts"].tz_convert(LON).minute <= 21 * 60 + 45 for x in t)
print("filters / presets OK:", len(t), "London-breakout trades on the synthetic data")

# 6. autopilot candidates: valid, only XAUUSD, only the two rules the research kept
for s in AP.all_specs("GOLD"):
    assert s["market"] == "GOLD" and s["symbols"] == ["XAUUSD"] and s["autopilot"], s
assert {s["id"] for s in AP.all_specs("GOLD")} == {"s_a10022", "s_a10023"}
h4 = CU.resample(m15, "4h", "GOLD")
assert set(h4.index.tz_convert(LON).hour) <= {0, 4, 8, 12, 16, 20}
print("gold autopilot specs OK:", len(AP.all_specs("GOLD")), "rules")

# 7. the book: qualify, trade, separate ledger
T0 = datetime(2026, 10, 21, 14, 1, tzinfo=UTC)        # Wednesday, overlap
clock = [T0]
GB._now = lambda: clock[0]
CACHE = {}


def fake_candles(market, symbol, tf, days, ttl=None):
    assert market == "GOLD", market
    key = (symbol, tf)
    if key not in CACHE:
        base = gold_bars("2024-10-01", "2026-10-21 14:00", freq="1h", seed=7) if tf in ("1h", "4h", "1d") else m15[m15.index < pd.Timestamp("2026-10-21 14:00Z")]
        CACHE[key] = base if tf in ("1h", "15m") else CU.resample(base, tf, "GOLD")
    return CACHE[key]


MK.candles = fake_candles
real_completed = MK.completed
MK.last_price = lambda m, s: float(real_completed(fake_candles(m, s, "4h", 30), "4h", clock[0], "GOLD")["close"].iloc[-1])
MK.completed = lambda df, tf, now=None, market="CRYPTO": real_completed(df, tf, clock[0], market)
q = AP.qualify("GOLD", force=True)
assert len(q["rows"]) == len(AP.CANDIDATES["GOLD"]) and all("XAUUSD" in r["per_symbol"] for r in q["rows"]), q["rows"]
print("gold qualify:", [(r["name"][12:45], r["ok"]) for r in q["rows"]])
store.put("g_ap_qual:GOLD", {**q, "ok": {"s_a10022": ["XAUUSD"]}})
import fno.custom as _cu
orig = _cu.signals
def sig(spec, df):
    lg, sh, info = orig(spec, df)
    lg = lg.copy(); sh = sh.copy(); lg.iloc[-1] = True; sh.iloc[-1] = False
    return lg, sh, info
_cu.signals = sig
# 28 Oct 2026: the Gold workspace was merged into Crypto. The retired gold book opens nothing new ...
res = GB.tick(("GOLD",))
assert not GB.positions("GOLD") and res["GOLD"][0].get("skipped", "").startswith("retired"), res
try:
    GB.manual_open("GOLD", {"symbol": "XAUUSD", "side": "long"})
    raise AssertionError("a retired book accepted a manual trade")
except ValueError:
    pass
# ... but a trade it already held keeps being managed: open one as before the merge, then retire again
GB.RETIRED = set()
res = GB.tick(("GOLD",))
GB.RETIRED = {"GOLD"}
pos = GB.positions("GOLD")
assert len(pos) == 1 and pos[0]["symbol"] == "XAUUSD" and pos[0]["strategy_id"] == "s_a10022", (res, pos)
res2 = GB.tick(("GOLD",))
assert len(GB.positions("GOLD")) == 1 and not any("error" in r for r in res2["GOLD"]), res2
assert pos[0]["qty"] != int(pos[0]["qty"]) or pos[0]["qty"] > 0        # ounces, fractions allowed
assert not GB.positions("CRYPTO") and not GB.positions("US")
_cu.signals = orig
# weekend: manage only, no new entries
clock[0] = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
store.put("g_ap_qual:GOLD", {**q, "ok": {"s_a10023": ["XAUUSD"]}})
_cu.signals = sig
GB.tick(("GOLD",))
assert all(p["strategy_id"] != "s_a10023" for p in GB.positions("GOLD")), "opened a gold trade on Saturday"
_cu.signals = orig
r = GB.close_position("GOLD", pos[0]["id"], pos[0]["entry"] * 1.01, "test")
assert r and r["market"] == "GOLD" and r["pnl"] > 0
assert LN.tags_for({"market": "GOLD", "time": "14:10", "side": "long"})[1] == "tod:overlap"
assert LN.tags_for({"market": "GOLD", "time": "03:00", "side": "long"})[1] == "tod:asia"
pf = GB.portfolio("GOLD")
assert pf["currency"] == "USD" and pf["max_leverage"] == 10.0 and pf["stats"]["trades"] == 1
assert GB.rules("GOLD")["built_in"] and GB.journal("GOLD")["trades"]
print("gold book OK:", r["pnl"], "USD")

# 8. daily bar end = 17:00 New York; PAXG daily/4h built on the same clocks
d_ix = pd.DatetimeIndex([pd.Timestamp("2026-10-21", tz=LON)])
e = MK.bar_ends(d_ix, "1d", "GOLD")[0].tz_convert(NY)
assert (e.hour, e.minute, e.day) == (17, 0, 21), e
assert MK.history_days("GOLD", "4h") > 60 and MK.history_days("GOLD", "1h") < 30
h = gold_bars("2026-10-12", "2026-10-21", freq="1h", seed=3)
h = pd.concat([h, pd.DataFrame({"open": 4000.0, "high": 4001.0, "low": 3999.0, "close": 4000.5, "volume": 1.0},
                               index=pd.DatetimeIndex([pd.Timestamp("2026-10-17 12:00Z").tz_convert(LON)]))]).sort_index()   # a Saturday PAXG print
MK._crypto = lambda sym, tf, days: h.tz_convert("UTC")
dd = MK._gold("PAXGUSDT", "1d", 20)
assert all(x.weekday() < 5 for x in dd.index), dd.index          # Saturday folded into Monday
assert set(MK._gold("PAXGUSDT", "4h", 10).index.hour) <= {0, 4, 8, 12, 16, 20}
print("bar clocks OK")

# 9. API
import app as APP  # noqa: E402
APP.SV.clean = APP.SV.clean
c = APP.app.test_client()
c.post("/api/login", json={"password": "pw"})
GLD.session_stats = lambda symbol="XAUUSD": {**GLD.stats_from_bars(m15), "symbol": symbol}
rj = c.get("/api/gold/sessions").get_json()
assert rj["clock"]["windows"] and rj["stats"]["rows"] and rj["notes"], rj.keys()
assert c.get("/api/gold/sessions?symbol=BTCUSDT").status_code >= 400
g = c.get("/api/gbook?market=GOLD").get_json()
assert g["market"] == "GOLD" and g["currency"] == "USD", g
s = c.get("/api/strategies").get_json()
assert "GOLD" in s["markets"] and s["markets"]["GOLD"]["sessions"]["overlap"] and "asia_high" in s["meta"]["levels"]
assert "GOLD" in c.get("/api/health").get_json()["global_runner"]["autopilot_pairs"]
cg = c.get("/api/gbook?market=CRYPTO").get_json()
assert "gold_book" not in cg or cg["gold_book"]["positions"], cg.get("gold_book")
print("API OK")
print("ALL GOLD OK")
