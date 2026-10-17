"""Crypto / US autopilot rule research (17 Oct 2026).

Question: which simple, well-known rule families have an edge AFTER the app's own costs on crypto (Binance spot) and
US stocks/ETFs, judged on data the selection never saw?

Protocol (fixed before looking at results):
  - Engine: fno.custom.simulate, the same code the live books use (signal at bar close, entry next open,
    stop/target inside the bar, stop wins ties, costs from COSTS_BPS deducted in R).
  - Rules: fixed textbook parameters, no tuning. Each family tried long-only and both sides.
  - Split by date. Crypto: design 2023-01-01 .. 2025-03-31, holdout 2025-04-01 .. latest.
    US (1h, ~2 years): design = first 60% of the period, holdout = last 40%.
  - Design pass (pooled over all symbols of the market): >= 100 trades, avg >= +0.05R, PF >= 1.10,
    and avg > 0 on at least 60% of symbols.
  - Holdout pass (only for design survivors): pooled avg > 0, PF >= 1.05, avg > 0 on at least 50% of symbols.
  Rules passing both go to the autopilot candidate list, where the per-symbol weekly gate still applies live.
Usage: python research/global_rules_search.py <data_dir>
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fno import custom as CU  # noqa: E402

DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data/global"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "global_rules_results.json")
CRYPTO = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT"]
US = ["SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "JPM", "GLD"]
CRYPTO_SPLIT = pd.Timestamp("2025-04-01", tz="UTC")


def c(l, op, r):
    return {"l": l, "op": op, "r": r}


P, V = (lambda k="close": {"price": k}), (lambda v: {"value": v})
I = lambda name, **kw: {"ind": name, **kw}   # noqa: E731
X = lambda ex: {**CU.DEFAULT_EXIT, "target_r": None, **ex}   # noqa: E731

FAMILIES = {
    "donchian20_trail3": ({"all": [c(P(), "crosses_above", I("donchian", n=20, out="upper"))]},
                          {"all": [c(P(), "crosses_below", I("donchian", n=20, out="lower"))]}, X({"stop_atr": 2.0, "trail_atr": 3.0})),
    "donchian55_trail4": ({"all": [c(P(), "crosses_above", I("donchian", n=55, out="upper"))]},
                          {"all": [c(P(), "crosses_below", I("donchian", n=55, out="lower"))]}, X({"stop_atr": 2.0, "trail_atr": 4.0})),
    "ema20_50_cross": ({"all": [c(I("ema", n=20), "crosses_above", I("ema", n=50))]},
                       {"all": [c(I("ema", n=20), "crosses_below", I("ema", n=50))]}, X({"stop_atr": 2.0, "trail_atr": 3.0, "exit_on_opposite": True})),
    "ema9_21_adx": ({"all": [c(I("ema", n=9), "crosses_above", I("ema", n=21)), c(I("adx", n=14, out="adx"), "gt", V(22))]},
                    {"all": [c(I("ema", n=9), "crosses_below", I("ema", n=21)), c(I("adx", n=14, out="adx"), "gt", V(22))]}, X({"stop_atr": 2.0, "target_r": 3.0, "trail_atr": 2.5, "exit_on_opposite": True})),
    "supertrend_10_3": ({"all": [c(I("supertrend", n=10, mult=3.0, out="dir"), "crosses_above", V(0))]},
                        {"all": [c(I("supertrend", n=10, mult=3.0, out="dir"), "crosses_below", V(0))]}, X({"stop_atr": 2.0, "exit_on_opposite": True})),
    "keltner_breakout": ({"all": [c(P(), "crosses_above", I("keltner", n=20, mult=2.0, out="upper"))]},
                         {"all": [c(P(), "crosses_below", I("keltner", n=20, mult=2.0, out="lower"))]}, X({"stop_atr": 2.0, "trail_atr": 3.0})),
    "macd_ema200": ({"all": [c(I("macd", fast=12, slow=26, sig=9, out="line"), "crosses_above", I("macd", fast=12, slow=26, sig=9, out="signal")), c(P(), "gt", I("ema", n=200))]},
                    {"all": [c(I("macd", fast=12, slow=26, sig=9, out="line"), "crosses_below", I("macd", fast=12, slow=26, sig=9, out="signal")), c(P(), "lt", I("ema", n=200))]}, X({"stop_atr": 2.0, "trail_atr": 3.0})),
    "trend_pullback_rsi": ({"all": [c(P(), "gt", I("ema", n=200)), c(I("rsi", n=14), "crosses_above", V(40))]},
                           {"all": [c(P(), "lt", I("ema", n=200)), c(I("rsi", n=14), "crosses_below", V(60))]}, X({"stop_atr": 2.0, "target_r": 2.0})),
    "rsi2_meanrev_trend": ({"all": [c(P(), "gt", I("sma", n=200)), c(I("rsi", n=2), "crosses_below", V(10))]},
                           {"all": [c(P(), "lt", I("sma", n=200)), c(I("rsi", n=2), "crosses_above", V(90))]}, X({"stop_atr": 2.5, "target_r": 1.0, "time_stop_bars": 5})),
    "bollinger_meanrev": ({"all": [c(P(), "lt", I("bb", n=20, k=2.0, out="lower")), c(I("rsi", n=14), "lt", V(30))]},
                          {"all": [c(P(), "gt", I("bb", n=20, k=2.0, out="upper")), c(I("rsi", n=14), "gt", V(70))]}, X({"stop_atr": 1.5, "target_r": 1.0, "time_stop_bars": 12})),
}
TFS = {"CRYPTO": ["1h", "4h", "1d"], "US": ["1h", "4h"]}


def spec(market, fam, tf, direction):
    lg, sh, ex = FAMILIES[fam]
    hold = "swing"
    return CU.validate({"name": f"{fam} {tf} {direction}", "market": market, "symbols": ["X"], "timeframe": tf, "direction": direction,
                        "long": lg, "short": sh, "exit": ex, "filters": {"max_trades_day": 3}, "hold": hold, "enabled": True, "auto": True})


def load(market, sym):
    f = f"{DATA}/crypto_{sym}_1h.csv.gz" if market == "CRYPTO" else f"{DATA}/us_{sym}_1h.csv.gz"
    if not os.path.exists(f):
        return None
    df = pd.read_csv(f, parse_dates=["ts"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    tz = "UTC" if market == "CRYPTO" else "America/New_York"
    df.index = df.index.tz_convert(tz)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def stats(rs):
    r = np.asarray(rs, float)
    if not len(r):
        return {"n": 0, "avg": 0.0, "pf": None}
    w, lo = r[r > 0].sum(), -r[r <= 0].sum()
    return {"n": int(len(r)), "avg": round(float(r.mean()), 3), "pf": round(float(w / lo), 2) if lo > 0 else None, "win": round(float((r > 0).mean() * 100), 1)}


def main():
    rows = []
    bars = {}
    for market, syms in (("CRYPTO", CRYPTO), ("US", US)):
        for s in syms:
            d = load(market, s)
            if d is not None and len(d) > 500:
                bars[(market, s)] = d
    print("loaded", {k: len(v) for k, v in bars.items()})
    for market in ("CRYPTO", "US"):
        syms = [s for (m, s) in bars if m == market]
        if not syms:
            continue
        span = {s: (bars[(market, s)].index[0], bars[(market, s)].index[-1]) for s in syms}
        for fam in FAMILIES:
            for tf in TFS[market]:
                for direction in ("long", "both"):
                    sp = spec(market, fam, tf, direction)
                    des, hol, per_des, per_hol = [], [], {}, {}
                    for s in syms:
                        b = CU.resample(bars[(market, s)], tf, market)
                        if market == "US":
                            b = b[(b.index.hour * 60 + b.index.minute >= 570) & (b.index.hour < 16)]
                        tr = CU.simulate(sp, b)
                        if market == "CRYPTO":
                            cut = CRYPTO_SPLIT
                        else:
                            a, z = span[s]
                            cut = a + (z - a) * 0.6
                        d_r = [t["R"] for t in tr if t["entry_ts"] < cut]
                        h_r = [t["R"] for t in tr if t["entry_ts"] >= cut]
                        des += d_r
                        hol += h_r
                        per_des[s] = stats(d_r)
                        per_hol[s] = stats(h_r)
                    D, H = stats(des), stats(hol)
                    pos_d = np.mean([v["avg"] > 0 for v in per_des.values() if v["n"]]) if per_des else 0
                    pos_h = np.mean([v["avg"] > 0 for v in per_hol.values() if v["n"]]) if per_hol else 0
                    design_ok = D["n"] >= 100 and D["avg"] >= 0.05 and (D["pf"] or 9) >= 1.10 and pos_d >= 0.6
                    hold_ok = design_ok and H["n"] >= 40 and H["avg"] > 0 and (H["pf"] or 9) >= 1.05 and pos_h >= 0.5
                    row = {"market": market, "family": fam, "tf": tf, "direction": direction, "design": D, "holdout": H,
                           "design_sym_pos": round(float(pos_d), 2), "holdout_sym_pos": round(float(pos_h), 2),
                           "design_ok": bool(design_ok), "holdout_ok": bool(hold_ok),
                           "holdout_by_symbol": {k: v for k, v in per_hol.items()} if design_ok else None}
                    rows.append(row)
                    print(f"{market:6} {fam:20} {tf:3} {direction:4} design n={D['n']:5} avg={D['avg']:+.3f} pf={D['pf']} pos={pos_d:.2f} | "
                          f"holdout n={H['n']:5} avg={H['avg']:+.3f} pf={H['pf']} pos={pos_h:.2f} {'PASS' if hold_ok else ('design-only' if design_ok else '')}", flush=True)
    json.dump({"run": datetime.utcnow().isoformat(timespec="seconds"), "rows": rows}, open(OUT, "w"), indent=1, default=str)
    print("tests:", len(rows), "design pass:", sum(r["design_ok"] for r in rows), "both pass:", sum(r["holdout_ok"] for r in rows))


if __name__ == "__main__":
    main()
