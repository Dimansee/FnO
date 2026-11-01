"""Round 67b: the best round-67 level rules added to TODAY's Autopilot (same replay as the Outcomes tab, like round 63b).

Candidates (picked on 2023-24 money in round 67, 5-min bars, at most 2 a day per index, out by 15:15):
  A  break of any level (CPR, pivots, previous day, S/R zones, opening range), only in the day's CPR direction, narrow-CPR days,
     stop beyond the signal bar, hold to the end of the day                      (round 67 #1: +8.0L 2023-24, +1.9L 2025-26)
  B  the same with confluence (another family's level within 0.1%)              (round 67 #6: +5.7L / +2.3L)
Priced as ATM options (app calibration with decay, 0.5% slippage per fill as the app's backtest), merged into today's Autopilot
trades and replayed with compounding at 1% risk, conviction sizing, big-run skip, drawdown brake, gates, weekly limit, free cash.
User's rule: adopt only if Autopilot + rule beats Autopilot alone. Usage: python research/round67b_add_to_autopilot.py <data_dir>
"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import outcomes as O
import round62_scalping7 as S
import round67_levels_grid as G
import round34 as R34
from fno import portfolio_bt as PB

DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data")
S.DATA = G.DATA = DATA
SLIP = 0.005


def rule_trades(sym, confluence):
    df = G.load(sym, "5m")
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    a = S.atr(h, l, c, 14)
    n = len(c)
    LV, fam, width, bias = G.day_levels(df)
    famid = np.array([G.FAMS.index(f) for f in fam], np.int64)
    dayid = (df.index.normalize().values.astype("int64") // 86_400_000_000_000).astype(np.int64)
    hm = df.index.hour * 60 + df.index.minute
    mins = np.asarray(hm, np.int64)
    _, dayend, _, _, _ = S.session_info("index", df.index)
    dayend = dayend | (hm >= 15 * 60 + 15)
    side, lvl, col = G.signals(o, h, l, c, a, LV, famid, -1, dayid, mins, 0)
    ok = (mins >= 9 * 60 + 20) & (mins <= 14 * 60 + 30) & (width < 0.25) & ~(((bias == 1) & (side == -1)) | ((bias == -1) & (side == 1)))
    if confluence:
        conf = np.zeros(LV.shape, bool)
        for j in range(LV.shape[1]):
            for q in range(LV.shape[1]):
                if fam[q] != fam[j]:
                    conf[:, j] |= np.abs(LV[:, j] - LV[:, q]) <= 0.001 * LV[:, j]
        cf = np.zeros(n, bool)
        hit = col >= 0
        cf[hit] = conf[np.flatnonzero(hit), col[hit]]
        ok &= cf
    side[~ok] = 0
    idx = np.flatnonzero(side)
    rank = pd.Series(1, index=idx).groupby(dayid[idx]).cumsum().values
    side[idx[rank > 2]] = 0
    stop = np.where(side == 1, np.minimum(l, c - 0.3 * a), np.where(side == -1, np.maximum(h, c + 0.3 * a), np.nan))
    nanv = np.full(n, np.nan)
    en, s2, R, b = S.sim(o, h, l, c, side, stop, nanv, np.zeros(n, bool), np.zeros(n, bool), 10 ** 6, dayend, np.ones(n, bool))
    e = o[en]
    xs = e * (1 + s2 * R)
    j = np.minimum(en + b - 1, n - 1)
    T = pd.DataFrame({"i": en, "j": j, "xs": xs, "side": s2, "sl": stop[en - 1]})
    vix1 = pd.read_csv(os.path.join(DATA, "INDIAVIX_1m_upstox.csv.gz"), parse_dates=["ts"]).set_index("ts").close
    vix1 = vix1[~vix1.index.duplicated()].sort_index()
    p_in, p_out, risk, dte = R34.price(sym, {"idx": df.index, "c": o}, T, vix1)
    lot = O.D.lot_size(sym)
    idx_t = df.index
    exit_ts = idx_t[j] + pd.Timedelta(minutes=5)
    out = pd.DataFrame({
        "date": [x.strftime("%Y-%m-%d") for x in idx_t[en]], "entry_time": [x.strftime("%H:%M") for x in idx_t[en]],
        "exit_time": [min(x, x.normalize() + pd.Timedelta(hours=15, minutes=20)).strftime("%H:%M") for x in exit_ts],
        "sym": sym, "strategy": "Level break (CPR side, narrow CPR)", "side": np.where(s2 > 0, "CALL", "PUT"),
        "prem_in": p_in * (1 + SLIP), "prem_out": np.maximum(p_out * (1 - SLIP), 0.05), "lot": lot,
        "risk_lot": np.maximum(risk * (1 + SLIP), p_in * 0.05) * lot, "R": R / np.maximum(np.abs(e - stop[en - 1]) / e, 1e-9),
        "spot_in": e, "spot_out": xs, "reason": "r67", "pieces": 1})
    out["risk_lot_capped"] = out["risk_lot"]
    out["score"] = PB._scores(out, O.candles(sym))
    return out


def run(T, q, start, end, A=None):
    T = T[(T.date >= start) & (T.date <= end)]
    if A is not None:
        A = A[(A.date >= start) & (A.date <= end)]
    R = PB.replay(T, q["capital"], q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"],
                  q["dd_half"] and q["sizing"] != "fixed", A)
    s = PB._summary(R, q["capital"])
    tk = R[R["skipped"].isna()] if "skipped" in R else R
    new = tk[tk.strategy.str.startswith("Level break")] if len(tk) else tk
    return {"final": s["final"], "net": s["net"], "max_dd_pct": s["max_dd_pct"], "trades": s["trades"],
            "years": {y["year"]: y["ret_pct"] for y in s["years"]}, "new_trades": int(len(new)),
            "new_pnl": float(new.pnl.sum()) if len(new) else 0.0, "skipped": int(R["skipped"].notna().sum()) if "skipped" in R else 0}


def main():
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = {s: O.trades(k, s) for s in O.SYMS}
    alt = {s: O.trades(ak, s) for s in O.SYMS} if ak else None
    cands = {"A (no confluence)": {s: rule_trades(s, False) for s in O.SYMS}, "B (with confluence)": {s: rule_trades(s, True) for s in O.SYMS}}
    periods = {"2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", "2026-10-23"), **O.PERIODS}
    res = {}
    for cap in O.CAPS:
        for per, (a, b) in periods.items():
            q = {**B0, "capital": cap}
            Tb = pd.concat(list(base.values()), ignore_index=True)
            Ab = pd.concat(list(alt.values()), ignore_index=True) if alt else None
            rows = {"Autopilot today": run(Tb, q, a, b, Ab)}
            for cn, tr in cands.items():
                for lab, syms in (("all three", O.SYMS), ("Nifty + Sensex", ["NIFTY", "SENSEX"])):
                    Tn = pd.concat([Tb] + [tr[s] for s in syms], ignore_index=True)
                    An = pd.concat([Ab] + [tr[s] for s in syms], ignore_index=True) if Ab is not None else None
                    rows[f"+ {cn}, {lab}"] = run(Tn, q, a, b, An)
            res[f"{cap}|{per}"] = rows
            print(f"\n== start Rs {cap:,} | {per} ({a} to {b})")
            for lab, r in rows.items():
                print(f"  {lab:<38} final Rs {r['final']:>12,.0f}  worst fall {r['max_dd_pct']:+.1f}%  trades {r['trades']:>4}  new {r['new_trades']:>3} (their P&L {r['new_pnl']:+,.0f})  skipped {r['skipped']}")
    json.dump(res, open(os.path.join(HERE, "round67b_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
