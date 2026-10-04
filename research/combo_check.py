import os; os.environ.setdefault("FNO_CALIB","1")
import json, numpy as np, pandas as pd
from datetime import date, timedelta
import engine as E, evr, combo as Cb
Cb.init()
D, DATES, SPLIT = Cb.D, Cb.DATES, Cb.SPLIT
W = dict(mult=1.75, stop_atr=2.0, rr=4.0, vwap=1, max_trades=1, vix_min=11, last_entry=870, otm=-1, be=0, ctx=-9, rmin=1.0, rmax=2.5)
CA = {'rr': None, 'be': 1.0, 'tstop': 45, 'ctx': -9, 'otm': -1, 'last_entry': 780, 'max_trades': 1, 'rmin': 1.0, 'rmax': 3.0, 'vix_min': 11, 'vix_max': 22, 'mode': 'break', 'trail': ''}
BUD = {date(2024, 2, 1), date(2024, 7, 23), date(2025, 2, 1), date(2026, 2, 1)}
def rules():
    out = []
    for s in Cb.SYMS:
        days = [d for d in E.load_days(s) if d.d >= Cb.START and d.d not in BUD]
        for d in days:
            if s == "NIFTY" and (d.expiry - d.d).days <= 1:
                d.expiry = E.expiry_for(d.d + timedelta(days=1), "weekly")
        tr = [dict(t, strat="noise") for t in E.run(days, s, "noise", W)] + [dict(t, strat="camarilla") for t in E.run(days, s, "camarilla", CA)]
        tr.sort(key=lambda t: (t["date"], t["in"])); busy = {}
        for t in tr:
            if busy.get(t["date"], 0) > t["in"]: continue
            out.append(t); busy[t["date"]] = t["out"]
    return out
def daily(tr):
    s = pd.Series([t["pnl"] for t in tr], index=[t["date"] for t in tr]).groupby(level=0).sum()
    return s.reindex(DATES, fill_value=0.0)
def show(name, tr):
    r = Cb.ev(tr, DATES, SPLIT); print(f"{name:52s} n{r['n']:4d} train ₹{r['train']/1000:+6.1f}k unseen ₹{r['unseen120']/1000:+5.1f}k (n{r['n_unseen']}) pf {r['pf']} win {r['win']}% {r['years']}")
    return tr
R = show("RULES (same period, Budget days skipped)", rules())
best = {"A: VWAP cross + ATR expanding + volume + OI (trail VWAP)": dict(trigger="vwap_x", cfilters=frozenset({"atr","oi","vol"}), exit="vwap"),
        "B: CPR cross + ATR expanding + narrow CPR + OI + RSI (2R)": dict(trigger="cpr_x", cfilters=frozenset({"atr","cpr","oi","rsi"}), exit="2R")}
base = dict(max_trades=2, vix_min=11, otm=-1)
def run(p):
    return sum((E.run(D[s], s, "combo", {**base, **p}) for s in Cb.SYMS), [])
for name, p in best.items():
    tr = show(name, run(p))
    c = np.corrcoef(daily(R), daily(tr))[0, 1]
    both = R + tr
    print(f"   correlation with the rules' daily P&L: {c:.2f}")
    show("   rules + this one", both)
    # neighbours: nudge each threshold
    orig = dict(rsi=5, atr=1.1, vol=1.3, pcr=1.0, cpr=0.5)
    src = open("combo.py").read()
    for lab, repl in (("RSI > 50", ("> 5", "> 0")), ("RSI > 60", ("> 5", "> 10")), ("ATR x1.05", ("1.1 * d.atr", "1.05 * d.atr")), ("ATR x1.2", ("1.1 * d.atr", "1.2 * d.atr")),
                      ("vol 1.0", ('>= 1.3', '>= 1.0')), ("vol 1.6", ('>= 1.3', '>= 1.6')), ("PCR 0.9/1.1", ("d.pcr >= 1.0 if side > 0 else d.pcr <= 1.0", "d.pcr >= 0.9 if side > 0 else d.pcr <= 1.1")),
                      ("CPR < 0.3%", ("d.cpr_w < 0.5", "d.cpr_w < 0.3")), ("CPR < 0.8%", ("d.cpr_w < 0.5", "d.cpr_w < 0.8"))):
        ns = {}
        exec(src.split("def s_combo")[0].split("def cond")[1].join(["def cond", ""]) if False else "", ns)
        code = "def cond" + src.split("def cond")[1].split("def s_combo")[0]
        code = code.replace(*repl)
        ns = {"np": np}; exec(code, ns)
        oldc = Cb.cond; Cb.cond = ns["cond"]
        try:
            r = Cb.ev(run(p), DATES, SPLIT)
        finally:
            Cb.cond = oldc
        print(f"   neighbour {lab:12s} n{r['n']:4d} train ₹{r['train']/1000:+6.1f}k unseen ₹{r['unseen120']/1000:+5.1f}k pf {r['pf']} {r['years']}")
