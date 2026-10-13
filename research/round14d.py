"""Round 14D — independent verification of round 14B's "sell the ATM straddle at open on Nifty expiry day" claim.

Written from scratch against the raw NSE bhavcopy (data/OPT_bhavcopy.csv.gz) and the Upstox index 5-min file
(data/NIFTY_5m_upstox.csv.gz); shares NOTHING with round14b.py except fno.indicators.charges and the lot history of round6.py.

Method (per Nifty expiry day = bhavcopy rows with date == expiry, sym NIFTY):
  spot ref   = 09:15 5-min bar OPEN of the index (variant: previous session's last 5-min close)
  ATM        = nearest 50-strike to the spot ref
  credit     = CE open + PE open (bhavcopy open = the 09:15 first print), × (1 − haircut) with haircut 0 / 2.7 / 5 %
               plus a "09:20 re-price" variant: credit_0920 = credit_open + (|S0920 − K| − |S0915 − K|), i.e. only the intrinsic
               change from the open → 09:20 index move, no decay, no quote-width correction
  exits      = (a) hold to settle: debit = intrinsic at the day's settlement index (bhavcopy `settle` on expiry rows is the index
               settlement value, identical on every strike of the day; checked against the 5-min 15:25 close below)
               (b) exit at the day's close (CE close + PE close)
               (c) 60 % per-leg stop: leg's day HIGH ≥ 1.6 × leg entry → leg bought back at 1.6 × entry, else at close
  slippage   = 0.5 % per leg per fill (credit × 0.995 on entry, debit × 1.005 on any buy-back; none on settlement) — also 1 %
  charges    = fno.indicators.charges (orders = 4 for close/stop exits, 2 for hold-to-settle: there is no buy order), with the
               pre-Oct-2024 STT / exchange rates of round6.charges_hist; the hold-to-settle variant ALSO pays STT 0.125 % of the
               intrinsic on the ITM leg (NSE levies this on the exercising BUYER, so it is a conservative extra; shown both ways)
  lot        = round6.LOTS history (50 → 25 → 75 → 65) and flat 65; per-lot-of-1 (points net of charges at qty 1 ... charges are
               a flat ₹20/order so the qty-1 figure is reported as points before charges)
Control: every non-expiry Nifty session of the same ISO week, nearest listed expiry ≥ date, same open fill, exit at close.
Run from research/:  python round14d.py | tee round14d.log  →  round14d.json
"""
import json, sys, time, gzip
from datetime import date, timedelta
import numpy as np
import pandas as pd

sys.path.insert(0, "..")
from fno import indicators as I  # noqa: E402

T0 = time.time()
OUT = {}
STEP = 50
LOT_HIST = [(date(2024, 4, 26), 50), (date(2024, 11, 20), 25), (date(2025, 10, 28), 75), (date(2099, 1, 1), 65)]   # from round6.py


def lot_on(d):
    return next(l for until, l in LOT_HIST if d < until)


def charges_on(d, pb, ps, qty, orders):
    """broker model of the time (round6.charges_hist for pre-Oct-2024, fno.indicators.charges after)."""
    if d < date(2024, 10, 1):
        bv, sv = pb * qty, ps * qty
        br = 20 * orders
        exch = 0.000503 * (bv + sv)
        sebi = 0.000001 * (bv + sv)
        return br + 0.000625 * sv + exch + sebi + 0.00003 * bv + 0.18 * (br + exch + sebi)
    return I.charges(pb, ps, qty, orders)


def pct(x):
    return float(np.round(100 * x, 1))


# ------------------------------------------------------------------ data
bh = pd.read_csv("data/OPT_bhavcopy.csv.gz", parse_dates=["date", "expiry"])
bh = bh[bh.sym == "NIFTY"].copy()
bh["d"] = bh.date.dt.date
bh["e"] = bh.expiry.dt.date
idx = pd.read_csv("data/NIFTY_5m_upstox.csv.gz", parse_dates=["ts"])
idx["d"] = idx.ts.dt.date
idx["t"] = idx.ts.dt.strftime("%H:%M")
day_open = idx[idx.t == "09:15"].set_index("d").open
day_0920 = idx[idx.t == "09:20"].set_index("d").open
day_close = idx.groupby("d").close.last()
day_prev_close = day_close.shift(1)
sessions = sorted(day_open.index)

exp_rows = bh[bh.d == bh.e]
exp_days = sorted(exp_rows.d.unique())
OUT["n_expiry_days"] = len(exp_days)
missing_idx = [str(d) for d in exp_days if d not in day_open.index]
OUT["expiry_days_without_index_bar"] = missing_idx
print(f"Nifty expiry days in bhavcopy: {len(exp_days)}  ({exp_days[0]} → {exp_days[-1]}); without a 09:15 index bar: {missing_idx}")

# settlement sanity: bhavcopy settle on expiry rows = one value per day; compare with the 15:25 5-min close
settle_by_day = exp_rows.groupby("d").settle.agg(["nunique", "first"])
assert (settle_by_day["nunique"] == 1).all(), "settle differs across strikes on an expiry day"
sd = pd.DataFrame({"settle": settle_by_day["first"], "close_1525": day_close.reindex(settle_by_day.index)})
sd["diff_pts"] = sd.settle - sd.close_1525
OUT["settle_vs_1525_close"] = {"median_abs_diff_pts": float(sd.diff_pts.abs().median()), "max_abs_diff_pts": float(sd.diff_pts.abs().max()),
                               "note": "settle = NSE final settlement (last 30-min weighted avg); 15:25 bar close differs by this much"}
print("settle vs 15:25 close: median |diff|", round(sd.diff_pts.abs().median(), 1), "pts, max", round(sd.diff_pts.abs().max(), 1))

# ------------------------------------------------------------------ one day
def legs_for(day, k, rows=None):
    g = exp_rows[exp_rows.d == day] if rows is None else rows
    ce = g[(g.strike == k) & (g.opt == "CE")]
    pe = g[(g.strike == k) & (g.opt == "PE")]
    if len(ce) != 1 or len(pe) != 1:
        return None
    return ce.iloc[0], pe.iloc[0]


def day_pnl(day, ce, pe, S_settle, S_ref_for_strike, *, haircut=0.027, slip=0.005, exit_mode="settle", stop=None,
            lot=None, reprice_0920=False, stt_exercise=True):
    """returns dict with points (per unit) and rupees for the chosen lot"""
    k = float(ce.strike)
    ce_in, pe_in = float(ce.open), float(pe.open)
    if ce_in <= 0 or pe_in <= 0:
        return None
    if reprice_0920:
        # crude straddle re-price from the 09:15 → 09:20 index move only: V(x) ≈ sqrt(V0² + x²), x = S − K (hyperbola model of a
        # straddle near the money; exact intrinsic far away, V0 at the money). V0 is backed out of the 09:15 quote, then V is
        # re-evaluated at the 09:20 index level and the change spread on both legs pro rata. No time decay, no quote-width effect.
        s15, s20 = float(day_open[day]), float(day_0920[day])
        v15 = ce_in + pe_in
        v0 = np.sqrt(max(v15 ** 2 - (s15 - k) ** 2, 1.0))
        v20 = np.sqrt(v0 ** 2 + (s20 - k) ** 2)
        f = v20 / v15
        ce_in, pe_in = ce_in * f, pe_in * f
    ce_in *= (1 - haircut) * (1 - slip)
    pe_in *= (1 - haircut) * (1 - slip)
    credit = ce_in + pe_in
    stopped = []
    if exit_mode == "settle":
        ce_out, pe_out = max(0.0, S_settle - k), max(0.0, k - S_settle)
        orders, buy_prem = 2, 0.0
        extra_stt_pts = 0.00125 * (ce_out + pe_out) if stt_exercise else 0.0
    else:
        ce_out, pe_out = float(ce.close), float(pe.close)
        if stop is not None:
            if float(ce.high) >= ce_in / ((1 - haircut) * (1 - slip)) * (1 + stop):   # stop measured on the raw entry price
                ce_out = ce_in / ((1 - haircut) * (1 - slip)) * (1 + stop)
                stopped.append("CE")
            if float(pe.high) >= pe_in / ((1 - haircut) * (1 - slip)) * (1 + stop):
                pe_out = pe_in / ((1 - haircut) * (1 - slip)) * (1 + stop)
                stopped.append("PE")
        ce_out *= 1 + slip
        pe_out *= 1 + slip
        orders, buy_prem = 4, ce_out + pe_out
        extra_stt_pts = 0.0
    debit = ce_out + pe_out
    pts = credit - debit - extra_stt_pts
    qty = lot if lot else lot_on(day)
    ch = charges_on(day, buy_prem, credit, qty, orders)
    rs = pts * qty - ch
    return {"date": str(day), "k": k, "credit": round(credit, 2), "debit": round(debit, 2), "pts": round(pts, 2), "qty": qty,
            "charges": round(ch, 1), "rs": round(rs), "stopped": stopped, "decay_pct": round(100 * (1 - debit / credit), 1)}


def summarize(rows, key="rs"):
    p = np.array([r[key] for r in rows], dtype=float)
    if len(p) == 0:
        return {"n": 0}
    w, l = p[p > 0].sum(), -p[p < 0].sum()
    cum = np.cumsum(p)
    dd = (np.maximum.accumulate(cum) - cum).max()
    roll5 = min(np.convolve(p, np.ones(5), "valid")) if len(p) >= 5 else float(p.sum())
    srt = np.sort(p)
    return {"n": int(len(p)), "net": float(p.sum().round()), "avg": float(p.mean().round(1)), "win_pct": pct((p > 0).mean()),
            "pf": float(np.round(w / l, 2)) if l > 0 else None, "worst": float(p.min().round(1)),
            "worst_date": rows[int(p.argmin())]["date"], "best": float(p.max().round(1)), "p10": float(np.percentile(p, 10).round(1)),
            "median": float(np.median(p).round(1)), "p90": float(np.percentile(p, 90).round(1)), "max_dd": float(np.round(dd, 1)),
            "worst_5_consecutive": float(np.round(roll5, 1)), "drop_best5_net": float(srt[:-5].sum().round()),
            "drop_best10_net": float(srt[:-10].sum().round()), "std": float(p.std().round(1))}


def run(spot_ref="open", **kw):
    rows, skipped = [], []
    for d in exp_days:
        if d not in day_open.index:
            skipped.append((str(d), "no index bar"))
            continue
        ref = float(day_open[d]) if spot_ref == "open" else float(day_prev_close[d])
        k = round(ref / STEP) * STEP
        L = legs_for(d, k)
        if L is None:
            skipped.append((str(d), f"no {k} pair"))
            continue
        r = day_pnl(d, L[0], L[1], float(L[0].settle), ref, **kw)
        if r is None:
            skipped.append((str(d), f"zero open at {k}"))
            continue
        r["spot_move_pct"] = round(100 * (float(day_close[d]) / float(day_open[d]) - 1), 2)
        r["weekday"] = d.strftime("%a")
        r["year"] = d.year
        rows.append(r)
    return rows, skipped


# ------------------------------------------------------------------ 1. rebuild
print("\n1. REBUILD (lot = history 50/25/75/65 unless stated; slippage 0.5 %/leg; charges of the time)")
OUT["rebuild"] = {}
variants = {
    "settle_h2.7": dict(haircut=0.027, exit_mode="settle"),
    "settle_h0": dict(haircut=0.0, exit_mode="settle"),
    "settle_h5": dict(haircut=0.05, exit_mode="settle"),
    "settle_h2.7_noExerciseSTT": dict(haircut=0.027, exit_mode="settle", stt_exercise=False),
    "settle_reprice0920_h0": dict(haircut=0.0, exit_mode="settle", reprice_0920=True),
    "settle_reprice0920_h2.7": dict(haircut=0.027, exit_mode="settle", reprice_0920=True),
    "close_h2.7": dict(haircut=0.027, exit_mode="close"),
    "stop60_close_h2.7": dict(haircut=0.027, exit_mode="close", stop=0.60),
    "stop60_close_h0": dict(haircut=0.0, exit_mode="close", stop=0.60),
    "stop60_close_h5": dict(haircut=0.05, exit_mode="close", stop=0.60),
    "settle_h2.7_slip1": dict(haircut=0.027, exit_mode="settle", slip=0.01),
    "stop60_close_h2.7_slip1": dict(haircut=0.027, exit_mode="close", stop=0.60, slip=0.01),
    "settle_h2.7_lot65": dict(haircut=0.027, exit_mode="settle", lot=65),
    "stop60_close_h2.7_lot65": dict(haircut=0.027, exit_mode="close", stop=0.60, lot=65),
    "settle_h2.7_prevclose": dict(haircut=0.027, exit_mode="settle"),
    "stop60_close_h2.7_prevclose": dict(haircut=0.027, exit_mode="close", stop=0.60),
}
ROWS = {}
for name, kw in variants.items():
    rows, skipped = run(spot_ref="prevclose" if name.endswith("prevclose") else "open", **kw)
    ROWS[name] = rows
    s = summarize(rows)
    s_pts = summarize(rows, "pts")
    OUT["rebuild"][name] = {"rs": s, "pts_per_unit": {k: s_pts[k] for k in ("net", "avg", "median", "p10", "p90", "worst", "pf")},
                           "skipped": skipped}
    print(f"  {name:32s} n {s['n']:3d}  net ₹{s['net']:>9,.0f}  PF {s['pf']}  win {s['win_pct']}%  worst ₹{s['worst']:>8,.0f} ({s['worst_date']})"
          f"  maxDD ₹{s['max_dd']:>7,.0f}  | pts/unit net {s_pts['net']:7.1f} avg {s_pts['avg']:5.1f} med {s_pts['median']:5.1f}"
          f"  skipped {len(skipped)}")

# ------------------------------------------------------------------ 2. break it
print("\n2. BREAK IT  (base = settle_h2.7 and stop60_close_h2.7)")
OUT["break"] = {}
for base in ("settle_h2.7", "stop60_close_h2.7"):
    rows = ROWS[base]
    df = pd.DataFrame(rows)
    b = {}
    b["by_year"] = df.groupby("year").rs.agg(["count", "sum", lambda x: pct((x > 0).mean())]).rename(columns={"<lambda_0>": "win_pct"}).round(0).to_dict("index")
    b["by_year_pts_per_unit"] = df.groupby("year").pts.agg(["sum", "mean", "median"]).round(1).to_dict("index")
    b["by_weekday"] = df.groupby("weekday").rs.agg(["count", "sum", "mean"]).round(0).to_dict("index")
    b["by_weekday_pts"] = df.groupby("weekday").pts.agg(["count", "sum", "mean", "median"]).round(1).to_dict("index")
    df["era"] = np.where(df.date < "2025-09-01", "Thu_era(to 2025-08-28)", "Tue_era(from 2025-09-02)")
    b["by_era"] = df.groupby("era").agg(n=("rs", "count"), rs=("rs", "sum"), rs_per_day=("rs", "mean"), pts_per_unit_sum=("pts", "sum"),
                                       pts_per_unit_mean=("pts", "mean"), pts_median=("pts", "median"), win_pct=("pts", lambda x: pct((x > 0).mean()))).round(1).to_dict("index")
    b["odd_weekday_days"] = df[df.weekday.isin(["Mon", "Wed"])][["date", "rs", "pts", "spot_move_pct"]].to_dict("records")
    b["top10_share_of_net_pct"] = pct(df.nlargest(10, "rs").rs.sum() / df.rs.sum())
    big = df[df.spot_move_pct.abs() > 1.0]
    small = df[df.spot_move_pct.abs() <= 1.0]
    losses_all = df[df.rs < 0].rs.sum()
    b["big_move_days"] = {"n": int(len(big)), "net": float(big.rs.sum().round()), "net_excluding_them": float(small.rs.sum().round()),
                          "share_of_all_losses_pct": pct(big[big.rs < 0].rs.sum() / losses_all) if losses_all else None,
                          "n_losing_days_total": int((df.rs < 0).sum()), "n_losing_days_in_big": int((big.rs < 0).sum())}
    b["move_buckets"] = df.groupby(pd.cut(df.spot_move_pct.abs(), [0, 0.25, 0.5, 1.0, 9], include_lowest=True), observed=True).rs.agg(["count", "sum", "mean"]).round(0).rename(index=str).to_dict("index")
    s = summarize(rows)
    b["drop_best5_net"], b["drop_best10_net"] = s["drop_best5_net"], s["drop_best10_net"]
    b["worst_day"] = {"date": s["worst_date"], "rs": s["worst"], "pts_per_unit": float(df.pts.min()),
                      "pts_per_lot65": float(np.round(df.pts.min() * 65, 0))}
    b["max_dd_rs"], b["worst_5_consecutive_rs"] = s["max_dd"], s["worst_5_consecutive"]
    sp = summarize(rows, "pts")
    b["max_dd_pts_per_unit"], b["worst_5_consecutive_pts_per_unit"] = sp["max_dd"], sp["worst_5_consecutive"]
    b["max_dd_per_lot65_rs"], b["worst_5_consecutive_per_lot65_rs"] = round(sp["max_dd"] * 65), round(sp["worst_5_consecutive"] * 65)
    b["distribution_rs"] = {k: s[k] for k in ("p10", "median", "p90", "avg", "std")}
    b["distribution_pts"] = {k: sp[k] for k in ("p10", "median", "p90", "avg", "std")}
    b["top10_days"] = df.nlargest(10, "rs")[["date", "rs", "pts", "spot_move_pct"]].to_dict("records")
    b["bottom10_days"] = df.nsmallest(10, "rs")[["date", "rs", "pts", "spot_move_pct"]].to_dict("records")
    OUT["break"][base] = b
    print(f"  [{base}]")
    print("   by year (₹ / pts-per-unit):", {y: (int(v["sum"]), int(v["count"])) for y, v in b["by_year"].items()},
          {y: round(v["sum"]) for y, v in b["by_year_pts_per_unit"].items()})
    print("   by weekday ₹:", {w: (int(v["count"]), int(v["sum"]), int(v["mean"])) for w, v in b["by_weekday"].items()})
    print("   by weekday pts/unit mean:", {w: v["mean"] for w, v in b["by_weekday_pts"].items()})
    print("   by era:", b["by_era"])
    print("   odd-weekday (holiday-shifted) expiries:", [(r["date"], r["rs"]) for r in b["odd_weekday_days"]], " top-10 days share of net:", b["top10_share_of_net_pct"], "%")
    print(f"   drop best 5 → ₹{b['drop_best5_net']:,.0f}; drop best 10 → ₹{b['drop_best10_net']:,.0f}")
    print(f"   > 1 % move days: {b['big_move_days']}")
    print("   move buckets:", b["move_buckets"])
    print(f"   worst day {b['worst_day']};  maxDD ₹{b['max_dd_rs']:,.0f}  worst 5 consecutive ₹{b['worst_5_consecutive_rs']:,.0f}"
          f"  (per unit: maxDD {b['max_dd_pts_per_unit']} pts, worst5 {b['worst_5_consecutive_pts_per_unit']} pts → ×65 ₹{b['max_dd_per_lot65_rs']:,} / ₹{b['worst_5_consecutive_per_lot65_rs']:,})")
    print(f"   distribution ₹ p10/med/p90 {b['distribution_rs']['p10']} / {b['distribution_rs']['median']} / {b['distribution_rs']['p90']};"
          f" pts {b['distribution_pts']['p10']} / {b['distribution_pts']['median']} / {b['distribution_pts']['p90']}")
for k in ("settle_h2.7_slip1", "stop60_close_h2.7_slip1", "settle_h5", "stop60_close_h5"):
    s = OUT["rebuild"][k]["rs"]
    print(f"   {k:28s} net ₹{s['net']:,.0f} PF {s['pf']} worst ₹{s['worst']:,.0f}")

# ------------------------------------------------------------------ 3. control
print("\n3. NON-EXPIRY CONTROL (same ISO week, nearest listed expiry, open fill → close; and a seeded random one per week)")
nonexp = bh[bh.d != bh.e]
by_day = {d: g for d, g in nonexp.groupby("d")}
exp_set = set(exp_days)
week_of = {d: d.isocalendar()[:2] for d in exp_days}
ctrl_rows = {h: [] for h in (0.0, 0.027, 0.05)}
ctrl_rand = {h: [] for h in (0.0, 0.027, 0.05)}
rng = np.random.default_rng(14)
for ed in exp_days:
    wk = week_of[ed]
    cands = [d for d in sessions if d.isocalendar()[:2] == wk and d not in exp_set and d in by_day and d >= exp_days[0]]
    if not cands:
        continue
    pick = cands[rng.integers(len(cands))]
    for d in cands:
        g = by_day[d]
        e = g[g.e >= d].e.min()
        g = g[g.e == e]
        k = round(float(day_open[d]) / STEP) * STEP
        L = legs_for(d, k, g)
        if L is None:
            continue
        for h in ctrl_rows:
            r = day_pnl(d, L[0], L[1], np.nan, None, haircut=h, exit_mode="close")
            if r is None:
                continue
            r["dte"] = (e - d).days
            r["spot_move_pct"] = round(100 * (float(day_close[d]) / float(day_open[d]) - 1), 2)
            ctrl_rows[h].append(r)
            if d == pick:
                ctrl_rand[h].append(r)
OUT["control"] = {}
for h in ctrl_rows:
    s, sr = summarize(ctrl_rows[h]), summarize(ctrl_rand[h])
    sp = summarize(ctrl_rows[h], "pts")
    dec = np.array([r["decay_pct"] for r in ctrl_rows[h]])
    OUT["control"][f"h{h}"] = {"all_same_week": s, "pts_per_unit": {k: sp[k] for k in ("net", "avg", "median")}, "one_random_per_week": sr,
                              "decay_open_to_close_pct": {"mean": float(dec.mean().round(1)), "median": float(np.median(dec).round(1))}}
    print(f"  haircut {h*100:.1f}%: all same-week non-expiry n {s['n']} net ₹{s['net']:,.0f} PF {s['pf']} avg ₹{s['avg']} | pts/unit avg {sp['avg']} med {sp['median']}"
          f" | decay mean {dec.mean():.1f}% med {np.median(dec):.1f}% | random-one-per-week n {sr['n']} net ₹{sr['net']:,.0f} PF {sr['pf']}")
for base in ("close_h2.7", "settle_h2.7"):
    dec = np.array([r["decay_pct"] for r in ROWS[base]])
    OUT["control"][f"expiry_{base}_decay_pct"] = {"mean": float(dec.mean().round(1)), "median": float(np.median(dec).round(1))}
    print(f"  expiry day {base}: decay mean {dec.mean():.1f}% median {np.median(dec):.1f}%")
# same thing with exit-at-close on expiry at 0 haircut for an apples-to-apples decay compare
rows0, _ = run(haircut=0.0, exit_mode="close")
dec0 = np.array([r["decay_pct"] for r in rows0])
OUT["control"]["expiry_close_h0"] = {"summary": summarize(rows0), "decay": {"mean": float(dec0.mean().round(1)), "median": float(np.median(dec0).round(1))}}
print(f"  expiry day close h0: net ₹{summarize(rows0)['net']:,.0f}, decay mean {dec0.mean():.1f}% median {np.median(dec0):.1f}%")

# ------------------------------------------------------------------ 4. data quality
print("\n4. DATA: zero / missing opens")
atm_pairs = []
for d in exp_days:
    if d not in day_open.index:
        continue
    k = round(float(day_open[d]) / STEP) * STEP
    g = exp_rows[exp_rows.d == d]
    for w in (-2, -1, 0, 1, 2):
        kk = k + w * STEP
        for o in ("CE", "PE"):
            r = g[(g.strike == kk) & (g.opt == o)]
            atm_pairs.append({"date": str(d), "w": w, "opt": o, "present": len(r) == 1, "open0": bool(len(r) == 1 and float(r.iloc[0].open) == 0),
                              "vol0": bool(len(r) == 1 and float(r.iloc[0].volume) == 0)})
ap = pd.DataFrame(atm_pairs)
OUT["data"] = {"expiry_rows_open0_pct": pct((exp_rows.open == 0).mean()), "expiry_rows_und_nan_pct": pct(exp_rows.und.isna().mean()),
               "atm_pm2_strikes": {"n_leg_slots": int(len(ap)), "missing": int((~ap.present).sum()), "open0": int(ap.open0.sum()), "vol0": int(ap.vol0.sum())},
               "atm_exact": {"missing": int((~ap[ap.w == 0].present).sum()), "open0": int(ap[ap.w == 0].open0.sum())},
               "handling": "a day whose ATM leg has open 0 or is missing is skipped (listed under rebuild.*.skipped); none were"}
oz = exp_rows[exp_rows.open == 0]
OUT["data"]["open0_rows_moneyness"] = {"median_abs_dist_from_settle_pts": float((oz.strike - oz.settle).abs().median()), "n": int(len(oz))}
print("  expiry rows with open 0:", pct((exp_rows.open == 0).mean()), "% — median |strike − settle| of those rows",
      round((oz.strike - oz.settle).abs().median()), "pts (deep OTM/ITM only)")
print("  ATM ±2 strikes:", OUT["data"]["atm_pm2_strikes"], " exact ATM:", OUT["data"]["atm_exact"])
# "first real trade" on the daily file is the open itself when volume > 0; the only alternative is the 1-min live days (section 5)

# ------------------------------------------------------------------ 5. the two real 0-DTE days
print("\n5. LIVE 0-DTE DAYS (1-min): 09:15 first print vs 09:20, and the path")
OUT["live"] = {}
for day in ("2026-10-06", "2026-10-13"):
    try:
        l = pd.read_csv(f"data/live/{day}/candles_1m.csv.gz")
    except FileNotFoundError:
        print("  missing", day)
        continue
    l = l[l.sym == "NIFTY"].copy()
    l["t"] = l.ts.str[11:16]
    ix = l[l.kind == "IDX"].set_index("t")
    opt = l[(l.kind.isin(["CE", "PE"])) & (l.expiry == day)]
    if ix.empty or opt.empty:
        print("  no 0-DTE rows", day)
        continue
    s15 = float(ix.loc["09:15", "open"])
    k = round(s15 / STEP) * STEP
    info = {"spot_0915_open": s15, "atm": k, "spot_0920_open": float(ix.loc["09:20", "open"]) if "09:20" in ix.index else None,
            "spot_1515": float(ix.loc["15:15", "close"]) if "15:15" in ix.index else None, "spot_last": float(ix.close.iloc[-1])}
    legs = {}
    for o in ("CE", "PE"):
        g = opt[(opt.strike == k) & (opt.kind == o)].set_index("t").sort_index()
        first_trade = g[g.volume > 0]
        legs[o] = {"open_0915": float(g.open.iloc[0]), "t_first_bar": g.index[0],
                   "first_traded_bar": first_trade.index[0] if len(first_trade) else None,
                   "first_traded_open": float(first_trade.open.iloc[0]) if len(first_trade) else None,
                   "open_0920": float(g.loc["09:20", "open"]) if "09:20" in g.index else None,
                   "close_0930": float(g.loc["09:30", "close"]) if "09:30" in g.index else None,
                   "close_1200": float(g.loc["12:00", "close"]) if "12:00" in g.index else None,
                   "close_1515": float(g.loc["15:15", "close"]) if "15:15" in g.index else None,
                   "high_to_1515": float(g[g.index <= "15:15"].high.max()), "last": float(g.close.iloc[-1])}
    c15 = legs["CE"]["open_0915"] + legs["PE"]["open_0915"]
    c20 = (legs["CE"]["open_0920"] or 0) + (legs["PE"]["open_0920"] or 0)
    cft = (legs["CE"]["first_traded_open"] or 0) + (legs["PE"]["first_traded_open"] or 0)
    c1515 = (legs["CE"]["close_1515"] or 0) + (legs["PE"]["close_1515"] or 0)
    info.update({"legs": legs, "straddle_0915": round(c15, 2), "straddle_0920": round(c20, 2), "straddle_first_traded": round(cft, 2),
                 "haircut_0915_vs_0920_pct": round(100 * (1 - c20 / c15), 2), "straddle_1515": round(c1515, 2),
                 "decay_0920_to_1515_pct": round(100 * (1 - c1515 / c20), 1) if c20 else None,
                 "spot_move_0915_to_1515_pct": round(100 * (info["spot_1515"] / s15 - 1), 2) if info["spot_1515"] else None})
    # path: straddle value every 30 min
    path = {}
    for t in ("09:15", "09:20", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30", "15:00", "15:15", "15:25"):
        v = 0
        for o in ("CE", "PE"):
            g = opt[(opt.strike == k) & (opt.kind == o)].set_index("t")
            if t in g.index:
                v += float(g.loc[t, "close"])
            else:
                v = None
                break
        path[t] = round(v, 2) if v is not None else None
    info["straddle_path_close"] = path
    OUT["live"][day] = info
    print(f"  {day}: spot 09:15 {s15} ATM {k}; straddle 09:15 open {c15:.2f}  first traded {cft:.2f}  09:20 open {c20:.2f} "
          f"(haircut {info['haircut_0915_vs_0920_pct']} %)  15:15 {c1515:.2f} (decay from 09:20 {info['decay_0920_to_1515_pct']} %), spot move {info['spot_move_0915_to_1515_pct']} %")
    print("     path:", path)
    print("     legs:", {o: {kk: v for kk, v in legs[o].items() if kk in ('open_0915', 'first_traded_bar', 'first_traded_open', 'open_0920', 'close_1515', 'high_to_1515')} for o in legs})

# the same first-print measure on ALL live days with a 1-min capture, nearest expiry (not 0-DTE) — more samples for the haircut
import os
hc = []
for day in sorted(os.listdir("data/live")):
    f = f"data/live/{day}/candles_1m.csv.gz"
    if not os.path.exists(f):
        continue
    l = pd.read_csv(f)
    l = l[l.sym == "NIFTY"].copy()
    l["t"] = l.ts.str[11:16]
    ix = l[l.kind == "IDX"].set_index("t")
    if "09:15" not in ix.index:
        continue
    opt = l[l.kind.isin(["CE", "PE"]) & l.expiry.notna()]
    e = sorted(opt.expiry.unique())[0]
    opt = opt[opt.expiry == e]
    k = round(float(ix.loc["09:15", "open"]) / STEP) * STEP
    v15 = v20 = 0
    ok = True
    for o in ("CE", "PE"):
        g = opt[(opt.strike == k) & (opt.kind == o)].set_index("t")
        if "09:15" not in g.index or "09:20" not in g.index:
            ok = False
            break
        v15 += float(g.loc["09:15", "open"])
        v20 += float(g.loc["09:20", "open"])
    if ok:
        hc.append({"date": day, "expiry": e, "dte": (date.fromisoformat(e) - date.fromisoformat(day)).days, "s0915": round(v15, 2), "s0920": round(v20, 2),
                   "haircut_pct": round(100 * (1 - v20 / v15), 2)})
OUT["live_haircut_all_days"] = hc
h = np.array([x["haircut_pct"] for x in hc])
OUT["live_haircut_summary"] = {"n": int(len(h)), "mean": float(h.mean().round(2)), "median": float(np.median(h).round(2)), "min": float(h.min()), "max": float(h.max())}
print("  first-print haircut on all live days (nearest expiry):", [(x["date"], x["dte"], x["haircut_pct"]) for x in hc])
print("  mean", h.mean().round(2), "median", np.median(h).round(2))

OUT["runtime_s"] = round(time.time() - T0, 1)
json.dump(OUT, open("round14d.json", "w"), indent=1, default=str)
print(f"\nwrote round14d.json ({OUT['runtime_s']}s)")
