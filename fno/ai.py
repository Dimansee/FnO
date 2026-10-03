"""The AI strategy (research round 8): gradient-boosted trees that score, at every 5-minute close
between 09:30 and 14:30, six candidate trades (buy CALL / buy PUT with a stop of 1.0 / 1.5 / 2.0 ATR,
target 2x the stop, held to 15:15) and recommend the best one when its expected R clears the threshold.

Everything here mirrors research/engine.py + research/ai_data.py so the live features are the ones
the model was trained on. The model itself is fno/ai_model.npz (numpy inference, see ai_model.py).
Honest note: the model's out-of-sample record is shown in the app next to the rules'; it is a
probability machine, not a crystal ball.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from . import ai_features as F
from . import ai_model as AM
from . import config as C
from . import indicators as I

STOPS = (1.0, 1.5, 2.0)
RR = 2.0
FIRST_BAR, LAST_BAR = 570, 870      # bar-start minutes of the decision window (09:30 .. 14:30)
MAX_TRADES_PER_DAY = 2
_SYM_ID = {"NIFTY": 0, "BANKNIFTY": 1}


def available() -> bool:
    try:
        AM.load()
        return True
    except Exception:
        return False


def threshold() -> float:
    return float(AM.meta().get("threshold", 0.3))


def train_until() -> date | None:
    v = AM.meta().get("train_until")
    return date.fromisoformat(v) if v else None


# ---------------------------------------------------------------- indicators (as research/engine.load_days)
def _rsi(c: pd.Series, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _adx(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    up, dn = h.diff(), -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(pdm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    ndi = 100 * pd.Series(ndm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def enrich(candles: pd.DataFrame, vix: pd.Series | float | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """All indicator columns on a continuous 5-minute series (tz-aware IST index). Returns (df, d15)."""
    df = candles.copy()
    tt = df.index.hour * 60 + df.index.minute
    df = df[(tt >= 555) & (tt <= 925)]
    df["e9"], df["e21"] = I.ema(df["close"], 9), I.ema(df["close"], 21)
    df["atr"] = I.atr(df)
    df["st"] = I.supertrend(df)
    df["adx"], df["rsi"] = _adx(df), _rsi(df["close"])
    df["vwap"] = I.vwap(df)
    d15 = df[["open", "high", "low", "close"]].resample("15min", origin="start_day", offset="15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    st15 = I.supertrend(d15)
    st15.index = st15.index + pd.Timedelta(minutes=10)
    df["st15"] = st15.reindex(df.index).ffill().fillna(1)
    d15["e5"], d15["e20"], d15["s44"], d15["rsi"] = I.ema(d15["close"], 5), I.ema(d15["close"], 20), \
        d15["close"].rolling(44).mean(), _rsi(d15["close"])
    d15["bar_end"] = d15.index + pd.Timedelta(minutes=15)
    df["e5"], df["s44"], df["e20"] = I.ema(df["close"], 5), df["close"].rolling(44).mean(), I.ema(df["close"], 20)
    df["e50"] = I.ema(df["close"], 50)
    m12, m26 = I.ema(df["close"], 12), I.ema(df["close"], 26)
    df["macd"] = m12 - m26
    df["macdh"] = df["macd"] - I.ema(df["macd"], 9)
    mid, sd = df["close"].rolling(20).mean(), df["close"].rolling(20).std()
    df["bbu"], df["bbl"], df["bbm"] = mid + 2 * sd, mid - 2 * sd, mid
    df["bbw"] = (4 * sd / mid).fillna(0)
    df["bbw_min"] = df["bbw"].rolling(60).min()
    ha_c = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    vo, vc = df["open"].values, ha_c.values
    hv = np.empty(len(df))
    if len(hv):
        hv[0] = (vo[0] + vc[0]) / 2
        for q in range(1, len(hv)):
            hv[q] = (hv[q - 1] + vc[q - 1]) / 2
    df["ha_o"], df["ha_c"] = hv, ha_c
    df["dch"], df["dcl"] = df["high"].rolling(20).max().shift(1), df["low"].rolling(20).min().shift(1)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    g = tp.groupby(df.index.date)
    cm = g.transform(lambda z: z.expanding().mean())
    df["vsd"] = ((tp - cm) ** 2).groupby(df.index.date).transform(lambda z: z.expanding().mean()) ** 0.5
    if isinstance(vix, pd.Series) and len(vix):
        v = vix.copy()
        v.index = v.index.tz_convert(df.index.tz) if v.index.tz is not None else v.index.tz_localize(df.index.tz)
        df["vix"] = v.reindex(df.index, method="ffill").ffill().fillna(14.0)
    else:
        df["vix"] = float(vix) if vix else 14.0
    day = df.index.date
    df["dopen"] = df.groupby(day)["open"].transform("first")
    df["mv"] = (df["close"] / df["dopen"] - 1).abs()
    df["tod"] = df.index.hour * 60 + df.index.minute
    piv = df.pivot_table(index=pd.Index(day, name="d"), columns="tod", values="mv")
    sig = piv.rolling(14, min_periods=10).mean().shift(1)
    df["sig"] = [float(sig.loc[d, t]) if (d in sig.index and t in sig.columns) else np.nan for d, t in zip(day, df["tod"])]
    return df, d15


def _days(df: pd.DataFrame) -> dict:
    """date -> positional slice of that session (cached on the frame; sessions are contiguous)."""
    key = ("_days", len(df))
    if df.attrs.get("_days_key") != key:
        arr = np.array(df.index.date)
        out, start = {}, 0
        for i in range(1, len(arr) + 1):
            if i == len(arr) or arr[i] != arr[start]:
                out[arr[start]] = slice(start, i)
                start = i
        df.attrs["_days_key"], df.attrs["_days"] = key, out
    return df.attrs["_days"]


def session(df: pd.DataFrame, d15: pd.DataFrame, day: date, symbol: str, expiry: date, gcues_avg=None,
            vix_chg_prev=0.0) -> tuple[dict, dict, list] | None:
    """bars / day scalars / 15-min context for one session (completed bars only must already be in df)."""
    days = _days(df)
    if day not in days:
        return None
    x = df.iloc[days[day]]
    prev_dates = [dd for dd in days if dd < day]
    if not prev_dates:
        return None
    prev = df.iloc[days[prev_dates[-1]]]
    pc, ph, pl = float(prev["close"].iloc[-1]), float(prev["high"].max()), float(prev["low"].min())
    pv = (ph + pl + pc) / 3
    bc = (ph + pl) / 2
    tc = 2 * pv - bc
    gap = (float(x["open"].iloc[0]) / pc - 1) * 100
    gavg = gcues_avg if gcues_avg is not None else 0.0
    ctx = (1 if gavg > 0.4 else -1 if gavg < -0.4 else 0) + (1 if gap > 0.25 else -1 if gap < -0.25 else 0) \
        + (-1 if vix_chg_prev > 5 else 1 if vix_chg_prev < -5 else 0)
    full = [dd for dd in prev_dates if days[dd].stop - days[dd].start >= 70]
    ranges = [float(df.iloc[days[dd]]["high"].max() - df.iloc[days[dd]]["low"].min()) for dd in full[-14:]]
    avg_range = float(np.mean(ranges)) if len(ranges) >= 5 else float(x["high"].max() - x["low"].min())
    rngp = ph - pl
    scal = {"prev_close": pc, "prev_high": ph, "prev_low": pl, "cpr_w": abs(tc - bc) / pc * 100, "gap": gap, "ctx": ctx,
            "vix_chg": vix_chg_prev, "dte": (expiry - day).days, "sym_id": _SYM_ID.get(symbol, 0), "avg_range": avg_range,
            "h3": pc + rngp * 1.1 / 4, "l3": pc - rngp * 1.1 / 4, "h4": pc + rngp * 1.1 / 2, "l4": pc - rngp * 1.1 / 2,
            "prev2_close": float(df.iloc[days[full[-2]]]["close"].iloc[-1]) if len(full) >= 2 else None,
            "prev_open": float(df.iloc[days[full[-1]]]["open"].iloc[0]) if full else None}
    cols = ("open", "high", "low", "close", "vwap", "e5", "e9", "e20", "e21", "e50", "s44", "st", "st15", "atr", "adx", "rsi",
            "vix", "sig", "macdh", "bbu", "bbl", "bbm", "bbw", "bbw_min", "ha_o", "ha_c", "dch", "dcl", "vsd")
    bars = {("o" if c == "open" else "h" if c == "high" else "l" if c == "low" else "c" if c == "close" else c): x[c].values.astype(float)
            for c in cols}
    bars["t"] = (x.index.hour * 60 + x.index.minute).values
    idx = {int(t): i for i, t in enumerate(bars["t"])}
    b15 = []
    for ts, r in d15.iloc[_days(d15).get(day, slice(0, 0))].iterrows():
        end = r["bar_end"]
        i5 = idx.get(end.hour * 60 + end.minute - 5)
        if i5 is not None:
            b15.append((i5, float(r["e5"]), float(r["e20"]), float(r["s44"]), float(r["rsi"])))
    return bars, scal, b15


# ---------------------------------------------------------------- scoring
def _matrix(feature_rows: list[dict]) -> np.ndarray:
    """(6 * n, cols) matrix: for every feature row the 6 candidate trades, in candidate order."""
    cols = AM.features_order()
    si, ki = cols.index("side"), cols.index("stop_k")
    base = np.array([[float(f.get(c, np.nan)) if c not in ("side", "stop_k") else 0.0 for c in cols] for f in feature_rows])
    X = np.repeat(base, 6, axis=0)
    j = np.tile(np.arange(6), len(feature_rows))
    X[:, si] = np.where(j < 3, 1, -1)
    X[:, ki] = np.array(STOPS)[j % 3]
    return X


def score_many(feature_rows: list[dict]) -> np.ndarray:
    """Expected R for every row's 6 candidates: shape (n, 6)."""
    if not feature_rows:
        return np.zeros((0, 6))
    return AM.expected_r(_matrix(feature_rows)).reshape(len(feature_rows), 6)


def score_bar(bars, scal, b15, i) -> dict | None:
    """Scores the 6 candidate trades at bar i. Returns dict with candidates, best, reasons."""
    if np.isnan(bars["sig"][i]) or bars["t"][i] < FIRST_BAR or bars["t"][i] > LAST_BAR:
        return None
    f = F.bar_features(bars, i, scal, b15)
    cols = AM.features_order()
    base = np.array([float(f.get(c, np.nan)) if c not in ("side", "stop_k") else 0.0 for c in cols])
    si, ki = cols.index("side"), cols.index("stop_k")
    X = np.tile(base, (6, 1))
    for j in range(6):
        X[j, si] = 1 if j < 3 else -1
        X[j, ki] = STOPS[j % 3]
    er, pw = AM.expected_r(X), AM.win_prob(X)
    cands = [{"side": "CE" if j < 3 else "PE", "stop_k": STOPS[j % 3], "exp_r": float(er[j]), "p_win": float(pw[j])} for j in range(6)]
    b = int(np.argmax(er))
    # reasons: what the score would be with each factor group set to its training-median ("neutral") value
    med = AM.meta().get("medians", {})
    reasons = []
    for name, feats in F.GROUPS.items():
        Xn = X[b: b + 1].copy()
        for c in feats:
            if c in cols and c in med:
                Xn[0, cols.index(c)] = med[c]
        delta = float(er[b] - AM.expected_r(Xn)[0])
        reasons.append({"factor": name, "effect": delta})
    reasons.sort(key=lambda r: -abs(r["effect"]))
    return {"candidates": cands, "best": b, "features": f, "reasons": reasons[:5], "threshold": threshold()}


def evaluate(symbol, candles, vix: dict, gcues, now: datetime, expiry: date, trades_today: int = 0,
             vix_chg_prev: float | None = None) -> dict:
    """Live evaluation. Returns {"signal", "checks", "plan", "ai"} in the same shape as the rule strategies."""
    out = {"signal": "WAIT", "checks": [], "plan": None, "ai": None}
    if symbol not in _SYM_ID:
        out["checks"].append((None, "The AI model is trained on Nifty and Bank Nifty only"))
        return out
    if not available():
        out["checks"].append((False, "AI model file missing on the server"))
        return out
    lvl = float(vix.get("level") or 14.0)
    chg = vix.get("chg_pct")
    prev_lvl = lvl / (1 + chg / 100) if chg not in (None, 0) else lvl
    vix_chg_prev = vix_chg_prev if vix_chg_prev is not None else 0.0
    df, d15 = enrich(candles, lvl)
    today = now.date()
    if now.tzinfo is not None:                                     # only completed bars
        df = df[df.index + timedelta(minutes=C.CANDLE_MIN) <= now]
        d15 = d15[d15["bar_end"] <= now]
    if df.empty or df.index[-1].date() != today:
        out["signal"] = "MARKET CLOSED"
        out["checks"].append((None, "The AI scores every completed 5-minute candle from 09:30 to 14:30 on a trading day"))
        return out
    df.loc[df.index.date == today, "vix"] = lvl
    first_ix = df[df.index.date == today].index[0]
    df.loc[first_ix, "vix"] = prev_lvl                                # so vix_day = today's change vs yesterday's close
    s = session(df, d15, today, symbol, expiry, (gcues or {}).get("avg_equity_chg"), vix_chg_prev)
    if s is None:
        out["checks"].append((None, "Need yesterday's session to build the levels"))
        return out
    bars, scal, b15 = s
    i = len(bars["c"]) - 1
    t = bars["t"][i]
    end = df.index[-1] + timedelta(minutes=C.CANDLE_MIN)
    if trades_today >= MAX_TRADES_PER_DAY:
        out["signal"] = "NO NEW ENTRIES"
        out["checks"].append((False, f"AI already took {MAX_TRADES_PER_DAY} trades in this instrument today (its limit)"))
        return out
    if t < FIRST_BAR:
        out["checks"].append((None, "AI scoring starts with the 09:30 candle"))
        return out
    if t > LAST_BAR:
        out["signal"] = "NO NEW ENTRIES"
        out["checks"].append((False, "AI entries only until the 14:35 close"))
        return out
    sc = score_bar(bars, scal, b15, i)
    if sc is None:
        out["checks"].append((None, "Need 10+ past sessions to measure the usual move"))
        return out
    best = sc["candidates"][sc["best"]]
    thr = sc["threshold"]
    spot = float(bars["c"][i])
    a = float(bars["atr"][i])
    sign = 1 if best["side"] == "CE" else -1
    risk = best["stop_k"] * a
    out["ai"] = {**{k: sc[k] for k in ("candidates", "best", "reasons", "threshold")}, "bar_end": end.strftime("%H:%M"),
                 "levels": {"entry": spot, "sl": spot - sign * risk, "target": spot + sign * RR * risk, "atr": a}}
    fresh = (now - end).total_seconds() / 60 <= C.SIGNAL_VALID_MIN
    out["checks"].append((None, f"{end:%H:%M} candle scored · best: buy {'CALL' if sign > 0 else 'PUT'}, stop {best['stop_k']:g} × ATR · "
                                f"expected {best['exp_r']:+.2f}R · win chance {best['p_win']*100:.0f}%"))
    ok = best["exp_r"] >= thr
    out["checks"].append((ok, f"Expected R {best['exp_r']:+.2f} {'≥' if ok else '<'} threshold {thr:.2f}"))
    if ok and not fresh:
        out["checks"].append((False, f"Signal older than {C.SIGNAL_VALID_MIN} minutes - wait for the next candle"))
        return out
    if ok:
        out["signal"] = "BUY CALL" if sign > 0 else "BUY PUT"
        out["plan"] = {"strategy": "ai", "opt": best["side"], "entry": spot, "risk_pts": risk, "sl": spot - sign * risk,
                       "target": spot + sign * RR * risk, "rr": RR, "exp_r": best["exp_r"], "p_win": best["p_win"],
                       "stop_k": best["stop_k"], "trail": "fixed stop and 2R target, square-off 15:15",
                       "reasons": sc["reasons"]}
    return out


# ---------------------------------------------------------------- backtest
def backtest_days(symbol, candles, vix_intraday, vix_daily, trade_from, exp_for_day, price_trade, log_skip):
    """Walks every session; returns trade dicts via price_trade(day_df, i_in, side, sl, tgt, exp, i_out, x_spot, reason).
    exp_for_day(d) -> expiry date; price_trade returns a trade dict, a ("skip", i, why) tuple or None."""
    df, d15 = enrich(candles, vix_intraday if vix_intraday is not None else (None if vix_daily is None else None))
    if vix_intraday is None and vix_daily is not None and len(vix_daily):
        vd = vix_daily.copy()
        vd.index = pd.to_datetime(vd.index).date
        df["vix"] = [float(vd[vd.index < d].iloc[-1]) if (vd.index < d).any() else 14.0 for d in df.index.date]
    trades = []
    dates = sorted(set(df.index.date))
    vchg = {}
    if vix_daily is not None and len(vix_daily) > 1:
        vd = vix_daily.copy(); vd.index = pd.to_datetime(vd.index).date
        pct = vd.pct_change() * 100
        vchg = {d: float(pct[pct.index < d].iloc[-1]) if (pct.index < d).any() else 0.0 for d in dates}
    # pass 1: features of every decision bar of every session
    sessions, feats, where = [], [], []
    slices = _days(df)
    cutoff = train_until()
    for n, d in enumerate(dates):
        if n == 0 or (trade_from and d < trade_from) or (cutoff and d < cutoff):
            continue                                   # never show in-sample days as a backtest
        day_df = df.iloc[slices[d]]
        if len(day_df) < 60:
            continue
        exp = exp_for_day(d)
        if exp is None:
            continue
        sess = session(df, d15, d, symbol, exp, None, vchg.get(d, 0.0))
        if sess is None:
            continue
        bars, scal, b15 = sess
        si = len(sessions)
        sessions.append((d, day_df, exp, bars, scal, b15))
        for i in range(len(bars["c"])):
            if bars["t"][i] < FIRST_BAR or bars["t"][i] > LAST_BAR or np.isnan(bars["sig"][i]):
                continue
            feats.append(F.bar_features(bars, i, scal, b15))
            where.append((si, i))
    # pass 2: one prediction batch
    er = score_many(feats)
    pw_needed = AM.win_prob(_matrix(feats)).reshape(len(feats), 6) if feats else np.zeros((0, 6))
    by_session: dict[int, dict[int, int]] = {}
    for r, (si, i) in enumerate(where):
        by_session.setdefault(si, {})[i] = r
    thr = threshold()
    # pass 3: trade each session
    for si, (d, day_df, exp, bars, scal, b15) in enumerate(sessions):
        n_bars = len(bars["c"])
        busy_until, n_tr, losses = -1, 0, 0
        for i in range(n_bars):
            r = by_session.get(si, {}).get(i)
            if r is None or i <= busy_until or n_tr >= MAX_TRADES_PER_DAY or losses >= 2:
                continue
            b = int(np.argmax(er[r]))
            if er[r, b] < thr:
                continue
            best = {"side": "CE" if b < 3 else "PE", "stop_k": STOPS[b % 3], "exp_r": float(er[r, b]), "p_win": float(pw_needed[r, b])}
            sign = 1 if best["side"] == "CE" else -1
            a, spot = float(bars["atr"][i]), float(bars["c"][i])
            risk = best["stop_k"] * a
            sl, tgt = spot - sign * risk, spot + sign * RR * risk
            x_spot, reason, k_out = None, None, n_bars - 1
            for k in range(i + 1, n_bars):
                adverse, favour = (bars["l"][k], bars["h"][k]) if sign > 0 else (bars["h"][k], bars["l"][k])
                if sign * (adverse - sl) <= 0:
                    x_spot, reason, k_out = (bars["o"][k] if sign * (bars["o"][k] - sl) < 0 else sl), f"Stop-loss ({best['stop_k']:g} ATR)", k
                    break
                if sign * (favour - tgt) >= 0:
                    x_spot, reason, k_out = (bars["o"][k] if sign * (bars["o"][k] - tgt) > 0 else tgt), "Target 2R", k
                    break
                if bars["t"][k] + 5 >= 915:
                    x_spot, reason, k_out = float(bars["c"][k]), "Square-off", k
                    break
            if reason is None:
                x_spot, reason = float(bars["c"][-1]), "Square-off"
            res = price_trade(day_df, i, best["side"], sl, tgt, exp, k_out, float(x_spot), reason, best)
            if res is None:
                continue
            if isinstance(res, tuple):
                log_skip(res[2])
                busy_until = i
                continue
            trades.append(res)
            busy_until = k_out
            n_tr += 1
            if res["pnl"] < 0:
                losses += 1
    return trades
