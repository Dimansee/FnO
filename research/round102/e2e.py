import sys, time, json; sys.path.insert(0,"/home/claude/dimansee/fno/research/round101")
import lib as L, pandas as pd
O, PB = L.O, L.PB
T0,A0,H0 = L.base_sets()
keys = [PB._trade_key(L.B0), PB._alt_key(L.B0), PB._hi_key(L.B0)]
c = O.candles("NIFTY"); vix = O.H.load("INDIAVIX")["close"]
new = []
for k in keys:
    t=time.time(); ex, otm, brs, mll = k
    T = PB._trades("NIFTY", c, vix, otm, PB.EXITS[ex]["ov"], brs, mll); T["score"] = PB._scores(T, c)
    T["cpr_w"] = PB.cpr_widths(c).reindex(T.date).values
    new.append(T); print(k, len(T), "rlevel", int((T.strategy=="Round-level break").sum()), round(time.time()-t), flush=True)
def swap(B, N): return pd.concat([B[B.sym!="NIFTY"], N.assign(sym="NIFTY") if "sym" not in N else N], ignore_index=True)
T1,A1,H1 = (swap(B,N) for B,N in zip((T0,A0,H0), new))
pd.to_pickle((T1,A1,H1), "/home/claude/r102/out/base_with_rlevel.pkl")
res={}
for cap in (20000,200000,500000,1000000):
    for p,(a,b) in L.PER.items():
        r0=L.replay(cap,a,b,T=T0,A=A0,H=H0); r1=L.replay(cap,a,b,T=T1,A=A1,H=H1)
        res[f"{cap}|{p}"]=[[round(r0["final"]/1e5,2),r0["dd"]],[round(r1["final"]/1e5,2),r1["dd"]]]
        print(cap,p,res[f"{cap}|{p}"],flush=True)
json.dump(res,open("/home/claude/r102/out/e2e.json","w"))
