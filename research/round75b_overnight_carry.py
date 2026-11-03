"""Round 75b: carry an option overnight after a strong close (2 Nov and 3 Nov: Nifty gapped +169 / +141 points).

Of the Nifty's ~760-point gain over 2-3 Nov (30 Oct close 23,053 -> 3 Nov close 23,811), 310 points came in the two opening
gaps, which no intraday rule can catch. Autopilot squares off at 15:15 every day. Test: buy at the close, sell next morning.

Signals on day D (calls; puts mirror):
  any      every day, both sides kept separately (the overnight drift itself)
  strong   D up >= 0.5% open->close and the close in the top 20% of D's range
  strong+  strong and the close above the previous day's high
  top10    D up >= 0.3% and the close in the top 10% of the range
Real NSE option prices (bhavcopy, Oct 2023 - Oct 2026, Nifty + Bank Nifty): buy the ATM strike of the nearest expiry that is
at least 1 trading day away at D's CLOSE price, sell at D+1's OPEN (first trade) or D+1's CLOSE. 1% slippage each side plus
charges (Rs 40 + 0.15% sell-side + 0.05% both sides). One lot. Random-side twin for the same days.
Usage: python research/round75b_overnight_carry.py
"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round25_harness as H

DATA = os.path.join(HERE, "data")
LOT = {"NIFTY": 65, "BANKNIFTY": 30}
STEP = {"NIFTY": 50, "BANKNIFTY": 100}
SLIP = float(os.environ.get("R75_SLIP", "0.01"))


def daily(sym):
    """Day candles; the signal uses only what is known at the 15:15 bar's close (s = close at 15:20, h/l up to then)."""
    x = H.load(sym)
    d = x.groupby(x.index.normalize()).agg(o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last"))
    y = x[x.index.hour * 60 + x.index.minute <= 15 * 60 + 15]
    e = y.groupby(y.index.normalize()).agg(h=("high", "max"), l=("low", "min"), c=("close", "last"))
    d["h15"], d["l15"], d["c15"] = e.h, e.l, e.c
    d.index = d.index.strftime("%Y-%m-%d")
    return d


def chain(sym):
    b = pd.read_csv(os.path.join(DATA, "OPT_bhavcopy.csv.gz"))
    b = b[(b.sym == sym)]
    return {k: g for k, g in b.groupby("date")}


def pick(g, day, spot, opt):
    """ATM strike, nearest expiry strictly after `day` (so it is still alive the next morning)."""
    g = g[(g.opt == opt) & (g.expiry > day)]
    if g.empty:
        return None
    e = g.expiry.min()
    g = g[g.expiry == e]
    k = g.strike.values[np.argmin(np.abs(g.strike.values - spot))]
    return e, k


def main():
    res = {}
    rows = []
    for sym in LOT:
        d = daily(sym)
        ch = chain(sym)
        days = sorted(set(d.index) & set(ch))
        for a, b in zip(days[:-1], days[1:]):
            r = d.loc[a]
            pos = (r.c15 - r.l15) / max(r.h15 - r.l15, 1e-9)
            mv = r.c15 / r.o - 1
            prv = d.index.get_loc(a)
            ph = d.iloc[prv - 1].h if prv > 0 else np.nan
            pl = d.iloc[prv - 1].l if prv > 0 else np.nan
            for s, opt in ((1, "CE"), (-1, "PE")):
                tags = ["any"]
                if s * mv >= 0.005 and (pos >= 0.8 if s == 1 else pos <= 0.2):
                    tags.append("strong")
                    if (s == 1 and r.c15 > ph) or (s == -1 and r.c15 < pl):
                        tags.append("strong+")
                if s * mv >= 0.003 and (pos >= 0.9 if s == 1 else pos <= 0.1):
                    tags.append("top10")
                p = pick(ch[a], a, r.c, opt)
                if p is None:
                    continue
                e, k = p
                ga = ch[a]; gb = ch[b]
                x = ga[(ga.opt == opt) & (ga.expiry == e) & (ga.strike == k)]
                y = gb[(gb.opt == opt) & (gb.expiry == e) & (gb.strike == k)]
                if x.empty or y.empty:
                    continue
                pin = float(x.close.iloc[0]); po_open = float(y.open.iloc[0]); po_close = float(y.close.iloc[0])
                if pin <= 1 or po_open <= 0 or int(x.volume.iloc[0]) == 0:
                    continue
                lot = LOT[sym]
                for ex, po in (("next_open", po_open), ("next_close", po_close)):
                    bi, so = pin * (1 + SLIP), po * (1 - SLIP)
                    rs = (so - bi) * lot - (40 + 0.0015 * so * lot + 0.0005 * (bi + so) * lot)
                    for t in tags:
                        rows.append({"sym": sym, "date": a, "side": s, "sig": t, "exit": ex, "pin": pin, "pout": po, "rs": rs,
                                     "ret": so / bi - 1, "gap": d.loc[b].o / r.c - 1, "dte": (pd.Timestamp(e) - pd.Timestamp(a)).days})
    T = pd.DataFrame(rows)
    T.to_csv(os.path.join(HERE, f"round75b_trades_{SLIP}.csv.gz"), index=False)
    out = []
    for (sig, ex, side), g in T.groupby(["sig", "exit", "side"]):
        y = g.groupby(g.date.str[:4]).rs.sum().round(0).to_dict()
        r = {"sig": sig, "exit": ex, "side": "CALL" if side > 0 else "PUT", "n": len(g), "win": round(float((g.rs > 0).mean()), 3),
             "ret": round(float(g.ret.mean()) * 100, 2), "rs_trade": round(float(g.rs.mean())), "rs_total": round(float(g.rs.sum())),
             "years": y, "by_sym": g.groupby("sym").rs.sum().round(0).to_dict(), "worst": round(float(g.rs.min())),
             "gap_pts_mean": round(float(g.gap.mean()) * 100, 3)}
        out.append(r)
        print(f"{sig:<8} {ex:<10} {r['side']:<4} n{r['n']:>4} win{r['win']:.2f} ret{r['ret']:+6.2f}% Rs/tr{r['rs_trade']:+6d} total{r['rs_total']:+8d} "
              f"worst{r['worst']:+6d} yrs {y} syms {r['by_sym']} gap{r['gap_pts_mean']:+.3f}%", flush=True)
    res["summary"] = out
    # shuffle control: same number of nights, random days, same side and exit (from the 'any' pool)
    rng = np.random.default_rng(7)
    res["shuffle"] = {}
    for r in out:
        if r["sig"] == "any":
            continue
        pool = T[(T.sig == "any") & (T.exit == r["exit"]) & (T.side == (1 if r["side"] == "CALL" else -1))].rs.values
        sims = np.array([rng.choice(pool, r["n"], replace=False).sum() for _ in range(2000)])
        p = float((sims >= r["rs_total"]).mean())
        res["shuffle"][f"{r['sig']}|{r['exit']}|{r['side']}"] = p
        print(f"shuffle {r['sig']:<8} {r['exit']:<10} {r['side']:<4} p={p:.4f}")
    json.dump(res, open(os.path.join(HERE, f"round75b_results_{SLIP}.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
