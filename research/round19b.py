import os; os.environ.setdefault("FNO_CALIB","1")
exec(open("round19.py").read().split("OUT={}")[0])
E.RISK=0.01
CUR=current(BASE)
def summ(tr):
    a=[t for t in tr if t["date"]<SPLIT]; b=[t for t in tr if t["date"]>=SPLIT]; s=stats(tr)
    return dict(n=len(tr), net_k=round(sum(t["pnl"] for t in tr)/1000,1), train_k=round(sum(t["pnl"] for t in a)/1000,1), unseen_k=round(sum(t["pnl"] for t in b)/1000,1), pf=s["pf"], dd_k=round(s["max_dd"]/1000,1))
def dayguard(tr, losers=2, maxtr=4, daypct=0.02):
    out=[]; st=defaultdict(lambda:[0,0,0.0])
    for t in sorted(tr,key=lambda t:(t["date"],t["in"])):
        n,l,p=st[t["date"]]
        # a trade only counts as known-loser if it closed before this one opened
        closed=[x for x in out if x["date"]==t["date"] and x["out"]<=t["in"]]
        l=sum(x["pnl"]<0 for x in closed); p=sum(x["pnl"] for x in closed)
        n=sum(1 for x in out if x["date"]==t["date"])
        if l>=losers or n>=maxtr or p<=-daypct*200000: continue
        out.append(t)
    return out
print("current        ", summ(CUR))
print("account guard  ", summ(dayguard(CUR)))
print("losers=3       ", summ(dayguard(CUR,losers=3)))
print("no loser limit ", summ(dayguard(CUR,losers=99)))
import json; json.dump({"cur":summ(CUR),"guard":summ(dayguard(CUR)),"losers3":summ(dayguard(CUR,losers=3))},open("round19b.json","w"),indent=1)
