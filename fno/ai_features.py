"""Features of one 5-minute bar for the AI strategy. Shared by the research table builder
(research/ai_data.py) and the live app (fno/ai.py), so training and serving never drift apart.

bars: dict of numpy arrays for the session so far (completed bars): t (minutes), o, h, l, c, vwap, e5, e9, e20, e21,
      e50, s44, st, st15, atr, adx, rsi, vix, sig, macdh, bbu, bbl, bbm, bbw, bbw_min, ha_o, ha_c, dch, dcl, vsd
day:  dict of scalars: prev_close, prev_high, prev_low, cpr_w, gap, ctx, vix_chg, dte, sym_id, h3, h4, l3, l4,
      avg_range (14-day mean of daily high-low), prev2_close (close two days ago), prev_open (yesterday's open)
b15:  list of (i5, e5, e20, s44, rsi) for completed 15-minute bars, i5 = index of the 5-min bar that closes it
"""
from __future__ import annotations

import numpy as np

NOISE_MULT = 1.75
GROUPS = {
    "Price vs open, VWAP and EMAs": ["vs_open", "vs_pc", "vs_vwap", "vs_e9", "vs_e21", "vs_e50", "e9_e21", "e21_e50", "m15_vs_e5", "m15_e5_e20", "m15_vs_s44"],
    "Noise band": ["noise_pos", "noise_up", "noise_lo", "band_w", "above_band", "sig_noise", "is_check"],
    "Camarilla and yesterday's levels": ["vs_ph", "vs_pl", "vs_h4", "vs_l4", "vs_h3", "vs_l3", "cpr_w", "sig_cam"],
    "Momentum (RSI, ADX, MACD, Supertrend, Heikin-Ashi)": ["st", "st15", "adx", "rsi", "macdh", "ha", "m15_rsi", "r1", "r3", "r6", "r12"],
    "Volatility and VIX": ["vix", "vix_day", "vix_chg_prev", "atr_pct", "atr_rel", "rv12", "bb_pos", "bbw_rel", "vsd", "bar_rng", "bar_body"],
    "Time of day and expiry": ["tod", "dte"],
    "Today's structure so far": ["day_rng", "vs_dhi", "vs_dlo", "bars_hi", "bars_lo", "body_so_far", "dc_pos"],
    "Gap, global cues, yesterday": ["gap", "ctx", "prev_day_ret", "prev_day_body"],
}


def bands(o0, prev_close, sig):
    sig = np.asarray(sig, dtype=float)
    return max(o0, prev_close) * (1 + NOISE_MULT * sig), min(o0, prev_close) * (1 - NOISE_MULT * sig)


def bar_features(bars: dict, i: int, day: dict, b15: list) -> dict:
    o, h, l, c, t = bars["o"], bars["h"], bars["l"], bars["c"], bars["t"]
    a = max(float(bars["atr"][i]), 1e-6)
    o0, pc = float(o[0]), day["prev_close"]
    up_band, lo_band = bands(o0, pc, bars["sig"])
    hi_so_far, lo_so_far = float(np.max(h[: i + 1])), float(np.min(l[: i + 1]))
    idx_hi, idx_lo = int(np.argmax(h[: i + 1])), int(np.argmin(l[: i + 1]))
    last15 = None
    for row in b15:
        if row[0] <= i:
            last15 = row
    vwap, e9, e21, e50 = bars["vwap"], bars["e9"], bars["e21"], bars["e50"]
    ci = float(c[i])
    is_check = (t[i] + 5) % 30 == 15 and t[i] + 5 >= 585
    noise_l = int(is_check and ci > up_band[i] and ci > vwap[i])
    noise_s = int(is_check and ci < lo_band[i] and ci < vwap[i])
    h4, l4 = day["h4"], day["l4"]
    cam_up = int(i >= 1 and c[i - 1] <= h4 < ci) or (int(i >= 2 and c[i - 2] <= h4 < c[i - 1] and ci > h4) * 2)
    cam_dn = int(i >= 1 and c[i - 1] >= l4 > ci) or (int(i >= 2 and c[i - 2] >= l4 > c[i - 1] and ci < l4) * 2)
    r1 = (ci - c[i - 1]) / a if i >= 1 else 0.0
    r3 = (ci - c[i - 3]) / a if i >= 3 else 0.0
    r6 = (ci - c[i - 6]) / a if i >= 6 else 0.0
    r12 = (ci - c[i - 12]) / a if i >= 12 else 0.0
    rv = float(np.std(np.diff(c[max(0, i - 12): i + 1]))) / a if i >= 3 else 0.0
    bw = bars["bbu"][i] - bars["bbl"][i]
    avg_range = day.get("avg_range") or 0.0
    dch, dcl = bars["dch"][i], bars["dcl"][i]
    bbw_min = bars["bbw_min"][i]
    return {
        "sym": day["sym_id"], "tod": int(t[i]), "dte": day["dte"],
        "vs_open": (ci - o0) / a, "vs_pc": (ci - pc) / a, "vs_vwap": (ci - vwap[i]) / a,
        "vs_e9": (ci - e9[i]) / a, "vs_e21": (ci - e21[i]) / a, "vs_e50": (ci - e50[i]) / a,
        "e9_e21": (e9[i] - e21[i]) / a, "e21_e50": (e21[i] - e50[i]) / a,
        "noise_pos": (ci - up_band[i]) / a if ci >= o0 else (ci - lo_band[i]) / a,
        "noise_up": (ci - up_band[i]) / a, "noise_lo": (ci - lo_band[i]) / a,
        "band_w": (up_band[i] - lo_band[i]) / a,
        "vs_ph": (ci - day["prev_high"]) / a, "vs_pl": (ci - day["prev_low"]) / a,
        "vs_h4": (ci - h4) / a, "vs_l4": (ci - l4) / a, "vs_h3": (ci - day["h3"]) / a, "vs_l3": (ci - day["l3"]) / a,
        "cpr_w": day["cpr_w"], "gap": day["gap"], "ctx": day["ctx"], "vix": float(bars["vix"][i]),
        "vix_day": float(bars["vix"][i] / bars["vix"][0] - 1), "vix_chg_prev": day["vix_chg"],
        "st": float(bars["st"][i]), "st15": float(bars["st15"][i]), "adx": float(bars["adx"][i]), "rsi": float(bars["rsi"][i]),
        "macdh": bars["macdh"][i] / a, "bb_pos": (ci - bars["bbm"][i]) / bw if bw > 0 else 0.0,
        "bbw_rel": bars["bbw"][i] / bbw_min if bbw_min > 0 else 1.0,
        "ha": 1 if bars["ha_c"][i] > bars["ha_o"][i] else -1,
        "dc_pos": (ci - dcl) / (dch - dcl) if dch > dcl else 0.5,
        "vsd": bars["vsd"][i] / a,
        "r1": r1, "r3": r3, "r6": r6, "r12": r12, "rv12": rv,
        "atr_pct": a / ci * 100, "atr_rel": a / (avg_range / 75) if avg_range else 1.0,
        "day_rng": (hi_so_far - lo_so_far) / avg_range if avg_range else 0.0,
        "vs_dhi": (ci - hi_so_far) / a, "vs_dlo": (ci - lo_so_far) / a,
        "bars_hi": i - idx_hi, "bars_lo": i - idx_lo,
        "body_so_far": (ci - o0) / (hi_so_far - lo_so_far) if hi_so_far > lo_so_far else 0.0,
        "bar_body": (ci - o[i]) / a, "bar_rng": (h[i] - l[i]) / a,
        "m15_vs_e5": (ci - last15[1]) / a if last15 else 0.0, "m15_e5_e20": (last15[1] - last15[2]) / a if last15 else 0.0,
        "m15_rsi": last15[4] if last15 else 50.0,
        "m15_vs_s44": (ci - last15[3]) / a if last15 and not np.isnan(last15[3]) else 0.0,
        "prev_day_ret": (pc - day["prev2_close"]) / a if day.get("prev2_close") else 0.0,
        "prev_day_body": (pc - day["prev_open"]) / a if day.get("prev_open") else 0.0,
        "sig_noise": noise_l - noise_s, "sig_cam": (1 if cam_up else 0) - (1 if cam_dn else 0), "is_check": int(is_check),
        "above_band": int(ci > up_band[i]) - int(ci < lo_band[i]),
    }
