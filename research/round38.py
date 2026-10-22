"""Round 38 (Oct 2026): why a Rs 20,000 / "always 1 lot" backtest shows about the same Net P&L for 30 and 60 days, and what
improves a small 1-lot account in ACTUAL conditions.

Step 1 - trades: the app's own backtester (fno.backtest.run, strategy "autopilot", sizing "one_lot", capital Rs 20,000) on all
recorded 5-minute history (Jan 2023 - 22 Oct 2026), for strikes 1 ITM / ATM / 1 OTM, per index.
Step 2 - "actual conditions" replay of those trades in time order with the live Autopilot rules the backtester does not apply:
  * trend-day slot only once equity >= Rs 40,000, Fibonacci slot only once equity >= Rs 2 lakh (service.py gates)
  * cash: the premiums of all open positions together must fit in the equity (the backtester checks each slot alone)
  * weekly loss limit 15% of equity below Rs 1 lakh (4% above), system trades only
  * the 1-lot floor only while one lot risks at most 15% of equity (risk per lot = |P&L / R|, or the index's median
    risk-to-premium ratio when |R| < 0.3)
Variants of the replay (what a small account could change):
  bt        - the app backtest as is (no gates, slots stack)
  live      - the live rules above
  live_1pos - live + at most one open position per index (no stacking of Camarilla + trend + Fib on the same move)
  live_tr0  - live but the trend-day slot from day one (no Rs 40k gate)
  live_tr0_1pos - both
Run: python round38.py [--gen] -> round38_results.json"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round25_harness as H25
from fno import backtest as B, config as C

CAP = 20_000
PKL = "/tmp/round38_trades.pkl"
SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
OTMS = {-1: "1 ITM (rule)", 0: "ATM", 1: "1 OTM"}


def gen():
    vix = H25.load("INDIAVIX")["close"]; out = {}
    for s in SYMS:
        c = H25.load(s)
        for o in OTMS:
            r = B.run(s, capital=CAP, candles=c, vix_intraday=vix, sizing="one_lot", strategy="autopilot", otm=o)
            T = pd.DataFrame(r["trades"]); T["sym"] = s; out[(s, o)] = T
            print(s, o, len(T), round(T.pnl.sum()), flush=True)
    pd.to_pickle(out, PKL)


def replay(T, mode):
    """T: trades of one or more indices. Returns the kept trades with running equity."""
    gates = mode != "bt"; one_pos = "1pos" in mode; tr0 = "tr0" in mode
    T = T.sort_values(["date", "entry_time"]).reset_index(drop=True)
    eq = CAP; kept = []; open_ = []; wk = None; wk_pnl = 0.0; wk_start = CAP
    for t in T.itertuples():
        ts_in, ts_out = f"{t.date} {t.entry_time}", f"{t.date} {t.exit_time}"
        cost = t.prem_in * t.lots * C.INDICES[t.sym]["lot"]
        if gates:
            # close finished positions
            for p in [p for p in open_ if p["out"] <= ts_in]:
                open_.remove(p)
            w = pd.Timestamp(t.date).to_period("W")
            if w != wk:
                wk, wk_pnl, wk_start = w, 0.0, eq
            lim = (C.AUTOPILOT_WEEKLY_SMALL if wk_start < C.AUTOPILOT_SMALL_ACCOUNT else 0.04) * wk_start
            if wk_pnl <= -lim:
                continue
            if t.strategy == "Trend-day" and not tr0 and eq < C.TREND_MIN_EQUITY:
                continue
            if t.strategy.startswith("Fibonacci") and eq < C.FIB_MIN_EQUITY:
                continue
            if one_pos and any(p["sym"] == t.sym for p in open_):
                continue
            if t.risk_lot > C.AUTOPILOT_MAX_TRADE_RISK * eq:
                continue
            if cost + sum(p["cost"] for p in open_) > eq:
                continue
            open_.append({"sym": t.sym, "out": ts_out, "cost": cost})
            wk_pnl += t.pnl
        eq += t.pnl
        kept.append({"date": t.date, "sym": t.sym, "strategy": t.strategy, "pnl": t.pnl, "eq": eq})
    return pd.DataFrame(kept)


def stats(K, days):
    if K.empty:
        return dict(n=0, net=0)
    eq = np.r_[CAP, K["eq"].values]; peak = np.maximum.accumulate(eq)
    K = K.assign(m=K.date.str[:7], y=K.date.str[:4])
    mon = K.groupby("m").pnl.sum()
    # rolling 30 / 60 trading-day windows: how often does a 60-day window make ~ the same as its last 30 days?
    d = pd.Series(K.groupby("date").pnl.sum()).reindex(days, fill_value=0.0)
    r30 = d.rolling(30).sum(); r60 = d.rolling(60).sum(); first30 = (r60 - r30).dropna()
    return dict(n=int(len(K)), net=round(float(K.pnl.sum())), end_equity=round(float(eq[-1])),
                max_fall=round(float((eq - peak).min())), lowest_equity=round(float(eq.min())),
                win=round(float((K.pnl > 0).mean() * 100), 1),
                months_up=f"{int((mon > 0).sum())}/{len(mon)}",
                top5_share=round(float(K.pnl.nlargest(5).sum() / K.pnl.sum()), 2) if K.pnl.sum() > 0 else None,
                years={y: round(float(v)) for y, v in K.groupby("y").pnl.sum().items()},
                by_strategy={k: round(float(v)) for k, v in K.groupby("strategy").pnl.sum().items()},
                median_30d=round(float(r30.median())), pct_30d_windows_negative=round(float((r30 < 0).mean() * 100)),
                pct_earlier30_negative=round(float((first30 < 0).mean() * 100)))


def rolling(T, mode, months=12):
    """Start a fresh Rs 20,000 account at every month start (2023-02 .. last full window) and run `months` months."""
    starts = pd.period_range("2023-02", pd.Period(T.date.max()[:7]) - months, freq="M")
    rows = []
    for st in starts:
        a, b = str(st.start_time.date()), str((st + months).start_time.date())
        K = replay(T[(T.date >= a) & (T.date < b)], mode)
        if K.empty:
            rows.append(dict(net=0, fall=0, low=CAP)); continue
        eq = np.r_[CAP, K["eq"].values]
        rows.append(dict(net=float(eq[-1] - CAP), fall=float((eq - np.maximum.accumulate(eq)).min()), low=float(eq.min())))
    R = pd.DataFrame(rows)
    return dict(windows=len(R), median_net=round(float(R.net.median())), p10_net=round(float(R.net.quantile(.1))),
                p90_net=round(float(R.net.quantile(.9))), pct_losing=round(float((R.net < 0).mean() * 100)),
                pct_below_half=round(float((R.low < CAP / 2).mean() * 100)), median_fall=round(float(R.fall.median())))


def why_30_60(T):
    """In the app backtest: over every 60-session window, how often did the EARLIER 30 sessions lose money?"""
    d = T.groupby("date").pnl.sum()
    days = sorted(set(T.date)); d = d.reindex(days, fill_value=0.0)
    r30 = d.rolling(30).sum(); r60 = d.rolling(60).sum(); first = (r60 - r30).dropna()
    near = ((r60 - r30).abs() < 0.15 * r30.abs()).dropna()
    return dict(pct_earlier30_negative=round(float((first < 0).mean() * 100)),
                pct_60_within_15pct_of_30=round(float(near.mean() * 100)),
                top5_days_share_of_net=round(float(d.nlargest(5).sum() / d.sum()), 2),
                best_10pct_days_share=round(float(d.nlargest(max(1, len(d) // 10)).sum() / d.sum()), 2))


if __name__ == "__main__":
    if "--gen" in sys.argv or not os.path.exists(PKL):
        gen()
    TR = pd.read_pickle(PKL)
    for k, T in TR.items():
        cost = T.prem_in * T.lots * C.INDICES[k[0]]["lot"]; big = T.R.abs() > 0.3
        ratio = float(((T.pnl / T.R).abs() / cost)[big].median())
        T["risk_lot"] = np.where(big, (T.pnl / T.R).abs() / T.lots, ratio * cost / T.lots)
    days = sorted({d for T in TR.values() for d in T.date})
    RES = {}
    books = {**{s: [s] for s in SYMS}, "NIFTY+SENSEX": ["NIFTY", "SENSEX"], "all three": SYMS}
    for bk, ss in books.items():
        for o, on in OTMS.items():
            T = pd.concat([TR[(s, o)] for s in ss])
            if bk in SYMS and o == -1:
                RES[f"{bk} | why 30 ~ 60"] = why_30_60(T); print(bk, "why", RES[f"{bk} | why 30 ~ 60"], flush=True)
            for mode in ["bt", "live", "live_1pos", "live_tr0", "live_tr0_1pos"]:
                st = {**stats(replay(T, mode), days), "rolling_12m_from_20k": rolling(T, mode)}
                RES[f"{bk} | {on} | {mode}"] = st
                print(bk, "|", on, "|", mode, st["rolling_12m_from_20k"], flush=True)
    json.dump(RES, open(os.path.join(HERE, "round38_results.json"), "w"), indent=1)
    print("saved")
