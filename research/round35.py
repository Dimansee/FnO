"""Round 35 (Oct 2026): where can rounds 31-33 be improved?
 1. Round 33 conviction score - which parts carry it (5 / 15 / 60 min, EMA side vs trend-line side), is it stable when the EMA length
    (10 / 20 / 50) or the swing size (2 / 3 candles) changes, does a walk-forward choice (pick on 2023-24, judge on 2025-26) agree with
    the shipped 1.33x / 0.67x at score >= 5, and does a graded size (more steps) beat it at the same average risk?
 2. Round 32's one-direction idea applied to the CURRENT rules: trades against the first 15-minute candle, against the CPR side, or
    against candle + pivot agreement - skip them or size them down? Alone and together with the conviction score.
 (Round 31, option selling, is on hold at the user's request.)
Uses the current-rule trades of round 33 (/tmp/round33_trades.pkl, re-priced exactly like the harness), Rs 2,000 risk a trade ("u").
Run: python round35.py -> round35_results.json"""
import os, sys, json, itertools, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round26_harness as H
from round29_levels import daily, levels

RES = {}
T = pd.read_pickle("/tmp/round33_trades.pkl"); T = T[T.src == "current"].copy().reset_index(drop=True)
CONV = T.sym.isin(["NIFTY", "SENSEX"]).values


def tf_feats(c5, tf, span=20, F=2):
    if tf == 5:
        s = c5[["open", "high", "low", "close"]]; end = s.index + pd.Timedelta(minutes=5)
    else:
        parts = []
        for _, g in c5.groupby(c5.index.date):
            parts.append(g[["open", "high", "low", "close"]].resample(
                f"{tf}min", origin=g.index[0].normalize() + pd.Timedelta(hours=9, minutes=15), label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna())
        s = pd.concat(parts)
        end = pd.DatetimeIndex([min(t + pd.Timedelta(minutes=tf), t.normalize() + pd.Timedelta(hours=15, minutes=30)) for t in s.index])
    Hh, L, Cc = s.high.values, s.low.values, s.close.values; n = len(s)
    ema = s.close.ewm(span=span, adjust=False).mean().values
    up_tl = np.zeros(n, bool); dn_tl = np.zeros(n, bool); sh, sl = [], []
    for k in range(n):
        q = k - F
        if q >= F:
            if Hh[q] == Hh[q - F:q + F + 1].max(): sh.append(q)
            if L[q] == L[q - F:q + F + 1].min(): sl.append(q)
        if len(sl) >= 2 and L[sl[-1]] > L[sl[-2]]:
            a, b = sl[-2], sl[-1]; up_tl[k] = Cc[k] > L[b] + (L[b] - L[a]) / (b - a) * (k - b)
        if len(sh) >= 2 and Hh[sh[-1]] < Hh[sh[-2]]:
            a, b = sh[-2], sh[-1]; dn_tl[k] = Cc[k] < Hh[b] + (Hh[b] - Hh[a]) / (b - a) * (k - b)
    return pd.DataFrame({"eu": Cc > ema, "ed": Cc < ema, "tu": up_tl, "td": dn_tl}, index=end)


def components(span=20, F=2):
    """Per trade: 6 agreement bits (ema5, tl5, ema15, tl15, ema60, tl60)."""
    out = np.zeros((len(T), 6), int)
    for sym in T.sym.unique():
        c5 = H.load(sym); days = {str(d): g.index for d, g in c5.groupby(c5.index.date)}
        fr = {tf: tf_feats(c5, tf, span, F) for tf in (5, 15, 60)}
        rows = np.where(T.sym.values == sym)[0]
        for r in rows:
            ts = days[T.date.iat[r]][T.i.iat[r]] + pd.Timedelta(minutes=5); sd = T.side.iat[r]
            for n, tf in enumerate((5, 15, 60)):
                f = fr[tf]; k = f.index.searchsorted(ts, side="right") - 1
                if k < 0: continue
                x = f.iloc[k]
                out[r, 2 * n] = int(x.eu if sd > 0 else x.ed); out[r, 2 * n + 1] = int(x.tu if sd > 0 else x.td)
    return out


def dstat(u, mask):
    s = pd.Series(u[mask]).groupby(T.date.values[mask]).sum().sort_index(); eq = s.cumsum()
    fall = float((eq - eq.cummax().clip(lower=0)).min())
    return dict(net=round(float(s.sum())), fall=round(fall), ret_fall=round(float(s.sum() / -fall), 2) if fall < 0 else None)


TR, TE = (T.y < "2026").values, (T.y == "2026").values
def judge(w, label, only=None):
    """Sizing weights w applied on Nifty + Sensex (Bank Nifty keeps 1x unless only=None and w given for all)."""
    u = T.u.values * w
    years = {y: round(float(u[(T.y == y).values].sum())) for y in ("2023", "2024", "2025", "2026")}
    r = dict(label=label, avg_risk=round(float(w[CONV].mean()), 2), train=dstat(u, TR), y2026=dstat(u, TE), years=years)
    return r


base = judge(np.ones(len(T)), "as before round 33 (1x everywhere)")
ship = judge(np.where(CONV, np.where(T.score.values >= 5, 1.33, 0.67), 1.0), "shipped: 1.33x at score >= 5, 0.67x below (Nifty, Sensex)")
print(base); print(ship)
RES["baseline"], RES["shipped"] = base, ship

# ---- 1a. which parts carry the score
comp = components(); names = ["5m EMA", "5m trend line", "15m EMA", "15m trend line", "1h EMA", "1h trend line"]
assert (comp.sum(1) == T.score.values).all(), "component sum must equal the round-33 score"
rows = []
m = CONV
for c, nm in enumerate(names):
    a, d = comp[:, c] == 1, comp[:, c] == 0
    yrs = {y: round(float(T.u[m & a & (T.y == y).values].mean() - T.u[m & d & (T.y == y).values].mean())) for y in ("2023", "2024", "2025", "2026")}
    rows.append(dict(part=nm, agree_share=round(float(a[m].mean() * 100)), avg_u_agree=round(float(T.u[m & a].mean())),
                     avg_u_against=round(float(T.u[m & d].mean())), gap_by_year=yrs, years_better=sum(v > 0 for v in yrs.values())))
P = pd.DataFrame(rows); print(P.to_string()); RES["parts"] = rows
# EMA-only and trend-line-only scores
ema3, tl3 = comp[:, [0, 2, 4]].sum(1), comp[:, [1, 3, 5]].sum(1)
alt = {}
for nm, sc, thr in (("EMA parts only (0-3), >= 3", ema3, 3), ("trend-line parts only (0-3), >= 2", tl3, 2), ("trend-line parts only, >= 3", tl3, 3),
                    ("15m + 1h only (0-4), >= 3", comp[:, 2:].sum(1), 3), ("15m + 1h only, >= 4", comp[:, 2:].sum(1), 4)):
    hi = sc >= thr; share = hi[CONV].mean()
    lo_w = (1 - 1.33 * share) / (1 - share) if share < 0.75 else 0.5        # keep the average risk at 1x
    alt[nm] = judge(np.where(CONV, np.where(hi, 1.33, max(lo_w, 0.25)), 1.0), nm + f" (high share {share:.0%})")
    print(alt[nm])
RES["alternative_scores"] = alt

# ---- 1b. stability: EMA length and swing size
stab = {}
for span, F in itertools.product((10, 20, 50), (2, 3)):
    sc = T.score.values if (span, F) == (20, 2) else components(span, F).sum(1)
    for thr in (4, 5):
        hi = sc >= thr; share = hi[CONV].mean(); lo_w = (1 - 1.33 * share) / (1 - share)
        r = judge(np.where(CONV, np.where(hi, 1.33, lo_w), 1.0), f"EMA {span}, swing {F}, score >= {thr} (low {lo_w:.2f}x)")
        stab[r["label"]] = r; print(r["label"], r["train"], r["y2026"])
RES["stability"] = stab

# ---- 1c. walk-forward: pick threshold / multiplier on 2023-24, judge on 2025-26
early, late = T.y.isin(["2023", "2024"]).values, T.y.isin(["2025", "2026"]).values
best = None
for thr, hi_m in itertools.product((3, 4, 5, 6), (1.2, 1.33, 1.5)):
    hi = T.score.values >= thr; share = hi[CONV & early].mean(); lo_m = (1 - hi_m * share) / (1 - share)
    if lo_m < 0.25: continue
    w = np.where(CONV, np.where(hi, hi_m, lo_m), 1.0); u = T.u.values * w
    st_e = dstat(u, early)
    if best is None or st_e["ret_fall"] > best[0]: best = (st_e["ret_fall"], thr, hi_m, lo_m)
_, thr, hi_m, lo_m = best
w = np.where(CONV, np.where(T.score.values >= thr, hi_m, lo_m), 1.0)
RES["walk_forward"] = dict(picked_on_2023_24=dict(threshold=thr, high=hi_m, low=round(lo_m, 2)),
                           late_2025_26_new=dstat(T.u.values * w, late), late_2025_26_base=dstat(T.u.values, late),
                           late_2025_26_shipped=dstat(T.u.values * np.where(CONV, np.where(T.score.values >= 5, 1.33, 0.67), 1.0), late))
print("walk-forward", RES["walk_forward"])

# ---- 1d. graded size by score (same average risk)
grades = {"3 steps: <=2 0.5x, 3-4 0.9x, 5-6 1.3x": {0: .5, 1: .5, 2: .5, 3: .9, 4: .9, 5: 1.3, 6: 1.3},
          "4 steps: <=2 0.5x, 3-4 0.85x, 5 1.2x, 6 1.5x": {0: .5, 1: .5, 2: .5, 3: .85, 4: .85, 5: 1.2, 6: 1.5},
          "linear: 0.4x + 0.17x per point": {k: 0.4 + 0.17 * k for k in range(7)}}
grd = {}
for nm, g in grades.items():
    raw = np.array([g[s] for s in T.score.values]); raw = raw / raw[CONV & TR].mean()     # same average risk as 1x on training
    grd[nm] = judge(np.where(CONV, raw, 1.0), nm); print(grd[nm])
RES["graded"] = grd

# ---- 2. round 32's one-direction idea on the current rules
D = {s: daily(s) for s in T.sym.unique()}
bias = {}
for s in T.sym.unique():
    c5 = H.load(s); Dd = D[s]; dates = list(Dd.index)
    for i in range(1, len(dates)):
        d = str(dates[i].date()); g = c5[c5.index.date == dates[i].date()]
        if len(g) < 3: continue
        L = levels(Dd.iloc[i - 1]); o, c930 = float(g.open.iloc[0]), float(g.close.iloc[2])
        orb = int(np.sign(c930 - o)); piv = 1 if c930 > L["classic"]["P"] else -1
        top, bot = max(L["cpr"]["TC"], L["cpr"]["BC"]), min(L["cpr"]["TC"], L["cpr"]["BC"])
        cpr = 1 if c930 > top else (-1 if c930 < bot else 0)
        bias[(s, d)] = dict(orb=orb, piv=piv, cpr=cpr, op=orb if orb == piv else 0)
day5 = {s: {str(d): g.index for d, g in H.load(s).groupby(H.load(s).index.date)} for s in T.sym.unique()}
entry_end = np.array([day5[s][d][i] + pd.Timedelta(minutes=5) for s, d, i in zip(T.sym, T.date, T.i)])
known = np.array([t.time() >= pd.Timestamp("09:30").time() for t in entry_end])        # the 09:30 bias exists only from 09:30
one = {}
for key, nm in (("orb", "first 15-min candle colour"), ("cpr", "CPR side at 09:30"), ("op", "candle and pivot agree")):
    b = np.array([bias.get((s, d), {}).get(key, 0) for s, d in zip(T.sym, T.date)])
    agree = known & (b != 0) & (b == T.side.values); against = known & (b != 0) & (b == -T.side.values)
    yrs = {y: [round(float(T.u[agree & (T.y == y).values].mean())), round(float(T.u[against & (T.y == y).values].mean()))] for y in ("2023", "2024", "2025", "2026")}
    skip = judge(np.where(against, 0.0, 1.0), f"skip trades against: {nm}")
    sz = np.where(agree, 1.2, np.where(against, 0.8, 1.0)); sz = sz / sz[TR].mean()
    size = judge(sz, f"1.2x with / 0.8x against: {nm}")
    both = np.where(CONV, np.where(T.score.values >= 5, 1.33, 0.67), 1.0) * sz
    both = judge(both, f"conviction + 1.2x/0.8x {nm}")
    one[nm] = dict(share_against=round(float(against.mean() * 100)), avg_u_with_vs_against_by_year=yrs, skip=skip, size=size, with_conviction=both)
    print(nm, "with/against by year", yrs); print("  ", skip); print("  ", size); print("  ", both)
RES["one_direction_on_current_rules"] = one
json.dump(RES, open(os.path.join(HERE, "round35_results.json"), "w"), indent=1, default=str)
print("saved")
