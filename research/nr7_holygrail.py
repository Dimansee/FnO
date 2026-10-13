"""Quick check: (2) Crabel NR7 + ORB, (3) Raschke 'Holy Grail' ADX/EMA-20 pullback. Same engine, pricing, sizing as the rules."""
import sys, io, contextlib, json
sys.argv = ["x"]
with contextlib.redirect_stdout(io.StringIO()):
    import round6 as R6
import engine as E, numpy as np, pandas as pd
SYMS = ("NIFTY", "BANKNIFTY")

def nr7_days(days):
    out = set()
    for i in range(7, len(days)):
        rng = [max(d.h) - min(d.l) for d in days[i-7:i]]
        if rng[-1] == min(rng):                      # yesterday was the narrowest of the last 7
            out.add(days[i].d)
    return out

def s_holy(d, j, p, st):
    for i in range(max(j, 3), len(d.t) - 1):
        t = d.t[i] + 5
        if t > p.get("last_entry", 870): return None
        if t < 600 or np.isnan(d.adx[i]) or d.adx[i] < p.get("adx", 30) or d.adx[i] < d.adx[i-2]: continue
        for side in (1, -1):
            above = all(side * (d.c[k] - d.e20[k]) > 0 for k in range(i-3, i))       # trend side before the pullback
            touch = (d.l[i] <= d.e20[i]) if side > 0 else (d.h[i] >= d.e20[i])
            if not (above and touch): continue
            nb = i + 1
            trig = d.h[i] if side > 0 else d.l[i]
            if side * ((d.h[nb] if side > 0 else d.l[nb]) - trig) <= 0: continue
            entry = min(max(trig, d.l[nb]), d.h[nb]) if side > 0 else max(min(trig, d.h[nb]), d.l[nb])
            stop = d.l[i] if side > 0 else d.h[i]
            stop = E._risk_clip(d, nb, entry, stop, side, p)
            r = abs(entry - stop)
            tgt = entry + side * p["rr"] * r if p.get("rr") else None
            return E.Sig(nb, side, stop, tgt, p.get("trail", ""), p.get("be", 0), 0, "holy", px=entry)
    return None
E.STRATS["holy"] = s_holy

def report(name, tr):
    if not tr:
        print(f"{name:52s} no trades"); return {"name": name, "trades": 0}
    s = R6.stats(tr); tr_t = [t for t in tr if t["date"] < R6.SPLIT]; tr_u = [t for t in tr if t["date"] >= R6.SPLIT]
    st, su = R6.stats(tr_t), R6.stats(tr_u)
    for z in (s, st, su): z.setdefault("pf", None); z.setdefault("max_dd", 0)
    print(f"{name:52s} n {s['trades']:4d} net ₹{s['net']:+8,.0f} PF {s['pf']}  train ₹{st['net']:+8,.0f}/{st['pf']}  unseen ₹{su['net']:+7,.0f}/{su['pf']}  DD ₹{s['max_dd']:,.0f}")
    return {"name": name, **s, "train": st, "unseen": su}

res = []
# (2) NR7 filter on ORB and on the two rules
ORB = dict(or_min=15, or_wmin=0.15, or_wmax=1.2, stop_atr=1.5, rr=3.0, vwap=1, max_trades=1, vix_min=11, last_entry=870, otm=-1, be=0, ctx=-9, rmin=1.0, rmax=2.5)
for sym in SYMS:
    days = R6.D[sym]; nr = nr7_days(days); sel = [d for d in days if d.d in nr]
    print(f"{sym}: NR7 days {len(sel)} of {len(days)}")
    res.append(report(f"{sym} 15-min ORB, all days", [dict(t, sym=sym) for t in E.run(days, sym, "orb", ORB)]))
    res.append(report(f"{sym} 15-min ORB, NR7 days only", [dict(t, sym=sym) for t in E.run(sel, sym, "orb", ORB)]))
    res.append(report(f"{sym} the two rules, all days", R6.both(sym)))
    res.append(report(f"{sym} the two rules, NR7 days only", R6.both(sym, days=sel)))
    res.append(report(f"{sym} the two rules, NOT NR7 days", R6.both(sym, days=[d for d in days if d.d not in nr])))
# (3) Holy Grail
for sym in SYMS:
    for adx, rr, trail in ((30, 2.0, ""), (30, None, "atr"), (30, 4.0, ""), (25, 2.0, ""), (25, 4.0, ""), (20, 2.0, ""), (20, 4.0, "")):
        P = dict(adx=adx, rr=rr, trail=trail, trail_atr=2.0, max_trades=2, vix_min=11, last_entry=870, otm=-1, ctx=-9, rmin=0.5, rmax=3.0, be=1.0)
        res.append(report(f"{sym} Holy Grail ADX>{adx} pullback, {'trail ATR' if trail else f'target {rr}R'}", [dict(t, sym=sym) for t in E.run(R6.D[sym], sym, "holy", P)]))
json.dump(res, open("nr7_holygrail.json", "w"), indent=1, default=str)
