import os; os.environ.setdefault("FNO_CALIB","1")
exec(open("round6.py").read().split("# ------------------------------------------------------------------ 2. entry delay")[0].replace('show("BASE','pass # show("BASE'))
import json, numpy as np
from collections import defaultdict
BUD = {date(2023,2,1), date(2024,2,1), date(2024,7,23), date(2025,2,1), date(2026,2,1)}
B = sorted([t for t in BASE if t["date"].weekday() < 5 and t["date"] not in BUD], key=lambda t: (t["date"], t["in"]))
def summ(tr):
    a=[t for t in tr if t["date"]<SPLIT]; b=[t for t in tr if t["date"]>=SPLIT]
    s=stats(tr)
    yrs={y:round(sum(t["pnl"] for t in tr if t["date"].year==y)/1000,1) for y in (2023,2024,2025,2026)}
    return dict(n=len(tr), net_k=round(sum(t["pnl"] for t in tr)/1000,1), train_k=round(sum(t["pnl"] for t in a)/1000,1), unseen_k=round(sum(t["pnl"] for t in b)/1000,1), unseen_n=len(b), pf=s["pf"], win=s["win"], dd_k=round(s["max_dd"]/1000,1), years=yrs)
def vix0(t): return DAYMAP[t["sym"]][t["date"]].vix[0]
OUT={}
OUT["base"]=summ(B); print("base", OUT["base"])
# 1. VIX ceiling per strategy
for strat in ("noise","camarilla"):
    for cap in (16,18,20,22,25,99):
        tr=[t for t in B if not (t["strat"]==strat and vix0(t)>=cap)]
        OUT[f"{strat}_vix<{cap}"]=summ(tr); print(strat, "vix<",cap, OUT[f"{strat}_vix<{cap}"])
    # trades split by VIX bucket for this strategy
    for lo,hi in ((0,13),(13,16),(16,19),(19,22),(22,99)):
        x=[t for t in B if t["strat"]==strat and lo<=vix0(t)<hi]
        if x: print(strat,"bucket",lo,hi,len(x),round(sum(t["pnl"] for t in x)/1000,1), stats(x)["pf"], stats(x)["win"])
# 2. event days

RBI = ["2023-02-08", "2023-04-06", "2023-06-08", "2023-08-10", "2023-10-06", "2023-12-08", "2024-02-08", "2024-04-05",
       "2024-06-07", "2024-08-08", "2024-10-09", "2024-12-06", "2025-02-07", "2025-04-09", "2025-06-06", "2025-08-06",
       "2025-10-01", "2025-12-05", "2026-02-06", "2026-04-08", "2026-06-05", "2026-08-05"]
BUDGET = ["2023-02-01", "2024-02-01", "2024-07-23", "2025-02-01", "2026-02-01"]
ELECTION = ["2024-06-03", "2024-06-04"]
FOMC = ["2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
        "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
        "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16"]


def next_session(ds):
    d = date.fromisoformat(ds)
    return next((x for x in DATES if x > d), None)


EV = {}
for lab, lst, nxt in (("RBI policy", RBI, False), ("Budget", BUDGET, False), ("Election results", ELECTION, False), ("Day after US Fed", FOMC, True)):
    for ds in lst:
        d = next_session(ds) if nxt else date.fromisoformat(ds)
        if d in set(DATES):
            EV.setdefault(d, lab)


lab=defaultdict(list)
for t in B: lab[EV.get(t["date"],"normal day")].append(t)
for k,v in sorted(lab.items(), key=lambda kv:-len(kv[1])): print("event",k,len(v),round(sum(t["pnl"] for t in v)/1000,1), stats(v)["pf"], stats(v)["win"])
OUT["events"]={k:dict(n=len(v),net_k=round(sum(t["pnl"] for t in v)/1000,1),pf=stats(v)["pf"]) for k,v in lab.items()}
# 3. kill switches replayed on the trade sequence
def killsw(tr, consec=None, week_pct=None, day_pause=None):
    out=[]; byday=defaultdict(list)
    for t in tr: byday[t["date"]].append(t)
    streak=0; paused_until=None; wk={}
    for d in sorted(byday):
        wkey=d.isocalendar()[:2]; wk.setdefault(wkey,0)
        for t in byday[d]:
            if paused_until and d<=paused_until: continue
            if week_pct and wk[wkey] <= -week_pct*200000: continue
            out.append(t); wk[wkey]+=t["pnl"]
            streak = streak+1 if t["pnl"]<0 else 0
            if consec and streak>=consec:
                paused_until=d+timedelta(days=day_pause or 1); streak=0
    return out
for name,kw in {"pause 1d after 3 losers":dict(consec=3,day_pause=1),"pause 2d after 4 losers":dict(consec=4,day_pause=2),"pause 3d after 5 losers":dict(consec=5,day_pause=3),
                "week loss cap 3%":dict(week_pct=.03),"week loss cap 4%":dict(week_pct=.04),"week loss cap 5%":dict(week_pct=.05)}.items():
    OUT["ks_"+name]=summ(killsw(B,**kw)); print(name, OUT["ks_"+name])
# 3b. camarilla/noise minimum VIX
for strat in ("camarilla","noise"):
    for lo in (12,13,14):
        tr=[t for t in B if not (t["strat"]==strat and vix0(t)<lo)]
        OUT[f"{strat}_vixmin{lo}"]=summ(tr); print(strat,"vix>=",lo,OUT[f"{strat}_vixmin{lo}"])
tr=[t for t in B if not (t["strat"]=="noise" and vix0(t)>=18) and not (t["strat"]=="camarilla" and vix0(t)>=20)]
OUT["combo_ceilings"]=summ(tr); print("noise<18 + cam<20", OUT["combo_ceilings"])
tr2=[t for t in tr if not (t["strat"]=="camarilla" and vix0(t)<13)]
OUT["combo_ceilings_cammin13"]=summ(tr2); print("+ cam>=13", OUT["combo_ceilings_cammin13"])
# 4. Bank Nifty on its own: by year
for sym in ("NIFTY","BANKNIFTY"):
    x=[t for t in B if t["sym"]==sym]; OUT["sym_"+sym]=summ(x); print(sym, OUT["sym_"+sym])
# 5. loser streak stats
json.dump(OUT, open("round18.json","w"), default=str, indent=1)
