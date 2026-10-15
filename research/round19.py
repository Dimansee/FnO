import os; os.environ.setdefault("FNO_CALIB","1")
exec(open("round6.py").read().split("# ------------------------------------------------------------------ 2. entry delay")[0].replace('show("BASE','pass # show("BASE'))
import json, statistics as stt
from collections import defaultdict
BUD = {date(2023,2,1), date(2024,2,1), date(2024,7,23), date(2025,2,1), date(2026,2,1)}
def vix0(t): return DAYMAP[t["sym"]][t["date"]].vix[0]
def current(tr):
    tr=[t for t in tr if t["date"].weekday()<5 and t["date"] not in BUD
        and not (t["strat"]=="noise" and vix0(t)>=18) and not (t["strat"]=="camarilla" and vix0(t)>20)]
    tr.sort(key=lambda t:(t["date"],t["in"]))
    out=[]; wk=defaultdict(float)
    for t in tr:
        k=t["date"].isocalendar()[:2]
        if wk[k] <= -0.04*200000: continue
        out.append(t); wk[k]+=t["pnl"]
    return out
def monthly(tr, compound=False, start=200000):
    eq=start; bym=defaultdict(float); peak=start; mdd=0
    for t in sorted(tr,key=lambda t:(t["date"],t["in"])):
        p=t["pnl"]*(eq/start if compound else 1)
        eq+=p; bym[(t["date"].year,t["date"].month)]+=p/ (eq-p if compound else start)
        peak=max(peak,eq); mdd=min(mdd,(eq-peak)/peak)
    v=list(bym.values())
    return dict(months=len(v), avg_pct=round(100*stt.mean(v),1), median_pct=round(100*stt.median(v),1), pos_months=round(100*sum(x>0 for x in v)/len(v)),
                best_pct=round(100*max(v),1), worst_pct=round(100*min(v),1), months_ge_50=sum(x>=.5 for x in v), maxdd_pct=round(100*mdd,1), final=round(eq))
OUT={}
for r in (0.01,0.015,0.02,0.03):
    E.RISK=r
    tr=current(book())
    OUT[f"risk{r}"]={"flat":monthly(tr),"compound":monthly(tr,True),"n":len(tr),"net_k":round(sum(t["pnl"] for t in tr)/1000,1)}
    print(f"risk {r:.1%}", OUT[f"risk{r}"], flush=True)
E.RISK=0.01
CUR=current(BASE)
def summ(tr):
    a=[t for t in tr if t["date"]<SPLIT]; b=[t for t in tr if t["date"]>=SPLIT]; s=stats(tr)
    return dict(n=len(tr), net_k=round(sum(t["pnl"] for t in tr)/1000,1), train_k=round(sum(t["pnl"] for t in a)/1000,1), unseen_k=round(sum(t["pnl"] for t in b)/1000,1), pf=s["pf"], dd_k=round(s["max_dd"]/1000,1), yrs={y:round(sum(t["pnl"] for t in tr if t["date"].year==y)/1000,1) for y in (2023,2024,2025,2026)})
OUT["current"]=summ(CUR); print("current", OUT["current"])
for wd,name in enumerate("Mon Tue Wed Thu Fri".split()):
    x=[t for t in CUR if t["date"].weekday()!=wd]; OUT["skip_"+name]=summ(x); print("skip",name,OUT["skip_"+name])
def hr(t): return (t["in"]+0)//60
for lo,hi in ((9,10),(10,11),(11,12),(12,15)):
    x=[t for t in CUR if not (lo<=t["in"]/60<hi)]; OUT[f"skip_{lo}-{hi}h"]=summ(x); print(f"skip entries {lo}-{hi}h",OUT[f"skip_{lo}-{hi}h"])
by=defaultdict(list)
for t in CUR: by[int(t["in"]//60)].append(t)
for h,x in sorted(by.items()): print("entry hour",h,len(x),round(sum(t["pnl"] for t in x)/1000,1), stats(x)["pf"])
json.dump(OUT,open("round19.json","w"),default=str,indent=1)
