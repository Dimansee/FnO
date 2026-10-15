import os, inspect, json; os.environ.setdefault("FNO_CALIB","1")
exec(open("round6.py").read().split("D = {s: E.load_days(s) for s in SYMS}")[0])
D = {s: E.load_days(s) for s in SYMS}
DATES = [d.d for d in D["NIFTY"]]; SPLIT = DATES[-120]
# patch the engine's sizing: floor of 1 lot (when the premium fits) and compounding by risk % above that
src = inspect.getsource(E.trade_pnl)
src = src.replace("if lots == 0 and risk_unit * lot <= 1.5 * budget:\n            lots = 1", "if lots == 0 and (p.get('floor1') or risk_unit * lot <= 1.5 * budget):\n            lots = 1")
src = src.replace("capital * 0.6 //", "capital * (1.0 if p.get('floor1') else 0.6) //")
assert "floor1" in src
ns = E.__dict__; exec(src, ns)
BUD = {date(2023,2,1), date(2024,2,1), date(2024,7,23), date(2025,2,1), date(2026,2,1)}
DAY = {s: {d.d: d for d in D[s]} for s in SYMS}
from collections import defaultdict
def trades_for(d, eq, mult, risk, fixed1, syms=SYMS):
    out=[]
    E.RISK = risk
    for sym in syms:
        dd = DAY[sym].get(d)
        if dd is None or d in BUD: continue
        v = dd.vix[0]
        for strat, par in (("noise", dict(W, mult=mult)), ("camarilla", CA)):
            if (strat=="noise" and v>=18) or (strat=="camarilla" and v>20): continue
            p = dict(par, floor1=True)
            if fixed1: p["one_lot"] = True
            for t in E.run([dd], sym, strat, p, capital=eq):
                out.append(dict(t, strat=strat, sym=sym))
    out.sort(key=lambda t: t["in"]); res=[]; busy=0
    for t in out:
        if busy > t["in"]: continue
        res.append(t); busy = t["out"]
    return res
def sim(cap0, risk, fixed1, weekly, mult=1.75, days=None):
    eq=cap0; peak=cap0; mdd=0; wk=defaultdict(float); n=0; skipped_afford=0; lo=cap0; bym=defaultdict(float); mstart={}
    for d in (days or DATES):
        k=d.isocalendar()[:2]
        m=(d.year,d.month); mstart.setdefault(m,eq)
        for t in trades_for(d, eq, mult, risk, fixed1):
            if weekly and wk[k] <= -weekly*max(eq,1): continue
            if t["prem_in"]*E.META[t["sym"]]["lot"] > eq: skipped_afford+=1; continue
            eq+=t["pnl"]; wk[k]+=t["pnl"]; n+=1
            peak=max(peak,eq); mdd=min(mdd,(eq-peak)/peak); lo=min(lo,eq)
        bym[m]=eq/mstart[m]-1
    v=list(bym.values())
    return dict(final=round(eq), mult_x=round(eq/cap0,2), trades=n, maxdd_pct=round(100*mdd,1), lowest=round(lo), cant_afford=skipped_afford,
                avg_month_pct=round(100*sum(v)/len(v),1), pos_months_pct=round(100*sum(x>0 for x in v)/len(v)), worst_month_pct=round(100*min(v),1), best_month_pct=round(100*max(v),1))
R={}
import sys
cfgs=[("fixed 1 lot, no compounding, no weekly cap", dict(risk=.01,fixed1=True,weekly=None)),
      ("1-lot floor + compound at 1% risk, no weekly cap", dict(risk=.01,fixed1=False,weekly=None)),
      ("1-lot floor + compound at 1% risk, weekly cap 4%", dict(risk=.01,fixed1=False,weekly=.04)),
      ("1-lot floor + compound at 2% risk, no weekly cap", dict(risk=.02,fixed1=False,weekly=None)),
      ("1-lot floor + compound at 1% risk, weekly cap 15%", dict(risk=.01,fixed1=False,weekly=.15)),
      ("fixed 1 lot, weekly cap 15%", dict(risk=.01,fixed1=True,weekly=.15)),
      ("1-lot floor + compound at 1%, band 2.0x, no weekly cap", dict(risk=.01,fixed1=False,weekly=None,mult=2.0))]
for name,kw in cfgs:
    R[name]=sim(20000, **kw); print(name, R[name], flush=True)
# starting capital sensitivity (same rule): does the first losing streak decide everything?
for c0 in (20000, 40000, 100000):
    R[f"start {c0} (1% compound, no cap)"]=sim(c0, risk=.01, fixed1=False, weekly=None); print(c0, R[f"start {c0} (1% compound, no cap)"], flush=True)
json.dump(R,open("round20.json","w"),indent=1)
