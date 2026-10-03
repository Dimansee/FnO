import json, pickle, sys
from multiprocessing import Pool
from pathlib import Path
import numpy as np
import optimize2 as O2
import optimize as O
import wf as W

if __name__ == "__main__":
    O.N_PER = 400
    cache = Path("daily_pnl_creators_cal.pkl" if O.E.CALIB else "daily_pnl_creators.pkl")
    if cache.exists():
        rows = pickle.load(open(cache, "rb"))
    else:
        with Pool(2) as pool:
            rows = pool.map(W.daily, O2.configs(), chunksize=8)
        pickle.dump(rows, open(cache, "wb"))
    rep = {}
    for st in sorted({r[0] for r in rows}):
        cands = [r for r in rows if r[0] == st]
        v = np.zeros(len(W.DATES)); n = np.zeros(len(W.DATES), dtype=int)
        for a in range(250, len(W.DATES), 30):
            c = W.pick(cands, a - 250, a); b = min(a + 30, len(W.DATES))
            if c:
                for s in W.SYMS:
                    v[a:b] += c[2][s][0][a:b]; n[a:b] += c[2][s][1][a:b]
        s = W.summarise(v[250:], n[250:])
        rep[st] = {"net": s["net"], "sharpe": s["sharpe"]}
        print(f"{st:9s} walk-forward OOS 2024-26: net ₹{s['net']/1000:+.1f}k sharpe {s['sharpe']:+.2f} trades {s['trades']} | last 120d ₹{v[-120:].sum()/1000:+.1f}k")
    json.dump(rep, open("wf_creators_cal.json" if O.E.CALIB else "wf_creators.json", "w"))
