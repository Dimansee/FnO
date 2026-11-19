import sys, json; sys.path.insert(0,"/home/claude/dimansee/fno/research/round101"); sys.path.insert(0,"/home/claude/r102")
import lib as L, zones as Z, numpy as np, pandas as pd
out=json.load(open("/home/claude/r102/out/holdrate.json"))
for sym in L.SYMS:
    df,o,h,l,c,hm,day=L.bar_of(sym,"5m"); off=Z.STEP[sym]/2
    for zf in (0.5,):
        res={}
        for tag,sh in (("round",0.0),("half-way (control)",off)):
            E=Z.events(df,o+sh,h+sh,l+sh,c+sh,day,sym,zf)
            nr=sum(np.isfinite(E[k]).sum() for k in ("rej_r","rej_s")); nb=sum(np.isfinite(E[k]).sum() for k in ("brk_u","brk_d"))
            res[tag]={"hold":round(nr/(nr+nb),3),"tests":int(nr+nb)}
        out[f"{sym}|z{zf}"]["control"]=res; print(sym,res)
json.dump(out,open("/home/claude/r102/out/holdrate.json","w"),indent=1,default=str)
