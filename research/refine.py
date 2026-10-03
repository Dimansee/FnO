"""Round 2: full grid around the winning family (noise-area momentum), choosing the
setting whose NEIGHBOURS also do well on the training days (a smoothed score), so
the pick isn't a lucky spike. Then report the untouched last 30/60/90/120 days."""
import itertools, json, math, sys, time
from multiprocessing import Pool
import numpy as np
import engine as E

SYMS = ["NIFTY", "BANKNIFTY"]
TEST = 120
DAYS = {s: E.load_days(s) for s in SYMS}
DATES = [d.d for d in DAYS["NIFTY"]]
SPLIT_DATE = DATES[-TEST]
GRID = {"mult": [1.25, 1.5, 1.75, 2.0], "stop_atr": [1.5, 2.0, 2.5], "rr": [2.0, 3.0, 4.0, None], "vwap": [0, 1],
        "max_trades": [1, 2], "vix_min": [0, 11, 12, 13], "last_entry": [810, 870], "otm": [0, -1]}
FIXED = {"be": 0, "ctx": -9, "rmin": 1.0, "rmax": 2.5, "vix_max": 99}
KEYS = list(GRID)


def run_one(vals):
    p = dict(FIXED, **dict(zip(KEYS, vals)))
    out = {}
    for s in SYMS:
        tr = E.run(DAYS[s], s, "noise", p)
        out[s] = [(t["date"], t["pnl"], t["R"]) for t in tr]
    return vals, out


def daily(trs, a, b):
    v = {}
    for d, pnl, _ in trs:
        if a <= d < b:
            v[d] = v.get(d, 0) + pnl
    return v


def sh(v, ndays):
    x = np.zeros(ndays)
    x[: len(v)] = list(v.values())
    return float(x.mean() / x.std() * math.sqrt(252)) if x.std() > 0 else 0.0


if __name__ == "__main__":
    combos = list(itertools.product(*GRID.values()))
    import os, pickle
    if os.path.exists("refine_rows.pkl"):
        rows = pickle.load(open("refine_rows.pkl", "rb"))
    else:
        t = time.time()
        with Pool(2) as pool:
            rows = pool.map(run_one, combos, chunksize=16)
        pickle.dump(rows, open("refine_rows.pkl", "wb"))
        print(len(rows), "configs simulated in", round(time.time() - t), "s")
    ntr = len([d for d in DATES if d < SPLIT_DATE])
    yrs = sorted({d.year for d in DATES})
    base = {}
    for vals, out in rows:
        tr_sh = []
        ok = True
        for s in SYMS:
            v = daily(out[s], DATES[0], SPLIT_DATE)
            tr_sh.append(sh(v, ntr))
            if sum(v.values()) <= 0:
                ok = False
        for y in yrs[:-1]:                          # every full training year profitable for the 2-index book
            if sum(p for s in SYMS for d, p, _ in out[s] if d.year == y and d < SPLIT_DATE) <= 0:
                ok = False
        base[vals] = (min(tr_sh) + 0.5 * sum(tr_sh)) if ok else -5 + sum(tr_sh)
    # neighbour-smoothed score: average over configs that differ in one setting by one notch
    def neigh(vals):
        out = [vals]
        for i, k in enumerate(KEYS):
            j = GRID[k].index(vals[i])
            for dj in (-1, 1):
                if 0 <= j + dj < len(GRID[k]):
                    nv = list(vals); nv[i] = GRID[k][j + dj]; out.append(tuple(nv))
        return out
    smooth = {v: float(np.mean([base[n] for n in neigh(v)])) for v in base}
    ranked = sorted(smooth, key=lambda v: -smooth[v])
    res = []
    for vals in ranked[:15]:
        out = dict(rows)[vals]
        r = {"params": dict(zip(KEYS, vals)), "smooth": smooth[vals], "raw": base[vals], "test": {}, "train": {}, "years": {}}
        for s in SYMS:
            tr_tr = [x for x in out[s] if x[0] < SPLIT_DATE]
            r["train"][s] = {"net": sum(x[1] for x in tr_tr), "n": len(tr_tr), "avgR": float(np.mean([x[2] for x in tr_tr])) if tr_tr else 0}
            for w in (30, 60, 90, 120):
                tt = [x for x in out[s] if x[0] >= DATES[-w]]
                pos, neg = sum(x[1] for x in tt if x[1] > 0), -sum(x[1] for x in tt if x[1] <= 0)
                r["test"].setdefault(s, {})[w] = {"net": sum(x[1] for x in tt), "n": len(tt), "pf": pos / neg if neg else 99,
                                                   "win": sum(x[1] > 0 for x in tt) / len(tt) * 100 if tt else 0}
            for y in yrs:
                r["years"].setdefault(s, {})[y] = round(sum(x[1] for x in out[s] if x[0].year == y))
        res.append(r)
    json.dump(res, open("refine.json", "w"), default=str, indent=1)
    allnets = [sum(x[1] for s in SYMS for x in out[s] if x[0] >= SPLIT_DATE) for _, out in rows]
    print("test-120 profitable share across ALL", len(rows), "settings:", round(np.mean(np.array(allnets) > 0) * 100), "%  median ₹",
          round(float(np.median(allnets))))
    good = [v for v in base if base[v] > 0]
    gn = [sum(x[1] for s in SYMS for x in dict(rows)[v][s] if x[0] >= SPLIT_DATE) for v in good]
    print("…among", len(good), "train-robust settings:", round(np.mean(np.array(gn) > 0) * 100), "% profitable, median ₹", round(float(np.median(gn))))
    for r in res[:8]:
        print(r["params"], "smooth %.2f raw %.2f" % (r["smooth"], r["raw"]))
        for s in SYMS:
            print("   ", s, "years", r["years"][s], "| test", " ".join(f"{w}d ₹{r['test'][s][w]['net']/1000:+.1f}k pf{r['test'][s][w]['pf']:.2f} n{r['test'][s][w]['n']}" for w in (30, 60, 90, 120)))
