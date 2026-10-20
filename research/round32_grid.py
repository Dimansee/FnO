"""Round 32 grid: 6 biases x 5 triggers x 4 trailing stops x 2 timeframes = 240 settings x 3 indices, up to 3 trades a day
(the first trade of each day gives the 1-trade-a-day version). Run: python round32_grid.py -> /tmp/round32_trades.pkl"""
import os, sys, itertools, pickle, warnings, time as _t
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
from multiprocessing import Pool
import round32_engine as E

GRID = [dict(bias=b, trig=g, trail=t, tf=tf) for b, g, t, tf in itertools.product(E.BIASES, E.TRIGGERS, E.TRAILS, (5, 15))]
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/round32_trades.pkl"


def work(args):
    sym, tf = args
    t0 = _t.time()
    vix = E.H.load("INDIAVIX")["close"]; meta = E.B.D.instrument(sym)
    days = E.prep(sym, tf)
    res = []; cache = {}
    for x in days:
        k0 = vix.index.searchsorted(x["day"].index[0], side="right") - 1
        if (float(vix.iloc[k0]) if k0 >= 0 else 14.0) < E.C.VIX_MIN:
            continue
        sigs = {g: E.signals(x, g) for g in E.TRIGGERS}
        for k, P in enumerate(GRID):
            if P["tf"] != tf: continue
            sig, stop, sh, sl = sigs[P["trig"]]
            bias = E.bias_of(x, P["bias"])
            trades = E.simulate(x, sig, stop, sh, sl, bias, P["trail"], lock_first=P["bias"] == "first")
            for n, (i_in, i_out, xs, side, sl0, c0, dist) in enumerate(trades):
                key = (i_in, i_out, round(xs, 2), side, round(sl0, 2))
                if key not in cache:
                    cache[key] = E.E30.price_trade(sym, x["d"], x["day"], i_in, i_out, xs, "CE" if side > 0 else "PE", sl0, vix, meta)
                pnl, rl = cache[key]
                res.append((k, sym, str(x["d"]), x["day"].index[i_in].strftime("%H:%M"), n, side, side * (xs - c0) / dist,
                            pnl, pnl / rl * 2000))
    print(sym, tf, "done", len(res), "trades", round(_t.time() - t0), "s", flush=True)
    return res


if __name__ == "__main__":
    jobs = [(s, tf) for s in ("NIFTY", "BANKNIFTY", "SENSEX") for tf in (5, 15)]
    rows = []
    with Pool(2) as p:
        for r in p.imap_unordered(work, jobs):
            rows += r
    df = pd.DataFrame(rows, columns=["k", "sym", "date", "entry", "n", "side", "R", "pnl", "u"])
    pickle.dump({"grid": GRID, "trades": df}, open(OUT, "wb"))
    print("saved", len(df), "trades")
