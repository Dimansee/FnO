"""Round 44 (25 Oct 2026): a rupee cap on the loss per lot. If the rule's stop would lose more than Rs 2,200 on one lot
(e.g. the setup says Rs 3,000), the stop is pulled in to where the option has lost Rs 2,200; trades whose stop is already
tighter are unchanged. Also tried: caps of Rs 1,500 / 3,000, and SKIPPING trades whose normal stop risks more than
Rs 2,200. Trades: fno.backtest.run strategy "autopilot", today's rules (big-run skip), Nifty + Bank Nifty + Sensex,
Jan 2023 - 23 Oct 2026, per-lot P&L at 1 ITM and 1 OTM. Accounts: Rs 2 lakh and Rs 20,000, today's Autopilot sizing
(compounding 1%, standard confidence, half risk in a drawdown, 1-lot floor while one lot risks <= 15%, 1 OTM strike
below Rs 60,000, trend slot from Rs 40k, Fibonacci from Rs 2L, weekly limit), sized on the capped risk; and fixed 1 lot.
Run: python round44.py -> round44_results.json"""
import os, sys, json, math, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round25_harness as H
from fno import backtest as B, strategy as S, indicators as I, config as C

SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
LOT = {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}
VOL5 = {"NIFTY": 1_565_850, "BANKNIFTY": 17_588, "SENSEX": 432_875}
FREEZE = {"NIFTY": 3510, "BANKNIFTY": 1440, "SENSEX": 1000}
V = {"current stop (no cap)": None, "cap Rs 1,500 per lot": 1500, "cap Rs 2,200 per lot": 2200, "cap Rs 3,000 per lot": 3000,
     "skip trades risking > Rs 2,200 per lot": ("skip_over", 2200)}
MTF = {}


def trades(cap, otm):
    v = H.load("INDIAVIX")["close"]; rows = []
    for s in SYMS:
        c = H.load(s)
        B.EXIT.clear(); B.EXIT["big_run_skip"] = True
        if cap is not None:
            B.EXIT["max_loss_lot"] = cap
        r = B.run(s, capital=1e9, candles=c, vix_intraday=v, sizing="one_lot", strategy="autopilot", otm=otm, live_rules=False)
        B.EXIT.clear()
        T = pd.DataFrame(r["trades"]); T["sym"] = s
        if s not in MTF:
            MTF[s] = S.mtf_frames(c)
        ends = pd.to_datetime(T.date + " " + T.entry_time)
        T["score"] = [S.mtf_score_at(MTF[s], e, 1 if sd == "CALL" else -1) for e, sd in zip(ends, T.side)]
        rows.append(T)
    T = pd.concat(rows, ignore_index=True)
    T["key"] = T.sym + T.date + T.entry_time + T.strategy
    return T.sort_values(["date", "entry_time"]).reset_index(drop=True)


def replay(Ti, To, start, fixed=False, k=0.06, max_lots=50):
    """Today's Autopilot sizing; uses the 1-OTM version of a trade while the account is below Rs 60k (live rule)."""
    oth = To.set_index("key") if To is not None else None
    eq = peak = float(start); open_pos, rows = [], []
    week, ws, wp, blocked = None, eq, 0.0, False
    for r in Ti.itertuples(index=False):
        if eq < C.SMALL_OTM_BELOW and oth is not None and r.key in oth.index:
            r2 = oth.loc[r.key]
            r2 = r2.iloc[0] if isinstance(r2, pd.DataFrame) else r2
            prem_in, prem_out, rl0 = float(r2.prem_in), float(r2.prem_out), float(r2.risk_lot_capped)
        else:
            prem_in, prem_out, rl0 = float(r.prem_in), float(r.prem_out), float(r.risk_lot_capped)
        ki, ko = r.date + r.entry_time, r.date + r.exit_time
        open_pos = [p for p in open_pos if p[0] > ki]
        wk = pd.Timestamp(r.date).isocalendar()[:2]
        if wk != week:
            week, ws, wp, blocked = wk, eq, 0.0, False
        if blocked or (r.strategy == "Trend-day" and eq < C.TREND_MIN_EQUITY) or (str(r.strategy).startswith("Fib") and eq < C.FIB_MIN_EQUITY):
            continue
        lot = LOT[r.sym]
        if fixed:
            lots = 1
        else:
            m = (C.CONVICTION_HIGH if r.score >= 5 else C.CONVICTION_LOW) if r.sym in C.CONVICTION_SYMS else 1.0
            if eq < (1 - C.DD_HALF_AT) * peak:
                m *= 0.5
            rl = max(rl0, 1.0)
            lots = int(eq * 0.01 * m // rl)
            if lots == 0:
                if rl <= C.AUTOPILOT_MAX_TRADE_RISK * eq:
                    lots = 1
                else:
                    continue
            lots = min(lots, max_lots)
        free = eq - sum(p[1] for p in open_pos)
        lots = min(lots, int(free // (prem_in * lot)))
        if lots <= 0:
            continue
        qty = lots * lot
        imp = k * math.sqrt(qty / VOL5[r.sym])
        p_in, p_out = prem_in * (1 + imp), max(prem_out * (1 - imp), 0.05)
        pnl = (p_out - p_in) * qty - I.charges(p_in, p_out, qty, orders=2 * math.ceil(qty / FREEZE[r.sym]), exch_pct=I.exch_rate(r.sym))
        eq += pnl; peak = max(peak, eq); wp += pnl
        if wp <= -(0.04 if ws >= C.AUTOPILOT_SMALL_ACCOUNT else C.AUTOPILOT_WEEKLY_SMALL) * ws:
            blocked = True
        open_pos.append((ko, p_in * qty))
        rows.append((r.date, pnl, eq))
    R = pd.DataFrame(rows, columns=["date", "pnl", "eq"])
    e = pd.concat([pd.Series([float(start)]), R["eq"]], ignore_index=True)
    yrs = {}
    prev = float(start)
    for y, g in R.groupby(R.date.str[:4]):
        yrs[y] = round(float(g["eq"].iloc[-1])); prev = g["eq"].iloc[-1]
    return {"final": round(float(e.iloc[-1])), "dd_pct": round(float((e / e.cummax() - 1).min() * 100), 1), "trades": int(len(R)),
            "worst_trade": round(float(R.pnl.min())) if len(R) else 0, "year_end": yrs}


def per_lot(T):
    loss = T[T.pnl < 0].pnl
    return {"n": int(len(T)), "train": int(T[T.date < "2026"].pnl.sum()), "y2026": int(T[T.date >= "2026"].pnl.sum()),
            "win": round(float((T.pnl > 0).mean() * 100), 1), "avg_loss": int(loss.mean()) if len(loss) else 0, "worst": int(T.pnl.min()),
            "capped": int((T.reason == "Rs loss cap").sum()), "by_sym": T.groupby("sym").pnl.sum().astype(int).to_dict(),
            "by_strategy": T.groupby("strategy").pnl.sum().astype(int).to_dict()}


if __name__ == "__main__":
    out = {}
    for nm, cap in V.items():
        Ti, To = trades(cap, -1), trades(cap, 1)
        res = {"per_lot_1ITM": per_lot(Ti), "per_lot_1OTM": per_lot(To)}
        for start in (200_000, 20_000):
            res[f"account_{start}"] = replay(Ti, To, start)
            res[f"fixed1lot_{start}"] = replay(Ti, To, start, fixed=True)
            for y0 in ("2026",):
                res[f"account_{start}_from_{y0}"] = replay(Ti[Ti.date >= y0], To[To.date >= y0], start)
        out[nm] = res
        a2, a20 = res["account_200000"], res["account_20000"]
        print(f"{nm:<40} per lot {res['per_lot_1ITM']['train']:>8} {res['per_lot_1ITM']['y2026']:>8} capped {res['per_lot_1ITM']['capped']:>4} worst {res['per_lot_1ITM']['worst']:>6} | "
              f"2L {a2['final']:>11,} dd {a2['dd_pct']:>6} | 20K {a20['final']:>10,} dd {a20['dd_pct']:>6} | 20K 2026 {res['account_20000_from_2026']['final']:>8,} | 2L 2026 {res['account_200000_from_2026']['final']:>9,}", flush=True)
    json.dump(out, open(os.path.join(HERE, "round44_results.json"), "w"), indent=1, default=str)
