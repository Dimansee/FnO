"""Strategy research engine: replays intraday option-buying (and one option-selling)
strategies on real 5-minute candles with realistic costs.

Option prices: Black-Scholes on the underlying with IV from the India VIX at that
5-minute bar (x instrument multiplier), slippage on both sides and full Indian
F&O charges. There is no free history of real option quotes, so this models them.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from fno import indicators as I  # noqa: E402

DATA = ROOT / "data"
META = {
    "NIFTY": {"lot": 65, "step": 50, "kind": "weekly", "iv_mult": 1.0},
    "BANKNIFTY": {"lot": 30, "step": 100, "kind": "monthly", "iv_mult": 1.2},
    "FINNIFTY": {"lot": 60, "step": 50, "kind": "monthly", "iv_mult": 1.05},
}
CAPITAL = 200_000
RISK = 0.01            # 1% of capital at risk per trade
SLIP = 0.005           # 0.5% slippage on each option fill
SQUARE_OFF = 15 * 60 + 15
T_OPEN = 9 * 60 + 15


# ---------------------------------------------------------------- expiries
def _last_wd(y, m, wd):
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    while d.weekday() != wd:
        d -= timedelta(days=1)
    return d


def expiry_for(day: date, kind: str) -> date:
    """NSE moved index expiries from Thursday to Tuesday from Sep 2025."""
    wd = 1 if day >= date(2025, 9, 1) else 3
    if kind == "weekly":
        d = day
        while not (d.weekday() == wd and (d - day).days >= 1):
            d += timedelta(days=1)
        return d
    y, m = day.year, day.month
    while True:
        e = _last_wd(y, m, wd)
        if (e - day).days >= 3:
            return e
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


# ---------------------------------------------------------------- data
def _read(name):
    df = pd.read_csv(DATA / f"{name}.csv.gz", parse_dates=["ts"])
    return df.set_index("ts").sort_index()


def _rsi(c: pd.Series, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _adx(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    up, dn = h.diff(), -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(pdm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    ndi = 100 * pd.Series(ndm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


@dataclass
class Day:
    d: date
    t: list            # minutes from midnight (bar start)
    o: list
    h: list
    l: list
    c: list
    vwap: list
    e9: list
    e21: list
    st: list           # supertrend 10,3 (+1/-1)
    st15: list         # supertrend on 15m bars, mapped to 5m (+1/-1, known at 15m close)
    atr: list
    adx: list
    rsi: list
    vix: list          # India VIX at this bar
    sig: list          # noise-area sigma at this bar's close (avg |close/open-1| last 14 days)
    prev_close: float
    prev_high: float
    prev_low: float
    cpr_w: float       # CPR width % of price
    pivot: float
    ctx: int           # global + gap + vix-change score known at the open (-3..+3)
    gap: float
    vix_chg: float
    expiry: date = None
    idx: dict = field(default_factory=dict)


def load_days(sym: str) -> list[Day]:
    df = _read(f"{sym}_5m_upstox")
    df = df[(df.index.time >= datetime.strptime("09:15", "%H:%M").time()) & (df.index.time <= datetime.strptime("15:25", "%H:%M").time())]
    vix = _read("INDIAVIX_5m_upstox")["close"].reindex(df.index).ffill()
    vd = _read("INDIAVIX_1d")["close"]
    df["e9"], df["e21"] = I.ema(df["close"], 9), I.ema(df["close"], 21)
    df["atr"] = I.atr(df)
    df["st"] = I.supertrend(df)
    df["adx"], df["rsi"] = _adx(df), _rsi(df["close"])
    df["vwap"] = I.vwap(df)
    # 15-minute supertrend, usable only after its 15m bar closes
    d15 = df[["open", "high", "low", "close"]].resample("15min", origin="start_day", offset="15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    st15 = I.supertrend(d15)
    st15.index = st15.index + pd.Timedelta(minutes=10)       # value known at the close of the 3rd 5m bar
    df["st15"] = st15.reindex(df.index).ffill().fillna(1)
    df["vix"] = vix.ffill().fillna(14)
    # noise-area sigma by time-of-day (Zarattini et al. "Beat the Market")
    day = df.index.date
    df["dopen"] = df.groupby(day)["open"].transform("first")
    df["mv"] = (df["close"] / df["dopen"] - 1).abs()
    df["tod"] = df.index.hour * 60 + df.index.minute
    piv = df.pivot_table(index=pd.Index(day, name="d"), columns="tod", values="mv")
    sig = piv.rolling(14, min_periods=10).mean().shift(1)    # previous 14 days only
    glob = {}
    for n in ("SP500", "NASDAQ", "NIKKEI", "HANGSENG"):
        s = _read(f"{n}_1d")["close"]
        glob[n] = s.pct_change() * 100
    g = pd.DataFrame(glob)
    g.index = pd.to_datetime(g.index).normalize()
    vchg = vd.pct_change() * 100
    vchg.index = pd.to_datetime(vchg.index).normalize()
    meta = META[sym]
    days, prev = [], None
    for d, x in df.groupby(day):
        if len(x) < 70:
            prev = x
            continue
        if prev is None:
            prev = x
            continue
        pc, ph, pl = float(prev["close"].iloc[-1]), float(prev["high"].max()), float(prev["low"].min())
        pv = (ph + pl + pc) / 3
        bc = (ph + pl) / 2
        tc = 2 * pv - bc
        dts = pd.Timestamp(d)
        gp = g[g.index < dts].tail(1)
        gavg = float(gp.mean(axis=1).iloc[0]) if len(gp) else 0.0
        vc = vchg[vchg.index < dts]
        vcl = float(vc.iloc[-1]) if len(vc) else 0.0
        gap = (x["open"].iloc[0] / pc - 1) * 100
        ctx = (1 if gavg > 0.4 else -1 if gavg < -0.4 else 0) + (1 if gap > 0.25 else -1 if gap < -0.25 else 0) \
            + (-1 if vcl > 5 else 1 if vcl < -5 else 0)
        srow = sig.loc[d] if d in sig.index else None
        sg = [float(srow.get(t, np.nan)) if srow is not None else np.nan for t in x["tod"]]
        dd = Day(d=d, t=list(x["tod"]), o=list(x["open"]), h=list(x["high"]), l=list(x["low"]), c=list(x["close"]),
                 vwap=list(x["vwap"]), e9=list(x["e9"]), e21=list(x["e21"]), st=list(x["st"]), st15=list(x["st15"]),
                 atr=list(x["atr"]), adx=list(x["adx"]), rsi=list(x["rsi"]), vix=list(x["vix"]), sig=sg,
                 prev_close=pc, prev_high=ph, prev_low=pl, cpr_w=abs(tc - bc) / pc * 100, pivot=pv, ctx=ctx,
                 gap=gap, vix_chg=vcl, expiry=expiry_for(d, meta["kind"]))
        dd.idx = {t: i for i, t in enumerate(dd.t)}
        days.append(dd)
        prev = x
    return days


# ---------------------------------------------------------------- option model
def _t(exp: date, d: date, minute: int):
    now = datetime.combine(d, datetime.min.time()) + timedelta(minutes=minute)
    return I.years_to_expiry(exp, now)


def opt_price(spot, k, d: Day, minute, iv, opt):
    return I.bs_price(spot, k, max(_t(d.expiry, d.d, minute), 1 / (365 * 24 * 12)), iv, opt)


@dataclass
class Sig:
    i: int                 # signal bar index (enter at its close)
    side: int              # +1 long (buy CE) / -1 short (buy PE)
    stop: float
    target: float | None   # underlying target, None = no fixed target
    trail: str = ""        # "", "vwap", "st", "atr", "noise"
    be_r: float = 1.0      # move stop to entry after this many R (0 = never)
    time_stop: int = 0     # minutes; exit if +0.5R not reached (0 = off)
    tag: str = ""


def simulate(d: Day, s: Sig, meta, p, upper=None, lower=None):
    """Walk forward from the entry bar. Returns (exit_index, exit_spot, reason)."""
    side, entry = s.side, d.c[s.i]
    risk = abs(entry - s.stop)
    stop, reached = s.stop, False
    n = len(d.t)
    for k in range(s.i + 1, n):
        hi, lo, cl, op = d.h[k], d.l[k], d.c[k], d.o[k]
        adverse, favour = (lo, hi) if side > 0 else (hi, lo)
        if side * (adverse - stop) <= 0:
            px = op if side * (op - stop) < 0 else stop        # gap through the stop fills at the open
            return k, px, "stop" if stop != entry else "breakeven"
        if s.target is not None and side * (favour - s.target) >= 0:
            px = op if side * (op - s.target) > 0 else s.target
            return k, px, "target"
        if s.be_r and side * (favour - (entry + side * s.be_r * risk)) >= 0 and side * (stop - entry) < 0:
            stop = entry
        if side * (favour - (entry + side * 0.5 * risk)) >= 0:
            reached = True
        end = d.t[k] + 5
        if s.trail == "vwap" and side * (cl - d.vwap[k]) < 0:
            return k, cl, "vwap exit"
        if s.trail == "st" and d.st[k] != side:
            return k, cl, "supertrend exit"
        if s.trail == "st15" and d.st15[k] != side:
            return k, cl, "supertrend15 exit"
        if s.trail == "atr":
            ns = (hi - p.get("trail_atr", 2.0) * d.atr[k]) if side > 0 else (lo + p.get("trail_atr", 2.0) * d.atr[k])
            if side * (ns - stop) > 0:
                stop = ns
        if s.trail == "noise" and (d.t[k] + 5) % 30 == 15:     # Zarattini: check on the half hour
            band = upper[k] if side > 0 else lower[k]
            lvl = max(band, d.vwap[k]) if side > 0 else min(band, d.vwap[k])
            if not math.isnan(lvl) and side * (cl - lvl) < 0:
                return k, cl, "noise exit"
        if s.time_stop and not reached and end - (d.t[s.i] + 5) >= s.time_stop:
            return k, cl, "time stop"
        if end >= SQUARE_OFF:
            return k, cl, "square-off"
    return n - 1, d.c[-1], "square-off"


def trade_pnl(d: Day, s: Sig, k_exit, px_exit, meta, p, capital=CAPITAL):
    side, entry = s.side, d.c[s.i]
    opt = "CE" if side > 0 else "PE"
    m_in, m_out = d.t[s.i] + 5, d.t[k_exit] + 5
    iv_in = max(d.vix[s.i] / 100 * meta["iv_mult"], 0.06)
    iv_out = max(d.vix[k_exit] / 100 * meta["iv_mult"], 0.06)
    step = meta["step"]
    k = round(entry / step) * step + side * p.get("otm", 0) * step
    prem_in = opt_price(entry, k, d, m_in, iv_in, opt) * (1 + SLIP)
    prem_stop = opt_price(s.stop, k, d, m_in, iv_in, opt)
    risk_unit = max(prem_in - prem_stop, prem_in * 0.05)
    lot = meta["lot"]
    budget = capital * RISK
    lots = int(budget // (risk_unit * lot))
    if lots == 0 and risk_unit * lot <= 1.5 * budget:
        lots = 1
    if lots == 0:
        return None
    lots = min(lots, int(capital * 0.6 // (prem_in * lot)) or 0)   # cannot spend more than 60% of capital
    if lots == 0:
        return None
    prem_out = opt_price(px_exit, k, d, m_out, iv_out, opt) * (1 - SLIP)
    qty = lots * lot
    pnl = (prem_out - prem_in) * qty - I.charges(prem_in, prem_out, qty)
    r_under = side * (px_exit - entry) / abs(entry - s.stop)
    return {"date": d.d, "in": m_in, "out": m_out, "side": side, "spot_in": entry, "spot_out": px_exit,
            "prem_in": prem_in, "prem_out": prem_out, "lots": lots, "pnl": pnl, "R": r_under, "budget_R": pnl / budget}


# ---------------------------------------------------------------- strategies
# Each strategy: fn(day, start_index, params, state) -> Sig | None (first signal at/after start)

def _ctx_ok(d, side, p):
    need = p.get("ctx", -9)            # -9 = ignore context
    return need <= -9 or side * d.ctx >= need


def _risk_clip(d, i, entry, stop, side, p):
    a = d.atr[i]
    r = abs(entry - stop)
    r = min(max(r, p.get("rmin", 0.5) * a), p.get("rmax", 1.5) * a)
    return entry - side * r


def s_orb(d: Day, j, p, st):
    """Opening-range breakout with VWAP/trend filters (the app's current rulebook family)."""
    orm = p["or_min"]
    n_or = orm // 5
    if len(d.t) <= n_or:
        return None
    hi, lo = max(d.h[:n_or]), min(d.l[:n_or])
    w = (hi - lo) / d.o[0] * 100
    if not (p.get("or_wmin", 0.15) <= w <= p.get("or_wmax", 1.2)):
        return None
    mid = (hi + lo) / 2
    for i in range(max(j, n_or), len(d.t)):
        if d.t[i] + 5 > p.get("last_entry", 870):
            return None
        c = d.c[i]
        for side in (1, -1):
            if side * (c - (hi if side > 0 else lo)) <= 0:
                continue
            if p.get("vwap", 1) and side * (c - d.vwap[i]) <= 0:
                continue
            if p.get("trend", 1) and not (side * (d.e9[i] - d.e21[i]) > 0 and d.st[i] == side):
                continue
            if p.get("adx", 0) and d.adx[i] < p["adx"]:
                continue
            if not _ctx_ok(d, side, p):
                continue
            if p.get("vix_spike", 99) < 99 and side > 0 and d.vix_chg > p["vix_spike"]:
                continue
            ref = {"mid": mid, "opp": lo if side > 0 else hi, "atr": c - side * p.get("stop_atr", 1.0) * d.atr[i]}[p.get("stop", "mid")]
            stop = _risk_clip(d, i, c, ref, side, p)
            r = abs(c - stop)
            tgt = c + side * p["rr"] * r if p.get("rr") else None
            return Sig(i, side, stop, tgt, p.get("trail", ""), p.get("be", 1.0), p.get("tstop", 45), "orb")
    return None


def s_orb_candle(d: Day, j, p, st):
    """Zarattini 5-min ORB: trade in the direction of the first candle(s); stop at their opposite extreme."""
    if j > 0:
        return None
    n_or = p["or_min"] // 5
    o, c = d.o[0], d.c[n_or - 1]
    if abs(c / o - 1) * 100 < p.get("min_body", 0.0):
        return None
    side = 1 if c > o else -1
    if not _ctx_ok(d, side, p):
        return None
    hi, lo = max(d.h[:n_or]), min(d.l[:n_or])
    i = n_or - 1
    if p.get("confirm", 0):            # wait for a close beyond the range instead of entering at once
        for i in range(n_or, len(d.t)):
            if d.t[i] + 5 > p.get("last_entry", 870):
                return None
            if side * (d.c[i] - (hi if side > 0 else lo)) > 0:
                break
        else:
            return None
    entry = d.c[i]
    stop = lo if side > 0 else hi
    stop = _risk_clip(d, i, entry, stop, side, p)
    r = abs(entry - stop)
    tgt = entry + side * p["rr"] * r if p.get("rr") else None
    return Sig(i, side, stop, tgt, p.get("trail", ""), p.get("be", 0), p.get("tstop", 0), "orb_candle")


def noise_bands(d: Day, mult):
    up, lo = [], []
    o, pc = d.o[0], d.prev_close
    for s in d.sig:
        if math.isnan(s):
            up.append(math.nan); lo.append(math.nan)
        else:
            up.append(max(o, pc) * (1 + mult * s)); lo.append(min(o, pc) * (1 - mult * s))
    return up, lo


def s_noise(d: Day, j, p, st):
    """Zarattini/Aziz/Barbon intraday momentum: trade breaks of the 'noise area', checked every 30 min."""
    up, lo = st.setdefault(("nb", d.d, p["mult"]), noise_bands(d, p["mult"]))
    for i in range(max(j, 5), len(d.t)):
        if d.t[i] + 5 > p.get("last_entry", 870):
            return None
        if (d.t[i] + 5) % 30 != 15:      # 9:45, 10:15, ...
            continue
        c = d.c[i]
        for side, band in ((1, up[i]), (-1, lo[i])):
            if math.isnan(band) or side * (c - band) <= 0:
                continue
            if p.get("vwap", 1) and side * (c - d.vwap[i]) <= 0:
                continue
            if not _ctx_ok(d, side, p):
                continue
            if p.get("st_agree") and d.st[i] != side:
                continue
            if p.get("adx", 0) and d.adx[i] < p["adx"]:
                continue
            stop = c - side * p.get("stop_atr", 1.5) * d.atr[i]
            stop = _risk_clip(d, i, c, stop, side, {"rmin": 0.5, "rmax": 3})
            r = abs(c - stop)
            tgt = c + side * p["rr"] * r if p.get("rr") else None
            return Sig(i, side, stop, tgt, "noise", p.get("be", 0), 0, "noise")
    return None


def s_vwap_pull(d: Day, j, p, st):
    """Trend-day VWAP / EMA21 pullback: trend up (price>VWAP, EMA9>EMA21, ADX), dip touches the
    pullback line and closes back in trend direction."""
    for i in range(max(j, p.get("start_bar", 6)), len(d.t)):
        if d.t[i] + 5 > p.get("last_entry", 870):
            return None
        if d.adx[i] < p.get("adx", 20):
            continue
        for side in (1, -1):
            if not (side * (d.e9[i] - d.e21[i]) > 0 and side * (d.c[i] - d.vwap[i]) > 0):
                continue
            line = d.vwap[i] if p["line"] == "vwap" else d.e21[i]
            touch = d.l[i] <= line * (1 + p.get("tol", 0.0005)) if side > 0 else d.h[i] >= line * (1 - p.get("tol", 0.0005))
            if not touch or side * (d.c[i] - d.o[i]) <= 0 or side * (d.c[i] - line) <= 0:
                continue
            if p.get("st", 1) and d.st[i] != side:
                continue
            if not _ctx_ok(d, side, p):
                continue
            swing = min(d.l[max(0, i - 3): i + 1]) if side > 0 else max(d.h[max(0, i - 3): i + 1])
            stop = _risk_clip(d, i, d.c[i], swing - side * 0.1 * d.atr[i], side, p)
            r = abs(d.c[i] - stop)
            tgt = d.c[i] + side * p["rr"] * r if p.get("rr") else None
            return Sig(i, side, stop, tgt, p.get("trail", ""), p.get("be", 1.0), p.get("tstop", 0), "vwap_pull")
    return None


def s_supertrend(d: Day, j, p, st):
    """Supertrend flip (5m or 15m) confirmed by VWAP side and ADX."""
    key = "st15" if p.get("tf") == 15 else "st"
    arr = getattr(d, key)
    for i in range(max(j, 3, 1), len(d.t)):
        if d.t[i] + 5 > p.get("last_entry", 870):
            return None
        if arr[i] == arr[i - 1] and not (p.get("first_bar", 0) and i == 3):
            continue
        side = int(arr[i])
        if p.get("vwap", 1) and side * (d.c[i] - d.vwap[i]) <= 0:
            continue
        if d.adx[i] < p.get("adx", 0):
            continue
        if not _ctx_ok(d, side, p):
            continue
        stop = _risk_clip(d, i, d.c[i], d.c[i] - side * p.get("stop_atr", 1.5) * d.atr[i], side, p)
        r = abs(d.c[i] - stop)
        tgt = d.c[i] + side * p["rr"] * r if p.get("rr") else None
        return Sig(i, side, stop, tgt, p.get("trail", key), p.get("be", 0), p.get("tstop", 0), "supertrend")
    return None


def s_pdhl(d: Day, j, p, st):
    """Previous-day high/low breakout (optionally only on narrow-CPR days, which tend to trend)."""
    if p.get("cpr_max") and d.cpr_w > p["cpr_max"]:
        return None
    for i in range(max(j, p.get("start_bar", 3)), len(d.t)):
        if d.t[i] + 5 > p.get("last_entry", 870):
            return None
        c = d.c[i]
        for side, lvl in ((1, d.prev_high), (-1, d.prev_low)):
            if side * (c - lvl) <= 0 or side * (d.c[i - 1] - lvl) > 0:
                continue            # need the cross on this bar
            if p.get("vwap", 1) and side * (c - d.vwap[i]) <= 0:
                continue
            if not _ctx_ok(d, side, p):
                continue
            stop = _risk_clip(d, i, c, lvl - side * p.get("buf_atr", 0.5) * d.atr[i], side, p)
            r = abs(c - stop)
            tgt = c + side * p["rr"] * r if p.get("rr") else None
            return Sig(i, side, stop, tgt, p.get("trail", ""), p.get("be", 1.0), p.get("tstop", 0), "pdhl")
    return None


def s_ema_adx(d: Day, j, p, st):
    """EMA 9/21 crossover with ADX strength and VWAP agreement."""
    for i in range(max(j, 2), len(d.t)):
        if d.t[i] + 5 > p.get("last_entry", 870):
            return None
        x0, x1 = d.e9[i - 1] - d.e21[i - 1], d.e9[i] - d.e21[i]
        if x0 * x1 > 0 or x1 == 0:
            continue
        side = 1 if x1 > 0 else -1
        if d.adx[i] < p.get("adx", 20):
            continue
        if p.get("vwap", 1) and side * (d.c[i] - d.vwap[i]) <= 0:
            continue
        if not _ctx_ok(d, side, p):
            continue
        stop = _risk_clip(d, i, d.c[i], d.c[i] - side * p.get("stop_atr", 1.5) * d.atr[i], side, p)
        r = abs(d.c[i] - stop)
        tgt = d.c[i] + side * p["rr"] * r if p.get("rr") else None
        return Sig(i, side, stop, tgt, p.get("trail", ""), p.get("be", 1.0), p.get("tstop", 0), "ema_adx")
    return None


def s_gap(d: Day, j, p, st):
    """Gap-and-go: big gap that holds the first N minutes -> trade in gap direction;
    gap-fade variant trades back toward the previous close."""
    if j > 0 or abs(d.gap) < p["gap"]:
        return None
    n = p.get("hold_min", 15) // 5
    g = 1 if d.gap > 0 else -1
    held = all(g * (d.c[k] - d.o[0]) > 0 for k in range(n)) if p["mode"] == "go" else g * (d.c[n - 1] - d.o[0]) < 0
    if not held:
        return None
    side = g if p["mode"] == "go" else -g
    i = n - 1
    if not _ctx_ok(d, side, p):
        return None
    ext = min(d.l[:n]) if side > 0 else max(d.h[:n])
    stop = _risk_clip(d, i, d.c[i], ext, side, p)
    r = abs(d.c[i] - stop)
    if p["mode"] == "fade":
        tgt = d.prev_close
    else:
        tgt = d.c[i] + side * p["rr"] * r if p.get("rr") else None
    return Sig(i, side, stop, tgt, p.get("trail", ""), p.get("be", 0), p.get("tstop", 0), "gap")


STRATS = {"orb": s_orb, "orb_candle": s_orb_candle, "noise": s_noise, "vwap_pull": s_vwap_pull,
          "supertrend": s_supertrend, "pdhl": s_pdhl, "ema_adx": s_ema_adx, "gap": s_gap}


def run(days: list[Day], sym: str, strat: str, p: dict, capital=CAPITAL):
    meta = META[sym]
    fn = STRATS[strat]
    st, out = {}, []
    for d in days:
        if not (p.get("vix_min", 0) <= d.vix[0] <= p.get("vix_max", 99)):
            continue
        j, n, losses = 0, 0, 0
        while n < p.get("max_trades", 2) and losses < p.get("max_losses", 2):
            s = fn(d, j, p, st)
            if s is None:
                break
            up = lo = None
            if s.trail == "noise":
                up, lo = st[("nb", d.d, p["mult"])]
            k, px, why = simulate(d, s, meta, p, up, lo)
            tr = trade_pnl(d, s, k, px, meta, p, capital)
            if tr:
                tr["why"] = why
                out.append(tr)
                n += 1
                losses += tr["pnl"] < 0
                j = k + 1
            else:
                j = s.i + 1          # couldn't size it (too risky/costly): keep looking from the next bar
            if j >= len(d.t):
                break
    return out


# ---------------------------------------------------------------- iron fly (option selling, defined risk)
def run_ironfly(days: list[Day], sym: str, p: dict, capital=CAPITAL):
    """Sell ATM straddle + buy wings (defined risk) at a fixed time; exit on combined
    stop (% of credit), target (% of credit) or 15:15. IV of wings gets a smile bump."""
    meta = META[sym]
    step, lot = meta["step"], meta["lot"]
    out = []
    for d in days:
        if p.get("skip_expiry", 1) and d.d == d.expiry:
            continue
        i = d.idx.get(p["entry"] - 5)
        if i is None:
            continue
        spot = d.c[i]
        k = round(spot / step) * step
        w = p["wing"] * step
        legs = [(k, "CE", -1), (k, "PE", -1), (k + w, "CE", 1), (k - w, "PE", 1)]

        def val(s, kk, minute):
            iv0 = max(d.vix[kk] / 100 * meta["iv_mult"], 0.06)
            v = 0.0
            for strike, opt, q in legs:
                iv = iv0 * (1 + 2.5 * abs(strike / s - 1))
                v += q * opt_price(s, strike, d, minute, iv, opt)
            return v            # negative = net credit position value

        m0 = d.t[i] + 5
        credit = -val(spot, i, m0) * (1 - SLIP * 2)
        if credit <= 0:
            continue
        max_loss = w - credit
        loss_at_stop = p["sl"] * credit
        budget = capital * RISK
        lots = max(1, int(budget // (loss_at_stop * lot)))
        lots = min(lots, int(capital // (max_loss * lot * 1.0)) or 1)   # margin ~ max loss for a fly
        why, pnl_u = "square-off", None
        for kk in range(i + 1, len(d.t)):
            m = d.t[kk] + 5
            worst = max(-val(d.h[kk], kk, m), -val(d.l[kk], kk, m))      # cost to close at the bar's extremes
            if worst - credit >= loss_at_stop:
                pnl_u, why = -loss_at_stop - SLIP * credit, "stop"
                break
            now = -val(d.c[kk], kk, m)
            if p.get("tp") and credit - now >= p["tp"] * credit:
                pnl_u, why = p["tp"] * credit - SLIP * credit, "target"
                break
            if m >= p.get("exit", SQUARE_OFF):
                pnl_u = credit - now - SLIP * credit
                break
        if pnl_u is None:
            pnl_u = credit - (-val(d.c[-1], len(d.t) - 1, d.t[-1] + 5))
        qty = lots * lot
        chg = I.charges(credit, credit, qty, orders=8) * 1.0
        pnl = pnl_u * qty - chg
        out.append({"date": d.d, "in": m0, "out": 0, "side": 0, "lots": lots, "pnl": pnl, "R": pnl_u / loss_at_stop,
                    "budget_R": pnl / budget, "why": why, "credit": credit})
    return out


# ---------------------------------------------------------------- metrics
def stats(trades: list[dict], ndays: int, capital=CAPITAL):
    if not trades:
        return {"n": 0, "net": 0.0, "pf": 0.0, "win": 0.0, "dd": 0.0, "sharpe": 0.0, "avgR": 0.0, "ret": 0.0, "days": ndays}
    pnl = np.array([t["pnl"] for t in trades])
    w, l = pnl[pnl > 0], pnl[pnl <= 0]
    eq = np.cumsum(pnl)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0], eq])) - np.concatenate([[0], eq])))
    by_day = pd.Series(pnl, index=[t["date"] for t in trades]).groupby(level=0).sum()
    daily = np.zeros(ndays)
    daily[: len(by_day)] = by_day.values
    sh = float(daily.mean() / daily.std() * math.sqrt(252)) if daily.std() > 0 else 0.0
    return {"n": len(pnl), "net": float(pnl.sum()), "pf": float(w.sum() / -l.sum()) if l.sum() < 0 else 99.0,
            "win": float(len(w) / len(pnl) * 100), "dd": dd, "sharpe": sh,
            "avgR": float(np.mean([t["budget_R"] for t in trades])), "ret": float(pnl.sum() / capital * 100), "days": ndays}
