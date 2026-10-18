"""Round 30 grid: 8 entries x 2 timeframes x 3 volume levels x ADX on/off x VWAP on/off x 4 exits = 768 settings x 3 indices."""
import os, sys, itertools, pickle, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
from multiprocessing import Pool
import round30_engine as E
ENTRIES = ["ema:9,21", "ema:20,50", "ema:5,13", "macd", "macd0", "emamacd:9,21", "emamacd:20,50", "donch"]
GRID = [dict(entry=e, tf=tf, vol=v, adx=a, vwap=w, exit=x) for e, tf, v, a, w, x in
        itertools.product(ENTRIES, (5, 15), (None, 1.3, 1.6), (False, True), (False, True), ("opp", "trail", "hold", "t2"))]
OUT = sys.argv[1] if len(sys.argv) > 1 else "round30_trades.pkl"
def work(args):
    sym, tf = args
    rel = pd.read_pickle(REL); vix = E.H.load("INDIAVIX")["close"]
    d5, x = E.frame(sym, tf, rel)
    res = []
    for k, P in enumerate(GRID):
        if P["tf"] != tf: continue
        for t in E.run(sym, d5, x, vix, P):
            res.append((k, t["sym"], t["date"], t["u"], t["pnl"], t["R"]))
    return res
REL = "/tmp/round30_rel.pkl"
if __name__ == "__main__":
    E.volume_proxy().to_pickle(REL)
    jobs = [(s, tf) for s in ("NIFTY", "BANKNIFTY", "SENSEX") for tf in (15, 5)]
    rows = []
    with Pool(2) as p:
        for r in p.imap_unordered(work, jobs):
            rows += r; print("done chunk", len(r), flush=True)
    df = pd.DataFrame(rows, columns=["k", "sym", "date", "u", "pnl", "R"])
    pickle.dump({"grid": GRID, "trades": df}, open(OUT, "wb"))
    print("saved", len(df), "trades")
