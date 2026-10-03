import os; os.environ.setdefault("FNO_CALIB","1")
exec(open("round6.py").read().split("BASE = book()")[0])
import json
out = {}
for cap in (194000, 197000, 200000, 203000, 206000):
    E.CAPITAL = cap
    tr = [t for s in SYMS for t in (lambda s: (lambda tr: tr)(both(s)))(s)] if False else None
    tr = []
    for s in SYMS:
        days = D[s]
        a = [dict(t, strat="noise", sym=s) for t in E.run(days, s, "noise", W, capital=cap)] + [dict(t, strat="camarilla", sym=s) for t in E.run(days, s, "camarilla", CA, capital=cap)]
        a.sort(key=lambda t: (t["date"], t["in"])); busy = {}
        for t in a:
            if busy.get(t["date"], 0) > t["in"]:
                continue
            tr.append(t); busy[t["date"]] = t["out"]
    st = stats(tr)
    out[cap] = {"net_per_2L": round(st["net"] * 200000 / cap), "trades": st["trades"], "unseen120": round(st["unseen120"] * 200000 / cap)}
    print(cap, out[cap], flush=True)
json.dump(out, open("round6_noise.json", "w"), indent=1)
