"""Count the Upstash commands each recurring job sends (offline, fakeredis + synthetic candles).

Runs: the Indian-market tick (/api/tick) during market hours, the crypto / US / gold tick (gbook.tick, which the
background runner calls every minute around the clock) and one dashboard refresh (/api/dashboard every 30 s while the
page is open). Prints commands per call and a rough monthly total.
Usage: python tests/count_redis.py
"""
import os, sys, json
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim
_url, _tok = upstash_shim.start(6411)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok, FNO_RUNNER="0")

import numpy as np, pandas as pd
from fno import config as C, store, market as M, context as X, markets as MK

rng = np.random.default_rng(5)
NOW = [datetime(2026, 10, 27, 10, 2, tzinfo=C.IST)]
C.now_ist = lambda: NOW[0]
C.today_ist = lambda: NOW[0].date()


def fake(start, end_dt, step=5, n_days=30):
    rows, px = [], start
    t = end_dt - timedelta(days=n_days)
    while t <= end_dt:
        o = px; px *= 1 + rng.normal(0, 0.0008)
        rows.append((t, o, max(o, px) * 1.0003, min(o, px) * 0.9997, px, 100))
        t += timedelta(minutes=step)
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")


def fake_india(start):
    rows, px = [], start
    d = NOW[0].date() - timedelta(days=30)
    while d <= NOW[0].date():
        if d.weekday() < 5:
            t = datetime(d.year, d.month, d.day, 9, 15, tzinfo=C.IST)
            for _ in range(75):
                if t > NOW[0]:
                    break
                o = px; px *= 1 + rng.normal(0, 0.0008)
                rows.append((t, o, max(o, px) * 1.0003, min(o, px) * 0.9997, px, 0))
                t += timedelta(minutes=5)
        d += timedelta(days=1)
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")


M.yahoo_candles = lambda t, period="5d", interval="5m": fake_india(1300 if ".NS" in t else 24800)
M.yahoo_daily = lambda t, period="1mo": pd.DataFrame({"close": [14.0, 13.2]}, index=pd.to_datetime(["2026-10-24", "2026-10-27"]))
M.master = lambda: {"lots": {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}, "eq_keys": {}}
M.upstox_public_history = lambda key, cal_days: pd.DataFrame()
X.global_cues = lambda: {"markets": {}, "avg_equity_chg": 0.0, "notes": []}
X.fetch_news = lambda s=None: {"items": [], "score": 0, "events": [], "notes": []}
X.fii_dii = lambda: None
_c = {}
MK.candles = lambda market, symbol, tf, days=30, ttl=60: _c.setdefault((symbol, tf), fake(100.0, datetime.now(C.IST), {"1h": 60, "4h": 240, "1d": 1440, "15m": 15, "5m": 5}.get(tf, 60), 200))
MK.last_price = lambda market, symbol: float(_c.get((symbol, "1h"), fake(100.0, datetime.now(C.IST), 60, 5)).close.iloc[-1])

from app import app
from fno import service as SV, gbook as GB

store.put("prefs", {**(store.get("prefs") or {}), "auto": True, "auto_syms": ["NIFTY", "BANKNIFTY", "SENSEX"]})
for m in ("CRYPTO", "US", "GOLD"):
    try:
        GB.set_prefs(m, {"auto": True})
    except Exception:
        store.put(f"g_prefs:{m}", {"auto": True})

c = app.test_client()
c.post("/api/login", json={"password": "pw"})


def measure(label, fn, n=5, advance=1):
    out = []
    for k in range(n):
        before = dict(store._counts)
        try:
            fn()
        except Exception as e:
            print(label, "error", e)
        NOW[0] = NOW[0] + timedelta(minutes=advance)
        diff = {key: v - before.get(key, 0) for key, v in store._counts.items() if v - before.get(key, 0)}
        out.append((sum(diff.values()), diff))
    tot = [x[0] for x in out]
    print(f"\n{label}: commands per call {tot}")
    agg = {}
    for _, dd in out[1:]:
        for key, v in dd.items():
            agg[key] = agg.get(key, 0) + v
    for key, v in sorted(agg.items(), key=lambda x: -x[1])[:15]:
        print(f"   {v / max(1, len(out) - 1):6.1f}  {key}")
    return float(np.mean(tot[1:])) if len(tot) > 1 else float(tot[0])


hdr = {"x-cron-secret": "c"}
india = measure("Indian tick (/api/tick, every minute 09:15-15:30)", lambda: c.post("/api/tick", headers=hdr), n=6)
glob = measure("Crypto/US/gold tick (gbook.tick, every minute, 24h)", lambda: GB.tick(), n=6)
dash = measure("Dashboard refresh (/api/dashboard, every 30 s while open)", lambda: c.get("/api/dashboard?symbol=NIFTY"), n=4, advance=0)
port = measure("Portfolio refresh (/api/portfolio)", lambda: c.get("/api/portfolio"), n=4, advance=0)
est = india * 375 * 22 + glob * 1440 * 31 + dash * 2 * 60 * 2 * 22
print(f"\nrough month: Indian ticks {india * 375 * 22:,.0f} + global ticks {glob * 1440 * 31:,.0f} + 2 h/day of page open {dash * 240 * 22:,.0f} = {est:,.0f}")

# ---- a longer run: 2 hours of minute ticks (cache expiry every 15 min), both books
if os.environ.get("LONG"):
    import time as _t
    real = _t.time
    off = [0.0]
    store.time.time = lambda: real() + off[0]
    before = dict(store._counts); n = 0
    for k in range(120):
        NOW[0] = datetime(2026, 10, 28, 9, 30, tzinfo=C.IST) + timedelta(minutes=k)
        off[0] = k * 60.0
        try:
            c.post("/api/tick", headers=hdr); GB.tick()
            if k % 1 == 0: c.get("/api/dashboard?symbol=NIFTY")
        except Exception as e:
            print("err", e)
        n += 1
    diff = {key: v - before.get(key, 0) for key, v in store._counts.items() if v - before.get(key, 0)}
    tot = sum(diff.values())
    print(f"\n2-hour run: {tot} commands = {tot / n:.1f} per minute -> {tot / n * 1440 * 30:,.0f} a month if every minute were like this")
    for key, v in sorted(diff.items(), key=lambda x: -x[1])[:25]:
        print(f"   {v:6d}  {key}")
