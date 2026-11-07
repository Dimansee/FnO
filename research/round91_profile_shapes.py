"""Round 91 (7 Nov 2026): volume-profile SHAPES (reel) - P, b, D and thin/long (trend) - as mechanical rules.

Reel (user, 7 Nov): "P shape: value built at the top after a rally; the thin part below is where buyers defend. b shape is the
mirror: the thin part above is where sellers defend. D shape is balance: fade the edges, take profit at the POC. Thin and long is
the trend: don't fade it, buy the pullback to the last spot that built volume. The shape tells you where, order flow tells you
when: sellers absorbed at the thin spot, buyers accepted in the body - that is the entry."

Profile of a window, built two ways (indices have no volume):
  tpo  time-at-price from 1-min index candles (Dalton's Market Profile - where P/b/D come from)
  vol  heavyweight volume proxy: real 5-min volume of the 10 large caps (banks only for Bank Nifty), each scaled by its own
       median bar volume, spread over the index's 5-min bar range
  Stocks (10 large caps) use their own real 5-min volume.
Window ("mode"):
  prev  the previous full session's profile, traded the next day 09:20-14:30
  morn  the developing profile 09:15-11:15, traded the same day 11:15-14:30
Shape of the window (R = range, A = daily ATR(14) known before the window; morning thresholds x0.6):
  trend  R >= 1.0 A and |close - open| >= 0.6 R  (thin, long, one-sided) - direction = close vs open
  P      POC in the top 38% of the range and VAL above the middle third        -> long at the LVN below value
  b      POC in the bottom 38% and VAH below the middle third                   -> short at the LVN above value
  D      POC in the middle, VA inside the range on both sides                   -> short at VAH / long at VAL, target POC
  (else: no shape)
Levels: LVN = lowest 3-bin-smoothed density between the outer 10% and value; trend level = the last HVN (local peak of density)
in the trend half nearest the close ("last spot to build volume"); 70% value area grown from the POC.
"When" (order flow is not in the data - candle / volume stand-ins):
  touch   limit fill at the level
  reject  a 5-min bar trades into the level (within 0.25 ATR5) and closes back on the defend side in its own top/bottom half
          ("sellers absorbed") - entry at that close
  accept  after a touch in the last 6 bars, the first close back inside the profile body (P: above VAL, b: below VAH,
          D: back inside value, trend: level + 0.25 ATR5) - "buyers accepted in the body"
  absorb  reject + that bar's volume (proxy / real) >= 1.5x its last-20-bar average (high volume, held) - vol/stocks only
Stop: beyond the level and the touch low/high by ks x ATR5 (ks 0.5 / 1.0). Target: POC (P/b/D) or 2R, 2R, or hold to 15:15.
One trade per rule/side per day. Stop checked before target inside a bar.
Money: ATM option, app pricing (round 34: calibrated BS, time decay, charges), 1 lot, 0.5% slippage per fill.
Random twin: same bar, same stop distance and R target, coin-flip side (close entries only; limit fills compare with 0).
Stocks: cash intraday, 0.09% round trip, in R.
Extra: Jozwicki & Trippner (2025) report a 'reaction' at the prior POC in ~90% of WIG20 sessions; here the same reaction rate
at the prior POC vs random prices inside the prior range.
Usage: python research/round91_profile_shapes.py [data_dir]
"""
import os, sys, json, itertools, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data")
import numpy as np, pandas as pd
import round34 as R34
R34.DATA = DATA
R34.D.master = lambda: {}

IDX = ["NIFTY", "BANKNIFTY", "SENSEX"]
STOCKS = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "ITC", "LT", "SBIN", "AXISBANK", "BHARTIARTL"]
BANKS = ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK"]
SLIP = 0.005
NB = 48
TH = {'p_hi': 0.62, 'p_lo': 0.38, 'va_lo': 0.34, 'va_hi': 0.66}   # shape thresholds (round 91b varies them)
_C = {}


def vix():
    if "vix" not in _C:
        v = pd.read_csv(os.path.join(DATA, "INDIAVIX_1m_upstox.csv.gz"), parse_dates=["ts"]).set_index("ts").close
        _C["vix"] = v[~v.index.duplicated()].sort_index()
    return _C["vix"]


def five(a):
    return a.groupby(a.index.date, group_keys=False).apply(
        lambda x: x.resample("5min", origin=x.index[0], label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna())


def stock5(s):
    if ("s5", s) not in _C:
        d = pd.read_csv(os.path.join(DATA, f"{s}_5m_upstox.csv.gz"), parse_dates=["ts"]).set_index("ts")
        d = d[~d.index.duplicated()].sort_index().between_time("09:15", "15:25")
        _C[("s5", s)] = d[d.index <= "2026-10-16 15:30"]
    return _C[("s5", s)].copy()


def proxy_volume(sym, idx5):
    names = BANKS if sym == "BANKNIFTY" else STOCKS
    tot = pd.Series(0.0, index=idx5)
    for s in names:
        v = stock5(s).volume.astype(float)
        v = v / v[v > 0].median()
        tot = tot.add(v.reindex(idx5).fillna(0.0), fill_value=0.0)
    return tot.values


def load(sym):
    """5-min bars (+ volume weight), 1-min bars, daily ATR, per-day index ranges."""
    if sym in _C:
        return _C[sym]
    if sym in IDX:
        m1 = R34.m1(sym)
        df = five(m1)
        df["w"] = proxy_volume(sym, df.index)
    else:
        df = stock5(sym)[["open", "high", "low", "close", "volume"]].rename(columns={"volume": "w"})
        m1 = None
    df["d"] = df.index.normalize()
    days = df.d.unique()
    g = df.groupby("d")
    dh, dl, dc = g.high.max(), g.low.min(), g.close.last()
    tr = np.maximum(dh - dl, np.maximum((dh - dc.shift()).abs(), (dl - dc.shift()).abs()))
    adr = tr.ewm(alpha=1 / 14, adjust=False).mean().shift()          # known before the day starts
    a5 = R34.atr(df.high.values, df.low.values, df.close.values, 14)
    pos = {d: np.flatnonzero(df.d.values == d) for d in days}
    m1pos = None
    if m1 is not None:
        m1d = m1.index.normalize()
        m1pos = {d: np.flatnonzero(m1d == d) for d in days}
    _C[sym] = (df, m1, a5, adr, days, pos, m1pos)
    return _C[sym]


def profile(lo, hi, wt, plo, phi):
    """Uniform spread of each bar's weight over its [low, high] onto NB bins between plo and phi."""
    edges = np.linspace(plo, phi, NB + 1)
    dens = np.zeros(NB)
    step = (phi - plo) / NB
    for l, h, w in zip(lo, hi, wt):
        if w <= 0 or not np.isfinite(w):
            continue
        a = int(np.clip((l - plo) / step, 0, NB - 1)); b = int(np.clip((h - plo) / step, 0, NB - 1))
        dens[a:b + 1] += w / (b - a + 1)
    mids = (edges[:-1] + edges[1:]) / 2
    return mids, dens


def describe(mids, dens, o, c, rng, atr_d, trend_k):
    """Shape and levels of one profile."""
    if dens.sum() <= 0 or rng <= 0 or not np.isfinite(atr_d):
        return None
    sm = np.convolve(dens, np.ones(3) / 3, mode="same")
    poc_i = int(np.argmax(sm))
    # 70% value area from the POC
    lo_i = hi_i = poc_i; tot = dens.sum(); acc = dens[poc_i]
    while acc < 0.7 * tot and (lo_i > 0 or hi_i < NB - 1):
        up = dens[hi_i + 1] if hi_i < NB - 1 else -1
        dn = dens[lo_i - 1] if lo_i > 0 else -1
        if up >= dn:
            hi_i += 1; acc += up
        else:
            lo_i -= 1; acc += dn
    plo = mids[0]; f = lambda x: (x - plo) / rng  # noqa: E731
    poc, val, vah = mids[poc_i], mids[lo_i], mids[hi_i]
    p, pl, ph = f(poc), f(val), f(vah)
    out = {"poc": poc, "val": val, "vah": vah, "shape": None}
    edge = max(1, int(0.1 * NB))
    if rng >= trend_k * atr_d and abs(c - o) >= 0.6 * rng:
        side = 1 if c > o else -1
        # last HVN: local density peaks in the trend half; pick the one nearest the close
        half = range(NB // 2, NB) if side > 0 else range(0, NB // 2)
        peaks = [i for i in half if 0 < i < NB - 1 and sm[i] >= sm[i - 1] and sm[i] >= sm[i + 1] and sm[i] > 0]
        if peaks:
            i = max(peaks) if side > 0 else min(peaks)
            out.update(shape="trend", side=side, level=mids[i])
        return out
    if p >= TH['p_hi'] and pl >= TH['va_lo'] and lo_i > edge:
        seg = range(edge, lo_i)
        i = min(seg, key=lambda k: sm[k])
        out.update(shape="P", side=1, level=mids[i])
    elif p <= TH['p_lo'] and ph <= TH['va_hi'] and hi_i < NB - 1 - edge:
        seg = range(hi_i + 1, NB - edge)
        i = min(seg, key=lambda k: sm[k])
        out.update(shape="b", side=-1, level=mids[i])
    elif TH['p_lo'] < p < TH['p_hi'] and lo_i > 0 and hi_i < NB - 1:
        out.update(shape="D")
    return out


def day_setups(sym, src, mode):
    """For each trading day: list of setups (side, level, accept_line, target_poc, first bar position, last entry bar)."""
    df, m1, a5, adr, days, pos, m1pos = load(sym)
    hm = df.index.hour.values * 60 + df.index.minute.values
    out = {}
    for k in range(1, len(days)):
        d = days[k]; P = pos[d]
        if mode == "prev":
            q = days[k - 1]; W = pos[q]
            o, c = df.open.values[W[0]], df.close.values[W[-1]]
            hi, lo = df.high.values[W].max(), df.low.values[W].min()
            thr = 1.0; first = P[hm[P] >= 9 * 60 + 20]
        else:
            W = P[hm[P] < 11 * 60 + 15]
            if len(W) < 20:
                continue
            o, c = df.open.values[W[0]], df.close.values[W[-1]]
            hi, lo = df.high.values[W].max(), df.low.values[W].min()
            thr = 0.6; first = P[hm[P] >= 11 * 60 + 15]
            q = d
        rng = hi - lo
        if src == "tpo":
            if m1pos is None:
                continue
            M = m1pos[q]
            if mode == "morn":
                mh = m1.index[M].hour * 60 + m1.index[M].minute
                M = M[mh < 11 * 60 + 15]
            mids, dens = profile(m1.low.values[M], m1.high.values[M], np.ones(len(M)), lo, hi)
        else:
            mids, dens = profile(df.low.values[W], df.high.values[W], df.w.values[W], lo, hi)
        s = describe(mids, dens, o, c, rng, float(adr.get(d, np.nan)), thr)
        if not s or not s["shape"]:
            continue
        last = first[hm[first] <= 14 * 60 + 30]
        if not len(last):
            continue
        if s["shape"] == "D":
            setups = [(-1, s["vah"], s["vah"], s["poc"], "D"), (1, s["val"], s["val"], s["poc"], "D")]
        elif s["shape"] == "P":
            setups = [(1, s["level"], s["val"], s["poc"], "P")]
        elif s["shape"] == "b":
            setups = [(-1, s["level"], s["vah"], s["poc"], "b")]
        else:
            setups = [(s["side"], s["level"], None, None, "trend")]
        out[d] = (setups, first[0], last[-1], P[-1])
    return out


def entries(sym, src, mode, trig):
    """Entries: (bar i, entry px, side, level, extreme seen, target poc, shape, limit?)"""
    df, m1, a5, adr, days, pos, m1pos = load(sym)
    o, h, l, c, w = (df[k].values for k in ("open", "high", "low", "close", "w"))
    wavg = pd.Series(w).rolling(20, min_periods=5).mean().shift().values
    rows = []
    for d, (setups, f0, f1, dlast) in day_setups(sym, src, mode).items():
        for side, lev, acc_line, poc, shp in setups:
            tol = 0.25 * a5[f0]
            line = acc_line if acc_line is not None else lev + side * tol   # the "body" price that must be won back
            touched_at, ext = None, None
            for i in range(f0, f1 + 1):
                near = (l[i] <= lev + tol) if side > 0 else (h[i] >= lev - tol)
                if trig == "touch":
                    hit = (l[i] <= lev) if side > 0 else (h[i] >= lev)
                    if hit:
                        fill = min(o[i], lev) if side > 0 else max(o[i], lev)
                        rows.append((i, fill, side, lev, l[i] if side > 0 else h[i], poc, shp, True)); break
                    continue
                if trig in ("reject", "absorb"):
                    if not near:
                        continue
                    mid = (h[i] + l[i]) / 2
                    ok = (c[i] > lev and c[i] >= mid) if side > 0 else (c[i] < lev and c[i] <= mid)
                    if ok and trig == "absorb":
                        ok = bool(np.isfinite(wavg[i]) and wavg[i] > 0 and w[i] >= 1.5 * wavg[i])
                    if ok:
                        rows.append((i, c[i], side, lev, l[i] if side > 0 else h[i], poc, shp, False)); break
                    continue
                # accept: touch the level, then a LATER close back inside the body within 6 bars
                if touched_at is not None and i - touched_at > 6:
                    touched_at, ext = None, None
                if near and touched_at is None:
                    touched_at, ext = i, (l[i] if side > 0 else h[i])
                    continue
                if touched_at is None:
                    continue
                ext = min(ext, l[i]) if side > 0 else max(ext, h[i])
                went_through = (ext < line) if side > 0 else (ext > line)
                back = (c[i] > line) if side > 0 else (c[i] < line)
                if went_through and back:
                    rows.append((i, c[i], side, lev, ext, poc, shp, False)); break
    return rows


def sim(sym, E, ks, tgt, flip=None):
    df, m1, a5, adr, days, pos, m1pos = load(sym)
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    dd = df.d.values
    hm = df.index.hour.values * 60 + df.index.minute.values
    out = []
    rng = np.random.default_rng(flip) if isinstance(flip, int) else None
    for (i, ep, side, lev, ext, poc, shp, lim) in E:
        A = a5[i]
        ref = min(lev, ext) if side > 0 else max(lev, ext)
        risk = abs(ep - ref) + ks * A
        risk = max(risk, 0.25 * A)
        if flip == "opp":
            side = -side
        elif rng is not None:
            side = int(rng.choice([1, -1]))
        sl = ep - side * risk
        if tgt == "poc" and poc is not None and flip is None and (poc - ep) * side > 0.25 * risk:
            tp = poc
        elif tgt == "close":
            tp = None
        else:
            tp = ep + side * 2 * risk
        j0 = i if lim else i + 1
        xs, j = None, None
        for k2 in range(j0, len(c)):
            if dd[k2] != dd[i]:
                xs, j = c[k2 - 1], k2 - 1; break
            if (l[k2] <= sl) if side > 0 else (h[k2] >= sl):
                xs, j = sl, k2; break
            if tp is not None and ((h[k2] >= tp) if side > 0 else (l[k2] <= tp)) and not (lim and k2 == i):
                xs, j = tp, k2; break
            if hm[k2] >= 15 * 60 + 10:
                xs, j = c[k2], k2; break
        if xs is None:
            continue
        out.append((i, j, ep, xs, side, sl, (xs - ep) * side / risk, shp, df.index[i].strftime("%Y-%m-%d")))
    T = pd.DataFrame(out, columns=["i", "j", "ep", "xs", "side", "sl", "R", "shape", "date"])
    return T


def money_idx(sym, T):
    if not len(T):
        return T
    df = load(sym)[0]
    ent = np.full(len(df), np.nan); ent[T.i.values] = T.ep.values
    p_in, p_out, risk, _ = R34.price(sym, {"idx": df.index, "c": ent}, T, vix())
    rs, _, _ = R34.money(sym, p_in, p_out, risk, SLIP, "fixed", 1)
    T = T.copy(); T["rs"] = rs
    return T


def summ(T, col):
    if T is None or not len(T):
        return {"n": 0}
    a, b = T[T.date < "2025"], T[T.date >= "2025"]
    r = {"n": int(len(T)), "win": round(float((T.R > 0).mean()), 3), "R": round(float(T.R.mean()), 3),
         "mean_col": round(float(T[col].mean()), 4) if col in T else None,
         "R_a": round(float(a.R.mean()), 3) if len(a) else None, "R_b": round(float(b.R.mean()), 3) if len(b) else None,
         "se": round(float(T.R.std() / np.sqrt(len(T))), 3)}
    if col in T:
        r.update({"rs": round(float(T[col].mean()), 0), "rs_a": round(float(a[col].sum()), 0), "rs_b": round(float(b[col].sum()), 0),
                  "years": T.groupby(T.date.str[:4])[col].sum().round(0).to_dict()})
    return r


def poc_reaction(sym):
    """Prior-session POC first touch next day: share of touches followed by a >= 0.3 daily-ATR move away (either way) within
    12 bars, vs 20 random prices inside the prior range."""
    df, m1, a5, adr, days, pos, m1pos = load(sym)
    h, l, c = df.high.values, df.low.values, df.close.values
    rng = np.random.default_rng(3)
    hit = {"poc": [], "rand": []}
    for k in range(1, len(days)):
        q, d = days[k - 1], days[k]; W, P = pos[q], pos[d]
        M = m1pos[q]
        lo, hi = l[W].min(), h[W].max()
        mids, dens = profile(m1.low.values[M], m1.high.values[M], np.ones(len(M)), lo, hi)
        A = float(adr.get(d, np.nan))
        if not np.isfinite(A) or hi <= lo:
            continue
        poc = mids[int(np.argmax(np.convolve(dens, np.ones(3) / 3, mode="same")))]
        for kind, levels in (("poc", [poc]), ("rand", list(rng.uniform(lo, hi, 20)))):
            for lev in levels:
                t = next((i for i in P[1:] if l[i] <= lev <= h[i]), None)
                if t is None:
                    continue
                seg = P[(P > t) & (P <= t + 12)]
                if not len(seg):
                    continue
                move = max(h[seg].max() - lev, lev - l[seg].min())
                hit[kind].append(move >= 0.3 * A)
    return {k: (round(float(np.mean(v)), 3), len(v)) for k, v in hit.items()}


def main():
    res = {"idx": {}, "stocks": {}, "poc_reaction": {}}
    for sym in IDX:
        res["poc_reaction"][sym] = poc_reaction(sym)
        print("POC reaction", sym, res["poc_reaction"][sym], flush=True)
    # index options
    for src, mode, trig in itertools.product(("tpo", "vol"), ("prev", "morn"), ("touch", "reject", "accept", "absorb")):
        if trig == "absorb" and src == "tpo":
            continue
        E = {s: entries(s, src, mode, trig) for s in IDX}
        for ks, tgt in itertools.product((0.5, 1.0), ("poc", "2r", "close")):
            parts, twins = [], []
            for s in IDX:
                T = sim(s, E[s], ks, tgt)
                if len(T):
                    parts.append(money_idx(s, T).assign(sym=s))
                    if trig != "touch":
                        W = sim(s, E[s], ks, "2r" if tgt == "poc" else tgt, flip=11)
                        twins.append(money_idx(s, W).assign(sym=s))
            if not parts:
                continue
            A = pd.concat(parts)
            for shp in ("P", "b", "D", "trend", "all"):
                X = A if shp == "all" else A[A["shape"] == shp]
                key = f"{src}|{mode}|{trig}|{ks}|{tgt}|{shp}"
                r = {"all": summ(X, "rs"), "by_sym": {s: summ(X[X.sym == s], "rs") for s in IDX}}
                if shp == "all" and twins:
                    r["twin"] = summ(pd.concat(twins), "rs")
                res["idx"][key] = r
            s_ = res["idx"][f"{src}|{mode}|{trig}|{ks}|{tgt}|all"]
            print(f"IDX {src} {mode} {trig:<6} ks{ks} {tgt:<5} n={s_['all']['n']:>5} R {s_['all']['R']:+.3f} "
                  f"(a {s_['all']['R_a']:+.3f} b {s_['all']['R_b']:+.3f}) Rs/tr {s_['all']['rs']:+.0f} "
                  f"23-24 {s_['all']['rs_a']:+,.0f} 25-26 {s_['all']['rs_b']:+,.0f}"
                  + (f" | twin R {s_['twin']['R']:+.3f} Rs/tr {s_['twin']['rs']:+.0f}" if 'twin' in s_ else "")
                  + " | " + " ".join(f"{k}:{res['idx'][f'{src}|{mode}|{trig}|{ks}|{tgt}|{k}']['all'].get('n', 0)}/"
                                    f"{res['idx'][f'{src}|{mode}|{trig}|{ks}|{tgt}|{k}']['all'].get('R', 0):+.2f}" for k in ("P", "b", "D", "trend")),
                  flush=True)
    # stocks with real volume (cash intraday, 0.09% round trip)
    for mode, trig in itertools.product(("prev", "morn"), ("touch", "reject", "accept", "absorb")):
        E = {s: entries(s, "vol", mode, trig) for s in STOCKS}
        for ks, tgt in itertools.product((0.5, 1.0), ("poc", "2r", "close")):
            parts = []
            for s in STOCKS:
                T = sim(s, E[s], ks, tgt)
                if len(T):
                    risk_pct = (T.ep - T.sl).abs() / T.ep
                    T["netR"] = T.R - 0.0009 / risk_pct
                    parts.append(T.assign(sym=s))
            if not parts:
                continue
            A = pd.concat(parts)
            for shp in ("P", "b", "D", "trend", "all"):
                X = A if shp == "all" else A[A["shape"] == shp]
                r = summ(X, "netR")
                res["stocks"][f"{mode}|{trig}|{ks}|{tgt}|{shp}"] = r
            s_ = res["stocks"][f"{mode}|{trig}|{ks}|{tgt}|all"]
            print(f"STK {mode} {trig:<6} ks{ks} {tgt:<5} n={s_['n']:>6} grossR {s_['R']:+.3f} (a {s_['R_a']:+.3f} b {s_['R_b']:+.3f}) "
                  f"netR/tr {s_['rs']:+.3f}", flush=True)
    json.dump(res, open(os.path.join(HERE, "round91_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
