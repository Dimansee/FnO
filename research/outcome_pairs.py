"""Outcomes, part 2 (26 Oct 2026): every PAIR of changes to today's Autopilot, run through the same portfolio backtest.

Factors (each has 1-7 alternatives to today's value). A pair changes two different factors at once; everything else stays
today's Autopilot (fno.portfolio_bt.BASELINE). Pairs that make no sense are left out (fixed lots ignore risk %, confidence
and the drawdown brake). Each pair is shown next to its two single changes, so you can see whether the two together beat
either one alone (do they add up, or cancel out?).
Capital Rs 2 lakh and Rs 20,000; full history 2 Jan 2023 - 23 Oct 2026 and 2026 alone (account restarted 1 Jan 2026).
Output: fno/outcomes_pairs.json (Outcomes tab, "Pairs" view) and pages appended to fno/outcomes.pdf.
Run: python research/outcome_pairs.py
"""
import os, sys, json, itertools, warnings
from multiprocessing import Pool
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
import outcomes as O
from fno import portfolio_bt as PB, config as C

OUT = os.path.join(ROOT, "fno", "outcomes_pairs.json")
_mem, _trades0 = {}, O.trades


def _trades_mem(k, s):
    if (k, s) not in _mem:
        _mem[(k, s)] = _trades0(k, s)
    return _mem[(k, s)]

# factor -> list of (label, changes)
F = {
    "Risk per trade": [("risk 0.5%", {"risk_pct": 0.5}), ("risk 1.5%", {"risk_pct": 1.5}), ("risk 2%", {"risk_pct": 2.0})],
    "Lot sizing": [("no compounding", {"sizing": "fixed_risk"}), ("fixed 1 lot", {"sizing": "fixed", "lots": 1}), ("fixed 3 lots", {"sizing": "fixed", "lots": 3})],
    "Confidence": [("no confidence sizing", {"confidence": "none"}), ("strong confidence", {"confidence": "strong"})],
    "Big-run skip": [("big-run skip off", {"big_run_skip": False})],
    "Drawdown brake": [("drawdown brake off", {"dd_half": False})],
    "Equity gates": [("gates off", {"gates": False})],
    "Strike": [("ATM", {"otm": 0}), ("1 OTM", {"otm": 1}), ("2 OTM", {"otm": 2})],
    "Exit": [(PB.EXITS[k]["label"], {"exit": k}) for k in ("lock_2r", "trail_25", "trail_2", "noise_t2", "noise_be", "half_2r", "t123")],
    "Loss cap": [("₹2,200/lot loss cap", {"max_loss_lot": 2200}), ("₹3,000/lot loss cap", {"max_loss_lot": 3000})],
    "Indices": [("no Bank Nifty", {"indices": ["NIFTY", "SENSEX"]}), ("no Sensex", {"indices": ["NIFTY", "BANKNIFTY"]}), ("Nifty only", {"indices": ["NIFTY"]})],
    "Lot cap": [("max 20 lots", {"max_lots": 20}), ("no lot cap", {"max_lots": 0})],
}
SHORT_EXIT = {"lock_2r": "lock +1R at +2R", "trail_25": "trail 1.5R after +2.5R", "trail_2": "trail 1R after +2R", "noise_t2": "Noise target 2R",
              "noise_be": "Noise BE at +1R", "half_2r": "half at 2R", "t123": "T1/T2/T3"}
F["Exit"] = [(SHORT_EXIT[c["exit"]], c) for _, c in F["Exit"]]
BAD = {frozenset(("Lot sizing", "Risk per trade")), frozenset(("Lot sizing", "Confidence")), frozenset(("Lot sizing", "Drawdown brake"))}


def cfg(ch):
    return {**O.B0, **ch}


def need_keys(chs):
    ks = set()
    for ch in chs:
        q = {**cfg(ch), "capital": 20000.0}
        for s in q["indices"]:
            ks.add((PB._trade_key(q), s))
            if PB._alt_key(q):
                ks.add((PB._alt_key(q), s))
    return sorted(ks, key=str)


def _gen(item):
    k, s = item
    O.trades(k, s)
    return item


def metrics(ch):
    out = {}
    for cap in O.CAPS:
        for pk, (a, b) in O.PERIODS.items():
            r = O.run_cfg({**cfg(ch), "capital": float(cap)}, a, b)
            out[f"{cap}_{pk}"] = {k: r.get(k) for k in ("final", "net", "max_dd_pct", "win_rate", "profit_factor", "trades", "losing_months", "months")}
    return out


def main():
    singles = [(f, lab, ch) for f, opts in F.items() for lab, ch in opts]
    pairs = []
    for (f1, l1, c1), (f2, l2, c2) in itertools.combinations(singles, 2):
        if f1 == f2 or frozenset((f1, f2)) in BAD:
            continue
        pairs.append((f1, l1, c1, f2, l2, c2))
    print(len(singles), "single changes,", len(pairs), "pairs", flush=True)
    keys = need_keys([{}] + [c for _, _, c in singles] + [{**a, **b} for _, _, a, _, _, b in pairs])
    todo = [k for k in keys if not os.path.exists(os.path.join(O.CACHE, f"{k[1]}_{k[0][0]}_{k[0][1]}_{int(k[0][2])}_{k[0][3]}.pkl"))]
    print(len(keys), "trade sets needed,", len(todo), "to simulate", flush=True)
    with Pool(2) as pool:
        for i, _ in enumerate(pool.imap_unordered(_gen, todo), 1):
            if i % 10 == 0:
                print(f"  simulated {i}/{len(todo)}", flush=True)
    O.trades = _trades_mem
    base = metrics({})
    S = {}
    for f, lab, ch in singles:
        S[lab] = metrics(ch)
    rows = []
    for n, (f1, l1, c1, f2, l2, c2) in enumerate(pairs, 1):
        m = metrics({**c1, **c2})
        rows.append({"f1": f1, "a": l1, "f2": f2, "b": l2, "ch": {**c1, **c2}, "runs": m})
        if n % 25 == 0:
            r = m["200000_full"]
            print(f"[{n}/{len(pairs)}] {l1} + {l2}: 2L {r['final']:,} dd {r['max_dd_pct']}", flush=True)
    out = {"generated": C.today_ist().isoformat(), "periods": O.PERIODS, "capitals": O.CAPS, "factors": {f: [l for l, _ in o] for f, o in F.items()},
           "baseline": base, "singles": [{"factor": f, "name": lab, "ch": ch, "runs": S[lab]} for f, lab, ch in singles], "pairs": rows,
           "notes": ["Each pair changes two settings at once; everything else is today's Autopilot.",
                     "'Adds up?' compares the pair with the better of its two single changes: 'yes' = the pair earns more than either alone, "
                     "'no' = one of the singles alone was better.",
                     "Compounded totals follow one historical path - neighbouring settings can differ a lot by luck. Check 2026 and the worst fall too."]}
    json.dump(out, open(OUT, "w"), indent=0, default=str)
    print("wrote", OUT, flush=True)
    return out


if __name__ == "__main__":
    main()
    import outcomes_pdf
    import gzip
    gf = os.path.join(ROOT, "fno", "outcomes_grid.json.gz")
    outcomes_pdf.build(json.load(open(O.OUT_JSON)), O.OUT_PDF, json.load(open(OUT)), json.load(gzip.open(gf, "rt")) if os.path.exists(gf) else None)
    print("wrote", O.OUT_PDF)
