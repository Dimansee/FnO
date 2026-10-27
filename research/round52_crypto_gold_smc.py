"""Round 52 (28 Oct 2026): liquidity / SMC / ICT concepts on crypto and gold (24-hour markets).

Same mechanical rules as round 50 (FVG, IFVG, order block, breaker, supply/demand, ICT 2022 model, OTE, CISD, SMT), plus:
  sweep        liquidity sweep reversal: a pool (previous UTC-day high/low, Asia range 00:00-07:00 UTC, unbroken swings,
               equal highs/lows) is wicked through and the bar closes back inside -> enter on that close
  sweep_choch  the same, entered only after a close through the last internal swing the other way (within 6 bars)
  asia_raid    Judas / London raid: the Asia range is swept between 07:00 and 10:00 UTC, then a CHoCH -> reversal
  silver       Silver Bullet in New York time: first FVG formed 07-08, 14-15 or 18-19 UTC, limit at its edge in the window
  pool_break   the OPPOSITE bet (academic evidence says breaks continue): a bar CLOSES beyond a pool -> trade the breakout
Variants: target 1.5R / 2R / 3R / next untouched pool; trend filter none / with (close vs 200-bar EMA); killzones none /
London 07-10 UTC + New York 12-16 UTC. Max hold 24 hours, max 2 trades per UTC day. A limit fill that also hits the stop in
the same bar is a full loss. Costs: crypto 16 bps round trip (3 bps slippage + 5 bps fee a side, as in the app), gold 10 bps
(2 + 3 bps a side, as in the gold book), subtracted in R using each trade's own stop distance. Random-direction twin on
every trade. Design = first half of each series, holdout = second half.
Data (research-data branch, research/data/global): crypto 5m BTC ETH SOL BNB XRP (Jan 2024 - Oct 2026), crypto 1h 10 coins
(Jan 2023 - Oct 2026), XAUUSD 1h (Jun 2024 - Oct 2026), PAXG 1h (Aug 2020 - Oct 2026).
Run: python round52_crypto_gold_smc.py <data_dir> -> round52_results.json
"""
from __future__ import annotations
import itertools, json, os, random, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import numpy as np, pandas as pd
import round50_smc as M

M.FIRST, M.LAST = -10, 10 ** 6
DATA = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "data" / "global"
COINS1H = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TONUSDT"]
COINS5M = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
SETUPS = ["sweep", "sweep_choch", "asia_raid", "pool_break", "fvg", "fvg_ce", "ifvg", "ob", "ob_ce", "breaker", "sd",
          "model2022", "silver", "ote", "cisd", "smt"]


class Day:
    def __init__(self, d, t, o, h, l, c, a, ph, pl):
        self.d, self.t, self.o, self.h, self.l, self.c, self.atr = d, t, o, h, l, c, a
        self.prev_high, self.prev_low = ph, pl


class Ctx(M.Ctx):
    """Opening-range pool replaced by the Asia session range (00:00-07:00 UTC), usable from 07:00 UTC."""
    def pools(self, side):
        d = self.d
        P = [(d.prev_high if side > 0 else d.prev_low, 0)]
        asia = [k for k, t in enumerate(d.t) if t < 7 * 60]
        if asia and len(asia) < self.n:
            hi = max(self.h[k] for k in asia) if side > 0 else min(self.l[k] for k in asia)
            P.append((hi, asia[-1] + 1))
        P += [(p, 0) for p in (self.yh if side > 0 else self.yl)]
        P += [(p, c + 1) for (_, c, p) in (self.sh if side > 0 else self.sl)]
        return P


def load(path):
    df = pd.read_csv(path, parse_dates=["ts"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[["open", "high", "low", "close"]].astype(float)
    df = df[~df.index.duplicated()].sort_index()
    tr = pd.concat([df.high - df.low, (df.high - df.close.shift()).abs(), (df.low - df.close.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    df["ema"] = df.close.ewm(span=200, adjust=False).mean()
    return df


def split_days(df):
    days, offs = [], []
    g = df.groupby(df.index.date)
    prev = None
    pos = 0
    for dd, x in g:
        n = len(x)
        if prev is not None and n >= 12:
            t = [ts.hour * 60 + ts.minute for ts in x.index]
            days.append(Day(dd, t, x.open.tolist(), x.high.tolist(), x.low.tolist(), x.close.tolist(), x.atr.tolist(),
                            float(prev.high.max()), float(prev.low.min())))
            offs.append(pos)
        prev = x
        pos += n
    return days, offs


def gen_extra(name, cx):
    S, c, h, l, a, n, t = [], cx.c, cx.h, cx.l, cx.a, cx.n, cx.d.t
    if name in ("sweep", "sweep_choch"):
        for k, s, ext in cx.sweeps():
            if name == "sweep":
                S.append((k, s, c[k], ext - s * 0.1 * a[k], False))
                continue
            ref = M.last_conf(cx.il if s < 0 else cx.ih, k)
            x = ext
            for j in range(k + 1, min(k + 7, n)):
                if (h[j] > x) if s < 0 else (l[j] < x):
                    break
                if ref is not None and s * (c[j] - ref) > 0:
                    S.append((j, s, c[j], x - s * 0.1 * a[j], False))
                    break
    elif name == "asia_raid":
        asia = [k for k in range(n) if t[k] < 7 * 60]
        if not asia or len(asia) >= n:
            return S
        ah, al = max(h[k] for k in asia), min(l[k] for k in asia)
        for up, lvl in ((1, ah), (-1, al)):
            for k in range(asia[-1] + 1, n):
                if t[k] >= 10 * 60:
                    break
                if (h[k] > lvl) if up > 0 else (l[k] < lvl):
                    s = -up
                    ext = h[k] if up > 0 else l[k]
                    for j in range(k, n):
                        if t[j] >= 13 * 60:
                            break
                        ext = max(ext, h[j]) if up > 0 else min(ext, l[j])
                        ref = M.last_conf(cx.il if s < 0 else cx.ih, j)
                        if j > k and ref is not None and s * (c[j] - ref) > 0:
                            S.append((j, s, c[j], ext - s * 0.1 * a[j], False))
                            break
                    break
    elif name == "pool_break":
        for side in (1, -1):
            for p, av in cx.pools(side):
                for k in range(max(av, 1), n):
                    if (h[k] > p) if side > 0 else (l[k] < p):
                        if side * (c[k] - p) > 0:              # closed beyond: breakout, trade with it
                            stop = (min(l[max(0, k - 2):k + 1]) if side > 0 else max(h[max(0, k - 2):k + 1])) - side * 0.1 * a[k]
                            S.append((k, side, c[k], stop, False))
                        break
    elif name == "silver":
        for w0, w1 in ((7 * 60, 8 * 60), (14 * 60, 15 * 60), (18 * 60, 19 * 60)):
            for k, s, edge, far, c1 in M.fvgs(cx, 0.5):
                if not (w0 <= t[k] < w1):
                    continue
                stop = c1 - s * 0.1 * a[k]
                last = next((j for j in range(k, n) if t[j] >= w1), n)
                f = M.limit_fill(cx, k + 1, last, s, edge, stop)
                if f:
                    S.append((f[0], s, f[1], stop, f[2]))
                break
    S = [x for x in S if x[1] * (x[2] - x[3]) > 0]
    return sorted(set(S))


def signals(name, cx, pc):
    if name in ("sweep", "sweep_choch", "asia_raid", "pool_break", "silver"):
        return gen_extra(name, cx)
    if name == "smt" and pc is None:
        return []
    return M.gen(name, cx, pc)


def run_series(label, df, cost, partner_df, maxhold, rng):
    days, offs = split_days(df)
    pmap = {}
    if partner_df is not None:
        pd_, _ = split_days(partner_df)
        pmap = {d.d: d for d in pd_}
    O, H, L, C, A, EMA = (df[k].values for k in ("open", "high", "low", "close", "atr", "ema"))
    T = df.index
    raw = {s: [] for s in SETUPS}
    prevd = None
    for di, (d, off) in enumerate(zip(days, offs)):
        y = days[di - 1] if di > 0 else Day(None, d.t, d.o, d.h, d.l, d.c, d.atr, d.prev_high, d.prev_low)
        cx = Ctx(d, y)
        pc = None
        if d.d in pmap and pmap.get(prevd) is not None:
            pc = Ctx(pmap[d.d], pmap[prevd])
        for s in SETUPS:
            for sg in signals(s, cx, pc):
                i, side, px, stop, hit = sg
                g = off + i
                p = cx.untouched_pool(i, side, px, abs(px - stop))
                raw[s].append((g, d.d, side, px, stop, hit, p, d.t[i]))
        prevd = d.d
    half = T[len(T) // 2]
    res = []
    for s in SETUPS:
        sig = sorted(raw[s])
        for tgt, htf, kz in itertools.product((1.5, 2, 3, "pool"), (False, True), (False, True)):
            busy, per_day, rows = -1, {}, []
            for (g, dd, side, px, stop, hit, pool, tm) in sig:
                if g <= busy or per_day.get(dd, 0) >= 2:
                    continue
                if htf and side * (C[g] - EMA[g]) <= 0:
                    continue
                if kz and not (7 * 60 <= tm < 10 * 60 or 12 * 60 <= tm < 16 * 60):
                    continue
                risk = abs(px - stop)
                if risk < 0.3 * A[g] or risk > 4 * A[g]:
                    continue
                tp = (pool if (tgt == "pool" and pool is not None) else px + side * (2 if tgt == "pool" else tgt) * risk)
                k, ex = walk(H, L, C, O, g, side, px, stop, tp, hit, maxhold)
                R = side * (ex - px) / risk
                rs = rng.choice((1, -1))
                lim = px != C[g]
                k2, ex2 = walk(H, L, C, O, g, rs, px, px - rs * risk, px + rs * abs(tp - px),
                               lim and ((L[g] <= px - risk) if rs > 0 else (H[g] >= px + risk)), maxhold)
                rR = rs * (ex2 - px) / risk
                cR = cost * px / risk
                busy = k
                per_day[dd] = per_day.get(dd, 0) + 1
                rows.append((T[g] < half, R, rR, R - cR, cR))
            if not rows:
                continue
            a = np.array(rows, dtype=float)
            e, h2 = a[a[:, 0] == 1], a[a[:, 0] == 0]
            res.append({"series": label, "setup": s, "tgt": tgt, "htf": htf, "kz": kz, "n": len(a),
                        "R": float(a[:, 1].mean()), "rndR": float(a[:, 2].mean()), "netR": float(a[:, 3].mean()),
                        "costR": float(np.median(a[:, 4])), "seR": float(a[:, 1].std() / np.sqrt(len(a))),
                        "win": float((a[:, 3] > 0).mean() * 100), "sumNet": float(a[:, 3].sum()),
                        "n1": len(e), "net1": float(e[:, 3].mean()) if len(e) else 0.0,
                        "n2": len(h2), "net2": float(h2[:, 3].mean()) if len(h2) else 0.0,
                        "R1": float(e[:, 1].mean()) if len(e) else 0.0, "R2": float(h2[:, 1].mean()) if len(h2) else 0.0})
    return res


def walk(H, L, C, O, g, side, px, stop, tp, hit, maxhold):
    if hit:
        return g, stop
    end = min(len(C) - 1, g + maxhold)
    for k in range(g + 1, end + 1):
        if (L[k] <= stop) if side > 0 else (H[k] >= stop):
            return k, (O[k] if side * (O[k] - stop) < 0 else stop)
        if (H[k] >= tp) if side > 0 else (L[k] <= tp):
            return k, (O[k] if side * (O[k] - tp) > 0 else tp)
    return end, C[end]


def main():
    rng = random.Random(52)
    out = []
    jobs = []
    for c in COINS5M:
        jobs.append((f"{c} 5m", DATA / f"crypto_{c}_5m.csv.gz", 0.0016, DATA / f"crypto_{'ETHUSDT' if c == 'BTCUSDT' else 'BTCUSDT'}_5m.csv.gz", 288))
    for c in COINS1H:
        jobs.append((f"{c} 1h", DATA / f"crypto_{c}_1h.csv.gz", 0.0016, DATA / f"crypto_{'ETHUSDT' if c == 'BTCUSDT' else 'BTCUSDT'}_1h.csv.gz", 24))
    jobs.append(("XAUUSD 1h", DATA / "gold_XAUUSD_1h.csv.gz", 0.0010, None, 24))
    jobs.append(("PAXG 1h", DATA / "crypto_PAXGUSDT_1h.csv.gz", 0.0016, None, 24))
    only = os.environ.get("R52_ONLY")
    for label, f, cost, pf, mh in jobs:
        if only and only not in label:
            continue
        df = load(f)
        pdf = load(pf) if pf else None
        r = run_series(label, df, cost, pdf, mh, rng)
        out += r
        b = [x for x in r if x["tgt"] == 2 and not x["htf"] and not x["kz"]]
        print(label, len(df), "bars |", " ".join(f"{x['setup']}:{x['n']}/{x['R']:+.2f}/{x['netR']:+.2f}" for x in b), flush=True)
        json.dump(out, open(HERE / "round52_results.json", "w"), indent=0)
    print("saved")


if __name__ == "__main__":
    main()
