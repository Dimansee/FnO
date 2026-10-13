"""Round 14A — more entries per day, done carefully: re-entry after a stop, pyramiding, trend-day-confirmed extras.

Baseline = the round 6 book (noise W + Camarilla CA, both indices, 1 trade per rule per day, one open position per index;
`round6.both()` merge rule). Option P&L via engine.trade_pnl (1 ITM, calibrated premiums, slippage, full charges), ₹2 lakh, 1 % risk.
Training = sessions before SPLIT, unseen = the last 120 sessions. Nothing is picked on the unseen half.
"Beats" = more net AND a better net/maxDD than the baseline on BOTH halves.

1. re-entry ONLY after a stop-loss exit (prev why == "stop"), same rule, direction any / same / opposite; one re-entry max;
   never the same option contract (strike + type) twice in a day
2. the same, but the re-entry signal must come within 60 / 120 minutes of the stop
3. pyramiding the first trade of the day: 2nd unit at +1R, 3rd at +2R (fills at the level, slippage applied); on each add the stop of
   every unit moves to the previous add level (breakeven on the first add); exits otherwise per the rule. Sizes 1:1:1 and 1:0.5:0.5.
4. 3rd / 4th entries only on days whose first trade hit target or closed >= +2R ("trend day confirmed"), versus on every day
5. pyramiding + re-entry-after-stop combined
+ a sanity table: P&L per attempt number, per previous-exit reason, per rule (uncapped run, 4 trades per index-day, same-option rule)

Run from research/ with FNO_CALIB=1. Writes round14a.json; pipe stdout to round14a.log.
W / CA / SPLIT / stats are copied from round6.py (importing round6 re-runs its whole study and rewrites round6_checks.json).
"""
import json, math, os, sys, time

os.environ.setdefault("FNO_CALIB", "1")
import numpy as np
import pandas as pd

import engine as E
from fno import indicators as I

T0 = time.time()
SYMS = ("NIFTY", "BANKNIFTY")
RULES = ("noise", "camarilla")
W = dict(mult=1.75, stop_atr=2.0, rr=4.0, vwap=1, max_trades=1, vix_min=11, last_entry=870, otm=-1,
         be=0, ctx=-9, rmin=1.0, rmax=2.5)
CA = {'rr': None, 'be': 1.0, 'tstop': 45, 'ctx': -9, 'otm': -1, 'last_entry': 780, 'max_trades': 1, 'rmin': 1.0,
      'rmax': 3.0, 'vix_min': 11, 'vix_max': 22, 'mode': 'break', 'trail': ''}
P = {"noise": W, "camarilla": CA}
D = {s: E.load_days(s) for s in SYMS}
DATES = [d.d for d in D["NIFTY"]]
SPLIT = DATES[-120]
TRAIN_DATES = [d for d in DATES if d < SPLIT]
UNSEEN_DATES = [d for d in DATES if d >= SPLIT]
HALVES = ("train", "unseen")
OUT = {"split": str(SPLIT), "train_days": len(TRAIN_DATES), "unseen_days": len(UNSEEN_DATES)}
print(f"days {len(DATES)}  training {TRAIN_DATES[0]}..{TRAIN_DATES[-1]} ({len(TRAIN_DATES)})  unseen {SPLIT}..{DATES[-1]} ({len(UNSEEN_DATES)})  "
      f"(load {time.time()-T0:.0f}s)", flush=True)


def r6_stats(tr):
    """round6.stats, verbatim."""
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


def r6_both(sym):
    """round6.both(): 1 trade per rule per day, one open position per index. Used only to check the replica below."""
    tr = [dict(t, strat="noise", sym=sym) for t in E.run(D[sym], sym, "noise", W)] + \
         [dict(t, strat="camarilla", sym=sym) for t in E.run(D[sym], sym, "camarilla", CA)]
    tr.sort(key=lambda t: (t["date"], t["in"]))
    out, busy = [], {}
    for t in tr:
        if busy.get(t["date"], 0) > t["in"]:
            continue
        out.append(t)
        busy[t["date"]] = t["out"]
    return out


ST = {s: {} for s in SYMS}       # strategy scratch state (noise bands), like E.run's `st`
MEMO = {}                        # (sym, date, rule, j) -> first trade of `rule` scanning from bar j


def strike_of(t, meta, p):
    return round(t["spot_in"] / meta["step"]) * meta["step"] + t["side"] * p.get("otm", 0) * meta["step"]


def first(sym, d, rule, j):
    """engine.run's inner loop (filters / unsizable -> keep scanning) from bar j; returns the trade dict with the Sig and
    exit bar attached. Memoised: the same (day, rule, j) is asked by every variant."""
    key = (sym, d.d, rule, j)
    if key in MEMO:
        return MEMO[key]
    p, meta, fn = P[rule], E.META[sym], E.STRATS[rule]
    res = None
    if p.get("vix_min", 0) <= d.vix[0] <= p.get("vix_max", 99):
        while j < len(d.t):
            s = fn(d, j, p, ST[sym])
            if s is None:
                break
            if p.get("filters") and not all(E.FILTERS[f](d, s.i, s.side) for f in p["filters"]):
                j = s.i + 1
                continue
            up = lo = None
            if s.trail == "noise":
                up, lo = ST[sym][("nb", d.d, p["mult"])]
            k, px, why = E.simulate(d, s, meta, p, up, lo)
            tr = E.trade_pnl(d, s, k, px, meta, p)
            if tr:
                tr.update(why=why, strat=rule, sym=sym, i=s.i, k=k, sig=s, px_exit=px, strike=strike_of(tr, meta, p),
                          charges=I.charges(tr["prem_in"], tr["prem_out"], tr["lots"] * meta["lot"]))
                res = tr
                break
            j = s.i + 1
    MEMO[key] = res
    return res


def fresh(sym, d, rule, j, side_req=None, used=None):
    """First signal of `rule` at/after bar j that satisfies the direction / never-the-same-option constraints."""
    while j < len(d.t):
        t = first(sym, d, rule, j)
        if t is None:
            return None
        if (side_req is None or t["side"] == side_req) and (used is None or (t["side"], t["strike"]) not in used):
            return t
        j = t["i"] + 1
    return None


# ---------------------------------------------------------------- pyramiding
def price_unit(d, sym, side, strike, px_in, i_in, k_exit, px_exit, lots):
    """engine.trade_pnl's pricing with the contract and the lot count given (add units: same contract as unit 1,
    size = a fraction of unit 1's lots, not sized off their own risk)."""
    meta, opt = E.META[sym], ("CE" if side > 0 else "PE")
    m_in, m_out = d.t[i_in] + 5, d.t[k_exit] + 5
    iv_in = E.iv_for(sym, d, d.vix[i_in], px_in, strike, opt)
    iv_out = E.iv_for(sym, d, d.vix[k_exit], px_exit, strike, opt)
    prem_in = E.opt_price(px_in, strike, d, m_in, iv_in, opt) * (1 + E.SLIP)
    prem_out = E.opt_price(px_exit, strike, d, m_out, iv_out, opt)
    prem_out = E.real_adjust(prem_in / (1 + E.SLIP), prem_out, (d.expiry - d.d).days, (m_out - m_in) / 60) * (1 - E.SLIP)
    qty = lots * meta["lot"]
    ch = I.charges(prem_in, prem_out, qty)
    return {"prem_in": prem_in, "prem_out": prem_out, "lots": lots, "pnl": (prem_out - prem_in) * qty - ch, "charges": ch, "in": m_in}


def reach_bar(d, side, entry, lvl, k_from, k_to):
    """First bar in [k_from, k_to) whose favourable extreme touches `lvl`."""
    for k in range(k_from, k_to):
        fav = d.h[k] if side > 0 else d.l[k]
        if side * (fav - lvl) >= 0:
            return k
    return None


LEVELS = (1.0, 2.0)


def pyramid(t, d, fracs):
    """Add a unit when the underlying reaches entry + 1R (then + 2R), filled at that level (or the bar's open if it gapped past),
    same option contract as unit 1, lots = frac x unit-1 lots (rounded, min 1 when the base is 1 lot), total outlay capped at 60 % of
    capital. On each add the stop of every unit moves to the previous add level (breakeven on the first add); the rest of the exit
    logic (trail / target / 15:15) is the rule's own, re-simulated with engine.simulate from the add bar."""
    s, p, meta, sym = t["sig"], P[t["strat"]], E.META[t["sym"]], t["sym"]
    side, entry = s.side, (s.px if s.px is not None else d.c[s.i])
    risk = abs(entry - s.stop)
    up = lo = None
    if s.trail == "noise":
        up, lo = ST[sym][("nb", d.d, p["mult"])]
    k_exit, px_exit, why = t["k"], t["px_exit"], t["why"]
    k_prev, units, outlay, capped = s.i, [], t["prem_in"] * t["lots"] * meta["lot"], 0
    for n, lvl in enumerate(LEVELS):
        ka = reach_bar(d, side, entry, entry + side * lvl * risk, k_prev + 1, k_exit)
        if ka is None:
            break
        add_px = entry + side * lvl * risk
        if side * (d.o[ka] - add_px) > 0:                 # gapped through the level: filled at the open
            add_px = d.o[ka]
        new_stop = entry if n == 0 else entry + side * LEVELS[n - 1] * risk
        want = int(t["lots"] * fracs[n] + 0.5)
        iv = E.iv_for(sym, d, d.vix[ka], add_px, t["strike"], "CE" if side > 0 else "PE")
        prem = E.opt_price(add_px, t["strike"], d, d.t[ka] + 5, iv, "CE" if side > 0 else "PE") * (1 + E.SLIP)
        lots = min(want, int((0.6 * E.CAPITAL - outlay) // (prem * meta["lot"])))
        if lots < want:
            capped += 1
        if lots <= 0:
            break
        s2 = E.Sig(ka, side, new_stop, s.target, s.trail, 0, 0, s.tag + "_add", px=add_px)
        k_exit, px_exit, why = E.simulate(d, s2, meta, p, up, lo)
        if why in ("stop", "breakeven"):
            why = f"add-stop@{'be' if n == 0 else f'+{LEVELS[n-1]:g}R'}"
        units.append((ka, add_px, lots))
        outlay += prem * lots * meta["lot"]
        k_prev = ka
    if not units:
        return dict(t, adds=0, pnl_plain=t["pnl"], entries=1, capped=0)
    u1 = E.trade_pnl(d, s, k_exit, px_exit, meta, p)
    parts = [dict(u1, charges=I.charges(u1["prem_in"], u1["prem_out"], u1["lots"] * meta["lot"]))] + \
            [price_unit(d, sym, side, t["strike"], add_px, ka, k_exit, px_exit, lots) for ka, add_px, lots in units]
    return dict(t, pnl=sum(x["pnl"] for x in parts), charges=sum(x["charges"] for x in parts), out=d.t[k_exit] + 5, k=k_exit,
                px_exit=px_exit, why=why, why_plain=t["why"], adds=len(units), pnl_plain=t["pnl"], entries=1 + len(units),
                unit_pnl=[round(x["pnl"]) for x in parts], add_lots=[x["lots"] for x in parts[1:]], capped=capped,
                R=side * (px_exit - entry) / risk)


# ---------------------------------------------------------------- day loop
def confirmed(t0):
    return t0["why"] == "target" or t0["R"] >= 2.0


def day_book(sym, d, cfg):
    """Baseline behaviour: each rule's first signal from bar 0; one open position per index; a candidate that starts while a
    position is open is dropped (the rule does not re-scan). cfg adds re-scans after an exit:
      reentry = dict(dir 'any'|'same'|'opp', window minutes|None, max, rules whose stop fires it, scope 'same'|'either')
      extra   = dict(when 'confirmed'|'always'|'unconfirmed', same_dir bool, max)
      pyramid = dict(fracs (f2, f3), all bool)   -- on the first trade of the day (all: on every trade)"""
    re_, ex, py = cfg.get("reentry"), cfg.get("extra"), cfg.get("pyramid")
    cand = {r: (first(sym, d, r, 0), "base") for r in RULES}
    out, busy, used, n_re, n_ex = [], -1, set(), 0, 0
    while any(v[0] is not None for v in cand.values()):
        r = min((q for q in RULES if cand[q][0] is not None), key=lambda q: (cand[q][0]["in"], RULES.index(q)))
        t, kind = cand[r]
        cand[r] = (None, None)
        if t["in"] < busy:
            continue
        t = dict(t, kind=kind, attempt=len(out) + 1, prev_why=out[-1]["why"] if out else "-", adds=0, pnl_plain=t["pnl"], entries=1, capped=0)
        if py and (not out or py.get("all")) and t["strat"] in py.get("rules", RULES):
            t = pyramid(t, d, py["fracs"])
        out.append(t)
        busy = t["out"]
        used.add((t["side"], t["strike"]))
        n_re += kind == "re"
        n_ex += kind == "extra"
        rescans = []                                   # (rule, side_req, window, kind)
        if re_ and t["why"] == "stop" and n_re < re_["max"] and t["strat"] in re_.get("rules", RULES):
            side_req = t["side"] if re_["dir"] == "same" else -t["side"] if re_["dir"] == "opp" else None
            for q in (RULES if re_.get("scope") == "either" else [r]):
                rescans.append((q, side_req, re_.get("window"), "re"))
        if ex and n_ex < ex["max"]:
            c = confirmed(out[0])
            if ex["when"] == "always" or (ex["when"] == "confirmed") == c:
                for q in RULES:
                    rescans.append((q, out[0]["side"] if ex.get("same_dir") else None, None, "extra"))
        for q, side_req, window, kind in rescans:
            cur = cand[q][0]
            if cur is not None and cur["in"] > t["out"]:
                continue                               # that rule's own (baseline) signal is still ahead: keep it
            if cur is not None and cur["in"] < busy:
                cand[q] = (None, None)                 # stale: it would be dropped on pop anyway
            nt = fresh(sym, d, q, t["k"] + 1, side_req, used)
            if nt is not None and window and nt["in"] - t["out"] > window:
                nt = None
            if nt is not None and (cand[q][0] is None or nt["in"] < cand[q][0]["in"]):
                cand[q] = (nt, kind)
    return out


def book(cfg):
    return [t for s in SYMS for d in D[s] for t in day_book(s, d, cfg)]


# ---------------------------------------------------------------- stats
def half(tr):
    s = r6_stats(tr)
    s.pop("unseen120", None); s.pop("years", None)
    if not tr:
        return dict(s, pf=None, max_dd=0, net_per_dd=None, charges=0, per_day=0, max_per_day=0, re=0, extra=0, adds=0)
    per = pd.Series([f"{t['sym']}|{t['date']}" for t in tr]).value_counts()
    return dict(s, net_per_dd=round(s["net"] / -s["max_dd"], 2) if s["max_dd"] < 0 else None,
                charges=round(sum(t["charges"] for t in tr)), per_day=round(float(per.mean()), 2), max_per_day=int(per.max()),
                re=int(sum(t.get("kind") == "re" for t in tr)), extra=int(sum(t.get("kind") == "extra" for t in tr)),
                adds=int(sum(t.get("adds", 0) for t in tr)))


def vc(xs):
    return {str(k): int(v) for k, v in pd.Series(list(xs)).value_counts().items()} if len(xs) else {}


def stats(tr):
    return {"train": half([t for t in tr if t["date"] < SPLIT]), "unseen": half([t for t in tr if t["date"] >= SPLIT]),
            "years": {y: round(float(sum(t["pnl"] for t in tr if t["date"].year == y))) for y in (2023, 2024, 2025, 2026)},
            "exits": vc([t["why"] for t in tr])}


def beats(s, b=None):
    b = b or B
    return bool(all(s[h]["net"] > b[h]["net"] and (s[h]["net_per_dd"] or -9) > (b[h]["net_per_dd"] or -9) for h in HALVES))


def train_better(s, b=None):
    b = b or B
    return bool(s["train"]["net"] > b["train"]["net"] and (s["train"]["net_per_dd"] or -9) > (b["train"]["net_per_dd"] or -9))


def show(name, s, base=True):
    t, u = s["train"], s["unseen"]
    flag = ""
    if base:
        flag = "  <-- BEATS base" if beats(s) else ("  (train better, unseen not)" if train_better(s) else "")
    print(f"{name:58s} train n{t['trades']:4d} ₹{t['net']/1000:+7.1f}k pf {t['pf'] or 0:.2f} dd ₹{-t['max_dd']/1000:3.0f}k n/dd {t['net_per_dd'] or 0:4.2f} | "
          f"unseen n{u['trades']:3d} ₹{u['net']/1000:+6.1f}k pf {u['pf'] or 0:.2f} dd ₹{-u['max_dd']/1000:3.0f}k n/dd {u['net_per_dd'] or 0:4.2f} | "
          f"chg ₹{(t['charges']+u['charges'])/1000:3.0f}k  re {t['re']+u['re']:3d} extra {t['extra']+u['extra']:3d} adds {t['adds']+u['adds']:3d}{flag}", flush=True)


def sub(tr, kind):
    """P&L of the added trades only (the re-entries / extras), train / unseen."""
    x = [t for t in tr if t.get("kind") == kind]
    return {"n": len(x), "net": round(sum(t["pnl"] for t in x)), "train_net": round(sum(t["pnl"] for t in x if t["date"] < SPLIT)),
            "unseen_net": round(sum(t["pnl"] for t in x if t["date"] >= SPLIT)), "unseen_n": sum(t["date"] >= SPLIT for t in x),
            "win": round(float(np.mean([t["pnl"] > 0 for t in x]) * 100), 1) if x else None,
            "by_rule": {r: round(sum(t["pnl"] for t in x if t["strat"] == r)) for r in RULES}, "exits": vc([t["why"] for t in x])}


def fmt_sub(a):
    return f"n{a['n']} net ₹{a['net']:+,} (train ₹{a['train_net']:+,} / unseen ₹{a['unseen_net']:+,} n{a['unseen_n']}) win {a['win']}% by rule {a['by_rule']} exits {a['exits']}"


def scaled_line(s):
    """Baseline P&L x the variant's average units per trade (lot rounding ignored): is the extra net more than the extra size?"""
    mult = {h: (s[h]["trades"] + s[h]["adds"]) / max(s[h]["trades"], 1) for h in HALVES}
    sc = {h: {"net": round(B[h]["net"] * mult[h]), "max_dd": round(B[h]["max_dd"] * mult[h]), "units_per_trade": round(mult[h], 2)} for h in HALVES}
    s["baseline_scaled_to_same_units"] = sc
    s["beats_scaled"] = bool(all(s[h]["net"] > sc[h]["net"] and (s[h]["net_per_dd"] or -9) > (B[h]["net_per_dd"] or -9) for h in HALVES))
    print(f"      baseline x this variant's average units/trade: train x{mult['train']:.2f} -> ₹{sc['train']['net']/1000:+.1f}k dd ₹{-sc['train']['max_dd']/1000:.0f}k | "
          f"unseen x{mult['unseen']:.2f} -> ₹{sc['unseen']['net']/1000:+.1f}k dd ₹{-sc['unseen']['max_dd']/1000:.0f}k"
          + ("   <-- beats the SCALED base on both" if s["beats_scaled"] else "   (does not beat the scaled base on both)"))


# ---------------------------------------------------------------- baseline (must equal round6.book())
BASE = book({})
B = stats(BASE)
_r6 = [t for s in SYMS for t in r6_both(s)]
assert len(BASE) == len(_r6) and abs(sum(t["pnl"] for t in BASE) - sum(t["pnl"] for t in _r6)) < 1e-6, (len(BASE), len(_r6))
assert all(a["date"] == b["date"] and a["in"] == b["in"] and abs(a["pnl"] - b["pnl"]) < 1e-9 for a, b in zip(BASE, _r6))
# price_unit must reproduce trade_pnl's premiums on the base contract
for t in BASE[:200]:
    d = {x.d: x for x in D[t["sym"]]}[t["date"]]
    u = price_unit(d, t["sym"], t["side"], t["strike"], t["spot_in"], t["i"], t["k"], t["px_exit"], t["lots"])
    assert abs(u["prem_in"] - t["prem_in"]) < 1e-9 and abs(u["prem_out"] - t["prem_out"]) < 1e-9 and abs(u["pnl"] - t["pnl"]) < 1e-6
OUT["baseline"] = {"book": B, "by_rule": {r: stats([t for t in BASE if t["strat"] == r]) for r in RULES},
                   "by_sym": {s: stats([t for t in BASE if t["sym"] == s]) for s in SYMS}}
print("\n=== BASELINE (round 6 book; the day loop here reproduces round6.both() trade-for-trade; price_unit == trade_pnl on 200 trades)")
show("baseline book", B, base=False)
for r in RULES:
    show(f"  {r}", OUT["baseline"]["by_rule"][r], base=False)
for s in SYMS:
    show(f"  {s}", OUT["baseline"]["by_sym"][s], base=False)
print("  exits:", B["exits"], " years:", B["years"])
print(f"  beats = net > train ₹{B['train']['net']:,} / unseen ₹{B['unseen']['net']:,} AND net/maxDD > {B['train']['net_per_dd']} / {B['unseen']['net_per_dd']}")
lots1 = np.mean([t["lots"] == 1 for t in BASE]) * 100
print(f"  lots per trade: 1 lot on {lots1:.0f}% of trades, mean {np.mean([t['lots'] for t in BASE]):.2f} (so '1:0.5:0.5' is 1:1:1 on 1-lot trades)")


# ---------------------------------------------------------------- 1. re-entry only after a stop
print("\n=== 1. re-entry only after a STOP exit, same rule, one re-entry max, never the same option contract twice")
R1 = {}
for rules in (RULES, ("noise",), ("camarilla",)):
    for dr in ("any", "same", "opp"):
        name = f"re-entry after stop: {'+'.join(rules)}, direction {dr}"
        cfg = {"reentry": {"dir": dr, "window": None, "max": 1, "rules": set(rules), "scope": "same"}}
        tr = book(cfg)
        s = stats(tr)
        s["reentries"] = sub(tr, "re")
        s["cfg"] = {"rules": list(rules), "dir": dr, "window": None, "max": 1}
        s["beats"] = beats(s)
        R1[name] = s
        show(name, s)
        print("      re-entries:", fmt_sub(s["reentries"]))
OUT["item1_reentry_after_stop"] = R1
print(f"  ({time.time()-T0:.0f}s)")

# ---------------------------------------------------------------- 2. ... within 60 / 120 minutes of the stop
print("\n=== 2. re-entry after a stop only if the signal comes within 60 / 120 minutes of the stop")
R2 = {}
for rules in (RULES, ("noise",), ("camarilla",)):
    for dr in ("any", "same", "opp"):
        for win in (60, 120):
            name = f"re-entry <= {win} min after stop: {'+'.join(rules)}, direction {dr}"
            cfg = {"reentry": {"dir": dr, "window": win, "max": 1, "rules": set(rules), "scope": "same"}}
            tr = book(cfg)
            s = stats(tr)
            s["reentries"] = sub(tr, "re")
            s["cfg"] = {"rules": list(rules), "dir": dr, "window": win, "max": 1}
            s["beats"] = beats(s)
            R2[name] = s
            show(name, s)
            print("      re-entries:", fmt_sub(s["reentries"]))
# how long after a stop does the next same-rule signal come? (unwindowed, direction any, both rules)
tr_any = book({"reentry": {"dir": "any", "window": None, "max": 1, "rules": set(RULES), "scope": "same"}})
byday = {}
for t in tr_any:
    byday.setdefault((t["sym"], t["date"]), []).append(t)
gaps = [t["in"] - lst[i - 1]["out"] for lst in byday.values() for i, t in enumerate(lst) if i and t.get("kind") == "re"]
R2["minutes_from_stop_to_reentry"] = {"n": len(gaps), "median": float(np.median(gaps)) if gaps else None,
                                      "share_within_60": round(float(np.mean([g <= 60 for g in gaps]) * 100), 1) if gaps else None,
                                      "share_within_120": round(float(np.mean([g <= 120 for g in gaps]) * 100), 1) if gaps else None}
print("  minutes from the stop to the re-entry signal (direction any, both rules):", R2["minutes_from_stop_to_reentry"])
OUT["item2_reentry_window"] = R2
print(f"  ({time.time()-T0:.0f}s)")

# ---------------------------------------------------------------- 3. pyramiding the first trade
print("\n=== 3. pyramiding the first trade of the day: +1 unit at +1R, +1 at +2R; stop of all units -> previous add level")
R3 = {}
# share of first trades that ever reach +1R / +2R before their own exit (the add trigger), by rule
reach = {}
for t in [x for x in BASE if x["attempt"] == 1]:
    d = {x.d: x for x in D[t["sym"]]}[t["date"]]
    s_, entry = t["sig"], (t["sig"].px if t["sig"].px is not None else d.c[t["sig"].i])
    risk = abs(entry - s_.stop)
    r1 = reach_bar(d, t["side"], entry, entry + t["side"] * 1.0 * risk, s_.i + 1, t["k"]) is not None
    r2 = r1 and reach_bar(d, t["side"], entry, entry + t["side"] * 2.0 * risk, s_.i + 1, t["k"]) is not None
    for key in ("all", t["strat"], t["sym"], "train" if t["date"] < SPLIT else "unseen"):
        g = reach.setdefault(key, {"n": 0, "r1": 0, "r2": 0})
        g["n"] += 1; g["r1"] += r1; g["r2"] += r2
R3["reach"] = {k: {"n": v["n"], "reach_1R_pct": round(v["r1"] / v["n"] * 100, 1), "reach_2R_pct": round(v["r2"] / v["n"] * 100, 1)} for k, v in reach.items()}
print("  share of first trades that reach +1R / +2R before their exit:", R3["reach"])
for rules_lab, py_rules in (("both rules", RULES), ("noise only", ("noise",)), ("camarilla only", ("camarilla",))):
    for fr_lab, fracs in (("1:1:1", (1.0, 1.0)), ("1:0.5:0.5", (0.5, 0.5))):
        name = f"pyramid first trade {rules_lab}, sizes {fr_lab}"
        tr = book({"pyramid": {"fracs": fracs, "rules": set(py_rules)}})
        s = stats(tr)
        pyr = [t for t in tr if t["attempt"] == 1 and t["strat"] in py_rules]
        added = [t for t in pyr if t["adds"]]
        by_why = {}
        for t in added:
            g = by_why.setdefault(t["why"], {"n": 0, "combined": 0.0, "plain": 0.0})
            g["n"] += 1; g["combined"] += t["pnl"]; g["plain"] += t["pnl_plain"]
        s["pyramid"] = {"first_trades": len(pyr), "with_1_add": sum(t["adds"] == 1 for t in added), "with_2_adds": sum(t["adds"] == 2 for t in added),
                        "capped_by_cash": sum(t["capped"] for t in added),
                        "plain_net_on_added": round(sum(t["pnl_plain"] for t in added)), "pyramided_net_on_added": round(sum(t["pnl"] for t in added)),
                        "delta_train": round(sum(t["pnl"] - t["pnl_plain"] for t in added if t["date"] < SPLIT)),
                        "delta_unseen": round(sum(t["pnl"] - t["pnl_plain"] for t in added if t["date"] >= SPLIT)),
                        "by_final_exit": {k: {"n": v["n"], "pyramided": round(v["combined"]), "plain": round(v["plain"])} for k, v in by_why.items()},
                        "plain_exit_of_added": vc([t["why_plain"] for t in added]),
                        "mean_add_lots": round(float(np.mean([l for t in added for l in t["add_lots"]])), 2) if added else None}
        s["cfg"] = {"rules": list(py_rules), "fracs": list(fracs)}
        s["beats"] = beats(s)
        # concentration of the gain, and the baseline scaled to the same average number of units (P&L x multiplier, no lot rounding)
        for h, lo_, hi_ in (("train", None, SPLIT), ("unseen", SPLIT, None)):
            dl = sorted([(t["pnl"] - t["pnl_plain"], t) for t in added if (lo_ is None or t["date"] >= lo_) and (hi_ is None or t["date"] < hi_)], key=lambda x: -x[0])
            tot = sum(x for x, _ in dl)
            s["pyramid"][f"top5_delta_{h}"] = [{"date": str(t["date"]), "sym": t["sym"], "rule": t["strat"], "side": t["side"], "adds": t["adds"], "exit": t["why"],
                                                 "R": round(t["R"], 2), "plain": round(t["pnl_plain"]), "pyramided": round(t["pnl"]), "units": t["unit_pnl"]} for x, t in dl[:5]]
            s["pyramid"][f"top5_share_of_delta_{h}_pct"] = round(sum(x for x, _ in dl[:5]) / tot * 100, 1) if tot else None
        scaled_line(s)
        R3[name] = s
        show(name, s)
        py = s["pyramid"]
        for h in HALVES:
            print(f"      {h}: top-5 trades = {py[f'top5_share_of_delta_{h}_pct']}% of the pyramid gain; " +
                  "; ".join(f"{x['date']} {x['sym'][:2]} {x['rule'][:3]} {'+' if x['side'] > 0 else '-'} adds {x['adds']} {x['exit']} R {x['R']} plain ₹{x['plain']:,} -> ₹{x['pyramided']:,} units {x['units']}" for x in py[f"top5_delta_{h}"]))
        print(f"      adds on {py['with_1_add']+py['with_2_adds']}/{py['first_trades']} first trades (1 add {py['with_1_add']}, 2 adds {py['with_2_adds']}, cash-capped {py['capped_by_cash']}, "
              f"mean add lots {py['mean_add_lots']}); on those trades pyramided ₹{py['pyramided_net_on_added']:+,} vs plain ₹{py['plain_net_on_added']:+,} "
              f"(delta train ₹{py['delta_train']:+,} / unseen ₹{py['delta_unseen']:+,})")
        print(f"      final exit of pyramided trades: {py['by_final_exit']}  (their plain exits: {py['plain_exit_of_added']})")
OUT["item3_pyramid"] = R3
print(f"  ({time.time()-T0:.0f}s)")

# ---------------------------------------------------------------- 4. 3rd / 4th entries only on confirmed trend days
print("\n=== 4. extra (3rd / 4th) entries, either rule, same-option rule: only on days whose first trade hit target or closed >= +2R, vs always")
R4 = {}
n_conf = {h: 0 for h in HALVES}; n_days = {h: 0 for h in HALVES}
for sym in SYMS:
    for d in D[sym]:
        day = day_book(sym, d, {})
        if day:
            h = "train" if d.d < SPLIT else "unseen"
            n_days[h] += 1; n_conf[h] += confirmed(day[0])
R4["confirmed_days"] = {h: {"index_days_with_a_trade": n_days[h], "confirmed": n_conf[h], "pct": round(n_conf[h] / n_days[h] * 100, 1)} for h in HALVES}
print("  index-days whose first trade confirmed a trend (target or >= +2R):", R4["confirmed_days"])
for when in ("confirmed", "always", "unconfirmed"):
    for same_dir in (True, False):
        for mx in (1, 2):
            name = f"extras on {when} days, {'same dir as 1st' if same_dir else 'any dir'}, max {mx}"
            cfg = {"extra": {"when": when, "same_dir": same_dir, "max": mx}}
            tr = book(cfg)
            s = stats(tr)
            s["extras"] = sub(tr, "extra")
            s["cfg"] = {"when": when, "same_dir": same_dir, "max": mx}
            s["beats"] = beats(s)
            R4[name] = s
            show(name, s)
            print("      extras:", fmt_sub(s["extras"]))
OUT["item4_trend_day_extras"] = R4
print(f"  ({time.time()-T0:.0f}s)")

# ---------------------------------------------------------------- 5. pyramiding + re-entry after stop
print("\n=== 5. pyramiding (first trade) + re-entry after a stop, combined; re-entry variant = best TRAINING net among items 1-2")


def pick(res):
    ok = {k: v for k, v in res.items() if isinstance(v, dict) and "cfg" in v and train_better(v)}
    if not ok:
        return None, None
    k = max(ok, key=lambda k: ok[k]["train"]["net"])
    return k, ok[k]


k_re, v_re = pick({**R1, **R2})
k_py, v_py = pick(R3)
R5 = {"picked_reentry": k_re, "picked_pyramid": k_py}
print(f"  re-entry pick (training): {k_re}" + (f"  train ₹{v_re['train']['net']:+,} unseen ₹{v_re['unseen']['net']:+,}" if v_re else " (none beats the base on training)"))
print(f"  pyramid pick (training):  {k_py}" + (f"  train ₹{v_py['train']['net']:+,} unseen ₹{v_py['unseen']['net']:+,}" if v_py else " (none beats the base on training)"))
re_cfg = dict(v_re["cfg"], rules=set(v_re["cfg"]["rules"]), scope="same") if v_re else {"dir": "any", "window": None, "max": 1, "rules": set(RULES), "scope": "same"}
R5["reentry_cfg_used"] = {k: (sorted(v) if isinstance(v, set) else v) for k, v in re_cfg.items()}
py_rules = set(v_py["cfg"]["rules"]) if v_py else set(RULES)
R5["pyramid_rules_used"] = sorted(py_rules)
print(f"  re-entry config used: {R5['reentry_cfg_used']}   pyramid rules used: {sorted(py_rules)}")
for fr_lab, fracs in (("1:1:1", (1.0, 1.0)), ("1:0.5:0.5", (0.5, 0.5))):
    for all_lab, all_ in (("pyramid 1st trade only", False), ("pyramid every trade incl. re-entry", True)):
        name = f"combined: re-entry[{'+'.join(sorted(re_cfg['rules']))}, {re_cfg['dir']}, win {re_cfg['window']}] + pyramid {fr_lab}, {all_lab}"
        tr = book({"reentry": re_cfg, "pyramid": {"fracs": fracs, "all": all_, "rules": py_rules}})
        s = stats(tr)
        s["reentries"] = sub(tr, "re")
        s["cfg"] = {"reentry": R5["reentry_cfg_used"], "pyramid_rules": sorted(py_rules), "fracs": list(fracs), "pyramid_all": all_}
        s["beats"] = beats(s)
        s["by_sym"] = {sym: stats([t for t in tr if t["sym"] == sym]) for sym in SYMS}
        R5[name] = s
        show(name, s)
        scaled_line(s)
        print("      re-entries:", fmt_sub(s["reentries"]))
OUT["item5_combined"] = R5
print(f"  ({time.time()-T0:.0f}s)")

# ---------------------------------------------------------------- sanity table
print("\n=== SANITY: uncapped run (re-scan after ANY exit, either rule, up to 4 trades per index-day, same-option rule, any direction)")
tr = book({"extra": {"when": "always", "same_dir": False, "max": 2}})
df = pd.DataFrame([{k: t[k] for k in ("date", "sym", "strat", "pnl", "why", "prev_why", "attempt")} for t in tr])
df["half"] = np.where(df["date"] < SPLIT, "train", "unseen")


def table(g):
    return {str(k): {"n": int(len(x)), "net": round(float(x.pnl.sum())), "per_trade": round(float(x.pnl.mean())), "win": round(float((x.pnl > 0).mean() * 100), 1),
                     "train_net": round(float(x[x.half == "train"].pnl.sum())), "unseen_net": round(float(x[x.half == "unseen"].pnl.sum())),
                     "unseen_n": int((x.half == "unseen").sum())} for k, x in g}


def ptable(title, tb):
    print(f"  {title}")
    for k, v in tb.items():
        print(f"    {k:34s} n{v['n']:4d} net ₹{v['net']/1000:+7.1f}k ₹{v['per_trade']:+6d}/trade win {v['win']:4.1f}%  train ₹{v['train_net']/1000:+6.1f}k  unseen ₹{v['unseen_net']/1000:+5.1f}k (n{v['unseen_n']})")


SAN = {"book": stats(tr), "by_attempt": table(df.groupby("attempt")), "by_prev_why": table(df[df.attempt > 1].groupby("prev_why")),
       "by_rule_attempt": table(df.groupby(["strat", "attempt"])), "by_rule_prev_why": table(df[df.attempt > 1].groupby(["strat", "prev_why"]))}
show("uncapped book (4/index-day, any exit)", SAN["book"])
ptable("by attempt number:", SAN["by_attempt"])
ptable("attempts 2+ by the previous trade's exit reason:", SAN["by_prev_why"])
ptable("by rule x attempt:", SAN["by_rule_attempt"])
ptable("attempts 2+ by rule x previous exit reason:", SAN["by_rule_prev_why"])
OUT["sanity"] = SAN

# ---------------------------------------------------------------- verdicts
print("\n=== VERDICTS (beats = more net AND better net/maxDD than the baseline on BOTH halves)")
V = []
for it, res in (("1 re-entry after stop", R1), ("2 re-entry within window", R2), ("3 pyramid", R3), ("4 trend-day extras", R4), ("5 combined", R5)):
    ks = [k for k, v in res.items() if isinstance(v, dict) and v.get("beats")]
    tb = [k for k, v in res.items() if isinstance(v, dict) and "cfg" in v and train_better(v) and not v.get("beats")]
    sc = [k for k, v in res.items() if isinstance(v, dict) and v.get("beats_scaled")]
    V.append({"item": it, "beats_both": ks, "train_only": tb, "beats_scaled_base": sc if any("beats_scaled" in v for v in res.values() if isinstance(v, dict)) else None})
    print(f"  {it}: beats on both: {ks or 'none'}")
    if any("beats_scaled" in v for v in res.values() if isinstance(v, dict)):
        print(f"      beats the baseline scaled to the same units: {sc or 'none'}")
    if tb:
        print(f"      better on training only: {tb}")
OUT["verdicts"] = V
OUT["runtime_s"] = round(time.time() - T0)
json.dump(OUT, open("round14a.json", "w"), indent=1, default=str)
print(f"wrote round14a.json ({OUT['runtime_s']}s)")
