"""Round 99 (11 Nov 2026): how can 2026 make more? Backtested on 2026 only.

User: "2023-24 made 18.27L, 2025-26 22.91L, 2026 till now only 5.58L - research on 2026 data how we can grow this amount and
back test only on 26 data." (Each period restarts from Rs 2L: 2023-24 is 24 months, 2025-26 22 months, 2026 10 months.)
Method: today's Autopilot (rounds 88 + 90), Outcomes engine, 2 Jan - 5 Nov 2026, Rs 2L and Rs 20k.
  A. where 2026 profit comes from: by rule and index (replay rows) and per lot vs earlier years
  B. one change at a time: risk %, confidence sizing, brakes and filters, strike, lot cap, loss cap, exits (incl. round 98),
     indices, dropping one rule
  C. walk-forward inside 2026: choose on Jan-Jun, check on Jul-5 Nov; combine the changes that pass Jan-Jun, check Jul-Nov
  2023-25 is reported for information only (the user asked for a 2026-only test).
Usage: python research/round99_grow_2026.py
"""
import itertools, json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import pandas as pd
import outcomes as O
import round98_reversal_and_trail as R98
from fno import portfolio_bt as PB

P26 = {"2026": ("2026-01-01", "2026-11-05"), "H1": ("2026-01-01", "2026-06-30"), "H2": ("2026-07-01", "2026-11-05")}
OLD = {"2023-25": ("2023-01-02", "2025-12-31")}
CAPS = (200_000, 20_000)
for k, (lab, ov) in R98.NEW.items():
    if ov is not None:
        PB.EXITS[k] = {"label": lab, "ov": ov}


def frames(q, a, b, drop=None):
    def cut(key):
        if not key:
            return None
        X = pd.concat([O.trades(key, s) for s in q["indices"]], ignore_index=True)
        X = X[(X.date >= a) & (X.date <= b)]
        for d in drop or []:
            st, _, sy = d.partition("|")
            X = X[~((X.strategy == st) & ((X.sym == sy) if sy else True))]
        return X
    return cut(PB._trade_key(q)), cut(PB._alt_key(q)), cut(PB._hi_key(q))


def replay(q, a, b, drop=None):
    T, A, H = frames(q, a, b, drop)
    return PB.replay(T, q["capital"], q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"],
                     q["dd_half"] and q["sizing"] != "fixed", A, cpr=q.get("cpr_size", False) and q["sizing"] != "fixed", hi=H,
                     channel=q.get("channel", True))


def run(ch, cap, a, b):
    ch = dict(ch); drop = ch.pop("drop", None)
    q = {**O.B0, **ch, "capital": cap}
    s = PB._summary(replay(q, a, b, drop), cap)
    return {"final": round(float(s.get("final", cap))), "dd": float(s.get("max_dd_pct", 0)), "n": int(s.get("trades", 0)),
            "wr": s.get("win_rate")}


def breakdown():
    q = {**O.B0, "capital": 200_000}
    R = replay(q, *P26["2026"])
    R = R[R["skipped"].isna()]
    g = R.groupby(["strategy", "sym"]).agg(n=("pnl", "size"), pnl=("pnl", "sum"), win=("pnl", lambda x: float((x > 0).mean())))
    by_rule = R.groupby("strategy").pnl.agg(["size", "sum"]).round(0)
    by_month = R.assign(m=R.date.str[:7]).groupby("m").pnl.sum().round(0)
    T = pd.concat([O.trades(PB._trade_key(q), s) for s in q["indices"]], ignore_index=True)
    T["yr"] = T.date.str[:4]
    per_lot = T.groupby(["strategy", "yr"]).pnl.agg(["size", "mean"]).round(0)
    return {"by_rule_sym": {f"{a}|{b}": {"n": int(r.n), "pnl": round(float(r.pnl)), "win": round(r.win, 3)} for (a, b), r in g.iterrows()},
            "by_rule": {k: {"n": int(v["size"]), "pnl": float(v["sum"])} for k, v in by_rule.iterrows()},
            "by_month": by_month.to_dict(),
            "per_lot_by_year": {f"{a}|{b}": {"n": int(v["size"]), "avg": float(v["mean"])} for (a, b), v in per_lot.iterrows()}}


def versions():
    V = {"today": {}}
    for r in (0.75, 1.5, 2.0, 2.5, 3.0):
        V[f"risk {r:g}%"] = {"risk_pct": r}
    V["confidence: none"] = {"confidence": "none"}
    V["confidence: strong"] = {"confidence": "strong"}
    V["drawdown half-risk OFF"] = {"dd_half": False}
    V["big-run skip OFF"] = {"big_run_skip": False}
    V["equity gates OFF"] = {"gates": False}
    V["CPR sizing OFF"] = {"cpr_size": False}
    V["small-account 1 OTM OFF"] = {"small_otm": False}
    V["2 OTM from 5L OFF"] = {"big_otm": False}
    V["channel slot OFF"] = {"channel": False}
    V["1 OTM always"] = {"otm": 1}
    V["2 OTM always"] = {"otm": 2}
    for m in (20, 100, 0):
        V[f"max lots {m or 'none'}"] = {"max_lots": m}
    V["loss cap Rs 1500/lot"] = {"max_loss_lot": 1500}
    for k in ["tp_2r", "trail_25"] + [k for k in R98.NEW if k != "trail_25"]:
        V[f"exit {k}"] = {"exit": k}
    for ix in (["NIFTY"], ["BANKNIFTY"], ["SENSEX"], ["NIFTY", "SENSEX"], ["NIFTY", "BANKNIFTY"], ["BANKNIFTY", "SENSEX"]):
        V["only " + "+".join(i[:4] for i in ix)] = {"indices": ix}
    for st in ("Noise band", "Camarilla", "Fibonacci 0.618", "Trend-day", "Channel 15m"):
        V[f"drop {st}"] = {"drop": [st]}
    for d in ("Camarilla|BANKNIFTY", "Noise band|NIFTY", "Noise band|SENSEX", "Noise band|BANKNIFTY"):
        V[f"drop {d}"] = {"drop": [d]}
    return V


def better(row, base, p):
    return all(row[str(c)][p]["final"] > base[str(c)][p]["final"] and row[str(c)][p]["dd"] >= base[str(c)][p]["dd"] - 2.0 for c in CAPS)


def evaluate(V, res, periods):
    for name, ch in V.items():
        if name in res:
            continue
        res[name] = {"ch": ch}
        for cap in CAPS:
            res[name][str(cap)] = {p: run(ch, cap, a, b) for p, (a, b) in periods.items()}
        r2, r20 = res[name]["200000"], res[name]["20000"]
        print(f"{name:<34} Rs2L " + " ".join(f"{p}:{r2[p]['final'] / 1e5:6.2f}L({r2[p]['dd']:+.0f}%)" for p in periods) +
              " | Rs20k " + " ".join(f"{p}:{r20[p]['final'] / 1e5:5.2f}L({r20[p]['dd']:+.0f}%)" for p in periods), flush=True)
        json.dump(res, open(os.path.join(HERE, "round99_results.json"), "w"), indent=1, default=str)


def merge(chs):
    out = {}
    for c in chs:
        for k, v in c.items():
            if k == "drop":
                out["drop"] = sorted(set(out.get("drop", [])) | set(v))
            else:
                out[k] = v
    return out


def main():
    res = {"_breakdown": breakdown()}
    print(json.dumps(res["_breakdown"], indent=1, default=str), flush=True)
    V = versions()
    evaluate(V, res, P26)
    base = res["today"]
    h1_ok = [n for n in V if n != "today" and better(res[n], base, "H1")]
    both = [n for n in h1_ok if better(res[n], base, "H2")]
    print("PASS Jan-Jun:", h1_ok, "\nPASS Jan-Jun AND Jul-Nov:", both, flush=True)
    # combinations of what passed Jan-Jun (one setting per knob), chosen on H1, checked on H2
    keyed = {}
    for n in h1_ok:
        k = next(iter(V[n]))
        keyed.setdefault(k, []).append(n)
    picks = [max(v, key=lambda n: res[n]["200000"]["H1"]["final"]) for v in keyed.values()]
    C = {}
    for r in range(2, min(4, len(picks)) + 1):
        for combo in itertools.combinations(picks, r):
            C[" + ".join(combo)] = merge(V[n] for n in combo)
    evaluate(C, res, P26)
    allv = {**V, **C}
    h1c = [n for n in allv if n != "today" and better(res[n], base, "H1")]
    fin = [n for n in h1c if better(res[n], base, "H2")]
    res["_pass_h1"], res["_pass_both"] = h1c, fin
    print("ALL PASS Jan-Jun AND Jul-Nov:", fin, flush=True)
    # information only: the earlier years for today + everything that passed both 2026 halves
    for n in ["today"] + fin:
        res[n]["old"] = {str(c): run(allv[n], c, *OLD["2023-25"]) for c in CAPS}
        print(f"  2023-25 {n:<40} Rs2L {res[n]['old']['200000']['final'] / 1e5:7.2f}L ({res[n]['old']['200000']['dd']:+.1f}%) "
              f"Rs20k {res[n]['old']['20000']['final'] / 1e5:6.2f}L ({res[n]['old']['20000']['dd']:+.1f}%)", flush=True)
    json.dump(res, open(os.path.join(HERE, "round99_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
