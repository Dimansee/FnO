"""Offline test: the day's journal context is not written from yesterday's candles (bug seen 4-5 Nov 2026). Run: python tests/test_journal_ctx.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(port=6431)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)
os.environ.pop("RENDER", None)

import pandas as pd  # noqa: E402
from fno import journal as J  # noqa: E402

ix = pd.date_range("2026-11-04 09:15", "2026-11-04 15:25", freq="5min", tz="Asia/Kolkata")
yday = pd.DataFrame({"open": 23900.0, "high": 23950.0, "low": 23850.0, "close": 23900.0, "volume": 0}, index=ix)
g = {"candles": yday, "now": pd.Timestamp("2026-11-05 09:16", tz="Asia/Kolkata"), "spot": 24378.0}
assert J.write_context("NIFTY", g, {}, None, {}) is None          # only yesterday's bars -> wait
assert J._get("2026-11-05", "ctx:NIFTY") is None                   # nothing stored, so the next tick retries
g["candles"] = yday.iloc[:0]
assert J.write_context("NIFTY", g, {}, None, {}) is None          # no candles at all -> wait
print("journal context guard tests passed")
