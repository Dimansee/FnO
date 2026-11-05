import pandas as pd, numpy as np
c=pd.read_csv('/home/claude/data/live/2026-11-05/candles_1m.csv.gz',parse_dates=['ts'])
N=pd.read_pickle('research/out_round85_n.pkl')
o=c[(c.sym=='NIFTY')&(c.kind=='CE')&(c.expiry=='2026-11-09')]
def px(k,t,col='close'):
    s=o[o.strike==k].set_index('ts').sort_index()
    s=s[s.index<=t]; return float(s[col].iloc[-1])
B=N[['open','high','low','close']].resample('5min').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
B['ema9']=B.close.ewm(span=9,adjust=False).mean()
tr=pd.concat([B.high-B.low,(B.high-B.close.shift()).abs(),(B.low-B.close.shift()).abs()],axis=1).max(axis=1)
B['atr']=tr.rolling(14,min_periods=5).mean()
B['vwap']=N.vwap.resample('5min').last()
END=pd.Timestamp('2026-11-05 15:15')
def run(entry_t, entry, stop0, mode):
    R=entry-stop0; stop=stop0; hi=entry; armed=False
    # walk 1-min bars for stop hits; update trail on 5-min closes
    for t,row in N.loc[entry_t:END].iterrows():
        if row.low<=stop: return t, stop, 'stop'
        hi=max(hi,row.high)
        bend=t+pd.Timedelta(minutes=1)
        if bend.minute%5==0:   # 5-min bar just closed
            bt=bend-pd.Timedelta(minutes=5)
            if bt not in B.index: continue
            b=B.loc[bt]
            if not armed and hi>=entry+R: armed=True; stop=max(stop,entry)          # breakeven at +1R
            if mode=='fixed': stop=stop0
            elif mode=='be_only': pass
            elif mode=='swing2' and armed:
                stop=max(stop, B.loc[:bt].low.iloc[-2:].min()-2)
            elif mode=='ema9' and armed and b.close<b.ema9: return bend, b.close, 'close<EMA9'
            elif mode=='vwap' and b.close<b.vwap: return bend, b.close, 'close<VWAP'
            elif mode=='atr2' and armed: stop=max(stop, hi-2*b.atr)
            elif mode=='step1R':
                k=int((hi-entry)//R); 
                if k>=1: stop=max(stop, entry+(k-1)*R)
            elif mode=='pts50' and armed: stop=max(stop, hi-50)
    return END, float(N.loc[:END].close.iloc[-1]), '15:15'
setups={
 'A 24350 retest 10:44 (5m close 10:45 bar -> buy 10:50)':(pd.Timestamp('2026-11-05 10:50'),24369.0,24340.0),
 'B 24350 retest, 1m confirm (buy 10:46)':(pd.Timestamp('2026-11-05 10:46'),24360.0,24340.0),
 'C 24300 double-bottom (09:35 bar close -> buy 09:40)':(pd.Timestamp('2026-11-05 09:40'),24358.8,24300.0),
}
rows=[]
for name,(t,e,s) in setups.items():
    for m in ['fixed','be_only','step1R','swing2','pts50','atr2','ema9','vwap']:
        xt,xp,why=run(t,e,s,m)
        for k in (24350,24400):
            pe=px(k,t-pd.Timedelta(minutes=1)); pxit=px(k,xt-pd.Timedelta(minutes=1)) if why!='stop' else px(k,xt)
            pnl=(pxit-pe)*65*2-180
            rows.append([name[:1],m,t.strftime('%H:%M'),e,s,xt.strftime('%H:%M'),round(xp,1),why,round(xp-e,1),round((xp-e)/(e-s),2),k,pe,round(pxit,1),round(pnl)])
df=pd.DataFrame(rows,columns=['set','trail','in','entry','stop','out','exit_spot','why','pts','R','strike','prem_in','prem_out','pnl_2lots'])
pd.set_option('display.width',250)
print(df.to_string(index=False))
df.to_csv('research/out_round85_trail_results.csv',index=False)
