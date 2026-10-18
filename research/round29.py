"""Round 29 (Oct 2026): pivot points. (1) Do pivot levels of each type (Classic, Fibonacci, Woodie, Camarilla, DeMark, CPR,
previous-day H/L/C) get more reactions than random levels? (2) Breakout and fade rules on every type, priced on options with costs.
(3) Pivots as filters on the live rules (CPR width, daily / weekly / monthly pivot side). (4) Does a narrow CPR predict a trend day?
(5) The Fibonacci 0.618 breakout added to / instead of Camarilla in the live set, and its 12-month risk. Train 2023-25, judged on 2026.
Run: python round29.py -> round29_results.json (about 40 minutes)"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RES = {}; STRAT_TRADES = {}

# ---- (1) reactions vs random levels
import sys; 
from round29_levels import *
rng=np.random.default_rng(29)
def react(sym, X_PCT=0.30):
    c=H.load(sym); D=daily(sym); dates=list(D.index)
    rows=[]
    offs=[]  # level offsets (pct of prev close) for the placebo
    days={d:g for d,g in c.groupby(c.index.date)}
    for i in range(1,len(dates)):
        prev=D.iloc[i-1]; d=dates[i].date(); g=days.get(d)
        if g is None or len(g)<60: continue
        L=levels(prev); o=float(g.open.iloc[0]); X=o*X_PCT/100
        hi=g.high.values; lo=g.low.values
        def test(level):
            if level>o: side='res'
            elif level<o: side='sup'
            else: return None
            for k in range(len(g)):
                if lo[k]<=level<=hi[k]:
                    # from touch onward: which comes first, +X away (reaction) or X beyond (break)
                    for j in range(k,len(g)):
                        up=hi[j]-level; dn=level-lo[j]
                        rev = dn>=X if side=='res' else up>=X
                        brk = up>=X if side=='res' else dn>=X
                        if rev and brk: return ('tie',k)
                        if rev: return ('react',k)
                        if brk: return ('break',k)
                    return ('none',k)
            return ('untouched',None)
        for typ,lv in L.items():
            for nm,val in lv.items():
                if nm=='width_pct': continue
                r=test(float(val))
                if r is None: continue
                rows.append(dict(sym=sym,date=str(d),type=typ,level=nm,res=r[0],y=str(d)[:4]))
                offs.append((float(val)/prev.c-1))
        # placebo: 6 random levels per day drawn from the pooled offsets of earlier days
        if len(offs)>200:
            for _ in range(6):
                off=offs[rng.integers(0,len(offs))]; r=test(float(prev.c*(1+off)))
                if r: rows.append(dict(sym=sym,date=str(d),type='RANDOM',level='rand',res=r[0],y=str(d)[:4]))
    return pd.DataFrame(rows)
df=pd.concat([react(s) for s in ['NIFTY','BANKNIFTY','SENSEX']])

t=df[df.res.isin(['react','break'])]
tab=t.groupby(['type']).res.apply(lambda x:(x=='react').mean()*100).round(1).to_frame('react_%')
tab['touches']=t.groupby('type').size()
tab['touched_%']=(df[df.res!='untouched'].groupby('type').size()/df.groupby('type').size()*100).round(1)
print(tab.sort_values('react_%'))
lv=t.groupby(['type','level']).res.apply(lambda x:(x=='react').mean()*100).round(1).unstack(0)
print(lv)
print(t.groupby(['type','y']).res.apply(lambda x:(x=='react').mean()*100).round(1).unstack())

RES['reactions']={'by_type':tab.reset_index().to_dict('records')}

# ---- (1b) distance-matched (mirror) test for the central pivot / CPR
import sys; 

def outcome(g,level,o,X):
    side='res' if level>o else 'sup'
    hi=g.high.values; lo=g.low.values
    for k in range(len(g)):
        if lo[k]<=level<=hi[k]:
            for j in range(k,len(g)):
                up=hi[j]-level; dn=level-lo[j]
                rev= dn>=X if side=='res' else up>=X; brk= up>=X if side=='res' else dn>=X
                if rev and brk: return None
                if rev: return 1
                if brk: return 0
            return None
    return None
res={}
for X_PCT in (0.2,0.3,0.5):
    real,mirror=[],[]
    for sym in ['NIFTY','BANKNIFTY','SENSEX']:
        c=H.load(sym); D=daily(sym); days={d:g for d,g in c.groupby(c.index.date)}
        for i in range(1,len(D)):
            g=days.get(D.index[i].date())
            if g is None or len(g)<60: continue
            L=levels(D.iloc[i-1]); o=float(g.open.iloc[0]); X=o*X_PCT/100
            for v in (L['classic']['P'],L['cpr']['TC'],L['cpr']['BC']):
                if abs(v-o)<1e-6: continue
                a=outcome(g,v,o,X); b=outcome(g,2*o-v,o,X)   # mirror: same distance from the open, other side
                if a is not None and b is not None: real.append(a); mirror.append(b)
    r,m=np.mean(real)*100,np.mean(mirror)*100; n=len(real); se=np.sqrt((r*(100-r)+m*(100-m))/n)
    res[X_PCT]=dict(n=n,pivot_react=round(r,1),mirror_react=round(m,1),diff=round(r-m,1),z=round((r-m)/se,1))
    print(X_PCT,res[X_PCT])

RES['central_pivot_vs_mirror']={str(k):v for k,v in res.items()}

# ---- (2) breakout and fade rules

import round29_harness as H
from round29_levels import levels
VIX=H.load('INDIAVIX')['close']; BIG=1e9
def prev(hist):
    last=hist[hist.index.date==hist.index[-1].date()]
    return pd.Series(dict(o=float(last.open.iloc[0]),h=float(last.high.max()),l=float(last.low.min()),c=float(last.close.iloc[-1])))
def brk(typ,up,dn,stop_up,stop_dn):
    def f(hist):
        L=levels(prev(hist))[typ]; return dict(h4=L[up],l4=L[dn],h3=L[stop_up],l3=L[stop_dn])
    return f
def fade(typ,key):
    def f(hist):
        L=levels(prev(hist))[typ]; return dict(F=L[key])
    return f
BASE=dict(cam_tstop=45,noise_trail='band_vwap',overlap=True,reentry=0,sizing='one_lot',noise_vix_max=-1)
S={'Classic R1/S1 breakout (stop P)':dict(strats=('camarilla',),levels_fn=brk('classic','R1','S1','P','P')),
   'Classic R2/S2 breakout (stop R1/S1)':dict(strats=('camarilla',),levels_fn=brk('classic','R2','S2','R1','S1')),
   'Fibonacci 0.618 breakout (stop 0.382)':dict(strats=('camarilla',),levels_fn=brk('fibonacci','R2','S2','R1','S1')),
   'Woodie R1/S1 breakout (stop P)':dict(strats=('camarilla',),levels_fn=brk('woodie','R1','S1','P','P')),
   'DeMark R1/S1 breakout (stop P)':dict(strats=('camarilla',),levels_fn=brk('demark','R1','S1','P','P')),
   'CPR breakout (TC/BC, stop other side)':dict(strats=('camarilla',),levels_fn=brk('cpr','TC','BC','BC','TC')),
   'Camarilla H4/L4 breakout (live rule)':dict(strats=('camarilla',)),
   'Central pivot P fade, target 1R':dict(strats=('fade',),levels_fn=fade('classic','P'),exit='fixed',targets=[(1.0,1)]),
   'Central pivot P fade, target 2R':dict(strats=('fade',),levels_fn=fade('classic','P'),exit='fixed',targets=[(2.0,1)]),
   'CPR top/bottom fade (nearest), target 1R':dict(strats=('fade',),levels_fn=fade('cpr','TC'),exit='fixed',targets=[(1.0,1)]),
   'Camarilla R3/S3 fade, target 1R':dict(strats=('fade',),levels_fn=fade('camarilla','R3'),exit='fixed',targets=[(1.0,1)]),
   'Classic R1 fade, target 1R':dict(strats=('fade',),levels_fn=fade('classic','R1'),exit='fixed',targets=[(1.0,1)]),
   'Classic S1 fade, target 1R':dict(strats=('fade',),levels_fn=fade('classic','S1'),exit='fixed',targets=[(1.0,1)])}
def st(df):
    if df.empty: return {'n':0}
    df=df.sort_values(['date','entry_time']); w=df.u[df.u>0].sum(); l=-df.u[df.u<=0].sum(); eq=df.u.cumsum()
    return dict(n=int(len(df)),net=round(float(df.u.sum())),pf=round(float(w/l),2) if l else None,win=round(float((df.pnl>0).mean()*100)),fall=round(float((eq-eq.cummax().clip(lower=0)).min())))
out={}
for nm,ov in S.items():
    rows=[]
    for s in ['NIFTY','BANKNIFTY','SENSEX']:
        rows+=[{**r,'sym':s} for r in H.run(s,H.load(s),VIX,{**BASE,**ov},capital=BIG)]
    df=pd.DataFrame(rows)
    if df.empty: print(nm,'no trades'); continue
    df['u']=df.pnl/df.risk_lot*2000; df['y']=df.date.str[:4]
    out[nm]=dict(train=st(df[df.y<'2026']),y2026=st(df[df.y=='2026']),years=df.groupby('y').u.sum().round().astype(int).to_dict(),
                 by_index={k:round(float(v)) for k,v in df[df.y<'2026'].groupby('sym').u.sum().items()})
    STRAT_TRADES[nm]=df
    print(nm[:44].ljust(44),'train',out[nm]['train'],'| 2026',out[nm]['y2026'],out[nm]['by_index'],flush=True)
RES['strategies']=out

# ---- (4) narrow CPR and trend days
rows = []
for sym in ['NIFTY', 'BANKNIFTY', 'SENSEX']:
    D = daily(sym)
    w = pd.Series([levels(D.iloc[i-1])['cpr']['width_pct'] for i in range(1, len(D))], index=D.index[1:])
    rk = w.rolling(20).apply(lambda a: (a[:-1] < a[-1]).mean(), raw=True)
    d = D.iloc[1:].copy(); d['rank'] = rk; d['trend'] = (d.c - d.o).abs() / d.o * 100 >= 0.8
    rows.append(d.dropna())
X = pd.concat(rows); X['bucket'] = pd.cut(X['rank'], [-.01, 1/3, 2/3, 1.01], labels=['narrow', 'middle', 'wide'])
RES['cpr_trend_days_pct'] = {str(k): round(float(v) * 100, 1) for k, v in X.groupby('bucket', observed=False).trend.mean().items()}
print('trend-day share by CPR width', RES['cpr_trend_days_pct'])
FIB = STRAT_TRADES['Fibonacci 0.618 breakout (stop 0.382)'].copy()

# ---- (3) pivots as filters on the live (round-27) set

from round29_levels import *
import round25_harness as H25, round25_trend as TB25
from datetime import time
_B = dict(cam_tstop=45, noise_trail="band_vwap", overlap=False, reentry=0, sizing="one_lot")
_T = dict(t0=time(10, 45), share=0.8, stop="vwap", mult=1.75, be=1.0, sizing="one_lot")
_rows = []
for _s, _cam in [("NIFTY", True), ("BANKNIFTY", True), ("SENSEX", False)]:
    _rows += [{**r, "sym": _s, "slot": "main"} for r in H25.run(_s, H25.load(_s), VIX, {**_B, **({} if _cam else {"cam_vix_max": -1})}, capital=BIG)]
for _s in ("NIFTY", "SENSEX"):
    _rows += [{**r, "sym": _s, "slot": "trend"} for r in TB25.run(_s, H25.load(_s), VIX, _T, capital=BIG)]
T = pd.DataFrame(_rows); T["u"] = T.pnl / T.risk_lot * 2000; LIVE = T.copy()
info={}
for sym in ['NIFTY','BANKNIFTY','SENSEX']:
    D=daily(sym)
    for i in range(1,len(D)):
        L=levels(D.iloc[i-1]); info[(sym,str(D.index[i].date()))]=dict(cpr_w=L['cpr']['width_pct'],P=L['classic']['P'])
    # weekly / monthly classic pivot from the previous week / month
    for fr,key in (('W-FRI','Pw'),('ME','Pm')):
        g=D.resample(fr).agg({'o':'first','h':'max','l':'min','c':'last'}).dropna()
        piv=((g.h+g.l+g.c)/3).shift(1)
        for d in D.index:
            k=piv.index.searchsorted(d)   # period containing d
            if k<len(piv) and not np.isnan(piv.iloc[k]): info.setdefault((sym,str(d.date())),{})[key]=float(piv.iloc[k])
T['cpr_w']=[info.get((s,d),{}).get('cpr_w') for s,d in zip(T.sym,T.date)]
for k in ('P','Pw','Pm'): T[k]=[info.get((s,d),{}).get(k) for s,d in zip(T.sym,T.date)]
T['y']=T.date.str[:4]; T['dir']=np.where(T.side=='CE',1,-1)
# CPR width relative to its own last 20 sessions (narrow = bottom third)
cw={}
for sym in ['NIFTY','BANKNIFTY','SENSEX']:
    s=pd.Series({d:v['cpr_w'] for (x,d),v in info.items() if x==sym and 'cpr_w' in v}).sort_index()
    rk=s.rolling(20).apply(lambda a:(a[:-1]<a[-1]).mean() if len(a)>1 else np.nan,raw=True)
    for d,v in rk.items(): cw[(sym,d)]=v
T['cpr_rank']=[cw.get((s,d)) for s,d in zip(T.sym,T.date)]
def st(df):
    eq=df.sort_values(['date','entry_time']).u.cumsum(); w=df.u[df.u>0].sum(); l=-df.u[df.u<=0].sum()
    return dict(n=len(df),net=round(df.u.sum()),pf=round(w/l,2) if l else None,fall=round((eq-eq.cummax().clip(lower=0)).min()))
F={'none (live set)':T.u.notna(),
   'only narrow CPR days (bottom third of 20 days)':T.cpr_rank<=1/3,
   'skip narrow CPR days':~(T.cpr_rank<=1/3),
   'only wide CPR days (top third)':T.cpr_rank>=2/3,
   'skip wide CPR days':~(T.cpr_rank>=2/3),
   'with daily pivot (calls above P, puts below)':np.sign(T.spot_in-T.P)==T.dir,
   'with weekly pivot':np.sign(T.spot_in-T.Pw)==T.dir,
   'with monthly pivot':np.sign(T.spot_in-T.Pm)==T.dir,
   'with weekly and monthly pivot':(np.sign(T.spot_in-T.Pw)==T.dir)&(np.sign(T.spot_in-T.Pm)==T.dir)}
out={}
for nm,m in F.items():
    d=T[m.fillna(False).astype(bool)]
    out[nm]=dict(train=st(d[d.y<'2026']),y2026=st(d[d.y=='2026']),years=d.groupby('y').u.sum().round().astype(int).to_dict())
    print(nm.ljust(46),'train',out[nm]['train'],'| 2026',out[nm]['y2026'],flush=True)
RES['filters_on_live_set']=out

# ---- (5) Fibonacci breakout with the live set, and 12-month risk
FIB["y"] = FIB.date.str[:4]
alld = [d for d in sorted(set(H.load("NIFTY").index.strftime("%Y-%m-%d"))) if d >= "2023-01-03"]
def _daily(df): return df.groupby("date").u.sum().reindex(alld).fillna(0)
def _st(s):
    eq = s.cumsum(); return dict(net=round(float(s.sum())), fall=round(float((eq - eq.cummax().clip(lower=0)).min())), sharpe=round(float(s.mean() / s.std() * np.sqrt(250)), 2))
sel = []
for s_, g_ in FIB[FIB.y < "2026"].groupby("sym"):
    yy = g_.groupby("y").u.sum(); pf_ = g_.u[g_.u > 0].sum() / -g_.u[g_.u <= 0].sum()
    if g_.u.sum() > 0 and (yy > 0).sum() >= 2 and pf_ >= 1.05: sel.append(s_)
combo = {"live set": _daily(LIVE), "live + Fibonacci breakout (picked indices)": _daily(pd.concat([LIVE, FIB[FIB.sym.isin(sel)]]))}
RES["fib_picked_indices"] = sel
RES["fib_combination"] = {k: dict(train=_st(v[v.index < "2026"]), y2026=_st(v[v.index >= "2026"])) for k, v in combo.items()}
print(RES["fib_combination"])
BROK = 47.2
roll = {}
for nm_, T in [("live set", LIVE), ("live + Fibonacci breakout", pd.concat([LIVE, FIB[FIB.sym.isin(sel)].assign(slot="fib")]))]:
    T = T.copy(); T["t_in"] = pd.to_datetime(T.date + " " + T.entry_time); T["t_out"] = pd.to_datetime(T.date + " " + T.exit_time); T = T.sort_values("t_in").reset_index(drop=True)
    
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
    
    r_ = {}
    for cap in (200000, 20000):
        rets, dds, stuck = [], [], 0
        for s0 in pd.date_range("2023-01-01", "2025-10-01", freq="MS"):
            e = (s0 + pd.DateOffset(years=1)).strftime("%Y-%m-%d"); eq, dd, _ = sim(0.01, start=cap, startdate=s0.strftime("%Y-%m-%d"), enddate=e)
            rets.append(eq / cap - 1); dds.append(dd); stuck += eq < 12000
        x = np.array(rets) * 100; dd_ = np.array(dds) * 100
        r_[f"Rs {cap:,}"] = dict(median_12m=round(float(np.median(x)), 1), worst_12m=round(float(x.min()), 1), losing_pct=round(float((x < 0).mean() * 100)),
                                 median_fall=round(float(np.median(dd_)), 1), fall_over_25_pct=round(float((dd_ < -25).mean() * 100)),
                                 cant_afford_lot_pct=round(stuck / len(x) * 100) if cap == 20000 else None)
    e26, dd26, R26 = sim(0.01, start=200000, startdate="2026-01-01")
    r_["2026 from Rs 2 lakh"] = dict(ret=round((e26 / 200000 - 1) * 100, 1), fall=round(dd26 * 100, 1))
    roll[nm_] = r_; print(nm_, r_, flush=True)
RES["rolling_12m"] = roll
json.dump(RES, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "round29_results.json"), "w"), indent=1, default=str)
print("saved round29_results.json")
