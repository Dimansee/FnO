"""Round 32 engine: ONE-DIRECTION day trading with trend lines, candle open/close logic, pivot points and trailing stops.

Each day gets one allowed direction (the "bias"), set at 09:30 and kept all day:
  free      - no bias, both directions (control)
  first     - the first signal's direction locks the day
  orb15     - colour of the first 15-minute candle (close vs open, 09:15-09:30)
  pivot     - 09:30 price above / below the classic daily pivot P
  cpr       - 09:30 price above the CPR top -> calls only, below the bottom -> puts only, inside -> no trade
  orb_pivot - first-candle colour and pivot side agree, else no trade
Entry triggers on the signal timeframe (5 or 15 minutes), in the bias direction only:
  tl        - trend-line break: the line through the last two falling swing highs (calls) / rising swing lows (puts),
              a candle closes through it. Stop: the pullback extreme since the second swing.
  cbreak    - candle logic: a green candle closes above the previous candle's high (red below the low for puts)
  cpull     - candle logic after a pullback: previous candle red, this one green and closes above its high
  pivbrk    - a candle closes through a classic pivot level (S2, S1, P, R1, R2) in the bias direction
  tl_candle - trend-line break on a green (red) candle that also closes beyond the previous candle
  Initial stop for the candle / pivot triggers: the low (high) of the last two candles. Every stop is kept between 0.5 and
  3 ATR (5-minute ATR).
Trailing stop (moves only in the trade's favour, updated at each signal-candle close, checked on 5-minute bars):
  prev   - low (high) of the candle that just closed
  swing  - the latest confirmed swing low (high)
  atr2   - highest (lowest) close since entry minus (plus) 2 ATR
  pivtgt - prev-candle trail plus exit at the next pivot level beyond the entry
Up to 3 trades a day (all in the day's direction), entries 09:30-14:30, square-off 15:15. The first trade of each day is the
"1 trade a day" version. Option, prices and costs exactly as round 30 / the app (1 strike ITM, calibrated Black-Scholes with the
real-price decay adjustment, 0.5% slippage a fill, 2026 charges); P&L also scaled to Rs 2,000 risk a trade ("u")."""
import os, sys
from datetime import timedelta, time
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import round26_harness as H
import round30_engine as E30
from round29_levels import daily, levels
from fno import indicators as I, config as C, backtest as B

BIASES = ["free", "first", "orb15", "pivot", "cpr", "orb_pivot"]
TRIGGERS = ["tl", "cbreak", "cpull", "pivbrk", "tl_candle"]
TRAILS = ["prev", "swing", "atr2", "pivtgt"]
FRACTAL = 2
T_FIRST, T_LAST = time(9, 30), time(14, 30)


def prep(sym, tf):
    """Per-day arrays: 5-minute bars (for stops and pricing) and signal bars (5 or 15 minutes) mapped to the 5-minute bar on
    which each one closes; previous-day pivot levels."""
    c5 = H.load(sym); d5 = I.add_indicators(c5)
    D = daily(sym); lv = {}
    for i in range(1, len(D)):
        L = levels(D.iloc[i - 1]); lv[D.index[i].date()] = (L["classic"], L["cpr"])
    days = []
    for d, day in d5.groupby(d5.index.date):
        if len(day) < 60 or d in C.BUDGET_DAYS or d not in lv:
            continue
        if tf == 5:
            s = day[["open", "high", "low", "close"]]
            j5 = np.arange(len(day))
        else:
            s = day[["open", "high", "low", "close"]].resample(f"{tf}min", label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
            ends = [t + pd.Timedelta(minutes=tf - 5) for t in s.index]
            pos = {t: k for k, t in enumerate(day.index)}
            keep = [k for k, t in enumerate(ends) if t in pos]
            s = s.iloc[keep]; j5 = np.array([pos[ends[k]] for k in keep])
        cl, cp = lv[d]
        t_end = np.array([(t + timedelta(minutes=5)).time() for t in day.index])
        days.append(dict(d=d, day=day, o5=day.open.values, h5=day.high.values, l5=day.low.values, c5=day.close.values,
                         atr=day.atr.values, tend=t_end, O=s.open.values, Hh=s.high.values, L=s.low.values, Cc=s.close.values,
                         j5=j5, P=cl["P"], piv=sorted([cl["S2"], cl["S1"], cl["P"], cl["R1"], cl["R2"]]),
                         tc=cp["TC"], bc=cp["BC"]))
    return days


def swings(Hh, L, n=FRACTAL):
    """Fractal swing highs / lows; a swing at bar k is known only at bar k + n."""
    sh, sl = [], []
    for k in range(n, len(Hh) - n):
        if Hh[k] == max(Hh[k - n:k + n + 1]): sh.append(k)
        if L[k] == min(L[k - n:k + n + 1]): sl.append(k)
    return sh, sl


def signals(x, trig):
    """For each signal bar i: (+1 call / -1 put / 0, stop level)."""
    O, Hh, L, Cc = x["O"], x["Hh"], x["L"], x["Cc"]; n = len(Cc)
    sig = np.zeros(n, int); stop = np.full(n, np.nan)
    sh, sl = swings(Hh, L)
    def tl_at(i, side):
        pts = [k for k in (sh if side > 0 else sl) if k + FRACTAL <= i]
        if len(pts) < 2: return None
        k1, k2 = pts[-2], pts[-1]
        y = Hh if side > 0 else L
        if side > 0 and not y[k2] < y[k1]: return None        # falling resistance for calls
        if side < 0 and not y[k2] > y[k1]: return None        # rising support for puts
        m = (y[k2] - y[k1]) / (k2 - k1)
        return k2, (lambda t: y[k2] + m * (t - k2))
    for i in range(1, n):
        green, red = Cc[i] > O[i], Cc[i] < O[i]
        up_c = green and Cc[i] > Hh[i - 1]; dn_c = red and Cc[i] < L[i - 1]
        s = 0; st = np.nan
        if trig in ("tl", "tl_candle"):
            for side in (1, -1):
                r = tl_at(i, side)
                if r is None: continue
                k2, f = r
                if i - 1 <= k2: continue
                if side * (Cc[i] - f(i)) > 0 and side * (Cc[i - 1] - f(i - 1)) <= 0:
                    if trig == "tl_candle" and not (up_c if side > 0 else dn_c): continue
                    s = side; st = L[k2:i + 1].min() if side > 0 else Hh[k2:i + 1].max(); break
        elif trig == "cbreak":
            if up_c: s, st = 1, min(L[i - 1], L[i])
            elif dn_c: s, st = -1, max(Hh[i - 1], Hh[i])
        elif trig == "cpull":
            if up_c and Cc[i - 1] < O[i - 1]: s, st = 1, min(L[i - 1], L[i])
            elif dn_c and Cc[i - 1] > O[i - 1]: s, st = -1, max(Hh[i - 1], Hh[i])
        elif trig == "pivbrk":
            for p in x["piv"]:
                if Cc[i] > p >= Cc[i - 1]: s, st = 1, min(L[i - 1], L[i]); break
                if Cc[i] < p <= Cc[i - 1]: s, st = -1, max(Hh[i - 1], Hh[i]); break
        sig[i] = s; stop[i] = st
    return sig, stop, sh, sl


def bias_of(x, kind):
    """+1 calls only, -1 puts only, 0 no trade today; None = no bias (free / first)."""
    if kind in ("free", "first"): return None
    k = 2                                           # the 09:25 bar closes at 09:30
    orb = 1 if x["c5"][k] > x["o5"][0] else (-1 if x["c5"][k] < x["o5"][0] else 0)
    px = x["c5"][k]
    piv = 1 if px > x["P"] else -1
    if kind == "orb15": return orb
    if kind == "pivot": return piv
    if kind == "cpr":
        top, bot = max(x["tc"], x["bc"]), min(x["tc"], x["bc"])
        return 1 if px > top else (-1 if px < bot else 0)
    if kind == "orb_pivot": return orb if orb == piv else 0
    raise ValueError(kind)


def simulate(x, sig, stop, sh, sl, bias, trail, max_trades=3, lock_first=False, min_stop=0.5, trail_after=0.0):
    """Trades for one day: list of (i5_in, i5_out, x_spot, side, stop_spot)."""
    if bias == 0: return []
    j5 = x["j5"]; h5, l5, c5, o5, atr, tend = x["h5"], x["l5"], x["c5"], x["o5"], x["atr"], x["tend"]
    sig_at5 = {int(j): i for i, j in enumerate(j5)}
    out = []; lock = None; i = 1; n = len(sig); last5 = -1
    while i < n and len(out) < max_trades:
        jj = int(j5[i])
        if jj <= last5 or sig[i] == 0 or tend[jj] < T_FIRST or tend[jj] > T_LAST:
            i += 1; continue
        side = sig[i]
        want = bias if bias is not None else lock
        if want is not None and side != want:
            i += 1; continue
        c0 = c5[jj]; a = atr[jj] if atr[jj] > 0 else abs(c0) * 0.001
        dist = min(max(side * (c0 - stop[i]), min_stop * a), 3.0 * a)
        sl0 = c0 - side * dist; live = sl0; best = c0
        tgt = None
        if trail == "pivtgt":
            beyond = [p for p in x["piv"] if side * (p - c0) > 0.25 * a]
            if beyond: tgt = min(beyond) if side > 0 else max(beyond)
        j_exit = None; xs = None
        for j in range(jj + 1, len(c5)):
            adv, fav = (l5[j], h5[j]) if side > 0 else (h5[j], l5[j])
            if side * (adv - live) <= 0:
                j_exit, xs = j, (o5[j] if side * (o5[j] - live) < 0 else live); break
            if tgt is not None and side * (fav - tgt) >= 0:
                j_exit, xs = j, (o5[j] if side * (o5[j] - tgt) > 0 else tgt); break
            if tend[j] >= C.SQUARE_OFF:
                j_exit, xs = j, c5[j]; break
            k = sig_at5.get(j)
            if k is not None and trail_after and side * (c5[j] - c0) < trail_after * dist:
                k = None                          # wider version: trail only once the trade is trail_after R in profit
            if k is not None:                      # a signal candle just closed: move the trailing stop
                if trail in ("prev", "pivtgt"):
                    cand = x["L"][k] if side > 0 else x["Hh"][k]
                elif trail == "swing":
                    pts = [q for q in (sl if side > 0 else sh) if q + FRACTAL <= k and q >= i - FRACTAL]
                    cand = (x["L"][pts[-1]] if side > 0 else x["Hh"][pts[-1]]) if pts else live
                else:
                    best = max(best, c5[j]) if side > 0 else min(best, c5[j])
                    cand = best - side * 2 * atr[j]
                if side * (cand - live) > 0: live = cand
        if j_exit is None:
            j_exit, xs = len(c5) - 1, c5[-1]
        out.append((jj, j_exit, float(xs), side, float(sl0), float(c0), float(dist)))
        if lock is None and bias is None and lock_first: lock = side
        last5 = j_exit
        i = int(np.searchsorted(j5, j_exit, side="right"))
    return out

