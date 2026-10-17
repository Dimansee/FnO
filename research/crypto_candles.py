"""Candle-pattern study on crypto (17 Oct 2026).

Do the classic candlestick patterns predict the next move in crypto, after costs, on data the test never tuned on?

Data: Binance spot 1h, Jan 2023 - Oct 2026, BTC ETH SOL BNB XRP DOGE ADA AVAX LINK TON; 4h and daily resampled (UTC).
Patterns are read on a COMPLETED bar; the trade/forward return starts at the NEXT bar's open (no look-ahead).
Two measurements, both net of a 16 bps round trip (3 bps slippage + 5 bps fee per side, as in the app):
  1. Forward return over 1 / 3 / 6 / 12 bars in the pattern's direction, minus the average same-direction return of ALL bars
     on that coin and timeframe ("edge vs a random bar"). t-stat over non-overlapping signals.
  2. The textbook trade: stop beyond the pattern's extreme, target 2R, out after 12 bars; average R per trade.
Each pattern is checked raw and in its textbook context (bullish reversal at a 20-bar low after a fall, bearish at a 20-bar
high after a rise; continuation patterns with the 50-EMA trend). Design = before 2025-04-01, holdout = after.
Usage: python research/crypto_candles.py <data_dir>
"""
import json
import os
import sys

import numpy as np
import pandas as pd

DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data/global"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crypto_candles_results.json")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT"]
SPLIT = pd.Timestamp("2025-04-01", tz="UTC")
COST = 0.0016
HORIZONS = (1, 3, 6, 12)


def load(sym):
    df = pd.read_csv(f"{DATA}/crypto_{sym}_1h.csv.gz", parse_dates=["ts"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def resample(df, tf):
    if tf == "1h":
        return df
    return df.resample({"4h": "4h", "1d": "1D"}[tf], label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def atr(df, n=14):
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def patterns(d):
    o, h, l, c = d["open"], d["high"], d["low"], d["close"]
    rng = (h - l).replace(0, np.nan)
    body = (c - o).abs()
    up, dn = c > o, c < o
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    a = atr(d)
    po, pc_, ph, pl = o.shift(), c.shift(), h.shift(), l.shift()
    p2o, p2c = o.shift(2), c.shift(2)
    small = body <= 0.3 * rng
    P = {}
    # (signal series, direction +1/-1, kind: 'rev' reversal or 'cont' continuation, stop reference)
    P["bull_engulfing"] = (up & (pc_ < po) & (c >= po) & (o <= pc_) & (body > (pc_ - po).abs()), 1, "rev")
    P["bear_engulfing"] = (dn & (pc_ > po) & (c <= po) & (o >= pc_) & (body > (pc_ - po).abs()), -1, "rev")
    P["hammer"] = ((lower >= 2 * body) & (upper <= 0.25 * rng) & (body > 0) & (rng > 0.5 * a), 1, "rev")
    P["shooting_star"] = ((upper >= 2 * body) & (lower <= 0.25 * rng) & (body > 0) & (rng > 0.5 * a), -1, "rev")
    P["bull_pin_bar"] = ((lower >= 0.66 * rng) & (rng > a), 1, "rev")
    P["bear_pin_bar"] = ((upper >= 0.66 * rng) & (rng > a), -1, "rev")
    P["doji_after_fall"] = ((body <= 0.1 * rng) & (c < c.shift(5)), 1, "rev")
    P["doji_after_rise"] = ((body <= 0.1 * rng) & (c > c.shift(5)), -1, "rev")
    P["morning_star"] = (dn.shift(2) & ((p2o - p2c) > 0.5 * a.shift(2)) & ((pc_ - po).abs() <= 0.3 * (ph - pl)) & up & (c > (p2o + p2c) / 2), 1, "rev")
    P["evening_star"] = (up.shift(2) & ((p2c - p2o) > 0.5 * a.shift(2)) & ((pc_ - po).abs() <= 0.3 * (ph - pl)) & dn & (c < (p2o + p2c) / 2), -1, "rev")
    P["three_white_soldiers"] = (up & up.shift() & up.shift(2) & (c > pc_) & (pc_ > p2c) & ~small & ~small.shift().fillna(True).astype(bool), 1, "cont")
    P["three_black_crows"] = (dn & dn.shift() & dn.shift(2) & (c < pc_) & (pc_ < p2c) & ~small & ~small.shift().fillna(True).astype(bool), -1, "cont")
    P["bull_marubozu"] = (up & (body >= 0.85 * rng) & (rng > 1.5 * a), 1, "cont")
    P["bear_marubozu"] = (dn & (body >= 0.85 * rng) & (rng > 1.5 * a), -1, "cont")
    # inside bar: previous bar inside the one before; signal when THIS bar closes out of the mother bar
    mh, ml = h.shift(2), l.shift(2)
    inside_prev = (ph <= mh) & (pl >= ml)
    P["inside_bar_break_up"] = (inside_prev & (c > mh), 1, "cont")
    P["inside_bar_break_down"] = (inside_prev & (c < ml), -1, "cont")
    P["outside_bar_up"] = ((h > ph) & (l < pl) & up & (c > ph), 1, "cont")
    P["outside_bar_down"] = ((h > ph) & (l < pl) & dn & (c < pl), -1, "cont")
    return P, a


def context(d, direction, kind):
    c = d["close"]
    e50 = c.ewm(span=50, adjust=False).mean()
    lo20, hi20 = d["low"].rolling(20).min(), d["high"].rolling(20).max()
    if kind == "rev":
        return (d["low"] <= lo20) & (c < c.shift(10)) if direction > 0 else (d["high"] >= hi20) & (c > c.shift(10))
    return (c > e50) & (e50 > e50.shift(5)) if direction > 0 else (c < e50) & (e50 < e50.shift(5))


def bracket(d, sig_idx, direction, a, stop_px):
    """Stop beyond the pattern extreme (at least 0.5 ATR away), target 2R, 12-bar cap; R net of costs."""
    o, h, l, c = (d[k].values for k in ("open", "high", "low", "close"))
    n, out, busy = len(d), [], -1
    for i in sig_idx:
        j = i + 1
        if j >= n or i <= busy:
            continue
        e = o[j]
        st = stop_px[i]
        risk = max(direction * (e - st), 0.5 * a[i])
        if not np.isfinite(risk) or risk <= 0:
            continue
        sl, tp = e - direction * risk, e + direction * 2 * risk
        r, k = None, j
        for k in range(j, min(j + 12, n)):
            if direction * ((l[k] if direction > 0 else h[k]) - sl) <= 0:      # stop first if both are touched
                r = -1.0
                break
            if direction * ((h[k] if direction > 0 else l[k]) - tp) >= 0:
                r = 2.0
                break
        if r is None:
            r = direction * (c[k] - e) / risk
        out.append((d.index[j], r - COST * e / risk))
        busy = k
    return out


def run():
    rows = []
    for tf in ("1h", "4h", "1d"):
        per = {}
        for sym in COINS:
            d = resample(load(sym), tf)
            P, a = patterns(d)
            logc = np.log(d["close"])
            fwd = {hz: (np.log(d["close"].shift(-hz)) - np.log(d["open"].shift(-1))) for hz in HORIZONS}   # from next open
            base = {hz: fwd[hz].mean() for hz in HORIZONS}
            for name, (sig, direction, kind) in P.items():
                for ctx_name in ("raw", "context"):
                    s = sig.fillna(False).astype(bool)
                    if ctx_name == "context":
                        s = s & context(d, direction, kind).fillna(False)
                    idx = np.flatnonzero(s.values[:-1])
                    stop_px = (d["low"] if direction > 0 else d["high"]).values
                    if kind == "rev":                      # reversal patterns: stop beyond the last 3 bars' extreme
                        stop_px = (d["low"].rolling(3).min() if direction > 0 else d["high"].rolling(3).max()).values
                    tr = bracket(d, idx, direction, a.values, stop_px)
                    key = (name, ctx_name)
                    agg = per.setdefault(key, {"dir": direction, "kind": kind, "ex": {p: {hz: [] for hz in HORIZONS} for p in ("design", "holdout")}, "R": {"design": [], "holdout": []}, "syms": {}})
                    ts = d.index
                    for hz in HORIZONS:
                        v = fwd[hz].values
                        last = -10 ** 9
                        for i in idx:
                            if i - last < hz:              # non-overlapping signals for the t-stat
                                continue
                            last = i
                            x = v[i]
                            if not np.isfinite(x):
                                continue
                            ex = direction * (x - base[hz]) - (COST if hz else 0)
                            agg["ex"]["design" if ts[i] < SPLIT else "holdout"][hz].append(ex)
                    for t, r in tr:
                        agg["R"]["design" if t < SPLIT else "holdout"].append(r)
                    agg["syms"][sym] = float(np.mean([r for _, r in tr])) if tr else None
        for (name, ctx_name), agg in per.items():
            def st(x):
                x = np.asarray(x, float)
                if len(x) < 2:
                    return {"n": int(len(x))}
                return {"n": int(len(x)), "mean_bps": round(float(x.mean() * 1e4), 1), "t": round(float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), 2)}
            row = {"tf": tf, "pattern": name, "context": ctx_name, "dir": agg["dir"],
                   "fwd_edge": {p: {hz: st(agg["ex"][p][hz]) for hz in HORIZONS} for p in ("design", "holdout")},
                   "trade_R": {p: {"n": len(agg["R"][p]), "avg": round(float(np.mean(agg["R"][p])), 3) if agg["R"][p] else None,
                                   "win": round(float(np.mean(np.array(agg["R"][p]) > 0) * 100), 1) if agg["R"][p] else None} for p in ("design", "holdout")},
                   "coins_positive": int(sum(1 for v in agg["syms"].values() if v is not None and v > 0)), "coins": int(sum(1 for v in agg["syms"].values() if v is not None))}
            rows.append(row)
            fd, fh = row["fwd_edge"]["design"][6], row["fwd_edge"]["holdout"][6]
            td, th = row["trade_R"]["design"], row["trade_R"]["holdout"]
            print(f"{tf:3} {name:22} {ctx_name:7} | 6-bar edge design {fd.get('mean_bps', 0):+7.1f}bps t={fd.get('t', 0):+5.2f} n={fd['n']:5} | holdout {fh.get('mean_bps', 0):+7.1f}bps t={fh.get('t', 0):+5.2f} n={fh['n']:5} | trade R design {td['avg']} holdout {th['avg']} (n {td['n']}/{th['n']}) coins+ {row['coins_positive']}/{row['coins']}", flush=True)
    json.dump(rows, open(OUT, "w"), indent=1)
    return rows


if __name__ == "__main__":
    rows = run()
    good = [r for r in rows if (r["trade_R"]["design"]["avg"] or -1) > 0.05 and (r["trade_R"]["holdout"]["avg"] or -1) > 0.05 and r["trade_R"]["design"]["n"] >= 100]
    print("tests:", len(rows), "trade R > +0.05 in both design and holdout (n>=100):", [(r["tf"], r["pattern"], r["context"]) for r in good])
