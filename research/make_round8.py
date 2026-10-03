"""Adds the AI setup (round 8) to fno/research_summary.json."""
import json
import numpy as np, pandas as pd
import lightgbm as lgb
S = json.load(open("../fno/research_summary.json"))
pol = json.load(open("ai_policies3_v1+_s12+_s13.json"))
one = json.load(open("ai_policies3_v1.json"))
two = json.load(open("ai_policies3__s12+_s13.json"))
meta = json.load(open("ai_meta.json"))
rules = {"net": 191900, "years": {"2024": 95934, "2025": 15670, "2026": 80332}, "last120": 56500, "pf": 1.32, "win": 30.6, "trades": 657}
imp = pd.Series(lgb.Booster(model_file="ai_reg.txt").feature_importance("gain"), index=meta["cols"]).sort_values(ascending=False)
imp = imp / imp.sum() * 100
LABEL = {"stop_k": "stop size asked about", "tod": "time of day", "cpr_w": "yesterday's CPR width", "vix": "India VIX level",
         "side": "direction asked about", "atr_pct": "ATR as % of price", "vix_chg_prev": "yesterday's VIX change", "ctx": "global cues + gap + VIX",
         "gap": "opening gap", "day_rng": "today's range so far", "vs_vwap": "price vs VWAP", "prev_day_body": "yesterday's candle body",
         "bars_lo": "bars since the day's low", "atr_rel": "ATR vs usual", "vix_day": "VIX change today", "bars_hi": "bars since the day's high",
         "vs_ph": "price vs yesterday's high", "prev_day_ret": "yesterday's return", "m15_e5_e20": "15-min EMA 5 vs 20", "dte": "days to expiry",
         "noise_pos": "position vs the noise band", "vs_open": "price vs today's open", "rsi": "RSI", "adx": "ADX"}
def pick(lst, name):
    return next(x for x in lst if x["name"].endswith(name))
S["ai"] = {
    "what": "Gradient-boosted trees (LightGBM, 3 seeds averaged, 64 inputs) score six candidate trades at every 5-minute close "
            "09:30-14:30 - buy CALL or PUT with a stop of 1.0 / 1.5 / 2.0 ATR and a 2R target, held to 15:15 - and recommend the best "
            "when its expected R clears a threshold. Inputs: price vs open / VWAP / EMAs, the noise band, Camarilla and yesterday's levels, "
            "RSI / ADX / MACD / Supertrend / Heikin-Ashi / Bollinger, VIX level and changes, gap and global cues, time of day, expiry, "
            "today's structure so far, and the two rule signals themselves.",
    "method": "Trained on 111,630 decision points (both indices, Jan 2023 - Oct 2026). Walk-forward: for every quarter from 2024-Q1 the model "
              "was trained only on the quarters before it and traded the quarter it had never seen; the 11 quarters are stitched together. "
              "Trades priced as the 1-ITM option with the real-price-corrected premiums, slippage and charges, ₹2 lakh, 1% risk, "
              "max 2 trades and 2 losers per index per day.",
    "threshold": 0.3, "train_until": meta["train_until"],
    "oos": {"ensemble": {k: {x: pick(pol, k)[x] for x in ("trades", "net", "win", "pf", "max_dd", "years", "last120")} for k in ("fixed 0.2", "fixed 0.3", "fixed 0.4")},
            "single_seed_0.3": {x: pick(one, "fixed 0.3")[x] for x in ("trades", "net", "win", "pf", "years", "last120")},
            "other_two_seeds_0.3": {x: pick(two, "fixed 0.3")[x] for x in ("trades", "net", "win", "pf", "years", "last120")}},
    "rules_same_period": rules,
    "versions": [
        {"name": "v1 - plain stop/target outcomes (shipped, as a 3-seed average)", "result": "the only version with any out-of-sample skill; a single seed looked good (+₹62k) but two other seeds of the same setup lost ₹21k - the average is about break-even"},
        {"name": "v2 - predict the move to the 15:15 close, heavier regularisation", "result": "no skill: its favourite inputs were weekday, VIX and CPR width, which it overfits; as a filter on the rules it only removed good trades"},
        {"name": "v3 - learn when to enter using the rules' own exits (2 ATR stop, 4R target, band trailing)", "result": "scores sat near zero out of sample; no usable edge as a strategy or as a filter"},
        {"name": "policies: validation-picked thresholds, top-x% of past scores, half-hour bars only, win-probability gates, smoothing", "result": "none stable across years"},
    ],
    "importance": [{"feature": LABEL.get(k, k), "pct": round(float(v), 1)} for k, v in imp.head(12).items()],
    "verdict": "On days it never saw, the AI did not beat the two rules: about break-even over 2024-26 with a losing 2025, versus the rules' "
               "+₹192k. It is shipped as an optional, off-by-default strategy so it can be watched paper-trading, and every number it shows "
               "in the app is from after its training cutoff. Use its probability as information, not as confirmation.",
}
json.dump(S, open("../fno/research_summary.json", "w"), default=str, indent=1)
print(json.dumps(S["ai"]["oos"]["ensemble"]["fixed 0.3"]), S["ai"]["importance"][:5])
