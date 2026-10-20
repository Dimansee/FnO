"""Round 32b: the same one-direction rules with a wider stop (at least 1 ATR) and the trail starting only after +1R, 15-minute
candles, swing / ATR trails - so option costs are a smaller share of each trade's risk. -> /tmp/round32_wide.pkl"""
import os, sys, itertools, pickle, warnings
warnings.filterwarnings("ignore"); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
from multiprocessing import Pool
import round32_engine as E
GRID = [dict(bias=b, trig=g, trail=t, tf=15, min_stop=1.0, trail_after=1.0) for b, g, t in
        itertools.product(E.BIASES, E.TRIGGERS, ("swing", "atr2"))]
def work(sym):
    vix = E.H.load("INDIAVIX")["close"]; meta = E.B.D.instrument(sym); res = []; cache = {}
    for x in E.prep(sym, 15):
        k0 = vix.index.searchsorted(x["day"].index[0], side="right") - 1
        if (float(vix.iloc[k0]) if k0 >= 0 else 14.0) < E.C.VIX_MIN: continue
        sigs = {g: E.signals(x, g) for g in E.TRIGGERS}
        for k, P in enumerate(GRID):
            tr = E.simulate(x, *sigs[P["trig"]], E.bias_of(x, P["bias"]), P["trail"], lock_first=P["bias"] == "first",
                            min_stop=1.0, trail_after=1.0)
            for n, (i_in, i_out, xs, side, sl0, c0, dist) in enumerate(tr):
                key = (i_in, i_out, round(xs, 2), side, round(sl0, 2))
                if key not in cache:
                    cache[key] = E.E30.price_trade(sym, x["d"], x["day"], i_in, i_out, xs, "CE" if side > 0 else "PE", sl0, vix, meta)
                pnl, rl = cache[key]
                res.append((k, sym, str(x["d"]), x["day"].index[i_in].strftime("%H:%M"), n, side, side * (xs - c0) / dist, pnl, pnl / rl * 2000))
    return res
if __name__ == "__main__":
    with Pool(2) as p: rows = sum(p.map(work, ["NIFTY", "BANKNIFTY", "SENSEX"]), [])
    df = pd.DataFrame(rows, columns=["k", "sym", "date", "entry", "n", "side", "R", "pnl", "u"])
    pickle.dump({"grid": GRID, "trades": df}, open("/tmp/round32_wide.pkl", "wb")); print("saved", len(df))
