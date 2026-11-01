"""Round 69b: the best round-69 versions (picked on 2023-24) added to today's Autopilot - same replay as rounds 63b / 67b.
  A  15-min swing high / low retest, narrow-CPR days, hold to the close (top pick on 2023-24 in both runs)
  B  15-min any new level retest, narrow CPR, HH/HL structure direction, hold to the close (positive every year)
  C  15-min minor round number retest (Nifty 100s, Bank Nifty / Sensex 500s), narrow CPR, structure direction, close
Usage: python research/round69b_add_to_autopilot.py <data_dir>"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
DATA = sys.argv[1]
sys.argv = [sys.argv[0], DATA, "structure"]
import numpy as np, pandas as pd
import round69_structure_round_numbers as M
import round67b_add_to_autopilot as B67
G, S = M.G, M.G.S
import round34 as R34, outcomes as O
from fno import portfolio_bt as PB
SLIP = 0.005
CFG = {"A swing retest, narrow CPR": ("swing", False), "B any level retest + structure": (None, True), "C round-100s retest + structure": ("round_minor", True)}


def trades(sym, fam, use_dir):
    df = M.load(sym, "15m")
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    a = S.atr(h, l, c, 14); n = len(c)
    LV, famarr, width, bias = M.day_levels(df)
    famid = np.array([G.FAMS.index(f) for f in famarr], np.int64)
    dayid = (df.index.normalize().values.astype("int64") // 86_400_000_000_000).astype(np.int64)
    mins = np.asarray(df.index.hour * 60 + df.index.minute, np.int64)
    _, dayend, _, _, _ = S.session_info("index", df.index)
    dayend = dayend | (mins >= 15 * 60 + 15)
    fi = -1 if fam is None else G.FAMS.index(fam)
    side, lvl, col = G.signals(o, h, l, c, a, LV, famid, fi, dayid, mins, 1)
    ok = (mins >= 9 * 60 + 20) & (mins <= 14 * 60 + 30) & (width < 0.25)
    if use_dir:
        ok &= ~(((bias == 1) & (side == -1)) | ((bias == -1) & (side == 1)))
    side[~ok] = 0
    idx = np.flatnonzero(side)
    rank = pd.Series(1, index=idx).groupby(dayid[idx]).cumsum().values
    side[idx[rank > 2]] = 0
    stop = np.where(side == 1, np.minimum(l, c - 0.3 * a), np.where(side == -1, np.maximum(h, c + 0.3 * a), np.nan))
    nanv = np.full(n, np.nan)
    en, s2, R, b = S.sim(o, h, l, c, side, stop, nanv, np.zeros(n, bool), np.zeros(n, bool), 10 ** 6, dayend, np.ones(n, bool))
    e = o[en]; xs = e * (1 + s2 * R); j = np.minimum(en + b - 1, n - 1)
    T = pd.DataFrame({"i": en, "j": j, "xs": xs, "side": s2, "sl": stop[en - 1]})
    vix1 = pd.read_csv(os.path.join(DATA, "INDIAVIX_1m_upstox.csv.gz"), parse_dates=["ts"]).set_index("ts").close
    vix1 = vix1[~vix1.index.duplicated()].sort_index()
    p_in, p_out, risk, dte = R34.price(sym, {"idx": df.index, "c": o}, T, vix1)
    lot = O.D.lot_size(sym); ix = df.index; ex = ix[j] + pd.Timedelta(minutes=15)
    out = pd.DataFrame({"date": [x.strftime("%Y-%m-%d") for x in ix[en]], "entry_time": [x.strftime("%H:%M") for x in ix[en]],
                        "exit_time": [min(x, x.normalize() + pd.Timedelta(hours=15, minutes=20)).strftime("%H:%M") for x in ex],
                        "sym": sym, "strategy": "Level break (r69)", "side": np.where(s2 > 0, "CALL", "PUT"),
                        "prem_in": p_in * (1 + SLIP), "prem_out": np.maximum(p_out * (1 - SLIP), 0.05), "lot": lot,
                        "risk_lot": np.maximum(risk * (1 + SLIP), p_in * 0.05) * lot, "R": R / np.maximum(np.abs(e - stop[en - 1]) / e, 1e-9),
                        "spot_in": e, "spot_out": xs, "reason": "r69", "pieces": 1})
    out["risk_lot_capped"] = out["risk_lot"]
    out["score"] = PB._scores(out, O.candles(sym))
    return out


def main():
    B0 = dict(O.B0); k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True)
    P = {"2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", "2026-10-23"), "2023-Oct 2026": ("2023-01-02", "2026-10-23"), "2026": ("2026-01-01", "2026-10-23")}
    res = {}
    cands = {name: pd.concat([trades(s, *cfg) for s in O.SYMS], ignore_index=True) for name, cfg in CFG.items()}
    for cap in (200_000, 20_000):
        for pn, (a, b) in P.items():
            q = {**B0, "capital": cap}
            rows = {"Autopilot today": B67.run(base, q, a, b, alt)}
            for nm, tr in cands.items():
                rows["+ " + nm] = B67.run(pd.concat([base, tr], ignore_index=True), q, a, b, pd.concat([alt, tr], ignore_index=True))
            res[f"{cap}|{pn}"] = rows
            print(f"\n== Rs {cap:,} | {pn}")
            for lab, r in rows.items():
                print(f"  {lab:<36} Rs {r['final']:>12,.0f}  fall {r['max_dd_pct']:+.1f}%  new trades {r['new_trades']:>4} (their P&L {r['new_pnl']:+,.0f})")
    json.dump(res, open(os.path.join(HERE, "round69b_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
