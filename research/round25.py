"""Round 25 (Oct 2026): how a Rs 2 lakh account should size. Per-lot trades of the current rule set (Noise + Camarilla on Nifty /
Bank Nifty, trend-day catcher on Nifty / Bank Nifty / Sensex) are replayed with compounding from Rs 2 lakh, the app's sizing
(lots = risk % of equity / risk of one lot, 1-lot floor while one lot risks <= 15% of equity), free-cash limit, weekly loss cap and the
Rs 40k catcher gate. Compared: risk 1-4%, open-risk caps, drawdown throttles, a tighter 1-lot limit; single path 2023-25 / 2026 and
every 12-month window from Jan 2023 to Oct 2025. Run: python round25.py -> round25_results.json"""
import sys; import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import round25_harness as H, round25_trend as TB, pandas as pd
from datetime import time
VIX=H.load('INDIAVIX')['close']; BIG=1e9
BASE=dict(cam_tstop=45,noise_trail='band_vwap',overlap=False,reentry=0,sizing='one_lot')
TREND=dict(t0=time(10,45),share=0.8,stop='vwap',mult=1.75,be=1.0,sizing='one_lot')
rows=[]
for s in ['NIFTY','BANKNIFTY']:
    rows+=[{**r,'slot':'main'} for r in H.run(s,H.load(s),VIX,BASE,capital=BIG)]
for s in ['NIFTY','BANKNIFTY','SENSEX']:
    rows+=[{**r,'slot':'trend'} for r in TB.run(s,H.load(s),VIX,TREND,capital=BIG)]
df=pd.DataFrame(rows); df['sym']=df['sym'].fillna('?')
T=df; print(len(df)); print(df.groupby(['sym','slot']).agg(n=('pnl','size'),pnl=('pnl','sum'),risk=('risk_lot','median'),cost=('cost_lot','median')).round())

import pandas as pd, numpy as np, json
T['t_in']=pd.to_datetime(T.date+' '+T.entry_time); T['t_out']=pd.to_datetime(T.date+' '+T.exit_time)
T=T.sort_values('t_in').reset_index(drop=True)
BROK=47.2
def sim(r, start=200000, startdate='2023-01-01', enddate='2099', cap_trade=0.15, gate=40000, open_cap=None, throttle=None, big_cap=None):
    t=T[(T.date>=startdate)&(T.date<enddate)]
    eq=start; peak=start; maxdd=0; open_=[]; rows=[]; wk=None; wk_eq=start; wk_pnl=0
    for x in t.itertuples():
        # close positions that ended before this entry
        still=[]
        for o in open_:
            if o['t_out']<=x.t_in: eq+=o['pnl']; wk_pnl+=o['pnl'] if o['wk']==wk else 0; peak=max(peak,eq); maxdd=min(maxdd,eq/peak-1); rows.append(o)
            else: still.append(o)
        open_=still
        w=x.t_in.strftime('%G-%V')
        if w!=wk: wk, wk_eq, wk_pnl = w, eq, 0
        cap=0.15 if eq<100000 else 0.04
        if wk_pnl <= -cap*wk_eq: continue
        if x.slot=='trend' and eq<gate: continue
        rr=r
        if throttle:
            dd=eq/peak-1
            for lvl,mult in throttle:
                if dd<=lvl: rr=r*mult
        lots=int(eq*rr//x.risk_lot)
        ct=cap_trade if (big_cap is None or eq<100000) else big_cap*(rr/r)
        if lots==0:
            if x.risk_lot<=ct*eq: lots=1
            else: continue
        free=eq-sum(o['cost'] for o in open_)
        lots=min(lots,int(free//x.cost_lot))
        if open_cap is not None:
            room=open_cap*eq-sum(o['riskrs'] for o in open_)
            lots=min(lots,int(room//x.risk_lot))
        if lots<1: continue
        pnl=(x.pnl+BROK)*lots-BROK
        open_.append(dict(riskrs=x.risk_lot*lots,t_out=x.t_out,pnl=pnl,cost=x.cost_lot*lots,wk=w,date=x.date,lots=lots,sym=x.sym,slot=x.slot,used=x.cost_lot*lots/eq,risk=x.risk_lot*lots/eq))
    for o in open_: eq+=o['pnl']; rows.append(o); peak=max(peak,eq); maxdd=min(maxdd,eq/peak-1)
    R=pd.DataFrame(rows)
    return eq,maxdd,R


import json
TH=[(-0.10,0.5),(-0.20,0.25)]
C={'1% (now: 1 lot up to 15%)':dict(r=0.01),
   '1%, 1 lot only up to 3% (>=1L)':dict(r=0.01,big_cap=0.03),
   '1%, 1 lot only up to 2% (>=1L)':dict(r=0.01,big_cap=0.02),
   '1%, up to 2% + throttle':dict(r=0.01,big_cap=0.02,throttle=TH),
   '1.5%, up to 2.25% + throttle':dict(r=0.015,big_cap=0.0225,throttle=TH),
   '1.5%, up to 3% + throttle':dict(r=0.015,big_cap=0.03,throttle=TH),
   '2%, up to 3% + throttle':dict(r=0.02,big_cap=0.03,throttle=TH),
   '1%, up to 2% + open<=4%':dict(r=0.01,big_cap=0.02,open_cap=0.04),
   '1.5%, up to 2.25% + open<=4% + throttle':dict(r=0.015,big_cap=0.0225,open_cap=0.04,throttle=TH)}
starts=pd.date_range('2023-01-01','2025-10-01',freq='MS')
out={}
for nm,kw in C.items():
    rets,dds=[],[]
    for st in starts:
        e=(st+pd.DateOffset(years=1)).strftime('%Y-%m-%d')
        eq,dd,_=sim(startdate=st.strftime('%Y-%m-%d'),enddate=e,**kw); rets.append(eq/200000-1); dds.append(dd)
    e_tr,dd_tr,_=sim(enddate='2026',**kw); e26,dd26,R=sim(startdate='2026-01-01',**kw); eA,ddA,RA=sim(**kw)
    r=np.array(rets)*100; d=np.array(dds)*100
    out[nm]=dict(median_12m=round(float(np.median(r)),1),worst_12m=round(float(r.min()),1),best_12m=round(float(r.max()),1),pct_windows_loss=round(float((r<0).mean()*100)),
                 median_maxdd=round(float(np.median(d)),1),worst_maxdd=round(float(d.min()),1),pct_dd_worse_25=round(float((d<-25).mean()*100)),
                 train_cagr=round(((e_tr/200000)**(1/3)-1)*100,1),train_maxdd=round(dd_tr*100,1),y2026_ret=round((e26/200000-1)*100,1),y2026_maxdd=round(dd26*100,1),
                 trades_2026=len(R),avg_lots_2026=round(float(R.lots.mean()),2),median_capital_used_pct=round(float(R.used.median()*100),1),
                 months_2026=R.groupby(R.date.str[:7]).pnl.sum().round().astype(int).to_dict(),full_by_year=RA.groupby(RA.date.str[:4]).pnl.sum().round().astype(int).to_dict())
    print(nm.ljust(40),{k:v for k,v in out[nm].items() if k not in ('months_2026','full_by_year')},flush=True)


TH=[(-0.10,0.5),(-0.20,0.25)]
for nm,kw in {"1%":dict(r=0.01),"1.5%":dict(r=0.015),"2%":dict(r=0.02),"2.5%":dict(r=0.025),"3%":dict(r=0.03),"4%":dict(r=0.04),
              "1% + throttle":dict(r=0.01,throttle=TH),"1.5% + throttle":dict(r=0.015,throttle=TH),"2% + open risk <= 4% + throttle":dict(r=0.02,open_cap=0.04,throttle=TH)}.items():
    if nm in out: continue
    rets,dds=[],[]
    for st in starts:
        e=(st+pd.DateOffset(years=1)).strftime('%Y-%m-%d'); eq,dd,_=sim(startdate=st.strftime('%Y-%m-%d'),enddate=e,**kw); rets.append(eq/200000-1); dds.append(dd)
    e_tr,dd_tr,_=sim(enddate='2026',**kw); e26,dd26,R=sim(startdate='2026-01-01',**kw)
    r=np.array(rets)*100; d=np.array(dds)*100
    out[nm]=dict(median_12m=round(float(np.median(r)),1),worst_12m=round(float(r.min()),1),best_12m=round(float(r.max()),1),pct_windows_loss=round(float((r<0).mean()*100)),
                 median_maxdd=round(float(np.median(d)),1),worst_maxdd=round(float(d.min()),1),pct_dd_worse_25=round(float((d<-25).mean()*100)),
                 train_cagr=round(((e_tr/200000)**(1/3)-1)*100,1),train_maxdd=round(dd_tr*100,1),y2026_ret=round((e26/200000-1)*100,1),y2026_maxdd=round(dd26*100,1),
                 avg_lots_2026=round(float(R.lots.mean()),2),median_capital_used_pct=round(float(R.used.median()*100),1),months_2026=R.groupby(R.date.str[:7]).pnl.sum().round().astype(int).to_dict())
    print(nm.ljust(40),{k:v for k,v in out[nm].items() if k!='months_2026'},flush=True)
json.dump(out,open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'round25_results.json'),'w'),indent=1,default=str)
print('saved round25_results.json')
