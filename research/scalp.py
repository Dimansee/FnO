"""Round 7b — can we scalp the 80-120 point moves? Every rule is traded on the option (1 strike ITM,
the app's expiry, real-price-corrected premiums, 0.5% slippage per fill, full charges, 1% risk sizing),
tuned on 2023-01 .. 2026-04 and then run on the last 120 trading days it never saw.

Rule families (1-minute bars, one position per index at a time):
  chase  - price is X points off the running swing low/high -> buy in that direction
  fade   - a swing has run F points within N minutes -> buy the other way (bet on the give-back)
  vrev   - price fell D points in the last 30 min, then bounced X points off the low -> buy the bounce
Exits: target T points, stop S points (or the swing low/high), time limit H minutes, 15:20 square-off.
A bar that touches both stop and target counts as a stop (conservative).
"""
import itertools, json, math, sys, time
from datetime import date, datetime, timedelta
import numpy as np
import pandas as pd
from numba import njit
from scipy.special import ndtr

import engine as E
import m1

SYMS = ("NIFTY", "BANKNIFTY")
import os
SLIP = float(os.environ.get("SCALP_SLIP", "0.005"))
TAG = os.environ.get("SCALP_TAG", "")
REV = 25.0


@njit(cache=True)
def sim(h, l, c, m, twap, o0, vix, scale, mode, X, T, S, H, F, N, t0, t1, trend, vixmin, maxn):
    """Returns trades (i_in, i_out, side, px_in, px_out, stop_dist)."""
    n = len(c)
    out = []
    lo, hi, lo_i, hi_i = l[0], h[0], 0, 0
    armed_up, armed_dn = True, True
    busy_until = -1
    ntr = 0
    for i in range(1, n - 1):
        if l[i] < lo:
            lo, lo_i, armed_up = l[i], i, True
        if h[i] > hi:
            hi, hi_i, armed_dn = h[i], i, True
        fresh = False
        if hi - c[i] >= REV * scale and hi_i < i and lo_i < hi_i:     # an up-swing ended
            fresh = True
        if c[i] - lo >= REV * scale and lo_i < i and hi_i < lo_i:     # a down-swing ended
            fresh = True
        side = 0
        if i > busy_until and m[i] >= t0 and m[i] <= t1 and vix[i] >= vixmin and ntr < maxn:
            if mode == 0:                       # chase
                if armed_up and c[i] - lo >= X * scale and lo_i < i:
                    side = 1
                elif armed_dn and hi - c[i] >= X * scale and hi_i < i:
                    side = -1
            elif mode == 1:                     # fade a fast run
                if armed_up and c[i] - lo >= F * scale and i - lo_i <= N:
                    side = -1
                elif armed_dn and hi - c[i] >= F * scale and i - hi_i <= N:
                    side = 1
            else:                               # bounce after a sharp fall (V-reversal)
                j = max(0, i - 30)
                hh = h[j]
                ll = l[j]
                for k in range(j, i + 1):
                    hh = max(hh, h[k])
                    ll = min(ll, l[k])
                if hh - ll >= F * scale:
                    if c[i] - ll >= X * scale and armed_up and lo_i < i and lo <= ll + 1e-9:
                        side = 1
                    elif hh - c[i] >= X * scale and armed_dn and hi_i < i and hi >= hh - 1e-9:
                        side = -1
            if side != 0 and trend == 1 and side * (c[i] - o0) <= 0:
                side = 0
            if side != 0 and trend == 2 and side * (c[i] - twap[i]) <= 0:
                side = 0
        if side != 0:
            if mode == 0 or mode == 2:
                if side > 0:
                    armed_up = False
                else:
                    armed_dn = False
            else:
                if side < 0:
                    armed_up = False
                else:
                    armed_dn = False
            px = c[i]
            if S > 0:
                stop = px - side * S * scale
            else:                               # stop at the swing extreme
                stop = lo if side > 0 else hi
                if mode == 1:
                    stop = (c[i] + (c[i] - lo) * 0.5) if side < 0 else (c[i] - (hi - c[i]) * 0.5)
            tgt = px + side * T * scale
            k_out, x = n - 1, c[n - 1]
            for k in range(i + 1, n):
                if side > 0:
                    if l[k] <= stop:
                        k_out, x = k, stop
                        break
                    if h[k] >= tgt:
                        k_out, x = k, tgt
                        break
                else:
                    if h[k] >= stop:
                        k_out, x = k, stop
                        break
                    if l[k] <= tgt:
                        k_out, x = k, tgt
                        break
                if k - i >= H or m[k] >= 920:
                    k_out, x = k, c[k]
                    break
            out.append((i, k_out, side, px, x, abs(px - stop)))
            busy_until = k_out
            ntr += 1
        if fresh:
            lo, lo_i, hi, hi_i = l[i], i, h[i], i
            armed_up = armed_dn = True
    return out


def run(days, mode, X, T, S, H, F, N, t0, t1, trend, vixmin, maxn):
    rows = []
    for dd in days:
        for i, k, side, pin, pout, sd in sim(dd.h, dd.l, dd.c, dd.m, dd.twap, dd.o[0], dd.vix, dd.scale,
                                              mode, X, T, S, H, F, N, t0, t1, trend, vixmin, maxn):
            rows.append((dd, i, k, side, pin, pout, sd))
    return rows


def price(rows):
    """Vectorised option P&L for a list of trades (rupees, after slippage, charges, 1% risk sizing)."""
    if not rows:
        return np.array([]), np.array([]), np.array([])
    sym = rows[0][0].sym
    meta = E.META[sym]
    step, lot = meta["step"], meta["lot"]
    side = np.array([r[3] for r in rows], float)
    pin = np.array([r[4] for r in rows]); pout = np.array([r[5] for r in rows]); sd = np.array([r[6] for r in rows])
    dte = np.array([(r[0].expiry - r[0].d).days for r in rows])
    k = np.round(pin / step) * step - side * step
    t_in = np.array([m1.years(r[0].expiry, r[0].d, r[0].m[r[1]] + 1) for r in rows])
    t_out = np.array([m1.years(r[0].expiry, r[0].d, r[0].m[r[2]] + 1) for r in rows])
    vin = np.array([r[0].vix[r[1]] for r in rows]); vout = np.array([r[0].vix[r[2]] for r in rows])
    ratio = np.array([_ratio(sym, dte[j]) for j in range(len(rows))])
    iv_in, iv_out = np.maximum(vin / 100 * ratio, 0.06), np.maximum(vout / 100 * ratio, 0.06)
    p_in, p_out = _bs(pin, k, t_in, iv_in, side > 0), _bs(pout, k, t_out, iv_out, side > 0)
    hours = (t_in - t_out) * 365 * 24
    rate = np.select([dte <= 2, dte <= 7, dte <= 14], [0.0126, 0.0063, 0.0016], 0.0010)
    p_out = np.maximum(p_in + 0.97 * (p_out - p_in) - p_in * rate * hours, 0.05)
    buy, sell = p_in * (1 + SLIP), p_out * (1 - SLIP)
    per_lot_risk = np.maximum(sd * 0.6, p_in * 0.05) * lot
    lots = np.floor(2000 / per_lot_risk)
    lots = np.where((lots == 0) & (per_lot_risk <= 3000), 1, lots)
    lots = np.minimum(lots, np.floor(120000 / (buy * lot)))
    q = lots * lot
    bv, sv = buy * q, sell * q
    exch, sebi = 0.0003503 * (bv + sv), 0.000001 * (bv + sv)
    ch = 40 + 0.001 * sv + exch + sebi + 0.00003 * bv + 0.18 * (40 + exch + sebi)
    pnl = np.where(q > 0, (sell - buy) * q - ch, 0.0)
    pts = side * (pout - pin) / np.array([r[0].scale for r in rows])
    return pnl, pts, np.array([r[0].d for r in rows])


_CAL = None


def _ratio(sym, dte):
    global _CAL
    if _CAL is None:
        E.use_calibration(True)
        _CAL = E.CALIB
    b = E._dteb(dte)
    return _CAL.get((sym, b, -1)) or _CAL.get((sym, b, 0)) or E.META[sym]["iv_mult"]


def _bs(s, k, t, iv, call):
    t = np.maximum(t, 1 / (365 * 24 * 4))
    d1 = (np.log(s / k) + 0.5 * iv * iv * t) / (iv * np.sqrt(t))
    d2 = d1 - iv * np.sqrt(t)
    return np.where(call, s * ndtr(d1) - k * ndtr(d2), k * ndtr(-d2) - s * ndtr(-d1))


GRID = []
for X, T, S, H in itertools.product((20, 30, 40), (20, 30, 50, 80), (15, 25, 0), (15, 30, 60)):
    for t0, t1, trend, vixmin in ((555, 900, 0, 0), (555, 645, 0, 0), (555, 900, 1, 0), (555, 900, 2, 0), (555, 900, 0, 13), (555, 645, 1, 13)):
        GRID.append(("chase", 0, X, T, S, H, 0, 0, t0, t1, trend, vixmin, 4))
for F, N, T, S, H in itertools.product((60, 80, 100), (10, 20, 30), (20, 30, 50), (20, 30, 0), (15, 30)):
    for t0, t1 in ((555, 900), (600, 900)):
        GRID.append(("fade", 1, 0, T, S, H, F, N, t0, t1, 0, 0, 4))
for F, X, T, S, H in itertools.product((40, 60, 80), (15, 25), (20, 30, 50), (15, 25, 0), (15, 30, 60)):
    for t0, t1, trend in ((555, 900, 0), (585, 870, 0), (555, 900, 2)):
        GRID.append(("vrev", 2, X, T, S, H, F, 0, t0, t1, trend, 0, 4))


if __name__ == "__main__":
    t_start = time.time()
    DAYS = {s: m1.load(s) for s in SYMS}
    dates = sorted({d.d for d in DAYS["NIFTY"]})
    split = dates[-120]
    years = (2023, 2024, 2025, 2026)
    res = []
    for g in GRID:
        rec = {"family": g[0], "params": dict(zip(("X", "T", "S", "H", "F", "N", "t0", "t1", "trend", "vixmin", "maxn"), g[2:])), "sym": {}}
        for s in SYMS:
            pnl, pts, dts = price(run(DAYS[s], *g[1:]))
            tr = dts < split
            rec["sym"][s] = {"n": int(len(pnl)), "train": float(pnl[tr].sum()), "unseen": float(pnl[~tr].sum()),
                             "train_pts": float(pts[tr].sum()), "win": float((pnl > 0).mean() * 100) if len(pnl) else 0,
                             "years": {y: float(pnl[np.array([d.year == y for d in dts], bool)].sum()) if len(pnl) else 0 for y in years},
                             "n_unseen": int((~tr).sum())}
        res.append(rec)
        if len(res) % 100 == 0:
            print(len(res), "/", len(GRID), round(time.time() - t_start), "s", flush=True)
    json.dump(res, open(f"r7_scalp{TAG}.json", "w"), default=str)
    df = pd.DataFrame([{"family": r["family"], **r["params"],
                        "train": sum(r["sym"][s]["train"] for s in SYMS), "unseen": sum(r["sym"][s]["unseen"] for s in SYMS),
                        "train_pts": sum(r["sym"][s]["train_pts"] for s in SYMS),
                        "n": sum(r["sym"][s]["n"] for s in SYMS), "n_unseen": sum(r["sym"][s]["n_unseen"] for s in SYMS),
                        "both_train_pos": all(r["sym"][s]["train"] > 0 for s in SYMS),
                        "all_years_pos": all(sum(r["sym"][s]["years"][y] for s in SYMS) > 0 for y in years[:-1]),
                        "win": np.mean([r["sym"][s]["win"] for s in SYMS])} for r in res])
    df.to_pickle(f"r7_scalp{TAG}.pkl")
    for fam, g in df.groupby("family"):
        print(f"\n== {fam}: {len(g)} rules | profitable on training after costs: {(g.train > 0).mean()*100:.0f}% | "
              f"positive in index points before costs: {(g.train_pts > 0).mean()*100:.0f}% | robust (both idx + every year): {(g.both_train_pos & g.all_years_pos).sum()}")
        print(g.sort_values("train", ascending=False).head(6).to_string(index=False))
    rob = df[df.both_train_pos & df.all_years_pos]
    print("\nrobust rules:", len(rob), "| of those profitable on unseen 120 days:", int((rob.unseen > 0).sum()))
    print("total time", round(time.time() - t_start), "s")
