"""Round 42 (24 Oct 2026): the user's plan, tested piece by piece.
  1. Start with Rs 2 lakh and compound (every trade sized from the account value at that moment).
  2. Lots set by confidence in the trade (1 lot ... 50 lots).
  3. (part C, round42_exits.py) T1/T2/T3 booking with early exit when the market reverses.
  4. "After a 250-400 point run in 2-3 days the market must come back" - does it, and does it matter for these trades?

Trades: the app's own backtester (fno.backtest.run, strategy "autopilot", current exits), one lot, all three indices,
Jan 2023 - 23 Oct 2026; per-lot premiums are then re-sized in a portfolio replay in time order:
  * lots = account x risk% x confidence multiplier / risk per lot (1-lot floor only while one lot risks <= 15% of the
    account), cut to what the cash can pay (all open premiums together <= account value)
  * live gates: trend slot from Rs 40k, Fibonacci from Rs 2 lakh; weekly loss limit 4% (15% below Rs 1 lakh)
  * big size costs more: extra slippage per fill = k x sqrt(quantity / typical 5-minute volume near the money) (square-root
    impact; volumes measured on the recorder's last 10 days: Nifty ~1.57M, Bank Nifty ~17.6k, Sensex ~433k qty per 5 min;
    k = 3% base, 6% stress), a participation cap of 10% of that 5-minute volume, brokerage per order with orders split at
    the exchange freeze quantity (Nifty 3,510, Bank Nifty 1,440 from 5 Oct 2026; Sensex 1,000 assumed).
Confidence: the app's 0-6 multi-timeframe score (20-EMA + trend line on 5m / 15m / 1h, round 33), checked on 2023-25 and 2026.
Run: python round42.py [--gen] -> round42_results.json
"""
import os, sys, json, math, pickle, warnings
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
EXCH = {"NIFTY": None, "BANKNIFTY": None, "SENSEX": I.exch_rate("SENSEX")}
PKL = os.path.join("/tmp", "round42_trades.pkl")


# ------------------------------------------------------------------ trades
def gen():
    v = H.load("INDIAVIX")["close"]
    out = {}
    for otm in (-1, 1):
        rows = []
        for s in SYMS:
            c = H.load(s)
            r = B.run(s, capital=200000, candles=c, vix_intraday=v, sizing="one_lot", strategy="autopilot", otm=otm, live_rules=False)
            T = pd.DataFrame(r["trades"]); T["sym"] = s
            mtf = S.mtf_frames(c)
            tz = c.index.tz
            ends = pd.to_datetime(T.date + " " + T.entry_time)
            if tz is not None:
                ends = ends.dt.tz_localize(tz)
            T["score"] = [S.mtf_score_at(mtf, e, 1 if sd == "CALL" else -1) for e, sd in zip(ends, T.side)]
            d = c.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
            tr = pd.concat([d.high - d.low, (d.high - d.close.shift()).abs(), (d.low - d.close.shift()).abs()], axis=1).max(axis=1)
            atr = tr.rolling(14).mean()
            mv3 = d.close.shift(1) - d.close.shift(4)          # points moved over the 3 sessions before the trade day
            key = pd.Index(d.index.date.astype(str) if hasattr(d.index.date, "astype") else [str(x) for x in d.index.date])
            m3 = dict(zip([str(x) for x in d.index.date], mv3.values)); a14 = dict(zip([str(x) for x in d.index.date], atr.shift(1).values))
            T["move3"] = T.date.map(m3); T["atr_d"] = T.date.map(a14)
            rows.append(T)
            print("gen", otm, s, len(T), int(T.pnl.sum()), flush=True)
        out[otm] = pd.concat(rows, ignore_index=True)
    pickle.dump(out, open(PKL, "wb"))
    return out


# ------------------------------------------------------------------ replay
def replay(T, start=200_000, risk=0.01, mult=None, fixed=None, k=0.03, part=0.10, compound=True, gates=True, one_order=False, since=None):
    """Portfolio replay in time order. mult(row) -> confidence multiplier. fixed = always this many lots."""
    T = T.sort_values(["date", "entry_time"]).reset_index(drop=True)
    if since:
        T = T[T.date >= since]
    eq, base = float(start), float(start)
    open_pos = []                      # (exit key, premium cash)
    week, week_start, week_pnl, blocked = None, eq, 0.0, False
    rows = []
    for r in T.itertuples():
        s, lot = r.sym, LOT[r.sym]
        key_in, key_out = r.date + r.entry_time, r.date + r.exit_time
        open_pos = [p for p in open_pos if p[0] > key_in]
        wk = pd.Timestamp(r.date).isocalendar()[:2]
        if wk != week:
            week, week_start, week_pnl, blocked = wk, eq, 0.0, False
        if blocked:
            continue
        acct = eq if compound else base
        if gates and ((r.strategy == "Trend-day" and acct < C.TREND_MIN_EQUITY) or (r.strategy.startswith("Fib") and acct < C.FIB_MIN_EQUITY)):
            continue
        m = mult(r) if mult else 1.0
        if fixed:
            lots = int(fixed)
        else:
            rl = max(float(r.risk_lot), 1.0)
            lots = int(acct * risk * m // rl)
            if lots == 0:
                if rl <= C.AUTOPILOT_MAX_TRADE_RISK * acct:
                    lots = 1
                else:
                    continue
        cap_liq = max(1, int(part * VOL5[s] / lot))
        lots = min(lots, cap_liq)
        if one_order:                                   # practical cap: never more than one exchange order (freeze quantity)
            lots = min(lots, FREEZE[s] // lot)
        cash_free = eq - sum(p[1] for p in open_pos)
        lots = min(lots, int(cash_free // (r.prem_in * lot)))
        if lots <= 0:
            continue
        qty = lots * lot
        imp = k * math.sqrt(qty / VOL5[s])
        p_in, p_out = r.prem_in * (1 + imp), max(r.prem_out * (1 - imp), 0.05)
        orders = 2 * math.ceil(qty / FREEZE[s])
        pnl = (p_out - p_in) * qty - I.charges(p_in, p_out, qty, orders=orders, exch_pct=EXCH[s])
        eq += pnl
        week_pnl += pnl
        cap = 0.04 if week_start >= C.AUTOPILOT_SMALL_ACCOUNT else C.AUTOPILOT_WEEKLY_SMALL
        if week_pnl <= -cap * week_start:
            blocked = True
        open_pos.append((key_out, p_in * qty))
        rows.append((r.date, r.sym, r.strategy, lots, pnl, eq, imp))
    R = pd.DataFrame(rows, columns=["date", "sym", "strategy", "lots", "pnl", "eq", "imp"])
    return R


def report(R, start=200_000):
    if R.empty:
        return {"trades": 0}
    eq = pd.concat([pd.Series([start]), R["eq"]], ignore_index=True)
    dd = (eq / eq.cummax() - 1).min()
    R["y"] = R.date.str[:4]
    yearly = {}
    prev = start
    for y, g in R.groupby("y"):
        end = g["eq"].iloc[-1]; yearly[y] = {"start": round(prev), "end": round(end), "ret_pct": round((end / prev - 1) * 100, 1), "max_lots": int(g.lots.max())}
        prev = end
    days = (pd.Timestamp(R.date.iloc[-1]) - pd.Timestamp(R.date.iloc[0])).days
    m = R.assign(m=R.date.str[:7]).groupby("m").pnl.sum()
    return {"trades": int(len(R)), "final": round(R["eq"].iloc[-1]), "multiple": round(R["eq"].iloc[-1] / start, 2),
            "cagr_pct": round(((R["eq"].iloc[-1] / start) ** (365 / max(days, 1)) - 1) * 100, 1), "max_dd_pct": round(dd * 100, 1),
            "worst_month": int(m.min()), "losing_months": int((m < 0).sum()), "months": int(len(m)), "avg_slip_extra_pct": round(R.imp.mean() * 100, 3),
            "max_lots": int(R.lots.max()), "yearly": yearly}


def conf_table(T):
    out = {}
    for nm, g in (("train", T[T.date < "2026"]), ("y2026", T[T.date >= "2026"])):
        rows = {}
        for b, gg in g.groupby(pd.cut(g.score, [-1, 2, 4, 6], labels=["0-2 low", "3-4 mid", "5-6 high"])):
            if len(gg):
                rows[str(b)] = {"n": int(len(gg)), "avg_pnl_lot": int(gg.pnl.mean()), "avg_R": round(float(gg.R.mean()), 3), "win": round(float((gg.pnl > 0).mean() * 100), 1)}
        out[nm] = rows
    return out


def run_table(T):
    """Trades after a big 3-day run: with the run vs against it (|move| >= 1.5 daily ATRs, ~250-400 Nifty pts)."""
    g = T.dropna(subset=["move3", "atr_d"]).copy()
    g["big"] = g.move3.abs() >= 1.5 * g.atr_d
    sgn = np.where(g.side == "CALL", 1, -1)
    g["with_run"] = np.sign(g.move3) == sgn
    out = {}
    for nm, h in (("train", g[g.date < "2026"]), ("y2026", g[g.date >= "2026"])):
        rows = {}
        for lab, q in (("normal days", h[~h.big]), ("big run, trade WITH it", h[h.big & h.with_run]), ("big run, trade AGAINST it", h[h.big & ~h.with_run])):
            rows[lab] = {"n": int(len(q)), "avg_pnl_lot": int(q.pnl.mean()) if len(q) else None, "avg_R": round(float(q.R.mean()), 3) if len(q) else None,
                         "win": round(float((q.pnl > 0).mean() * 100), 1) if len(q) else None, "net": int(q.pnl.sum())}
        out[nm] = rows
    return out


if __name__ == "__main__":
    TT = gen() if ("--gen" in sys.argv or not os.path.exists(PKL)) else pickle.load(open(PKL, "rb"))
    res = {"confidence": {}, "big_runs": {}, "replays": {}}
    for otm, lab in ((-1, "1 ITM"), (1, "1 OTM")):
        T = TT[otm]
        res["confidence"][lab] = conf_table(T)
        res["big_runs"][lab] = run_table(T)
        conv = lambda r: (C.CONVICTION_HIGH if r.score >= 5 else C.CONVICTION_LOW) if r.sym in C.CONVICTION_SYMS else 1.0    # live today
        strong = lambda r: 2.0 if r.score >= 5 else (1.0 if r.score >= 3 else 0.5)
        SC = {
            "A fixed 1 lot, no compounding": dict(fixed=1),
            "B fixed 3 lots, no compounding": dict(fixed=3),
            "C compounding, 1% risk (no confidence)": dict(),
            "D compounding, 1% risk + live confidence (1.33x / 0.67x)": dict(mult=conv),
            "E compounding, 1% risk + strong confidence (2x / 1x / 0.5x)": dict(mult=strong),
            "F compounding, 2% risk + live confidence": dict(risk=0.02, mult=conv),
            "G compounding, 3% risk + live confidence": dict(risk=0.03, mult=conv),
            "H 1% risk + live confidence, NO compounding (size from Rs 2L)": dict(mult=conv, compound=False),
            "D1 = D, capped at one exchange order per trade": dict(mult=conv, one_order=True),
            "E1 = E, capped at one exchange order per trade": dict(mult=strong, one_order=True),
            "F1 = F (2% risk), capped at one exchange order": dict(risk=0.02, mult=conv, one_order=True),
            "D26 = D, starting Rs 2L on 1 Jan 2026 (unseen year only)": dict(mult=conv, since="2026-01-01"),
            "E26 = E, starting Rs 2L on 1 Jan 2026": dict(mult=strong, since="2026-01-01"),
            "C26 = C, starting Rs 2L on 1 Jan 2026": dict(since="2026-01-01"),
        }
        for k_ in (0.03, 0.06):
            for nm, kw in SC.items():
                R = replay(T, k=k_, **kw)
                rep = report(R)
                res["replays"].setdefault(f"{lab} k={k_}", {})[nm] = rep
                print(f"{lab} k={k_} {nm[:60]:<60} final {rep['final']:>12,} x{rep['multiple']:<6} CAGR {rep['cagr_pct']:>6}% DD {rep['max_dd_pct']:>6}% maxlots {rep['max_lots']:>4} losing months {rep['losing_months']}/{rep['months']}", flush=True)
    json.dump(res, open(os.path.join(HERE, "round42_results.json"), "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items() if k != "replays"}, indent=1))
