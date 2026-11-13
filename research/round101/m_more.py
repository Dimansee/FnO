"""Part M: more on the squeeze candidate chosen in part L (Sensex, 14 bars, exit when momentum turns, 1 a day, half size):
year-by-year inside one continuous run, overlap with today's trades, sizes, a Nifty twin, by side / hour / quarter."""
import sys, json; sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
os_env = __import__("os").environ
os_env.setdefault("L_CAND", "SENSEX,14,2.0,1.5,signal,1")
sys.argv = [sys.argv[0], "0"]          # stage 0 = define everything, run nothing
import l_squeeze as Q
from lib import *
P, own = Q.CP, Q.COWN; ref = Q.ref; CELLS = Q.CELLS
res = {"cand": Q.CNAME, "own": own}
print("\nA. one continuous run 2023 - Oct 2026: money at each year end")
res["year_end"] = {}
for cap in (200_000, 20_000, 25_000):
    for tag, X in (("today", None), ("with squeeze", Q.half(P))):
        R = replay(cap, *PER["full"], extra=X, raw=True); R = R[R.pnl.notna()] if "pnl" in R else R
        eq = R.groupby(R.date.str[:4]).equity.last(); res["year_end"][f"{cap}|{tag}"] = eq.to_dict()
        print(f"   Rs {cap:>7,} {tag:<13}" + "".join(f"  {y}: {v / 1e5:>8.2f}L" for y, v in eq.items()), flush=True)
print("\nB. overlap with today's trades (1 lot each)")
T0, A0, H0 = base_sets()
t2m = lambda s: int(s[:2]) * 60 + int(s[3:])
by = {}
for r in T0.itertuples(index=False):
    by.setdefault(r.date, []).append((r.sym, r.side, t2m(r.entry_time), t2m(r.exit_time), r.strategy))
n = len(P); same = opp = same_sx = alone = 0
for r in P.itertuples(index=False):
    a, b = t2m(r.entry_time), t2m(r.exit_time); o = [x for x in by.get(r.date, []) if x[2] < b and x[3] > a]
    s_ = [x for x in o if x[1] == r.side]; same += bool(s_); opp += bool([x for x in o if x[1] != r.side]); same_sx += bool([x for x in s_ if x[0] == "SENSEX"]); alone += not o
print(f"   squeeze trades {n}: an existing trade open on the same side at some point {same} ({same / n * 100:.0f}%), on Sensex itself {same_sx} ({same_sx / n * 100:.0f}%), opposite side {opp} ({opp / n * 100:.0f}%), nothing else open {alone} ({alone / n * 100:.0f}%)")
pl = lambda F: ((F.prem_out - F.prem_in) * F.lot).groupby(F.date).sum()
d0, d1 = pl(T0), pl(P); J = pd.concat([d0, d1], axis=1).fillna(0); J.columns = ["today", "sq"]
print(f"   daily P&L correlation with today's rules (all days): {J.corr().iloc[0, 1]:.2f}; on days with a squeeze trade: {J[J.sq != 0].corr().iloc[0, 1]:.2f}")
bad = J[J.today < J.today.quantile(0.1)]; print(f"   on today's worst 10% of days ({len(bad)}): squeeze total {bad.sq.sum():+,.0f} per lot, average {bad.sq.mean():+,.0f} a day")
res["overlap"] = {"n": n, "same_side": same, "same_side_sensex": same_sx, "opposite": opp, "alone": alone, "corr": float(J.corr().iloc[0, 1]), "worst10_sum": float(bad.sq.sum())}
print("\nC. by side, entry hour and quarter (alone, 1 lot, Rs)")
P = P.assign(rs=(P.prem_out - P.prem_in) * P.lot, h=P.entry_time.str[:2], q=P.date.str[:4] + "Q" + ((P.date.str[5:7].astype(int) - 1) // 3 + 1).astype(str), half=np.where(P.date < "2025", "2023-24", "2025-26"))
print(P.groupby(["half", "side"]).agg(n=("rs", "size"), rs=("rs", "sum"), avgR=("R", "mean")).round(2).to_string())
print(P.groupby(["half", "h"]).agg(n=("rs", "size"), rs=("rs", "sum"), avgR=("R", "mean")).round(2).to_string())
qq = P.groupby("q").rs.sum().round(0); print("   quarters:", qq.to_dict()); print(f"   quarters positive: {(qq > 0).sum()} of {len(qq)}")
res["quarters"] = qq.to_dict()
print("\nD. sizes and a Nifty twin")
res["variants"] = {}
NP, nown = Q.piece("NIFTY", Q.squeeze("NIFTY", 14, 2.0, 1.5), "signal", 1)
for tag, X in (("Sensex full size", Q.half(Q.CP, 1)), ("Sensex half size (candidate)", Q.half(Q.CP, 2)), ("Sensex third size", Q.half(Q.CP, 3)), ("Sensex quarter size", Q.half(Q.CP, 4)),
               ("Nifty twin, half size", Q.half(NP, 2)), ("Sensex + Nifty twin, both half", Q.half(Q.CP, 2) + Q.half(NP, 2)), ("Sensex + Nifty twin, both quarter", Q.half(Q.CP, 4) + Q.half(NP, 4))):
    row, b, f = Q.judge(X); Q.line(tag, row, b, f); res["variants"][tag] = {"better": b, "fall_ok": f, "cells": Q.pack(row)}
print("   Nifty twin alone:", json.dumps(nown))
json.dump(res, open(OUT + "/m_more.json", "w"), default=float)
print("done")
