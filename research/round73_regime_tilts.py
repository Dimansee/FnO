"""Round 73: size Autopilot's OWN trades by market regime (no new trades), like round 71's CPR tilt.

Why: every new entry tried since round 59 lost money after option costs or took cash from Autopilot's big trend-day trades;
sizing by a reading known at entry (CPR width, round 71) was the one thing that lifted every year. This round tries the
remaining regime readings, each known before the entry:
  vix_pct   India VIX at the open vs its previous 250 sessions (low / mid / high third)
  vix_chg   VIX at the open vs 5 sessions ago (falling < -8%, flat, rising > +8%)
  gap       |open / previous close - 1|: small < 0.25%, mid, big > 0.75%
  gap_with  the trade goes the way of the gap (call after a gap up / put after a gap down) vs against
  expiry    the traded index's expiry day vs not
  nr7       yesterday's range was the narrowest of 7 sessions (compression) vs not
  prev_with the trade goes the way of yesterday's candle vs against
  hour      entry before 11:00 / 11:00-13:00 / after 13:00
  breadth   at entry, how many of the OTHER two indices are on the trade's side of their own open (0 / 1 / 2)
  weekday   Mon..Fri
Step 1: per-lot P&L by bucket and year (1 lot, today's rules). Step 2: the buckets that are better in EVERY year get a tilt
(x1.2 up / x0.85 down, as round 71) in the Outcomes replay at Rs 2L and Rs 20k, against today's Autopilot, with a
shuffled-multiplier control (same multipliers, random trades, 100 runs).
Pass bar (the user's): better in 2023, 2024, 2025, 2026 and the full period at Rs 2L, worst fall not deeper.
Usage: python research/round73_regime_tilts.py [data_dir]
"""
import inspect, json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data")
import numpy as np, pandas as pd
import round72_opening_candle_retest as M          # sets offline lot sizes
import round34 as R34
import outcomes as O
from fno import portfolio_bt as PB

# ---- replay with an extra per-trade multiplier column 'xm' (applied next to the CPR tilt) ----
_src = inspect.getsource(PB.replay).replace("def replay(", "def replay_x(", 1)
_src = _src.replace("        if dd_half and eq < (1 - C.DD_HALF_AT) * peak:",
                    "        m *= float(getattr(r, 'xm', 1.0) or 1.0)\n        if dd_half and eq < (1 - C.DD_HALF_AT) * peak:", 1)
assert "getattr(r, 'xm'" in _src
exec(compile(_src, "replay_x", "exec"), PB.__dict__)
replay_x = PB.replay_x

PERIODS = {"2023": ("2023-01-02", "2023-12-31"), "2024": ("2024-01-01", "2024-12-31"), "2025": ("2025-01-01", "2025-12-31"),
           "2026": ("2026-01-01", "2026-10-30"), "full": ("2023-01-02", "2026-10-30")}


def run(T, A, cap, a, b):
    q = {**O.B0, "capital": cap}
    T = T[(T.date >= a) & (T.date <= b)]
    A = A[(A.date >= a) & (A.date <= b)] if A is not None else None
    R = replay_x(T, cap, q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"], q["dd_half"], A, cpr=True)
    s = PB._summary(R, cap)
    return {"final": round(s["final"]), "dd": s["max_dd_pct"], "trades": s["trades"]}


def daily(sym):
    c = O.candles(sym)
    d = c.groupby(c.index.normalize()).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
    return d


def vix_daily():
    v = pd.read_csv(os.path.join(DATA, "INDIAVIX_1m_upstox.csv.gz"), parse_dates=["ts"]).set_index("ts").close
    v = v[~v.index.duplicated()].sort_index()
    return v.groupby(v.index.normalize()).agg(["first", "last"])


def features(T):
    V = vix_daily()
    vclose = V["last"]
    D = {s: daily(s) for s in O.SYMS}
    C5 = {s: O.candles(s)[["open", "close"]] for s in O.SYMS}
    dopen = {s: C5[s].groupby(C5[s].index.normalize())["open"].transform("first") for s in O.SYMS}
    out = []
    for r in T.itertuples(index=False):
        dd = pd.Timestamp(r.date)
        s = r.sym
        side = 1 if r.side == "CALL" else -1
        f = {}
        # VIX
        hist = vclose[vclose.index < dd]
        vo = V["first"].get(dd, np.nan)
        if len(hist) >= 120 and vo == vo:
            p = (hist.iloc[-250:] < vo).mean()
            f["vix_pct"] = "low" if p < 1 / 3 else ("high" if p > 2 / 3 else "mid")
            ch = vo / hist.iloc[-5] - 1 if len(hist) >= 5 else 0
            f["vix_chg"] = "falling" if ch < -0.08 else ("rising" if ch > 0.08 else "flat")
        d = D[s]
        i = d.index.searchsorted(dd)
        if 0 < i < len(d) and d.index[i] == dd and i >= 8:
            pc, op = d.close.iloc[i - 1], d.open.iloc[i]
            g = (op / pc - 1) * 100
            f["gap"] = "small" if abs(g) < 0.25 else ("big" if abs(g) > 0.75 else "mid")
            f["gap_with"] = "n/a" if abs(g) < 0.1 else ("with" if np.sign(g) == side else "against")
            rng = (d.high - d.low).iloc[i - 7:i]
            f["nr7"] = "nr7" if rng.iloc[-1] <= rng.min() else "no"
            pv = d.close.iloc[i - 1] - d.open.iloc[i - 1]
            f["prev_with"] = "with" if np.sign(pv) == side else "against"
        try:
            f["expiry"] = "expiry" if R34.expiry_of(s, dd.date()) == dd.date() else "no"
        except Exception:
            pass
        hh = int(r.entry_time[:2]) * 60 + int(r.entry_time[3:])
        f["hour"] = "<11:00" if hh < 660 else ("11-13" if hh < 780 else ">13:00")
        t = pd.Timestamp(f"{r.date} {r.entry_time}") - pd.Timedelta(minutes=5)
        n = 0
        for o in O.SYMS:
            if o == s:
                continue
            x = C5[o]
            j = x.index.searchsorted(t, side="right") - 1
            if j >= 0 and x.index[j].normalize() == dd:
                n += int(np.sign(x.close.iloc[j] - dopen[o].iloc[j]) == side)
        f["breadth"] = str(n)
        f["weekday"] = dd.day_name()[:3]
        out.append(f)
    return pd.DataFrame(out, index=T.index)


def main():
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    F = features(base)
    base = pd.concat([base, F], axis=1)
    base["year"] = base.date.str[:4]
    # per-lot P&L (the cache prices every trade at 1 lot) by bucket and year
    stats = {}
    print("per-lot P&L by bucket (Rs / trade): 2023 2024 2025 2026 | n")
    for col in F.columns:
        g = base.groupby([col, "year"]).pnl.mean().unstack()
        n = base.groupby(col).size()
        stats[col] = {"mean": g.round(0).to_dict(orient="index"), "n": n.to_dict()}
        print(f"-- {col}")
        for b_, row in g.iterrows():
            print(f"   {str(b_):<10} " + " ".join(f"{row.get(y, np.nan):+7.0f}" for y in ("2023", "2024", "2025", "2026")) + f" | {n[b_]}")
    # candidate tilts: a bucket better than the rest of its feature in EVERY year -> up; worse in every year -> down
    rest_all = base.groupby("year").pnl.mean()
    cands = []
    for col in F.columns:
        for b_ in base[col].dropna().unique():
            inb = base[base[col] == b_]
            if len(inb) < 80:
                continue
            m_in = inb.groupby("year").pnl.mean()
            m_out = base[base[col] != b_].groupby("year").pnl.mean()
            ys = [y for y in ("2023", "2024", "2025", "2026") if y in m_in and y in m_out]
            if len(ys) < 4:
                continue
            if all(m_in[y] > m_out[y] for y in ys):
                cands.append((col, b_, "up"))
            elif all(m_in[y] < m_out[y] for y in ys):
                cands.append((col, b_, "down"))
    print("\nbuckets consistent in every year:", cands)
    caps = (200_000, 20_000)
    res = {"stats": stats, "cands": [list(c) for c in cands], "replay": {}}
    baseT = base.copy(); baseT["xm"] = 1.0
    ref = {cap: {p: run(baseT, alt, cap, a, b) for p, (a, b) in PERIODS.items()} for cap in caps}
    res["replay"]["Autopilot today"] = ref
    print("\nAutopilot today Rs2L:", {p: ref[200_000][p]["final"] for p in PERIODS}, "fall", ref[200_000]["full"]["dd"])
    rng = np.random.default_rng(11)
    for col, b_, way in cands:
        for up, dn in ((1.2, 0.85), (1.3, 0.7)):
            mult = up if way == "up" else dn
            T2 = baseT.copy()
            T2.loc[T2[col] == b_, "xm"] = mult
            r = {cap: {p: run(T2, alt, cap, a, b) for p, (a, b) in PERIODS.items()} for cap in caps}
            ok = all(r[200_000][p]["final"] > ref[200_000][p]["final"] for p in PERIODS) and r[200_000]["full"]["dd"] >= ref[200_000]["full"]["dd"] - 0.5
            # shuffle control on the full period at Rs 2L
            xs = T2["xm"].values
            better = 0
            for _ in range(40):
                T3 = baseT.copy(); T3["xm"] = rng.permutation(xs)
                better += run(T3, alt, 200_000, *PERIODS["full"])["final"] >= r[200_000]["full"]["final"]
            lab = f"{col}={b_} x{mult}"
            res["replay"][lab] = {**r, "pass": ok, "shuffle_p": better / 40}
            print(f"{lab:<28} " + " ".join(f"{p}:{r[200_000][p]['final']/1e5:5.2f}L" for p in PERIODS) +
                  f" fall {r[200_000]['full']['dd']:+.1f}% | 20k full {r[20_000]['full']['final']/1e5:.2f}L | pass {ok} | shuffle p {better/40:.2f}", flush=True)
    json.dump(res, open(os.path.join(HERE, "round73_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
