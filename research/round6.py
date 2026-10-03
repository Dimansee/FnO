"""Round 6 robustness checks of the app's two strategies (noise band + Camarilla, both indices,
real-price-corrected premiums). Run with FNO_CALIB=1.

1. results by days-to-expiry, weekday and expiry days
2. entry delay (buy at the next bar's open / a worse price inside the next bar)
3. luck test: block-bootstrap of daily P&L -> range of 1-year drawdowns; risk 0.5 / 1 / 1.5 / 2 %
4. event days (RBI policy, Budget, election results, day after a US Fed decision)
5. regimes: VIX at the open, gap, trend vs range day
6. real contract history: actual listed expiries (Bank Nifty weeklies until Nov 2024), historical
   lot sizes, STT / exchange charges before Oct 2024
"""
import json, os, sys
from datetime import date, timedelta
import numpy as np
import pandas as pd

os.environ.setdefault("FNO_CALIB", "1")
import engine as E

SYMS = ("NIFTY", "BANKNIFTY")
W = dict(mult=1.75, stop_atr=2.0, rr=4.0, vwap=1, max_trades=1, vix_min=11, last_entry=870, otm=-1,
         be=0, ctx=-9, rmin=1.0, rmax=2.5)
CA = {'rr': None, 'be': 1.0, 'tstop': 45, 'ctx': -9, 'otm': -1, 'last_entry': 780, 'max_trades': 1, 'rmin': 1.0,
      'rmax': 3.0, 'vix_min': 11, 'vix_max': 22, 'mode': 'break', 'trail': ''}
D = {s: E.load_days(s) for s in SYMS}
DATES = [d.d for d in D["NIFTY"]]
SPLIT = DATES[-120]
OUT = {}


def both(sym, days=None, w=W, ca=CA):
    days = days or D[sym]
    tr = [dict(t, strat="noise", sym=sym) for t in E.run(days, sym, "noise", w)] + \
         [dict(t, strat="camarilla", sym=sym) for t in E.run(days, sym, "camarilla", ca)]
    tr.sort(key=lambda t: (t["date"], t["in"]))
    out, busy = [], {}
    for t in tr:
        if busy.get(t["date"], 0) > t["in"]:
            continue
        out.append(t)
        busy[t["date"]] = t["out"]
    return out


def book(**kw):
    return [t for s in SYMS for t in both(s, **kw)]


def stats(tr):
    if not tr:
        return {"trades": 0, "net": 0}
    p = np.array([t["pnl"] for t in tr])
    daily = pd.Series(p, index=[t["date"] for t in tr]).groupby(level=0).sum().reindex(DATES, fill_value=0)
    eq = daily.cumsum().values
    dd = float((eq - np.maximum.accumulate(np.concatenate([[0], eq]))[1:]).min())
    pos, neg = p[p > 0].sum(), -p[p <= 0].sum()
    return {"trades": len(p), "net": round(float(p.sum())), "per_trade": round(float(p.mean())),
            "win": round(float((p > 0).mean() * 100), 1), "pf": round(float(pos / neg), 2) if neg else None,
            "max_dd": round(dd), "unseen120": round(float(sum(t["pnl"] for t in tr if t["date"] >= SPLIT))),
            "years": {y: round(float(sum(t["pnl"] for t in tr if t["date"].year == y))) for y in (2023, 2024, 2025, 2026)}}


def groups(tr, key):
    g = {}
    for t in tr:
        g.setdefault(key(t), []).append(t)
    return {k: {kk: v for kk, v in stats(x).items() if kk in ("trades", "net", "per_trade", "win", "pf")} for k, x in sorted(g.items(), key=lambda kv: str(kv[0]))}


def show(name, s):
    print(f"{name:42s} n{s['trades']:4d} net ₹{s['net']/1000:+7.1f}k  /trade ₹{s.get('per_trade',0):+5d}  win {s.get('win',0):4.1f}%  "
          f"pf {s.get('pf') or 0:.2f}  maxDD ₹{s.get('max_dd',0)/1000:.0f}k  unseen120 ₹{s.get('unseen120',0)/1000:+.1f}k  {s.get('years')}", flush=True)


BASE = book()
OUT["base"] = stats(BASE)
show("BASE (app rules)", OUT["base"])

# ------------------------------------------------------------------ 1. expiry / weekday
DAYMAP = {s: {d.d: d for d in D[s]} for s in SYMS}
bh = pd.read_csv("data/OPT_bhavcopy.csv.gz", usecols=["date", "sym", "expiry"], parse_dates=["date", "expiry"])
EXPS = {s: sorted(set(bh[bh.sym == s]["expiry"].dt.date)) for s in SYMS}


def real_expiries(sym):
    """Listed expiry dates: from NSE bhavcopy (Oct 2023+), before that the rule of the time."""
    xs = set(EXPS[sym])
    d = DATES[0]
    while d < date(2023, 10, 31):
        wd = 3 if (sym == "NIFTY" or d < date(2023, 9, 1)) else 2       # Bank Nifty weekly moved to Wednesday Sep 2023
        if d.weekday() == wd:
            xs.add(d)
        d += timedelta(days=1)
    return sorted(xs)


REAL_EXP = {s: real_expiries(s) for s in SYMS}
EXPIRY_DAY = {s: set(REAL_EXP[s]) for s in SYMS}


def dte_bucket(t):
    x = (DAYMAP[t["sym"]][t["date"]].expiry - t["date"]).days
    return "1" if x <= 1 else "2" if x <= 2 else "3-4" if x <= 4 else "5-7" if x <= 7 else "8-14" if x <= 14 else "15+"


OUT["by_weekday"] = groups(BASE, lambda t: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][t["date"].weekday()])
OUT["by_dte"] = groups(BASE, dte_bucket)
OUT["by_expiry_day"] = groups(BASE, lambda t: f"{t['sym']} {'expiry day' if t['date'] in EXPIRY_DAY[t['sym']] else 'other day'}")
OUT["by_strategy"] = groups(BASE, lambda t: f"{t['sym']} {t['strat']}")
print("weekday", OUT["by_weekday"]); print("dte", OUT["by_dte"]); print("expiry", OUT["by_expiry_day"])
no_exp = [t for t in BASE if t["date"] not in EXPIRY_DAY[t["sym"]]]
OUT["skip_expiry_days"] = stats(no_exp)
show("skip each index's expiry days", OUT["skip_expiry_days"])

# ------------------------------------------------------------------ 2. entry delay
ORIG = dict(E.STRATS)


def delayed(fn, how):
    def g(d, j, p, st):
        s = fn(d, j, p, st)
        if s is None or s.i + 1 >= len(d.t):
            return s
        k = s.i + 1
        if how == "next_open":
            s.px = d.o[k]
        else:                                   # a worse fill: open moved 25% of the next bar's range against us
            s.px = d.o[k] + s.side * 0.25 * (d.h[k] - d.l[k])
        if s.side * (s.px - s.stop) <= 0:       # already through the stop -> skip
            return None
        s.i = k - 1                             # price is from the start of bar k; simulation walks from k
        return s
    return g


for how in ("next_open", "worse_fill"):
    E.STRATS.update({k: delayed(ORIG[k], how) for k in ("noise", "camarilla")})
    OUT[f"delay_{how}"] = stats(book())
    show(f"entry: {how}", OUT[f"delay_{how}"])
E.STRATS.update(ORIG)

# ------------------------------------------------------------------ 3. luck test + risk level
rng = np.random.default_rng(7)


def bootstrap(tr, capital=E.CAPITAL, n=5000, horizon=250, block=5):
    daily = pd.Series([t["pnl"] for t in tr], index=[t["date"] for t in tr]).groupby(level=0).sum().reindex(DATES, fill_value=0).values
    nb = horizon // block
    starts = rng.integers(0, len(daily) - block, size=(n, nb))
    paths = daily[starts[..., None] + np.arange(block)].reshape(n, -1)
    eq = paths.cumsum(axis=1)
    dd = (eq - np.maximum.accumulate(np.concatenate([np.zeros((n, 1)), eq], axis=1), axis=1)[:, 1:]).min(axis=1)
    end = eq[:, -1]
    return {"median_year": round(float(np.median(end))), "p_losing_year": round(float((end < 0).mean() * 100), 1),
            "dd_median_pct": round(float(-np.median(dd) / capital * 100), 1), "dd_95_pct": round(float(-np.percentile(dd, 5) / capital * 100), 1),
            "dd_99_pct": round(float(-np.percentile(dd, 1) / capital * 100), 1), "worst_year_5pct": round(float(np.percentile(end, 5)))}


OUT["risk"] = {}
for r in (0.005, 0.01, 0.015, 0.02):
    E.RISK = r
    tr = BASE if r == 0.01 else book()
    s, b = stats(tr), bootstrap(tr)
    OUT["risk"][f"{r*100:g}%"] = {**{k: s[k] for k in ("trades", "net", "max_dd", "unseen120", "years")}, **b}
    print(f"risk {r*100:g}%:", OUT["risk"][f"{r*100:g}%"], flush=True)
E.RISK = 0.01

# ------------------------------------------------------------------ 4. event days
RBI = ["2023-02-08", "2023-04-06", "2023-06-08", "2023-08-10", "2023-10-06", "2023-12-08", "2024-02-08", "2024-04-05",
       "2024-06-07", "2024-08-08", "2024-10-09", "2024-12-06", "2025-02-07", "2025-04-09", "2025-06-06", "2025-08-06",
       "2025-10-01", "2025-12-05", "2026-02-06", "2026-04-08", "2026-06-05", "2026-08-05"]
BUDGET = ["2023-02-01", "2024-02-01", "2024-07-23", "2025-02-01", "2026-02-01"]
ELECTION = ["2024-06-03", "2024-06-04"]
FOMC = ["2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
        "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
        "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16"]


def next_session(ds):
    d = date.fromisoformat(ds)
    return next((x for x in DATES if x > d), None)


EV = {}
for lab, lst, nxt in (("RBI policy", RBI, False), ("Budget", BUDGET, False), ("Election results", ELECTION, False), ("Day after US Fed", FOMC, True)):
    for ds in lst:
        d = next_session(ds) if nxt else date.fromisoformat(ds)
        if d in set(DATES):
            EV.setdefault(d, lab)
OUT["events"] = groups(BASE, lambda t: EV.get(t["date"], "normal day"))
OUT["skip_events"] = stats([t for t in BASE if t["date"] not in EV])
print("events", OUT["events"]); show("skip event days", OUT["skip_events"])

# ------------------------------------------------------------------ 5. regimes
def vix_b(t):
    v = DAYMAP[t["sym"]][t["date"]].vix[0]
    return "<12" if v < 12 else "12-14" if v < 14 else "14-16" if v < 16 else "16-20" if v < 20 else "20+"


def gap_b(t):
    g = DAYMAP[t["sym"]][t["date"]].gap
    return "gap down >0.5%" if g < -0.5 else "gap up >0.5%" if g > 0.5 else "small gap"


def daytype(t):
    d = DAYMAP[t["sym"]][t["date"]]
    rng_ = max(d.h) - min(d.l)
    body = abs(d.c[-1] - d.o[0])
    return "trend day (close far from open)" if body >= 0.6 * rng_ else "range day" if body <= 0.25 * rng_ else "mixed day"


OUT["by_vix"] = groups(BASE, vix_b)
OUT["by_gap"] = groups(BASE, gap_b)
OUT["by_daytype"] = groups(BASE, daytype)
print("vix", OUT["by_vix"]); print("gap", OUT["by_gap"]); print("daytype", OUT["by_daytype"])
# share of trading days of each type (Nifty)
dt_share = {}
for d in D["NIFTY"]:
    rng_, body = max(d.h) - min(d.l), abs(d.c[-1] - d.o[0])
    k = "trend" if body >= 0.6 * rng_ else "range" if body <= 0.25 * rng_ else "mixed"
    dt_share[k] = dt_share.get(k, 0) + 1
OUT["daytype_share"] = {k: round(v / len(D["NIFTY"]) * 100) for k, v in dt_share.items()}

# ------------------------------------------------------------------ 6. real contract history
LOTS = {"NIFTY": [(date(2024, 4, 26), 50), (date(2024, 11, 20), 25), (date(2025, 10, 28), 75), (date(2099, 1, 1), 65)],
        "BANKNIFTY": [(date(2023, 7, 1), 25), (date(2024, 11, 20), 15), (date(2025, 4, 25), 30), (date(2025, 10, 28), 35), (date(2099, 1, 1), 30)]}


def lot_on(sym, d):
    return next(l for until, l in LOTS[sym] if d < until)


ORIG_CH = E.I.charges
CUR = {"d": None}


def charges_hist(pb, ps, qty, orders=2):
    if CUR["d"] and CUR["d"] < date(2024, 10, 1):
        bv, sv = pb * qty, ps * qty
        br = 20 * orders
        exch = 0.000503 * (bv + sv)
        sebi = 0.000001 * (bv + sv)
        return round(br + 0.000625 * sv + exch + sebi + 0.00003 * bv + 0.18 * (br + exch + sebi), 2)
    return ORIG_CH(pb, ps, qty, orders)


def realistic(sym):
    days, saved = D[sym], [(d, d.expiry) for d in D[sym]]
    exps = REAL_EXP[sym]
    for d in days:                            # app rule: nearest listed expiry, never on its expiry day
        d.expiry = next(e for e in exps if (e - d.d).days >= 1)
    lot0 = E.META[sym]["lot"]
    E.I.charges = charges_hist
    out = []
    try:
        for d in days:
            CUR["d"] = d.d
            E.META[sym]["lot"] = lot_on(sym, d.d)
            out += both(sym, days=[d])
    finally:
        E.META[sym]["lot"] = lot0
        E.I.charges = ORIG_CH
        for d, e in saved:
            d.expiry = e
    return out


# same per-day loop with today's specs, so the comparison isolates the history change
def per_day_today(sym):
    out = []
    for d in D[sym]:
        out += both(sym, days=[d])
    return out


TODAY = [t for s in SYMS for t in per_day_today(s)]
REAL = [t for s in SYMS for t in realistic(s)]
OUT["specs_today"] = stats(TODAY)
OUT["specs_real"] = stats(REAL)
OUT["specs_real_by_sym"] = groups(REAL, lambda t: t["sym"])
show("per-day loop, today's contract specs", OUT["specs_today"])
show("real expiries + lots + charges of the time", OUT["specs_real"])
json.dump(OUT, open("round6_checks.json", "w"), default=str, indent=1)
print("wrote round6_checks.json")
