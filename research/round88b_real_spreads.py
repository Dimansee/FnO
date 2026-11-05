"""Round 88b: real bid/ask cost per strike from the market recorder (research/data/live/<date>/snapshots.json.gz).
Half-spread % = (ask - bid) / 2 / mid, i.e. the cost of crossing to the far side on one fill versus the mid price.
Nearest expiry, snapshots 09:20-15:15, by index and moneyness (1 ITM, ATM, 1 OTM, 2 OTM), calls and puts together.
The backtests charge 0.5% per fill plus a size impact term; round 88 stressed +1% per fill on top.
Usage: python research/round88b_real_spreads.py [data_dir]
"""
import gzip, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data")
import numpy as np, pandas as pd

STEP = {"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100}


def main():
    live = os.path.join(DATA, "live")
    out = []
    for day in sorted(os.listdir(live)):
        f = os.path.join(live, day, "snapshots.json.gz")
        if not os.path.exists(f):
            continue
        s = json.load(gzip.open(f))
        F = s["fields"]
        for snap in s["snapshots"]:
            ts = str(snap.get("t") or snap.get("ts") or "")
            hm = ts[11:16] if len(ts) >= 16 else ts[:5]
            if hm and not ("09:20" <= hm <= "15:15"):
                continue
            sym = snap.get("sym") or snap.get("symbol")
            spot = snap.get("spot")
            if sym not in STEP or not spot:
                continue
            rows = pd.DataFrame(snap["rows"], columns=F)
            exps = sorted({snap["exp"]}) if "exp" in snap else []
            st = STEP[sym]
            atm = round(spot / st) * st
            for side, sgn in (("ce", 1), ("pe", -1)):
                b, a = f"{side}_bid", f"{side}_ask"
                if b not in rows or a not in rows:
                    continue
                for lab, off in (("1 ITM", -1), ("ATM", 0), ("1 OTM", 1), ("2 OTM", 2)):
                    k = atm + sgn * off * st
                    r = rows[rows.strike == k]
                    if r.empty:
                        continue
                    bid, ask = float(r[b].iloc[0]), float(r[a].iloc[0])
                    if bid <= 0 or ask <= bid:
                        continue
                    mid = (bid + ask) / 2
                    out.append({"date": day, "sym": sym, "exp": snap.get("exp"), "m": lab, "half_pct": (ask - bid) / 2 / mid * 100, "mid": mid})
    D = pd.DataFrame(out)
    if D.empty:
        print("no usable snapshots"); return
    # nearest expiry per (date, sym)
    D = D[D.exp == D.groupby(["date", "sym"]).exp.transform("min")]
    t = D.groupby(["sym", "m"]).half_pct.agg(["median", lambda x: np.percentile(x, 90), "count"]).round(3)
    t.columns = ["median %", "p90 %", "n"]
    print(f"days {D.date.nunique()} ({D.date.min()} .. {D.date.max()})")
    print(t.to_string())
    json.dump({"days": int(D.date.nunique()), "table": {f"{a}|{b}": v for (a, b), v in t.to_dict("index").items()}},
              open(os.path.join(HERE, "round88b_results.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
