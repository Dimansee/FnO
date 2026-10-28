"""Offline test: crypto candles never come from a source that stopped updating (gold showed March 2025 prices, 29 Oct 2026).
Run: python tests/test_market_fresh.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np
import pandas as pd
from fno import markets as MK

def bars(end, n=50, step="4h", px=4000.0):
    idx = pd.date_range(end=end, periods=n, freq=step, tz="UTC")
    return pd.DataFrame({"open": px, "high": px + 1, "low": px - 1, "close": px, "volume": 1.0}, index=idx)

now = pd.Timestamp.now(tz="UTC").floor("4h")
stale = bars(pd.Timestamp("2025-03-11 12:00", tz="UTC"), px=2905.0)
good = bars(now, px=4002.0)
assert MK.fresh(good, "4h") and not MK.fresh(stale, "4h") and not MK.fresh(pd.DataFrame(), "4h")
assert MK.fresh(bars(now - pd.Timedelta(hours=12), step="1h"), "1h") is False      # 12 h old hourly data is stale
assert MK.fresh(bars(pd.Timestamp.now(tz="UTC").floor("1D") - pd.Timedelta(days=1), step="1D"), "1d")

calls = []
def boom(*a, **k):
    calls.append("binance"); raise RuntimeError("451 restricted")
MK._binance = boom
MK._bybit = lambda *a, **k: (calls.append("bybit"), stale)[1]
MK._yahoo_crypto = lambda *a, **k: (calls.append("yahoo"), good)[1]
df = MK._crypto("PAXGUSDT", "4h", 30)
assert calls == ["binance", "bybit", "yahoo"] and df["close"].iloc[-1] == 4002.0 and MK.source("PAXGUSDT", "4h") == "Yahoo Finance"
# Binance fresh -> used directly
MK._binance = lambda *a, **k: good
assert MK._crypto("BTCUSDT", "4h", 30)["close"].iloc[-1] == 4002.0 and MK.source("BTCUSDT", "4h") == "Binance"
# nothing fresh -> an error, never the old prices
MK._binance = boom
MK._yahoo_crypto = lambda *a, **k: stale
try:
    MK._crypto("PAXGUSDT", "4h", 30)
    raise AssertionError("stale data was accepted")
except RuntimeError as e:
    assert "No up-to-date price data" in str(e) and "out of date" in str(e)
print("market freshness tests passed")
