"""Part I: the one piece from part G that raised the final money in all six cells (Keltner 2.0 breakout 15m, 3R target, Sensex)
but failed the worst-fall rule. Deep check: (1) smaller sizes, (2) neighbours (band width, target, stop, trades a day, index),
(3) null control - pieces with RANDOM entries (same count, same hours, same exit) added the same way."""
import sys, json, time; sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from lib import *
sys.argv = [sys.argv[0], "15m"]
import b_families as bf
bf.TF, bf.STEP = "15m", 15
CELLS = [(cap, p) for cap in (200_000, 20_000) for p in PER]
ref = {x: replay(x[0], *PER[x[1]]) for x in CELLS}
res = {"today": {f"{c}|{p}": v for (c, p), v in ref.items()}}
D = {s: bf.families(s) for s in SYMS}


def piece(sym, L, S, k_stop, tp, pdy, label):
    df, o, h, l, c, hm, day, a14, F = D[sym]
    rows = simulate(o, h, l, c, hm, day, L, S, k_stop * a14, tp_R=tp, per_day=pdy, step=15)
    T = price(sym, df, rows, 15)
    return to_piece(sym, T, label), (float(T[T.date < "2025"].rs.sum()), float(T[T.date >= "2025"].rs.sum()), len(T))


def kelt(sym, k):
    df, o, h, l, c, hm, day, a14, F = D[sym]
    e20 = ema(c, 20); a10 = atr(h, l, c, 10); up, dn = e20 + k * a10, e20 - k * a10
    pc = np.roll(c, 1); su, sd = np.roll(up, 1), np.roll(dn, 1)
    return np.nan_to_num((c > up) & (pc <= su)) > 0, np.nan_to_num((c < dn) & (pc >= sd)) > 0


def judge(P, mult=1):
    X = [P.assign(risk_lot=P.risk_lot * mult, risk_lot_capped=P.risk_lot_capped * mult)]
    row = {x: replay(x[0], *PER[x[1]], extra=X) for x in CELLS}
    better = sum(row[x]["final"] > ref[x]["final"] for x in CELLS)
    fall_ok = all(row[x]["dd"] >= ref[x]["dd"] - 2 for x in CELLS)
    return row, better, fall_ok


def show(tag, row, better, fall_ok, own=None):
    s = "".join(f"{row[x]['final'] / 1e5:>8.2f}{'*' if row[x]['final'] > ref[x]['final'] else ' '}({row[x]['dd']:>4.0f})" for x in CELLS)
    print(f"   {tag:<46}{s}  better {better}/6 fall_ok {fall_ok}" + (f"  own {own[0]:+,.0f}/{own[1]:+,.0f} n={own[2]}" if own else "") + ("  PASS" if better == 6 and fall_ok else ""), flush=True)


print("today" + " " * 44 + "".join(f"{ref[x]['final'] / 1e5:>8.2f} ({ref[x]['dd']:>4.0f})" for x in CELLS))
# 1 sizes
print("\n1. Keltner 2.0 / 3R / stop 2 ATR / 3 a day, Sensex - by size")
L, S = kelt("SENSEX", 2.0); P0, own0 = piece("SENSEX", L, S, 2.0, 3.0, 3, "kelt")
res["sizes"] = {}
for nm, m in (("full", 1), ("half", 2), ("third", 3), ("quarter", 4)):
    row, b, f = judge(P0, m); show(nm, row, b, f, own0); res["sizes"][nm] = {"better": b, "fall_ok": f, "cells": {f"{c}|{p}": v for (c, p), v in row.items()}}
# 2 neighbours
print("\n2. neighbours (full and half size)")
res["neigh"] = []
for sym in ("SENSEX", "NIFTY", "BANKNIFTY"):
    for k in (1.5, 2.0, 2.5):
        L, S = kelt(sym, k)
        for ks, tp in ((1.5, 2.0), (2.0, 2.0), (2.0, 3.0), (2.0, 4.0), (1.5, 3.0)):
            for pdy in (1, 3):
                P, own = piece(sym, L, S, ks, tp, pdy, "kelt")
                for nm, m in (("full", 1), ("half", 2)):
                    row, b, f = judge(P, m)
                    tag = f"{sym[:6]} k{k} stop{ks} tp{tp:.0f}R {pdy}/d {nm}"
                    show(tag, row, b, f, own)
                    res["neigh"].append({"tag": tag, "sym": sym, "k": k, "stop": ks, "tp": tp, "pdy": pdy, "size": nm, "better": b, "fall_ok": f, "own": own,
                                         "cells": {f"{c}|{p}": v for (c, p), v in row.items()}})
    json.dump(res, open(OUT + "/i_keltner.json", "w"), default=str)
# 3 null control: random entries, same number of signals, same exit, Sensex
print("\n3. random-entry control (Sensex, same number of signals, 3R target, stop 2 ATR, 3 a day)")
df, o, h, l, c, hm, day, a14, F = D["SENSEX"]
L0, S0 = kelt("SENSEX", 2.0); ok = (hm >= 9 * 60 + 20) & (hm <= 14 * 60 + 45); n_sig = int((L0 & ok).sum() + (S0 & ok).sum()); idx = np.where(ok)[0]
rng = np.random.default_rng(101); res["null"] = []
NR = int(sys.argv[2]) if len(sys.argv) > 2 else 40
for r in range(NR):
    pick = rng.choice(idx, n_sig, replace=False); side = rng.random(n_sig) < 0.5
    L = np.zeros(len(c), bool); S = np.zeros(len(c), bool); L[pick[side]] = True; S[pick[~side]] = True
    P, own = piece("SENSEX", L, S, 2.0, 3.0, 3, "rand")
    for nm, m in (("full", 1), ("half", 2)):
        row, b, f = judge(P, m); show(f"random {r + 1} {nm}", row, b, f, own)
        res["null"].append({"r": r, "size": nm, "better": b, "fall_ok": f, "own": own, "cells": {f"{c_}|{p}": v for (c_, p), v in row.items()}})
    json.dump(res, open(OUT + "/i_keltner.json", "w"), default=str)
nf = [x for x in res["null"] if x["size"] == "full"]
print(f"\nrandom pieces (full size): better in all six {sum(x['better'] == 6 for x in nf)} of {len(nf)}; in 5+ {sum(x['better'] >= 5 for x in nf)}; average cells better {np.mean([x['better'] for x in nf]):.2f}")
print("done")
