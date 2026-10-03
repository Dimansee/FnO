"""AI setup, step 1 - the learning table.

One row per 5-minute bar close (09:30-14:30), per index, 2023-01 .. 2026-10:
  features  everything the app can see at that moment (price vs open / VWAP / EMAs / bands /
            Camarilla / yesterday's levels, momentum, ATR, ADX, RSI, Bollinger, Supertrend,
            VIX level and change, gap, global cues, time of day, day structure so far, expiry)
  labels    what a trade opened at that close would have done, for a long and a short, with a
            stop of 1.0 / 1.5 / 2.0 ATR and a target of 2x the stop, held to 15:15:
            R multiple (+2 target, -1 stop, else the square-off result; gap through the stop fills
            at the open; a bar touching both counts as the stop).
Output: ai_rows.parquet
"""
import os, sys
import numpy as np
import pandas as pd
from numba import njit

os.environ.setdefault("FNO_CALIB", "1")
import engine as E
from fno import ai_features as F

SYMS = ("NIFTY", "BANKNIFTY")
STOPS = (1.0, 1.5, 2.0)
RR = 2.0
FIRST, LAST = 570, 870          # first / last decision bar (bar start minutes): 09:30 .. 14:30
SQUARE = 915


@njit(cache=True)
def outcome(o, h, l, c, t, i, side, stop_pts, tgt_pts):
    entry = c[i]
    stop, tgt = entry - side * stop_pts, entry + side * tgt_pts
    n = len(c)
    for k in range(i + 1, n):
        adverse = l[k] if side > 0 else h[k]
        favour = h[k] if side > 0 else l[k]
        if side * (adverse - stop) <= 0:
            px = o[k] if side * (o[k] - stop) < 0 else stop
            return side * (px - entry) / stop_pts, k
        if side * (favour - tgt) >= 0:
            return tgt_pts / stop_pts, k
        if t[k] + 5 >= SQUARE:
            return side * (c[k] - entry) / stop_pts, k
    return side * (c[n - 1] - entry) / stop_pts, n - 1


def day_arrays(d):
    x = {k: np.asarray(v, dtype=float) for k, v in d.x.items()}
    return {"t": np.asarray(d.t), "o": np.asarray(d.o), "h": np.asarray(d.h), "l": np.asarray(d.l), "c": np.asarray(d.c),
            "vwap": np.asarray(d.vwap), "e5": np.asarray(d.e5), "e9": np.asarray(d.e9), "e20": np.asarray(d.e20), "e21": np.asarray(d.e21),
            "e50": x["e50"], "s44": np.asarray(d.s44), "st": np.asarray(d.st), "st15": np.asarray(d.st15), "atr": np.asarray(d.atr),
            "adx": np.asarray(d.adx), "rsi": np.asarray(d.rsi), "vix": np.asarray(d.vix), "sig": np.asarray(d.sig, dtype=float),
            "macdh": x["macdh"], "bbu": x["bbu"], "bbl": x["bbl"], "bbm": x["bbm"], "bbw": x["bbw"], "bbw_min": x["bbw_min"],
            "ha_o": x["ha_o"], "ha_c": x["ha_c"], "dch": x["dch"], "dcl": x["dcl"], "vsd": x["vsd"]}


def day_rows(d, sym_id, avg_range, prev_days):
    bars = day_arrays(d)
    o, h, l, c, t = bars["o"], bars["h"], bars["l"], bars["c"], bars["t"]
    n = len(c)
    day = {"prev_close": d.prev_close, "prev_high": d.prev_high, "prev_low": d.prev_low, "cpr_w": d.cpr_w, "gap": d.gap,
           "ctx": d.ctx, "vix_chg": d.vix_chg, "dte": (d.expiry - d.d).days, "sym_id": sym_id, "avg_range": avg_range,
           "h3": d.cam["h3"], "h4": d.cam["h4"], "l3": d.cam["l3"], "l4": d.cam["l4"],
           "prev2_close": prev_days[-2].c[-1] if len(prev_days) >= 2 else None,
           "prev_open": prev_days[-1].o[0] if prev_days else None}
    b15 = [(i5, e5_, e20_, s44_, rsi_) for (i5, o15, h15, l15, c15, e5_, e20_, s44_, rsi_) in d.b15]
    i_close = min(int(np.searchsorted(t, SQUARE - 5)), n - 1)
    rows = []
    for i in range(n):
        if t[i] < FIRST or t[i] > LAST or np.isnan(bars["sig"][i]):
            continue
        a = bars["atr"][i]
        f = F.bar_features(bars, i, day, b15)
        labels = {}
        for s in (1, -1):
            for k in STOPS:
                r, kx = outcome(o, h, l, c, t, i, s, k * a, RR * k * a)
                labels[f"R_{'L' if s > 0 else 'S'}_{k}"] = r
                labels[f"X_{'L' if s > 0 else 'S'}_{k}"] = int(kx)
        labels["fwd"] = (c[i_close] - c[i]) / a
        labels["fwd12"] = (c[min(i + 12, n - 1)] - c[i]) / a
        rows.append({"date": d.d, "i": i, "spot": c[i], "atr": a, **f, **labels})
    return rows


def build():
    out = []
    for sid, sym in enumerate(SYMS):
        days = E.load_days(sym)
        ranges = []
        for n, d in enumerate(days):
            avg = float(np.mean(ranges[-14:])) if len(ranges) >= 5 else float(max(d.h) - min(d.l))
            out += day_rows(d, sid, avg, days[max(0, n - 2): n])
            ranges.append(max(d.h) - min(d.l))
        print(sym, "rows", len(out), flush=True)
    df = pd.DataFrame(out)
    old = pd.read_parquet("ai_rows.parquet")
    df["R_T_L"], df["R_T_S"], df["X_T_L"], df["X_T_S"] = old["R_T_L"].values, old["R_T_S"].values, old["X_T_L"].values, old["X_T_S"].values
    common = [c for c in old.columns if c in df.columns and c not in ("date",)]
    diff = {c: float(np.nanmax(np.abs(pd.to_numeric(df[c]) - pd.to_numeric(old[c])))) for c in common if np.issubdtype(df[c].dtype, np.number)}
    bad = {k: v for k, v in diff.items() if v > 1e-9}
    print("columns differing from the saved table:", bad or "none", "| dropped:", [c for c in old.columns if c not in df.columns])
    df.to_parquet("ai_rows.parquet", index=False)
    print(df.shape, df["date"].min(), df["date"].max())
    return df


if __name__ == "__main__":
    build()
