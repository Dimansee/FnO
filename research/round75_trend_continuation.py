"""Round 75: trend-continuation entries with small targets (20 / 30 / 50 Nifty points) - the 29 Oct - 3 Nov rally.

User (3 Nov): Nifty gained ~500 points in two sessions and Autopilot took nothing; is there any other entry (breakout or
otherwise) that catches 20-50 points? Rounds 67/69/72/74 covered level breaks, retests, structure, round numbers, opening-candle
retests and gap-and-hold. Not yet tested on the indices in this form: entries *inside* an established intraday trend.

Families (calls; puts mirror). "Day up" = close above the day's open and above the session average price (equal-weight VWAP
proxy, index candles have no volume), with price above that average on >= 70% of today's bars so far.
  pullback  10:00-14:30, day up, the day's high is >= 3 bars old, this bar dips to the 20-EMA or the average (within 0.05%) and
            closes back above both -> buy next bar; stop = lowest low of the last 3 bars - 0.03%
  basebreak 10:30-14:30, day up, the last 6 bars made no new high and their range is <= 0.35%, this bar closes above the day's
            high -> buy next bar; stop = base low
  afternoon 13:00-14:45, day up, close above the 12:00-13:00 box high -> buy next bar; stop = box low
  follow    yesterday closed in the top 20% of its range and up >= 0.5%; 09:30-11:00 close above the 09:15-09:30 high
            -> buy next bar; stop = opening-range low
Exits: fixed target 20 / 30 / 50 Nifty points (same % on Bank Nifty and Sensex: 0.087% / 0.13% / 0.22%), a 3-bar swing trail,
or hold to 15:15; stop always active. One trade per family per index per day.
Measured as ATM options priced like the app (time decay, charges, 0.25% slippage) at Rs 2,000 risk, by year, with a
random-direction twin; best version picked on 2023-24 only, then added to today's Autopilot in the Outcomes replay.
Also prints where each family fired on 23 Oct - 3 Nov.
Usage: python research/round75_trend_continuation.py
"""
import itertools, json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round25_harness as H
import round72_opening_candle_retest as M          # option pricing, slippage, risk per trade
import round73_regime_tilts as R73
import outcomes as O
from fno import portfolio_bt as PB

SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
TGT = {"t20": 20 / 23000, "t30": 30 / 23000, "t50": 50 / 23000}
_F = {}


def frame(sym):
    """5-min candles (research file + live days) with per-day helpers."""
    if sym in _F:
        return _F[sym]
    df = H.load(sym)[["open", "high", "low", "close"]].copy()
    df = df[df.index.dayofweek < 5]
    d = df.index.normalize()
    tp = (df.high + df.low + df.close) / 3
    g = tp.groupby(d)
    df["avg"] = g.cumsum() / g.cumcount().add(1)
    df["ema"] = df.close.ewm(span=20, adjust=False).mean()
    df["dopen"] = df.open.groupby(d).transform("first")
    up = (df.close > df.avg).astype(float)
    df["share"] = up.groupby(d).cumsum() / up.groupby(d).cumcount().add(1)
    df["hm"] = df.index.hour * 60 + df.index.minute
    _F[sym] = df
    return df


def days(df):
    d = df.index.normalize()
    starts = np.flatnonzero(np.r_[True, d[1:] != d[:-1]])
    return list(zip(starts, np.r_[starts[1:], len(df)]))


def signals(sym, fam):
    df = frame(sym)
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    av, em, dop, sh, hm = (df[k].values for k in ("avg", "ema", "dopen", "share", "hm"))
    out, prev = [], None
    for a, b in days(df):
        last = a + int(np.searchsorted(hm[a:b], 15 * 60 + 15, side="left")) - 1
        last = min(last, b - 1)
        if b - a < 40 or hm[a] != 555:
            prev = None
            continue
        done = set()
        for i in range(a + 3, last):
            t = hm[i]
            for s in (1, -1):
                if s in done:
                    continue
                trend = s * (c[i] - dop[i]) > 0 and s * (c[i] - av[i]) > 0 and (sh[i] if s == 1 else 1 - sh[i]) >= 0.7
                stop = None
                if fam == "pullback" and 600 <= t <= 870 and trend:
                    ext = h[a:i + 1] if s == 1 else -l[a:i + 1]
                    age = i - (a + int(np.argmax(ext)))
                    ref = max(em[i], av[i]) if s == 1 else min(em[i], av[i])
                    touch = (l[i] <= ref * 1.0005) if s == 1 else (h[i] >= ref * 0.9995)
                    back = s * (c[i] - em[i]) > 0 and s * (c[i] - av[i]) > 0
                    if age >= 3 and touch and back:
                        stop = (l[i - 2:i + 1].min() * 0.9997) if s == 1 else (h[i - 2:i + 1].max() * 1.0003)
                elif fam == "basebreak" and 630 <= t <= 870 and trend and i - 6 > a:
                    base = slice(i - 6, i)
                    dh = h[a:i - 6].max() if s == 1 else l[a:i - 6].min()
                    if s == 1:
                        ok = h[base].max() <= dh and (h[base].max() - l[base].min()) / c[i] <= 0.0035 and c[i] > dh
                        stop = l[base].min() if ok else None
                    else:
                        ok = l[base].min() >= dh and (h[base].max() - l[base].min()) / c[i] <= 0.0035 and c[i] < dh
                        stop = h[base].max() if ok else None
                elif fam == "afternoon" and 780 <= t <= 885 and trend:
                    box = np.arange(a, i)[(hm[a:i] >= 720) & (hm[a:i] < 780)]
                    if len(box) == 12:
                        bh, bl = h[box].max(), l[box].min()
                        if s == 1 and c[i] > bh and c[i - 1] <= bh:
                            stop = bl
                        elif s == -1 and c[i] < bl and c[i - 1] >= bl:
                            stop = bh
                elif fam == "follow" and prev is not None and 570 <= t <= 660:
                    pH, pL, pC, pO = prev
                    pos = (pC - pL) / max(pH - pL, 1e-9)
                    strong = (pos >= 0.8 and pC / pO - 1 >= 0.005) if s == 1 else (pos <= 0.2 and pC / pO - 1 <= -0.005)
                    orb = slice(a, a + 3)
                    lvl = h[orb].max() if s == 1 else l[orb].min()
                    if strong and s * (c[i] - lvl) > 0 and s * (c[i - 1] - lvl) <= 0:
                        stop = l[orb].min() if s == 1 else h[orb].max()
                if stop is not None and s * (o[i + 1] - stop) > 0:
                    out.append((i + 1, s, stop, last))
                    done.add(s)
        prev = (h[a:b].max(), l[a:b].min(), c[b - 1], o[a])
    return out


def manage(o, h, l, c, i0, s, stop, last, ex):
    e = o[i0]
    tgt = e * (1 + s * TGT[ex]) if ex in TGT else None
    sl = stop
    for k in range(i0, last + 1):
        if (s == 1 and l[k] <= sl) or (s == -1 and h[k] >= sl):
            return k, (sl if k == i0 else (min(o[k], sl) if s == 1 else max(o[k], sl)))
        if tgt is not None and ((s == 1 and h[k] >= tgt) or (s == -1 and l[k] <= tgt)):
            return k, tgt
        if ex == "trail" and k >= i0 + 2:
            sw = l[k - 2:k + 1].min() if s == 1 else h[k - 2:k + 1].max()
            sl = max(sl, sw) if s == 1 else min(sl, sw)
    return last, c[last]


def build(sym, sig, ex, twin=None):
    df = frame(sym)
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    rng = np.random.default_rng(twin) if twin is not None else None
    rows = []
    for j, s, stop, last in sig:
        e = o[j]
        if rng is not None and int(rng.choice([1, -1])) != s:
            s, stop = -s, e - (stop - e)
        k, xp = manage(o, h, l, c, j, s, stop, last, ex)
        rows.append({"i": j, "j": k, "xs": xp, "side": s, "sl": stop, "e": e, "R": s * (xp - e) / abs(e - stop),
                     "pts": s * (xp - e) * 23000 / e, "date": df.index[j].strftime("%Y-%m-%d"), "t_in": df.index[j].strftime("%H:%M"),
                     "t_out": (df.index[k] + pd.Timedelta(minutes=5)).strftime("%H:%M")})
    T = pd.DataFrame(rows)
    if T.empty:
        return T
    p_in, p_out, risk, _ = M.R34.price(sym, {"idx": df.index, "c": o}, T, M.vix())
    pi, po = p_in * (1 + M.SLIP), np.maximum(p_out * (1 - M.SLIP), 0.05)
    rk = np.maximum(risk * (1 + M.SLIP), pi * 0.05)
    lot = M.R34.D.lot_size(sym)
    lots = np.clip(np.floor(M.RISK_RS / (rk * lot)), 1, 40)
    qty = lots * lot
    T["rs"] = (po - pi) * qty - (40 + 0.0015 * po * qty + 0.0005 * (pi + po) * qty)
    T["prem_in"], T["prem_out"], T["risk_lot"], T["lot"], T["sym"] = pi, po, rk * lot, lot, sym
    return T


def summ(T):
    if T is None or T.empty:
        return {"n": 0}
    y = T.groupby(T.date.str[:4]).rs.sum().round(0).to_dict()
    return {"n": int(len(T)), "win": round(float((T.R > 0).mean()), 3), "avgR": round(float(T.R.mean()), 3),
            "pts": round(float(T.pts.mean()), 1), "rs_trade": round(float(T.rs.mean()), 0), "rs_total": round(float(T.rs.sum()), 0),
            "years": y, "a": round(float(T[T.date < "2025"].rs.sum()), 0), "b": round(float(T[T.date >= "2025"].rs.sum()), 0)}


def autopilot_rows(T, name):
    lot = T["lot"].values
    pi = T["prem_in"].values / (1 + M.SLIP) * 1.005
    po = np.maximum(T["prem_out"].values / (1 - M.SLIP) * 0.995, 0.05)
    out = pd.DataFrame({"date": T.date, "entry_time": T.t_in, "exit_time": [min(x, "15:20") for x in T.t_out], "sym": T.sym,
                        "strategy": name, "side": np.where(T.side > 0, "CALL", "PUT"), "prem_in": pi, "prem_out": po,
                        "lot": lot, "risk_lot": np.maximum(T["risk_lot"].values / lot / (1 + M.SLIP) * 1.005, pi * 0.05) * lot,
                        "R": T["R"].values, "spot_in": T["e"].values, "spot_out": T["xs"].values, "reason": "r75", "pieces": 1})
    out["risk_lot_capped"] = out["risk_lot"]
    parts = []
    for s, g in out.groupby("sym"):
        g = g.copy()
        g["cpr_w"] = PB.cpr_widths(O.candles(s)).reindex(g.date).values
        g["score"] = PB._scores(g, O.candles(s))
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


FAMS = ["pullback", "basebreak", "afternoon", "follow"]
EXITS = ["t20", "t30", "t50", "trail", "close"]


def recent(sig_cache):
    """Where each family fired on 23 Oct - latest, with the 30-pt target and hold-to-close outcomes."""
    rows = []
    for sym in SYMS:
        df = frame(sym)
        for fam in FAMS:
            sg = [x for x in sig_cache[(sym, fam)] if df.index[x[0]] >= pd.Timestamp("2026-10-23")]
            if not sg:
                continue
            for ex in ("t30", "close"):
                T = build(sym, sg, ex)
                for r in T.itertuples():
                    rows.append({"sym": sym, "fam": fam, "exit": ex, "date": r.date, "in": r.t_in, "out": r.t_out,
                                 "side": "CALL" if r.side > 0 else "PUT", "pts": round(r.pts * r.e / 23000, 1), "R": round(r.R, 2), "rs": round(r.rs)})
    return rows


def main():
    res = {"grid": []}
    sig = {(s, f): signals(s, f) for s in SYMS for f in FAMS}
    res["recent"] = recent(sig)
    for r in res["recent"]:
        print("recent", r)
    best = None
    for fam, ex in itertools.product(FAMS, EXITS):
        T = pd.concat([build(s, sig[(s, fam)], ex) for s in SYMS], ignore_index=True)
        T = T[T.date <= "2026-10-30"]
        W = pd.concat([build(s, sig[(s, fam)], ex, twin=5) for s in SYMS], ignore_index=True)
        W = W[W.date <= "2026-10-30"]
        a, t = summ(T), summ(W)
        res["grid"].append({"fam": fam, "exit": ex, "all": a, "twin": t,
                            "by_sym": {s: summ(T[T.sym == s]) for s in SYMS}})
        print(f"{fam:<10} {ex:<6} n{a['n']:>5} win{a.get('win', 0):.2f} R{a.get('avgR', 0):+.3f} pts{a.get('pts', 0):+5.1f} Rs/tr{a.get('rs_trade', 0):+6.0f} "
              f"23-24 {a.get('a', 0):+9.0f} 25-26 {a.get('b', 0):+9.0f} yrs {a.get('years')} | twin R{t.get('avgR', 0):+.3f} Rs {t.get('rs_total', 0):+.0f}", flush=True)
        if a.get("n", 0) >= 60 and a["a"] > 0 and (best is None or a["a"] > best[1]["all"]["a"]):     # chosen on 2023-24 only
            best = (T, res["grid"][-1])
    if best:
        T, r = best
        print("\nchosen on 2023-24:", r["fam"], r["exit"], "2025-26:", r["all"]["b"])
        res["chosen"] = {"fam": r["fam"], "exit": r["exit"]}
        new = autopilot_rows(T, f"Trend continuation {r['fam']} (r75)")
        B0 = dict(O.B0)
        k, ak = PB._trade_key(B0), PB._alt_key(B0)
        base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
        alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
        for cap in (200_000, 20_000):
            ref = {p_: R73.run(base.assign(xm=1.0), alt.assign(xm=1.0) if alt is not None else None, cap, a_, b_) for p_, (a_, b_) in R73.PERIODS.items()}
            Tn = pd.concat([base, new], ignore_index=True).assign(xm=1.0)
            An = pd.concat([alt, new], ignore_index=True).assign(xm=1.0) if alt is not None else None
            add = {p_: R73.run(Tn, An, cap, a_, b_) for p_, (a_, b_) in R73.PERIODS.items()}
            res[f"replay_{cap}"] = {"today": ref, "plus_r75": add}
            for lab, rr in (("Autopilot today", ref), ("+ r75 pick", add)):
                print(f"  Rs {cap:,} {lab:<16} " + " ".join(f"{p_}:{rr[p_]['final'] / 1e5:6.2f}L" for p_ in R73.PERIODS) + f" fall {rr['full']['dd']:+.1f}%", flush=True)
    json.dump(res, open(os.path.join(HERE, "round75_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
