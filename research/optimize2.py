"""Round 3: the setups popular Indian YouTube/F&O creators teach, through the same honest loop."""
import json, sys, time
from multiprocessing import Pool
import optimize as O

O.COMMON = {"rr": [1.5, 2.0, 3.0, None], "be": [0, 1.0], "tstop": [0, 45], "ctx": [-9, 0], "otm": [0, 1],
            "last_entry": [780, 870], "max_trades": [1, 2, 3], "rmin": [0.3, 0.5, 1.0], "rmax": [1.5, 3.0],
            "vix_min": [0, 11], "vix_max": [99, 22]}
O.GRID = {
    "ema5": {"sides": ["both", "short", "long"], "tf_l": [15, 5], "valid": [1, 3], "rr": [2.0, 3.0, None],
             "trail": ["", "vwap"], "skip_mid": [0, 1]},
    "inside": {"tf": [5, 15], "min_mother_atr": [0.5, 1.0, 1.5], "vwap": [0, 1], "first_entry": [0, 720],
               "rr": [1.0, 2.0, 3.0, None], "trail": ["", "vwap", "atr"], "trail_atr": [1.5, 2.5]},
    "ma44": {"tf": [5, 15], "tol": [0.0005, 0.001, 0.002], "shorts": [0, 1], "valid_bars": [3, 6], "rr": [2.0, 3.0],
             "trail": ["", "vwap"]},
    "rsi6040": {"hi": [60, 65, 70], "htf": [0, 1], "htf_slack": [0, 10], "vwap": [0, 1], "stop_atr": [1.0, 1.5, 2.0],
                "trail": ["", "vwap", "st"]},
    "mtf": {"swing": [4, 6, 10], "vwap": [0, 1], "trail": ["", "vwap", "st15"]},
    "fib": {"swing_end": [600, 615, 645], "min_swing_atr": [2, 3, 4], "zone": [(0.382, 0.5), (0.5, 0.618)],
            "ext": [1.272, 1.618], "rr": [None, 2.0]},
}


def configs():
    out = []
    for st, g in O.GRID.items():
        out += [(st, p) for p in O.sample({**O.COMMON, **g}, O.N_PER)]
    return out


if __name__ == "__main__":
    O.N_PER = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    jobs = configs()
    print(len(jobs), "configurations"); sys.stdout.flush()
    t = time.time()
    with Pool(2) as pool:
        res = pool.map(O.evaluate, jobs, chunksize=8)
    for r in res:
        r["score"] = O.score(r)
    print("done in", round(time.time() - t), "s")
    json.dump(res, open("results_creators.json", "w"), default=str)
