import json, pandas as pd
R=json.load(open("out/swing.json")); rows=[]
for k,v in R.items():
    sym,g,t,e,st=k.split("|"); m=v["model"]; mt=v["model_twin"]; r=v["real"] or {}; rt=v["real_twin"] or {}
    rows.append(dict(key=k,sym=sym,gate=g,trig=t,ex=e,strike=st,n=m["n"],a_n=m["a_n"],b_n=m["b_n"],a=m["a"],b=m["b"],win=m["win"],hold=v["hold"],pts=v["idx_pts"],
        tw=(mt["a"]+mt["b"]), ra=r.get("a"),rb=r.get("b"),rn=r.get("n"),rtw=(rt.get("a",0)+rt.get("b",0)) if rt else None, y=m.get("years")))
D=pd.DataFrame(rows); D["both"]=(D.a>0)&(D.b>0); D["rboth"]=(D.ra>0)&(D.rb>0)
print("versions",len(D),"model both+",D.both.sum(),"real both+",D.rboth.sum(), "of real", D.ra.notna().sum())
print("index points per trade median by gate:", D.groupby("gate").pts.median().round(1).to_dict())
for c in ("gate","trig","ex","strike","sym"):
    print(D.groupby(c).agg(v=("n","size"),both=("both","sum"),rboth=("rboth","sum"),med_tot=("a",lambda x: 0),pts=("pts","median"),hold=("hold","median"),trades=("n","median")).drop(columns="med_tot").to_string())
D["tot"]=D.a+D.b; D["rtot"]=D.ra+D.rb
print(D.sort_values("a",ascending=False).head(12)[["key","n","a","b","tw","ra","rb","rn","pts","hold"]].round(0).to_string())
print(D[D.both].sort_values("tot",ascending=False).head(15)[["key","n","a","b","tw","ra","rb","rtot","pts","hold","win"]].round(2).to_string())
print("total model Rs all versions median per trade", (D.tot/D.n).median())
D.to_pickle("out/swing_all.pkl")
