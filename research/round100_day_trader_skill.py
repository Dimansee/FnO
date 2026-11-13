"""Round 100: the "Day Trader skill" pack (github.com/jakeschincariol/day-trader-skill) tested on crypto.

The pack is written for US stocks only (its risk check BLOCKS crypto). Its trade is:
  scanner (daily setups) -> opening-range breakout entry -> stop at the range midpoint, target 2R (or 1R) -> flat by the close.
Re-coded here exactly with its defaults, for 24-hour crypto with three "opens":
  Asia = 00:00 UTC, London = 08:00 London time, New York = 09:30 New York time (the US stock open the pack trades).
  Opening range = first 15 min; entry = first 5-min close above the range high, between open+15 min and open+6 h
  (the pack: 09:45-15:30); stop = range midpoint; target 2R (pack default) and 1R (its example); flat 6 h 25 min after
  the open (the pack: 15:55). Long only (the pack is long only). Fill at the trigger close, stop checked before the target
  inside a bar. Costs: 8 bps a side (the app's crypto cost) and 4 bps (cheap maker fees) as a check.
Daily filters (the pack's scanner, on yesterday's UTC daily closes): 20-day closing-high breakout, bull flag, reclaimed the
20-day average, RSI(14) < 30, oversold bounce. Each is tested as "ORB only on days this coin was flagged".
Design < 1 Apr 2025 <= unseen. Data: Binance spot 5m, Jan 2024 - Nov 2026 (research-data branch).
Usage: python research/round100_day_trader_skill.py DATA_DIR
"""
import json
import os
import sys

import numpy as np
import pandas as pd

DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data/global"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "round100_day_trader_skill_results.json")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
SPLIT = pd.Timestamp("2025-04-01", tz="UTC")
SESSIONS = {"Asia": ("UTC", 0, 0), "London": ("Europe/London", 8, 0), "NewYork": ("America/New_York", 9, 30)}


def load(sym):
    df = pd.read_csv(f"{DATA}/crypto_{sym}_5m.csv.gz", parse_dates=["ts"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float).sort_index()


def rsi(c, n=14):                                   # the pack's RSI: simple averages of the last n changes
    d = c.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def daily_flags(m5):
    """The pack's find_setups() on daily closes; flags for day D use closes up to D-1."""
    c = m5["close"].resample("1D").last().dropna()
    hi20 = c.shift(1).rolling(20).max()
    ma20, ma20p = c.rolling(20).mean(), c.shift(1).rolling(20).mean()
    run = c.shift(5) / c.shift(15) - 1
    rng5 = (c.rolling(5).max() - c.rolling(5).min()) / c
    r = rsi(c)
    f = pd.DataFrame({
        "breakout_20d": c > hi20,
        "bull_flag": (run > 0.10) & (rng5 < 0.05),
        "reclaim_ma20": (c.shift(1) < ma20p) & (c > ma20),
        "oversold": r < 30,
        "oversold_bounce": (r.shift(1) < 30) & (c > c.shift(1)),
    })
    f.index = f.index + pd.Timedelta(days=1)          # known at the start of the next UTC day
    return f


def orb_trades(m5, sym, sess, tz, hh, mm):
    loc = m5.tz_convert(tz)
    out = []
    for day, D in loc.groupby(loc.index.date):
        t0 = pd.Timestamp(day).tz_localize(tz) + pd.Timedelta(hours=hh, minutes=mm)
        W = D[(D.index >= t0) & (D.index < t0 + pd.Timedelta(minutes=385))]
        if len(W) < 70:
            continue
        orw = W[W.index < t0 + pd.Timedelta(minutes=15)]
        if len(orw) < 3:
            continue
        oh, ol = orw["high"].max(), orw["low"].min()
        rest = W[(W.index >= t0 + pd.Timedelta(minutes=15)) & (W.index <= t0 + pd.Timedelta(hours=6))]
        trig = rest[rest["close"] > oh]
        if trig.empty:
            continue
        ti = trig.index[0]
        entry = float(trig["close"].iloc[0])
        stop = (oh + ol) / 2
        risk = entry - stop
        if risk <= 0:
            continue
        after = W[W.index > ti]
        res = {}
        for tgt in (1.0, 2.0):
            target = entry + tgt * risk
            exitp, why = float(W["close"].iloc[-1]), "close"
            for _, b in after.iterrows():
                if b["low"] <= stop:
                    exitp, why = stop, "stop"
                    break
                if b["high"] >= target:
                    exitp, why = target, "target"
                    break
            res[tgt] = (exitp, why)
        out.append({"sym": sym, "sess": sess, "t": ti.tz_convert("UTC"), "utc_day": ti.tz_convert("UTC").normalize(),
                    "entry": entry, "risk": risk, "risk_pct": risk / entry * 100,
                    **{f"x{int(k)}": v[0] for k, v in res.items()}, **{f"w{int(k)}": v[1] for k, v in res.items()}})
    return out


def summarise(df, tgt, cost):
    if df.empty:
        return {"n": 0}
    gross = (df[f"x{tgt}"] - df["entry"]) / df["entry"]
    net = gross - 2 * cost
    r_net = net * df["entry"] / df["risk"]
    out = {"n": int(len(df)), "gross_pct": round(float(gross.mean() * 100), 4), "net_pct": round(float(net.mean() * 100), 4),
           "net_R": round(float(r_net.mean()), 3), "win_pct": round(float((net > 0).mean() * 100), 1),
           "pf": round(float(net[net > 0].sum() / -net[net < 0].sum()), 2) if (net < 0).any() else None,
           "t": round(float(r_net.mean() / (r_net.std(ddof=1) / np.sqrt(len(r_net)))), 2) if len(r_net) > 2 else None,
           "coins_pos": int(sum(net[df["sym"] == s].mean() > 0 for s in df["sym"].unique()))}
    return out


def main():
    rows, flags = [], {}
    for sym in COINS:
        m5 = load(sym)
        flags[sym] = daily_flags(m5)
        for sess, (tz, hh, mm) in SESSIONS.items():
            rows += orb_trades(m5, sym, sess, tz, hh, mm)
        print(sym, "done", len(rows), flush=True)
    T = pd.DataFrame(rows)
    for f in ["breakout_20d", "bull_flag", "reclaim_ma20", "oversold", "oversold_bounce"]:
        T[f] = [bool(flags[s][f].get(d, False)) if d in flags[s].index else False for s, d in zip(T["sym"], T["utc_day"])]
    T["period"] = np.where(T["t"] < SPLIT, "design", "unseen")
    res = {"median_stop_pct": round(float(T["risk_pct"].median()), 3), "cells": []}
    filters = ["all"] + ["breakout_20d", "bull_flag", "reclaim_ma20", "oversold", "oversold_bounce"]
    for sess in ["All"] + list(SESSIONS):
        for flt in filters:
            sub = T if sess == "All" else T[T["sess"] == sess]
            if flt != "all":
                sub = sub[sub[flt]]
            for tgt in (2, 1):
                for cost in (8e-4, 4e-4):
                    cell = {"session": sess, "filter": flt, "target": f"{tgt}R", "cost_bps_side": int(cost * 1e4)}
                    for p in ("design", "unseen"):
                        cell[p] = summarise(sub[sub["period"] == p], tgt, cost)
                    res["cells"].append(cell)
    json.dump(res, open(OUT, "w"), indent=1, default=str)
    print("median stop distance %:", res["median_stop_pct"])
    print(f"{'session':8} {'filter':16} tgt cost |  design: n  gross%  net%  netR  win% |  unseen: n  gross%  net%  netR  win%  t  coins+")
    for c in res["cells"]:
        if c["cost_bps_side"] != 8 and c["filter"] != "all":
            continue
        d, u = c["design"], c["unseen"]
        if not u.get("n"):
            continue
        print(f"{c['session']:8} {c['filter']:16} {c['target']:3} {c['cost_bps_side']:3}  | {d.get('n',0):6} {d.get('gross_pct',0):7.3f} {d.get('net_pct',0):6.3f} {d.get('net_R',0):5.2f} {d.get('win_pct',0):5.1f}"
              f" | {u['n']:6} {u['gross_pct']:7.3f} {u['net_pct']:6.3f} {u['net_R']:5.2f} {u['win_pct']:5.1f} {u['t'] or 0:5.1f} {u['coins_pos']}")


if __name__ == "__main__":
    main()
