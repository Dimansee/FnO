"""Round 102d: candidates from 102a (zones) and 102b (swing) added to today's Autopilot (Outcomes replay, all live rules incl. the
Rs 5L channel slot and 2-OTM switch). Cells Rs 20k / 2L / 5L x 2023-24 / 2025-26 / full. Bar (house): more money than today in the
six Rs 2L + Rs 20k cells, worst fall no more than 2 points deeper. Rs 5L cells shown too."""
import sys, json
sys.path.insert(0, "/home/claude/dimansee/fno/research/round101"); sys.path.insert(0, "/home/claude/r102")
import lib as L
import numpy as np, pandas as pd
import zones as Z, swing as W
from fno import portfolio_bt as PB
M = L.M
CAPS = (20000, 200000, 500000)
T0, A0, H0 = L.base_sets()


def cells(extra=None):
    return {f"{cap}|{p}": L.replay(cap, a, b, extra=extra, T=T0, A=A0, H=H0) for cap in CAPS for p, (a, b) in L.PER.items()}


BASE = cells()


def judge(r):
    six = [k for k in r if not k.startswith("500000")]
    better = sum(r[k]["final"] > BASE[k]["final"] for k in six)
    deeper = any(r[k]["dd"] < BASE[k]["dd"] - 2 for k in six)
    b5 = sum(r[k]["final"] > BASE[k]["final"] for k in r if k.startswith("500000"))
    return {"better6": better, "fall_ok": not deeper, "pass": better == 6 and not deeper, "better_5L": b5}


def zone_piece(key):
    sym, tf, z, mode, cnt, col, stop, ex, pdy = key.split("|")
    stepmin = 5 if tf == "5m" else 15
    df, o, h, l, c, hm, day = L.bar_of(sym, tf)
    E = Z.events(df, o, h, l, c, day, sym, float(z[1:]))
    k = int(cnt[-1]); cn = cnt[:-1]
    sig, st, tg = Z.signals(E, o, h, l, c, mode, cn, k, col == "col", stop, ex, c * 0.0002)
    rows = Z.sim(o, h, l, c, hm, day, sig, st, tg, trail_R=1.0 if ex == "trail" else 0.0, step=stepmin, per_day=int(pdy[0]))
    T = Z.priced(sym, df, rows, stepmin)
    return L.to_piece(sym, T, "Zone " + mode), T


def swing_piece(key):
    sym, g, t, e, st = key.split("|")
    d = W.frames(sym); T = W.trades(d, g, t, e); P = W.model_price(sym, T, 1 if st == "ITM" else 0)
    lot = P["lot"].values; pi = P.prem_in.values * 1.005; po = np.maximum(P.prem_out.values * 0.995, 0.05)
    risk = np.abs(P.ep.values - P.sl.values)
    out = pd.DataFrame({"date": P.d_out.dt.strftime("%Y-%m-%d"), "entry_time": "15:15", "exit_time": "15:20", "sym": sym,
                        "strategy": "Swing r102", "side": np.where(P.side > 0, "CALL", "PUT"), "prem_in": pi, "prem_out": po, "lot": lot,
                        "risk_lot": np.maximum(P.risk.values * 1.005, pi * 0.05) * lot, "R": P.pts.values / np.maximum(risk, 1e-9),
                        "spot_in": P.ep.values, "spot_out": P["xs"].values, "reason": "r102 swing (booked on exit day)", "pieces": 1})
    out["risk_lot_capped"] = out["risk_lot"]
    out["cpr_w"] = np.nan
    out["score"] = 0
    return out, P


def half(P, m=2):
    return P.assign(risk_lot=P.risk_lot * m, risk_lot_capped=P.risk_lot_capped * m)


def fmt(r):
    return {k: [round(v["final"] / 1e5, 2), v["dd"]] for k, v in r.items()}


if __name__ == "__main__":
    Dz = pd.read_pickle("/home/claude/r102/out/zones_all.pkl")
    Ds = pd.read_pickle("/home/claude/r102/out/swing_all.pkl")
    cand = []
    # design-period picks (house method: best 2023-24 result, judged afterwards)
    for mode in ("rev", "brk"):
        sel = Dz[(Dz["mode"] == mode) & (Dz.a_n >= 150)].sort_values("a", ascending=False)
        sel = sel.drop_duplicates(subset=["a", "b"]).head(5)
        cand += [("zone", k, f"design pick ({mode})") for k in sel.key]
    for k in Ds[Ds.n >= 20].sort_values("a", ascending=False).drop_duplicates(subset=["a", "b"]).head(3).key:
        cand.append(("swing", k, "design pick"))
    # hindsight: the steadiest groups found in the grid (flagged, not a fair pick)
    cand += [("zone", "NIFTY|15m|z0.5|brk|week1|col|candle|eod|1d", "hindsight: break after 1+ rejections this week"),
             ("zone", "NIFTY|15m|z0.5|brk|day3|col|candle|eod|1d", "hindsight: break after 3+ rejections today"),
             ("zone", "NIFTY|5m|z1.0|rev|day1|col|zone|eod|1d", "hindsight: your +/-50 reversal, best version"),
             ("swing", "NIFTY|gD|brk|sw2R|ATM", "hindsight: Nifty daily-trend breakout swing"),
             ("swing", "NIFTY|gD|pbD|sw2R|ATM", "hindsight: Nifty daily-trend pullback swing")]
    res = {"base": fmt(BASE), "cands": []}
    print("BASE", fmt(BASE), flush=True)
    for kind, key, why in cand:
        try:
            P, raw = zone_piece(key) if kind == "zone" else swing_piece(key)
        except Exception as e:
            print("ERR", key, e); continue
        for size, X in (("full", P), ("half", half(P))):
            r = cells([X]); j = judge(r)
            row = {"kind": kind, "key": key, "why": why, "size": size, "trades": int(len(P)), **j, "cells": fmt(r)}
            res["cands"].append(row)
            print(kind, key, size, why, j, fmt(r), flush=True)
    json.dump(res, open("/home/claude/r102/out/addtest.json", "w"), indent=1)
