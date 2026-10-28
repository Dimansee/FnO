"""Offline test: crypto/US/gold money backtest (fno/gbacktest.py) and its API. Run: python tests/test_gbacktest.py"""
import os, sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(port=6423)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)
os.environ.pop("RENDER", None)

import numpy as np
import pandas as pd

from fno import gbacktest as GBT, gbook as GB, gorders as GO, markets as MK  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 10, 1, tzinfo=UTC)


def tr(sym, h_in, h_out, R, entry=100.0, sl=95.0):
    return {"symbol": sym, "entry_ts": pd.Timestamp(T0 + timedelta(hours=h_in)), "exit_ts": pd.Timestamp(T0 + timedelta(hours=h_out)),
            "entry": entry, "sl0": sl, "R": R}


# 1. money_sim: compounding, max-open limit, free-cash limit
m = GBT.money_sim([tr("A", 0, 1, 2.0), tr("B", 2, 3, -1.0)], 1000, 1.0, 5.0, 4)
# trade 1 risks 10 (1% of 1000) -> +20; trade 2 risks 1% of 1020 = 10.2 -> -10.2
assert abs(m["final"] - (1000 + 20 - 10.2)) < 1e-6, m
assert m["trades"] == 2 and m["skipped_full"] == 0

m = GBT.money_sim([tr("A", 0, 10, 1.0), tr("B", 1, 10, 1.0), tr("C", 2, 10, 1.0)], 1000, 1.0, 5.0, 2)
assert m["trades"] == 2 and m["skipped_full"] == 1 and m["max_open_seen"] == 2, m

# 1x leverage, 1% stop: risk 10 wants 1000 of position, capped at 1000*1/4*1.5 = 375 (= margin at 1x);
# two fit (750), the third overlapping one has no free cash. Each risks 375*1% = 3.75 -> +3.75 at 1R
m = GBT.money_sim([tr("A", 0, 10, 1.0, sl=99.0), tr("B", 1, 10, 1.0, sl=99.0), tr("C", 2, 10, 1.0, sl=99.0)], 1000, 1.0, 1.0, 4)
assert m["skipped_cash"] == 1 and m["trades"] == 2, m
assert abs(m["net"] - 7.5) < 1e-6, m
print("money_sim OK")

# 2. run(): whole book from fake candles, single coin, history cap, errors per coin
r = np.random.default_rng(3)


def fake_candles(market, symbol, tf, days, ttl=None):
    if symbol == "TONUSDT":
        raise RuntimeError("feed down")
    mins = MK.MINUTES[tf]
    n = int(days * 1440 / mins)
    idx = pd.date_range(end=T0, periods=n, freq=f"{mins}min", tz="UTC")
    drift = np.repeat(r.choice([0.004, -0.004], size=n // 50 + 1), 50)[:n]
    px = 100 * np.cumprod(1 + drift + r.normal(0, 0.004, n))
    o = np.concatenate([[100], px[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, px) * 1.003, "low": np.minimum(o, px) * 0.997, "close": px, "volume": 100.0}, index=idx)


MK.candles = fake_candles
raw = GB.rules("CRYPTO")["built_in"][0]["spec"]          # Supertrend 10/3 (4h), as the Backtest screen sends it
res = GBT.run(raw, "CRYPTO", "ALL", 365, 1000.0)
mo = res["money"]
assert mo["currency"] == "USDT" and mo["capital"] == 1000.0
assert res["total"]["n"] > 0 and mo["trades"] + mo["skipped_full"] + mo["skipped_cash"] <= res["total"]["n"]
assert "TONUSDT" in res["errors"] and len(res["by_symbol"]) == 9, (res["errors"], len(res["by_symbol"]))
assert all("symbol" in t for t in res["trades"]) and any(t["pnl"] is not None for t in res["trades"])
assert abs(sum(x["pnl"] for x in res["by_symbol"]) - mo["net"]) < 0.05, (sum(x["pnl"] for x in res["by_symbol"]), mo["net"])
one = GBT.run(raw, "CRYPTO", "BTCUSDT", 1500, 1000.0)
assert one["symbol"] == "BTCUSDT" and one["money"]["trades"] > 0
fast = dict(raw, timeframe="5m")
cut = GBT.run(fast, "CRYPTO", "ALL", 365, 1000.0, busy=True)
assert any("shortened" in n for n in cut["notes"]), cut["notes"]
print("run OK:", res["total"]["n"], "signals,", mo["trades"], "taken, net", mo["net"])

# 3. API: INR amounts, reset in rupees, fx on the account
GO.fx = lambda: {"USDINR": 80.0, "src": "test", "t": "now"}
from app import app  # noqa: E402
c = app.test_client()
assert c.post("/api/login", json={"password": "pw"}).status_code == 200
j = c.post("/api/gbook/backtest", json={"market": "CRYPTO", "spec": raw, "symbol": "ALL", "days": 365, "capital": 80000, "currency": "INR"}).get_json()
assert "error" not in j, j
assert abs(j["money"]["capital"] - 1000.0) < 1e-6 and j["fx"]["USDINR"] == 80.0, j["money"]["capital"]
j = c.post("/api/gbook/reset", json={"market": "CRYPTO", "capital": 20000, "currency": "INR"}).get_json()
assert j["ok"] and abs(j["capital"] - 250.0) < 1e-6, j
d = c.get("/api/gbook?market=CRYPTO").get_json()
assert d["capital_start"] == 250.0 and d["fx"]["USDINR"] == 80.0
assert c.post("/api/gbook/backtest", json={"market": "CRYPTO", "spec": raw, "days": 5}).status_code == 400
print("API OK")
print("ALL GBACKTEST OK")
