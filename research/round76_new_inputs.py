"""Round 76: three inputs Autopilot has never used, tested on Autopilot's own trades (user, 3 Nov 2026).

  IV vs realised  India VIX at the open vs the index's realised volatility (last 10 / 20 sessions of 5-min returns,
                  annualised): options cheap or dear vs how much the index really moves; plus VIX's own 1-year percentile
  Global cues     last US session (S&P 500, Nasdaq, Dow close vs previous close; it ends at ~01:30 IST before India opens)
                  and today's Asian open (Nikkei, Hang Seng open vs previous close, both open before 09:15 IST) - with or
                  against the trade's side, and big (> 0.5%) or not
  FII positioning NSE participant-wise OI from the previous day: FII index-futures long share (long / (long + short)),
                  its 1-day change, FII index call vs put long, client (retail) futures long share - with / against the side
Method (same as round 73): every trade of today's Autopilot (Jan 2023 - Oct 2026, Nifty + Bank Nifty + Sensex) gets the
features; per-lot P&L by bucket and year; a bucket that is better (or worse) than the rest in EVERY year becomes a candidate;
candidates are replayed as size tilts (1.2x / 0.85x, 1.3x / 0.7x) and as skips in the Outcomes replay at Rs 2L and Rs 20k.
Pass = better in 2023, 2024, 2025, 2026 and the full period at Rs 2L with no deeper worst fall, plus a 40-run shuffle control.
Trades with no data for a feature (global before its file starts, FII before the backfill) keep size 1x.
Usage: python research/round76_new_inputs.py
"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
DATA = os.path.join(HERE, "data")
import numpy as np, pandas as pd
import round73_regime_tilts as R73
import outcomes as O
from fno import portfolio_bt as PB

YEARS = ("2023", "2024", "2025", "2026")


# ------------------------------------------------------------------ feature sources (only data known before the entry)
def vix_open():
    v = pd.read_csv(os.path.join(DATA, "INDIAVIX_1m_upstox.csv.gz"), parse_dates=["ts"]).set_index("ts").close
    v = v[~v.index.duplicated()].sort_index()
    return v.groupby(v.index.normalize()).first()


def realised(sym):
    c = O.candles(sym).close
    r = np.log(c).diff()
    d = c.index.normalize()
    r[d != np.r_[d[:1], d[:-1]]] = np.nan                 # drop the overnight gap
    dv = (r ** 2).groupby(d).sum()                         # daily realised variance
    out = {}
    for n in (10, 20):
        out[n] = np.sqrt(dv.rolling(n).mean().shift(1) * 252) * 100   # up to yesterday, in VIX units
    return out


def global_daily():
    """{sym: daily OHLC}; the long backfill when present, else the Oct-2023+ files."""
    f = os.path.join(DATA, "global_long_1d.csv.gz")
    names = {"^GSPC": "SP500", "^IXIC": "NASDAQ", "^DJI": "DOW", "^N225": "NIKKEI", "^HSI": "HANGSENG"}
    out = {}
    if os.path.exists(f):
        g = pd.read_csv(f)
        for s, x in g.groupby("sym"):
            out[names[s]] = x.set_index(pd.to_datetime(x.ts))[["open", "close"]].sort_index()
    for s in names.values():
        p = os.path.join(DATA, f"{s}_1d.csv.gz")
        if os.path.exists(p):
            x = pd.read_csv(p, parse_dates=["ts"]).set_index("ts")[["open", "close"]].sort_index()
            x.index = x.index.normalize()
            out[s] = x if s not in out else pd.concat([out[s], x[x.index > out[s].index[-1]]])
    return out


def participant():
    f = os.path.join(DATA, "participant_oi_hist.csv.gz")
    if not os.path.exists(f):
        return None
    p = pd.read_csv(f)
    p.columns = [c.strip() for c in p.columns]
    p["Client Type"] = p["Client Type"].str.strip()
    w = {}
    for ct in ("FII", "Client"):
        x = p[p["Client Type"] == ct].set_index("date").sort_index()
        w[ct] = x
    fi, cl = w["FII"], w["Client"]
    d = pd.DataFrame(index=pd.to_datetime(fi.index))
    d["fii_fut_long"] = (fi["Future Index Long"] / (fi["Future Index Long"] + fi["Future Index Short"])).values
    d["fii_opt_cp"] = ((fi["Option Index Call Long"] - fi["Option Index Call Short"]) - (fi["Option Index Put Long"] - fi["Option Index Put Short"])).values
    c2 = cl.reindex(fi.index)
    d["cli_fut_long"] = (c2["Future Index Long"] / (c2["Future Index Long"] + c2["Future Index Short"])).values
    return d.sort_index()


# ------------------------------------------------------------------ per-trade features
def features(T):
    V = vix_open()
    RV = {s: realised(s) for s in O.SYMS}
    G = global_daily()
    P = participant()
    rows = []
    for r in T.itertuples(index=False):
        dd = pd.Timestamp(r.date)
        side = 1 if r.side == "CALL" else -1
        f = {}
        vo = V.get(dd, np.nan)
        hist = V[V.index < dd]
        if vo == vo:
            for n in (10, 20):
                rv = RV[r.sym][n].get(dd, np.nan)
                if rv == rv and rv > 0:
                    ratio = vo / rv
                    f[f"iv_rv{n}"] = "cheap" if ratio < 0.9 else ("dear" if ratio > 1.3 else "fair")
            if len(hist) >= 120:
                p = (hist.iloc[-250:] < vo).mean()
                f["vix_pct"] = "low" if p < 1 / 3 else ("high" if p > 2 / 3 else "mid")
        for us in ("SP500", "NASDAQ", "DOW"):
            x = G.get(us)
            if x is None:
                continue
            x = x[x.index < dd]                              # last US session that closed before India's open
            if len(x) >= 2 and (dd - x.index[-1]).days <= 4 and x.index[0] < dd - pd.Timedelta(days=5):
                ret = x.close.iloc[-1] / x.close.iloc[-2] - 1
                if abs(ret) < 0.001:
                    f[f"{us}_side"] = "flat"
                else:
                    f[f"{us}_side"] = ("with" if np.sign(ret) == side else "against") + ("_big" if abs(ret) > 0.005 else "")
        for asia in ("NIKKEI", "HANGSENG"):
            x = G.get(asia)
            if x is None or dd not in x.index:
                continue
            i = x.index.get_loc(dd)
            if i >= 1 and x.index[0] < dd - pd.Timedelta(days=5):
                g = x.open.iloc[i] / x.close.iloc[i - 1] - 1
                f[f"{asia}_open"] = "flat" if abs(g) < 0.002 else (("with" if np.sign(g) == side else "against") + ("_big" if abs(g) > 0.007 else ""))
        if P is not None:
            x = P[P.index < dd]
            if len(x) >= 2 and (dd - x.index[-1]).days <= 5:
                fl, fl0 = x.fii_fut_long.iloc[-1], x.fii_fut_long.iloc[-2]
                f["fii_fut"] = ("with" if (fl - 0.5) * side > 0 else "against") + ("_strong" if abs(fl - 0.5) > 0.2 else "")
                ch = fl - fl0
                f["fii_fut_chg"] = "flat" if abs(ch) < 0.01 else ("with" if ch * side > 0 else "against")
                f["fii_opt"] = "with" if x.fii_opt_cp.iloc[-1] * side > 0 else "against"
                cf = x.cli_fut_long.iloc[-1]
                f["client_fut"] = "with" if (cf - 0.5) * side > 0 else "against"
        rows.append(f)
    return pd.DataFrame(rows, index=T.index)


def main():
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    base = base[base.date <= R73.PERIODS["full"][1]].reset_index(drop=True)
    F = features(base)
    base = pd.concat([base, F], axis=1)
    base["year"] = base.date.str[:4]
    res = {"coverage": {c: {"n": int(base[c].notna().sum()), "first": base.loc[base[c].notna(), "date"].min()} for c in F.columns},
           "stats": {}, "cands": [], "replay": {}}
    print("coverage:", res["coverage"])
    print("per-lot P&L by bucket (Rs / trade): 2023 2024 2025 2026 | n")
    for col in F.columns:
        g = base.groupby([col, "year"]).pnl.mean().unstack()
        n = base.groupby(col).size()
        res["stats"][col] = {"mean": g.round(0).to_dict(orient="index"), "n": n.to_dict()}
        print(f"-- {col}")
        for b_, row in g.iterrows():
            print(f"   {str(b_):<13} " + " ".join(f"{row.get(y, np.nan):+7.0f}" for y in YEARS) + f" | {n[b_]}")
    cands = []
    for col in F.columns:
        has = base[base[col].notna()]
        ys = [y for y in YEARS if (has.year == y).sum() >= 40]
        if len(ys) < 3:
            continue
        for b_ in has[col].unique():
            inb, outb = has[has[col] == b_], has[has[col] != b_]
            if len(inb) < 60:
                continue
            mi, mo = inb.groupby("year").pnl.mean(), outb.groupby("year").pnl.mean()
            yy = [y for y in ys if y in mi and y in mo]
            if len(yy) < len(ys):
                continue
            if all(mi[y] > mo[y] for y in yy):
                cands.append((col, b_, "up"))
            elif all(mi[y] < mo[y] for y in yy):
                cands.append((col, b_, "down"))
    res["cands"] = [list(c) for c in cands]
    print("\nbuckets consistent in every year with data:", cands)
    caps = (200_000, 20_000)
    baseT = base.copy(); baseT["xm"] = 1.0
    A = alt.assign(xm=1.0) if alt is not None else None
    ref = {cap: {p: R73.run(baseT, A, cap, a, b) for p, (a, b) in R73.PERIODS.items()} for cap in caps}
    res["replay"]["Autopilot today"] = ref
    print("\nAutopilot today Rs2L:", {p: ref[200_000][p]["final"] for p in R73.PERIODS}, "fall", ref[200_000]["full"]["dd"])
    rng = np.random.default_rng(11)
    for col, b_, way in cands:
        versions = [(f"x{u}/{d}", u, d) for u, d in ((1.2, 0.85), (1.3, 0.7))] + [("skip", None, None)]
        for lab0, up, dn in versions:
            T2 = baseT.copy()
            if lab0 == "skip":
                if way == "up":
                    continue
                T2 = T2[T2[col] != b_]
            else:
                mult = up if way == "up" else dn
                T2.loc[T2[col] == b_, "xm"] = mult
            r = {cap: {p: R73.run(T2, A, cap, a, b) for p, (a, b) in R73.PERIODS.items()} for cap in caps}
            ok = all(r[200_000][p]["final"] > ref[200_000][p]["final"] for p in R73.PERIODS) and r[200_000]["full"]["dd"] >= ref[200_000]["full"]["dd"] - 0.5
            better = 0
            if lab0 == "skip":
                m = int((baseT[col] == b_).sum())
                for _ in range(40):
                    drop = rng.choice(baseT.index[baseT[col].notna()], m, replace=False)
                    better += R73.run(baseT.drop(drop), A, 200_000, *R73.PERIODS["full"])["final"] >= r[200_000]["full"]["final"]
            else:
                xs = T2["xm"].values
                for _ in range(40):
                    T3 = baseT.copy(); T3["xm"] = rng.permutation(xs)
                    better += R73.run(T3, A, 200_000, *R73.PERIODS["full"])["final"] >= r[200_000]["full"]["final"]
            lab = f"{col}={b_} {lab0}"
            res["replay"][lab] = {**{str(c): v for c, v in r.items()}, "pass": ok, "shuffle_p": better / 40}
            print(f"{lab:<34} " + " ".join(f"{p}:{r[200_000][p]['final'] / 1e5:5.2f}L" for p in R73.PERIODS) +
                  f" fall {r[200_000]['full']['dd']:+.1f}% | 20k full {r[20_000]['full']['final'] / 1e5:.2f}L | pass {ok} | shuffle p {better / 40:.2f}", flush=True)
    json.dump(res, open(os.path.join(HERE, "round76_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
