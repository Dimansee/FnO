"""Trend-day catcher: separate rule, one trade per index per day, entries on half-hour checks from T0 to 14:15."""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import round23_harness as H, pandas as pd, numpy as np
from datetime import timedelta, time
from fno import strategy as S, indicators as I, config as C, pricing as PR
SLIP=H.SLIP
def run(symbol, candles, vix, P, capital=200000, start='2023-01-01'):
    meta=H.B.D.instrument(symbol); lot=meta['lot']; step=meta['step']
    df=I.add_indicators(candles); frames=[(d,g) for d,g in df.groupby(df.index.date)]
    vx=vix.sort_index(); out=[]
    for n,(d,day) in enumerate(frames):
        if n==0 or len(day)<60 or str(d)<start or d in C.BUDGET_DAYS: continue
        hist=pd.concat([g for _,g in frames[max(0,n-15):n]]); sigma=S.noise_sigma(hist)
        if not sigma: continue
        bands=S.noise_bands(day,float(hist['close'].iloc[-1]),sigma,P['mult'])
        def vix_at(ts):
            i=vx.index.searchsorted(ts,side='right')-1; return float(vx.iloc[i]) if i>=0 else 14.0
        v0=vix_at(day.index[0])
        if v0<C.VIX_MIN or v0>P.get('vmax',99): continue
        exp=I.pick_trading_expiry(I.demo_expiries(meta['expiry'],d,3,meta.get('exchange','NSE')),meta['expiry'],d)
        iv_of=lambda ts,s,k,opt: PR.iv(symbol,vix_at(ts),s,k,opt,(exp-d).days,step,meta['iv_mult'])
        idx=list(day.index); o=float(day['open'].iloc[0])
        above=(day['close']>day['vwap']).astype(int).cumsum(); cnt=np.arange(1,len(day)+1)
        ent=None
        for i,ts in enumerate(idx):
            end=ts+timedelta(minutes=5)
            if end.time()<P['t0'] or end.time()>time(14,15): continue
            if not S._is_check(ts): continue
            b=day.loc[ts]; up,lo=bands.loc[ts,'up'],bands.loc[ts,'lo']
            if pd.isna(up): continue
            share_up=above.iloc[i]/cnt[i]; share_dn=1-share_up
            if b['close']>up and b['close']>b['vwap'] and share_up>=P['share']: ent=(i,'CE'); break
            if b['close']<lo and b['close']<b['vwap'] and share_dn>=P['share']: ent=(i,'PE'); break
        if not ent: continue
        i,opt=ent; ts_in=idx[i]; b=day.loc[ts_in]; sign=1 if opt=='CE' else -1; spot=float(b['close']); a=float(b['atr'])
        if P['stop']=='vwap': risk=max(abs(spot-float(b['vwap']))+0.5*a, 1.0*a); risk=min(risk,3*a)
        else: risk=P['stop']*a
        sl=spot-sign*risk; t_in=ts_in+timedelta(minutes=5)
        k=round(spot/step)*step - sign*C.STRIKE_ITM*step
        def prem(s,when,ts):
            t=I.years_to_expiry(exp,when.to_pydatetime().replace(tzinfo=None)); return I.bs_price(s,k,max(t,1/(365*24*12)),iv_of(ts,s,k,opt),opt)
        p_in=prem(spot,t_in,ts_in)*(1+SLIP); ru=max(p_in-prem(sl,t_in,ts_in),p_in*0.05)
        if P['sizing']=='one_lot':
            if p_in*lot>capital: continue
            lots=1
        else:
            budget=capital*C.RISK_PER_TRADE; lots=int(budget//(ru*lot))
            if lots==0 and ru*lot<=1.5*budget: lots=1
            if lots==0: continue
            lots=min(lots,int(capital//(p_in*lot)))
        live=sl; reason=None
        for ts in idx[i+1:]:
            c=day.loc[ts]; end=ts+timedelta(minutes=5)
            adv,fav=(c['low'],c['high']) if sign>0 else (c['high'],c['low'])
            if sign*(adv-live)<=0: reason='stop'; xs=c['open'] if sign*(c['open']-live)<0 else live
            elif P.get('be') and sign*(fav-(spot+sign*P['be']*risk))>=0 and sign*(live-spot)<0: live=spot
            if reason is None and end.time()>=C.SQUARE_OFF: reason='squareoff'; xs=float(c['close'])
            if reason: x_ts,x_bar=end,ts; break
        if reason is None: x_bar=idx[-1]; xs=float(day['close'].iloc[-1]); x_ts=idx[-1]+timedelta(minutes=5); reason='squareoff'
        p_out=PR.real_adjust(p_in/(1+SLIP),prem(xs,x_ts,x_bar),(exp-d).days,(x_ts-t_in).total_seconds()/3600)*(1-SLIP)
        q=lots*lot; pnl=(p_out-p_in)*q-I.charges(p_in,p_out,q,exch_pct=I.exch_rate(symbol))
        out.append(dict(date=str(d),entry_time=t_in.strftime('%H:%M'),exit_time=x_ts.strftime('%H:%M'),side=opt,R=sign*(xs-spot)/risk,lots=lots,pnl=pnl,reason=reason,strategy='trend',sym=symbol))
    return out
