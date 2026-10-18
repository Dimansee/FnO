"""Round 24 (Oct 2026): (1) all rules on Sensex (1-minute BSE history 2023-01 -> 2026-10), (2) debit spreads instead of bought
options on VIX >= 18 days, (3) an earlier start for the trend-day catcher, (4) one-year bootstrap of a Rs 20,000 account with the
catcher. Same pricing as the app backtester. Run: python round24.py [sensex|spread|early|boot ...]  -> round24_results.json"""
import itertools, json, os, sys
from datetime import time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
import pandas as pd
import round23_harness as H23, round23_trend as TB, round24_harness as H24

VIX = H23.load("INDIAVIX")["close"]
BASE = dict(cam_tstop=45, noise_trail="band_vwap", overlap=False, reentry=0)
TREND = dict(t0=time(10, 45), share=0.8, stop="vwap", mult=1.75, be=1.0)
OUT = os.path.join(HERE, "round24_results.json")
out = json.load(open(OUT)) if os.path.exists(OUT) else {}


def st(df):
    if df.empty:
        return {"n": 0, "net": 0}
    df = df.sort_values(["date", "entry_time"])
    w, l = df.pnl[df.pnl > 0].sum(), -df.pnl[df.pnl <= 0].sum()
    eq = df.pnl.cumsum()
    return {"n": int(len(df)), "net": round(float(df.pnl.sum())), "pf": round(float(w / l), 2) if l else None,
            "fall": round(float((eq - eq.cummax().clip(lower=0)).min()))}


def split(df):
    df = df.copy(); df["y"] = df.date.str[:4]; df["m"] = df.date.str[:7]
    return {"train_2023_25": st(df[df.y < "2026"]), "y2026": st(df[df.y == "2026"]),
            "years": {k: round(float(v)) for k, v in df.groupby("y").pnl.sum().items()},
            "months_2026": {k: round(float(v)) for k, v in df[df.y == "2026"].groupby("m").pnl.sum().items()}}


want = set(sys.argv[1:]) or {"sensex", "spread", "early", "boot"}

if "sensex" in want:
    SX = H23.load("SENSEX"); r = {}
    for sz, cap in [("risk", 200_000), ("one_lot", 20_000)]:
        cur = pd.DataFrame(H23.run("SENSEX", SX, VIX, {**BASE, "sizing": sz}, capital=cap))
        no = pd.DataFrame(H23.run("SENSEX", SX, VIX, {**BASE, "sizing": sz, "cam_vix_max": -1}, capital=cap))
        trd = pd.DataFrame(TB.run("SENSEX", SX, VIX, {**TREND, "sizing": sz}, capital=cap))
        for nm, df in [("noise + camarilla", cur), ("noise only", no), ("trend-day catcher", trd), ("noise + camarilla + catcher", pd.concat([cur, trd])),
                       ("noise + catcher", pd.concat([no, trd]))]:
            r[f"{sz} {nm}"] = split(df)
            print("sensex", sz, nm, r[f"{sz} {nm}"]["train_2023_25"], r[f"{sz} {nm}"]["y2026"], flush=True)
    out["sensex"] = r

if "spread" in want:
    r = {}
    for sz in ("risk", "one_lot"):
        for nm, ov in [("current", {}), ("no ceilings, bought option", dict(noise_vix_max=99, cam_vix_max=99))] + \
                [(f"no ceilings, debit spread {w} strikes wide when VIX >= 18", dict(noise_vix_max=99, cam_vix_max=99, spread_vix=18, spread_w=w)) for w in (2, 4, 8, 12)]:
            rows = []
            for s in ("NIFTY", "BANKNIFTY"):
                rows += H24.run(s, H24.load(s), VIX, {**BASE, "sizing": sz, **ov})
            df = pd.DataFrame(rows)
            r[f"{sz} {nm}"] = {**split(df), "spread_trades": st(df[df.spread]) if "spread" in df else None}
            print("spread", sz, nm, r[f"{sz} {nm}"]["train_2023_25"], r[f"{sz} {nm}"]["y2026"], flush=True)
    out["spread"] = r

if "early" in want:
    r = {}
    for t0, share in itertools.product([time(9, 45), time(10, 15), time(10, 45)], [0.7, 0.8]):
        rows = []
        for s in ("NIFTY", "BANKNIFTY"):
            rows += TB.run(s, H23.load(s), VIX, {**TREND, "t0": t0, "share": share, "sizing": "risk"})
        df = pd.DataFrame(rows)
        r[f"{t0:%H:%M} share {share}"] = {**split(df), "oct13": df[df.date == "2026-10-13"][["sym", "entry_time"]].assign(pnl=df.pnl.round()).values.tolist()}
        print("early", t0, share, r[f"{t0:%H:%M} share {share}"]["train_2023_25"], flush=True)
    out["early"] = r

if "boot" in want:
    cur, trd = [], []
    for s in ("NIFTY", "BANKNIFTY"):
        cur += H23.run(s, H23.load(s), VIX, {**BASE, "sizing": "one_lot"}, capital=20_000)
        trd += TB.run(s, H23.load(s), VIX, {**TREND, "sizing": "one_lot"}, capital=20_000)
    cur, trd = pd.DataFrame(cur), pd.DataFrame(trd)
    for d in (cur, trd):
        d["t_in"] = pd.to_datetime(d.date + " " + d.entry_time); d["t_out"] = pd.to_datetime(d.date + " " + d.exit_time)
    free = trd[trd.apply(lambda r: not ((cur.sym == r.sym) & (cur.date == r.date) & (cur.t_in <= r.t_in) & (cur.t_out > r.t_in)).any(), axis=1)]
    V = {"current rules": (cur.assign(c=0), None), "current + catcher": (pd.concat([cur.assign(c=0), trd.assign(c=1)]), None),
         "current + catcher only when the index has no open trade": (pd.concat([cur.assign(c=0), free.assign(c=1)]), None),
         "current + catcher once equity >= Rs 40k": (pd.concat([cur.assign(c=0), trd.assign(c=1)]), 40_000)}
    rng, START, STUCK = np.random.default_rng(23), 20_000, 12_000
    weeks = sorted(set(pd.date_range("2023-01-02", "2026-10-16", freq="W-MON").strftime("%G-%V")))
    r = {}
    for name, (df, gate) in V.items():
        df = df.sort_values("t_in"); df["wk"] = df.t_in.dt.strftime("%G-%V")
        g = {w: list(zip(x.pnl, x.c)) for w, x in df.groupby("wk")}
        ends, falls, stuck = [], [], 0
        for _ in range(2000):
            eq = peak = START; mf = 0.0; dead = False
            for w in rng.choice(weeks, 52):
                for p, c in g.get(w, []):
                    if gate and c and eq < gate:
                        continue
                    eq += p; peak = max(peak, eq); mf = min(mf, eq / peak - 1)
                    if eq < STUCK:
                        dead = True; break
                if dead:
                    break
            ends.append(eq); falls.append(mf); stuck += dead
        e, f = np.array(ends), np.array(falls)
        r[name] = {"median_end": round(float(np.median(e))), "p5": round(float(np.percentile(e, 5))), "p95": round(float(np.percentile(e, 95))),
                   "end_below_start_pct": round(float((e < START).mean() * 100), 1), "fall_50_pct": round(float((f <= -0.5).mean() * 100), 1),
                   "cant_afford_a_lot_pct": round(stuck / 2000 * 100, 1)}
        print("boot", name, r[name], flush=True)
    out["boot_20k"] = r

json.dump(out, open(OUT, "w"), indent=1, default=str)
print("saved", OUT)
