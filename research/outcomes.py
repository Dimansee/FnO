"""Outcomes matrix (25 Oct 2026): every single change to today's Autopilot, run through the same portfolio backtest.

Baseline = fno.portfolio_bt.BASELINE (today's live rules: compounding 1%, standard confidence, big-run skip, half risk in a
>15% drawdown, 1 ITM with 1 OTM below Rs 60k, current exits, no loss cap, gates, 50-lot cap) on Nifty + Bank Nifty + Sensex.
Each scenario changes ONE thing (plus a few named combinations). Run at Rs 2 lakh and Rs 20,000 starting capital, over the
full history (2 Jan 2023 - 23 Oct 2026) and 2026 alone (account restarted on 1 Jan 2026).
Output: fno/outcomes.json (read by the Outcomes tab) and fno/outcomes.pdf.
Run: python research/outcomes.py [--pdf-only]
"""
import os, sys, json, pickle, warnings, time as _time
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
import pandas as pd
import round25_harness as H
from fno import portfolio_bt as PB, config as C, market as D
D.lot_size = lambda s: {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}[s]   # current exchange lot sizes (no database offline)

CACHE = "/tmp/outcomes_trades"
os.makedirs(CACHE, exist_ok=True)
SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
CAPS = [200_000, 20_000]
PERIODS = {"full": ("2023-01-02", "2026-10-23"), "y2026": ("2026-01-01", "2026-10-23")}
OUT_JSON = os.path.join(ROOT, "fno", "outcomes.json")
OUT_PDF = os.path.join(ROOT, "fno", "outcomes.pdf")

B0 = dict(PB.BASELINE, indices=SYMS)


def sc(group, name, **ch):
    return {"group": group, "name": name, "ch": ch}


SCEN = [sc("Baseline", "Current Autopilot rules")]
for k, v in PB.EXITS.items():
    if k != "current":
        SCEN.append(sc("Exits & targets", v["label"], exit=k))
SCEN += [sc("Risk per trade", f"Risk {r:g}% a trade", risk_pct=r) for r in (0.5, 0.75, 1.5, 2.0, 3.0)]
SCEN += [sc("Lot sizing", "No compounding (1% of the starting capital)", sizing="fixed_risk"),
         sc("Lot sizing", "Fixed 1 lot", sizing="fixed", lots=1),
         sc("Lot sizing", "Fixed 3 lots", sizing="fixed", lots=3),
         sc("Lot sizing", "Fixed 10 lots", sizing="fixed", lots=10)]
SCEN += [sc("Confidence sizing", "No confidence sizing (all 1x)", confidence="none"),
         sc("Confidence sizing", "Strong confidence (2x / 1x / 0.5x, all indices)", confidence="strong")]
SCEN += [sc("Filters & brakes", "Big-run skip OFF", big_run_skip=False),
         sc("Filters & brakes", "Half risk in a drawdown OFF", dd_half=False),
         sc("Filters & brakes", "Equity gates OFF (trend & Fib from day one)", gates=False),
         sc("Filters & brakes", "Small-account 1 OTM OFF (always 1 ITM)", small_otm=False)]
SCEN += [sc("Strike", "ATM strike", otm=0), sc("Strike", "1 OTM always", otm=1), sc("Strike", "2 OTM always", otm=2)]
SCEN += [sc("Lot cap", f"Max {m} lots" if m else "No lot cap", max_lots=m) for m in (0, 20, 100, 200)]
SCEN += [sc("Loss cap per lot", f"Close at ₹{m:,} loss per lot", max_loss_lot=m) for m in (1500, 2200, 3000)]
SCEN += [sc("Indices", "Nifty only", indices=["NIFTY"]), sc("Indices", "Nifty + Sensex (no Bank Nifty)", indices=["NIFTY", "SENSEX"]),
         sc("Indices", "Nifty + Bank Nifty (no Sensex)", indices=["NIFTY", "BANKNIFTY"]), sc("Indices", "Bank Nifty only", indices=["BANKNIFTY"]),
         sc("Indices", "Sensex only", indices=["SENSEX"])]
SCEN += [sc("Combinations", "Strong confidence + risk 1.5%", confidence="strong", risk_pct=1.5),
         sc("Combinations", "Strong confidence, no drawdown brake", confidence="strong", dd_half=False),
         sc("Combinations", "Risk 2% + no lot cap", risk_pct=2.0, max_lots=0),
         sc("Combinations", "All filters off (no big-run skip, no brake, no gates)", big_run_skip=False, dd_half=False, gates=False),
         sc("Combinations", "Today's rules before round 42-43 (no big-run skip, no brake)", big_run_skip=False, dd_half=False),
         sc("Combinations", "Strong confidence + lock +1R at +2R", confidence="strong", exit="lock_2r")]

_cand, _vix = {}, None


def candles(sym):
    if sym not in _cand:
        _cand[sym] = H.load(sym)
    return _cand[sym]


def trades(key, sym):
    ex, otm, brs, mll = key
    f = os.path.join(CACHE, f"{sym}_{ex}_{otm}_{int(brs)}_{mll}.pkl")
    if os.path.exists(f):
        return pickle.load(open(f, "rb"))
    global _vix
    if _vix is None:
        _vix = H.load("INDIAVIX")["close"]
    c = candles(sym)
    t0 = _time.time()
    T = PB._trades(sym, c, _vix, otm, PB.EXITS[ex]["ov"], brs, mll)
    if not T.empty:
        T["score"] = PB._scores(T, c)
    pickle.dump(T, open(f, "wb"))
    print(f"  trades {sym} {key}: {len(T)} in {_time.time() - t0:.0f}s", flush=True)
    return T


def run_cfg(q, start, end):
    k = PB._trade_key(q); ak = PB._alt_key(q)
    T = pd.concat([trades(k, s) for s in q["indices"]], ignore_index=True)
    T = T[(T.date >= start) & (T.date <= end)]
    A = None
    if ak:
        A = pd.concat([trades(ak, s) for s in q["indices"]], ignore_index=True)
        A = A[(A.date >= start) & (A.date <= end)]
    R = PB.replay(T, q["capital"], q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"],
                  q["dd_half"] and q["sizing"] != "fixed", A)
    s = PB._summary(R, q["capital"])
    if not s.get("trades"):
        return {"trades": 0, "final": q["capital"], "net": 0, "multiple": 1, "max_dd_pct": 0, "win_rate": 0, "profit_factor": None,
                "cagr_pct": 0, "losing_months": 0, "months": 0, "worst_month": 0, "max_lots": 0, "skipped": s.get("skipped", {})}
    keep = ("trades", "final", "net", "multiple", "cagr_pct", "max_dd_pct", "max_dd_rs", "win_rate", "profit_factor", "avg_R",
            "worst_trade", "best_trade", "max_lots", "avg_lots", "losing_months", "months", "worst_month", "best_month", "skipped")
    out = {k_: s[k_] for k_ in keep if k_ in s}
    out["years"] = {y["year"]: y["ret_pct"] for y in s["years"]}
    out["by_index"] = {g["key"]: g["net"] for g in s["by_index"]}
    return out


def verdict(row, base):
    """Plain verdict against the baseline at the same capital and period: profit first, the worst fall second."""
    if row is base:
        return "baseline"
    dn = row["net"] - base["net"]
    dd = row["max_dd_pct"] - base["max_dd_pct"]          # less negative = shallower fall
    rel = dn / max(abs(base["net"]), 1)
    if rel > 0.02 and dd >= -2:
        return "better"
    if rel > 0.02:
        return "more profit, deeper fall"
    if rel < -0.02 and dd > 2:
        return "less profit, shallower fall"
    if rel < -0.02:
        return "worse"
    return "about the same"


def main():
    res = []
    for i, s_ in enumerate(SCEN):
        row = {"group": s_["group"], "name": s_["name"], "changes": PB.differences({**B0, **s_["ch"]}) or [], "runs": {}}
        if s_["ch"].get("indices"):
            row["changes"] = row["changes"] + ["indices: " + ", ".join(s_["ch"]["indices"])]
        for cap in CAPS:
            for pk, (a, b) in PERIODS.items():
                q = {**B0, **s_["ch"], "capital": float(cap)}
                row["runs"][f"{cap}_{pk}"] = run_cfg(q, a, b)
        r = row["runs"]
        print(f"[{i + 1}/{len(SCEN)}] {s_['name'][:55]:<55} 2L full {r['200000_full']['final']:>11,} dd {r['200000_full']['max_dd_pct']:>6} | "
              f"20k full {r['20000_full']['final']:>9,} dd {r['20000_full']['max_dd_pct']:>6}", flush=True)
        res.append(row)
    base = res[0]
    for row in res:
        row["verdict"] = {k: verdict(row["runs"][k] if row is not base else base["runs"][k], base["runs"][k]) if row is not base else "baseline"
                          for k in base["runs"]}
    out = {"generated": C.today_ist().isoformat(), "baseline": {k: v for k, v in B0.items()}, "periods": PERIODS, "capitals": CAPS,
           "notes": ["Same engine as the Backtest tab's portfolio backtest (option prices modelled from the index, charges, slippage that grows "
                     "with order size, freeze-size order splits, weekly loss limit, cash check).",
                     "Each row changes only what its 'Changes' column says; everything else is today's Autopilot.",
                     "'2026 only' restarts the account on 1 Jan 2026 - an unseen year for most rules (they were picked on 2023-25).",
                     "Compounded totals follow ONE historical path: a few big wins or losses early change the size of every later trade, so neighbouring settings (e.g. risk 1.5% vs 2%) can differ by a lot. Read the worst fall and the 2026 column alongside the final value; the Rs 20,000 rows are the most path-sensitive.",
                     "The AI judge (Nifty sizing) is not modelled; nor are real bid-ask spreads beyond the slippage model.",
                     "A result that looks better on one window is not proof: most ideas here were already tested and rejected in research rounds 40-44."],
           "rows": res}
    json.dump(out, open(OUT_JSON, "w"), indent=0, default=str)
    print("wrote", OUT_JSON)
    return out


if __name__ == "__main__":
    if "--pdf-only" in sys.argv:
        out = json.load(open(OUT_JSON))
    else:
        out = main()
    import outcomes_pdf
    pf = os.path.join(ROOT, "fno", "outcomes_pairs.json")
    outcomes_pdf.build(out, OUT_PDF, json.load(open(pf)) if os.path.exists(pf) else None)
    print("wrote", OUT_PDF)
