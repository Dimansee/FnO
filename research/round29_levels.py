"""Pivot levels of every type, from the previous session (daily) and previous week (weekly), for Nifty / Bank Nifty / Sensex."""
import sys; import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import round26_harness as H, pandas as pd, numpy as np
def daily(sym):
    c=H.load(sym); g=c.groupby(c.index.date)
    d=pd.DataFrame({'o':g.open.first(),'h':g.high.max(),'l':g.low.min(),'c':g.close.last()}); d.index=pd.to_datetime(d.index); return d
def levels(prev):
    h,l,c,o=prev.h,prev.l,prev.c,prev.o; r=h-l; P=(h+l+c)/3
    L={}
    L['classic']=dict(P=P,R1=2*P-l,S1=2*P-h,R2=P+r,S2=P-r,R3=h+2*(P-l),S3=l-2*(h-P))
    L['fibonacci']=dict(P=P,R1=P+.382*r,S1=P-.382*r,R2=P+.618*r,S2=P-.618*r,R3=P+r,S3=P-r)
    Pw=(h+l+2*c)/4; L['woodie']=dict(P=Pw,R1=2*Pw-l,S1=2*Pw-h,R2=Pw+r,S2=Pw-r)
    L['camarilla']=dict(P=P,R3=c+r*1.1/4,S3=c-r*1.1/4,R4=c+r*1.1/2,S4=c-r*1.1/2)
    X=h+2*l+c if c<o else (2*h+l+c if c>o else h+l+2*c)
    L['demark']=dict(P=X/4,R1=X/2-l,S1=X/2-h)
    BC=(h+l)/2; TC=2*P-BC
    L['cpr']=dict(P=P,TC=max(TC,BC),BC=min(TC,BC),width_pct=abs(TC-BC)/c*100)
    L['prevday']=dict(PDH=h,PDL=l,PDC=c)
    return L
