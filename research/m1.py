"""1-minute data for the rally / scalping / stop-loss studies (round 7).

Each day: numpy arrays of 1-minute open/high/low/close (09:15-15:29), India VIX, TWAP (indices have
no volume, same as the app's VWAP), plus yesterday's levels and the option expiry the app would buy.
"""
import math
from datetime import date, datetime, timedelta
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import engine as E

POINTS_REF = "NIFTY"


@dataclass
class D1:
    d: date
    sym: str
    m: np.ndarray        # minute of day (bar start)
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    vix: np.ndarray
    twap: np.ndarray
    pc: float            # previous close
    ph: float
    pl: float
    cam: dict
    expiry: date
    gap: float
    scale: float = 1.0   # points multiplier vs Nifty (Bank Nifty ~ 2.2x), so "80 Nifty points" is comparable
    atr_d: float = 0.0   # average daily range of the previous 14 days (points)


def load(sym, days5=None):
    x = pd.read_csv(f"data/{sym}_1m_upstox.csv.gz", parse_dates=["ts"])
    v = pd.read_csv("data/INDIAVIX_1m_upstox.csv.gz", parse_dates=["ts"]).set_index("ts")["close"]
    x = x[(x.ts.dt.hour * 60 + x.ts.dt.minute).between(555, 929)]
    x["vix"] = v.reindex(x.ts).ffill().bfill().values
    x["d"] = x.ts.dt.date
    x["m"] = x.ts.dt.hour * 60 + x.ts.dt.minute
    days5 = days5 or {d.d: d for d in E.load_days(sym)}
    nifty = pd.read_csv("data/NIFTY_1m_upstox.csv.gz", parse_dates=["ts"]) if sym != "NIFTY" else None
    nclose = nifty.groupby(nifty.ts.dt.date)["close"].last() if nifty is not None else None
    out, ranges = [], []
    for d, g in x.groupby("d"):
        if len(g) < 300:
            continue
        day5 = days5.get(d)
        h, l, c = g.high.values, g.low.values, g.close.values
        tp = (h + l + c) / 3
        if day5 is None:
            ranges.append(h.max() - l.min())
            continue
        exp = day5.expiry
        if sym == "NIFTY" and (exp - d).days <= 1:          # rule from round 6: roll to next week with 1 day left
            exp = E.expiry_for(d + timedelta(days=1), "weekly")
        sc = 1.0
        if nclose is not None and d in nclose.index:
            sc = float(c[-1] / nclose[d])
        dd = D1(d=d, sym=sym, m=g.m.values, o=g.open.values, h=h, l=l, c=c, vix=g.vix.values,
                twap=np.cumsum(tp) / np.arange(1, len(tp) + 1), pc=day5.prev_close, ph=day5.prev_high, pl=day5.prev_low,
                cam=day5.cam, expiry=exp, gap=day5.gap, scale=sc,
                atr_d=float(np.mean(ranges[-14:])) if len(ranges) >= 5 else float(h.max() - l.min()))
        out.append(dd)
        ranges.append(h.max() - l.min())
    return out


# ---------------------------------------------------------------- option premiums (vectorised Black-Scholes)
def _ncdf(x):
    return 0.5 * (1 + np.vectorize(math.erf)(x / math.sqrt(2)))


def bs(spot, k, t, iv, call):
    t = np.maximum(t, 1 / (365 * 24 * 4))
    iv = np.maximum(iv, 0.01)
    d1 = (np.log(spot / k) + 0.5 * iv * iv * t) / (iv * np.sqrt(t))
    d2 = d1 - iv * np.sqrt(t)
    return np.where(call, spot * _ncdf(d1) - k * _ncdf(d2), k * _ncdf(-d2) - spot * _ncdf(-d1))


def years(exp: date, d: date, minute):
    end = datetime.combine(exp, datetime.min.time()) + timedelta(hours=15, minutes=30)
    now = datetime.combine(d, datetime.min.time())
    return np.maximum(((end - now).total_seconds() - np.asarray(minute) * 60) / (365 * 24 * 3600), 0)


def option_trade(dd: D1, side, i_in, i_out, px_in, px_out, itm=1):
    """Premium in / out (per unit) of the 1-strike-ITM option of the app's expiry, real-price corrected."""
    meta = E.META[dd.sym]
    step = meta["step"]
    k = round(px_in / step) * step - side * itm * step
    call = side > 0
    opt = "CE" if call else "PE"
    m_in, m_out = dd.m[i_in] + 1, dd.m[i_out] + 1
    iv_in = _iv(dd, dd.vix[i_in], px_in, k, opt)
    iv_out = _iv(dd, dd.vix[i_out], px_out, k, opt)
    p_in = float(bs(px_in, k, years(dd.expiry, dd.d, m_in), iv_in, call))
    p_out = float(bs(px_out, k, years(dd.expiry, dd.d, m_out), iv_out, call))
    p_out = E.real_adjust(p_in, p_out, (dd.expiry - dd.d).days, (m_out - m_in) / 60)
    return p_in, p_out


class _DayShim:
    def __init__(self, dd):
        self.d, self.expiry = dd.d, dd.expiry


def _iv(dd, vix, spot, k, opt):
    return E.iv_for(dd.sym, _DayShim(dd), vix, spot, k, opt, dd.expiry)


def pnl_rupees(dd, p_in, p_out, risk_pts, side_delta=0.6, capital=200_000, risk=0.01, slip=0.005, fixed_lots=None):
    """Size like the app (1% risk on the premium move to the stop, approx. delta 0.6 for 1 ITM) and charge costs."""
    from fno import indicators as I
    lot = E.META[dd.sym]["lot"]
    buy, sell = p_in * (1 + slip), p_out * (1 - slip)
    if fixed_lots:
        lots = fixed_lots
    else:
        per_lot_risk = max(risk_pts * side_delta, p_in * 0.05) * lot
        lots = int(capital * risk // per_lot_risk) or (1 if per_lot_risk <= 1.5 * capital * risk else 0)
        lots = min(lots, int(capital * 0.6 // (buy * lot)))
    if lots <= 0:
        return None, 0
    q = lots * lot
    return (sell - buy) * q - I.charges(buy, sell, q), lots
