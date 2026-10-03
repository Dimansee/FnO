"""Policies on saved out-of-sample predictions, version 2: forward-move model, R model, and the
AI as a filter on the rule trades (or the rules as a filter on the AI).
usage: python ai_policy2.py _v2"""
import json, os, sys
from datetime import date, timedelta
import numpy as np
import pandas as pd

os.environ.setdefault("FNO_CALIB", "1")
import engine as E
import ai_train as T
from ai_data import SYMS, STOPS, RR

TAG = sys.argv[1] if len(sys.argv) > 1 else "_v2"
df = pd.read_parquet("ai_rows.parquet")
df["date"] = pd.to_datetime(df["date"]).dt.date
er = np.load(f"ai_er_oos{TAG}.npy")
ef = np.load(f"ai_ef_oos{TAG}.npy") if os.path.exists(f"ai_ef_oos{TAG}.npy") else np.full(len(df), np.nan)
D = {}
for s in SYMS:
    D[s] = E.load_days(s)
    for d in D[s]:
        if s == "NIFTY" and (d.expiry - d.d).days <= 1:
            d.expiry = E.expiry_for(d.d + timedelta(days=1), "weekly")
        T.DAYS[(s, d.d)] = d
dates = np.array(df["date"])
q = np.array([T.quarter(d) for d in dates])
quarters = sorted(set(q[q >= 20241]))
oos = q >= 20241
uniq = sorted(set(dates))
last120 = uniq[-120]
ROW = {(SYMS[s], d, int(i)): r for r, (s, d, i) in enumerate(zip(df["sym"].values, dates, df["i"].values))}
BUD = {date(2023, 2, 1), date(2024, 2, 1), date(2024, 7, 23), date(2025, 2, 1), date(2026, 2, 1)}
W = dict(mult=1.75, stop_atr=2.0, rr=4.0, vwap=1, max_trades=1, vix_min=11, last_entry=870, otm=-1, be=0, ctx=-9, rmin=1.0, rmax=2.5)
CA = {'rr': None, 'be': 1.0, 'tstop': 45, 'ctx': -9, 'otm': -1, 'last_entry': 780, 'max_trades': 1, 'rmin': 1.0,
      'rmax': 3.0, 'vix_min': 11, 'vix_max': 22, 'mode': 'break', 'trail': ''}


def summarise(tr, name, store):
    if not tr:
        print(f"{name:52s} no trades"); return None
    p = np.array([t["pnl"] for t in tr])
    pos, neg = p[p > 0].sum(), -p[p <= 0].sum()
    daily = pd.Series(p, index=[t["date"] for t in tr]).groupby(level=0).sum()
    eq = daily.cumsum().values
    dd = float((eq - np.maximum.accumulate(np.concatenate([[0], eq]))[1:]).min())
    yrs = {y: round(float(sum(t["pnl"] for t in tr if t["date"].year == y))) for y in (2024, 2025, 2026)}
    u = round(float(sum(t["pnl"] for t in tr if t["date"] >= last120)))
    print(f"{name:52s} n{len(p):4d} net ₹{p.sum()/1000:+7.1f}k win {100*(p>0).mean():4.1f}% pf {pos/neg if neg else 99:.2f} "
          f"maxDD ₹{dd/1000:.0f}k  {yrs}  last120 ₹{u/1000:+.1f}k", flush=True)
    r = {"name": name, "trades": len(p), "net": round(float(p.sum())), "win": round(float((p > 0).mean() * 100), 1),
         "pf": round(float(pos / neg), 2) if neg else None, "max_dd": round(dd), "years": yrs, "last120": u}
    store.append(r)
    return r


def fwd_policy(thr, stop_k, min_bars_apart=0):
    """side = sign of the expected move to the close when |E[move]| > thr ATR; fixed stop / 2x target."""
    e = np.full_like(er, np.nan)
    col = {1: STOPS.index(stop_k), -1: 3 + STOPS.index(stop_k)}
    ok = ~np.isnan(ef)
    e[ok & (ef > thr), col[1]] = ef[ok & (ef > thr)]
    e[ok & (ef < -thr), col[-1]] = -ef[ok & (ef < -thr)]
    return e


def agree_policy(thr_r, thr_f):
    """best R-combo must clear thr_r AND the forward model must point the same way by thr_f."""
    e = er.copy()
    best = np.nanargmax(np.where(np.isnan(e), -9, e), axis=1)
    side = np.where(best < 3, 1, -1)
    bad = np.isnan(ef) | (side * ef < thr_f)
    e[bad] = np.nan
    return e


def rules_trades(sym):
    days = [d for d in D[sym] if d.d not in BUD and d.d >= date(2024, 1, 1)]
    tr = [dict(t, strat="noise", sym=sym) for t in E.run(days, sym, "noise", W)] + \
         [dict(t, strat="camarilla", sym=sym) for t in E.run(days, sym, "camarilla", CA)]
    tr.sort(key=lambda t: (t["date"], t["in"]))
    out, busy = [], {}
    for t in tr:
        if busy.get(t["date"], 0) > t["in"]:
            continue
        out.append(t)
        busy[t["date"]] = t["out"]
    return out


def gate(trades, fn):
    """keep a rule trade only if fn(er_row, ef_row, side) is true; a dropped trade frees the slot (approximation: no re-fill)."""
    out = []
    for t in trades:
        day = T.DAYS[(t["sym"], t["date"])]
        i = day.idx.get(t["in"] - 5)
        r = ROW.get((t["sym"], t["date"], i))
        if r is None:
            out.append(t)          # outside the AI's decision window: keep
            continue
        if fn(er[r], ef[r], t["side"]):
            out.append(t)
    return out


if __name__ == "__main__":
    res = []
    for thr in (-0.05, 0.0, 0.05, 0.1, 0.15, 0.2):
        summarise(sum((T.simulate(df, er, thr, T.DAYS, price=True, sel=(q == Q)) for Q in quarters), []), f"R-model, fixed threshold {thr}", res)
    for thr in ((0.3, 0.75) if not np.isnan(ef).all() else ()):
        for k in (1.5, 2.0):
            e = fwd_policy(thr, k)
            summarise(sum((T.simulate(df, e, -9, T.DAYS, price=True, sel=(q == Q)) for Q in quarters), []), f"forward model |E[move]|>{thr} ATR, stop {k} ATR", res)
    for tr_, tf in (((0.1, 0.3), (0.2, 0.3)) if not np.isnan(ef).all() else ()):
        e = agree_policy(tr_, tf)
        summarise(sum((T.simulate(df, e, tr_, T.DAYS, price=True, sel=(q == Q)) for Q in quarters), []), f"R>{tr_} and forward agrees by {tf}", res)
    rules = rules_trades("NIFTY") + rules_trades("BANKNIFTY")
    summarise(rules, "RULES alone (2024->)", res)
    ecol = lambda e_, s: e_[2 if s > 0 else 5]  # noqa: E731
    for name, fn in (("rules, AI R-model >= -0.1", lambda e_, f_, s: np.isnan(ecol(e_, s)) or ecol(e_, s) >= -0.1),
                     ("rules, AI R-model >= -0.05", lambda e_, f_, s: np.isnan(ecol(e_, s)) or ecol(e_, s) >= -0.05),
                     ("rules, AI R-model >= 0.05", lambda e_, f_, s: np.isnan(ecol(e_, s)) or ecol(e_, s) >= 0.05),
                     ("rules, AI forward model agrees (any size)", lambda e_, f_, s: not np.isnan(f_) and s * f_ > 0),
                     ("rules, AI forward model agrees by 0.3 ATR", lambda e_, f_, s: not np.isnan(f_) and s * f_ > 0.3),
                     ("rules, AI R-model (stop 2 ATR) >= 0", lambda e_, f_, s: not np.isnan(e_[2 if s > 0 else 5]) and e_[2 if s > 0 else 5] >= 0),
                     ("rules, AI R-model (stop 2 ATR) >= 0.1", lambda e_, f_, s: not np.isnan(e_[2 if s > 0 else 5]) and e_[2 if s > 0 else 5] >= 0.1),
                     ("rules, AI not strongly against (fwd > -0.3)", lambda e_, f_, s: np.isnan(f_) or s * f_ > -0.3)):
        summarise(gate(rules, fn), name, res)
    json.dump(res, open(f"ai_policies2{TAG}.json", "w"), indent=1, default=str)
