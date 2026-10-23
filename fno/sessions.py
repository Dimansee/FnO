"""World trading sessions (used by the gold book, and available to any strategy as a time filter).

Each session is defined in its own city's clock, so daylight-saving changes are handled automatically:
  Asia      00:00 UTC (09:00 Tokyo, 08:00 Shanghai, 05:30 IST) until London opens
  London    08:00-17:00 London   (13:30-22:30 IST in Indian winter, 12:30-21:30 IST in British summer)
  New York  08:00-17:00 New York (18:30-03:30 IST in Indian winter, 17:30-02:30 IST in US summer)
  Overlap   London and New York both open (08:00 New York until 17:00 London; about 18:30-22:30 IST in winter)
Fixed points that move gold: LBMA gold price auctions 10:30 and 15:00 London, COMEX floor open 08:20 New York,
US economic data 08:30 New York, Fed decisions 14:00 New York, COMEX daily break 17:00-18:00 New York.
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

UTC = ZoneInfo("UTC")
LON = ZoneInfo("Europe/London")
NY = ZoneInfo("America/New_York")
IST = ZoneInfo("Asia/Kolkata")

NAMES = {"asia": "Asia (Tokyo / Shanghai / Mumbai)", "london": "London", "newyork": "New York", "overlap": "London + New York overlap"}

# key events, in the city where they happen
EVENTS = [
    {"id": "lon_open", "name": "London open", "tz": LON, "at": (8, 0), "why": "European banks start; gold volume jumps and the Asian range is often broken."},
    {"id": "lbma_am", "name": "LBMA gold price (AM auction)", "tz": LON, "at": (10, 30), "why": "The world's benchmark gold price is set. Short spikes around it are common."},
    {"id": "comex_open", "name": "COMEX open", "tz": NY, "at": (8, 20), "why": "US gold futures floor session starts."},
    {"id": "us_data", "name": "US economic data", "tz": NY, "at": (8, 30), "why": "Jobs, inflation (CPI) and other US numbers come out; the biggest gold moves of the day often start here."},
    {"id": "lbma_pm", "name": "LBMA gold price (PM auction)", "tz": LON, "at": (15, 0), "why": "Second benchmark auction; also where many funds trade. London starts winding down after it."},
    {"id": "comex_close", "name": "COMEX floor close", "tz": NY, "at": (13, 30), "why": "US settlement; the overlap's volume fades after this."},
    {"id": "break", "name": "Daily break (market shut 1 h)", "tz": NY, "at": (17, 0), "why": "COMEX / spot gold pause 17:00-18:00 New York; new trading day starts at 18:00."},
]


def _minutes(idx: pd.DatetimeIndex, tz) -> np.ndarray:
    loc = idx.tz_convert(tz)
    return np.asarray(loc.hour * 60 + loc.minute)


def mask(idx: pd.DatetimeIndex, name: str) -> np.ndarray:
    """True for bars that START inside the session. idx must be tz-aware."""
    if name in (None, "", "any"):
        return np.ones(len(idx), bool)
    if idx.tz is None:
        idx = idx.tz_localize(UTC)
    lon = _minutes(idx, LON)
    ny = _minutes(idx, NY)
    if name == "london":
        return (lon >= 8 * 60) & (lon < 17 * 60)
    if name == "newyork":
        return (ny >= 8 * 60) & (ny < 17 * 60)
    if name == "overlap":
        return (lon >= 8 * 60) & (lon < 17 * 60) & (ny >= 8 * 60) & (ny < 17 * 60)
    if name == "asia":
        utc = _minutes(idx, UTC)
        # from 00:00 UTC until London opens (07:00 UTC in British summer, 08:00 UTC in winter)
        return utc < _lon_open_utc(idx)
    raise ValueError(f"Unknown session '{name}'")


def _lon_open_utc(idx) -> np.ndarray:
    loc = idx.tz_convert(LON)
    utc = idx.tz_convert(UTC)
    off = (loc.hour * 60 + loc.minute - (utc.hour * 60 + utc.minute)) % 1440
    return 8 * 60 - np.asarray(off)


def label(ts) -> str:
    """Which session(s) a moment belongs to, in words."""
    i = pd.DatetimeIndex([pd.Timestamp(ts)])
    if i.tz is None:
        i = i.tz_localize(UTC)
    if mask(i, "overlap")[0]:
        return "overlap"
    for n in ("london", "newyork", "asia"):
        if mask(i, n)[0]:
            return n
    return "late"          # after New York closes, before Asia (quiet hours)


def _at(day, tz, hm) -> datetime:
    return datetime.combine(day, dtime(*hm), tzinfo=tz)


def windows_ist(day) -> list[dict]:
    """Each session's start and end in Indian time for the given (Indian) date."""
    lon_o, lon_c = _at(day, LON, (8, 0)), _at(day, LON, (17, 0))
    ny_o, ny_c = _at(day, NY, (8, 0)), _at(day, NY, (17, 0))
    asia_o = _at(day, UTC, (0, 0))
    out = [
        {"id": "asia", "name": NAMES["asia"], "start": asia_o, "end": lon_o},
        {"id": "london", "name": NAMES["london"], "start": lon_o, "end": lon_c},
        {"id": "newyork", "name": NAMES["newyork"], "start": ny_o, "end": ny_c},
        {"id": "overlap", "name": NAMES["overlap"], "start": max(lon_o, ny_o), "end": min(lon_c, ny_c)},
    ]
    for w in out:
        w["start_ist"] = w["start"].astimezone(IST).strftime("%H:%M")
        w["end_ist"] = w["end"].astimezone(IST).strftime("%H:%M")
    return out


def clock(now: datetime | None = None) -> dict:
    """Live session board: what is open now, today's windows in IST, and the next key events."""
    from . import markets as MK
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ist = now.astimezone(IST)
    open_ = MK.gold_open(now, pad_after=0)
    wins = windows_ist(ist.date())
    for w in wins:
        w["live"] = bool(open_ and w["start"] <= now < w["end"])
        w["start"], w["end"] = w["start"].isoformat(), w["end"].isoformat()
    evs = []
    for d in range(0, 4):
        for e in EVENTS:
            day = (now.astimezone(e["tz"]) + timedelta(days=d)).date()
            t = _at(day, e["tz"], e["at"])
            if t <= now or t.weekday() >= 5:
                continue
            evs.append({"id": e["id"], "name": e["name"], "why": e["why"], "at": t.astimezone(timezone.utc).isoformat(),
                        "ist": t.astimezone(IST).strftime("%a %H:%M"), "local": t.strftime("%H:%M ") + ("London" if e["tz"] is LON else "New York"),
                        "in_min": int((t - now).total_seconds() // 60)})
    evs.sort(key=lambda x: x["at"])
    seen, nxt = set(), []
    for e in evs:
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        nxt.append(e)
    return {"now_ist": ist.strftime("%a %d %b, %H:%M IST"), "gold_open": open_, "session_now": label(now) if open_ else "closed",
            "windows": wins, "next_events": nxt[:6],
            "dst_note": "London and New York change clocks on different dates (late March / late October-early November); "
                        "the IST times above already account for that."}
