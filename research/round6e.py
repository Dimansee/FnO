import os; os.environ.setdefault("FNO_CALIB","1")
exec(open("round6d.py").read().split("res = {}")[0])
x1 = {}
for s in SYMS:
    x = pd.read_csv(f"data/{s}_1m_upstox.csv.gz", parse_dates=["ts"])
    k = list(zip(x.ts.dt.date, x.ts.dt.hour * 60 + x.ts.dt.minute))
    x1[s] = (dict(zip(k, x.low)), dict(zip(k, x.high)))
def limit(fn, wait):
    def g(d, j, p, st):
        s = fn(d, j, p, st)
        if s is None:
            return s
        lo, hi = x1[CUR["sym"]]
        px = d.c[s.i]
        m0 = d.t[s.i] + 5
        for m in range(m0, m0 + wait):                # limit order at the signal price, live `wait` minutes
            l, h = lo.get((d.d, m)), hi.get((d.d, m))
            if l is None:
                continue
            if (s.side > 0 and l <= px) or (s.side < 0 and h >= px):
                s.px = px
                s.i = s.i + (m - m0) // 5             # simulation continues from the bar it filled in
                return s
        return "skip", s.i
    def h(d, j, p, st):
        r = g(d, j, p, st)
        while isinstance(r, tuple):                   # unfilled: look for the next signal after it
            r = g(d, r[1] + 1, p, st)
        return r
    return h
out = {}
for wait in (2, 5, 10):
    E.STRATS.update({k: limit(ORIG[k], wait) for k in ("noise", "camarilla")})
    tr = []
    for s in SYMS:
        CUR["sym"] = s
        tr += both(s)
    out[wait] = stats(tr)
    show(f"limit at signal price, {wait} min", out[wait])
E.STRATS.update(ORIG)
json.dump(out, open("round6_limit.json", "w"), default=str, indent=1)
