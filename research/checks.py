import engine as E, numpy as np, itertools
WIN = dict(mult=1.75, stop_atr=2.0, rr=4.0, vwap=1, max_trades=1, vix_min=11, last_entry=870, otm=-1 if E.CALIB else 0,
           be=0, ctx=-9, rmin=1.0, rmax=2.5, vix_max=99)
BASE = dict(or_min=15, rr=2.0, stop="mid", vwap=1, trend=1, be=1.0, tstop=45, ctx=-9, max_trades=2)   # app today (approx.)
D = {s: E.load_days(s) for s in ("NIFTY", "BANKNIFTY", "FINNIFTY")}
dates = [d.d for d in D["NIFTY"]]
def win(tr, w):
    t = [x for x in tr if x["date"] >= dates[-w]]
    pos, neg = sum(x["pnl"] for x in t if x["pnl"] > 0), -sum(x["pnl"] for x in t if x["pnl"] <= 0)
    return sum(x["pnl"] for x in t), (pos / neg if neg else 99), len(t), (sum(x["pnl"] > 0 for x in t) / len(t) * 100 if t else 0)
def show(name, strat, p, syms=("NIFTY", "BANKNIFTY")):
    tot = {w: 0 for w in (30, 60, 90, 120)}
    for s in syms:
        tr = E.run(D[s], s, strat, p)
        ys = {y: round(sum(x["pnl"] for x in tr if x["date"].year == y) / 1000, 1) for y in (2023, 2024, 2025, 2026)}
        ws = {w: win(tr, w) for w in (30, 60, 90, 120)}
        for w in tot: tot[w] += ws[w][0]
        print(f"  {name:10s} {s:9s} years(k) {ys} | " + " ".join(f"{w}d {v[0]/1000:+.1f}k pf{v[1]:.2f} n{v[2]} w{v[3]:.0f}%" for w, v in ws.items()))
    print(f"  {name:10s} BOTH      " + " ".join(f"{w}d ₹{tot[w]/1000:+.1f}k" for w in tot))
print("== chosen vs app's current rules")
show("NEW", "noise", WIN); show("CURRENT", "orb", BASE)
print("== unseen instrument (never used in tuning)")
show("NEW", "noise", WIN, ("FINNIFTY",))
print("== double slippage (1% per fill)")
E.SLIP = 0.01; show("NEW 2xslip", "noise", WIN); E.SLIP = 0.005
print("== neighbours (one setting changed): last-120d total for both indices")
G = {"mult": [1.5, 1.75, 2.0], "stop_atr": [1.5, 2.0, 2.5], "rr": [3.0, 4.0, None], "vwap": [0, 1], "max_trades": [1, 2],
     "vix_min": [0, 11, 12, 13], "last_entry": [810, 870], "otm": [0, -1, -2]}
res = []
for k, vals in G.items():
    for v in vals:
        if v == WIN[k]: continue
        p = dict(WIN, **{k: v})
        t120 = sum(win(E.run(D[s], s, "noise", p), 120)[0] for s in ("NIFTY", "BANKNIFTY"))
        tall = sum(sum(x["pnl"] for x in E.run(D[s], s, "noise", p)) for s in ("NIFTY", "BANKNIFTY"))
        res.append((k, v, t120, tall))
for k, v, a, b in res: print(f"  {k}={v}: last120 ₹{a/1000:+.1f}k   full-history ₹{b/1000:+.1f}k")
print("  neighbours profitable in last 120d:", sum(a > 0 for _, _, a, _ in res), "/", len(res))
