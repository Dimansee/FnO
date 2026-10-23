"""Round 40 (23 Oct 2026): are the targets too far, and does protecting open profit beat the current exits?

Trigger: on 23 Oct both Nifty trades went well into profit (Camarilla PUT +2.96R, trend-day PUT +1.1R; Nifty fell ~186
points from 10:06 to 11:xx) and then came back to the index breakeven stop, which on the option was a loss (time decay +
the option's own slippage), while Bank Nifty's Camarilla PUT ran to 15:15 for +Rs 5.3k. The user sees "very high targets".

Facts checked first (fno/backtest.py, live code): only the Noise rule has a target (4 x a 2-ATR stop = ~8 ATR of 5-min
bars, roughly 120-200 Nifty points). Camarilla, Fibonacci and trend-day have NO target: stop to entry at +1R, then hold
(Camarilla/Fib: 45-min time stop if +0.5R is not reached), square-off 15:15. The "target" premium shown for them is only
a display value.

Protocol: the app's own backtester (fno.backtest.run, strategy "autopilot", one lot, rule strike 1 ITM and the small-
account 1 OTM), all recorded 5-minute history Jan 2023 - 23 Oct 2026, all three indices. Exit variants via
fno.backtest.EXIT. Choose on 2023-25, judge on 2026 (unseen). A variant is adopted only if it beats the current exits
in 2023-25 AND in 2026, at both strikes, and is not carried by one index.
Run: python round40.py -> round40_results.json
"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round25_harness as H
from fno import backtest as B
_f0 = B._flag
B._flag = lambda n: False if n == "big_run_skip" else _f0(n)     # research runs measure the rules without the round 42 filter unless asked

SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
V = {
    "V0 current exits": {},
    "V1 breakeven stop at +0.25R (covers option decay)": {"be_lock": 0.25},
    "V2 breakeven later, at +1.5R": {"be_r": 1.5},
    "V3 lock +1R once +2R (all rules)": {"lock_steps": [(2.0, 1.0)]},
    "V4 lock +1R at +2R, +2R at +3R": {"lock_steps": [(2.0, 1.0), (3.0, 2.0)]},
    "V5 trail 1R behind best once +2R": {"giveback": (2.0, 1.0)},
    "V6 trail 1R behind best once +1.5R": {"giveback": (1.5, 1.0)},
    "V7 trail 1.5R behind best once +2.5R": {"giveback": (2.5, 1.5)},
    "V8 Noise target 2R (was 4R)": {"noise_rr": 2.0},
    "V9 Noise target 3R": {"noise_rr": 3.0},
    "V10 Noise no target (trail only)": {"noise_rr": None},
    "V11 Noise breakeven at +1R": {"noise_be_r": 1.0},
}
OTMS = {-1: "1 ITM (rule strike)", 1: "1 OTM (small-account strike)"}


def stats(df):
    if df.empty:
        return {"n": 0}
    w, l = df.pnl[df.pnl > 0].sum(), -df.pnl[df.pnl <= 0].sum()
    eq = df.sort_values(["date", "entry_time"]).pnl.cumsum()
    return {"n": int(len(df)), "net": int(df.pnl.sum()), "pf": round(float(w / l), 2) if l else None, "win": round(float((df.pnl > 0).mean() * 100), 1),
            "fall": int((eq - eq.cummax().clip(lower=0)).min())}


def run_all(ov, otm):
    B.EXIT.clear(); B.EXIT.update(ov)
    v = H.load("INDIAVIX")["close"]
    rows = []
    for s in SYMS:
        r = B.run(s, capital=200000, candles=H.load(s), vix_intraday=v, sizing="one_lot", strategy="autopilot", otm=otm, live_rules=False)
        T = pd.DataFrame(r["trades"]); T["sym"] = s; rows.append(T)
    B.EXIT.clear()
    T = pd.concat(rows, ignore_index=True); T["y"] = T.date.str[:4]
    return T


def summarise(T):
    tr, ts = T[T.y < "2026"], T[T.y == "2026"]
    return {"train": stats(tr), "y2026": stats(ts), "years": T.groupby("y").pnl.sum().astype(int).to_dict(),
            "by_sym_train": tr.groupby("sym").pnl.sum().astype(int).to_dict(), "by_sym_2026": ts.groupby("sym").pnl.sum().astype(int).to_dict(),
            "by_strategy_train": tr.groupby("strategy").pnl.sum().astype(int).to_dict(), "by_strategy_2026": ts.groupby("strategy").pnl.sum().astype(int).to_dict()}


if __name__ == "__main__":
    out = {"variants": {}, "facts": {}}
    base = None
    for otm, lab in OTMS.items():
        for nm, ov in V.items():
            T = run_all(ov, otm)
            if nm.startswith("V0"):
                base = T
                if otm == -1:   # how often a trade is well in profit and still ends at or below zero
                    g = T.copy()
                    f = {}
                    for k, gg in g.groupby("strategy"):
                        f[k] = {"n": int(len(gg)), "reach_1R": round(float((gg.mfe_R >= 1).mean() * 100), 1), "reach_2R": round(float((gg.mfe_R >= 2).mean() * 100), 1),
                                "reach_4R": round(float((gg.mfe_R >= 4).mean() * 100), 1),
                                "reached_2R_but_lost": int(((gg.mfe_R >= 2) & (gg.pnl <= 0)).sum()), "be_stop_after_1R_pnl": int(gg[gg.reason == "Breakeven stop"].pnl.sum()),
                                "be_stops": int((gg.reason == "Breakeven stop").sum()), "median_mfe_R": round(float(gg.mfe_R.median()), 2),
                                "avg_exit_R_winners": round(float(gg[gg.pnl > 0].R.mean()), 2)}
                    out["facts"] = f
                    out["today"] = T[T.date == "2026-10-23"][["sym", "strategy", "side", "entry_time", "exit_time", "spot_in", "spot_out", "R", "mfe_R", "pnl", "reason"]].to_dict("records")
            out["variants"].setdefault(nm, {})[lab] = summarise(T)
            today = T[T.date == "2026-10-23"]
            out["variants"][nm][lab]["today"] = int(today.pnl.sum())
            s = out["variants"][nm][lab]
            print(f"{lab[:6]} {nm[:52]:<52} train {s['train']['net']:>8} pf {s['train']['pf']} fall {s['train']['fall']:>7} | 2026 {s['y2026']['net']:>7} pf {s['y2026']['pf']} | 23 Oct {s['today']:>6}", flush=True)
    json.dump(out, open(os.path.join(HERE, "round40_results.json"), "w"), indent=1, default=str)
    print(json.dumps(out["facts"], indent=1))
