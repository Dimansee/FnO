"""Round 102f: combinations.
1. Forward selection (house method): from the pool of new pieces (zone + swing, full or half size), add the piece that raises the
   Rs 2L 2023-24 final most with the worst fall no more than 2 points deeper; up to 3 steps; then judge the result on every cell.
2. Hindsight pair search: every pair of the strongest pieces (those better in >= 3 of 6 cells alone), judged on all cells.
3. Each of those also with the trend-day gate at Rs 20k instead of Rs 40k."""
import sys, json, itertools
sys.path.insert(0, "/home/claude/dimansee/fno/research/round101"); sys.path.insert(0, "/home/claude/r102")
import addtest as AD
import lib as L
from fno import config as C

R = json.load(open("/home/claude/r102/out/addtest.json"))
pool = {}
for c in R["cands"]:
    k = (c["kind"], c["key"])
    if k not in pool:
        pool[k] = AD.zone_piece(c["key"])[0] if c["kind"] == "zone" else AD.swing_piece(c["key"])[0]
items = [(k, s) for k in pool for s in ("full", "half")]
piece = lambda it: pool[it[0]] if it[1] == "full" else AD.half(pool[it[0]])
BASE = AD.BASE
a, b = L.PER["2023-24"]


def one(extra, cap=200000, per="2023-24"):
    x, y = L.PER[per]
    return L.replay(cap, x, y, extra=extra, T=AD.T0, A=AD.A0, H=AD.H0)


out = {"forward": [], "pairs": [], "gate20k": []}
chosen = []; best = BASE["200000|2023-24"]
for step in range(3):
    top = None
    for it in items:
        if it in chosen:
            continue
        r = one([piece(x) for x in chosen + [it]])
        if r["dd"] >= BASE["200000|2023-24"]["dd"] - 2 and r["final"] > (top[1]["final"] if top else best["final"]):
            top = (it, r)
    if not top:
        break
    chosen.append(top[0]); best = top[1]
    cells = AD.cells([piece(x) for x in chosen]); j = AD.judge(cells)
    out["forward"].append({"step": step + 1, "set": [f"{x[0][1]} ({x[1]})" for x in chosen], **j, "cells": AD.fmt(cells)})
    print("forward", step + 1, [f"{x[0][1]} ({x[1]})" for x in chosen], j, AD.fmt(cells), flush=True)

strong = sorted({(c["kind"], c["key"]) for c in R["cands"] if c["better6"] >= 3})
sitems = [(k, s) for k in strong for s in ("full", "half")]
print("pair pool", len(sitems), flush=True)
npass = 0
for p, q in itertools.combinations(sitems, 2):
    if p[0] == q[0]:
        continue
    cells = AD.cells([piece(p), piece(q)]); j = AD.judge(cells)
    npass += j["pass"]
    out["pairs"].append({"set": [f"{p[0][1]} ({p[1]})", f"{q[0][1]} ({q[1]})"], **j, "cells": AD.fmt(cells)})
    print("pair", p[0][1], p[1], "+", q[0][1], q[1], j, flush=True)
print("pairs passing", npass, "of", len(out["pairs"]))

orig = C.TREND_MIN_EQUITY
C.TREND_MIN_EQUITY = 20000
try:
    cells = AD.cells(); out["gate20k"].append({"set": ["trend-day gate Rs 20k"], **AD.judge(cells), "cells": AD.fmt(cells)})
    print("gate20k alone", AD.judge(cells), AD.fmt(cells), flush=True)
    for it in sitems:
        cells = AD.cells([piece(it)]); j = AD.judge(cells)
        out["gate20k"].append({"set": ["trend-day gate Rs 20k", f"{it[0][1]} ({it[1]})"], **j, "cells": AD.fmt(cells)})
        print("gate20k +", it[0][1], it[1], j, flush=True)
finally:
    C.TREND_MIN_EQUITY = orig
json.dump(out, open("/home/claude/r102/out/combo.json", "w"), indent=1)
