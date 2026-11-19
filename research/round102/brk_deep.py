"""Round 102h: Nifty round-level break slot, deep check (Devansh 19 Nov: deeper falls are fine if the profit is worth it).
Step 1 risk-matched: piece x size x account risk 0.6-1.0%, chosen on Rs 2L 2023-24 only (fall within 2 points), judged elsewhere.
Step 2 full-risk version (accepting a deeper fall): stress (entry one bar late, worse fills), random-entry control, each year, capitals."""
import sys, json
sys.path.insert(0, "/home/claude/dimansee/fno/research/round101"); sys.path.insert(0, "/home/claude/r102")
import addtest as AD, lib as L, zones as Z
import numpy as np, pandas as pd
M = L.M
KEYS = ["NIFTY|15m|z0.5|brk|week1|col|candle|eod|3d", "NIFTY|15m|z0.5|brk|week1|any|candle|trail|3d",
        "NIFTY|15m|z0.5|brk|week2|any|candle|eod|3d", "NIFTY|15m|z0.5|brk|week1|col|candle|eod|1d"]
T0, A0, H0 = AD.T0, AD.A0, AD.H0
CAPS = (20000, 200000, 500000, 1000000)


def rep(cap, per, extra=None, **kw):
    a, b = (L.PER | L.YEARS)[per]
    return L.replay(cap, a, b, extra=extra, T=T0, A=A0, H=H0, **kw)


def cells(extra=None, caps=CAPS, **kw):
    return {f"{c}|{p}": rep(c, p, extra, **kw) for c in caps for p in L.PER}


def fmt(r):
    return {k: [round(v["final"] / 1e5, 2), v["dd"]] for k, v in r.items()}


BASE = cells()
print("BASE", fmt(BASE), flush=True)


def judge(r, base=BASE):
    six = [k for k in r if k.split("|")[0] in ("20000", "200000")]
    return {"better6": sum(r[k]["final"] > base[k]["final"] for k in six),
            "fall_ok": not any(r[k]["dd"] < base[k]["dd"] - 2 for k in six),
            "worst_fall_change": max(base[k]["dd"] - r[k]["dd"] for k in six),
            "better_big": sum(r[k]["final"] > base[k]["final"] for k in r if k.split("|")[0] in ("500000", "1000000"))}


def piece(key, shift=0, slip=0.0):
    """shift = enter `shift` bars later; slip = extra worse fill each side (fraction of premium)."""
    sym, tf, z, mode, cnt, col, stop, ex, pdy = key.split("|")
    df, o, h, l, c, hm, day = L.bar_of(sym, tf)
    E = Z.events(df, o, h, l, c, day, sym, float(z[1:]))
    sig, st, tg = Z.signals(E, o, h, l, c, mode, cnt[:-1], int(cnt[-1]), col == "col", stop, ex, c * 0.0002)
    if shift:
        sig = np.r_[np.zeros(shift), sig[:-shift]]; st = np.r_[np.full(shift, np.nan), st[:-shift]]; tg = np.r_[np.full(shift, np.nan), tg[:-shift]]
        same = np.r_[np.zeros(shift, bool), day[shift:] == day[:-shift]]; sig = np.where(same, sig, 0)
    rows = Z.sim(o, h, l, c, hm, day, sig, st, tg, trail_R=1.0 if ex == "trail" else 0.0, step=15, per_day=int(pdy[0]))
    T = Z.priced(sym, df, rows, 15)
    if slip:
        T = T.copy(); T["prem_in"] = T.prem_in * (1 + slip); T["prem_out"] = T.prem_out * (1 - slip)
    return L.to_piece(sym, T, "Zone brk"), T


res = {"base": fmt(BASE)}
P = {k: piece(k)[0] for k in KEYS}
# step 1: risk-matched selection on Rs 2L 2023-24
sel = []
for k in KEYS:
    for mult in (1.0, 2.0, 3.0):
        for r in (0.6, 0.7, 0.8, 0.9, 1.0):
            v = rep(200000, "2023-24", [AD.half(P[k], mult)], risk_pct=r)
            sel.append({"key": k, "mult": mult, "risk": r, "final": v["final"], "dd": v["dd"]})
S = pd.DataFrame(sel); b0 = BASE["200000|2023-24"]
ok = S[(S.final > b0["final"]) & (S.dd >= b0["dd"] - 2)].sort_values("final", ascending=False)
print("risk-matched candidates on 2023-24:", len(ok), "of", len(S), flush=True)
res["risk_matched"] = []
for _, x in ok.head(6).iterrows():
    c = cells([AD.half(P[x.key], x.mult)], risk_pct=x.risk); j = judge(c)
    res["risk_matched"].append({"key": x.key, "size": 1 / x.mult, "risk": x.risk, **j, "cells": fmt(c)})
    print("RM", x.key, 1 / x.mult, x.risk, j, fmt(c), flush=True)

# step 2: full risk, accept deeper fall; stress + controls for the two strongest
res["full"] = {}
for k in KEYS[:2]:
    for mult in (1.0, 2.0):
        lab = f"{k} size {1 / mult:.2f}"; out = {}
        out["as_is"] = fmt(c := cells([AD.half(P[k], mult)])); out["judge"] = judge(c)
        for sname, kw in (("entry one bar late", {"shift": 1}), ("+0.5% worse fills", {"slip": 0.005}), ("+1% worse fills", {"slip": 0.01}),
                          ("late + 0.5%", {"shift": 1, "slip": 0.005})):
            c = cells([AD.half(piece(k, **kw)[0], mult)]); out[sname] = {"cells": fmt(c), **judge(c)}
        yrs = {}
        for cap in (20000, 200000, 500000):
            for y in L.YEARS:
                t0 = rep(cap, y); t1 = rep(cap, y, [AD.half(P[k], mult)])
                yrs[f"{cap}|{y}"] = [[round(t0["final"] / 1e5, 2), t0["dd"]], [round(t1["final"] / 1e5, 2), t1["dd"]]]
        out["years"] = yrs
        # random-entry control: same trade days count, random 15-min bar in 09:30-14:30, random side, same exit rule (15:15) and stop distance
        df, o, h, l, c_, hm, day = L.bar_of("NIFTY", "15m")
        _, Traw = piece(k)
        rng = np.random.default_rng(5); beats = {}; npass = 0; N = 10
        cand_bars = np.where((hm >= 9 * 60 + 30) & (hm <= 14 * 60 + 30))[0]
        days_u = pd.unique(day)
        for s in range(N):
            dsel = rng.choice(len(days_u), len(Traw), replace=False)
            sig = np.zeros(len(c_)); st = np.full(len(c_), np.nan); tg = np.full(len(c_), np.nan)
            dist = rng.permutation((Traw.ep - Traw.sl).abs().values)
            for n_, di in enumerate(dsel):
                bars = cand_bars[day[cand_bars] == days_u[di]]
                if not len(bars): continue
                t = rng.choice(bars); sd = rng.choice([1, -1])
                sig[t] = sd; st[t] = c_[t] - sd * dist[n_]
            rows = Z.sim(o, h, l, c_, hm, day, sig, st, tg, trail_R=1.0 if "trail" in k else 0.0, step=15, per_day=1)
            R = L.to_piece("NIFTY", Z.priced("NIFTY", df, rows, 15), "Zone brk")
            cr = cells([AD.half(R, mult)]); jr = judge(cr); npass += jr["better6"] == 6
            for kk in cr:
                beats[kk] = beats.get(kk, 0) + (json.loads(json.dumps(out["as_is"]))[kk][0] * 1e5 > cr[kk]["final"])
        out["random"] = {"beats_share": {kk: v / N for kk, v in beats.items()}, "random_better_in_all_six": npass, "n": N}
        res["full"][lab] = out
        print("FULL", lab, out["judge"], {s: (out[s]["better6"], out[s]["worst_fall_change"]) for s in ("entry one bar late", "+0.5% worse fills", "+1% worse fills", "late + 0.5%")},
              out["random"], flush=True)
        json.dump(res, open("/home/claude/r102/out/brk_deep.json", "w"), indent=1, default=str)
json.dump(res, open("/home/claude/r102/out/brk_deep.json", "w"), indent=1, default=str)
print("done")
