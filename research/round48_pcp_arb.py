"""Round 48 (28 Oct 2026). Put-call parity / box-spread arbitrage check on recorded Nifty & Bank Nifty option candles.

Parity (European index options): C - P = (F - K) e^{-rT}  =>  X(K) = C - P + K e^{-rT} is the same for every strike.
Spot/futures and dividends are not needed if we compare two strikes of the same expiry (a box spread):
    box mispricing per unit = |X(K1) - X(K2)|   (buy the cheap synthetic, sell the dear one, settle at expiry)
Only 5m bars where all 4 legs actually traded (volume > 0) are used, so stale prints are excluded.
Costs (from 1 Apr 2026): STT 0.15% on option sale premium and 0.15% on exercised intrinsic, exchange 0.0355%
of premium, stamp 0.003% on buys, GST 18% on exchange + brokerage, Rs 20 per leg, plus half-spread slippage
per leg (Nifty 0.5 pt, Bank Nifty 1.0 pt; liquid near-ATM strikes, optimistic).
"""
import itertools
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / "data"
R = 0.065
LOT = {"NIFTY": 65, "BANKNIFTY": 30}
STEP = {"NIFTY": 50, "BANKNIFTY": 100}
SLIP = {"NIFTY": 0.5, "BANKNIFTY": 1.0}

df = pd.read_csv(DATA / "OPT_live5m.csv.gz", parse_dates=["ts", "expiry"])
df = df[(df.volume > 0) & (df.ts.dt.time >= pd.Timestamp("09:20").time()) & (df.ts.dt.time <= pd.Timestamp("15:25").time())]
w = df.pivot_table(index=["ts", "sym", "expiry", "strike"], columns="opt", values="close").dropna().reset_index()
w["T"] = ((w.expiry + pd.Timedelta(hours=15, minutes=30)) - w.ts).dt.total_seconds() / (365 * 86400)
w = w[w["T"] > 0.5 / 365]
w["X"] = w.CE - w.PE + w.strike * np.exp(-R * w["T"])

rows = []
for (ts, sym, ex), g in w.groupby(["ts", "sym", "expiry"]):
    if len(g) < 2:
        continue
    fwd = float(np.median(g.X))                      # implied (discounted) forward
    g = g[(g.strike - fwd).abs() <= 6 * STEP[sym]]   # near-ATM, the liquid strikes
    for a, b in itertools.combinations(g.itertuples(), 2):
        k1, k2 = sorted((a, b), key=lambda r: r.strike)
        dev = k1.X - k2.X                            # >0: synthetic at K1 is dear -> sell K1 synth, buy K2 synth
        width = k2.strike - k1.strike
        prem_sold = (k1.CE + k2.PE) if dev > 0 else (k1.PE + k2.CE)
        prem_all = k1.CE + k1.PE + k2.CE + k2.PE
        q = LOT[sym]
        cost = (4 * SLIP[sym] + 0.0015 * prem_sold + 0.0015 * width + 0.000355 * prem_all * 1.18
                + 0.00003 * (prem_all - prem_sold) + 4 * 20 * 1.18 / q)
        rows.append({"ts": ts, "sym": sym, "dte": round(k1.T * 365, 1), "width": width,
                     "k1": k1.strike, "k2": k2.strike, "exp": ex, "sdev": dev, "dev": abs(dev), "cost": cost, "net": abs(dev) - cost})
r = pd.DataFrame(rows)
r.to_csv(Path(__file__).resolve().parent / "round48_pcp_arb_results.csv.gz", index=False)
print("window:", w.ts.min(), "->", w.ts.max(), " box observations (all 4 legs traded in the bar):", len(r))
for sym, g in r.groupby("sym"):
    q = LOT[sym]
    pos = g[g.net > 0]
    bars = g.groupby("ts").net.max()
    print(f"\n{sym}: {len(g)} boxes, {g.ts.nunique()} bars")
    print("  gross deviation pts  median %.2f  p90 %.2f  p99 %.2f" % tuple(g.dev.quantile([.5, .9, .99])))
    print("  cost pts             median %.2f" % g.cost.median())
    print("  boxes net > 0: %d (%.2f%%), net > 5 pts: %d" % (len(pos), 100 * len(pos) / len(g), (g.net > 5).sum()))
    print("  bars with any net>0 box: %d of %d (%.1f%%)" % ((bars > 0).sum(), len(bars), 100 * (bars > 0).mean()))
    if len(pos):
        print("  when net>0: median net %.2f pts = Rs %.0f per lot; median width %d; median dte %.1f"
              % (pos.net.median(), pos.net.median() * q, pos.width.median(), pos.dte.median()))
        print("  by month:", pos.groupby(pos.ts.dt.to_period("M")).size().to_dict())

# Does the gap survive to the next bar, when you could actually place the 4 orders?
nxt = r[["ts", "sym", "exp", "k1", "k2", "sdev"]].copy()
nxt["ts"] = nxt.ts - pd.Timedelta(minutes=5)
m = r[r.net > 0].merge(nxt.rename(columns={"sdev": "sdev_next"}), on=["ts", "sym", "exp", "k1", "k2"])
m["net_next"] = np.sign(m.sdev) * m.sdev_next - m.cost
print("\nNext-bar check (gap seen in bar t, trade at bar t+5min prices), boxes with both bars traded:", len(m))
for sym, g in m.groupby("sym"):
    print(f"  {sym}: n={len(g)}  same-sign next bar {100*(np.sign(g.sdev)==np.sign(g.sdev_next)).mean():.1f}%  "
          f"net_next>0 {100*(g.net_next>0).mean():.1f}%  mean net_next {g.net_next.mean():.2f} pts "
          f"(Rs {g.net_next.mean()*LOT[sym]:.0f}/lot)  corr(dev_t, dev_t+1) {np.corrcoef(g.sdev, g.sdev_next)[0,1]:.2f}")
# noise yardstick: index 5-min range
for sym in ("NIFTY", "BANKNIFTY"):
    x = pd.read_csv(DATA / f"{sym}_5m_upstox.csv.gz", parse_dates=["ts"])
    x = x[x.ts >= "2026-05-22"]
    print(f"  {sym} median 5-min index range since May: {(x.high-x.low).median():.1f} pts")
