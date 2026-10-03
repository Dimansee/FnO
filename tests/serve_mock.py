"""Serve the app locally on :8765 with synthetic data (for UI checks)."""

import os, sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402  local stand-in for Upstash Redis
_url, _tok = upstash_shim.start()
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)

import numpy as np
import pandas as pd

from fno import config as C, store, market as M, context as X, brokers as B, strategy as S

# ---------- synthetic market ----------
rng = np.random.default_rng(5)
NOW = [datetime(2026, 10, 1, 10, 52, tzinfo=C.IST)]  # a Thursday, 7 min after the 10:45 check
C.now_ist = lambda: NOW[0]
C.today_ist = lambda: NOW[0].date()


def fake(start, end_dt, trend=0.0006):
    rows, px = [], start
    d = end_dt.date() - timedelta(days=30)   # the noise band needs 14+ past sessions
    while d <= end_dt.date():
        if d.weekday() < 5:
            t = datetime(d.year, d.month, d.day, 9, 15, tzinfo=C.IST)
            for _ in range(75):
                if t > end_dt:
                    break
                o = px
                px *= 1 + (trend if d == end_dt.date() else 0) + rng.normal(0, 0.0008)
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


@app.after_request
def relax(resp):  # local http: drop the Secure cookie flag
    sc = resp.headers.get("Set-Cookie")
    if sc:
        resp.headers["Set-Cookie"] = sc.replace("; Secure", "")
    return resp


app.run(port=8765)
