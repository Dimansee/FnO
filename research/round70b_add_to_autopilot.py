"""Round 70b: the round-70 OI-wall rejection breakout (15-min, OI wall, >= 2 rejections, stop below the breakout candle) added to
today's Autopilot - same replay as rounds 63b / 67b / 69b. Exits: '50 then trail' (best, positive every year) and the user's
'half at +50, half at +90'. Nifty + Bank Nifty, Oct 2023 - Oct 2026 (OI history). Usage: python research/round70b_add_to_autopilot.py <data_dir>"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
DATA = sys.argv[1]
import numpy as np, pandas as pd
import round70_oi_wall_rejection_breakout as M
import round67b_add_to_autopilot as B67
import round34 as R34, outcomes as O
from fno import portfolio_bt as PB
M.DATA = M.S.DATA = M.OI.DATA = DATA
SLIP = 0.005


def trades(sym, exit_kind, need=2):
    F = M.OI.oi_features(sym)
    df = M.load(sym, "15m")
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    n = len(c); d = df.index.normalize(); days = d.unique()
    dpos = {dd: np.flatnonzero(d == dd) for dd in days}
    hm = df.index.hour * 60 + df.index.minute
    vix1 = pd.read_csv(os.path.join(DATA, "INDIAVIX_1m_upstox.csv.gz"), parse_dates=["ts"]).set_index("ts").close
    vix1 = vix1[~vix1.index.duplicated()].sort_index()
    rows = []
    for di, dd in enumerate(days):
        if dd not in F.index or di < 2:
            continue
        bars = dpos[dd]
        lb = bars[hm[bars] <= 15 * 60 + 15]
        if len(bars) < 10 or not len(lb):
            continue
        last = lb[-1]; start = dpos[days[di - 2]][0]; f = F.loc[dd]
        for s, L in ((1, f.cw1), (-1, f.pw1)):
            if not np.isfinite(L):
                continue
            for i in bars:
                if hm[i] < 9 * 60 + 20 or hm[i] > 14 * 60 + 30 or i == bars[0]:
                    continue
                brk = (c[i] > L and c[i - 1] <= L) if s == 1 else (c[i] < L and c[i - 1] >= L)
                if not brk:
                    continue
                if M.rejections(h, l, c, L, s, start, i) >= need and i + 1 <= last:
                    stop = l[i] if s == 1 else h[i]
                    if s * (o[i + 1] - stop) > 0:
                        legs = M.manage(o, h, l, c, i + 1, s, stop, exit_kind, last)
                        outs, fr, kmax = [], [], 0
                        for frac, k, xp in legs:
                            T = pd.DataFrame({"i": [i + 1], "j": [k], "xs": [xp], "side": [s], "sl": [stop]})
                            p_in, p_out, risk, _ = R34.price(sym, {"idx": df.index, "c": o}, T, vix1)
                            outs.append(float(p_out[0])); fr.append(frac); kmax = max(kmax, k)
                        p_out_eff = float(np.dot(fr, outs) / sum(fr))
                        ex = df.index[kmax] + pd.Timedelta(minutes=15)
                        rows.append({"date": dd.strftime("%Y-%m-%d"), "entry_time": df.index[i + 1].strftime("%H:%M"),
                                     "exit_time": min(ex, ex.normalize() + pd.Timedelta(hours=15, minutes=20)).strftime("%H:%M"),
                                     "sym": sym, "strategy": "Level break (OI wall r70)", "side": "CALL" if s == 1 else "PUT",
                                     "prem_in": float(p_in[0]) * (1 + SLIP), "prem_out": max(p_out_eff * (1 - SLIP), 0.05), "lot": O.D.lot_size(sym),
                                     "risk_lot": max(float(risk[0]) * (1 + SLIP), float(p_in[0]) * 0.05) * O.D.lot_size(sym),
                                     "R": 0.0, "spot_in": o[i + 1], "spot_out": legs[-1][2], "reason": "r70", "pieces": len(legs)})
                break
    out = pd.DataFrame(rows)
    out["risk_lot_capped"] = out["risk_lot"]
    out["score"] = PB._scores(out, O.candles(sym))
    return out


def main():
    B0 = dict(O.B0); k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True)
    cands = {f"+ OI-wall breakout, {ex}": pd.concat([trades(s, ex) for s in ("NIFTY", "BANKNIFTY")], ignore_index=True) for ex in ("50 then trail", "half 50/90")}
    for nm, tr in cands.items():
        print(nm, "trades", len(tr), flush=True)
    P = {"Oct 2023-2024": ("2023-10-10", "2024-12-31"), "2025": ("2025-01-01", "2025-12-31"), "2026": ("2026-01-01", "2026-10-09"), "Oct 2023-Oct 2026": ("2023-10-10", "2026-10-09")}
    res = {}
    for cap in (200_000, 20_000):
        for pn, (a, b) in P.items():
            q = {**B0, "capital": cap}
            rows = {"Autopilot today": B67.run(base, q, a, b, alt)}
            for nm, tr in cands.items():
                rows[nm] = B67.run(pd.concat([base, tr], ignore_index=True), q, a, b, pd.concat([alt, tr], ignore_index=True))
            res[f"{cap}|{pn}"] = rows
            print(f"\n== Rs {cap:,} | {pn}")
            for lab, r in rows.items():
                print(f"  {lab:<40} Rs {r['final']:>12,.0f}  fall {r['max_dd_pct']:+.1f}%  new trades {r['new_trades']:>3} (their P&L {r['new_pnl']:+,.0f})")
    json.dump(res, open(os.path.join(HERE, "round70b_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
