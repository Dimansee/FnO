"""Round 91b: is the one lead from round 91 real? Previous-day b-shape profile -> short (bought PUT) at the thin area above value,
held to 15:15. It was picked from 420 index rows, so:
  1. Neighbours: profile (vol proxy / TPO) x trigger (touch / reject / accept) x stop (0.5 / 1.0 ATR5) x POC threshold
     (0.33 / 0.38 / 0.43) x value-area cap (0.60 / 0.66 / 0.72) x bins (32 / 48 / 64) = 324 versions, held to the close.
  2. Each index on its own; the same entries traded the OTHER way (bought CALL) - if the opposite also pays, it is not the shape.
  3. Added to today's Autopilot (Outcomes replay incl. rounds 88 and 90), Rs 2L and Rs 20k, both halves and full period.
Usage: python research/round91b_bshape_check.py [data_dir]
"""
import os, sys, json, itertools, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data")
import numpy as np, pandas as pd
import round91_profile_shapes as M
import outcomes as O
from fno import portfolio_bt as PB
M.DATA = M.R34.DATA = DATA
SLIP = 0.005


def b_trades(sym, src, trig, ks, flip=None):
    E = [e for e in M.entries(sym, src, "prev", trig) if e[6] == "b"]
    T = M.sim(sym, E, ks, "close", flip=flip)
    return M.money_idx(sym, T).assign(sym=sym) if len(T) else T


def half(T):
    a, b = T[T.date < "2025"], T[T.date >= "2025"]
    return {"n": len(T), "rs_tr": round(float(T.rs.mean()), 0) if len(T) else 0, "a": round(float(a.rs.sum())), "b": round(float(b.rs.sum())),
            "R": round(float(T.R.mean()), 3) if len(T) else 0, "years": T.groupby(T.date.str[:4]).rs.sum().round(0).to_dict()}


def to_pb(T, lab):
    df = {s: M.load(s)[0] for s in T.sym.unique()}
    rows = []
    for s, g in T.groupby("sym"):
        d = df[s]
        ent = np.full(len(d), np.nan); ent[g.i.values] = g.ep.values
        p_in, p_out, risk, _ = M.R34.price(s, {"idx": d.index, "c": ent}, g, M.vix())
        lot = M.R34.D.lot_size(s)
        pi = p_in * (1 + SLIP); po = np.maximum(p_out * (1 - SLIP), 0.05)
        o = pd.DataFrame({"date": g.date.values, "entry_time": d.index[g.i.values].strftime("%H:%M"),
                          "exit_time": [min(x, "15:20") for x in (d.index[g.j.values] + pd.Timedelta(minutes=5)).strftime("%H:%M")],
                          "sym": s, "strategy": f"Profile b-shape r91 {lab}", "side": np.where(g.side.values > 0, "CALL", "PUT"),
                          "prem_in": pi, "prem_out": po, "lot": lot, "risk_lot": np.maximum(risk * (1 + SLIP), pi * 0.05) * lot,
                          "R": g.R.values, "spot_in": g.ep.values, "spot_out": g.xs.values, "reason": "r91", "pieces": 1})
        o["risk_lot_capped"] = o["risk_lot"]
        cw = PB.cpr_widths(O.candles(s)); o["cpr_w"] = cw.reindex(o.date).values
        o["score"] = PB._scores(o, O.candles(s))
        rows.append(o)
    return pd.concat(rows, ignore_index=True)


def replay(q, a, b, extra=None):
    k, ak, hk = PB._trade_key(q), PB._alt_key(q), PB._hi_key(q)
    cut = lambda X: X[(X.date >= a) & (X.date <= b)]  # noqa: E731
    T = pd.concat([O.trades(k, s) for s in q["indices"]], ignore_index=True)
    A = pd.concat([O.trades(ak, s) for s in q["indices"]], ignore_index=True) if ak else None
    H = pd.concat([O.trades(hk, s) for s in q["indices"]], ignore_index=True) if hk else None
    if extra is not None:
        T = pd.concat([T, extra], ignore_index=True)
        A = pd.concat([A, extra], ignore_index=True) if A is not None else None
        H = pd.concat([H, extra], ignore_index=True) if H is not None else None
    R = PB.replay(cut(T), q["capital"], q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"],
                  q["dd_half"] and q["sizing"] != "fixed", cut(A) if A is not None else None, cpr=q.get("cpr_size", False),
                  hi=cut(H) if H is not None else None, channel=q.get("channel", True))
    s = PB._summary(R, q["capital"])
    tk = R[R["skipped"].isna()] if "skipped" in R else R
    new = tk[tk.strategy.str.contains("r91")] if len(tk) else tk
    return {"final": s["final"], "dd": s["max_dd_pct"], "new": int(len(new)), "new_pnl": float(new.pnl.sum()) if len(new) else 0.0}


def main():
    res = {"neigh": {}, "by_sym": {}, "opp": {}, "add": {}}
    # 1. neighbours
    for nb, plo, vah in itertools.product((32, 48, 64), (0.33, 0.38, 0.43), (0.60, 0.66, 0.72)):
        M.NB = nb; M.TH.update(p_lo=plo, va_hi=vah)
        for src, trig in itertools.product(("vol", "tpo"), ("touch", "reject", "accept")):
            E = {s: [e for e in M.entries(s, src, "prev", trig) if e[6] == "b"] for s in M.IDX}
            for ks in (0.5, 1.0):
                parts = [M.money_idx(s, M.sim(s, E[s], ks, "close")).assign(sym=s) for s in M.IDX if E[s]]
                parts = [p for p in parts if len(p)]
                if not parts:
                    continue
                T = pd.concat(parts)
                res["neigh"][f"{nb}|{plo}|{vah}|{src}|{trig}|{ks}"] = half(T)
        print("neighbours done for bins", nb, plo, vah, flush=True)
    M.NB = 48; M.TH.update(p_lo=0.38, va_hi=0.66)
    N = pd.DataFrame(res["neigh"]).T
    both = ((N.a > 0) & (N.b > 0)).mean()
    print(f"neighbours: {len(N)} versions, Rs/trade median {N.rs_tr.median():+.0f}, positive both halves {both:.0%}, "
          f"positive overall {(N.a + N.b > 0).mean():.0%}", flush=True)
    for col in ("src", "trig"):
        pass
    # 2. by index and the opposite direction (the round-91 pick and the TPO twin)
    picks = [("vol", "reject", 1.0), ("vol", "touch", 1.0), ("tpo", "reject", 1.0), ("tpo", "touch", 1.0)]
    keep = {}
    for src, trig, ks in picks:
        key = f"{src}|{trig}|{ks}"
        parts = [b_trades(s, src, trig, ks) for s in M.IDX]
        T = pd.concat([p for p in parts if len(p)])
        keep[key] = T
        res["by_sym"][key] = {s: half(T[T.sym == s]) for s in M.IDX if (T.sym == s).any()}
        Op = pd.concat([p for p in (b_trades(s, src, trig, ks, flip="opp") for s in M.IDX) if len(p)])
        res["opp"][key] = {"short (rule)": half(T), "long (opposite)": half(Op)}
        print(key, "rule", res["opp"][key]["short (rule)"], "| opposite", res["opp"][key]["long (opposite)"],
              "| by index", {s: (v["n"], v["rs_tr"], v["a"], v["b"]) for s, v in res["by_sym"][key].items()}, flush=True)
    # 3. add to today's Autopilot
    P = {"2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", "2026-10-16"), "full": ("2023-01-02", "2026-10-16")}
    B0 = dict(O.B0)
    extras = {f"+ b-shape ({k})": to_pb(keep[k], k) for k in ("vol|reject|1.0", "tpo|reject|1.0", "vol|touch|1.0")}
    for cap in (200_000, 20_000):
        for pn, (a, b) in P.items():
            q = {**B0, "capital": cap}
            row = {"Autopilot today": replay(q, a, b)}
            for lab, X in extras.items():
                row[lab] = replay(q, a, b, X)
            res["add"][f"{cap}|{pn}"] = row
            print(f"Rs {cap:,} {pn}: " + " | ".join(f"{k} {v['final'] / 1e5:.2f}L ({v['dd']:+.1f}%, new {v['new']} {v['new_pnl']:+,.0f})"
                                                   for k, v in row.items()), flush=True)
    json.dump(res, open(os.path.join(HERE, "round91b_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
