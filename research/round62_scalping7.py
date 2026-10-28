"""Round 62: the "7 scalping strategies at a glance" table, tested as written (28 Oct 2026).

Strategies (rules from the user's table; where it is vague the choice is stated):
 S1 Moving-average ribbon: SMA 5/8/13 on 2-min. Long the bar the three line up upward with clear spacing (each gap >= 0.05
    ATR; fresh alignment). Stop 13 SMA - 0.1 ATR ("2-3 ticks"). Target the last 20-bar swing high (if at least 0.5 ATR
    away, else 1.5x the risk). Exit when SMA5 crosses back below SMA8 (ribbon compresses), volume falls under half its
    average, or 30 bars. Active hours only (India 09:15-11:30 and 13:30-15:00; crypto 07-21 UTC). Shorts mirror.
 S2 Relative strength / weakness exit: long a 20-bar breakout while the instrument beats its benchmark (price / benchmark
    ratio above its 20-bar EMA); exit when that ratio falls below its 10-bar EMA. Stop 1 ATR, cap 48 bars. Shorts mirror.
    Compared with the same entries and a plain 1.5R target. Benchmarks: Nifty for stocks, BTC for coins, SPY for US.
 S3 Momentum: RSI(14) + MACD(12,26,9) on 1-min, first two trading hours. Buy when RSI crosses above 40 with MACD > 0 and
    volume above average; sell when RSI crosses below 60 with MACD < 0. Exit at the first momentum weakness (MACD histogram
    shrinks) or RSI 70 / 30; stop 0.1% from entry; cap 10 bars. (Scaling out in thirds not modelled.)
 S4 Pivot point: daily floor pivots on 1-min. Buy the first touch of S2 / S1 / P from above that closes back above it,
    target the next pivot up, stop at the pivot below; sell the mirror at R1 / R2 / P. Max hold 5 bars.
 S5 RMI trend sync: RMI(14, momentum 5) + Supertrend(10, 3). Buy when price is above Supertrend and RMI is above 30
    (signal when both first become true); exit when RMI falls back below 70 after being above it, or price closes through
    Supertrend; stop = the Supertrend line. Tested on 1-min and 5-min.
 S6 Linear regression channel: 50-bar regression line +- 2 standard deviations, with Bollinger 20/2. Buy when the low
    touches the lower channel line, closes back inside and the channel slopes up; target the opposite line (and, as a
    variant, the middle). Stop 0.5 sigma outside the channel. Exit if the slope changes sign or the Bollinger width jumps
    above 1.5x its 50-bar average. 2-min and 5-min. Shorts mirror.
 S7a EMA crossover: EMA 9/21 on 5-min, ATR(20). Long on the cross up with rising volume; target 1.5 ATR, stop 1 ATR; exit
    if the EMAs flatten (gap < 0.1 ATR); cap 24 bars.
 S7b EMA / RSI filter: 1-min. Long when EMA 9 crosses above EMA 21 with price above EMA 9 and RSI > 50; exit on the
    opposite cross or RSI back through 50; stop the 5-bar swing low; target the 30-bar high; exit if the EMAs compress.
Markets: Nifty / Bank Nifty / Sensex 1-min (2023 - 16 Oct 2026; index volume does not exist, so volume rules are skipped
there), 12 Nifty stocks 5-min (2023-), crypto BTC ETH SOL BNB XRP 5-min (2024-), 14 US stocks / ETFs 5-min (Aug-Oct
2026 only). India and US positions are closed by the end of the session (India 15:20, no new trades after 15:00).
Costs per round trip (fraction of price): India index via ATM option ~0.012% (about 3 Nifty points: brokerage, charges
and half the spread, an optimistic figure - real fills were worse in rounds 34 / 36), India index futures 0.06%,
India stocks intraday 0.06%, crypto 0.16% (taker) and 0.08%, US 0.02%. Entry at the next bar's open; stop before target.
Usage: python research/round62_scalping7.py <data_dir>
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from numba import njit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from fno import indicators as IND  # noqa: E402

DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data"
OUT = os.path.join(HERE, "round62_results.json")
INDEX = ["NIFTY", "BANKNIFTY", "SENSEX"]
STOCKS = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "BHARTIARTL", "ITC", "LT", "AXISBANK"]
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
US = ["SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD", "JPM"]
COSTS = {"index": {"option ~3 pts": 0.00012, "futures": 0.0006}, "stock": {"intraday": 0.0006},
         "crypto": {"taker 0.16%": 0.0016, "0.08%": 0.0008}, "us": {"0.02%": 0.0002}}


# ------------------------------------------------------------------ data
def agg(df, rule):
    return df.resample(rule, label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


def load(market, sym, tf):
    if market == "index":
        df = pd.read_csv(f"{DATA}/{sym}_1m_upstox.csv.gz", parse_dates=["ts"]).set_index("ts")
    elif market == "stock":
        df = pd.read_csv(f"{DATA}/{sym}_5m_upstox.csv.gz", parse_dates=["ts"]).set_index("ts")
    elif market == "crypto":
        df = pd.read_csv(f"{DATA}/global/crypto_{sym}_5m.csv.gz", parse_dates=["ts"]).set_index("ts")
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
    else:
        df = pd.read_csv(f"{DATA}/global/us_{sym}_5m.csv.gz", parse_dates=["ts"]).set_index("ts")
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df[~df.index.duplicated()].sort_index()
    if tf in ("2m", "5m") and market == "index":
        # resample inside each session only (09:15 anchor)
        df = df.groupby(df.index.date, group_keys=False).apply(lambda g: g.resample({"2m": "2min", "5m": "5min"}[tf], origin=g.index[0], label="left", closed="left")
                                                              .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna())
    return df


def session_info(market, idx):
    """(day id, minutes since session open, day-end flag, entry-allowed flag, active-hours flag, first-2h flag)."""
    t = pd.DatetimeIndex(idx)
    day = t.normalize().values.astype("int64") // 86_400_000_000_000
    hm = t.hour * 60 + t.minute
    n = len(t)
    last = np.r_[day[1:] != day[:-1], True]
    if market in ("index", "stock"):          # IST timestamps
        mins = hm - (9 * 60 + 15)
        dayend = last | (hm >= 15 * 60 + 20)
        allowed = (hm >= 9 * 60 + 15) & (hm < 15 * 60) & ~last
        active = ((hm < 11 * 60 + 30) | ((hm >= 13 * 60 + 30) & (hm < 15 * 60)))
        first2 = mins < 120
    elif market == "us":                      # UTC; Aug-Oct = EDT, session 13:30-20:00
        mins = hm - (13 * 60 + 30)
        dayend = last | (hm >= 19 * 60 + 50)
        allowed = (mins >= 0) & (hm < 19 * 60 + 30) & ~last
        active = allowed
        first2 = (mins >= 0) & (mins < 120)
    else:                                     # crypto, UTC, never closes
        mins = hm
        dayend = np.zeros(n, bool)
        allowed = np.ones(n, bool)
        active = (hm >= 7 * 60) & (hm < 21 * 60)
        first2 = ((hm >= 7 * 60) & (hm < 9 * 60)) | ((hm >= 13 * 60 + 30) & (hm < 15 * 60 + 30))   # London open + NY open
    return day, dayend, allowed & np.r_[~dayend[1:], False], active, first2


# ------------------------------------------------------------------ indicators
def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


def sma(x, n):
    return pd.Series(x).rolling(n, min_periods=n).mean().values


def wilder(x, n):
    return pd.Series(x).ewm(alpha=1 / n, adjust=False).mean().values


def atr(h, l, c, n=14):
    pc = np.r_[c[0], c[:-1]]
    return wilder(np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc))), n)


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    up, dn = wilder(np.where(d > 0, d, 0.0), n), wilder(np.where(d < 0, -d, 0.0), n)
    return 100 - 100 / (1 + up / np.where(dn == 0, 1e-12, dn))


def rmi(c, n=14, m=5):
    d = c - np.r_[np.full(m, c[0]), c[:-m]]
    up, dn = wilder(np.where(d > 0, d, 0.0), n), wilder(np.where(d < 0, -d, 0.0), n)
    return 100 * up / np.where(up + dn == 0, 1e-12, up + dn)


@njit(cache=True)
def linreg(c, w):
    n = len(c)
    mid, slope, sd = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    x = np.arange(w) - (w - 1) / 2.0
    sxx = (x * x).sum()
    for i in range(w - 1, n):
        y = c[i - w + 1:i + 1]
        my = y.mean()
        b = (x * (y - my)).sum() / sxx
        fit = my + b * x
        mid[i], slope[i], sd[i] = fit[-1], b, np.sqrt(((y - fit) ** 2).mean())
    return mid, slope, sd


def cross_up(a, b):
    return (a > b) & (np.r_[a[0], a[:-1]] <= np.r_[b[0], b[:-1]])


def prev(x):
    return np.r_[x[0], x[:-1]]


# ------------------------------------------------------------------ simulator
@njit(cache=True)
def sim(o, h, l, c, side, stop, tgt, ex_l, ex_s, cap, dayend, allowed):
    """One position at a time. Signal on bar i close -> entry at bar i+1 open. Inside a bar the stop is checked before the
    target; a gap through the stop fills at the open. ex_l / ex_s on bar j close -> exit at j+1 open. cap bars max.
    dayend[j] -> exit at bar j close. Returns entry index, side, gross return (fraction), bars held."""
    n = len(o)
    en, sd, rr, bb = np.empty(n, np.int64), np.empty(n, np.int64), np.empty(n), np.empty(n, np.int64)
    m = 0
    i = 0
    while i < n - 1:
        s = side[i]
        if s == 0 or not allowed[i]:
            i += 1
            continue
        j = i + 1
        e = o[j]
        sl, tp = stop[i], tgt[i]
        if np.isfinite(sl) and s * (e - sl) <= 0:      # opened beyond the stop: skip
            i += 1
            continue
        if np.isfinite(tp) and s * (tp - e) <= 0:
            i += 1
            continue
        x = np.nan
        k = j
        while k < n:
            if np.isfinite(sl):
                if s == 1 and l[k] <= sl:
                    x = min(o[k], sl) if k > j else sl
                    break
                if s == -1 and h[k] >= sl:
                    x = max(o[k], sl) if k > j else sl
                    break
            if np.isfinite(tp):
                if s == 1 and h[k] >= tp:
                    x = max(o[k], tp) if k > j else tp
                    break
                if s == -1 and l[k] <= tp:
                    x = min(o[k], tp) if k > j else tp
                    break
            if dayend[k] or k - j + 1 >= cap or k == n - 1:
                x = c[k]
                break
            if (s == 1 and ex_l[k]) or (s == -1 and ex_s[k]):
                x = o[k + 1]
                k += 1
                break
            k += 1
        en[m], sd[m], rr[m], bb[m] = j, s, s * (x - e) / e, k - j + 1
        m += 1
        i = k + 1 if k > i else i + 1
    return en[:m], sd[:m], rr[:m], bb[:m]


# ------------------------------------------------------------------ strategies
def strategies(market, tf, df, bench=None):
    """Yield (name, side, stop, target, exit_long, exit_short, cap, extra-allowed)."""
    o, h, l, c, v = (df[k].values for k in ("open", "high", "low", "close", "volume"))
    n = len(c)
    has_vol = np.nansum(v) > 0
    vavg = sma(v, 20)
    A = atr(h, l, c, 14)
    nanv, F = np.full(n, np.nan), np.zeros(n, bool)
    day, dayend, allowed, active, first2 = session_info(market, df.index)
    out = []

    def add(name, L, S, stop, tgt, exl, exs, cap, extra=None):
        side = np.zeros(n, np.int64)
        side[L] = 1
        side[S & ~L] = -1
        out.append((name, side, stop, tgt, exl, exs, cap, allowed & (extra if extra is not None else True)))

    # S1 ribbon (2m for the index, 5m elsewhere)
    if tf in ("2m", "5m") and not (market == "index" and tf == "5m"):
        s5, s8, s13 = sma(c, 5), sma(c, 8), sma(c, 13)
        g = 0.05 * A
        up = (s5 - s8 > g) & (s8 - s13 > g)
        dn = (s8 - s5 > g) & (s13 - s8 > g)
        L, S = up & ~prev(up), dn & ~prev(dn)
        hh = pd.Series(h).rolling(20).max().shift(1).values
        ll = pd.Series(l).rolling(20).min().shift(1).values
        stop = np.where(L, s13 - 0.1 * A, np.where(S, s13 + 0.1 * A, np.nan))
        risk = np.abs(c - stop)
        tg = np.where(L, np.where(hh > c + 0.5 * A, hh, c + 1.5 * risk), np.where(S, np.where(ll < c - 0.5 * A, ll, c - 1.5 * risk), np.nan))
        vdrop = (v < 0.5 * vavg) if has_vol else F
        add("S1 ribbon 5-8-13", L, S, stop, tg, (s5 < s8) | vdrop, (s5 > s8) | vdrop, 30, active)
    # S2 relative strength / weakness exit
    if bench is not None and tf == "5m":
        ratio = c / bench
        re20, re10 = ema(ratio, 20), ema(ratio, 10)
        hi20 = pd.Series(h).rolling(20).max().shift(1).values
        lo20 = pd.Series(l).rolling(20).min().shift(1).values
        L, S = (c > hi20) & (ratio > re20), (c < lo20) & (ratio < re20)
        stop = np.where(L, c - A, np.where(S, c + A, np.nan))
        add("S2 RS breakout, exit on RS weakness", L, S, stop, nanv, ratio < re10, ratio > re10, 48)
        add("S2 RS breakout, 1.5R target (comparison)", L, S, stop, np.where(L, c + 1.5 * A, np.where(S, c - 1.5 * A, np.nan)), F, F, 48)
    # S3 momentum (1m index; 5m elsewhere)
    if (market == "index" and tf == "1m") or (market != "index" and tf == "5m"):
        r = rsi(c)
        macd = ema(c, 12) - ema(c, 26)
        hist = macd - ema(macd, 9)
        vok = (v > vavg) if has_vol else ~F
        L = (r >= 40) & (prev(r) < 40) & (macd > 0) & vok
        S = (r <= 60) & (prev(r) > 60) & (macd < 0) & vok
        stop = np.where(L, c * 0.999, np.where(S, c * 1.001, np.nan))
        weak_l, weak_s = (hist < prev(hist)) | (r >= 70), (hist > prev(hist)) | (r <= 30)
        win = first2 & (active if market != "crypto" else True)
        add("S3 momentum RSI+MACD, 0.1% stop", L, S, stop, nanv, weak_l, weak_s, 10, win)
        stop2 = np.where(L, c - A, np.where(S, c + A, np.nan))
        add("S3 momentum RSI+MACD, 1 ATR stop (variant)", L, S, stop2, nanv, weak_l, weak_s, 10, win)
    # S4 pivot scalping (1m index; 5m elsewhere)
    if (market == "index" and tf == "1m") or (market != "index" and tf == "5m"):
        d = pd.Series(day, index=df.index)
        g = df.groupby(day).agg(H=("high", "max"), L=("low", "min"), C=("close", "last"))
        P = (g.H + g.L + g.C) / 3
        lv = pd.DataFrame({"S3": g.L - 2 * (g.H - P), "S2": P - (g.H - g.L), "S1": 2 * P - g.H, "P": P, "R1": 2 * P - g.L,
                           "R2": P + (g.H - g.L), "R3": g.H + 2 * (P - g.L)}).shift(1)        # previous session's levels
        LV = lv.reindex(d.values).values                      # (n, 7) ordered S3..R3
        L, S = np.zeros(n, bool), np.zeros(n, bool)
        stop, tg = np.full(n, np.nan), np.full(n, np.nan)
        pc = prev(c)
        touched = np.zeros((n, 7), bool)
        for k in range(7):
            col = LV[:, k]
            t = (l <= col) & (h >= col)
            ser = pd.Series(t.astype(int)).groupby(day).cumsum().values
            touched[:, k] = ser == 1                          # first touch of this level today
        for k, name in ((1, "S2"), (2, "S1"), (3, "P")):      # supports: bounce long
            lvk = LV[:, k]
            sig = touched[:, k] & (pc > lvk) & (l <= lvk) & (c > lvk)
            L |= sig
            stop = np.where(sig & np.isnan(stop), LV[:, k - 1], stop)
            tg = np.where(sig & np.isnan(tg), LV[:, k + 1], tg)
        for k, name in ((5, "R2"), (4, "R1"), (3, "P")):      # resistances: rejection short
            lvk = LV[:, k]
            sig = touched[:, k] & (pc < lvk) & (h >= lvk) & (c < lvk) & ~L
            S |= sig
            stop = np.where(sig & ~L & np.isnan(stop), LV[:, k + 1], stop)
            tg = np.where(sig & ~L & np.isnan(tg), LV[:, k - 1], tg)
        add("S4 pivot bounce, next pivot target", L, S, stop, tg, F, F, 5 if tf == "1m" else 3)
    # S5 RMI + Supertrend (1m and 5m)
    if tf in ("1m", "5m"):
        rm = rmi(c)
        tr, upb, lob = IND.supertrend_bands(df, 10, 3.0)
        tr = np.asarray(tr)
        stl = np.where(tr > 0, np.asarray(lob), np.asarray(upb))
        bull, bear = (tr > 0) & (rm > 30), (tr < 0) & (rm < 70)
        L, S = bull & ~prev(bull), bear & ~prev(bear)
        rev_l = (rm < 70) & (pd.Series(rm).rolling(10).max().shift(1).values >= 70)
        rev_s = (rm > 30) & (pd.Series(rm).rolling(10).min().shift(1).values <= 30)
        add("S5 RMI + Supertrend", L, S, np.where(L | S, stl, np.nan), nanv, rev_l | (tr < 0), rev_s | (tr > 0), 60)
    # S6 linear-regression channel (2m and 5m)
    if tf in ("2m", "5m"):
        mid, slope, sd = linreg(c, 50)
        lo, up = mid - 2 * sd, mid + 2 * sd
        bbw = 4 * pd.Series(c).rolling(20).std().values
        wide = bbw > 1.5 * sma(bbw, 50)
        L = (l <= lo) & (c > lo) & (slope > 0)
        S = (h >= up) & (c < up) & (slope < 0)
        stop = np.where(L, lo - 0.5 * sd, np.where(S, up + 0.5 * sd, np.nan))
        exl, exs = (slope < 0) | wide, (slope > 0) | wide
        add("S6 regression channel, target opposite band", L, S, stop, np.where(L, up, np.where(S, lo, np.nan)), exl, exs, 60)
        add("S6 regression channel, target middle", L, S, stop, np.where(L | S, mid, np.nan), exl, exs, 60)
    # S7a EMA 9/21 on 5m
    if tf == "5m":
        e9, e21 = ema(c, 9), ema(c, 21)
        A20 = atr(h, l, c, 20)
        vr = ((v > prev(v)) & (v > vavg)) if has_vol else ~F
        L, S = cross_up(e9, e21) & vr, cross_up(e21, e9) & vr
        flat = np.abs(e9 - e21) < 0.1 * A20
        add("S7a EMA 9/21 cross, 1.5 ATR target", L, S, np.where(L, c - A20, np.where(S, c + A20, np.nan)),
            np.where(L, c + 1.5 * A20, np.where(S, c - 1.5 * A20, np.nan)), flat, flat, 24)
    # S7b EMA / RSI filter on 1m (index) or 5m
    if (market == "index" and tf == "1m") or (market != "index" and tf == "5m"):
        e9, e21 = ema(c, 9), ema(c, 21)
        r = rsi(c)
        L = cross_up(e9, e21) & (c > e9) & (r > 50)
        S = cross_up(e21, e9) & (c < e9) & (r < 50)
        lo5 = pd.Series(l).rolling(5).min().values
        hi5 = pd.Series(h).rolling(5).max().values
        hh = pd.Series(h).rolling(30).max().shift(1).values
        ll = pd.Series(l).rolling(30).min().shift(1).values
        comp = np.abs(e9 - e21) < 0.05 * A
        add("S7b EMA 9/21 + RSI 50 filter", L, S, np.where(L, lo5, np.where(S, hi5, np.nan)),
            np.where(L & (hh > c), hh, np.where(S & (ll < c), ll, np.nan)),
            cross_up(e21, e9) | (r < 50) | comp, cross_up(e9, e21) | (r > 50) | comp, 30)
    return out


def main():
    t0 = time.time()
    res = {}
    plan = [("index", s, tf) for s in INDEX for tf in ("1m", "2m", "5m")] + [("stock", s, "5m") for s in STOCKS] + \
           [("crypto", s, "5m") for s in COINS] + [("us", s, "5m") for s in US]
    bench_cache = {}
    for market, sym, tf in plan:
        try:
            df = load(market, sym, tf)
        except FileNotFoundError:
            print("missing", market, sym); continue
        bench = None
        bsym = {"stock": ("index", "NIFTY"), "crypto": ("crypto", "BTCUSDT"), "us": ("us", "SPY")}.get(market)
        if bsym and sym != bsym[1]:
            if bsym not in bench_cache:
                b = load(bsym[0], bsym[1], "5m" if bsym[0] == "index" else tf)
                bench_cache[bsym] = b["close"]
            bench = bench_cache[bsym].reindex(df.index).ffill().values
        o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
        day, dayend, _, _, _ = session_info(market, df.index)
        years = df.index.year.values
        ndays = len(np.unique(day))
        for name, side, stop, tgt, exl, exs, cap, allow in strategies(market, tf, df, bench):
            en, sd, R, bars = sim(o, h, l, c, side, stop, tgt, exl, exs, cap, dayend, allow)
            key = json.dumps({"market": market, "tf": tf, "strategy": name})
            d = res.setdefault(key, {"R": [], "yr": [], "bars": [], "days": 0, "sym": []})
            d["R"] += R.tolist(); d["yr"] += years[en].tolist(); d["bars"] += bars.tolist(); d["days"] += ndays; d["sym"] += [sym] * len(R)
        print(market, sym, tf, round(time.time() - t0), "s", flush=True)
    rows = []
    for key, d in res.items():
        v = json.loads(key)
        R, yr, sy = np.array(d["R"]), np.array(d["yr"]), np.array(d["sym"])
        if not len(R):
            continue
        row = {"v": v, "n": int(len(R)), "per_day": round(len(R) / max(d["days"], 1), 2), "gross_pct": round(float(R.mean() * 100), 4),
               "gross_win": round(float((R > 0).mean() * 100), 1), "avg_bars": round(float(np.mean(d["bars"])), 1), "net": {}}
        for cn, cf in COSTS[v["market"]].items():
            N = R - cf
            row["net"][cn] = {"avg_pct": round(float(N.mean() * 100), 4), "win": round(float((N > 0).mean() * 100), 1),
                              "total_pct": round(float(N.sum() * 100), 1),
                              "years": {str(y): round(float(N[yr == y].mean() * 100), 4) for y in np.unique(yr)},
                              "symbols_positive": int(sum(1 for s in np.unique(sy) if N[sy == s].mean() > 0)), "symbols": int(len(np.unique(sy)))}
        rows.append(row)
    json.dump(rows, open(OUT, "w"), indent=0)
    print("rows", len(rows), round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
