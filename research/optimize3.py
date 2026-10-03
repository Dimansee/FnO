"""Round 5: remaining families (EMA formulas, MACD, Bollinger, candlestick patterns, Heikin-Ashi, Donchian,
VWAP sigma bands, Camarilla) + COMBINATIONS: the current rule confirmed by 1, 2 or 3 other signals, and
'voting' (enter only when at least k of n signals agree). Real-price-corrected premiums (FNO_CALIB=1)."""
import itertools, json, sys, time
from multiprocessing import Pool
import optimize as O

O.COMMON = {"rr": [1.5, 2.0, 3.0, None], "be": [0, 1.0], "tstop": [0, 45], "ctx": [-9, 0], "otm": [0, -1],
            "last_entry": [780, 870], "max_trades": [1, 2], "rmin": [0.3, 0.5, 1.0], "rmax": [1.5, 3.0],
            "vix_min": [0, 11], "vix_max": [99, 22]}
O.GRID = {
    "ema_trend": {"mode": ["stack", "cross", "triple"], "line": [9, 21], "vwap": [0, 1]},
    "macd": {"zero": [0, 1], "vwap": [0, 1], "stop_atr": [1.0, 1.5, 2.0], "trail": ["", "vwap", "st"]},
    "bb": {"mode": ["squeeze", "revert"], "sq": [1.1, 1.3]},
    "candle": {"only": [None, "engulfing", "hammer", "shooting star"], "at": ["vwap", "ema21", "bb"], "trend": [0, 1], "tol": [0.0005, 0.0015]},
    "heikin": {"n_same": [1, 2], "nowick": [0, 1], "vwap": [0, 1], "stop_atr": [1.0, 1.5, 2.0], "trail": ["", "vwap", "st"]},
    "donchian": {"n": [12, 20, 30], "stop": ["mid", "opp"], "vwap": [0, 1], "trail": ["", "vwap", "atr"]},
    "vwap_sd": {"mode": ["revert", "break"], "k": [1.5, 2.0, 2.5]},
    "camarilla": {"mode": ["break", "revert"], "trail": ["", "vwap"]},
}
NOISE = dict(mult=1.75, stop_atr=2.0, rr=4.0, vwap=1, max_trades=1, vix_min=11, last_entry=870, otm=-1, be=0, ctx=-9, rmin=1.0, rmax=2.5)
FL = ["st", "st15", "ema_stack", "macd", "rsi50", "adx20", "ha", "bbw_up", "strong_close", "beyond_pdhl", "gap_dir", "ctx", "e50"]


def combo_jobs():
    jobs = [("noise", dict(NOISE, tag="base"))]
    for r in (1, 2, 3):
        for c in itertools.combinations(FL, r):
            jobs.append(("noise", dict(NOISE, filters=list(c), tag="+".join(c))))
    for n_ in (4, 6, 8):
        pool = FL[:n_]
        for k in range(1, n_ + 1):
            jobs.append(("noise", dict(NOISE, vote=(k, pool), tag=f"vote {k}/{n_}")))
    return jobs


if __name__ == "__main__":
    O.N_PER = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    fam = []
    for st, g in O.GRID.items():
        fam += [(st, p) for p in O.sample({**O.COMMON, **g}, O.N_PER)]
    jobs = fam + combo_jobs()
    print(len(fam), "family configs +", len(jobs) - len(fam), "combinations"); sys.stdout.flush()
    t = time.time()
    with Pool(2) as pool:
        res = pool.map(O.evaluate, jobs, chunksize=8)
    for r in res:
        r["score"] = O.score(r)
    print("done in", round(time.time() - t), "s")
    json.dump(res, open("results_r5_cal.json", "w"), default=str)
