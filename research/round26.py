"""Round 26 (Oct 2026): with large capital / many lots, do fixed stops with fixed targets (one target, T1/T2/T3 partial exits, a
market-dependent target) or small targets with more trades beat the current exits? Same entries as the live rules (Noise + Camarilla
on Nifty / Bank Nifty, trend-day catcher on all three indices), 10 lots on every trade, brokerage charged per order (so it shrinks
per lot), 0.5% slippage per fill. Train 2023-25, judged on 2026. Run: python round26.py [lots] -> round26_results.json"""
import sys, json; import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import round26_harness as H, round26_trend as TB, pandas as pd
from datetime import time
VIX=H.load('INDIAVIX')['close']
LOTS=int(sys.argv[1]) if len(sys.argv)>1 else 10
BASE=dict(cam_tstop=45,noise_trail='band_vwap',overlap=False,reentry=0,sizing='risk',fixed_lots=LOTS)
TREND=dict(t0=time(10,45),share=0.8,stop='vwap',mult=1.75,be=1.0,sizing='risk',fixed_lots=LOTS)
F=lambda **k: dict(exit='fixed',**k)
V={'X0 current exits (trail / BE / time stop / 15:15)':{},
   'X1 fixed SL + T 0.5R':F(targets=[(0.5,1)]),
   'X2 fixed SL + T 1R':F(targets=[(1.0,1)]),
   'X3 fixed SL + T 1.5R':F(targets=[(1.5,1)]),
   'X4 fixed SL + T 2R':F(targets=[(2.0,1)]),
   'X5 T1/T2/T3 = 0.5/1/1.5R (thirds)':F(targets=[(0.5,1),(1.0,1),(1.5,1)]),
   'X6 T1/T2/T3 = 1/2/3R (thirds)':F(targets=[(1.0,1),(2.0,1),(3.0,1)]),
   'X7 T1/T2/T3 = 1/2/3R + SL to entry after T1':F(targets=[(1.0,1),(2.0,1),(3.0,1)],be_t1=True),
   'X8 one target by market: 2R if trending (VWAP 80%) else 0.75R':F(targets='situational'),
   'X9 T 0.5R + up to 3 more trades after a target':F(targets=[(0.5,1)],reentry=3,reentry_after={'target'}),
   'X10 T 1R + up to 3 more trades after a target':F(targets=[(1.0,1)],reentry=3,reentry_after={'target'})}
def st(df):
    if df.empty: return {'n':0}
    df=df.sort_values(['date','entry_time']); w=df.pnl[df.pnl>0].sum(); l=-df.pnl[df.pnl<=0].sum(); eq=df.pnl.cumsum()
    return dict(n=int(len(df)),net=round(float(df.pnl.sum())),pf=round(float(w/l),2) if l else None,win=round(float((df.pnl>0).mean()*100)),
                avg=round(float(df.pnl.mean())),fall=round(float((eq-eq.cummax().clip(lower=0)).min())))
out={}
for nm,ov in V.items():
    rows=[]
    for s in ['NIFTY','BANKNIFTY']: rows+=H.run(s,H.load(s),VIX,{**BASE,**ov},capital=1e9)
    for s in ['NIFTY','BANKNIFTY','SENSEX']: rows+=[{**r,'sym':s} for r in TB.run(s,H.load(s),VIX,{**TREND,**{k:v for k,v in ov.items() if k not in ('reentry','reentry_after')}},capital=1e9)]
    df=pd.DataFrame(rows); df['y']=df.date.str[:4]; df['m']=df.date.str[:7]
    out[nm]=dict(train=st(df[df.y<'2026']),y2026=st(df[df.y=='2026']),years=df.groupby('y').pnl.sum().round().astype(int).to_dict(),
                 months_2026=df[df.y=='2026'].groupby('m').pnl.sum().round().astype(int).to_dict(),
                 trades_per_day=round(len(df)/df.date.nunique(),2))
    w_=df[df.pnl>0]; l_=df[df.pnl<=0]; out[nm].update(avg_win=round(float(w_.pnl.mean())),avg_loss=round(float(l_.pnl.mean())),avg_R_win=round(float(w_.R.mean()),2),avg_R_loss=round(float(l_.R.mean()),2))
    print(nm[:58].ljust(58),'train',out[nm]['train'],'| 2026',out[nm]['y2026'],flush=True)
json.dump(out,open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'round26_results.json'),'w'),default=str,indent=1)
