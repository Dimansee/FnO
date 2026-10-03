"""Round 7c — "my stop-loss got hit and then price went to my target".

A. The app's own trades (noise band + Camarilla, round-6 rules), checked on real 1-minute prices:
   of the trades stopped out, how many later came back to the entry / +1R / +2R / the target the
   same day, and how far past the stop price went first.
B. Would a different stop have paid? Engine re-runs (option P&L, 1% risk sizing so a wider stop means
   fewer lots): stop 0.75x / 1.25x / 1.5x as far, stop only on a 5-minute CLOSE beyond it (with a
   hard stop 1.5x away), and booking profit at a fixed +80 / +100 / +120 Nifty points.
C. Any entry, any stop: entries every 15 minutes in both directions, stop S points, target 1-3R.
   How often "stop first, target later" happens on real prices vs a random market with the same
   volatility (shuffled 1-minute moves) - i.e. is there real stop hunting beyond chance?
D. Real option premiums (NSE contracts' own candles): premium stop -20/-30/-40 %, target +20...+100 %.
"""
import json, math, os
from datetime import date, timedelta
import numpy as np
import pandas as pd
from numba import njit

os.environ.setdefault("FNO_CALIB", "1")
import engine as E
import m1

SYMS = ("NIFTY", "BANKNIFTY")
OUT = {}
W = dict(mult=1.75, stop_atr=2.0, rr=4.0, vwap=1, max_trades=1, vix_min=11, last_entry=870, otm=-1, be=0, ctx=-9, rmin=1.0, rmax=2.5)
CA = {'rr': None, 'be': 1.0, 'tstop': 45, 'ctx': -9, 'otm': -1, 'last_entry': 780, 'max_trades': 1, 'rmin': 1.0,
      'rmax': 3.0, 'vix_min': 11, 'vix_max': 22, 'mode': 'break', 'trail': ''}
D5 = {s: E.load_days(s) for s in SYMS}
DATES = [d.d for d in D5["NIFTY"]]
SPLIT = DATES[-120]
BUD = {date(2023, 2, 1), date(2024, 2, 1), date(2024, 7, 23), date(2025, 2, 1), date(2026, 2, 1)}
bh = pd.read_csv("data/OPT_bhavcopy.csv.gz", usecols=["sym", "expiry"], parse_dates=["expiry"])
BN_EXP = set(bh[bh.sym == "BANKNIFTY"].expiry.dt.date)
d0 = DATES[0]
while d0 < date(2023, 10, 31):
    if d0.weekday() == (3 if d0 < date(2023, 9, 1) else 2):
        BN_EXP.add(d0)
    d0 += timedelta(days=1)
for d in D5["NIFTY"]:                                      # round-6 rule: roll to next week with 1 day left
    if (d.expiry - d.d).days <= 1:
        d.expiry = E.expiry_for(d.d + timedelta(days=1), "weekly")
SKIP = {"NIFTY": BUD, "BANKNIFTY": BUD}
DAYS = {s: [d for d in D5[s] if d.d not in SKIP[s]] for s in SYMS}

SIGS = {}
ORIG = dict(E.STRATS)


def capture(name, fn, stop_mult=1.0, tgt_pts=None):
    def g(d, j, p, st):
        s = fn(d, j, p, st)
        if s is None:
            return s
        entry = d.c[s.i]
        if stop_mult != 1.0:
            s.stop = entry - s.side * abs(entry - s.stop) * stop_mult
        if tgt_pts:
            sc = d.c[0] / NIFTY_OPEN.get(d.d, d.c[0])
            t = entry + s.side * tgt_pts * sc
            if s.target is None or s.side * (s.target - t) > 0:
                s.target = t
        SIGS[(CUR["sym"], d.d, d.t[s.i] + 5)] = (s.side, entry, s.stop, s.target, name)
        return s
    return g


NIFTY_OPEN = {d.d: d.c[0] for d in D5["NIFTY"]}
CUR = {"sym": None}


def both(sym, days, w=W, ca=CA):
    CUR["sym"] = sym
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
    return [t for s in SYMS for t in both(s, DAYS[s])]


def stats(tr):
    p = np.array([t["pnl"] for t in tr])
    daily = pd.Series(p, index=[t["date"] for t in tr]).groupby(level=0).sum().reindex(DATES, fill_value=0)
    eq = daily.cumsum().values
    pos, neg = p[p > 0].sum(), -p[p <= 0].sum()
    return {"trades": len(p), "net": round(float(p.sum())), "train": round(float(sum(t["pnl"] for t in tr if t["date"] < SPLIT))),
            "unseen120": round(float(sum(t["pnl"] for t in tr if t["date"] >= SPLIT))), "win": round(float((p > 0).mean() * 100), 1),
            "pf": round(float(pos / neg), 2), "max_dd": round(float((eq - np.maximum.accumulate(np.concatenate([[0], eq]))[1:]).min())),
            "years": {y: round(float(sum(t["pnl"] for t in tr if t["date"].year == y))) for y in (2023, 2024, 2025, 2026)}}


# ---------------------------------------------------------------- A. stopped trades on 1-minute prices
E.STRATS.update({"noise": capture("noise", ORIG["noise"]), "camarilla": capture("camarilla", ORIG["camarilla"])})
BASE = book()
OUT["rules_base"] = stats(BASE)
print("base (round-6 rules):", OUT["rules_base"], flush=True)
M1 = {s: {dd.d: dd for dd in m1.load(s)} for s in SYMS}
rows = []
for t in BASE:
    side, entry, stop, target, name = SIGS[(t["sym"], t["date"], t["in"])]
    dd = M1[t["sym"]].get(t["date"])
    if dd is None:
        continue
    risk = abs(entry - stop)
    i0 = int(np.searchsorted(dd.m, t["in"]))
    end = int(np.searchsorted(dd.m, 915))
    lo, hi = dd.l[i0:end], dd.h[i0:end]
    adverse = (stop - lo) * side if side > 0 else (hi - stop)            # >0 means beyond the stop
    hit = np.nonzero((lo <= stop) if side > 0 else (hi >= stop))[0]
    fav = (hi - entry) if side > 0 else (entry - lo)
    mae_r = float(((entry - lo).max() if side > 0 else (hi - entry).max()) / risk)
    r = {"sym": t["sym"], "strat": t["strat"], "date": t["date"], "why": t["why"], "pnl": t["pnl"], "risk_pts": risk / dd.scale,
         "mae_r": mae_r, "stopped": len(hit) > 0}
    if len(hit):
        k = hit[0]
        after_fav = fav[k:]
        r["back_to_entry"] = bool((after_fav >= 0).any())
        r["then_1r"] = bool((after_fav >= risk).any())
        r["then_2r"] = bool((after_fav >= 2 * risk).any())
        r["then_target"] = bool(target is not None and (after_fav >= abs(target - entry)).any())
        j = np.nonzero(after_fav >= risk)[0]
        upto = k + (j[0] if len(j) else len(after_fav))
        r["beyond_stop_r"] = float(max(0.0, ((stop - lo[k:upto + 1].min()) if side > 0 else (hi[k:upto + 1].max() - stop))) / risk)
        r["beyond_stop_pts"] = r["beyond_stop_r"] * risk / dd.scale
    rows.append(r)
A = pd.DataFrame(rows)
st = A[A.stopped]
OUT["A"] = {
    "trades": len(A), "touched_stop": int(len(st)), "touched_stop_pct": round(len(st) / len(A) * 100, 1),
    "after_stop_back_to_entry_pct": round(st.back_to_entry.mean() * 100, 1),
    "after_stop_reached_1R_pct": round(st.then_1r.mean() * 100, 1), "after_stop_reached_2R_pct": round(st.then_2r.mean() * 100, 1),
    "after_stop_reached_target_pct_noise": round(st[st.strat == "noise"].then_target.mean() * 100, 1),
    "beyond_stop_before_1R_median_R": round(float(st[st.then_1r].beyond_stop_r.median()), 2),
    "beyond_stop_before_1R_quartiles_pts": [round(float(st[st.then_1r].beyond_stop_pts.quantile(q)), 1) for q in (.25, .5, .75)],
    "share_of_stop_then_1R_within_0.25R_beyond": round(float((st[st.then_1r].beyond_stop_r <= 0.25).mean() * 100), 1),
    "winners_mae_R_quartiles": [round(float(A[A.pnl > 0].mae_r.quantile(q)), 2) for q in (.5, .75, .9)],
    "by_strategy": {k: {"stopped": int(len(g)), "then_1R_pct": round(g.then_1r.mean() * 100, 1), "back_to_entry_pct": round(g.back_to_entry.mean() * 100, 1)}
                    for k, g in st.groupby("strat")},
}
print("A:", OUT["A"], flush=True)

# ---------------------------------------------------------------- B. different stops, real sizing
ORIG_SIM = E.simulate


def sim_close(d, s, meta, p, upper=None, lower=None):
    """Stop only when a 5-minute candle CLOSES beyond it (hard stop 1.5x as far)."""
    side, entry = s.side, (s.px if s.px is not None else d.c[s.i])
    soft = s.stop
    hard = entry - side * 1.5 * abs(entry - soft)
    s2 = E.Sig(s.i, s.side, hard, s.target, s.trail, 0, s.time_stop, s.tag, s.px)
    risk = abs(entry - soft)
    stop_be = None
    reached = False
    n = len(d.t)
    for k in range(s.i + 1, n):
        hi, lo, cl, op = d.h[k], d.l[k], d.c[k], d.o[k]
        adverse, favour = (lo, hi) if side > 0 else (hi, lo)
        if side * (adverse - hard) <= 0:
            return k, (op if side * (op - hard) < 0 else hard), "stop"
        if s.target is not None and side * (favour - s.target) >= 0:
            return k, (op if side * (op - s.target) > 0 else s.target), "target"
        lvl = stop_be if stop_be is not None else soft
        if side * (cl - lvl) <= 0:
            return k, cl, "stop (close)" if stop_be is None else "breakeven"
        if s.be_r and stop_be is None and side * (favour - (entry + side * s.be_r * risk)) >= 0:
            stop_be = entry
        end = d.t[k] + 5
        if s.trail == "noise" and end % 30 == 15:
            band = upper[k] if side > 0 else lower[k]
            lv = max(band, d.vwap[k]) if side > 0 else min(band, d.vwap[k])
            if not math.isnan(lv) and side * (cl - lv) < 0:
                return k, cl, "noise exit"
        if side * (favour - (entry + side * 0.5 * risk)) >= 0:
            reached = True
        if s.time_stop and not reached and end - (d.t[s.i] + 5) >= s.time_stop:
            return k, cl, "time stop"
        if end >= E.SQUARE_OFF:
            return k, cl, "square-off"
    return n - 1, d.c[-1], "square-off"


OUT["B"] = {}
for lab, mult, tgt, simf in (("stop as now", 1.0, None, ORIG_SIM), ("stop 0.75x as far", 0.75, None, ORIG_SIM),
                              ("stop 1.25x as far", 1.25, None, ORIG_SIM), ("stop 1.5x as far", 1.5, None, ORIG_SIM),
                              ("stop on 5-min close (hard 1.5x)", 1.0, None, sim_close),
                              ("book profit at +80 Nifty pts", 1.0, 80, ORIG_SIM), ("book profit at +100", 1.0, 100, ORIG_SIM),
                              ("book profit at +120", 1.0, 120, ORIG_SIM)):
    E.simulate = simf
    E.STRATS.update({"noise": capture("noise", ORIG["noise"], mult, tgt), "camarilla": capture("camarilla", ORIG["camarilla"], mult, tgt)})
    OUT["B"][lab] = stats(book())
    print(f"B {lab:34s}", OUT["B"][lab], flush=True)
E.simulate = ORIG_SIM
E.STRATS.update(ORIG)


# ---------------------------------------------------------------- C. generic: stop first, target later
@njit(cache=True)
def generic(h, l, c, m, S, T, every):
    """For entries every `every` minutes 09:30-14:30, long and short: outcome codes
    0 target first, 1 stop first then target later, 2 stop first never target, 3 neither by 15:15.
    Also returns depth beyond the stop before the target (for code 1)."""
    out = []
    n = len(c)
    for i in range(n):
        if m[i] < 570 or m[i] > 870 or (m[i] - 570) % every:
            continue
        for side in (1, -1):
            e = c[i]
            st, tg = e - side * S, e + side * T
            code, depth, stopped = 3, 0.0, False
            for k in range(i + 1, n):
                if m[k] >= 915:
                    break
                adv = l[k] if side > 0 else h[k]
                fav = h[k] if side > 0 else l[k]
                if not stopped:
                    if side * (adv - st) <= 0:
                        stopped = True
                        depth = side * (st - adv)
                        if side * (fav - tg) >= 0:
                            pass
                        continue
                    if side * (fav - tg) >= 0:
                        code = 0
                        break
                else:
                    depth = max(depth, side * (st - adv))
                    if side * (fav - tg) >= 0:
                        code = 1
                        break
            if stopped and code == 3:
                code = 2
            out.append((code, depth))
    return out


def shuffled(dd, rng):
    """Same day, same 1-minute moves in random order: a market with no memory and no 'hunting'."""
    r = np.diff(dd.c, prepend=dd.o[0])
    hl_up, hl_dn = dd.h - np.maximum(dd.o, dd.c), np.minimum(dd.o, dd.c) - dd.l
    idx = rng.permutation(len(r))
    c = dd.o[0] + np.cumsum(r[idx])
    o = np.concatenate([[dd.o[0]], c[:-1]])
    return np.maximum(o, c) + hl_up[idx], np.minimum(o, c) - hl_dn[idx], c


rng = np.random.default_rng(3)
OUT["C"] = {}
for S in (10, 15, 20, 30, 40, 60):
    for RR in (1, 2, 3):
        real, rand = [], []
        for sym in SYMS:
            for dd in M1[sym].values():
                real += generic(dd.h, dd.l, dd.c, dd.m, S * dd.scale, S * RR * dd.scale, 15)
                if dd.d.day % 3 == 0:                        # a third of days is plenty for the random baseline
                    h, l, c = shuffled(dd, rng)
                    rand += generic(h, l, c, dd.m, S * dd.scale, S * RR * dd.scale, 15)
        R, Q = np.array(real), np.array(rand)
        f = lambda X, code: round(float((X[:, 0] == code).mean() * 100), 1)  # noqa: E731
        stopped = R[R[:, 0] >= 1]
        OUT["C"][f"S{S}_R{RR}"] = {
            "stop_pts": S, "target_pts": S * RR, "n": len(R),
            "target_first": f(R, 0), "stop_then_target": f(R, 1), "stop_never_target": f(R, 2), "neither": f(R, 3),
            "of_stopped_then_target_pct": round(float((stopped[:, 0] == 1).mean() * 100), 1),
            "random_market_stop_then_target": f(Q, 1), "random_market_target_first": f(Q, 0),
            "depth_beyond_stop_median_pts": round(float(np.median(R[R[:, 0] == 1][:, 1])), 1) if (R[:, 0] == 1).any() else None,
            "depth_within_5pts_pct": round(float((R[R[:, 0] == 1][:, 1] <= 5).mean() * 100), 1) if (R[:, 0] == 1).any() else None,
        }
        print("C", S, RR, OUT["C"][f"S{S}_R{RR}"], flush=True)

# ---------------------------------------------------------------- D. real option premiums
opt = pd.read_csv("data/OPT_live5m.csv.gz", parse_dates=["ts", "expiry"])
opt = opt[(opt.ts.dt.hour * 60 + opt.ts.dt.minute).between(555, 925)]
opt["d"], opt["m"] = opt.ts.dt.date, opt.ts.dt.hour * 60 + opt.ts.dt.minute
spot = {}
for sym in SYMS:
    for dd in M1[sym].values():
        spot[(sym, dd.d)] = (dd.m, dd.c)
res = []
for (sym, d, exp, k, typ), g in opt.groupby(["sym", "d", "expiry", "strike", "opt"]):
    if (sym, d) not in spot or g.volume.sum() < 50000 or (exp.date() - d).days < 1:
        continue
    mm, cc = spot[(sym, d)]
    step = E.META[sym]["step"]
    g = g.sort_values("m")
    for t_in in range(570, 871, 30):
        x = g[g.m == t_in - 5]
        if x.empty:
            continue
        j = int(np.searchsorted(mm, t_in - 1))
        if j >= len(cc):
            continue
        atm = round(cc[j] / step) * step
        mny = (k - atm) / step * (1 if typ == "CE" else -1)
        if not -1 <= mny <= 0:                                  # ATM or 1 strike ITM, like the app
            continue
        e = float(x.close.iloc[0])
        nxt = g[(g.m >= t_in) & (g.m < 915)]
        if e < 20 or nxt.empty:
            continue
        lo, hi = nxt.low.values / e - 1, nxt.high.values / e - 1
        for slp in (0.2, 0.3, 0.4):
            for tp in (0.2, 0.4, 0.6, 1.0):
                s_hit = np.nonzero(lo <= -slp)[0]
                t_hit = np.nonzero(hi >= tp)[0]
                if len(t_hit) and (not len(s_hit) or t_hit[0] < s_hit[0]):
                    code, depth = 0, 0.0
                elif len(s_hit):
                    later = t_hit[t_hit > s_hit[0]]
                    code = 1 if len(later) else 2
                    upto = later[0] if len(later) else len(lo)
                    depth = float(-lo[s_hit[0]:upto].min() - slp)
                else:
                    code, depth = 3, 0.0
                res.append((sym, slp, tp, code, depth, (exp.date() - d).days))
P = pd.DataFrame(res, columns=["sym", "sl", "tp", "code", "depth", "dte"])
OUT["D"] = {}
for (slp, tp), g in P.groupby(["sl", "tp"]):
    st_ = g[g.code >= 1]
    OUT["D"][f"SL-{int(slp*100)}% TP+{int(tp*100)}%"] = {
        "entries": len(g), "target_first_pct": round((g.code == 0).mean() * 100, 1),
        "stop_then_target_pct": round((g.code == 1).mean() * 100, 1),
        "of_stopped_then_target_pct": round((st_.code == 1).mean() * 100, 1) if len(st_) else None,
        "depth_below_stop_median_pct_of_entry": round(float(g[g.code == 1].depth.median() * 100), 1) if (g.code == 1).any() else None}
    print("D", slp, tp, OUT["D"][f"SL-{int(slp*100)}% TP+{int(tp*100)}%"], flush=True)
OUT["D_meta"] = {"days": int(opt.d.nunique()), "from": str(opt.d.min()), "to": str(opt.d.max()), "contracts_days": int(len(P) / 12)}
json.dump(OUT, open("r7_stops.json", "w"), default=str, indent=1)
print("wrote r7_stops.json")
