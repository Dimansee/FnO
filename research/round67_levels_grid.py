"""Round 67: trading the classic intraday levels - CPR, floor pivots, previous-day high / low / close, multi-touch S/R zones and
the opening range - every behaviour, with "matching" (confluence) between them (1 Nov 2026).

User: "what if we do trades on Pivots / CPR, S/R zones, prev day high low, on the logic of these try every possible outcome and
matching from this".

Data: Nifty, Bank Nifty, Sensex 1-minute index candles (Upstox) 2 Jan 2023 - 16 Oct 2026 -> 5-minute bars (also 15-minute).
Levels for each day use only earlier data:
  cpr     P = (H+L+C)/3, BC = (H+L)/2, TC = 2P - BC (previous session)            pivots  S1-S3 / R1-R3 (classic, previous session)
  pd      previous-day high, low, close                                         sr      multi-touch zones: confirmed 15-min swing highs /
  or      09:15-09:30 opening range high / low (usable from 09:30)                      lows of the last 10 sessions within 0.15%, 2+ touches
Behaviours on a 5-min (or 15-min) close, entries 09:20-14:30 (next bar's open), out by 15:15, at most 2 trades per index per day:
  break   close through a level (previous close on the other side)
  retest  within 12 bars after a break, price comes back to the level (within 0.1 ATR) and closes on the breakout side
  bounce  first touch of the day: the bar trades through the level and closes back on its original side (rejection)
  fakeout the previous bar closed through the level, this bar closes back across it -> trade the reversal
Stops: 'level' (0.5 ATR beyond the level) or 'bar' (beyond the signal bar's extreme, at least 0.3 ATR from entry).
Targets: next level of any family (at least 1R away, else 2R), 1.5R, 2R, end of day (stop only).
Filters: confluence (another family's level within 0.1% of this one) on / off; CPR width (all / narrow < 0.25% / wide > 0.5%);
day bias (open above TC -> longs only, below BC -> shorts only) on / off; window (to 14:30 / to 11:30).
Measures: index R per trade (gross) against a random-direction twin (same bar, stop distance and R multiple), and money: the
same trades priced as ATM options (the app's calibrated Black-Scholes with decay, real charges, 0.25% slippage per fill, Rs 2,000
risk a trade, research/round34.py), by year. Design 2023-24, judged on 2025-26.
Usage: python research/round67_levels_grid.py <data_dir>
"""
import itertools
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from numba import njit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import round62_scalping7 as S  # noqa: E402

DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data")
S.DATA = DATA
OUT = os.path.join(HERE, "round67_results.json")
SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
FAMS = ["cpr", "pivots", "pd", "sr", "or"]
SLIP = 0.0025


def load(sym, tf):
    df = S.load("index", sym, "5m")
    if tf == "15m":
        df = df.groupby(df.index.date, group_keys=False).apply(lambda g: g.resample("15min", origin=g.index[0], label="left", closed="left")
                                                               .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna())
    return df


def day_levels(df):
    """Per-bar arrays of every level (n x L) with a family id per column, plus CPR width and the day's bias (no look-ahead)."""
    d = df.index.normalize()
    g = df.groupby(d).agg(H=("high", "max"), L=("low", "min"), C=("close", "last"), O=("open", "first"))
    pH, pL, pC = g.H.shift(1), g.L.shift(1), g.C.shift(1)
    P = (pH + pL + pC) / 3
    BC = (pH + pL) / 2
    TC = 2 * P - BC
    TC, BC = np.maximum(TC, BC), np.minimum(TC, BC)
    lv = pd.DataFrame({"cpr_TC": TC, "cpr_P": P, "cpr_BC": BC,
                       "pivots_R1": 2 * P - pL, "pivots_S1": 2 * P - pH, "pivots_R2": P + (pH - pL), "pivots_S2": P - (pH - pL),
                       "pivots_R3": pH + 2 * (P - pL), "pivots_S3": pL - 2 * (pH - P), "pd_H": pH, "pd_L": pL, "pd_C": pC})
    width = ((TC - BC) / P * 100)
    bias = np.where(g.O > TC, 1, np.where(g.O < BC, -1, 0))
    per = lv.reindex(d).values
    # opening range 09:15-09:30, known from 09:30
    t = df.index
    first15 = (t.hour == 9) & (t.minute < 30)
    orh = df["high"].where(first15).groupby(d).transform("max").values
    orl = df["low"].where(first15).groupby(d).transform("min").values
    known = ~((t.hour == 9) & (t.minute < 30))
    orh, orl = np.where(known, orh, np.nan), np.where(known, orl, np.nan)
    # S/R zones from 15-min swings of the previous 10 sessions (2+ touches within 0.15%), one set per day
    h15 = df.resample("15min").agg({"high": "max", "low": "min"}).dropna()
    hs, ls = h15["high"].values, h15["low"].values
    k = 3
    piv = []
    for p in range(k, len(hs) - k):
        if hs[p] >= hs[p - k:p + k + 1].max():
            piv.append((h15.index[p + k], hs[p]))          # known when confirmed (k bars later)
        if ls[p] <= ls[p - k:p + k + 1].min():
            piv.append((h15.index[p + k], ls[p]))
    piv = pd.DataFrame(piv, columns=["t", "px"]).sort_values("t")
    days = g.index
    zones = {}
    for i, day in enumerate(days):
        if i < 10:
            zones[day] = []
            continue
        pp = piv[(piv.t < day) & (piv.t >= days[i - 10])].px.values
        pp = np.sort(pp)
        out, j = [], 0
        while j < len(pp):
            grp = [pp[j]]
            while j + 1 < len(pp) and pp[j + 1] - grp[0] <= 0.0015 * grp[0]:
                j += 1
                grp.append(pp[j])
            if len(grp) >= 2:
                out.append(float(np.mean(grp)))
            j += 1
        zones[day] = sorted(out, key=lambda z: -1)[:8]
    zmax = 8
    Z = np.full((len(df), zmax), np.nan)
    for day, zs in zones.items():
        m = d == day
        for q, z in enumerate(zs[:zmax]):
            Z[m, q] = z
    cols = list(lv.columns)
    LV = np.hstack([per, orh[:, None], orl[:, None], Z])
    fam = [c.split("_")[0] for c in cols] + ["or", "or"] + ["sr"] * zmax
    return LV, np.array(fam), width.reindex(d).values, pd.Series(bias, index=g.index).reindex(d).values


@njit(cache=True)
def signals(o, h, l, c, a, LV, famid, fsel, day, mins, beh):
    """For family fsel (-1 = all) and behaviour beh (0 break, 1 retest, 2 bounce, 3 fakeout): side, the level and its column."""
    n, m = LV.shape
    side = np.zeros(n, np.int64)
    lvl = np.full(n, np.nan)
    col = np.full(n, -1, np.int64)
    touched = np.zeros(m, np.bool_)
    brk_dir = np.zeros(m, np.int64)
    brk_bar = np.full(m, -100, np.int64)
    cur = -1
    for i in range(1, n):
        if day[i] != cur:
            cur = day[i]
            touched[:] = False
            brk_dir[:] = 0
            brk_bar[:] = -100
        if day[i - 1] != day[i]:
            continue
        A = a[i]
        if not (A > 0):
            continue
        for j in range(m):
            if fsel >= 0 and famid[j] != fsel:
                continue
            L = LV[i, j]
            if not np.isfinite(L) or not np.isfinite(LV[i - 1, j]):
                continue
            up = c[i] > L and c[i - 1] <= L
            dn = c[i] < L and c[i - 1] >= L
            s = 0
            if up or dn:
                d = 1 if up else -1
                if beh == 0:
                    s = d
                elif beh == 3 and brk_dir[j] == -d and brk_bar[j] == i - 1:
                    s = d                                   # yesterday's bar broke the other way, this one closes back
                brk_dir[j], brk_bar[j] = d, i
            if beh == 1 and s == 0 and brk_dir[j] != 0 and 1 <= i - brk_bar[j] <= 12:
                d = brk_dir[j]
                if d == 1 and l[i] <= L + 0.1 * A and c[i] > L:
                    s = 1
                    brk_dir[j] = 0
                elif d == -1 and h[i] >= L - 0.1 * A and c[i] < L:
                    s = -1
                    brk_dir[j] = 0
            if beh == 2 and s == 0 and not touched[j] and l[i] <= L <= h[i]:
                if c[i - 1] > L and c[i] > L:
                    s = 1
                elif c[i - 1] < L and c[i] < L:
                    s = -1
            if l[i] <= L <= h[i]:
                touched[j] = True
            if s != 0 and side[i] == 0:
                side[i], lvl[i], col[i] = s, L, j
    return side, lvl, col


def main():
    t0 = time.time()
    import round34 as R34                       # option pricing / money (needs scipy)
    vix1 = pd.read_csv(os.path.join(DATA, "INDIAVIX_1m_upstox.csv.gz"), parse_dates=["ts"]).set_index("ts").close
    vix1 = vix1[~vix1.index.duplicated()].sort_index()
    rng = np.random.default_rng(67)
    res = {}
    for tf in ("5m", "15m"):
        for sym in SYMS:
            df = load(sym, tf)
            o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
            a = S.atr(h, l, c, 14)
            n = len(c)
            LV, fam, width, bias = day_levels(df)
            famid = np.array([FAMS.index(f) for f in fam], np.int64)
            dayid = (df.index.normalize().values.astype("int64") // 86_400_000_000_000).astype(np.int64)
            hm = df.index.hour * 60 + df.index.minute
            mins = np.asarray(hm, np.int64)
            _, dayend, _, _, _ = S.session_info("index", df.index)
            dayend = dayend | (hm >= 15 * 60 + 15)
            yrs = df.index.year.values
            # confluence: another family's level within 0.1% of each level
            conf = np.zeros(LV.shape, bool)
            for j in range(LV.shape[1]):
                for q in range(LV.shape[1]):
                    if fam[q] != fam[j]:
                        conf[:, j] |= np.abs(LV[:, j] - LV[:, q]) <= 0.001 * LV[:, j]
            # all levels sorted per bar (for 'next level' targets)
            LVs = np.sort(np.where(np.isfinite(LV), LV, np.nan), axis=1)
            for fi, fname in [(-1, "all")] + list(enumerate(FAMS)):
                for bi, bname in enumerate(("break", "retest", "bounce", "fakeout")):
                    side0, lvl, col = signals(o, h, l, c, a, LV, famid, fi, dayid, mins, bi)
                    cf = np.zeros(n, bool)
                    hit = col >= 0
                    cf[hit] = conf[np.flatnonzero(hit), col[hit]]
                    for stop_kind in ("level", "bar"):
                        if stop_kind == "level":
                            stop = np.where(side0 == 1, lvl - 0.5 * a, np.where(side0 == -1, lvl + 0.5 * a, np.nan))
                        else:
                            stop = np.where(side0 == 1, np.minimum(l, c - 0.3 * a), np.where(side0 == -1, np.maximum(h, c + 0.3 * a), np.nan))
                        risk = np.abs(c - stop)
                        # next level beyond the entry, at least 1R away
                        nxt = np.full(n, np.nan)
                        for i in np.flatnonzero(side0):
                            row = LVs[i][np.isfinite(LVs[i])]
                            if side0[i] == 1:
                                cand = row[row >= c[i] + risk[i]]
                                nxt[i] = cand[0] if len(cand) else c[i] + 2 * risk[i]
                            else:
                                cand = row[row <= c[i] - risk[i]]
                                nxt[i] = cand[-1] if len(cand) else c[i] - 2 * risk[i]
                        tg = {"next level": nxt, "1.5R": c + side0 * 1.5 * risk, "2R": c + side0 * 2.0 * risk, "end of day": np.full(n, np.nan)}
                        for (wn, wcut), (cw, wmask), bf, cfl in itertools.product((("to 14:30", 14 * 60 + 30), ("to 11:30", 11 * 60 + 30)),
                                                                                   (("any CPR", np.ones(n, bool)), ("narrow CPR", width < 0.25), ("wide CPR", width > 0.5)),
                                                                                   (False, True), (False, True)):
                            sd = side0.copy()
                            ok = (mins >= 9 * 60 + 20) & (mins <= wcut) & wmask
                            if cfl:
                                ok &= cf
                            if bf:
                                ok &= ~(((bias == 1) & (sd == -1)) | ((bias == -1) & (sd == 1)))
                            sd[~ok] = 0
                            # at most 2 signals per day
                            idx = np.flatnonzero(sd)
                            if len(idx):
                                dd = dayid[idx]
                                rank = pd.Series(1, index=idx).groupby(dd).cumsum().values
                                sd[idx[rank > 2]] = 0
                            idx = np.flatnonzero(sd)
                            if len(idx) < 20:
                                continue
                            allow = np.ones(n, bool)
                            # random-direction twin
                            tw = sd.copy()
                            flip = rng.random(len(idx)) < 0.5
                            tw[idx[flip]] *= -1
                            for tn, tgt in tg.items():
                                tw_stop = np.where(tw == sd, stop, 2 * c - stop)
                                tw_tgt = np.where(np.isfinite(tgt), np.where(tw == sd, tgt, 2 * c - tgt), np.nan)
                                en, s2, R, b = S.sim(o, h, l, c, sd, stop, tgt, np.zeros(n, bool), np.zeros(n, bool), 10 ** 6, dayend, allow)
                                en2, s3, R2, _ = S.sim(o, h, l, c, tw, tw_stop, tw_tgt, np.zeros(n, bool), np.zeros(n, bool), 10 ** 6, dayend, allow)
                                rr = np.abs(o[en] - stop[en - 1]) / o[en]
                                rr2 = np.abs(o[en2] - tw_stop[en2 - 1]) / o[en2]
                                Ri, R2i = R / np.maximum(rr, 1e-6), R2 / np.maximum(rr2, 1e-6)
                                # option money
                                j = np.minimum(en + b - 1, n - 1)
                                T = pd.DataFrame({"i": en, "j": j, "xs": o[en] * (1 + s2 * R), "side": s2, "sl": stop[en - 1]})
                                p_in, p_out, prisk, dte = R34.price(sym, {"idx": df.index, "c": o}, T, vix1)
                                pnl, lots, chg = R34.money(sym, p_in, p_out, prisk, SLIP)
                                key = json.dumps({"tf": tf, "family": fname, "behaviour": bname, "stop": stop_kind, "target": tn, "window": wn,
                                                  "cpr": cw, "bias": bf, "confluence": cfl})
                                dct = res.setdefault(key, {"R": [], "tR": [], "tyr": [], "pnl": [], "yr": [], "sym": []})
                                dct["R"] += Ri.tolist(); dct["tR"] += R2i.tolist(); dct["tyr"] += yrs[en2].tolist(); dct["pnl"] += pnl.tolist(); dct["yr"] += yrs[en].tolist(); dct["sym"] += [sym] * len(R)
            print(tf, sym, round(time.time() - t0), "s", flush=True)
    import pickle
    pickle.dump(res, open("/tmp/claude-0/-home-claude/56339fbc-19b1-55f3-af45-02b63ba15574/scratchpad/r67_raw.pkl", "wb"))
    rows = []
    for key, d in res.items():
        R, tR, P, yr, sy, ty = (np.array(d[k]) for k in ("R", "tR", "pnl", "yr", "sym", "tyr"))
        e = yr <= 2024
        rows.append({"v": json.loads(key), "n": int(len(R)), "n_early": int(e.sum()), "n_late": int((~e).sum()),
                     "R_early": round(float(R[e].mean()), 3) if e.any() else None, "R_late": round(float(R[~e].mean()), 3) if (~e).any() else None,
                     "twin_early": round(float(tR[ty <= 2024].mean()), 3) if (ty <= 2024).any() else None, "twin_late": round(float(tR[ty > 2024].mean()), 3) if (ty > 2024).any() else None,
                     "rs_early": round(float(P[e].sum())), "rs_late": round(float(P[~e].sum())),
                     "rs_years": {str(y): round(float(P[yr == y].sum())) for y in np.unique(yr)},
                     "rs_per_index": {s: round(float(P[sy == s].sum())) for s in np.unique(sy)}, "win": round(float((P > 0).mean() * 100), 1)})
    json.dump(rows, open(OUT, "w"))
    print("variants", len(rows), round(time.time() - t0), "s")


if __name__ == "__main__":
    main()
