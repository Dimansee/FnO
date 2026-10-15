import os; os.environ.setdefault("FNO_CALIB","1")
src_head=open("round20.py").read().split("R={}")[0]
src_head=src_head.replace('src = src.replace("capital * 0.6 //"','src = src.replace("\\"prem_in\\": prem_in,","\\"prem_in\\": prem_in, \\"risk_lot\\": risk_unit*lot,").replace("capital * 0.6 //"')
exec(src_head)
import statistics as stt
T=[]
for d in DATES:
    for t in trades_for(d, 200000, 1.75, .01, True):   # one lot every time; we filter by risk afterwards
        T.append(t)
print("trades", len(T), "risk_lot" in T[0])
months=sorted({(d.year,d.month) for d in DATES})
def run(ms, cap, cap0=20000, weekly=None):
    eq=cap0; peak=eq; mdd=0; low=eq; n=0; wk=defaultdict(float)
    for t in T:
        if (t["date"].year,t["date"].month) not in ms: continue
        lot=E.META[t["sym"]]["lot"]; k=t["date"].isocalendar()[:2]
        if t["prem_in"]*lot > eq: continue
        if cap is not None and t["risk_lot"] > cap*eq: continue
        if weekly and wk[k] <= -weekly*eq: continue
        eq+=t["pnl"]; wk[k]+=t["pnl"]; n+=1; peak=max(peak,eq); mdd=min(mdd,(eq-peak)/peak); low=min(low,eq)
    return eq,mdd,low,n
R={}
for cap in (None,.20,.15,.12,.10,.08,.06):
    wins=[run(set(months[i:i+12]),cap) for i in range(len(months)-11)]
    fin=[w[0] for w in wins]; full=run(set(months),cap)
    R[str(cap)]=dict(median_final=round(stt.median(fin)), pct_below_start=round(100*sum(f<20000 for f in fin)/len(fin)), pct_dipped_below_10k=round(100*sum(w[2]<10000 for w in wins)/len(wins)),
                     median_dd=round(100*stt.median(w[1] for w in wins)), worst_dd=round(100*min(w[1] for w in wins)), median_trades=int(stt.median(w[3] for w in wins)), full_final=round(full[0]), full_dd=round(100*full[1]))
    print("risk cap per trade",cap, R[str(cap)], flush=True)
import json; json.dump(R,open("round20c.json","w"),indent=1)
