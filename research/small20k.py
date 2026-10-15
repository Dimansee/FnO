import os; os.environ.setdefault("FNO_CALIB","1")
exec(open("round6.py").read().split("D = {s: E.load_days(s) for s in SYMS}")[0])
D = {s: E.load_days(s) for s in SYMS}
DATES = [d.d for d in D["NIFTY"]]
import json
from datetime import timedelta
BUD = {date(2023,2,1), date(2024,2,1), date(2024,7,23), date(2025,2,1), date(2026,2,1)}
end = DATES[-1]; start = end - timedelta(days=30)
win = [d for d in DATES if d > start]
print("window", win[0], "to", win[-1], len(win), "trading days")
DAY = {s: {d.d: d for d in D[s]} for s in SYMS}
def day_trades(d, eq, one_lot):
    out=[]
    for sym in SYMS:
        dd = DAY[sym].get(d)
        if dd is None or d in BUD or dd.vix[0] > 99: continue
        v = dd.vix[0]
        for strat, par, vmax in (("noise", W, 18), ("camarilla", CA, 20)):
            if (strat=="noise" and v>=18) or (strat=="camarilla" and v>20): continue
            p = dict(par, one_lot=True) if one_lot else par
            for t in E.run([dd], sym, strat, p, capital=eq):
                out.append(dict(t, strat=strat, sym=sym))
    out.sort(key=lambda t: t["in"]); res=[]; busy=0
    for t in out:
        if busy > t["in"]: continue
        res.append(t); busy = t["out"]
    return res
def sim(start_cap, one_lot, weekly=True):
    eq = start_cap; log=[]; wk={}
    for d in win:
        k=d.isocalendar()[:2]; wk.setdefault(k,0)
        for t in day_trades(d, eq, one_lot):
            if weekly and wk[k] <= -0.04*start_cap: continue
            if one_lot and t["prem_in"]*META_LOT[t["sym"]] > eq: continue
            eq += t["pnl"]; wk[k]+=t["pnl"]
            log.append((str(d), t["sym"], t["strat"], t["lots"], round(t["pnl"])))
    return dict(start=start_cap, end=round(eq), pnl=round(eq-start_cap), pct=round(100*(eq/start_cap-1),1), trades=len(log), log=log)
META_LOT = {s: E.META[s]["lot"] for s in SYMS}
R={}
R["20k strict app rules"]=sim(20000, False)
R["20k one-lot mode"]=sim(20000, True)
R["20k one-lot, no weekly cap"]=sim(20000, True, weekly=False)
R["2L strict app rules"]=sim(200000, False)
R["20k strict, no weekly cap"]=sim(20000, False, weekly=False)
for k,v in R.items(): print(k, {x:v[x] for x in ("start","end","pnl","pct","trades")})
print(R["20k one-lot mode"]["log"])
print(R["2L strict app rules"]["log"])
json.dump({"window":[str(win[0]),str(win[-1])],**R},open("small20k.json","w"),indent=1)
