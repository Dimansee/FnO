"""Round 80: buying imbalance - does the buy/sell pressure behind a signal decide whether Autopilot's trade works?

There is no order-book or aggressor-side history for 2023-26 (the recorder keeps option bid/ask PRICES, OI and volume, not
buy/sell quantities), and index candles have no volume. Proxy that exists for the whole period: signed volume of the
heavyweight stocks (Nifty / Sensex: 10 Nifty heavyweights; Bank Nifty: HDFC Bank, ICICI Bank, SBI, Axis Bank).
  imbalance(L) = sum(volume x sign(close - open)) / sum(volume) over each stock's 5-min bars of the last L minutes before
  the entry (bars fully closed), averaged over the stocks; +1 = all volume on rising bars (buyers in control), -1 = sellers.
  Also 'surge' = that window's volume vs its usual level at that time of day.
Each Autopilot trade (today's BASELINE, Jan 2023 - Oct 2026) gets 'with' = imbalance in the trade's direction >= T,
'against' <= -T, else neutral (T = 0.2 / 0.4; L = 15 / 30 min). Per-lot P&L by year; anything better / worse in EVERY year is
replayed (skip 'against', or 1.3x 'with') at Rs 2L and Rs 20k, with a shuffle control (labels shuffled within year).
Usage: python research/round80_buy_imbalance.py
"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round73_regime_tilts as R73
import outcomes as O
from fno import portfolio_bt as PB

DATA = os.path.join(HERE, "data")
HEAVY = {"NIFTY": ["HDFCBANK", "RELIANCE", "ICICIBANK", "INFY", "TCS", "LT", "SBIN", "AXISBANK", "BHARTIARTL", "ITC"],
         "BANKNIFTY": ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK"]}
HEAVY["SENSEX"] = HEAVY["NIFTY"]
P = {**R73.PERIODS, "2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", "2026-10-30")}


def stock(s):
    x = pd.read_csv(os.path.join(DATA, f"{s}_5m_upstox.csv.gz"), parse_dates=["ts"]).set_index("ts")
    x = x[~x.index.duplicated()].sort_index()
    sv = x.volume * np.sign(x.close - x.open)
    tod = x.index.strftime("%H:%M")
    med = x.volume.groupby(tod).transform(lambda v: v.shift(1).rolling(20, min_periods=10).median())
    return pd.DataFrame({"v": x.volume, "sv": sv, "med": med}, index=x.index)


def features(T):
    S = {s: stock(s) for s in set(sum(HEAVY.values(), []))}
    out = {}
    for L in (15, 30):
        imb, surge = [], []
        for r in T.itertuples(index=False):
            t = pd.Timestamp(f"{r.date} {r.entry_time}")
            a = t - pd.Timedelta(minutes=L)
            vals, sur = [], []
            for s in HEAVY[r.sym]:
                d = S[s]
                w = d[(d.index >= a) & (d.index + pd.Timedelta(minutes=5) <= t)]
                if len(w) and w.v.sum() > 0:
                    vals.append(w.sv.sum() / w.v.sum())
                    if w.med.notna().all() and w.med.sum() > 0:
                        sur.append(w.v.sum() / w.med.sum())
            imb.append(np.mean(vals) if vals else np.nan)
            surge.append(np.mean(sur) if sur else np.nan)
        out[f"imb{L}"] = imb
        out[f"surge{L}"] = surge
    return pd.DataFrame(out, index=T.index)


def main():
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    base = base[base.date <= "2026-10-30"].reset_index(drop=True)
    F = features(base)
    base = pd.concat([base, F], axis=1)
    sg = np.where(base.side == "CALL", 1, -1)
    base["rs"] = (base.prem_out - base.prem_in) * base.lot
    base["y"] = base.date.str[:4]
    res = {"buckets": {}, "replays": {}}
    cand = []
    for L in (15, 30):
        d = sg * base[f"imb{L}"]
        for T_ in (0.2, 0.4):
            lab = np.where(d >= T_, "with", np.where(d <= -T_, "against", "neutral"))
            lab = np.where(base[f"imb{L}"].isna(), "na", lab)
            g = base.assign(b=lab).groupby(["b", "y"]).rs.agg(["count", "mean"]).round(0)
            res["buckets"][f"L{L}_T{T_}"] = g.reset_index().to_dict("records")
            print(f"\nimbalance last {L} min, threshold {T_}:\n" + g.unstack("y").to_string())
            rest = {b: base[lab != b].groupby("y").rs.mean() for b in ("with", "against")}
            for b in ("with", "against"):
                m = base[lab == b].groupby("y").rs.mean()
                diff = (m - rest[b]).reindex(["2023", "2024", "2025", "2026"])
                if diff.notna().all() and ((diff > 0).all() or (diff < 0).all()):
                    cand.append((L, T_, b, "better" if (diff > 0).all() else "worse", lab))
                    print(f"  -> '{b}' is {'better' if (diff > 0).all() else 'worse'} than the rest in every year: {diff.round(0).to_dict()}")
    # surge (volume coming in) with / against
    for L in (15, 30):
        hi = base[f"surge{L}"] >= 1.5
        print(f"\nsurge >= 1.5x in last {L} min:", base[hi].groupby("y").rs.mean().round(0).to_dict(), "rest", base[~hi].groupby("y").rs.mean().round(0).to_dict())
    if not cand:
        print("\nno bucket better or worse than the rest in every year -> nothing to replay")
    for L, T_, b, kind, lab in cand:
        xm = np.where(lab == b, 0.0 if kind == "worse" else 1.3, 1.0)
        name = f"L{L}_T{T_}_{b}_{'skip' if kind == 'worse' else 'x1.3'}"
        rng = np.random.default_rng(1)
        sh = base.groupby("y").cumcount() * 0 + xm
        sh = pd.Series(xm).groupby(base.y).transform(lambda v: rng.permutation(v.values)).values
        for cap in (200_000, 20_000):
            ref = {p: R73.run(base.assign(xm=1.0), alt.assign(xm=1.0) if alt is not None else None, cap, a, b_) for p, (a, b_) in P.items()}
            new = {p: R73.run(base.assign(xm=xm), alt.assign(xm=1.0) if alt is not None else None, cap, a, b_) for p, (a, b_) in P.items()}
            shu = {p: R73.run(base.assign(xm=sh), alt.assign(xm=1.0) if alt is not None else None, cap, a, b_) for p, (a, b_) in P.items()}
            res["replays"][f"{name}_{cap}"] = {"today": ref, "rule": new, "shuffled": shu}
            for lb, rr in (("today", ref), (name, new), ("shuffled", shu)):
                print(f"  Rs {cap:,} {lb:<24} " + " ".join(f"{p}:{rr[p]['final'] / 1e5:6.2f}L" for p in P) + f" fall {rr['full']['dd']:+.1f}%", flush=True)
    json.dump(res, open(os.path.join(HERE, "round80_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
