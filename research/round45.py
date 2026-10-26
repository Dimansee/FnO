"""Round 45 (26 Oct 2026): "the market reclaimed yesterday's close and kept going" - can that be a rule?

Trigger: 26 Oct Nifty opened 22,480 (gap down 0.5% below the 22,600 close), climbed back above 22,600 around 10:00, held it
and reached ~22,700. The user: "if it sustains 22,600 it may go to 22,700 - why can't the setup take that?"

Rule tested (fixed before looking at results):
  gap      today's open at least G% away from yesterday's close (G = 0.3%)
  reclaim  a 5-min close back across yesterday's close, 09:20-13:00 (first time today)
  sustain  N consecutive 5-min closes on the far side (N = 1 = at once, 2 = "sustains" 10 min, 3 = 15 min); entry at that close
  stop     the day's extreme so far (gap low for a CALL, gap high for a PUT), kept between 1 and 3 ATR(14, 5-min)
  exit     2R target / stop / 15:15 square-off  (also tested: no target, hold to 15:15)
Mirror: a gap UP that falls back below yesterday's close = PUT.
Judged in R (index points / risk) and rough rupees per lot (option delta 0.6, Rs 60 costs a round trip), 2023-25 vs 2026,
per index. Compared with the opposite trade on the same bars (does the direction matter at all?).
Run: python round45.py
"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round25_harness as H

SYMS = {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}


def atr(df, n=14):
    pc = df.close.shift()
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def trades(sym, gap_min=0.3, sustain=1, target_r=2.0, flip=False):
    c = H.load(sym).copy()
    c["atr"] = atr(c)
    c["d"] = c.index.date
    days = sorted(set(c.d))
    out, prev_close = [], None
    for d in days:
        g = c[c.d == d]
        if prev_close is None or len(g) < 60:
            prev_close = float(g.close.iloc[-1]) if len(g) else prev_close
            continue
        o = float(g.open.iloc[0])
        gap = (o / prev_close - 1) * 100
        pc = prev_close
        prev_close = float(g.close.iloc[-1])
        if abs(gap) < gap_min:
            continue
        sd = 1 if gap < 0 else -1                       # gap down -> reclaim upward -> CALL
        t = g.index.time
        win = (t >= pd.Timestamp("09:20").time()) & (t <= pd.Timestamp("13:00").time())
        cl = g.close.values
        beyond = (cl - pc) * sd > 0
        run, k = 0, None
        for i in range(len(g)):
            run = run + 1 if beyond[i] else 0
            if run >= sustain and win[i]:
                k = i
                break
            if t[i] > pd.Timestamp("13:00").time():
                break
        if k is None:
            continue
        e = cl[k]
        ext = g.low.values[:k + 1].min() if sd > 0 else g.high.values[:k + 1].max()
        a = float(g.atr.iloc[k]) if g.atr.iloc[k] == g.atr.iloc[k] else float((g.high - g.low).iloc[:k + 1].mean())
        risk = min(max(abs(e - ext), a), 3 * a)
        s = sd * (-1 if flip else 1)
        stop, tgt = e - s * risk, (e + s * target_r * risk if target_r else None)
        R, why = None, "15:15"
        for j in range(k + 1, len(g)):
            hi, lo = g.high.values[j], g.low.values[j]
            if (s > 0 and lo <= stop) or (s < 0 and hi >= stop):
                R, why = -1.0, "stop"
                break
            if tgt is not None and ((s > 0 and hi >= tgt) or (s < 0 and lo <= tgt)):
                R, why = target_r, "target"
                break
            if t[j] >= pd.Timestamp("15:15").time():
                R = s * (cl[j] - e) / risk
                break
        if R is None:
            R = s * (cl[-1] - e) / risk
        out.append({"sym": sym, "date": str(d), "y": str(d)[:4], "gap": round(gap, 2), "side": "CALL" if s > 0 else "PUT", "time": str(t[k])[:5],
                    "entry": round(e, 1), "risk_pts": round(risk, 1), "R": round(R, 3), "why": why,
                    "rs_lot": round(R * risk * 0.6 * SYMS[sym] - 60, 0)})
    return pd.DataFrame(out)


def stats(df):
    if df.empty:
        return {"n": 0}
    w, l = df.rs_lot[df.rs_lot > 0].sum(), -df.rs_lot[df.rs_lot <= 0].sum()
    return {"n": int(len(df)), "win": round(float((df.R > 0).mean() * 100), 1), "avg_R": round(float(df.R.mean()), 3),
            "rs_lot": int(df.rs_lot.sum()), "pf": round(float(w / l), 2) if l else None}


if __name__ == "__main__":
    res = {}
    for sustain in (1, 2, 3):
        for tr in (2.0, 0):
            for flip in (False, True):
                T = pd.concat([trades(s, 0.3, sustain, tr, flip) for s in SYMS], ignore_index=True)
                key = f"sustain {sustain} bar(s) · {'2R target' if tr else 'hold to 15:15'}{' · OPPOSITE trade' if flip else ''}"
                r = {"2023-25": stats(T[T.y < "2026"]), "2026": stats(T[T.y == "2026"]),
                     "by_index_2023_25": {s: stats(T[(T.sym == s) & (T.y < "2026")]) for s in SYMS},
                     "by_index_2026": {s: stats(T[(T.sym == s) & (T.y == "2026")]) for s in SYMS},
                     "by_year": {y: stats(g) for y, g in T.groupby("y")}}
                res[key] = r
                print(f"{key:<55} 23-25 n {r['2023-25']['n']:>3} win {r['2023-25'].get('win')} avgR {r['2023-25'].get('avg_R')} Rs/lot {r['2023-25'].get('rs_lot')} | "
                      f"2026 n {r['2026']['n']:>3} win {r['2026'].get('win')} avgR {r['2026'].get('avg_R')} Rs/lot {r['2026'].get('rs_lot')}", flush=True)
    T = trades("NIFTY", 0.3, 2, 2.0)
    res["nifty_recent"] = T.tail(12).to_dict("records")
    json.dump(res, open(os.path.join(HERE, "round45_results.json"), "w"), indent=1, default=str)
