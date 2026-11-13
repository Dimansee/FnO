"""Part E: today's own rules on the indices where they are NOT traded (rule x index blocks left out by rounds 27 / 29 / 90):
Camarilla on Sensex, trend-day and Fibonacci on Bank Nifty, the 15-min channel on Bank Nifty. Re-checked on today's engine
(profit trail, 2 OTM tier, CPR sizing). Each alone and all together, both halves, Rs 2L and Rs 20k."""
import sys, json, pickle, os, itertools; sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from lib import *
F = OUT + "/blocks.pkl"
if os.path.exists(F):
    NEW = pickle.load(open(F, "rb"))
else:
    T0, A0, H0 = base_sets()
    vix = O.H.load("INDIAVIX")["close"]
    old_sym, old_ch = dict(C.AUTOPILOT_SYM_STRATEGIES), C.CHANNEL_SYMS
    C.AUTOPILOT_SYM_STRATEGIES = {"BANKNIFTY": ["noise", "camarilla", "trend", "fib"], "SENSEX": ["noise", "camarilla", "trend", "fib"]}
    C.CHANNEL_SYMS = ("NIFTY", "SENSEX", "BANKNIFTY")
    NEW = {}
    try:
        for name, key, ref in (("T", PB._trade_key(B0), T0), ("A", PB._alt_key(B0), A0), ("H", PB._hi_key(B0), H0)):
            parts = []
            for sym in ("BANKNIFTY", "SENSEX"):
                ex, otm, brs, mll = key
                X = PB._trades(sym, O.candles(sym), vix, otm, PB.EXITS[ex]["ov"], brs, mll)
                X["score"] = PB._scores(X, O.candles(sym))
                have = set(ref[ref.sym == sym].strategy.unique())
                same = X[X.strategy.isin(have)]
                print(name, sym, "all", len(X), "| existing slots", len(same), "vs baseline", int((ref.sym == sym).sum()),
                      "| pnl equal:", abs(float(((same.prem_out - same.prem_in)).sum()) - float(((ref[ref.sym == sym].prem_out - ref[ref.sym == sym].prem_in)).sum())) < 1e-6, flush=True)
                parts.append(X[~X.strategy.isin(have)])
            NEW[name] = pd.concat(parts, ignore_index=True)
    finally:
        C.AUTOPILOT_SYM_STRATEGIES, C.CHANNEL_SYMS = old_sym, old_ch
    pickle.dump(NEW, open(F, "wb"))
N = NEW["T"].copy(); N["rs"] = (N.prem_out - N.prem_in) * N.lot
print(f"\n{'new block':<28}{'n':>5}{'win%':>6}{'avgR':>7}{'2023':>10}{'2024':>10}{'2025':>10}{'2026':>10}")
for (sym, st), g in N.groupby(["sym", "strategy"]):
    yr = g.groupby(g.date.str[:4]).rs.sum()
    print(f"{st + ' ' + sym:<28}{len(g):>5}{(g.rs > 0).mean() * 100:>6.0f}{g.R.mean():>7.2f}" + "".join(f"{yr.get(y, 0):>10,.0f}" for y in ("2023", "2024", "2025", "2026")))
blocks = sorted({(r.sym, r.strategy) for r in N.itertuples()})
ref = {(cap, p): replay(cap, a, b) for cap in (200_000, 20_000) for p, (a, b) in PER.items()}
T0, A0, H0 = base_sets()
def run(sel):
    pick = lambda X: X[[(s, st) in sel for s, st in zip(X.sym, X.strategy)]]
    T = pd.concat([T0, pick(NEW["T"])], ignore_index=True); A = pd.concat([A0, pick(NEW["A"])], ignore_index=True); H = pd.concat([H0, pick(NEW["H"])], ignore_index=True)
    return {(cap, p): replay(cap, a, b, T=T, A=A, H=H) for cap in (200_000, 20_000) for p, (a, b) in PER.items()}
out = {"today": {f"{c}|{p}": v for (c, p), v in ref.items()}}
print(f"\n{'added':<52}" + "".join(f"{'2L ' + p:>12}" for p in PER) + "".join(f"{'20k ' + p:>12}" for p in PER) + "   fall 2L")
print(f"{'(today)':<52}" + "".join(f"{ref[(c, p)]['final'] / 1e5:>12.2f}" for c in (200_000, 20_000) for p in PER) + f"   {ref[(200_000, 'full')]['dd']:+.1f}%")
for r in range(1, len(blocks) + 1):
    for sel in itertools.combinations(blocks, r):
        row = run(set(sel)); ok = all(row[k]["final"] > ref[k]["final"] and row[k]["dd"] >= ref[k]["dd"] - 2 for k in ref)
        lab = " + ".join(f"{st} {s}" for s, st in sel)
        out[lab] = {f"{c}|{p}": v for (c, p), v in row.items()}; out[lab]["pass"] = ok
        if r == 1 or r == len(blocks) or ok:
            print(f"{lab[:51]:<52}" + "".join(f"{row[(c, p)]['final'] / 1e5:>11.2f}{'*' if row[(c, p)]['final'] > ref[(c, p)]['final'] else ' '}" for c in (200_000, 20_000) for p in PER) +
                  f"   {row[(200_000, 'full')]['dd']:+.1f}%  {'PASS' if ok else ''}", flush=True)
print("combinations tried:", len(out) - 1, "| pass:", [k for k, v in out.items() if isinstance(v, dict) and v.get("pass")])
json.dump(out, open(OUT + "/e_blocks.json", "w"), indent=1, default=str)
