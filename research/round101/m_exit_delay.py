import sys, os, json; sys.path.insert(0, "research/round101")
os.environ.setdefault("L_CAND", "SENSEX,14,2.0,1.5,signal,1"); sys.argv = [sys.argv[0], "0"]
import l_squeeze as Q
from lib import *
L, S, xL, xS = Q.CSIG
d = lambda x: np.nan_to_num(Q.sh(np.asarray(x, float), 1)) > 0
out = {}
for tag, sig, kw in (("exit one bar later", (L, S, d(xL), d(xS)), {}), ("entry and exit one bar later", (L, S, d(xL), d(xS)), {"delay": 1}), ("entry one bar later, +0.5% fills, exit one bar later", (L, S, d(xL), d(xS)), {"delay": 1, "slip": 0.005})):
    P, own = Q.piece("SENSEX", sig, "signal", 1, **kw); row, b, f = Q.judge(Q.half(P)); Q.line(tag, row, b, f, own); out[tag] = {"better": b, "fall_ok": f, "cells": Q.pack(row), "own": own}
# 5-minute bars version of the same idea (42 five-minute bars = 14 x 15m is a different rule; here: same rule computed on 5m bars with n=14 and n=42)
json.dump(out, open(OUT + "/m_exitdelay.json", "w"), default=float)
