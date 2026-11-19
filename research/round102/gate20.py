import sys, json; sys.path.insert(0,"/home/claude/dimansee/fno/research/round101")
import lib as L
from fno import config as C
T,A,H=L.base_sets(); out={}
for cap in (15000,20000,25000,30000,35000):
    for lab,(a,b) in {**L.PER, **L.YEARS}.items():
        r0=L.replay(cap,a,b,T=T,A=A,H=H); C.TREND_MIN_EQUITY=20000; r1=L.replay(cap,a,b,T=T,A=A,H=H); C.TREND_MIN_EQUITY=40000
        out[f"{cap}|{lab}"]=[r0,r1]; print(cap,lab,round(r0["final"]),r0["dd"],"->",round(r1["final"]),r1["dd"],flush=True)
json.dump(out,open("/home/claude/r102/out/gate20.json","w"),indent=1)
