"""The user's own strategies, tested on crypto (17 Oct 2026).

Strategies the user brought to the India F&O app earlier, re-coded for 24-hour crypto (UTC day = session):
  1. India Noise-band rule (Zarattini noise area: open x (1 +/- 1.75 x typical move-since-open), checks every 30 min,
     long above upper band and VWAP / short below lower band and VWAP, exit when back inside at a check, flat at day end)
  2. India Camarilla breakout (5m close crosses yesterday's H4 / L4; stop at H3 / L3 kept 1-3 ATR; stop to entry at +1R;
     exit if +0.5R not reached in 45 min; one trade per side per day) - all day and first-12h variants
  3. D1 break -> first reversal candle -> break of it (video): stop at the signal candle, target the opposite D1 level or 2R
  4. Chandelier Exit (ATR 1, x2), alone (stop and reverse) and with ZLSMA(50) filter (Instagram comment)
  5. SMA 200 + RSI(10) pullback (Instagram post): close > SMA200 and RSI10 < 30 -> buy next open, exit RSI10 > 40 or 10 bars
  6. Creator strategies (round 3): 5 EMA alert candle, 44 MA bounce, RSI 60/40, inside-bar breakout
  7. Suggested strategies (nr7 card): Raschke Holy Grail, Crabel NR7 breakout
Engine: next-bar or stop-order fills, stop wins a bar that touches both stop and target, costs 8 bps per side
(5 fee + 3 slippage, the app's crypto cost). Data: Binance spot 5m Jan 2024 - Oct 2026 (BTC, ETH, SOL, BNB, XRP) and
1h Jan 2023 - Oct 2026 resampled for slower timeframes. Design = before 1 Apr 2025, unseen = after. No tuning:
parameters are the ones the user gave (or the textbook ones where they gave none).
Usage: python research/crypto_user_strategies.py <data_dir>
"""
import json
import os
import sys

import numpy as np
import pandas as pd

DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data/global"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crypto_user_strategies_results.json")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
COST = 8e-4           # per side
SPLIT = pd.Timestamp("2025-04-01", tz="UTC")


# ------------------------------------------------------------------ data + indicators
def load(sym, iv):
    df = pd.read_csv(f"{DATA}/crypto_{sym}_{iv}.csv.gz", parse_dates=["ts"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float).sort_index()


def resample(df, rule):
    return df.resample(rule, label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def tr(df):
    pc = df["close"].shift()
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df, n=14):
    return tr(df).ewm(alpha=1 / n, adjust=False).mean()


def adx(df, n=14):
    up, dn = df["high"].diff(), -df["low"].diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = atr(df, n)
    pdi = 100 * pdm.ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * mdm.ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def linreg(s, n):
    x = np.arange(n)
    xm = x.mean()
    den = ((x - xm) ** 2).sum()

    def f(y):
        b = ((x - xm) * (y - y.mean())).sum() / den
        return y.mean() + b * (n - 1 - xm)
    return s.rolling(n).apply(f, raw=True)


def zlsma(s, n=50):
    l1 = linreg(s, n)
    return 2 * l1 - linreg(l1, n)


def chandelier(df, n=1, mult=2.0):
    """everget Chandelier Exit (close-based): returns direction +1/-1 and the active stop line."""
    a = mult * atr(df, n) if n > 1 else mult * tr(df)
    c = df["close"].values
    hh = df["close"].rolling(n).max().values - a.values
    ll = df["close"].rolling(n).min().values + a.values
    ls, ss = hh.copy(), ll.copy()
    d = np.ones(len(df))
    for i in range(1, len(df)):
        if np.isnan(ls[i - 1]):
            continue
        ls[i] = max(ls[i], ls[i - 1]) if c[i - 1] > ls[i - 1] else ls[i]
        ss[i] = min(ss[i], ss[i - 1]) if c[i - 1] < ss[i - 1] else ss[i]
        d[i] = 1 if c[i] > ss[i - 1] else -1 if c[i] < ls[i - 1] else d[i - 1]
    return pd.Series(d, index=df.index), pd.Series(ls, index=df.index), pd.Series(ss, index=df.index)


# ------------------------------------------------------------------ engine
def run(df, sigs, eod=False):
    """sigs: list of dicts sorted by 'i' (bar index where the order is live):
       side +1/-1, kind 'open' (fill at open[i]) or 'stop' (fill if bar i trades through 'level'),
       stop, target (or None), be_r, time_bars + time_r (exit at close if +time_r R not reached in time_bars),
       max_bars, exit_on (np.bool array: exit at that bar's close), reverse_on (same, and the next signal flips).
    One position at a time. Returns trades with R and net % after costs."""
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    idx = df.index
    day = (idx.asi8 // 86_400_000_000_000) if eod else None
    out, busy_until = [], -1
    n = len(df)
    for s in sigs:
        i = s["i"]
        if i <= busy_until or i >= n:
            continue
        side = s["side"]
        if s["kind"] == "open":
            px = o[i]
        else:
            lv = s["level"]
            if (side > 0 and h[i] < lv) or (side < 0 and l[i] > lv):
                continue
            px = max(o[i], lv) if side > 0 else min(o[i], lv)
        stop = s["stop"]
        risk = side * (px - stop)
        if not np.isfinite(risk) or risk <= 0:
            continue
        tgt = s.get("target")
        be = s.get("be_r")
        ex_on = s.get("exit_on")
        tb, trr = s.get("time_bars"), s.get("time_r", 0.5)
        mx = s.get("max_bars")
        best = 0.0
        exit_px, k, why = None, i, ""
        for k in range(i, n):
            # stop / target inside the bar (stop first). On the entry bar of a stop order only the part after the fill counts; keep it simple: check both.
            if side > 0:
                if l[k] <= stop:
                    exit_px, why = min(o[k], stop) if k > i else stop, "stop"
                    break
                if tgt is not None and h[k] >= tgt:
                    exit_px, why = max(o[k], tgt) if k > i else tgt, "target"
                    break
            else:
                if h[k] >= stop:
                    exit_px, why = max(o[k], stop) if k > i else stop, "stop"
                    break
                if tgt is not None and l[k] <= tgt:
                    exit_px, why = min(o[k], tgt) if k > i else tgt, "target"
                    break
            r_now = side * (c[k] - px) / risk
            best = max(best, side * ((h[k] if side > 0 else l[k]) - px) / risk)
            if be and best >= be and side * (stop - px) < 0:
                stop = px
            if ex_on is not None and ex_on[k]:
                exit_px, why = c[k], "rule"
                break
            if tb and k - i + 1 >= tb and best < trr:
                exit_px, why = c[k], "time"
                break
            if mx and k - i + 1 >= mx:
                exit_px, why = c[k], "max bars"
                break
            if eod and (k + 1 >= n or day[k + 1] != day[k]):
                exit_px, why = c[k], "day end"
                break
        if exit_px is None:
            exit_px, why = c[n - 1], "end of data"
        gross = side * (exit_px - px)
        net_pct = gross / px - 2 * COST
        out.append({"t": idx[i], "side": side, "R": (gross - 2 * COST * px) / risk, "pct": net_pct * 100, "why": why})
        busy_until = k
    return out


# ------------------------------------------------------------------ strategies (each returns trades for one coin)
def day_groups(df):
    return df.index.floor("D")


def s_noise(df5):
    d = df5.copy()
    d["day"] = day_groups(d)
    op = d.groupby("day")["open"].transform("first")
    d["tod"] = d.index.hour * 60 + d.index.minute
    mv = (d["close"] / op - 1).abs()
    piv = mv.groupby([d["day"], d["tod"]]).first().unstack()
    sigma = piv.rolling(14, min_periods=10).mean().shift(1)          # previous 14 days, same time of day
    sg = sigma.stack().rename("sg")
    d = d.join(sg, on=["day", "tod"])
    pc = d.groupby("day")["close"].last().shift(1)
    d["pc"] = d["day"].map(pc)
    up = np.maximum(op, d["pc"]) * (1 + 1.75 * d["sg"])
    lo = np.minimum(op, d["pc"]) * (1 - 1.75 * d["sg"])
    tp = (d["high"] + d["low"] + d["close"]) / 3
    vwap = (tp * d["volume"]).groupby(d["day"]).cumsum() / d["volume"].groupby(d["day"]).cumsum()
    check = np.asarray((d.index + pd.Timedelta(minutes=5)).minute % 30 == 0)
    c = d["close"].values
    longs = check & (c > up.values) & (c > vwap.values)
    shorts = check & (c < lo.values) & (c < vwap.values)
    exit_l = check & ((c < np.maximum(up.values, vwap.values)) | np.isnan(up.values))
    exit_s = check & ((c > np.minimum(lo.values, vwap.values)) | np.isnan(lo.values))
    a = atr(d, 14).values
    sigs = []
    for i in np.flatnonzero(longs | shorts)[:-1]:
        side = 1 if longs[i] else -1
        bound = max(up.values[i], vwap.values[i]) if side > 0 else min(lo.values[i], vwap.values[i])
        stop = bound if side * (c[i] - bound) > 0.2 * a[i] else c[i] - side * a[i]
        sigs.append({"i": i + 1, "side": side, "kind": "open", "stop": stop, "exit_on": exit_l if side > 0 else exit_s})
    return run(d, sigs, eod=True)


def s_camarilla(df5, first_hours=None):
    d = df5
    dayk = day_groups(d)
    daily = d.groupby(dayk).agg(h=("high", "max"), l=("low", "min"), c=("close", "last")).shift(1)
    rng = (daily["h"] - daily["l"]) * 1.1
    lv = pd.DataFrame({"h4": daily["c"] + rng / 2, "h3": daily["c"] + rng / 4, "l3": daily["c"] - rng / 4, "l4": daily["c"] - rng / 2})
    L = lv.reindex(dayk).values
    c = d["close"].values
    a = atr(d, 14).values
    hrs = d.index.hour.values
    sigs, done = [], set()
    for i in range(1, len(d) - 1):
        if dayk[i] != dayk[i - 1] or not np.isfinite(L[i, 0]):
            continue
        if first_hours and hrs[i] >= first_hours:
            continue
        for side, cross, ref in ((1, c[i - 1] <= L[i, 0] < c[i], L[i, 1]), (-1, c[i - 1] >= L[i, 3] > c[i], L[i, 2])):
            if cross and (dayk[i], side) not in done:
                done.add((dayk[i], side))
                risk = min(max(abs(c[i] - ref), a[i]), 3 * a[i])
                sigs.append({"i": i + 1, "side": side, "kind": "open", "stop": c[i] - side * risk, "be_r": 1.0, "time_bars": 9, "time_r": 0.5})
    return run(d, sigs, eod=True)


def s_d1break(df5, target="d1"):
    d = df5
    dayk = day_groups(d)
    daily = d.groupby(dayk).agg(h=("high", "max"), l=("low", "min")).shift(1)
    H, Lw = daily["h"].reindex(dayk).values, daily["l"].reindex(dayk).values
    o, h, l, c = (d[k].values for k in ("open", "high", "low", "close"))
    hrs = d.index.hour.values
    sigs = []
    state = {}
    for i in range(1, len(d) - 1):
        k = dayk[i]
        if k != dayk[i - 1]:
            state = {"low_broken": False, "high_broken": False, "done_l": False, "done_s": False}
        if not np.isfinite(H[i]) or hrs[i] >= 23:
            continue
        if c[i] < Lw[i]:
            state["low_broken"] = True
        if c[i] > H[i]:
            state["high_broken"] = True
        # first green candle after a low break -> buy if the next candle breaks its high
        if state["low_broken"] and not state["done_l"] and c[i] > o[i]:
            state["done_l"] = True
            stop, risk = l[i], h[i] - l[i]
            tgt = H[i] if target == "d1" else h[i] + 2 * risk
            sigs.append({"i": i + 1, "side": 1, "kind": "stop", "level": h[i], "stop": stop, "target": tgt})
        if state["high_broken"] and not state["done_s"] and c[i] < o[i]:
            state["done_s"] = True
            stop, risk = h[i], h[i] - l[i]
            tgt = Lw[i] if target == "d1" else l[i] - 2 * risk
            sigs.append({"i": i + 1, "side": -1, "kind": "stop", "level": l[i], "stop": stop, "target": tgt})
    return run(d, sigs, eod=True)


def s_chandelier(df, use_zl):
    dirn, ls, ss = chandelier(df, 1, 2.0)
    zl = zlsma(df["close"], 50) if use_zl else None
    dv = dirn.values
    flip = np.r_[False, dv[1:] != dv[:-1]]
    c = df["close"].values
    sigs = []
    buy_sig, sell_sig = flip & (dv > 0), flip & (dv < 0)
    if use_zl:
        z = zl.values
        zl_cross_dn = np.r_[False, (c[1:] < z[1:]) & (c[:-1] >= z[:-1])]
        zl_cross_up = np.r_[False, (c[1:] > z[1:]) & (c[:-1] <= z[:-1])]
    for i in np.flatnonzero(buy_sig | sell_sig)[:-1]:
        side = 1 if buy_sig[i] else -1
        if use_zl and not ((side > 0 and c[i] > zl.values[i]) or (side < 0 and c[i] < zl.values[i])):
            continue
        ex = sell_sig if side > 0 else buy_sig
        if use_zl:
            ex = ex | (zl_cross_dn if side > 0 else zl_cross_up)
        stop = ls.values[i] if side > 0 else ss.values[i]
        sigs.append({"i": i + 1, "side": side, "kind": "open", "stop": stop if np.isfinite(stop) else c[i] * (1 - side * 0.01), "exit_on": ex})
    return run(df, sigs)


def s_rsi10(df):
    sma = df["close"].rolling(200).mean()
    r = rsi(df["close"], 10)
    c = df["close"].values
    ent = (c > sma.values) & (r.values < 30)
    ex = r.values > 40
    a = atr(df, 14).values
    # no stop in the original: a far 5 x ATR safety stop only so R can be measured
    sigs = [{"i": i + 1, "side": 1, "kind": "open", "stop": c[i] - 5 * a[i], "exit_on": ex, "max_bars": 10} for i in np.flatnonzero(ent)[:-1]]
    return run(df, sigs)


def s_5ema(df):
    e = ema(df["close"], 5).values
    h, l = df["high"].values, df["low"].values
    sigs = []
    for i in range(5, len(df) - 1):
        if l[i] > e[i]:        # alert candle fully above the 5 EMA -> sell on a break of its low
            sigs.append({"i": i + 1, "side": -1, "kind": "stop", "level": l[i], "stop": h[i], "target": l[i] - 3 * (h[i] - l[i])})
        elif h[i] < e[i]:      # fully below -> buy on a break of its high
            sigs.append({"i": i + 1, "side": 1, "kind": "stop", "level": h[i], "stop": l[i], "target": h[i] + 3 * (h[i] - l[i])})
    return run(df, sigs)


def s_44ma(df):
    m = df["close"].rolling(44).mean().values
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    rising = np.r_[np.zeros(5, bool), m[5:] > m[:-5]]
    sigs = []
    for i in np.flatnonzero(rising & (l <= m) & (c > m) & (c > o))[:-1]:
        sigs.append({"i": i + 1, "side": 1, "kind": "stop", "level": h[i], "stop": l[i], "target": h[i] + 2 * (h[i] - l[i])})
    return run(df, sigs)


def s_rsi6040(df):
    r = rsi(df["close"], 14).values
    up = np.r_[False, (r[1:] > 60) & (r[:-1] <= 60)]
    dn = np.r_[False, (r[1:] < 40) & (r[:-1] >= 40)]
    c = df["close"].values
    a = atr(df, 14).values
    sigs = []
    for i in np.flatnonzero(up | dn)[:-1]:
        side = 1 if up[i] else -1
        sigs.append({"i": i + 1, "side": side, "kind": "open", "stop": c[i] - side * 2 * a[i], "exit_on": dn if side > 0 else up})
    return run(df, sigs)


def s_insidebar(df):
    h, l = df["high"].values, df["low"].values
    sigs = []
    for i in range(2, len(df) - 1):
        if h[i] < h[i - 1] and l[i] > l[i - 1]:
            mh, ml = h[i - 1], l[i - 1]
            rng = mh - ml
            sigs.append({"i": i + 1, "side": 1, "kind": "stop", "level": mh, "stop": ml, "target": mh + 2 * rng})
            sigs.append({"i": i + 1, "side": -1, "kind": "stop", "level": ml, "stop": mh, "target": ml - 2 * rng})
    return run(df, sigs)


def s_holygrail(df):
    ax = adx(df, 14).values
    e = ema(df["close"], 20).values
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    sigs = []
    for i in range(30, len(df) - 1):
        if ax[i] > 30 and ax[i] >= ax[i - 3]:
            if c[i - 1] > e[i - 1] and l[i] <= e[i]:      # uptrend pulls back to the 20 EMA
                hh = h[max(0, i - 20):i].max()
                sigs.append({"i": i + 1, "side": 1, "kind": "stop", "level": h[i], "stop": l[i], "target": max(hh, h[i] + 1.5 * (h[i] - l[i]))})
            elif c[i - 1] < e[i - 1] and h[i] >= e[i]:
                ll = l[max(0, i - 20):i].min()
                sigs.append({"i": i + 1, "side": -1, "kind": "stop", "level": l[i], "stop": h[i], "target": min(ll, l[i] - 1.5 * (h[i] - l[i]))})
    return run(df, sigs)


def s_nr7(df):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    rng = h - l
    sigs = []
    for i in range(7, len(df) - 1):
        if rng[i] <= rng[i - 6:i + 1].min():
            sigs.append({"i": i + 1, "side": 1, "kind": "stop", "level": h[i], "stop": l[i], "max_bars": 1})
            sigs.append({"i": i + 1, "side": -1, "kind": "stop", "level": l[i], "stop": h[i], "max_bars": 1})
    return run(df, sigs)


# ------------------------------------------------------------------ driver
def tests_for(sym):
    out = {}
    m5 = load(sym, "5m")
    h1 = load(sym, "1h")
    tfs = {"5m": m5, "15m": resample(m5, "15min"), "1h": h1, "4h": resample(h1, "4h"), "1d": resample(h1, "1D")}
    plan = [
        ("India Noise band (5m, 30-min checks)", "5m", s_noise),
        ("India Camarilla H4/L4 breakout (5m, all day)", "5m", s_camarilla),
        ("India Camarilla H4/L4 breakout (5m, first 12h UTC)", "5m", lambda d: s_camarilla(d, 12)),
        ("D1 break reversal, target opposite D1 level (5m)", "5m", s_d1break),
        ("D1 break reversal, 2R target (5m)", "5m", lambda d: s_d1break(d, "2r")),
    ]
    for tf in ("5m", "15m", "1h", "4h"):
        plan += [(f"Chandelier Exit 1/2 ({tf})", tf, lambda d: s_chandelier(d, False)), (f"Chandelier Exit + ZLSMA 50 ({tf})", tf, lambda d: s_chandelier(d, True))]
    for tf in ("1h", "4h", "1d"):
        plan.append((f"SMA200 + RSI(10) pullback ({tf})", tf, s_rsi10))
    for tf in ("5m", "15m", "1h"):
        plan.append((f"5 EMA alert candle, 3R ({tf})", tf, s_5ema))
    for tf in ("1h", "4h", "1d"):
        plan.append((f"44 MA bounce, 2R ({tf})", tf, s_44ma))
        plan.append((f"Inside-bar breakout, 2R ({tf})", tf, s_insidebar))
        plan.append((f"Holy Grail ADX>30 + 20 EMA ({tf})", tf, s_holygrail))
    for tf in ("15m", "1h", "4h"):
        plan.append((f"RSI 60/40 cross ({tf})", tf, s_rsi6040))
    for tf in ("4h", "1d"):
        plan.append((f"NR7 breakout, one bar ({tf})", tf, s_nr7))
    for name, tf, fn in plan:
        df = tfs[tf]
        if tf in ("1h", "4h", "1d"):
            df = df[df.index >= pd.Timestamp("2023-01-01", tz="UTC")]
        out[name] = [(t["t"].value, t["R"], t["pct"]) for t in fn(df)]
    return sym, out


def st(rows):
    if not rows:
        return {"n": 0}
    R = np.array([r[1] for r in rows])
    P = np.array([r[2] for r in rows])
    lo = -P[P <= 0].sum()
    return {"n": len(rows), "win": round(float((P > 0).mean() * 100), 1), "avg_pct": round(float(P.mean()), 3), "avg_R": round(float(np.clip(R, -5, 50).mean()), 3),
            "pf": round(float(P[P > 0].sum() / lo), 2) if lo > 0 else None, "sum_pct": round(float(P.sum()), 1),
            "t": round(float(P.mean() / (P.std(ddof=1) / np.sqrt(len(P)))), 2) if len(P) > 2 else None}


def main():
    # one coin per child process (keeps memory low on small machines); results cached next to the script
    import pickle
    import subprocess
    res = {}
    for sym in COINS:
        f = os.path.join(os.path.dirname(OUT), f".cache_user_{sym}.pkl")
        if not os.path.exists(f):
            subprocess.run([sys.executable, "-W", "ignore", os.path.abspath(__file__), DATA, "--coin", sym], check=True)
        res[sym] = pickle.load(open(f, "rb"))
    names = list(res[COINS[0]])
    rows = []
    for nm in names:
        allr = sum((res[s][nm] for s in COINS), [])
        des = [r for r in allr if r[0] < SPLIT.value]
        hol = [r for r in allr if r[0] >= SPLIT.value]
        gross_hol = [(r[0], r[1], r[2] + 2 * COST * 100) for r in hol]
        per = {s: st([r for r in res[s][nm] if r[0] >= SPLIT.value]) for s in COINS}
        row = {"strategy": nm, "design": st(des), "unseen": st(hol), "unseen_before_costs": st(gross_hol), "unseen_by_coin": per}
        D, H = row["design"], row["unseen"]
        row["pass"] = bool(D.get("n", 0) >= 50 and H.get("n", 0) >= 50 and D["avg_pct"] > 0 and H["avg_pct"] > 0 and (H["pf"] or 0) >= 1.1
                           and sum(1 for v in per.values() if v.get("n") and v["avg_pct"] > 0) >= 3)
        rows.append(row)
        print(f"{nm:52} design n={D.get('n',0):6} {D.get('avg_pct',0):+.3f}% win {D.get('win',0):4} | unseen n={H.get('n',0):6} {H.get('avg_pct',0):+.3f}% "
              f"win {H.get('win',0):4} PF {H.get('pf')} sum {H.get('sum_pct',0):+.0f}% (gross {row['unseen_before_costs'].get('avg_pct',0):+.3f}%) {'PASS' if row['pass'] else ''}", flush=True)
    json.dump({"rows": rows, "split": str(SPLIT.date()), "cost_per_side": COST}, open(OUT, "w"), indent=1, default=str)


if __name__ == "__main__":
    if "--coin" in sys.argv:
        import pickle
        sym = sys.argv[sys.argv.index("--coin") + 1]
        pickle.dump(tests_for(sym)[1], open(os.path.join(os.path.dirname(OUT), f".cache_user_{sym}.pkl"), "wb"))
        print("done", sym, flush=True)
    else:
        main()
