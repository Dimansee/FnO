"""What if the rules could take up to N trades a day (signals permitting), never re-entering the same option contract?"""
import sys, json; sys.argv = ["x"]
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    import round6 as R6
import engine as E, numpy as np, pandas as pd
from fno import indicators as I
SYMS = ("NIFTY", "BANKNIFTY")

def book(cap, same_option_ok, max_losses=99):
    W = dict(R6.W, max_trades=cap, max_losses=max_losses); CA = dict(R6.CA, max_trades=cap, max_losses=max_losses)
    out = []
    for sym in SYMS:
        meta = E.META[sym]; step = meta["step"]
        tr = [dict(t, strat="noise", sym=sym) for t in E.run(R6.D[sym], sym, "noise", W)] + \
             [dict(t, strat="camarilla", sym=sym) for t in E.run(R6.D[sym], sym, "camarilla", CA)]
        tr.sort(key=lambda t: (t["date"], t["in"]))
        busy, used, n_day = {}, {}, {}
        for t in tr:
            d = t["date"]
            if busy.get(d, 0) > t["in"] or n_day.get(d, 0) >= cap:
                continue
            k = round(t["spot_in"] / step) * step - t["side"] * step          # 1-ITM strike actually bought
            key = (d, t["side"], k)
            if not same_option_ok and key in used:
                continue
            used[key] = 1; busy[d] = t["out"]; n_day[d] = n_day.get(d, 0) + 1
            out.append(t)
    return out

def stats(tr, label):
    s = R6.stats(tr); df = pd.DataFrame(tr)
    ch = sum(I.charges(t["prem_in"], t["prem_out"], t["lots"] * E.META[t["sym"]]["lot"]) for t in tr)
    train = [t for t in tr if t["date"] < R6.SPLIT]; unseen = [t for t in tr if t["date"] >= R6.SPLIT]
    st, su = R6.stats(train), R6.stats(unseen)
    per = df.groupby("date").size()
    print(f"{label:44s} trades {s['trades']:4d} ({per.mean():.2f}/day, max {per.max()})  net ₹{s['net']:+8,.0f}  train ₹{st['net']:+8,.0f} PF {st['pf']}  unseen ₹{su['net']:+7,.0f} PF {su['pf']}  "
          f"maxDD ₹{s['max_dd']:,.0f}  win {s['win']}%  charges ₹{ch:,.0f}")
    return {"label": label, "trades": s["trades"], "per_day": round(float(per.mean()), 2), "net": s["net"], "train": st, "unseen": su, "max_dd": s["max_dd"], "win": s["win"], "charges": round(ch)}

res = []
res.append(stats(book(1, True), "baseline: 1 trade per rule per day"))
for cap in (2, 4, 6):
    res.append(stats(book(cap, False), f"up to {cap}/day, never the same option twice"))
    res.append(stats(book(cap, True), f"up to {cap}/day, re-entry allowed"))
res.append(stats(book(6, False, max_losses=2), "up to 6/day, same-option rule, stop after 2 losers"))
# which attempt makes the money?
tr = book(6, False); df = pd.DataFrame(tr); df["n"] = df.groupby(["sym", "date"]).cumcount() + 1
print("\nby attempt number (up to 6/day, same-option rule):")
print(df.groupby("n").pnl.agg(["count", "sum", "mean"]).round(0).to_string())
print("\nby exit reason of the trade BEFORE a re-entry:")
df["prev_why"] = df.groupby(["sym", "date"]).why.shift(1)
print(df[df.n > 1].groupby("prev_why").pnl.agg(["count", "sum", "mean"]).round(0).to_string())
json.dump({"results": res, "by_attempt": df.groupby("n").pnl.agg(["count", "sum", "mean"]).round(0).to_dict()}, open("more_trades.json", "w"), indent=1, default=str)
