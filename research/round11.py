"""Round 11 — rules from the web research that our data can test now (1-minute index 2023-26, VIX, daily OI).
A. Time-of-day audit: return and range by 30-min slot and weekday; the blog claims (2:30-3:30 bullish, 12:30-13:30 dead, >1% open reverts).
B. Last-half-hour intraday momentum (Gao/Baltussen): at 15:00 take the sign of open->15:00; exit 15:28. Variants + option P&L.
C. Initial-balance (09:15-10:15) rules: % of days whose high/low is set in the first hour; IB-width-filtered extension trade.
D. VIX-percentile gate on the two rules (trade only when VIX above/below its rolling 1-year 75th / 25th percentile).
E. Expiry-day afternoon: realised vol 15:00-15:30 on expiry vs other days; the 'be flat by 2 pm' vs 'sell 2-3 pm' question, and the
   previous-day max-OI strike crossing after 13:30 on expiry days (daily OI from bhavcopy).
"""
import json, os
from datetime import date, timedelta
import numpy as np
import pandas as pd

os.environ.setdefault("FNO_CALIB", "1")
import engine as E
import m1

SYMS = ("NIFTY", "BANKNIFTY")
OUT = {}
M = {s: m1.load(s) for s in SYMS}
DATES = [d.d for d in M["NIFTY"]]
SPLIT = DATES[-120]
vixd = pd.read_csv("data/INDIAVIX_1d.csv.gz", parse_dates=["ts"]).set_index("ts")["close"]
vixd.index = vixd.index.date


def slot(m):
    return f"{m // 60:02d}:{m % 60:02d}"


ONLY_E = os.environ.get("ONLY_E")
if ONLY_E:
    OUT = json.load(open("round11.json")) if os.path.exists("round11.json") else {}
# ---------------------------------------------------------------- A. time-of-day audit
rows = []
for dd in ([] if ONLY_E else M["NIFTY"]):
    o0 = dd.o[0]
    for a in range(555, 930, 30):
        b = min(a + 30, 930)
        sel = (dd.m >= a) & (dd.m < b)
        if not sel.any():
            continue
        seg_o, seg_c = dd.o[sel][0], dd.c[sel][-1]
        rows.append({"date": dd.d, "slot": slot(a), "ret": (seg_c / seg_o - 1) * 100, "rng": (dd.h[sel].max() - dd.l[sel].min()) / o0 * 100,
                     "dow": dd.d.weekday(), "open_move": (dd.c[np.searchsorted(dd.m, 570) - 1] / o0 - 1) * 100 if dd.m.max() > 570 else 0})
A = pd.DataFrame(rows)
tod = A.groupby("slot").agg(avg_ret_pct=("ret", "mean"), green_pct=("ret", lambda x: (x > 0).mean() * 100), avg_range_pct=("rng", "mean"), n=("ret", "size")).round(3)
OUT["A_slots"] = tod.reset_index().to_dict("records")
day = A[A.slot == "09:15"].copy()
full = pd.DataFrame([{"date": dd.d, "ret": (dd.c[-1] / dd.o[0] - 1) * 100, "dow": dd.d.weekday()} for dd in M["NIFTY"]])
OUT["A_weekday"] = full.groupby("dow").agg(avg_ret_pct=("ret", "mean"), green_pct=("ret", lambda x: (x > 0).mean() * 100), n=("ret", "size")).round(3).reset_index().to_dict("records")
# >1% move in the first 15 min: does the rest of the day revert?
big = []
for dd in M["NIFTY"]:
    i = int(np.searchsorted(dd.m, 570)) - 1
    if i < 5:
        continue
    mv = (dd.c[i] / dd.o[0] - 1) * 100
    if abs(mv) >= 0.7:
        rest = (dd.c[-1] / dd.c[i] - 1) * 100
        big.append({"date": dd.d, "open_move": mv, "rest": rest, "reverted": np.sign(rest) != np.sign(mv)})
big = pd.DataFrame(big)
OUT["A_big_open"] = {"days": int(len(big)), "reverted_pct": round(float(big.reverted.mean() * 100), 1) if len(big) else None,
                     "avg_rest_same_sign_pct": round(float((big.rest * np.sign(big.open_move)).mean()), 3) if len(big) else None}
print("A slots:\n", tod.to_string()); print("A weekday:", OUT["A_weekday"]); print("A big open (>=0.7% by 09:30):", OUT["A_big_open"], flush=True)


# ---------------------------------------------------------------- B. last-half-hour momentum
def option_pnl(dd, side, i_in, i_out, capital=200000):
    px_in, px_out = dd.c[i_in], dd.c[i_out]
    p_in, p_out = m1.option_trade(dd, side, i_in, i_out, px_in, px_out, itm=1)
    risk_pts = 0.3 * dd.scale * 25            # notional: size as 1% risk on a 0.3-ATR-ish stop is unfair; use fixed 1 lot instead
    pnl, lots = m1.pnl_rupees(dd, p_in, p_out, risk_pts, fixed_lots=1)
    return pnl


def lhm(days, t_in=900, t_out=928, min_move=0.0, use_first=False, vix_gate=None):
    out = []
    for dd in days:
        i_in = int(np.searchsorted(dd.m, t_in)); i_out = min(int(np.searchsorted(dd.m, t_out)), len(dd.c) - 1)
        if i_in >= len(dd.c) - 2:
            continue
        ref = dd.c[int(np.searchsorted(dd.m, 585)) - 1] if use_first else dd.c[i_in]
        mv = (ref / dd.o[0] - 1) * 100
        if abs(mv) < min_move:
            continue
        if vix_gate and not vix_gate(dd):
            continue
        side = 1 if mv > 0 else -1
        pts = side * (dd.c[i_out] - dd.c[i_in]) / dd.scale
        pnl = option_pnl(dd, side, i_in, i_out)
        out.append({"date": dd.d, "side": side, "pts": pts, "pnl": pnl if pnl is not None else 0.0})
    return pd.DataFrame(out)


def summ(df, name):
    if df.empty:
        print(name, "no trades"); return {}
    tr = df[df.date < SPLIT]; te = df[df.date >= SPLIT]
    r = {"n": len(df), "win_pct": round(float((df.pts > 0).mean() * 100), 1), "avg_pts": round(float(df.pts.mean()), 1),
         "pts_total": round(float(df.pts.sum())), "pnl_1lot": round(float(df.pnl.sum())), "pnl_train": round(float(tr.pnl.sum())), "pnl_unseen120": round(float(te.pnl.sum())),
         "years": {y: round(float(df[df.date.map(lambda d: d.year) == y].pnl.sum())) for y in (2023, 2024, 2025, 2026)}}
    print(f"{name:58s} n{r['n']:4d} win {r['win_pct']}% avg {r['avg_pts']:+.1f} pts  1-lot ₹{r['pnl_1lot']/1000:+.1f}k (train {r['pnl_train']/1000:+.1f}k, unseen {r['pnl_unseen120']/1000:+.1f}k) {r['years']}", flush=True)
    return r


OUT["B"] = {}
for s in SYMS:
    OUT["B"][s] = {
        "15:00 sign of open->15:00, exit 15:28": summ(lhm(M[s]), f"{s} LHM 15:00"),
        "same, only if |move| >= 0.3%": summ(lhm(M[s], min_move=0.3), f"{s} LHM |move|>=0.3%"),
        "same, only if |move| >= 0.6%": summ(lhm(M[s], min_move=0.6), f"{s} LHM |move|>=0.6%"),
        "14:30 entry": summ(lhm(M[s], t_in=870), f"{s} LHM 14:30"),
        "first half-hour sign, 15:00 entry": summ(lhm(M[s], use_first=True), f"{s} first-30min sign"),
        "fade instead (opposite side)": summ(lhm(M[s]).assign(pts=lambda x: -x.pts, pnl=0.0), f"{s} fade"),
    }

# ---------------------------------------------------------------- C. initial balance
OUT["C"] = {}
for s in SYMS:
    hi_first, lo_first, either, rows = 0, 0, 0, []
    for dd in M[s]:
        i = int(np.searchsorted(dd.m, 615))
        if i < 30 or i >= len(dd.c) - 30:
            continue
        ibh, ibl = dd.h[:i].max(), dd.l[:i].min()
        dh, dl = dd.h.max(), dd.l.min()
        hf, lf = dh <= ibh, dl >= ibl
        hi_first += hf; lo_first += lf; either += (hf or lf)
        ib = (ibh - ibl) / dd.scale
        atr = dd.atr_d / dd.scale if dd.atr_d else 150
        # extension trade: after 10:15, first close beyond IB high/low; stop IB midpoint; target 0.8*IB beyond; exit 15:15
        side, k_in = 0, None
        for k in range(i, len(dd.c)):
            if dd.m[k] + 1 > 915:
                break
            if dd.c[k] > ibh:
                side, k_in = 1, k; break
            if dd.c[k] < ibl:
                side, k_in = -1, k; break
        if side:
            entry = dd.c[k_in]; stop = (ibh + ibl) / 2; tgt = entry + side * 0.8 * (ibh - ibl)
            x, k_out, why = None, len(dd.c) - 1, "sq"
            for k in range(k_in + 1, len(dd.c)):
                adv, fav = (dd.l[k], dd.h[k]) if side > 0 else (dd.h[k], dd.l[k])
                if side * (adv - stop) <= 0:
                    x, k_out, why = stop, k, "stop"; break
                if side * (fav - tgt) >= 0:
                    x, k_out, why = tgt, k, "target"; break
                if dd.m[k] + 1 >= 915:
                    x, k_out, why = dd.c[k], k, "sq"; break
            if x is None:
                x = dd.c[-1]
            pts = side * (x - entry) / dd.scale
            p_in, p_out = m1.option_trade(dd, side, k_in, k_out, entry, x, itm=1)
            pnl, lots = m1.pnl_rupees(dd, p_in, p_out, abs(entry - stop))
            rows.append({"date": dd.d, "ib_atr": ib / atr, "pts": pts, "pnl": pnl or 0.0, "why": why, "narrow": ib / atr <= 0.5, "medium": 0.33 <= ib / atr <= 0.5})
    n = len(M[s])
    R = pd.DataFrame(rows)
    def blk(x, name):
        if x.empty: return {}
        tr, te = x[x.date < SPLIT], x[x.date >= SPLIT]
        r = {"n": len(x), "win_pct": round(float((x.pts > 0).mean() * 100), 1), "pnl": round(float(x.pnl.sum())), "train": round(float(tr.pnl.sum())), "unseen120": round(float(te.pnl.sum())),
             "years": {y: round(float(x[x.date.map(lambda d: d.year) == y].pnl.sum())) for y in (2023, 2024, 2025, 2026)}}
        print(f"{name:58s} n{r['n']:4d} win {r['win_pct']}%  ₹{r['pnl']/1000:+.1f}k (train {r['train']/1000:+.1f}k, unseen {r['unseen120']/1000:+.1f}k) {r['years']}", flush=True)
        return r
    OUT["C"][s] = {"high_set_in_first_hour_pct": round(hi_first / n * 100, 1), "low_set_in_first_hour_pct": round(lo_first / n * 100, 1), "either_pct": round(either / n * 100, 1),
                   "ib_extension_all": blk(R, f"{s} IB extension, any IB"), "ib_extension_medium_ib": blk(R[R.medium], f"{s} IB extension, IB 1/3-1/2 ATR"),
                   "ib_extension_narrow_ib": blk(R[R.narrow], f"{s} IB extension, IB <= 1/2 ATR")}
    print(s, "high/low in first hour:", OUT["C"][s]["high_set_in_first_hour_pct"], OUT["C"][s]["low_set_in_first_hour_pct"], "either", OUT["C"][s]["either_pct"], flush=True)

# ---------------------------------------------------------------- D. VIX percentile gate on the rules
W = dict(mult=1.75, stop_atr=2.0, rr=4.0, vwap=1, max_trades=1, vix_min=11, last_entry=870, otm=-1, be=0, ctx=-9, rmin=1.0, rmax=2.5)
CA = {'rr': None, 'be': 1.0, 'tstop': 45, 'ctx': -9, 'otm': -1, 'last_entry': 780, 'max_trades': 1, 'rmin': 1.0, 'rmax': 3.0, 'vix_min': 11, 'vix_max': 22, 'mode': 'break', 'trail': ''}
BUD = {date(2023, 2, 1), date(2024, 2, 1), date(2024, 7, 23), date(2025, 2, 1), date(2026, 2, 1)}
vs = pd.Series(vixd.values, index=pd.to_datetime(list(vixd.index)))
pct = vs.rolling(250, min_periods=120).rank(pct=True).shift(1)
PCT = {d.date(): float(v) for d, v in pct.items() if not np.isnan(v)}
D5 = {}
for s in SYMS:
    days = [d for d in E.load_days(s) if d.d not in BUD]
    for d in days:
        if s == "NIFTY" and (d.expiry - d.d).days <= 1:
            d.expiry = E.expiry_for(d.d + timedelta(days=1), "weekly")
    D5[s] = days


def rules(days_by_sym):
    out = []
    for s, days in days_by_sym.items():
        tr = [dict(t, strat="noise") for t in E.run(days, s, "noise", W)] + [dict(t, strat="camarilla") for t in E.run(days, s, "camarilla", CA)]
        tr.sort(key=lambda t: (t["date"], t["in"])); busy = {}
        for t in tr:
            if busy.get(t["date"], 0) > t["in"]: continue
            out.append(t); busy[t["date"]] = t["out"]
    return out


def ev(tr, name):
    p = np.array([t["pnl"] for t in tr]); pos, neg = p[p > 0].sum(), -p[p <= 0].sum()
    r = {"n": len(tr), "net": round(float(p.sum())), "train": round(float(sum(t["pnl"] for t in tr if t["date"] < SPLIT))), "unseen120": round(float(sum(t["pnl"] for t in tr if t["date"] >= SPLIT))),
         "pf": round(float(pos / neg), 2) if neg else None, "years": {y: round(float(sum(t["pnl"] for t in tr if t["date"].year == y))) for y in (2023, 2024, 2025, 2026)}}
    print(f"{name:58s} n{r['n']:4d} ₹{r['net']/1000:+.1f}k pf {r['pf']} (train {r['train']/1000:+.1f}k, unseen {r['unseen120']/1000:+.1f}k) {r['years']}", flush=True)
    return r


base = rules(D5)
OUT["D"] = {"rules": ev(base, "rules, all days with a VIX percentile")}
for name, f in (("VIX pct >= 0.75 only", lambda d: PCT.get(d, 0) >= 0.75), ("VIX pct >= 0.5 only", lambda d: PCT.get(d, 0) >= 0.5), ("VIX pct <= 0.5 only", lambda d: PCT.get(d, 1) <= 0.5),
                ("VIX pct <= 0.25 only", lambda d: PCT.get(d, 1) <= 0.25), ("VIX level < 14 only", lambda d: vixd.get(d, 99) < 14), ("VIX level >= 14 only", lambda d: vixd.get(d, 0) >= 14)):
    OUT["D"][name] = ev([t for t in base if f(t["date"])], "rules, " + name)
# rules by VIX percentile bucket
buck = {}
for t in base:
    b = "no pct" if t["date"] not in PCT else f"{int(PCT[t['date']] * 4) * 25}-{int(PCT[t['date']] * 4) * 25 + 25}"
    buck.setdefault(b, []).append(t["pnl"])
OUT["D"]["by_bucket"] = {k: {"n": len(v), "net": round(float(sum(v))), "per_trade": round(float(np.mean(v)))} for k, v in sorted(buck.items())}
print("D by VIX percentile bucket:", OUT["D"]["by_bucket"], flush=True)

# ---------------------------------------------------------------- E. expiry-day afternoon
bh = pd.read_csv("data/OPT_bhavcopy.csv.gz", parse_dates=["date", "expiry"])
bh["date"], bh["expiry"] = bh["date"].dt.date, bh["expiry"].dt.date
EXP = {s: set(bh[bh.sym == s].expiry) for s in SYMS}
OUT["E"] = {}
for s in SYMS:
    rv_e, rv_o, last90_e, last90_o = [], [], [], []
    for dd in M[s]:
        sel = dd.m >= 900
        if sel.sum() < 20:
            continue
        r = np.std(np.diff(np.log(dd.c[sel]))) * 100 * np.sqrt(375)
        sel2 = dd.m >= 840
        mv = abs(dd.c[-1] / dd.c[sel2][0] - 1) * 100
        (rv_e if dd.d in EXP[s] else rv_o).append(r)
        (last90_e if dd.d in EXP[s] else last90_o).append(mv)
    OUT["E"][s] = {"rv_15:00-15:30_expiry": round(float(np.mean(rv_e)), 2), "rv_15:00-15:30_other": round(float(np.mean(rv_o)), 2),
                   "abs_move_14:00-close_expiry_pct": round(float(np.mean(last90_e)), 3), "abs_move_14:00-close_other_pct": round(float(np.mean(last90_o)), 3), "expiry_days": len(rv_e)}
    # max-OI strike crossing after 13:30 on expiry days (previous day's OI)
    trades = []
    for dd in M[s]:
        if dd.d not in EXP[s]:
            continue
        prev = max((d for d in set(bh[bh.sym == s].date) if d < dd.d), default=None)
        if prev is None:
            continue
        g = bh[(bh.sym == s) & (bh.date == prev) & (bh.expiry == dd.d)]
        if g.empty:
            continue
        oi = g.groupby("strike").oi.sum()
        i0 = int(np.searchsorted(dd.m, 810))
        spot = dd.c[i0]
        near = oi[np.abs(oi.index.values / spot - 1) <= 0.015]
        if near.empty:
            continue
        k = near.idxmax()
        if near.max() < 2 * near.drop(k).median() if len(near) > 1 else True:
            continue
        side, k_in = 0, None
        for j in range(i0 + 1, len(dd.c)):
            if dd.m[j] + 1 > 905:
                break
            if dd.c[j - 1] <= k < dd.c[j]:
                side, k_in = 1, j; break
            if dd.c[j - 1] >= k > dd.c[j]:
                side, k_in = -1, j; break
        if not side:
            continue
        entry = dd.c[k_in]; k_out = min(int(np.searchsorted(dd.m, 915)), len(dd.c) - 1)
        stop_pts = 0.3 * entry / 100 * 0.5
        x, why = dd.c[k_out], "sq"
        for j in range(k_in + 1, k_out + 1):
            if side * (dd.l[j] if side > 0 else dd.h[j]) <= side * (entry - side * stop_pts) * 1:
                pass
        pts = side * (x - entry) / dd.scale
        p_in, p_out = m1.option_trade(dd, side, k_in, k_out, entry, x, itm=0)
        pnl, lots = m1.pnl_rupees(dd, p_in, p_out, 40 * dd.scale, fixed_lots=1)
        trades.append({"date": dd.d, "pts": pts, "pnl": pnl or 0.0})
    T = pd.DataFrame(trades)
    OUT["E"][s]["max_oi_cross_after_1330"] = {"n": len(T), "win_pct": round(float((T.pts > 0).mean() * 100), 1) if len(T) else None, "avg_pts": round(float(T.pts.mean()), 1) if len(T) else None,
                                               "pnl_1lot_atm": round(float(T.pnl.sum())) if len(T) else None}
    print(s, "E:", OUT["E"][s], flush=True)
json.dump(OUT, open("round11.json", "w"), default=str, indent=1)
print("wrote round11.json")
