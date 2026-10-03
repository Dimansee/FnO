"""Does the formula move like real option prices INSIDE a day?  For every day / expiry / CE+PE
in the real 5-min option data: buy ATM at 09:45..14:15 checks, hold to every later check or 15:15,
and compare the REAL premium change with the formula's change (same contract, same index moves)."""
import sys
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import engine as E

E.use_calibration("--raw" not in sys.argv)
o = pd.read_csv("data/OPT_live5m.csv.gz", parse_dates=["ts"])
o["expiry"] = pd.to_datetime(o["expiry"]).dt.date
o["d"] = o.ts.dt.date
o["m"] = o.ts.dt.hour * 60 + o.ts.dt.minute
rows = []
for sym in ("NIFTY", "BANKNIFTY"):
    days = {d.d: d for d in E.load_days(sym)}
    meta = E.META[sym]
    g = o[o.sym == sym].set_index(["d", "expiry", "strike", "opt", "m"]).sort_index()
    for (d, e), _ in o[o.sym == sym].groupby(["d", "expiry"]):
        dte = (e - d).days
        if d not in days or dte < 1 or dte > 35:
            continue
        D = days[d]
        old = D.expiry; D.expiry = e
        checks = [m for m in range(580, 856, 30) if m in D.idx]
        for a in checks:
            ia = D.idx[a]
            spot_a = D.c[ia]
            k = round(spot_a / meta["step"]) * meta["step"]
            for opt in ("CE", "PE"):
                try:
                    ra = g.loc[(d, e, float(k), opt, a)]
                except KeyError:
                    continue
                if ra["volume"] <= 0:
                    continue
                for b in [m for m in checks if m > a] + [910]:
                    if b not in D.idx:
                        continue
                    try:
                        rb = g.loc[(d, e, float(k), opt, b)]
                    except KeyError:
                        continue
                    ib = D.idx[b]
                    ma = E.opt_price(spot_a, k, D, a + 5, E.iv_for(sym, D, D.vix[ia], spot_a, k, opt, e), opt)
                    mb = E.opt_price(D.c[ib], k, D, b + 5, E.iv_for(sym, D, D.vix[ib], D.c[ib], k, opt, e), opt)
                    sign = 1 if opt == "CE" else -1
                    rows.append({"sym": sym, "dte": dte, "opt": opt, "hold": b - a, "move": sign * (D.c[ib] - spot_a),
                                 "real_a": float(ra["close"]), "real_chg": float(rb["close"]) - float(ra["close"]),
                                 "mod_a": ma, "mod_chg": mb - ma})
        D.expiry = old
x = pd.DataFrame(rows)
x["dteb"] = pd.cut(x.dte, [0, 2, 7, 14, 40], labels=["1-2", "3-7", "8-14", "15+"])
print(len(x), "real holding windows")
for (sym, b), y in x.groupby(["sym", "dteb"], observed=True):
    # real_chg ~ slope * mod_chg + intercept
    A = np.vstack([y.mod_chg, np.ones(len(y))]).T
    slope, icpt = np.linalg.lstsq(A, y.real_chg, rcond=None)[0]
    print(f"{sym:9s} dte {b:5s} n={len(y):5d} | real change = {slope:.2f} x formula change {icpt:+.2f} pts | "
          f"avg real {y.real_chg.mean():+.2f} vs formula {y.mod_chg.mean():+.2f} pts | entry price real/formula {np.median(y.real_a / y.mod_a):.2f}")
x.to_csv("intraday_fit.csv.gz", index=False, compression="gzip")
