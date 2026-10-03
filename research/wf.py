"""Walk-forward test: every 30 trading days, re-pick each strategy's settings using ONLY
the previous TRAIN days, then trade the next 30 days with them. Stitching those
unseen 30-day blocks together gives an honest out-of-sample track record."""
import json, math, pickle, sys, time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

import engine as E
import optimize as O

SYMS = O.SYMS
DAYS = O.DAYS
DATES = [d.d for d in DAYS["NIFTY"]]
assert DATES == [d.d for d in DAYS["BANKNIFTY"]]
POS = {d: i for i, d in enumerate(DATES)}
TRAIN = int(sys.argv[1]) if len(sys.argv) > 1 else 250
STEP = 30
CACHE = Path("daily_pnl.pkl")


def daily(job):
    st, p = job
    out = {}
    for s in SYMS:
        tr = E.run(DAYS[s], s, st, p)
        v, n = np.zeros(len(DATES)), np.zeros(len(DATES), dtype=int)
        for t in tr:
            v[POS[t["date"]]] += t["pnl"]
            n[POS[t["date"]]] += 1
        out[s] = (v, n)
    return st, p, out


def sharpe(x):
    sd = x.std()
    return float(x.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0


def pick(cands, a, b):
    """Best config on days [a, b): both indices must be profitable; rank by Sharpe of the combined book,
    tie-broken by the weaker index (so one lucky index can't carry it)."""
    best, bs = None, -1e9
    for c in cands:
        vs = [c[2][s][0][a:b] for s in SYMS]
        ns = sum(int(c[2][s][1][a:b].sum()) for s in SYMS)
        if ns < 30 or any(v.sum() <= 0 for v in vs):
            continue
        tot = vs[0] + vs[1]
        h = len(tot) // 2
        if tot[:h].sum() <= 0 or tot[h:].sum() <= 0:
            continue
        sc = sharpe(vs[0] + vs[1]) + 0.5 * min(sharpe(v) for v in vs)
        if sc > bs:
            best, bs = c, sc
    return best


def summarise(v, n):
    eq = np.cumsum(v)
    dd = float(np.max(np.maximum.accumulate(np.concatenate([[0], eq])) - np.concatenate([[0], eq])))
    pos, neg = v[v > 0].sum(), -v[v < 0].sum()
    return {"net": float(v.sum()), "pf_days": float(pos / neg) if neg else 99.0, "sharpe": sharpe(v), "dd": dd,
            "trades": int(n.sum()), "days": len(v)}


if __name__ == "__main__":
    if CACHE.exists():
        rows = pickle.load(open(CACHE, "rb"))
    else:
        jobs = [j for j in O.configs() if j[0] != "ironfly"]
        t = time.time()
        with Pool(2) as pool:
            rows = pool.map(daily, jobs, chunksize=8)
        pickle.dump(rows, open(CACHE, "wb"))
        print("simulated", len(rows), "configs in", round(time.time() - t), "s")
    strategies = sorted({r[0] for r in rows})
    report = {}
    for st in strategies + ["ALL"]:
        cands = [r for r in rows if st == "ALL" or r[0] == st]
        v = {s: np.zeros(len(DATES)) for s in SYMS}
        n = {s: np.zeros(len(DATES), dtype=int) for s in SYMS}
        chosen = []
        for a in range(TRAIN, len(DATES), STEP):
            c = pick(cands, a - TRAIN, a)
            b = min(a + STEP, len(DATES))
            chosen.append((str(DATES[a]), c[0] if c else None, c[1] if c else None))
            if c is None:
                continue                      # nothing passed -> stay flat that block
            for s in SYMS:
                v[s][a:b] = c[2][s][0][a:b]
                n[s][a:b] = c[2][s][1][a:b]
        tot, cnt = v["NIFTY"] + v["BANKNIFTY"], n["NIFTY"] + n["BANKNIFTY"]
        oos = slice(TRAIN, len(DATES))
        rep = {"oos_from": str(DATES[TRAIN]), "all": summarise(tot[oos], cnt[oos]),
               "by_symbol": {s: summarise(v[s][oos], n[s][oos]) for s in SYMS},
               "windows": {w: summarise(tot[-w:], cnt[-w:]) for w in (30, 60, 90, 120, 250)},
               "flat_blocks": sum(1 for x in chosen if x[1] is None), "blocks": len(chosen), "last_choice": chosen[-1]}
        # by calendar quarter
        q = {}
        for i in range(TRAIN, len(DATES)):
            k = f"{DATES[i].year}Q{(DATES[i].month - 1) // 3 + 1}"
            q[k] = q.get(k, 0) + tot[i]
        rep["quarters"] = {k: round(x) for k, x in q.items()}
        report[st] = rep
        a = rep["all"]
        print(f"{st:11s} OOS {rep['oos_from']}→  net ₹{a['net']/1000:+7.1f}k  sharpe {a['sharpe']:+.2f}  pf(days) {a['pf_days']:.2f}  "
              f"maxDD ₹{a['dd']/1000:.0f}k  trades {a['trades']}  flat {rep['flat_blocks']}/{rep['blocks']} | last "
              + " ".join(f"{w}d:{rep['windows'][w]['net']/1000:+.0f}k" for w in (30, 60, 90, 120)))
    json.dump(report, open(f"wf_report_{TRAIN}.json", "w"), default=str, indent=1)
