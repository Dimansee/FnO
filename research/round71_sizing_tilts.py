"""Round 71: the remaining combinations - size today's Autopilot trades up or down instead of adding trades (1 Nov 2026).

Why: every extra rule since round 63 failed the same way - its trades took cash from Autopilot's bigger trend trades. Here no
trade is added or removed; Autopilot's own trades are sized up or down (risk x tilt) by readings the research found useful:
  cpr     narrow CPR (< 0.25%) day: x1.3, wide CPR (> 0.5%): x0.7                       (round 67: breaks work on narrow days)
  gamma   dealer gamma from the previous day's option OI (Nifty / Bank Nifty): net GEX = sum over strikes of
          (call OI - put OI) x gamma x S^2 x 1%  (nearest two expiries, strikes within 10%, gamma from Black-Scholes with
          India VIX). Short gamma (GEX < 0: dealers chase moves -> trend days): x1.3; long gamma (> 0: moves get pushed back): x0.7
  oiwall  trade goes through a broken OI wall (call above the call wall / put below the put wall): x1.3;
          trade runs into the wall within 0.3%: x0.7                                    (rounds 68 and 70)
  combined  score = sum of the three (+1 / -1 each), risk x (1 + 0.15 x score), kept between 0.55 and 1.45
Also the stronger version of each (x1.5 / x0.5).
Harness: Outcomes-tab replay (fno.portfolio_bt.replay, today's BASELINE, Rs 2L, compounding). Tilts work by scaling each trade's
risk per lot. Control: the same multipliers shuffled at random across the same trades, 200 times -> p.
User's rule: adopt only if better in EVERY period.  Usage: python research/round71_sizing_tilts.py <data_dir>
"""
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import numpy as np
import pandas as pd
from scipy.stats import norm

DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data")
import round68_oi as OI  # noqa: E402
OI.DATA = DATA


def cpr_width(sym):
    a = pd.read_csv(f"{DATA}/{sym}_1m_upstox.csv.gz", parse_dates=["ts"]).set_index("ts")
    a = a[~a.index.duplicated()].sort_index().between_time("09:15", "15:29")
    g = a.groupby(a.index.normalize()).agg(H=("high", "max"), L=("low", "min"), C=("close", "last"))
    P = (g.H + g.L + g.C) / 3
    BC = (g.H + g.L) / 2
    TC = 2 * P - BC
    w = (abs(TC - BC) / P * 100).shift(1)          # yesterday's range -> today's CPR
    return w


def gex(sym):
    B = pd.read_csv(f"{DATA}/OPT_bhavcopy.csv.gz", parse_dates=["date", "expiry"])
    B = B[B.sym == sym]
    close = OI.daily_close(sym)
    vix = pd.read_csv(f"{DATA}/INDIAVIX_1d.csv.gz", parse_dates=["ts"]).set_index("ts")["close"]
    vix.index = pd.to_datetime(vix.index).normalize()
    out = {}
    for d, g in B.groupby("date"):
        if d not in close.index:
            continue
        S = float(close.loc[d])
        iv = float(vix.asof(d)) / 100 * (1.15 if sym == "BANKNIFTY" else 1.0)
        exps = sorted(g.expiry[g.expiry > d].unique())[:2]
        e = g[g.expiry.isin(exps) & (abs(g.strike / S - 1) <= 0.10)]
        T = np.maximum((e.expiry - d).dt.days.values + 0.6, 0.3) / 365
        K = e.strike.values
        d1 = (np.log(S / K) + 0.5 * iv * iv * T) / (iv * np.sqrt(T))
        gam = norm.pdf(d1) / (S * iv * np.sqrt(T))
        sign = np.where(e.opt.values == "CE", 1.0, -1.0)
        out[d] = float((sign * e.oi.values * gam).sum() * S * S * 0.01)
    s = pd.Series(out).sort_index()
    sessions = close.index
    nxt = {t: sessions[sessions > t][0] for t in s.index if (sessions > t).any()}
    s = s[s.index.isin(list(nxt))]
    s.index = pd.DatetimeIndex([nxt[t] for t in s.index])
    return s


def main():
    import outcomes as O
    from fno import portfolio_bt as PB
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    T = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    A = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    W = {s: cpr_width(s) for s in O.SYMS}
    G = {s: gex(s) for s in ("NIFTY", "BANKNIFTY")}
    F = {s: OI.oi_features(s) for s in ("NIFTY", "BANKNIFTY")}
    for s, g in G.items():
        print(s, "GEX days", len(g), "short-gamma share", round(float((g < 0).mean()), 2), flush=True)

    def feats(X):
        X = X.copy()
        d = pd.to_datetime(X.date)
        X["w"] = np.nan; X["g"] = np.nan; X["cw1"] = np.nan; X["pw1"] = np.nan
        for s in O.SYMS:
            m = (X.sym == s).values
            X.loc[m, "w"] = W[s].reindex(d[m]).values
            if s in G:
                X.loc[m, "g"] = G[s].reindex(d[m]).values
                Fx = F[s].reindex(d[m])
                X.loc[m, "cw1"] = Fx.cw1.values; X.loc[m, "pw1"] = Fx.pw1.values
        c = X.side == "CALL"
        sc_cpr = np.where(X.w < 0.25, 1, np.where(X.w > 0.5, -1, 0))
        sc_g = np.where(X.g < 0, 1, np.where(X.g > 0, -1, 0))
        thru = np.where(c, X.spot_in > X.cw1, X.spot_in < X.pw1)
        gc, gp = (X.cw1 - X.spot_in) / X.spot_in, (X.spot_in - X.pw1) / X.spot_in
        into = np.where(c, (gc > 0) & (gc < 0.003), (gp > 0) & (gp < 0.003))
        sc_w = np.where(X.cw1.notna() & thru, 1, np.where(X.cw1.notna() & into, -1, 0))
        X["sc_cpr"], X["sc_g"], X["sc_w"] = sc_cpr, sc_g, sc_w
        return X
    T = feats(T)
    A = feats(A) if A is not None else None

    def tilt_series(X, name, up, dn):
        if name == "combined":
            sc = X.sc_cpr + X.sc_g + X.sc_w
            return np.clip(1 + (up - 1) / 2 * sc, 0.55, 1.45)
        col = {"cpr": "sc_cpr", "gamma": "sc_g", "oiwall": "sc_w"}[name]
        return np.where(X[col] > 0, up, np.where(X[col] < 0, dn, 1.0))

    def apply(X, m):
        X = X.copy()
        X["risk_lot"] = X.risk_lot / m
        X["risk_lot_capped"] = X.risk_lot_capped / m
        return X

    keyof = lambda X: list(zip(X.sym, X.date, X.entry_time, X.strategy))      # noqa: E731

    def replay(X, Y, a, b, cap=200_000):
        X = X[(X.date >= a) & (X.date <= b)]
        Y = Y[(Y.date >= a) & (Y.date <= b)] if Y is not None else None
        q = {**B0, "capital": cap}
        R = PB.replay(X, cap, q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"], q["dd_half"], Y)
        s = PB._summary(R, cap)
        return s["final"], s["max_dd_pct"]

    rng = np.random.default_rng(71)
    P_all = {"2023-24": ("2023-01-02", "2024-12-31"), "2025": ("2025-01-01", "2025-12-31"), "2026": ("2026-01-01", "2026-10-23"), "2023 - Oct 2026": ("2023-01-02", "2026-10-23")}
    P_oi = {"Oct 2023-24": ("2023-10-10", "2024-12-31"), "2025": ("2025-01-01", "2025-12-31"), "2026": ("2026-01-01", "2026-10-09"), "Oct 2023 - Oct 2026": ("2023-10-10", "2026-10-09")}
    res = {}
    for name in ("cpr", "gamma", "oiwall", "combined"):
        for up, dn in ((1.3, 0.7), (1.5, 0.5)):
            P = P_all if name == "cpr" else P_oi
            m = tilt_series(T, name, up, dn)
            mA = None
            if A is not None:
                mp = dict(zip(keyof(T), m))
                mA = np.array([mp.get(kk, 1.0) for kk in keyof(A)])
            Tm, Am = apply(T, m), (apply(A, mA) if A is not None else None)
            row = {}
            line = f"{name:<9} x{up}/x{dn}:"
            for pn, (a, b) in P.items():
                base = replay(T, A, a, b)
                r = replay(Tm, Am, a, b)
                inper = ((T.date >= a) & (T.date <= b)).values
                sims = []
                if pn not in ("2023 - Oct 2026", "Oct 2023 - Oct 2026"):
                    idx = np.flatnonzero(inper & (m != 1.0) | inper)
                    for _ in range(100):
                        ms = m.copy()
                        sel = np.flatnonzero(inper)
                        ms[sel] = rng.permutation(m[sel])
                        mpS = dict(zip(keyof(T), ms))
                        mAS = np.array([mpS.get(kk, 1.0) for kk in keyof(A)]) if A is not None else None
                        sims.append(replay(apply(T, ms), apply(A, mAS) if A is not None else None, a, b)[0])
                p = float(np.mean(np.array(sims) >= r[0])) if sims else None
                row[pn] = {"base": base[0], "base_fall": base[1], "tilt": r[0], "tilt_fall": r[1], "p": p}
                line += f" | {pn} {r[0]/1e5:.2f}L vs {base[0]/1e5:.2f}L ({r[1]:.0f}% vs {base[1]:.0f}%)" + (f" p{p:.2f}" if p is not None else "")
            row["better_every_period"] = all(v["tilt"] > v["base"] for v in row.values() if isinstance(v, dict))
            res[f"{name} x{up}/x{dn}"] = row
            print(line + ("  <== better in every period" if row["better_every_period"] else ""), flush=True)
    json.dump(res, open(os.path.join(HERE, "round71_results.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
