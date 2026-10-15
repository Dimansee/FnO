import os; os.environ.setdefault("FNO_CALIB","1")
src=open("round21.py").read()
exec(src.split("# ------------------------------------------------ C. multiple targets")[0])
# strength-filter (walk-forward): drop the weakest third each year, chosen from earlier years only
def strength_filter(trs, F_, drop=1/3):
    keep=[]
    for y in sorted({t["date"].year for t in trs}):
        prior=[(t,f) for t,f in zip(trs,F_) if t["date"].year<y]; test=[(t,f) for t,f in zip(trs,F_) if t["date"].year==y]
        chosen=[]
        if y>2023:
            for k in names:
                if k=="is_noise": continue
                ds=[]
                for yy in sorted({t["date"].year for t,_ in prior}):
                    hi=[t["pnl"] for t,f in prior if t["date"].year==yy and not math.isnan(f[k]) and H(k,f[k])]; lo=[t["pnl"] for t,f in prior if t["date"].year==yy and not math.isnan(f[k]) and not H(k,f[k])]
                    ds.append(stt.mean(hi)-stt.mean(lo) if hi and lo else 0)
                if all(d>0 for d in ds): chosen.append((k,1))
                elif all(d<0 for d in ds): chosen.append((k,-1))
        if not chosen: keep+=[t for t,_ in test]; continue
        sc=lambda f: sum(1 for k,sg in chosen if not math.isnan(f[k]) and (H(k,f[k]) if sg>0 else not H(k,f[k])))
        scores=sorted(sc(f) for _,f in test); cut=scores[int(len(scores)*drop)]
        keep+=[t for t,f in test if sc(f)>=cut]
    return keep
def run_all(wn,wc):
    tr=[]
    for s in SYMS: tr+=both(s,w=wn,ca=wc)
    return current(tr)
res={}
def rep(name,tr): res[name]=summ(tr); print(name,res[name],flush=True)
rep("A current", CUR)
# 1 strength filter only (no size change)
rep("B +strength filter (skip weakest third)", strength_filter(CUR,F))
# 3 exit-at-target + re-entry (target 2R, stop-to-entry after 1R, then allow a 2nd trade only if first did not lose)
T=run_all(dict(W,rr=2.0,max_trades=2,max_losses=1), dict(CA,rr=2.0,max_trades=2,max_losses=1)); rep("C target 2R + re-entry after non-loss", T)
T1=run_all(dict(W,max_trades=2,max_losses=1), dict(CA,max_trades=2,max_losses=1)); rep("D current exits + re-entry after non-loss", T1)
# all combined
Fc=[feats(t) for t in T]
rep("E ALL: 2R exit + re-entry + strength filter", strength_filter(T,Fc))
Fd=[feats(t) for t in T1]
rep("F current exits + re-entry + strength filter", strength_filter(T1,Fd))
json.dump(res,open("round22.json","w"),default=str,indent=1)
