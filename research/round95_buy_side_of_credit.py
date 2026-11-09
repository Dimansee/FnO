"""Round 95 (9 Nov 2026): can the credit-strategy finding (round 94) be used for option BUYING?
Round 94 (selling, real NSE prices): bear call spreads won, bull put spreads lost. The buyer's mirror: buying put spreads should
gain and buying call spreads should lose - but the buyer pays costs too. The user keeps the app buy-only, so here the same
real prices, same strikes and timing, from the buyer's side:
  LP   buy the OTM put (k x expected move below), hold to expiry          - pure buying
  LC   buy the OTM call (k x EM above), hold to expiry                   - pure buying
  BPD  bear put debit spread: buy that put, sell a lower put (w below)   - buying with a sold wing (needs hedge margin)
  BCD  bull call debit spread: buy that call, sell a higher call
Enter at the close 1-4 trading days before the nearest expiry, k = 0.5 / 1.0 / 1.5, wing 0.5 / 1 / 2%, hold to expiry, no stop.
Fills 2% (min 0.25 pt) worse, app charges, STT on exercise. Also: Autopilot's own CALL vs PUT result per trade (does the same
call/put asymmetry show in today's intraday buying?).
Usage: python research/round95_buy_side_of_credit.py
"""
import itertools, json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round94_credit_spreads as M
from fno import indicators as I


def long_trade(sym, D, T, E, side, spot, em, k, w):
    px, op, days, near, dopen, dclose = D
    st, lot = M.STEP[sym], M.LOT[sym]
    opt = "PE" if side == "P" else "CE"
    kb = np.floor((spot - k * em) / st) * st if side == "P" else np.ceil((spot + k * em) / st) * st
    pb = px.get((T, E, kb, opt))
    if pb is None or pb <= 0.5:
        return None
    se = dclose.get(E)
    if se is None:
        return None
    iv = lambda K: max(K - se, 0) if side == "P" else max(se - K, 0)  # noqa: E731
    cost = M.buy_fill(pb)
    pnl = (iv(kb) - cost) * lot - I.charges(cost, 0, lot, orders=1) - 0.00125 * iv(kb) * lot
    risk = cost * lot
    if w:
        kw = kb - max(st, round(w * spot / st) * st) if side == "P" else kb + max(st, round(w * spot / st) * st)
        pw = px.get((T, E, kw, opt))
        if pw is None:
            return None
        got = M.sell_fill(pw)
        pnl += (got - iv(kw)) * lot - I.charges(0, got, lot, orders=1)
        risk = (cost - got) * lot
    return {"pnl": pnl, "maxloss": risk}


def run(sym, D, kind, N, k, w):
    px, op, days, near, dopen, dclose = D
    idx = {d: i for i, d in enumerate(days)}
    out = []
    for E in sorted({e[0] for e in near.values() if e}):
        if E not in idx or idx[E] - N < 0:
            continue
        T = days[idx[E] - N]
        if not near.get(T) or near[T][0] != E or T not in M.VIXD.index or T not in dclose:
            continue
        spot = dclose[T]; em = spot * M.VIXD[T] / 100 * np.sqrt(N / 252)
        r = long_trade(sym, D, T, E, "P" if kind in ("LP", "BPD") else "C", spot, em, k, w if kind in ("BPD", "BCD") else 0)
        if r:
            out.append({"date": str(T), "pnl": r["pnl"], "maxloss": r["maxloss"], "stopped": False})
    return pd.DataFrame(out)


def autopilot_call_put():
    import outcomes as O
    from fno import portfolio_bt as PB
    k = PB._trade_key(dict(O.B0))
    T = pd.concat([O.trades(k, s).assign(sym=s) for s in O.B0["indices"]], ignore_index=True)
    T["per_lot"] = T.pnl / T.lots.clip(lower=1)
    g = T.groupby(["side", T.date.str[:4]]).per_lot.agg(["count", "mean"]).round(0)
    h = T.assign(half=np.where(T.date < "2025", "2023-24", "2025-26")).groupby(["sym", "side", "half"]).per_lot.agg(["count", "mean"]).round(0)
    return g, h


def main():
    res = {}
    for sym in ("NIFTY", "BANKNIFTY"):
        D = M.load(sym)
        for kind, N, kk in itertools.product(("LP", "LC", "BPD", "BCD"), (1, 2, 3, 4), (0.5, 1.0, 1.5)):
            for w in ((0,) if kind in ("LP", "LC") else (0.005, 0.01, 0.02)):
                s = M.stats(run(sym, D, kind, N, kk, w))
                key = f"{sym}|{kind}|N{N}|k{kk}|w{w}"
                res[key] = {"stats": s, "pass": M.ok(s)}
    J = pd.DataFrame({k: {"pass": v["pass"], **{c: v["stats"].get(c) for c in ("n", "total", "a", "b", "ex_top10pct", "pf", "win", "worst", "avg_maxloss")}}
                      for k, v in res.items()}).T
    J["sym"] = [k.split("|")[0] for k in J.index]; J["kind"] = [k.split("|")[1] for k in J.index]
    for c in ("total", "a", "b", "ex_top10pct"):
        J[c] = J[c].astype(float)
    print(J.groupby(["sym", "kind"]).agg(passed=("pass", "sum"), n=("pass", "size"), med_total=("total", "median"),
                                         med_a=("a", "median"), med_b=("b", "median"), med_ex10=("ex_top10pct", "median")).round(0).to_string())
    best = J[J["pass"] == True].sort_values("total", ascending=False).head(10)  # noqa: E712
    print("\nPASSING (best 10):\n", best[["n", "win", "total", "a", "b", "ex_top10pct", "pf", "worst", "avg_maxloss"]].to_string())
    g, h = autopilot_call_put()
    print("\nAutopilot per lot by side and year:\n", g.to_string(), "\n", h.to_string())
    out = {"grid": res, "autopilot_side_year": {f"{a}|{b}": v for (a, b), v in g.to_dict("index").items()},
           "autopilot_side_half": {f"{a}|{b}|{c}": v for (a, b, c), v in h.to_dict("index").items()}}
    json.dump(out, open(os.path.join(HERE, "round95_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
