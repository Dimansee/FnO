"""Round 28 (Oct 2026): rolling 12-month risk of the old vs the round-27 rule set at Rs 2 lakh and Rs 20,000 (1% risk, compounding,
app sizing / cash / weekly-cap / catcher-gate rules, 34 windows Jan 2023 - Oct 2025). Run: python round28.py -> round28_results.json"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import round25_harness as H, round25_trend as TB
from datetime import time
VIX = H.load("INDIAVIX")["close"]; BIG = 1e9
B = dict(cam_tstop=45, noise_trail="band_vwap", overlap=False, reentry=0, sizing="one_lot")
TP = dict(t0=time(10, 45), share=0.8, stop="vwap", mult=1.75, be=1.0, sizing="one_lot")
def main(s, cam=True): return [{**r, "sym": s, "slot": "main"} for r in H.run(s, H.load(s), VIX, {**B, **({} if cam else {"cam_vix_max": -1})}, capital=BIG)]
def catch(s): return [{**r, "sym": s, "slot": "trend"} for r in TB.run(s, H.load(s), VIX, TP, capital=BIG)]
SETS = {"old set": lambda: main("NIFTY") + main("BANKNIFTY") + catch("NIFTY") + catch("BANKNIFTY") + catch("SENSEX"),
        "round 27 set": lambda: main("NIFTY") + main("BANKNIFTY") + main("SENSEX", cam=False) + catch("NIFTY") + catch("SENSEX")}
BROK = 47.2
res = {}
for nm, mk in SETS.items():
    T = pd.DataFrame(mk())
    T["t_in"] = pd.to_datetime(T.date + " " + T.entry_time); T["t_out"] = pd.to_datetime(T.date + " " + T.exit_time)
    T = T.sort_values("t_in").reset_index(drop=True)
    
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
    
    r = {}
    for cap in (200000, 20000):
        rets, dds, stuck = [], [], 0
        for st in pd.date_range("2023-01-01", "2025-10-01", freq="MS"):
            e = (st + pd.DateOffset(years=1)).strftime("%Y-%m-%d")
            eq, dd, _ = sim(0.01, start=cap, startdate=st.strftime("%Y-%m-%d"), enddate=e)
            rets.append(eq / cap - 1); dds.append(dd); stuck += eq < 12000
        x = np.array(rets) * 100; d = np.array(dds) * 100
        r[f"Rs {cap:,}"] = dict(median_12m=round(float(np.median(x)), 1), worst_12m=round(float(x.min()), 1), best_12m=round(float(x.max()), 1),
                                windows_losing_pct=round(float((x < 0).mean() * 100)), median_fall=round(float(np.median(d)), 1),
                                windows_fall_over_25_pct=round(float((d < -25).mean() * 100)),
                                windows_ending_below_12k_pct=round(stuck / len(x) * 100) if cap == 20000 else None)
    res[nm] = r; print(nm, r, flush=True)
json.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "round28_results.json"), "w"), indent=1)
