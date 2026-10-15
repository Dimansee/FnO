import os; os.environ.setdefault("FNO_CALIB","1")
exec(open("round20.py").read().split("R={}")[0])
import statistics as stt
T=[]
for d in DATES:
    for t in trades_for(d, 20000, 1.75, .01, True): T.append(t)
print("trades", len(T))
months=sorted({(d.year,d.month) for d in DATES})
out=[]
for i in range(len(months)-11):
    ms=set(months[i:i+12]); eq=20000; peak=eq; mdd=0; low=eq; ntr=0
    for t in T:
        if (t["date"].year,t["date"].month) not in ms: continue
        if t["prem_in"]*E.META[t["sym"]]["lot"] > eq: continue
        eq+=t["pnl"]; ntr+=1; peak=max(peak,eq); mdd=min(mdd,(eq-peak)/peak); low=min(low,eq)
    out.append((months[i],round(eq),round(100*mdd),round(low),ntr))
fin=[o[1] for o in out]
print("12-month windows:", len(out), "median final", round(stt.median(fin)), "min", min(fin), "max", max(fin), "below start:", sum(f<20000 for f in fin), "below 10k at some point:", sum(o[3]<10000 for o in out), "worst dd%", min(o[2] for o in out), "median dd%", stt.median(o[2] for o in out))
for o in out[::6]: print(o)
# monthly median on the full run
eq=20000; bym=defaultdict(float); ms={}
for t in sorted(T,key=lambda t:(t["date"],t["in"])):
    m=(t["date"].year,t["date"].month); ms.setdefault(m,eq)
    if t["prem_in"]*E.META[t["sym"]]["lot"] > eq: continue
    eq+=t["pnl"]; bym[m]=eq/ms[m]-1
v=list(bym.values()); print("median month %", round(100*stt.median(v),1), "months", len(v), "months>=50%", sum(x>=.5 for x in v), "top3 trades share of profit", round(100*sum(sorted((t['pnl'] for t in T), reverse=True)[:3])/sum(t['pnl'] for t in T)))
import json; json.dump({"windows":out,"median_month":round(100*stt.median(v),1)},open("round20b.json","w"),indent=1)
