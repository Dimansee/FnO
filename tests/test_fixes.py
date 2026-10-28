"""Review fixes of 28 Oct 2026, on the real Postgres path (fno/pgkv.py).
Needs TEST_DATABASE_URL (a local postgres) - skipped otherwise.
Covers: atomic close (one winner, trade always saved), encrypted broker secrets, login rate limit, fail-closed
SESSION_SECRET, gated deep health, tick / entry locks, square-off without a live price, auto-entry data guard."""
import os, sys, json, threading
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
URL = os.environ.get("TEST_DATABASE_URL")
if not URL:
    print("skip: TEST_DATABASE_URL not set"); sys.exit(0)
os.environ.update(DATABASE_URL=URL, APP_PASSWORD="pw", SESSION_SECRET="secret-1", CRON_SECRET="c", FNO_RUNNER="0")
for k in ("KV_REST_API_URL", "KV_REST_API_TOKEN"):
    os.environ.pop(k, None)

import pandas as pd
from fno import pgkv, config as C, store, paper as P, service as SV
pgkv._q(f"drop table if exists {pgkv.TABLE}", fetch="count"); pgkv._reset()
assert store.backend() == "supabase"

# ---- 1. atomic close: 8 threads close the same position, exactly one trade is written, the position is gone
pos = {"id": "t1", "symbol": "NIFTY", "legs": [{"key": "K1", "side": "BUY", "price": 100.0, "strike": 25000, "opt": "CE"}],
       "lots": 1, "lot": 65, "qty": 65, "expiry": "2026-11-03", "entry_prem": 100.0, "margin": 6500, "opened": "2026-10-28T10:00:00+05:30",
       "mode": "signal", "source": "test", "plan": {"strategy": "noise"}}
store.insert_position(pos)
res = []
ts = [threading.Thread(target=lambda: res.append(P.close_position("t1", {"K1": 110.0}, "Target"))) for _ in range(8)]
[t.start() for t in ts]; [t.join() for t in ts]
wins = [r for r in res if r]
assert len(wins) == 1, res
assert pgkv.execute("HGET", store.POSITIONS, "t1") is None
tr = json.loads(pgkv.execute("HGET", store.TRADES, "t1"))
assert tr["reason"] == "Target" and tr["exit_prem"] == 110.0 and tr["gross_pnl"] == 650.0
assert P.close_position("t1", {"K1": 1.0}, "again") is None
print("1 atomic close OK")

# a failure while building the trade record must leave the position open (nothing lost)
store.insert_position({**pos, "id": "t2"})
try:
    store.close_position_into_trades("t2", lambda p: 1 / 0)
except ZeroDivisionError:
    pass
assert pgkv.execute("HGET", store.POSITIONS, "t2") is not None and pgkv.execute("HGET", store.TRADES, "t2") is None
store.take_position("t2")
print("1b failed close keeps the position OK")

# ---- 2. secrets encrypted at rest, transparent to callers, old plaintext upgraded on read
store.put("upstox", {"token": "abc123"})
raw = pgkv.execute("HGET", store.SETTINGS, "upstox")
assert raw.startswith("enc:") and "abc123" not in raw, raw[:20]
store._mem.clear()
assert store.get("upstox") == {"token": "abc123"} and store.get_many(["upstox", "nope"]) == {"upstox": {"token": "abc123"}}
pgkv.execute("HSET", store.SETTINGS, "fyers", json.dumps({"app_id": "x", "token": "plain"}))
store._mem.clear()
assert store.get("fyers")["token"] == "plain"
assert pgkv.execute("HGET", store.SETTINGS, "fyers").startswith("enc:")
store.put("prefs", {"auto": True})
assert pgkv.execute("HGET", store.SETTINGS, "prefs") == '{"auto":true}'            # ordinary settings stay plain
C.SESSION_SECRET = "changed"; store._mem.clear()
assert store.get("upstox") is None                                                    # unreadable -> treated as not set
C.SESSION_SECRET = "secret-1"; store._mem.clear()
assert store.get("upstox") == {"token": "abc123"}
print("2 encrypted secrets OK")

# ---- 3. login rate limit, fail-closed secret, deep health gate, generic errors
from app import app
c = app.test_client()
for i in range(10):
    assert c.post("/api/login", json={"password": "bad"}).status_code == 401
r = c.post("/api/login", json={"password": "pw"})
assert r.status_code == 429, r.status_code
pgkv.execute("DEL", *[k for k in pgkv.execute("SCAN", "0", "MATCH", "fno:login_fail:*", "COUNT", 100)[1]])
assert c.post("/api/login", json={"password": "pw"}).status_code == 200
assert c.get("/api/portfolio").status_code != 401
C.SESSION_SECRET = ""
assert c.get("/api/portfolio").status_code == 401 and c.post("/api/login", json={"password": "pw"}).status_code == 503
C.SESSION_SECRET = "secret-1"
anon = app.test_client()
h = anon.get("/api/health?deep=1").get_json()
assert "yahoo_vix" not in h and h["ok"], h.keys()
assert anon.get("/api/journal/day?date=2026-10-27").status_code == 401                # data endpoints locked by default
print("3 auth / health / data lock OK")

# ---- 4. locks: a tick while another runs is skipped; two entries on one index never run together
assert SV._db_lock("tick", 60) is not None
C.now_ist = lambda: datetime(2026, 10, 28, 11, 0, tzinfo=C.IST)
assert SV.tick() == {"skipped": "another tick is running"}
pgkv.execute("DEL", "fno:lock:tick")
inside, peak = [0], [0]
orig = SV._place_signal


def slow(*a, **k):
    inside[0] += 1; peak[0] = max(peak[0], inside[0])
    __import__("time").sleep(0.3)
    inside[0] -= 1
    return True, "ok"


SV._place_signal = slow
ts = [threading.Thread(target=lambda: SV.place_signal("NIFTY", auto=True)) for _ in range(4)]
[t.start() for t in ts]; [t.join() for t in ts]
assert peak[0] == 1, peak
SV._place_signal = orig
print("4 tick / entry locks OK")

# ---- 5. data guard for automatic entries
now = datetime(2026, 10, 28, 11, 0, tzinfo=C.IST)
idx = pd.date_range(now - timedelta(minutes=60), periods=10, freq="5min")
g = {"vix": {"level": 14.0, "assumed": True}, "candles": pd.DataFrame({"close": range(10)}, index=idx), "now": now}
assert "VIX" in SV._data_problem(g)
g["vix"] = {"level": 13.2}
old = pd.date_range(now - timedelta(minutes=90), periods=6, freq="5min")
assert "minutes old" in SV._data_problem({**g, "candles": pd.DataFrame({"close": range(6)}, index=old)})
assert SV._data_problem(g) is None
print("5 auto-entry data guard OK")


# ---- 6. square-off is guaranteed even with no live price: closed at the last price seen
class NoPrice:
    errors = []
    br = None

    def vix(self): return {"level": 13.0}
    def spot(self, s): return None
    def leg_prices(self, pos, spots, v): return {}
    def candles(self, s): return pd.DataFrame()
    def exit_fills(self, pos, prices, mode): return prices


store.insert_position({**pos, "id": "t3", "last_px": {"K1": 123.4}, "last_spot": 25010.0})
C.now_ist = lambda: datetime(2026, 10, 28, 15, 16, tzinfo=C.IST)
out = SV.manage_positions(NoPrice())
assert [x["id"] for x in out] == ["t3"], out
t3 = json.loads(pgkv.execute("HGET", store.TRADES, "t3"))
assert t3["exit_prem"] == 123.4 and t3["price_missing"] and "last known price" in t3["reason"]
store.insert_position({**pos, "id": "t4"})                      # before 15:15: stays open (only an alert)
C.now_ist = lambda: datetime(2026, 10, 28, 14, 0, tzinfo=C.IST)
assert SV.manage_positions(NoPrice()) == [] and pgkv.execute("HGET", store.POSITIONS, "t4")
C.now_ist = lambda: datetime(2026, 10, 29, 9, 40, tzinfo=C.IST)  # yesterday's position, next morning: closed
assert [x["id"] for x in SV.manage_positions(NoPrice())] == ["t4"]
print("6 guaranteed square-off OK")
print("test_fixes: all OK")
