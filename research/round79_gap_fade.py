"""Round 79: gap fade (gap fill) as an entry - one of the few classic intraday index ideas not yet in the log.

Day opens with a gap of >= G% vs yesterday's close (G = 0.3 / 0.5 / 0.8). Fade it only after the open fails:
  wait  E = 09:30 or 09:45: price is back below the opening price (gap up; mirror for gap down) and below the
        09:15-E low (variant 'brk'), or just below the open (variant 'open')
  buy   the ATM option against the gap at the next 5-min bar; stop = the day's high so far (gap up); target = yesterday's
        close (the gap filled) or hold to 15:15
Priced like rounds 72/75 (app option model, 0.25% slippage, charges, Rs 2,000 risk), random-direction twin, best version
picked on 2023-24 only, then added to today's Autopilot in the Outcomes replay (Rs 2L and Rs 20k).
Usage: python research/round79_gap_fade.py
"""
import itertools, json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round75_trend_continuation as R75
import round73_regime_tilts as R73
import outcomes as O
from fno import portfolio_bt as PB

SYMS = R75.SYMS
GRID = dict(g=[0.003, 0.005, 0.008], wait=[570, 585], trig=["open", "brk"], exit=["fill", "close"])


def signals(sym, g, wait, trig):
    df = R75.frame(sym)
    o, h, l, c, hm = (df[k].values for k in ("open", "high", "low", "close", "hm"))
    out, pc = [], None
    for a, b in R75.days(df):
        if b - a < 40 or hm[a] != 555 or pc is None:
            pc = c[b - 1]
            continue
        last = min(a + int(np.searchsorted(hm[a:b], 915, side="left")) - 1, b - 1)
        gap = o[a] / pc - 1
        if abs(gap) >= g:
            s = -1 if gap > 0 else 1                       # trade against the gap
            w = a + int(np.searchsorted(hm[a:b], wait, side="left"))
            for i in range(w, min(last, a + 30)):          # until about 11:45
                lvl = o[a] if trig == "open" else (l[a:w].min() if s == -1 else h[a:w].max())
                if s * (c[i] - lvl) > 0:
                    stop = h[a:i + 1].max() if s == -1 else l[a:i + 1].min()
                    if s * (o[i + 1] - stop) > 0 and s * (pc - o[i + 1]) > 0:
                        out.append((i + 1, s, stop, last, pc))
                    break
        pc = c[b - 1]
    return out


def build(sym, sig, ex, twin=None):
    rows = []
    df = R75.frame(sym)
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    rng = np.random.default_rng(twin) if twin is not None else None
    for j, s, stop, last, pc in sig:
        e = o[j]
        tgt = pc
        if rng is not None and int(rng.choice([1, -1])) != s:
            s, stop, tgt = -s, e - (stop - e), e - (pc - e)
        k, xp = last, c[last]
        for q in range(j, last + 1):
            if (s == 1 and l[q] <= stop) or (s == -1 and h[q] >= stop):
                k, xp = q, (stop if q == j else (min(o[q], stop) if s == 1 else max(o[q], stop))); break
            if ex == "fill" and ((s == 1 and h[q] >= tgt) or (s == -1 and l[q] <= tgt)):
                k, xp = q, tgt; break
        rows.append({"i": j, "j": k, "xs": xp, "side": s, "sl": stop, "e": e, "R": s * (xp - e) / abs(e - stop),
                     "pts": s * (xp - e) * 23000 / e, "date": df.index[j].strftime("%Y-%m-%d"), "t_in": df.index[j].strftime("%H:%M"),
                     "t_out": (df.index[k] + pd.Timedelta(minutes=5)).strftime("%H:%M")})
    T = pd.DataFrame(rows)
    if T.empty:
        return T
    M = R75.M
    p_in, p_out, risk, _ = M.R34.price(sym, {"idx": df.index, "c": o}, T, M.vix())
    pi, po = p_in * (1 + M.SLIP), np.maximum(p_out * (1 - M.SLIP), 0.05)
    rk = np.maximum(risk * (1 + M.SLIP), pi * 0.05)
    lot = M.R34.D.lot_size(sym)
    qty = np.clip(np.floor(M.RISK_RS / (rk * lot)), 1, 40) * lot
    T["rs"] = (po - pi) * qty - (40 + 0.0015 * po * qty + 0.0005 * (pi + po) * qty)
    T["prem_in"], T["prem_out"], T["risk_lot"], T["lot"], T["sym"] = pi, po, rk * lot, lot, sym
    return T


def main():
    res = {"grid": []}
    best = None
    for g, wait, trig, ex in itertools.product(*GRID.values()):
        T = pd.concat([build(s, signals(s, g, wait, trig), ex) for s in SYMS], ignore_index=True)
        T = T[T.date <= "2026-10-30"]
        W = pd.concat([build(s, signals(s, g, wait, trig), ex, twin=7) for s in SYMS], ignore_index=True)
        W = W[W.date <= "2026-10-30"]
        a, t = R75.summ(T), R75.summ(W)
        res["grid"].append({"g": g, "wait": wait, "trig": trig, "exit": ex, "all": a, "twin": t})
        print(f"gap{g:.1%} wait{wait} {trig:<4} {ex:<5} n{a['n']:>4} win{a.get('win', 0):.2f} R{a.get('avgR', 0):+.3f} Rs/tr{a.get('rs_trade', 0):+6.0f} "
              f"23-24 {a.get('a', 0):+9.0f} 25-26 {a.get('b', 0):+9.0f} yrs {a.get('years')} | twin R{t.get('avgR', 0):+.3f}", flush=True)
        if a.get("n", 0) >= 60 and a["a"] > 0 and (best is None or a["a"] > best[1]["all"]["a"]):
            best = (T, res["grid"][-1])
    if best:
        T, r = best
        print("\nchosen on 2023-24:", {k: r[k] for k in ("g", "wait", "trig", "exit")}, "2025-26:", r["all"]["b"])
        res["chosen"] = {k: r[k] for k in ("g", "wait", "trig", "exit")}
        new = R75.autopilot_rows(T, "Gap fade (r79)")
        B0 = dict(O.B0)
        k, ak = PB._trade_key(B0), PB._alt_key(B0)
        base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
        alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
        P = {**R73.PERIODS, "2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", "2026-10-30")}
        for cap in (200_000, 20_000):
            ref = {p: R73.run(base.assign(xm=1.0), alt.assign(xm=1.0) if alt is not None else None, cap, a_, b_) for p, (a_, b_) in P.items()}
            Tn = pd.concat([base, new], ignore_index=True).assign(xm=1.0)
            An = pd.concat([alt, new], ignore_index=True).assign(xm=1.0) if alt is not None else None
            add = {p: R73.run(Tn, An, cap, a_, b_) for p, (a_, b_) in P.items()}
            res[f"replay_{cap}"] = {"today": ref, "plus_r79": add}
            for lab, rr in (("Autopilot today", ref), ("+ gap fade", add)):
                print(f"  Rs {cap:,} {lab:<16} " + " ".join(f"{p}:{rr[p]['final'] / 1e5:6.2f}L" for p in P) + f" fall {rr['full']['dd']:+.1f}%", flush=True)
    else:
        print("\nno version made money in 2023-24")
    json.dump(res, open(os.path.join(HERE, "round79_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
