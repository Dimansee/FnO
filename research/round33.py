"""Round 33 (Oct 2026): three improvements asked for, tested on the CURRENT Autopilot rules and on the round-32 one-direction logic.
 1. Sideways cut: if the trade stalls (no progress after N bars / choppy last 30 min / 5-min ADX weak and falling) exit, and
    optionally re-enter in the same direction when price breaks out of the stall range (momentum back).
 2. Multi-timeframe: on 5-min, 15-min and 1-hour candles, is price on the trade's side of its 20-EMA and of its trend line
    (rising support through the last two swing lows / falling resistance through the last two swing highs)? Score 0-6.
    Used as a filter (trade only when score >= k) and as conviction for size.
 3. Size by conviction (2x lots on high scores, half on low) and partial booking (half at +1R; half at +1R with the rest to
    breakeven; thirds at +1R / +2R / runner).
Trades are re-priced bar by bar with the round-30 pricer, which reproduces the harness exactly (entry = the bar before entry_time);
each change is applied as the difference between the modified and the original re-priced P&L. Rs 2,000 risk a trade ("u").
Choose on 2023-25, judge on 2026. Run: python round33.py -> round33_results.json"""
import os, sys, json, itertools, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import round26_harness as H, round30_engine as E30, round32_engine as E32
from fno import backtest as B, indicators as I

VIX = H.load("INDIAVIX")["close"]; SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
BROK = 20.0                      # one extra exit order (Rs) for each partial booking
RES = {}


# ---------------- market data per index
class Mkt:
    def __init__(self, sym):
        c = H.load(sym); self.sym = sym; self.meta = B.D.instrument(sym)
        d5 = I.add_indicators(c); d5["adx"] = E30.adx(c)
        self.d5 = d5; self.days = {str(d): g for d, g in d5.groupby(d5.index.date)}
        self.feat = {tf: self._tf_features(c, tf) for tf in (5, 15, 60)}
        self.cache = {}

    def _tf_features(self, c, tf):
        if tf == 5:
            s = c.copy(); end = s.index + pd.Timedelta(minutes=5)
        else:
            parts = []
            for d, g in c.groupby(c.index.date):
                r = g.resample(f"{tf}min", origin=g.index[0].normalize() + pd.Timedelta(hours=9, minutes=15), label="left",
                               closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
                parts.append(r)
            s = pd.concat(parts)
            end = pd.DatetimeIndex([min(t + pd.Timedelta(minutes=tf), t.normalize() + pd.Timedelta(hours=15, minutes=30)) for t in s.index])
        Hh, L, Cc = s.high.values, s.low.values, s.close.values; n = len(s)
        ema = s.close.ewm(span=20, adjust=False).mean().values
        up_tl = np.zeros(n, bool); dn_tl = np.zeros(n, bool); sh, sl = [], []; F = 2
        for k in range(n):
            q = k - F                                 # swing at q is confirmed at k
            if q >= F:
                if Hh[q] == Hh[q - F:q + F + 1].max(): sh.append(q)
                if L[q] == L[q - F:q + F + 1].min(): sl.append(q)
            if len(sl) >= 2 and L[sl[-1]] > L[sl[-2]]:
                a, b = sl[-2], sl[-1]; up_tl[k] = Cc[k] > L[b] + (L[b] - L[a]) / (b - a) * (k - b)
            if len(sh) >= 2 and Hh[sh[-1]] < Hh[sh[-2]]:
                a, b = sh[-2], sh[-1]; dn_tl[k] = Cc[k] < Hh[b] + (Hh[b] - Hh[a]) / (b - a) * (k - b)
        return pd.DataFrame({"ema_up": Cc > ema, "ema_dn": Cc < ema, "up_tl": up_tl, "dn_tl": dn_tl}, index=end)

    def score(self, ts_end, side):
        """0-6: on 5 / 15 / 60 min, price on the trade's side of the 20-EMA (1) and of the trend line (1)."""
        sc = 0
        for tf in (5, 15, 60):
            f = self.feat[tf]; k = f.index.searchsorted(ts_end, side="right") - 1
            if k < 0: continue
            r = f.iloc[k]
            sc += int(r.ema_up if side > 0 else r.ema_dn) + int(r.up_tl if side > 0 else r.dn_tl)
        return sc

    def price(self, date, i, j, xs, side):
        key = (date, i, j, round(xs, 2), side)
        if key not in self.cache:
            day = self.days[date]
            self.cache[key] = E30.price_trade(self.sym, pd.Timestamp(date).date(), day, i, j, float(xs), "CE" if side > 0 else "PE",
                                              float(day.close.iloc[i]), VIX, self.meta)[0]
        return self.cache[key]


M = {s: Mkt(s) for s in SYMS}
print("market data ready", flush=True)


# ---------------- trades of the current rules
def live_trades():
    L = pd.read_pickle("/tmp/round32_live.pkl"); rows = []
    for r in L.itertuples():
        m = M[r.sym]; day = m.days[r.date]; idx = [x.strftime("%H:%M") for x in day.index]
        try:
            i = idx.index(r.entry_time) - 1; j = idx.index(r.exit_time) - 1
        except ValueError:
            continue
        side = 1 if r.side == "CE" else -1; c0 = float(day.close.iloc[i]); a = float(day.atr.iloc[i]) or c0 * 0.001
        if r.slot == "trend":
            dist = min(max(abs(c0 - float(day.vwap.iloc[i])) + 0.5 * a, a), 3 * a); xs = c0 + side * r.R * dist
        else:
            xs = float(r.spot_out)
            dist = abs(xs - c0) / abs(r.R) if abs(r.R) > 0.05 else 1.5 * a
        rows.append(dict(src="current", slot=r.slot, sym=r.sym, date=r.date, i=i, j=j, side=side, c0=c0, dist=dist, xs=xs,
                         u=r.u, rl=r.risk_lot, base=r.pnl))
    return pd.DataFrame(rows)


def r32_trades():
    """The best round-32 settings on 2023-25 (and the strongest one-direction version), with exits kept."""
    keep = {"r32 free|pivbrk|swing|15m|1": ("free", "pivbrk", "swing", 1), "r32 first|pivbrk|swing|15m|3": ("first", "pivbrk", "swing", 3),
            "r32 orb_pivot|pivbrk|swing|15m|3": ("orb_pivot", "pivbrk", "swing", 3)}
    rows = []
    for s in SYMS:
        m = M[s]
        for x in E32.prep(s, 15):
            k0 = VIX.index.searchsorted(x["day"].index[0], side="right") - 1
            if (float(VIX.iloc[k0]) if k0 >= 0 else 14.0) < E32.C.VIX_MIN: continue
            sig = E32.signals(x, "pivbrk")
            for nm, (b, g, t, mx) in keep.items():
                for (i, j, xs, side, sl0, c0, dist) in E32.simulate(x, *sig, E32.bias_of(x, b), t, max_trades=mx, lock_first=b == "first"):
                    d = str(x["d"]); p = m.price(d, i, j, xs, side)
                    day = m.days[d]
                    rl = E30.price_trade(s, x["d"], day, i, j, xs, "CE" if side > 0 else "PE", sl0, VIX, m.meta)[1]
                    rows.append(dict(src=nm, slot="r32", sym=s, date=d, i=i, j=j, side=side, c0=c0, dist=dist, xs=xs,
                                     u=p / rl * 2000, rl=rl, base=p))
    return pd.DataFrame(rows)


TR = pd.concat([live_trades(), r32_trades()], ignore_index=True)
TR["y"] = TR.date.str[:4]
TR["score"] = [M[r.sym].score(M[r.sym].days[r.date].index[r.i] + pd.Timedelta(minutes=5), r.side) for r in TR.itertuples()]
TR["rep"] = [M[r.sym].price(r.date, r.i, r.j, r.xs, r.side) for r in TR.itertuples()]
print("trades", TR.groupby("src").size().to_dict(), "parity (current) max diff",
      round(float((TR[TR.src == "current"].rep - TR[TR.src == "current"].base).abs().max()), 2), flush=True)


# ---------------- modifications: each returns a list of legs (i_in, i_out, x_spot, fraction) and extra orders
def legs_sideways(r, day, rule, reentry):
    h, l, c, o = day.high.values, day.low.values, day.close.values, day.open.values
    adx = day.adx.values; side, c0, dist = r.side, r.c0, r.dist
    best = 0.0; cut = None
    for k in range(r.i + 1, r.j):
        best = max(best, side * ((h[k] if side > 0 else l[k]) - c0) / dist)
        n = k - r.i; prof = side * (c[k] - c0) / dist
        if rule.startswith("T"):
            if n >= int(rule[1:]) and best < 0.5: cut = k; break
        elif rule == "ER":
            if n >= 6:
                seg = c[k - 6:k + 1]; er = abs(seg[-1] - seg[0]) / max(np.abs(np.diff(seg)).sum(), 1e-9)
                if er < 0.3 and abs(prof) < 0.5: cut = k; break
        elif rule == "ADX":
            if n >= 6 and adx[k] < 20 and adx[k] < adx[k - 1] and abs(prof) < 0.5: cut = k; break
    if cut is None: return [(r.i, r.j, r.xs, 1.0)], 0
    legs = [(r.i, cut, float(c[cut]), 1.0)]
    if reentry:
        w = slice(max(r.i + 1, cut - 5), cut + 1)
        lvl = (h[w].max() if side > 0 else l[w].min()); stop = (l[w].min() if side > 0 else h[w].max())
        for k in range(cut + 1, r.j):
            if side * (c[k] - lvl) > 0:
                out, xs = r.j, r.xs
                for m in range(k + 1, r.j + 1):
                    if side * ((l[m] if side > 0 else h[m]) - stop) <= 0:
                        out, xs = m, (o[m] if side * (o[m] - stop) < 0 else stop); break
                legs.append((k, out, float(xs), 1.0)); break
    return legs, 0


def legs_partial(r, day, kind):
    h, l, o = day.high.values, day.low.values, day.open.values; side, c0, dist = r.side, r.c0, r.dist
    def first_hit(level, start):
        for k in range(start, r.j + 1):
            if side * ((h[k] if side > 0 else l[k]) - level) >= 0:
                return k, (o[k] if side * (o[k] - level) > 0 else level)
        return None
    t1 = first_hit(c0 + side * dist, r.i + 1)
    if kind == "none" or t1 is None or t1[0] >= r.j:
        return [(r.i, r.j, r.xs, 1.0)], 0
    k1, x1 = t1
    if kind == "half":
        return [(r.i, k1, x1, 0.5), (r.i, r.j, r.xs, 0.5)], 1
    if kind == "half_be":
        for m in range(k1 + 1, r.j + 1):
            if side * ((l[m] if side > 0 else h[m]) - c0) <= 0:
                return [(r.i, k1, x1, 0.5), (r.i, m, (o[m] if side * (o[m] - c0) < 0 else c0), 0.5)], 1
        return [(r.i, k1, x1, 0.5), (r.i, r.j, r.xs, 0.5)], 1
    if kind == "thirds":
        t2 = first_hit(c0 + 2 * side * dist, k1)
        if t2 is None or t2[0] >= r.j:
            return [(r.i, k1, x1, 1 / 3), (r.i, r.j, r.xs, 2 / 3)], 1
        return [(r.i, k1, x1, 1 / 3), (r.i, t2[0], t2[1], 1 / 3), (r.i, r.j, r.xs, 1 / 3)], 2
    raise ValueError(kind)


def apply(df, side_rule="none", reentry=False, partial="none", min_score=0, size="none"):
    out = []
    for r in df.itertuples():
        if r.score < min_score: continue
        m = M[r.sym]; day = m.days[r.date]
        if side_rule != "none":
            legs, extra = legs_sideways(r, day, side_rule, reentry)
            # partial booking applies to the first leg when both are used
            if partial != "none":
                r1 = r._replace(j=legs[0][1], xs=legs[0][2]); pl, extra = legs_partial(r1, day, partial)
                legs = pl + legs[1:]
        else:
            legs, extra = legs_partial(r, day, partial)
        pnl = sum(f * m.price(r.date, a, b, x, r.side) for a, b, x, f in legs)
        u = r.u + (pnl - r.rep) / r.rl * 2000 - BROK * extra
        mult = 1.0
        if size in ("2x_high", "both") and r.score >= 5: mult = 2.0
        if size in ("half_low", "both") and r.score <= 2: mult = 0.5
        out.append((r.date, r.y, r.sym, u * mult, mult))
    return pd.DataFrame(out, columns=["date", "y", "sym", "u", "mult"])


def stats(t):
    if len(t) == 0: return dict(n=0)
    s = t.groupby("date").u.sum().sort_index(); eq = s.cumsum(); fall = float((eq - eq.cummax().clip(lower=0)).min())
    w = t.u[t.u > 0].sum(); l = -t.u[t.u <= 0].sum()
    return dict(n=int(len(t)), net=round(float(t.u.sum())), win=round(float((t.u > 0).mean() * 100), 1),
                pf=round(float(w / l), 2) if l else None, fall=round(fall), ret_fall=round(float(t.u.sum() / -fall), 2) if fall < 0 else None,
                risk=round(float(t.mult.mean()), 2))


def evaluate(src, label, **kw):
    t = apply(TR[TR.src == src], **kw)
    return dict(label=label, **kw, train=stats(t[t.y < "2026"]), y2026=stats(t[t.y == "2026"]),
                years={y: round(float(v)) for y, v in t.groupby("y").u.sum().items()})


VARIANTS = [("as is", {})]
for rule in ("T6", "T9", "T12", "ER", "ADX"):
    VARIANTS += [(f"sideways cut {rule}", dict(side_rule=rule)), (f"sideways cut {rule} + re-entry", dict(side_rule=rule, reentry=True))]
for k in (2, 3, 4, 5):
    VARIANTS.append((f"multi-timeframe score >= {k}", dict(min_score=k)))
for sz in ("2x_high", "half_low", "both"):
    VARIANTS.append((f"size by score: {sz}", dict(size=sz)))
for p in ("half", "half_be", "thirds"):
    VARIANTS.append((f"partial booking: {p}", dict(partial=p)))

for src in TR.src.unique():
    print(f"\n===== {src}  (score distribution {TR[TR.src == src].score.value_counts().sort_index().to_dict()})", flush=True)
    res = []
    for lab, kw in VARIANTS:
        e = evaluate(src, lab, **kw); res.append(e)
        print(f"{lab:38s} train {e['train']} | 2026 {e['y2026']}", flush=True)
    RES[src] = res

# score vs outcome on the current rules (does the multi-timeframe score predict anything?)
c = TR[TR.src == "current"]
RES["score_vs_outcome_current"] = {int(k): dict(n=int(len(g)), avg_u=round(float(g.u.mean())), win=round(float((g.u > 0).mean() * 100), 1))
                                   for k, g in c.groupby("score")}
print("\nscore vs outcome (current rules):", RES["score_vs_outcome_current"])
json.dump(RES, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "round33_results.json"), "w"), indent=1, default=str)
TR.to_pickle("/tmp/round33_trades.pkl")
print("saved round33_results.json")
