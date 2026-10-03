import os; os.environ.setdefault("FNO_CALIB","1")
exec(open("round6d.py").read().split("res = {}")[0])
E.STRATS.update({k: delayed(ORIG[k], 1) for k in ("noise", "camarilla")})
L1 = []
for s in SYMS:
    CUR["sym"] = s; L1 += both(s)
E.STRATS.update(ORIG)
b = {(t["sym"], t["date"], t["in"]): t for t in BASE}
l = {(t["sym"], t["date"], t["in"]): t for t in L1}
common = set(b) & set(l)
d = np.array([l[x]["pnl"] - b[x]["pnl"] for x in common])
pin = np.array([l[x]["prem_in"] / b[x]["prem_in"] - 1 for x in common]) * 100
same_exit = np.mean([l[x]["out"] == b[x]["out"] for x in common]) * 100
print("matched trades", len(common), "of", len(BASE), len(L1))
print("P&L change on matched trades: total ₹", round(d.sum()), " median ₹", round(np.median(d)), " mean ₹", round(d.mean()))
print("premium paid change % median", round(np.median(pin), 2), "mean", round(pin.mean(), 2), "| same exit time", round(same_exit), "%")
only_b = sum(b[x]["pnl"] for x in set(b) - set(l)); only_l = sum(l[x]["pnl"] for x in set(l) - set(b))
print("trades only in base: ₹", round(only_b), "| only in delayed: ₹", round(only_l))
diff_exit = [x for x in common if l[x]["out"] != b[x]["out"]]
print("matched but different exit:", len(diff_exit), "P&L change there ₹", round(sum(l[x]["pnl"] - b[x]["pnl"] for x in diff_exit)))
ob = sorted(set(b) - set(l)); ol = sorted(set(l) - set(b))
print("base-only sample:", [(x, b[x]["strat"], round(b[x]["pnl"])) for x in ob[:6]])
print("delayed-only sample:", [(x, l[x]["strat"], round(l[x]["pnl"])) for x in ol[:6]])
from collections import Counter
print("base-only by strat", Counter(b[x]["strat"] for x in ob), "delayed-only by strat", Counter(l[x]["strat"] for x in ol))
same_day = [x for x in ob if any(y[0] == x[0] and y[1] == x[1] for y in ol)]
print("base-only trades whose day has a delayed-only trade:", len(same_day))
