"""Round 85 (5 Nov 2026): CE on the reversal from the session average after a gap (Nifty 24,350 on 5 Nov), trail choices.
Part 1 (trail.py logic) replays 5 Nov on recorded option prices; part 2 below is the 2023 - Oct 2026 event study on 1-min index data."""
# Event study: gap day, opening low holds, pullback to session average after 10:00, buy on 5m close back above it.
import pandas as pd, numpy as np, sys
out=[]
for sym in ['NIFTY','BANKNIFTY','SENSEX']:
    d=pd.read_csv(f'/home/claude/data/{sym}_1m_upstox.csv.gz',parse_dates=['ts']).set_index('ts')
    d=d[(d.index.time>=pd.Timestamp('09:15').time())&(d.index.time<=pd.Timestamp('15:29').time())]
    days=sorted(set(d.index.date)); prevc=None
    for day in days:
        x=d[d.index.date==day]
        if len(x)<300: prevc=x.close.iloc[-1] if len(x) else prevc; continue
        if prevc is None: prevc=x.close.iloc[-1]; continue
        gap=x.open.iloc[0]/prevc-1; pc=prevc; prevc=x.close.iloc[-1]
        for side in (1,-1):
            if side*gap<0.005: continue
            tp=(x.high+x.low+x.close)/3; avg=tp.expanding().mean()
            b=x.resample('5min').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
            b['avg']=avg.resample('5min').last()
            orl=x.iloc[:15].low.min() if side==1 else x.iloc[:15].high.max()
            ent=None; touched=False; ext=None
            for t,r in b.iterrows():
                if t.time()<pd.Timestamp('10:00').time() or t.time()>pd.Timestamp('13:30').time(): continue
                sofar=x.loc[:t+pd.Timedelta(minutes=4)]
                broke = (sofar.low.min()<orl) if side==1 else (sofar.high.max()>orl)
                if broke: break
                tol=r.avg*0.0005
                if (side==1 and r.low<=r.avg+tol) or (side==-1 and r.high>=r.avg-tol):
                    touched=True; ext=(r.low if side==1 else r.high) if ext is None else (min(ext,r.low) if side==1 else max(ext,r.high))
                    if (side==1 and r.close>r.avg and r.close>r.open) or (side==-1 and r.close<r.avg and r.close<r.open):
                        ent=(t+pd.Timedelta(minutes=5), r.close, ext - side*r.avg*0.0002); break
            if not ent: continue
            t0,e,s=ent; R=abs(e-s)
            if R<=0 or R>e*0.004: continue
            path=x.loc[t0:pd.Timestamp(f'{day} 15:14')]
            if path.empty: continue
            res={}
            for mode in ['fixed','be1R','avg_trail','trail0.2%','swing2']:
                stop=s; hi=e; armed=False; xp=None
                for t,r in path.iterrows():
                    if (side==1 and r.low<=stop) or (side==-1 and r.high>=stop): xp=stop; break
                    hi=max(hi,r.high) if side==1 else min(hi,r.low)
                    if (side*(hi-e))>=R: armed=True
                    if (t+pd.Timedelta(minutes=1)).minute%5==0:
                        bt=t-pd.Timedelta(minutes=4)
                        if mode=='be1R' and armed: stop=max(stop,e) if side==1 else min(stop,e)
                        if mode=='avg_trail':
                            a=avg.loc[:t].iloc[-1]
                            if side*(r.close-a)<0: xp=r.close; break
                        if mode=='trail0.2%' and armed:
                            ns=hi-side*e*0.002; stop=max(stop,ns) if side==1 else min(stop,ns)
                        if mode=='swing2' and armed:
                            bb=x.loc[:t].resample('5min').agg({'low':'min','high':'max'}).iloc[-2:]
                            ns=bb.low.min() if side==1 else bb.high.max(); stop=max(stop,ns) if side==1 else min(stop,ns)
                if xp is None: xp=path.close.iloc[-1]
                res[mode]=side*(xp-e)/e*100
            out.append(dict(sym=sym,day=day,side=side,gap=gap*100,t=t0.time(),R_pct=R/e*100,**res,
                            close_move=side*(path.close.iloc[-1]-e)/e*100))
df=pd.DataFrame(out); df['yr']=pd.to_datetime(df.day).dt.year
df.to_csv('research/out_round85_hist_events.csv',index=False)
modes=['fixed','be1R','avg_trail','trail0.2%','swing2']
COST=0.06   # % of index: approx option round-trip cost + spread + decay hurdle for an ATM weekly over a few hours (delta-adjusted)
print('events',len(df), 'cost hurdle per trade (index %%) = %.2f'%COST)
for g,sub in [('ALL',df),('gap>=1%',df[df.gap.abs()>=1])]+[(str(y),df[df.yr==y]) for y in sorted(df.yr.unique())]+[(s,df[df.sym==s]) for s in df.sym.unique()]+[('calls',df[df.side==1]),('puts',df[df.side==-1])]:
    line=f'{g:9s} n={len(sub):4d} '
    for m in modes: line+=f'| {m} avg {sub[m].mean():+.3f}% net {sub[m].mean()-COST:+.3f}% win {(sub[m]>0).mean()*100:3.0f}% '
    print(line)
