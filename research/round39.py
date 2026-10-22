"""Round 39 (Oct 2026): can the AI experimental strategy be made useful?

Part A - why the shipped AI no longer trades. Walk-forward exactly like ai_train.py (LightGBM, 64 features, label = index R of
  a call / put with a 1.0 / 1.5 / 2.0 ATR stop and 2x target held to 15:15), retrained every quarter on all earlier data.
  For each test quarter: the share of bars whose best expected R clears the fixed threshold (0.2 / 0.3), the average best
  expected R, and calibration (realised R by predicted decile). Then a RANK threshold instead of a fixed one: trade the bars
  whose prediction is in the top 1% / 0.5% of the training set's last 3 months.
Part B - the AI as a judge of the rules' own signals (meta-labelling). Every Noise / Camarilla / trend / Fibonacci trade of
  the current set on Nifty and Bank Nifty (round 32 baseline, Rs 2,000 risk a trade = "u"), with the 64 features of its
  entry bar turned to the trade's side. A small LightGBM learns which signals win, walk-forward (quarterly or monthly
  retrain, expanding window, from 2024). Policies on unseen quarters: skip the bottom 25% / 40%, or risk 1.33x / 0.67x
  by top / bottom half (the conviction sizes). Label-shuffle control: 20 runs with the training labels shuffled.
Run: python round39.py -> round39_results.json"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import lightgbm as lgb

D = pd.read_parquet(os.path.join(HERE, "ai_rows.parquet")); D["date"] = D.date.astype(str)
D["symn"] = D.sym.map({0: "NIFTY", 1: "BANKNIFTY"}); D["q"] = pd.to_datetime(D.date).dt.to_period("Q").astype(str)
META = json.load(open(os.path.join(HERE, "ai_meta.json"))); COLS = [c for c in META["cols"] if c not in ("side", "stop_k")]
RES = {}

# ------------------------------------------------------------------ Part A
STOPS = (1.0, 1.5, 2.0)
P = dict(objective="huber", alpha=1.0, learning_rate=0.03, num_leaves=31, min_data_in_leaf=300, feature_fraction=0.7,
         lambda_l2=5.0, bagging_fraction=0.8, bagging_freq=1, verbose=-1, seed=11, num_threads=4)


def long(df):
    parts = []
    for s, tag in ((1, "L"), (-1, "S")):
        for k in STOPS:
            p = df[COLS].copy(); p["side"] = s; p["stop_k"] = k; p["y"] = df[f"R_{tag}_{k}"].values; p["row"] = np.arange(len(df))
            parts.append(p)
    return pd.concat(parts, ignore_index=True)


def part_a():
    qs = sorted(D.q.unique()); out = []; trades = {k: [] for k in ("fixed0.2", "fixed0.3", "top1%", "top0.5%")}
    for q in [x for x in qs if x >= "2024Q1"]:
        tr, te = D[D.q < q], D[D.q == q]
        if te.empty: continue
        Lt = long(tr); m = lgb.train(P, lgb.Dataset(Lt[COLS + ["side", "stop_k"]], Lt.y), 300)
        Le = long(te); pe = m.predict(Le[COLS + ["side", "stop_k"]]).reshape(6, len(te)).T     # (bars, 6)
        ye = Le.y.values.reshape(6, len(te)).T
        last = tr[tr.q >= sorted(tr.q.unique())[-1]]
        Ll = long(last); pl = m.predict(Ll[COLS + ["side", "stop_k"]]).reshape(6, len(last)).T.max(1)
        best = pe.max(1); bi = pe.argmax(1); yb = ye[np.arange(len(te)), bi]
        dec = pd.qcut(pd.Series(best).rank(method="first"), 10, labels=False)
        out.append(dict(quarter=q, train_rows=len(tr), mean_best_expR=round(float(best.mean()), 3),
                        share_ge_0_2=round(float((best >= 0.2).mean() * 100), 2), share_ge_0_3=round(float((best >= 0.3).mean() * 100), 2),
                        realised_R_top_decile=round(float(yb[dec == 9].mean()), 3), realised_R_bottom_decile=round(float(yb[dec == 0].mean()), 3)))
        thr = {"fixed0.2": 0.2, "fixed0.3": 0.3, "top1%": float(np.quantile(pl, 0.99)), "top0.5%": float(np.quantile(pl, 0.995))}
        te2 = te.assign(best=best, yb=yb)
        for k, t in thr.items():                                      # first qualifying bar per index per day, 1 trade a day
            s = te2[te2.best >= t].sort_values("i").groupby(["symn", "date"]).head(1)
            trades[k] += [dict(quarter=q, R=float(r)) for r in s.yb]
        print(out[-1], {k: len([x for x in v if x["quarter"] == q]) for k, v in trades.items()}, flush=True)
    summ = {}
    for k, v in trades.items():
        T = pd.DataFrame(v)
        summ[k] = dict(trades=len(T), avg_R_before_costs=round(float(T.R.mean()), 3) if len(T) else None,
                       by_year={y: [int(len(g)), round(float(g.R.sum()), 1)] for y, g in T.groupby(T.quarter.str[:4])} if len(T) else {})
    return dict(quarters=out, policies=summ, note="index R before option costs; option costs and decay take roughly 0.1-0.2R a trade")


# ------------------------------------------------------------------ Part B
DIR = ["vs_open", "vs_pc", "vs_vwap", "vs_e9", "vs_e21", "vs_e50", "e9_e21", "e21_e50", "noise_pos", "vs_ph", "vs_pl", "vs_h4", "vs_l4",
       "vs_h3", "vs_l3", "gap", "ctx", "st", "st15", "macdh", "bb_pos", "ha", "dc_pos", "r1", "r3", "r6", "r12", "vs_dhi", "vs_dlo",
       "body_so_far", "bar_body", "m15_vs_e5", "m15_e5_e20", "m15_vs_s44", "prev_day_ret", "prev_day_body", "vix_day"]
PB = dict(objective="regression", learning_rate=0.03, num_leaves=7, min_data_in_leaf=40, feature_fraction=0.6, lambda_l2=10.0,
          bagging_fraction=0.8, bagging_freq=1, verbose=-1, num_threads=4)


def trades_with_features():
    L = pd.read_pickle("/tmp/round32_live.pkl"); L = L[L.sym.isin(["NIFTY", "BANKNIFTY"])].copy()
    h, m = L.entry_time.str[:2].astype(int), L.entry_time.str[3:].astype(int)
    L["i"] = ((h * 60 + m) - (9 * 60 + 15)) // 5 - 1
    J = L.merge(D[["symn", "date", "i"] + COLS].rename(columns={"symn": "sym"}), on=["sym", "date", "i"], how="inner")
    sg = np.where(J.side == "CE", 1.0, -1.0)
    for c in DIR:
        if c in J: J[c] = J[c] * sg
    for c in ("rsi", "m15_rsi"):
        if c in J: J[c] = (J[c] - 50) * sg
    J["slot_id"] = J.slot.map({"main": 0, "trend": 1, "fib": 2}); J["strat_id"] = J.strategy.map({"noise": 0, "camarilla": 1, "trend": 2}).fillna(3)
    J["q"] = pd.to_datetime(J.date).dt.to_period("Q").astype(str); J["m"] = J.date.str[:7]
    return J.sort_values(["date", "entry_time"]).reset_index(drop=True)


FB = None


def walk(J, period="q", shuffle_seed=None, seed=11):
    X = FB; preds = np.full(len(J), np.nan); trp = {}
    keys = sorted(J[period].unique()); start = "2024Q1" if period == "q" else "2024-01"
    rng = np.random.default_rng(shuffle_seed)
    for k in [x for x in keys if x >= start]:
        tr = J[J[period] < k]; te = J[J[period] == k]
        y = np.clip(tr.u.values / 2000.0, -1.5, 4.0)
        if shuffle_seed is not None: y = rng.permutation(y)
        m = lgb.train({**PB, "seed": seed}, lgb.Dataset(tr[X], y), 200)
        preds[te.index] = m.predict(te[X]); trp[k] = m.predict(tr[X])
    return preds, trp


def policies(J, preds, trp, period="q"):
    J = J.assign(p=preds); T = J[J.p.notna()].copy(); out = {}
    T["cut25"] = T[period].map({k: np.quantile(v, .25) for k, v in trp.items()})
    T["cut40"] = T[period].map({k: np.quantile(v, .40) for k, v in trp.items()})
    T["med"] = T[period].map({k: np.median(v) for k, v in trp.items()})
    def st(u, n):
        s = pd.Series(u.values, index=T.date.values).groupby(level=0).sum().sort_index(); eq = s.cumsum()
        return dict(n=int(n), net=round(float(u.sum())), fall=round(float((eq - eq.cummax().clip(lower=0)).min())),
                    by_year={y: round(float(v)) for y, v in u.groupby(T.date.str[:4]).sum().items()})
    out["all trades (now)"] = st(T.u, len(T))
    out["skip bottom 25%"] = st(T.u.where(T.p >= T.cut25, 0.0), (T.p >= T.cut25).sum())
    out["skip bottom 40%"] = st(T.u.where(T.p >= T.cut40, 0.0), (T.p >= T.cut40).sum())
    out["size 1.33 / 0.67"] = st(T.u * np.where(T.p >= T.med, 1.33, 0.67), len(T))
    hi, lo = T.u[T.p >= T.med], T.u[T.p < T.med]
    out["avg u top half vs bottom half"] = [round(float(hi.mean())), round(float(lo.mean()))]
    return out


if __name__ == "__main__":
    RES["A_why_the_ai_stopped_trading"] = part_a()
    J = trades_with_features()
    FB = COLS + ["slot_id", "strat_id"]
    print("rule trades with features", len(J), J.groupby(["sym", "slot"]).size().to_dict(), flush=True)
    for per in ("q", "m"):
        pr, trp = walk(J, per)
        RES[f"B_meta_label_{per}"] = policies(J, pr, trp, per)
        print(per, json.dumps(RES[f"B_meta_label_{per}"]), flush=True)
    # shuffle control on the quarterly version: how often does random labelling do as well?
    base = RES["B_meta_label_q"]; gain = {k: base[k]["net"] - base["all trades (now)"]["net"] for k in ("skip bottom 25%", "skip bottom 40%", "size 1.33 / 0.67")}
    sh = {k: [] for k in gain}
    for s in range(20):
        pr, trp = walk(J, "q", shuffle_seed=s)
        r = policies(J, pr, trp, "q")
        for k in gain: sh[k].append(r[k]["net"] - r["all trades (now)"]["net"])
    RES["B_shuffle_control"] = {k: dict(real_gain=gain[k], shuffled_median=round(float(np.median(v))),
                                       p_value=round(float((np.array(v) >= gain[k]).mean()), 2)) for k, v in sh.items()}
    print(json.dumps(RES["B_shuffle_control"]), flush=True)
    json.dump(RES, open(os.path.join(HERE, "round39_results.json"), "w"), indent=1)
    print("saved")
