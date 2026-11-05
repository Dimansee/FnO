"""Round 86 (5 Nov 2026): BTST / overnight carry forward check - the live carry rule (fno/carry.signal) on every recorded day 28 Sep - 5 Nov 2026, real recorded option prices, buy 15:20 +1%, sell next open -1%, Rs 90 fees. Run from the repo root."""
import sys, pandas as pd, os
sys.path.insert(0,'.')
from datetime import date
from fno import carry as CA
L='research/data/live'; days=sorted(d for d in os.listdir(L) if d[:2]=='20' and os.path.exists(f'{L}/{d}/candles_1m.csv.gz'))
cache={}
def load(d):
    if d not in cache: cache[d]=pd.read_csv(f'{L}/{d}/candles_1m.csv.gz',parse_dates=['ts'])
    return cache[d]
LOT={'NIFTY':65,'BANKNIFTY':30,'SENSEX':20}; STEP={'NIFTY':50,'BANKNIFTY':100,'SENSEX':100}
rows=[]
for i,d in enumerate(days[:-1]):
    nd=days[i+1]; c=load(d); c2=load(nd)
    for sym in LOT:
        x=c[(c.sym==sym)&(c.kind=='IDX')].set_index('ts').sort_index()
        if x.empty: continue
        b5=x[['open','high','low','close']].resample('5min').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
        sg=CA.signal(b5, date.fromisoformat(d))
        if not sg['side']: rows.append(dict(day=d,sym=sym,side=0,move=sg.get('move_pct'),pos=sg.get('close_in_range'))); continue
        opt='CE' if sg['side']>0 else 'PE'
        spot=x.loc[:f'{d} 15:20'].close.iloc[-1]; k=round(spot/STEP[sym])*STEP[sym]
        o=c[(c.sym==sym)&(c.kind==opt)&(c.strike==k)]
        exps=sorted(e for e in o.expiry.unique() if e>d)
        if not exps: rows.append(dict(day=d,sym=sym,side=sg['side'],note='no strike data')); continue
        e=exps[0]; s1=o[o.expiry==e].set_index('ts').sort_index()
        buy=s1.loc[f'{d} 15:20':f'{d} 15:21'].close
        s2=c2[(c2.sym==sym)&(c2.kind==opt)&(c2.strike==k)&(c2.expiry==e)].set_index('ts').sort_index()
        if buy.empty or s2.empty: rows.append(dict(day=d,sym=sym,side=sg['side'],note='missing price')); continue
        bp=buy.iloc[-1]*1.01; sp=s2.open.iloc[0]*0.99
        nx=c2[(c2.sym==sym)&(c2.kind=='IDX')].sort_values('ts')
        gap=(nx.open.iloc[0]/x.close.iloc[-1]-1)*100
        pnl=(sp-bp)*LOT[sym]-90
        rows.append(dict(day=d,sym=sym,side=sg['side'],move=sg['move_pct'],pos=sg['close_in_range'],opt=f'{k:g}{opt} {e}',buy=round(bp,1),sell_next_open=round(sp,1),next_gap=round(gap,2),pnl=round(pnl)))
df=pd.DataFrame(rows); pd.set_option('display.width',250)
t=df[df.side!=0]; print(t.to_string(index=False))
print('\nsignals',len(t),'total pnl', t.pnl.sum(), 'by sym', t.groupby('sym').pnl.sum().to_dict(), 'wins', (t.pnl>0).sum())
print('days checked',len(days)-1)
