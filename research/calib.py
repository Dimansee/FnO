"""How far is the formula (Black-Scholes + India VIX) from REAL option prices?

Uses NSE's official daily closing prices for every Nifty / Bank Nifty option (2023-26):
for the contracts the strategy would actually buy (nearest expiry >= 1 day away, strikes
around the money) compare the real close with the formula's 15:30 price, then solve the
IV that reproduces the real price. The ratio real IV / (VIX x multiplier) by days-to-expiry
and moneyness becomes the correction used by the backtest.
"""
import math
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd

sys.path.insert(0, "..")
from fno import indicators as I  # noqa: E402

D = "data"
META = {"NIFTY": {"step": 50, "iv_mult": 1.0}, "BANKNIFTY": {"step": 100, "iv_mult": 1.2}}


def implied_vol(price, s, k, t, opt):
    lo, hi = 0.01, 3.0
    if price <= max(0.0, (s - k) if opt == "CE" else (k - s)) + 0.05:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if I.bs_price(s, k, t, mid, opt) > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def load():
    b = pd.read_csv(f"{D}/OPT_bhavcopy.csv.gz", parse_dates=["date", "expiry"])
    b["date"], b["expiry"] = b["date"].dt.date, b["expiry"].dt.date
    vix = pd.read_csv(f"{D}/INDIAVIX_1d.csv.gz", parse_dates=["ts"]).set_index("ts")["close"]
    vix.index = vix.index.date
    und = {s: pd.read_csv(f"{D}/{s}_1d.csv.gz", parse_dates=["ts"]).set_index("ts")["close"] for s in META}
    for s in und:
        und[s].index = und[s].index.date
    return b, vix, und


def rows():
    b, vix, und = load()
    out = []
    for (d, sym), g in b.groupby(["date", "sym"]):
        if d not in vix.index:
            continue
        spot = float(g["und"].dropna().iloc[0]) if g["und"].notna().any() else (float(und[sym].get(d)) if d in und[sym].index else None)
        if not spot:
            continue
        exps = sorted(e for e in g["expiry"].unique() if (e - d).days >= 1)
        if not exps:
            continue
        for rank, e in enumerate(exps[:2]):
            t = I.years_to_expiry(e, datetime.combine(d, datetime.min.time()).replace(hour=15, minute=30))
            step = META[sym]["step"]
            atm = round(spot / step) * step
            for _, r in g[(g["expiry"] == e) & ((g["strike"] - atm).abs() <= 4 * step) & (g["volume"] > 0)].iterrows():
                m = round((r["strike"] - atm) / step) * (1 if r["opt"] == "CE" else -1)   # + = OTM steps
                iv = implied_vol(float(r["close"]), spot, float(r["strike"]), t, r["opt"])
                model = I.bs_price(spot, float(r["strike"]), t, float(vix[d]) / 100 * META[sym]["iv_mult"], r["opt"])
                out.append({"date": d, "sym": sym, "rank": rank, "dte": (e - d).days, "otm": m, "opt": r["opt"],
                            "real": float(r["close"]), "model": model, "iv": iv, "vix": float(vix[d]), "spot": spot})
    return pd.DataFrame(out)


if __name__ == "__main__":
    x = rows()
    x.to_csv("calib_rows.csv.gz", index=False, compression="gzip")
    x = x.dropna(subset=["iv"])
    x["ratio"] = x["iv"] / (x["vix"] / 100)
    x["err"] = x["model"] / x["real"] - 1
    x["dteb"] = pd.cut(x["dte"], [0, 1, 2, 4, 7, 14, 40], labels=["1", "2", "3-4", "5-7", "8-14", "15+"])
    for sym in META:
        y = x[(x.sym == sym) & (x["otm"].between(-2, 2))]
        print(f"\n{sym}: real IV / VIX (median) by days-to-expiry x strikes OTM (+) / ITM (-)")
        print(y.pivot_table(index="dteb", columns="otm", values="ratio", aggfunc="median", observed=True).round(2))
        print(f"{sym}: formula price vs real price (median % error, + = formula too high)")
        print((y.pivot_table(index="dteb", columns="otm", values="err", aggfunc="median", observed=True) * 100).round(0))
    tab = x[x["otm"].between(-4, 4)].groupby(["sym", "dteb", "otm"], observed=True)["ratio"].median().reset_index()
    tab.to_csv("../fno/iv_calibration.csv", index=False)
    print("\nwrote fno/iv_calibration.csv", len(tab), "rows")
