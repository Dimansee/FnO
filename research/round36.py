"""Round 36 (Oct 2026): breakout scalping with every rule the user asked for, tested together and one by one.

User spec (22 Oct 2026):
  1. scalp only on a breakout
  2. enter / exit on EMA 9 and EMA 15 together with the candle's open and close
  3. measure whether the candle is strong, and use it for entry / exit
  4. mark pivots, major points, trend lines and the points where the market reversed - these are the key levels
  5. look at OI, if it helps
  6. trade only with the market's direction; if the market is not one-way, take no directional trade

Rules as coded (every value is known at the close of the signal candle; entry acts on the next minute like the app)
  CORE (always on)  long: EMA 9 > EMA 15, the candle closes above EMA 9 and is green (close > open). Short is the mirror.
  ENTRY TYPE        range   close breaks the high of the last 15 one-minute candles (6 five-minute candles) - a consolidation breakout
                    level   close crosses a key level this candle: previous-day high / low, floor pivots P R1 S1 R2 S2, the
                            09:15-09:30 opening range, 5-minute swing highs / lows of today and yesterday (where the market
                            reversed) and the trend line through the last two swing highs (falling) or swing lows (rising)
                    any     range or level
                    bounce  reversal at a key level: the candle tags a level from the right side and closes back away from it
  STRONG CANDLE     body >= 60% of the range, close in the outer 25% of the range, range >= the average of the last 10 candles
  DIRECTION         none | 5m: 5-minute EMA 9 > 15 and price above the 5-minute EMA 9
                    | 5m+one-way: plus the last 30 minutes moved efficiently (efficiency ratio >= 0.30, same direction)
                    | 5m+one-way+day: plus price on the trade's side of the day open, previous close and pivot P, and the day so
                      far is one-sided (|price - day open| >= 50% of the day's range)
  EXIT              ema9  candle closes back through EMA 9    | ema15  candle closes back through EMA 15
                    1.5R  target at 1.5 x the stop
  STOP              candle  beyond the signal candle's far end (min 0.3 ATR, max 2 ATR)   | atr  1 ATR
  TIME CAP          none | every trade out within 5 minutes ("HFT" as the user defined it)
  Charts: 1-minute and 5-minute. One position per index at a time. Entries 09:20-15:00, square-off 15:15.
  OI (tagged on every trade, then tested as a filter): previous-day put/call OI ratio near the money and the distance to the
  biggest call-OI (resistance) / put-OI (support) strike, from the NSE option bhavcopy (Nifty / Bank Nifty, Oct 2023 onward);
  on the recorded sessions also the intraday 15-minute change in put OI minus call OI around ATM.

Part A: 2023-01 to 2026-10-16, 1-minute Nifty / Bank Nifty / Sensex, the ATM option priced like the app (round 34 harness).
         Settings are picked on 2023-24 only and judged on 2025-26.
Part B: the 15 recorded sessions (28 Sep - 21 Oct 2026), Nifty and Bank Nifty, REAL 1-minute option prices, entry and exit at the
         next minute's open plus half the REAL bid-ask spread, REAL intraday OI.
Run: python round36.py  ->  round36_results.json
"""
import os, sys, json, gzip, itertools, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
from datetime import datetime, time
import numpy as np, pandas as pd
from numba import njit
import round34 as R34
from fno import indicators as I, market as D

DATA = R34.DATA; LIVE = os.path.join(DATA, "live"); SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
WORK = os.environ.get("R36_WORK", "/tmp"); RES = {}
NLEV = 11   # PDH PDL P R1 S1 R2 S2 ORH ORL OI-call-wall OI-put-wall


def ema(x, n): return pd.Series(x).ewm(span=n, adjust=False).mean().values


def atr(h, l, c, n=14):
    pc = np.r_[c[0], c[:-1]]; tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values


# ------------------------------------------------------------------ OI levels from the daily option bhavcopy
def oi_daily(sym, a=None):
    """Per trading day: put/call OI ratio within 10 strikes of the close, biggest call-OI strike above and put-OI strike below
    (nearest expiry). Used for the NEXT day."""
    if sym not in ("NIFTY", "BANKNIFTY"): return pd.DataFrame()
    b = pd.read_csv(f"{DATA}/OPT_bhavcopy.csv.gz"); b = b[b.sym == sym]
    parts = [b[["date", "expiry", "strike", "opt", "oi", "close"]]]
    for d in sorted(os.listdir(LIVE)):
        p = f"{LIVE}/{d}/bhav_fo.csv.gz"
        if d[:2] != "20" or not os.path.exists(p): continue
        x = pd.read_csv(p); x = x[(x.TckrSymb == sym) & x.OptnTp.isin(["CE", "PE"])]
        if len(x) == 0: continue
        parts.append(pd.DataFrame(dict(date=x.TradDt, expiry=x.XpryDt, strike=x.StrkPric, opt=x.OptnTp, oi=x.OpnIntrst, close=x.ClsPric)))
    b = pd.concat(parts); b = b[b.date.astype(str) <= "2099"]; b["date"] = b.date.astype(str); b["expiry"] = b.expiry.astype(str)
    step = D.instrument(sym)["step"]
    spot = (R34.m1(sym) if a is None else a).close.resample("1D").last().dropna(); spot.index = spot.index.strftime("%Y-%m-%d")
    out = []
    for d, g in b.groupby("date"):
        if d not in spot.index: continue
        g = g[g.expiry >= d]; g = g[g.expiry == g.expiry.min()]; s = spot[d]
        near = g[(g.strike - s).abs() <= 10 * step]
        ce, pe = near[near.opt == "CE"].oi.sum(), near[near.opt == "PE"].oi.sum()
        cw = g[(g.opt == "CE") & (g.strike > s)].sort_values("oi").tail(1).strike
        pw = g[(g.opt == "PE") & (g.strike < s)].sort_values("oi").tail(1).strike
        out.append(dict(date=d, pcr=pe / ce if ce else np.nan, call_wall=float(cw.iloc[0]) if len(cw) else np.nan,
                        put_wall=float(pw.iloc[0]) if len(pw) else np.nan))
    return pd.DataFrame(out).set_index("date")


# ------------------------------------------------------------------ levels and signals on one chart
@njit(cache=True)
def level_kernel(o, h, l, c, day, L, swh, swht, swl, swlt, tcur, at, tol):
    """Per bar: crossed any level up / down by the close, bounced off a level, room to the next level above / below.
    Levels = static per-day L[:, k] plus 5-minute swing highs / lows (today + yesterday) and the trend lines through the last
    two swing highs (falling) / swing lows (rising)."""
    n = len(c); brk_up = np.zeros(n, np.bool_); brk_dn = np.zeros(n, np.bool_)
    bnc_up = np.zeros(n, np.bool_); bnc_dn = np.zeros(n, np.bool_)
    room_up = np.full(n, np.inf); room_dn = np.full(n, np.inf)
    K = 8; sh = np.full(K, np.nan); sht = np.full(K, np.nan); shd = np.full(K, -1); nsh = 0
    sl_ = np.full(K, np.nan); slt = np.full(K, np.nan); sld = np.full(K, -1); nsl = 0
    lev = np.empty(L.shape[1] + 2 * K + 2)
    for i in range(n):
        if not np.isnan(swh[i]):
            for q in range(K - 1): sh[q] = sh[q + 1]; sht[q] = sht[q + 1]; shd[q] = shd[q + 1]
            sh[K - 1] = swh[i]; sht[K - 1] = swht[i]; shd[K - 1] = day[i]; nsh = min(nsh + 1, K)
        if not np.isnan(swl[i]):
            for q in range(K - 1): sl_[q] = sl_[q + 1]; slt[q] = slt[q + 1]; sld[q] = sld[q + 1]
            sl_[K - 1] = swl[i]; slt[K - 1] = swlt[i]; sld[K - 1] = day[i]; nsl = min(nsl + 1, K)
        m = 0
        for k in range(L.shape[1]):
            if not np.isnan(L[i, k]): lev[m] = L[i, k]; m += 1
        dmin = day[i] - 4   # day codes are calendar days; keep swings from the last trading day or two
        for q in range(K):
            if shd[q] >= dmin and not np.isnan(sh[q]): lev[m] = sh[q]; m += 1
            if sld[q] >= dmin and not np.isnan(sl_[q]): lev[m] = sl_[q]; m += 1
        # trend lines
        if nsh >= 2 and shd[K - 2] >= dmin and sh[K - 1] < sh[K - 2] and sht[K - 1] > sht[K - 2]:
            lev[m] = sh[K - 1] + (sh[K - 1] - sh[K - 2]) / (sht[K - 1] - sht[K - 2]) * (tcur[i] - sht[K - 1]); m += 1
        if nsl >= 2 and sld[K - 2] >= dmin and sl_[K - 1] > sl_[K - 2] and slt[K - 1] > slt[K - 2]:
            lev[m] = sl_[K - 1] + (sl_[K - 1] - sl_[K - 2]) / (slt[K - 1] - slt[K - 2]) * (tcur[i] - slt[K - 1]); m += 1
        same = i > 0 and day[i - 1] == day[i]
        tl = tol * at[i]
        for q in range(m):
            v = lev[q]
            if same and c[i - 1] <= v < c[i]: brk_up[i] = True
            if same and c[i - 1] >= v > c[i]: brk_dn[i] = True
            if l[i] <= v + tl and c[i] > v + tl and o[i] > v: bnc_up[i] = True
            if h[i] >= v - tl and c[i] < v - tl and o[i] < v: bnc_dn[i] = True
            if v > c[i] and v - c[i] < room_up[i]: room_up[i] = v - c[i]
            if v < c[i] and c[i] - v < room_dn[i]: room_dn[i] = c[i] - v
    return brk_up, brk_dn, bnc_up, bnc_dn, room_up, room_dn


def build(a, sym, oid):
    """a = 1-minute OHLC (index = ts). Returns per-chart feature dicts for the 1m and 5m charts."""
    idx = a.index; o, h, l, c = (a[k].values for k in ("open", "high", "low", "close"))
    dkey = idx.normalize()
    # daily levels from the previous session
    dd = a.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    pv = pd.DataFrame(index=dd.index)
    pv["pdh"], pv["pdl"], pv["pdc"] = dd.high.shift(1), dd.low.shift(1), dd.close.shift(1)
    P = (pv.pdh + pv.pdl + pv.pdc) / 3; pv["P"] = P
    pv["R1"], pv["S1"] = 2 * P - pv.pdl, 2 * P - pv.pdh
    pv["R2"], pv["S2"] = P + (pv.pdh - pv.pdl), P - (pv.pdh - pv.pdl)
    if len(oid):
        od = oid.copy(); od.index = pd.to_datetime(od.index); od = od.reindex(dd.index).shift(1)   # previous day's OI
        pv["cw"], pv["pw"], pv["pcr"] = od.call_wall, od.put_wall, od.pcr
    else:
        pv["cw"] = pv["pw"] = pv["pcr"] = np.nan
    PV = pv.reindex(dkey)
    mins = idx.hour.values * 60 + idx.minute.values
    day = dkey.values.astype("datetime64[D]").astype(np.int64)
    # opening range 09:15-09:29, valid from 09:30
    orr = a[(mins >= 555) & (mins <= 569)].groupby(dkey[(mins >= 555) & (mins <= 569)]).agg({"high": "max", "low": "min"})
    orh = orr.high.reindex(dkey).values; orl = orr.low.reindex(dkey).values
    orh = np.where(mins >= 569, orh, np.nan); orl = np.where(mins >= 569, orl, np.nan)
    Lm = np.column_stack([PV.pdh.values, PV.pdl.values, PV.P.values, PV.R1.values, PV.S1.values, PV.R2.values, PV.S2.values,
                          orh, orl, PV.cw.values, PV.pw.values]).astype(np.float64)
    dopen = pd.Series(o, index=idx).groupby(dkey).transform("first").values
    dhi = pd.Series(h, index=idx).groupby(dkey).cummax().values; dlo = pd.Series(l, index=idx).groupby(dkey).cummin().values
    # 5-minute bars
    b5 = a.resample("5min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    b5 = b5[(b5.index.time >= time(9, 15)) & (b5.index.time <= time(15, 25))]
    pos = pd.Series(np.arange(len(idx)), index=idx)
    last1 = pos.reindex(b5.index + pd.Timedelta(minutes=4)).values; ok = ~np.isnan(last1)
    b5 = b5[ok]; last1 = last1[ok].astype(int)
    f9, f15 = ema(b5.close.values, 9), ema(b5.close.values, 15)
    # 5-minute swing highs / lows (2 candles each side), confirmed at the close of the 2nd candle after
    H5, L5 = b5.high.values, b5.low.values; n5 = len(b5); t5 = np.arange(n5, dtype=np.float64)
    d5 = b5.index.normalize().values.astype("datetime64[D]").astype(np.int64)
    swh = np.full(len(idx), np.nan); swht = np.full(len(idx), np.nan); swl = np.full(len(idx), np.nan); swlt = np.full(len(idx), np.nan)
    for k in range(2, n5 - 2):
        if d5[k - 2] != d5[k + 2]: continue
        if H5[k] > max(H5[k - 2], H5[k - 1], H5[k + 1], H5[k + 2]): swh[last1[k + 2]] = H5[k]; swht[last1[k + 2]] = t5[k]
        if L5[k] < min(L5[k - 2], L5[k - 1], L5[k + 1], L5[k + 2]): swl[last1[k + 2]] = L5[k]; swlt[last1[k + 2]] = t5[k]
    # continuous 5-minute clock for every 1-minute bar (for trend-line projection)
    tc1 = np.full(len(idx), np.nan); tc1[last1] = t5; tc1 = pd.Series(tc1).bfill().ffill().values
    def onto1(v):
        out = np.full(len(idx), np.nan); out[last1] = v; return pd.Series(out).ffill().values
    up5 = onto1((f9 > f15).astype(float)) > 0.5; dn5 = onto1((f9 < f15).astype(float)) > 0.5; f9_1 = onto1(f9)
    # efficiency ratio of the last 30 minutes
    cs = pd.Series(c); er = (cs - cs.shift(30)).abs() / cs.diff().abs().rolling(30).sum(); er = er.values
    mv30 = (cs - cs.shift(30)).values
    same30 = pd.Series(day).shift(30).values == day
    er = np.where(same30, er, np.nan)
    day_dir = (c - dopen) / np.maximum(dhi - dlo, 1e-9)
    common = dict(sym=sym, Lm=Lm, swh=swh, swht=swht, swl=swl, swlt=swlt, tc1=tc1, up5=up5, dn5=dn5, f9_1=f9_1, er=er, mv30=mv30,
                  dopen=dopen, pdc=PV.pdc.values, P=PV.P.values, day_dir=day_dir, pcr=PV.pcr.values, cw=PV.cw.values, pw=PV.pw.values)
    charts = {}
    for ch in ("1m", "5m"):
        if ch == "1m":
            sel = np.arange(len(idx)); co, chh, cl, cc = o, h, l, c; e9, e15 = ema(c, 9), ema(c, 15); N = 15
            swh_c, swht_c, swl_c, swlt_c = swh, swht, swl, swlt
        else:
            sel = last1; co, chh, cl, cc = b5.open.values, H5, L5, b5.close.values; e9, e15 = f9, f15; N = 6
            # swing events land on the 5-minute bar that confirms them
            swh_c, swht_c, swl_c, swlt_c = swh[sel], swht[sel], swl[sel], swlt[sel]
        at = atr(chh, cl, cc)
        dy = day[sel]; mn = mins[sel]
        rng = chh - cl; avg10 = pd.Series(rng).shift(1).rolling(10).mean().values
        body = np.abs(cc - co)
        strong_up = (cc > co) & (body >= 0.6 * rng) & ((chh - cc) <= 0.25 * rng) & (rng >= avg10)
        strong_dn = (cc < co) & (body >= 0.6 * rng) & ((cc - cl) <= 0.25 * rng) & (rng >= avg10)
        hiN = pd.Series(chh).shift(1).rolling(N).max().values; loN = pd.Series(cl).shift(1).rolling(N).min().values
        sameN = pd.Series(dy).shift(N).values == dy
        rng_up = sameN & (cc > hiN); rng_dn = sameN & (cc < loN)
        bu, bd, nu, nd, ru, rd = level_kernel(co, chh, cl, cc, dy, Lm[sel], swh_c, swht_c, swl_c, swlt_c, tc1[sel], at, 0.1)
        g = {k: (v[sel] if isinstance(v, np.ndarray) and len(v) == len(idx) else v) for k, v in common.items()}
        g.update(ts_last=idx[sel], o=co, h=chh, l=cl, c=cc, e9=e9, e15=e15, at=at, day=dy, mins=mn,
                 strong_up=strong_up, strong_dn=strong_dn, rng_up=rng_up, rng_dn=rng_dn, brk_up=bu, brk_dn=bd,
                 bnc_up=nu, bnc_dn=nd, room_up=ru, room_dn=rd, tmax=5 if ch == "1m" else 1)
        charts[ch] = g
    return charts


def signal(F, P):
    c, o, e9, e15 = F["c"], F["o"], F["e9"], F["e15"]
    core_up = (e9 > e15) & (c > e9) & (c > o); core_dn = (e9 < e15) & (c < e9) & (c < o)
    et = P["entry"]
    if et == "range": up, dn = F["rng_up"], F["rng_dn"]
    elif et == "level": up, dn = F["brk_up"], F["brk_dn"]
    elif et == "any": up, dn = F["rng_up"] | F["brk_up"], F["rng_dn"] | F["brk_dn"]
    else: up, dn = F["bnc_up"], F["bnc_dn"]
    up = up & core_up; dn = dn & core_dn
    if P["strong"]: up = up & F["strong_up"]; dn = dn & F["strong_dn"]
    d = P["dir"]
    if d >= 1:
        up = up & F["up5"] & (c > F["f9_1"]); dn = dn & F["dn5"] & (c < F["f9_1"])
    if d >= 2:
        er = np.nan_to_num(F["er"]); up = up & (er >= 0.30) & (F["mv30"] > 0); dn = dn & (er >= 0.30) & (F["mv30"] < 0)
    if d >= 3:
        up = up & (c > F["dopen"]) & (c > F["pdc"]) & (c > F["P"]) & (F["day_dir"] >= 0.5)
        dn = dn & (c < F["dopen"]) & (c < F["pdc"]) & (c < F["P"]) & (F["day_dir"] <= -0.5)
    sig = np.zeros(len(c), np.int8); sig[up] = 1; sig[dn & ~up] = -1
    if P["stop"] == "candle":
        sd = np.where(sig > 0, c - F["l"], F["h"] - c) + 0.05 * F["at"]
        sd = np.clip(sd, 0.3 * F["at"], 2.0 * F["at"])
    else:
        sd = F["at"].copy()
    return sig, np.nan_to_num(sd, nan=0.0)


@njit(cache=True)
def sim(o, h, l, c, mins, day, sig, sd, e9, e15, xmode, r_tp, tcap, first_m, last_m, sq_m):
    """xmode 0: close through EMA 9, 1: close through EMA 15, 2: target r_tp x stop. reason: 0 stop 1 target 2 ema 3 time 4 sq-off."""
    n = len(c); out = np.zeros((n // 2 + 1, 6)); m = 0; i = 0
    while i < n - 1:
        s = sig[i]
        if s == 0 or mins[i] + 1 < first_m or mins[i] + 1 > last_m or sd[i] <= 0:
            i += 1; continue
        c0 = c[i]; sl = c0 - s * sd[i]; tp = c0 + s * r_tp * sd[i]
        j = i + 1; xs = c[i]; why = 4
        while j < n:
            if day[j] != day[i]:
                j -= 1; xs = c[j]; why = 4; break
            adv = l[j] if s > 0 else h[j]; fav = h[j] if s > 0 else l[j]
            if s * (adv - sl) <= 0:
                xs = o[j] if s * (o[j] - sl) < 0 else sl; why = 0; break
            if xmode == 2 and s * (fav - tp) >= 0:
                xs = o[j] if s * (o[j] - tp) > 0 else tp; why = 1; break
            if xmode == 0 and s * (c[j] - e9[j]) < 0:
                xs = c[j]; why = 2; break
            if xmode == 1 and s * (c[j] - e15[j]) < 0:
                xs = c[j]; why = 2; break
            if tcap > 0 and j - i >= tcap:
                xs = c[j]; why = 3; break
            if mins[j] + 1 >= sq_m:
                xs = c[j]; why = 4; break
            j += 1
        if j >= n: j = n - 1; xs = c[j]
        out[m, 0] = i; out[m, 1] = j; out[m, 2] = xs; out[m, 3] = s; out[m, 4] = sl; out[m, 5] = why; m += 1
        i = j + 1
    return out[:m]


def price(sym, t_in, t_out, s_in, xs, sl, side, vix1):
    """Model premiums like the app (round 34 harness), ATM strike."""
    meta = D.instrument(sym); step = meta["step"]; mult = meta["iv_mult"]
    dates = [x.date() for x in t_in]
    exps = np.array([R34.expiry_of(sym, d) for d in dates])
    exp_dt = pd.to_datetime([datetime.combine(e, time(15, 30)) for e in exps])
    yin = np.maximum((exp_dt - t_in).total_seconds().values, 0) / (365 * 24 * 3600)
    yout = np.maximum((exp_dt - t_out).total_seconds().values, 0) / (365 * 24 * 3600)
    dte = np.array([(e - d).days for e, d in zip(exps, dates)])
    vx = vix1.reindex(t_in - pd.Timedelta(minutes=1), method="ffill").fillna(14.0).values
    call = side > 0; k = np.round(s_in / step) * step
    p_in = R34.bs(s_in, k, yin, R34.ivv(sym, vx, s_in, k, call, dte, step, mult), call)
    p_sl = R34.bs(sl, k, yin, R34.ivv(sym, vx, sl, k, call, dte, step, mult), call)
    p_out = R34.bs(xs, k, yout, R34.ivv(sym, vx, xs, k, call, dte, step, mult), call)
    hours = (t_out - t_in).total_seconds().values / 3600
    rate = np.array([next(r for lim, r in R34.PR.EXTRA_DECAY if x <= lim) for x in dte])
    p_out = np.maximum(p_in + R34.PR.CHG_SCALE * (p_out - p_in) - p_in * rate * hours, 0.05)
    return p_in, p_out, np.maximum(p_in - p_sl, p_in * 0.05)


GRID = [dict(chart=ch, entry=en, strong=st, dir=dr, exit=ex, stop=sp, tcap=tc)
        for ch, en, st, dr, ex, sp, tc in itertools.product(("1m", "5m"), ("range", "level", "any", "bounce"), (False, True),
                                                            (0, 1, 2, 3), ("ema9", "ema15", "1.5R"), ("candle", "atr"), (False, True))]
DIRN = {0: "no direction filter", 1: "5m trend", 2: "5m trend + one-way", 3: "5m trend + one-way + day bias"}


def label(P):
    return (f"{P['chart']} {P['entry']} breakout" if P["entry"] != "bounce" else f"{P['chart']} level bounce") + \
           (" + strong candle" if P["strong"] else "") + f" | {DIRN[P['dir']]} | exit {P['exit']}, stop {P['stop']}" + \
           (", out within 5 min" if P["tcap"] else "")


def run_grid(charts, sym, live_mask=None):
    rows = []
    for kk, P in enumerate(GRID):
        F = charts[P["chart"]]
        sig, sd = signal(F, P)
        if live_mask is not None: sig[~live_mask[P["chart"]]] = 0
        xm = {"ema9": 0, "ema15": 1, "1.5R": 2}[P["exit"]]
        out = sim(F["o"], F["h"], F["l"], F["c"], F["mins"], F["day"], sig, sd.astype(np.float64), F["e9"], F["e15"], xm, 1.5,
                  F["tmax"] if P["tcap"] else 0, 9 * 60 + 20, 15 * 60, 15 * 60 + 15)
        if len(out) == 0: continue
        T = pd.DataFrame(out, columns=["i", "j", "xs", "side", "sl", "why"]).astype({"i": int, "j": int, "side": int, "why": int})
        T["k_grid"] = kk; T["sym"] = sym
        rows.append(T)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def tag(T, charts):
    """Attach per-trade context used for the filter studies (OI, room to the next level)."""
    out = []
    for ch in ("1m", "5m"):
        m = T.k_grid.map(lambda k: GRID[k]["chart"]) == ch
        if not m.any(): continue
        F = charts[ch]; t = T[m].copy(); i = t.i.values; s = t.side.values
        t["t_in"] = F["ts_last"][i] + pd.Timedelta(minutes=1); t["t_out"] = F["ts_last"][t.j.values] + pd.Timedelta(minutes=1)
        t["s_in"] = F["c"][i]; t["pts"] = s * (t["xs"].values - F["c"][i]); t["at"] = F["at"][i]
        t["room"] = np.where(s > 0, F["room_up"][i], F["room_dn"][i]) / F["at"][i]
        t["pcr"] = F["pcr"][i]
        wall = np.where(s > 0, F["cw"][i] - F["c"][i], F["c"][i] - F["pw"][i]); t["oi_wall_atr"] = wall / F["at"][i]
        out.append(t)
    return pd.concat(out).sort_index()


def money_cols(T, sym, vix1):
    p_in, p_out, risk = price(sym, pd.DatetimeIndex(T.t_in), pd.DatetimeIndex(T.t_out), T.s_in.values, T["xs"].values, T.sl.values,
                              T.side.values, vix1)
    T["p_in"], T["p_out"], T["risk"] = p_in, p_out, risk
    for s in (0.0025, 0.001):
        T[f"pnl_{s}"], T["lots"], T[f"chg_{s}"] = R34.money(sym, p_in, p_out, risk, s)
    T["gross"] = (p_out - p_in) * T.lots * D.instrument(sym)["lot"]
    return T


def part_a():
    vix1 = pd.read_csv(f"{DATA}/INDIAVIX_1m_upstox.csv.gz", parse_dates=["ts"]).set_index("ts").close
    allT = []
    dcats = sorted(set().union(*[set(R34.m1(x).index.strftime("%Y-%m-%d").unique()) for x in SYMS]))
    ycats = sorted(set(d[:4] for d in dcats))
    for sym in SYMS:
        a = R34.m1(sym); oid = oi_daily(sym)
        charts = build(a, sym, oid)
        T = run_grid(charts, sym)
        T = tag(T, charts); T = money_cols(T, sym, vix1)
        T["date"] = T.t_in.dt.strftime("%Y-%m-%d"); T["y"] = T.date.str[:4]
        T = T.drop(columns=["xs", "sl", "i", "j", "t_in", "t_out", "s_in", "at", "risk", "why"])
        for col in T.columns:
            if T[col].dtype == np.float64: T[col] = T[col].astype(np.float32)
        T["k_grid"] = T.k_grid.astype(np.int16); T["side"] = T.side.astype(np.int8)
        T["date"] = pd.Categorical(T.date, categories=dcats); T["y"] = pd.Categorical(T.y, categories=ycats)
        T["sym"] = pd.Categorical(T.sym, categories=SYMS)
        allT.append(T)
        print(sym, len(T), "trades", round(T["pnl_0.0025"].sum()), flush=True)
    A = pd.concat(allT, ignore_index=True); A.to_pickle(f"{WORK}/round36_A.pkl")
    return A


def summarise_a(A):
    A["half"] = np.where(A.y.astype(str) <= "2024", "pick", "judge")
    days = A.groupby("half").date.nunique()
    g = A.groupby(["k_grid", "half"])
    S = pd.DataFrame(dict(n=g.size(), pts=g.pts.mean(), net25=g["pnl_0.0025"].sum(), net10=g["pnl_0.001"].sum(),
                          win=g["pnl_0.0025"].apply(lambda x: (x > 0).mean() * 100))).unstack("half")
    S.columns = [f"{a}_{b}" for a, b in S.columns]; S = S.fillna(0)
    S["setting"] = [label(GRID[k]) for k in S.index]
    yrs = A.groupby(["k_grid", "y"])["pnl_0.001"].sum().unstack("y").fillna(0)
    S["years_pos_10"] = (yrs > 0).sum(axis=1)
    S["per_day"] = (S.n_pick + S.n_judge) / (days.sum() * 3)
    R = dict(settings=len(S), trades=int(len(A)),
             positive_full_25=int(((S.net25_pick + S.net25_judge) > 0).sum()),
             positive_full_10=int(((S.net10_pick + S.net10_judge) > 0).sum()),
             positive_both_halves_25=int(((S.net25_pick > 0) & (S.net25_judge > 0)).sum()),
             positive_both_halves_10=int(((S.net10_pick > 0) & (S.net10_judge > 0)).sum()),
             positive_every_year_10=int((S.years_pos_10 == yrs.shape[1]).sum()),
             positive_pts_pick=int((S.pts_pick > 0).sum()), positive_pts_both=int(((S.pts_pick > 0) & (S.pts_judge > 0)).sum()))
    # honest pick: top 10 on 2023-24 (0.25% slippage), judged on 2025-26
    top = S.sort_values("net25_pick", ascending=False).head(10)
    R["picked_on_2023_24"] = [dict(setting=r.setting, trades_per_day=round(r.per_day, 1),
                                   pick_net25=round(r.net25_pick), judge_net25=round(r.net25_judge), judge_net10=round(r.net10_judge),
                                   pick_pts=round(r.pts_pick, 2), judge_pts=round(r.pts_judge, 2), judge_win=round(r.win_judge, 1),
                                   years_10={y: round(v) for y, v in yrs.loc[k].items()})
                              for k, r in top.iterrows()]
    topedge = S[(S.n_pick >= 200)].sort_values("pts_pick", ascending=False).head(10)
    R["best_edge_on_2023_24"] = [dict(setting=r.setting, n=int(r.n_pick + r.n_judge), pick_pts=round(r.pts_pick, 2),
                                      judge_pts=round(r.pts_judge, 2), judge_net25=round(r.net25_judge), judge_net10=round(r.net10_judge))
                                 for k, r in topedge.iterrows()]
    # what each rule does to the edge before costs (index points per trade), all else equal
    G = pd.DataFrame(GRID); G.index.name = "k_grid"
    E = S[["pts_pick", "pts_judge", "net25_pick", "net25_judge", "n_pick", "n_judge"]].join(G)
    eff = {}
    for col, base in (("strong", False), ("dir", 0), ("entry", "range"), ("exit", "ema9"), ("stop", "atr"), ("tcap", False), ("chart", "1m")):
        others = [x for x in G.columns if x != col]
        piv = E.reset_index().pivot_table(index=others, columns=col, values=["pts_pick", "pts_judge"])
        d = {}
        for v in G[col].unique():
            if v == base: continue
            d[str(v)] = dict(pick=round(float((piv[("pts_pick", v)] - piv[("pts_pick", base)]).mean()), 2),
                             judge=round(float((piv[("pts_judge", v)] - piv[("pts_judge", base)]).mean()), 2))
        eff[col] = dict(vs=str(base), change_in_points_per_trade=d)
    R["rule_effects"] = eff
    # cost picture
    R["costs"] = dict(avg_pts=round(float(A.pts.mean()), 2), gross_rs=round(float(A.gross.mean())), fees_rs=round(float(A["chg_0.0025"].mean())),
                      slip_rs_25=round(float(((A.p_in + A.p_out) * 0.0025 * A.lots * A.sym.map(lambda s: D.instrument(s)["lot"])).mean())))
    RES["part_a"] = R
    S.to_pickle(f"{WORK}/round36_S.pkl")
    pd.set_option("display.width", 260); pd.set_option("display.max_colwidth", 95)
    print({k: v for k, v in R.items() if k not in ("picked_on_2023_24", "rule_effects", "best_edge_on_2023_24")})
    print(pd.DataFrame(R["picked_on_2023_24"]).drop(columns="years_10").to_string())
    print(pd.DataFrame(R["best_edge_on_2023_24"]).to_string())
    print(json.dumps(eff, indent=1))
    return S


def oi_study(A, S):
    """OI filters and room-to-level on the trades of the 20 best settings picked on 2023-24 (Nifty / Bank Nifty, Oct 2023 on)."""
    top = S.sort_values("net25_pick", ascending=False).head(20).index
    T = A[A.k_grid.isin(top) & A.sym.isin(["NIFTY", "BANKNIFTY"]) & A.pcr.notna()].copy()
    out = {}
    def row(m, name):
        x = T[m]
        return dict(filter=name, n=int(len(x)), pts=round(float(x.pts.mean()), 2), rs25=round(float(x["pnl_0.0025"].mean())),
                    pick_rs25=round(float(x[x.half == "pick"]["pnl_0.0025"].mean())) if (x.half == "pick").any() else None,
                    judge_rs25=round(float(x[x.half == "judge"]["pnl_0.0025"].mean())) if (x.half == "judge").any() else None)
    lng = T.side > 0
    out["rows"] = [row(np.ones(len(T), bool), "all trades (no OI filter)"),
                   row((lng & (T.pcr >= 1)) | (~lng & (T.pcr < 1)), "PCR agrees (long if PCR >= 1, short if < 1)"),
                   row((lng & (T.pcr < 1)) | (~lng & (T.pcr >= 1)), "PCR disagrees"),
                   row(T.oi_wall_atr >= 3, "biggest OI wall >= 3 ATR away in trade direction"),
                   row(T.oi_wall_atr < 3, "OI wall < 3 ATR away"),
                   row(T.room >= 1.5, "next key level >= 1.5 ATR away (room to run)"),
                   row(T.room < 1.5, "next key level < 1.5 ATR away")]
    RES["oi_study_history"] = out
    print(pd.DataFrame(out["rows"]).to_string())


# ------------------------------------------------------------------ Part B
def live_days():
    return sorted(d for d in os.listdir(LIVE) if d[:2] == "20" and os.path.exists(f"{LIVE}/{d}/candles_1m.csv.gz")
                  and os.path.exists(f"{LIVE}/{d}/snapshots.json.gz"))


def part_b(S):
    days = live_days(); rows = []
    vix1 = pd.read_csv(f"{DATA}/INDIAVIX_1m_upstox.csv.gz", parse_dates=["ts"]).set_index("ts").close
    vx = []
    for d in days:
        x = pd.read_csv(f"{LIVE}/{d}/candles_1m.csv.gz"); x = x[x.sym == "INDIAVIX"]
        vx.append(x.assign(ts=pd.to_datetime(x.ts)).set_index("ts").close)
    vix1 = pd.concat([vix1] + vx); vix1 = vix1[~vix1.index.duplicated()].sort_index()
    for sym in ("NIFTY", "BANKNIFTY"):
        meta = D.instrument(sym); lot = meta["lot"]; step = meta["step"]
        a = R34.m1(sym); parts = [a]; opt = {}; spreads = {}; oi_rows = []
        for d in days:
            x = pd.read_csv(f"{LIVE}/{d}/candles_1m.csv.gz"); x["ts"] = pd.to_datetime(x.ts)
            ix = x[(x.sym == sym) & (x.kind == "IDX")]
            if len(ix): parts.append(ix.set_index("ts")[["open", "high", "low", "close"]])
            oo = x[(x.sym == sym) & x.kind.isin(["CE", "PE"])]
            if len(oo):
                oo = oo[oo.expiry == oo.expiry.min()]
                for (k, kind), g in oo.groupby(["strike", "kind"]): opt[(d, float(k), kind)] = g.set_index("ts").open
            Sx = json.load(gzip.open(f"{LIVE}/{d}/snapshots.json.gz"))
            for sn in Sx["snapshots"]:
                if sn.get("sym") != sym: continue
                spot = sn["spot"]; ce = pe = 0.0
                for r in sn["rows"]:
                    k = float(r[0])
                    spreads.setdefault((d, k, "CE"), []).append((sn["t"], r[2], r[3]))
                    spreads.setdefault((d, k, "PE"), []).append((sn["t"], r[8], r[9]))
                    if abs(k - spot) <= 5 * step: ce += r[4] or 0; pe += r[10] or 0
                oi_rows.append(dict(ts=pd.Timestamp(f"{d} {sn['t']}"), ce=ce, pe=pe))
        A1 = pd.concat(parts); A1 = A1[~A1.index.duplicated(keep="first")].sort_index().between_time("09:15", "15:29")
        charts = build(A1, sym, oi_daily(sym, A1))
        OI = pd.DataFrame(oi_rows).drop_duplicates("ts").set_index("ts").sort_index()
        OI["bias"] = (OI.pe.diff(3) - OI.ce.diff(3)) / (OI.pe + OI.ce)   # 15-minute put-OI build minus call-OI build
        OI.loc[OI.index.normalize() != pd.Series(OI.index.normalize(), index=OI.index).shift(3).values, "bias"] = np.nan
        live_mask = {ch: np.isin(pd.DatetimeIndex(F["ts_last"]).strftime("%Y-%m-%d"), days) for ch, F in charts.items()}
        T = run_grid(charts, sym, live_mask)
        if T.empty: continue
        T = tag(T, charts)
        p_in_m, p_out_m, risk_m = price(sym, pd.DatetimeIndex(T.t_in), pd.DatetimeIndex(T.t_out), T.s_in.values, T["xs"].values,
                                        T.sl.values, T.side.values, vix1)
        sp_cache = {}
        def spr(d, k, kind, t):
            key = (d, k, kind, t.strftime("%H:%M"))
            if key not in sp_cache:
                best = None
                for (tt, b, a_) in spreads.get((d, k, kind), []):
                    if tt <= key[3] and b and a_ and b > 0 and a_ > 0: best = a_ - b
                sp_cache[key] = best
            return sp_cache[key]
        for q, r in enumerate(T.itertuples()):
            d = r.t_in.strftime("%Y-%m-%d"); kind = "CE" if r.side > 0 else "PE"; k = float(np.round(r.s_in / step) * step)
            px = opt.get((d, k, kind))
            if px is None or r.t_in not in px.index or r.t_out not in px.index: continue
            a_in, a_out = px[r.t_in], px[r.t_out]
            if not (a_in > 0): continue
            s_in, s_out = spr(d, k, kind, r.t_in), spr(d, k, kind, r.t_out)
            if s_in is None or s_out is None: continue
            f_in, f_out = a_in + s_in / 2, max(a_out - s_out / 2, 0.05)
            lots = float(np.clip(np.floor(R34.RISK / (risk_m[q] * lot)), 1, R34.MAX_LOTS)); qty = lots * lot
            bv, sv = f_in * qty, f_out * qty; ex = I.exch_rate(sym) * (bv + sv); se = 1e-6 * (bv + sv)
            chg = 40 + 0.0015 * sv + ex + se + 0.00003 * bv + 0.18 * (40 + ex + se)
            b = OI.bias.asof(r.t_in - pd.Timedelta(minutes=1)) if len(OI) else np.nan
            rows.append(dict(k_grid=r.k_grid, sym=sym, date=d, side=r.side, pts=r.pts, real=(f_out - f_in) * qty - chg,
                             model=R34.money(sym, np.array([p_in_m[q]]), np.array([p_out_m[q]]), np.array([risk_m[q]]), 0.0025)[0][0],
                             spread_pct=s_in / a_in * 100, oi_bias=b, room=r.room))
    B = pd.DataFrame(rows); B.to_pickle(f"{WORK}/round36_B.pkl")
    return B


def summarise_b(B, S):
    R = dict(days=int(B.date.nunique()), trades=int(len(B)), real_total=round(float(B.real.sum())), model_total=round(float(B.model.sum())),
             corr=round(float(B.real.corr(B.model)), 2), median_spread_pct=round(float(B.spread_pct.median()), 2))
    g = B.groupby("k_grid").real.sum()
    R["settings_positive_real"] = f"{int((g > 0).sum())} of {len(g)}"
    top = S.sort_values("net25_pick", ascending=False).head(10).index
    R["history_top10_on_real"] = [dict(setting=label(GRID[k]), n=int((B.k_grid == k).sum()), real=round(float(B[B.k_grid == k].real.sum())),
                                       liquid_real=round(float(B[(B.k_grid == k) & (B.spread_pct <= 0.5)].real.sum())))
                                  for k in top]
    lng = B.side > 0; ok = B.oi_bias.notna()
    agree = ok & ((lng & (B.oi_bias > 0)) | (~lng & (B.oi_bias < 0))); dis = ok & ~agree
    R["oi_intraday"] = dict(agree=dict(n=int(agree.sum()), avg_real=round(float(B[agree].real.mean())), win=round(float((B[agree].real > 0).mean() * 100), 1), pts=round(float(B[agree].pts.mean()), 2)),
                            disagree=dict(n=int(dis.sum()), avg_real=round(float(B[dis].real.mean())), win=round(float((B[dis].real > 0).mean() * 100), 1), pts=round(float(B[dis].pts.mean()), 2)))
    lq = B.spread_pct <= 0.5
    R["liquid"] = dict(liquid_avg=round(float(B[lq].real.mean())), wide_avg=round(float(B[~lq].real.mean())), liquid_share=round(float(lq.mean() * 100), 1))
    best = B[lq & agree].groupby("k_grid").real.agg(["sum", "size"]).sort_values("sum", ascending=False).head(5)
    R["best_real_with_spread_and_oi"] = [dict(setting=label(GRID[k]), n=int(r["size"]), real=round(float(r["sum"]))) for k, r in best.iterrows()]
    RES["part_b"] = R
    print(json.dumps(R, indent=1, default=str))


if __name__ == "__main__":
    A = pd.read_pickle(f"{WORK}/round36_A.pkl") if os.path.exists(f"{WORK}/round36_A.pkl") and "--fresh" not in sys.argv else part_a()
    S = summarise_a(A)
    oi_study(A, S)
    B = part_b(S); summarise_b(B, S)
    json.dump(RES, open(os.path.join(HERE, "round36_results.json"), "w"), indent=1, default=str)
    print("saved")
