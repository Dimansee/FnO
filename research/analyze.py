import json, sys
from collections import defaultdict

res = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "results.json"))
SY = ["NIFTY", "BANKNIFTY"]
by = defaultdict(list)
for r in res:
    by[r["strategy"]].append(r)


def line(r):
    tr, te = r["train"], r["test"]
    a = " | ".join(f"{s[:4]} tr sh {tr[s]['all']['sharpe']:+.2f} pf {tr[s]['all']['pf']:.2f} n {tr[s]['all']['n']} net {tr[s]['all']['net']/1000:+.0f}k" for s in SY)
    b = " | ".join(f"{s[:4]} " + " ".join(f"{w}:{te[s]['last'+str(w)]['net']/1000:+.0f}k/pf{te[s]['last'+str(w)]['pf']:.2f}" for w in (30, 60, 90, 120)) for s in SY)
    return f"score {r['score']:+.2f}\n    TRAIN {a}\n    TEST  {b}"


print("strategy        configs  robust(both idx, both halves +)")
for st, rs in sorted(by.items()):
    ok = [r for r in rs if r["score"] > -50]
    print(f"{st:15s} {len(rs):5d}   {len(ok)}")
print()
for st, rs in sorted(by.items(), key=lambda kv: -max(r["score"] for r in kv[1])):
    rs.sort(key=lambda r: -r["score"])
    print("=" * 100)
    print(st)
    for r in rs[: int(sys.argv[2]) if len(sys.argv) > 2 else 3]:
        print("  ", {k: v for k, v in r["params"].items()})
        print("  ", line(r))
