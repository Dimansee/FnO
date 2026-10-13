"""Round 14C — market-structure inputs that were NOT in rounds 9-10: cross-index agreement, heavyweight breadth,
Bank Nifty vs Nifty relative strength, a previous-day "flow" proxy, and expected (ATM straddle) vs realised move.

Baseline = the round 6 book (noise W + Camarilla CA, both indices, 1 trade per rule per day, one open position per index,
round6.both() merge). Option P&L via engine.trade_pnl (1 ITM, calibrated premiums, slippage, full charges), ₹2 lakh, 1 % risk.
Training = sessions before SPLIT, unseen = the last 120 sessions. Nothing is picked on the unseen half.
"Beats" = more net AND a better net/maxDD than the baseline on BOTH halves.

Each input is tested (a) as a FILTER on both rules through engine.run's p["filters"] (signal bar must agree with the trade
direction; the engine keeps scanning when it does not, exactly as round 10 did) plus its INVERSE (trade only on disagreement,
to show the input is not noise), and (b) where sensible as a standalone ENTRY trigger (state turns true -> buy at the close,
stop 2 x ATR clipped 1-2.5 ATR, target 4R, no trail, 1 trade per index-day, VIX >= 11, 1 ITM, entries 09:45-14:30).
Inputs:
 1. cross-index: at the signal bar the OTHER index is on the same side of its own VWAP (x_vwap), and also beyond its own
    noise band (x_nb, mult 1.75) / 15-min opening range (x_or). Aligned by date and bar time.
 2. heavyweight breadth: of RELIANCE HDFCBANK ICICIBANK INFY TCS SBIN AXISBANK BHARTIARTL LT ITC, how many are above their
    own session VWAP (breadthK_v) / up from their open (breadthK_o) at the signal bar; longs need >= K, shorts <= 10-K; K = 6/7/8.
 3. relative strength: own return from the open minus the other index's (rs_open) and its 30-min change (rs_30): trade only
    the index that is stronger in the trade's direction.
 4. flow proxy: there is NO daily FII/DII file in research/data (only 10 days of participant OI under data/live), so the
    "flow" is yesterday's close-to-close return (flow1) and the 3-session return (flow3) of the index itself. Said honestly.
 5. expected vs realised: yesterday's closing ATM straddle of the nearest expiry (NSE bhavcopy, Oct 2023 -> 1 Oct 2026),
    scaled to one session (straddle / sqrt(trading sessions to expiry)) = implied daily move; realised = session range so far
    at the signal bar; trade only when realised < 50/60/80 % of implied (room to run) vs > 100 % (already spent). Compared
    against the baseline restricted to the days that have a straddle (sub-baseline), since the bhavcopy covers 2023-10-04+.
 + a "k of n agree" vote across inputs 1,2,3(,5); + each input's standalone predictive power (next-30-min and to-15:15 index
   move by input bucket, half-hour bars 09:45-14:15, both indices, TRAINING days only).
Run from research/ with FNO_CALIB=1. Writes round14c.json; pipe stdout to round14c.log.
"""
import json, math, os, sys, time
from datetime import date

os.environ.setdefault("FNO_CALIB", "1")
import numpy as np
import pandas as pd

import engine as E

T0 = time.time()
SYMS = ("NIFTY", "BANKNIFTY")
OTHER = {"NIFTY": "BANKNIFTY", "BANKNIFTY": "NIFTY"}
RULES = ("noise", "camarilla")
STOCKS = ("RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK", "BHARTIARTL", "LT", "ITC")
W = dict(mult=1.75, stop_atr=2.0, rr=4.0, vwap=1, max_trades=1, vix_min=11, last_entry=870, otm=-1,
         be=0, ctx=-9, rmin=1.0, rmax=2.5)
CA = {'rr': None, 'be': 1.0, 'tstop': 45, 'ctx': -9, 'otm': -1, 'last_entry': 780, 'max_trades': 1, 'rmin': 1.0,
      'rmax': 3.0, 'vix_min': 11, 'vix_max': 22, 'mode': 'break', 'trail': ''}
D = {s: E.load_days(s) for s in SYMS}
DAYMAP = {s: {d.d: d for d in D[s]} for s in SYMS}
DATES = [d.d for d in D["NIFTY"]]
SPLIT = DATES[-120]
HALVES = ("train", "unseen")
OUT = {"split": str(SPLIT), "train_days": sum(d < SPLIT for d in DATES), "unseen_days": sum(d >= SPLIT for d in DATES)}
print(f"days {len(DATES)} {DATES[0]}..{DATES[-1]}  training {OUT['train_days']}  unseen {SPLIT}.. ({OUT['unseen_days']})  (load {time.time()-T0:.0f}s)", flush=True)


def sgn(x):
    return 1 if x > 0 else -1 if x < 0 else 0


# ---------------------------------------------------------------- input arrays, attached to every Day as d.mkt
def breadth_table():
    """(date, tod) -> (# above own session VWAP, # up from own open, # stocks with a bar)."""
    acc = None
    for s in STOCKS:
        df = E._read(f"{s}_5m_upstox")
        df = df[(df.index.time >= pd.Timestamp("09:15").time()) & (df.index.time <= pd.Timestamp("15:25").time())]
        day = df.index.date
        tp = (df["high"] + df["low"] + df["close"]) / 3
        pv = (tp * df["volume"]).groupby(day).cumsum()
        vv = df["volume"].groupby(day).cumsum().replace(0, np.nan)
        vwap = pv / vv
        dopen = df["open"].groupby(day).transform("first")
        x = pd.DataFrame({"av": (df["close"] > vwap).astype(int).values, "up": (df["close"] > dopen).astype(int).values, "n": 1},
                         index=pd.MultiIndex.from_arrays([day, df.index.hour * 60 + df.index.minute], names=["d", "tod"]))
        x = x[~x.index.duplicated()]
        acc = x if acc is None else acc.add(x, fill_value=0)
    return acc


def implied_table():
    """(sym, bhav date) -> {expiry: ATM straddle close}; the trade day then takes the nearest expiry >= trade day."""
    b = pd.read_csv("data/OPT_bhavcopy.csv.gz", parse_dates=["date", "expiry"])
    b["date"], b["expiry"] = b["date"].dt.date, b["expiry"].dt.date
    b = b[(b["close"] > 0)]
    out = {}
    for (d, sym, e), g in b.groupby(["date", "sym", "expiry"]):
        if sym not in SYMS or (e - d).days < 1:
            continue
        und = float(g["und"].dropna().iloc[0]) if g["und"].notna().any() else None
        if und is None:
            dd = DAYMAP[sym].get(d)
            und = dd.c[-1] if dd else None
        if und is None:
            continue
        k = g.iloc[(g["strike"] - und).abs().argsort().iloc[0]]["strike"]
        ce, pe = g[(g["strike"] == k) & (g["opt"] == "CE")], g[(g["strike"] == k) & (g["opt"] == "PE")]
        if len(ce) and len(pe):
            out.setdefault((sym, d), {})[e] = float(ce["close"].iloc[0] + pe["close"].iloc[0])
    return out


BR = breadth_table()
BR_IDX = {k: (int(r.av), int(r.up), int(r.n)) for k, r in zip(BR.index, BR.itertuples())}
IMPL = implied_table()
NB = {s: {d.d: E.noise_bands(d, W["mult"]) for d in D[s]} for s in SYMS}
DATE_POS = {d: n for n, d in enumerate(DATES)}
print(f"breadth rows {len(BR)} ({BR.index.get_level_values(0).min()}..{BR.index.get_level_values(0).max()}), "
      f"straddle days {len(IMPL)} (bhavcopy {min(k[1] for k in IMPL)}..{max(k[1] for k in IMPL)})  ({time.time()-T0:.0f}s)", flush=True)

n_em = {s: 0 for s in SYMS}
for s in SYMS:
    days = D[s]
    for n, d in enumerate(days):
        d.sym = s
        od = DAYMAP[OTHER[s]].get(d.d)
        m = len(d.t)
        nan = [math.nan] * m
        x = {"o_vwap": [0] * m, "o_nb": [0] * m, "o_or": [0] * m, "br_v": list(nan), "br_o": list(nan),
             "rs_open": list(nan), "rs_30": list(nan), "em": list(nan), "rng": list(nan)}
        if od is not None:
            oup, olo = NB[OTHER[s]][od.d]
            orh, orl = max(od.h[:3]), min(od.l[:3])                      # 15-min opening range, complete before 09:45
            for i, t in enumerate(d.t):
                oi = od.idx.get(t)
                if oi is None:
                    continue
                oc = od.c[oi]
                x["o_vwap"][i] = sgn(oc - od.vwap[oi])
                x["o_or"][i] = 1 if oc > orh else -1 if oc < orl else 0
                if not math.isnan(oup[oi]):
                    x["o_nb"][i] = 1 if oc > oup[oi] else -1 if oc < olo[oi] else 0
                x["rs_open"][i] = (d.c[i] / d.o[0] - 1) - (oc / od.o[0] - 1)
            for i in range(6, m):
                if not (math.isnan(x["rs_open"][i]) or math.isnan(x["rs_open"][i - 6])):
                    x["rs_30"][i] = x["rs_open"][i] - x["rs_open"][i - 6]
        for i, t in enumerate(d.t):
            r = BR_IDX.get((d.d, t))
            if r and r[2] >= 8:
                x["br_v"][i], x["br_o"][i] = r[0], r[1]
        # flow proxy: yesterday's close-to-close and 3-session return of the index itself
        d.flow1 = d.prev_close / days[n - 1].prev_close - 1 if n >= 1 else math.nan
        d.flow3 = d.prev_close / days[n - 3].prev_close - 1 if n >= 3 else math.nan
        # implied daily move from yesterday's closing ATM straddle, nearest expiry >= today, per session
        d.implied = math.nan
        prev = days[n - 1].d if n >= 1 else None
        tab = IMPL.get((s, prev)) if prev else None
        if tab:
            exps = sorted(e for e in tab if e >= d.d)
            if exps:
                e = exps[0]
                ntd = max(sum(1 for q in DATES if prev < q <= e), 1)
                d.implied = tab[e] / math.sqrt(ntd)
                n_em[s] += 1
        hi, lo = -1e18, 1e18
        for i in range(m):
            hi, lo = max(hi, d.h[i]), min(lo, d.l[i])
            x["rng"][i] = hi - lo
            if not math.isnan(d.implied):
                x["em"][i] = (hi - lo) / d.implied
        d.mkt = x
print(f"implied-move days: {n_em}  ({time.time()-T0:.0f}s)", flush=True)


# ---------------------------------------------------------------- filters (engine.FILTERS: fn(day, bar, side) -> bool)
def _br(d, i, s, k, key):
    v = d.mkt[key][i]
    return not math.isnan(v) and (v >= k if s > 0 else v <= 10 - k)


def _br_inv(d, i, s, k, key):
    v = d.mkt[key][i]
    return not math.isnan(v) and (v <= 10 - k if s > 0 else v >= k)


def _em(d, i, s, lo, hi):
    v = d.mkt["em"][i]
    return not math.isnan(v) and lo <= v < hi


NEW = {
    "x_vwap": lambda d, i, s: d.mkt["o_vwap"][i] == s,
    "x_nb": lambda d, i, s: d.mkt["o_vwap"][i] == s and d.mkt["o_nb"][i] == s,
    "x_or": lambda d, i, s: d.mkt["o_vwap"][i] == s and d.mkt["o_or"][i] == s,
    "inv_x_vwap": lambda d, i, s: d.mkt["o_vwap"][i] == -s,
    "inv_x_nb": lambda d, i, s: d.mkt["o_nb"][i] == -s,
    "inv_x_or": lambda d, i, s: d.mkt["o_or"][i] == -s,
    "rs_open": lambda d, i, s: s * (d.mkt["rs_open"][i] if not math.isnan(d.mkt["rs_open"][i]) else 0) > 0,
    "rs_30": lambda d, i, s: s * (d.mkt["rs_30"][i] if not math.isnan(d.mkt["rs_30"][i]) else 0) > 0,
    "inv_rs_open": lambda d, i, s: s * (d.mkt["rs_open"][i] if not math.isnan(d.mkt["rs_open"][i]) else 0) < 0,
    "inv_rs_30": lambda d, i, s: s * (d.mkt["rs_30"][i] if not math.isnan(d.mkt["rs_30"][i]) else 0) < 0,
    "flow1": lambda d, i, s: not math.isnan(d.flow1) and s * d.flow1 > 0,
    "flow3": lambda d, i, s: not math.isnan(d.flow3) and s * d.flow3 > 0,
    "inv_flow1": lambda d, i, s: not math.isnan(d.flow1) and s * d.flow1 < 0,
    "inv_flow3": lambda d, i, s: not math.isnan(d.flow3) and s * d.flow3 < 0,
    "em_lt50": lambda d, i, s: _em(d, i, s, 0, 0.5),
    "em_lt60": lambda d, i, s: _em(d, i, s, 0, 0.6),
    "em_lt80": lambda d, i, s: _em(d, i, s, 0, 0.8),
    "em_gt100": lambda d, i, s: _em(d, i, s, 1.0, 1e9),
    "em_any": lambda d, i, s: _em(d, i, s, 0, 1e9),
}
for k in (6, 7, 8):
    for key, suf in (("br_v", "v"), ("br_o", "o")):
        NEW[f"breadth{k}_{suf}"] = (lambda k, key: (lambda d, i, s: _br(d, i, s, k, key)))(k, key)
        NEW[f"inv_breadth{k}_{suf}"] = (lambda k, key: (lambda d, i, s: _br_inv(d, i, s, k, key)))(k, key)
NEW["rs_both"] = lambda d, i, s: NEW["rs_open"](d, i, s) and NEW["rs_30"](d, i, s)
E.FILTERS.update(NEW)


# ---------------------------------------------------------------- books and stats (round6.both / round14a.stats pattern)
def both(sym, w=W, ca=CA):
    tr = [dict(t, strat="noise", sym=sym) for t in E.run(D[sym], sym, "noise", w)] + \
         [dict(t, strat="camarilla", sym=sym) for t in E.run(D[sym], sym, "camarilla", ca)]
    tr.sort(key=lambda t: (t["date"], t["in"]))
    out, busy = [], {}
    for t in tr:
        if busy.get(t["date"], 0) > t["in"]:
            continue
        out.append(t)
        busy[t["date"]] = t["out"]
    return out


def book(extra=None, vote=None):
    w, ca = dict(W), dict(CA)
    if extra:
        w["filters"], ca["filters"] = list(extra), list(extra)
    if vote:
        w["vote"], ca["vote"] = vote, vote
    return [t for s in SYMS for t in both(s, w, ca)]


def half(tr):
    if not tr:
        return {"trades": 0, "net": 0, "per_trade": 0, "win": 0, "pf": None, "max_dd": 0, "net_per_dd": None}
    p = np.array([t["pnl"] for t in tr])
    daily = pd.Series(p, index=[t["date"] for t in tr]).groupby(level=0).sum().reindex(DATES, fill_value=0)
    eq = daily.cumsum().values
    dd = float((eq - np.maximum.accumulate(np.concatenate([[0], eq]))[1:]).min())
    pos, neg = p[p > 0].sum(), -p[p <= 0].sum()
    return {"trades": len(p), "net": round(float(p.sum())), "per_trade": round(float(p.mean())),
            "win": round(float((p > 0).mean() * 100), 1), "pf": round(float(pos / neg), 2) if neg else None,
            "max_dd": round(dd), "net_per_dd": round(float(p.sum()) / -dd, 2) if dd < 0 else None}


def stats(tr):
    return {"train": half([t for t in tr if t["date"] < SPLIT]), "unseen": half([t for t in tr if t["date"] >= SPLIT]),
            "by_rule": {r: {h: half([t for t in tr if t["strat"] == r and ((t["date"] < SPLIT) == (h == "train"))])["net"] for h in HALVES} for r in RULES},
            "by_sym": {s: {h: half([t for t in tr if t["sym"] == s and ((t["date"] < SPLIT) == (h == "train"))])["net"] for h in HALVES} for s in SYMS},
            "years": {y: round(float(sum(t["pnl"] for t in tr if t["date"].year == y))) for y in (2023, 2024, 2025, 2026)}}


def beats(s, b):
    return bool(all(s[h]["net"] > b[h]["net"] and (s[h]["net_per_dd"] or -9) > (b[h]["net_per_dd"] or -9) for h in HALVES))


def train_better(s, b):
    return bool(s["train"]["net"] > b["train"]["net"] and (s["train"]["net_per_dd"] or -9) > (b["train"]["net_per_dd"] or -9))


def show(name, s, b=None):
    t, u = s["train"], s["unseen"]
    flag = ""
    if b is not None:
        s["beats"], s["train_only"] = beats(s, b), train_better(s, b) and not beats(s, b)
        flag = "  <-- BEATS base" if s["beats"] else ("  (train better, unseen not)" if s["train_only"] else "")
    print(f"{name:34s} train n{t['trades']:4d} ₹{t['net']/1000:+7.1f}k pf {t['pf'] or 0:.2f} win {t['win']:4.1f}% dd ₹{-t['max_dd']/1000:3.0f}k n/dd {t['net_per_dd'] or 0:5.2f} | "
          f"unseen n{u['trades']:3d} ₹{u['net']/1000:+6.1f}k pf {u['pf'] or 0:.2f} win {u['win']:4.1f}% dd ₹{-u['max_dd']/1000:3.0f}k n/dd {u['net_per_dd'] or 0:5.2f} | "
          f"rule N/C ₹{s['by_rule']['noise']['train']/1000:+.0f}k/{s['by_rule']['camarilla']['train']/1000:+.0f}k{flag}", flush=True)


BASE = book()
B = stats(BASE)
OUT["baseline"] = B
print("\n=== BASELINE (round 6 book)")
show("baseline book", B)
print(f"  beats = net > train ₹{B['train']['net']:,} / unseen ₹{B['unseen']['net']:,} AND net/maxDD > {B['train']['net_per_dd']} / {B['unseen']['net_per_dd']}")


def has_em(t):
    return not math.isnan(DAYMAP[t["sym"]][t["date"]].implied)


B_EM = stats([t for t in BASE if has_em(t)])
OUT["baseline_em_days"] = B_EM
show("baseline, straddle days only", B_EM)

# ---------------------------------------------------------------- 1-5 as filters on the two rules
GROUPS = {
    "1 cross-index": ["x_vwap", "x_nb", "x_or", "inv_x_vwap", "inv_x_nb", "inv_x_or"],
    "2 breadth": [f"breadth{k}_{s}" for s in ("v", "o") for k in (6, 7, 8)] + ["inv_breadth7_v", "inv_breadth7_o"],
    "3 relative strength": ["rs_open", "rs_30", "rs_both", "inv_rs_open", "inv_rs_30"],
    "4 flow proxy": ["flow1", "flow3", "inv_flow1", "inv_flow3"],
    "5 expected vs realised": ["em_lt50", "em_lt60", "em_lt80", "em_gt100", "em_any"],
}
OUT["filters"] = {}
for grp, names in GROUPS.items():
    base = B_EM if grp.startswith("5") else B
    print(f"\n=== {grp} — as a FILTER on both rules" + ("  (vs the straddle-days sub-baseline)" if grp.startswith("5") else ""))
    for nm in names:
        s = stats(book([nm]))
        OUT["filters"][nm] = s
        show(nm, s, base)

# ---------------------------------------------------------------- the same inputs read on the baseline's OWN signal bars (no re-scan)
print("\n=== baseline trades split by the input at their signal bar (pass = input agrees; train net / unseen net, ₹ per trade)")
OUT["base_split"] = {}
for grp, names in GROUPS.items():
    for nm in names:
        if nm.startswith("inv_") or nm == "em_any":
            continue
        f = E.FILTERS[nm]
        rec = {}
        for lab in ("pass", "fail"):
            sel = [t for t in BASE if (not grp.startswith("5") or has_em(t)) and f(DAYMAP[t["sym"]][t["date"]], DAYMAP[t["sym"]][t["date"]].idx[t["in"] - 5], t["side"]) == (lab == "pass")]
            rec[lab] = {h: {"n": len(x), "net": round(sum(t["pnl"] for t in x)), "per": round(sum(t["pnl"] for t in x) / len(x)) if x else 0}
                        for h, x in (("train", [t for t in sel if t["date"] < SPLIT]), ("unseen", [t for t in sel if t["date"] >= SPLIT]))}
        OUT["base_split"][nm] = rec
        p, q = rec["pass"], rec["fail"]
        print(f"  {nm:14s} pass train n{p['train']['n']:4d} ₹{p['train']['net']/1000:+7.1f}k ({p['train']['per']:+5d}/t) unseen n{p['unseen']['n']:3d} ₹{p['unseen']['net']/1000:+6.1f}k ({p['unseen']['per']:+5d}/t) | "
              f"fail train n{q['train']['n']:4d} ₹{q['train']['net']/1000:+7.1f}k ({q['train']['per']:+5d}/t) unseen n{q['unseen']['n']:3d} ₹{q['unseen']['net']/1000:+6.1f}k ({q['unseen']['per']:+5d}/t)", flush=True)

# ---------------------------------------------------------------- votes
print("\n=== k of n agree (vote) on both rules")
OUT["votes"] = {}
for names in (["x_vwap", "breadth7_v", "rs_open"], ["x_vwap", "breadth7_v", "rs_open", "em_lt60"]):
    for k in range(2, len(names) + 1):
        key = f"{k} of {len(names)} [{','.join(names)}]"
        tr = book(vote=(k, names))
        s = stats(tr)
        OUT["votes"][key] = s
        show(key[:34], s, B)
        if "em_lt60" in names:        # em_lt60 is False before the bhavcopy starts: judge the straddle days against their own sub-baseline too
            s2 = stats([t for t in tr if has_em(t)])
            OUT["votes"][key + " (straddle days)"] = s2
            show("   straddle days vs sub-baseline", s2, B_EM)

# ---------------------------------------------------------------- standalone entry triggers
ENTRY = dict(stop_atr=2.0, rr=4.0, max_trades=1, vix_min=11, otm=-1, rmin=1.0, rmax=2.5, first_entry=585, last_entry=870)


def own_vwap(d, i, s):
    return s * (d.c[i] - d.vwap[i]) > 0


def own_nb(d, i, s):
    up, lo = NB[d.sym][d.d]
    b = up[i] if s > 0 else lo[i]
    return not math.isnan(b) and s * (d.c[i] - b) > 0


CONDS = {
    "e_xidx_nb": lambda d, i, s: own_vwap(d, i, s) and own_nb(d, i, s) and NEW["x_nb"](d, i, s),
    "e_xidx_vwap": lambda d, i, s: own_vwap(d, i, s) and NEW["x_vwap"](d, i, s) and NEW["x_or"](d, i, s),
    "e_breadth7": lambda d, i, s: own_vwap(d, i, s) and NEW["breadth7_v"](d, i, s),
    "e_breadth8": lambda d, i, s: own_vwap(d, i, s) and NEW["breadth8_v"](d, i, s),
    "e_breadth8_o": lambda d, i, s: own_vwap(d, i, s) and NEW["breadth8_o"](d, i, s),
    "e_rs": lambda d, i, s: own_vwap(d, i, s) and NEW["rs_both"](d, i, s),
    "e_vote3of4": lambda d, i, s: own_vwap(d, i, s) and sum(NEW[f](d, i, s) for f in ("x_vwap", "breadth7_v", "rs_open", "em_lt60")) >= 3,
    "e_vote4of4": lambda d, i, s: own_vwap(d, i, s) and all(NEW[f](d, i, s) for f in ("x_vwap", "breadth7_v", "rs_open", "em_lt60")),
}


def make_trigger(cond):
    def fn(d, j, p, st):
        for i in range(max(j, 1), len(d.t)):
            if not E._window_ok(d, i, p):
                if d.t[i] + 5 > p["last_entry"]:
                    return None
                continue
            for side in (1, -1):
                if cond(d, i, side) and not cond(d, i - 1, side):
                    return E._sig(d, i, side, d.c[i], d.c[i] - side * p["stop_atr"] * d.atr[i], p, "mkt")
        return None
    return fn


for nm, c in CONDS.items():
    E.STRATS[nm] = make_trigger(c)
print("\n=== standalone ENTRY triggers (state turns true -> buy at close; stop 2xATR, 4R, 1/day, VIX>=11, 1 ITM, 09:45-14:30)")
OUT["entries"] = {}
for nm in CONDS:
    tr = [dict(t, strat=nm, sym=s) for s in SYMS for t in E.run(D[s], s, nm, ENTRY)]
    s = stats(tr)
    s["by_rule"] = {"noise": {"train": 0}, "camarilla": {"train": 0}}
    OUT["entries"][nm] = s
    show(nm, s, B)
    # the same trigger added to the baseline book (one open position per index; book trades first, trigger fills the gaps)
    merged = []
    for sym in SYMS:
        allt = sorted([t for t in BASE if t["sym"] == sym] + [t for t in tr if t["sym"] == sym], key=lambda t: (t["date"], t["in"]))
        busy = {}
        for t in allt:
            if busy.get(t["date"], 0) > t["in"]:
                continue
            merged.append(t); busy[t["date"]] = t["out"]
    s2 = stats(merged)
    OUT["entries"][nm + " + book"] = s2
    show("   + baseline book", s2, B)

# ---------------------------------------------------------------- predictive power of each input (training days only)
print("\n=== predictive power, TRAINING days, half-hour bars 09:45-14:15: mean next-30-min / to-15:15 index move (bp), n, t-stat")
rows = []
for s in SYMS:
    for d in D[s]:
        if d.d >= SPLIT:
            continue
        ic = d.idx.get(910)
        if ic is None:
            continue
        x = d.mkt
        for i in range(5, len(d.t)):
            tt = d.t[i] + 5
            if tt % 30 != 15 or tt < 585 or tt > 855 or i + 6 >= len(d.t):
                continue
            c = d.c[i]
            mv = sgn(c - d.o[0])
            rows.append({"sym": s, "f30": (d.c[i + 6] / c - 1) * 1e4, "fc": (d.c[ic] / c - 1) * 1e4,
                         "o_vwap": x["o_vwap"][i], "o_nb": x["o_nb"][i], "br_v": x["br_v"][i], "br_o": x["br_o"][i],
                         "rs_open": sgn(x["rs_open"][i]) if not math.isnan(x["rs_open"][i]) else 0,
                         "rs_30": sgn(x["rs_30"][i]) if not math.isnan(x["rs_30"][i]) else 0,
                         "flow1": sgn(d.flow1) if not math.isnan(d.flow1) else 0, "flow3": sgn(d.flow3) if not math.isnan(d.flow3) else 0,
                         "em": x["em"][i], "mv": mv, "own_vwap": sgn(c - d.vwap[i])})
PR = pd.DataFrame(rows)


def bucket_table(df, key, buckets, signed_by=None, label=None):
    """mean forward move per bucket; `signed_by` multiplies the move by a direction column (so + = the input's direction)."""
    out = {}
    for s in SYMS:
        g = df[df.sym == s]
        for bname, mask in buckets(g).items():
            q = g[mask]
            if len(q) < 20:
                continue
            f30 = q.f30 * (q[signed_by] if signed_by else 1)
            fc = q.fc * (q[signed_by] if signed_by else 1)
            out[f"{s} {bname}"] = {"n": int(len(q)), "f30_bp": round(float(f30.mean()), 1), "t30": round(float(f30.mean() / (f30.std() / math.sqrt(len(q)))), 1),
                                  "fc_bp": round(float(fc.mean()), 1), "tc": round(float(fc.mean() / (fc.std() / math.sqrt(len(q)))), 1)}
    print(f"  {label or key}")
    for k, v in out.items():
        print(f"    {k:40s} n{v['n']:5d}  next30 {v['f30_bp']:+6.1f}bp (t {v['t30']:+4.1f})   to-15:15 {v['fc_bp']:+6.1f}bp (t {v['tc']:+4.1f})")
    return out


OUT["predictive"] = {}
OUT["predictive"]["o_vwap"] = bucket_table(PR, "o_vwap", lambda g: {"other above VWAP": g.o_vwap == 1, "other below VWAP": g.o_vwap == -1},
                                           label="1. other index vs its VWAP (raw move; + = up)")
OUT["predictive"]["o_vwap_agree"] = bucket_table(PR, "o_vwap", lambda g: {"other agrees with own VWAP side": (g.o_vwap == g.own_vwap) & (g.own_vwap != 0),
                                                                           "other disagrees": (g.o_vwap == -g.own_vwap) & (g.own_vwap != 0)},
                                                 signed_by="own_vwap", label="1. signed by OWN VWAP side: other agrees vs disagrees (+ = continuation)")
OUT["predictive"]["o_nb"] = bucket_table(PR, "o_nb", lambda g: {"other beyond its band (signed)": g.o_nb != 0}, signed_by="o_nb",
                                         label="1. other index beyond its noise band, move signed by that direction")
OUT["predictive"]["br_v"] = bucket_table(PR, "br_v", lambda g: {"breadth 0-2": g.br_v <= 2, "breadth 3-4": (g.br_v >= 3) & (g.br_v <= 4), "breadth 5": g.br_v == 5,
                                                                 "breadth 6-7": (g.br_v >= 6) & (g.br_v <= 7), "breadth 8-10": g.br_v >= 8},
                                         label="2. # heavyweights above own VWAP (raw move)")
OUT["predictive"]["br_o"] = bucket_table(PR, "br_o", lambda g: {"up-from-open 0-2": g.br_o <= 2, "up-from-open 3-4": (g.br_o >= 3) & (g.br_o <= 4), "up-from-open 5": g.br_o == 5,
                                                                 "up-from-open 6-7": (g.br_o >= 6) & (g.br_o <= 7), "up-from-open 8-10": g.br_o >= 8},
                                         label="2. # heavyweights up from their open (raw move)")
OUT["predictive"]["rs_open"] = bucket_table(PR, "rs_open", lambda g: {"this index stronger from open": g.rs_open == 1, "this index weaker": g.rs_open == -1},
                                            label="3. relative strength from the open (raw move)")
OUT["predictive"]["rs_30"] = bucket_table(PR, "rs_30", lambda g: {"RS rising last 30m": g.rs_30 == 1, "RS falling": g.rs_30 == -1},
                                          label="3. 30-min RS change (raw move)")
OUT["predictive"]["flow"] = bucket_table(PR, "flow1", lambda g: {"yesterday up": g.flow1 == 1, "yesterday down": g.flow1 == -1, "3-day up": g.flow3 == 1, "3-day down": g.flow3 == -1},
                                         label="4. flow proxy (raw move)")
OUT["predictive"]["em_signed"] = bucket_table(PR[PR.em.notna() & (PR.mv != 0)], "em",
                                              lambda g: {"realised < 50% implied": g.em < 0.5, "50-80%": (g.em >= 0.5) & (g.em < 0.8), "80-100%": (g.em >= 0.8) & (g.em < 1.0), "> 100%": g.em >= 1.0},
                                              signed_by="mv", label="5. realised range so far / implied daily move, move signed by the direction so far (+ = continuation)")
em_abs = PR[PR.em.notna()].assign(f30=lambda q: q.f30.abs(), fc=lambda q: q.fc.abs())
OUT["predictive"]["em_abs"] = bucket_table(em_abs, "em", lambda g: {"realised < 50% implied": g.em < 0.5, "50-80%": (g.em >= 0.5) & (g.em < 0.8), "80-100%": (g.em >= 0.8) & (g.em < 1.0), "> 100%": g.em >= 1.0},
                                           label="5. the same, |move| (does 'room to run' mean bigger moves?)")
OUT["predictive"]["implied_check"] = {s: {"median_implied_pts": round(float(np.nanmedian([d.implied for d in D[s]])), 1),
                                          "median_realised_full_range_pts": round(float(np.median([max(d.h) - min(d.l) for d in D[s] if not math.isnan(d.implied)])), 1),
                                          "median_ratio_full_day": round(float(np.nanmedian([(max(d.h) - min(d.l)) / d.implied for d in D[s]])), 2)} for s in SYMS}
print("  implied-move sanity:", OUT["predictive"]["implied_check"])
print(f"  breadth coverage: {np.mean(~PR.br_v.isna())*100:.0f}% of bars; cross-index {np.mean(PR.o_vwap != 0)*100:.0f}%; straddle {np.mean(PR.em.notna())*100:.0f}%")

# ---------------------------------------------------------------- verdicts
print("\n=== VERDICTS (beats = more net AND better net/maxDD than the baseline on BOTH halves)")
V = {}
for sec in ("filters", "votes", "entries"):
    ks = [k for k, v in OUT[sec].items() if v.get("beats")]
    tb = [k for k, v in OUT[sec].items() if v.get("train_only")]
    V[sec] = {"beats_both": ks, "train_only": tb}
    print(f"  {sec}: beats on both: {ks or 'none'};  train better only: {tb or 'none'}")
OUT["verdicts"] = V
json.dump(OUT, open("round14c.json", "w"), default=str, indent=1)
print(f"wrote round14c.json ({time.time()-T0:.0f}s)")
