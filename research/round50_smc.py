"""Round 50 (28 Oct 2026): every liquidity / SMC / ICT concept as a mechanical rule, Nifty + Bank Nifty + Sensex.

Setups (both directions, entries 09:30-14:30, max 2 trades a day, stop/target in index points):
  fvg        3-candle fair value gap (c1 high < c3 low), middle candle displacement >= 1 ATR; limit at the gap edge
             or its 50% (consequent encroachment) within 12 bars; stop beyond candle 1.
  ifvg       inverse FVG: an opposite FVG is closed through -> enter on that close; stop beyond the extreme since.
  ob         order block: last opposite candle before a >= 1 ATR displacement that breaks structure (BOS);
             limit at the OB edge (or 50%) within 20 bars; stop beyond the OB.
  breaker    a failed order block: price makes a new extreme past it, then closes back through the OB -> limit at the
             breaker edge within 12 bars; stop beyond the new extreme.
  sd         supply/demand: a base of 1-3 small candles (bodies <= 50% of range, base <= 1 ATR) then a >= 1.2 ATR
             departure candle; fresh zone, limit at the proximal edge on the first return (>= 3 bars later).
  model2022  ICT 2022 model: sell-side (buy-side) liquidity sweep of PDH/PDL, opening range, swings or equal H/L,
             then within 8 bars an FVG whose middle candle makes a market structure shift; limit at the FVG edge;
             stop beyond the sweep.
  silver     Silver Bullet, Indian windows 10:00-11:00 and 13:30-14:30: first displacement FVG formed inside the
             window, limit at its edge inside the window; stop beyond candle 1.
  judas      Judas swing: PDH/PDL or the 09:15-09:30 range is swept before 09:45, then a close through the last
             internal swing the other way before 10:30 -> enter on that close; stop beyond the sweep.
  ote        optimal trade entry: a >= 2 ATR leg that broke structure, limit at the 70.5% retracement within 20 bars
             (62-79% zone); stop beyond the leg's origin.
  cisd       liquidity sweep then change in state of delivery: a close back through the open of the candle run that
             delivered price into the sweep -> enter on that close; stop beyond the sweep.
  smt        SMT divergence: one index sweeps its last swing low (high) and closes back while its partner index
             (Nifty <-> Bank Nifty; Sensex <-> Nifty) does not take its own -> enter on the close; stop beyond.
Variants: targets 1.5R / 2R / 3R / next liquidity pool (>= 1R, else 2R); 15m trend filter none / with;
premium-discount filter none / on (longs only below the day's range midpoint, shorts above);
time none / killzones (09:15-10:45, 13:30-15:00); fvg / ob also edge vs 50% entry.
Each trade: index R, a random-direction twin (same bar, stop distance, R:R), and the 1-ITM option P&L on the
engine.py harness (calibrated premiums, 0.5% slippage per fill, full charges, 1 lot).
Run: python round50_smc.py -> round50_smc_results.json
"""
from __future__ import annotations
import itertools, json, os, random, sys
from pathlib import Path
os.environ["FNO_CALIB"] = "1"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import numpy as np, pandas as pd
import engine as E

E.META.setdefault("SENSEX", {"lot": 20, "step": 100, "kind": "weekly", "iv_mult": 1.0})
SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
PARTNER = {"NIFTY": "BANKNIFTY", "BANKNIFTY": "NIFTY", "SENSEX": "NIFTY"}
FIRST, LAST = 9 * 60 + 30, 14 * 60 + 30


def ensure_sensex_5m():
    p = HERE / "data" / "SENSEX_5m_upstox.csv.gz"
    if p.exists():
        return
    a = pd.read_csv(HERE / "data" / "SENSEX_1m_upstox.csv.gz", parse_dates=["ts"]).set_index("ts").between_time("09:15", "15:29")
    b = a.resample("5min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    b.reset_index().to_csv(p, index=False)


def fractals(h, l, N):
    sh, sl = [], []
    for i in range(N, len(h) - N):
        if h[i] > max(h[i - N:i]) and h[i] >= max(h[i + 1:i + N + 1]):
            sh.append((i, i + N, h[i]))
        if l[i] < min(l[i - N:i]) and l[i] <= min(l[i + 1:i + N + 1]):
            sl.append((i, i + N, l[i]))
    return sh, sl


def last_conf(pts, i):
    v = [p for (_, c, p) in pts if c < i]
    return v[-1] if v else None


class Ctx:
    def __init__(self, d, y):
        self.d, self.y = d, y
        self.h, self.l, self.c, self.o, self.a = d.h, d.l, d.c, d.o, d.atr
        self.n = len(d.c)
        self.sh, self.sl = fractals(d.h, d.l, 2)
        self.ih, self.il = fractals(d.h, d.l, 1)
        yh, yl = fractals(y.h, y.l, 2)
        self.yh = [p for (i, _, p) in yh if max(y.h[i + 1:], default=-1e9) < p]
        self.yl = [p for (i, _, p) in yl if min(y.l[i + 1:], default=1e9) > p]

    def pools(self, side):
        """(price, available_from_bar) of liquidity on one side (+1 = highs/BSL, -1 = lows/SSL)"""
        d = self.d
        P = [(d.prev_high if side > 0 else d.prev_low, 0), ((max if side > 0 else min)(self.h[:3] if side > 0 else self.l[:3]), 3)]
        P += [(p, 0) for p in (self.yh if side > 0 else self.yl)]
        P += [(p, c + 1) for (_, c, p) in (self.sh if side > 0 else self.sl)]
        return P

    def untouched_pool(self, i, side, entry, risk):
        best = None
        for p, a in self.pools(side):
            if a > i or side * (p - entry) < risk:
                continue
            seg = self.h[a:i + 1] if side > 0 else self.l[a:i + 1]
            if len(seg) and (max(seg) >= p if side > 0 else min(seg) <= p):
                continue
            if best is None or side * (p - best) < 0:
                best = p
        return best

    def sweeps(self, wmin=0.1):
        """(bar, side_of_trade, extreme): pool on one side wicked through and closed back inside"""
        out = []
        for side in (1, -1):
            pools = self.pools(side)
            # equal highs / lows count as pools too (two swings within 0.04%)
            sw = [(c + 1, p) for (_, c, p) in (self.sh if side > 0 else self.sl)]
            for (a1, p1), (a2, p2) in itertools.combinations(sw, 2):
                if abs(p1 - p2) <= 0.0004 * p1:
                    pools.append(((max if side > 0 else min)(p1, p2), max(a1, a2)))
            for p, a in pools:
                for k in range(max(a, 1), self.n):
                    if (self.h[k] > p) if side > 0 else (self.l[k] < p):
                        back = (self.c[k] < p) if side > 0 else (self.c[k] > p)
                        if back and abs((self.h[k] if side > 0 else self.l[k]) - p) >= wmin * self.a[k]:
                            out.append((k, -side, self.h[k] if side > 0 else self.l[k]))
                        break
        out = sorted(set(out))
        return out


def limit_fill(cx, start, end, side, px, stop):
    """first bar in [start, end) whose range reaches px; None if price closes beyond the stop first"""
    for k in range(start, min(end, cx.n)):
        if (cx.l[k] <= px) if side > 0 else (cx.h[k] >= px):
            fill = min(cx.o[k], px) if side > 0 else max(cx.o[k], px)
            hit = (cx.l[k] <= stop) if side > 0 else (cx.h[k] >= stop)
            return k, fill, hit          # hit: filled and stopped in the same bar -> counted as a full loss
        if side * (cx.c[k] - stop) <= 0:
            return None
    return None


def fvgs(cx, disp):
    out = []
    for k in range(2, cx.n):
        body = abs(cx.c[k - 1] - cx.o[k - 1])
        if body < disp * cx.a[k - 1]:
            continue
        if cx.l[k] > cx.h[k - 2] and cx.c[k - 1] > cx.o[k - 1]:
            out.append((k, 1, cx.l[k], cx.h[k - 2], cx.l[k - 2]))      # top edge, bottom edge, candle-1 low
        if cx.h[k] < cx.l[k - 2] and cx.c[k - 1] < cx.o[k - 1]:
            out.append((k, -1, cx.h[k], cx.l[k - 2], cx.h[k - 2]))
    return out


def gen(name, cx, partner=None):
    """raw signals: list of (i, side, px, stop)"""
    S, c, h, l, o, a, n = [], cx.c, cx.h, cx.l, cx.o, cx.a, cx.n
    if name in ("fvg", "fvg_ce"):
        for k, s, edge, far, c1 in fvgs(cx, 1.0):
            px = edge if name == "fvg" else (edge + far) / 2
            stop = c1 - s * 0.1 * a[k]
            f = limit_fill(cx, k + 1, k + 13, s, px, stop)
            if f:
                S.append((f[0], s, f[1], stop, f[2]))
    elif name == "ifvg":
        for k, s, edge, far, c1 in fvgs(cx, 0.5):
            lvl = far if s > 0 else far      # opposite side: the gap's far edge (bullish gap bottom = c1 high)
            for j in range(k + 1, min(k + 13, n)):
                if -s * (c[j] - (h[k - 2] if s > 0 else l[k - 2])) > 0:   # closed through the whole gap
                    ext = (max(h[k:j + 1]) if s > 0 else min(l[k:j + 1]))
                    S.append((j, -s, c[j], ext + s * 0.1 * a[j]))
                    break
    elif name in ("ob", "ob_ce"):
        for j in range(3, n):
            body = c[j] - o[j]
            if abs(body) < a[j]:
                continue
            s = 1 if body > 0 else -1
            ref = last_conf(cx.sh if s > 0 else cx.sl, j)
            if ref is None or s * (c[j] - ref) <= 0:
                continue
            ob = next((k for k in range(j - 1, max(j - 6, -1), -1) if s * (c[k] - o[k]) < 0), None)
            if ob is None:
                continue
            top, bot = (h[ob], l[ob]) if s > 0 else (l[ob], h[ob])
            px = top if name == "ob" else (top + bot) / 2
            stop = bot - s * 0.1 * a[j]
            f = limit_fill(cx, j + 1, j + 21, s, px, stop)
            if f:
                S.append((f[0], s, f[1], stop, f[2]))
    elif name == "breaker":
        for j in range(3, n):
            body = c[j] - o[j]
            if abs(body) < a[j]:
                continue
            s = 1 if body > 0 else -1           # displacement direction of the original OB
            ob = next((k for k in range(j - 1, max(j - 6, -1), -1) if s * (c[k] - o[k]) < 0), None)
            if ob is None:
                continue
            # OB fails: price later trades beyond the displacement extreme then closes back through the OB's far side
            ext_lvl = h[j] if s > 0 else l[j]
            made = None
            for k in range(j + 1, min(j + 25, n)):
                if made is None and s * ((h[k] if s > 0 else l[k]) - ext_lvl) > 0:
                    made = k
                far = l[ob] if s > 0 else h[ob]
                if made is not None and -s * (c[k] - far) > 0:
                    edge = far
                    swing = max(h[j:k + 1]) if s > 0 else min(l[j:k + 1])
                    stop = swing + s * 0.1 * a[k]
                    f = limit_fill(cx, k + 1, k + 13, -s, edge, stop)
                    if f:
                        S.append((f[0], -s, f[1], stop, f[2]))
                    break
    elif name == "sd":
        for j in range(4, n):
            body = c[j] - o[j]
            if abs(body) < 1.2 * a[j]:
                continue
            s = 1 if body > 0 else -1
            for blen in (1, 2, 3):
                b = range(j - blen, j)
                if min(b) < 0:
                    break
                if all(abs(c[k] - o[k]) <= 0.5 * (h[k] - l[k] + 1e-9) for k in b) and max(h[k] for k in b) - min(l[k] for k in b) <= a[j]:
                    zt, zb = (max(h[k] for k in b), min(l[k] for k in b)) if s > 0 else (min(l[k] for k in b), max(h[k] for k in b))
                    stop = zb - s * 0.1 * a[j]
                    f = limit_fill(cx, j + 3, n, s, zt, stop)
                    # zone must still be fresh until the fill bar (no touch between j+1 and j+2)
                    if f and not any(((l[k] <= zt) if s > 0 else (h[k] >= zt)) for k in range(j + 1, min(j + 3, n))):
                        S.append((f[0], s, f[1], stop, f[2]))
                    break
    elif name == "model2022":
        for k, s, ext in cx.sweeps():
            for g in fvgs(cx, 0.8):
                gk, gs, edge, far, c1 = g
                if gs != s or not (k < gk <= k + 8):
                    continue
                ref = last_conf(cx.ih if s > 0 else cx.il, k)
                if ref is None or s * (c[gk - 1] - ref) <= 0:
                    continue
                stop = ext - s * 0.1 * a[gk]
                f = limit_fill(cx, gk + 1, gk + 9, s, edge, stop)
                if f:
                    S.append((f[0], s, f[1], stop, f[2]))
                break
    elif name == "silver":
        for w0, w1 in ((10 * 60, 11 * 60), (13 * 60 + 30, 14 * 60 + 30)):
            for k, s, edge, far, c1 in fvgs(cx, 0.5):
                t = cx.d.t[k]
                if not (w0 <= t < w1):
                    continue
                stop = c1 - s * 0.1 * a[k]
                last = next((j for j in range(k, n) if cx.d.t[j] >= w1), n)
                f = limit_fill(cx, k + 1, last, s, edge, stop)
                if f:
                    S.append((f[0], s, f[1], stop, f[2]))
                break
    elif name == "judas":
        or_h, or_l = max(h[:3]), min(l[:3])
        for side_lvls in ((1, (cx.d.prev_high, or_h)), (-1, (cx.d.prev_low, or_l))):
            up, lv = side_lvls
            done = False
            for k in range(0, n):
                if cx.d.t[k] >= 9 * 60 + 45 or done:
                    break
                for p in lv:
                    if k < 3 and p in (or_h, or_l):
                        continue
                    if (h[k] > p) if up > 0 else (l[k] < p):
                        ext = max(h[:k + 1]) if up > 0 else min(l[:k + 1])
                        s = -up
                        for j in range(k + 1, n):
                            if cx.d.t[j] >= 10 * 60 + 30:
                                break
                            if (h[j] > ext) if up > 0 else (l[j] < ext):
                                ext = h[j] if up > 0 else l[j]
                            ref = last_conf(cx.il if s < 0 else cx.ih, j)
                            if ref is not None and s * (c[j] - ref) > 0:
                                S.append((j, s, c[j], ext - s * 0.1 * a[j]))
                                break
                        done = True
                        break
    elif name == "ote":
        for s, P0, P1 in ((1, cx.sl, cx.sh), (-1, cx.sh, cx.sl)):
            for (i0, c0, p0) in P0:
                nxt = [(i1, c1_, p1) for (i1, c1_, p1) in P1 if i1 > i0]
                if not nxt:
                    continue
                i1, c1_, p1 = nxt[0]
                leg = s * (p1 - p0)
                prior = [p for (i, c_, p) in P1 if c_ < i0]
                if leg < 2 * a[i1] or not prior or s * (p1 - prior[-1]) <= 0:
                    continue
                px = p1 - s * 0.705 * leg
                stop = p0 - s * 0.1 * a[i1]
                f = limit_fill(cx, c1_ + 1, c1_ + 21, s, px, stop)
                if f:
                    S.append((f[0], s, f[1], stop, f[2]))
    elif name == "cisd":
        for k, s, ext in cx.sweeps():
            # the run of candles that delivered price into the sweep (opposite colour to the trade)
            j = k
            while j - 1 >= 0 and -s * (c[j - 1] - o[j - 1]) > 0:
                j -= 1
            if -s * (c[k] - o[k]) > 0:
                run_open = o[j]
            else:
                if j - 1 < 0:
                    continue
                j2 = k - 1
                while j2 - 1 >= 0 and -s * (c[j2 - 1] - o[j2 - 1]) > 0:
                    j2 -= 1
                run_open = o[j2]
            x = ext
            for m in range(k + 1, min(k + 9, n)):
                x = (min(x, l[m]) if s > 0 else max(x, h[m]))
                if s * (c[m] - run_open) > 0:
                    S.append((m, s, c[m], x - s * 0.1 * a[m]))
                    break
    elif name == "smt" and partner is not None:
        P = partner
        tmap = {t: i for i, t in enumerate(P.d.t)}
        for s, own, oth in ((1, cx.sl, P.sl), (-1, cx.sh, P.sh)):
            for k in range(3, n):
                ref = last_conf(own, k)
                if ref is None or not ((l[k] < ref < c[k]) if s > 0 else (h[k] > ref > c[k])):
                    continue
                pk = tmap.get(cx.d.t[k])
                if pk is None:
                    continue
                pref = last_conf(oth, pk)
                if pref is None:
                    continue
                held = min(P.l[max(0, pk - 2):pk + 1]) > pref if s > 0 else max(P.h[max(0, pk - 2):pk + 1]) < pref
                if held:
                    S.append((k, s, c[k], (l[k] if s > 0 else h[k]) - s * 0.1 * a[k]))
    S = [x if len(x) == 5 else x + (False,) for x in S]
    S = [x for x in S if FIRST <= cx.d.t[x[0]] + 5 <= LAST and x[1] * (x[2] - x[3]) > 0]
    return sorted(set(S))


SETUPS = ["fvg", "fvg_ce", "ifvg", "ob", "ob_ce", "breaker", "sd", "model2022", "silver", "judas", "ote", "cisd", "smt"]


def precompute(sym, days, pdays):
    pidx = {d.d: i for i, d in enumerate(pdays)} if pdays else {}
    raw = {s: {} for s in SETUPS}
    ctx = {}
    for nd in range(1, len(days)):
        d = days[nd]
        cx = Ctx(d, days[nd - 1])
        ctx[nd] = cx
        pc = None
        if pdays and d.d in pidx and pidx[d.d] > 0:
            j = pidx[d.d]; pc = Ctx(pdays[j], pdays[j - 1])
        for s in SETUPS:
            sig = gen(s, cx, pc)
            if sig:
                raw[s][nd] = sig
    return raw, ctx


def run_variant(sym, days, raw, ctx, setup, tgt, htf, pd_, kz, rng):
    meta = E.META[sym]; out = []
    for nd, sigs in raw[setup].items():
        d, cx = days[nd], ctx[nd]
        busy, taken = -1, 0
        for (i, s, px, stop, hit) in sigs:
            if i <= busy or taken >= 2:
                continue
            if htf and d.st15[i] != s:
                continue
            if pd_:
                mid = (max(cx.h[:i + 1]) + min(cx.l[:i + 1])) / 2
                if s * (px - mid) > 0:
                    continue
            if kz:
                t = d.t[i] + 5
                if not (t <= 10 * 60 + 45 or 13 * 60 + 30 <= t):
                    continue
            risk = abs(px - stop)
            if risk < 0.3 * d.atr[i] or risk > 4 * d.atr[i]:
                continue
            if tgt == "pool":
                p = cx.untouched_pool(i, s, px, risk)
                T = p if p is not None else px + s * 2 * risk
            else:
                T = px + s * tgt * risk
            sg = E.Sig(i, s, stop, T, "", 0, 0, setup, px)
            k, ex, _ = (i, stop, "stop") if hit else E.simulate(d, sg, meta, {})
            R = s * (ex - px) / risk
            rs = rng.choice((1, -1))
            tw = E.Sig(i, rs, px - rs * risk, px + rs * abs(T - px), "", 0, 0, "rnd", px)
            k2, ex2, _ = E.simulate(d, tw, meta, {})
            if px != d.c[i] and ((d.l[i] <= px - risk) if rs > 0 else (d.h[i] >= px + risk)):
                ex2 = px - rs * risk                     # limit fill: twin also stopped inside the fill bar
            tr = E.trade_pnl(d, sg, k, ex, meta, {"otm": -1, "one_lot": True})
            busy, taken = k, taken + 1
            out.append({"date": str(d.d), "R": R, "rR": rs * (ex2 - px) / risk, "pnl": tr["pnl"] if tr else 0.0,
                        "t": d.t[i] + 5, "side": s})
    return out


def summ(tr):
    if not tr:
        return None
    df = pd.DataFrame(tr)
    e, l = df[df.date < "2025"], df[df.date >= "2025"]
    w, ls = df.pnl[df.pnl > 0].sum(), -df.pnl[df.pnl < 0].sum()
    eq = np.concatenate([[0], df.pnl.cumsum().values])
    return {"n": len(df), "avgR": float(df.R.mean()), "rndR": float(df.rR.mean()), "seR": float(df.R.std() / np.sqrt(len(df))) if len(df) > 1 else 0.0,
            "winR": float((df.R > 0).mean() * 100), "net": float(df.pnl.sum()), "pf": float(w / ls) if ls else 99.0,
            "dd": float((np.maximum.accumulate(eq) - eq).max()), "early": float(e.pnl.sum()), "late": float(l.pnl.sum()),
            "n_early": len(e), "n_late": len(l), "R_early": float(e.R.mean()) if len(e) else 0.0, "R_late": float(l.R.mean()) if len(l) else 0.0,
            "years": {k: round(float(v)) for k, v in df.groupby(df.date.str[:4]).pnl.sum().items()}}


def main():
    ensure_sensex_5m()
    rng = random.Random(47)
    days = {s: E.load_days(s) for s in SYMS}
    for s in SYMS:
        print(s, len(days[s]), days[s][0].d, days[s][-1].d, flush=True)
    res, trades_keep = [], {}
    for sym in SYMS:
        raw, ctx = precompute(sym, days[sym], days[PARTNER[sym]])
        print(sym, "signals", {k: sum(len(v) for v in raw[k].values()) for k in SETUPS}, flush=True)
        for setup in SETUPS:
            for tgt, htf, pd_, kz in itertools.product((1.5, 2, 3, "pool"), (False, True), (False, True), (False, True)):
                tr = run_variant(sym, days[sym], raw, ctx, setup, tgt, htf, pd_, kz, rng)
                sm = summ(tr)
                if sm:
                    res.append({"sym": sym, "setup": setup, "tgt": tgt, "htf": htf, "pd": pd_, "kz": kz, **sm})
                    trades_keep[f"{sym}|{setup}|{tgt}|{htf}|{pd_}|{kz}"] = tr
        print(sym, "done", len(res), flush=True)
    json.dump(res, open(HERE / "round50_smc_results.json", "w"), indent=0, default=str)
    import pickle
    pickle.dump(trades_keep, open("/tmp/round50_trades.pkl", "wb"))
    print("saved")


if __name__ == "__main__":
    main()
