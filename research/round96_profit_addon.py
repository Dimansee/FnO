"""Round 96 (10 Nov 2026): add 10-15 lots once a trade is Rs 1,500-2,500 in profit, with their stop at breakeven or in profit.

User: "what if we add more lot about 10-15 more lot after the profit of 1500-2500 and their sl will be breakeven or in some profit".
Earlier: pyramiding on re-entry deepened the fall to -57% (r14a, old rules); sizing up the next signal while a trade was +2R
lowered 2023-24 and the full period (r93 B). This is the intraday version: add to the SAME open trade.
Method (today's Autopilot, rounds 88 + 90, Outcomes replay at Rs 2L and Rs 20k):
  - every taken trade's option is re-marked on each 5-min bar with the app's own pricing (Black-Scholes at the trade's implied
    vol backed out of its entry price, the app's extra intraday decay, 0.5% slippage per fill) - checked against the stored exits
  - trigger: open profit of the trade (its lots) >= Rs 1,500 / 2,000 / 2,500 at a bar close -> buy N more lots (10 / 15) at that
    close, limited by free cash (equity minus the premium already paid)
  - stop for the added lots: 'BE' close them if the whole position's open profit falls back to Rs 0; 'lock' ... to +Rs 500;
    'own' close them if the added lots' own price falls to their entry. Otherwise they exit with the trade.
  - charges per order (app formula) on the add-on.
Bar closes only (an intrabar stop would fill worse). Add-on profits are not re-invested in sizing (cash check only).
Usage: python research/round96_profit_addon.py
"""
import json, math, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
from datetime import datetime, timedelta
import numpy as np, pandas as pd
import outcomes as O
from fno import portfolio_bt as PB, indicators as I, pricing as PR, market as D
from scipy.optimize import brentq

SLIP = 0.005
PER = {"2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", "2026-10-16"), "full": ("2023-01-02", "2026-10-16")}
_C = {}


def otm_of(t):
    t = str(t)
    n = int(t.split()[0]) if t[0].isdigit() else 0
    return -n if "ITM" in t else (n if "OTM" in t else 0)


def candles(sym):
    if sym not in _C:
        c = O.candles(sym)
        _C[sym] = (c, c.index.values, c.close.values)
    return _C[sym]


def attach(R, frames):
    """The replay output has no strike type / lot: take them from whichever source frame (1 OTM / 1 ITM / 2 OTM) has the
    same signal with the nearest entry premium."""
    look = {}
    for F in frames:
        if F is None:
            continue
        for r in F.itertuples(index=False):
            look.setdefault((r.sym, r.date, r.entry_time, r.strategy), []).append((r.type, r.prem_in, r.lot))
    ty, lt = [], []
    for r in R.itertuples(index=False):
        c = look.get((r.sym, r.date, r.entry_time, r.strategy), [])
        if not c:
            ty.append(None); lt.append(None); continue
        best = min(c, key=lambda x: abs(x[1] - r.prem_in))
        ty.append(best[0]); lt.append(best[2])
    R = R.copy(); R["type"] = ty; R["lot"] = lt
    return R[R["type"].notna()].reset_index(drop=True)


def paths(R):
    """For each taken trade: list of (bar_ts, mid_mark) between entry and exit, and the stored exit check."""
    out, chk = {}, []
    meta = {s: D.instrument(s) for s in R.sym.unique()}
    for i, r in R.iterrows():
        m = meta[r.sym]; step = m["step"]
        d = datetime.strptime(r.date, "%Y-%m-%d").date()
        exp = I.pick_trading_expiry(I.demo_expiries(m["expiry"], d, 3, m.get("exchange", "NSE")), m["expiry"], d)
        if exp is None:
            continue
        opt = "CE" if r.side == "CALL" else "PE"; sign = 1 if opt == "CE" else -1
        k = round(r.spot_in / step) * step + sign * otm_of(r.type) * step
        t_in = datetime.strptime(f"{r.date} {r.entry_time}", "%Y-%m-%d %H:%M")
        t_out = datetime.strptime(f"{r.date} {r.exit_time}", "%Y-%m-%d %H:%M")
        mid_in = r.prem_in / (1 + SLIP)
        T0 = max(I.years_to_expiry(exp, t_in), 1 / (365 * 24 * 12))
        try:
            iv = brentq(lambda v: I.bs_price(r.spot_in, k, T0, v, opt) - mid_in, 0.01, 3.0)
        except Exception:
            continue
        c, ts, cl = candles(r.sym)
        a = ts.searchsorted(np.datetime64(t_in)); b = ts.searchsorted(np.datetime64(t_out))
        dte = (exp - d).days
        marks = []
        for j in range(a, b):
            when = pd.Timestamp(ts[j]).to_pydatetime() + timedelta(minutes=5)
            if when > t_out:
                break
            raw = I.bs_price(float(cl[j]), k, max(I.years_to_expiry(exp, when), 1 / (365 * 24 * 12)), iv, opt)
            marks.append((when, PR.real_adjust(mid_in, raw, dte, (when - t_in).total_seconds() / 3600)))
        out[i] = marks
        if marks:
            chk.append((marks[-1][1] * (1 - SLIP), r.prem_out))
    return out, chk


def overlay(R, P, cap, trig, n_add, stop):
    """Rupee P&L of add-ons for each taken trade, cash-limited; returns (total by date frame, stats)."""
    rows = []
    for i, r in R.iterrows():
        marks = P.get(i)
        if not marks:
            continue
        lots, lot = int(r.lots), int(r.lot)
        eq_before = float(r.equity - r.pnl)
        free = eq_before - r.prem_in * lot * lots
        added, x = None, None
        for when, mid in marks:
            par = (mid * (1 - SLIP) - r.prem_in) * lot * lots
            if added is None:
                if par >= trig:
                    p_add = mid * (1 + SLIP)
                    want = n_add(lots, eq_before) if callable(n_add) else n_add
                    q = min(want, int(max(free, 0) // (p_add * lot)))
                    if q <= 0:
                        break
                    added = (when, p_add, q)
                continue
            _, p_add, q = added
            addp = (mid * (1 - SLIP) - p_add) * lot * q
            hit = (stop == "BE" and par + addp <= 0) or (stop == "lock" and par + addp <= 500) or (stop == "own" and mid * (1 - SLIP) <= p_add)
            if hit:
                x = mid * (1 - SLIP); break
        if added is None:
            continue
        _, p_add, q = added
        stopped = x is not None
        x = x if stopped else r.prem_out
        pnl = (x - p_add) * lot * q - I.charges(p_add, x, lot * q)
        rows.append({"date": r.date, "sym": r.sym, "lots": q, "pnl": pnl, "stopped": stopped})
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
            R = R[R["skipped"].isna()].reset_index(drop=True) if "skipped" in R else R
            R = attach(R, (T, A, H))
            P, chk = paths(R)
            if pn == "full":
                e = np.array(chk)
                print(f"price check Rs {cap:,}: re-marked exit vs stored exit, median abs diff {np.median(np.abs(e[:, 0] - e[:, 1]) / e[:, 1]):.2%}, "
                      f"corr {np.corrcoef(e[:, 0], e[:, 1])[0, 1]:.3f}", flush=True)
            row = {"today": {"final": round(s0["final"]), "dd": s0["max_dd_pct"], "trades": int(len(R))}}
            for trig in (1500, 2000, 2500):
                for n_add in (10, 15):
                    for stop in ("BE", "lock", "own"):
                        X = overlay(R, P, cap, trig, n_add, stop)
                        tot = float(X.pnl.sum()) if len(X) else 0.0
                        # equity path with add-ons (not re-invested): today's curve + cumulative add-on P&L by date
                        eq = R.groupby("date").equity.last()
                        add = X.groupby("date").pnl.sum().reindex(eq.index).fillna(0).cumsum() if len(X) else 0
                        curve = eq + add
                        dd = float((curve / curve.cummax() - 1).min() * 100)
                        row[f"{trig}|{n_add}|{stop}"] = {"final": round(s0["final"] + tot), "addon_total": round(tot), "n": int(len(X)),
                                                         "avg_lots": round(float(X.lots.mean()), 1) if len(X) else 0,
                                                         "win": round(float((X.pnl > 0).mean()), 3) if len(X) else None,
                                                         "worst": round(float(X.pnl.min())) if len(X) else 0, "dd": round(dd, 1),
                                                         "stopped": round(float(X.stopped.mean()), 3) if len(X) else None}
            res[f"{cap}|{pn}"] = row
            best = sorted(((k2, v) for k2, v in row.items() if k2 != "today"), key=lambda kv: -kv[1]["final"])[:4]
            worst = sorted(((k2, v) for k2, v in row.items() if k2 != "today"), key=lambda kv: kv[1]["final"])[:2]
            print(f"Rs {cap:,} {pn}: today {row['today']['final'] / 1e5:.2f}L ({row['today']['dd']}%) | best " +
                  " ; ".join(f"{k2} {v['final'] / 1e5:.2f}L add-on {v['addon_total']:+,} n={v['n']} lots~{v['avg_lots']} win {v['win']} dd {v['dd']}%" for k2, v in best) +
                  " | worst " + " ; ".join(f"{k2} {v['final'] / 1e5:.2f}L" for k2, v in worst), flush=True)
    json.dump(res, open(os.path.join(HERE, "round96_results.json"), "w"), indent=1)


if __name__ == "__main__" and "--small" not in sys.argv:
    main()


SIZES = {"+50% of its lots": lambda L, e: max(1, int(round(0.5 * L))), "+100% of its lots": lambda L, e: L,
         "+3 lots": 3, "+5 lots": 5,
         "+15 lots only from Rs 5L": lambda L, e: 15 if e >= 500_000 else 0,
         "+100% of its lots only from Rs 5L": lambda L, e: L if e >= 500_000 else 0}


def main_small():
    q0 = dict(O.B0)
    k, ak, hk = PB._trade_key(q0), PB._alt_key(q0), PB._hi_key(q0)
    T = pd.concat([O.trades(k, s) for s in q0["indices"]], ignore_index=True)
    A = pd.concat([O.trades(ak, s) for s in q0["indices"]], ignore_index=True) if ak else None
    H = pd.concat([O.trades(hk, s) for s in q0["indices"]], ignore_index=True) if hk else None
    res = json.load(open(os.path.join(HERE, "round96_results.json")))
    for cap in (200_000, 20_000):
        for pn, (a, b) in PER.items():
            cut = lambda X: X[(X.date >= a) & (X.date <= b)] if X is not None else None  # noqa: E731
            q = {**q0, "capital": cap}
            R = PB.replay(cut(T), cap, q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"],
                          q["dd_half"], cut(A), cpr=q.get("cpr_size", False), hi=cut(H), channel=q.get("channel", True))
            s0 = PB._summary(R, cap)
            R = R[R["skipped"].isna()].reset_index(drop=True); R = attach(R, (T, A, H))
            P, _ = paths(R)
            row = res.setdefault(f"{cap}|{pn}", {})
            for trig in (1500, 2500):
                for nm, fn in SIZES.items():
                    for stop in ("BE", "lock"):
                        X = overlay(R, P, cap, trig, fn, stop)
                        tot = float(X.pnl.sum()) if len(X) else 0.0
                        eq = R.groupby("date").equity.last()
                        add = X.groupby("date").pnl.sum().reindex(eq.index).fillna(0).cumsum() if len(X) else 0
                        curve = eq + add
                        dd = float((curve / curve.cummax() - 1).min() * 100)
                        row[f"{trig}|{nm}|{stop}"] = {"final": round(s0["final"] + tot), "addon_total": round(tot), "n": int(len(X)), "dd": round(dd, 1)}
                        print(f"Rs {cap:,} {pn:<8} {trig} {nm:<36} {stop:<4} final {(s0['final'] + tot) / 1e5:7.2f}L (today {s0['final'] / 1e5:6.2f}L) "
                              f"dd {dd:+.1f}% (today {s0['max_dd_pct']}%) n={len(X)}", flush=True)
    json.dump(res, open(os.path.join(HERE, "round96_results.json"), "w"), indent=1)


if __name__ == "__main__" and "--small" in sys.argv:
    main_small()
