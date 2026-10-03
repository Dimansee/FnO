import os; os.environ.setdefault("FNO_CALIB","1")
exec(open("round6.py").read().split("# ------------------------------------------------------------------ 2. entry delay")[0].replace('show("BASE','pass # show("BASE'))
import numpy as np
BUD = {date(2023,2,1), date(2024,2,1), date(2024,7,23), date(2025,2,1), date(2026,2,1)}
def ev(tr):
    tr_ = [t for t in tr if t["date"] < SPLIT]; te = [t for t in tr if t["date"] >= SPLIT]
    f = lambda x: (round(sum(t["pnl"] for t in x)/1000,1), len(x))
    return f(tr_), f(te), stats(tr)["max_dd"], stats(tr)["years"]
cands = {
 "base": lambda t: True,
 "skip BN expiry day": lambda t: not (t["sym"]=="BANKNIFTY" and t["date"] in EXPIRY_DAY["BANKNIFTY"]),
 "skip all expiry days": lambda t: t["date"] not in EXPIRY_DAY[t["sym"]],
 "skip DTE<=1": lambda t: (DAYMAP[t["sym"]][t["date"]].expiry - t["date"]).days > 1,
 "VIX at open < 20": lambda t: DAYMAP[t["sym"]][t["date"]].vix[0] < 20,
 "VIX at open >= 12": lambda t: DAYMAP[t["sym"]][t["date"]].vix[0] >= 12,
 "skip Budget day": lambda t: t["date"] not in BUD,
 "skip day after Fed": lambda t: EV.get(t["date"]) != "Day after US Fed" if "EV" in globals() else True,
}
for k, f in cands.items():
    print(f"{k:24s} train ₹k,n / unseen120 ₹k,n / maxDD / years:", ev([t for t in BASE if f(t)]))
# expiry-day split by period: is the BN expiry-day loss stable?
for y in (2023, 2024, 2025, 2026):
    x=[t for t in BASE if t["sym"]=="BANKNIFTY" and t["date"] in EXPIRY_DAY["BANKNIFTY"] and t["date"].year==y]
    z=[t for t in BASE if t["sym"]=="NIFTY" and t["date"] in EXPIRY_DAY["NIFTY"] and t["date"].year==y]
    print(y, "BN expiry-day", len(x), round(sum(t["pnl"] for t in x)), "| Nifty expiry-day", len(z), round(sum(t["pnl"] for t in z)))
print("BN expiry-day trades by strategy", groups([t for t in BASE if t["sym"]=="BANKNIFTY" and t["date"] in EXPIRY_DAY["BANKNIFTY"]], lambda t: t["strat"]))
