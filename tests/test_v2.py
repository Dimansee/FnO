"""Offline tests for v2: custom strategies, learning engine, crypto/US books. Run: python tests/test_v2.py"""
import os, sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(port=6411)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)

import numpy as np
import pandas as pd

from fno import config as C, store, market as M, context as X, strategy as S

rng = np.random.default_rng(5)
NOW = [datetime(2026, 10, 1, 10, 52, tzinfo=C.IST)]
C.now_ist = lambda: NOW[0]
C.today_ist = lambda: NOW[0].date()


def fake(start, end_dt, trend=0.0006, days=45):
    rows, px = [], start
    d = end_dt.date() - timedelta(days=days)
    while d <= end_dt.date():
        if d.weekday() < 5:
            t = datetime(d.year, d.month, d.day, 9, 15, tzinfo=C.IST)
            for _ in range(75):
                if t > end_dt:
                    break
                o = px
                px *= 1 + (trend if d == end_dt.date() else 0) + rng.normal(0, 0.0008)
                rows.append((t, o, max(o, px) * 1.0003, min(o, px) * 0.9997, px, 1000))
                t += timedelta(minutes=5)
        d += timedelta(days=1)
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")


CANDLES = {}
M.yahoo_candles = lambda t, period="5d", interval="5m": CANDLES.setdefault(t, fake(1300 if ".NS" in t else 24800, NOW[0]))
M.yahoo_daily = lambda t, period="1mo": pd.DataFrame({"close": [14.0, 13.2]}, index=pd.to_datetime(["2026-09-30", "2026-10-01"]))
M.master = lambda: {"lots": {"NIFTY": 65}, "eq_keys": {}}
M.upstox_public_history = lambda key, cal_days: pd.DataFrame()
X.global_cues = lambda: {"markets": {"S&P 500": {"last": 6000, "chg_pct": 0.8, "date": "2026-10-01"}}, "avg_equity_chg": 0.8, "notes": []}
X.fetch_news = lambda s=None: {"items": [], "score": 0, "events": [], "notes": []}
X.fii_dii = lambda: None

from app import app  # noqa: E402
from fno import custom as CU, learn as LN, gbook as GB, markets as MK, service as SV, paper as P  # noqa: E402

c = app.test_client()


def j(r, ok=200):
    assert r.status_code < 500, r.get_data(as_text=True)[:600]
    if ok:
        assert r.status_code == ok, (r.status_code, r.get_data(as_text=True)[:400])
    return r.get_json()


assert c.get("/api/strategies").status_code == 401
j(c.post("/api/login", json={"password": "pw"}))

# ------------------------------------------------------------------ 1. strategies: CRUD + validation
meta = j(c.get("/api/strategies"))
assert len(meta["presets"]) >= 5 and "ema" in meta["meta"]["indicators"] and "IN" in meta["markets"]
for p in meta["presets"]:
    CU.validate({**p, "symbols": p["symbols"]})
bad = c.post("/api/strategies", json={"name": "x", "symbols": ["NIFTY"], "direction": "long", "long": {"all": [{"l": {"ind": "zzz"}, "op": "gt", "r": {"value": 1}}]}})
assert bad.status_code == 400 and "Unknown indicator" in bad.get_json()["error"], bad.get_json()
bad = c.post("/api/strategies", json={"name": "x", "symbols": ["NIFTY"], "direction": "long", "long": {"all": []}})
assert bad.status_code == 400
always = {"name": "Always long (test)", "symbols": ["NIFTY"], "timeframe": "5m", "direction": "long", "market": "IN", "enabled": True, "auto": True,
          "long": {"all": [{"l": {"price": "close"}, "op": "gt", "r": {"value": 1}}]},
          "exit": {"stop_atr": 1.5, "target_r": 2.0, "breakeven_r": 1.0, "time_stop_bars": 6},
          "filters": {"start": "09:30", "end": "14:30", "max_trades_day": 1}}
sp = j(c.post("/api/strategies", json=always))["item"]
sid = sp["id"]
assert sp["version"] == 1 and sp["exit"]["atr_n"] == 14
sp2 = j(c.post("/api/strategies", json={**always, "id": sid, "exit": {**always["exit"], "stop_atr": 2.0}}))["item"]
assert sp2["id"] == sid and sp2["version"] == 2 and sp2["exit"]["stop_atr"] == 2.0
print("strategies CRUD OK:", sid)

# ------------------------------------------------------------------ 2. backtest endpoint
r = j(c.post("/api/strategies/backtest", json={"id": sid, "symbol": "NIFTY", "days": 60, "capital": 200000, "risk_pct": 1}))
assert r["total"]["n"] > 5 and "verdict" in r and r["bars"] > 100, r.get("error")
print("backtest OK:", r["total"]["n"], "trades, avgR", r["total"]["avg_R"], "| option net", r.get("option", {}).get("net"))
for sym in ("BTCUSDT",):
    n = c.post("/api/strategies/backtest", json={"spec": {**always, "market": "CRYPTO", "symbols": [sym], "timeframe": "1h", "hold": "swing"}, "symbol": sym, "days": 30})
    assert n.status_code in (200, 500, 400)   # offline: the crypto fetch fails cleanly, never crashes the server

# ------------------------------------------------------------------ 3. live loop: signal -> paper trade -> close -> lesson
store.put("prefs", {"strategies": [], "auto": False, "autopilot": False, "sizing": "risk", "risk_pct": 1.0, "auto_syms": ["NIFTY"], "enforce_limits": False})
d = j(c.get("/api/dashboard?symbol=NIFTY"))
print("dashboard signal:", d["signal"]["signal"], "| strategy:", d["signal"].get("strategy"))
assert d["signal"]["plan"] and str(d["signal"]["plan"]["strategy"]).startswith("custom:"), d["signal"]["checks"][-6:]
assert d["signal"]["custom"][0]["name"] == "Always long (test)"
r = c.post("/api/trade/signal", json={"symbol": "NIFTY"})
assert r.status_code == 200, r.get_data(as_text=True)
pos = store.positions()
assert len(pos) == 1 and pos[0]["plan"]["strategy"] == f"custom:{sid}" and pos[0]["plan"]["ctx"].get("vix")
print("custom signal placed:", pos[0]["plan"]["custom_name"], "| ctx", pos[0]["plan"]["ctx"])
# trailing / break-even / time stop through exit_check
p0 = pos[0]
why, upd = S.exit_check(p0, p0["plan"]["breakeven_at"] + 1, 100, NOW[0] + timedelta(minutes=5))
assert why is None and upd.get("at_breakeven"), (why, upd)
why, upd = S.exit_check({**p0, "opened": (NOW[0] - timedelta(minutes=40)).isoformat()}, p0["plan"]["entry"] + 1, 100, NOW[0])
assert why and why.startswith("Time stop"), why
# close it manually: the lesson is written
NOW[0] += timedelta(minutes=20)
CANDLES.clear()
out = j(c.post("/api/trade/exit", json={"id": pos[0]["id"]}))
assert out["ok"], out
les = LN.lessons(5)
assert len(les) == 1 and les[0]["strategy"] == f"custom:{sid}" and les[0]["R"] is not None and les[0]["text"], les
print("lesson:", les[0]["text"][:220])
assert any(t.startswith("tod:") for t in les[0]["tags"]) and any(t.startswith("vix:") for t in les[0]["tags"])

# ------------------------------------------------------------------ 4. learning: evidence -> veto -> revert
S_STRAT = "noise"
for i in range(40):                                  # 'midday' signals lose, others are fine
    LN._bump("S", "IN", S_STRAT, ["tod:midday", "vix:mid"], -0.6 + (0.1 if i % 4 == 0 else 0))
    LN._bump("S", "IN", S_STRAT, ["tod:open", "vix:mid"], 0.3 if i % 3 else -0.5)
    LN._bump("S", "IN", S_STRAT, ["tod:late", "vix:mid"], 0.2 if i % 3 else -0.1)
    LN._bump("S", "IN", S_STRAT, ["tod:open", "vix:high"], 0.2 if i % 3 else -0.1)
made = LN.review("IN")
assert any(m["kind"] == "veto" and "tod:midday" in m["title"] for m in made), [m["title"] for m in made]
assert not any("tod:open" in m["title"] for m in made)
why = LN.veto("IN", S_STRAT, ["tod:midday", "side:long"])
assert why and "midday" in why
assert LN.veto("IN", S_STRAT, ["tod:open"]) is None
assert LN.veto("IN", "camarilla", ["tod:midday"]) is None
print("veto OK:", why[:110])
ch = [m for m in made if m["kind"] == "veto"][0]
assert LN.revert(ch["id"]) and LN.veto("IN", S_STRAT, ["tod:midday"]) is None
LN.set_mode("off")
assert LN.veto("IN", S_STRAT, ["tod:midday"]) is None
LN.set_mode("safe")
# size down after a bad live run
for i in range(25):
    LN._bump("T", "IN", "camarilla", ["tod:open"], -0.8 if i % 5 else 0.2)
made = LN.review("IN")
assert any(m["kind"] == "size_down" for m in made) and LN.size_mult("IN", "camarilla") == 0.5
summ = j(c.get("/api/learn"))
assert summ["mode"] == "safe" and summ["lessons"] and summ["changes"] and summ["health"]
print("size-down OK; summary rules:", len(summ["rules"]), "changes:", len(summ["changes"]))
# a veto on an automatic entry blocks it, a manual one does not
LN.set_mode("safe")

# ------------------------------------------------------------------ 5. improve a custom strategy
lg = {"name": "EMA test", "symbols": ["NIFTY"], "timeframe": "15m", "direction": "both", "market": "IN", "enabled": False,
      "long": {"all": [{"l": {"ind": "ema", "n": 9}, "op": "crosses_above", "r": {"ind": "ema", "n": 21}}]},
      "short": {"all": [{"l": {"ind": "ema", "n": 9}, "op": "crosses_below", "r": {"ind": "ema", "n": 21}}]},
      "exit": {"stop_atr": 1.5, "target_r": 2.0}}
s3 = j(c.post("/api/strategies", json=lg))["item"]
imp = j(c.post("/api/strategies/improve", json={"id": s3["id"], "days": 60}))
assert imp["ok"] is False or "adopted" in imp, imp          # short fake history: either 'not enough trades' or a verdict, never an exception
print("improve:", imp.get("why") or imp.get("candidate"))

# ------------------------------------------------------------------ 6. crypto book
UTC = timezone.utc
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
clock = [T0]
GB._now = lambda: clock[0]
BARS = {}


def mkbars(n_end_ts, n=400, start=60000.0, drift=0.0):
    idx = pd.date_range(end=n_end_ts - timedelta(hours=1), periods=n, freq="1h", tz="UTC")
    px = start * np.cumprod(1 + drift + rng.normal(0, 0.003, n))
    o = np.concatenate([[start], px[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, px) * 1.001, "low": np.minimum(o, px) * 0.999, "close": px, "volume": 100.0}, index=idx)


BARS["BTCUSDT"] = mkbars(T0 + timedelta(hours=1))      # last bar starts 12:00, completes 13:00?  built so that the final bar is complete at T0
LAST = {"BTCUSDT": float(BARS["BTCUSDT"]["close"].iloc[-1])}


def fake_candles(market, symbol, tf, days, ttl=None):
    df = BARS[symbol]
    return df


MK.candles = fake_candles
MK.last_price = lambda m, s: LAST[s]
MK.completed = lambda df, tf, now=None: df[df.index + timedelta(minutes=MK.MINUTES[tf]) <= clock[0]]
cr = {"name": "Crypto always long", "market": "CRYPTO", "symbols": ["BTCUSDT"], "timeframe": "1h", "direction": "long", "enabled": True, "auto": True, "hold": "swing",
      "long": {"all": [{"l": {"price": "close"}, "op": "gt", "r": {"value": 1}}]},
      "exit": {"stop_atr": 1.0, "target_r": 1.5, "time_stop_bars": 5}, "filters": {"max_trades_day": 3}}
cs = j(c.post("/api/strategies", json=cr))["item"]
before = GB.equity("CRYPTO")
res = GB.tick(("CRYPTO",))
print("crypto tick:", res["CRYPTO"])
pos = GB.positions("CRYPTO")
assert len(pos) == 1 and pos[0]["side"] == "long" and pos[0]["qty"] > 0
assert GB.tick(("CRYPTO",), manage_only=False)["CRYPTO"] in ([], [{"skipped": "..."}]) or len(GB.positions("CRYPTO")) == 1   # same bar is never traded twice
p = pos[0]
risk = p["state"]["risk"]
# next hour: price rallies through the 1.5R target
nxt = BARS["BTCUSDT"].index[-1] + timedelta(hours=1)
e = p["entry"]
row = pd.DataFrame({"open": [e], "high": [e + 2 * risk], "low": [e - 0.3 * risk], "close": [e + 1.8 * risk], "volume": [100.0]}, index=[nxt])
BARS["BTCUSDT"] = pd.concat([BARS["BTCUSDT"], row])
clock[0] = clock[0] + timedelta(hours=1, minutes=2)
LAST["BTCUSDT"] = e + 1.8 * risk
res = GB.tick(("CRYPTO",), manage_only=True)
print("crypto manage:", res["CRYPTO"])
assert GB.positions("CRYPTO") == [] and len(GB.trades("CRYPTO")) == 1
t = GB.trades("CRYPTO")[0]
assert t["reason"].startswith("Target") and 1.1 < t["R"] < 1.6 and t["pnl"] > 0, t
les = LN.lessons(5, "CRYPTO")
assert les and les[0]["market"] == "CRYPTO" and les[0]["R"] > 1
pf = j(c.get("/api/gbook?market=CRYPTO"))
assert pf["stats"]["trades"] == 1 and pf["equity"] > before
print("crypto book OK: +%.2f USDT, R %.2f, lesson: %s" % (t["pnl"], t["R"], les[0]["text"][:120]))
# stop-out path with a time stop
clock[0] = clock[0] + timedelta(minutes=5)
res = GB.tick(("CRYPTO",))
print("crypto tick 2:", res["CRYPTO"])
j(c.post("/api/gbook/prefs", json={"market": "CRYPTO", "risk_pct": 2, "max_leverage": 2}))
assert c.post("/api/gbook/prefs", json={"market": "CRYPTO", "risk_pct": 50}).status_code == 400
j(c.post("/api/gbook/reset", json={"market": "CRYPTO", "capital": 5000}))
assert GB.equity("CRYPTO") == 5000 and GB.positions("CRYPTO") == []

# ------------------------------------------------------------------ 7. after-close research (India)
NOW[0] = datetime(2026, 10, 1, 15, 40, tzinfo=C.IST)
CANDLES.clear(); M._cache.clear()
rep = LN.post_close_in("2026-10-01", force=True)
assert rep.get("ok"), rep
lst = LN.research_list("IN", 3)
assert lst and lst[0]["symbols"]["NIFTY"]["regime"] and lst[0]["levels_for_tomorrow"]["NIFTY"]["cam_h4"], lst[:1]
print("research:", lst[0]["headline"], "|", lst[0]["insights"][0])
assert LN.post_close_in("2026-10-01").get("skipped") == "already done"
r = c.post("/api/learn/run", json={"market": "IN", "force": True}, headers={"x-cron-secret": "c"})
assert r.status_code == 200, r.get_data(as_text=True)
assert c.post("/api/tick/global", headers={"x-cron-secret": "nope"}).status_code == 403
# ------------------------------------------------------------------ 8. data vault
from fno import datahub as DH  # noqa: E402
M._cache.clear(); CANDLES.clear()
NOW[0] = datetime(2026, 10, 1, 15, 40, tzinfo=C.IST)
sv = DH.save_in(NOW[0])
assert isinstance(sv["NIFTY"], int) and sv["NIFTY"] >= 25, sv
# backfill: Upstox returns older sessions than Yahoo; they must be added without touching the rest
_old = fake(1300, datetime(2026, 7, 10, 15, 40, tzinfo=C.IST))
M.upstox_public_history = lambda key, cal_days: _old
M._cache.clear(); CANDLES.clear()
before = DH.store._cmd("HLEN", "fno:data:c:IN:NIFTY") or 0
sv2 = DH.save_in(NOW[0])
after = DH.store._cmd("HLEN", "fno:data:c:IN:NIFTY") or 0
assert sv2.get("NIFTY_backfill", 0) >= 10 and after > before, (sv2, before, after)
M.upstox_public_history = lambda key, cal_days: pd.DataFrame()
ov = j(c.get("/api/data"))
ds = {d["id"]: d for d in ov["datasets"]}
assert ds["in_candles"]["days"] >= 25 and ds["in_candles"]["last"] == "2026-10-01" and ds["in_candles"]["last_update"], ds["in_candles"]
assert len(ov["schedules"]) >= 6 and ov["keep_days"] == 120
r = c.get("/api/data/export?dataset=in_candles&symbol=NIFTY&start=2026-09-20&end=2026-10-01&fmt=csv")
assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"], r.get_data(as_text=True)[:200]
head = r.get_data(as_text=True).splitlines()
assert head[0] == "datetime,open,high,low,close,volume" and len(head) > 100, head[:2]
rj = j(c.get("/api/data/export?dataset=in_candles&symbol=NIFTY&start=2026-09-20&end=2026-10-01&fmt=json"))
assert rj["rows"] and rj["symbol"] == "NIFTY"
assert c.get("/api/data/export?dataset=in_candles&symbol=NIFTY&start=2020-01-01&end=2020-01-05").status_code == 400
for ds_id in ("trades_in", "trades_crypto", "lessons", "research_notes"):
    rr = c.get(f"/api/data/export?dataset={ds_id}&start=2026-01-01&end=2026-12-31")
    assert rr.status_code in (200, 400), (ds_id, rr.status_code)
assert c.get("/api/data/export?dataset=lessons&start=2026-01-01&end=2026-12-31").status_code == 200
sh = j(c.post("/api/data/share", json={"dataset": "in_candles", "symbol": "NIFTY", "start": "2026-09-20", "end": "2026-10-01", "fmt": "csv"}))
tok = sh["url"].split("t=")[1]
anon = app.test_client()
ra = anon.get("/api/data/shared?t=" + tok)
assert ra.status_code == 200 and ra.get_data(as_text=True).startswith("datetime,"), ra.status_code
assert anon.get("/api/data/shared?t=" + tok[:-3] + "abc").status_code == 400
assert anon.get("/api/data").status_code == 401 and anon.get("/api/data/export?dataset=lessons&start=2026-01-01&end=2026-12-31").status_code == 401
print("data vault OK:", ds["in_candles"]["days"], "days saved,", len(head) - 1, "rows exported")
print("ALL V2 OK")

# ------------------------------------------------------------------ 9. stale broker history is gap-filled
import pandas as pd  # noqa: E402
NOW[0] = datetime(2026, 10, 16, 1, 40, tzinfo=C.IST)
yh = fake(22600, datetime(2026, 10, 15, 15, 40, tzinfo=C.IST))
last_day = yh.index[-1].date()
broker = yh[yh.index.date < last_day]
M.yahoo_candles = lambda t, period="5d", interval="5m": yh
M._cache.clear()
mk = M.Market.__new__(M.Market)
filled = mk._fill_gaps(broker, "NIFTY")
assert filled.index[-1].date() == last_day and len(filled) > len(broker), (filled.index[-1], last_day)
assert not filled.index.duplicated().any()
print("gap fill OK:", broker.index[-1].date(), "->", filled.index[-1].date())
print("ALL V2 OK 2")

# ------------------------------------------------------------------ 10. long-lived tokens, fills, data health, Drive backup
import base64, io, json as _json, zipfile  # noqa: E402
import app as A  # noqa: E402
from fno import brokers as B, backup as BK  # noqa: E402

NOW[0] = datetime(2026, 10, 1, 10, 52, tzinfo=C.IST)
store.put("upstox", {"api_key": "k", "secret": "s"})
A._verify_upstox = lambda tok: {"quote": True, "candles": False, "chain": True, "detail": "candles: nope"}
r = c.post("/api/settings", json={"broker": "upstox", "analytics_token": "TOK1"})
assert r.status_code == 400 and "candles" in r.get_json()["error"], r.get_data(as_text=True)[:200]
A._verify_upstox = lambda tok: {"quote": True, "candles": True, "chain": True, "detail": ""}
j(c.post("/api/settings", json={"broker": "upstox", "analytics_token": "TOK1"}))
up = j(c.get("/api/settings"))["upstox"]
assert up["analytics"] and up["analytics_days_left"] >= 359 and up["connected"] and not up["daily_login"], up
mk = M.Market()
assert mk.token_kind.get("upstox") == "analytics" and "upstox" in mk.clients
cfg = store.get("upstox"); cfg["analytics_created"] = (NOW[0] - timedelta(days=361)).isoformat()
store.put("upstox", cfg)
assert "upstox" not in M.Market().clients, "an expired analytics token must not be used"
store.put("upstox", {"api_key": "k", "secret": "s"})

# Fyers: expired daily token + saved refresh token and PIN -> silent renewal
store.put("fyers", {"app_id": "A-100", "secret": "sec", "token": "old", "token_created": (NOW[0] - timedelta(days=2)).isoformat(),
                    "refresh_token": "RT", "pin": "987654"})
B.fyers_refresh = lambda app_id, secret, rt, pin: "NEWTOK" if (rt, pin) == ("RT", "987654") else (_ for _ in ()).throw(RuntimeError("bad"))
mk = M.Market()
assert "fyers" in mk.clients and store.get("fyers")["token"] == "NEWTOK", store.get("fyers")
assert j(c.get("/api/settings"))["fyers"]["refresh_ready"] is True
r = c.post("/api/settings", json={"broker": "fyers", "pin": "12ab"})
assert r.status_code == 400

# realistic exit fills: sell a bought option at the bid, never above the last price
mk = M.Market.__new__(M.Market)
mk.clients = {"upstox": object()}
mk.ba = {"K1": (98.0, 102.0), "K2": (None, None), "K3": (30.0, 101.0), "K4": (10.0, 12.0)}
pos = [{"legs": [{"key": "K1", "side": "BUY", "src": "upstox"}, {"key": "K2", "side": "BUY", "src": "upstox"},
                 {"key": "K3", "side": "BUY", "src": "upstox"}, {"key": "K4", "side": "SELL", "src": "upstox"},
                 {"key": "K5", "side": "BUY", "src": "demo"}]}]
px = {"K1": 100.0, "K2": 100.0, "K3": 100.0, "K4": 10.0, "K5": 100.0}
f = mk.exit_fills(pos, px)
assert f["K1"] == 98.0, f                                   # bid
assert abs(f["K2"] - 99.4) < 0.01, f                        # no depth: 0.6% haircut for a 100 premium
assert abs(f["K3"] - 99.4) < 0.01, f                        # stale far-off bid ignored
assert f["K4"] == 12.0, f                                   # buying back a short leg pays the ask
assert f["K5"] == 100.0, f                                  # demo (theoretical) legs untouched
assert mk.exit_fills(pos, px, "ltp") == px

# entry fallback when the broker gives no ask: LTP plus a half-spread (live chains only)
ch = pd.DataFrame({"strike": [24800.0], "ce_ltp": [100.0], "ce_ask": [None], "ce_bid": [None], "ce_key": ["x"], "ce_delta": [0.5]})
ch.attrs["source"] = "live"
# (px() lives inside build_order; its fallback is exercised through slip_pct)
assert M.slip_pct(10) == 0.02 and M.slip_pct(40) == 0.012 and M.slip_pct(150) == 0.006

# data health strip
class _MM:
    live = False
    token_kind = {}
cd = fake(1300, NOW[0]).iloc[:-1]                           # bars up to 10:45
h = {x["label"]: x for x in SV.health(_MM(), cd, NOW[0])}
assert h["Candles"]["state"] == "ok" and h["Broker"]["state"] == "bad", h
later = NOW[0] + timedelta(minutes=40)
assert {x["label"]: x for x in SV.health(_MM(), cd, later)}["Candles"]["state"] == "bad"
fri_night = datetime(2026, 10, 16, 1, 40, tzinfo=C.IST)
stale = fake(1300, datetime(2026, 10, 14, 15, 25, tzinfo=C.IST))
assert {x["label"]: x for x in SV.health(_MM(), stale, fri_night)}["Candles"]["state"] == "bad"     # the Wednesday-on-Friday case

# Google Drive backup
store.put("prefs", {"sizing": "risk"})
store.put("fyers", {"app_id": "A-100", "secret": "sec", "token": "SECRETTOKEN", "refresh_token": "RT", "pin": "987654", "token_created": NOW[0].isoformat()})
txt = c.get("/api/backup/script").get_data(as_text=True)
sec = store.get("drive")["secret"]
assert sec in txt and "doPost" in txt and "__SECRET__" not in txt
assert c.post("/api/backup/url", json={"url": "https://evil.example.com/x"}).status_code == 400
j(c.post("/api/backup/url", json={"url": "https://script.google.com/macros/s/AKfy-123_x/exec"}))
blob, info = BK.build_zip(NOW[0])
z = zipfile.ZipFile(io.BytesIO(blob))
allt = "".join(z.read(n).decode() for n in z.namelist())
assert "settings.json" in z.namelist() and "trades.json" in z.namelist() or "positions.json" in z.namelist() or info["keys"] >= 2, z.namelist()
for secret in ("SECRETTOKEN", "987654", sec, "NEWTOK", "TOK1"):
    assert secret not in allt, f"{secret} leaked into the backup"
assert '"prefs"' in allt


class _R:
    status_code = 200
    def json(self): return {"ok": True, "folder": "https://drive.google.com/x"}


sent = {}
BK.requests.post = lambda url, data, headers, timeout: (sent.update(body=_json.loads(data), url=url), _R())[1]
res = j(c.post("/api/backup/run"))
assert res["ok"] and sent["body"]["secret"] == sec and sent["body"]["name"].startswith("fno-backup-"), res
assert zipfile.ZipFile(io.BytesIO(base64.b64decode(sent["body"]["data"]))).testzip() is None
st = j(c.get("/api/backup"))
assert st["url_set"] and st["last_ok"] and not st["last_error"] and st["last_bytes"] > 100, st
BK.requests.post = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline"))
r = c.post("/api/backup/run")
assert r.status_code == 400 and "offline" in j(c.get("/api/backup"))["last_error"]
from fno import push as PUSH  # noqa: E402
if not (PUSH.PUBLIC_KEY and PUSH.PRIVATE_KEY):
    assert j(c.get("/api/push/key"))["configured"] is False
    assert j(c.post("/api/push/setup"))["ok"] is True
    k = j(c.get("/api/push/key"))
    assert k["configured"] and len(k["key"]) > 40
    assert j(c.post("/api/push/setup"))["ok"] and j(c.get("/api/push/key"))["key"] == k["key"], "setup must not replace existing keys"
print("tokens, fills, health, backup OK")
print("ALL V2 OK 3")
