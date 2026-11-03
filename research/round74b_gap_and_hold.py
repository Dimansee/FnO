"""Round 74b: gap-and-hold above Camarilla H4 / below L4 - the 2 Nov 2026 situation Autopilot has no rule for.

2 Nov: Nifty opened +0.89% at 23,259, above H4 (23,170); Sensex +1.04% above H4. Camarilla needs a break of H4 from below
and the noise band was never reached, so no rule fired while Nifty closed +1.7%.
Rule (calls; puts mirror below L4):
  - the day opens above yesterday's Camarilla H4 and the gap is >= g (0.3% / 0.5%)
  - by the check time (10:15 or 10:45) price has held: no 5-min close below the opening 15-min low (gap not filled),
    and the last close is above the open (or just above the opening-range low: 'hold' variants)
  - enter next bar; stop = opening 15-min low (or H4); exit 15:15, or trail under the prior 5-min swing low ('trail')
One trade per index per day. Nifty, Bank Nifty, Sensex, Jan 2023 - Oct 2026, ATM option priced like the app, Rs 2,000 risk,
random-direction twin; then added to today's Autopilot in the Outcomes replay (Rs 2L and Rs 25k).
Usage: python research/round74b_gap_and_hold.py [data_dir]
"""
import itertools, json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round72_opening_candle_retest as M          # 5m loader, option pricing (build), offline lots
import round73_regime_tilts as R73
import outcomes as O
from fno import portfolio_bt as PB

SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]


def signals(sym, gap=0.003, check=615, hold="above_open", stop_kind="or_low"):
    df = M.load(sym)
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    d = df.index.normalize()
    hm = np.asarray(df.index.hour * 60 + df.index.minute)
    starts = np.flatnonzero(np.r_[True, d[1:] != d[:-1]])
    ends = np.r_[starts[1:], len(c)]
    out = []
    prev = None
    for a, b in zip(starts, ends):
        bars = np.arange(a, b)
        bars = bars[hm[bars] <= 15 * 60 + 10]
        if prev is not None and len(bars) > 20 and hm[bars[0]] == 555:
            H, L, C = prev
            h4, l4 = C + (H - L) * 1.1 / 2, C - (H - L) * 1.1 / 2
            op = o[a]
            for s in (1, -1):
                lvl = h4 if s == 1 else l4
                if not (s * (op - lvl) > 0 and s * (op / C - 1) >= gap):
                    continue
                orb = bars[:3]                                        # 09:15-09:30
                or_ext = l[orb].min() if s == 1 else h[orb].max()
                upto = bars[hm[bars] < check]
                if len(upto) < 4:
                    continue
                held = all(s * (c[k] - or_ext) > 0 for k in upto)
                last = upto[-1]
                if hold == "above_open":
                    held = held and s * (c[last] - op) > 0
                if not held:
                    continue
                j = last + 1
                if j > bars[-1]:
                    continue
                stop = or_ext if stop_kind == "or_low" else (lvl if s * (o[j] - lvl) > 0 else or_ext)
                if s * (o[j] - stop) <= 0:
                    continue
                out.append((j, s, stop, None, bars[-1]))
        day = slice(a, b)
        prev = (h[day].max(), l[day].min(), c[b - 1])
    return out


def manage(o, h, l, c, i0, s, stop, last, exit_kind):
    sl = stop
    for k in range(i0, last + 1):
        if (s == 1 and l[k] <= sl) or (s == -1 and h[k] >= sl):
            return k, (sl if k == i0 else (min(o[k], sl) if s == 1 else max(o[k], sl)))
        if exit_kind == "trail" and k >= i0 + 2:
            sw = l[k - 2:k + 1].min() if s == 1 else h[k - 2:k + 1].max()
            sl = max(sl, sw) if s == 1 else min(sl, sw)
    return last, c[last]


def build(sym, sig, exit_kind, twin=None):
    df = M.load(sym)
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    rng = np.random.default_rng(twin) if twin is not None else None
    rows = []
    for j, s, stop, _, last in sig:
        e = o[j]
        if rng is not None and int(rng.choice([1, -1])) != s:
            s, stop = -s, e - (stop - e)
        k, xp = manage(o, h, l, c, j, s, stop, last, exit_kind)
        rows.append({"i": j, "j": k, "xs": xp, "side": s, "sl": stop, "e": e, "R": s * (xp - e) / abs(e - stop), "pts": s * (xp - e),
                     "date": df.index[j].strftime("%Y-%m-%d"), "t_in": df.index[j].strftime("%H:%M"),
                     "t_out": (df.index[k] + pd.Timedelta(minutes=5)).strftime("%H:%M")})
    T = pd.DataFrame(rows)
    if T.empty:
        return T
    p_in, p_out, risk, _ = M.R34.price(sym, {"idx": df.index, "c": o}, T, M.vix())
    pi, po = p_in * (1 + M.SLIP), np.maximum(p_out * (1 - M.SLIP), 0.05)
    rk = np.maximum(risk * (1 + M.SLIP), pi * 0.05)
    lot = M.R34.D.lot_size(sym)
    lots = np.clip(np.floor(M.RISK_RS / (rk * lot)), 1, 40)
    qty = lots * lot
    T["rs"] = (po - pi) * qty - (40 + 0.0015 * po * qty + 0.0005 * (pi + po) * qty)
    T["prem_in"], T["prem_out"], T["risk_lot"], T["lot"], T["sym"] = pi, po, rk * lot, lot, sym
    return T


GRID = {"gap": [0.003, 0.005], "check": [615, 645], "hold": ["above_open", "or_only"], "stop_kind": ["or_low", "h4"], "exit": ["close", "trail"]}


def autopilot_rows(T):
    lot = T["lot"].values
    pi = T["prem_in"].values / (1 + M.SLIP) * 1.005
    po = np.maximum(T["prem_out"].values / (1 - M.SLIP) * 0.995, 0.05)
    out = pd.DataFrame({"date": T.date, "entry_time": T.t_in, "exit_time": [min(x, "15:20") for x in T.t_out], "sym": T.sym,
                        "strategy": "Gap and hold (r74b)", "side": np.where(T.side > 0, "CALL", "PUT"), "prem_in": pi, "prem_out": po,
                        "lot": lot, "risk_lot": np.maximum(T["risk_lot"].values / lot / (1 + M.SLIP) * 1.005, pi * 0.05) * lot,
                        "R": T["R"].values, "spot_in": T["e"].values, "spot_out": T["xs"].values, "reason": "r74b", "pieces": 1})
    out["risk_lot_capped"] = out["risk_lot"]
    parts = []
    for s, g in out.groupby("sym"):
        g = g.copy()
        g["cpr_w"] = PB.cpr_widths(O.candles(s)).reindex(g.date).values
        g["score"] = PB._scores(g, O.candles(s))
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def main():
    res = {"grid": []}
    keys = list(GRID)
    best = None
    for combo in itertools.product(*GRID.values()):
        p = dict(zip(keys, combo))
        ex = p.pop("exit")
        Ts, Tw = [], []
        for s in SYMS:
            sg = signals(s, **p)
            Ts.append(build(s, sg, ex)); Tw.append(build(s, sg, ex, twin=5))
        T = pd.concat(Ts, ignore_index=True); W = pd.concat(Tw, ignore_index=True)
        a, t = M.summ(T), M.summ(W)
        r = {**p, "exit": ex, "all": a, "twin": t}
        res["grid"].append(r)
        print(f"gap{p['gap']} chk{p['check']} {p['hold']:<10} {p['stop_kind']:<6} {ex:<5} n{a['n']:>4} win{a.get('win',0):.2f} R{a.get('avgR',0):+.2f} "
              f"Rs/tr{a.get('rs_trade',0):+6.0f} 23-24 {a.get('a',0):+9.0f} 25-26 {a.get('b',0):+9.0f} yrs {a.get('years')} | twin R{t.get('avgR',0):+.2f}", flush=True)
        if a.get("n", 0) >= 60 and a["a"] > 0 and (best is None or a["a"] > best[1]["all"]["a"]):    # chosen on 2023-24 only
            best = (T, r)
    if best:
        T, r = best
        print("\nchosen on 2023-24:", {k: r[k] for k in keys}, "2025-26:", r["all"]["b"])
        new = autopilot_rows(T)
        B0 = dict(O.B0)
        k, ak = PB._trade_key(B0), PB._alt_key(B0)
        base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
        alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
        for cap in (200_000, 25_000):
            ref = {p_: R73.run(base.assign(xm=1.0), alt.assign(xm=1.0) if alt is not None else None, cap, a_, b_) for p_, (a_, b_) in R73.PERIODS.items()}
            Tn = pd.concat([base, new], ignore_index=True).assign(xm=1.0)
            An = pd.concat([alt, new], ignore_index=True).assign(xm=1.0) if alt is not None else None
            add = {p_: R73.run(Tn, An, cap, a_, b_) for p_, (a_, b_) in R73.PERIODS.items()}
            res[f"replay_{cap}"] = {"today": ref, "plus_gap_hold": add}
            for lab, rr in (("Autopilot today", ref), ("+ gap and hold", add)):
                print(f"  Rs {cap:,} {lab:<18} " + " ".join(f"{p_}:{rr[p_]['final']/1e5:6.2f}L" for p_ in R73.PERIODS) + f" fall {rr['full']['dd']:+.1f}%")
    json.dump(res, open(os.path.join(HERE, "round74b_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
