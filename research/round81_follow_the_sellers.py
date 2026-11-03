"""Round 81: how do option sellers work - and can a BUYER profit by taking the sellers' side (going opposite to the crowd of buyers)?

Background: in Indian index options most buyers (clients / retail) lose and writers (mostly Pro desks and FIIs) earn the
premium. Autopilot never sells (house rule), so the question is only whether the sellers' POSITIONING tells a buyer which way
to buy. Signals, all from the previous day's data (no look-ahead), turned into a direction for the next session:
  client_contra   NSE participant OI, Client net index-option position (call long - call short) - (put long - put short),
                  z-score vs its last 20 days; clients most bullish (z >= Z) -> buy puts, most bearish (z <= -Z) -> buy calls
  pro_follow      same measure for Pro (prop desks, the biggest writers) - go their way
  fii_follow      same measure for FII - go their way
  writers         bhavcopy OI build-up near the money (round 68): new put OI - new call OI within 2% of spot, / OI there;
                  puts being written (>= +T) -> sellers expect support -> buy calls; calls written (<= -T) -> buy puts
                  (Nifty and Bank Nifty from Oct 2023; Sensex uses Nifty's reading)
  combo           client_contra and writers agree
Trade A (stand-alone): next session, buy the ATM option at the 09:30 bar's open, stop 0.5% on the index, hold to 15:15
  (and a 1R-trail variant), priced like rounds 72/75 (app option model, 0.25% slippage, charges, Rs 2,000 risk), random twin.
Trade B (with Autopilot): Autopilot trades in the sellers' direction vs against, per year; anything better / worse in every
  year is replayed (1.3x / skip) at Rs 2L and Rs 20k with a shuffle control.
Usage: python research/round81_follow_the_sellers.py
"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round75_trend_continuation as R75
import round73_regime_tilts as R73
import round68_oi as R68
import outcomes as O
from fno import portfolio_bt as PB

DATA = os.path.join(HERE, "data")
SYMS = R75.SYMS
P = {**R73.PERIODS, "2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", "2026-10-30")}


def participant():
    p = pd.read_csv(os.path.join(DATA, "participant_oi_hist.csv.gz"), parse_dates=["date"])
    out = {}
    for who in ("Client", "Pro", "FII"):
        g = p[p["Client Type"].str.strip() == who].set_index("date").sort_index()
        net = (g["Option Index Call Long"] - g["Option Index Call Short"]) - (g["Option Index Put Long"] - g["Option Index Put Short"])
        z = (net - net.rolling(20, min_periods=10).mean().shift(1)) / net.rolling(20, min_periods=10).std().shift(1)
        out[who] = z[~z.index.duplicated(keep="last")]
    return pd.DataFrame(out)


def next_session_map(series_idx, sessions):
    return {t: sessions[sessions > t][0] for t in series_idx if (sessions > t).any()}


def directions(Z=1.0, T=0.05):
    """date (session to trade) -> {signal: +1 call / -1 put / 0} per index."""
    sess = pd.DatetimeIndex(sorted(R75.frame("NIFTY").index.normalize().unique()))
    pz = participant()
    mp = next_session_map(pz.index, sess)
    pz = pz[pz.index.isin(list(mp))]
    pz.index = pd.DatetimeIndex([mp[t] for t in pz.index])
    pz = pz[~pz.index.duplicated(keep="last")]
    w = {s: R68.oi_features(s)["build"] for s in ("NIFTY", "BANKNIFTY")}
    w["SENSEX"] = w["NIFTY"]
    D = {}
    for sym in SYMS:
        df = pd.DataFrame(index=sess)
        df["client_contra"] = np.where(pz.Client.reindex(sess) >= Z, -1, np.where(pz.Client.reindex(sess) <= -Z, 1, 0))
        df["pro_follow"] = np.where(pz.Pro.reindex(sess) >= Z, 1, np.where(pz.Pro.reindex(sess) <= -Z, -1, 0))
        df["fii_follow"] = np.where(pz.FII.reindex(sess) >= Z, 1, np.where(pz.FII.reindex(sess) <= -Z, -1, 0))
        b = w[sym][~w[sym].index.duplicated(keep="last")].reindex(sess)
        df["writers"] = np.where(b >= T, 1, np.where(b <= -T, -1, 0))
        df["combo"] = np.where((df.client_contra == df.writers) & (df.writers != 0), df.writers, 0)
        D[sym] = df
    return D


def standalone(D, name, ex):
    out = []
    for sym in SYMS:
        df = R75.frame(sym)
        o, hm = df.open.values, df.hm.values
        dd = D[sym][name]
        sig = []
        for a, b in R75.days(df):
            if b - a < 40 or hm[a] != 555:
                continue
            day = df.index[a].normalize()
            s = int(dd.get(day, 0) or 0)
            if not s:
                continue
            i = a + int(np.searchsorted(hm[a:b], 570, side="left"))
            last = min(a + int(np.searchsorted(hm[a:b], 915, side="left")) - 1, b - 1)
            if i >= last:
                continue
            sig.append((i, s, o[i] * (1 - s * 0.005), last))
        T = R75.build(sym, sig, ex)
        W = R75.build(sym, sig, ex, twin=11)
        if len(T):
            out.append((T, W))
    T = pd.concat([x[0] for x in out], ignore_index=True)
    W = pd.concat([x[1] for x in out], ignore_index=True)
    return T[T.date <= "2026-10-30"], W[W.date <= "2026-10-30"]


def main():
    res = {"standalone": [], "autopilot": {}, "replays": {}}
    for Z, T_ in (() if os.environ.get("R81_ONLY_B") else ((1.0, 0.05), (1.5, 0.10))):
        D = directions(Z, T_)
        for name in ("client_contra", "pro_follow", "fii_follow", "writers", "combo"):
            for ex in ("close", "trail"):
                T, W = standalone(D, name, ex)
                a, t = R75.summ(T), R75.summ(W)
                res["standalone"].append({"Z": Z, "T": T_, "sig": name, "exit": ex, "all": a, "twin": t})
                print(f"Z{Z} T{T_} {name:<13} {ex:<5} n{a['n']:>4} win{a.get('win', 0):.2f} R{a.get('avgR', 0):+.3f} Rs/tr{a.get('rs_trade', 0):+6.0f} "
                      f"23-24 {a.get('a', 0):+9.0f} 25-26 {a.get('b', 0):+9.0f} yrs {a.get('years')} | twin R{t.get('avgR', 0):+.3f} Rs/tr {t.get('rs_trade', 0):+.0f}", flush=True)
    # B: Autopilot trades with / against the sellers
    D = directions(1.0, 0.05)
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    base = base[base.date <= "2026-10-30"].reset_index(drop=True)
    base["rs"] = (base.prem_out - base.prem_in) * base.lot
    base["y"] = base.date.str[:4]
    sd = np.where(base.side == "CALL", 1, -1)
    cand = []
    for name in ("client_contra", "pro_follow", "fii_follow", "writers", "combo"):
        sel = np.array([int(D[s][name].get(pd.Timestamp(d), 0) or 0) for s, d in zip(base.sym, base.date)])
        lab = np.where(sel == 0, "none", np.where(sel == sd, "with", "against"))
        g = base.assign(b=lab).groupby(["b", "y"]).rs.agg(["count", "mean"]).round(0)
        res["autopilot"][name] = g.reset_index().to_dict("records")
        print(f"\nAutopilot trades vs '{name}':\n" + g.unstack("y").to_string())
        for bkt in ("with", "against"):
            m = base[lab == bkt].groupby("y").rs.mean()
            rest = base[lab != bkt].groupby("y").rs.mean()
            diff = (m - rest).reindex(["2023", "2024", "2025", "2026"])
            if diff.notna().all() and ((diff > 0).all() or (diff < 0).all()):
                kind = "better" if (diff > 0).all() else "worse"
                print(f"  -> '{bkt}' {kind} than the rest in every year {diff.round(0).to_dict()}")
                cand.append((name, bkt, kind, lab))
    for name, bkt, kind, lab in cand:
        hit = lab == bkt
        rng = np.random.default_rng(3)
        shit = pd.Series(hit).groupby(base.y.values).transform(lambda v: rng.permutation(v.values)).values.astype(bool)
        tag = f"{name}_{bkt}_{'x1.3' if kind == 'better' else 'skip'}"
        for cap in (200_000, 20_000):
            def run(mask):                                   # skip = drop the rows (xm=0 is floored to 1 lot by the replay)
                if kind == "better":
                    X = base.assign(xm=np.where(mask, 1.3, 1.0))
                else:
                    X = base[~mask].assign(xm=1.0)
                return {p: R73.run(X, alt.assign(xm=1.0) if alt is not None else None, cap, a, b) for p, (a, b) in P.items()}
            ref, new, shu = run(np.zeros(len(base), bool)), run(hit), run(shit)
            res["replays"][f"{tag}_{cap}"] = {"today": ref, "rule": new, "shuffled": shu}
            for lb, rr in (("today", ref), (tag, new), ("shuffled", shu)):
                print(f"  Rs {cap:,} {lb:<30} " + " ".join(f"{p}:{rr[p]['final'] / 1e5:6.2f}L" for p in P) + f" fall {rr['full']['dd']:+.1f}%", flush=True)
    if not cand:
        print("\nno with/against bucket is better or worse in every year -> nothing to replay")
    json.dump(res, open(os.path.join(HERE, "round81_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
