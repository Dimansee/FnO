"""Round 31 (Oct 2026): option SELLING on real NSE prices - the 9:20-style short straddle with a stop per leg, short strangles and
iron condors at the India-VIX expected move, intraday and held to the weekly expiry. Real daily option OHLC (NSE F&O bhavcopy,
Oct 2023 - Oct 2026, Nifty and Bank Nifty). Each leg is independent, so daily data is enough: a leg's stop is hit if that day's high
reaches entry x (1 + stop); otherwise it is bought back at the close. Sells fill 2% below the open (first-print haircut), stop
buy-backs 2% above the stop price, wings bought 2% above the open. Current lot sizes (65 / 30). Train Oct 2023 - Dec 2025, test 2026.
Run: python round31.py -> round31_results.json"""
import os, sys, json, itertools
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
from fno import indicators as I
import round26_harness as H
HAIR, SLIPB = 0.02, 0.02
LOT = {"NIFTY": 65, "BANKNIFTY": 30}; STEP = {"NIFTY": 50, "BANKNIFTY": 100}
BH = pd.read_csv(os.path.join(HERE, "data", "OPT_bhavcopy.csv.gz"))
BH = BH[(BH.open > 0)]
VIXD = H.load("INDIAVIX").close.groupby(lambda t: t.date()).first()
def chain(sym):
    b = BH[BH.sym == sym].copy(); b["date"] = pd.to_datetime(b.date).dt.date; b["expiry"] = pd.to_datetime(b.expiry).dt.date
    return {k: g.set_index(["strike", "opt"]) for k, g in b.groupby(["date", "expiry"])}, sorted(b.date.unique())
def opens(sym):
    c = H.load(sym); g = c.groupby(c.index.date); return g.open.first(), g.close.last()

def sell_leg(row, stop):
    entry = row.open * (1 - HAIR)
    if stop and row.high >= entry * (1 + stop):
        exitp = entry * (1 + stop) * (1 + SLIPB); hit = True
    else:
        exitp = row.close; hit = False
    return entry, exitp, hit
def buy_leg(row):
    return row.open * (1 + SLIPB), row.close

def intraday(sym, mode, stop, k_em=0.0, wing=None, skip_0dte=False, expiry_only=False):
    CH, days = chain(sym); op, _ = opens(sym); lot = LOT[sym]; step = STEP[sym]; out = []
    for d in days:
        exps = sorted(e for (dd, e) in CH if dd == d and e >= d)
        if not exps: continue
        e = exps[1] if (skip_0dte and exps[0] == d and len(exps) > 1) else exps[0]
        if expiry_only and e != d: continue
        if sym == "BANKNIFTY" and expiry_only is False and (e - d).days > 7: pass
        g = CH[(d, e)]; s0 = op.get(d)
        if s0 is None or d not in VIXD.index: continue
        em = s0 * VIXD[d] / 100 / np.sqrt(252) * k_em          # one-day expected move x k
        kc = round((s0 + em) / step) * step; kp = round((s0 - em) / step) * step
        legs = [("CE", kc, "S"), ("PE", kp, "S")]
        if wing:
            w = s0 * VIXD[d] / 100 / np.sqrt(252) * wing
            legs += [("CE", round((s0 + w) / step) * step, "B"), ("PE", round((s0 - w) / step) * step, "B")]
        try:
            rows = [(o, k, side, g.loc[(k, o)]) for o, k, side in legs]
        except KeyError:
            continue
        pnl, hits = 0.0, 0
        for o, k, side, r in rows:
            if isinstance(r, pd.DataFrame): r = r.iloc[0]
            if side == "S":
                a, b, hit = sell_leg(r, stop); hits += hit
                pnl += (a - b) * lot - I.charges(b, a, lot)
            else:
                a, b = buy_leg(r); pnl += (b - a) * lot - I.charges(a, b, lot)
        out.append({"sym": sym, "date": str(d), "dte": (e - d).days, "pnl": pnl, "stops": hits})
    return pd.DataFrame(out)

def weekly(sym, k_em, stop, wing=None):
    """Enter at the open of the first session after an expiry, strikes at +/- k x the expected move to expiry, hold to expiry close,
    leg stop checked against each day's high. Nifty only (weekly)."""
    CH, days = chain(sym); op, _ = opens(sym); lot = LOT[sym]; step = STEP[sym]; out = []
    allexp = sorted({e for (_, e) in CH})
    for e_prev, e in zip(allexp, allexp[1:]):
        dd = [d for d in days if e_prev < d <= e]
        if len(dd) < 2 or dd[0] not in VIXD.index: continue
        d0 = dd[0]; s0 = op.get(d0)
        if s0 is None or (d0, e) not in CH: continue
        em = s0 * VIXD[d0] / 100 * np.sqrt(len(dd) / 252) * k_em
        legs = [("CE", round((s0 + em) / step) * step, "S"), ("PE", round((s0 - em) / step) * step, "S")]
        if wing:
            w = s0 * VIXD[d0] / 100 * np.sqrt(len(dd) / 252) * wing
            legs += [("CE", round((s0 + w) / step) * step, "B"), ("PE", round((s0 - w) / step) * step, "B")]
        pnl, ok = 0.0, True
        for o, k, side in legs:
            path = [CH[(d, e)].loc[(k, o)] if (d, e) in CH and (k, o) in CH[(d, e)].index else None for d in dd]
            if path[0] is None or path[-1] is None: ok = False; break
            path = [p.iloc[0] if isinstance(p, pd.DataFrame) else p for p in path]
            if side == "S":
                entry = path[0].open * (1 - HAIR); exitp = path[-1].close
                for p in path:
                    if p is not None and stop and p.high >= entry * (1 + stop):
                        exitp = entry * (1 + stop) * (1 + SLIPB); break
                pnl += (entry - exitp) * lot - I.charges(exitp, entry, lot)
            else:
                a = path[0].open * (1 + SLIPB); b = path[-1].close; pnl += (b - a) * lot - I.charges(a, b, lot)
        if ok: out.append({"sym": sym, "date": str(d0), "expiry": str(e), "days": len(dd), "pnl": pnl})
    return pd.DataFrame(out)

def stats(df):
    if df.empty: return {"n": 0}
    s = df.sort_values("date").pnl; eq = s.cumsum()
    w, l = s[s > 0].sum(), -s[s <= 0].sum()
    return dict(n=int(len(s)), net=round(float(s.sum())), pf=round(float(w / l), 2) if l else None, win=round(float((s > 0).mean() * 100)),
                worst=round(float(s.min())), worst5=round(float(s.nsmallest(5).sum())), fall=round(float((eq - eq.cummax().clip(lower=0)).min())),
                without_best10=round(float(s.sum() - s.nlargest(10).sum())))
def split(df):
    df = df.copy(); df["y"] = df.date.str[:4]
    return dict(train=stats(df[df.y < "2026"]), y2026=stats(df[df.y == "2026"]), years={k: round(float(v)) for k, v in df.groupby("y").pnl.sum().items()})

if __name__ == "__main__":
    res = {}
    V = {}
    for sym in ("NIFTY", "BANKNIFTY"):
        for stop in (None, 0.25, 0.30, 0.50):
            V[f"{sym} straddle at the open, every day, stop {stop}"] = lambda s=sym, st=stop: intraday(s, "straddle", st)
            V[f"{sym} straddle, every day, no 0-DTE, stop {stop}"] = lambda s=sym, st=stop: intraday(s, "straddle", st, skip_0dte=True)
            V[f"{sym} straddle, expiry day only, stop {stop}"] = lambda s=sym, st=stop: intraday(s, "straddle", st, expiry_only=True)
        for k in (0.5, 1.0):
            for stop in (None, 0.5, 1.0):
                V[f"{sym} strangle at {k}x day's expected move, stop {stop}"] = lambda s=sym, kk=k, st=stop: intraday(s, "strangle", st, k_em=kk)
            V[f"{sym} iron condor {k}x / wings {k + 1}x, no stop"] = lambda s=sym, kk=k: intraday(s, "condor", None, k_em=kk, wing=kk + 1)
    for k in (0.75, 1.0, 1.5):
        for stop in (None, 1.0, 2.0):
            V[f"NIFTY weekly strangle at {k}x expected move to expiry, stop {stop}"] = lambda kk=k, st=stop: weekly("NIFTY", kk, st)
        V[f"NIFTY weekly iron condor {k}x / wings {k + 1}x"] = lambda kk=k: weekly("NIFTY", kk, None, wing=kk + 1)
    for nm, f in V.items():
        df = f(); res[nm] = split(df)
        print(nm.ljust(66), res[nm]["train"], "| 2026", res[nm]["y2026"], flush=True)
    json.dump(res, open(os.path.join(HERE, "round31_results.json"), "w"), indent=1)
