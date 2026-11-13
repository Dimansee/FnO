"""Part B: strategy families never run on the India index-option engine (or only before today's engine): standalone test.
1 lot ATM option, 0.5% slippage a fill, charges, entries 09:20-14:45, flat at 15:15. Design 2023-24, unseen 2025-26.
usage: b_families.py <tf>   (5m | 15m)"""
import sys, json, time, itertools; sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from lib import *
TF = sys.argv[1] if len(sys.argv) > 1 else "5m"
STEP = 5 if TF == "5m" else 15
sh = lambda x, k=1: np.r_[np.full(k, np.nan), x[:-k]]


def roll(x, n, f):
    return getattr(pd.Series(x).rolling(n), f)().values


def session(day, x, f):
    return pd.Series(x).groupby(day).transform(f).values


def supertrend(h, l, c, n, m):
    a = atr(h, l, c, n); mid = (h + l) / 2; ub, lb = mid + m * a, mid - m * a
    d = np.ones(len(c)); fu, fl = ub.copy(), lb.copy()
    for i in range(1, len(c)):
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or c[i - 1] > fu[i - 1] or np.isnan(fu[i - 1])) else fu[i - 1]
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or c[i - 1] < fl[i - 1] or np.isnan(fl[i - 1])) else fl[i - 1]
        d[i] = 1 if c[i] > fu[i - 1] else (-1 if c[i] < fl[i - 1] else d[i - 1])
    return d


def psar(h, l, af0=0.02, afmax=0.2):
    n = len(h); d = np.ones(n); sar = l[0]; ep = h[0]; af = af0; up = True
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
            if l[i] < sar:
                up = False; sar = ep; ep = l[i]; af = af0
            elif h[i] > ep:
                ep = h[i]; af = min(af + af0, afmax)
        else:
            sar = max(sar, h[i - 1], h[i - 2] if i > 1 else h[i - 1])
            if h[i] > sar:
                up = True; sar = ep; ep = h[i]; af = af0
            elif l[i] < ep:
                ep = l[i]; af = min(af + af0, afmax)
        d[i] = 1 if up else -1
    return d


def value_areas(sym):
    """Previous session's time-based value area (70% of 1-minute closes around the busiest price) per day."""
    m = R34.m1(sym); out = {}
    for d, g in m.groupby(m.index.date):
        x = g["close"].values; w = max(x.mean() * 0.0002, 1e-9)
        b = np.round(x / w).astype(int); u, cnt = np.unique(b, return_counts=True)
        k = int(np.argmax(cnt)); lo = hi = k; tot = cnt[k]; need = 0.7 * cnt.sum()
        while tot < need:
            dn = cnt[lo - 1] if lo > 0 else -1; up = cnt[hi + 1] if hi + 1 < len(u) else -1
            if up >= dn:
                hi += 1; tot += cnt[hi]
            else:
                lo -= 1; tot += cnt[lo]
        out[pd.Timestamp(d).value] = (u[lo] * w, u[hi] * w, u[k] * w)
    return out


def families(sym):
    df, o, h, l, c, hm, day = bar_of(sym, TF)
    n = len(c); a14 = atr(h, l, c, 14); pc = sh(c)
    F = {}                                             # name -> (L, S, xL, xS, extra)
    new = np.r_[True, day[1:] != day[:-1]]
    # 1 Supertrend flip
    for nn, mm in ((10, 3), (10, 2), (7, 3)):
        d = supertrend(h, l, c, nn, mm); F[f"supertrend {nn}/{mm}"] = ((d > 0) & (sh(d) < 0), (d < 0) & (sh(d) > 0), d < 0, d > 0, {})
    # 2 Parabolic SAR flip
    for af in (0.02, 0.01):
        d = psar(h, l, af, af * 10); F[f"psar {af}"] = ((d > 0) & (sh(d) < 0), (d < 0) & (sh(d) > 0), d < 0, d > 0, {})
    # 3 Ichimoku: Tenkan/Kijun cross on the right side of the cloud
    ten = (roll(h, 9, "max") + roll(l, 9, "min")) / 2; kij = (roll(h, 26, "max") + roll(l, 26, "min")) / 2
    sa = sh((ten + kij) / 2, 26); sb = sh((roll(h, 52, "max") + roll(l, 52, "min")) / 2, 26)
    top, bot = np.fmax(sa, sb), np.fmin(sa, sb); dk = ten - kij
    F["ichimoku cross+cloud"] = ((dk > 0) & (sh(dk) <= 0) & (c > top), (dk < 0) & (sh(dk) >= 0) & (c < bot), dk < 0, dk > 0, {})
    F["ichimoku cloud break"] = ((c > top) & (pc <= sh(top)) & (dk > 0), (c < bot) & (pc >= sh(bot)) & (dk < 0), c < kij, c > kij, {})
    # 4 Keltner channel breakout
    e20 = ema(c, 20); a10 = atr(h, l, c, 10)
    for k in (1.5, 2.0):
        up, dn = e20 + k * a10, e20 - k * a10
        F[f"keltner {k}"] = ((c > up) & (pc <= sh(up)), (c < dn) & (pc >= sh(dn)), c < e20, c > e20, {})
    # 5 Stochastic 14,3,3
    lo14, hi14 = roll(l, 14, "min"), roll(h, 14, "max"); k_ = roll(100 * (c - lo14) / np.maximum(hi14 - lo14, 1e-9), 3, "mean"); d_ = roll(k_, 3, "mean")
    F["stoch momentum 80/20"] = ((k_ > 80) & (sh(k_) <= 80), (k_ < 20) & (sh(k_) >= 20), k_ < 50, k_ > 50, {})
    F["stoch reversal 20/80"] = ((k_ > d_) & (sh(k_) <= sh(d_)) & (k_ < 25), (k_ < d_) & (sh(k_) >= sh(d_)) & (k_ > 75), k_ > 80, k_ < 20, {})
    # 6 Heikin-Ashi run
    hc = (o + h + l + c) / 4; ho = np.empty(n); ho[0] = (o[0] + c[0]) / 2
    for i in range(1, n):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    hl, hh = np.minimum(l, np.minimum(ho, hc)), np.maximum(h, np.maximum(ho, hc))
    g = hc > ho; g3 = g & sh(g.astype(float)).astype(bool) & (sh(g.astype(float), 2) > 0); r3 = (~g) & (sh(g.astype(float)) == 0) & (sh(g.astype(float), 2) == 0)
    F["heikin 3 strong"] = (g3 & (np.abs(ho - hl) < 1e-9) & (c > e20) & ~(sh(g3.astype(float)) > 0), r3 & (np.abs(hh - ho) < 1e-9) & (c < e20) & ~(sh(r3.astype(float)) > 0), ~g, g, {})
    # 7 Linear regression trend (slope + R2)
    for win in (20,):
        x = np.arange(win); xm = x.mean(); sxx = ((x - xm) ** 2).sum()
        cs = pd.Series(c)
        slope = cs.rolling(win).apply(lambda y: ((x - xm) * (y - y.mean())).sum() / sxx, raw=True).values
        r2 = cs.rolling(win).apply(lambda y: np.corrcoef(x, y)[0, 1] ** 2, raw=True).values
        ok = r2 > 0.7
        F["linreg trend r2>0.7"] = (ok & ~(sh(ok.astype(float)) > 0) & (slope > 0), ok & ~(sh(ok.astype(float)) > 0) & (slope < 0), slope < 0, slope > 0, {})
    # 8 Session average price (index has no volume: time-weighted) with deviation bands
    tp_ = (h + l + c) / 3; cnt = pd.Series(np.ones(n)).groupby(day).cumsum().values
    vw = pd.Series(tp_).groupby(day).cumsum().values / cnt
    dev = c - vw; sd = np.sqrt(pd.Series(dev ** 2).groupby(day).cumsum().values / cnt)
    for k in (1.5, 2.0):
        up, dn = vw + k * sd, vw - k * sd; late = cnt * STEP >= 45
        F[f"avg-price band {k} trend"] = (late & (c > up) & (pc <= sh(up)), late & (c < dn) & (pc >= sh(dn)), c < vw, c > vw, {})
    up, dn = vw + 2.5 * sd, vw - 2.5 * sd; u2, d2 = vw + 2 * sd, vw - 2 * sd; late = cnt * STEP >= 60
    was_hi = roll((h > up).astype(float), 3, "max") > 0; was_lo = roll((l < dn).astype(float), 3, "max") > 0
    F["avg-price band 2.5 fade"] = (late & was_lo & (c > d2) & (pc <= sh(d2)), late & was_hi & (c < u2) & (pc >= sh(u2)), c >= vw, c <= vw, {})
    # 9 Initial balance (first hour) break and failed break
    first = hm < 10 * 60 + 15
    ibh = session(day, np.where(first, h, -np.inf), "max"); ibl = session(day, np.where(first, l, np.inf), "min"); after = ~first
    brk_u = after & (c > ibh) & (pc <= ibh); brk_d = after & (c < ibl) & (pc >= ibl)
    F["initial balance break"] = (brk_u, brk_d, c < (ibh + ibl) / 2, c > (ibh + ibl) / 2, {})
    above = roll((after & (h > ibh)).astype(float), 3, "max") > 0; below = roll((after & (l < ibl)).astype(float), 3, "max") > 0
    F["initial balance failed break"] = (after & below & (c > ibl) & (pc <= ibl), after & above & (c < ibh) & (pc >= ibh), None, None, {})
    # 10 Value area 80% rule (open outside yesterday's value area, back inside for two bars -> the other side of the area)
    VA = value_areas(sym); ud = np.unique(day); prev = {d: VA.get(ud[i - 1]) for i, d in enumerate(ud) if i > 0}
    val = np.array([prev.get(d, (np.nan,) * 3)[0] if prev.get(d) else np.nan for d in day]); vah = np.array([prev.get(d, (np.nan,) * 3)[1] if prev.get(d) else np.nan for d in day])
    dopen = session(day, o, "first"); inside = (c < vah) & (c > val); two = inside & (sh(inside.astype(float)) > 0) & ~(sh(inside.astype(float), 2) > 0)
    F["value area 80% rule"] = (two & (dopen < val), two & (dopen > vah), None, None, {"tpL": vah, "tpS": val})
    # 11 TTM squeeze release (Bollinger inside Keltner, then out, direction = momentum)
    m20 = roll(c, 20, "mean"); s20 = roll(c, 20, "std"); sq_on = (m20 + 2 * s20 < e20 + 1.5 * a10) & (m20 - 2 * s20 > e20 - 1.5 * a10)
    rel = (~sq_on) & (sh(sq_on.astype(float)) > 0); mom = c - (roll(h, 20, "max") + roll(l, 20, "min") + 2 * m20) / 4
    F["ttm squeeze release"] = (rel & (mom > 0), rel & (mom < 0), mom < 0, mom > 0, {})
    # 12 DMI cross with ADX
    upm, dnm = h - sh(h), sh(l) - l; pdm = np.where((upm > dnm) & (upm > 0), upm, 0.0); ndm = np.where((dnm > upm) & (dnm > 0), dnm, 0.0)
    tr = atr(h, l, c, 14); pdi = 100 * pd.Series(pdm).ewm(alpha=1 / 14, adjust=False).mean().values / tr; ndi = 100 * pd.Series(ndm).ewm(alpha=1 / 14, adjust=False).mean().values / tr
    adx = pd.Series(100 * np.abs(pdi - ndi) / np.maximum(pdi + ndi, 1e-9)).ewm(alpha=1 / 14, adjust=False).mean().values; dd = pdi - ndi
    F["dmi cross adx>20"] = ((dd > 0) & (sh(dd) <= 0) & (adx > 20), (dd < 0) & (sh(dd) >= 0) & (adx > 20), dd < 0, dd > 0, {})
    # 13 RSI(2) pullback in the trend (Connors, intraday)
    dlt = c - pc; up_ = pd.Series(np.where(dlt > 0, dlt, 0.0)).ewm(alpha=1 / 2, adjust=False).mean().values; dn_ = pd.Series(np.where(dlt < 0, -dlt, 0.0)).ewm(alpha=1 / 2, adjust=False).mean().values
    rsi2 = 100 - 100 / (1 + up_ / np.maximum(dn_, 1e-9)); e200 = ema(c, 200)
    F["rsi2 pullback in trend"] = ((c > e200) & (rsi2 < 10) & (sh(rsi2) >= 10), (c < e200) & (rsi2 > 90) & (sh(rsi2) <= 90), rsi2 > 70, rsi2 < 30, {})
    # 14 Kaufman efficiency ratio breakout
    chg = np.abs(c - sh(c, 20)); vol = roll(np.abs(c - pc), 20, "sum"); er = chg / np.maximum(vol, 1e-9)
    F["efficiency ratio>0.5 + 20-bar high"] = ((er > 0.5) & (c >= roll(h, 20, "max")) & ~(sh(er) > 0.5), (er > 0.5) & (c <= roll(l, 20, "min")) & ~(sh(er) > 0.5), er < 0.2, er < 0.2, {})
    # 15 Afternoon break of the morning range
    morn = hm < 13 * 60
    mh = session(day, np.where(morn, h, -np.inf), "max"); ml = session(day, np.where(morn, l, np.inf), "min"); pm = ~morn
    F["afternoon break of morning range"] = (pm & (c > mh) & (pc <= mh), pm & (c < ml) & (pc >= ml), None, None, {})
    return df, o, h, l, c, hm, day, a14, F


EXITS = {"signal exit, stop 1.5 ATR": dict(k=1.5, tp_R=0, sig=True), "2R target, stop 1.5 ATR": dict(k=1.5, tp_R=2.0, sig=False),
         "3R target, stop 2 ATR": dict(k=2.0, tp_R=3.0, sig=False), "trail 1.5R, stop 1 ATR": dict(k=1.0, tp_R=0, sig=False, trail_R=1.5)}


def main():
    res = []; t0 = time.time()
    for sym in SYMS:
        df, o, h, l, c, hm, day, a14, F = families(sym)
        print(sym, TF, len(c), "bars", len(F), "families", round(time.time() - t0), flush=True)
        for name, (L, S, xL, xS, ex) in F.items():
            L = np.nan_to_num(np.asarray(L, float)) > 0; S = np.nan_to_num(np.asarray(S, float)) > 0
            for en, e in EXITS.items():
                if "tpL" in ex and en != "2R target, stop 1.5 ATR":
                    continue
                for per_day in (1, 3):
                    rows = simulate(o, h, l, c, hm, day, L, S, e["k"] * a14, tp_R=e["tp_R"], xL=xL if e["sig"] else None, xS=xS if e["sig"] else None,
                                    per_day=per_day, step=STEP, trail_R=e.get("trail_R", 0))
                    T = price(sym, df, rows, STEP)
                    s = summ(T); s.update(sym=sym, tf=TF, family=name, exit=en, per_day=per_day)
                    res.append(s)
        json.dump(res, open(OUT + f"/b_{TF}.json", "w"), default=str)
    print("done", len(res), round(time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
