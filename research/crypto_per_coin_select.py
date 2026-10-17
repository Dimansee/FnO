"""Per-coin "best setup" selection, walk-forward (17 Oct 2026).

Question: if each coin (BTC, ETH, SOL, BNB, XRP) trades whichever setup did best on THAT coin over the trailing
12 months, re-chosen every quarter, does it make money on the following quarter (data the choice never saw)?
Compared: choose by best average R (profit per trade) vs choose by best win %, vs the fixed live autopilot rules.
Library: the 10 textbook families of global_rules_search.py x 1h/4h/1d x long/both = 60 setups per coin.
Engine and costs = the app's (fno.custom.simulate). Usage: python research/crypto_per_coin_select.py <data_dir>
"""
import json, os, sys
from concurrent.futures import ProcessPoolExecutor
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.argv += [] if len(sys.argv) > 1 else ["research/data/global"]
import global_rules_search as G  # noqa: E402
from fno import custom as CU  # noqa: E402

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
SETUPS = [(f, tf, d) for f in G.FAMILIES for tf in ("1h", "4h", "1d") for d in ("long", "both")]
LOOKBACK = pd.Timedelta(days=365)
QUARTERS = pd.date_range("2024-01-01", "2026-10-01", freq="QS", tz="UTC")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crypto_per_coin_select_results.json")


def run_coin(sym):
    raw = G.load("CRYPTO", sym)
    out = {}
    for fam, tf, d in SETUPS:
        b = CU.resample(raw, tf, "CRYPTO") if tf != "1h" else raw
        out[f"{fam}|{tf}|{d}"] = [(t["entry_ts"].value, t["R"]) for t in CU.simulate(G.spec("CRYPTO", fam, tf, d), b)]
    return sym, out


def window(trs, a, z):
    return [r for ts, r in trs if a.value <= ts < z.value]


def pick(per_setup, a, z, how):
    best, key = None, None
    for k, trs in per_setup.items():
        r = np.array(window(trs, a, z))
        if len(r) < 12:
            continue
        lo = -r[r <= 0].sum()
        pf = r[r > 0].sum() / lo if lo > 0 else 9
        if r.mean() <= 0.05 or pf < 1.1:
            continue
        score = r.mean() if how == "avgR" else (r > 0).mean()
        if best is None or score > best:
            best, key = score, k
    return key


def st(r):
    r = np.asarray(r, float)
    if not len(r):
        return {"n": 0}
    lo = -r[r <= 0].sum()
    return {"n": len(r), "avg_R": round(r.mean(), 3), "win": round((r > 0).mean() * 100, 1), "total_R": round(r.sum(), 1),
            "pf": round(r[r > 0].sum() / lo, 2) if lo > 0 else None, "t": round(r.mean() / (r.std(ddof=1) / np.sqrt(len(r))), 2) if len(r) > 2 else None}


def main():
    with ProcessPoolExecutor(5) as ex:
        data = dict(ex.map(run_coin, COINS))
    fixed = ["supertrend_10_3|4h|both", "donchian20_trail3|1d|long", "supertrend_10_3|1d|both"]
    res = {"avgR": {}, "win": {}, "fixed": {}}
    picks = {"avgR": [], "win": []}
    for i in range(len(QUARTERS) - 1):
        q0, q1 = QUARTERS[i], QUARTERS[i + 1]
        for sym in COINS:
            for how in ("avgR", "win"):
                k = pick(data[sym], q0 - LOOKBACK, q0, how)
                picks[how].append((str(q0.date()), sym, k))
                res[how].setdefault(sym, []).extend(window(data[sym][k], q0, q1) if k else [])
            for k in fixed:
                res["fixed"].setdefault(sym, []).extend(window(data[sym][k], q0, q1))
    # last quarter is partial (Oct 2026): included up to the data end
    summary = {}
    for how, per in res.items():
        allr = sum(per.values(), [])
        summary[how] = {"all": st(allr), **{s: st(v) for s, v in per.items()}}
        print(how.ljust(6), summary[how]["all"], {s: summary[how][s].get("avg_R") for s in COINS})
    # what each coin would trade from now on (chosen on the last 12 months)
    now = pd.Timestamp.now(tz="UTC")
    current = {s: {h: pick(data[s], now - LOOKBACK, now, h) for h in ("avgR", "win")} for s in COINS}
    for s in COINS:
        for h in ("avgR", "win"):
            k = current[s][h]
            current[s][h] = {"setup": k, "last12m": st(window(data[s][k], now - LOOKBACK, now)) if k else None}
    print(json.dumps(current, indent=1))
    json.dump({"summary": summary, "picks": picks, "current": current}, open(OUT, "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
