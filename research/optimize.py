"""Search loop: many strategies x many settings, tuned on OLD days only, then
judged on the newest 30/60/90/120 days that the search never saw."""
import itertools, json, math, random, sys, time
from multiprocessing import Pool
import engine as E

random.seed(7)
SYMS = ["NIFTY", "BANKNIFTY"]
TEST_DAYS = 120
DAYS = {s: E.load_days(s) for s in SYMS}
SPLIT = {s: (d[:-TEST_DAYS], d[-TEST_DAYS:]) for s, d in DAYS.items()}

COMMON = {"rr": [1.5, 2.0, 3.0, None], "be": [0, 1.0], "tstop": [0, 45, 90], "ctx": [-9, 0, 1], "otm": [0, 1],
          "last_entry": [780, 870], "max_trades": [1, 2], "rmin": [0.5, 1.0], "rmax": [1.5, 2.5],
          "vix_min": [0, 12], "vix_max": [99, 22]}
GRID = {
    "orb": {"or_min": [15, 30], "stop": ["mid", "opp", "atr"], "vwap": [0, 1], "trend": [0, 1], "adx": [0, 20],
            "or_wmax": [0.8, 1.2], "trail": ["", "vwap", "atr"], "trail_atr": [1.5, 2.5]},
    "orb_candle": {"or_min": [5, 15], "confirm": [0, 1], "rr": [2, 3, 5, 10, None], "min_body": [0, 0.1],
                   "trail": ["", "vwap"]},
    "noise": {"mult": [1.0, 1.25, 1.5, 2.0], "vwap": [0, 1], "rr": [None, 3.0], "stop_atr": [1.0, 1.5, 2.0]},
    "vwap_pull": {"line": ["vwap", "ema21"], "adx": [20, 25, 30], "st": [0, 1], "rr": [1.5, 2.0, 3.0], "start_bar": [3, 6],
                  "trail": ["", "vwap"]},
    "supertrend": {"tf": [5, 15], "adx": [0, 20, 25], "vwap": [0, 1], "stop_atr": [1.0, 1.5, 2.0], "rr": [None, 2.0, 3.0],
                   "trail": ["st", "st15", ""]},
    "pdhl": {"cpr_max": [0, 0.15, 0.25], "buf_atr": [0.3, 0.5, 1.0], "trail": ["", "vwap", "atr"], "trail_atr": [1.5, 2.5]},
    "ema_adx": {"adx": [20, 25, 30], "stop_atr": [1.0, 1.5, 2.0], "trail": ["", "vwap"]},
    "gap": {"gap": [0.3, 0.5, 0.8], "mode": ["go", "fade"], "hold_min": [10, 15, 30]},
}
FLY = {"entry": [560, 570, 600], "wing": [6, 8, 10, 12], "sl": [0.2, 0.3, 0.5], "tp": [0, 0.3, 0.5],
       "exit": [915, 870], "skip_expiry": [0, 1], "vix_min": [0, 12], "vix_max": [99, 20]}
N_PER = int(sys.argv[1]) if len(sys.argv) > 1 else 300


def sample(grid, n):
    keys = list(grid)
    full = math.prod(len(grid[k]) for k in keys)
    if full <= n:
        return [dict(zip(keys, v)) for v in itertools.product(*grid.values())]
    seen, out = set(), []
    while len(out) < n:
        v = tuple(random.choice(grid[k]) for k in keys)
        if v not in seen:
            seen.add(v); out.append(dict(zip(keys, v)))
    return out


def configs():
    out = []
    for st, g in GRID.items():
        grid = {**COMMON, **g}
        if st == "noise":
            grid.pop("tstop")
        out += [(st, p) for p in sample(grid, N_PER)]
    out += [("ironfly", p) for p in sample(FLY, N_PER)]
    return out


def go(days, sym, st, p):
    return E.run_ironfly(days, sym, p) if st == "ironfly" else E.run(days, sym, st, p)


def evaluate(job):
    st, p = job
    res = {"strategy": st, "params": p, "train": {}, "test": {}}
    for sym in SYMS:
        tr_days, te_days = SPLIT[sym]
        tr = go(tr_days, sym, st, p)
        h = len(tr_days) // 2
        first = [t for t in tr if t["date"] < tr_days[h].d]
        res["train"][sym] = {"all": E.stats(tr, len(tr_days)), "h1": E.stats(first, h),
                             "h2": E.stats([t for t in tr if t["date"] >= tr_days[h].d], len(tr_days) - h)}
        te = go(te_days, sym, st, p)
        res["test"][sym] = {f"last{w}": E.stats([t for t in te if t["date"] >= te_days[-w].d], w) for w in (30, 60, 90, 120)}
    return res


def score(r):
    """Robust train score: must make money on both indices AND in both halves of the train period."""
    tr = r["train"]
    if any(tr[s]["all"]["n"] < 40 for s in SYMS):
        return -99
    if any(tr[s][h]["net"] <= 0 for s in SYMS for h in ("h1", "h2")):
        return -50 + sum(tr[s]["all"]["sharpe"] for s in SYMS)
    return min(tr[s]["all"]["sharpe"] for s in SYMS) + 0.25 * sum(tr[s]["all"]["sharpe"] for s in SYMS)


if __name__ == "__main__":
    jobs = configs()
    for s in SYMS:
        print(s, "train", SPLIT[s][0][0].d, "->", SPLIT[s][0][-1].d, len(SPLIT[s][0]), "days | test",
              SPLIT[s][1][0].d, "->", SPLIT[s][1][-1].d)
    print(len(jobs), "configurations"); sys.stdout.flush()
    t = time.time()
    with Pool(2) as pool:
        res = pool.map(evaluate, jobs, chunksize=8)
    for r in res:
        r["score"] = score(r)
    print("done in", round(time.time() - t), "s")
    json.dump(res, open("results_cal.json" if E.CALIB else "results.json", "w"), default=str)
