"""Offline end-to-end test: in-memory DB, synthetic prices, fake brokers."""
import os, sys
from datetime import date, datetime, timedelta

os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", SUPABASE_URL="x", SUPABASE_KEY="k", DB_SECRET="d")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import pandas as pd

from fno import config as C, store, market as M, context as X, brokers as B, strategy as S

# ---------- in-memory store ----------
DB = {"settings": {}, "pos": {}, "trades": {}}
store.get = lambda k, d=None: DB["settings"].get(k, d)
store.get_many = lambda ks: {k: DB["settings"][k] for k in ks if k in DB["settings"]}
store.put = lambda k, v: DB["settings"].__setitem__(k, v)
store.delete_setting = lambda k: DB["settings"].pop(k, None)
store.positions = lambda: [dict(v) for v in DB["pos"].values()]
store.insert_position = lambda p: DB["pos"].__setitem__(p["id"], p)
store.update_position = lambda p: DB["pos"].__setitem__(p["id"], p)
store.take_position = lambda i: DB["pos"].pop(i, None)
store.clear_positions = lambda: DB["pos"].clear()
store.trades = lambda: list(DB["trades"].values())
store.insert_trade = lambda r: DB["trades"].__setitem__(r["id"], r)
store.clear_trades = lambda: DB["trades"].clear()
store.ping = lambda: True

# ---------- synthetic market ----------
rng = np.random.default_rng(5)
NOW = [datetime.combine(C.today_ist(), datetime.min.time().replace(hour=11, minute=2), tzinfo=C.IST)]
C.now_ist = lambda: NOW[0]
C.today_ist = lambda: NOW[0].date()


def fake(start, end_dt, trend=0.0006):
    rows, px = [], start
    d = end_dt.date() - timedelta(days=8)
    while d <= end_dt.date():
        if d.weekday() < 5:
            t = datetime(d.year, d.month, d.day, 9, 15, tzinfo=C.IST)
            for _ in range(75):
                if t > end_dt:
                    break
                o = px
                px *= 1 + trend + rng.normal(0, 0.0008)
                rows.append((t, o, max(o, px) * 1.0003, min(o, px) * 0.9997, px, 0))
                t += timedelta(minutes=5)
        d += timedelta(days=1)
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")


CANDLES = {}
M.yahoo_candles = lambda t, period="5d", interval="5m": CANDLES.setdefault(t, fake(1300 if ".NS" in t else 24800, NOW[0]))
M.yahoo_daily = lambda t, period="1mo": pd.DataFrame({"close": [14.0, 13.2]}, index=pd.to_datetime(["2026-09-30", "2026-10-01"]))
M.master = lambda: {"lots": {"NIFTY": 65}, "eq_keys": {}}
X.global_cues = lambda: {"markets": {"S&P 500": {"last": 6000, "chg_pct": 0.8, "date": "2026-10-01"}}, "avg_equity_chg": 0.8, "notes": []}
X.fetch_news = lambda s=None: {"items": [{"title": "Nifty rallies <b>", "link": "http://x", "when": None, "score": 4}], "score": 4, "events": [], "notes": []}
X.fii_dii = lambda: None

from app import app  # noqa: E402

c = app.test_client()


def j(r):
    assert r.status_code < 500, r.get_data(as_text=True)[:500]
    return r.get_json()


assert c.get("/api/dashboard").status_code == 401
assert c.post("/api/login", json={"password": "bad"}).status_code == 401
assert j(c.post("/api/login", json={"password": "pw"}))["ok"]
assert "F&amp;O" in c.get("/").get_data(as_text=True)

d = j(c.get("/api/dashboard?symbol=NIFTY"))
print("signal:", d["signal"]["signal"], "score", d["signal"]["score"], "source", d["source"], "lot", d["lot"], "cash", d["cash"])
for ok, t in d["signal"]["checks"]:
    print("   ", ok, t)
print("order:", d["order"] and {k: d["order"][k] for k in ("type", "entry_prem", "sl_prem", "target_prem", "lots")})
assert len(d["chart"]) > 20

r = c.post("/api/trade/signal", json={"symbol": "NIFTY"})
print("place signal:", r.status_code, r.get_json())

ch = j(c.get("/api/chain?symbol=NIFTY"))
print("chain rows", len(ch["rows"]), "atm", ch["atm"], "expiries", ch["expiries"][:2], "src", ch["source"])
r = j(c.post("/api/trade/manual", json={"symbol": "NIFTY", "expiry": ch["expiry"], "strike": ch["atm"], "opt": "PE", "side": "BUY", "lots": 1}))
print("manual:", r)

p = j(c.get("/api/portfolio"))
print("portfolio cash", p["cash"], "positions", len(p["positions"]), "unreal", round(p["unrealised"], 2))

# advance time 50 minutes -> signal trade should hit target / breakeven / time stop
NOW[0] = NOW[0] + timedelta(minutes=50)
CANDLES.clear()
assert c.post("/api/tick").status_code == 403
t = j(c.post("/api/tick", headers={"x-cron-secret": "c"}))
print("tick:", t)
# square-off
NOW[0] = NOW[0].replace(hour=15, minute=16)
CANDLES.clear()
print("tick sq-off:", j(c.post("/api/tick", headers={"x-cron-secret": "c"})))
p = j(c.get("/api/portfolio"))
print("after: positions", len(p["positions"]), "trades", len(p["history"]), "stats", {k: v for k, v in p["stats"].items() if k != "equity"})
pid = None

print("context:", list(j(c.get("/api/context?symbol=RELIANCE")).keys()))
print("stock dash:", j(c.get("/api/dashboard?symbol=RELIANCE"))["signal"]["signal"])

# settings + broker login redirects
j(c.post("/api/settings", json={"broker": "upstox", "id": "KEY", "secret": "SEC"}))
s = j(c.get("/api/settings"))
print("settings:", s["upstox"], s["fyers"]["configured"])
r = c.get("/api/upstox/login")
print("upstox login ->", r.status_code, r.headers["Location"][:90])
r = c.get("/api/upstox/callback?code=x&state=WRONG")
print("bad state ->", r.headers["Location"])
B.upstox_exchange = lambda *a: "TOKEN123"
st = DB["settings"]["upstox"]["state"]
r = c.get(f"/api/upstox/callback?code=x&state={st}")
print("good callback ->", r.headers["Location"], DB["settings"]["upstox"].get("token"))

# with a 'live' (fake) Upstox broker
class FakeUp(B.Upstox):
    def __init__(self, tok): self.name = "upstox"
    def candles(self, key): return M.yahoo_candles("^NSEI")
    def quotes(self, keys): return {k: {"ltp": 13.0 if "VIX" in k else 101.5, "prev_close": 12.5} for k in keys}
    def expiries(self, key): return ([C.today_ist() + timedelta(days=5), C.today_ist() + timedelta(days=12)], 65)
    def chain(self, key, exp):
        ch = M.theoretical_chain("NIFTY", exp, 24900, 13)
        ch["ce_oi"] = np.linspace(1e5, 5e5, len(ch)); ch["pe_oi"] = np.linspace(5e5, 1e5, len(ch))
        ch["ce_key"] = ["NSE_FO|C%d" % i for i in range(len(ch))]; ch["pe_key"] = ["NSE_FO|P%d" % i for i in range(len(ch))]
        return ch
B.Upstox = FakeUp
NOW[0] = NOW[0].replace(hour=11, minute=0) + timedelta(days=0)
CANDLES.clear()
d = j(c.get("/api/dashboard?symbol=NIFTY"))
print("live dash:", d["source"], d["live"], "PCR factor", [f for f in d["signal"]["factors"] if "Put" in f["factor"]][0]["value"])
ch = j(c.get("/api/chain?symbol=NIFTY"))
print("live chain stats", ch["stats"])
r = j(c.post("/api/trade/manual", json={"symbol": "NIFTY", "expiry": ch["expiry"], "strike": ch["atm"], "opt": "CE", "side": "BUY", "lots": 1}))
print(r)
p = j(c.get("/api/portfolio"))
print("live portfolio:", [(x["legs"][0]["src"], x["current_prem"]) for x in p["positions"]])

bt = j(c.post("/api/backtest", json={"symbol": "NIFTY", "min_score": 2, "capital": 200000}))
print("backtest keys", list(bt.keys()), bt.get("summary", {}).get("trades"))
print("reset", j(c.post("/api/account/reset", json={"capital": 300000})))
print("ALL OK")
