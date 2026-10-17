"""Crypto-specific strategy study (17 Oct 2026). Companion to crypto_candles.py.

Data: Binance spot 1h (Jan 2023 - Oct 2026) for 10 coins, Binance USD-M perpetual funding rates.
Costs 16 bps round trip (3 bps slippage + 5 bps fee per side), as in the app. Design < 2025-04-01 <= holdout.

  S1 Seasonality: hour-of-day (UTC) and weekday average returns; does any pattern hold in both halves?
  S2 Cross-sectional momentum: each Monday hold the 3 coins with the best 4-week return (plus variants) vs holding all 10.
  S3 BTC regime filter: the live Supertrend 4h rule on the coins, taking longs only while BTC is above its 200-day
     average and shorts only while below.
  S4 Volatility squeeze breakout: Bollinger width in its lowest 10% of the last 180 bars, then a close outside the band.
  S5 Funding rate: contrarian after extreme funding, and as a filter that skips crowded trend trades.
Usage: python research/crypto_strategies.py <data_dir>
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fno import custom as CU  # noqa: E402
from fno import gautopilot as AP  # noqa: E402

DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data/global"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crypto_strategies_results.json")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT"]
SPLIT = pd.Timestamp("2025-04-01", tz="UTC")
COST = 0.0016
RES = {}


def load(sym):
    df = pd.read_csv(f"{DATA}/crypto_{sym}_1h.csv.gz", parse_dates=["ts"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def rs(df, tf):
    return df if tf == "1h" else df.resample({"4h": "4h", "1d": "1D"}[tf], label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def tstat(x):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    return round(float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), 2) if len(x) > 2 else None


def rstats(r):
    r = np.asarray(r, float)
    if not len(r):
        return {"n": 0}
    lo = -r[r <= 0].sum()
    return {"n": int(len(r)), "avg": round(float(r.mean()), 3), "pf": round(float(r[r > 0].sum() / lo), 2) if lo > 0 else None, "t": tstat(r)}


def split_stats(trades):
    return {"design": rstats([t["R"] for t in trades if t["entry_ts"] < SPLIT]), "holdout": rstats([t["R"] for t in trades if t["entry_ts"] >= SPLIT])}


def sim_with(spec, df, lg, sh):
    """Run the app's own simulator with precomputed entry signals."""
    orig = CU.signals
    CU.signals = lambda s, d: (lg.reindex(d.index).fillna(False).astype(bool), sh.reindex(d.index).fillna(False).astype(bool), {})
    try:
        return CU.simulate(spec, df)
    finally:
        CU.signals = orig


def spec_for(tf, ex, direction="both"):
    return CU.validate({"name": "x", "market": "CRYPTO", "symbols": ["BTCUSDT"], "timeframe": tf, "direction": direction, "hold": "swing",
                        "long": {"all": [{"l": {"price": "close"}, "op": "gt", "r": {"value": 0}}]},
                        "short": {"all": [{"l": {"price": "close"}, "op": "lt", "r": {"value": 0}}]},
                        "exit": {**CU.DEFAULT_EXIT, "target_r": None, **ex}, "filters": {"max_trades_day": 3}})


H = {s: load(s) for s in COINS}


# ------------------------------------------------------------------ S1 seasonality
def s1():
    rets = []
    for s, d in H.items():
        r = np.log(d["close"]).diff()
        rets.append(pd.DataFrame({"r": r, "hour": d.index.hour, "wd": d.index.weekday, "design": d.index < SPLIT, "sym": s}))
    R = pd.concat(rets).dropna()
    out = {"hour": {}, "weekday": {}}
    for key, col in (("hour", "hour"), ("weekday", "wd")):
        for v, g in R.groupby(col):
            dsg, hol = g[g.design].r, g[~g.design].r
            out[key][int(v)] = {"design_bps": round(dsg.mean() * 1e4, 2), "design_t": tstat(dsg), "holdout_bps": round(hol.mean() * 1e4, 2), "holdout_t": tstat(hol)}
    # consistency: same sign and |t|>2 in both halves
    cons = {k: [v for v, x in out[k].items() if x["design_t"] and x["holdout_t"] and abs(x["design_t"]) > 2 and abs(x["holdout_t"]) > 2 and np.sign(x["design_t"]) == np.sign(x["holdout_t"])] for k in out}
    corr = np.corrcoef([out["hour"][h]["design_bps"] for h in range(24)], [out["hour"][h]["holdout_bps"] for h in range(24)])[0, 1]
    out["consistent_buckets"] = cons
    out["hour_profile_correlation_design_vs_holdout"] = round(float(corr), 2)
    # trade test: best 6 design hours long-only vs costs (one round trip per day)
    best = sorted(range(24), key=lambda h: -out["hour"][h]["design_bps"])[:6]
    hol = R[(~R.design) & R.hour.isin(best)].groupby([R.sym, R.index.date]).r.sum() if False else None
    out["best_design_hours_utc"] = best
    out["best_hours_holdout_bps_per_hour"] = round(float(R[(~R.design) & R.hour.isin(best)].r.mean() * 1e4), 2)
    out["all_hours_holdout_bps_per_hour"] = round(float(R[~R.design].r.mean() * 1e4), 2)
    RES["S1_seasonality"] = out
    print("S1 hour corr design/holdout", out["hour_profile_correlation_design_vs_holdout"], "consistent:", cons,
          "| best design hours", best, "holdout bps/h", out["best_hours_holdout_bps_per_hour"], "vs all", out["all_hours_holdout_bps_per_hour"])
    print("   weekday:", {k: (v["design_bps"], v["holdout_bps"]) for k, v in out["weekday"].items()})


# ------------------------------------------------------------------ S2 cross-sectional momentum
def s2():
    close = pd.DataFrame({s: d["close"] for s, d in H.items()}).resample("1D").last()
    btc200 = close["BTCUSDT"].rolling(200).mean()
    wk = close.resample("W-MON", label="left", closed="left").first()      # Monday 00:00 prices
    out = {}
    for look in (7, 28, 56):
        for top in (2, 3, 5):
            for filt in (False, True):
                eq, held, rets = 1.0, set(), []
                for i in range(1, len(wk) - 1):
                    t = wk.index[i]
                    past = close.loc[:t].iloc[-look - 1:] if len(close.loc[:t]) > look else None
                    if past is None:
                        continue
                    mom = (past.iloc[-1] / past.iloc[0] - 1).dropna()
                    pick = set(mom.sort_values(ascending=False).index[:top]) if len(mom) >= top else set()
                    if filt and not (close["BTCUSDT"].loc[:t].iloc[-1] > btc200.loc[:t].iloc[-1]):
                        pick = set()
                    nxt = wk.iloc[i + 1] / wk.iloc[i] - 1
                    r = float(np.nanmean([nxt[s] for s in pick])) if pick else 0.0
                    turnover = len(pick ^ held) / max(top, 1)
                    r -= turnover * COST / 2 * 2
                    held = pick
                    rets.append((t, r))
                s = pd.Series(dict(rets))
                key = f"top{top}_look{look}d" + ("_btc200filter" if filt else "")
                out[key] = {p: _perf(s[(s.index < SPLIT) if p == "design" else (s.index >= SPLIT)]) for p in ("design", "holdout")}
    ew = (wk.shift(-1) / wk - 1).mean(axis=1).dropna()
    btc = (wk["BTCUSDT"].shift(-1) / wk["BTCUSDT"] - 1).dropna()
    out["benchmark_equal_weight_all"] = {p: _perf(ew[(ew.index < SPLIT) if p == "design" else (ew.index >= SPLIT)]) for p in ("design", "holdout")}
    out["benchmark_btc_hold"] = {p: _perf(btc[(btc.index < SPLIT) if p == "design" else (btc.index >= SPLIT)]) for p in ("design", "holdout")}
    RES["S2_xs_momentum"] = out
    for k, v in out.items():
        print(f"S2 {k:32} design {v['design']} | holdout {v['holdout']}")


def _perf(s):
    s = s.dropna()
    if not len(s):
        return {}
    eq = (1 + s).cumprod()
    yrs = len(s) / 52
    return {"cagr_pct": round(float((eq.iloc[-1] ** (1 / yrs) - 1) * 100), 1), "sharpe": round(float(s.mean() / s.std() * np.sqrt(52)), 2) if s.std() > 0 else None,
            "maxdd_pct": round(float((eq / eq.cummax() - 1).min() * 100), 1), "weeks": int(len(s))}


# ------------------------------------------------------------------ S3 BTC regime filter on the live trend rule
def s3():
    btc_d = rs(H["BTCUSDT"], "1d")["close"]
    bull = (btc_d > btc_d.rolling(200).mean()).shift(1)        # yesterday's close vs its 200-day average (known at today's open)
    cand = next(c for c in AP.CANDIDATES["CRYPTO"] if c["id"] == "s_a10001")
    spec = AP._spec("CRYPTO", cand, ["BTCUSDT"])
    base, filt = [], []
    for s, d in H.items():
        b = rs(d, "4h")
        tr = CU.simulate(spec, b)
        for t in tr:
            day = t["entry_ts"].floor("1D")
            ok = bull.get(day)
            if ok is None or pd.isna(ok):
                continue
            base.append(t)
            if (t["side"] == "long" and ok) or (t["side"] == "short" and not ok):
                filt.append(t)
    RES["S3_btc_regime"] = {"supertrend4h_all": split_stats(base), "supertrend4h_btc_regime": split_stats(filt)}
    print("S3", RES["S3_btc_regime"])


# ------------------------------------------------------------------ S4 volatility squeeze breakout
def s4():
    out = {}
    for tf in ("4h", "1d"):
        trades = []
        sp = spec_for(tf, {"stop_atr": 2.0, "trail_atr": 3.0})
        for s, d in H.items():
            b = rs(d, tf)
            c = b["close"]
            mid, sd = c.rolling(20).mean(), c.rolling(20).std()
            up, lo = mid + 2 * sd, mid - 2 * sd
            width = (up - lo) / mid
            squeeze = width.rolling(180, min_periods=60).rank(pct=True) <= 0.10
            was = squeeze.shift(1).rolling(5).max().fillna(0).astype(bool)          # squeeze within the last 5 bars
            lg = was & (c > up) & (c.shift() <= up.shift())
            sh = was & (c < lo) & (c.shift() >= lo.shift())
            trades += sim_with(sp, b, lg, sh)
        out[tf] = split_stats(trades)
        long_only = [t for t in trades if t["side"] == "long"]
        out[tf + "_long_only"] = split_stats(long_only)
    RES["S4_squeeze"] = out
    print("S4", out)


# ------------------------------------------------------------------ S5 funding rate
def s5():
    out = {"contrarian": {}, "filter": {}}
    files = {s: f"{DATA}/funding_{s}.csv.gz" for s in COINS if os.path.exists(f"{DATA}/funding_{s}.csv.gz")}
    if not files:
        RES["S5_funding"] = {"skipped": "no funding data"}
        print("S5 skipped: no funding data")
        return
    rows, fund_by = [], {}
    for s, f in files.items():
        fr = pd.read_csv(f).set_index("ts")["rate"]
        fr.index = pd.to_datetime(fr.index, utc=True, format="ISO8601").floor("h")
        fr = fr[~fr.index.duplicated()]
        fund_by[s] = fr
        px = H[s]["close"]
        pct = fr.rolling(270, min_periods=90).rank(pct=True)          # ~90 days of 8h prints
        for hz in (24, 72):
            fwd = (px.shift(-hz) / px - 1).reindex(fr.index, method="ffill")
            hi = (pct >= 0.95) & (fr > 0.0003)
            lo = (pct <= 0.05) & (fr < 0)
            for name, m, sign in (("short_after_high_funding", hi, -1), ("long_after_negative_funding", lo, 1)):
                v = (sign * fwd[m] - COST).dropna()
                for t, x in v.items():
                    rows.append({"rule": name, "hz": hz, "ts": t, "x": x, "sym": s})
        # baseline: all prints
    R = pd.DataFrame(rows)
    for (name, hz), g in R.groupby(["rule", "hz"]):
        d, h = g[g.ts < SPLIT].x, g[g.ts >= SPLIT].x
        out["contrarian"][f"{name}_{hz}h"] = {"design_pct": round(d.mean() * 100, 2) if len(d) else None, "design_n": int(len(d)), "design_t": tstat(d),
                                               "holdout_pct": round(h.mean() * 100, 2) if len(h) else None, "holdout_n": int(len(h)), "holdout_t": tstat(h)}
    # filter: skip Supertrend 4h longs when funding is in its top 10% (crowded longs)
    cand = next(c for c in AP.CANDIDATES["CRYPTO"] if c["id"] == "s_a10001")
    spec = AP._spec("CRYPTO", cand, ["BTCUSDT"])
    base, kept = [], []
    for s, fr in fund_by.items():
        pct = fr.rolling(270, min_periods=90).rank(pct=True)
        tr = CU.simulate(spec, rs(H[s], "4h"))
        for t in tr:
            p = pct.loc[:t["entry_ts"]].iloc[-1] if len(pct.loc[:t["entry_ts"]]) else np.nan
            if not np.isfinite(p):
                continue
            base.append(t)
            if not ((t["side"] == "long" and p >= 0.9) or (t["side"] == "short" and p <= 0.1)):
                kept.append(t)
    out["filter"] = {"supertrend4h_all": split_stats(base), "supertrend4h_skip_crowded": split_stats(kept)}
    RES["S5_funding"] = out
    for k, v in out["contrarian"].items():
        print("S5", k, v)
    print("S5 filter", out["filter"])


if __name__ == "__main__":
    only = os.environ.get("ONLY")
    for f in (s1, s2, s3, s4, s5):
        if only and f.__name__ not in only.split(","):
            continue
        try:
            f()
        except Exception as e:
            import traceback
            traceback.print_exc()
            RES[f.__name__] = {"error": str(e)}
    json.dump(RES, open(OUT, "w"), indent=1, default=str)
