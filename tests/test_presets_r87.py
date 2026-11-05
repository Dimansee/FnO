"""Offline test: strategy templates from 5 Nov 2026 (TradingView built-ins, RSI 60/40, gap-day VWAP reclaim) validate and
run, and the new previous-bar prices / 'move vs ATR' indicator behave. Run: python tests/test_presets_r87.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np, pandas as pd  # noqa: E402
from fno import custom as CU, ind as X  # noqa: E402

ix = pd.date_range("2026-11-02 09:15", periods=75, freq="5min", tz="Asia/Kolkata").append(
     pd.date_range("2026-11-03 09:15", periods=75, freq="5min", tz="Asia/Kolkata"))
rng = np.random.default_rng(1)
c = 24000 + np.cumsum(rng.normal(0, 8, len(ix)))
df = pd.DataFrame({"open": c + rng.normal(0, 3, len(ix)), "close": c, "volume": 0.0}, index=ix)
df["high"] = df[["open", "close"]].max(axis=1) + 4
df["low"] = df[["open", "close"]].min(axis=1) - 4

# previous-bar prices = TradingView's x[1]
cache = {}
for k in ("open", "high", "low", "close"):
    s = X.operand_series(df, {"price": "prev_" + k}, cache)
    assert np.isnan(s.iloc[0]) and s.iloc[5] == df[k].iloc[4]
m = X.operand_series(df, {"ind": "chg_atr", "n": 5}, cache)
a = X.atr(df, 5)
assert abs(m.iloc[20] - (df.close.iloc[20] - df.close.iloc[19]) / a.iloc[20]) < 1e-9
assert "prev_close" in X.meta()["prices"] and "chg_atr" in X.meta()["indicators"]

names = [p["name"] for p in CU.PRESETS]
for want in ("TradingView: Bar up/down", "TradingView: Bollinger Bands", "TradingView: Channel breakout", "TradingView: Consecutive up/down",
             "TradingView: Greedy (gap bars)", "TradingView: Inside bar", "TradingView: Momentum", "TradingView: 2 moving-average cross",
             "TradingView: Volatility expansion close", "RSI 60/40", "Gap day: VWAP reclaim (24,350 on 5 Nov)"):
    assert want in names, want
for p in CU.PRESETS:
    spec = CU.validate(p)
    assert len(p.get("about", "")) <= 300, p["name"]              # validate() keeps 300 characters
    if spec["market"] == "IN":
        L, S, _ = CU.signals(spec, CU.resample(df, spec["timeframe"], "IN"))
        assert len(L) and len(S)
print("strategy template tests passed")
