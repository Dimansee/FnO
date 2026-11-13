"""Part A: review of today's slots. Per rule x index: trades, win %, avg R, Rs per lot by year; and what happens to the
portfolio when one slot is removed (leave-one-out) - both halves, Rs 2L and Rs 20k."""
import sys, json; sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from lib import *
T, A, H = base_sets()
T = T.copy(); T["rs"] = (T.prem_out - T.prem_in) * T.lot; T["yr"] = T.date.str[:4]
print(f"{'slot':<28}{'n':>5}{'win%':>6}{'avgR':>7}" + "".join(f"{y:>10}" for y in ("2023", "2024", "2025", "2026")) + f"{'2023-24':>10}{'2025-26':>10}")
res = {"slots": {}, "loo": {}}
for (sym, st), g in T.groupby(["sym", "strategy"]):
    yr = g.groupby("yr").rs.sum()
    a, b = g[g.date < "2025"].rs.sum(), g[g.date >= "2025"].rs.sum()
    res["slots"][f"{st} {sym}"] = {"n": len(g), "win": float((g.rs > 0).mean()), "avgR": float(g.R.mean()), "years": yr.round(0).to_dict(), "a": float(a), "b": float(b),
                                    "avgR_a": float(g[g.date < "2025"].R.mean()), "avgR_b": float(g[g.date >= "2025"].R.mean())}
    print(f"{st + ' ' + sym:<28}{len(g):>5}{(g.rs > 0).mean() * 100:>6.0f}{g.R.mean():>7.2f}" + "".join(f"{yr.get(y, 0):>10,.0f}" for y in ("2023", "2024", "2025", "2026")) + f"{a:>10,.0f}{b:>10,.0f}")
ref = {(cap, p): replay(cap, a, b) for cap in (200_000, 20_000) for p, (a, b) in PER.items()}
print("\nleave one slot out (final Rs lakh; * = better than today)")
print(f"{'removed':<28}" + "".join(f"{'2L ' + p:>12}" for p in PER) + "".join(f"{'20k ' + p:>12}" for p in PER) + "   worst fall 2L")
print(f"{'(today)':<28}" + "".join(f"{ref[(c, p)]['final'] / 1e5:>12.2f}" for c in (200_000, 20_000) for p in PER) + f"   {ref[(200_000, 'full')]['dd']:+.1f}%")
for (sym, st), g in T.groupby(["sym", "strategy"]):
    keep = lambda X: None if X is None else X[~((X.sym == sym) & (X.strategy == st))]
    row = {}
    for cap in (200_000, 20_000):
        for p, (a, b) in PER.items():
            row[(cap, p)] = replay(cap, a, b, T=keep(T), A=keep(A), H=keep(H))
    ok = all(row[k]["final"] > ref[k]["final"] and row[k]["dd"] >= ref[k]["dd"] - 2 for k in ref)
    res["loo"][f"{st} {sym}"] = {f"{c}|{p}": row[(c, p)] for c, p in row}; res["loo"][f"{st} {sym}"]["pass"] = ok
    print(f"{st + ' ' + sym:<28}" + "".join(f"{row[(c, p)]['final'] / 1e5:>11.2f}{'*' if row[(c, p)]['final'] > ref[(c, p)]['final'] else ' '}" for c in (200_000, 20_000) for p in PER) +
          f"   {row[(200_000, 'full')]['dd']:+.1f}%  {'PASS' if ok else ''}")
res["today"] = {f"{c}|{p}": v for (c, p), v in ref.items()}
json.dump(res, open(OUT + "/a_review.json", "w"), indent=1, default=str)
