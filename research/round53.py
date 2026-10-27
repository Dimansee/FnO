"""Round 53 (28 Oct 2026): retest the displacement lead (round 51) against everything it could be repeating.

Displacement = a 5-min candle whose body is >= k x ATR(14) in the trade's direction among the last m closed bars before
the entry (round 51: k = 1, m = 3). Trades: round 43's generator (fno.backtest.run "autopilot", today's rules incl. the
big-run skip, 1 lot, 1 ITM, Nifty / Bank Nifty / Sensex, Jan 2023 -> latest).
  A  definition grid: k in 0.75 / 1 / 1.25 / 1.5, m in 1 / 2 / 3 / 5 -> per-trade gap in 2023-24, 2025, 2026
  B  overlap: the gap inside conviction-score buckets (live sizing uses score >= 5), inside each rule, inside 'agent
     agreement' (an earlier trade on the same index and side that day; pending p-agents) and inside the 09:30 CPR side
     (trades against it; pending p-cpr-against)
  C  walk-forward: choose the size tilt (1.25 / 1.5 / 2x with it, 1 / 0.75 / 0.5x without) on 2023-24, judge on 2025-26
  D  tilts together: displacement + agreement + CPR-against, each alone and combined (Rs 2L account, bootstrap)
  E  Rs 20,000 account (round 43 replay; the live small-account strike rule is not modelled here)
Run: python round53.py -> round53_results.json
"""
import os, sys, json, pickle, itertools, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round25_harness as H
import round43 as R43

SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]


def add_features(T):
    T = T.copy()
    for k, m in itertools.product((0.75, 1.0, 1.25, 1.5), (1, 2, 3, 5)):
        T[f"d_{k}_{m}"] = False
    T["cpr_side"] = 0
    for sym in SYMS:
        c = H.load(sym).copy()
        tr = pd.concat([c.high - c.low, (c.high - c.close.shift()).abs(), (c.low - c.close.shift()).abs()], axis=1).max(axis=1)
        c["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
        c["body"] = c.close - c.open
        days = {d: g for d, g in c.groupby(c.index.date)}
        dk = sorted(days); prev = {dk[i]: dk[i - 1] for i in range(1, len(dk))}
        for idx, r in T[T.sym == sym].iterrows():
            d = pd.Timestamp(r.date).date()
            if d not in days:
                continue
            g = days[d]
            et = pd.Timestamp(f"{r.date} {r.entry_time}")
            done = g[g.index + pd.Timedelta(minutes=5) <= et]
            if len(done) == 0:
                continue
            b = (done.body * r.sgn / done.atr).values
            for k, m in itertools.product((0.75, 1.0, 1.25, 1.5), (1, 2, 3, 5)):
                T.at[idx, f"d_{k}_{m}"] = bool((b[-m:] >= k).any())
            if d in prev:
                y = days[prev[d]]
                P = (y.high.max() + y.low.min() + y.close.iloc[-1]) / 3
                BC = (y.high.max() + y.low.min()) / 2
                TC = 2 * P - BC
                first = g[g.index + pd.Timedelta(minutes=5) <= pd.Timestamp(f"{r.date} 09:30")]
                if len(first):
                    px = first.close.iloc[-1]
                    T.at[idx, "cpr_side"] = 1 if px > max(TC, BC) else (-1 if px < min(TC, BC) else 0)
    T["disp"] = T["d_1.0_3"]
    T["cpr_against"] = (T.cpr_side != 0) & (T.cpr_side != T.sgn)
    T = T.sort_values(["date", "entry_time"]).reset_index(drop=True)
    agree = []
    for i, r in T.iterrows():
        agree.append(bool(((T.date == r.date) & (T.sym == r.sym) & (T.sgn == r.sgn) & (T.entry_time < r.entry_time)).any()))
    T["agree"] = agree
    T["per"] = np.where(T.date < "2025", "2023-24", np.where(T.date < "2026", "2025", "2026"))
    return T


def gap(X, col):
    a, b = X[X[col]], X[~X[col]]
    return {"n_with": int(len(a)), "avg_with": round(float(a.pnl.mean()), 0) if len(a) else None,
            "n_without": int(len(b)), "avg_without": round(float(b.pnl.mean()), 0) if len(b) else None}


if __name__ == "__main__":
    T0 = pickle.load(open(R43.PKL, "rb")) if os.path.exists(R43.PKL) else R43.gen()
    T = add_features(T0)
    out = {"A_definition": {}, "B_overlap": {}, "C_walkforward": {}, "D_combined": {}, "E_20k": {}}
    print("A. definition grid: avg Rs/trade with vs without, by period")
    for k, m in itertools.product((0.75, 1.0, 1.25, 1.5), (1, 2, 3, 5)):
        col = f"d_{k}_{m}"
        row = {p: gap(T[T.per == p], col) for p in ("2023-24", "2025", "2026")}
        share = round(float(T[col].mean() * 100), 1)
        out["A_definition"][col] = {"share_pct": share, **row}
        print(f"  k={k:<5} m={m}  share {share:5.1f}%  " + "  ".join(f"{p}: {v['avg_with']}/{v['avg_without']}" for p, v in row.items()), flush=True)
    print("B. overlap: displacement gap inside other groups")
    groups = {"score>=5": T.score >= 5, "score<5": T.score < 5, "agree": T.agree, "no agree": ~T.agree,
              "CPR against": T.cpr_against, "CPR with/inside": ~T.cpr_against}
    for st in T.strategy.unique():
        groups[f"rule {st}"] = T.strategy == st
    for nm, msk in groups.items():
        X = T[msk]
        out["B_overlap"][nm] = {"all": gap(X, "disp"), "2026": gap(X[X.per == "2026"], "disp")}
        g, g6 = out["B_overlap"][nm]["all"], out["B_overlap"][nm]["2026"]
        print(f"  {nm:24s} all {g['avg_with']}/{g['avg_without']} (n {g['n_with']}/{g['n_without']})  2026 {g6['avg_with']}/{g6['avg_without']}", flush=True)
    print("  correlation disp~score>=5", round(float(np.corrcoef(T.disp, T.score >= 5)[0, 1]), 3),
          " disp~agree", round(float(np.corrcoef(T.disp, T.agree)[0, 1]), 3), " disp~cpr_against", round(float(np.corrcoef(T.disp, T.cpr_against)[0, 1]), 3))
    print("C. walk-forward: tilt chosen on 2023-24, judged on 2025-26 (Rs 2L account each period)")
    tr, te = T[T.date < "2025"], T[T.date >= "2025"]
    base_tr, base_te = R43.replay(tr), R43.replay(te)
    grid = []
    for up, dn in itertools.product((1.25, 1.5, 2.0), (1.0, 0.75, 0.5)):
        fn = (lambda u, d_: (lambda r, e, p: u if r.disp else d_))(up, dn)
        a, b = R43.replay(tr, size_fn=fn), R43.replay(te, size_fn=fn)
        grid.append({"up": up, "down": dn, "train": a, "test": b})
        print(f"  {up}x / {dn}x  2023-24 {a['final']:>9} ({a['dd']})  2025-26 {b['final']:>9} ({b['dd']})", flush=True)
    best = max(grid, key=lambda g: g["train"]["final"] / max(1, -g["train"]["dd"]))
    out["C_walkforward"] = {"baseline": {"train": base_tr, "test": base_te}, "grid": grid, "chosen_on_train_return_per_fall": best}
    print(f"  baseline 2023-24 {base_tr['final']} ({base_tr['dd']})  2025-26 {base_te['final']} ({base_te['dd']});  chosen {best['up']}x/{best['down']}x -> test {best['test']}")
    print("D. tilts together (Rs 2L, full history; 2026 restart; bootstrap 1,000)")
    V = {"baseline": None,
         "displacement 1.5/0.75": lambda r, e, p: 1.5 if r.disp else 0.75,
         "agreement 1.33/0.67": lambda r, e, p: 1.33 if r.agree else 0.67,
         "CPR-against 1.25": lambda r, e, p: 1.25 if r.cpr_against else 1.0,
         "displacement + agreement": lambda r, e, p: (1.5 if r.disp else 0.75) * (1.33 if r.agree else 0.67),
         "displacement + CPR-against": lambda r, e, p: (1.5 if r.disp else 0.75) * (1.25 if r.cpr_against else 1.0),
         "all three": lambda r, e, p: (1.5 if r.disp else 0.75) * (1.33 if r.agree else 0.67) * (1.25 if r.cpr_against else 1.0)}
    for nm, fn in V.items():
        full, y26, bt = R43.replay(T, size_fn=fn), R43.replay(T[T.date >= "2026"], size_fn=fn), R43.boot(T, n=1000, size_fn=fn)
        out["D_combined"][nm] = {"full": full, "2026": y26, "boot": bt}
        print(f"  {nm:28s} full {full['final']:>9} ({full['dd']})  2026 {y26['final']:>8} ({y26['dd']})  boot {bt}", flush=True)
    print("E. Rs 20,000 account")
    for nm in ("baseline", "displacement 1.5/0.75"):
        fn = V[nm]
        full = R43.replay(T, start=20_000, size_fn=fn); y26 = R43.replay(T[T.date >= "2026"], start=20_000, size_fn=fn)
        bt = R43.boot(T, n=1000, start=20_000, size_fn=fn)
        out["E_20k"][nm] = {"full": full, "2026": y26, "boot": bt}
        print(f"  {nm:28s} full {full['final']:>9} ({full['dd']})  2026 {y26['final']:>8} ({y26['dd']})  boot {bt}", flush=True)
    json.dump(out, open(os.path.join(HERE, "round53_results.json"), "w"), indent=1, default=str)
    pickle.dump(T, open("/tmp/round53_trades.pkl", "wb"))
    print("saved")
