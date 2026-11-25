"""Round 107 (25 Nov 2026): do pre-open global cues (US close, crude, gold, USD/INR, Asian open) predict the Indian day?
Targets per index day D: gap = open/prev close - 1; day = close/open - 1 (the part Autopilot trades); also 09:15-10:15.
Every cue uses only data known before 09:15 IST on D (overnight bars dated before D; Asian open on D).
Design 2023-11..2024-12, unseen 2025-01..2026-10. Directional hit rates, correlations, quintile spreads, walk-forward model."""
import os, sys, json
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); D = ROOT + "/data"
def daily(f):
    d = pd.read_csv(f"{D}/{f}.csv.gz"); d["date"] = pd.to_datetime(d.ts.astype(str).str[:10]); return d.set_index("date").sort_index()
def ind(sym):
    c = pd.read_csv(f"{D}/{sym}_5m_upstox.csv.gz" if sym != "SENSEX" else f"{D}/SENSEX_1m_upstox.csv.gz")
    c["ts"] = pd.to_datetime(c.ts.astype(str).str[:19]); c = c.set_index("ts")
    c = c.between_time("09:15", "15:29")
    g = c.groupby(c.index.normalize())
    o = g.open.first(); cl = g.close.last()
    h1 = c.between_time("09:15", "10:14").groupby(c.between_time("09:15", "10:14").index.normalize()).close.last()
    x = pd.DataFrame({"open": o, "close": cl, "h1": h1}); x["prev"] = x.close.shift(1)
    x["gap"] = x.open / x.prev - 1; x["day"] = x.close / x.open - 1; x["first_hour"] = x.h1 / x.open - 1; x["rest"] = x.close / x.h1 - 1
    return x.dropna()
def ret_before(s, dates, col="close"):
    """close-to-close return of the last bar dated strictly before each Indian date."""
    r = s[col].pct_change()
    idx = s.index.searchsorted(dates, side="left") - 1
    return pd.Series(np.where(idx >= 0, r.values[np.maximum(idx, 0)], np.nan), index=dates)
def open_gap_on(s, dates):
    pc = s.close.shift(1); m = pd.Series((s.open / pc - 1).values, index=s.index)
    return m.reindex(dates)
gl = pd.read_csv(f"{D}/global_long_1d.csv.gz"); gl["date"] = pd.to_datetime(gl.ts.astype(str).str[:10])
G = {s: g.set_index("date").sort_index() for s, g in gl.groupby("sym")}
src = {"SP500": daily("SP500_1d"), "NASDAQ": daily("NASDAQ_1d"), "DOW": daily("DOW_1d"), "CRUDE": daily("CRUDE_1d"),
       "USDINR": daily("USDINR_1d"), "GOLD": daily("global/gold_XAUUSD_1d"), "NIKKEI": G["^N225"], "HANGSENG": G["^HSI"],
       "INDIAVIX": daily("INDIAVIX_1d")}
out = {}
for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
    X = ind(sym); X = X[(X.index >= "2023-11-06") & (X.index <= "2026-10-30")]
    dts = X.index
    F = pd.DataFrame(index=dts)
    for k in ("SP500", "NASDAQ", "DOW", "CRUDE", "USDINR", "GOLD"):
        F[k] = ret_before(src[k], dts)
    F["NIKKEI_open"] = open_gap_on(src["NIKKEI"], dts); F["HSI_open"] = open_gap_on(src["HANGSENG"], dts)
    F["VIX_chg"] = ret_before(src["INDIAVIX"], dts)
    F["prev_day"] = X.close.pct_change().shift(0).reindex(dts)  # placeholder replaced below
    F["prev_day"] = (X.prev / X.prev.shift(1) - 1)
    Z = F.join(X[["gap", "day", "first_hour", "rest"]]).dropna()
    Z["era"] = np.where(Z.index < "2025-01-01", "design", "unseen")
    res = {}
    print(f"\n===== {sym}: {len(Z)} days ({(Z.era=='design').sum()} design 2023-11..2024-12, {(Z.era=='unseen').sum()} unseen 2025-01..2026-10)")
    print(f"share of days up after the open: {(Z.day>0).mean()*100:.1f}%   gap and day same sign: {((Z.gap>0)==(Z.day>0)).mean()*100:.1f}%  corr(gap, day) {Z.gap.corr(Z.day):+.3f}")
    print(f"{'cue':12s} | corr w/ GAP  design / unseen | corr w/ DAY (after open) design / unseen | hit: sign(cue)=sign(day) design / unseen | day move top-20% minus bottom-20% cue days (unseen, bp)")
    for k in F.columns:
        r = {}
        for e in ("design", "unseen"):
            z = Z[Z.era == e]
            r[e] = (z[k].corr(z.gap), z[k].corr(z.day), ((np.sign(z[k]) == np.sign(z.day)) & (z[k] != 0)).sum() / max((z[k] != 0).sum(), 1))
        u = Z[Z.era == "unseen"]; q = pd.qcut(u[k].rank(method="first"), 5, labels=False)
        spread = (u.day[q == 4].mean() - u.day[q == 0].mean()) * 1e4
        res[k] = {"gap_corr": [round(r['design'][0], 3), round(r['unseen'][0], 3)], "day_corr": [round(r['design'][1], 3), round(r['unseen'][1], 3)],
                  "hit": [round(r['design'][2], 3), round(r['unseen'][2], 3)], "q5_minus_q1_bp": round(spread, 1)}
        print(f"{k:12s} | {r['design'][0]:+.3f} / {r['unseen'][0]:+.3f}             | {r['design'][1]:+.3f} / {r['unseen'][1]:+.3f}                        | {r['design'][2]*100:5.1f}% / {r['unseen'][2]*100:5.1f}%              | {spread:+.1f}")
    # gap itself (known at 09:15) as a cue for the rest of the day
    for e in ("design", "unseen"):
        z = Z[Z.era == e]; big = z[z.gap.abs() > 0.0075]
        print(f"  gap as cue ({e}): corr(gap, day) {z.gap.corr(z.day):+.3f}; |gap|>0.75%: {len(big)} days, day continued the gap {((np.sign(big.gap)==np.sign(big.day))).mean()*100:.0f}%, avg day move in gap direction {(np.sign(big.gap)*big.day).mean()*1e4:+.0f} bp")
    # walk-forward linear model on all cues: fit design, test unseen; and expanding monthly refit
    cols = list(F.columns)
    from numpy.linalg import lstsq
    def fit(z, y):
        A = np.c_[np.ones(len(z)), z[cols].values]; b = lstsq(A, y.values, rcond=None)[0]; return b
    pred = pd.Series(index=Z.index, dtype=float)
    months = sorted(set(Z.index.to_period("M")))
    for m in months:
        tr = Z[Z.index.to_period("M") < m]
        if (tr.era == "design").sum() < 200 or len(tr) < 200: continue
        te = Z[Z.index.to_period("M") == m]
        for tgt in ("day",):
            b = fit(tr, tr[tgt]); pred.loc[te.index] = np.c_[np.ones(len(te)), te[cols].values] @ b
    u = Z.loc[pred.dropna().index]; p = pred.dropna()
    hit = (np.sign(p) == np.sign(u.day)).mean()
    base = max((u.day > 0).mean(), 1 - (u.day > 0).mean())
    strat = (np.sign(p) * u.day)
    print(f"  walk-forward model (all cues -> day after open), {len(u)} test days: hit {hit*100:.1f}% vs always-majority {base*100:.1f}%; "
          f"long/short-the-prediction {strat.mean()*1e4:+.1f} bp/day (t={strat.mean()/strat.std()*np.sqrt(len(strat)):.2f})")
    bg = fit(Z[Z.era=='design'], Z[Z.era=='design'].gap); pg = np.c_[np.ones((Z.era=='unseen').sum()), Z[Z.era=='unseen'][cols].values] @ bg
    ug = Z[Z.era=='unseen']; print(f"  model for the GAP (fit design, test unseen): corr {np.corrcoef(pg, ug.gap)[0,1]:+.3f}, sign hit {(np.sign(pg)==np.sign(ug.gap)).mean()*100:.1f}%")
    res["_model"] = {"day_hit": round(hit, 3), "majority": round(base, 3), "ls_bp": round(strat.mean() * 1e4, 2), "t": round(strat.mean() / strat.std() * np.sqrt(len(strat)), 2)}
    out[sym] = res
    Z.to_csv(f"/tmp/r107_{sym}.csv")
json.dump(out, open(os.path.dirname(os.path.abspath(__file__)) + "/r107_a.json", "w"), indent=1)
