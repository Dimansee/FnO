"""Round 12 — what the recorded live data says (research/data/live/<date>/).
A. The journal: every recommendation (de-duplicated per signal candle) and what the real option did.
B. Real option premium vs the formula the backtest uses (1-ITM contracts, 1-minute data, VIX from the same file).
C. Premium decay by time of day: expiry day vs other days (ATM straddle, real prices).
D. Bid-ask spread cost by moneyness and time (5-minute chain snapshots).
E. Open interest: intraday change-in-OI put/call ratio vs the next 30-minute index move; OI concentration vs the close.
F. Real volume: option + futures volume bursts and CE-vs-PE volume imbalance vs the next 15 / 30-minute move.
G. Straddle premium vs its VWAP (the AlgoTest rule) on the days with snapshots.
All on 5-9 sessions: these are descriptions and leads, not proof.
"""
import glob, gzip, json, math, os, sys
from datetime import date, datetime
import numpy as np
import pandas as pd

sys.path.insert(0, "..")
from fno import indicators as I  # noqa: E402
from fno import pricing as PR  # noqa: E402

ROOT = "data/live"
DAYS = sorted(os.path.basename(p) for p in glob.glob(f"{ROOT}/*") if os.path.exists(f"{p}/candles_1m.csv.gz"))
STEP = {"NIFTY": 50, "BANKNIFTY": 100}
LOT = {"NIFTY": 65, "BANKNIFTY": 30}
OUT = {"days": DAYS}


def load_day(d):
    c = pd.read_csv(f"{ROOT}/{d}/candles_1m.csv.gz", parse_dates=["ts"])
    c = c[c.sym.isin(["NIFTY", "BANKNIFTY", "INDIAVIX"])]
    c["m"] = c.ts.dt.hour * 60 + c.ts.dt.minute
    c = c[c.m.between(555, 929)]
    return c


def idx_series(c, sym):
    x = c[(c.sym == sym) & (c.kind == "IDX")].sort_values("ts")
    return x.set_index("m")["close"]


def nearest_expiry(c, sym, d):
    e = sorted(x for x in c[(c.sym == sym) & (c.kind.isin(["CE", "PE"]))].expiry.dropna().unique() if x > d)
    return e[0] if e else None


def contract(c, sym, exp, strike, opt):
    x = c[(c.sym == sym) & (c.kind == opt) & (c.expiry == exp) & (c.strike == strike)].sort_values("ts")
    return x.set_index("m") if len(x) else None


# ---------------------------------------------------------------- A. journal
recs = []
for d in DAYS:
    p = f"{ROOT}/{d}/journal.json"
    if not os.path.exists(p):
        continue
    j = json.load(open(p))
    seen = set()
    for r in j.get("recommendations", []):
        key = (r["symbol"], r["strategy"], r["opt"], r["option"]["strike"])           # the duplicate bug: same signal recorded every scan
        if key in seen:
            continue
        seen.add(key)
        o = r.get("result") or {}
        pr = o.get("premium") or {}
        recs.append({"date": d, "time": r["time"][:5], "sym": r["symbol"], "strategy": r["strategy"], "opt": r["opt"], "strike": r["option"]["strike"],
                     "entry_prem": r["option"].get("entry_prem"), "outcome": o.get("index_outcome"), "R": o.get("R"), "mfe": o.get("mfe_pts"), "mae": o.get("mae_pts"),
                     "prem_high_pct": pr.get("max_gain_pct"), "prem_low_pct": pr.get("max_loss_pct"), "prem_exit": pr.get("at_exit"),
                     "exit_pct": round((pr["at_exit"] / r["option"]["entry_prem"] - 1) * 100, 1) if pr.get("at_exit") and r["option"].get("entry_prem") else None})
A = pd.DataFrame(recs)
OUT["A_journal"] = {"unique_recommendations": int(len(A)), "rows": A.to_dict("records"),
                    "by_strategy": {k: {"n": int(len(g)), "targets": int((g.outcome == "target").sum()), "stops": int((g.outcome == "stop").sum()),
                                        "sum_R": round(float(g.R.fillna(0).sum()), 2), "avg_exit_pct": round(float(g.exit_pct.dropna().mean()), 1) if g.exit_pct.notna().any() else None}
                                    for k, g in A.groupby("strategy")} if len(A) else {}}
print("A journal:", OUT["A_journal"]["by_strategy"]); print(A.to_string(index=False) if len(A) else "none", flush=True)

# ---------------------------------------------------------------- B. real premium vs formula
rows = []
for d in DAYS:
    c = load_day(d)
    dd = date.fromisoformat(d)
    vix = idx_series(c, "INDIAVIX")
    for sym in ("NIFTY", "BANKNIFTY"):
        spot = idx_series(c, sym)
        exp = nearest_expiry(c, sym, d)
        if exp is None or spot.empty:
            continue
        exp_d = date.fromisoformat(exp)
        dte = (exp_d - dd).days
        for m in range(570, 900, 30):
            if m not in spot.index:
                continue
            s = float(spot[m])
            atm = round(s / STEP[sym]) * STEP[sym]
            for opt, k in (("CE", atm - STEP[sym]), ("PE", atm + STEP[sym]), ("CE", atm), ("PE", atm)):
                ct = contract(c, sym, exp, k, opt)
                if ct is None or m not in ct.index:
                    continue
                real = float(ct.loc[m, "close"])
                v = float(vix.get(m, vix.iloc[-1] if len(vix) else 14))
                t = I.years_to_expiry(exp_d, datetime(dd.year, dd.month, dd.day, m // 60, m % 60))
                iv = PR.iv(sym, v, s, k, opt, dte, STEP[sym], 1.0 if sym == "NIFTY" else 1.2)
                model = I.bs_price(s, k, t, iv, opt)
                rows.append({"date": d, "sym": sym, "m": m, "dte": dte, "mny": "1 ITM" if k != atm else "ATM", "real": real, "model": model, "err_pct": (model / real - 1) * 100})
B = pd.DataFrame(rows)
OUT["B_pricing"] = {"n": int(len(B)), "median_err_pct": round(float(B.err_pct.median()), 1),
                    "by_dte": {str(k): {"n": int(len(g)), "median_err_pct": round(float(g.err_pct.median()), 1)} for k, g in B.groupby("dte")},
                    "by_sym": {k: round(float(g.err_pct.median()), 1) for k, g in B.groupby("sym")},
                    "by_hour": {str(k): round(float(g.err_pct.median()), 1) for k, g in B.groupby(B.m // 60)}}
print("B pricing error (formula vs real, + = formula too high):", OUT["B_pricing"], flush=True)

# ---------------------------------------------------------------- C. decay by time of day (ATM straddle, real)
rows = []
for d in DAYS:
    c = load_day(d)
    for sym in ("NIFTY", "BANKNIFTY"):
        spot = idx_series(c, sym); exp = nearest_expiry(c, sym, d)
        if exp is None or 570 not in spot.index:
            continue
        s0 = float(spot[570]); atm = round(s0 / STEP[sym]) * STEP[sym]
        ce, pe = contract(c, sym, exp, atm, "CE"), contract(c, sym, exp, atm, "PE")
        if ce is None or pe is None:
            continue
        st = (ce["close"] + pe["close"]).dropna()
        if 570 not in st.index:
            continue
        base = float(st[570])
        for m in (600, 660, 720, 780, 840, 900, 925):
            if m in st.index:
                rows.append({"date": d, "sym": sym, "expiry_day": exp == d, "dte": (date.fromisoformat(exp) - date.fromisoformat(d)).days, "m": m,
                             "straddle_pct_of_0930": float(st[m]) / base * 100, "spot_move_pct": abs(float(spot.get(m, s0)) / s0 - 1) * 100})
Cc = pd.DataFrame(rows)
OUT["C_decay"] = {f"{k[0]} {'expiry' if k[1] else 'other'}": g.groupby("m").straddle_pct_of_0930.mean().round(1).to_dict() for k, g in Cc.groupby(["sym", "expiry_day"])} if len(Cc) else {}
print("C ATM straddle as % of its 09:30 value:", OUT["C_decay"], flush=True)

# ---------------------------------------------------------------- D/E/G. chain snapshots
snap_days = [d for d in DAYS if os.path.exists(f"{ROOT}/{d}/snapshots.json.gz")]
spread_rows, oi_rows, strad_rows = [], [], []
F = ["strike", "ce_ltp", "ce_bid", "ce_ask", "ce_oi", "ce_vol", "ce_iv", "pe_ltp", "pe_bid", "pe_ask", "pe_oi", "pe_vol", "pe_iv"]
for d in snap_days:
    S = json.load(gzip.open(f"{ROOT}/{d}/snapshots.json.gz", "rt"))["snapshots"]
    c = load_day(d)
    for sym in ("NIFTY", "BANKNIFTY"):
        spot = idx_series(c, sym)
        exps = sorted({s["exp"] for s in S if s["sym"] == sym and s["exp"] > d})
        if not exps or spot.empty:
            continue
        exp = exps[0]
        snaps = [s for s in S if s["sym"] == sym and s["exp"] == exp]
        first = None
        series = []
        for s in snaps:
            t = s["t"]; m = int(t[:2]) * 60 + int(t[3:])
            df = pd.DataFrame(s["rows"], columns=F)
            sp = s["spot"] or float(spot.get(m, np.nan))
            if not sp or np.isnan(sp):
                continue
            atm = round(sp / STEP[sym]) * STEP[sym]
            df["mny"] = ((df.strike - atm) / STEP[sym]).round().astype(int)
            near = df[df.mny.abs() <= 3]
            for _, r in near.iterrows():
                for side in ("ce", "pe"):
                    if r[f"{side}_bid"] and r[f"{side}_ask"] and r[f"{side}_ltp"]:
                        spread_rows.append({"date": d, "sym": sym, "m": m, "mny": int(r.mny) * (1 if side == "ce" else -1), "spread_pct": (r[f"{side}_ask"] - r[f"{side}_bid"]) / r[f"{side}_ltp"] * 100})
            wide = df[df.mny.abs() <= 8]
            ce_oi, pe_oi = wide.ce_oi.sum(), wide.pe_oi.sum()
            if first is None:
                first = (ce_oi, pe_oi)
            d_ce, d_pe = ce_oi - first[0], pe_oi - first[1]
            dpcr = d_pe / d_ce if d_ce > 0 else (np.inf if d_pe > 0 else np.nan)
            maxoi = wide.loc[(wide.ce_oi + wide.pe_oi).idxmax(), "strike"]
            a = df[df.strike == atm]
            strad = float(a.ce_ltp.iloc[0] + a.pe_ltp.iloc[0]) if len(a) and a.ce_ltp.iloc[0] and a.pe_ltp.iloc[0] else np.nan
            series.append({"m": m, "spot": sp, "pcr": pe_oi / ce_oi if ce_oi else np.nan, "dpcr": dpcr, "d_ce": d_ce, "d_pe": d_pe, "maxoi": maxoi, "straddle": strad,
                           "ce_vol": wide.ce_vol.sum(), "pe_vol": wide.pe_vol.sum()})
        if len(series) < 10:
            continue
        T = pd.DataFrame(series).sort_values("m")
        T["fwd30"] = [float(spot.get(min(m + 30, 929), np.nan)) - s for m, s in zip(T.m, T.spot)]
        T["fwd30"] = T["fwd30"] / STEP[sym] * (50 if sym == "NIFTY" else 50)          # in Nifty-ish points: BN /2
        if sym == "BANKNIFTY":
            T["fwd30"] = T["fwd30"] / 2
        # straddle VWAP (time-weighted, volume not per-snapshot)
        T["svwap"] = T.straddle.expanding().mean()
        for i in range(1, len(T)):
            r, prev = T.iloc[i], T.iloc[i - 1]
            oi_rows.append({"date": d, "sym": sym, "m": int(r.m), "dpcr_up": bool(np.isfinite(r.dpcr) and np.isfinite(prev.dpcr) and prev.dpcr < 1 <= r.dpcr),
                            "dpcr_dn": bool(np.isfinite(r.dpcr) and np.isfinite(prev.dpcr) and prev.dpcr >= 1 > r.dpcr),
                            "dpcr": r.dpcr if np.isfinite(r.dpcr) else np.nan, "pcr": r.pcr, "fwd30": r.fwd30,
                            "vol_imb": (r.ce_vol - r.pe_vol) / (r.ce_vol + r.pe_vol) if (r.ce_vol + r.pe_vol) else np.nan,
                            "straddle_vs_vwap": (r.straddle / r.svwap - 1) * 100 if r.svwap and np.isfinite(r.straddle) else np.nan,
                            "straddle_chg_pct": (r.straddle / prev.straddle - 1) * 100 if prev.straddle and np.isfinite(r.straddle) and np.isfinite(prev.straddle) else np.nan,
                            "spot_vs_maxoi": (r.spot - r.maxoi) / STEP[sym]})
        strad_rows.append({"date": d, "sym": sym, "close_vs_maxoi_pts": float(T.spot.iloc[-1] - T.maxoi.iloc[-1]), "maxoi_1330": float(T[T.m >= 810].maxoi.iloc[0]) if (T.m >= 810).any() else None,
                           "spot_1330": float(T[T.m >= 810].spot.iloc[0]) if (T.m >= 810).any() else None, "close": float(T.spot.iloc[-1])})
SP = pd.DataFrame(spread_rows)
OUT["D_spread"] = {"by_moneyness_median_pct": SP.groupby("mny").spread_pct.median().round(2).to_dict() if len(SP) else {},
                   "by_hour_atm_median_pct": SP[SP.mny.abs() <= 1].groupby(SP.m // 60).spread_pct.median().round(2).to_dict() if len(SP) else {},
                   "by_sym_atm": SP[SP.mny.abs() <= 1].groupby("sym").spread_pct.median().round(2).to_dict() if len(SP) else {}}
print("D spread % of premium:", OUT["D_spread"], flush=True)
OI = pd.DataFrame(oi_rows)
if len(OI):
    def cond(name, mask):
        g = OI[mask & OI.fwd30.notna()]
        return {"n": int(len(g)), "avg_fwd30_pts": round(float(g.fwd30.mean()), 1) if len(g) else None, "up_pct": round(float((g.fwd30 > 0).mean() * 100), 1) if len(g) else None}
    base = OI[OI.fwd30.notna()]
    OUT["E_oi"] = {"all": {"n": int(len(base)), "avg_fwd30_pts": round(float(base.fwd30.mean()), 1), "up_pct": round(float((base.fwd30 > 0).mean() * 100), 1)},
                   "dPCR crosses above 1 (puts being written faster)": cond("up", OI.dpcr_up),
                   "dPCR crosses below 1 (calls being written faster)": cond("dn", OI.dpcr_dn),
                   "dPCR > 1.5": cond("hi", OI.dpcr > 1.5), "dPCR < 0.67": cond("lo", OI.dpcr < 0.67),
                   "PCR level > 1.2": cond("p", OI.pcr > 1.2), "PCR level < 0.8": cond("q", OI.pcr < 0.8),
                   "CE volume share > 60%": cond("v", OI.vol_imb > 0.2), "PE volume share > 60%": cond("w", OI.vol_imb < -0.2),
                   "spot above max-OI strike by 2+ strikes": cond("a", OI.spot_vs_maxoi >= 2), "spot below max-OI strike by 2+ strikes": cond("b", OI.spot_vs_maxoi <= -2),
                   "corr(dPCR, next 30 min)": round(float(OI[["dpcr", "fwd30"]].replace([np.inf, -np.inf], np.nan).dropna().corr().iloc[0, 1]), 3),
                   "corr(vol imbalance, next 30 min)": round(float(OI[["vol_imb", "fwd30"]].dropna().corr().iloc[0, 1]), 3),
                   "close_vs_maxoi": strad_rows}
    print("E OI / volume (next 30-min move, Nifty-equivalent points):", json.dumps(OUT["E_oi"], default=str)[:1500], flush=True)
    OUT["G_straddle_vwap"] = {"straddle below its running average -> next 30 min straddle change %":
                              round(float(OI[(OI.straddle_vs_vwap < 0)].straddle_chg_pct.shift(-1).mean()), 2) if (OI.straddle_vs_vwap < 0).any() else None,
                              "straddle above its running average -> next 30 min straddle change %":
                              round(float(OI[(OI.straddle_vs_vwap > 0)].straddle_chg_pct.shift(-1).mean()), 2) if (OI.straddle_vs_vwap > 0).any() else None,
                              "note": "5-min snapshots, time-weighted average; 5 days only"}
    print("G straddle vs its average:", OUT["G_straddle_vwap"], flush=True)

# ---------------------------------------------------------------- F. real volume (1-minute)
rows = []
for d in DAYS:
    c = load_day(d)
    for sym in ("NIFTY", "BANKNIFTY"):
        spot = idx_series(c, sym); exp = nearest_expiry(c, sym, d)
        if exp is None or spot.empty:
            continue
        opts = c[(c.sym == sym) & (c.kind.isin(["CE", "PE"])) & (c.expiry == exp)]
        v = opts.groupby(["m", "kind"]).volume.sum().unstack(fill_value=0)
        if "CE" not in v or "PE" not in v:
            continue
        tot = (v.CE + v.PE)
        tot5 = tot.rolling(5).sum()
        usual = tot5.expanding().median()
        for m in range(590, 880, 5):
            if m not in tot5.index or m + 30 not in spot.index or m not in spot.index:
                continue
            burst = tot5[m] / usual[m] if usual[m] else np.nan
            imb = (v.CE.rolling(5).sum()[m] - v.PE.rolling(5).sum()[m]) / tot5[m] if tot5[m] else np.nan
            mv15 = (spot[min(m + 15, 929)] - spot[m]) / (1 if sym == "NIFTY" else 2) if min(m + 15, 929) in spot.index else np.nan
            mv30 = (spot[m + 30] - spot[m]) / (1 if sym == "NIFTY" else 2)
            prev5 = (spot[m] - spot[m - 5]) / (1 if sym == "NIFTY" else 2) if m - 5 in spot.index else np.nan
            rows.append({"date": d, "sym": sym, "m": m, "burst": burst, "imb": imb, "mv15": mv15, "mv30": mv30, "prev5": prev5})
V = pd.DataFrame(rows)
if len(V):
    def blk(mask, name):
        g = V[mask]
        return {"n": int(len(g)), "avg_next30_pts": round(float(g.mv30.mean()), 1), "up_pct": round(float((g.mv30 > 0).mean() * 100), 1),
                "avg_abs_next30": round(float(g.mv30.abs().mean()), 1)}
    OUT["F_volume"] = {"all": blk(V.mv30.notna(), "all"),
                       "volume burst >= 2x usual (5 min)": blk(V.burst >= 2, "b2"), "burst >= 3x": blk(V.burst >= 3, "b3"),
                       "burst >= 2x and last 5 min up": blk((V.burst >= 2) & (V.prev5 > 0), "bu"), "burst >= 2x and last 5 min down": blk((V.burst >= 2) & (V.prev5 < 0), "bd"),
                       "CE volume share > 65%": blk(V.imb > 0.3, "ce"), "PE volume share > 65%": blk(V.imb < -0.3, "pe"),
                       "CE share > 65% and last 5 min up": blk((V.imb > 0.3) & (V.prev5 > 0), "ceu"), "PE share > 65% and last 5 min down": blk((V.imb < -0.3) & (V.prev5 < 0), "ped"),
                       "corr(burst, |next 30|)": round(float(V[["burst", "mv30"]].assign(mv30=V.mv30.abs()).dropna().corr().iloc[0, 1]), 3),
                       "corr(CE-PE volume imbalance, next 30)": round(float(V[["imb", "mv30"]].dropna().corr().iloc[0, 1]), 3),
                       "corr(last 5 min move, next 30)": round(float(V[["prev5", "mv30"]].dropna().corr().iloc[0, 1]), 3)}
    print("F volume (real option volume, 1-min):", json.dumps(OUT["F_volume"])[:1400], flush=True)
json.dump(OUT, open("live_study.json", "w"), default=str, indent=1)
print("wrote live_study.json")
