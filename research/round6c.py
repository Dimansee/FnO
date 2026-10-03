import os; os.environ.setdefault("FNO_CALIB","1")
exec(open("round6b.py").read().split("cands = {")[0])
from datetime import timedelta
orig = {d.d: d.expiry for d in D["NIFTY"]}
for d in D["NIFTY"]:
    if (d.expiry - d.d).days <= 1:
        d.expiry = E.expiry_for(d.d + timedelta(days=1), "weekly")
ROLL = book()
print("roll Nifty to next week when 1 day left:", ev(ROLL))
skipbn = lambda t: not (t["sym"]=="BANKNIFTY" and t["date"] in EXPIRY_DAY["BANKNIFTY"])
print("roll + skip BN expiry day:", ev([t for t in ROLL if skipbn(t)]))
print("roll + skip BN expiry + skip Budget:", ev([t for t in ROLL if skipbn(t) and t["date"] not in BUD]))
print("roll + skip BN expiry + Budget + VIX<20:", ev([t for t in ROLL if skipbn(t) and t["date"] not in BUD and DAYMAP[t["sym"]][t["date"]].vix[0] < 20]))
# proper re-run with the filters inside the engine (a skipped trade can free the slot for another)
import engine as E2
skipdays = {"BANKNIFTY": set(EXPIRY_DAY["BANKNIFTY"]) | BUD, "NIFTY": set(BUD)}
R = []
for s in SYMS:
    R += both(s, days=[d for d in D[s] if d.d not in skipdays[s]])
print("RE-RUN roll + skip BN expiry + Budget:", ev(R), stats(R)["pf"], stats(R)["win"], stats(R)["trades"])
for w in (30, 60, 90, 120):
    print(w, "days:", round(sum(t["pnl"] for t in R if t["date"] >= DATES[-w])), "vs base", round(sum(t["pnl"] for t in BASE if t["date"] >= DATES[-w])))
import json; json.dump({"rerun": stats(R), "windows": {w: [round(sum(t["pnl"] for t in R if t["date"] >= DATES[-w])), round(sum(t["pnl"] for t in BASE if t["date"] >= DATES[-w]))] for w in (30,60,90,120)}}, open("round6_rules.json","w"), default=str, indent=1)
