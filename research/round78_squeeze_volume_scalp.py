"""Round 78: user's scalp idea (3 Nov 2026) - when the market consolidates and volume suddenly comes in, take the break;
target 10-15 points on the option PREMIUM, at least 2 lots every time.

Data: 1-minute index candles Jan 2023 - Oct 2026 (Nifty, Bank Nifty, Sensex). Index candles have no volume, so "volume comes in"
uses the heavyweight-stock proxy of rounds 9/34: each heavyweight's 5-minute volume divided by its median volume at the same time
of day over the previous 20 sessions, averaged over the stocks (Nifty / Sensex: 10 Nifty heavyweights; Bank Nifty: HDFC Bank,
ICICI Bank, SBI, Axis Bank). Known only when the 5-minute bar has closed.

Rule (calls; puts mirror):
  consolidation  the last N 1-min bars (N = 15 or 30) have a high-low range <= W% of price (W = 0.10% / 0.15%; Bank Nifty x1.3)
  break          this 1-min bar closes above the box high (below the low for puts), 09:30-14:45
  volume         the last completed 5-min heavyweight volume ratio >= V (none / 1.3 / 1.5)
  entry          at this bar's close, ATM option (weekly; Bank Nifty monthly), 2 lots fixed
  target         premium +10 or +15 points (converted to an index level with the entry delta and the app's premium scale)
  stop           box low (index) or premium -10 points; time stop 20 minutes; 15:15 square-off
  sides          both, or calls only ("gets high anytime up")
Pricing like the app (calibrated Black-Scholes + real-price decay adjustment, research/round34.price); slippage 0.25% and 0.5%
per fill + real charges. Random-direction twin. Best version picked on 2023-24 only and checked on 2025-26.
Usage: python research/round78_squeeze_volume_scalp.py
"""
import itertools, json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
from datetime import time
import numpy as np, pandas as pd
import round34 as R34
from fno import pricing as PR, market as D

DATA = os.path.join(HERE, "data")
SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
HEAVY = {"NIFTY": ["HDFCBANK", "RELIANCE", "ICICIBANK", "INFY", "TCS", "LT", "SBIN", "AXISBANK", "BHARTIARTL", "ITC"],
         "BANKNIFTY": ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK"]}
HEAVY["SENSEX"] = HEAVY["NIFTY"]
GRID = dict(n=[15, 30], w=[0.0010, 0.0015], vol=[0.0, 1.3, 1.5], tgt=[10.0, 15.0], stop=["box", "prem10"], sides=["both", "calls"])
TSTOP = int(os.environ.get("R78_TSTOP", "20"))
END = "2026-10-30"


def rel_volume(stocks):
    parts = []
    for s in stocks:
        x = pd.read_csv(os.path.join(DATA, f"{s}_5m_upstox.csv.gz"), parse_dates=["ts"]).set_index("ts")["volume"]
        x = x[~x.index.duplicated()].sort_index()
        tod = x.index.strftime("%H:%M")
        med = x.groupby(tod).transform(lambda v: v.shift(1).rolling(20, min_periods=10).median())
        parts.append((x / med).rename(s))
    r = pd.concat(parts, axis=1).mean(axis=1)
    r.index = r.index + pd.Timedelta(minutes=5)          # known at the bar's close
    return r


def frame(sym, rel):
    a = R34.m1(sym)
    a = a[a.index <= END + " 15:30"]
    idx = a.index
    o, h, l, c = (a[k].values for k in ("open", "high", "low", "close"))
    mins = idx.hour.values * 60 + idx.minute.values
    day = idx.normalize().values.astype("datetime64[D]").astype(np.int64)
    # latest completed 5-min heavyweight ratio at each 1-min bar's close (bar start + 1 min)
    rv = rel.reindex(idx + pd.Timedelta(minutes=1), method="ffill").values
    return dict(a=a, idx=idx, o=o, h=h, l=l, c=c, mins=mins, day=day, rel=rv)


def boxes(F, n):
    """Rolling high/low of the previous n bars inside the same day (NaN when the box crosses the day start)."""
    hh = pd.Series(F["h"]).rolling(n).max().shift(1).values
    ll = pd.Series(F["l"]).rolling(n).min().shift(1).values
    d = F["day"]
    same = np.r_[np.zeros(n, bool), d[n:] == d[:-n]]
    hh[~same] = np.nan
    ll[~same] = np.nan
    return hh, ll


def signals(F, n, w, vol, sides, sym):
    hh, ll = boxes(F, n)
    c, mins = F["c"], F["mins"]
    wq = w * (1.3 if sym == "BANKNIFTY" else 1.0)
    tight = (hh - ll) / c <= wq
    win = (mins + 1 >= 570) & (mins + 1 <= 885)
    vok = np.ones(len(c), bool) if vol == 0 else (np.nan_to_num(F["rel"]) >= vol)
    up = tight & win & vok & (c > hh)
    dn = tight & win & vok & (c < ll) & (sides == "both")
    sig = np.zeros(len(c), np.int8)
    sig[up] = 1
    sig[dn] = -1
    return sig, hh, ll


def delta_scale(sym, F, i, side):
    """Index points per 1 premium point at entry (ATM delta x the app's premium-change scale)."""
    return 1.0 / (0.5 * PR.CHG_SCALE)


def simulate(F, sig, dist, tgt, stop, sym):
    o, h, l, c, mins, day = F["o"], F["h"], F["l"], F["c"], F["mins"], F["day"]
    rows, i, N = [], 0, len(c)
    k_pts = delta_scale(sym, F, 0, 1)
    nz = np.flatnonzero(sig)
    p = 0
    while p < len(nz):
        i = nz[p]
        s = int(sig[i])
        e = c[i]
        t_idx = e + s * tgt * k_pts
        sl = e - s * dist[i] if stop == "box" else e - s * 10.0 * k_pts
        j, xs, why = i, e, "square"
        k = i + 1
        while k < N and day[k] == day[i]:
            adv, fav = (l[k], h[k]) if s > 0 else (h[k], l[k])
            if s * (adv - sl) <= 0:
                j, xs, why = k, (sl if s * (o[k] - sl) > 0 else o[k]), "stop"; break
            if s * (fav - t_idx) >= 0:
                j, xs, why = k, (t_idx if s * (t_idx - o[k]) >= 0 else o[k]), "target"; break
            if k - i >= TSTOP:
                j, xs, why = k, c[k], "time"; break
            if mins[k] + 1 >= 915:
                j, xs, why = k, c[k], "square"; break
            k += 1
        else:
            j, xs = k - 1, c[k - 1]
        rows.append((i, j, xs, s, sl, why))
        while p < len(nz) and nz[p] <= j:              # one position at a time
            p += 1
    return pd.DataFrame(rows, columns=["i", "j", "xs", "side", "sl", "why"])


def money(sym, F, T, vix1, slip):
    p_in, p_out, risk, dte = R34.price(sym, {"idx": F["idx"], "c": F["c"]}, T, vix1)
    pnl, lots, chg = R34.money(sym, p_in, p_out, risk, slip, lots_mode="fixed", fixed_lots=2)
    return p_in, p_out, pnl, chg


def summ(T, col):
    if T.empty:
        return {"n": 0}
    y = T.groupby(T.date.str[:4])[col].sum().round(0).to_dict()
    return {"n": int(len(T)), "per_day": round(len(T) / max(T.date.nunique(), 1), 2), "win": round(float((T[col] > 0).mean()), 3),
            "hit_target": round(float((T.why == "target").mean()), 3), "prem_pts": round(float(T.prem_pts.mean()), 2),
            "rs_trade": round(float(T[col].mean())), "a": round(float(T[T.date < "2025"][col].sum())), "b": round(float(T[T.date >= "2025"][col].sum())),
            "years": y}


def main():
    vix1 = pd.read_csv(os.path.join(DATA, "INDIAVIX_1m_upstox.csv.gz"), parse_dates=["ts"]).set_index("ts").close
    vix1 = vix1[~vix1.index.duplicated()].sort_index()
    rels = {"NIFTY": rel_volume(HEAVY["NIFTY"]), "BANKNIFTY": rel_volume(HEAVY["BANKNIFTY"])}
    rels["SENSEX"] = rels["NIFTY"]
    FR = {s: frame(s, rels[s]) for s in SYMS}
    res = {"grid": []}
    keys = list(itertools.product(*GRID.values()))
    for kk, (n, w, vol, tgt, stop, sides) in enumerate(keys):
        parts, twins = [], []
        for sym in SYMS:
            F = FR[sym]
            sig, hh, ll = signals(F, n, w, vol, sides, sym)
            dist = np.where(sig > 0, F["c"] - ll, hh - F["c"])
            T = simulate(F, sig, dist, tgt, stop, sym)
            if T.empty:
                continue
            rng = np.random.default_rng(kk)
            sg2 = sig.copy()
            nzs = np.flatnonzero(sg2)
            sg2[nzs] = rng.choice(np.array([1, -1], np.int8), len(nzs)) if sides == "both" else 1
            # twin: same entry minutes, random side, mirrored box
            W = simulate(F, sg2, dist, tgt, stop, sym) if sides == "both" else None
            for X, out in ((T, parts), (W, twins)):
                if X is None or X.empty:
                    continue
                X["date"] = [str(x.date()) for x in F["idx"][X["i"].values]]
                X["sym"] = sym
                pi, po, pnl, chg = money(sym, F, X, vix1, 0.0025)
                X["prem_pts"] = po - pi
                X["rs"] = pnl
                X["rs_05"] = money(sym, F, X, vix1, 0.005)[2]
                out.append(X)
        T = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        W = pd.concat(twins, ignore_index=True) if twins else pd.DataFrame()
        r = {"n": n, "w": w, "vol": vol, "tgt": tgt, "stop": stop, "sides": sides, "all": summ(T, "rs"), "slip05": summ(T, "rs_05"),
             "twin": summ(W, "rs") if len(W) else None, "by_sym": {s: summ(T[T.sym == s], "rs") for s in SYMS} if len(T) else {}}
        res["grid"].append(r)
        a = r["all"]
        print(f"N{n} W{w:.2%} V{vol} T{tgt:g} {stop:<6} {sides:<5} n{a.get('n', 0):>6} /day{a.get('per_day', 0):5.2f} win{a.get('win', 0):.2f} "
              f"tgt{a.get('hit_target', 0):.2f} prem{a.get('prem_pts', 0):+6.2f} Rs/tr{a.get('rs_trade', 0):+6.0f} 23-24 {a.get('a', 0):+10,.0f} "
              f"25-26 {a.get('b', 0):+10,.0f} | 0.5% slip 23-24 {r['slip05'].get('a', 0):+10,.0f} 25-26 {r['slip05'].get('b', 0):+10,.0f}"
              + (f" | twin Rs/tr {r['twin'].get('rs_trade', 0):+.0f}" if r["twin"] else ""), flush=True)
    ok = [r for r in res["grid"] if r["all"].get("n", 0) >= 100]
    best = max(ok, key=lambda r: r["all"]["a"]) if ok else None
    res["chosen_on_2023_24"] = best
    pos = sum(1 for r in ok if r["all"]["a"] > 0 and r["all"]["b"] > 0)
    res["positive_both_halves"] = pos
    print(f"\n{len(ok)} versions; positive in both 2023-24 and 2025-26 at 0.25% slippage: {pos}")
    if best:
        print("best on 2023-24:", {k: best[k] for k in ("n", "w", "vol", "tgt", "stop", "sides")}, "2023-24", best["all"]["a"], "2025-26", best["all"]["b"],
              "years", best["all"]["years"], "by index", {s: v.get("rs_trade") for s, v in best["by_sym"].items()})
    json.dump(res, open(os.path.join(HERE, "round78_results.json" if TSTOP == 20 else f"round78_results_t{TSTOP}.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
