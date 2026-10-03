import os; os.environ.setdefault("FNO_CALIB","1")
exec(open("round6b.py").read().split("cands = {")[0])
from datetime import timedelta
for d in D["NIFTY"]:
    if (d.expiry - d.d).days <= 1:
        d.expiry = E.expiry_for(d.d + timedelta(days=1), "weekly")
R = []
for s in SYMS:
    R += both(s, days=[d for d in D[s] if d.d not in BUD])
st = stats(R)
print("roll + skip Budget:", ev(R), st["pf"], st["win"], st["trades"], st["max_dd"])
import json
json.dump({"rerun": st, "windows": {w: [round(sum(t["pnl"] for t in R if t["date"] >= DATES[-w])), round(sum(t["pnl"] for t in BASE if t["date"] >= DATES[-w]))] for w in (30,60,90,120)}}, open("round6_rules.json","w"), default=str, indent=1)
