"""Re-test of the two round-14C watch-list leads on the CURRENT rules (Noise W + Camarilla CA, VIX caps, one trade per index
at a time), 17 Oct 2026.
  A. trade the laggard index: take a signal only when this index is WEAKER than the other since the open in the trade's
     direction (inv_rs_open) - 14C found it on Camarilla. Tested on Camarilla only (the lead), Noise only and both.
  B. breakouts after the implied move is spent: take a signal only when the session range so far is >= 100% of the implied
     daily move (yesterday's closing ATM straddle of the nearest expiry / sqrt(sessions to expiry), NSE bhavcopy from Oct 2023).
     Compared with the baseline on the days that have a straddle (sub-baseline). Thresholds 80 / 100 / 120% shown for stability.
Candidates fixed before looking. Train < 1 Jul 2025, unseen after. perm_p = chance a random subset of the baseline trades of
the same size has an average trade as good.
Run from research/ (FNO_CALIB=1). Prints a table; writes leads_14c_retest.json.
"""
import json, math, os, time
from datetime import date, timedelta
os.environ.setdefault("FNO_CALIB", "1")
import numpy as np, pandas as pd
import engine as E

SYMS = ("NIFTY", "BANKNIFTY"); OTHER = {"NIFTY": "BANKNIFTY", "BANKNIFTY": "NIFTY"}
START, END, SPLIT = date(2023, 1, 2), date(2026, 10, 1), date(2025, 7, 1)
BUD = {date(2024, 2, 1), date(2024, 7, 23), date(2025, 2, 1), date(2026, 2, 1)}
W = dict(mult=1.75, stop_atr=2.0, rr=4.0, vwap=1, max_trades=1, vix_min=11, last_entry=870, otm=-1, be=0, ctx=-9, rmin=1.0, rmax=2.5)
CA = {'rr': None, 'be': 1.0, 'tstop': 45, 'ctx': -9, 'otm': -1, 'last_entry': 780, 'max_trades': 1, 'rmin': 1.0, 'rmax': 3.0,
      'vix_min': 11, 'vix_max': 22, 'mode': 'break', 'trail': ''}
T0 = time.time()


def load(sym):
    days = [d for d in E.load_days(sym) if START <= d.d <= END and d.d not in BUD]
    for d in days:
        if sym == "NIFTY" and (d.expiry - d.d).days <= 1:
            d.expiry = E.expiry_for(d.d + timedelta(days=1), "weekly")
    return days


D = {s: load(s) for s in SYMS}
DAYMAP = {s: {d.d: d for d in D[s]} for s in SYMS}
DATES = [d.d for d in D["NIFTY"]]


def implied_table():
    b = pd.read_csv("data/OPT_bhavcopy.csv.gz", parse_dates=["date", "expiry"])
    b["date"], b["expiry"] = b["date"].dt.date, b["expiry"].dt.date
    b = b[b["close"] > 0]; out = {}
    for (d, sym, e), g in b.groupby(["date", "sym", "expiry"]):
        if sym not in SYMS or (e - d).days < 1: continue
        und = float(g["und"].dropna().iloc[0]) if g["und"].notna().any() else (DAYMAP[sym][d].c[-1] if d in DAYMAP[sym] else None)
        if und is None: continue
        k = g.iloc[(g["strike"] - und).abs().argsort().iloc[0]]["strike"]
        ce, pe = g[(g["strike"] == k) & (g["opt"] == "CE")], g[(g["strike"] == k) & (g["opt"] == "PE")]
        if len(ce) and len(pe): out.setdefault((sym, d), {})[e] = float(ce["close"].iloc[0] + pe["close"].iloc[0])
    return out


IMPL = implied_table()
for s in SYMS:
    for n, d in enumerate(D[s]):
        od = DAYMAP[OTHER[s]].get(d.d); m = len(d.t)
        rs = [math.nan] * m; em = [math.nan] * m
        if od is not None:
            for i, t in enumerate(d.t):
                oi = od.idx.get(t)
                if oi is not None: rs[i] = (d.c[i] / d.o[0] - 1) - (od.c[oi] / od.o[0] - 1)
        d.implied = math.nan
        prev = D[s][n - 1].d if n else None
        tab = IMPL.get((s, prev)) if prev else None
        if tab:
            ex = sorted(e for e in tab if e >= d.d)
            if ex:
                ntd = max(sum(1 for q in DATES if prev < q <= ex[0]), 1); d.implied = tab[ex[0]] / math.sqrt(ntd)
        hi, lo = -1e18, 1e18
        for i in range(m):
            hi, lo = max(hi, d.h[i]), min(lo, d.l[i])
            if not math.isnan(d.implied): em[i] = (hi - lo) / d.implied
        d.rs, d.em = rs, em
print(f"loaded {len(DATES)} sessions, straddle days {sum(not math.isnan(d.implied) for d in D['NIFTY'])} ({time.time()-T0:.0f}s)", flush=True)

F = E.FILTERS
F["laggard"] = lambda d, i, s: not math.isnan(d.rs[i]) and s * d.rs[i] < 0
F["leader"] = lambda d, i, s: not math.isnan(d.rs[i]) and s * d.rs[i] > 0
for th in (0.8, 1.0, 1.2):
    F[f"spent{int(th*100)}"] = (lambda t: (lambda d, i, s: not math.isnan(d.em[i]) and d.em[i] >= t))(th)
    F[f"room{int(th*100)}"] = (lambda t: (lambda d, i, s: not math.isnan(d.em[i]) and d.em[i] < t))(th)
F["has_em"] = lambda d, i, s: not math.isnan(d.em[i])


def rules(fn=None, fc=None):
    out = []
    for s in SYMS:
        wn, ca = dict(W), dict(CA)
        if fn: wn["filters"] = list(fn)
        if fc: ca["filters"] = list(fc)
        tr = [dict(t, strat="noise", sym=s) for t in E.run(D[s], s, "noise", wn)] + [dict(t, strat="camarilla", sym=s) for t in E.run(D[s], s, "camarilla", ca)]
        tr.sort(key=lambda t: (t["date"], t["in"])); busy = {}
        for t in tr:
            if busy.get(t["date"], 0) > t["in"]: continue
            out.append(t); busy[t["date"]] = t["out"]
    return sorted(out, key=lambda t: (t["date"], t["in"]))


def pf(x):
    x = np.asarray(x)
    return round(float(x[x > 0].sum() / -x[x <= 0].sum()), 2) if len(x) and (x <= 0).any() and x[x <= 0].sum() else None


def stats(tr, base):
    p = np.array([t["pnl"] for t in tr]) if tr else np.array([0.0])
    cum = np.cumsum(p); dd = float((cum - np.maximum.accumulate(np.r_[0, cum])[1:]).min())
    a = [t["pnl"] for t in tr if t["date"] < SPLIT]; u = [t["pnl"] for t in tr if t["date"] >= SPLIT]
    yrs = {y: round(sum(t["pnl"] for t in tr if t["date"].year == y) / 1000, 1) for y in (2023, 2024, 2025, 2026)}
    bp = np.array([t["pnl"] for t in base]); k = len(tr); perm = None
    if 0 < k < len(bp):
        rng = np.random.default_rng(7); mean = p.mean()
        perm = round(float((np.array([rng.choice(bp, k, replace=False).mean() for _ in range(3000)]) >= mean).mean()), 3)
    return dict(n=len(tr), net_k=round(p.sum() / 1000, 1), pf=pf(p), dd_k=round(dd / 1000, 1), train_k=round(sum(a) / 1000, 1), train_pf=pf(a),
                unseen_n=len(u), unseen_k=round(sum(u) / 1000, 1), unseen_pf=pf(u), years_k=yrs, perm_p=perm)


BASE = rules()
BASE_EM = rules(["has_em"], ["has_em"])
res = {"baseline (all days)": stats(BASE, BASE), "baseline, straddle days only (sub-baseline for B)": stats(BASE_EM, BASE_EM)}
tests = {
    "A1 laggard - Camarilla only (the lead)": (None, ["laggard"], BASE),
    "A2 laggard - Noise only": (["laggard"], None, BASE),
    "A3 laggard - both rules": (["laggard"], ["laggard"], BASE),
    "A4 leader - Camarilla only [diag]": (None, ["leader"], BASE),
    "B1 spent >=80% - both": (["spent80"], ["spent80"], BASE_EM),
    "B2 spent >=100% - both (the lead)": (["spent100"], ["spent100"], BASE_EM),
    "B3 spent >=120% - both": (["spent120"], ["spent120"], BASE_EM),
    "B4 spent >=100% - Noise only": (["spent100"], ["has_em"], BASE_EM),
    "B5 spent >=100% - Camarilla only": (["has_em"], ["spent100"], BASE_EM),
    "B6 room <100% - both [diag]": (["room100"], ["room100"], BASE_EM),
}
for nm, (fn, fc, base) in tests.items():
    res[nm] = stats(rules(fn, fc), base); print(nm, res[nm], flush=True)
json.dump(res, open("leads_14c_retest.json", "w"), indent=1, default=str)
for k, v in res.items():
    print(f"{k:52s} n={v['n']:4d} net={v['net_k']:7.1f}k pf={v['pf']} dd={v['dd_k']}k train={v['train_k']}k/{v['train_pf']} unseen={v['unseen_n']}/{v['unseen_k']}k/{v['unseen_pf']} years={v['years_k']} p={v['perm_p']}")
print(f"done {time.time()-T0:.0f}s")
