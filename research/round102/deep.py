"""Round 102g: deep check of the only combination that passed: Nifty swing 'gD|pbH|sw2R' (daily close > 200 EMA and 20 EMA > 50 EMA,
an hour today touched the 1-hour 50 EMA, day closes above the 1-hour 20 EMA on a green day candle -> call; mirror for puts;
stop = 2-day low on a close, target 2R on a close, out after 10 sessions) at different sizes, on model and on real NSE prices,
against random-entry and random-direction controls, and year by year."""
import sys, json
sys.path.insert(0, "/home/claude/dimansee/fno/research/round101"); sys.path.insert(0, "/home/claude/r102")
import addtest as AD, swing as W, lib as L
import numpy as np, pandas as pd

KEY = "NIFTY|gD|pbH|sw2R|ATM"
d = W.frames("NIFTY")
T = W.trades(d, "gD", "pbH", "sw2R")


def to_rows(P, real=False):
    lot = W.R34.D.lot_size("NIFTY")
    slip = 0.01 if real else 0.005
    pi = P.prem_in.values * (1 + slip); po = np.maximum(P.prem_out.values * (1 - slip), 0.05)
    risk = np.abs(P.ep.values - P.sl.values)
    rl = (np.maximum(P.risk.values * 1.005, pi * 0.05) if "risk" in P else pi * 0.5) * lot
    out = pd.DataFrame({"date": pd.to_datetime(P.d_out).dt.strftime("%Y-%m-%d"), "entry_time": "15:15", "exit_time": "15:20", "sym": "NIFTY",
                        "strategy": "Swing r102", "side": np.where(P.side > 0, "CALL", "PUT"), "prem_in": pi, "prem_out": po, "lot": lot,
                        "risk_lot": rl, "R": P.pts.values / np.maximum(risk, 1e-9), "spot_in": P.ep.values, "spot_out": P["xs"].values,
                        "reason": "r102", "pieces": 1})
    out["risk_lot_capped"] = out.risk_lot; out["cpr_w"] = np.nan; out["score"] = 0
    return out


def real_rows(T):
    R = W.real_price("NIFTY", T)
    # risk per lot for sizing: model risk of the same trade (real data has no intraday stop price)
    P = W.model_price("NIFTY", T)
    R = R.merge(P[["d_in", "risk"]], on="d_in", how="left")
    return to_rows(R, real=True)


res = {"base": AD.fmt(AD.BASE), "trades": int(len(T)), "by_year": T.groupby(T.d_in.dt.year).size().to_dict(), "sizes": {}, "random": {}, "years": {}}
P = W.model_price("NIFTY", T)
print("trades", len(T), res["by_year"], "1-lot model by year", P.groupby(P.date.str[:4]).rs.sum().round(0).to_dict(), flush=True)
Rr = W.real_price("NIFTY", T)
print("1-lot real by year", Rr.groupby(Rr.date.str[:4]).rs.sum().round(0).to_dict(), "n", len(Rr), flush=True)
res["one_lot_model"] = P.groupby(P.date.str[:4]).rs.sum().round(0).to_dict(); res["one_lot_real"] = Rr.groupby(Rr.date.str[:4]).rs.sum().round(0).to_dict()
res["one_lot_real_n"] = int(len(Rr)); res["win_model"] = float((P.rs > 0).mean()); res["idx_pts"] = float(P.pts.mean())
base_rows = to_rows(P); rr = real_rows(T)
for m in (1.0, 1.5, 2.0, 3.0):
    for lab, X in (("model", base_rows), ("real", rr)):
        c = AD.cells([AD.half(X, 1 / m)]); j = AD.judge(c)
        res["sizes"][f"{lab} x{m}"] = {**j, "cells": AD.fmt(c)}
        print(lab, m, j, AD.fmt(c), flush=True)

# controls at x2 (model): (a) same days, random direction; (b) random entry days, same hold lengths and side mix
rng = np.random.default_rng(11)
days = d.index[200:]; closes = d.c
real_c = AD.cells([AD.half(base_rows, 0.5)])
for ctl in ("twin", "random_days"):
    better = {k: 0 for k in real_c}; passes = 0; N = 30
    for s in range(N):
        X = T.copy()
        if ctl == "twin":
            X["side"] = X.side * rng.choice([1, -1], len(X))
        else:
            pos = np.sort(rng.choice(np.arange(200, len(d) - 11), len(X), replace=False))
            X["i"] = pos; X["j"] = np.minimum(pos + X.hold.values, len(d) - 1)
            X["d_in"] = d.index[X.i.values]; X["d_out"] = d.index[X.j.values]
            X["ep"] = d.c.values[X.i.values]; X["xs"] = d.c.values[X.j.values]
            X["side"] = rng.permutation(X.side.values)
        X["sl"] = X.ep - X.side * (T.ep - T.sl).abs().values
        c = AD.cells([AD.half(to_rows(W.model_price("NIFTY", X)), 0.5)]); j = AD.judge(c); passes += j["pass"]
        for k in c:
            better[k] += real_c[k]["final"] > c[k]["final"]
    res["random"][ctl] = {"real_beats_share": {k: v / N for k, v in better.items()}, "controls_passing": passes, "n": N}
    print(ctl, res["random"][ctl], flush=True)

# each year alone at x2 (model)
for cap in (20000, 200000, 500000):
    for y, (a, b) in L.YEARS.items():
        t0 = L.replay(cap, a, b, T=AD.T0, A=AD.A0, H=AD.H0); t1 = L.replay(cap, a, b, extra=[AD.half(base_rows, 0.5)], T=AD.T0, A=AD.A0, H=AD.H0)
        res["years"][f"{cap}|{y}"] = [[round(t0["final"] / 1e5, 2), t0["dd"]], [round(t1["final"] / 1e5, 2), t1["dd"]]]
        print(cap, y, res["years"][f"{cap}|{y}"], flush=True)
json.dump(res, open("/home/claude/r102/out/deep.json", "w"), indent=1, default=str)
