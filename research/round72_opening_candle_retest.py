"""Round 72: the user's two-level rotation from 2 Nov 2026 (Nifty 23350 CE, entry 65, exit 82).

The user's chart: the first 5-minute candle's high is broken, price runs up and is rejected at a top (the morning high),
falls back to the opening candle's high, the level holds ("rejection from opening candle high"), buy a call there and
exit when price gets back to the top ("rejection from top"). Puts are the mirror (opening candle's low, morning low).

Rules as coded (long side; short is the mirror):
  L    = the opening level: the 09:15 5-min candle's high (variant: the 09:15-09:30 high)
  1.   a 5-min close above L after 09:20                                  -> "broken"
  2.   TOP = highest high since the break; it must be at least `run` above L (0.15% / 0.25%)
  3.   retest: a later bar trades down to within `tol` of L (low <= L*(1+tol)) and closes back above L
       (variant: and closes green) -> enter on the next bar's open, 09:30-14:30, first retest of the day per side only
  4.   room: TOP - entry >= `room` x (entry - stop), else skip
  5.   stop: retest bar's low minus 0.03%
  6.   exits: "top"   = all out at TOP minus 0.02% (the user's exit)
              "top+trail" = at TOP move the stop to entry, trail 0.15% behind the best price
              "2R"    = all out at 2R
              "close" = hold to 15:15 (stop still active)
Measured: index points / R, an option leg priced like the app (ATM, time decay, charges, slippage) at Rs 2,000 risk, by year,
and a random-direction twin (same bar, same stop distance and same exit rule mirrored) to see whether the level adds an edge.

Usage: python research/round72_opening_candle_retest.py [data_dir]
"""
import os, sys, json, itertools, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data")
import numpy as np, pandas as pd
import round62_scalping7 as S
import round34 as R34
S.DATA = R34.DATA = DATA
R34.D.master = lambda: {}          # offline: lot sizes from the built-in instrument table, not the app's database

SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
SLIP = 0.0025
RISK_RS = 2000.0
_C = {}


def load(sym):
    if sym not in _C:
        _C[sym] = S.load("index", sym, "5m")
    return _C[sym]


def vix():
    if "vix" not in _C:
        v = pd.read_csv(os.path.join(DATA, "INDIAVIX_1m_upstox.csv.gz"), parse_dates=["ts"]).set_index("ts").close
        _C["vix"] = v[~v.index.duplicated()].sort_index()
    return _C["vix"]


def manage(o, h, l, c, i0, s, stop, top, exit_kind, last):
    """Return (exit bar, exit price). s = +1 long / -1 short."""
    e = o[i0]
    risk = abs(e - stop)
    tgt = top * (1 - s * 0.0002) if exit_kind in ("top", "top+trail") else (e + s * 2 * risk if exit_kind == "2R" else None)
    sl, best, trailing = stop, e, False
    for k in range(i0, last + 1):
        if (s == 1 and l[k] <= sl) or (s == -1 and h[k] >= sl):
            px = sl if k == i0 else (min(o[k], sl) if s == 1 else max(o[k], sl))
            return k, px
        if tgt is not None and not trailing and ((s == 1 and h[k] >= tgt) or (s == -1 and l[k] <= tgt)):
            if exit_kind == "top+trail":
                trailing, sl = True, e
            else:
                return k, tgt
        best = max(best, h[k]) if s == 1 else min(best, l[k])
        if trailing:
            sl = max(sl, best * (1 - 0.0015)) if s == 1 else min(sl, best * (1 + 0.0015))
    return last, c[last]


def signals(sym, level="5m", run=0.0015, tol=0.0005, green=False, room=1.0):
    """List of setups: (entry bar, side, stop, top, day_last)."""
    df = load(sym)
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    d = df.index.normalize()
    hm = np.asarray(df.index.hour * 60 + df.index.minute)
    out = []
    starts = np.flatnonzero(np.r_[True, d[1:] != d[:-1]])
    ends = np.r_[starts[1:], len(c)]
    for a, b in zip(starts, ends):
        bars = np.arange(a, b)
        bars = bars[hm[bars] <= 15 * 60 + 10]
        if len(bars) < 20 or hm[bars[0]] != 9 * 60 + 15:
            continue
        last = bars[-1]
        k0 = 1 if level == "5m" else 3
        for s in (1, -1):
            L = h[a:a + k0].max() if s == 1 else l[a:a + k0].min()
            broken, top = False, None
            for i in bars[k0:]:
                if not broken:
                    if (s == 1 and c[i] > L) or (s == -1 and c[i] < L):
                        broken, top = True, (h[i] if s == 1 else l[i])
                    continue
                touch = (l[i] <= L * (1 + tol)) if s == 1 else (h[i] >= L * (1 - tol))
                ext = s * (top - L) / L >= run
                if touch:
                    held = (c[i] > L) if s == 1 else (c[i] < L)
                    if not held:                               # level lost: setup over for this side
                        break
                    if ext and (not green or (s == 1 and c[i] > o[i]) or (s == -1 and c[i] < o[i])):
                        j = i + 1
                        if j > last or hm[j] < 9 * 60 + 30 or hm[j] > 14 * 60 + 30:
                            break
                        stop = l[i] * (1 - 0.0003) if s == 1 else h[i] * (1 + 0.0003)
                        e = o[j]
                        if s * (e - stop) <= 0 or s * (top - e) < room * abs(e - stop):
                            break
                        out.append((j, s, stop, top, last))
                        break
                    continue
                top = max(top, h[i]) if s == 1 else min(top, l[i])
    return out


def build(sym, sig, exit_kind, twin_seed=None):
    df = load(sym)
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    rng = np.random.default_rng(twin_seed) if twin_seed is not None else None
    rows = []
    for j, s, stop, top, last in sig:
        e = o[j]
        if rng is not None:                                    # random-direction twin: same distances, coin-flip side
            s2 = int(rng.choice([1, -1]))
            if s2 != s:
                stop, top = e - (stop - e), e - (top - e)
                s = s2
        k, xp = manage(o, h, l, c, j, s, stop, top, exit_kind, last)
        rows.append({"i": j, "j": k, "xs": xp, "side": s, "sl": stop, "e": e, "R": s * (xp - e) / abs(e - stop),
                     "pts": s * (xp - e), "date": df.index[j].strftime("%Y-%m-%d"), "t_in": df.index[j].strftime("%H:%M"),
                     "t_out": (df.index[k] + pd.Timedelta(minutes=5)).strftime("%H:%M")})
    T = pd.DataFrame(rows)
    if T.empty:
        return T
    p_in, p_out, risk, _ = R34.price(sym, {"idx": df.index, "c": o}, T, vix())
    pi, po = p_in * (1 + SLIP), np.maximum(p_out * (1 - SLIP), 0.05)
    rk = np.maximum(risk * (1 + SLIP), pi * 0.05)
    lot = R34.D.lot_size(sym)
    lots = np.clip(np.floor(RISK_RS / (rk * lot)), 1, 40)
    qty = lots * lot
    chg = 40 + 0.0015 * po * qty + 0.0005 * (pi + po) * qty
    T["prem_in"], T["prem_out"], T["risk_lot"], T["lot"] = pi, po, rk * lot, lot
    T["rs"] = (po - pi) * qty - chg
    T["sym"] = sym
    return T


def summ(T):
    if T is None or T.empty:
        return {"n": 0}
    y = T.groupby(T.date.str[:4]).rs.sum().round(0).to_dict()
    return {"n": int(len(T)), "win": round(float((T.R > 0).mean()), 3), "avgR": round(float(T.R.mean()), 3),
            "pts": round(float(T.pts.mean()), 1), "rs_trade": round(float(T.rs.mean()), 0), "rs_total": round(float(T.rs.sum()), 0),
            "years": y, "a": round(float(T[T.date < "2025"].rs.sum()), 0), "b": round(float(T[T.date >= "2025"].rs.sum()), 0)}


GRID = {"level": ["5m", "15m"], "run": [0.0015, 0.0025], "tol": [0.0005, 0.001], "green": [False, True], "room": [1.0, 1.5],
        "exit": ["top", "top+trail", "2R", "close"]}


def main():
    res = []
    keys = list(GRID)
    sigcache = {}
    for combo in itertools.product(*GRID.values()):
        p = dict(zip(keys, combo))
        sk = tuple(p[k] for k in keys if k != "exit")
        Ts, Tw = [], []
        for sym in SYMS:
            if (sym, sk) not in sigcache:
                sigcache[(sym, sk)] = signals(sym, p["level"], p["run"], p["tol"], p["green"], p["room"])
            sg = sigcache[(sym, sk)]
            Ts.append(build(sym, sg, p["exit"]))
            Tw.append(build(sym, sg, p["exit"], twin_seed=7))
        T = pd.concat(Ts, ignore_index=True); W = pd.concat(Tw, ignore_index=True)
        r = {**p, "all": summ(T), "twin": summ(W), "by_sym": {s: summ(T[T.sym == s]) for s in SYMS}}
        res.append(r)
        a = r["all"]
        print(f"{p['level']:>3} run{p['run']:.4f} tol{p['tol']:.4f} green{int(p['green'])} room{p['room']} {p['exit']:<9} "
              f"n{a['n']:>4} win{a['win']:.2f} R{a['avgR']:+.2f} Rs/tr{a['rs_trade']:+6.0f} 23-24 {a['a']:+9.0f} 25-26 {a['b']:+9.0f} "
              f"| twin R{r['twin']['avgR']:+.2f} Rs/tr{r['twin']['rs_trade']:+6.0f}", flush=True)
    json.dump(res, open(os.path.join(HERE, "round72_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
