"""Round 54 (28 Oct 2026): agent agreement and displacement sizing on top of conviction + the AI judge (pending p-agents).

Today's live sizing for Nifty: conviction (round 33, score >= 5 -> 1.33x, else 0.67x) times the AI judge (round 39,
1.33x / 0.67x by its rating, Nifty only), total capped 0.5x-1.6x. Bank Nifty / Sensex: conviction rules as in config.
The judge is rebuilt walk-forward exactly like round 39 Part B (small LightGBM on the entry bar's 64 features turned to
the trade's side + rule id, label = trade R per lot clipped -1.5..4, retrained every quarter on all earlier trades, from
2024 Q1); the live model is trained to 9 Oct 2026, so using it on past trades would be look-ahead.
Trades: round 53's (today's Autopilot rules incl. big-run skip, Nifty / Bank Nifty / Sensex, with displacement = last
5-min candle body >= 1 ATR our way, and agreement = an earlier trade on the same index and side that day).
Window: 2024-01 -> Oct 2026 (the judge needs 2023 to learn). Rs 2L account replay (round 43), 2026 restart, bootstrap.
Variants: conviction only / + judge (= live) / live + agreement 1.33-0.67 / live + displacement 1.5-0.75 /
live + displacement + agreement / live + all three (+ CPR-against 1.25), all capped 0.5x-1.6x like the live total.
Needs /tmp/ai_rows.parquet (ai_data.py day_rows, built without the old-label merge) and /tmp/round53_trades.pkl.
Run: python round54.py -> round54_results.json
"""
import os, sys, json, pickle, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd, lightgbm as lgb
import round43 as R43
from fno import config as C

D = pd.read_parquet("/tmp/ai_rows.parquet"); D["date"] = D.date.astype(str)
META = json.load(open(os.path.join(HERE, "ai_meta.json") if os.path.exists(os.path.join(HERE, "ai_meta.json")) else "/tmp/ai_meta.json")); COLS = [c for c in META["cols"] if c not in ("side", "stop_k")]
DIR = ["vs_open", "vs_pc", "vs_vwap", "vs_e9", "vs_e21", "vs_e50", "e9_e21", "e21_e50", "noise_pos", "vs_ph", "vs_pl", "vs_h4", "vs_l4",
       "vs_h3", "vs_l3", "gap", "ctx", "st", "st15", "macdh", "bb_pos", "ha", "dc_pos", "r1", "r3", "r6", "r12", "vs_dhi", "vs_dlo",
       "body_so_far", "bar_body", "m15_vs_e5", "m15_e5_e20", "m15_vs_s44", "prev_day_ret", "prev_day_body", "vix_day"]
PB = dict(objective="regression", learning_rate=0.03, num_leaves=7, min_data_in_leaf=40, feature_fraction=0.6, lambda_l2=10.0,
          bagging_fraction=0.8, bagging_freq=1, verbose=-1, num_threads=4, seed=11)


def judge_walk(T):
    N = T[T.sym == "NIFTY"].copy()
    h, m = N.entry_time.str[:2].astype(int), N.entry_time.str[3:].astype(int)
    N["i"] = ((h * 60 + m) - (9 * 60 + 15)) // 5 - 1
    F = D[D.sym == 0][["date", "i"] + COLS]
    J = N[["date", "i", "sgn", "strategy", "pnl", "risk_lot"]].reset_index().merge(F, on=["date", "i"], how="inner").set_index("index")
    for c in DIR:
        if c in J:
            J[c] = J[c] * J.sgn
    for c in ("rsi", "m15_rsi"):
        if c in J:
            J[c] = (J[c] - 50) * J.sgn
    J["slot_id"] = J.strategy.map({"Fibonacci 0.618": 2, "Trend-day": 1}).fillna(0)
    J["strat_id"] = J.strategy.map({"Noise band": 0, "Camarilla": 1, "Trend-day": 2, "Fibonacci 0.618": 3}).fillna(3)
    J["q"] = pd.to_datetime(J.date).dt.to_period("Q").astype(str)
    J["y"] = np.clip(J.pnl / J.risk_lot.clip(lower=1), -1.5, 4.0)
    X = COLS + ["slot_id", "strat_id"]
    T["judge"] = 1.0; T["judge_p"] = np.nan
    for q in sorted(x for x in J.q.unique() if x >= "2024Q1"):
        tr, te = J[J.q < q], J[J.q == q]
        mdl = lgb.train(PB, lgb.Dataset(tr[X], tr.y), 200)
        med = float(np.median(mdl.predict(tr[X])))
        p = mdl.predict(te[X])
        T.loc[te.index, "judge_p"] = p
        T.loc[te.index, "judge"] = np.where(p >= med, 1.33, 0.67)
    return T, len(J)


def conv(r):
    return (C.CONVICTION_HIGH if r.score >= 5 else C.CONVICTION_LOW) if r.sym in C.CONVICTION_SYMS else 1.0


def mk(judge=True, agree=False, disp=False, cpr=False):
    def fn(r, e, p):
        c = conv(r)
        x = c * (r.judge if judge else 1.0) * ((1.33 if r.agree else 0.67) if agree else 1.0) \
            * ((1.5 if r.disp else 0.75) if disp else 1.0) * ((1.25 if r.cpr_against else 1.0) if cpr else 1.0)
        return float(np.clip(x, 0.5, 1.6)) / c          # replay multiplies conviction itself
    return fn


if __name__ == "__main__":
    T = pickle.load(open("/tmp/round53_trades.pkl", "rb"))
    T, nj = judge_walk(T)
    W = T[T.date >= "2024"].copy()
    rated = W[(W.sym == "NIFTY") & W.judge_p.notna()]
    print("Nifty trades rated by the walk-forward judge:", len(rated), "of", int((W.sym == 'NIFTY').sum()), "(features end 9 Oct 2026)")
    hi, lo = rated[rated.judge > 1], rated[rated.judge < 1]
    print(f"judge top half avg Rs {hi.pnl.mean():.0f} ({len(hi)}) vs bottom half Rs {lo.pnl.mean():.0f} ({len(lo)})")
    out = {"rated": len(rated), "judge_halves": [round(float(hi.pnl.mean())), round(float(lo.pnl.mean()))], "overlap": {}, "variants": {}}
    print("overlap (Nifty, rated): corr judge-top vs disp", round(float(np.corrcoef(rated.judge > 1, rated.disp)[0, 1]), 3),
          "vs agree", round(float(np.corrcoef(rated.judge > 1, rated.agree)[0, 1]), 3))
    for jn, jm in (("judge top", rated.judge > 1), ("judge bottom", rated.judge < 1)):
        for col in ("disp", "agree"):
            X = rated[jm]
            a, b = X[X[col]], X[~X[col]]
            out["overlap"][f"{jn} / {col}"] = [round(float(a.pnl.mean())), len(a), round(float(b.pnl.mean())), len(b)]
            print(f"  inside {jn:12s}: {col:6s} yes Rs {a.pnl.mean():6.0f} ({len(a)})  no Rs {b.pnl.mean():6.0f} ({len(b)})")
    V = {"conviction only": mk(judge=False), "live (conviction + judge)": mk(),
         "live + agreement": mk(agree=True), "live + displacement": mk(disp=True),
         "live + displacement + agreement": mk(agree=True, disp=True), "live + all three": mk(agree=True, disp=True, cpr=True)}
    for nm, fn in V.items():
        full = R43.replay(W, size_fn=fn); y25 = R43.replay(W[W.date < "2026"], size_fn=fn); y26 = R43.replay(W[W.date >= "2026"], size_fn=fn)
        bt = R43.boot(W, n=1000, size_fn=fn)
        out["variants"][nm] = {"2024-26": full, "2024-25": y25, "2026": y26, "boot": bt}
        print(f"{nm:34s} 2024-26 {full['final']:>9} ({full['dd']})  2024-25 {y25['final']:>9} ({y25['dd']})  2026 {y26['final']:>8} ({y26['dd']})  boot {bt}", flush=True)
    json.dump(out, open(os.path.join(HERE, "round54_results.json"), "w"), indent=1, default=str)
    print("saved")
