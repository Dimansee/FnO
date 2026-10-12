"""Round 13 — sizing, exits, strike, re-entry, Bank Nifty tuning, Market Profile rules.
All on the 5-minute engine with corrected premiums (FNO_CALIB=1). Baseline = round 6 book (noise W + Camarilla CA,
both indices, one position at a time per index). Training = sessions before SPLIT (last 120 sessions are unseen).
Nothing is tuned on the unseen set: every grid is picked on training and then reported on unseen.

A. position sizing by regime (VIX percentile / opening-range percentile / days to expiry), fixed multiplier sets + Kelly
B. exit management grids (noise: target / breakeven / trail / partial exit; camarilla: time stop / breakeven)
C. strike selection (2 ITM .. 1 OTM)
D. re-entry after a stop (max_trades 2 / 3)
E. Bank Nifty tuned on its own (noise mult x stop x rr; camarilla vix_max x time stop)
F. Market Profile rules from yesterday's TPO profile (80% rule, acceptance, rejection, open-drive, POC-migration filter)
"""
import copy, itertools, json, math, os, time
import numpy as np
import pandas as pd

os.environ.setdefault("FNO_CALIB", "1")
import engine as E

T0 = time.time()
SYMS = ("NIFTY", "BANKNIFTY")
W = dict(mult=1.75, stop_atr=2.0, rr=4.0, vwap=1, max_trades=1, vix_min=11, last_entry=870, otm=-1,
         be=0, ctx=-9, rmin=1.0, rmax=2.5)
CA = {'rr': None, 'be': 1.0, 'tstop': 45, 'ctx': -9, 'otm': -1, 'last_entry': 780, 'max_trades': 1, 'rmin': 1.0,
      'rmax': 3.0, 'vix_min': 11, 'vix_max': 22, 'mode': 'break', 'trail': ''}
D = {s: E.load_days(s) for s in SYMS}
DATES = [d.d for d in D["NIFTY"]]
SPLIT = DATES[-120]
TRAIN_DATES = [d for d in DATES if d < SPLIT]
UNSEEN_DATES = [d for d in DATES if d >= SPLIT]
DAYMAP = {s: {d.d: d for d in D[s]} for s in SYMS}
POS = {s: {d.d: i for i, d in enumerate(D[s])} for s in SYMS}
OUT = {"split": str(SPLIT), "train_days": len(TRAIN_DATES), "unseen_days": len(UNSEEN_DATES)}
print(f"days {len(DATES)}  training {TRAIN_DATES[0]}..{TRAIN_DATES[-1]} ({len(TRAIN_DATES)})  unseen {SPLIT}..{DATES[-1]} ({len(UNSEEN_DATES)})")


# ---------------------------------------------------------------- helpers (round 6 style)
def run_tagged(sym, strat, p, tag=None, days=None):
    return [dict(t, strat=tag or strat, sym=sym) for t in E.run(days or D[sym], sym, strat, p)]


def merge(trs):
    """One position at a time per day (same rule as round6.both)."""
    tr = sorted(trs, key=lambda t: (t["date"], t["in"]))
    out, busy = [], {}
    for t in tr:
        if busy.get(t["date"], 0) > t["in"]:
            continue
        out.append(t)
        busy[t["date"]] = t["out"]
    return out


def both(sym, w=W, ca=CA, extra=(), noise_strat="noise", cam_strat="camarilla"):
    trs = []
    if w is not None:
        trs += run_tagged(sym, noise_strat, w, "noise")
    if ca is not None:
        trs += run_tagged(sym, cam_strat, ca, "camarilla")
    for name, strat, p in extra:
        trs += run_tagged(sym, strat, p, name)
    return merge(trs)


def book(syms=SYMS, **kw):
    return [t for s in syms for t in both(s, **kw)]


def _stats(tr, dates):
    if not tr:
        return {"n": 0, "net": 0, "per_trade": 0, "win": None, "pf": None, "max_dd": 0, "net_per_dd": None}
    p = np.array([t["pnl"] for t in tr])
    daily = pd.Series(p, index=[t["date"] for t in tr]).groupby(level=0).sum().reindex(dates, fill_value=0)
    eq = daily.cumsum().values
    dd = float((eq - np.maximum.accumulate(np.concatenate([[0], eq]))[1:]).min())
    pos, neg = p[p > 0].sum(), -p[p <= 0].sum()
    return {"n": int(len(p)), "net": round(float(p.sum())), "per_trade": round(float(p.mean())),
            "win": round(float((p > 0).mean() * 100), 1), "pf": round(float(pos / neg), 2) if neg else None,
            "max_dd": round(dd), "net_per_dd": round(float(p.sum() / -dd), 2) if dd < 0 else None}


def stats(tr):
    return {"train": _stats([t for t in tr if t["date"] < SPLIT], TRAIN_DATES),
            "unseen": _stats([t for t in tr if t["date"] >= SPLIT], UNSEEN_DATES),
            "years": {y: round(float(sum(t["pnl"] for t in tr if t["date"].year == y))) for y in (2023, 2024, 2025, 2026)}}


def beats(s, base):
    """'Improvement' = more net on BOTH training and unseen than the baseline."""
    return bool(s["train"]["net"] > base["train"]["net"] and s["unseen"]["net"] > base["unseen"]["net"])


def beats_ratio(s, base):
    """Stricter: more net AND a better net/maxDD on both halves (matters when a change only adds exposure)."""
    return beats(s, base) and all((s[h]["net_per_dd"] or 0) > (base[h]["net_per_dd"] or 0) for h in ("train", "unseen"))


def small(s, n=30):
    return s["unseen"]["n"] < n


def show(name, s, base=None):
    t, u = s["train"], s["unseen"]
    flag = ""
    if base is not None:
        flag = "  <-- beats base on both" if beats(s, base) else ""
        if small(s):
            flag += f"  (unseen n={u['n']}: small)"
    print(f"{name:46s} train n{t['n']:4d} ₹{t['net']/1000:+7.1f}k pf {t['pf'] or 0:.2f} dd ₹{-t['max_dd']/1000:4.0f}k | "
          f"unseen n{u['n']:3d} ₹{u['net']/1000:+6.1f}k pf {u['pf'] or 0:.2f} dd ₹{-u['max_dd']/1000:3.0f}k | "
          f"{' '.join(f'{y}:{v/1000:+.0f}k' for y, v in s['years'].items())}{flag}", flush=True)


# ---------------------------------------------------------------- baseline
BASE = book()
B = stats(BASE)
OUT["baseline"] = {"book": B, "by_sym": {s: stats([t for t in BASE if t["sym"] == s]) for s in SYMS},
                   "by_rule": {r: stats([t for t in BASE if t["strat"] == r]) for r in ("noise", "camarilla")},
                   "exits": {r: dict(pd.Series([t["why"] for t in BASE if t["strat"] == r]).value_counts()) for r in ("noise", "camarilla")}}
print("\n=== BASELINE (round 6 book: noise W + camarilla CA, both indices)")
show("baseline book", B)
for s in SYMS:
    show(f"  {s}", OUT["baseline"]["by_sym"][s])
for r in ("noise", "camarilla"):
    show(f"  {r} rule", OUT["baseline"]["by_rule"][r])
print("  exit reasons:", OUT["baseline"]["exits"])
print("  NOTE: the shipped noise rule hard-codes the Zarattini half-hour 'noise' trail (s_noise -> Sig(trail='noise'));"
      " 'no trail' below is a variant, not the baseline.")

# ---------------------------------------------------------------- A. position sizing by regime
print("\n=== A. position sizing by regime (same trades, P&L x multiplier; lot rounding and the 60% cash cap ignored)")
VIX0 = {s: np.array([d.vix[0] for d in D[s]]) for s in SYMS}
OR30 = {s: np.array([(max(d.h[:6]) - min(d.l[:6])) / d.o[0] * 100 for d in D[s]]) for s in SYMS}


def trailing_q(arr, k, n=250, minn=60):
    hist = arr[max(0, k - n):k]
    if len(hist) < minn:
        return None
    return min(int((hist < arr[k]).mean() * 4), 3)


def b_vix(t):
    return trailing_q(VIX0[t["sym"]], POS[t["sym"]][t["date"]])


def b_or(t):
    if t["in"] < 585:                     # entered before 09:45: the 30-min range is not known yet
        return None
    return trailing_q(OR30[t["sym"]], POS[t["sym"]][t["date"]])


def b_dte(t):
    x = (DAYMAP[t["sym"]][t["date"]].expiry - t["date"]).days
    return 0 if x <= 2 else 1 if x <= 4 else 2 if x <= 7 else 3


BUCKETS = {"vix_pct": (b_vix, ["q1 <25", "q2 25-50", "q3 50-75", "q4 75+"]),
           "or30_pct": (b_or, ["q1 narrow", "q2", "q3", "q4 wide"]),
           "dte": (b_dte, ["1-2", "3-4", "5-7", "8+"])}
SCHEMES = {"vix_pct": {"0.5/1/1.5/1": [0.5, 1, 1.5, 1], "0.5/1/1.5/1.5": [0.5, 1, 1.5, 1.5], "0.5/1/1/1.5": [0.5, 1, 1, 1.5],
                       "1/1/1.5/1.5": [1, 1, 1.5, 1.5], "0/1/1.5/1.5": [0, 1, 1.5, 1.5], "1.5/1/0.5/0.5 (inverse check)": [1.5, 1, 0.5, 0.5]},
           "or30_pct": {"0.5/1/1.5/1": [0.5, 1, 1.5, 1], "1/1/1.5/1.5": [1, 1, 1.5, 1.5], "1.5/1/1/0.5": [1.5, 1, 1, 0.5],
                        "0.5/1/1/1.5": [0.5, 1, 1, 1.5], "1/1/1/0.5": [1, 1, 1, 0.5]},
           "dte": {"0/1/1/1": [0, 1, 1, 1], "0.5/1/1/1": [0.5, 1, 1, 1], "0.5/1/1.25/1.5": [0.5, 1, 1.25, 1.5],
                   "1.5/1/1/0.5": [1.5, 1, 1, 0.5], "1/1/1.5/1.5": [1, 1, 1.5, 1.5]}}


def scaled(tr, fn, mults):
    out = []
    for t in tr:
        b = fn(t)
        m = 1.0 if b is None else mults[b]
        out.append(dict(t, pnl=t["pnl"] * m, budget_R=t["budget_R"] * m, mult=m))
    return out


def kelly_mult(xs, cap=2.0):
    """Risk multiplier (x the 1% budget) that maximises mean log growth of the trade sample; capped at 2 (= 2% risk)."""
    xs = np.array(xs)
    if len(xs) < 10:
        return 1.0
    grid = np.arange(0, cap + 1e-9, 0.05)
    best, bm = -9, 0.0
    for m in grid:
        f = 0.01 * m * xs
        if (f <= -0.99).any():
            continue
        g = float(np.mean(np.log1p(f)))
        if g > best:
            best, bm = g, float(m)
    return bm


A = {"bucket_training_stats": {}, "bucket_unseen_stats": {}, "schemes": {}, "kelly": {}}
train_tr = [t for t in BASE if t["date"] < SPLIT]
unseen_tr = [t for t in BASE if t["date"] >= SPLIT]
print(f"  baseline net/maxDD: train {B['train']['net_per_dd']}  unseen {B['unseen']['net_per_dd']}")


def by_bucket(tr, fn, labels, dates):
    grp = {}
    for t in tr:
        grp.setdefault(fn(t), []).append(t)
    return grp, {(labels[b] if b is not None else "unknown"): {kk: v for kk, v in _stats(x, dates).items() if kk in ("n", "net", "per_trade", "win", "pf")}
                 for b, x in sorted(grp.items(), key=lambda kv: -1 if kv[0] is None else kv[0])}


def show_a(name, s, avg_m):
    show(name, s, B)
    print(f"{'':48s} avg mult {avg_m:.2f}  net/maxDD train {s['train']['net_per_dd']} unseen {s['unseen']['net_per_dd']}"
          f"{'  <-- better net AND net/DD on both' if beats_ratio(s, B) else ''}")


for key, (fn, labels) in BUCKETS.items():
    grp, A["bucket_training_stats"][key] = by_bucket(train_tr, fn, labels, TRAIN_DATES)
    _, A["bucket_unseen_stats"][key] = by_bucket(unseen_tr, fn, labels, UNSEEN_DATES)
    print(f"  training by {key}:", A["bucket_training_stats"][key])
    print(f"  unseen   by {key}:", A["bucket_unseen_stats"][key])
    A["schemes"][key] = {}
    for name, mults in SCHEMES[key].items():
        sc = scaled(BASE, fn, mults)
        s = stats(sc)
        s["avg_mult"] = round(float(np.mean([t["mult"] for t in sc])), 2)
        s["beats_ratio"] = beats_ratio(s, B)
        A["schemes"][key][name] = s
        show_a(f"  {key} {name}", s, s["avg_mult"])
    # Kelly-ish: multiplier per bucket from training only (in-sample on training, out-of-sample on unseen)
    km = {}
    for b, x in grp.items():
        if b is None:
            continue
        km[b] = kelly_mult([t["budget_R"] for t in x])
    mults = [km.get(b, 1.0) for b in range(4)]
    half = [m / 2 for m in mults]
    A["kelly"][key] = {"full_kelly_mults": mults, "half_kelly_mults": half,
                       "full": stats(scaled(BASE, fn, mults)), "half": stats(scaled(BASE, fn, half))}
    for h, mm in (("full", mults), ("half", half)):
        A["kelly"][key][h]["beats_ratio"] = beats_ratio(A["kelly"][key][h], B)
        show_a(f"  {key} {h}-Kelly (training-fit) {[round(m, 2) for m in mm]}", A["kelly"][key][h], float(np.mean(mm)))
A["kelly"]["flat_all_trades"] = kelly_mult([t["budget_R"] for t in train_tr])
print(f"  Kelly on all training trades, flat: {A['kelly']['flat_all_trades']:.2f}x the 1% budget (cap 2x)")
OUT["A_sizing"] = A

# ---------------------------------------------------------------- B. exit management
print("\n=== B. exit management")


def s_noise_x(d, j, p, st):
    s = E.s_noise(d, j, p, st)
    if s is not None:
        s.trail = p.get("trail_mode", "noise")
    return s


E.STRATS["noise_x"] = s_noise_x


def run_partial(days, sym, p, first_rr=2.0):
    """Half the position out at +2R (no trail), the other half run with the config's exit; P&L = average of the two."""
    meta, st, out = E.META[sym], {}, []
    for d in days:
        if not (p.get("vix_min", 0) <= d.vix[0] <= p.get("vix_max", 99)):
            continue
        s = s_noise_x(d, 0, p, st)
        if s is None:
            continue
        up, lo = st[("nb", d.d, p["mult"])]
        entry = d.c[s.i]
        r = abs(entry - s.stop)
        sa = copy.copy(s)
        sa.target, sa.trail = entry + s.side * first_rr * r, ""
        ka, pa, wa = E.simulate(d, sa, meta, p, up, lo)
        kb, pb, wb = E.simulate(d, s, meta, p, up, lo)
        ta, tb = E.trade_pnl(d, sa, ka, pa, meta, p), E.trade_pnl(d, s, kb, pb, meta, p)
        if not ta or not tb:
            continue
        t = dict(tb, pnl=(ta["pnl"] + tb["pnl"]) / 2, budget_R=(ta["budget_R"] + tb["budget_R"]) / 2, out=max(ta["out"], tb["out"]),
                 why=f"{wa}+{wb}", strat="noise", sym=sym)
        out.append(t)
    return out


def book_noise_cfg(w, partial=False):
    out = []
    for s in SYMS:
        tr = run_partial(D[s], s, w) if partial else run_tagged(s, "noise_x", w, "noise")
        out += merge(tr + run_tagged(s, "camarilla", CA, "camarilla"))
    return out


Bx = {"noise": {}, "noise_partial": {}, "camarilla": {}}
TRAILS = {"none": ("", None), "noise": ("noise", None), "atr2": ("atr", 2.0), "atr3": ("atr", 3.0)}
for rr, be, tn in itertools.product((3.0, 4.0, 5.0, None), (0, 1.0, 1.5), TRAILS):
    tm, ta = TRAILS[tn]
    w = dict(W, rr=rr, be=be, trail_mode=tm)
    if ta:
        w["trail_atr"] = ta
    name = f"target {rr or 'none'} be {be or 'off'} trail {tn}"
    s = stats(book_noise_cfg(w))
    Bx["noise"][name] = s
    show(f"  noise {name}", s, B)
for rr, be, tn in itertools.product((4.0, 5.0, None), (0, 1.0), ("noise", "atr2")):
    tm, ta = TRAILS[tn]
    w = dict(W, rr=rr, be=be, trail_mode=tm)
    if ta:
        w["trail_atr"] = ta
    name = f"half@2R + rest target {rr or 'none'} be {be or 'off'} trail {tn}"
    s = stats(book_noise_cfg(w, partial=True))
    Bx["noise_partial"][name] = s
    show(f"  noise {name}", s, B)
for ts, be in itertools.product((30, 45, 60), (0, 1.0, 1.5)):
    ca = dict(CA, tstop=ts, be=be)
    name = f"time stop {ts} be {be or 'off'}"
    s = stats(book(ca=ca))
    Bx["camarilla"][name] = s
    show(f"  camarilla {name}", s, B)


def rank(dct, key="train"):
    return sorted(dct.items(), key=lambda kv: -kv[1][key]["net"])


Bsum = {}
for k, dct in Bx.items():
    top = rank(dct)[:3]
    Bsum[k] = {"best_on_training": [{"name": n, **{h: {kk: s[h][kk] for kk in ("n", "net", "pf", "max_dd")} for h in ("train", "unseen")}, "beats_base_both": beats(s, B)} for n, s in top],
               "n_configs": len(dct), "n_beat_base_both": sum(beats(s, B) for s in dct.values()),
               "winners": [n for n, s in dct.items() if beats(s, B)]}
    print(f"  {k}: {Bsum[k]['n_beat_base_both']}/{len(dct)} beat the base on both halves; best on training: {top[0][0]} "
          f"(train ₹{top[0][1]['train']['net']/1000:+.1f}k, unseen ₹{top[0][1]['unseen']['net']/1000:+.1f}k)")
OUT["B_exits"] = {"grid": Bx, "summary": Bsum}

# ---------------------------------------------------------------- C. strike selection
print("\n=== C. strike selection (otm: -2 = 2 ITM, -1 = 1 ITM (baseline), 0 = ATM, +1 = 1 OTM)")
C = {}
for otm in (-2, -1, 0, 1):
    w, ca = dict(W, otm=otm), dict(CA, otm=otm)
    bk = book(w=w, ca=ca)
    row = {"book": stats(bk), "detail": {}}
    for s in SYMS:
        for r, strat, p in (("noise", "noise", w), ("camarilla", "camarilla", ca)):
            tr = run_tagged(s, strat, p, r)
            sq = [t for t in tr if t["why"] == "square-off"]
            tt = [t for t in tr if t["date"] < SPLIT]
            row["detail"][f"{s} {r}"] = {"n": len(tr), "net": round(sum(t["pnl"] for t in tr)), "train_net": round(sum(t["pnl"] for t in tt)),
                                        "unseen_net": round(sum(t["pnl"] for t in tr if t["date"] >= SPLIT)),
                                        "avg_premium": round(float(np.mean([t["prem_in"] for t in tr])), 1), "avg_lots": round(float(np.mean([t["lots"] for t in tr])), 2),
                                        "sq_n": len(sq), "sq_net": round(sum(t["pnl"] for t in sq)), "sq_per_trade": round(sum(t["pnl"] for t in sq) / len(sq)) if sq else 0,
                                        "sq_share_of_gross_pnl": round(sum(abs(t["pnl"]) for t in sq) / max(sum(abs(t["pnl"]) for t in tr), 1) * 100, 1)}
    C[str(otm)] = row
    show(f"  otm {otm:+d} book", row["book"], B if otm != -1 else None)
    for k, v in row["detail"].items():
        print(f"      {k:22s} n{v['n']:4d} net ₹{v['net']/1000:+7.1f}k (train {v['train_net']/1000:+.1f}k / unseen {v['unseen_net']/1000:+.1f}k) "
              f"prem ₹{v['avg_premium']:6.1f} lots {v['avg_lots']:.2f} | square-off: n{v['sq_n']:3d} ₹{v['sq_net']/1000:+6.1f}k ₹{v['sq_per_trade']:+5d}/trade {v['sq_share_of_gross_pnl']}% of gross")
OUT["C_strike"] = C

# ---------------------------------------------------------------- D. re-entry after a stop
print("\n=== D. re-entry (max_trades 2 / 3, max_losses 2)")
Dx = {"book": {}, "attempts": {}}
for mt in (1, 2, 3):
    w, ca = dict(W, max_trades=mt, max_losses=2), dict(CA, max_trades=mt, max_losses=2)
    s = stats(book(w=w, ca=ca))
    Dx["book"][f"max_trades {mt}"] = s
    show(f"  book max_trades {mt}", s, B if mt > 1 else None)
# attempts per rule (raw runs, max_trades 3): P&L of attempt 2 / 3 by the exit reason of the first trade
att = {}
for s in SYMS:
    for r, p in (("noise", dict(W, max_trades=3, max_losses=2)), ("camarilla", dict(CA, max_trades=3, max_losses=2))):
        tr = run_tagged(s, r, p, r)
        byday = {}
        for t in tr:
            byday.setdefault(t["date"], []).append(t)
        for d, lst in byday.items():
            lst.sort(key=lambda t: t["in"])
            for k, t in enumerate(lst):
                first_why = lst[0]["why"]
                att.setdefault(r, {}).setdefault(k + 1, {}).setdefault(first_why if k else "-", []).append(t)
for r, byk in att.items():
    Dx["attempts"][r] = {}
    for k, byw in sorted(byk.items()):
        for why, lst in byw.items():
            tt = [t for t in lst if t["date"] < SPLIT]
            uu = [t for t in lst if t["date"] >= SPLIT]
            Dx["attempts"][r][f"attempt {k} after first={why}"] = {"n": len(lst), "net": round(sum(t["pnl"] for t in lst)), "per_trade": round(sum(t["pnl"] for t in lst) / len(lst)),
                                                                    "win": round(float(np.mean([t["pnl"] > 0 for t in lst]) * 100), 1),
                                                                    "train_net": round(sum(t["pnl"] for t in tt)), "unseen_net": round(sum(t["pnl"] for t in uu)), "unseen_n": len(uu)}
    for k, v in Dx["attempts"][r].items():
        print(f"  {r:10s} {k:34s} n{v['n']:4d} net ₹{v['net']/1000:+7.1f}k ₹{v['per_trade']:+5d}/trade win {v['win']}%  train ₹{v['train_net']/1000:+.1f}k unseen ₹{v['unseen_net']/1000:+.1f}k (n{v['unseen_n']})")


# post-hoc variant (picked after seeing the table above, so NOT a clean test): camarilla may re-enter once, but only after a stop
def cam_reenter_after_stop(sym):
    tr = run_tagged(sym, "camarilla", dict(CA, max_trades=2, max_losses=2), "camarilla")
    byday = {}
    for t in tr:
        byday.setdefault(t["date"], []).append(t)
    out = []
    for lst in byday.values():
        lst.sort(key=lambda t: t["in"])
        out.append(lst[0])
        if len(lst) > 1 and lst[0]["why"] == "stop":
            out.append(lst[1])
    return out


sel = [t for s in SYMS for t in merge(run_tagged(s, "noise", W, "noise") + cam_reenter_after_stop(s))]
Dx["posthoc_camarilla_reenter_only_after_stop"] = stats(sel)
show("  POST-HOC book: camarilla 2nd try only after a stop", Dx["posthoc_camarilla_reenter_only_after_stop"], B)
OUT["D_reentry"] = Dx

# ---------------------------------------------------------------- E. Bank Nifty on its own terms
print("\n=== E. Bank Nifty tuned on training only")
Ex = {"noise": {}, "camarilla": {}}
BN = "BANKNIFTY"
bn_base_noise = stats(run_tagged(BN, "noise", W))
bn_base_cam = stats(run_tagged(BN, "camarilla", CA))
bn_base_book = OUT["baseline"]["by_sym"][BN]
show("  BN noise default (Nifty-tuned W)", bn_base_noise)
for mult, sa, rr in itertools.product((1.5, 1.75, 2.0, 2.25), (1.5, 2.0, 2.5), (3.0, 4.0, 5.0)):
    name = f"mult {mult} stop {sa} rr {rr}"
    Ex["noise"][name] = stats(run_tagged(BN, "noise", dict(W, mult=mult, stop_atr=sa, rr=rr)))
show("  BN camarilla default (CA)", bn_base_cam)
for vm, ts in itertools.product((18, 22, 26), (30, 45, 60)):
    name = f"vix_max {vm} tstop {ts}"
    Ex["camarilla"][name] = stats(run_tagged(BN, "camarilla", dict(CA, vix_max=vm, tstop=ts)))
Esum = {}
for r, base in (("noise", bn_base_noise), ("camarilla", bn_base_cam)):
    ok = {n: s for n, s in Ex[r].items() if s["train"]["n"] >= 40}
    top = rank(ok)[:5]
    for n, s in top:
        show(f"  BN {r} {n}", s, base)
    bestn, bests = top[0]
    Esum[r] = {"default": base, "best_on_training": bestn, "best": bests, "beats_default_both": beats(bests, base),
               "top5_training": [{"name": n, "train_net": s["train"]["net"], "unseen_net": s["unseen"]["net"], "train_n": s["train"]["n"], "unseen_n": s["unseen"]["n"]} for n, s in top],
               "n_beat_default_both": sum(beats(s, base) for s in ok.values()), "n_configs": len(Ex[r])}
    print(f"  BN {r}: best on training = {bestn}; beats the Nifty-tuned default on both halves: {Esum[r]['beats_default_both']}; "
          f"{Esum[r]['n_beat_default_both']}/{len(ok)} configs beat it on both")


def parse(name):
    toks = name.split()
    return {toks[i]: float(toks[i + 1]) for i in range(0, len(toks), 2)}


pn, pc = parse(Esum["noise"]["best_on_training"]), parse(Esum["camarilla"]["best_on_training"])
w_bn = dict(W, mult=pn["mult"], stop_atr=pn["stop"], rr=pn["rr"])
ca_bn = dict(CA, vix_max=pc["vix_max"], tstop=int(pc["tstop"]))
bn_book_tuned = stats(both(BN, w=w_bn, ca=ca_bn))
full_tuned = stats(both("NIFTY") + both(BN, w=w_bn, ca=ca_bn))
Esum["bn_book_default"] = bn_base_book
Esum["bn_book_tuned"] = bn_book_tuned
Esum["full_book_with_bn_tuned"] = full_tuned
show("  BN book default", bn_base_book)
show("  BN book with BN-tuned noise + camarilla", bn_book_tuned, bn_base_book)
show("  full book with BN-tuned rules", full_tuned, B)
OUT["E_banknifty"] = {"grid": Ex, "summary": Esum}

# ---------------------------------------------------------------- F. Market Profile / Auction Market Theory
print("\n=== F. Market Profile rules (yesterday's TPO profile from 5-min bars)")
BUCKET = {"NIFTY": 10, "BANKNIFTY": 25}


def profile(h, l, b):
    lo, hi = int(math.floor(min(l) / b)), int(math.floor(max(h) / b))
    cnt = np.zeros(hi - lo + 1)
    for hh, ll in zip(h, l):
        cnt[int(ll // b) - lo: int(hh // b) - lo + 1] += 1
    ties = np.flatnonzero(cnt == cnt.max())
    poc = int(ties[np.argmin(np.abs(ties - len(cnt) / 2))])        # tie: closest to the middle of the range
    need, a, z, acc = 0.7 * cnt.sum(), poc, poc, cnt[poc]
    while acc < need:
        up = cnt[z + 1] if z + 1 < len(cnt) else -1
        dn = cnt[a - 1] if a > 0 else -1
        if up < 0 and dn < 0:
            break
        if up >= dn:
            z += 1; acc += up
        else:
            a -= 1; acc += dn
    return {"poc": (lo + poc + 0.5) * b, "vah": (lo + z + 1) * b, "val": (lo + a) * b}


PROF = {s: {D[s][i].d: profile(D[s][i - 1].h, D[s][i - 1].l, BUCKET[s]) for i in range(1, len(D[s]))} for s in SYMS}
for s in SYMS:
    w_ = [(p["vah"] - p["val"]) / DAYMAP[s][d].prev_close * 100 for d, p in PROF[s].items()]
    print(f"  {s}: value-area width median {np.median(w_):.2f}% of price; open inside yesterday's VA on "
          f"{np.mean([PROF[s][d.d]['val'] <= d.o[0] <= PROF[s][d.d]['vah'] for d in D[s][1:]])*100:.0f}% of days")
CLIP = {"rmin": 0.5, "rmax": 3.0}


def _mk(d, i, side, stop, p, tag, tgt=None):
    entry = d.c[i]
    stop = E._risk_clip(d, i, entry, stop, side, CLIP)
    if tgt is None:
        tgt = entry + side * p["rr"] * abs(entry - stop) if p.get("rr") else None
    return E.Sig(i, side, stop, tgt, p.get("trail", ""), p.get("be", 0), p.get("tstop", 0), tag)


def s_mp80(d, j, p, st):
    """Open outside yesterday's value area, price comes back and holds 2 closes inside -> target the far side of the VA."""
    pr = p["prof"].get(d.d)
    if not pr:
        return None
    vah, val, o = pr["vah"], pr["val"], d.o[0]
    if val <= o <= vah:
        return None
    side = 1 if o < val else -1
    inside = 0
    for i in range(1, len(d.t)):
        inside = inside + 1 if val <= d.c[i] <= vah else 0
        if inside >= 2 and i >= j and E._window_ok(d, i, p):
            entry, tgt = d.c[i], (vah if side > 0 else val)
            if side * (tgt - entry) <= 0:
                return None
            stop = (val if side > 0 else vah) - side * 0.25 * d.atr[i]
            return _mk(d, i, side, stop, p, "mp80", None if p.get("rr") else tgt)
        if inside == 0 and i >= j and side * (d.c[i] - (vah if side > 0 else val)) > 0:
            return None                                  # went straight through the VA: no 80% setup
    return None


def s_mp_accept(d, j, p, st):
    """Two consecutive closes beyond VAH / VAL after being inside -> momentum, 2xATR stop, 4R."""
    pr = p["prof"].get(d.d)
    if not pr:
        return None
    for i in range(max(j, 2), len(d.t)):
        if not E._window_ok(d, i, p):
            continue
        for side, lvl in ((1, pr["vah"]), (-1, pr["val"])):
            if side * (d.c[i] - lvl) > 0 and side * (d.c[i - 1] - lvl) > 0 and side * (d.c[i - 2] - lvl) <= 0:
                return _mk(d, i, side, d.c[i] - side * p.get("stop_atr", 2.0) * d.atr[i], p, "mp_accept")
    return None


def s_mp_reject(d, j, p, st):
    """Tail beyond VAH / VAL with the close back inside -> fade toward the POC, stop beyond the tail."""
    pr = p["prof"].get(d.d)
    if not pr:
        return None
    for i in range(max(j, 1), len(d.t)):
        if not E._window_ok(d, i, p):
            continue
        for side, lvl in ((-1, pr["vah"]), (1, pr["val"])):
            tail = d.h[i] > lvl if side < 0 else d.l[i] < lvl
            if not (tail and side * (d.c[i] - lvl) > 0):
                continue
            entry, tgt = d.c[i], pr["poc"]
            if side * (tgt - entry) <= 0:
                continue
            stop = (d.h[i] if side < 0 else d.l[i]) - side * 0.25 * d.atr[i]
            return _mk(d, i, side, stop, p, "mp_reject", None if p.get("rr") else tgt)
    return None


def s_mp_drive(d, j, p, st):
    """Open-drive: first 3 bars all one way with the open at the extreme; continuation at 09:45 if it still holds."""
    if j > 0 or len(d.t) < 7:
        return None
    o, c, h, l = d.o[:3], d.c[:3], d.h[:3], d.l[:3]
    tol = 0.1 * d.atr[2]
    up = all(cc > oo for cc, oo in zip(c, o)) and c[0] < c[1] < c[2] and min(l) >= d.o[0] - tol
    dn = all(cc < oo for cc, oo in zip(c, o)) and c[0] > c[1] > c[2] and max(h) <= d.o[0] + tol
    if not (up or dn):
        return None
    side = 1 if up else -1
    i = d.idx.get(580)                     # bar closing 09:45
    if i is None or side * (d.c[i] - c[2]) <= 0:
        return None
    return _mk(d, i, side, d.c[i] - side * p.get("stop_atr", 2.0) * d.atr[i], p, "mp_drive")


def dev_poc(d, upto, b):
    return profile(d.h[:upto + 1], d.l[:upto + 1], b)["poc"]


def s_noise_mig(d, j, p, st):
    """Noise rule, but only in the direction of today's developing POC vs yesterday's POC."""
    pr = p["prof"].get(d.d)
    if not pr:
        return None
    while True:
        s = E.s_noise(d, j, p, st)
        if s is None:
            return None
        if p.get("fixed90") and s.i < 17:          # developing POC of the first 90 min is only known from 10:45
            j = s.i + 1
            continue
        k = min(s.i, 17) if p.get("fixed90") else s.i
        dp = dev_poc(d, k, p["bucket"])
        bias = 1 if dp > pr["poc"] else -1 if dp < pr["poc"] else 0
        if s.side == bias:
            return s
        j = s.i + 1


E.STRATS.update({"mp80": s_mp80, "mp_accept": s_mp_accept, "mp_reject": s_mp_reject, "mp_drive": s_mp_drive, "noise_mig": s_noise_mig})
MP_BASE = dict(rr=4.0, stop_atr=2.0, be=0, max_trades=1, vix_min=11, otm=-1, first_entry=585, last_entry=870, ctx=-9)
RULES = {"80% rule (target far side of VA)": ("mp80", dict(MP_BASE, rr=None)),
         "80% rule, 4R target": ("mp80", dict(MP_BASE)),
         "acceptance beyond VAH/VAL (2 closes), 2xATR, 4R": ("mp_accept", dict(MP_BASE)),
         "rejection tail -> POC": ("mp_reject", dict(MP_BASE, rr=None)),
         "rejection tail, 4R": ("mp_reject", dict(MP_BASE)),
         "open-drive continuation, 2xATR, 4R": ("mp_drive", dict(MP_BASE))}
F = {"rules": {}, "filter5": {}}
for name, (strat, p) in RULES.items():
    row = {"by_sym": {}, "exits": {}}
    alltr = []
    for s in SYMS:
        tr = run_tagged(s, strat, dict(p, prof=PROF[s]), name)
        alltr += tr
        row["by_sym"][s] = stats(tr)
        row["exits"][s] = dict(pd.Series([t["why"] for t in tr]).value_counts()) if tr else {}
    row["both"] = stats(alltr)
    row["book_plus_rule"] = stats([t for s_ in SYMS for t in both(s_, extra=[(name, strat, dict(p, prof=PROF[s_]))])])
    F["rules"][name] = row
    show(f"  {name} (both)", row["both"])
    for s in SYMS:
        show(f"      {s}", row["by_sym"][s])
    show(f"      book + rule", row["book_plus_rule"], B)
    print("      exits:", row["exits"])
for name, extra in (("POC migration filter on noise (developing POC as of entry)", {}), ("POC migration filter on noise (fixed 90-min POC, entries from 10:45)", {"fixed90": 1})):
    out = []
    rule_only = []
    for s in SYMS:
        w = dict(W, prof=PROF[s], bucket=BUCKET[s], **extra)
        rule_only += run_tagged(s, "noise_mig", w, "noise")
        out += merge(run_tagged(s, "noise_mig", w, "noise") + run_tagged(s, "camarilla", CA, "camarilla"))
    F["filter5"][name] = {"noise_rule": stats(rule_only), "book": stats(out)}
    show(f"  {name}: noise rule", F["filter5"][name]["noise_rule"], OUT["baseline"]["by_rule"]["noise"])
    show(f"      book", F["filter5"][name]["book"], B)
# which side does migration pick, and how often does it agree with the noise signal?
OUT["F_profile"] = F

# ---------------------------------------------------------------- verdicts
V = {}
V["A_net"] = {k: [n for n, s in d_.items() if beats(s, B)] for k, d_ in A["schemes"].items()}
V["A_net_and_ratio"] = {k: [n for n, s in d_.items() if beats_ratio(s, B)] for k, d_ in A["schemes"].items()}
V["A_kelly"] = {k: {h: {"net": beats(A["kelly"][k][h], B), "net_and_ratio": beats_ratio(A["kelly"][k][h], B)} for h in ("full", "half")} for k in BUCKETS}
V["B"] = {k: Bsum[k]["winners"] for k in Bsum}
V["C"] = [k for k, r in C.items() if k != "-1" and beats(r["book"], B)]
V["D"] = [k for k, s in Dx["book"].items() if k != "max_trades 1" and beats(s, B)]
V["D_posthoc_cam_reenter_after_stop"] = beats(Dx["posthoc_camarilla_reenter_only_after_stop"], B)
V["E"] = {"noise_best_beats_default_both": Esum["noise"]["beats_default_both"], "cam_best_beats_default_both": Esum["camarilla"]["beats_default_both"],
          "bn_book_tuned_beats_both": beats(bn_book_tuned, bn_base_book), "full_book_beats_both": beats(full_tuned, B)}
V["F"] = {"rules_profitable_both_halves": [n for n, r in F["rules"].items() if r["both"]["train"]["net"] > 0 and r["both"]["unseen"]["net"] > 0],
          "book_plus_rule_beats_base": [n for n, r in F["rules"].items() if beats(r["book_plus_rule"], B)],
          "filter5_beats_base": [n for n, r in F["filter5"].items() if beats(r["book"], B)]}
OUT["verdicts"] = V
OUT["runtime_s"] = round(time.time() - T0)
print("\n=== VERDICTS (beats the baseline book on BOTH training and unseen)")
for k, v in V.items():
    print(f"  {k}: {v}")
json.dump(OUT, open("round13.json", "w"), default=str, indent=1)
print(f"wrote round13.json  ({OUT['runtime_s']}s)")
