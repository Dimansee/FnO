"""Outcomes, part 3 (26 Oct 2026): EVERY combination of the main settings, run through the portfolio backtest.

Factors (current Autopilot value first):
  money      compound 1% (current) / 0.5% / 1.5% / 2% / no compounding 1% / fixed 1 lot / fixed 3 lots
  confidence standard (current) / none / strong            (not used with fixed lots)
  brake      half risk in a >15% drawdown on (current) / off (not used with fixed lots)
  strike     1 ITM (current) / ATM / 1 OTM / 2 OTM
  exit       current / lock +1R at +2R / trail 1.5R after +2.5R / trail 1R after +2R / Noise target 2R / Noise BE +1R / half at 2R
  bigrun     big-run skip on (current) / off
  losscap    none (current) / Rs 2,200 per lot / Rs 3,000 per lot
  gates      on (current) / off
  indices    all three (current) / Nifty + Sensex / Nifty only
32 x 4 x 7 x 2 x 3 x 2 x 3 = 32,256 combinations, each at Rs 2 lakh and Rs 20,000, full history and 2026 alone.
Output: fno/outcomes_grid.json.gz (Outcomes tab -> "Every combination"), top lists appended to fno/outcomes.pdf.
Run: python research/outcome_grid.py
"""
import os, sys, json, gzip, itertools, warnings, time
from multiprocessing import Pool
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
import outcomes as O
from fno import portfolio_bt as PB, config as C

OUT = os.path.join(ROOT, "fno", "outcomes_grid.json.gz")
EX_SHORT = {"current": "current exits", "lock_2r": "lock +1R at +2R", "trail_25": "trail 1.5R after +2.5R", "trail_2": "trail 1R after +2R",
            "noise_t2": "Noise target 2R", "noise_be": "Noise stop to entry at +1R", "half_2r": "half at 2R, rest runs"}
F = [
    ("Lots / risk", [("compound 1%", {}), ("compound 0.5%", {"risk_pct": 0.5}), ("compound 1.5%", {"risk_pct": 1.5}), ("compound 2%", {"risk_pct": 2.0}),
                     ("no compounding 1%", {"sizing": "fixed_risk"}), ("fixed 1 lot", {"sizing": "fixed", "lots": 1}), ("fixed 3 lots", {"sizing": "fixed", "lots": 3})]),
    ("Confidence", [("standard", {}), ("none", {"confidence": "none"}), ("strong", {"confidence": "strong"})]),
    ("Drawdown brake", [("on", {}), ("off", {"dd_half": False})]),
    ("Strike", [("1 ITM", {}), ("ATM", {"otm": 0}), ("1 OTM", {"otm": 1}), ("2 OTM", {"otm": 2})]),
    ("Exit", [(EX_SHORT[k], {} if k == "current" else {"exit": k}) for k in EX_SHORT]),
    ("Big-run skip", [("on", {}), ("off", {"big_run_skip": False})]),
    ("Loss cap / lot", [("none", {}), ("₹2,200", {"max_loss_lot": 2200}), ("₹3,000", {"max_loss_lot": 3000})]),
    ("Equity gates", [("on", {}), ("off", {"gates": False})]),
    ("Indices", [("all three", {}), ("Nifty + Sensex", {"indices": ["NIFTY", "SENSEX"]}), ("Nifty only", {"indices": ["NIFTY"]})]),
]
RUNS = [(cap, pk) for cap in O.CAPS for pk in O.PERIODS]
_mem = {}


def _trades_mem(k, s):
    if (k, s) not in _mem:
        _mem[(k, s)] = O._trades_disk(k, s)
    return _mem[(k, s)]


O._trades_disk = O.trades


def combos():
    for idx in itertools.product(*[range(len(o)) for _, o in F]):
        if F[0][1][idx[0]][1].get("sizing") == "fixed" and (idx[1] or idx[2]):
            continue                                  # fixed lots ignore confidence and the drawdown brake
        yield idx


def cfg(idx):
    ch = {}
    for (name, opts), i in zip(F, idx):
        ch.update(opts[i][1])
    return {**O.B0, **ch}


def _gen(item):
    O.trades(*item)
    return item


def _run(chunk):
    O.trades = _trades_mem
    out = []
    for idx in chunk:
        q = cfg(idx)
        row = list(idx)
        for cap, pk in RUNS:
            a, b = O.PERIODS[pk]
            try:
                r = O.run_cfg({**q, "capital": float(cap)}, a, b)
                row += [int(r["final"]), r["max_dd_pct"], r["win_rate"], r["profit_factor"], r["trades"]]
            except Exception:
                row += [cap, 0, 0, None, 0]
        out.append(row)
    return out


def main():
    allc = list(combos())
    print(len(allc), "combinations", flush=True)
    keys = set()
    for idx in allc:
        q = {**cfg(idx), "capital": 20000.0}
        for s in q["indices"]:
            keys.add((PB._trade_key(q), s))
            if PB._alt_key(q):
                keys.add((PB._alt_key(q), s))
    todo = [k for k in sorted(keys, key=str) if not os.path.exists(os.path.join(O.CACHE, f"{k[1]}_{k[0][0]}_{k[0][1]}_{int(k[0][2])}_{k[0][3]}.pkl"))]
    print(len(keys), "trade sets,", len(todo), "to simulate", flush=True)
    with Pool(2) as pool:
        for i, _ in enumerate(pool.imap_unordered(_gen, todo), 1):
            if i % 20 == 0:
                print(f"  simulated {i}/{len(todo)}", flush=True)
    # group combos by trade keys so each worker reuses loaded trade sets
    allc.sort(key=lambda idx: (idx[3], idx[4], idx[5], idx[6], idx[8]))
    chunks = [allc[i:i + 400] for i in range(0, len(allc), 400)]
    rows, t0 = [], time.time()
    with Pool(2) as pool:
        for i, part in enumerate(pool.imap(_run, chunks), 1):
            rows += part
            if i % 5 == 0:
                print(f"  replayed {len(rows)}/{len(allc)} in {time.time() - t0:.0f}s", flush=True)
    base = next(r for r in rows if not any(r[:len(F)]))
    out = {"generated": C.today_ist().isoformat(), "periods": O.PERIODS, "runs": [f"{c}_{p}" for c, p in RUNS],
           "metrics": ["final", "max_dd_pct", "win_rate", "profit_factor", "trades"],
           "factors": [{"name": n, "options": [l for l, _ in o], "changes": [c for _, c in o]} for n, o in F],
           "baseline": base, "rows": rows,
           "notes": ["Every combination of the settings above; everything else is today's Autopilot (50-lot cap, weekly loss limit, 1 OTM below Rs 60k with the 1 ITM strike).",
                     "Fixed-lot rows ignore confidence sizing and the drawdown brake (they would be identical).",
                     "Each total follows one historical path - the best of 32,000 combinations is partly luck. Prefer combinations that are good in BOTH periods and at both capitals, with a shallow worst fall."]}
    with gzip.open(OUT, "wt") as f:
        json.dump(out, f, separators=(",", ":"), default=str)
    print("wrote", OUT, os.path.getsize(OUT), flush=True)
    return out


if __name__ == "__main__":
    main()
    import outcomes_pdf
    pf = os.path.join(ROOT, "fno", "outcomes_pairs.json")
    with gzip.open(OUT, "rt") as f:
        grid = json.load(f)
    outcomes_pdf.build(json.load(open(O.OUT_JSON)), O.OUT_PDF, json.load(open(pf)) if os.path.exists(pf) else None, grid)
    print("wrote", O.OUT_PDF)
