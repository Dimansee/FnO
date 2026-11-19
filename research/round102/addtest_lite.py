import sys; sys.path.insert(0,"/home/claude/dimansee/fno/research/round101"); sys.path.insert(0,"/home/claude/r102")
import lib as L, zones as Z, numpy as np, pandas as pd
def zone_raw(key):
    sym, tf, z, mode, cnt, col, stop, ex, pdy = key.split("|"); stepmin = 5 if tf=="5m" else 15
    df,o,h,l,c,hm,day=L.bar_of(sym,tf); E=Z.events(df,o,h,l,c,day,sym,float(z[1:]))
    sig,st,tg=Z.signals(E,o,h,l,c,mode,cnt[:-1],int(cnt[-1]),col=="col",stop,ex,c*0.0002)
    rows=Z.sim(o,h,l,c,hm,day,sig,st,tg,trail_R=1.0 if ex=="trail" else 0.0,step=stepmin,per_day=int(pdy[0]))
    return Z.priced(sym,df,rows,stepmin).assign(sym=sym)
