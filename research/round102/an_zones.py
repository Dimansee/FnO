import json, pandas as pd, numpy as np
rows=[]
for s in ("NIFTY","BANKNIFTY","SENSEX"):
    R=json.load(open(f"out/zones_{s}.json"))
    for k,v in R.items():
        sym,tf,z,mode,cnt,col,stop,ex,pd_=k.split("|")
        a=v["v"]; t=v["twin"]
        rows.append(dict(key=k,sym=sym,tf=tf,z=z,mode=mode,cnt=cnt,col=col,stop=stop,ex=ex,pd=pd_,n=a["n"],a_n=a["a_n"],b_n=a["b_n"],a=a["a"],b=a["b"],a_avg=a["a_avg"],b_avg=a["b_avg"],win=a["win"],pts=a.get("pts",0),
            ta=t["a"],tb=t["b"],t_avg=(t["a"]+t["b"])/max(t["n"],1), y=a.get("years",{})))
D=pd.DataFrame(rows); D.to_pickle("out/zones_all.pkl")
print("versions",len(D)); D["both"]=(D.a>0)&(D.b>0); D["avg"]=(D.a+D.b)/D.n
print("positive 23-24:",(D.a>0).sum()," 25-26:",(D.b>0).sum()," both:",D.both.sum())
D["edge"]=D.avg-D.t_avg
g=D.groupby(["mode","z"]).agg(v=("n","size"),pos_a=("a",lambda x:(x>0).sum()),pos_b=("b",lambda x:(x>0).sum()),both=("both","sum"),med_a=("a_avg","median"),med_b=("b_avg","median"),edge=("edge","median"),pts=("pts","median"))
print(g.round(1).to_string())
g=D.groupby(["mode","cnt"]).agg(v=("n","size"),both=("both","sum"),med_a=("a_avg","median"),med_b=("b_avg","median"),edge=("edge","median"),pts=("pts","median"),trades=("n","median"))
print(g.round(1).to_string())
g=D.groupby(["sym","mode"]).agg(v=("n","size"),both=("both","sum"),med_a=("a_avg","median"),med_b=("b_avg","median"),edge=("edge","median"))
print(g.round(1).to_string())
g=D.groupby(["tf","col","stop","ex"]).agg(both=("both","sum"),med_a=("a_avg","median"),med_b=("b_avg","median"),edge=("edge","median"))
print(g.round(1).to_string())
print("\nTop 15 by 2023-24 total (design pick) and their 2025-26:")
print(D.sort_values("a",ascending=False).head(15)[["key","a_n","a","a_avg","b_n","b","b_avg","edge"]].round(0).to_string())
print("\nboth-positive versions:")
print(D[D.both].sort_values("b",ascending=False).head(25)[["key","a_n","a","b_n","b","win","edge","ta","tb"]].round(0).to_string())
