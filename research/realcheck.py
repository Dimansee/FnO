"""Replay the strategy's trades on REAL 5-minute option candles (contracts still listed at
fetch time, so mostly 1-5 weeks to expiry) and compare with the formula on the SAME contract."""
import sys
from datetime import date, datetime, timedelta
import numpy as np
import pandas as pd
import engine as E

W = dict(mult=1.75, stop_atr=2.0, rr=4.0, vwap=1, max_trades=1, vix_min=11, last_entry=870, otm=0, be=0, ctx=-9, rmin=1.0, rmax=2.5)
o = pd.read_csv("data/OPT_live5m.csv.gz", parse_dates=["ts"])
o["expiry"] = pd.to_datetime(o["expiry"]).dt.date
o = o.set_index(["sym", "expiry", "strike", "opt", "ts"]).sort_index()
first = o.reset_index().groupby(["sym", "expiry"])["ts"].min()


def bar(sym, e, k, opt, d, minute):
    ts = pd.Timestamp(datetime.combine(d, datetime.min.time()) + timedelta(minutes=minute))
    try:
        return o.loc[(sym, e, float(k), opt, ts)]
    except KeyError:
        return None


def run(sym, otm=0):
    days = E.load_days(sym)
    meta = E.META[sym]
    tr = E.run(days, sym, "noise", dict(W, otm=otm))
    byday = {d.d: d for d in days}
    out = []
    for t in tr:
        d = byday[t["date"]]
        if d.d < date(2026, 5, 6):
            continue
        cands = sorted(e for (s, e), f in first.items() if s == sym and f.date() < d.d and (e - d.d).days >= 1)
        if not cands:
            continue
        e = cands[0]
        side = t["side"]; opt = "CE" if side > 0 else "PE"
        i = d.t.index(t["in"] - 5); k = d.t.index(t["out"] - 5)
        strike = round(t["spot_in"] / meta["step"]) * meta["step"] + side * otm * meta["step"]
        bi, bk = bar(sym, e, strike, opt, d.d, d.t[i]), bar(sym, e, strike, opt, d.d, d.t[k])
        if bi is None or bk is None or bi["volume"] == 0:
            continue
        why = t["why"]
        real_in = float(bi["close"])
        real_out = float(bk["low"] if why in ("stop", "breakeven") else bk["high"] if why == "target" else bk["close"])
        # the formula on the same contract
        dd = d; old = dd.expiry; dd.expiry = e
        iv_in = E.iv_for(sym, dd, d.vix[i], t["spot_in"], strike, opt, e); iv_out = E.iv_for(sym, dd, d.vix[k], t["spot_out"], strike, opt, e)
        mod_in = E.opt_price(t["spot_in"], strike, dd, t["in"], iv_in, opt)
        mod_out = E.opt_price(t["spot_out"], strike, dd, t["out"], iv_out, opt)
        mod_out = E.real_adjust(mod_in, mod_out, (e - d.d).days, (t["out"] - t["in"]) / 60)
        dd.expiry = old
        out.append({"date": d.d, "sym": sym, "opt": opt, "exp": e, "dte": (e - d.d).days, "why": why, "R": t["R"],
                    "real_in": real_in, "real_out": real_out, "mod_in": mod_in, "mod_out": mod_out,
                    "real_chg": real_out - real_in, "mod_chg": mod_out - mod_in, "mins": t["out"] - t["in"]})
    return pd.DataFrame(out)


if __name__ == "__main__":
    if "--calib" in sys.argv:
        E.use_calibration()
    for sym in ("NIFTY", "BANKNIFTY"):
        x = run(sym, -1 if E.CALIB else 0)
        if x.empty:
            print(sym, "no overlapping trades"); continue
        lot = E.META[sym]["lot"]
        x["real_pnl"] = (x.real_out * (1 - E.SLIP) - x.real_in * (1 + E.SLIP)) * lot - 60
        x["mod_pnl"] = (x.mod_out * (1 - E.SLIP) - x.mod_in * (1 + E.SLIP)) * lot - 60
        pd.set_option("display.width", 200)
        print(x[["date", "opt", "dte", "why", "R", "real_in", "real_out", "mod_in", "mod_out", "real_pnl", "mod_pnl"]].round(1).to_string())
        print(f"{sym}: {len(x)} trades | REAL option P&L ₹{x.real_pnl.sum():,.0f} (1 lot each, win {(x.real_pnl > 0).mean()*100:.0f}%) | "
              f"FORMULA on same contracts ₹{x.mod_pnl.sum():,.0f} | corr of premium change {np.corrcoef(x.real_chg, x.mod_chg)[0,1]:.2f} | "
              f"median entry-price error {((x.mod_in / x.real_in) - 1).median()*100:+.0f}%")
        x.to_csv(f"realcheck_{sym}.csv", index=False)
