"""Options flow from the app's own recorded option-chain snapshots (fno/recorder.py):

- gex_profile: gamma exposure (GEX) by strike for the latest snapshot of a symbol, the
  zero-gamma level, the call/put walls and the day's time series of total net GEX.
- oi_heatmap: open-interest change since the first snapshot of the day, strike x time.
- read: a plain-language, hedged reading of the profile (educational, not advice).

Formulas
  gamma            Black-Scholes gamma (indicators.bs_greeks, r = RISK_FREE, q = 0)
  GEX per strike   gamma x OI x lot x spot^2 x 0.01   ("rupee gamma per 1% move")
                   calls positive, puts negative - the SpotGamma dealer-sign convention,
                   which assumes dealers are net long calls / short puts versus customers.
  zero gamma       the spot level where cumulative net GEX (summed from the lowest strike)
                   crosses zero, linearly interpolated between the two strikes around it.
Everything here is server-side numpy/maths; no paid data. Only the nearest expiry in the
snapshots is used (the recorder stores the nearest two).
"""
from __future__ import annotations

from datetime import date, datetime, time

import numpy as np

from . import config as C
from . import indicators as I
from .recorder import FIELDS

IX = {f: i for i, f in enumerate(FIELDS)}
GEX_SCALE = 0.01           # per 1% move of the underlying
IV_FLOOR, IV_CAP = 0.03, 1.5


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _f(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(v) else v


def _snaps(day_dict: dict, symbol: str) -> list[dict]:
    """The symbol's snapshots for its nearest expiry, in time order."""
    snaps = [s for s in (day_dict or {}).get("snapshots") or [] if s.get("sym") == symbol and s.get("rows")]
    if not snaps:
        return []
    exp = min(s.get("exp") or "9999-12-31" for s in snaps)
    out = [s for s in snaps if (s.get("exp") or "9999-12-31") == exp]
    out.sort(key=lambda s: s.get("t") or "")
    return out


def _lot(symbol: str) -> int:
    try:
        from . import market as M
        return int(M.lot_size(symbol))
    except Exception:
        return int((C.INDICES.get(symbol) or C.STOCKS.get(symbol) or {}).get("lot") or 1)


def _vix_level() -> float:
    try:
        from . import market as M
        return float(M.Market().vix()["level"])
    except Exception:
        return 14.0


def _years_left(day: str, exp: str, t: str) -> float:
    """Years to expiry from the snapshot's date and HH:MM, expiry at 15:30 IST (floor ~15 min)."""
    try:
        d = date.fromisoformat(day)
        hh, mm = (int(x) for x in t.split(":"))
        now = datetime.combine(d, time(hh, mm))
        e = date.fromisoformat(exp)
    except Exception:
        return 1 / 365
    secs = (datetime.combine(e, time(15, 30)) - now).total_seconds()
    return max(secs, 15 * 60) / (365 * 24 * 3600)


def _fill_iv(ivs: list[float | None], fallback: float) -> list[float]:
    """IV per strike as a fraction. None -> nearest neighbour's IV, else the VIX level."""
    n = len(ivs)
    out: list[float | None] = [None] * n
    for i, v in enumerate(ivs):
        v = _f(v)
        if v and v > 0:
            v = v / 100 if v > 3 else v                 # recorder stores percent (13.5); tolerate fractions
            out[i] = min(max(v, IV_FLOOR), IV_CAP)
    known = [i for i, v in enumerate(out) if v is not None]
    for i in range(n):
        if out[i] is None:
            if known:
                j = min(known, key=lambda k: abs(k - i))
                out[i] = out[j]
            else:
                out[i] = min(max(fallback / 100, IV_FLOOR), IV_CAP)
    return out  # type: ignore[return-value]


def _gex_rows(snap: dict, day: str, lot: int, vix: float):
    """Per-strike call/put GEX for one snapshot -> (strikes, call_gex, put_gex) as numpy arrays."""
    spot = _f(snap.get("spot"))
    rows = [r for r in snap.get("rows") or [] if _f(r[IX["strike"]])]
    if not spot or not rows:
        return None
    rows.sort(key=lambda r: r[IX["strike"]])
    strikes = np.array([float(r[IX["strike"]]) for r in rows])
    t = _years_left(day, snap.get("exp") or day, snap.get("t") or "15:30")
    ce_iv = _fill_iv([r[IX["ce_iv"]] for r in rows], vix)
    pe_iv = _fill_iv([r[IX["pe_iv"]] for r in rows], vix)
    scale = lot * spot * spot * GEX_SCALE
    call = np.zeros(len(rows))
    put = np.zeros(len(rows))
    for i, r in enumerate(rows):
        k = strikes[i]
        coi, poi = _f(r[IX["ce_oi"]]) or 0.0, _f(r[IX["pe_oi"]]) or 0.0
        if coi:
            call[i] = I.bs_greeks(spot, k, t, ce_iv[i], "CE")["gamma"] * coi * scale
        if poi:
            put[i] = -I.bs_greeks(spot, k, t, pe_iv[i], "PE")["gamma"] * poi * scale
    return strikes, call, put, spot, t


def _zero_gamma(strikes: np.ndarray, net: np.ndarray):
    """Spot level where cumulative net GEX crosses zero (linear interpolation), else None."""
    cum = np.cumsum(net)
    if len(cum) < 2 or not np.any(cum != 0):
        return None
    for i in range(1, len(cum)):
        a, b = cum[i - 1], cum[i]
        if a == 0:
            return float(strikes[i - 1])
        if (a < 0 < b) or (a > 0 > b):
            return float(strikes[i - 1] + (strikes[i] - strikes[i - 1]) * (-a) / (b - a))
    return None


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def gex_profile(day_dict: dict, symbol: str, lot: int | None = None, vix: float | None = None) -> dict | None:
    """GEX by strike for the symbol's latest snapshot plus the day's net-GEX / spot time series."""
    snaps = _snaps(day_dict, symbol)
    if not snaps:
        return None
    day = (day_dict or {}).get("date") or C.today_ist().isoformat()
    lot = lot or _lot(symbol)
    vix = vix if vix is not None else _vix_level()
    series = []
    latest = None
    for s in snaps:
        g = _gex_rows(s, day, lot, vix)
        if g is None:
            continue
        strikes, call, put, spot, t = g
        net = call + put
        series.append({"t": s.get("t"), "spot": spot, "total_net": float(net.sum())})
        latest = (s, strikes, call, put, net, spot, t)
    if latest is None:
        return None
    s, strikes, call, put, net, spot, t = latest
    pos = [i for i in range(len(net)) if net[i] > 0]
    neg = [i for i in range(len(net)) if net[i] < 0]
    call_wall = float(strikes[max(pos, key=lambda i: net[i])]) if pos else None
    put_wall = float(strikes[min(neg, key=lambda i: net[i])]) if neg else None
    return {
        "symbol": symbol, "date": day, "time": s.get("t"), "expiry": s.get("exp"), "source": s.get("src"),
        "spot": spot, "lot": lot, "years_to_expiry": round(t, 6), "vix_used": vix,
        "strikes": [float(k) for k in strikes],
        "call_gex": [float(x) for x in call], "put_gex": [float(x) for x in put], "net_gex": [float(x) for x in net],
        "total_net": float(net.sum()), "total_call": float(call.sum()), "total_put": float(put.sum()),
        "zero_gamma": _zero_gamma(strikes, net), "call_wall": call_wall, "put_wall": put_wall,
        "series": series, "snapshots": len(snaps),
        "unit": "rupee gamma per 1% move",
        "formula": "GEX = BS gamma x OI x lot x spot^2 x 0.01; calls +, puts - (SpotGamma dealer convention)",
    }


def oi_heatmap(day_dict: dict, symbol: str) -> dict | None:
    """OI change since the first snapshot of the day: rows = strikes (union, sorted), cols = snapshot times."""
    snaps = _snaps(day_dict, symbol)
    if not snaps:
        return None
    strikes = sorted({float(r[IX["strike"]]) for s in snaps for r in s["rows"] if _f(r[IX["strike"]])})
    kidx = {k: i for i, k in enumerate(strikes)}
    n, m = len(strikes), len(snaps)
    ce = np.full((n, m), np.nan)
    pe = np.full((n, m), np.nan)
    times, spots, pcr = [], [], []
    for j, s in enumerate(snaps):
        times.append(s.get("t"))
        spots.append(_f(s.get("spot")))
        tc = tp = 0.0
        for r in s["rows"]:
            k = _f(r[IX["strike"]])
            if not k:
                continue
            c, p = _f(r[IX["ce_oi"]]), _f(r[IX["pe_oi"]])
            if c is not None:
                ce[kidx[k], j] = c
                tc += c
            if p is not None:
                pe[kidx[k], j] = p
                tp += p
        pcr.append(round(tp / tc, 3) if tc else None)
    # baseline = first snapshot that has the strike (strikes drift in/out as spot moves)
    base_ce = np.array([next((v for v in row if not np.isnan(v)), np.nan) for row in ce])
    base_pe = np.array([next((v for v in row if not np.isnan(v)), np.nan) for row in pe])
    dce = ce - base_ce[:, None]
    dpe = pe - base_pe[:, None]
    tol = lambda a: [[None if np.isnan(v) else float(v) for v in row] for row in a]
    return {
        "symbol": symbol, "strikes": strikes, "times": times, "spots": spots,
        "ce_change": tol(dce), "pe_change": tol(dpe), "pe_minus_ce": tol(dpe - dce),
        "ce_oi": tol(ce), "pe_oi": tol(pe), "pcr": pcr,
        "max_abs_change": float(np.nanmax(np.abs(dpe - dce))) if np.any(~np.isnan(dpe - dce)) else 0.0,
    }


def _fmt(v):
    return f"{v:,.0f}" if v is not None else "–"


def read(profile: dict | None) -> str:
    """2-3 hedged, plain-language sentences about the profile. Educational, not advice."""
    if not profile:
        return "No snapshots yet today - the recorder starts at 09:15; the profile fills in from the first snapshot."
    spot, zg = profile.get("spot"), profile.get("zero_gamma")
    total = profile.get("total_net") or 0.0
    cw, pw = profile.get("call_wall"), profile.get("put_wall")
    if zg is None:
        s1 = (f"Spot {_fmt(spot)}: net gamma exposure is {'positive' if total > 0 else 'negative' if total < 0 else 'flat'} "
              f"across the recorded strikes and there is no zero-gamma crossing inside them, so the regime read is weak.")
    elif spot >= zg:
        s1 = (f"Spot {_fmt(spot)} is above the zero-gamma level {_fmt(zg)}: under the dealer-sign convention dealers are net long gamma, "
              f"which tends to dampen moves (hedging sells rallies and buys dips).")
    else:
        s1 = (f"Spot {_fmt(spot)} is below the zero-gamma level {_fmt(zg)}: under the dealer-sign convention dealers are net short gamma, "
              f"which tends to amplify moves (hedging chases price).")
    walls = []
    if cw is not None:
        walls.append(f"largest call wall at {_fmt(cw)}" + (" (often acts as resistance)" if spot and cw >= spot else ""))
    if pw is not None:
        walls.append(f"put wall at {_fmt(pw)}" + (" (often acts as support)" if spot and pw <= spot else ""))
    s2 = ("The " + ", ".join(walls) + ".") if walls else "No clear call or put wall in the recorded strikes."
    ser = profile.get("series") or []
    s3 = ""
    if len(ser) >= 2:
        first, last = ser[0]["total_net"], ser[-1]["total_net"]
        trend = "risen" if last > first else "fallen" if last < first else "been flat"
        s3 = f" Total net GEX has {trend} since the first snapshot at {ser[0]['t']} ({len(ser)} snapshots, through {ser[-1]['t']})."
    return f"{s1} {s2}{s3} This is a model estimate from one expiry and {profile.get('snapshots', 0)} recorded snapshots, not a forecast."
