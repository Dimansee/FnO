"""Round 43 (25 Oct 2026): what is left to improve on the live rule set? Every remaining gap, tested the same way.

Trades: the app's backtester (fno.backtest.run, strategy "autopilot", live exits, big-run skip ON = today's rules), 1 lot,
1 ITM, Nifty + Bank Nifty + Sensex, 2 Jan 2023 - 23 Oct 2026. Each trade gets: weekday, entry time, opening gap, India VIX at
the open, the 0-6 confidence score, how many trades on the same side were already open across the indices, and the day's
running result before it.

Ideas (fixed before looking; each one either removes trades or changes size):
  bank      Bank Nifty: drop it / keep only one of its two rules (it lost money in the round 42 account replay)
  time      no new entries after 13:30 / after 12:30; none before 09:30
  gap       skip days with a gap > 0.8%; or trade only in the gap's direction
  vix       skip VIX < 12; skip VIX > 16
  weekday   skip Mondays / Fridays
  stack     at most 2 / 1 open trades on the same side across all indices (23 Oct had 4 PUTs on one move)
  daystop   no new trades after 2 losing trades that day / after the day is down 1.5%
  score     skip confidence 0-2
  ddsize    half risk while the account is > 15% below its peak
  vixsize   risk x 14/VIX (0.6-1.4x)
Judged on: per-lot net 2023-25 and 2026 (and per index), the Rs 2 lakh compounding account (today's sizing, stress
slippage), and 2,000 one-year bootstrap paths. A trade-removal filter needs BOTH periods better and a shuffle test
p < 0.05 (removing the same number of random trades); with ~20 ideas, about one passes by luck at 5%.
Run: python round43.py [--gen] -> round43_results.json
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
PKL = "/tmp/round43_trades.pkl"


def gen():
    v = H.load("INDIAVIX")["close"]
    rows = []
    for s in SYMS:
        c = H.load(s)
        B.EXIT.clear(); B.EXIT["big_run_skip"] = True
        r = B.run(s, capital=1e9, candles=c, vix_intraday=v, sizing="one_lot", strategy="autopilot", otm=-1, live_rules=False)
        B.EXIT.clear()
        T = pd.DataFrame(r["trades"]); T["sym"] = s
        mtf = S.mtf_frames(c)
        ends = pd.to_datetime(T.date + " " + T.entry_time)
        T["score"] = [S.mtf_score_at(mtf, e, 1 if sd == "CALL" else -1) for e, sd in zip(ends, T.side)]
        d = c.resample("1D").agg({"open": "first", "close": "last"}).dropna()
        gap = (d.open / d.close.shift() - 1) * 100
        T["gap"] = T.date.map(dict(zip([str(x.date()) for x in d.index], gap.values)))
        vd = v.resample("1D").first().dropna()
        T["vix"] = T.date.map(dict(zip([str(x.date()) for x in vd.index], vd.values)))
        rows.append(T)
        print("gen", s, len(T), int(T.pnl.sum()), flush=True)
    T = pd.concat(rows, ignore_index=True)
    T["wd"] = pd.to_datetime(T.date).dt.weekday
    T["sgn"] = np.where(T.side == "CALL", 1, -1)
    T = T.sort_values(["date", "entry_time"]).reset_index(drop=True)
    # same-side trades already open at entry, and the day's result / losers before this entry (all indices)
    stack, day_pnl, day_los = [], [], []
    for i, r in T.iterrows():
        same = T[(T.date == r.date) & (T.entry_time < r.entry_time) & (T.exit_time > r.entry_time) & (T.sgn == r.sgn)]
        done = T[(T.date == r.date) & (T.exit_time <= r.entry_time)]
        stack.append(len(same)); day_pnl.append(done.pnl.sum()); day_los.append(int((done.pnl < 0).sum()))
    T["stack"], T["day_pnl_before"], T["day_losers_before"] = stack, day_pnl, day_los
    pickle.dump(T, open(PKL, "wb"))
    return T


def replay(T, start=200_000, risk=0.01, size_fn=None, k=0.06, max_lots=50, daystop=None, stack_max=None):
    """Rs 2 lakh compounding account with today's Autopilot sizing (standard confidence), cash, gates, weekly limit.
    size_fn(row, eq, peak) -> extra multiplier. daystop: ('losers', n) / ('pct', x). stack_max: max same-side open."""
    T = T.sort_values(["date", "entry_time"]).reset_index(drop=True)
    eq = peak = float(start)
    open_pos, rows = [], []
    week, ws, wp, blocked = None, eq, 0.0, False
    day, dpnl, dlos, dstart = None, 0.0, 0, eq
    for r in T.itertuples(index=False):
        ki, ko = r.date + r.entry_time, r.date + r.exit_time
        closed = [p for p in open_pos if p[0] <= ki]
        open_pos = [p for p in open_pos if p[0] > ki]
        if r.date != day:
            day, dpnl, dlos, dstart = r.date, 0.0, 0, eq
        for p in closed:                           # realised within the day for the day-stop
            if p[3] == r.date:
                dpnl += p[2]; dlos += int(p[2] < 0)
        wk = pd.Timestamp(r.date).isocalendar()[:2]
        if wk != week:
            week, ws, wp, blocked = wk, eq, 0.0, False
        if blocked:
            continue
        if daystop and ((daystop[0] == "losers" and dlos >= daystop[1]) or (daystop[0] == "pct" and dpnl <= -daystop[1] / 100 * dstart)):
            continue
        if stack_max is not None and sum(1 for p in open_pos if p[4] == r.sgn) >= stack_max:
            continue
        if (r.strategy == "Trend-day" and eq < C.TREND_MIN_EQUITY) or (str(r.strategy).startswith("Fib") and eq < C.FIB_MIN_EQUITY):
            continue
        m = (C.CONVICTION_HIGH if r.score >= 5 else C.CONVICTION_LOW) if r.sym in C.CONVICTION_SYMS else 1.0
        if size_fn:
            m *= size_fn(r, eq, peak)
        rl = max(float(r.risk_lot), 1.0)
        lots = int(eq * risk * m // rl)
        if lots == 0:
            if rl <= C.AUTOPILOT_MAX_TRADE_RISK * eq:
                lots = 1
            else:
                continue
        lots = min(lots, max_lots, max(1, int(0.1 * VOL5[r.sym] / LOT[r.sym])))
        free = eq - sum(p[1] for p in open_pos)
        lots = min(lots, int(free // (r.prem_in * LOT[r.sym])))
        if lots <= 0:
            continue
        qty = lots * LOT[r.sym]
        imp = k * math.sqrt(qty / VOL5[r.sym])
        p_in, p_out = r.prem_in * (1 + imp), max(r.prem_out * (1 - imp), 0.05)
        pnl = (p_out - p_in) * qty - I.charges(p_in, p_out, qty, orders=2 * math.ceil(qty / FREEZE[r.sym]), exch_pct=I.exch_rate(r.sym))
        eq += pnl; peak = max(peak, eq); wp += pnl
        if wp <= -(0.04 if ws >= C.AUTOPILOT_SMALL_ACCOUNT else C.AUTOPILOT_WEEKLY_SMALL) * ws:
            blocked = True
        open_pos.append((ko, p_in * qty, pnl, r.date, r.sgn))
        rows.append((r.date, pnl, eq))
    R = pd.DataFrame(rows, columns=["date", "pnl", "eq"])
    if R.empty:
        return {"final": start, "dd": 0.0, "trades": 0}
    e = pd.concat([pd.Series([float(start)]), R["eq"]], ignore_index=True)
    return {"final": round(float(R["eq"].iloc[-1])), "dd": round(float((e / e.cummax() - 1).min() * 100), 1), "trades": int(len(R))}


def boot(T, n=1000, seed=7, **kw):
    T = T.copy(); T["m"] = T.date.str[:7]
    months = sorted(T.m.unique()); rng = np.random.default_rng(seed)
    fin, dds = [], []
    by_m = {m: g for m, g in T.groupby("m")}
    for _ in range(n):
        parts = []
        for i, m in enumerate(rng.choice(months, 12)):
            g = by_m[m].copy()
            g["date"] = [(pd.Timestamp("2030-01-01") + pd.Timedelta(days=31 * i + int(x[8:10]) - 1)).date().isoformat() for x in g.date]
            parts.append(g)
        r = replay(pd.concat(parts, ignore_index=True), **kw)
        fin.append(r["final"]); dds.append(r["dd"])
    f, d = np.array(fin), np.array(dds)
    return {"median": int(np.median(f)), "p10": int(np.percentile(f, 10)), "loss_year_pct": round(float((f < 200000).mean() * 100), 1),
            "fall30_pct": round(float((d < -30).mean() * 100), 1)}


def per_lot(T):
    tr, ts = T[T.date < "2026"], T[T.date >= "2026"]
    return {"train": int(tr.pnl.sum()), "y2026": int(ts.pnl.sum()), "n": int(len(T)),
            "by_sym_train": tr.groupby("sym").pnl.sum().astype(int).to_dict(), "by_sym_2026": ts.groupby("sym").pnl.sum().astype(int).to_dict(),
            "years": T.groupby(T.date.str[:4]).pnl.sum().astype(int).to_dict()}


def shuffle_p(T, keep, n=4000, seed=1):
    """Probability that removing the same number of random 2023-25 trades does at least as well as this filter."""
    tr = T[T.date < "2026"]
    k_ = tr[~keep[tr.index]]
    if not len(k_):
        return None
    obs = -k_.pnl.sum()                      # gain from removing them
    x = tr.pnl.values; rng = np.random.default_rng(seed)
    sims = np.array([-x[rng.choice(len(x), len(k_), replace=False)].sum() for _ in range(n)])
    return round(float((sims >= obs).mean()), 4)


if __name__ == "__main__":
    T = gen() if ("--gen" in sys.argv or not os.path.exists(PKL)) else pickle.load(open(PKL, "rb"))
    hm = T.entry_time
    FILT = {
        "bank: drop Bank Nifty": T.sym != "BANKNIFTY",
        "bank: Bank Nifty Camarilla only": ~((T.sym == "BANKNIFTY") & (T.strategy == "Noise band")),
        "bank: Bank Nifty Noise only": ~((T.sym == "BANKNIFTY") & (T.strategy == "Camarilla")),
        "time: no entries after 13:30": hm <= "13:30",
        "time: no entries after 12:30": hm <= "12:30",
        "time: no entries before 09:30": hm >= "09:30",
        "gap: skip |gap| > 0.8%": T.gap.abs().fillna(0) <= 0.8,
        "gap: only trade the gap's direction": (np.sign(T.gap.fillna(0)) == T.sgn) | (T.gap.abs().fillna(0) < 0.1),
        "vix: skip VIX < 12": T.vix.fillna(15) >= 12,
        "vix: skip VIX > 16": T.vix.fillna(15) <= 16,
        "weekday: skip Mondays": T.wd != 0,
        "weekday: skip Fridays": T.wd != 4,
        "stack: at most 2 same-side open": T["stack"] < 2,
        "stack: at most 1 same-side open": T["stack"] < 1,
        "daystop: none after 2 losers": T.day_losers_before < 2,
        "score: skip 0-2": T.score >= 3,
    }
    base_pl = per_lot(T)
    base_acct = replay(T)
    out = {"baseline": {"per_lot": base_pl, "account": base_acct}, "filters": {}, "sizing": {}}
    print("BASE per lot", base_pl["train"], base_pl["y2026"], "| account", base_acct, flush=True)
    for nm, keep in FILT.items():
        X = T[keep]
        pl = per_lot(X)
        acct = replay(X)
        p = shuffle_p(T, keep)
        better = pl["train"] > base_pl["train"] and pl["y2026"] > base_pl["y2026"]
        out["filters"][nm] = {"per_lot": pl, "account": acct, "removed": int((~keep).sum()), "shuffle_p": p, "both_better": better}
        print(f"{nm:<42} removed {int((~keep).sum()):>4} | per lot {pl['train']:>8} {pl['y2026']:>8} | acct {acct['final']:>10,} dd {acct['dd']:>6} | p {p} {'BOTH+' if better else ''}", flush=True)
    SZ = {
        "daystop: none after the day is down 1.5% (account)": dict(daystop=("pct", 1.5)),
        "stack (account): at most 2 same-side": dict(stack_max=2),
        "ddsize: half risk while > 15% below peak": dict(size_fn=lambda r, eq, pk: 0.5 if eq < 0.85 * pk else 1.0),
        "ddsize: 1.25x risk while > 15% below peak": dict(size_fn=lambda r, eq, pk: 1.25 if eq < 0.85 * pk else 1.0),
        "vixsize: risk x 14/VIX (0.6-1.4x)": dict(size_fn=lambda r, eq, pk: min(max(14 / (r.vix if r.vix == r.vix and r.vix else 14), 0.6), 1.4)),
    }
    tr, ts = T[T.date < "2026"], T[T.date >= "2026"]
    for nm, kw in SZ.items():
        a = replay(T, **kw); a_tr = replay(tr, **kw); a_ts = replay(ts, **kw)
        out["sizing"][nm] = {"account": a, "from_2L_2023_25": a_tr, "from_2L_2026": a_ts}
        print(f"{nm:<52} acct {a['final']:>10,} dd {a['dd']:>6} | 2023-25 {a_tr['final']:>10,} | 2026 {a_ts['final']:>9,}", flush=True)
    out["baseline"]["from_2L_2023_25"] = replay(tr); out["baseline"]["from_2L_2026"] = replay(ts)
    print("BASE 2023-25", out["baseline"]["from_2L_2023_25"], "2026", out["baseline"]["from_2L_2026"])
    json.dump(out, open(os.path.join(HERE, "round43_results.json"), "w"), indent=1, default=str)
