import sys, json; sys.path.insert(0,"/home/claude/dimansee/fno/research/round101"); sys.path.insert(0,"/home/claude/r102")
import lib as L, numpy as np, pandas as pd, addtest_lite as AT
T0,A0,H0=L.base_sets()
out={}
for sym in L.SYMS:
    a=L.M.R34.m1(sym); d=a.groupby(a.index.strftime("%Y-%m-%d")).agg(o=("open","first"),c=("close","last"))
    d["mv"]=(d.c/d.o-1)*100; td=d[d.mv.abs()>=0.8]
    b=T0[T0.sym==sym]; 
    def caught(tr):
        n=0
        for dt,r in td.iterrows():
            x=tr[(tr.date==dt)]
            if len(x)==0: continue
            sd=np.where(x.side.isin(["CALL"]) if "side" in x and x.side.dtype==object else x.side>0,1,-1)
            if (sd*np.sign(r.mv)>0).any(): n+=1
        return n
    o1=caught(b)
    zp=AT.zone_raw(f"{sym}|15m|z0.5|brk|week1|col|candle|eod|1d")
    o2=caught(zp)
    both=0
    for dt,r in td.iterrows():
        x=b[b.date==dt]; y=zp[zp.date==dt]
        ok1=len(x) and (np.where(x.side=="CALL",1,-1)*np.sign(r.mv)>0).any()
        ok2=len(y) and ((y.side*np.sign(r.mv))>0).any()
        both+= bool(ok1 or ok2)
    out[sym]={"trend_days":len(td),"autopilot_signal_with_trend":o1,"zone_break_with_trend":o2,"either":both}
    print(sym,out[sym])
json.dump(out,open("/home/claude/r102/out/trendcatch.json","w"))
