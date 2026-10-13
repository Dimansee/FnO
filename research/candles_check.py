"""Quick check - do classic candlestick patterns predict anything on daily NSE stocks (Nifty 500, 6 years)?

Patterns (standard textbook definitions, computed from adjusted OHLC):
  bullish:    hammer, bullish engulfing, morning star, piercing line
  bearish:    shooting star, bearish engulfing, evening star, dark cloud cover
  indecision: doji, spinning top, harami (bullish / bearish by context)
Each pattern is scored two ways, the same train (< 2025-07-01) / unseen (>= 2025-07-01) split as the stock rules:
  * the average forward return over 5 and 10 sessions, against ALL days (the "random control")
  * the hit rate of a +5% move (bullish) or -5% move (bearish) within 10 sessions, against the control
  * the same, but only when the pattern appears after a 5-day fall / rise (the "context" most books require)
A pattern is a usable signal only if it beats the control on BOTH halves by a clear margin.  usage: python candles_check.py -> candles_check.json
"""
import json
import numpy as np
import pandas as pd

d = pd.read_csv("data/stocks_1d.csv.gz", parse_dates=["date"]).sort_values(["sym", "date"])
f = (d["adj close"] / d["close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
for c in ("open", "high", "low", "close"):
    d[c] = d[c] * f
d = d[(d.volume > 0) & (d.high > d.low)].copy()
g = d.groupby("sym")
o, h, l, c = d.open, d.high, d.low, d.close
body = (c - o).abs(); rng = h - l; up_sh = h - np.maximum(o, c); lo_sh = np.minimum(o, c) - l
avg_body = body.groupby(d.sym).transform(lambda s: s.rolling(20, min_periods=10).mean())
o1, h1, l1, c1 = (g[k].shift(1) for k in ("open", "high", "low", "close"))
o2, c2 = g["open"].shift(2), g["close"].shift(2)
b1 = (c1 - o1).abs()
ret5 = g["close"].shift(-5) / c - 1; ret10 = g["close"].shift(-10) / c - 1
fwd_hi = g["high"].transform(lambda s: s[::-1].rolling(10, min_periods=1).max()[::-1].shift(-1)) / c - 1
fwd_lo = g["low"].transform(lambda s: s[::-1].rolling(10, min_periods=1).min()[::-1].shift(-1)) / c - 1
down5 = c1 < g["close"].shift(6) * 0.97          # 5-day fall before the candle (the context bullish reversals need)
up5 = c1 > g["close"].shift(6) * 1.03
small = body <= 0.1 * rng
P = {
    "hammer": (lo_sh >= 2 * body) & (up_sh <= 0.1 * rng) & (body > 0) & (body <= 0.35 * rng),
    "bullish engulfing": (c1 < o1) & (c > o) & (o <= c1) & (c >= o1) & (body > b1),
    "morning star": (c2 < o2) & ((o2 - c2) > avg_body) & ((c1 - o1).abs() < 0.3 * (o2 - c2)) & (c > o) & (c > (o2 + c2) / 2),
    "piercing line": (c1 < o1) & (c > o) & (o < l1) & (c > (o1 + c1) / 2) & (c < o1),
    "shooting star": (up_sh >= 2 * body) & (lo_sh <= 0.1 * rng) & (body > 0) & (body <= 0.35 * rng),
    "bearish engulfing": (c1 > o1) & (c < o) & (o >= c1) & (c <= o1) & (body > b1),
    "evening star": (c2 > o2) & ((c2 - o2) > avg_body) & ((c1 - o1).abs() < 0.3 * (c2 - o2)) & (c < o) & (c < (o2 + c2) / 2),
    "dark cloud cover": (c1 > o1) & (c < o) & (o > h1) & (c < (o1 + c1) / 2) & (c > o1),
    "doji": small,
    "spinning top": (body > 0.1 * rng) & (body <= 0.3 * rng) & (up_sh >= body) & (lo_sh >= body),
    "bullish harami": (c1 < o1) & (np.maximum(o, c) <= o1) & (np.minimum(o, c) >= c1) & (body < 0.6 * b1),
    "bearish harami": (c1 > o1) & (np.maximum(o, c) <= c1) & (np.minimum(o, c) >= o1) & (body < 0.6 * b1),
}
DIR = {"hammer": 1, "bullish engulfing": 1, "morning star": 1, "piercing line": 1, "shooting star": -1, "bearish engulfing": -1, "evening star": -1,
       "dark cloud cover": -1, "doji": 0, "spinning top": 0, "bullish harami": 1, "bearish harami": -1}
train = d.date < "2025-07-01"


def score(mask, side):
    out = {}
    for half, m in (("train", mask & train), ("unseen", mask & ~train)):
        m = m & ret10.notna()
        n = int(m.sum())
        if n < 30:
            out[half] = {"n": n}; continue
        r5, r10 = ret5[m], ret10[m]
        hit = (fwd_hi[m] >= 0.05).mean() if side >= 0 else (fwd_lo[m] <= -0.05).mean()
        adverse = (fwd_lo[m] <= -0.05).mean() if side >= 0 else (fwd_hi[m] >= 0.05).mean()
        out[half] = {"n": n, "avg_5d_pct": round(float(r5.mean() * 100), 2), "avg_10d_pct": round(float(r10.mean() * 100), 2),
                     "up_10d_pct": round(float((r10 > 0).mean() * 100), 1), "hit_5pct_in_10d": round(float(hit * 100), 1), "adverse_5pct_in_10d": round(float(adverse * 100), 1)}
    return out


res = {"control_all_days": score(pd.Series(True, index=d.index), 1), "control_all_days_bearish_view": score(pd.Series(True, index=d.index), -1),
       "control_after_5d_fall": score(down5, 1), "control_after_5d_rise": score(up5, -1), "patterns": {}}
for k, m in P.items():
    side = DIR[k]
    ctx = down5 if side >= 0 else up5
    res["patterns"][k] = {"direction": {1: "bullish", -1: "bearish", 0: "indecision"}[side], "any": score(m, side if side else 1),
                          "in_context": score(m & ctx, side if side else 1)}
    if side == 0:
        res["patterns"][k]["any_bearish_view"] = score(m, -1); res["patterns"][k]["in_context_after_rise_bearish_view"] = score(m & up5, -1)


def line(name, s, ctrl):
    t, u = s.get("train", {}), s.get("unseen", {})
    ct, cu = ctrl["train"], ctrl["unseen"]
    f = lambda x, k: f"{x[k]:+.2f}" if k in x else "  n/a"
    return (f"{name:38s} n={t.get('n',0):6d}/{u.get('n',0):5d} | avg10d train {f(t,'avg_10d_pct')} vs ctrl {ct['avg_10d_pct']:+.2f} | unseen {f(u,'avg_10d_pct')} vs ctrl {cu['avg_10d_pct']:+.2f}"
            f" | hit5% train {t.get('hit_5pct_in_10d','n/a')} vs {ct['hit_5pct_in_10d']} | unseen {u.get('hit_5pct_in_10d','n/a')} vs {cu['hit_5pct_in_10d']}")


print("control (all days, bullish view):", res["control_all_days"])
print("control (all days, bearish view):", res["control_all_days_bearish_view"])
for k, v in res["patterns"].items():
    side = DIR[k]
    ctrl = res["control_all_days"] if side >= 0 else res["control_all_days_bearish_view"]
    cctx = res["control_after_5d_fall"] if side >= 0 else res["control_after_5d_rise"]
    print(line(k + " (any)", v["any"], ctrl))
    print(line(k + " (after 5d " + ("fall" if side >= 0 else "rise") + ")", v["in_context"], cctx))
# verdict: beats the context control on avg 10d AND hit rate on both halves, in the pattern's direction
verdict = {}
for k, v in res["patterns"].items():
    side = DIR[k] or 1
    cctx = res["control_after_5d_fall"] if side > 0 else res["control_after_5d_rise"]
    ok = True
    for half in ("train", "unseen"):
        s = v["in_context"].get(half, {})
        if "avg_10d_pct" not in s:
            ok = False; break
        better_avg = side * (s["avg_10d_pct"] - cctx[half]["avg_10d_pct"]) >= 0.5
        better_hit = s["hit_5pct_in_10d"] - cctx[half]["hit_5pct_in_10d"] >= 3
        ok = ok and better_avg and better_hit
    verdict[k] = "beats control on both halves" if ok else "no edge over control"
res["verdict"] = verdict
print("\nverdict:", verdict)
json.dump(res, open("candles_check.json", "w"), indent=1)

# does a bullish candle on the signal day improve the tested stock rule (3-day drop >= 10% while above the 200-day average)?
sma200 = g["close"].transform(lambda s: s.rolling(200, min_periods=120).mean())
rule = (c / g["close"].shift(3) - 1 <= -0.10) & (c > sma200)
hit5 = (fwd_hi >= 0.05)
bull_any = P["hammer"] | P["bullish engulfing"] | P["morning star"] | P["piercing line"] | P["bullish harami"]
indec = P["doji"] | P["spinning top"]
bear_any = P["shooting star"] | P["bearish engulfing"] | P["evening star"] | P["dark cloud cover"] | P["bearish harami"]
rule_res = {}
for name, m in (("rule (all)", rule), ("rule + bullish candle", rule & bull_any), ("rule + doji/spinning top", rule & indec), ("rule + bearish candle", rule & bear_any), ("rule, no bearish candle", rule & ~bear_any)):
    rule_res[name] = score(m, 1)
    t, u = rule_res[name]["train"], rule_res[name]["unseen"]
    print(f"{name:28s} train n={t['n']:5d} hit5%={t.get('hit_5pct_in_10d')} avg10d={t.get('avg_10d_pct')} | unseen n={u['n']:4d} hit5%={u.get('hit_5pct_in_10d')} avg10d={u.get('avg_10d_pct')}")
res["rule_with_candles"] = rule_res
json.dump(res, open("candles_check.json", "w"), indent=1)
