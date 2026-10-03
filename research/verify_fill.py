import os; os.environ.setdefault("FNO_CALIB","1")
exec(open("round6d.py").read().split("res = {}")[0])
def worse(fn, frac):
    def g(d, j, p, st):
        while True:
            s = fn(d, j, p, st)
            if s is None or s.i + 1 >= len(d.t):
                return s
            k = s.i + 1
            px = d.o[k] + s.side * frac * (d.h[k] - d.l[k])
            if s.side * (px - s.stop) <= 0:
                j = s.i + 1; continue
            s.px = px
            return s
    return g
b = {(t["sym"], t["date"], t["in"]): t for t in BASE}
for frac in (0.1, 0.25):
    E.STRATS.update({k: worse(ORIG[k], frac) for k in ("noise", "camarilla")})
    L = []
    for s in SYMS:
        CUR["sym"] = s; L += both(s)
    E.STRATS.update(ORIG)
    l = {(t["sym"], t["date"], t["in"]): t for t in L}
    c = set(b) & set(l)
    pts = np.mean([t["side"] * (l[x]["spot_in"] - b[x]["spot_in"]) / (1 if x[0] == "NIFTY" else 2.2) for x, t in ((x, b[x]) for x in c)])
    print(f"fill worse by {frac:.0%} of the next 5-min candle (avg {pts:.1f} Nifty pts): matched {len(c)} trades, P&L change ₹{round(sum(l[x]['pnl'] - b[x]['pnl'] for x in c))}, "
          f"premium paid +{np.mean([l[x]['prem_in']/b[x]['prem_in']-1 for x in c])*100:.2f}%")
