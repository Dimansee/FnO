import sys; sys.path.insert(0,"/home/claude/dimansee/fno/research/round101"); sys.path.insert(0,"/home/claude/r102")
import lib as L, zones as Z, numpy as np, pandas as pd
from fno import strategy as S
a=L.M.R34.m1("NIFTY")
c5=a.groupby(a.index.normalize(),group_keys=False).apply(lambda x:x.resample("5min",origin=x.index[0].normalize()+pd.Timedelta(hours=9,minutes=15)).agg({"open":"first","high":"max","low":"min","close":"last"}).dropna())
b=S.channel_frame(c5)
df,o,h,l,c,hm,day=L.bar_of("NIFTY","15m")
print("bars live",len(b),"research",len(df))
j=b.index.intersection(df.index); print("common",len(j), "max ohlc diff", float((b.loc[j,["open","high","low","close"]].values-df.loc[j,["open","high","low","close"]].values).__abs__().max()))
ev=S.rlevel_events(b,"NIFTY"); sg=S.rlevel_signals(b,ev)
E=Z.events(df,o,h,l,c,day,"NIFTY",0.5)
sig,st,tg=Z.signals(E,o,h,l,c,"brk","week",1,False,"candle","trail",c*0.0002)
R=pd.DataFrame({"sig":sig,"stop":st},index=df.index).loc[j]; Lv=sg.loc[j]
print("signals live",int((Lv.sig!=0).sum()),"research",int((R.sig!=0).sum()),"same side",int(((Lv.sig==R.sig)&(R.sig!=0)).sum()),
      "stop max diff", float(np.nanmax(np.abs(Lv.stop.values-R.stop.values))))
mis=j[(Lv.sig.values!=R.sig.values)]; print("mismatch bars",len(mis), list(mis[:5]))
