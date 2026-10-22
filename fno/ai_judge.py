"""The AI judge (research round 39): a small gradient-boosted model that scores the RULES' own signals instead of finding its
own trades. Given the 64 market features of the signal's 5-minute bar (the AI strategy's features, turned to the trade's
side) plus which slot / rule fired, it predicts how good the trade is. Autopilot then risks 1.33x on the better half of
signals and 0.67x on the weaker half, on top of conviction sizing (capped 0.5x-1.6x), on Nifty only.

Walk-forward 2024 - Oct 2026 on Nifty (quarterly retrain, never seeing the quarter it judged): flat +Rs 2.09L, conviction
+Rs 2.33L, AI judge + conviction +Rs 3.05L with the same deepest fall as conviction (-Rs 34.8k vs -34.2k), better than
conviction in every year; 20 label-shuffled runs never matched it. On Bank Nifty it judged backwards, so it is not used there.

fno/ai_judge.npz: trees exported by research/round39_export.py (same layout as ai_model.npz) + meta (columns, cut, dates).
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
_M: dict = {}


def load() -> dict:
    if not _M:
        z = np.load(HERE / "ai_judge.npz")
        _M.update({k: z[k] for k in z.files})
        _M["meta"] = json.loads(str(z["meta"]))
    return _M


def available() -> bool:
    try:
        load()
        return True
    except Exception:
        return False


def meta() -> dict:
    return load()["meta"]


def predict(x: np.ndarray) -> np.ndarray:
    m = load()
    feat, thr, left, right, dleft, value = m["feat"], m["thr"], m["left"], m["right"], m["dleft"], m["value"]
    x = np.atleast_2d(x); out = np.zeros(len(x)); rows = np.arange(len(x))
    for r in m["roots"]:
        node = np.full(len(x), int(r))
        while True:
            f = feat[node]; active = f >= 0
            if not active.any():
                break
            xv = x[rows[active], f[active]]
            go_left = np.where(np.isnan(xv), dleft[node[active]] == 1, xv <= thr[node[active]])
            node[active] = np.where(go_left, left[node[active]], right[node[active]])
        out += value[node]
    return out


def row(features: dict, side: int, slot: str, strategy: str) -> np.ndarray:
    """The model's input row from the AI strategy's bar features, turned to the trade's side (+1 call / -1 put)."""
    mt = meta(); f = dict(features)
    for c in mt["dir_cols"]:
        if c in f and f[c] is not None:
            f[c] = float(f[c]) * side
    for c in mt["centered_cols"]:
        if c in f and f[c] is not None:
            f[c] = (float(f[c]) - 50) * side
    f["slot_id"] = mt["slot_id"].get(slot, 0)
    f["strat_id"] = mt["strat_id"].get(strategy, 3)
    return np.array([float(f.get(c, np.nan)) if f.get(c) is not None else np.nan for c in mt["cols"]])


def bar_features(symbol, candles, vix: dict, now, expiry, signal_bar: str | None, gcues_avg=None, vix_chg_prev=0.0,
                 vix_series=None) -> dict | None:
    """The AI strategy's features of the signal's bar (the bar ending at signal_bar 'HH:MM', else the last completed one).
    vix_series: today's 5-minute India VIX closes, so the per-bar VIX and 'VIX vs today's first bar' match the training
    table (research used intraday VIX); without it today's bars get the current level and the first bar yesterday's close."""
    from . import ai as AI, ai_features as F
    from . import config as C
    lvl = float((vix or {}).get("level") or 14.0); chg = (vix or {}).get("chg_pct")
    prev_lvl = lvl / (1 + chg / 100) if chg not in (None, 0) else lvl
    df, d15 = AI.enrich(candles, lvl)
    if now.tzinfo is not None:
        df = df[df.index + timedelta(minutes=C.CANDLE_MIN) <= now]
        d15 = d15[d15["bar_end"] <= now]
    today = now.date()
    if df.empty or df.index[-1].date() != today:
        return None
    tmask = df.index.date == today
    vs = None
    if vix_series is not None and len(vix_series):
        try:
            vs = vix_series.copy()
            if getattr(vs.index, "tz", None) is None and getattr(df.index, "tz", None) is not None:
                vs.index = vs.index.tz_localize(df.index.tz)
            elif getattr(vs.index, "tz", None) is not None and getattr(df.index, "tz", None) is None:
                vs.index = vs.index.tz_localize(None)
            vs = vs.sort_index().reindex(df.index[tmask], method="ffill")
            if vs.isna().all():
                vs = None
        except Exception:
            vs = None
    if vs is not None:
        df.loc[tmask, "vix"] = vs.fillna(lvl).values
    else:
        df.loc[tmask, "vix"] = lvl
        df.loc[df[tmask].index[0], "vix"] = prev_lvl
    s = AI.session(df, d15, today, symbol, expiry, gcues_avg, vix_chg_prev)
    if s is None:
        return None
    bars, scal, b15 = s
    i = len(bars["c"]) - 1
    if signal_bar:
        hh, mm = int(signal_bar[:2]), int(signal_bar[3:5])
        hit = np.where(bars["t"] + C.CANDLE_MIN == hh * 60 + mm)[0]
        if len(hit):
            i = int(hit[0])
    if np.isnan(bars["sig"][i]) or bars["t"][i] < AI.FIRST_BAR or bars["t"][i] > AI.LAST_BAR:
        return None
    return F.bar_features(bars, i, scal, b15)


def judge(features: dict, side: int, slot: str, strategy: str) -> dict:
    mt = meta(); p = float(predict(row(features, side, slot, strategy))[0])
    good = p >= mt["cut"]
    return {"pred": round(p, 3), "cut": round(mt["cut"], 3), "good": bool(good), "mult": mt["high"] if good else mt["low"]}
