"""Round 64b: the live crypto book through the money simulation (fno/gbacktest.money_sim): which rules, leverage and the
high-volatility skip, Rs 2L at 1% risk, max 4 open, 2023 - Oct 2026 and both halves. Run from the repo root."""
import sys, numpy as np, pandas as pd
sys.path.insert(0,'research'); sys.path.insert(0,'.')
import round64_untested_signals as M, round55_engine as E
from fno import gbacktest as GBT
M.DATA='/home/claude/rd/research/data/global'
def live_trades(rules=None):
    out=[]
    for rule,(tf,kind,trail) in M.LIVE.items():
        if rules and rule not in rules: continue
        for sym in M.C11:
            df=M.load(sym,tf); o,h,l,c=(df[k].values for k in("open","high","low","close")); a=E.atr(df); n=len(c)
            side=np.zeros(n,np.int64); ol=np.zeros(n,np.bool_); osr=np.zeros(n,np.bool_)
            if kind=="st":
                dr=E.supertrend_dir(h,l,c,a,3.0); fl=np.r_[False,(dr[1:]==1)&(dr[:-1]==-1)]; fs=np.r_[False,(dr[1:]==-1)&(dr[:-1]==1)]
                side[fl],side[fs]=1,-1; ol,osr=fl,fs
            else:
                up=pd.Series(h).rolling(kind).max().shift(1).values; side[(c>up)&np.r_[False,c[:-1]<=up[:-1]]]=1
            side[df.index<pd.Timestamp("2023-01-01",tz="UTC")]=0
            per=365*(6 if tf=="4h" else 1); atrp=pd.Series(a/c); q_hi=atrp.rolling(per,min_periods=per//2).quantile(2/3).values
            nanv=np.full(n,np.nan)
            en,sd,R,b,g=E.run(o,h,l,c,a,side,nanv,nanv,ol,osr,0.0,trail,0.0,0.0,0,0.0,2.0)
            step=pd.Timedelta(hours=4) if tf=="4h" else pd.Timedelta(days=1)
            for k in range(len(en)):
                i=en[k]; e=o[i]
                out.append({"symbol":sym,"rule":rule,"entry_ts":df.index[i],"exit_ts":df.index[min(i+b[k]-1,n-1)]+step,"entry":e,"sl0":e-sd[k]*2*a[i-1],"R":R[k],"hiv":bool(atrp.values[i-1]>=q_hi[i-1])})
    return out
L=live_trades()
P={"all":("2023-01-01","2026-12-31"),"early":("2023-01-01","2025-03-31"),"late":("2025-04-01","2026-12-31")}
def run(T,lev,mo,risk=1.0):
    r={}
    for k,(a,b) in P.items():
        A,B=pd.Timestamp(a,tz="UTC"),pd.Timestamp(b,tz="UTC")
        m=GBT.money_sim([t for t in T if A<=t["entry_ts"]<=B],200000,risk,lev,mo); r[k]=m
    return r
fmt=lambda r:" | ".join(f"{k} ₹{v['final']/1e5:.2f}L ({v['max_dd_pct']:.0f}%)" for k,v in r.items())
print("R per trade, all signals:", {rl:round(float(np.mean([t['R'] for t in L if t['rule']==rl])),3) for rl in M.LIVE})
for rules in (None,["Supertrend 4h"],["Supertrend 4h","Supertrend daily"],["Supertrend 4h","Supertrend daily","Donchian 20 daily"]):
    T=[t for t in L if rules is None or t["rule"] in rules]
    for lev,mo in ((1,4),(3,4),(3,8),(5,8)):
        r=run(T,lev,mo); rf=run([t for t in T if not t["hiv"]],lev,mo)
        print(f"{'+'.join(rules) if rules else 'ALL 4 RULES (live book)':<55} lev {lev}x max {mo}: {fmt(r)}\n{'':<55}   skip high-vol: {fmt(rf)}")
# what got taken at 1x
m=GBT.money_sim(L,200000,1.0,1.0,4)
print("\n=== candidate fixes vs live book (Rs 2L, 1% risk)")
base3=["Supertrend 4h","Supertrend daily","Donchian 20 daily"]
C={"Live book (4 rules, 1x, max 4)":(None,1,4,False),
   "Drop Donchian 55 4h (1x, max 4)":(base3,1,4,False),
   "All 4 rules, 3x leverage (max 4)":(None,3,4,False),
   "Drop Donchian 55 + 3x (max 4)":(base3,3,4,False),
   "Drop Donchian 55 + 3x + skip high-vol":(base3,3,4,True),
   "Drop Donchian 55 + 1x + skip high-vol":(base3,1,4,True)}
for nm,(rules,lev,mo,hv) in C.items():
    T=[t for t in L if (rules is None or t["rule"] in rules) and (not hv or not t["hiv"])]
    r=run(T,lev,mo)
    yrs={}
    m=GBT.money_sim(T,200000,1.0,lev,mo)
    print(f"{nm:<42} {fmt(r)} | trades {m['trades']} skipped {m['skipped_full']+m['skipped_cash']}")
