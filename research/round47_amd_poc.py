"""Round 47 (28 Oct 2026). Reel setup backtest: trend (HH/HL) -> consolidation -> fixed-range volume profile POC ->
manipulation below the box (sweep + close back inside) -> breakout -> long (mirror for shorts).

Entries tested: E1 reclaim (close back inside the box after the sweep), E2 breakout close,
E3 limit at the box POC after the breakout (retest). Stop below the manipulation extreme.
Control: plain breakout of the box in the trend direction with no manipulation first.

Stocks: 10 large caps, 5m with real volume -> volume POC, cash intraday, net R after costs.
Indices: Nifty / Bank Nifty 5m (no volume) -> time-at-price POC, 1-ITM option via engine.py costs.
Gross edge is measured against a random-direction twin trade (same bar, stop distance, R:R).
"""
from __future__ import annotations

import itertools
import json
import os
import random
import sys
from pathlib import Path

os.environ["FNO_CALIB"] = "1"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine as E  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

STOCKS = ["RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS", "ITC", "LT", "AXISBANK", "BHARTIARTL"]
FIRST, LAST, SQ = 9 * 60 + 30, 14 * 60 + 30, 15 * 60 + 15
STOCK_COST = 0.0009          # round trip: STT/stamp/exch/GST ~0.05% + 0.02% slippage each side


class D:  # light day container
    def __init__(self, t, o, h, l, c, v, atr):
        self.t, self.o, self.h, self.l, self.c, self.v, self.atr = t, o, h, l, c, v, atr


def stock_days(sym):
    df = pd.read_csv(HERE / "data" / f"{sym}_5m_upstox.csv.gz", parse_dates=["ts"]).set_index("ts")
    tr = pd.concat([df.high - df.low, (df.high - df.close.shift()).abs(), (df.low - df.close.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    out = []
    for _, g in df.groupby(df.index.date):
        if len(g) < 60:
            continue
        out.append(D([x.hour * 60 + x.minute for x in g.index], *(g[k].tolist() for k in ("open", "high", "low", "close", "volume", "atr"))))
    return out


def fractals(h, l, N=3):
    sh, sl = [], []
    for i in range(N, len(h) - N):
        if h[i] > max(h[i - N:i]) and h[i] >= max(h[i + 1:i + N + 1]):
            sh.append((i + N, h[i]))
        if l[i] < min(l[i - N:i]) and l[i] <= min(l[i + 1:i + N + 1]):
            sl.append((i + N, l[i]))
    return sh, sl


def trend_at(sh, sl, i):
    """+1 if last two confirmed swing highs AND lows are rising (HH + HL), -1 if both falling, else 0"""
    hs = [p for (c, p) in sh if c <= i][-2:]
    ls = [p for (c, p) in sl if c <= i][-2:]
    if len(hs) < 2 or len(ls) < 2:
        return 0
    if hs[1] > hs[0] and ls[1] > ls[0]:
        return 1
    if hs[1] < hs[0] and ls[1] < ls[0]:
        return -1
    return 0


def poc(h, l, v, lo, hi, use_vol, bins=30):
    edges = np.linspace(lo, hi, bins + 1)
    w = np.zeros(bins)
    for a, b, vol in zip(h, l, v):
        top, bot = a, b
        i0 = max(0, np.searchsorted(edges, bot, "right") - 1)
        i1 = min(bins - 1, np.searchsorted(edges, top, "left") - 1)
        if i1 < i0:
            i1 = i0
        w[i0:i1 + 1] += (vol if use_vol else 1.0) / (i1 - i0 + 1)
    k = int(np.argmax(w))
    return (edges[k] + edges[k + 1]) / 2


def setups(H, L, C, O, V, A, T, start, cfg, use_vol):
    """scan today's bars (index >= start) of the 2-day buffer; return trade dicts (entry idx, side, px, stop, kind)"""
    K, m, need_trend, mode = cfg["K"], cfg["m"], cfg["trend"], cfg["mode"]
    sh, sl = fractals(H, L)
    n, out, b = len(C), [], start + K - 1
    while b < n - 2:
        lo, hi = min(L[b - K + 1:b + 1]), max(H[b - K + 1:b + 1])
        if hi - lo > m * A[b] or b - K + 1 < start:
            b += 1
            continue
        side = trend_at(sh, sl, b - K + 1) if need_trend else 0
        if need_trend and side == 0:
            b += 1
            continue
        e = b                                        # extend the box while bars stay inside it
        while e + 1 < n and H[e + 1] <= hi and L[e + 1] >= lo:
            e += 1
        P = poc(H[b - K + 1:e + 1], L[b - K + 1:e + 1], V[b - K + 1:e + 1], lo, hi, use_vol)
        j = e + 1
        if j >= n:
            break
        up_break, dn_break = H[j] > hi, L[j] < lo
        sides = [side] if need_trend else [1, -1]
        nxt = j + 1
        for sd in sides:
            box_out = (lo if sd > 0 else hi)        # the side manipulation should hit
            box_go = (hi if sd > 0 else lo)          # the breakout side
            swept = dn_break if sd > 0 else up_break
            if mode == "plain":                      # control: breakout in trend direction, no sweep
                for k in range(j, min(j + 6, n)):
                    if sd * (C[k] - box_go) > 0:
                        out.append({"i": k, "side": sd, "px": C[k], "stop": box_out - sd * 0.1 * A[k], "kind": "plain"})
                        nxt = k + 1
                        break
                    if sd * (C[k] - box_out) < 0:
                        break
                continue
            if not swept:
                continue
            ext, rec = (L[j], None) if sd > 0 else (H[j], None)
            for k in range(j, min(j + 4, n)):        # close back inside within 3 bars
                ext = min(ext, L[k]) if sd > 0 else max(ext, H[k])
                if sd * (C[k] - box_out) > 0:
                    rec = k
                    break
            if rec is None:
                continue
            stop = ext - sd * 0.1 * A[rec]
            if mode == "reclaim":
                out.append({"i": rec, "side": sd, "px": C[rec], "stop": stop, "kind": "reclaim"})
                nxt = rec + 1
                continue
            brk = None
            for k in range(rec + 1, min(rec + 13, n)):
                if sd * (L[k] - ext) < 0 if sd > 0 else sd * (H[k] - ext) < 0:
                    break                            # new extreme beyond manipulation: invalid
                if sd * (C[k] - box_go) > 0:
                    brk = k
                    break
            if brk is None:
                continue
            if mode == "breakout":
                out.append({"i": brk, "side": sd, "px": C[brk], "stop": stop, "kind": "breakout"})
                nxt = brk + 1
                continue
            for k in range(brk + 1, min(brk + 13, n)):   # mode == "poc": limit at POC after the breakout
                if (L[k] <= P) if sd > 0 else (H[k] >= P):
                    px = min(O[k], P) if sd > 0 else max(O[k], P)
                    out.append({"i": k, "side": sd, "px": px, "stop": stop, "kind": "poc"})
                    nxt = k + 1
                    break
                if sd * (C[k] - stop) < 0:
                    break
        b = max(nxt, e + 1)
    return [s for s in out if FIRST <= T[s["i"]] + 5 <= LAST]


def walk(H, L, C, O, T, i, side, px, stop, tgt):
    if px != C[i] and ((L[i] <= stop) if side > 0 else (H[i] >= stop)):
        return i, stop                               # limit fill and stop in the same bar: assume stopped
    for k in range(i + 1, len(C)):
        if (L[k] <= stop) if side > 0 else (H[k] >= stop):
            return k, (O[k] if side * (O[k] - stop) < 0 else stop)
        if (H[k] >= tgt) if side > 0 else (L[k] <= tgt):
            return k, (O[k] if side * (O[k] - tgt) > 0 else tgt)
        if T[k] + 5 >= SQ:
            return k, C[k]
    return len(C) - 1, C[-1]


def run(days, cfg, rng, index_sym=None, edays=None):
    res = []
    for n_d in range(1, len(days)):
        y, d = days[n_d - 1], days[n_d]
        cat = lambda a, b: list(a) + list(b)
        H, L, C, O = cat(y.h, d.h), cat(y.l, d.l), cat(y.c, d.c), cat(y.o, d.o)
        V = cat(getattr(y, "v", [1] * len(y.c)), getattr(d, "v", [1] * len(d.c)))
        A, T = cat(y.atr, d.atr), [-1] * len(y.c) + list(d.t)
        s0 = len(y.c)
        busy, taken = -1, 0
        for s in setups(H, L, C, O, V, A, T, s0, cfg, index_sym is None):
            if s["i"] <= busy or taken >= 2:
                continue
            risk = abs(s["px"] - s["stop"])
            if risk < 0.3 * A[s["i"]] or risk > 4 * A[s["i"]] or s["side"] * (s["px"] - s["stop"]) <= 0:
                continue
            tgt = s["px"] + s["side"] * cfg["tgt"] * risk
            k, ex = walk(H, L, C, O, T, s["i"], s["side"], s["px"], s["stop"], tgt)
            R = s["side"] * (ex - s["px"]) / risk
            rs = rng.choice((1, -1))
            k2, ex2 = walk(H, L, C, O, T, s["i"], rs, s["px"], s["px"] - rs * risk, s["px"] + rs * cfg["tgt"] * risk)
            rR = rs * (ex2 - s["px"]) / risk
            busy, taken = k, taken + 1
            row = {"date": str(getattr(d, "d", "")) or str(n_d), "R": R, "rR": rR, "risk_pct": risk / s["px"]}
            if index_sym:
                ed = edays[n_d]
                sig = E.Sig(s["i"] - s0, s["side"], s["stop"], tgt, "", 0, 0, "amd", s["px"])
                tr = E.trade_pnl(ed, sig, k - s0, ex, E.META[index_sym], {"otm": -1})
                row["pnl"] = tr["pnl"] if tr else 0.0
                row["date"] = str(ed.d)
            else:
                row["netR"] = R - STOCK_COST / (risk / s["px"])
            res.append(row)
    return res


def grid():
    for K, m, tr, mode, tgt in itertools.product((6, 12), (1.5, 2.5), (True, False),
                                                 ("reclaim", "breakout", "poc", "plain"), (1.5, 2, 3)):
        yield {"K": K, "m": m, "trend": tr, "mode": mode, "tgt": tgt}


def main():
    rng = random.Random(11)
    out = []
    sdays = {s: stock_days(s) for s in STOCKS}
    # attach dates for stock days
    for s in STOCKS:
        df = pd.read_csv(HERE / "data" / f"{s}_5m_upstox.csv.gz", parse_dates=["ts"])
        ds = [k for k, g in df.groupby(df.ts.dt.date) if len(g) >= 60]
        for dd, day in zip(ds, sdays[s]):
            day.d = dd
    for cfg in grid():
        rows = []
        for s in STOCKS:
            rows += [dict(r, sym=s) for r in run(sdays[s], cfg, rng)]
        out.append({"market": "stocks", **cfg, "trades": rows})
    print("stocks done", flush=True)
    for sym in ("NIFTY", "BANKNIFTY"):
        edays = E.load_days(sym)
        days = [D(d.t, d.o, d.h, d.l, d.c, [1] * len(d.c), d.atr) for d in edays]
        for dd, ed in zip(days, edays):
            dd.d = ed.d
        for cfg in grid():
            out.append({"market": sym, **cfg, "trades": run(days, cfg, rng, sym, edays)})
        print(sym, "done", flush=True)
    (HERE / "round47_amd_poc_results.json").write_text(json.dumps(out, default=str))
    print("saved")


if __name__ == "__main__":
    main()
