"""AI setup, step 2 - walk-forward training and an honest test.

Model: LightGBM gradient-boosted trees. Input = the features of a bar + the trade asked about
(long/short, stop 1.0/1.5/2.0 ATR). Output = expected R of that trade. A second model gives the
probability the trade ends positive.

Walk-forward: for every quarter from 2024-Q1, train on everything before it (the first 12 months
of 2023 are the first training set), pick the "trade only if expected R > threshold" level on the
last 3 months of that training set (true out-of-sample for the threshold), retrain on the whole
set, and trade the quarter. Stitching the quarters gives 2¾ years the model never saw.

Trading: at every 5-min close 09:30-14:30, when flat, score 6 candidate trades (2 sides x 3 stops),
take the best if above the threshold; exits stop / target (2x) / 15:15; max 2 trades and 2 losers
per index per day; one open trade per index. P&L on the 1-ITM option with the calibrated prices,
slippage and charges (engine.trade_pnl), ₹2 lakh, 1% risk.
"""
import json, os, sys, time
from datetime import date
import numpy as np
import pandas as pd
import lightgbm as lgb

os.environ.setdefault("FNO_CALIB", "1")
import engine as E
from ai_data import SYMS, STOPS, RR, FIRST, LAST

SEED = int(os.environ.get("AI_SEED", "11"))
CFG = os.environ.get("AI_CFG", "v1")
BASE = dict(learning_rate=0.03, bagging_fraction=0.8, bagging_freq=1, verbose=-1, seed=SEED, num_threads=2)
CFGS = {
    "v1": dict(num_leaves=31, min_data_in_leaf=300, feature_fraction=0.7, lambda_l2=5.0),
    "v2": dict(num_leaves=15, min_data_in_leaf=1500, feature_fraction=0.5, lambda_l2=20.0, extra_trees=True),
    "v3": dict(num_leaves=31, min_data_in_leaf=400, feature_fraction=0.7, lambda_l2=5.0),     # rule-style exits label
    "v4": dict(num_leaves=15, min_data_in_leaf=800, feature_fraction=0.6, lambda_l2=10.0),    # same label, more regularised
}
TRAIL = CFG in ("v3", "v4")               # label: trade with the noise strategy's exits (only side matters)
PARAMS = dict(BASE, objective="huber", alpha=1.0, **CFGS[CFG])
FWD = dict(BASE, objective="huber", alpha=1.5, **CFGS[CFG])
CLS = dict(BASE, objective="binary", **CFGS[CFG])
ROUNDS = 300
CUTOFF = date.fromisoformat(os.environ.get("AI_CUTOFF", "2026-04-09"))   # the shipped model never sees days from here on
FINAL_ONLY = bool(os.environ.get("AI_FINAL_ONLY"))
TAG = ("" if CFG == "v1" else "_" + CFG) + ("" if SEED == 11 else f"_s{SEED}")
THRS = [0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5]
MAX_TRADES, MAX_LOSSES = 2, 2


def long_table(df):
    feats = [c for c in df.columns if not (c.startswith("R_") or c.startswith("X_") or c in ("date", "i", "spot", "atr", "fwd", "fwd12"))]
    feats = [c for c in feats if c not in ("dow",)]            # weekday: an overfitting magnet with no mechanism
    parts = []
    for s, tag in ((1, "L"), (-1, "S")):
        for k in ((2.0,) if TRAIL else STOPS):
            p = df[feats].copy()
            p["side"] = s
            p["stop_k"] = k
            p["y"] = df[f"R_T_{tag}"].values if TRAIL else df[f"R_{tag}_{k}"].values
            p["row"] = np.arange(len(df))
            parts.append(p)
    L = pd.concat(parts, ignore_index=True)
    return L, feats + ["side", "stop_k"]


def fit(L, cols, rows_mask, df=None, with_cls=False):
    tr = L[L["row"].isin(np.nonzero(rows_mask)[0])]
    reg = lgb.train(PARAMS, lgb.Dataset(tr[cols], tr["y"]), ROUNDS)
    cls = lgb.train(CLS, lgb.Dataset(tr[cols], (tr["y"] > 0).astype(int)), ROUNDS) if with_cls else None
    fwd = None
    if df is not None:
        fc = [c for c in cols if c not in ("side", "stop_k")]
        sub = df[rows_mask]
        fwd = lgb.train(FWD, lgb.Dataset(sub[fc], sub["fwd"].clip(-6, 6)), ROUNDS) if not TRAIL else None
    return reg, cls, fwd


def predict(models, L, cols, rows_mask, n, df=None):
    """Returns arrays (n, 6) of expected R, P(win), and (n,) expected forward move for the rows in mask (others NaN)."""
    reg, cls, fwd = models
    sub = L[L["row"].isin(np.nonzero(rows_mask)[0])]
    er = np.full((n, 6), np.nan)
    pw = np.full((n, 6), np.nan)
    ef = np.full(n, np.nan)
    combo = ((sub["side"].values < 0).astype(int) * 3 + np.searchsorted(STOPS, sub["stop_k"].values))
    er[sub["row"].values, combo] = reg.predict(sub[cols])
    if cls is not None:
        pw[sub["row"].values, combo] = cls.predict(sub[cols])
    if fwd is not None and df is not None:
        fc = [c for c in cols if c not in ("side", "stop_k")]
        ef[rows_mask] = fwd.predict(df[rows_mask][fc])
    return er, pw, ef


def simulate(df, er, thr, days_map, price=True, sel=None):
    """Trade the rows where sel is True with threshold thr. Returns trade dicts."""
    out = []
    sel = np.ones(len(df), bool) if sel is None else sel
    for (sym_id, d), g in df[sel].groupby(["sym", "date"], sort=True):
        day = days_map[(SYMS[sym_id], d)]
        meta = E.META[SYMS[sym_id]]
        busy_until, n_tr, losses = -1, 0, 0
        for ridx, i in zip(g.index.values, g["i"].values):
            if i <= busy_until or n_tr >= MAX_TRADES or losses >= MAX_LOSSES:
                continue
            e = er[ridx]
            if np.all(np.isnan(e)):
                continue
            b = int(np.nanargmax(e))
            if e[b] < thr:
                continue
            side = 1 if b < 3 else -1
            k = STOPS[b % 3]
            a = day.atr[i]
            entry = day.c[i]
            if TRAIL:
                s = E.Sig(int(i), side, entry - side * 2 * a, entry + side * 8 * a, "noise", 0, 0, "ai")
                up, lo = bands(day)
                kx, px, why = E.simulate(day, s, meta, {}, up, lo)
                k = 2.0
            else:
                s = E.Sig(int(i), side, entry - side * k * a, entry + side * RR * k * a, "", 0, 0, "ai")
                kx, px, why = E.simulate(day, s, meta, {})
            r = side * (px - entry) / (k * a)
            tr = {"sym": SYMS[sym_id], "date": d, "in": day.t[i] + 5, "out": day.t[kx] + 5, "side": side, "stop_k": k,
                  "exp_R": float(e[b]), "R": r, "why": why}
            if price:
                p = E.trade_pnl(day, s, kx, px, meta, {"otm": -1})
                if p is None:
                    busy_until = i
                    continue
                tr["pnl"] = p["pnl"]
                tr["lots"] = p["lots"]
            out.append(tr)
            busy_until = kx
            n_tr += 1
            if r < 0:
                losses += 1
    return out


def pick_threshold(df, er, mask):
    best, bs = THRS[0], -1e9
    for thr in THRS:
        tr = simulate(df, er, thr, DAYS, price=False, sel=mask)
        if len(tr) < 20:
            continue
        r = np.array([t["R"] for t in tr])
        sc = r.sum() / np.sqrt(len(r))          # total R, penalising churn
        if sc > bs:
            best, bs = thr, sc
    return best


def bands(d):
    sig = np.asarray(d.sig, dtype=float)
    o0, pc = d.o[0], d.prev_close
    return max(o0, pc) * (1 + 1.75 * sig), min(o0, pc) * (1 - 1.75 * sig)


def quarter(d):
    return d.year * 10 + (d.month - 1) // 3 + 1


def run():
    df = pd.read_parquet("ai_rows.parquet")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    L, cols = long_table(df)
    dates = np.array(df["date"])
    q = np.array([quarter(d) for d in dates])
    quarters = sorted(set(q[q >= 20241])) if not FINAL_ONLY else []
    er_all = np.full((len(df), 6), np.nan)
    pw_all = np.full((len(df), 6), np.nan)
    ef_all = np.full(len(df), np.nan)
    log = []
    t0 = time.time()
    for Q in quarters:
        y, qq = divmod(int(Q), 10)
        q_start = date(y, 3 * (qq - 1) + 1, 1)
        train = dates < q_start
        test = q == Q
        m = fit(L, cols, train, df=df)
        er_t, _, ef_t = predict(m, L, cols, test, len(df), df=df)
        er_all[test] = er_t[test]
        ef_all[test] = ef_t[test]
        trs = simulate(df, er_t, 0.2, DAYS, price=True, sel=test)
        net = sum(t["pnl"] for t in trs)
        log.append({"quarter": int(Q), "train_rows": int(train.sum()), "trades": len(trs), "net_thr0.2": round(net)})
        print(f"Q{Q} trades@0.2 {len(trs):4d} net ₹{net/1000:+.1f}k   ({round(time.time()-t0)} s)", flush=True)
    if not FINAL_ONLY:
        np.save(f"ai_er_oos{TAG}.npy", er_all)
        np.save(f"ai_ef_oos{TAG}.npy", ef_all)
        np.save(f"ai_pw_oos{TAG}.npy", pw_all)
        json.dump({"cfg": CFG, "log": log}, open(f"ai_walkforward{TAG}.json", "w"), indent=1)
    # final models for the app: trained on days before CUTOFF only, so the app's backtest after it is out-of-sample
    m = fit(L, cols, dates < CUTOFF, df=df, with_cls=True)
    m[0].save_model(f"ai_reg{TAG}.txt")
    m[1].save_model(f"ai_cls{TAG}.txt")
    if m[2] is not None:
        m[2].save_model(f"ai_fwd{TAG}.txt")
    json.dump({"cfg": CFG, "cols": cols, "stops": [2.0] if TRAIL else list(STOPS), "rr": RR, "trail": TRAIL,
               "train_until": CUTOFF.isoformat(), "train_rows": int((dates < CUTOFF).sum()),
               "medians": {c: float(L[c].median()) for c in cols}}, open(f"ai_meta{TAG}.json", "w"), indent=1)
    imp = pd.Series(m[0].feature_importance("gain"), index=cols).sort_values(ascending=False)
    print(imp.head(20))
    if m[2] is not None:
        imp2 = pd.Series(m[2].feature_importance("gain"), index=[c for c in cols if c not in ("side", "stop_k")]).sort_values(ascending=False)
        print("fwd model:", imp2.head(12).to_dict())


def report(tr, df):
    p = np.array([t["pnl"] for t in tr])
    dts = [t["date"] for t in tr]
    years = sorted({d.year for d in dts})
    pos, neg = p[p > 0].sum(), -p[p <= 0].sum()
    daily = pd.Series(p, index=dts).groupby(level=0).sum()
    eq = daily.cumsum().values
    dd = float((eq - np.maximum.accumulate(np.concatenate([[0], eq]))[1:]).min())
    uniq = sorted(set(df["date"]))
    last120 = uniq[-120]
    s = {"trades": len(p), "net": round(float(p.sum())), "win": round(float((p > 0).mean() * 100), 1), "pf": round(float(pos / neg), 2),
         "max_dd": round(dd), "years": {y: round(float(sum(x for x, d in zip(p, dts) if d.year == y))) for y in years},
         "unseen120": round(float(sum(x for x, d in zip(p, dts) if d >= last120))),
         "by_sym": {s_: round(float(sum(t["pnl"] for t in tr if t["sym"] == s_))) for s_ in SYMS},
         "by_stop": {k: round(float(sum(t["pnl"] for t in tr if t["stop_k"] == k))) for k in STOPS},
         "by_side": {"long": round(float(sum(t["pnl"] for t in tr if t["side"] > 0))), "short": round(float(sum(t["pnl"] for t in tr if t["side"] < 0)))}}
    print("OOS 2024->", json.dumps(s))
    json.dump(s, open("ai_oos_summary.json", "w"), indent=1)
    return s


DAYS = {}
if __name__ == "__main__":
    for s in SYMS:
        for d in E.load_days(s):
            DAYS[(s, d.d)] = d
    run()
