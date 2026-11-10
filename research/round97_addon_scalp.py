"""Round 97 (10 Nov 2026): add 15 / 20 / 25 lots once a trade is in profit and scalp them: target 5-10 premium points,
stop 2-3 premium points.

User: "add 20-25 lot or 15 lot around and make target of 10 points only for those add on lots and their SL will be 2-3 points,
minimum target point will be 5". Follows round 96 (add-ons held with the trade: more profit, falls -51% to -69%).
Method: today's Autopilot (rounds 88 + 90), Outcomes replay at Rs 2L and Rs 20k, Jan 2023 - Oct 2026.
  - trigger (as round 96): the trade's open profit reaches Rs 1,500 or Rs 2,500 at a 5-min close
  - add N = 15 / 20 / 25 lots at that close (cash-limited), app pricing, 0.5% slippage per fill, app charges
  - add-on exit on 1-MIN index candles: option re-priced at each minute's index high and low (implied vol from the trade's
    entry price); target +5 / +10 premium points, stop -2 / -3 points; stop counted first when both hit in one minute;
    otherwise the add-on exits with the trade
  - stress: 1 extra point lost on every stop fill (stops slip on fast candles)
  - also only from Rs 5L equity
Add-on profits are not re-invested in sizing (cash check only).
Usage: python research/round97_addon_scalp.py
"""
import itertools, json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
from datetime import datetime, timedelta
import numpy as np, pandas as pd
import outcomes as O
import round34 as R34
import round96_profit_addon as R96
from fno import portfolio_bt as PB, indicators as I, pricing as PR, market as D
from scipy.optimize import brentq

SLIP = 0.005
PER = R96.PER
_M1 = {}


def m1(sym):
    if sym not in _M1:
        a = R34.m1(sym)
        _M1[sym] = (a.index.values, a.high.values, a.low.values, a.close.values)
    return _M1[sym]


def setup(R):
    """Per taken trade: pricing closure + 5-min marks (trigger) + 1-min (hi, lo) premium path."""
    out = {}
    meta = {s: D.instrument(s) for s in R.sym.unique()}
    for i, r in R.iterrows():
        m = meta[r.sym]; step = m["step"]
        d = datetime.strptime(r.date, "%Y-%m-%d").date()
        exp = I.pick_trading_expiry(I.demo_expiries(m["expiry"], d, 3, m.get("exchange", "NSE")), m["expiry"], d)
        if exp is None:
            continue
        opt = "CE" if r.side == "CALL" else "PE"; sign = 1 if opt == "CE" else -1
        k = round(r.spot_in / step) * step + sign * R96.otm_of(r.type) * step
        t_in = datetime.strptime(f"{r.date} {r.entry_time}", "%Y-%m-%d %H:%M")
        t_out = datetime.strptime(f"{r.date} {r.exit_time}", "%Y-%m-%d %H:%M")
        mid_in = r.prem_in / (1 + SLIP)
        T0 = max(I.years_to_expiry(exp, t_in), 1 / (365 * 24 * 12))
        try:
            iv = brentq(lambda v: I.bs_price(r.spot_in, k, T0, v, opt) - mid_in, 0.01, 3.0)
        except Exception:
            continue
        dte = (exp - d).days

        def mark(spot, when, k=k, iv=iv, opt=opt, exp=exp, mid_in=mid_in, t_in=t_in, dte=dte):
            raw = I.bs_price(spot, k, max(I.years_to_expiry(exp, when), 1 / (365 * 24 * 12)), iv, opt)
            return PR.real_adjust(mid_in, raw, dte, (when - t_in).total_seconds() / 3600)

        ts, hi, lo, cl = m1(r.sym)
        a = ts.searchsorted(np.datetime64(t_in)); b = ts.searchsorted(np.datetime64(t_out))
        mins = []
        for j in range(a, b):
            when = pd.Timestamp(ts[j]).to_pydatetime() + timedelta(minutes=1)
            if when > t_out:
                break
            ph, pl = mark(hi[j], when), mark(lo[j], when)
            mins.append((when, max(ph, pl), min(ph, pl), mark(cl[j], when)))
        out[i] = (mins, opt)
    return out


def overlay(R, S, trig, n_add, tp, sl, extra_stop=0.0, gate=0):
    rows = []
    for i, r in R.iterrows():
        if i not in S:
            continue
        mins, opt = S[i]
        lots, lot = int(r.lots), int(r.lot)
        eq_before = float(r.equity - r.pnl)
        if eq_before < gate:
            continue
        free = eq_before - r.prem_in * lot * lots
        trig_at = None
        for when, mh, ml, mc in mins:                  # trigger on minute closes that fall on 5-min marks
            if when.minute % 5 == 0 and (mc * (1 - SLIP) - r.prem_in) * lot * lots >= trig:
                trig_at = when; p_add = mc * (1 + SLIP); break
        if trig_at is None:
            continue
        q = min(n_add, int(max(free, 0) // (p_add * lot)))
        if q <= 0:
            continue
        x, how = None, "with trade"
        for when, mh, ml, mc in mins:
            if when <= trig_at:
                continue
            if ml * (1 - SLIP) <= p_add - sl:
                x, how = p_add - sl - extra_stop, "stop"; break
            if mh * (1 - SLIP) >= p_add + tp:
                x, how = p_add + tp, "target"; break
        if x is None:
            x = r.prem_out
        pnl = (x - p_add) * lot * q - I.charges(p_add, x, lot * q)
        rows.append({"date": r.date, "sym": r.sym, "lots": q, "pnl": pnl, "how": how})
    return pd.DataFrame(rows)


def main():
    q0 = dict(O.B0)
    k, ak, hk = PB._trade_key(q0), PB._alt_key(q0), PB._hi_key(q0)
    T = pd.concat([O.trades(k, s) for s in q0["indices"]], ignore_index=True)
    A = pd.concat([O.trades(ak, s) for s in q0["indices"]], ignore_index=True) if ak else None
    H = pd.concat([O.trades(hk, s) for s in q0["indices"]], ignore_index=True) if hk else None
    res = {}
    for cap in (200_000, 20_000):
        for pn, (a, b) in PER.items():
            cut = lambda X: X[(X.date >= a) & (X.date <= b)] if X is not None else None  # noqa: E731
            q = {**q0, "capital": cap}
            R = PB.replay(cut(T), cap, q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"],
                          q["dd_half"], cut(A), cpr=q.get("cpr_size", False), hi=cut(H), channel=q.get("channel", True))
            s0 = PB._summary(R, cap)
            R = R[R["skipped"].isna()].reset_index(drop=True); R = R96.attach(R, (T, A, H))
            S = setup(R)
            eq = R.groupby("date").equity.last()
            row = {"today": {"final": round(s0["final"]), "dd": s0["max_dd_pct"]}}
            for trig, n_add, tp, sl, xs, gate in itertools.product((1500, 2500), (15, 20, 25), (5, 10), (2, 3), (0.0, 1.0), (0, 500_000)):
                X = overlay(R, S, trig, n_add, tp, sl, xs, gate)
                tot = float(X.pnl.sum()) if len(X) else 0.0
                add = X.groupby("date").pnl.sum().reindex(eq.index).fillna(0).cumsum() if len(X) else 0
                curve = eq + add
                dd = float((curve / curve.cummax() - 1).min() * 100)
                hw = X.how.value_counts(normalize=True).round(3).to_dict() if len(X) else {}
                row[f"{trig}|{n_add}|tp{tp}|sl{sl}|slip{xs}|gate{gate}"] = {
                    "final": round(s0["final"] + tot), "addon": round(tot), "n": int(len(X)), "dd": round(dd, 1),
                    "avg_lots": round(float(X.lots.mean()), 1) if len(X) else 0, "per_trade": round(tot / len(X)) if len(X) else 0,
                    "target": hw.get("target"), "stop": hw.get("stop"), "with_trade": hw.get("with trade")}
            res[f"{cap}|{pn}"] = row
            top = sorted(((k2, v) for k2, v in row.items() if k2 != "today"), key=lambda kv: -kv[1]["final"])[:5]
            print(f"Rs {cap:,} {pn}: today {row['today']['final'] / 1e5:.2f}L ({row['today']['dd']}%) | " +
                  " ; ".join(f"{k2} {v['final'] / 1e5:.2f}L add {v['addon']:+,} n={v['n']} lots~{v['avg_lots']} /tr {v['per_trade']:+,} "
                             f"tgt {v['target']} stop {v['stop']} dd {v['dd']}%" for k2, v in top), flush=True)
            # one fixed version per line for the write-up: the user's own numbers
            for k2 in ("1500|20|tp10|sl3|slip0.0|gate0", "1500|20|tp10|sl3|slip1.0|gate0", "1500|15|tp5|sl2|slip0.0|gate0",
                       "1500|25|tp10|sl2|slip0.0|gate0", "2500|20|tp10|sl3|slip0.0|gate500000"):
                v = row[k2]
                print(f"   {k2:<36} final {v['final'] / 1e5:7.2f}L add {v['addon']:+,} n={v['n']} /tr {v['per_trade']:+,} "
                      f"target {v['target']} stop {v['stop']} with-trade {v['with_trade']} dd {v['dd']}%", flush=True)
    json.dump(res, open(os.path.join(HERE, "round97_results.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
