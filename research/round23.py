import os; os.environ.setdefault("FNO_CALIB","1")
src=open("round21.py").read()
exec(src.split("# ------------------------------------------------ A. what predicts")[0])
import numpy as np
res={}
def run_all(wn=W,wc=CA):
    tr=[]
    for s in SYMS: tr+=both(s,w=wn,ca=wc)
    return tr
RAW=run_all()
def filt(tr,nmax=18,cmax=20):
    tr=[t for t in tr if t["date"].weekday()<5 and t["date"] not in BUD and not (t["strat"]=="noise" and vix0(t)>=nmax) and not (t["strat"]=="camarilla" and vix0(t)>cmax)]
    tr.sort(key=lambda t:(t["date"],t["in"])); out=[]; wkk=defaultdict(float)
    for t in tr:
        k=t["date"].isocalendar()[:2]
        if wkk[k] <= -0.04*200000: continue
        out.append(t); wkk[k]+=t["pnl"]
    return out
BASEF=filt(RAW); res["base"]=summ(BASEF); print("base",res["base"],flush=True)
# 1 slippage / price-bias break-even
res["slip"]={}
for sl in (0.005,0.01,0.015,0.02,0.03):
    E.SLIP=sl; tr=filt(run_all()); res["slip"][sl]=summ(tr); print("slip",sl,res["slip"][sl],flush=True)
E.SLIP=0.005
# 2 concentration
pn=sorted([t["pnl"] for t in BASEF],reverse=True); tot=sum(pn)
res["conc"]={f"without best {k}":round((tot-sum(pn[:k]))/1000,1) for k in (1,5,10,20)}
res["conc"]["total_k"]=round(tot/1000,1); res["conc"]["best20_share_%"]=round(100*sum(pn[:20])/tot,1)
print("conc",res["conc"],flush=True)
# 3 plateau
res["plateau"]={}
for m in (1.5,1.75,2.0):
    for sa in (1.5,2.0,2.5):
        tr=filt(run_all(dict(W,mult=m,stop_atr=sa))); n=[t for t in tr if t["strat"]=="noise"]
        res["plateau"][f"noise mult {m} / stop {sa}"]=dict(noise_k=round(sum(t["pnl"] for t in n)/1000,1), n=len(n), yrs={y:round(sum(t["pnl"] for t in n if t["date"].year==y)/1000,1) for y in (2023,2024,2025,2026)})
        print("plateau",m,sa,res["plateau"][f"noise mult {m} / stop {sa}"],flush=True)
res["vixceil"]={}
for nm in (14,16,18,20,22,99):
    for cm in (16,18,20,22,99):
        tr=filt(RAW,nm,cm); res["vixceil"][f"noise<{nm} cam<={cm}"]=dict(net_k=round(sum(t["pnl"] for t in tr)/1000,1), unseen_k=round(sum(t["pnl"] for t in tr if t["date"]>=SPLIT)/1000,1), dd_k=round(stats(tr)["max_dd"]/1000,1))
print("vixceil",{k:v["net_k"] for k,v in res["vixceil"].items()},flush=True)
# 4 whole-lot sizing at small capital, weekly block bootstrap
wk=defaultdict(list)
for t in BASEF: wk[t["date"].isocalendar()[:2]].append(t)
WKS=[list(v) for v in wk.values()]
def simulate(cap0,mode,rng):
    eq=cap0; peak=cap0; path=[eq]; lowest=eq
    for _ in range(52):
        for t in WKS[rng.integers(len(WKS))]:
            lots0=max(t["lots"],1); per=t["pnl"]/lots0; cost=t["prem_in"]*E.META[t["sym"]]["lot"]; rpl=2000/(lots0+0.5)
            if mode=="fixed 1 lot":
                n=1 if cost<=eq else 0
            elif mode=="1 lot, skip if one lot risks >15%":
                n=1 if (cost<=eq and rpl<=0.15*eq) else 0
            elif mode=="compound lots (1% risk, whole lots, min 1 if <=15%)":
                n=int(eq*0.01//rpl); n=n if n>=1 else (1 if (rpl<=0.15*eq and cost<=eq) else 0)
                n=min(n,int(eq//max(cost,1)))
            elif mode=="same + halve size when 10% below peak":
                r=0.005 if eq<0.9*peak else 0.01
                n=int(eq*r//rpl); n=n if n>=1 else (1 if (rpl<=0.15*eq and cost<=eq) else 0)
                n=min(n,int(eq//max(cost,1)))
            eq+=per*n; peak=max(peak,eq); lowest=min(lowest,eq)
            if eq<=0: return 0,0,peak
        path.append(eq)
    return eq,lowest,peak
res["sizing"]={}
for cap0 in (20000,50000,100000,200000):
    for mode in ("fixed 1 lot","1 lot, skip if one lot risks >15%","compound lots (1% risk, whole lots, min 1 if <=15%)","same + halve size when 10% below peak"):
        rng=np.random.default_rng(7); ends=[];lows=[]
        for _ in range(2000):
            e,l,p=simulate(cap0,mode,rng); ends.append(e); lows.append(l)
        ends=np.array(ends); lows=np.array(lows)
        r=dict(median_end=round(float(np.median(ends))), p5_end=round(float(np.percentile(ends,5))), p_below_start=round(float((ends<cap0).mean()*100)), p_50pct_fall=round(float((lows<0.5*cap0).mean()*100)), p_ruin=round(float((lows<0.2*cap0).mean()*100)))
        res["sizing"][f"{cap0}|{mode}"]=r; print("size",cap0,mode,r,flush=True)
json.dump(res,open("round23.json","w"),default=str,indent=1)
