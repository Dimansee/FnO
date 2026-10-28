"""Round 58: "EMA 20/50 + RSI + support/resistance + volume" pullback strategy on crypto (28 Oct 2026).

Rules exactly as given to the user by another AI, coded without hindsight (signal on the closed bar, entry next open):
  Long: EMA20 > EMA50; within the last 5 bars price pulled back to the EMA20 (low within 0.25 ATR of it) or to a
        support zone (a confirmed swing low of the last 100 bars, within 0.5 ATR); RSI(14) in the band and rising;
        bullish candle (close > open), optionally volume up (above the previous bar and its 20-bar average).
  Short: mirror image.
  Stop: beyond the pullback's extreme (lowest low / highest high of those 5 bars) - 0.1 ATR. Target 1:2 (also 1.5R,
        3R and a 3 ATR trailing stop). "Avoid sideways markets": no EMA20/50 cross in the last 20 bars (on/off).
Variants: timeframe 5m, 15m, 15m with the 4h trend (EMA20 > EMA50 on 4h, the suggested combination), 1h, 4h, daily;
pullback EMA only / EMA or S/R; RSI 40-60 / 35-65; volume on/off; sideways filter on/off; long only (spot) / both.
Coins: 15m and 5m BTC ETH SOL BNB XRP (2024-), 1h 4h daily 10 coins + PAXG (2023-). Costs 16 bps round trip.
Design < 1 Apr 2025 <= holdout. Usage: python research/round58_ema_rsi_sr.py <data_dir>
"""
import itertools
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from numba import njit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import round55_engine as E  # noqa: E402

DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data/global"
OUT = os.path.join(HERE, "round58_results.json")
C11 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT", "PAXGUSDT"]
C5 = C11[:5]
TFS = {"5m": C5, "15m": C5, "15m+4h": C5, "1h": C11, "4h": C11, "1d": C11}
CAP = {"5m": 288, "15m": 192, "15m+4h": 192, "1h": 120, "4h": 60, "1d": 30}    # 1 day / 2 / 2 / 5 / 10 / 30 days
EXITS = [("2R", 2.0, 0.0), ("1.5R", 1.5, 0.0), ("3R", 3.0, 0.0), ("trail 3 ATR", 0.0, 3.0)]


def agg(df, rule):
    return df.resample(rule, label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def load(sym, tf):
    src = "5m" if tf in ("5m", "15m", "15m+4h") else "1h"
    df = pd.read_csv(f"{DATA}/crypto_{sym}_{src}.csv.gz", parse_dates=["ts"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    if src == "1h":
        df = df[df.index >= "2022-06-01"]          # EMA warm-up; trades counted from 2023
    if tf in ("15m", "15m+4h"):
        df = agg(df, "15min")
    elif tf in ("4h", "1d"):
        df = agg(df, {"4h": "4h", "1d": "1D"}[tf])
    return df


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    up = pd.Series(np.where(d > 0, d, 0.0)).ewm(alpha=1 / n, adjust=False).mean().values
    dn = pd.Series(np.where(d < 0, -d, 0.0)).ewm(alpha=1 / n, adjust=False).mean().values
    return 100 - 100 / (1 + up / np.where(dn == 0, 1e-12, dn))


@njit(cache=True)
def sr_touch(h, l, a, k, look, tol):
    """sup[i]: bar i's low came within tol*ATR of a confirmed swing low (support) of the last `look` bars.
    res[i]: bar i's high came within tol*ATR of a confirmed swing high (resistance)."""
    n = len(h)
    sup = np.zeros(n, np.bool_)
    res = np.zeros(n, np.bool_)
    lows = np.zeros(n, np.bool_)
    highs = np.zeros(n, np.bool_)
    for p in range(k, n - k):
        lo = True
        hi = True
        for q in range(p - k, p + k + 1):
            if l[q] < l[p]:
                lo = False
            if h[q] > h[p]:
                hi = False
        lows[p] = lo
        highs[p] = hi
    for i in range(k + 1, n):
        if not np.isfinite(a[i]):
            continue
        t = tol * a[i]
        for p in range(max(0, i - look), i - k):          # confirmed: p + k < i
            if lows[p] and abs(l[i] - l[p]) <= t and l[i] >= l[p] - t:
                sup[i] = True
            if highs[p] and abs(h[i] - h[p]) <= t:
                res[i] = True
    return sup, res


def signals(df, htf_trend=None):
    o, h, l, c, v = (df[k].values for k in ("open", "high", "low", "close", "volume"))
    a = E.atr(df)
    e20, e50 = E.ema(c, 20), E.ema(c, 50)
    r = rsi(c)
    sup, res = sr_touch(h, l, a, 5, 100, 0.5)
    up = e20 > e50
    near_e20_l = l <= e20 + 0.25 * a
    near_e20_s = h >= e20 - 0.25 * a
    roll = lambda x: pd.Series(x.astype(float)).rolling(5, min_periods=1).max().values > 0
    pb_ema_l, pb_ema_s = roll(near_e20_l), roll(near_e20_s)
    pb_sr_l, pb_sr_s = roll(near_e20_l | sup), roll(near_e20_s | res)
    cross = np.r_[False, up[1:] != up[:-1]].astype(float)
    no_chop = pd.Series(cross).rolling(20, min_periods=1).sum().values == 0
    vol_up = (v > np.r_[v[0], v[:-1]]) & (v > pd.Series(v).rolling(20, min_periods=1).mean().values)
    bull, bear = c > o, c < o
    r_up, r_dn = r > np.r_[r[0], r[:-1]], r < np.r_[r[0], r[:-1]]
    lo5 = pd.Series(l).rolling(5, min_periods=1).min().values
    hi5 = pd.Series(h).rolling(5, min_periods=1).max().values
    stop_l, stop_s = lo5 - 0.1 * a, hi5 + 0.1 * a
    if htf_trend is not None:
        up_l, dn_s = up & htf_trend, (~up) & (~htf_trend)
    else:
        up_l, dn_s = up, ~up
    return dict(o=o, h=h, l=l, c=c, a=a, r=r, up_l=up_l, dn_s=dn_s, pb={"ema": (pb_ema_l, pb_ema_s), "ema_sr": (pb_sr_l, pb_sr_s)},
                no_chop=no_chop, vol_up=vol_up, bull=bull, bear=bear, r_up=r_up, r_dn=r_dn, stop_l=stop_l, stop_s=stop_s)


def htf_trend_for(df15, sym):
    d4 = agg(df15, "4h")
    up4 = pd.Series(E.ema(d4["close"].values, 20) > E.ema(d4["close"].values, 50), index=d4.index)
    up4.index = up4.index + pd.Timedelta(hours=4)              # known only when the 4h bar has closed
    return up4.reindex(df15.index, method="ffill").fillna(False).values.astype(bool)


def main():
    t0 = time.time()
    res = {}
    for tf, coins in TFS.items():
        for sym in coins:
            df = load(sym, tf)
            S = signals(df, htf_trend_for(df, sym) if tf == "15m+4h" else None)
            o, h, l, c, a, r = S["o"], S["h"], S["l"], S["c"], S["a"], S["r"]
            n = len(c)
            z = np.zeros(n, np.bool_)
            nan = np.full(n, np.nan)
            ts = df.index
            valid = ts >= pd.Timestamp("2023-01-01", tz="UTC")
            hold = ts >= E.SPLIT
            for pbk, band, vol, chop, sides in itertools.product(("ema", "ema_sr"), ((40, 60), (35, 65)), (False, True), (False, True), ("long", "both")):
                pbl, pbs = S["pb"][pbk]
                L = S["up_l"] & pbl & (r >= band[0]) & (r <= band[1]) & S["r_up"] & S["bull"] & valid
                Sh = S["dn_s"] & pbs & (r >= band[0]) & (r <= band[1]) & S["r_dn"] & S["bear"] & valid
                if vol:
                    L &= S["vol_up"]; Sh &= S["vol_up"]
                if chop:
                    L &= S["no_chop"]; Sh &= S["no_chop"]
                side = np.zeros(n, np.int64)
                side[L] = 1
                if sides == "both":
                    side[Sh & ~L] = -1
                stop = np.where(side == 1, S["stop_l"], np.where(side == -1, S["stop_s"], np.nan))
                for exn, tr, trail in EXITS:
                    en, sd, R, b, g = E.run(o, h, l, c, a, side, stop, nan, z, z, tr, trail, 0.0, 0.0, CAP[tf], 0.0)
                    key = json.dumps({"tf": tf, "pullback": pbk, "rsi": f"{band[0]}-{band[1]}", "volume": vol, "no_sideways": chop, "sides": sides, "exit": exn})
                    d = res.setdefault(key, {"R": [], "g": [], "h": [], "coin": [], "ts": [], "entry": [], "stop": [], "bars": []})
                    d["R"] += R.tolist(); d["g"] += g.tolist(); d["h"] += hold[en].tolist(); d["coin"] += [sym] * len(R)
                    d["ts"] += [x.isoformat() for x in ts[en]]
                    d["entry"] += o[en].tolist(); d["stop"] += stop[en - 1].tolist(); d["bars"] += b.tolist()
        print(tf, "done", round(time.time() - t0), "s", flush=True)
    rows = []
    for key, d in res.items():
        R, g, hd, coin = np.array(d["R"]), np.array(d["g"]), np.array(d["h"], bool), np.array(d["coin"])
        per = {s: round(float(R[(coin == s) & hd].mean()), 3) for s in np.unique(coin) if ((coin == s) & hd).sum()}
        b3 = np.isin(coin, ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        rows.append({"v": json.loads(key), "design": E.stats(R[~hd]), "holdout": E.stats(R[hd]), "btc_eth_sol_holdout": E.stats(R[hd & b3]),
                     "gross_design": round(float(g[~hd].mean()), 3) if (~hd).sum() else None, "gross_holdout": round(float(g[hd].mean()), 3) if hd.sum() else None,
                     "coins_pos_holdout": sum(1 for x in per.values() if x > 0), "coins": len(per), "per_coin_holdout": per,
                     "avg_bars": round(float(np.mean(d["bars"])), 1) if d["bars"] else None})
    json.dump(rows, open(OUT, "w"))
    # trade lists for the money simulation of the suggested setups (large: not committed)
    keep = {k: v for k, v in res.items() if json.loads(k)["tf"] in ("15m+4h", "4h") and json.loads(k)["exit"] == "2R"}
    json.dump(keep, open(OUT.replace(".json", "_trades.json"), "w"))
    print("variants", len(rows), round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
