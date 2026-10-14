"""Round 17 - "put 60 % of the account into every recommended option" instead of the 1 %-risk sizing.

Same trade book as the two rules (round6.book: same entries, same exits, same real-price-corrected premiums, slippage and charges),
re-sized three ways, each with and without a PREMIUM cut-off (sell when the option has lost X % of what it cost, checked every
5 minutes, before the rule's own exit):
  risk1   - what runs today: lots so that the spot stop costs 1 % of capital (cap 60 % of capital in premium)
  deploy  - lots = 60 % of the account / (premium x lot), account fixed at 2 lakh
  compound- same, but the account is the running equity (60 % of whatever is in the account that morning)
The premium path is the engine's option pricing on the day's 5-minute spot path with the entry IV plus the measured real-price decay,
so the cut-off is tested on the premium a buyer would have seen, not on the spot.
Cut-offs are chosen on the training half only; the unseen 120 sessions are reported as they come. usage: FNO_CALIB=1 python round17_size.py
"""
import json, os
import numpy as np
import pandas as pd

os.environ.setdefault("FNO_CALIB", "1")
import engine as E
import round6 as R6
from fno import indicators as I

CAP = E.CAPITAL
REC = []
_orig = E.trade_pnl


def _rec(d, s, k_exit, px_exit, meta, p, capital=CAP):
    tr = _orig(d, s, k_exit, px_exit, meta, p, capital)
    if tr is not None:
        tr["_ref"] = (d, s, k_exit, meta)
    return tr


E.trade_pnl = _rec
book = R6.book()
E.trade_pnl = _orig
print(len(book), "trades", book[0]["date"], "..", book[-1]["date"])


def premium_path(t):
    """premium per unit at each 5-min bar from entry to the rule's exit (entry bar included), after slippage both ways"""
    d, s, k_exit, meta = t["_ref"]
    side = s.side; opt = "CE" if side > 0 else "PE"
    entry = s.px if s.px is not None else d.c[s.i]
    k = round(entry / meta["step"]) * meta["step"] - side * meta["step"]       # 1 ITM, as the rules use
    sym = next(n for n, m in E.META.items() if m is meta)
    m_in = d.t[s.i] + 5
    iv_in = E.iv_for(sym, d, d.vix[s.i], entry, k, opt)
    prem_in = E.opt_price(entry, k, d, m_in, iv_in, opt) * (1 + E.SLIP)
    prem_stop = E.opt_price(s.stop, k, d, m_in, iv_in, opt)
    risk_unit = max(prem_in - prem_stop, prem_in * 0.05)
    path = []
    for j in range(s.i, k_exit + 1):
        m = d.t[j] + 5
        px = d.c[j] if j < k_exit else t["spot_out"]
        iv = E.iv_for(sym, d, d.vix[j], px, k, opt)
        pr = E.opt_price(px, k, d, m, iv, opt)
        pr = E.real_adjust(prem_in / (1 + E.SLIP), pr, (d.expiry - d.d).days, (m - m_in) / 60) * (1 - E.SLIP)
        path.append(pr)
    return prem_in, path, meta["lot"], risk_unit


book.sort(key=lambda t: (t["date"], t["in"]))          # chronological - compounding and the cash lock depend on it
rows = []
for t in book:
    prem_in, path, lot, risk_unit = premium_path(t)
    rows.append({"date": t["date"], "sym": t["sym"], "strat": t["strat"], "in": t["in"], "out": t["out"], "prem_in": prem_in, "path": path, "lot": lot, "risk_unit": risk_unit,
                 "lots_risk1": t["lots"], "pnl_risk1": t["pnl"]})
# sanity: the recomputed exit premium should match the engine's
chk = [abs(r["path"][-1] - t["prem_out"]) / t["prem_out"] for r, t in zip(rows, book)]
print(f"exit-premium check: median |diff| {np.median(chk)*100:.2f} %, max {np.max(chk)*100:.2f} %")
SPLIT = R6.SPLIT
DATES = R6.DATES


def exit_prem(r, cut):
    """premium received: first bar where the option is down >= cut (then that bar's premium), else the rule's exit"""
    if cut is None:
        return r["path"][-1], False
    lim = r["prem_in"] * (1 - cut)
    for pr in r["path"][1:]:
        if pr <= lim:
            return pr, True
    return r["path"][-1], False


def run(frac, cut, start_eq=CAP, half=None, max_lots=None, risk1=False, risk=None):
    """account simulation: each trade spends `frac` of the equity at entry (cash still locked in an open position is not available);
    ruin (equity <= 0) ends the run.  risk1=True reproduces today's 1 %-risk sizing on the same account."""
    eq = start_eq; out = []; peak = eq; open_pos = []
    sel = [r for r in rows if (half != "train" or r["date"] < SPLIT) and (half != "unseen" or r["date"] >= SPLIT)]
    for r in sel:
        open_pos = [o for o in open_pos if o[0] == r["date"] and o[1] > r["in"]]
        locked = sum(o[2] for o in open_pos)
        if risk1:
            lots = r["lots_risk1"]
        elif risk:                                   # today's rule, compounding: risk% of current equity on the spot stop, at most 60 % of equity in premium
            budget = eq * risk; ru = r["risk_unit"] * r["lot"]
            lots = int(budget // ru) or (1 if ru <= 1.5 * budget else 0)
            lots = min(lots, int(max(0.0, eq * 0.6 - locked) // (r["prem_in"] * r["lot"])))
        else:
            lots = int(max(0.0, eq * frac - locked) // (r["prem_in"] * r["lot"]))
        if max_lots:
            lots = min(lots, max_lots)
        if lots <= 0:
            continue
        qty = lots * r["lot"]; po, hit = exit_prem(r, cut)
        spent = r["prem_in"] * qty
        pnl = (po - r["prem_in"]) * qty - I.charges(r["prem_in"], po, qty)
        eq += pnl; peak = max(peak, eq)
        open_pos.append((r["date"], r["out"], spent))
        out.append({"date": r["date"], "pnl": pnl, "cut": hit, "lots": lots, "spent": spent, "charges": I.charges(r["prem_in"], po, qty), "eq": eq, "dd_pct": (eq / peak - 1) * 100})
        if eq <= 0.02 * start_eq:
            out[-1]["ruin"] = True
            break
    return out


def stats(tr, start_eq=CAP):
    if not tr:
        return {"trades": 0}
    p = np.array([t["pnl"] for t in tr]); eqs = np.array([t["eq"] for t in tr])
    pos, neg = p[p > 0].sum(), -p[p <= 0].sum()
    daily = pd.Series(p, index=[t["date"] for t in tr]).groupby(level=0).sum()
    months = daily.groupby(lambda d: (d.year, d.month)).sum()
    return {"trades": len(p), "net": round(float(p.sum())), "end_equity": round(float(eqs[-1])), "multiple": round(float(eqs[-1] / start_eq), 2),
            "win": round(float((p > 0).mean() * 100), 1), "pf": round(float(pos / neg), 2) if neg else None,
            "max_dd_pct": round(float(min(t["dd_pct"] for t in tr)), 1), "max_dd_rs": round(float(min(0, (eqs - np.maximum.accumulate(np.concatenate([[start_eq], eqs]))[1:]).min()))),
            "worst_trade": round(float(p.min())), "worst_trade_pct_eq": round(float(min(t["pnl"] / (t["eq"] - t["pnl"]) for t in tr) * 100), 1),
            "worst_month": round(float(months.min())), "months_neg": int((months < 0).sum()), "months": len(months),
            "avg_spent": round(float(np.mean([t["spent"] for t in tr]))), "avg_lots": round(float(np.mean([t["lots"] for t in tr])), 1), "max_lots": int(max(t["lots"] for t in tr)),
            "cut_hits_pct": round(float(np.mean([t["cut"] for t in tr]) * 100), 1), "charges": round(float(sum(t["charges"] for t in tr))),
            "ruin": any(t.get("ruin") for t in tr)}


CUTS = [None, 0.15, 0.2, 0.25, 0.3]
FRACS = [0.6, 0.4, 0.3, 0.2, 0.1, 0.05]
res = {}
def show(key, a, b):
    print(f"{key:24s} TRAIN x{a['multiple']:>6} DD {a['max_dd_pct']:>6}% worst trade {a['worst_trade_pct_eq']:>6}% of eq, neg months {a['months_neg']}/{a['months']}, PF {a['pf']}{' RUIN' if a['ruin'] else ''}"
          f" | UNSEEN x{b['multiple']:>5} DD {b['max_dd_pct']:>6}% PF {b['pf']}{' RUIN' if b['ruin'] else ''} | max lots {max(a['max_lots'], b['max_lots'])}")
a = stats(run(0, None, half="train", risk1=True)); b = stats(run(0, None, half="unseen", risk1=True)); res["today: 1 % risk"] = {"train": a, "unseen": b}; show("today: 1 % risk", a, b)
for frac in FRACS:
    for cut in CUTS:
        key = f"{int(frac*100)} % of equity" + (f", cut {int(cut*100)} %" if cut else "")
        a = stats(run(frac, cut, half="train")); b = stats(run(frac, cut, half="unseen"))
        res[key] = {"train": a, "unseen": b}; show(key, a, b)
for risk in (0.01, 0.015, 0.02, 0.03):
    key = f"risk {risk*100:g} % of equity (today's rule, compounding)"
    a = stats(run(0, None, half="train", risk=risk)); b = stats(run(0, None, half="unseen", risk=risk)); res[key] = {"train": a, "unseen": b}; show(key, a, b)
for frac in (0.15,):
    key = f"{int(frac*100)} % of equity"
    a = stats(run(frac, None, half="train")); b = stats(run(frac, None, half="unseen")); res[key] = {"train": a, "unseen": b}; show(key, a, b)
# same with a liquidity cap of 20 lots (what one person can realistically fill at the touch)
for frac in (0.6, 0.2, 0.1):
    key = f"{int(frac*100)} % of equity, max 20 lots"
    a = stats(run(frac, None, half="train", max_lots=20)); b = stats(run(frac, None, half="unseen", max_lots=20)); res[key] = {"train": a, "unseen": b}; show(key, a, b)
# the rule's own spot stop, expressed as a premium loss - so the cut-off numbers can be compared with what the rules already do
sl = []
for r, t in zip(rows, book):
    d, s, k_exit, meta = t["_ref"]
    sl.append(1 - r["path"][-1] / r["prem_in"])
loss_at_rule_exit = np.array(sl)
print("premium lost at the rule's own exit, losing trades: median", round(float(np.median(loss_at_rule_exit[loss_at_rule_exit > 0]) * 100), 1), "%, 90th pct",
      round(float(np.percentile(loss_at_rule_exit[loss_at_rule_exit > 0], 90) * 100), 1), "%")
# how often does 60 % even fit: lots and cash
res["_meta"] = {"trades": len(rows), "split": SPLIT.isoformat(), "rule_exit_premium_loss_median_pct": round(float(np.median(loss_at_rule_exit[loss_at_rule_exit > 0]) * 100), 1),
                "rule_exit_premium_loss_p90_pct": round(float(np.percentile(loss_at_rule_exit[loss_at_rule_exit > 0], 90) * 100), 1)}
json.dump(res, open("round17_size.json", "w"), indent=1, default=str)

# what the ONE historical order of trades hides: reshuffle the same trades 3,000 times (per-trade return on the premium, frac of equity
# in each trade, charges ~ 1.1 % of premium) and look at the spread of outcomes for each fraction - the sequence is luck, the trade list is not
rng = np.random.default_rng(7)
boot = {}
for frac in (0.6, 0.4, 0.3, 0.2, 0.1, 0.05):
    base = run(frac, None)
    tret = np.array([t["pnl"] / (t["eq"] - t["pnl"]) for t in base])      # each trade's return on the equity it was taken with (real charges, lot rounding)
    ends, dds = [], []
    for _ in range(3000):
        eq = np.cumprod(1 + rng.permutation(tret))
        ends.append(eq[-1]); dds.append((eq / np.maximum.accumulate(np.concatenate([[1], eq]))[1:] - 1).min())
    ends, dds = np.array(ends), np.array(dds)
    k = f"{int(frac*100)} %"
    boot[k] = {"historical_multiple": round(float(base[-1]["eq"] / CAP), 2), "median_multiple": round(float(np.median(ends)), 2), "p10_multiple": round(float(np.percentile(ends, 10)), 2),
               "p90_multiple": round(float(np.percentile(ends, 90)), 2), "median_max_dd_pct": round(float(np.median(dds)) * 100, 1), "p90_max_dd_pct": round(float(np.percentile(dds, 10)) * 100, 1),
               "p_dd_worse_than_50pct": round(float((dds < -0.5).mean()) * 100, 1), "p_dd_worse_than_80pct": round(float((dds < -0.8).mean()) * 100, 1),
               "avg_trade_pct_of_eq": round(float(tret.mean()) * 100, 2), "worst_trade_pct_of_eq": round(float(tret.min()) * 100, 1), "charges_pct_of_premium": round(float(sum(t["charges"] for t in base) / sum(t["spent"] for t in base)) * 100, 2)}
    b = boot[k]
    print(f"{k:>4} of equity: historical x{b['historical_multiple']}; reshuffled: median end x{b['median_multiple']} (10-90 % x{b['p10_multiple']}-x{b['p90_multiple']}), "
          f"median max DD {b['median_max_dd_pct']} %, 1-in-10 worst DD {b['p90_max_dd_pct']} %, chance of >50 % DD {b['p_dd_worse_than_50pct']} %, >80 % {b['p_dd_worse_than_80pct']} %; worst single trade {b['worst_trade_pct_of_eq']} % of equity; charges {b['charges_pct_of_premium']} % of premium")
res["reshuffled"] = boot
prem_ret = np.array([(r["path"][-1] - r["prem_in"]) / r["prem_in"] - 0.004 for r in rows])
g = prem_ret.mean(); v = prem_ret.var()
print(f"per-trade return on premium: mean {g*100:+.1f} %, sd {prem_ret.std()*100:.1f} %, win {(prem_ret>0).mean()*100:.0f} %; Kelly fraction of equity ≈ {g/v*100:.0f} % (full), half-Kelly {g/v*50:.0f} %")
res["_meta"]["kelly_pct"] = round(float(g / v * 100), 1)
json.dump(res, open("round17_size.json", "w"), indent=1, default=str)
