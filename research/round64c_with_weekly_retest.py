"""Round 64c: round 64b again WITH the app's weekly re-test (fno/gautopilot.qualify) replayed week by week.
Each Monday 00:00 UTC a rule may trade only if its trades closed in the look-back (4h rules 365 days, daily rules 1,095 days,
or whatever data exists) number >= 30 with avg >= +0.03R and PF >= 1.05; a coin is paused if it has >= 6 such trades averaging
below -0.10R. Then the money simulation (Rs 2L, 1% risk, max 4 open). Run from the repo root."""
import sys, numpy as np, pandas as pd
sys.path.insert(0, 'research'); sys.path.insert(0, '.')
import round64_untested_signals as M, round55_engine as E
from fno import gbacktest as GBT
M.DATA = sys.argv[1] if len(sys.argv) > 1 else 'research/data/global'
LOOK = {"4h": 365, "1d": 1095}


def all_trades():
    out = []
    for rule, (tf, kind, trail) in M.LIVE.items():
        for sym in M.C11:
            df = M.load(sym, tf); o, h, l, c = (df[k].values for k in ("open", "high", "low", "close")); a = E.atr(df); n = len(c)
            side = np.zeros(n, np.int64); ol = np.zeros(n, np.bool_); osr = np.zeros(n, np.bool_)
            if kind == "st":
                dr = E.supertrend_dir(h, l, c, a, 3.0); fl = np.r_[False, (dr[1:] == 1) & (dr[:-1] == -1)]; fs = np.r_[False, (dr[1:] == -1) & (dr[:-1] == 1)]
                side[fl], side[fs] = 1, -1; ol, osr = fl, fs
            else:
                up = pd.Series(h).rolling(kind).max().shift(1).values; side[(c > up) & np.r_[False, c[:-1] <= up[:-1]]] = 1
            side[:60] = 0
            nanv = np.full(n, np.nan)
            en, sd, R, b, g = E.run(o, h, l, c, a, side, nanv, nanv, ol, osr, 0.0, trail, 0.0, 0.0, 0, 0.0, 2.0)
            step = pd.Timedelta(hours=4) if tf == "4h" else pd.Timedelta(days=1)
            for k in range(len(en)):
                i = en[k]; e = o[i]
                out.append({"symbol": sym, "rule": rule, "tf": tf, "entry_ts": df.index[i], "exit_ts": df.index[min(i + b[k] - 1, n - 1)] + step,
                            "entry": e, "sl0": e - sd[k] * 2 * a[i - 1], "R": R[k]})
    return pd.DataFrame(out)


def gate(T):
    """Keep trades whose rule (and coin) passed the most recent weekly re-test before entry."""
    keep = np.zeros(len(T), bool)
    T = T.reset_index(drop=True)
    weeks = pd.date_range("2023-01-02", "2026-11-02", freq="W-MON", tz="UTC")
    for rule, G in T.groupby("rule"):
        lb = pd.Timedelta(days=LOOK[G.tf.iloc[0]])
        for w0, w1 in zip(weeks[:-1], weeks[1:]):
            past = G[(G.exit_ts < w0) & (G.exit_ts >= w0 - lb)]
            r = past.R.values
            if len(r) < 30 or r.mean() < 0.03:
                continue
            lo = -r[r <= 0].sum()
            if lo > 0 and r[r > 0].sum() / lo < 1.05:
                continue
            per = past.groupby("symbol").R.agg(["count", "mean"])
            bad = set(per[(per["count"] >= 6) & (per["mean"] < -0.10)].index)
            m = (G.entry_ts >= w0) & (G.entry_ts < w1) & (~G.symbol.isin(bad))
            keep[G.index[m]] = True
    return T[keep]


if __name__ == "__main__":
    T = all_trades()
    T = T[T.entry_ts >= pd.Timestamp("2023-01-01", tz="UTC")].reset_index(drop=True) if False else T
    Tg = gate(T)
    Tg = Tg[Tg.entry_ts >= pd.Timestamp("2023-01-01", tz="UTC")]
    print("trades after the weekly gate:", Tg.groupby("rule").R.agg(["count", "mean"]).round(3).to_dict())
    P = {"all": ("2023-01-01", "2026-12-31"), "early": ("2023-01-01", "2025-03-31"), "late": ("2025-04-01", "2026-12-31")}
    three = ["Supertrend 4h", "Supertrend daily", "Donchian 20 daily"]
    C = {"Live book (4 rules, 1x)": (None, 1), "Drop Donchian 55 4h, 1x": (three, 1), "All 4 rules, 3x": (None, 3),
         "Drop Donchian 55 4h + 3x": (three, 3), "Drop Donchian 55 4h + 2x": (three, 2), "All 4 rules, 2x": (None, 2)}
    for nm, (rules, lev) in C.items():
        X = Tg if rules is None else Tg[Tg.rule.isin(rules)]
        s = f"{nm:<28}"
        for k, (a, b) in P.items():
            A, B = pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC")
            m = GBT.money_sim(X[(X.entry_ts >= A) & (X.entry_ts <= B)].to_dict("records"), 200000, 1.0, lev, 4)
            s += f" | {k} Rs {m['final']/1e5:.2f}L ({m['max_dd_pct']:.0f}%)"
        print(s)
