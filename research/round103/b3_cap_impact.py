import sys, json
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import r103lib as R3
from fno import portfolio_bt as PB
out = {}
K0 = PB.IMPACT_K
print("market-impact multiplier x (1 = app model 0.06*sqrt(qty/5-min volume)); 500-lot order on Nifty = 32,500 qty")
for mult in (1, 2, 3, 5):
    PB.IMPACT_K = K0 * mult
    for cap in (1_000_000, 2_500_000, 5_000_000, 10_000_000):
        row = {}
        for ml in (50, 100, 200, 500):
            R = R3.L.replay(cap, *R3.PER["full"], T=R3.T0, A=R3.A0, H=R3.H0, raw=True, max_lots=ml)
            s = PB._summary(R, cap)
            row[ml] = (s["final"], s["max_dd_pct"], s["avg_slip_extra_pct"])
        out[f"{mult}|{cap}"] = row
        print(f"impact x{mult} cap {cap/1e5:.0f}L: " + " | ".join(f"max {ml}: {v[0]/1e5:,.0f}L ({v[1]:.0f}%) slip {v[2]:.2f}%" for ml, v in row.items()), flush=True)
PB.IMPACT_K = K0
json.dump(out, open("/tmp/r103_b3.json", "w"), indent=1)
