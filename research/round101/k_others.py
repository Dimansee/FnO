"""Part K: the three families with versions that made money alone in BOTH periods (average-price band 2.0 trend, Ichimoku
cross + cloud, TTM squeeze; 15m) - the same risk-matched test as part J. Is it a real improvement or only more risk? Risk-matched test: add the piece and LOWER the risk per trade for the whole account until the worst fall is back inside the
2-point rule. Everything is chosen on 2023-24 only (piece, size, risk %), then judged on 2025-26, the full period and Rs 20k."""
import sys, json, time, pickle, os; sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from lib import *
sys.argv = [sys.argv[0], "15m"]
import b_families as bf
bf.TF, bf.STEP = "15m", 15
CELLS = [(cap, p) for cap in (200_000, 20_000) for p in PER]
RISK = (0.6, 0.7, 0.8, 0.9, 1.0)
PK = OUT + "/other_pieces.pkl"
if os.path.exists(PK):
    pieces, own = pickle.load(open(PK, "rb"))
else:
    pieces, own = {}, {}
    for sym in SYMS:
        df, o, h, l, c, hm, day, a14, F = bf.families(sym)
        for fam in ("avg-price band 2.0 trend", "ichimoku cross+cloud", "ttm squeeze release"):
            L, S, xL, xS, ex = F[fam]
            L = np.nan_to_num(np.asarray(L, float)) > 0; S = np.nan_to_num(np.asarray(S, float)) > 0
            for en, e in bf.EXITS.items():
                for pdy in (1, 3):
                    rows = simulate(o, h, l, c, hm, day, L, S, e["k"] * a14, tp_R=e["tp_R"], xL=xL if e["sig"] else None, xS=xS if e["sig"] else None, per_day=pdy, step=15, trail_R=e.get("trail_R", 0))
                    T = price(sym, df, rows, 15); key = f"{sym} {fam} [{en.split(',')[0]}] {pdy}/d"
                    if len(T) < 100:
                        continue
                    pieces[key] = to_piece(sym, T, "r101k " + key)
                    own[key] = {"design": float(T[T.date < "2025"].rs.sum()), "unseen": float(T[T.date >= "2025"].rs.sum()), "n": len(T),
                                "years": T.groupby(T.date.str[:4]).rs.sum().round(0).to_dict()}
    pickle.dump((pieces, own), open(PK, "wb"))
print("pieces", len(pieces), flush=True)


def ext(keys, mult):
    return [pieces[k].assign(risk_lot=pieces[k].risk_lot * mult, risk_lot_capped=pieces[k].risk_lot_capped * mult) for k in keys]


ref = {x: replay(x[0], *PER[x[1]]) for x in CELLS}
res = {"today": {f"{c}|{p}": v for (c, p), v in ref.items()}, "own": own}
print("today frontier (risk % -> final L / fall):")
res["today_frontier"] = {}
for r in (0.6, 0.8, 1.0, 1.2, 1.4):
    row = {x: replay(x[0], *PER[x[1]], risk_pct=r) for x in CELLS}
    res["today_frontier"][str(r)] = {f"{c}|{p}": v for (c, p), v in row.items()}
    print(f"   risk {r:.1f}  " + "".join(f"{row[x]['final'] / 1e5:>8.2f}({row[x]['dd']:>4.0f})" for x in CELLS), flush=True)
# design-period search
a, b = PER["2023-24"]; base = ref[(200_000, "2023-24")]; floor = base["dd"] - 2.0
cand = []
t0 = time.time()
for k in pieces:
    for nm, m in (("full", 1), ("half", 2)):
        for r in RISK:
            v = replay(200_000, a, b, extra=ext([k], m), risk_pct=r)
            cand.append({"key": k, "size": nm, "mult": m, "risk": r, "final": v["final"], "dd": v["dd"]})
print("design search", len(cand), round(time.time() - t0), "s", flush=True)
ok = sorted([x for x in cand if x["dd"] >= floor and x["final"] > base["final"]], key=lambda x: -x["final"])
print(f"2023-24 Rs 2L: today {base['final'] / 1e5:.2f}L fall {base['dd']}%; versions better with fall inside 2 pts: {len(ok)} of {len(cand)}")
res["design_ok"] = len(ok); res["design_n"] = len(cand); res["top"] = []
seen = set(); top = []
for x in ok:
    if (x["key"], x["size"]) in seen:
        continue
    seen.add((x["key"], x["size"])); top.append(x)
    if len(top) == 12:
        break
print("\ntop by 2023-24 (chosen there), then the other five cells:")
for x in top:
    row = {c_: replay(c_[0], *PER[c_[1]], extra=ext([x["key"]], x["mult"]), risk_pct=x["risk"]) for c_ in CELLS}
    better = sum(row[c_]["final"] > ref[c_]["final"] for c_ in CELLS); fall_ok = all(row[c_]["dd"] >= ref[c_]["dd"] - 2 for c_ in CELLS)
    yrs = {y: replay(200_000, *YEARS[y], extra=ext([x["key"]], x["mult"]), risk_pct=x["risk"])["final"] for y in YEARS}
    x.update(cells={f"{c}|{p}": v for (c, p), v in row.items()}, better=better, fall_ok=fall_ok, years=yrs); res["top"].append(x)
    print(f"   {x['key']:<30} {x['size']:<4} risk {x['risk']:.1f} " + "".join(f"{row[c_]['final'] / 1e5:>8.2f}{'*' if row[c_]['final'] > ref[c_]['final'] else ' '}({row[c_]['dd']:>4.0f})" for c_ in CELLS)
          + f"  better {better}/6 fall_ok {fall_ok}" + ("  PASS" if better == 6 and fall_ok else ""), flush=True)
    json.dump(res, open(OUT + "/k_others.json", "w"), default=str)
print("years today:", {y: round(replay(200_000, *YEARS[y])["final"] / 1e5, 2) for y in YEARS})
for x in res["top"][:5]:
    print("   ", x["key"], x["size"], x["risk"], {y: round(v / 1e5, 2) for y, v in x["years"].items()}, "own", own[x["key"]]["years"])
# all versions, all risk levels: does ANY pass all six with the fall rule (this is hindsight, shown for completeness)
print("\nhindsight scan: every version x size x risk, all six cells")
allpass = []
for k in pieces:
    for nm, m in (("full", 1), ("half", 2)):
        for r in (0.7, 0.8, 0.9):
            row = {c_: replay(c_[0], *PER[c_[1]], extra=ext([k], m), risk_pct=r) for c_ in CELLS}
            better = sum(row[c_]["final"] > ref[c_]["final"] for c_ in CELLS); fall_ok = all(row[c_]["dd"] >= ref[c_]["dd"] - 2 for c_ in CELLS)
            allpass.append({"key": k, "size": nm, "risk": r, "better": better, "fall_ok": fall_ok, "cells": {f"{c}|{p}": v for (c, p), v in row.items()}})
            if better == 6 and fall_ok:
                print("   PASS", k, nm, r, "".join(f"{row[c_]['final'] / 1e5:>8.2f}({row[c_]['dd']:>4.0f})" for c_ in CELLS), flush=True)
    res["scan"] = allpass; json.dump(res, open(OUT + "/k_others.json", "w"), default=str)
print(f"hindsight scan: pass {sum(x['better'] == 6 and x['fall_ok'] for x in allpass)} of {len(allpass)}; better in all six (any fall) {sum(x['better'] == 6 for x in allpass)}; fall ok {sum(x['fall_ok'] for x in allpass)}")
print("done")
