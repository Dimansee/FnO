"""Round 70: the user's OI-wall breakout with point targets (1 Nov 2026).

User: "Suppose there is support at 23000 with maximum OI, and the last 3 times the market moved back from there, but this time a
candle closes above 23000 - enter there, exit at 23050 and trail from there, or book half at 23050 and half at ~23090."

Setup (long; shorts are the mirror at the put wall):
  level      the previous day's largest call-OI strike (OI wall), nearest expiry - also tested: the 2nd-largest wall, plain
             100-point levels with no OI behind them (control), and the OI wall without any rejection requirement (control)
  rejections price came within 0.05% of the level (bar high), closed below it, then fell back at least 0.15% (~35 Nifty points)
             before touching again; counted over today and the previous 2 sessions; need >= 2 or >= 3
  break      a 5-min (or 15-min) candle closes above the level after closing below it; entry at the next candle's open,
             09:20 - 14:30, one trade per level per day
  stop       0.15% below the level (~35 points)  or  the breakout candle's low
  exits      (points are Nifty at ~23,000; scaled as % for Bank Nifty: +50 = 0.22%, +90 = 0.39%, trail 30 = 0.13%)
             full@50        all out at +50
             50 then trail  at +50 the stop moves to entry, then trails 30 points behind the best price
             half 50/90     half out at +50 (stop to entry), half at +90
             half 50+trail  half out at +50, the rest trails 30 points behind the best price
             hold to close  stop only, out at 15:15
Money: each leg priced as an ATM option (app calibration with decay, charges, 0.25% slippage per fill), Rs 2,000 risk per trade
(lots from the option's risk to the stop). Nifty + Bank Nifty, 10 Oct 2023 - 9 Oct 2026 (the OI history). Early = to 2024.
Usage: python research/round70_oi_wall_rejection_breakout.py <data_dir>
"""
import itertools
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import numpy as np
import pandas as pd

DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data")
import round62_scalping7 as S  # noqa: E402
import round68_oi as OI  # noqa: E402
S.DATA = DATA
OI.DATA = DATA
SYMS = ["NIFTY", "BANKNIFTY"]
SLIP = 0.0025
CACHE = {}
T50, T90, TR, STOPP = 50 / 23000, 90 / 23000, 30 / 23000, 0.0015
NEAR, AWAY = 0.0005, 0.0015


def load(sym, tf):
    df = S.load("index", sym, "5m")
    if tf == "15m":
        df = df.groupby(df.index.date, group_keys=False).apply(lambda g: g.resample("15min", origin=g.index[0], label="left", closed="left")
                                                               .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna())
    return df[df.index >= "2023-10-05"]


def rejections(h, l, c, L, side, start, i):
    """Completed rejections at level L between bar `start` and bar i-1: price came within 0.05% of the level without closing
    through it, then moved back at least 0.15% (resistance for side=1, support for side=-1)."""
    n_rej, touched = 0, False
    for k in range(start, i):
        if side == 1:
            touch = h[k] >= L * (1 - NEAR) and c[k] < L
            away = c[k] <= L * (1 - AWAY)
        else:
            touch = l[k] <= L * (1 + NEAR) and c[k] > L
            away = c[k] >= L * (1 + AWAY)
        if touch:
            touched = True
        elif touched and away:
            n_rej += 1
            touched = False
    return n_rej


def manage(o, h, l, c, i0, s, stop, exit_kind, day_last):
    """Simulate one trade entered at bar i0's open. Returns list of legs (fraction, exit bar, exit price)."""
    e = o[i0]
    t1, t2, tr = e * (1 + s * T50), e * (1 + s * T90), e * TR
    best = e
    legs, frac = [], 1.0
    sl = stop
    moved = False
    for k in range(i0, day_last + 1):
        if s == 1:
            if l[k] <= sl:
                legs.append((frac, k, min(o[k], sl) if k > i0 else sl)); return legs
        else:
            if h[k] >= sl:
                legs.append((frac, k, max(o[k], sl) if k > i0 else sl)); return legs
        hit1 = (h[k] >= t1) if s == 1 else (l[k] <= t1)
        hit2 = (h[k] >= t2) if s == 1 else (l[k] <= t2)
        if exit_kind == "full@50" and hit1:
            legs.append((1.0, k, t1)); return legs
        if exit_kind in ("half 50/90", "half 50+trail") and hit1 and frac == 1.0:
            legs.append((0.5, k, t1)); frac = 0.5; sl = e; moved = True
        if exit_kind == "half 50/90" and frac == 0.5 and hit2:
            legs.append((0.5, k, t2)); return legs
        if exit_kind == "50 then trail" and hit1 and not moved:
            sl = e; moved = True
        best = max(best, h[k]) if s == 1 else min(best, l[k])
        if moved and exit_kind in ("50 then trail", "half 50+trail"):
            sl = max(sl, best - tr) if s == 1 else min(sl, best + tr)
    legs.append((frac, day_last, c[day_last]))
    return legs


def main():
    import round34 as R34
    vix1 = pd.read_csv(os.path.join(DATA, "INDIAVIX_1m_upstox.csv.gz"), parse_dates=["ts"]).set_index("ts").close
    vix1 = vix1[~vix1.index.duplicated()].sort_index()
    rows = {}
    for sym in SYMS:
        F = OI.oi_features(sym)
        for tf in ("5m", "15m"):
            df = load(sym, tf)
            o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
            n = len(c)
            d = df.index.normalize()
            days = d.unique()
            dpos = {dd: np.flatnonzero(d == dd) for dd in days}
            hm = df.index.hour * 60 + df.index.minute
            Fx = F.reindex(days)
            trades = []
            for di, dd in enumerate(days):
                if dd not in F.index or di < 2:
                    continue
                bars = dpos[dd]
                if len(bars) < 20 or not (hm[bars] <= 15 * 60 + 15).any():
                    continue                           # special / short sessions (e.g. Muhurat)
                start = dpos[days[di - 2]][0]
                prev_close = c[bars[0] - 1]
                levels = []
                f = Fx.loc[dd]
                for lab, up, dn in (("OI wall", f.cw1, f.pw1), ("2nd OI wall", f.cw2, f.pw2)):
                    levels += [(lab, 1, up), (lab, -1, dn)]
                step = 100 if sym == "NIFTY" else 500
                for q in range(-3, 4):
                    L = (np.floor(prev_close / step) + q) * step
                    if L not in (f.cw1, f.pw1, f.cw2, f.pw2):
                        levels += [("100-pt level, no OI", 1 if L > prev_close else -1, L)]
                for lab, s, L in levels:
                    if not np.isfinite(L):
                        continue
                    for i in bars:
                        if hm[i] < 9 * 60 + 20 or hm[i] > 14 * 60 + 30 or i == bars[0]:
                            continue
                        brk = (c[i] > L and c[i - 1] <= L) if s == 1 else (c[i] < L and c[i - 1] >= L)
                        if not brk:
                            continue
                        nrej = rejections(h, l, c, L, s, start, i)
                        trades.append((dd, i, lab, s, L, nrej))
                        break                      # one trade per level per day (the first break)
            for (dd, i, lab, s, L, nrej) in trades:
                if i + 1 >= n:
                    continue
                lb = dpos[dd][hm[dpos[dd]] <= 15 * 60 + 15]
                if not len(lb):
                    continue
                last = lb[-1]
                if i + 1 > last:
                    continue
                for stop_kind in ("35 pts below the level", "breakout candle low"):
                    stop = L * (1 - s * STOPP) if stop_kind.startswith("35") else (l[i] if s == 1 else h[i])
                    if s * (o[i + 1] - stop) <= 0:
                        continue
                    for ex in ("full@50", "50 then trail", "half 50/90", "half 50+trail", "hold to close"):
                        legs = manage(o, h, l, c, i + 1, s, stop, ex, last)
                        for need in (0, 2, 3):
                            if lab != "OI wall" and need != 0 and lab == "100-pt level, no OI":
                                pass
                            if nrej < need:
                                continue
                            key = json.dumps({"tf": tf, "level": lab, "rejections": f">= {need}" if need else "any", "stop": stop_kind, "exit": ex})
                            rows.setdefault(key, []).append({"sym": sym, "date": dd, "i": i + 1, "s": s, "stop": stop, "legs": legs, "e": o[i + 1]})
            print(sym, tf, "signals", len(trades), flush=True)
            # price legs per key for this sym/tf
            for key, lst in rows.items():
                for t in lst:
                    if t.get("priced") or t["sym"] != sym or json.loads(key)["tf"] != tf:
                        continue
                    pnl, pts = 0.0, 0.0
                    for frac, k, xp in t["legs"]:
                        ck = (sym, tf, t["i"], k, round(xp, 2), t["s"], round(t["stop"], 2))
                        if ck in CACHE:
                            pnl += CACHE[ck] * frac; pts += frac * t["s"] * (xp - t["e"]) / t["e"] * 23000; continue
                        T = pd.DataFrame({"i": [t["i"]], "j": [k], "xs": [xp], "side": [t["s"]], "sl": [t["stop"]]})
                        p_in, p_out, risk, _ = R34.price(sym, {"idx": df.index, "c": o}, T, vix1)
                        pl, lots, chg = R34.money(sym, p_in, p_out, risk, SLIP)
                        CACHE[ck] = float(pl[0])
                        pnl += float(pl[0]) * frac
                        pts += frac * t["s"] * (xp - t["e"]) / t["e"] * 23000
                    t.update(priced=True, pnl=pnl, pts=pts)
    out = []
    for key, lst in rows.items():
        P = np.array([t["pnl"] for t in lst]); pts = np.array([t["pts"] for t in lst])
        yr = np.array([t["date"].year for t in lst]); sy = np.array([t["sym"] for t in lst])
        e = yr <= 2024
        out.append({"v": json.loads(key), "n": len(lst), "n_early": int(e.sum()), "n_late": int((~e).sum()),
                    "pts": round(float(pts.mean()), 1), "win": round(float((P > 0).mean() * 100), 1),
                    "rs": round(float(P.sum())), "rs_early": round(float(P[e].sum())), "rs_late": round(float(P[~e].sum())),
                    "per_trade": round(float(P.mean())), "years": {str(y): round(float(P[yr == y].sum())) for y in np.unique(yr)},
                    "per_index": {s_: round(float(P[sy == s_].sum())) for s_ in np.unique(sy)}})
    json.dump(out, open(os.path.join(HERE, "round70_results.json"), "w"), indent=0)
    print("variants", len(out))


if __name__ == "__main__":
    main()
