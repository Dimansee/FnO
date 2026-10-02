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


from app import app
import fno.service as SV
# login cookie needs Secure; serve over http locally -> relax
import app as A
_orig=A.login
@app.after_request
def relax(resp):
    sc=resp.headers.get("Set-Cookie")
    if sc: resp.headers["Set-Cookie"]=sc.replace("; Secure","")
    return resp
app.run(port=8765)
