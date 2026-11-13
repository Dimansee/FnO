"""Manual orders with your own stop-loss / target / trailing stop, editing them, and the market-session states (13 Nov 2026).
Offline: in-memory DB, synthetic prices. Run: python tests/test_manual_levels.py"""
import os, sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
PG = os.environ.get("TEST_DATABASE_URL")                     # set it to run on the real Postgres store (as on Render) instead of the Redis stand-in
if PG:
    os.environ.update(DATABASE_URL=PG, APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", FNO_RUNNER="0")
    for _k in ("KV_REST_API_URL", "KV_REST_API_TOKEN"):
        os.environ.pop(_k, None)
else:
    import upstash_shim  # noqa: E402
    _url, _tok = upstash_shim.start()
    os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)

import numpy as np
import pandas as pd

from fno import config as C, store, market as M, context as X, service as SV, paper as P
if PG:
    from fno import pgkv
    pgkv._q(f"drop table if exists {pgkv.TABLE}", fetch="count"); pgkv._reset()
    assert store.backend() == "supabase"

rng = np.random.default_rng(7)
NOW = [datetime(2026, 10, 1, 10, 52, tzinfo=C.IST)]          # Thursday; Fri 2 Oct 2026 is an NSE holiday
C.now_ist = lambda: NOW[0]
C.today_ist = lambda: NOW[0].date()


def fake(start, end_dt):
    rows, px = [], start
    d = end_dt.date() - timedelta(days=30)
    while d <= end_dt.date():
        if d.weekday() < 5:
            t = datetime(d.year, d.month, d.day, 9, 15, tzinfo=C.IST)
            for _ in range(75):
                if t > end_dt:
                    break
                o = px
                px *= 1 + rng.normal(0, 0.0006)
                rows.append((t, o, max(o, px) * 1.0003, min(o, px) * 0.9997, px, 0))
                t += timedelta(minutes=5)
        d += timedelta(days=1)
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")


CANDLES = {}
M.yahoo_candles = lambda t, period="5d", interval="5m": CANDLES.setdefault(t, fake(24800, NOW[0]))
M.yahoo_daily = lambda t, period="1mo": pd.DataFrame({"close": [14.0, 13.2]}, index=pd.to_datetime(["2026-09-30", "2026-10-01"]))
M.master = lambda: {"lots": {"NIFTY": 65}, "eq_keys": {}}
M.upstox_public_history = lambda key, cal_days: pd.DataFrame()
X.global_cues = lambda: {"markets": {}, "avg_equity_chg": 0.0, "notes": []}
X.fetch_news = lambda s=None: {"items": [], "score": 0, "events": [], "notes": []}
X.fii_dii = lambda: None
SV._push_safe = lambda *a, **k: None

PX = {}                                                      # option key -> price we want the "market" to show
_orig = M.Market.leg_prices
M.Market.leg_prices = lambda self, positions, spots, vix: {lg["key"]: PX.get(lg["key"], lg["price"]) for p in positions for lg in p["legs"]}

from app import app  # noqa: E402
c = app.test_client()


def j(r, code=None):
    assert r.status_code < 500, r.get_data(as_text=True)[:500]
    if code:
        assert r.status_code == code, (r.status_code, r.get_data(as_text=True)[:300])
    return r.get_json()


assert j(c.post("/api/login", json={"password": "pw"}))["ok"]
tick = lambda: j(c.post("/api/tick", headers={"x-cron-secret": "c"}))   # noqa: E731

# ---------- 1. session states
s = SV.session_info()
assert s["state"] == "open" and s["can_order"] and s["entries_open"], s
s = SV.session_info(NOW[0].replace(hour=14, minute=45))
assert s["state"] == "open" and s["can_order"] and not s["entries_open"], s
s = SV.session_info(NOW[0].replace(hour=15, minute=20))
assert s["state"] == "squareoff" and not s["can_order"], s
s = SV.session_info(NOW[0].replace(hour=16, minute=0))
assert s["state"] == "closed" and s["why"] == "after" and s["next_open"].startswith("2026-10-05T09:15"), s    # Fri holiday + weekend
assert "Mon 05 Oct" in s["detail"], s
s = SV.session_info(NOW[0].replace(hour=8, minute=30))
assert s["state"] == "closed" and s["why"] == "pre" and "today" in s["detail"], s
s = SV.session_info(datetime(2026, 10, 2, 11, 0, tzinfo=C.IST))
assert s["why"] == "holiday" and not s["can_order"], s
s = SV.session_info(datetime(2026, 10, 3, 11, 0, tzinfo=C.IST))
assert s["why"] == "weekend" and "Mon 05 Oct" in s["detail"], s
print("1 session states OK")

d = j(c.get("/api/dashboard?symbol=NIFTY"))
assert d["session"]["state"] == "open" and d["positions"] == [] and d["equity"] == 200000
row = next(r for r in d["chain"] if r["strike"] == d["atm"])
assert "ce_ask" in row and "ce_bid" in row
ch = j(c.get("/api/chain?symbol=NIFTY"))
atm = ch["atm"]
ce = next(r for r in ch["rows"] if r["strike"] == atm)
px0 = ce["ce_ask"] or ce["ce_ltp"]
order = lambda **k: c.post("/api/trade/manual", json={"symbol": "NIFTY", "expiry": ch["expiry"], "strike": atm, "opt": "CE", "side": "BUY", "lots": 1, **k})   # noqa: E731

# ---------- 2. wrong levels are refused, nothing is opened
r = j(order(sl=px0 + 5), 400); assert "Stop-loss" in r["message"], r
r = j(order(target=px0 - 5), 400); assert "Target" in r["message"], r
r = j(order(trail=px0 + 1), 400); assert "Trailing" in r["message"], r
assert not store.positions()
print("2 bad levels refused OK")

# ---------- 3. no levels: stays open whatever the price does (only the 15:15 square-off closes it)
r = j(order(), 200)
assert "No stop-loss or target set" in r["message"], r
p0 = store.positions()[0]
key = p0["legs"][0]["key"]
assert p0["exits_on"] and p0["m_sl"] is None and p0["m_target"] is None and p0["mode"] == "manual"
PX[key] = px0 * 0.2
assert tick() is not None and len(store.positions()) == 1
v = j(c.get("/api/portfolio"))["positions"][0]
assert v["kind"] == "manual" and v["editable"] and v["sl_px"] is None and v["target_px"] is None and v["sl_prem"] is None and v["at_sl_rs"] is None, v
assert v["strike"] == atm and v["opt"] == "CE" and v["bullish"] and abs(v["ltp"] - px0 * 0.2) < 0.01 and v["opened_ts"]
print("3 manual trade without levels is left alone OK")

# ---------- 4. edit: add a stop-loss and a target later
PX[key] = px0
pid = p0["id"]
r = j(c.post("/api/trade/modify", json={"id": pid, "sl": px0 + 3}), 400); assert "exit at once" in r["message"], r
r = j(c.post("/api/trade/modify", json={"id": pid, "sl": round(px0 - 20, 2), "target": round(px0 + 40, 2)}), 200)
assert "Saved" in r["message"], r
p1 = store.positions()[0]
assert p1["m_sl"] == round(px0 - 20, 2) and p1["m_target"] == round(px0 + 40, 2) and p1["m_rev"] != p0["m_rev"]
v = j(c.get("/api/dashboard?symbol=NIFTY"))["positions"][0]
assert v["sl_px"] == p1["m_sl"] and v["target_px"] == p1["m_target"] and v["at_sl_rs"] == round(-20 * 65) and v["at_target_rs"] == round(40 * 65), v
PX[key] = px0 - 10
tick(); assert len(store.positions()) == 1                    # between the levels: nothing happens
PX[key] = px0 - 21
t = tick()
assert not store.positions(), t
tr = store.trades()[-1] if store.trades()[-1]["id"] == pid else next(x for x in store.trades() if x["id"] == pid)
assert tr["reason"].startswith("Stop-loss hit (your level"), tr["reason"]
assert abs(tr["exit_prem"] - (px0 - 21)) < 0.01 and tr["pnl"] < 0
print("4 edit + stop-loss exit OK:", tr["reason"], tr["pnl"])

# ---------- 5. target set at entry
PX.clear()
r = j(order(sl=round(px0 - 15, 2), target=round(px0 + 30, 2)), 200)
assert "SL ₹" in r["message"] and "target ₹" in r["message"], r
p = store.positions()[0]; key = p["legs"][0]["key"]
PX[key] = px0 + 31
tick(); assert not store.positions()
tr = next(x for x in store.trades() if x["id"] == p["id"])
assert tr["reason"].startswith("Target hit (your level"), tr["reason"]
assert tr["pnl"] > 0
print("5 target exit OK:", tr["reason"], tr["pnl"])

# ---------- 6. trailing stop
PX.clear()
j(order(trail=20), 200)
p = store.positions()[0]; key = p["legs"][0]["key"]; e = p["legs"][0]["price"]
assert p["m_sl"] == round(e - 20, 2) and not p["trail_moved"] and p["m_trail"] == 20
PX[key] = e + 50; tick()
p = store.positions()[0]
assert p["m_sl"] == round(e + 30, 2) and p["trail_moved"], p
PX[key] = e + 40; tick()                                      # pulls back but not to the stop: the stop never moves back
p = store.positions()[0]
assert p["m_sl"] == round(e + 30, 2)
PX[key] = e + 29; tick()
assert not store.positions()
tr = next(x for x in store.trades() if x["id"] == p["id"])
assert tr["reason"].startswith("Trailing stop-loss hit"), tr["reason"]
assert tr["gross_pnl"] > 0
print("6 trailing stop OK:", tr["reason"], tr["pnl"])

# ---------- 7. a sold option: stop above, target below
PX.clear()
pe = next(r_ for r_ in ch["rows"] if r_["strike"] == atm)
sp = pe["pe_bid"] or pe["pe_ltp"]
sell = lambda **k: c.post("/api/trade/manual", json={"symbol": "NIFTY", "expiry": ch["expiry"], "strike": atm, "opt": "PE", "side": "SELL", "lots": 1, **k})   # noqa: E731
r = j(sell(sl=sp - 5), 400); assert "above" in r["message"], r
j(sell(sl=round(sp + 25, 2), target=round(sp - 30, 2)), 200)
p = store.positions()[0]; key = p["legs"][0]["key"]
assert p["m_sl"] == round(sp + 25, 2) and p["sl_prem"] == -round(sp + 25, 2) and p["entry_prem"] < 0
v = j(c.get("/api/portfolio"))["positions"][0]
assert v["side"] == "SELL" and v["bullish"] and v["at_sl_rs"] == round(-25 * 65) and v["at_target_rs"] == round(30 * 65), v
PX[key] = sp + 26; tick()
assert not store.positions()
tr = next(x for x in store.trades() if x["id"] == p["id"])
assert tr["reason"].startswith("Stop-loss hit") and tr["gross_pnl"] < 0, tr
print("7 sold option levels OK")

# ---------- 8. rule trades cannot be edited; an edit made during a check is not overwritten
PX.clear()
j(order(sl=round(px0 - 30, 2)), 200)
p = store.positions()[0]
store.update_position({**p, "mode": "signal", "plan": {"strategy": "noise", "opt": "CE", "sl": 1, "target": 9e9, "entry": 2}})
r = j(c.post("/api/trade/modify", json={"id": p["id"], "sl": 1}), 400); assert "rule" in r["message"], r
store.update_position(p)
stale = dict(p)
j(c.post("/api/trade/modify", json={"id": p["id"], "sl": round(px0 - 12, 2)}), 200)
assert SV._edited_meanwhile(stale) and not SV._edited_meanwhile(store.positions()[0])
assert store.positions()[0]["m_sl"] == round(px0 - 12, 2)
r = j(c.post("/api/trade/modify", json={"id": p["id"]}), 200)                          # clearing everything
p = store.positions()[0]
assert p["m_sl"] is None and p["m_target"] is None and p["m_trail"] is None and "no stop-loss" in r["message"]
r = j(c.post("/api/trade/modify", json={"id": "nope", "sl": 1}), 400)
print("8 edit guards OK")

# ---------- 9. live chart shows the manual trade with a side and its own levels
j(c.post("/api/trade/modify", json={"id": p["id"], "sl": round(px0 - 18, 2), "target": round(px0 + 22, 2)}), 200)
lc = j(c.get("/api/chart/live?symbol=NIFTY"))
t = next(x for x in lc["trades"] if x["id"] == p["id"])
assert t["manual"] and t["side"] == "CE" and t["sl_prem"] == round(px0 - 18, 2) and t["target_prem"] == round(px0 + 22, 2) and t["contract"].startswith("BUY"), t
assert t["risk"]["to_stop"] == round(18 * 65), t["risk"]
assert lc["live"] is not None
print("9 live chart manual trade OK")

# ---------- 10. square-off still closes manual trades; after hours: no orders, honest status
NOW[0] = NOW[0].replace(hour=15, minute=16); CANDLES.clear()
tick()
assert not store.positions()
tr = next(x for x in store.trades() if x["id"] == p["id"])
assert tr["reason"].startswith("Square-off"), tr["reason"]
n_before = len(store.trades())
r = j(order(), 400)
assert "Square-off time" in r["message"] and not store.positions(), r
NOW[0] = NOW[0].replace(hour=18, minute=0); CANDLES.clear()
r = j(order(), 400)
assert "Market closed" in r["message"] and "Mon 05 Oct" in r["message"], r
assert len(store.trades()) == n_before and not store.positions()
d = j(c.get("/api/dashboard?symbol=NIFTY"))
assert d["signal"]["signal"] == "MARKET CLOSED" and d["order"] is None and d["session"]["state"] == "closed", d["signal"]["signal"]
assert "Opens Mon 05 Oct" in d["signal"]["checks"][0][1], d["signal"]["checks"]
st = j(c.get("/api/autopilot/status"))
assert st["session"]["state"] == "closed"
assert j(c.get("/api/portfolio"))["session"]["state"] == "closed"
NOW[0] = datetime(2026, 10, 5, 9, 0, tzinfo=C.IST); CANDLES.clear()
d = j(c.get("/api/dashboard?symbol=NIFTY"))
assert d["signal"]["signal"] == "MARKET CLOSED" and d["session"]["why"] == "pre"
print("10 square-off + closed-market states OK")
print("test_manual_levels: all OK")
os._exit(0)
