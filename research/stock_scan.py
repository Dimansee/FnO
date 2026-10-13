"""Nightly stock scan -> research/data/stock_picks.json (the app's Picks view).

Runs every trading day. Three tiers, strongest first, each with entry, target, risk line, exit rule and a strength score:
  rule      the tested rule (round 16): close above the 200-day average, down more than 10% over the last three closes,
            Nifty 500 with >= ₹1 cr median daily turnover. Buy next open, +5% limit sell, out at the open after the 5th close.
            Backtest 2021-26: 64% hit the target, +1.4% a trade after costs, ~14 signals a month on the full list.
  near      the same setup with a 7-10% drop (tested variant: weaker - 58% hit, roughly break-even on training, +12%/yr unseen).
  breakout  52-week-high breakout on volume >= 1.5x its 50-day average, above the 200-day average (tested: 44% wins, 23%/yr training,
            8%/yr unseen with a 15% trailing stop - a momentum setup that pays in trend years and bleeds in flat ones).
Strength (0-100) = how closely the stock matches the tested setup (drop size, trend margin, liquidity, RSI, volume), weighted by the
tier's measured win rate. It is a match score, not a prediction. Risk lines are safety limits for the paper exercise; the tested
rules themselves exit on time/target (a 7% stop made the mean-reversion rule worse in the backtest).
Earlier picks are settled from the data so the app shows what each one did.
"""
import json, os
from datetime import date, datetime, timedelta, timezone
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "stock_picks.json")
IST = timezone(timedelta(hours=5, minutes=30))
MAXPOS, HOLD, LOOKBACK_DAYS, MIN_TURN = 10, 5, 60, 1e7

d = pd.read_csv(os.path.join(HERE, "data", "stocks_1d.csv.gz"), parse_dates=["date"]).sort_values(["sym", "date"])
f = (d["adj close"] / d["close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
for c in ("open", "high", "low", "close"):
    d[c] = d[c] * f
C = d.pivot(index="date", columns="sym", values="close"); O = d.pivot(index="date", columns="sym", values="open")
H = d.pivot(index="date", columns="sym", values="high"); L = d.pivot(index="date", columns="sym", values="low"); V = d.pivot(index="date", columns="sym", values="volume")
cover = C.notna().mean(axis=1); fresh = ((C != C.shift(1)) | (V != V.shift(1))).where(C.notna()).mean(axis=1)
ok = (cover >= 0.5) & (fresh >= 0.2)
while len(C) > 1 and not ok.iloc[len(C) - 1]:
    C, O, H, L, V = C.iloc[:-1], O.iloc[:-1], H.iloc[:-1], L.iloc[:-1], V.iloc[:-1]
names = pd.read_csv(os.path.join(HERE, "data", "stocks_list.csv")).set_index("Symbol")
SMA200 = C.rolling(200, min_periods=200).mean(); R3 = C / C.shift(3) - 1
TURN = (C * V).rolling(50, min_periods=20).median(); VOL50 = V.rolling(50, min_periods=50).mean()
HI252 = H.rolling(252, min_periods=252).max().shift(1)


def rsi(s, n=14):
    ch = s.diff(); up = ch.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(); dn = (-ch.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


RSI = C.apply(rsi)
dates = C.index
TIERS = {
    "rule": {"label": "Rule pick", "win": 64, "target_pct": 5.0, "hold": 5, "risk_pct": 8.0, "exit": "+5% limit sell at once; sell at the open after the 5th close if not filled. Safety line −8% (not part of the tested rule)."},
    "near": {"label": "Near-rule (weaker)", "win": 58, "target_pct": 5.0, "hold": 5, "risk_pct": 8.0, "exit": "Same as the rule pick: +5% target, out after 5 closes. Weaker setup - smaller size."},
    "breakout": {"label": "Breakout watch", "win": 44, "target_pct": 15.0, "hold": 60, "risk_pct": 10.0, "exit": "Trail a 15% stop from the highest close; out after 60 sessions. Safety line −10%."},
}


def row(s, i, tier, strength, basis):
    c = float(C.iloc[i][s]); t = TIERS[tier]
    return {"sym": s, "name": str(names["Company Name"].get(s, s)), "industry": str(names["Industry"].get(s, "")), "tier": tier, "setup": t["label"], "strength": int(round(strength)),
            "close": round(c, 2), "entry": "next open (reference ₹%.2f)" % c, "entry_ref": round(c, 2), "target": round(c * (1 + t["target_pct"] / 100), 2), "target_pct": t["target_pct"],
            "risk_line": round(c * (1 - t["risk_pct"] / 100), 2), "risk_pct": t["risk_pct"], "exit_rule": t["exit"], "hold_days": t["hold"], "basis": basis,
            "drop_3d_pct": round(float(R3.iloc[i][s] * 100), 1) if not pd.isna(R3.iloc[i][s]) else None,
            "above_200dma_pct": round(float((c / SMA200.iloc[i][s] - 1) * 100), 1) if not pd.isna(SMA200.iloc[i][s]) else None,
            "rsi14": round(float(RSI.iloc[i][s])) if not pd.isna(RSI.iloc[i][s]) else None,
            "turnover_cr": round(float(TURN.iloc[i][s] / 1e7), 1) if not pd.isna(TURN.iloc[i][s]) else None,
            "backtest_win_pct": t["win"]}


def picks_on(i):
    out = []
    c, r3, sma, tu, rs, hi, v, v50 = C.iloc[i], R3.iloc[i], SMA200.iloc[i], TURN.iloc[i], RSI.iloc[i], HI252.iloc[i], V.iloc[i], VOL50.iloc[i]
    liq = tu >= MIN_TURN
    for s in C.columns:
        if pd.isna(c[s]) or pd.isna(sma[s]) or not liq.get(s, False):
            continue
        above = c[s] / sma[s] - 1
        if above <= 0:
            if not pd.isna(hi[s]) and c[s] > hi[s] and v50[s] and v[s] >= 1.5 * v50[s] and above > 0:
                pass
            continue
        drop = r3[s]
        liqscore = min(1.0, np.log10(max(tu[s], 1e7) / 1e7) / 2)            # ₹1 cr -> 0, ₹100 cr -> 1
        if drop <= -0.10:
            st = 55 + min(20, (-drop - 0.10) * 200) + 10 * liqscore + (10 if 20 <= (rs[s] or 50) <= 40 else 0) + min(5, above * 50)
            out.append(row(s, i, "rule", min(100, st), f"3-day drop {drop*100:.1f}% while {above*100:.1f}% above the 200-day average; RSI {rs[s]:.0f}; turnover ₹{tu[s]/1e7:.0f} cr/day"))
        elif drop <= -0.07:
            st = 35 + (-drop - 0.07) * 300 + 10 * liqscore + (5 if 20 <= (rs[s] or 50) <= 40 else 0)
            out.append(row(s, i, "near", min(100, st), f"3-day drop {drop*100:.1f}% (7-10% band) while {above*100:.1f}% above the 200-day average; RSI {rs[s]:.0f}"))
        if not pd.isna(hi[s]) and c[s] > hi[s] and v50[s] and v[s] >= 1.5 * v50[s]:
            st = 30 + min(15, (v[s] / v50[s] - 1.5) * 10) + 10 * liqscore + min(10, above * 50)
            out.append(row(s, i, "breakout", min(100, st), f"new 52-week high on {v[s]/v50[s]:.1f}x average volume, {above*100:.1f}% above the 200-day average"))
    out.sort(key=lambda r: -r["strength"])
    return out


def settle(p, i):
    s, t = p["sym"], TIERS[p["tier"]]
    if i + 1 >= len(dates):
        return None
    entry = float(O.iloc[i + 1][s])
    if pd.isna(entry):
        return None
    tgt, risk = entry * (1 + t["target_pct"] / 100), entry * (1 - t["risk_pct"] / 100)
    peak = entry
    for k in range(i + 1, min(i + 1 + t["hold"], len(dates))):
        hi_, lo_, cl_ = H.iloc[k][s], L.iloc[k][s], C.iloc[k][s]
        if pd.isna(cl_):
            continue
        if p["tier"] == "breakout":
            peak = max(peak, cl_)
            if cl_ <= peak * 0.85 or lo_ <= risk:
                px = cl_ if cl_ <= peak * 0.85 else risk
                return {"entry": round(entry, 2), "exit": round(px, 2), "exit_date": dates[k].date().isoformat(), "how": "trailing stop" if cl_ <= peak * 0.85 else "risk line", "ret_pct": round((px / entry - 1) * 100 - 0.2, 2)}
            if hi_ >= tgt:
                return {"entry": round(entry, 2), "exit": round(tgt, 2), "exit_date": dates[k].date().isoformat(), "how": f"target +{t['target_pct']:.0f}%", "ret_pct": round((tgt / entry - 1) * 100 - 0.2, 2)}
        else:
            if lo_ <= risk and k > i + 1:
                return {"entry": round(entry, 2), "exit": round(risk, 2), "exit_date": dates[k].date().isoformat(), "how": "risk line", "ret_pct": round((risk / entry - 1) * 100 - 0.2, 2)}
            if hi_ >= tgt:
                px = max(tgt, float(O.iloc[k][s])) if k > i + 1 else tgt
                return {"entry": round(entry, 2), "exit": round(px, 2), "exit_date": dates[k].date().isoformat(), "how": "target +5%", "ret_pct": round((px / entry - 1) * 100 - 0.2, 2)}
    k = i + 1 + t["hold"]
    if k >= len(dates):
        last = float(C.iloc[-1][s])
        return {"entry": round(entry, 2), "open": True, "last": round(last, 2), "ret_pct": round((last / entry - 1) * 100 - 0.2, 2), "days_held": len(dates) - (i + 1)}
    px = float(O.iloc[k][s])
    return {"entry": round(entry, 2), "exit": round(px, 2), "exit_date": dates[k].date().isoformat(), "how": "time exit", "ret_pct": round((px / entry - 1) * 100 - 0.2, 2)}


last = len(dates) - 1
today = picks_on(last)[:15]
history = []
for i in range(max(0, last - LOOKBACK_DAYS), last):
    ps = picks_on(i)[:15]
    if not ps:
        continue
    for p in ps:
        p["result"] = settle(p, i)
    history.append({"signal_date": dates[i].date().isoformat(), "buy_date": dates[i + 1].date().isoformat() if i + 1 < len(dates) else None, "picks": ps})
rec = {}
for tier in TIERS:
    closed = [p["result"]["ret_pct"] for h in history for p in h["picks"] if p["tier"] == tier and p.get("result") and not p["result"].get("open")]
    rec[tier] = {"n": len(closed), "win_pct": round(100 * float(np.mean([r > 0 for r in closed]))) if closed else None, "avg_ret_pct": round(float(np.mean(closed)), 2) if closed else None}
out = {"generated": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"), "data_to": dates[last].date().isoformat(), "buy_on": "next trading day's open",
       "tiers": {k: {"label": v["label"], "backtest_win_pct": v["win"], "exit": v["exit"]} for k, v in TIERS.items()},
       "rule": "Scanned every trading day after the close. Rule pick = the tested mean-reversion setup (Nifty 500, ≥ ₹1 cr/day): close above the 200-day average and down more than 10% over three closes; "
               "near-rule = the same with a 7-10% drop (weaker); breakout = new 52-week high on 1.5x volume above the 200-day average. Paper only.",
       "expected": "Strength is how closely the stock matches the tested setup (drop, trend margin, liquidity, RSI, volume), weighted by that setup's measured win rate - a match score, not a prediction. "
                   "Rule picks: 64% hit +5% in the 2021-26 backtest, +1.4% a trade after costs; plan on 8-12% a year on the capital used. Near-rule and breakout tiers are weaker and shown so there is always something to watch.",
       "today": today, "record_last_60_sessions": rec, "history": history}
json.dump(out, open(OUT, "w"), indent=1)
print(f"{dates[last].date()}: {len(today)} candidate(s) {[(p['sym'], p['tier'], p['strength']) for p in today[:8]]}; record {rec}")
