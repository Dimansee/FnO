"""Gold by trading session: how much gold moves, and which way, in Asia, London, New York and the overlap.

Measured live from recent gold bars (XAUUSD = COMEX gold futures via Yahoo, 15-minute bars, last ~59 days; or PAXG
from Binance), every bar assigned to its session with fno/sessions.py (daylight-saving aware). Per session:
  range     average high-low of the session, in $ and as a share of the whole day's range
  extremes  how often the day's high / low was made in that session
  drift     average close-to-close move during the session and how often it was up
  hours     average range of each hour of the day, shown in IST (the "when does gold move" heat strip)
These are descriptive numbers to plan around (when to watch, how wide stops need to be), not a trading signal:
the autopilot's rules still have to pass their own backtest before they trade.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import markets as MK
from . import sessions as SS

ORDER = ("asia", "london", "overlap", "newyork")


def _day_key(idx: pd.DatetimeIndex) -> pd.Index:
    """COMEX trading day: 18:00 New York to 17:00 New York, labelled by the date it ends on."""
    ny = idx.tz_convert(SS.NY)
    return pd.Index((ny + pd.Timedelta(hours=6)).date)


def stats_from_bars(df: pd.DataFrame) -> dict:
    """The session table from intraday bars (tz-aware index). Separate from the fetch so tests and research can call it."""
    if df.empty or df.index.tz is None:
        return {"rows": [], "hours": [], "days": 0}
    df = df[~df.index.duplicated()].sort_index()
    day = _day_key(df.index)
    g = df.groupby(day)
    day_hi, day_lo = g["high"].max(), g["low"].min()
    day_rng = (day_hi - day_lo)
    full = day_rng[g.size() >= max(8, int(g.size().median() * 0.7))].index      # skip half days / holidays
    rows = []
    for name in ORDER:
        m = SS.mask(df.index, name)
        sub = df[m]
        if sub.empty:
            continue
        sd = day[m]
        sg = sub.groupby(sd)
        hi, lo = sg["high"].max(), sg["low"].min()
        op, cl = sg["open"].first(), sg["close"].last()
        keep = hi.index.intersection(full)
        if not len(keep):
            continue
        hi, lo, op, cl = hi[keep], lo[keep], op[keep], cl[keep]
        rng = hi - lo
        share = (rng / day_rng[keep]).replace([np.inf, -np.inf], np.nan)
        move = (cl / op - 1) * 100
        made_hi = (hi >= day_hi[keep] - 1e-9).mean() * 100
        made_lo = (lo <= day_lo[keep] + 1e-9).mean() * 100
        px = float(cl.iloc[-1])
        rows.append({"session": name, "name": SS.NAMES[name], "days": int(len(keep)),
                     "avg_range": round(float(rng.mean()), 2), "avg_range_pct": round(float((rng / op).mean() * 100), 3),
                     "share_of_day": round(float(share.mean() * 100), 1),
                     "made_day_high_pct": round(float(made_hi), 1), "made_day_low_pct": round(float(made_lo), 1),
                     "avg_move_pct": round(float(move.mean()), 3), "up_pct": round(float((move > 0).mean() * 100), 1),
                     "t_move": round(float(move.mean() / (move.std(ddof=1) / np.sqrt(len(move)))), 2) if len(move) > 2 and move.std() > 0 else None,
                     "price": px})
    # hour-of-day strip in IST
    ist = df.index.tz_convert(SS.IST)
    hr = pd.Series((df["high"] - df["low"]).values, index=ist.hour)
    per_bar_min = max(1, int(round(pd.Series(df.index).diff().dropna().median().total_seconds() / 60))) if len(df) > 2 else 60
    scale = 60 / per_bar_min     # sum of bar ranges per hour understates the hourly range; report avg bar range x bars/hour as "activity"
    hours = []
    for h in range(24):
        v = hr[hr.index == h]
        if len(v):
            hours.append({"ist_hour": h, "avg_bar_range": round(float(v.mean()), 2), "activity": round(float(v.mean() * scale), 2), "bars": int(len(v))})
    top = sorted(hours, key=lambda x: -x["activity"])[:3]
    return {"rows": rows, "hours": hours, "busiest_ist": [f"{x['ist_hour']:02d}:00-{(x['ist_hour'] + 1) % 24:02d}:00" for x in top],
            "days": int(len(full)), "first": df.index[0].isoformat(timespec="minutes"), "last": df.index[-1].isoformat(timespec="minutes"),
            "bar_minutes": per_bar_min}


def session_stats(symbol: str = "XAUUSD") -> dict:
    """Live session table for a gold symbol (cached 3 h)."""
    from . import market as M

    def build():
        df = MK.candles("GOLD", symbol, "15m", 59, ttl=3600)
        out = stats_from_bars(df)
        out.update(symbol=symbol, at=datetime.now(timezone.utc).isoformat(timespec="minutes"),
                   source="Yahoo Finance GC=F (COMEX gold futures)" if symbol == "XAUUSD" else "Binance PAXG/USDT")
        return out
    return M.cached(f"gold_sess:{symbol}", 3 * 3600, build)


def board(symbol: str = "XAUUSD") -> dict:
    """Everything the Gold dashboard's session panel shows."""
    out = {"clock": SS.clock()}
    try:
        out["stats"] = session_stats(symbol)
    except Exception as e:
        out["stats"] = {"error": str(e)[:160], "rows": [], "hours": []}
    out["notes"] = NOTES
    return out


# Background the panel shows under the numbers (research summary; full write-up in the Project docs).
NOTES = [
    "Gold trades almost 24 hours on weekdays (Sunday 18:00 to Friday 17:00 New York, one-hour break daily). In IST that is roughly Monday 04:30 to Saturday 03:30.",
    "Measured on COMEX gold, Jun 2024 - Oct 2026 (hourly): the busiest hours are 18:00-21:00 IST in British/US summer (the London + New York overlap, with US data at 08:30 New York). A typical hour there moves about $24, twice the ~$12 of the quietest Asian hours. There is a smaller bump around 06:00-07:00 IST when Shanghai opens.",
    "London (avg $55 a session) and New York (avg $54) carry most of the day's range; New York makes the day's high on 54% of days. Asia averages $41: quieter per hour, but not dead.",
    "No session has a reliable direction. Asia was slightly up on average (2023-25 on PAXG), the Western sessions slightly down in 2021 and 2026, but none is statistically clear and none survived trading costs as a rule.",
    "Every session rule tested lost money after costs: London breakout of the Asian range, its fade, the Asian-hours drift, the New York opening-range breakout. Use the sessions to decide when to watch and how wide stops must be, not as signals.",
    "For an Indian resident, trading XAUUSD with overseas forex/CFD brokers is not allowed (RBI alert list). The real-money route is MCX gold (Gold, Gold Mini, Gold Petal) through a broker like Fyers, 09:00-23:30 IST (23:55 when the US is on winter time). This book is paper only.",
]
