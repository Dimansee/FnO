"""Part D: buying both sides (long ATM straddle) - a non-directional buy that today's rules never take.
Entry at 09:20 or 09:45; exit when the index has moved X% from entry (take the move), or 15:15; optional 'dead market' cut at 12:30
when the index is still within 0.15% of entry. Day filters: all days, NR7, inside day, narrow CPR, low VIX, expiry day, 3+ days to expiry.
Both legs priced with the app's model (1 lot each, 0.5% slippage a fill, charges). Model caveat: IV follows India VIX, so an
intraday IV crush / spike in the option itself is not captured."""
import sys, json; sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from lib import *
res = []
for sym in SYMS:
    df, o, h, l, c, hm, day = bar_of(sym, "5m")
    a14 = atr(h, l, c, 14)
    D = pd.DataFrame({"day": day, "h": h, "l": l, "c": c}); g = D.groupby("day")
    dh, dl, dc = g.h.max(), g.l.min(), g.c.last(); rng = dh - dl
    nr7 = (rng.shift(1) <= rng.shift(1).rolling(7).min())
    inside = (dh.shift(1) < dh.shift(2)) & (dl.shift(1) > dl.shift(2))
    p = (dh + dl + dc) / 3; bc = (dh + dl) / 2; cw = ((2 * p - 2 * bc).abs() / p).shift(1)
    narrow = cw < cw.rolling(60, min_periods=20).quantile(0.33).shift(1)
    v = M.vix(); vd = v.groupby(v.index.normalize()).last(); vd.index = vd.index.astype("int64")
    vprev = vd.shift(1).reindex(dh.index).ffill(); lowvix = vprev < vprev.rolling(250, min_periods=60).quantile(0.3).shift(1)
    dates = pd.to_datetime(dh.index); dte = pd.Series([(R34.expiry_of(sym, d.date()) - d.date()).days for d in dates], index=dh.index)
    FLT = {"all days": pd.Series(True, index=dh.index), "NR7 yesterday": nr7, "inside day yesterday": inside, "narrow CPR": narrow, "low VIX (bottom 30%)": lowvix,
           "expiry day": dte == 0, "3+ days to expiry": dte >= 3}
    idx_by_day = pd.Series(np.arange(len(c))).groupby(day).agg(["first", "last"])
    for entry in (9 * 60 + 20, 9 * 60 + 45):
        for move in (0.004, 0.006, 0.009, None):
            for cut in (False, True):
                rows = {}
                for d_, (i0, i1) in idx_by_day.iterrows():
                    ii = np.where((day == d_) & (hm == entry))[0]
                    if not len(ii):
                        continue
                    i = int(ii[0]); ep = o[i]; j = i; xs = None
                    while True:
                        if move and abs(c[j] - ep) >= move * ep:
                            xs = c[j]; break
                        if cut and hm[j] >= 12 * 60 + 25 and abs(c[j] - ep) < 0.0015 * ep:
                            xs = c[j]; break
                        if hm[j] >= 15 * 60 + 10 or j >= i1:
                            xs = c[j]; break
                        j += 1
                    rows[d_] = (i, j, ep, xs)
                legs = []
                for side in (1, -1):
                    T = price(sym, df, [(i, j, ep, xs, side, ep - side * a14[i]) for (i, j, ep, xs) in rows.values()], 5)
                    legs.append(T.set_index("date").rs)
                both = (legs[0] + legs[1]).rename("rs").reset_index(); both["dayv"] = list(rows.keys())
                prem = None
                for fn, f in FLT.items():
                    keep = both[both.dayv.map(f.reindex(dh.index).fillna(False)).values.astype(bool)]
                    a, b = keep[keep.date < "2025"], keep[keep.date >= "2025"]
                    res.append({"sym": sym, "entry": f"{entry // 60}:{entry % 60:02d}", "move": move or 0, "cut": cut, "filter": fn, "a_n": len(a), "b_n": len(b),
                                "a": float(a.rs.sum()), "b": float(b.rs.sum()), "win": float((keep.rs > 0).mean()) if len(keep) else 0})
    print(sym, "done", flush=True)
json.dump(res, open(OUT + "/d_straddle.json", "w"))
R = pd.DataFrame(res)
V = R.groupby(["filter", "entry", "move", "cut"]).agg(a=("a", "sum"), b=("b", "sum"), a_n=("a_n", "sum"), b_n=("b_n", "sum"), pos_a=("a", lambda x: (x > 0).sum()), pos_b=("b", lambda x: (x > 0).sum())).reset_index()
V["a_avg"] = V.a / V.a_n.clip(lower=1); V["b_avg"] = V.b / V.b_n.clip(lower=1)
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
print("straddle versions:", len(V), "| design +:", int((V.a > 0).sum()), "| unseen +:", int((V.b > 0).sum()), "| both +:", int(((V.a > 0) & (V.b > 0)).sum()))
print(V.groupby("filter").agg(versions=("a", "size"), design_pos=("a", lambda x: (x > 0).sum()), unseen_pos=("b", lambda x: (x > 0).sum()), med_a=("a_avg", "median"), med_b=("b_avg", "median"), n_a=("a_n", "median")).round(0).to_string())
print("\ntop by design (min 60 design trades):"); print(V[V.a_n >= 60].sort_values("a_avg", ascending=False).head(10).round(0).to_string(index=False))
