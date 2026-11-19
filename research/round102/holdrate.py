import sys, json; sys.path.insert(0,"/home/claude/dimansee/fno/research/round101"); sys.path.insert(0,"/home/claude/r102")
import lib as L, zones as Z, numpy as np, pandas as pd
out={}
for sym in L.SYMS:
    df,o,h,l,c,hm,day=L.bar_of(sym,"5m")
    for zf in (1.0,0.5):
        E=Z.events(df,o,h,l,c,day,sym,zf)
        rows=[]
        for side,rk,bk,rd,rw,bd,bw in (("res","rej_r","brk_u","rd","rw","bud","buw"),("sup","rej_s","brk_d","sd","sw","bdd","bdw")):
            m=np.isfinite(E[rk]); rows+= [("rej",d,w) for d,w in zip(E[rd][m],E[rw][m])]
            m=np.isfinite(E[bk]); rows+= [("brk",d,w) for d,w in zip(E[bd][m],E[bw][m])]
        D=pd.DataFrame(rows,columns=["out","d","w"])
        D["dbin"]=D.d.clip(upper=5).astype(int); D["wbin"]=pd.cut(D.w,[-1,0,2,5,10,1000],labels=["0","1-2","3-5","6-10","11+"])
        byd=D.groupby("dbin").out.apply(lambda x:(x=="rej").mean()).round(3).to_dict()
        nd=D.groupby("dbin").size().to_dict()
        byw=D.groupby("wbin",observed=True).out.apply(lambda x:(x=="rej").mean()).round(3).to_dict()
        nw=D.groupby("wbin",observed=True).size().to_dict()
        # rejections per level per day
        out[f"{sym}|z{zf}"]={"hold_by_prior_today":byd,"n_today":nd,"hold_by_prior_week":byw,"n_week":nw,"tests":len(D),"tests_per_day":round(len(D)/len(np.unique(day)),1)}
        print(sym,zf,out[f"{sym}|z{zf}"])
json.dump(out,open("/home/claude/r102/out/holdrate.json","w"),indent=1,default=str)
