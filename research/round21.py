import os; os.environ.setdefault("FNO_CALIB","1")
exec(open("round19.py").read().split("OUT={}")[0])
import random, math, statistics as stt, json
E.RISK=0.01
CUR=current(BASE)
OUT={}
def summ(tr):
    a=[t for t in tr if t["date"]<SPLIT]; b=[t for t in tr if t["date"]>=SPLIT]; s=stats(tr)
    return dict(n=len(tr), net_k=round(sum(t["pnl"] for t in tr)/1000,1), train_k=round(sum(t["pnl"] for t in a)/1000,1), unseen_k=round(sum(t["pnl"] for t in b)/1000,1), pf=s["pf"], win=s["win"], dd_k=round(s["max_dd"]/1000,1),
                yrs={y:round(sum(t["pnl"] for t in tr if t["date"].year==y)/1000,1) for y in (2023,2024,2025,2026)})
OUT["cur"]=summ(CUR); print("current", OUT["cur"], flush=True)
# ------------------------------------------------ A. what predicts a winning trade?
def feats(t):
    d=DAYMAP[t["sym"]][t["date"]]; i=d.idx[t["in"]-5]; s=t["side"]; c=d.c[i]; a=max(d.atr[i],1e-9)
    f={}
    f["adx"]=d.adx[i]; f["rsi_aligned"]=s*(d.rsi[i]-50); f["ema_stack"]=1.0 if (s*(d.e9[i]-d.e21[i])>0 and s*(d.e21[i]-d.x["e50"][i])>0) else 0.0
    f["supertrend_ok"]=1.0 if d.st[i]==s else 0.0; f["st15_ok"]=1.0 if d.st15[i]==s else 0.0; f["macd_aligned"]=s*d.x["macdh"][i]/a
    f["vwap_dist_atr"]=s*(c-d.vwap[i])/a
    f["day_move_vs_usual"]=(abs(c/d.o[0]-1)/d.sig[i]) if d.sig[i] and not math.isnan(d.sig[i]) and d.sig[i]>0 else float("nan")
    lvl=d.cam["h4"] if s>0 else d.cam["l4"]; f["beyond_cam_atr"]=s*(c-lvl)/a
    f["vix"]=d.vix[i]; f["gap_aligned"]=s*d.gap; f["ctx_aligned"]=s*d.ctx; f["prev_range_pct"]=(d.prev_high-d.prev_low)/d.prev_close*100
    f["cpr_width"]=d.cpr_w; f["bb_width"]=d.x["bbw"][i]; f["entry_hour"]=t["in"]/60; f["dte"]=(d.expiry-d.d).days
    f["beyond_prev_hl"]=1.0 if (c>d.prev_high if s>0 else c<d.prev_low) else 0.0
    f["strong_close"]=((c-d.l[i])/max(d.h[i]-d.l[i],1e-9)) if s>0 else ((d.h[i]-c)/max(d.h[i]-d.l[i],1e-9))
    f["is_noise"]=1.0 if t["strat"]=="noise" else 0.0
    return f
F=[feats(t) for t in CUR]
names=[k for k in F[0]]
med={k:stt.median(x[k] for x in F if not math.isnan(x[k])) for k in names}
eqmode={k:(sum(1 for x in F if not math.isnan(x[k]) and x[k]>med[k])<0.15*len(F)) for k in names}   # binary-ish features: "high" means >= the median
def H(k,v): return (v>=med[k]) if eqmode[k] else (v>med[k])
rows=[]
for k in names:
    per={}
    for y in (2023,2024,2025,2026):
        hi=[t["pnl"] for t,f in zip(CUR,F) if t["date"].year==y and not math.isnan(f[k]) and H(k,f[k])]
        lo=[t["pnl"] for t,f in zip(CUR,F) if t["date"].year==y and not math.isnan(f[k]) and not H(k,f[k])]
        per[y]=(stt.mean(hi)-stt.mean(lo)) if hi and lo else 0
    hi=[t for t,f in zip(CUR,F) if not math.isnan(f[k]) and H(k,f[k])]; lo=[t for t,f in zip(CUR,F) if not math.isnan(f[k]) and not H(k,f[k])]
    wr=lambda x: round(100*sum(t["pnl"]>0 for t in x)/max(len(x),1),1)
    rows.append((k, wr(hi), wr(lo), round(stt.mean(t["pnl"] for t in hi)), round(stt.mean(t["pnl"] for t in lo)), sum(v>0 for v in per.values()), sum(v<0 for v in per.values())))
print("\nA. feature: win% when HIGH / when LOW | avg ₹ high / low | years where HIGH better / worse (of 4)")
for r in sorted(rows,key=lambda r:-(r[5]-r[6])): print(f"{r[0]:18s} {r[1]:5} {r[2]:5} | {r[3]:6} {r[4]:6} | {r[5]} / {r[6]}")
OUT["features"]=rows
# ------------------------------------------------ B. size up the strong ones (walk-forward, chosen only from earlier years)
def walk(sz=2.0, top=1/3, seed=0):
    tot_base=0; tot_wt=0; picks={}; base=[]; wt=[]; rnd=[]
    for y in (2024,2025,2026):
        prior=[(t,f) for t,f in zip(CUR,F) if t["date"].year<y]; test=[(t,f) for t,f in zip(CUR,F) if t["date"].year==y]
        chosen=[]
        for k in names:
            if k in("is_noise",): continue
            ds=[]
            for yy in sorted({t["date"].year for t,_ in prior}):
                hi=[t["pnl"] for t,f in prior if t["date"].year==yy and not math.isnan(f[k]) and H(k,f[k])]; lo=[t["pnl"] for t,f in prior if t["date"].year==yy and not math.isnan(f[k]) and not H(k,f[k])]
                ds.append(stt.mean(hi)-stt.mean(lo) if hi and lo else 0)
            if all(d>0 for d in ds): chosen.append((k,1))
            elif all(d<0 for d in ds): chosen.append((k,-1))
        picks[y]=[f"{k}{'+' if sgn>0 else '-'}" for k,sgn in chosen]
        def score(f): return sum(1 for k,sgn in chosen if not math.isnan(f[k]) and (H(k,f[k]) if sgn>0 else (not H(k,f[k]))))
        sc=[score(f) for _,f in test]
        if not chosen: 
            wt+= [t["pnl"] for t,_ in test]; base+=[t["pnl"] for t,_ in test]; rnd+=[[t["pnl"] for t,_ in test]]; continue
        cut=sorted(sc,reverse=True)[max(int(len(sc)*top)-1,0)]
        base+=[t["pnl"] for t,_ in test]
        wt+=[t["pnl"]*(sz if s_>=cut else 1) for (t,_),s_ in zip(test,sc)]
        # control: same number of trades sized up, chosen at random
        n_up=sum(1 for s_ in sc if s_>=cut); rr=[]
        for sd in range(200):
            rnd_=random.Random(sd); idx=set(rnd_.sample(range(len(test)),n_up)); rr.append(sum(t["pnl"]*(sz if j in idx else 1) for j,(t,_) in enumerate(test)))
        rnd.append(rr)
    return picks, sum(base), sum(wt), base, wt, rnd
picks,b,w,base,wt,rnd=walk()
ctrl=stt.mean(sum(r[sd] for r in rnd if isinstance(r,list) and len(r)==200) for sd in range(200)) if all(len(r)==200 for r in rnd if isinstance(r,list)) else None
print("\nB. walk-forward 2024-26: base ₹%.1fk | 2x on the strongest third ₹%.1fk | random-third control ~₹%s" % (b/1000, w/1000, "n/a" if ctrl is None else round(ctrl/1000,1)))
print("   features used per year:", picks)
OUT["sizeup"]=dict(base_k=round(b/1000,1), strong2x_k=round(w/1000,1), control_k=None if ctrl is None else round(ctrl/1000,1), picks=picks)
# ------------------------------------------------ C. multiple targets (needs >=3 lots, so a bigger account)
def run_leg(strat, **chg):
    par=dict(W if strat=="noise" else CA, **chg)
    tr=[]
    for s in SYMS:
        tr+= [dict(t, strat=strat, sym=s) for t in E.run(D[s], s, strat, par)]
    return tr
def combine(legs):
    # legs: list of (weight, trades). trades keyed by (sym,date,strat): independent runs enter the same signals
    key=lambda t:(t["sym"],t["date"],t["strat"])
    bykey=defaultdict(list)
    for w,tr in legs:
        for t in tr: bykey[key(t)].append((w,t))
    out=[]
    for k,v in bykey.items():
        if len(v)==len(legs):
            t0=dict(v[0][1]); t0["pnl"]=sum(w*t["pnl"] for w,t in v); out.append(t0)
    return out
def book_of(tr):
    tr=[t for t in tr if t["date"].weekday()<5 and t["date"] not in BUD and not (t["strat"]=="noise" and vix0(t)>=18) and not (t["strat"]=="camarilla" and vix0(t)>20)]
    tr.sort(key=lambda t:(t["date"],t["in"])); out=[]; busy={}
    for t in tr:
        if busy.get(t["date"],0)>t["in"]: continue
        out.append(t); busy[t["date"]]=t["out"]
    return out
variants={}
base_n=run_leg("noise"); base_c=run_leg("camarilla")
variants["current exit (all in one)"]=[(1,base_n),(1,base_c)]
def mk(chg_n, chg_c): return run_leg("noise",**chg_n), run_leg("camarilla",**chg_c)
t15=mk(dict(rr=1.5),dict(rr=1.5)); t2=mk(dict(rr=2.0),dict(rr=2.0)); t3=mk(dict(rr=3.0,be=1.5),dict(rr=3.0,be=1.5)); t2be=mk(dict(rr=2.0,be=1.0),dict(rr=2.0,be=1.0))
variants["single target 2R"]=[(1,t2[0]),(1,t2[1])]
def split(parts):
    # parts: list of (weight, (noise_trades, cam_trades))
    return parts
def comb(parts):
    n=combine([(w,p[0]) for w,p in parts]); c=combine([(w,p[1]) for w,p in parts]); return book_of(n+c)
cur_pair=(base_n,base_c)
OUT["multi"]={}
for name,parts in {
    "current exit (all in one)":[(1,cur_pair)],
    "single target 2R (all out)":[(1,t2)],
    "1/2 at 2R + 1/2 current exit":[(.5,t2),(.5,cur_pair)],
    "1/2 at 1.5R + 1/2 current exit":[(.5,t15),(.5,cur_pair)],
    "1/3 at 1.5R, 1/3 at 3R (stop to entry after 1.5R), 1/3 current exit":[(1/3,t15),(1/3,t3),(1/3,cur_pair)],
    "1/2 at 2R, 1/2 current exit with stop to entry after 2R":[(.5,t2),(.5,(run_leg("noise",be=2.0),run_leg("camarilla",be=2.0)))],
}.items():
    bk=comb(parts); OUT["multi"][name]=summ(bk); print("C.",name, OUT["multi"][name], flush=True)
# ------------------------------------------------ D. re-entry: second trade per rule per index per day
W2=dict(W,max_trades=2); CA2=dict(CA,max_trades=2)
tr=[]
for s in SYMS: tr+=both(s,w=W2,ca=CA2)
OUT["reentry2"]=summ(current(tr)); print("D. 2 trades per rule per day:", OUT["reentry2"])
W3=dict(W,max_trades=2,max_losses=1); CA3=dict(CA,max_trades=2,max_losses=1)
tr=[]
for s in SYMS: tr+=both(s,w=W3,ca=CA3)
OUT["reentry2_afterwin"]=summ(current(tr)); print("D. second trade only if the first did not lose:", OUT["reentry2_afterwin"])
json.dump(OUT,open("round21.json","w"),default=str,indent=1)
