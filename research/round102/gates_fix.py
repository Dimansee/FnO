import sys, json
sys.path.insert(0, "/home/claude/dimansee/fno/research/round101")
import lib as L
T,A,H = L.base_sets()
res=json.load(open("/home/claude/r102/out/gates.json"))
for name, over in (("without the Rs 5L rules (no channel slot, no 2-OTM)", {"channel": False}), ("without the 2-OTM switch only", {})):
    out={}
    for cap in (20000,200000,500000):
        for p,(a,b) in L.PER.items():
            out[f"{cap}|{p}"]=L.replay(cap,a,b,T=T,A=A,H=None,**over)
    res[name]=out; print(name,{k:(round(v["final"]/1e5,2),v["dd"]) for k,v in out.items()})
json.dump(res,open("/home/claude/r102/out/gates.json","w"),indent=1)
