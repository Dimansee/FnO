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
# Lower-circuit signature: the session closed at its low and down exactly one price-band step (5/10/20%). Such a stock has sellers and
# no buyers - a buy next day may be impossible to sell (35 of 161 such rule signals were locked again from the open the next day).
R1 = C / C.shift(1) - 1
# NSE's current price bands (sec_list.csv, saved nightly by fetch_stocks.py): exact circuit width per stock; "No Band" = F&O stock,
# no circuit at all. Missing -> the band-step heuristic (2/5/10/20%).
BANDS, SERIES = {}, {}
_bp = os.path.join(HERE, "data", "price_bands.csv")
if os.path.exists(_bp):
    _bd = pd.read_csv(_bp); _bd.columns = [c.strip() for c in _bd.columns]
    for _s, _se, _b in zip(_bd["Symbol"].astype(str).str.strip(), _bd["Series"].astype(str).str.strip(), _bd["Band"].astype(str).str.strip()):
        SERIES[_s] = _se
        BANDS[_s] = None if _b.lower().startswith("no") else (float(_b) / 100 if _b.replace(".", "").isdigit() else BANDS.get(_s))


def band_hit(r1):
    """True where the day's fall reached the stock's lower price band (real band if known, else a 2/5/10/20% step)."""
    out = pd.DataFrame(False, index=r1.index, columns=r1.columns)
    for b in (0.02, 0.05, 0.10, 0.20):
        out |= (r1 <= -(b - 0.0015)) & (r1 >= -(b + 0.0015))
    for s_ in r1.columns:
        if s_ in BANDS:
            out[s_] = False if BANDS[s_] is None else (r1[s_] <= -(BANDS[s_] - 0.0015))
    return out


def t2t_note(s_):
    se = SERIES.get(s_)
    return f"; trade-to-trade ({se} series): delivery only, no intraday, usually under exchange surveillance" if se and se not in ("EQ", "") else ""


_band = band_hit(R1)
LOCKED = (C <= L * 1.0005) & _band
# Frozen all session at the lower limit (high = low at a band-step fall): nothing could be bought or sold that day.
FROZEN = ((H - L) <= 0.0005 * C) & _band
# 17 Oct 2026 retest (fno/stock_circuit_universe_2026-10.md): rule signals with a lower circuit in the 3-day drop bounced BETTER
# (train 84% / +3.2% a trade, unseen 72% / +2.2%) than the rest (74% / +1.9%, 59% / +0.5%), as long as the stock was not frozen
# at the next open. Skipping them cut the realistic backtest from 20.9% to 17.0% a year. So they stay in the list, flagged with
# the circuit dates, and are not bought if they are frozen at the open (the 09:30 live check, and settle() below).
SKIPPED = []                                                          # kept for the app; no longer filled (see above)
dates = C.index
TIERS = {
    "rule": {"label": "Rule pick", "win": 64, "target_pct": 5.0, "hold": 5, "risk_pct": 8.0, "exit": "+5% limit sell at once; sell at the open after the 5th close if not filled. Safety line −8% (not part of the tested rule)."},
    "near": {"label": "Near-rule (weaker)", "win": 58, "target_pct": 5.0, "hold": 5, "risk_pct": 8.0, "exit": "Same as the rule pick: +5% target, out after 5 closes. Weaker setup - smaller size."},
    "breakout": {"label": "Breakout watch", "win": 44, "target_pct": 15.0, "hold": 60, "risk_pct": 10.0, "exit": "Trail a 15% stop from the highest close; out after 60 sessions. Safety line −10%."},
    "down": {"label": "Downside watch", "win": 42, "target_pct": -5.0, "hold": 5, "risk_pct": -8.0, "exit": "Expect a −5% pullback within 5 sessions; wrong if it rises 8% instead. Cash: do not buy / book profit if holding. F&O names: a put or short future is the only way to act on it. Weak evidence."},
}
FO = set(pd.read_csv(os.path.join(HERE, "data", "fo_list.csv"))["Symbol"].astype(str)) if os.path.exists(os.path.join(HERE, "data", "fo_list.csv")) else set()


def row(s, i, tier, strength, basis):
    c = float(C.iloc[i][s]); t = TIERS[tier]
    return {"sym": s, "name": str(names["Company Name"].get(s, s)), "industry": str(names["Industry"].get(s, "")), "tier": tier, "setup": t["label"], "strength": int(round(strength)),
            "direction": "down" if tier == "down" else "up", "fo": s in FO, "price_date": dates[i].date().isoformat(),
            "current": round(float(C.iloc[-1][s]), 2) if not pd.isna(C.iloc[-1][s]) else None, "current_date": dates[-1].date().isoformat(),
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
        drop = r3[s]
        if above <= 0:
            if not pd.isna(drop) and drop >= 0.10:
                st = 25 + min(10, (drop - 0.10) * 100) + 5 * min(1.0, np.log10(max(tu[s], 1e7) / 1e7) / 2) + (5 if (rs[s] or 50) >= 70 else 0)
                out.append(row(s, i, "down", min(40, st), f"3-day rise {drop*100:+.1f}% while {above*100:.1f}% below the 200-day average (bounce in a downtrend); RSI {rs[s]:.0f}"))
            continue
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
    for r in out:
        if SERIES.get(r["sym"]) not in (None, "", "EQ"):
            r["t2t"] = SERIES[r["sym"]]; r["basis"] += t2t_note(r["sym"])
        cd = [dates[k].date().isoformat() for k in range(max(0, i - 2), i + 1) if LOCKED.iloc[k][r["sym"]]]
        if cd and r["direction"] == "up":
            r["circuit_days"] = cd
            r["basis"] += f"; hit the lower circuit on {', '.join(cd)} - buy only if it trades freely at the open, skip if it is stuck at the limit"
    out.sort(key=lambda r: -r["strength"])
    return out


def settle(p, i):
    s, t = p["sym"], TIERS[p["tier"]]
    if i + 1 >= len(dates):
        return None
    entry = float(O.iloc[i + 1][s])
    if pd.isna(entry):
        return None
    if p["direction"] == "up" and bool(FROZEN.iloc[i + 1][s]) and float(C.iloc[i + 1][s]) < float(C.iloc[i][s]):
        return {"skipped": True, "how": "frozen at the lower circuit at the open - not bought", "exit_date": dates[i + 1].date().isoformat()}
    tgt, risk = entry * (1 + t["target_pct"] / 100), entry * (1 - t["risk_pct"] / 100)
    peak = entry
    for k in range(i + 1, min(i + 1 + t["hold"], len(dates))):
        hi_, lo_, cl_ = H.iloc[k][s], L.iloc[k][s], C.iloc[k][s]
        if pd.isna(cl_):
            continue
        if p["tier"] == "down":
            tgt_d, risk_u = entry * (1 + t["target_pct"] / 100), entry * (1 - t["risk_pct"] / 100)
            if hi_ >= risk_u and k > i + 1:
                return {"entry": round(entry, 2), "exit": round(risk_u, 2), "exit_date": dates[k].date().isoformat(), "how": "rose instead (risk line)", "ret_pct": round((entry / risk_u - 1) * 100 - 0.2, 2)}
            if lo_ <= tgt_d:
                return {"entry": round(entry, 2), "exit": round(tgt_d, 2), "exit_date": dates[k].date().isoformat(), "how": "fell to target −5%", "ret_pct": round((entry / tgt_d - 1) * 100 - 0.2, 2)}
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
    sign = -1 if p["tier"] == "down" else 1
    if k >= len(dates):
        last = float(C[s].dropna().iloc[-1])
        return {"entry": round(entry, 2), "open": True, "last": round(last, 2), "ret_pct": round(sign * (last / entry - 1) * 100 - 0.2, 2), "days_held": len(dates) - (i + 1)}
    px = float(O.iloc[k][s])
    if pd.isna(px):
        px = float(C.iloc[:k + 1][s].dropna().iloc[-1])
    return {"entry": round(entry, 2), "exit": round(px, 2), "exit_date": dates[k].date().isoformat(), "how": "time exit", "ret_pct": round(sign * (px / entry - 1) * 100 - 0.2, 2)}


last = len(dates) - 1
today = picks_on(last)[:15]
broad = []
try:
    import glob
    wp = os.path.join(HERE, "work", "all_1y.csv.gz")
    if os.path.exists(wp):
        bd = pd.read_csv(wp, parse_dates=["date"]).sort_values(["sym", "date"]); bl = pd.read_csv(os.path.join(HERE, "work", "all_list.csv")).set_index("Symbol")
        bd = bd[~bd["sym"].isin(set(C.columns))]
        bf = (bd["adj close"] / bd["close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
        for c in ("open", "high", "low", "close"):
            bd[c] = bd[c] * bf
        BC = bd.pivot(index="date", columns="sym", values="close"); BH = bd.pivot(index="date", columns="sym", values="high"); BV = bd.pivot(index="date", columns="sym", values="volume"); BLo = bd.pivot(index="date", columns="sym", values="low")
        bcov = BC.notna().mean(axis=1); bfr = ((BC != BC.shift(1)) | (BV != BV.shift(1))).where(BC.notna()).mean(axis=1); bok = (bcov >= 0.5) & (bfr >= 0.2)
        while len(BC) > 1 and not bok.iloc[len(BC) - 1]:
            BC, BH, BV, BLo = BC.iloc[:-1], BH.iloc[:-1], BV.iloc[:-1], BLo.iloc[:-1]
        BLOCK = (BC <= BLo * 1.0005) & band_hit(BC / BC.shift(1) - 1)
        bs200 = BC.rolling(200, min_periods=200).mean().iloc[-1]; br3 = (BC / BC.shift(3) - 1).iloc[-1]; btu = (BC * BV).rolling(50, min_periods=20).median().iloc[-1]
        brs = BC.apply(rsi).iloc[-1]; bhi = BH.rolling(252, min_periods=200).max().shift(1).iloc[-1]; bv = BV.iloc[-1]; bv50 = BV.rolling(50, min_periods=50).mean().iloc[-1]
        bc = BC.iloc[-1]
        for s_ in BC.columns:
            if pd.isna(bc[s_]) or pd.isna(bs200[s_]) or not (btu.get(s_, 0) >= MIN_TURN):
                continue
            above = bc[s_] / bs200[s_] - 1; drop = br3[s_]
            if above <= 0 or pd.isna(drop):
                continue
            tier = "rule" if drop <= -0.10 else "near" if drop <= -0.07 else ("breakout" if (not pd.isna(bhi[s_]) and bc[s_] > bhi[s_] and bv50[s_] and bv[s_] >= 1.5 * bv50[s_]) else None)
            if not tier:
                continue
            t = TIERS[tier]; c_ = float(bc[s_]); liqscore = min(1.0, np.log10(max(btu[s_], 1e7) / 1e7) / 2)
            st = (55 + min(20, (-drop - 0.10) * 200) + (10 if 20 <= (brs[s_] or 50) <= 40 else 0) + min(5, above * 50) if tier == "rule" else 35 + (-drop - 0.07) * 300 if tier == "near" else 30 + min(15, (bv[s_] / bv50[s_] - 1.5) * 10)) + 10 * liqscore
            broad.append({"sym": s_, "name": str(bl["Company Name"].get(s_, s_)) if s_ in bl.index else s_, "industry": str(bl["Industry"].get(s_, "") or "") if s_ in bl.index else "", "tier": tier, "setup": t["label"] + " · outside Nifty 500",
                          "strength": int(min(100, st)), "direction": "up", "fo": s_ in FO, "price_date": BC.index[-1].date().isoformat(), "current": round(c_, 2), "current_date": BC.index[-1].date().isoformat(),
                          "close": round(c_, 2), "entry_ref": round(c_, 2), "target": round(c_ * (1 + t["target_pct"] / 100), 2), "target_pct": t["target_pct"], "risk_line": round(c_ * (1 - t["risk_pct"] / 100), 2),
                          "risk_pct": t["risk_pct"], "exit_rule": t["exit"], "hold_days": t["hold"], "basis": f"3-day move {drop*100:+.1f}%, {above*100:.1f}% above the 200-day average; turnover ₹{btu[s_]/1e7:.0f} cr/day. Outside the tested Nifty 500 universe.",
                          "drop_3d_pct": round(float(drop * 100), 1), "above_200dma_pct": round(float(above * 100), 1), "rsi14": round(float(brs[s_])) if not pd.isna(brs[s_]) else None,
                          "turnover_cr": round(float(btu[s_] / 1e7), 1), "backtest_win_pct": None, "broad": True})
            r_ = broad[-1]
            if SERIES.get(s_) not in (None, "", "EQ"):
                r_["t2t"] = SERIES[s_]; r_["basis"] += t2t_note(s_)
            cd = [BC.index[k].date().isoformat() for k in range(max(0, len(BC) - 3), len(BC)) if BLOCK.iloc[k][s_]]
            if cd:
                r_["circuit_days"] = cd; r_["basis"] += f"; hit the lower circuit on {', '.join(cd)} - buy only if it trades freely at the open"
        broad.sort(key=lambda r: -r["strength"]); broad = broad[:20]
        # 17 Oct 2026 all-NSE test (2,344 stocks, 6 years): Nifty 500 picks first plus at most 2 of 10 slots from outside the Nifty 500
        # gave 26.2%/yr vs 20.1% Nifty-500-only (worst fall -17.5% vs -12.4%, unseen 11.2% vs 4.6%); letting outside stocks take any
        # slot gave a -39% fall. So the 2 strongest outside rule picks join today's list, marked; the rest stay on the watch list.
        for r_ in [r for r in broad if r["tier"] == "rule"][:2]:
            today.append(dict(r_, setup="Rule pick · outside Nifty 500", backtest_win_pct=TIERS["rule"]["win"], outside_slot=True,
                              basis=r_["basis"].replace("Outside the tested Nifty 500 universe.", "Outside the Nifty 500: hold at most 2 such stocks at a time.")))
except Exception as e:
    print("broad scan skipped:", e)
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
    closed = [p["result"]["ret_pct"] for h in history for p in h["picks"] if p["tier"] == tier and p.get("result") and "ret_pct" in p["result"] and not p["result"].get("open") and np.isfinite(p["result"]["ret_pct"])]
    rec[tier] = {"n": len(closed), "win_pct": round(100 * float(np.mean([r > 0 for r in closed]))) if closed else None, "avg_ret_pct": round(float(np.mean(closed)), 2) if closed else None}
out = {"generated": datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"), "data_to": dates[last].date().isoformat(), "buy_on": "next trading day's open",
       "tiers": {k: {"label": v["label"], "backtest_win_pct": v["win"], "exit": v["exit"]} for k, v in TIERS.items()},
       "rule": "Scanned every trading day after the close. Rule pick = the tested mean-reversion setup (Nifty 500, ≥ ₹1 cr/day): close above the 200-day average and down more than 10% over three closes; "
               "near-rule = the same with a 7-10% drop (weaker); breakout = new 52-week high on 1.5x volume above the 200-day average; downside watch = a 10%+ three-day bounce in a stock below its 200-day average, expected to give back 5% (weak evidence: 42% hit in training vs 26% for random days, and it faded on the unseen period). Paper only.",
       "expected": "Strength is how closely the stock matches the tested setup (drop, trend margin, liquidity, RSI, volume), weighted by that setup's measured win rate - a match score, not a prediction. "
                   "Rule picks: 64% hit +5% in the 2021-26 backtest, +1.4% a trade after costs; plan on 8-12% a year on the capital used. Near-rule and breakout tiers are weaker and shown so there is always something to watch.",
       "today": today, "skipped_circuit": SKIPPED, "today_broad": broad, "record_last_60_sessions": rec, "history": history}
json.dump(out, open(OUT, "w"), indent=1)
print(f"{dates[last].date()}: {len(today)} candidate(s) {[(p['sym'], p['tier'], p['strength']) for p in today[:8]]}; record {rec}")
