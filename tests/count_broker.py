"""Count the broker (Upstox / Fyers) HTTP requests the app really sends (offline, fake broker replies).

Simulates N market minutes: the every-minute /api/tick (QStash) + the dashboard open and refreshing every 30 s.
Prints requests per minute and the worst rolling 1-min / 30-min windows against each broker's published limits:
  Upstox  : 50/s, 500/min, 2000 per 30 min            (upstox.com/developer/api-documentation/rate-limiting)
  Fyers   : Data APIs, Standard plan from 16 Oct 2026: 5/s, 50/min, 5,000/day; >3 per-minute breaches = blocked for the day
Usage: python tests/count_broker.py [upstox|fyers] [minutes]
"""
import os, sys, time as _rt
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim
_url, _tok = upstash_shim.start(6417)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok, FNO_RUNNER="0")

import numpy as np, pandas as pd, requests
from fno import config as C, store, market as M, context as X, markets as MK, brokers as B

BROKER = (sys.argv[1] if len(sys.argv) > 1 else "fyers").lower()
MINUTES = int(sys.argv[2]) if len(sys.argv) > 2 else 30

# ---------------- fake clock shared by the app (IST) and the caches (epoch) ----------------
T0 = datetime(2026, 11, 12, 10, 0, tzinfo=C.IST)
OFF = [0.0]
REAL0 = _rt.time()
fake_epoch = lambda: T0.timestamp() + OFF[0]
C.now_ist = lambda: datetime.fromtimestamp(fake_epoch(), C.IST)
C.today_ist = lambda: C.now_ist().date()
M._t.time = fake_epoch
B._tm.time = fake_epoch
B._tm.sleep = lambda s: OFF.__setitem__(0, OFF[0] + max(0.0, s))   # pacing sleeps move the fake clock
store.time.time = fake_epoch

rng = np.random.default_rng(3)


def bars(step_min, days, start=24800.0):
    rows, px = [], start
    d = C.today_ist() - timedelta(days=days)
    now = C.now_ist()
    while d <= now.date():
        if d.weekday() < 5:
            t = datetime(d.year, d.month, d.day, 9, 15, tzinfo=C.IST)
            while t.time() < C.MARKET_CLOSE and t <= now:
                o = px; px *= 1 + rng.normal(0, 0.0007)
                rows.append([t, o, max(o, px) * 1.0003, min(o, px) * 0.9997, px, 1000])
                t += timedelta(minutes=step_min)
        d += timedelta(days=1)
    return rows


# ---------------- fake broker replies + request log ----------------
LOG = []        # (fake epoch, broker, endpoint)


class Resp:
    def __init__(self, j, code=200):
        self._j, self.status_code, self.text, self.content = j, code, "", b"x"

    def json(self):
        return self._j


def ep_name(url):
    for k in ("market-quote", "option/contract", "option/chain", "historical-candle/intraday", "historical-candle",
              "/quotes", "options-chain-v3", "/history"):
        if k in url:
            return k.strip("/")
    return url


def fake_get(self, url, params=None, timeout=None, **kw):
    p = params or {}
    if "api.upstox.com" in url:
        LOG.append((fake_epoch(), "upstox", ep_name(url)))
        if "market-quote" in url:
            ks = p["instrument_key"].split(",")
            return Resp({"data": {k: {"instrument_token": k, "last_price": 100.0, "net_change": 1.0, "depth": {}} for k in ks}})
        if "option/contract" in url:
            e = [(C.today_ist() + timedelta(days=d)).isoformat() for d in (5, 12, 19)]
            return Resp({"data": [{"expiry": x, "lot_size": 75} for x in e]})
        if "option/chain" in url:
            return Resp({"data": [{"strike_price": 24500 + 50 * i, "underlying_spot_price": 24800,
                                    "call_options": {"instrument_key": f"NSE_FO|C{i}", "market_data": {"ltp": 100, "oi": 10}},
                                    "put_options": {"instrument_key": f"NSE_FO|P{i}", "market_data": {"ltp": 90, "oi": 10}}} for i in range(13)]})
        if "intraday" in url:
            step = 1 if url.endswith("/1") else 5
            rows = [r for r in bars(step, 0) if r[0].date() == C.today_ist()]
            return Resp({"data": {"candles": [[r[0].isoformat()] + r[1:] for r in rows]}})
        rows = [r for r in bars(5, 30) if r[0].date() < C.today_ist()]
        return Resp({"data": {"candles": [[r[0].isoformat()] + r[1:] for r in rows]}})
    if "fyers.in" in url:
        LOG.append((fake_epoch(), "fyers", ep_name(url)))
        if url.endswith("/quotes"):
            return Resp({"s": "ok", "d": [{"n": k, "v": {"lp": 100.0, "prev_close_price": 99.0}} for k in p["symbols"].split(",")]})
        if "options-chain" in url:
            ex = [{"date": (C.today_ist() + timedelta(days=d)).strftime("%d-%m-%Y"), "expiry": str(1000 + d)} for d in (5, 12, 19)]
            oc = [{"strike_price": -1, "ltp": 24800}] + [{"strike_price": 24500 + 50 * i, "option_type": t, "symbol": f"NSE:X{i}{t}", "ltp": 100, "oi": 10, "lot_size": 75}
                                                        for i in range(13) for t in ("CE", "PE")]
            return Resp({"s": "ok", "data": {"expiryData": ex, "optionsChain": oc}})
        if "/history" in url:
            step = int(p["resolution"])
            fr, to = p["range_from"], p["range_to"]
            rows = [r for r in bars(step, 30) if fr <= r[0].date().isoformat() <= to]
            return Resp({"s": "ok", "candles": [[int(r[0].timestamp())] + r[1:] for r in rows]})
    raise RuntimeError(f"unexpected outbound call in test: {url}")


requests.Session.get = fake_get

# Everything that is not the broker: offline stand-ins (same as tests/count_redis.py)
def _yh(t, period="5d", interval="5m"):
    rows = bars(1 if interval == "1m" else 5, 30)
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")
M.yahoo_candles = _yh
M.yahoo_daily = lambda t, period="1mo": pd.DataFrame({"close": [14.0, 13.2]}, index=pd.to_datetime(["2026-11-11", "2026-11-12"]))
M.master = lambda: {"lots": {"NIFTY": 75, "BANKNIFTY": 35, "SENSEX": 20}, "eq_keys": {}}
M.upstox_public_history = lambda key, cal_days: pd.DataFrame()
X.global_cues = lambda: {"markets": {}, "avg_equity_chg": 0.0, "notes": []}
X.fetch_news = lambda s=None: {"items": [], "score": 0, "events": [], "notes": []}
X.fii_dii = lambda: None
MK.candles = lambda market, symbol, tf, days=30, ttl=60: pd.DataFrame(bars(5, 3), columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")
try:
    from fno import recorder as R
    R._nse_get = lambda url: (_ for _ in ()).throw(RuntimeError("offline"))
except Exception:
    pass

from app import app
from fno import service as SV

created = C.now_ist().isoformat(timespec="seconds")
if BROKER == "upstox":
    store.put("upstox", {"token": "t", "token_created": created})
else:
    store.put("fyers", {"token": "t", "app_id": "APP-100", "token_created": created})
store.put("broker_pref", BROKER)
store.put("prefs", {**(store.get("prefs") or {}), "auto": True, "auto_syms": ["NIFTY", "BANKNIFTY", "SENSEX"]})
try:
    from fno import scalp as SC
    SC.save_settings({"on": True, "syms": ["NIFTY", "BANKNIFTY", "SENSEX"]})
except Exception:
    pass

c = app.test_client()
c.post("/api/login", json={"password": "pw"})
hdr = {"x-cron-secret": "c"}

errors = Counter()
for k in range(MINUTES):
    OFF[0] = k * 60.0
    for path, method in (("/api/tick", "post"), ("/api/dashboard?symbol=NIFTY", "get")):
        r = getattr(c, method)(path, headers=hdr)
        if r.status_code >= 400:
            errors[(path, r.status_code)] += 1
    OFF[0] = k * 60.0 + 30
    c.get("/api/dashboard?symbol=NIFTY")

mine = [x for x in LOG if x[1] == BROKER]
print(f"\n== {BROKER}: {len(mine)} requests in {MINUTES} simulated minutes = {len(mine) / MINUTES:.1f}/min "
      f"(tick every minute + dashboard open, refresh every 30 s)")
for e, n in Counter(x[2] for x in mine).most_common():
    print(f"   {n / MINUTES:6.1f}/min  {e}")
ts = sorted(x[0] for x in mine)
def worst(win):
    j, best = 0, 0
    for i, t in enumerate(ts):
        while ts[j] < t - win:
            j += 1
        best = max(best, i - j + 1)
    return best
lim = {"upstox": (50, 500, 2000), "fyers": (5, 50, None)}[BROKER]
w1, w60, w1800 = worst(1), worst(60), worst(1800)
print(f"   worst 1 s window: {w1} (limit {lim[0]})   worst 60 s: {w60} (limit {lim[1]})   worst 30 min: {w1800 if MINUTES >= 30 else '-'}"
      f"{f' (limit {lim[2]})' if lim[2] else ''}")
if BROKER == "fyers":
    print(f"   whole market day (375 min) at this rate: ~{len(mine) / MINUTES * 375:,.0f} (Standard plan limit 5,000/day)")
else:
    print(f"   per 30 min at this rate: ~{len(mine) / MINUTES * 30:,.0f} (limit 2,000)")
if errors:
    print("   http errors:", dict(errors))
