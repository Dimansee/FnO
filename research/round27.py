"""Round 27 (Oct 2026): an "ultimate" combination. Every rule x index is a block (Noise, Camarilla, trend-day catcher on Nifty,
Bank Nifty, Sensex = 9 blocks), traded alone at Rs 2,000 risk a trade. Blocks are picked and weighted on 2023-25 only, then judged on
2026; walk-forward re-picking every quarter from the last 12 months is tested too. The winning pick is re-checked under the app's
real slot rules (Noise and Camarilla share one slot per index; the catcher has its own). Run: python round27.py -> round27_results.json"""
"""Every block = one rule on one index, traded on its own (no blocking by other rules), 1 lot, P&L also scaled to Rs 2,000 risk."""
import sys; import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import round25_harness as H, round25_trend as TB, pandas as pd
from datetime import time
VIX=H.load('INDIAVIX')['close']; BIG=1e9
B=dict(cam_tstop=45,noise_trail='band_vwap',overlap=True,reentry=0,sizing='one_lot')
rows=[]
for s in ['NIFTY','BANKNIFTY','SENSEX']:
    c=H.load(s)
    for nm,ov in [('noise',dict(cam_vix_max=-1)),('camarilla',dict(noise_vix_max=-1))]:
        rows+=[{**r,'block':f'{s}:{nm}'} for r in H.run(s,c,VIX,{**B,**ov},capital=BIG)]
    rows+=[{**r,'block':f'{s}:catcher'} for r in TB.run(s,c,VIX,dict(t0=time(10,45),share=0.8,stop='vwap',mult=1.75,be=1.0,sizing='one_lot'),capital=BIG)]
df=pd.DataFrame(rows); df['u']=df.pnl/df.risk_lot*2000
BLOCKS=df.copy()
df['y']=df.date.str[:4]
print(df.pivot_table(index='block',columns='y',values='u',aggfunc='sum').round(0))
print(df.groupby('block').size())

import numpy as np, json
df=BLOCKS.drop(columns=['y'], errors='ignore')
days=sorted(df.date.unique())
M=df.pivot_table(index='date',columns='block',values='u',aggfunc='sum').reindex(days).fillna(0)
blocks=list(M.columns)
TR=M[M.index<'2026']; TE=M[M.index>='2026']
def st(s):
    eq=s.cumsum(); dd=(eq-eq.cummax().clip(lower=0)).min()
    sh=s.mean()/s.std()*np.sqrt(250) if s.std()>0 else 0
    return dict(net=round(float(s.sum())),maxdd=round(float(dd)),sharpe=round(float(sh),2))
def yrs(s): return {y:round(float(v)) for y,v in s.groupby(s.index.str[:4]).sum().items()}
def mon(s): return {m:round(float(v)) for m,v in s[s.index>='2026'].groupby(s[s.index>='2026'].index.str[:7]).sum().items()}
out={}
def add(name,w,note=''):
    w=pd.Series(w).reindex(blocks).fillna(0)
    s=(M*w).sum(axis=1)
    out[name]=dict(blocks={k:round(float(v),2) for k,v in w.items() if v},train=st(s[s.index<'2026']),y2026=st(s[s.index>='2026']),years=yrs(s),months_2026=mon(s),note=note)
    print(name.ljust(46),'train',out[name]['train'],'| 2026',out[name]['y2026'],flush=True)
    return s
live=['NIFTY:noise','NIFTY:camarilla','BANKNIFTY:noise','BANKNIFTY:camarilla','NIFTY:catcher','BANKNIFTY:catcher','SENSEX:catcher']
add('M0 current live set',{b:1 for b in live})
add('M1 all 9 blocks',{b:1 for b in blocks})
# M2 train-selected
ty=TR.groupby(TR.index.str[:4]).sum()
pf=lambda s: s[s>0].sum()/-s[s<0].sum() if (s<0).any() else 9
sel=[b for b in blocks if TR[b].sum()>0 and (ty[b]>0).sum()>=2 and pf(df[(df.block==b)&(df.date<'2026')].u)>=1.05]
print('selected on 2023-25:',sel)
add('M2 picked on 2023-25 (profitable, 2 of 3 yrs, PF>=1.05)',{b:1 for b in sel})
vol=TR[sel].std(); iv=(1/vol); iv=iv/iv.mean()
add('M3 picked + inverse-volatility weights',iv.to_dict())
shp=(TR.mean()/TR.std()).sort_values(ascending=False)
add('M6 top 3 blocks by 2023-25 Sharpe',{b:1 for b in shp.index[:3]})
# walk-forward: re-pick each quarter from trailing 12 months
qs=pd.period_range('2024Q1','2026Q4',freq='Q')
def wf(kind):
    parts=[]; picks={}
    for q in qs:
        a,b=q.start_time.strftime('%Y-%m-%d'),q.end_time.strftime('%Y-%m-%d')
        look=M[(M.index>=(q.start_time-pd.DateOffset(years=1)).strftime('%Y-%m-%d'))&(M.index<a)]
        cur=M[(M.index>=a)&(M.index<=b)]
        if cur.empty: continue
        if kind=='positive':
            w={k:1.0 for k in blocks if look[k].sum()>0}
        else:
            sh=(look.mean()/look.std()).clip(lower=0).fillna(0); w=(sh/sh[sh>0].mean()).to_dict() if (sh>0).any() else {}
        picks[str(q)]=sorted(k for k,v in w.items() if v>0)
        parts.append((cur*pd.Series(w).reindex(blocks).fillna(0)).sum(axis=1))
    return pd.concat(parts),picks
for kind,name in [('positive','M4 walk-forward: blocks with a profitable last 12 months'),('sharpe','M5 walk-forward: weights by last-12-month Sharpe')]:
    s,p=wf(kind)
    out[name]=dict(picks=p,y2024_25=st(s[s.index<'2026']),y2026=st(s[s.index>='2026']),years=yrs(s),months_2026=mon(s))
    print(name.ljust(46),'2024-25',out[name]['y2024_25'],'| 2026',out[name]['y2026'],flush=True)
# same-period reference for walk-forward
for nm in ['M0 current live set','M2 picked on 2023-25 (profitable, 2 of 3 yrs, PF>=1.05)']:
    w=pd.Series(out[nm]['blocks']).reindex(blocks).fillna(0); s=(M*w).sum(axis=1)
    out[nm]['y2024_25']=st(s[(s.index>='2024')&(s.index<'2026')])
    print('  ref',nm[:30],'2024-25',out[nm]['y2024_25'])
out['correlation_train']=TR.corr().round(2).to_dict()
RESULTS={'combinations':out}

import json
VIX=H.load('INDIAVIX')['close']; BIG=1e9
B=dict(cam_tstop=45,noise_trail='band_vwap',overlap=False,reentry=0,sizing='one_lot')
T=dict(t0=time(10,45),share=0.8,stop='vwap',mult=1.75,be=1.0,sizing='one_lot')
def main(s,cam=True): return [{**r,'sym':s,'slot':'main'} for r in H.run(s,H.load(s),VIX,{**B,**({} if cam else {'cam_vix_max':-1})},capital=BIG)]
def catch(s): return [{**r,'sym':s,'slot':'trend'} for r in TB.run(s,H.load(s),VIX,T,capital=BIG)]
cur=main('NIFTY')+main('BANKNIFTY')+catch('NIFTY')+catch('BANKNIFTY')+catch('SENSEX')
new=main('NIFTY')+main('BANKNIFTY')+main('SENSEX',cam=False)+catch('NIFTY')+catch('SENSEX')
def st(s):
    eq=s.cumsum(); return dict(net=round(float(s.sum())),maxdd=round(float((eq-eq.cummax().clip(lower=0)).min())),sharpe=round(float(s.mean()/s.std()*np.sqrt(250)),2))
out={}
days=None
for nm,rows in [('current live set',cur),('ultimate pick (M2 under live slot rules)',new)]:
    df=pd.DataFrame(rows); df['u']=df.pnl/df.risk_lot*2000;
    d=df.groupby('date').u.sum()
    alld=sorted(set(H.load('NIFTY').index.strftime('%Y-%m-%d'))); d=d.reindex([x for x in alld if x>='2023-01-03']).fillna(0)
    m=d[d.index>='2026']
    out[nm]=dict(train=st(d[d.index<'2026']),y2026=st(m),years={y:round(float(v)) for y,v in d.groupby(d.index.str[:4]).sum().items()},
                 months_2026={k:round(float(v)) for k,v in m.groupby(m.index.str[:7]).sum().items()},trades=len(df))
    print(nm.ljust(42),out[nm]['train'],out[nm]['y2026'],out[nm]['years'],flush=True)
RESULTS['live_slot_rules']=out
json.dump(RESULTS,open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'round27_results.json'),'w'),indent=1,default=str)
print('saved round27_results.json')
