"""Round 14B — expiry-day (0 DTE) option SELLING on real option prices, Nifty and Bank Nifty.

Two samples, both real prices, no model:
A. DAILY — NSE bhavcopy Oct 2023 → Oct 2026 (data/OPT_bhavcopy.csv.gz): sell at the day's open (first trade ≈ 09:15), a per-leg
   stop counts as HIT if the leg's day HIGH reaches the stop level (conservative: no intraday ordering, so a leg that spiked and
   came back is still a stopped leg; fill = stop level + slippage), else buy back at the day's CLOSE (last trade, proxy for the
   15:15 square-off) or let it SETTLE (expiry days: settle = intrinsic at the closing settlement price; no exit charges).
   Coarse: 09:15 entry only, no 10:15 variants, no combined stop, no "losing side" rule.
B. INTRADAY — real 1-minute option candles data/live/<date>/candles_1m.csv.gz (2026-09-28 → 2026-10-13, 11 sessions), which hold the
   only true 0-DTE sessions we have: Nifty expiries 2026-10-06 and 2026-10-13 (Bank Nifty is monthly, its expiries fall outside
   the capture). All five strategy families run here, with the other 9 Nifty days + 11 Bank Nifty days as the non-expiry control.
   NOTE: data/OPT_live5m.csv.gz holds only contracts still LISTED on 2026-10-01 (earliest expiry 2026-10-06), so it contains ZERO
   expiry-day sessions; it is used only as a second, larger non-expiry intraday control (5-min bars, nearest listed expiry).

Costs: 0.5 % slippage per leg each way, fno.indicators.charges per leg (orders=2 per leg → 4 per straddle round trip), lot sizes
NIFTY 65 / BANKNIFTY 30 from fno/config applied to every year (lots were different in 2023–25; ₹ figures are "at today's lot").
Margin is an ASSUMPTION: ₹1.5 lakh per Nifty straddle/strangle, ₹2.5 lakh per Bank Nifty one; "return on margin" = net ₹ / margin.
Nothing is tuned; every stop level is reported.

Run from research/: python round14b.py | tee round14b.log  → round14b.json
"""
import glob, json, os, sys, time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from fno.indicators import charges  # noqa: E402

T0 = time.time()
SLIP = 0.005
LOT = {"NIFTY": 65, "BANKNIFTY": 30}
STEP = {"NIFTY": 50, "BANKNIFTY": 100}
MARGIN = {"NIFTY": 150_000, "BANKNIFTY": 250_000}
STOPS = (0.25, 0.40, 0.60, 1.00, None)
OUT = {"notes": __doc__, "daily": {}, "intraday": {}}


def pct(a):
    return float(np.round(100 * a, 1))


def leg_charges(entry, exit_, qty, settled=False):
    """Short leg: sold at `entry`, bought back at `exit_` (or settled: no buy order, no buy-side charges)."""
    if settled:
        return charges(0.0, entry, qty, orders=1)
    return charges(exit_, entry, qty, orders=2)


def summarize(rows, sym):
    """rows: list of dicts with pnl (₹ net), credit_rs, stopped (0/1/2), date."""
    if not rows:
        return {"n": 0}
    p = np.array([r["pnl"] for r in rows], float)
    w = p[p > 0].sum()
    l = -p[p < 0].sum()
    worst_i = int(p.argmin())
    out = {
        "n": int(len(p)), "net": float(p.sum().round()), "avg": float(p.mean().round()), "win_pct": pct((p > 0).mean()),
        "pf": float(np.round(w / l, 2)) if l > 0 else None, "worst": float(p.min().round()), "worst_date": rows[worst_i]["date"],
        "best": float(p.max().round()), "p10": float(np.percentile(p, 10).round()), "median": float(np.median(p).round()),
        "p90": float(np.percentile(p, 90).round()), "std": float(p.std().round()),
        "avg_credit_rs": float(np.mean([r["credit_rs"] for r in rows]).round()),
        "one_leg_stopped_pct": pct(np.mean([r["stopped"] >= 1 for r in rows])),
        "both_stopped_pct": pct(np.mean([r["stopped"] == 2 for r in rows])),
        "ret_on_margin_pct": pct(p.sum() / MARGIN[sym]),
        "avg_ret_per_day_pct": pct(p.mean() / MARGIN[sym]),
        "max_dd": float((np.maximum.accumulate(np.cumsum(p)) - np.cumsum(p)).max().round()),
    }
    if "decay_pct" in rows[0]:
        out["avg_decay_pct"] = float(np.round(np.nanmean([r["decay_pct"] for r in rows]), 1))
    return out


# ---------------------------------------------------------------------------------------------------------------------------
# A. DAILY (bhavcopy)
# ---------------------------------------------------------------------------------------------------------------------------
B = pd.read_csv("data/OPT_bhavcopy.csv.gz")
B = B[(B.open > 0) & (B.volume > 0)]
IDX = {s: pd.read_csv(f"data/{s}_5m_upstox.csv.gz") for s in LOT}
VIX = pd.read_csv("data/INDIAVIX_5m_upstox.csv.gz")
VIX["date"] = VIX.ts.str[:10]
VIX_OPEN = VIX.groupby("date").open.first()
SPOT = {}
for s, x in IDX.items():
    x["date"] = x.ts.str[:10]
    g = x.groupby("date")
    SPOT[s] = pd.DataFrame({"open": g.open.first(), "close": g.close.last()})


def daily_day(sym, day, rows, spot_open, spot_close, stop, width, exit_mode):
    """One short straddle/strangle from the bhavcopy row set of one (sym, expiry, date). Returns a row dict or None."""
    atm = round(spot_open / STEP[sym]) * STEP[sym]
    ce_k, pe_k = atm + width * STEP[sym], atm - width * STEP[sym]
    ce = rows[(rows.opt == "CE") & (rows.strike == ce_k)]
    pe = rows[(rows.opt == "PE") & (rows.strike == pe_k)]
    if len(ce) != 1 or len(pe) != 1:
        return None
    ce, pe = ce.iloc[0], pe.iloc[0]
    qty = LOT[sym]
    pnl = 0.0
    stopped = 0
    credit = 0.0
    exit_total = 0.0
    for leg in (ce, pe):
        entry = leg.open * (1 - SLIP)
        credit += leg.open
        if stop is not None and leg.high >= leg.open * (1 + stop):
            ex = leg.open * (1 + stop) * (1 + SLIP)
            stopped += 1
            pnl += (entry - ex) * qty - leg_charges(entry, ex, qty)
            exit_total += leg.open * (1 + stop)
        elif exit_mode == "settle":
            # on expiry days the bhavcopy's settle column is the underlying's final settlement value → option = intrinsic
            ex = leg.settle
            if leg.date == leg.expiry:
                ex = max(0.0, leg.settle - leg.strike) if leg.opt == "CE" else max(0.0, leg.strike - leg.settle)
            pnl += (entry - ex) * qty - leg_charges(entry, ex, qty, settled=True)
            exit_total += ex
        else:
            ex = leg.close * (1 + SLIP)
            pnl += (entry - ex) * qty - leg_charges(entry, ex, qty)
            exit_total += leg.close
    return {"date": day, "sym": sym, "pnl": round(pnl), "credit_pts": round(credit, 2), "credit_rs": round(credit * qty),
            "stopped": stopped, "decay_pct": round(100 * (1 - exit_total / credit), 1) if credit else np.nan,
            "spot_move_pct": round(100 * (spot_close / spot_open - 1), 2), "vix": float(VIX_OPEN.get(day, np.nan))}


print("=" * 110)
print("A. DAILY bhavcopy test (Oct 2023 → Oct 2026). Sell at open; stop = day high of the leg reached the level (conservative);")
print("   exit at close (≈ 15:15 square-off proxy) or settle. Lots NIFTY 65 / BN 30 throughout; slippage 0.5 %/leg/way; charges.")
print("=" * 110)
B["dte"] = (pd.to_datetime(B.expiry) - pd.to_datetime(B.date)).dt.days
near = B.sort_values("dte").groupby(["sym", "date"]).dte.first().rename("near_dte").reset_index()
B = B.merge(near, on=["sym", "date"])
B = B[B.dte == B.near_dte]  # nearest listed expiry only (0 DTE on expiry days)
DAILY = {}
for sym in LOT:
    bs = B[B.sym == sym]
    days = sorted(bs.date.unique())
    exp_days = sorted(bs[bs.dte == 0].date.unique())
    print(f"\n{sym}: {len(days)} sessions with bhavcopy, {len(exp_days)} expiry days "
          f"(weekday mix {pd.to_datetime(pd.Series(exp_days)).dt.day_name().value_counts().to_dict()})")
    groups = {d: g for d, g in bs.groupby("date")}
    res = {}
    for width in (0, 1, 2):
        for stop in STOPS:
            for exit_mode in ("close", "settle"):
                for sample in ("expiry", "control"):
                    rows = []
                    for d in (exp_days if sample == "expiry" else [x for x in days if x not in exp_days]):
                        if d not in SPOT[sym].index:
                            continue
                        r = daily_day(sym, d, groups[d], SPOT[sym].loc[d, "open"], SPOT[sym].loc[d, "close"], stop, width, exit_mode)
                        if r is not None:
                            rows.append(r)
                    key = f"w{width}_stop{int(stop * 100) if stop else 'none'}_{exit_mode}_{sample}"
                    res[key] = summarize(rows, sym)
                    res[key]["rows"] = rows if (sample == "expiry" and exit_mode == "settle" and width == 0) else None
    DAILY[sym] = res
    name = {0: "straddle", 1: "strangle ±1", 2: "strangle ±2"}
    print(f"{'strategy':<14}{'stop':>6}{'exit':>8} | {'expiry n':>8}{'net ₹':>10}{'win%':>6}{'PF':>6}{'worst':>9}{'decay%':>7}"
          f"{'1leg stp%':>10}{'maxDD':>8}{'ret/mgn%':>9} | {'ctrl n':>7}{'net ₹':>10}{'win%':>6}{'PF':>6}{'worst':>9}")
    for width in (0, 1, 2):
        for stop in STOPS:
            for exit_mode in ("close", "settle"):
                e = res[f"w{width}_stop{int(stop * 100) if stop else 'none'}_{exit_mode}_expiry"]
                c = res[f"w{width}_stop{int(stop * 100) if stop else 'none'}_{exit_mode}_control"]
                print(f"{name[width]:<14}{(str(int(stop * 100)) + '%') if stop else 'none':>6}{exit_mode:>8} | {e['n']:>8}{e['net']:>10,.0f}"
                      f"{e['win_pct']:>6}{str(e['pf']):>6}{e['worst']:>9,.0f}{e['avg_decay_pct']:>7}{e['one_leg_stopped_pct']:>10}"
                      f"{e['max_dd']:>8,.0f}{e['ret_on_margin_pct']:>9} | {c['n']:>7}{c['net']:>10,.0f}{c['win_pct']:>6}{str(c['pf']):>6}{c['worst']:>9,.0f}")
    # straddle hold-to-settle: distribution, by year, by VIX, by spot move
    rows = res["w0_stopnone_settle_expiry"]["rows"]
    df = pd.DataFrame(rows)
    s = res["w0_stopnone_settle_expiry"]
    print(f"\n  straddle, no stop, hold to settle: avg credit {df.credit_pts.mean():.0f} pts (₹{s['avg_credit_rs']:,.0f}), "
          f"median decay to settle {df.decay_pct.median():.0f}%, P&L p10/median/p90 ₹{s['p10']:,.0f} / ₹{s['median']:,.0f} / ₹{s['p90']:,.0f}, "
          f"worst ₹{s['worst']:,.0f} on {s['worst_date']}, best ₹{s['best']:,.0f}")
    df["year"] = df.date.str[:4]
    by_year = df.groupby("year").pnl.agg(["count", "sum", lambda x: 100 * (x > 0).mean()]).round(0)
    by_year.columns = ["n", "net", "win%"]
    print("  by year:\n" + by_year.to_string())
    df["vix_bucket"] = pd.cut(df.vix, [0, 12, 15, 99], labels=["VIX<12", "12-15", ">15"])
    bv = df.groupby("vix_bucket", observed=True).pnl.agg(["count", "sum", "mean"]).round(0)
    print("  by opening VIX:\n" + bv.to_string())
    df["move_bucket"] = pd.cut(df.spot_move_pct.abs(), [-1, 0.25, 0.5, 1.0, 99], labels=["|move|<0.25%", "0.25-0.5", "0.5-1", ">1%"])
    bm = df.groupby("move_bucket", observed=True).pnl.agg(["count", "sum", "mean"]).round(0)
    print("  by |open→close spot move|:\n" + bm.to_string())
    res["straddle_settle_by_year"] = by_year.reset_index().to_dict("records")
    res["straddle_settle_by_vix"] = bv.reset_index().astype(str).to_dict("records")
    res["straddle_settle_by_move"] = bm.reset_index().astype(str).to_dict("records")
    # decay on expiry days vs control, straddle no stop
    e_close = res["w0_stopnone_close_expiry"]
    c_close = res["w0_stopnone_close_control"]
    print(f"  straddle decay open→close: expiry days {e_close['avg_decay_pct']}% (n {e_close['n']}), "
          f"non-expiry (nearest expiry) {c_close['avg_decay_pct']}% (n {c_close['n']}); open→settle on expiry {s['avg_decay_pct']}%")
OUT["daily"] = DAILY

# ---------------------------------------------------------------------------------------------------------------------------
# B. INTRADAY (real 1-minute candles, Sep 28 → Oct 13 2026; plus the 5-min OPT_live5m file as a non-expiry control)
# ---------------------------------------------------------------------------------------------------------------------------
print("\n" + "=" * 110)
print("B. INTRADAY on real option candles. 1-min: data/live/* (2026-09-28 → 10-13). 5-min: OPT_live5m (May → Oct 2026, no expiry days).")
print("=" * 110)


def load_live_1m():
    frames = []
    for f in sorted(glob.glob("data/live/*/candles_1m.csv.gz")):
        x = pd.read_csv(f)
        frames.append(x)
    x = pd.concat(frames, ignore_index=True)
    x["date"] = x.ts.str[:10]
    x["t"] = x.ts.str[11:16]
    return x


def intraday_sessions(df, idx_df, sym):
    """Yield (date, expiry, dte, opt_frame, idx_frame) for the nearest listed expiry each session."""
    o = df[(df.sym == sym) & (df.kind.isin(["CE", "PE"]))]
    for d, g in o.groupby("date"):
        exps = sorted(g.expiry.dropna().unique())
        exps = [e for e in exps if e >= d]
        if not exps:
            continue
        e = exps[0]
        dte = (pd.Timestamp(e) - pd.Timestamp(d)).days
        idx = idx_df[idx_df.date == d].sort_values("t")
        if idx.empty:
            continue
        yield d, e, dte, g[g.expiry == e], idx


def leg_series(g, strike, kind, timeline):
    s = g[(g.strike == strike) & (g.kind == kind)].drop_duplicates("t").set_index("t")[["open", "high", "low", "close", "volume"]]
    s = s.reindex(timeline)
    s[["open", "close"]] = s[["open", "close"]].ffill()
    s["high"] = s.high.fillna(s.close)
    s["low"] = s.low.fillna(s.close)
    s["open"] = s.open.fillna(s.close)
    return s


def run_intraday(sym, d, dte, g, idx, entry_t, width, stop, exit_t, combined=None, side=None, settle_px=None):
    """Short straddle/strangle (or one side if `side`) from bar `entry_t`; per-leg stop on bar high; combined stop on the sum of
    closes; square-off at bar `exit_t` open; hold to settle if exit_t == 'settle' (intrinsic at the index close, or real settle)."""
    timeline = list(idx.t)
    if entry_t not in timeline:
        return None
    i0 = timeline.index(entry_t)
    spot_ref = idx.close.iloc[i0 - 1] if i0 > 0 else idx.open.iloc[0]
    atm = round(spot_ref / STEP[sym]) * STEP[sym]
    legs = {}
    if side in (None, "CE"):
        legs["CE"] = atm + width * STEP[sym]
    if side in (None, "PE"):
        legs["PE"] = atm - width * STEP[sym]
    qty = LOT[sym]
    ser = {}
    for k, strike in legs.items():
        s = leg_series(g, strike, k, timeline)
        if np.isnan(s.open.iloc[i0]) or s.open.iloc[i0] <= 0:
            return None
        ser[k] = s
    tl = timeline[i0:]
    entry = {k: ser[k].open.iloc[i0] for k in ser}
    credit = sum(entry.values())
    open_legs = dict(entry)
    pnl, stopped, exit_total = 0.0, 0, 0.0
    why = {}
    i_exit = len(timeline) if exit_t == "settle" else (timeline.index(exit_t) if exit_t in timeline else len(timeline) - 1)
    for i in range(i0, len(timeline)):
        t = timeline[i]
        if i >= i_exit:
            break
        # per-leg stops (check bar high; include the entry bar's remaining range)
        for k in list(open_legs):
            if stop is not None and ser[k].high.iloc[i] >= entry[k] * (1 + stop):
                ex = entry[k] * (1 + stop)
                e_ = entry[k] * (1 - SLIP)
                pnl += (e_ - ex * (1 + SLIP)) * qty - leg_charges(e_, ex * (1 + SLIP), qty)
                exit_total += ex
                stopped += 1
                why[k] = f"stop@{t}"
                del open_legs[k]
        if not open_legs:
            break
        if combined is not None and len(open_legs) == len(legs):
            tot = sum(ser[k].close.iloc[i] for k in open_legs)
            if tot >= credit * (1 + combined):
                for k in list(open_legs):
                    ex = ser[k].close.iloc[i]
                    e_ = entry[k] * (1 - SLIP)
                    pnl += (e_ - ex * (1 + SLIP)) * qty - leg_charges(e_, ex * (1 + SLIP), qty)
                    exit_total += ex
                    why[k] = f"combined@{t}"
                    del open_legs[k]
                stopped = 2
                break
    for k in list(open_legs):
        e_ = entry[k] * (1 - SLIP)
        if exit_t == "settle":
            sc = settle_px if settle_px else idx.close.iloc[-1]  # real settlement if known, else the index's last 1-min close
            ex = max(0.0, sc - legs[k]) if k == "CE" else max(0.0, legs[k] - sc)
            pnl += (e_ - ex) * qty - leg_charges(e_, ex, qty, settled=True)
            why[k] = "settle"
        else:
            ex = ser[k].open.iloc[i_exit]
            pnl += (e_ - ex * (1 + SLIP)) * qty - leg_charges(e_, ex * (1 + SLIP), qty)
            why[k] = f"sqoff@{timeline[i_exit]}"
        exit_total += ex
    return {"date": d, "sym": sym, "dte": int(dte), "pnl": round(pnl), "credit_pts": round(credit, 2), "credit_rs": round(credit * qty),
            "stopped": stopped, "decay_pct": round(100 * (1 - exit_total / credit), 1), "why": why, "strikes": legs,
            "spot_ref": float(spot_ref)}


def intraday_suite(sessions, sym, label, settle_map=None):
    """Run every variant over a list of sessions; return {variant: summary}."""
    variants = []
    for width in (0, 1, 2):
        for stop in STOPS:
            for entry_t, exit_t in (("09:20", "15:15"), ("09:20", "15:25"), ("10:15", "15:15"), ("09:20", "settle")):
                variants.append((f"w{width}_stop{int(stop * 100) if stop else 'none'}_{entry_t}-{exit_t}",
                                 dict(width=width, stop=stop, entry_t=entry_t, exit_t=exit_t)))
    for comb in (0.30, 0.50):
        variants.append((f"w0_comb{int(comb * 100)}_09:20-15:15", dict(width=0, stop=None, entry_t="09:20", exit_t="15:15", combined=comb)))
        variants.append((f"w0_comb{int(comb * 100)}_09:20-settle", dict(width=0, stop=None, entry_t="09:20", exit_t="settle", combined=comb)))
    variants.append(("losing_side_10:15_stop40", dict(width=0, stop=0.40, entry_t="10:15", exit_t="15:15", side="auto")))
    out = {}
    for name, kw in variants:
        rows = []
        for d, e, dte, g, idx in sessions:
            kw2 = dict(kw)
            if kw2["exit_t"] == "settle" and dte > 0:
                continue  # settlement only exists on the expiry day
            if kw2.get("side") == "auto":
                # spot at 10:15 vs the day's open: above → sell the PE (side away from the move)
                i = list(idx.t).index("10:15") if "10:15" in list(idx.t) else None
                if i is None:
                    continue
                kw2["side"] = "PE" if idx.close.iloc[i - 1] >= idx.open.iloc[0] else "CE"
            r = run_intraday(sym, d, dte, g, idx, settle_px=(settle_map or {}).get((sym, d)), **kw2)
            if r is not None:
                rows.append(r)
        if not rows:
            continue
        out[name] = summarize(rows, sym)
        out[name]["rows"] = rows if len(rows) <= 12 else None
    return out


L = load_live_1m()
L = L[L.sym.isin(LOT)]
IDX1 = {s: L[(L.sym == s) & (L.kind == "IDX")] for s in LOT}
# real final-settlement index values where the day folder has an NSE bhavcopy (SttlmPric on a 0-DTE row = underlying settlement)
SETTLE = {}
for f in glob.glob("data/live/*/bhav_fo.csv.gz"):
    d = f.split("/")[2]
    try:
        bf = pd.read_csv(f)
        sub = bf[(bf.TckrSymb.isin(list(LOT))) & (bf.XpryDt == d) & (bf.SttlmPric > 1000)]
        for sym_, v in sub.groupby("TckrSymb").SttlmPric.first().items():
            SETTLE[(sym_, d)] = float(v)
    except Exception as ex:
        print("bhav_fo skipped", f, ex)
print("real settlement values found:", SETTLE)
INTRA = {}
for sym in LOT:
    sess = list(intraday_sessions(L, IDX1[sym], sym))
    exp = [s for s in sess if s[2] == 0]
    ctl = [s for s in sess if s[2] > 0]
    print(f"\n{sym} 1-min: {len(sess)} sessions, {len(exp)} expiry days {[s[0] for s in exp]}, {len(ctl)} non-expiry (DTE {sorted(set(s[2] for s in ctl))})")
    INTRA[sym] = {"n_expiry": len(exp), "expiry_dates": [s[0] for s in exp], "n_control": len(ctl)}
    if exp:
        INTRA[sym]["expiry"] = intraday_suite(exp, sym, "expiry", SETTLE)
    INTRA[sym]["control_1m"] = intraday_suite(ctl, sym, "control")

# 5-min OPT_live5m as a second (non-expiry) control
F = pd.read_csv("data/OPT_live5m.csv.gz")
F["date"] = F.ts.str[:10]
F["t"] = F.ts.str[11:16]
F = F.rename(columns={"opt": "kind"})
F = F[F.t <= "15:25"]
IDX5 = {}
for s, x in IDX.items():
    y = x.copy()
    y["t"] = y.ts.str[11:16]
    IDX5[s] = y[(y.date >= "2026-05-05") & (y.date <= "2026-10-01")]
for sym in LOT:
    sess = list(intraday_sessions(F, IDX5[sym], sym))
    exp = [s for s in sess if s[2] == 0]
    ctl = [s for s in sess if s[2] > 0]
    dtes = [s[2] for s in ctl]
    print(f"{sym} 5-min OPT_live5m: {len(sess)} sessions, {len(exp)} expiry days (file holds only still-listed contracts), "
          f"control DTE min/median/max {min(dtes)}/{int(np.median(dtes))}/{max(dtes)}")
    INTRA[sym]["control_5m_dte"] = {"n": len(ctl), "dte_min": min(dtes), "dte_median": int(np.median(dtes)), "dte_max": max(dtes)}
    INTRA[sym]["control_5m"] = intraday_suite(ctl, sym, "control5m")
OUT["intraday"] = INTRA

print(f"\n{'variant':<34} | {'NIFTY expiry (1-min)':>22} | {'NIFTY ctrl 1m':>14} | {'NIFTY ctrl 5m':>14} | {'BN ctrl 1m':>12} | {'BN ctrl 5m':>12}")


def cell(s):
    if not s or s.get("n", 0) == 0:
        return f"{'-':>14}"
    
    return f"n{s['n']:>3} ₹{s['net']:>8,.0f}"


for name in INTRA["NIFTY"].get("expiry", INTRA["NIFTY"]["control_1m"]):
    e = INTRA["NIFTY"].get("expiry", {}).get(name)
    print(f"{name:<34} | {cell(e):>22} | {cell(INTRA['NIFTY']['control_1m'].get(name)):>14} | {cell(INTRA['NIFTY']['control_5m'].get(name)):>14}"
          f" | {cell(INTRA['BANKNIFTY']['control_1m'].get(name)):>12} | {cell(INTRA['BANKNIFTY']['control_5m'].get(name)):>12}")

print("\nNifty expiry days, 1-min, per day (straddle no stop → settle / 15:15; stop 40 % → 15:15; losing side):")
for name in ("w0_stopnone_09:20-settle", "w0_stopnone_09:20-15:15", "w0_stop40_09:20-15:15", "w0_stop40_10:15-15:15", "w0_comb30_09:20-15:15",
             "losing_side_10:15_stop40", "w1_stop40_09:20-15:15", "w2_stopnone_09:20-settle"):
    for r in INTRA["NIFTY"].get("expiry", {}).get(name, {}).get("rows") or []:
        print(f"  {name:<28} {r['date']} strikes {r['strikes']} credit {r['credit_pts']:.0f} pts  decay {r['decay_pct']}%  "
              f"net ₹{r['pnl']:,.0f}  {r['why']}")

# cross-check: the same two Nifty expiry days in the bhavcopy daily method
print("\nCross-check, same two Nifty expiry days, daily-bhavcopy method (straddle, no stop, settle):")
for r in DAILY["NIFTY"]["w0_stopnone_settle_expiry"]["rows"]:
    if r["date"] in INTRA["NIFTY"]["expiry_dates"]:
        print(f"  {r['date']} credit {r['credit_pts']:.0f} pts decay {r['decay_pct']}% net ₹{r['pnl']:,.0f}")
print("  (the 10-13 session is after the bhavcopy's last date, so only 10-06 can appear)")

# ---------------------------------------------------------------------------------------------------------------------------
# C. The bhavcopy "open" is the 09:15 first print. How far is it from a fillable 09:20 price? Measured on the 10 live days where
#    both exist, then applied as an entry haircut to the daily expiry-day straddle.
# ---------------------------------------------------------------------------------------------------------------------------
print("\nC. First-print bias: bhavcopy open (= 09:15 1-min open) vs the 09:20 1-min open, ATM straddle credit, live days:")
bias_rows = []
for sym in LOT:
    for d, e, dte, g, idx in intraday_sessions(L, IDX1[sym], sym):
        tl = list(idx.t)
        if "09:15" not in tl or "09:20" not in tl:
            continue
        atm = round(idx.open.iloc[0] / STEP[sym]) * STEP[sym]
        c15 = c20 = 0.0
        ok = True
        for k in ("CE", "PE"):
            s_ = g[(g.strike == atm) & (g.kind == k)].set_index("t")
            if "09:15" not in s_.index or "09:20" not in s_.index:
                ok = False
                break
            c15 += s_.loc["09:15", "open"]
            c20 += s_.loc["09:20", "open"]
        if ok:
            bias_rows.append({"sym": sym, "date": d, "c0915": c15, "c0920": c20, "bias_pct": 100 * (c15 / c20 - 1)})
bias = pd.DataFrame(bias_rows)
BIAS = {}
for sym, gb in bias.groupby("sym"):
    BIAS[sym] = float(gb.bias_pct.mean())
    print(f"  {sym}: n {len(gb)}, straddle credit 09:15 first print vs 09:20: mean {gb.bias_pct.mean():+.1f}%, median {gb.bias_pct.median():+.1f}%, "
          f"min {gb.bias_pct.min():+.1f}%, max {gb.bias_pct.max():+.1f}%")
OUT["first_print_bias"] = {"rows": bias_rows, "mean_pct": BIAS}


def daily_day_haircut(sym, day, rows, spot_open, spot_close, stop, width, exit_mode, h):
    """daily_day with the entry (and the stop reference) taken at open × (1 − h)."""
    rr = rows.copy()
    rr["open"] = rr.open * (1 - h)
    return daily_day(sym, day, rr, spot_open, spot_close, stop, width, exit_mode)


print("  Daily expiry-day straddle re-run with the entry credit cut by that mean bias (stops measured from the cut entry):")
OUT["daily_haircut"] = {}
for sym in LOT:
    bs = B[B.sym == sym]
    exp_days = sorted(bs[bs.dte == 0].date.unique())
    all_days = sorted(bs.date.unique())
    groups = {d: gg for d, gg in bs.groupby("date")}
    h = max(0.0, BIAS[sym]) / 100
    OUT["daily_haircut"][sym] = {"haircut_pct": round(100 * h, 1)}
    for label, stop, exit_mode, width in (("straddle none settle", None, "settle", 0), ("straddle 60% close", 0.60, "close", 0),
                                          ("strangle±1 none settle", None, "settle", 1), ("strangle±1 100% close", 1.0, "close", 1)):
        for sample in ("expiry", "control"):
            rows = []
            for d in (exp_days if sample == "expiry" else [x for x in all_days if x not in exp_days]):
                if d not in SPOT[sym].index:
                    continue
                r = daily_day_haircut(sym, d, groups[d], SPOT[sym].loc[d, "open"], SPOT[sym].loc[d, "close"], stop, width, exit_mode, h)
                if r is not None:
                    rows.append(r)
            sm = summarize(rows, sym)
            OUT["daily_haircut"][sym][f"{label}_{sample}"] = sm
            print(f"    {sym:<10} {label:<24} {sample:<8} n {sm['n']:>4}  net ₹{sm['net']:>9,.0f}  win {sm['win_pct']}%  PF {sm['pf']}  "
                  f"worst ₹{sm['worst']:,.0f}  maxDD ₹{sm['max_dd']:,.0f}  ret/margin {sm['ret_on_margin_pct']}%  (haircut {100 * h:.1f}%)")

# tidy rows out of the json (keep only the small ones)
for sym in DAILY:
    for k, v in DAILY[sym].items():
        if isinstance(v, dict) and v.get("rows") is not None and len(v["rows"]) > 200:
            v["rows"] = None
OUT["runtime_s"] = round(time.time() - T0, 1)
json.dump(OUT, open("round14b.json", "w"), indent=1, default=str)
print(f"\nwrote round14b.json ({OUT['runtime_s']}s)")
