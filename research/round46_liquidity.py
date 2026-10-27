"""Round 46 (28 Oct 2026). Liquidity / SMC backtest: swing highs & lows, liquidity pools, sweeps, BOS / CHoCH.

Setup A  sweep reversal : a pool (PDH/PDL, opening range, swing, equal highs/lows) is wicked
                          through and the candle closes back inside -> trade the reversal,
                          optionally only after a 5m CHoCH (close beyond the last internal swing).
Setup B  BOS continuation: 15m trend up + 5m BOS up, then a minor swing low is swept -> long
                          (mirror for shorts).
Runs on the same harness as rounds 1-43 (engine.py): 5m Nifty / Bank Nifty candles 2023 -> Oct 2026,
option P&L on a 1-ITM option with VIX-based IV + real-price calibration, 0.5% slippage per fill,
full Indian F&O charges, Rs 2L capital, 1% risk. Gross edge is also measured in index R and
compared with a random-direction benchmark on the same entry bars and stops.
"""
from __future__ import annotations

import itertools
import json
import os
import random
import sys
from pathlib import Path

os.environ["FNO_CALIB"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine as E  # noqa: E402
import numpy as np  # noqa: E402

FIRST, LAST_DAY, LAST_AM = 9 * 60 + 30, 14 * 60 + 30, 11 * 60 + 15
EQ_TOL = 0.0004


def fractals(h, l, N):
    """confirmed swing points: list of (bar, confirm_bar, price)"""
    sh, sl = [], []
    for i in range(N, len(h) - N):
        if h[i] > max(h[i - N:i]) and h[i] >= max(h[i + 1:i + N + 1]):
            sh.append((i, i + N, h[i]))
        if l[i] < min(l[i - N:i]) and l[i] <= min(l[i + 1:i + N + 1]):
            sl.append((i, i + N, l[i]))
    return sh, sl


def unbroken_yday(y, N):
    """yesterday's swing highs/lows that price never traded through later that day"""
    sh, sl = fractals(y.h, y.l, N)
    hi = [p for (i, _, p) in sh if max(y.h[i + 1:], default=-1e9) < p]
    lo = [p for (i, _, p) in sl if min(y.l[i + 1:], default=1e9) > p]
    return hi, lo


def build_pools(d, y, N, which):
    """pools: dict(price, side(+1 = highs / buy-side liquidity, -1 = lows), kind, avail)"""
    pools = []
    add = lambda p, s, k, a: pools.append({"p": p, "s": s, "k": k, "a": a, "alive": True})
    if which in ("key", "all", "eq"):
        add(d.prev_high, 1, "pd", 0)
        add(d.prev_low, -1, "pd", 0)
    if which in ("key", "all"):
        add(max(d.h[:3]), 1, "or", 3)
        add(min(d.l[:3]), -1, "or", 3)
    sh, sl = fractals(d.h, d.l, N)
    yh, yl = unbroken_yday(y, N) if y is not None else ([], [])
    if which in ("swing", "all"):
        for p in yh:
            add(p, 1, "ysw", 0)
        for p in yl:
            add(p, -1, "ysw", 0)
        for (_, c, p) in sh:
            add(p, 1, "sw", c + 1)
        for (_, c, p) in sl:
            add(p, -1, "sw", c + 1)
    if which == "eq":   # equal highs / lows: two swings within 0.04% (today + unbroken yesterday)
        for side, today, yd in ((1, sh, yh), (-1, sl, yl)):
            pts = [(0, p) for p in yd] + [(c + 1, p) for (_, c, p) in today]
            for (a1, p1), (a2, p2) in itertools.combinations(pts, 2):
                if abs(p1 - p2) <= EQ_TOL * p1:
                    add(max(p1, p2) if side > 0 else min(p1, p2), side, "eq", max(a1, a2))
    return pools


def sweeps(d, pools, wmin):
    """walk bars; return list of (bar, side_of_trade, level). A pool dies once traded through."""
    out = []
    for i in range(len(d.t)):
        for pl in pools:
            if not pl["alive"] or i < pl["a"]:
                continue
            if pl["s"] > 0 and d.h[i] > pl["p"]:
                pl["alive"] = False
                if d.c[i] < pl["p"] and d.h[i] - pl["p"] >= wmin * d.atr[i]:
                    out.append((i, -1, pl["p"]))
            elif pl["s"] < 0 and d.l[i] < pl["p"]:
                pl["alive"] = False
                if d.c[i] > pl["p"] and pl["p"] - d.l[i] >= wmin * d.atr[i]:
                    out.append((i, 1, pl["p"]))
    out.sort()
    # several pools swept by one candle -> one event
    ded, seen = [], set()
    for e in out:
        if (e[0], e[1]) not in seen:
            seen.add((e[0], e[1]))
            ded.append(e)
    return ded


def opposite_target(d, pools_all, i, side, entry, risk):
    """nearest alive pool on the other side that is at least 1R away (draw on liquidity)"""
    cands = [pl["p"] for pl in pools_all if pl["a"] <= i and pl["s"] == side
             and side * (pl["p"] - entry) >= risk
             and (max(d.h[pl["a"]:i + 1]) < pl["p"] if side > 0 else min(d.l[pl["a"]:i + 1]) > pl["p"])]
    if not cands:
        return entry + side * 2 * risk
    return min(cands) if side > 0 else max(cands)


def make_sig(d, i, side, stop, tgt_mode, pools_all, tag):
    entry = d.c[i]
    risk = abs(entry - stop)
    a = d.atr[i]
    if risk < 0.3 * a or risk > 3 * a or side * (entry - stop) <= 0:
        return None
    if tgt_mode == "pool":
        tgt = opposite_target(d, pools_all, i, side, entry, risk)
    else:
        tgt = entry + side * tgt_mode * risk
    return E.Sig(i, side, stop, tgt, "", 0, 0, tag)


def signals_A(d, y, cfg):
    N, which, conf, htf, last = cfg["N"], cfg["pools"], cfg["confirm"], cfg["htf"], cfg["last"]
    pools = build_pools(d, y, N, which)
    ev = sweeps(d, pools, 0.1)
    tpools = build_pools(d, y, N, "all")          # used only to find the opposite-liquidity target
    ish, isl = fractals(d.h, d.l, 1)              # internal structure for CHoCH
    sigs = []
    for (s, side, lvl) in ev:
        if htf == "with" and d.st15[s] != side:
            continue
        if htf == "against" and d.st15[s] == side:
            continue
        ext = d.h[s] if side < 0 else d.l[s]
        if conf == "none":
            i = s
        else:
            pts = isl if side < 0 else ish            # short needs a close below the last internal low
            prev = [p for (_, c, p) in pts if c <= s]
            if not prev:
                continue
            ref = prev[-1]
            i = None
            for k in range(s + 1, min(s + 7, len(d.t))):
                if side < 0 and d.h[k] > ext or side > 0 and d.l[k] < ext:
                    break                              # sweep extreme taken out: setup invalid
                if side * (d.c[k] - ref) > 0:
                    i = k
                    break
            if i is None:
                continue
        if not (FIRST <= d.t[i] + 5 <= last):
            continue
        stop_ext = max(d.h[s:i + 1]) if side < 0 else min(d.l[s:i + 1])
        stop = stop_ext - side * 0.1 * d.atr[i]
        sg = make_sig(d, i, side, stop, cfg["tgt"], tpools, "A")
        if sg:
            sigs.append(sg)
    return sigs


def signals_B(d, y, cfg):
    N, last = cfg["N"], cfg["last"]
    sh, sl = fractals(d.h, d.l, N)
    ish, isl = fractals(d.h, d.l, 1)
    tpools = build_pools(d, y, N, "all")
    sigs = []
    bos_bar = {1: -99, -1: -99}
    for i in range(3, len(d.t)):
        for side, pts in ((1, sh), (-1, sl)):
            prev = [p for (_, c, p) in pts if c < i]
            if prev and side * (d.c[i] - prev[-1]) > 0 and side * (d.c[i - 1] - prev[-1]) <= 0:
                bos_bar[side] = i
        if not (FIRST <= d.t[i] + 5 <= last):
            continue
        side = d.st15[i]
        if i - bos_bar[side] > 12 or bos_bar[side] == i:
            continue
        pts = isl if side > 0 else ish            # minor swing on the pullback, formed after the BOS
        minor = [p for (b, c, p) in pts if c < i and b > bos_bar[side]]
        if not minor:
            continue
        m = minor[-1]
        swept = (d.l[i] < m < d.c[i]) if side > 0 else (d.h[i] > m > d.c[i])
        if not swept:
            continue
        stop = (d.l[i] if side > 0 else d.h[i]) - side * 0.1 * d.atr[i]
        sg = make_sig(d, i, side, stop, cfg["tgt"], tpools, "B")
        if sg:
            sigs.append(sg)
            bos_bar[side] = -99                      # one entry per BOS
    return sigs


def run_cfg(days, sym, cfg, rng):
    meta = E.META[sym]
    gen = signals_A if cfg["setup"] == "A" else signals_B
    trades = []
    for n_d, d in enumerate(days):
        if n_d == 0:
            continue
        sigs = gen(d, days[n_d - 1], cfg)
        busy, taken, losses = -1, 0, 0
        for s in sigs:
            if s.i <= busy or taken >= 2 or losses >= 2:
                continue
            k, px, why = E.simulate(d, s, meta, {})
            risk = abs(d.c[s.i] - s.stop)
            R = s.side * (px - d.c[s.i]) / risk
            # random-direction twin: same bar, same stop distance and R:R
            rs = rng.choice((1, -1))
            tR = abs(s.target - d.c[s.i]) / risk
            twin = E.Sig(s.i, rs, d.c[s.i] - rs * risk, d.c[s.i] + rs * tR * risk, "", 0, 0, "rnd")
            k2, px2, _ = E.simulate(d, twin, meta, {})
            rR = rs * (px2 - d.c[s.i]) / risk
            tr = E.trade_pnl(d, s, k, px, meta, {"otm": -1})
            busy = k
            taken += 1
            pnl = tr["pnl"] if tr else 0.0
            losses += pnl < 0
            trades.append({"date": str(d.d), "R": R, "rR": rR, "pnl": pnl, "sized": tr is not None,
                           "why": why, "side": s.side, "pts": s.side * (px - d.c[s.i])})
    return trades


def summarise(trades):
    if not trades:
        return None
    R = np.array([t["R"] for t in trades])
    rR = np.array([t["rR"] for t in trades])
    pnl = np.array([t["pnl"] for t in trades])
    eq = np.concatenate([[0], np.cumsum(pnl)])
    yr = {}
    for t in trades:
        yr[t["date"][:4]] = yr.get(t["date"][:4], 0) + t["pnl"]
    early = sum(t["pnl"] for t in trades if t["date"] < "2025")
    late = sum(t["pnl"] for t in trades if t["date"] >= "2025")
    w, l = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    return {"n": len(trades), "win": float((pnl > 0).mean() * 100), "idx_win": float((R > 0).mean() * 100),
            "avgR": float(R.mean()), "rndR": float(rR.mean()), "edgeR": float(R.mean() - rR.mean()),
            "net": float(pnl.sum()), "pf": float(w / l) if l else 99.0,
            "dd": float((np.maximum.accumulate(eq) - eq).max()), "y2324": float(early), "y2526": float(late),
            "years": {k: round(v) for k, v in sorted(yr.items())}}


def main():
    rng = random.Random(7)
    res = []
    for sym in ("NIFTY", "BANKNIFTY"):
        days = E.load_days(sym)
        print(sym, len(days), days[0].d, days[-1].d, flush=True)
        grid = []
        for N, pools, conf, htf, last, tgt in itertools.product(
                (2, 3), ("key", "swing", "all", "eq"), ("none", "choch"), ("none", "with", "against"),
                (LAST_DAY, LAST_AM), (1.5, 2, 3, "pool")):
            grid.append({"setup": "A", "N": N, "pools": pools, "confirm": conf, "htf": htf, "last": last, "tgt": tgt})
        for N, last, tgt in itertools.product((2, 3), (LAST_DAY, LAST_AM), (1.5, 2, 3, "pool")):
            grid.append({"setup": "B", "N": N, "last": last, "tgt": tgt})
        for g in grid:
            tr = run_cfg(days, sym, g, rng)
            sm = summarise(tr)
            if sm:
                res.append({"sym": sym, **g, **sm})
        print(sym, "done", len(res), flush=True)
    out = Path(__file__).resolve().parent / "round46_liquidity_results.json"
    out.write_text(json.dumps(res, indent=1, default=str))
    print("saved", out)


if __name__ == "__main__":
    main()
