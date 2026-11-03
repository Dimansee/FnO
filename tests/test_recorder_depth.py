"""Recorder order-book capture (round 80 follow-up): best bid/ask quantity in every chain row, and ONE full-market-quote call per
snapshot for the ATM +- 2 strikes (Upstox only) - never during an Upstox cool-down, and never able to break the chain snapshot.

Run: PYTHONPATH=. python tests/test_recorder_depth.py   (needs `pip install "fakeredis[lua]"`)
"""
import os, sys
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(6415)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)

import pandas as pd

from fno import config as C, recorder as R, brokers as BR

CALLS = {"chain": 0, "depth": 0}


class FakeUpstox(BR.Upstox):
    name = "upstox"

    def __init__(self):
        pass

    def chain(self, key, expiry):
        CALLS["chain"] += 1
        sp = 24810.0 if "Nifty 50" in key else 52030.0
        st = 50 if sp < 30000 else 100
        rows = []
        for k in range(-20, 21):
            s_ = round(sp / st) * st + k * st
            rows.append({"strike": float(s_), "spot": sp, "ce_key": f"CE|{s_}", "pe_key": f"PE|{s_}", "ce_ltp": 100.0, "pe_ltp": 90.0,
                         "ce_bid": 99.5, "ce_ask": 100.5, "pe_bid": 89.5, "pe_ask": 90.5, "ce_oi": 1e5, "pe_oi": 2e5, "ce_vol": 1e6,
                         "pe_vol": 2e6, "ce_iv": 12.0, "pe_iv": 13.0, "ce_bq": 650.0, "ce_aq": 1300.0, "pe_bq": 975.0, "pe_aq": 325.0})
        return pd.DataFrame(rows)

    def _get(self, path, params=None):                 # the real pacing / cool-down wrapper is bypassed only for the HTTP part
        if BR.upstox_cooldown_left() > 0:
            raise BR.RateLimited("cool-down")
        CALLS["depth"] += 1
        keys = params["instrument_key"].split(",")
        return {k: {"instrument_token": k, "last_price": 100.0, "total_buy_quantity": 50_000, "total_sell_quantity": 80_000,
                    "volume": 1_000_000, "oi": 100_000,
                    "depth": {"buy": [{"price": 99.5 - i * 0.05, "quantity": 650 * (i + 1), "orders": 3} for i in range(5)],
                              "sell": [{"price": 100.5 + i * 0.05, "quantity": 325 * (i + 1), "orders": 2} for i in range(5)]}} for k in keys}


class FakeMarket:
    live = True
    source = "LIVE · upstox"

    def __init__(self):
        self.br = FakeUpstox()

    def key(self, sym):
        return {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank"}[sym]

    def expiries_and_lot(self, sym):
        return [date(2026, 11, 9), date(2026, 11, 17)], 65


C.today_ist = lambda: date(2026, 11, 4)
now = datetime(2026, 11, 4, 10, 0, tzinfo=C.IST)
st = R.snapshot(now, market=FakeMarket())
assert st["saved"] == 4 and st["depth_rows"] == 2 * 10, st                    # 2 symbols x 5 strikes x CE/PE
assert CALLS["depth"] == 1, CALLS                                              # ONE order-book call for both symbols
d = R.day("2026-11-04")
assert len(d["snapshots"][0]["rows"][0]) == len(R.FIELDS) == 17 and d["snapshots"][0]["rows"][0][13:] == [650.0, 1300.0, 975.0, 325.0]
dp = d["depth"]
assert len(dp) == 2 and {x["sym"] for x in dp} == {"NIFTY", "BANKNIFTY"} and dp[0]["exp"] == "2026-11-09", dp
row = dp[0]["rows"][0]
assert row[3] == 50_000 and row[4] == 80_000 and len(row[7]) == 5 and row[7][0][1] == 650, row
assert len({r[0] for r in dp[0]["rows"]}) == 5
print("depth capture OK:", st)

BR._ux["until"] = BR._tm.time() + 120                                         # Upstox cool-down: no order-book call at all
st2 = R.snapshot(now.replace(minute=5), market=FakeMarket())
assert st2["depth_rows"] == 0 and CALLS["depth"] == 1, (st2, CALLS)
BR._ux["until"] = 0.0
print("cool-down respected OK")


class Boom(FakeUpstox):
    def depth(self, keys):
        raise BR.RateLimited("429")


fm = FakeMarket(); fm.br = Boom()
st3 = R.snapshot(now.replace(minute=10), market=fm)
assert st3["saved"] == 4 and st3["depth_rows"] == 0 and any("depth" in e for e in st3["errors"]), st3
print("a depth failure never stops the chain snapshot OK")
