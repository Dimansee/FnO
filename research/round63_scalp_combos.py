"""Round 63: combinations of the round-62 scalping strategies (29 Oct 2026). User: "is there any combo that works".

Each of the 8 round-62 entries (ribbon, RS breakout, momentum, pivot bounce, RMI + Supertrend, regression channel, EMA 9/21,
EMA + RSI) is taken only when 0-3 other "confirmations" agree with its direction at the signal bar:
  ribbon (SMA 5 > 8 > 13), macd (> 0), supertrend (10, 3 up), rmi (> 50), ema (9 > 21), slope (50-bar regression up),
  rsi (> 50), htf (price above the 1-hour EMA 50, using only finished 1-hour bars).   Shorts: all mirrored.
That is 93 filter sets x 8 entries per market / timeframe. Each keeps its own round-62 exits.
Markets / timeframes: Nifty, Bank Nifty, Sensex 1m / 2m / 5m / 15m; 10 Nifty stocks 5m / 15m; 5 coins 5m / 15m.
Costs: index ATM option ~0.012% (optimistic), stocks 0.06%, crypto 0.08% (optimistic maker-ish) and 0.16% (taker).
Honest test: combinations are ranked on trades before 1 Apr 2025 only and judged on the trades after.
Usage: python research/round63_scalp_combos.py <data_dir>
"""
import itertools
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import round62_scalping7 as S  # noqa: E402
from fno import indicators as IND  # noqa: E402

S.DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data"
OUT = os.path.join(HERE, "round63_results.json")
SPLIT = pd.Timestamp("2025-04-01")
STATES = ["ribbon", "macd", "supertrend", "rmi", "ema", "slope", "rsi", "htf"]
FSETS = [()] + [fs for k in (1, 2, 3) for fs in itertools.combinations(STATES, k)]
COST = {"index": {"option": 0.00012}, "stock": {"intraday": 0.0006}, "crypto": {"0.08%": 0.0008, "0.16%": 0.0016}}


def load(market, sym, tf):
    if tf == "15m":
        base = S.load(market, sym, "5m" if market != "index" else "5m")
        if market == "index":
            return base.groupby(base.index.date, group_keys=False).apply(lambda g: g.resample("15min", origin=g.index[0], label="left", closed="left")
                                                                         .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna())
        return S.agg(base, "15min")
    return S.load(market, sym, tf)


def states(df):
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    s5, s8, s13 = S.sma(c, 5), S.sma(c, 8), S.sma(c, 13)
    macd = S.ema(c, 12) - S.ema(c, 26)
    tr = np.asarray(IND.supertrend_bands(df, 10, 3.0)[0])
    e9, e21 = S.ema(c, 9), S.ema(c, 21)
    _, slope, _ = S.linreg(c, 50)
    r = S.rsi(c)
    rm = S.rmi(c)
    hc = df["close"].resample("1h").last().dropna()
    he = pd.Series(S.ema(hc.values, 50), index=hc.index + pd.Timedelta(hours=1))      # known once the hour is finished
    he = he.reindex(df.index, method="ffill").values
    up = {"ribbon": (s5 > s8) & (s8 > s13), "macd": macd > 0, "supertrend": tr > 0, "rmi": rm > 50, "ema": e9 > e21,
          "slope": slope > 0, "rsi": r > 50, "htf": c > he}
    dn = {"ribbon": (s5 < s8) & (s8 < s13), "macd": macd < 0, "supertrend": tr < 0, "rmi": rm < 50, "ema": e9 < e21,
          "slope": slope < 0, "rsi": r < 50, "htf": c < he}
    return up, dn


def main():
    t0 = time.time()
    plan = [("index", s, tf) for s in S.INDEX for tf in ("1m", "2m", "5m", "15m")] + [("stock", s, tf) for s in S.STOCKS for tf in ("5m", "15m")] + \
           [("crypto", s, tf) for s in S.COINS for tf in ("5m", "15m")]
    res = {}
    bench_cache = {}
    for market, sym, tf in plan:
        df = load(market, sym, tf)
        bench = None
        bsym = {"stock": ("index", "NIFTY"), "crypto": ("crypto", "BTCUSDT")}.get(market)
        if bsym and sym != bsym[1]:
            k = (bsym, tf)
            if k not in bench_cache:
                bench_cache[k] = load(bsym[0], bsym[1], tf)["close"]
            bench = bench_cache[k].reindex(df.index).ffill().values
        o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
        day, dayend, _, _, _ = S.session_info(market, df.index)
        hold = df.index >= SPLIT
        years = df.index.year.values
        up, dn = states(df)
        tf_s = "5m" if tf == "15m" else tf          # round-62 strategy set for this bar size (15m reuses the 5m rules)
        for name, side, stop, tgt, exl, exs, cap, allow in S.strategies(market, tf_s, df, bench):
            if "comparison" in name or "variant" in name:
                continue
            for fs in FSETS:
                sd = side.copy()
                for f in fs:
                    sd[(sd == 1) & ~up[f]] = 0
                    sd[(sd == -1) & ~dn[f]] = 0
                if not (sd != 0).any():
                    continue
                en, s2, R, bars = S.sim(o, h, l, c, sd, stop, tgt, exl, exs, cap, dayend, allow)
                key = json.dumps({"market": market, "tf": tf, "entry": name, "filters": list(fs)})
                d = res.setdefault(key, {"R": [], "h": [], "yr": [], "sym": []})
                d["R"] += R.tolist(); d["h"] += hold[en].tolist(); d["yr"] += years[en].tolist(); d["sym"] += [sym] * len(R)
        print(market, sym, tf, round(time.time() - t0), "s", flush=True)
    rows = []
    for key, d in res.items():
        v = json.loads(key)
        R, hd, yr, sy = np.array(d["R"]), np.array(d["h"], bool), np.array(d["yr"]), np.array(d["sym"])
        row = {"v": v, "n_d": int((~hd).sum()), "n_h": int(hd.sum()), "gross_d": float(R[~hd].mean() * 100) if (~hd).any() else None,
               "gross_h": float(R[hd].mean() * 100) if hd.any() else None, "net": {}}
        for cn, cf in COST[v["market"]].items():
            N = R - cf
            row["net"][cn] = {"d": float(N[~hd].mean() * 100) if (~hd).any() else None, "h": float(N[hd].mean() * 100) if hd.any() else None,
                              "years": {str(y): round(float(N[yr == y].mean() * 100), 4) for y in np.unique(yr)},
                              "sym_pos_h": int(sum(1 for s in np.unique(sy) if (hd & (sy == s)).any() and N[hd & (sy == s)].mean() > 0)),
                              "syms": int(len(np.unique(sy)))}
        rows.append(row)
    json.dump(rows, open(OUT, "w"))
    print("rows", len(rows), round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
