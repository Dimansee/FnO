"""Part C: market intraday momentum (Gao, Han, Li & Zhou 2018, JFE): the first half-hour return (from yesterday's close)
predicts the last half-hour. On Nifty / Bank Nifty / Sensex: (1) the raw effect on the index, (2) as a bought ATM option."""
import sys, json; sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from lib import *
out = {"raw": {}, "opt": []}
for sym in SYMS:
    df, o, h, l, c, hm, day = bar_of(sym, "5m")
    a14 = atr(h, l, c, 14)
    D = pd.DataFrame({"day": day, "hm": hm, "o": o, "c": c})
    g = D.groupby("day")
    dopen = g.o.first(); dclose = g.c.last(); prevc = dclose.shift(1)
    at = lambda m: D[D.hm == m].set_index("day").c          # close of the bar that STARTS at minute m
    c0945, c1410, c1425, c1440, c1455, c1510, c1520 = (at(m) for m in (9 * 60 + 40, 14 * 60 + 10, 14 * 60 + 25, 14 * 60 + 40, 14 * 60 + 55, 15 * 60 + 10, 15 * 60 + 20))
    X = pd.DataFrame({"r1": c0945 / prevc - 1, "r_open": c0945 / dopen - 1, "r_on": dopen / prevc - 1, "r12a": c1440 / c1410 - 1, "r12b": c1455 / c1425 - 1,
                      "r_day_a": c1440 / dopen - 1, "r_day_b": c1455 / dopen - 1, "last_a": c1510 / c1440 - 1, "last_b": c1520 / c1455 - 1,
                      "last_full": dclose / c1455 - 1}).dropna()
    X["date"] = pd.to_datetime(X.index).strftime("%Y-%m-%d")
    X["big"] = X.r1.abs() > X.r1.abs().rolling(60, min_periods=20).median().shift(1)
    raw = {}
    for per, m in (("2023-24", X.date < "2025"), ("2025-26", X.date >= "2025")):
        Z = X[m]
        for pred in ("r1", "r_open", "r_on", "r12a", "r12b", "r_day_a", "r_day_b"):
            s = np.sign(Z[pred])
            raw[f"{pred}|{per}"] = {"days": int(len(Z)), "corr_last30": float(np.corrcoef(Z[pred], Z.last_full)[0, 1]),
                                    "bps_14:45-15:15": float((s * Z.last_a).mean() * 1e4), "bps_15:00-15:25": float((s * Z.last_b).mean() * 1e4),
                                    "bps_15:00-close": float((s * Z.last_full).mean() * 1e4), "hit_15:00-close": float((s * Z.last_full > 0).mean())}
        Zb = Z[Z.big]; s = np.sign(Zb.r1)
        raw[f"r1 big|{per}"] = {"days": int(len(Zb)), "bps_15:00-close": float((s * Zb.last_full).mean() * 1e4), "bps_14:45-15:15": float((s * Zb.last_a).mean() * 1e4)}
    out["raw"][sym] = raw
    print(sym, "raw effect (bps with the predictor's sign):")
    for k, v in raw.items():
        print("   ", f"{k:<18}", {a: round(b, 2) for a, b in v.items()})
    # as bought options
    dayix = pd.Series(day)
    def sig(pred_cols, entry_start, big=False):
        L = np.zeros(len(c), bool); S = np.zeros(len(c), bool)
        ok = X if not big else X[X.big]
        sgn = np.sign(ok[pred_cols[0]])
        for p in pred_cols[1:]:
            sgn = sgn.where(np.sign(ok[p]) == sgn, 0)
        m = dayix.map(sgn).fillna(0).values
        bar = hm == entry_start - 5                           # the bar that closes at the entry time
        L[bar & (m > 0)] = True; S[bar & (m < 0)] = True
        return L, S
    P = {"first half-hour (from prev close)": ["r1"], "09:15-09:45 only": ["r_open"], "overnight gap": ["r_on"], "half-hour before entry": ["r12"],
         "first half-hour + half-hour before agree": ["r1", "r12"], "day so far": ["r_day"], "first half-hour + day so far agree": ["r1", "r_day"]}
    for pn, cols in P.items():
        for entry, sfx in ((14 * 60 + 45, "a"), (15 * 60, "b")):
            cc = [("r12" + sfx if x == "r12" else "r_day_" + sfx if x == "r_day" else x) for x in cols]
            for sq in (15 * 60 + 15, 15 * 60 + 25):
                for big in (False, True):
                    L, S = sig(cc, entry, big)
                    rows = simulate(o, h, l, c, hm, day, L, S, 3 * a14, first=entry, last=entry, sq=sq, per_day=1, step=5)
                    T = price(sym, df, rows, 5); s = summ(T)
                    s.update(sym=sym, pred=pn, entry=f"{entry // 60}:{entry % 60:02d}", exit=f"{sq // 60}:{sq % 60:02d}", big=big)
                    out["opt"].append(s)
json.dump(out, open(OUT + "/c_itsm.json", "w"), default=str)
R = pd.DataFrame(out["opt"])
V = R.groupby(["pred", "entry", "exit", "big"]).agg(a=("a", "sum"), b=("b", "sum"), a_n=("a_n", "sum"), b_n=("b_n", "sum"), pos_a=("a", lambda x: (x > 0).sum()), pos_b=("b", lambda x: (x > 0).sum())).reset_index()
V["a_avg"] = V.a / V.a_n.clip(lower=1); V["b_avg"] = V.b / V.b_n.clip(lower=1)
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
print("\noption versions:", len(V), "| design +:", int((V.a > 0).sum()), "| unseen +:", int((V.b > 0).sum()), "| both +:", int(((V.a > 0) & (V.b > 0)).sum()))
print(V.sort_values("a_avg", ascending=False).head(12).round(0).to_string(index=False))
print("\nboth-period positives:"); print(V[(V.a > 0) & (V.b > 0)].round(0).to_string(index=False))
