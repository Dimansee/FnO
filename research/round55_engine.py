"""Round 55 engine: fast (numba) bar-by-bar trade simulator + level signal generators for crypto.

Mirrors the app's exit order (fno/custom.py step_pos): gap-through-stop at the open, stop before target inside a bar,
then break-even, then the trailing stop moves at the bar end. Entry at the NEXT bar's open after the signal bar closes.
Costs: 16 bps round trip on notional (app crypto cost), expressed in R.
"""
import numpy as np
import pandas as pd
from numba import njit

COST = 0.0016
SPLIT = pd.Timestamp("2025-04-01", tz="UTC")


# ------------------------------------------------------------------ data
def load(data_dir, sym, tf):
    src = "5m" if tf in ("5m", "15m") else "1h"
    df = pd.read_csv(f"{data_dir}/crypto_{sym}_{src}.csv.gz", parse_dates=["ts"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    if tf not in (src,):
        rule = {"15m": "15min", "4h": "4h", "1d": "1D"}[tf]
        df = df.resample(rule, label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return df


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean().values


def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


# ------------------------------------------------------------------ simulator
@njit(cache=True)
def run(o, h, l, c, a, side_sig, stop_px, tgt_px, opp_l, opp_s, target_r, trail, be_r, part_r, tstop, min_rr, stop_atr=2.0):
    """side_sig[i] in {-1,0,1} decided on bar i close. stop_px[i]: absolute stop (nan -> 2 ATR).
    tgt_px[i]: absolute target from a level (nan -> use target_r; 0 target_r -> none).
    opp_l/opp_s: opposite-signal exit flags (exit at next open). Returns arrays (entry_i, side, R, bars, gross_R)."""
    n = len(o)
    ent = np.empty(n, np.int64)
    sd = np.empty(n, np.int64)
    rr = np.empty(n, np.float64)
    bars = np.empty(n, np.int64)
    gr = np.empty(n, np.float64)
    m = 0
    i = 1
    while i < n - 1:
        s = side_sig[i]
        if s == 0 or not np.isfinite(a[i]) or a[i] <= 0:
            i += 1
            continue
        j = i + 1
        e = o[j]
        sl = stop_px[i]
        if not np.isfinite(sl):
            sl = e - s * stop_atr * a[i]
        risk = s * (e - sl)
        if risk < 0.5 * a[i]:          # gap ate the stop room: use the minimum room
            risk = 0.5 * a[i]
            sl = e - s * risk
        tp = np.nan
        if np.isfinite(tgt_px[i]):
            tp = tgt_px[i]
            if s * (tp - e) < min_rr * risk:
                i += 1
                continue
        elif target_r > 0:
            tp = e + s * target_r * risk
        best = e
        sl0 = sl
        part_done = False
        part_R = 0.0
        exit_px = c[n - 1]
        k_exit = n - 1
        held = 0
        pend = False
        first = True
        k = j
        while k < n:
            held += 1
            if pend:
                exit_px = o[k]
                k_exit = k
                break
            adv = l[k] if s > 0 else h[k]
            fav = h[k] if s > 0 else l[k]
            if (not first) and s * (o[k] - sl) <= 0:
                exit_px = o[k]
                k_exit = k
                break
            if s * (adv - sl) <= 0:
                exit_px = sl
                k_exit = k
                break
            if part_r > 0 and not part_done and s * (fav - (e + s * part_r * risk)) >= 0:
                part_done = True
                part_R = part_r
                if s * (sl - e) < 0:
                    sl = e
            if np.isfinite(tp) and s * (fav - tp) >= 0:
                exit_px = tp
                k_exit = k
                break
            if be_r > 0 and s * (fav - (e + s * be_r * risk)) >= 0 and s * (sl - e) < 0:
                sl = e
            if trail > 0:
                if s > 0:
                    best = max(best, fav)
                else:
                    best = min(best, fav)
                cand = best - s * trail * a[i]
                if s * (cand - sl) > 0:
                    sl = cand
            first = False
            if tstop > 0 and held >= tstop:
                exit_px = c[k]
                k_exit = k
                break
            if (s > 0 and opp_s[k]) or (s < 0 and opp_l[k]):
                pend = True
            k += 1
        g = s * (exit_px - e) / risk
        if part_done:
            g = 0.5 * part_R + 0.5 * g
        ent[m] = j
        sd[m] = s
        gr[m] = g
        rr[m] = g - COST * e / risk
        bars[m] = k_exit - j + 1
        m += 1
        i = max(k_exit, i + 1)
    return ent[:m], sd[:m], rr[:m], bars[:m], gr[:m]


# ------------------------------------------------------------------ swing pivots + multi-touch levels
@njit(cache=True)
def pivots(h, l, k):
    n = len(h)
    ph = np.zeros(n, np.bool_)
    pl = np.zeros(n, np.bool_)
    for p in range(k, n - k):
        hh = True
        ll = True
        for q in range(p - k, p + k + 1):
            if q == p:
                continue
            if h[q] > h[p]:
                hh = False
            if l[q] < l[p]:
                ll = False
        ph[p] = hh
        pl[p] = ll
    return ph, pl


@njit(cache=True)
def swing_levels(o, h, l, c, a, k, lookback, tol, min_touch, mode, rej, buf, tgt_mode):
    """mode 0 = bounce (touch the level and close back on the same side), 1 = breakout (close through the level).
    Levels = prices of confirmed swing highs AND lows (role reversal counts) inside the lookback.
    Strength of a level = number of pivots within tol*ATR of it (the 'tested again and again' count).
    tgt_mode 1 = target the next strong level in the trade direction."""
    n = len(o)
    ph, pl = pivots(h, l, k)
    side = np.zeros(n, np.int64)
    stop = np.full(n, np.nan)
    tgt = np.full(n, np.nan)
    pp = np.empty(n * 2)
    for i in range(k + 2, n):
        # gather confirmed pivots (p + k <= i) within lookback
        cnt = 0
        lo = max(0, i - lookback)
        for p in range(lo, i - k + 1):
            if ph[p]:
                pp[cnt] = h[p]
                cnt += 1
            if pl[p]:
                pp[cnt] = l[p]
                cnt += 1
        if cnt < min_touch:
            continue
        t = tol * a[i]
        bestL = -1.0
        bestS = -1.0
        bestLs = 0
        bestSs = 0
        for u in range(cnt):
            x = pp[u]
            if x < l[i] - a[i] or x > h[i] + a[i]:      # only levels this bar could have touched or crossed
                continue
            st = 0
            for v in range(cnt):
                if abs(pp[v] - x) <= t:
                    st += 1
            if st < min_touch:
                continue
            if mode == 0:
                # support bounce: was above, touched, closed back above
                if c[i - 1] > x + t and l[i] <= x + 0.25 * a[i] and c[i] > x:
                    if (not rej) or (c[i] - l[i] >= 0.5 * (h[i] - l[i]) and c[i] > o[i]):
                        if bestL < 0 or x > bestL:
                            bestL = x
                            bestLs = st
                if c[i - 1] < x - t and h[i] >= x - 0.25 * a[i] and c[i] < x:
                    if (not rej) or (h[i] - c[i] >= 0.5 * (h[i] - l[i]) and c[i] < o[i]):
                        if bestS < 0 or x < bestS:
                            bestS = x
                            bestSs = st
            else:
                if c[i - 1] <= x and c[i] > x + 0.1 * a[i]:
                    if bestL < 0 or x > bestL:
                        bestL = x
                if c[i - 1] >= x and c[i] < x - 0.1 * a[i]:
                    if bestS < 0 or x < bestS:
                        bestS = x
        s = 0
        x = 0.0
        if bestL > 0 and bestS < 0:
            s = 1
            x = bestL
        elif bestS > 0 and bestL < 0:
            s = -1
            x = bestS
        if s == 0:
            continue
        side[i] = s
        if mode == 0:
            stop[i] = (min(l[i], x - t) - buf * a[i]) if s > 0 else (max(h[i], x + t) + buf * a[i])
        else:
            stop[i] = (x - buf * a[i]) if s > 0 else (x + buf * a[i])
        if tgt_mode == 1:
            nxt = np.nan
            for u in range(cnt):
                y = pp[u]
                if s * (y - c[i]) <= 0.5 * a[i]:
                    continue
                st = 0
                for v in range(cnt):
                    if abs(pp[v] - y) <= t:
                        st += 1
                if st < min_touch:
                    continue
                if not np.isfinite(nxt) or s * (y - nxt) < 0:
                    nxt = y
            if np.isfinite(nxt):
                tgt[i] = nxt - s * 0.1 * a[i]       # exit just in front of the next level
            else:
                tgt[i] = c[i] + s * 3.0 * abs(c[i] - stop[i])
    return side, stop, tgt


@njit(cache=True)
def fixed_levels(o, h, l, c, a, lv, sup_mask, mode, rej, buf):
    """lv: (n, m) level prices valid on each bar (nan = none). sup_mask[j]: 1 level may act as support (long bounce /
    short break), -1 as resistance, 0 both. mode 0 bounce, 1 breakout."""
    n, mcols = lv.shape
    side = np.zeros(n, np.int64)
    stop = np.full(n, np.nan)
    tgt = np.full(n, np.nan)
    for i in range(1, n):
        bestL = np.nan
        bestS = np.nan
        for j in range(mcols):
            x = lv[i, j]
            if not np.isfinite(x) or lv[i - 1, j] != x:     # level must already exist on the previous bar
                continue
            if mode == 0:
                if sup_mask[j] >= 0 and c[i - 1] > x and l[i] <= x + 0.1 * a[i] and c[i] > x:
                    if (not rej) or (c[i] > o[i] and c[i] - l[i] >= 0.5 * (h[i] - l[i])):
                        if not np.isfinite(bestL) or x > bestL:
                            bestL = x
                if sup_mask[j] <= 0 and c[i - 1] < x and h[i] >= x - 0.1 * a[i] and c[i] < x:
                    if (not rej) or (c[i] < o[i] and h[i] - c[i] >= 0.5 * (h[i] - l[i])):
                        if not np.isfinite(bestS) or x < bestS:
                            bestS = x
            else:
                if sup_mask[j] <= 0 and c[i - 1] <= x and c[i] > x + 0.1 * a[i]:
                    if not np.isfinite(bestL) or x > bestL:
                        bestL = x
                if sup_mask[j] >= 0 and c[i - 1] >= x and c[i] < x - 0.1 * a[i]:
                    if not np.isfinite(bestS) or x < bestS:
                        bestS = x
        if np.isfinite(bestL) and not np.isfinite(bestS):
            side[i] = 1
            stop[i] = (min(l[i], bestL) - buf * a[i]) if mode == 0 else (bestL - buf * a[i])
        elif np.isfinite(bestS) and not np.isfinite(bestL):
            side[i] = -1
            stop[i] = (max(h[i], bestS) + buf * a[i]) if mode == 0 else (bestS + buf * a[i])
    return side, stop, tgt


def period_levels(df, period, kind):
    """Previous UTC day / ISO week H, L, C -> floor pivots or Camarilla, aligned to every bar of the current period."""
    day = df.index.floor("D")
    key = day if period == "D" else day - pd.to_timedelta(day.weekday, unit="D")      # Monday 00:00 UTC
    agg = df.groupby(key).agg({"high": "max", "low": "min", "close": "last"})
    prev = agg.shift(1)
    H, L, C = prev["high"], prev["low"], prev["close"]
    if kind == "floor":
        P = (H + L + C) / 3
        cols = {"S2": P - (H - L), "S1": 2 * P - H, "P": P, "R1": 2 * P - L, "R2": P + (H - L)}
        mask = np.array([0, 0, 0, 0, 0])
    else:  # camarilla
        rg = (H - L) * 1.1
        cols = {"L4": C - rg / 2, "L3": C - rg / 4, "H3": C + rg / 4, "H4": C + rg / 2}
        mask = np.array([0, 0, 0, 0])
    lv = pd.DataFrame(cols)
    out = lv.reindex(key).values
    return out, mask


def round_levels(c):
    """The two round numbers around the previous close (step = 10^(digits-1): BTC 80,000 -> 1,000; ETH 3,000 -> 100)."""
    prev = np.r_[c[0], c[:-1]]
    step = 10 ** (np.floor(np.log10(prev)) - 1)
    f = np.floor(prev / step) * step
    # keep a level fixed while price stays inside the same step band
    return np.c_[f, f + step], np.array([0, 0])


# ------------------------------------------------------------------ trend rules (live crypto book)
@njit(cache=True)
def supertrend_dir(h, l, c, a, mult):
    n = len(c)
    d = np.ones(n, np.int64)
    up = np.zeros(n)
    dn = np.zeros(n)
    for i in range(n):
        m = (h[i] + l[i]) / 2
        ub = m + mult * a[i]
        lb = m - mult * a[i]
        if i == 0:
            up[i], dn[i] = ub, lb
            continue
        up[i] = ub if (ub < up[i - 1] or c[i - 1] > up[i - 1]) else up[i - 1]
        dn[i] = lb if (lb > dn[i - 1] or c[i - 1] < dn[i - 1]) else dn[i - 1]
        if d[i - 1] == 1:
            d[i] = -1 if c[i] < dn[i] else 1
        else:
            d[i] = 1 if c[i] > up[i] else -1
    return d


def stats(R, ts=None):
    R = np.asarray(R, float)
    if len(R) == 0:
        return {"n": 0}
    lo = -R[R <= 0].sum()
    out = {"n": int(len(R)), "avg": round(float(R.mean()), 3), "win": round(float((R > 0).mean() * 100), 1),
           "pf": round(float(R[R > 0].sum() / lo), 2) if lo > 0 else None, "tot": round(float(R.sum()), 1)}
    if len(R) > 2 and R.std() > 0:
        out["t"] = round(float(R.mean() / (R.std(ddof=1) / np.sqrt(len(R)))), 2)
    return out
