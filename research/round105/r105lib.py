"""Round 105 helpers (23 Nov 2026): 'after a 2-3 day one-way run, trade the other way'.
Streak = consecutive daily closes in one direction (close vs previous close), known at the previous day's close."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)).replace("round105", "round103"))
import r103lib as R3
import numpy as np, pandas as pd
L = R3.L; O = L.O; M = L.M
SYMS = L.SYMS


def daily_from_5m(sym):
    c = O.candles(sym)
    g = c.groupby(c.index.date)
    d = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last()})
    d.index = pd.to_datetime(d.index)
    return d


def daily_long(sym):
    """Long daily history (file) extended with the 5-min days after it ends."""
    a = pd.read_csv(f"{R3.ROOT}/research/data/idx_daily_{sym}.csv.gz", parse_dates=["ts"]).set_index("ts")[["open", "high", "low", "close"]]
    b = daily_from_5m(sym)
    return pd.concat([a, b[b.index > a.index[-1]]]).sort_index()


def streaks(d):
    """Adds: run (signed count of consecutive closes one way, as of that day's close), cum (% move over the run),
    and p_run / p_cum = the same known BEFORE the day starts (yesterday's values)."""
    ch = np.sign(d.close.diff().fillna(0).values).astype(int)
    c = d.close.values; run = np.zeros(len(d), int); start = np.zeros(len(d))
    for i in range(1, len(d)):
        if ch[i] != 0 and ch[i] == np.sign(run[i - 1]):
            run[i] = run[i - 1] + ch[i]; start[i] = start[i - 1]
        else:
            run[i] = ch[i]; start[i] = c[i - 1]
    d = d.copy(); d["run"] = run
    d["cum"] = np.where(run != 0, (c / np.where(start > 0, start, np.nan) - 1) * 100, 0.0)
    d["p_run"] = d.run.shift(1).fillna(0).astype(int); d["p_cum"] = d.cum.shift(1).fillna(0.0)
    d["pc"] = d.close.shift(1)
    return d
