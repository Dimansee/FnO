"""Round 68: open interest (OI) - as levels and as filters on today's Autopilot (1 Nov 2026).

User: "one more thing if we also use OI data in this" (on top of round 67's CPR / pivots / previous-day / S/R levels).
Data: NSE option bhavcopy (research/data/OPT_bhavcopy.csv.gz): every Nifty and Bank Nifty strike's daily OI, 9 Oct 2023 -
9 Oct 2026. Sensex is not in it. Intraday OI exists only for the few recorded weeks (too short).
Features for trading day D use only the bhavcopy of the previous trading day (no look-ahead), nearest expiry after that day,
strikes within 5% of the close:
  call wall (largest call OI strike) and the 2nd one, put wall and the 2nd one, max pain (strike where option buyers get least),
  PCR = put OI / call OI, its change from the day before (same expiry), and OI build-up near the money (strikes within 2%):
  (new put OI - new call OI) / total OI there  (> 0: writers adding puts = support building, bullish reading).
Part C - filters on TODAY's Autopilot (Outcomes-tab replay: Rs 2L, compounding, conviction sizing, all brakes and gates):
  Nifty and Bank Nifty trades are skipped when the OI reading disagrees; Sensex trades unchanged. Periods 10 Oct 2023 - 2024 and
  2025 - 9 Oct 2026. Control: the same number of eligible trades skipped at random, 200 times -> p.
  User's rule: adopt only if Autopilot gets better in BOTH periods (and it must beat random skipping).
Part B - OI walls and max pain as a level family in the round-67 grid (break / retest / bounce / false break, confluence with
  CPR / pivots / previous day / S/R / opening range), Nifty and Bank Nifty 5m and 15m, Oct 2023 - Oct 2026.
Usage: python research/round68_oi.py <data_dir> [C|B|both]
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

DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data")
PART = sys.argv[2] if len(sys.argv) > 2 else "both"
SYMS = ["NIFTY", "BANKNIFTY"]


def daily_close(sym):
    a = pd.read_csv(f"{DATA}/{sym}_1m_upstox.csv.gz", parse_dates=["ts"]).set_index("ts")
    a = a[~a.index.duplicated()].sort_index()
    return a["close"].groupby(a.index.normalize()).last()


def oi_features(sym):
    B = pd.read_csv(f"{DATA}/OPT_bhavcopy.csv.gz", parse_dates=["date", "expiry"])
    B = B[B.sym == sym]
    close = daily_close(sym)
    rows = []
    prev = None
    for d, g in B.groupby("date"):
        if d not in close.index:
            continue
        spot = float(close.loc[d])
        exp = g.expiry[g.expiry > d].min()
        if pd.isna(exp):
            continue
        e = g[(g.expiry == exp) & (abs(g.strike / spot - 1) <= 0.05)]
        ce = e[e.opt == "CE"].groupby("strike").oi.sum()
        pe = e[e.opt == "PE"].groupby("strike").oi.sum()
        if ce.sum() <= 0 or pe.sum() <= 0:
            continue
        cs, ps = ce.sort_values(ascending=False), pe.sort_values(ascending=False)
        ks = np.array(sorted(set(ce.index) | set(pe.index)))
        cev, pev = ce.reindex(ks, fill_value=0).values, pe.reindex(ks, fill_value=0).values
        pay = [(cev * np.maximum(K - ks, 0)).sum() + (pev * np.maximum(ks - K, 0)).sum() for K in ks]
        near = abs(ks / spot - 1) <= 0.02
        r = {"date": d, "expiry": exp, "spot": spot, "cw1": cs.index[0], "cw2": cs.index[1] if len(cs) > 1 else np.nan,
             "pw1": ps.index[0], "pw2": ps.index[1] if len(ps) > 1 else np.nan, "maxpain": ks[int(np.argmin(pay))],
             "pcr": pe.sum() / ce.sum(), "ce_near": cev[near].sum(), "pe_near": pev[near].sum(), "ce_by_k": dict(zip(ks, cev)), "pe_by_k": dict(zip(ks, pev))}
        if prev is not None and prev["expiry"] == exp:
            r["dpcr"] = r["pcr"] - prev["pcr"]
            kk = ks[near]
            dc = sum(r["ce_by_k"].get(k, 0) - prev["ce_by_k"].get(k, 0) for k in kk)
            dp = sum(r["pe_by_k"].get(k, 0) - prev["pe_by_k"].get(k, 0) for k in kk)
            tot = r["ce_near"] + r["pe_near"]
            r["build"] = (dp - dc) / tot if tot else np.nan
        else:
            r["dpcr"], r["build"] = np.nan, np.nan
        prev = r
        rows.append({k: v for k, v in r.items() if k not in ("ce_by_k", "pe_by_k")})
    F = pd.DataFrame(rows).set_index("date").sort_index()
    F["pcr_med20"] = F.pcr.rolling(20, min_periods=10).median()
    # features of bhav day t are used on the next index session
    sessions = close.index
    nxt = {t: sessions[sessions > t][0] for t in F.index if (sessions > t).any()}
    F = F[F.index.isin(list(nxt))]
    F.index = pd.DatetimeIndex([nxt[t] for t in F.index])
    return F


# ------------------------------------------------------------------ part C: filters on today's Autopilot
def part_c():
    import outcomes as O
    from fno import portfolio_bt as PB
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    T = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    A = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    feats = {s: oi_features(s) for s in SYMS}
    for s, F in feats.items():
        print(s, "OI days", len(F), F.index.min().date(), "->", F.index.max().date(), "| PCR median", round(F.pcr.median(), 2), flush=True)

    def attach(X):
        X = X.copy()
        X["d"] = pd.to_datetime(X.date)
        for c in ("cw1", "pw1", "maxpain", "pcr", "dpcr", "build", "pcr_med20"):
            X[c] = np.nan
        for s, F in feats.items():
            m = X.sym == s
            Fx = F.reindex(X.loc[m, "d"])
            for c in ("cw1", "pw1", "maxpain", "pcr", "dpcr", "build", "pcr_med20"):
                X.loc[m, c] = Fx[c].values
        X["call"] = X.side == "CALL"
        return X
    T, A = attach(T), (attach(A) if A is not None else None)
    P = {"Oct 2023 - 2024": ("2023-10-10", "2024-12-31"), "2025 - Oct 2026": ("2025-01-01", "2026-10-09"), "both together": ("2023-10-10", "2026-10-09")}

    def skips(X):
        c, sp = X.call, X.spot_in
        gap_c = (X.cw1 - sp) / sp
        gap_p = (sp - X.pw1) / sp
        f = {
            "PCR agrees (calls only if PCR >= 1, puts if < 1)": np.where(c, X.pcr < 1, X.pcr >= 1),
            "PCR vs its 20-day median agrees": np.where(c, X.pcr < X.pcr_med20, X.pcr >= X.pcr_med20),
            "PCR change agrees (rising for calls)": np.where(c, X.dpcr < 0, X.dpcr > 0),
            "OI build-up agrees (puts added for calls)": np.where(c, X.build < 0, X.build > 0),
            "PCR and build-up both agree": np.where(c, (X.pcr < 1) | (X.build < 0), (X.pcr >= 1) | (X.build > 0)),
            "Skip calls within 0.3% below the call wall / puts within 0.3% above the put wall": np.where(c, (gap_c > 0) & (gap_c < 0.003), (gap_p > 0) & (gap_p < 0.003)),
            "Skip calls within 0.5% below the call wall / puts within 0.5% above the put wall": np.where(c, (gap_c > 0) & (gap_c < 0.005), (gap_p > 0) & (gap_p < 0.005)),
            "Only toward max pain (calls below it, puts above it, 0.3% band)": np.where(c, sp > X.maxpain * 1.003, sp < X.maxpain * 0.997),
            "Only away from max pain": np.where(c, sp < X.maxpain * 0.997, sp > X.maxpain * 1.003),
            "Only through the wall (calls above the call wall, puts below the put wall)": np.where(c, sp <= X.cw1, sp >= X.pw1),
        }
        has = X.pcr.notna().values
        return {k: (np.asarray(v, bool) & has) for k, v in f.items()}

    def replay(X, Y, cap, a, b):
        X = X[(X.date >= a) & (X.date <= b)]
        Y = Y[(Y.date >= a) & (Y.date <= b)] if Y is not None else None
        q = {**B0, "capital": cap}
        R = PB.replay(X, q["capital"], q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"], q["dd_half"], Y)
        s = PB._summary(R, cap)
        return s["final"], s["max_dd_pct"], s["trades"]

    rng = np.random.default_rng(68)
    SK = skips(T)
    keyof = lambda X: list(zip(X.sym, X.date, X.entry_time, X.strategy))      # noqa: E731
    res = {}
    for cap in (200_000,):
        for pn, (a, b) in P.items():
            base = replay(T, A, cap, a, b)
            inper = ((T.date >= a) & (T.date <= b)).values
            elig = np.flatnonzero(inper & T.pcr.notna().values)
            out = {"Autopilot today": {"final": base[0], "fall": base[1], "trades": base[2]}}
            print(f"\n== Rs {cap:,} | {pn}: Autopilot today Rs {base[0]:,.0f} (fall {base[1]:+.1f}%, {base[2]} trades)", flush=True)
            for fn, m in SK.items():
                drop = m & inper
                X = T[~drop]
                Y = A[~pd.Series(keyof(A)).isin(set(keyof(T[drop]))).values] if A is not None else None
                r = replay(X, Y, cap, a, b)
                nd = int(drop.sum())
                sims = []
                if pn != "both together":
                    for _ in range(200):
                        rd = np.zeros(len(T), bool)
                        rd[rng.choice(elig, nd, replace=False)] = True
                        Xr = T[~rd]
                        Yr = A[~pd.Series(keyof(A)).isin(set(keyof(T[rd]))).values] if A is not None else None
                        sims.append(replay(Xr, Yr, cap, a, b)[0])
                p = float(np.mean(np.array(sims) >= r[0])) if sims else None
                out[fn] = {"final": r[0], "fall": r[1], "skipped": nd, "p_random": p, "random_median": float(np.median(sims)) if sims else None}
                print(f"  {fn:<86} Rs {r[0]:>12,.0f} ({'+' if r[0] >= base[0] else '-'}) fall {r[1]:+.1f}% skipped {nd:>3}" + (f"  random-skip median Rs {np.median(sims):,.0f}, p {p:.2f}" if sims else ""), flush=True)
            res[f"{cap}|{pn}"] = out
    json.dump(res, open(os.path.join(HERE, "round68_filters_results.json"), "w"), indent=1, default=str)


# ------------------------------------------------------------------ part B: OI walls as levels in the round-67 grid
def part_b():
    import round67_levels_grid as G
    G.DATA = DATA
    G.S.DATA = DATA
    G.SYMS = SYMS
    G.OUT = os.path.join(HERE, "round68_levels_results.json")
    G.FAMS = G.FAMS + ["oi"]
    feats = {s: oi_features(s) for s in SYMS}
    _load, _levels = G.load, G.day_levels

    def load(sym, tf):
        df = _load(sym, tf)
        return df[df.index >= "2023-10-10"]

    def day_levels(df):
        LV, fam, width, bias = _levels(df)
        sym = next(s for s in SYMS if abs(df["close"].iloc[-1] / daily_close(s).iloc[-1] - 1) < 0.02)
        F = feats[sym].reindex(df.index.normalize())
        X = F[["cw1", "cw2", "pw1", "pw2", "maxpain"]].values
        return np.hstack([LV, X]), np.concatenate([fam, ["oi"] * 5]), width, bias
    G.load, G.day_levels = load, day_levels
    G.main()


if __name__ == "__main__":
    if PART in ("C", "both"):
        part_c()
    if PART in ("B", "both"):
        part_b()
