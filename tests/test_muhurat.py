"""9 Nov 2026 review: Sunday's Muhurat hour (8 Nov) must not become "yesterday" for CPR, pivots, gap or the 3-day run."""
from datetime import datetime

import numpy as np
import pandas as pd

from fno import config as C
from fno import strategy as S


def _day(d, start="09:15", end="15:25", base=100.0, hi=None, lo=None):
    idx = pd.date_range(f"{d} {start}", f"{d} {end}", freq="5min", tz=C.IST)
    px = np.full(len(idx), base)
    df = pd.DataFrame({"open": px, "high": px + 1, "low": px - 1, "close": px, "volume": 1.0}, index=idx)
    if hi is not None:
        df.iloc[len(df) // 2, df.columns.get_loc("high")] = hi
    if lo is not None:
        df.iloc[len(df) // 2, df.columns.get_loc("low")] = lo
    return df


def _candles(with_budget_sat=False):
    fri = _day("2026-11-06", base=100, hi=110, lo=90)
    fri.iloc[-1, fri.columns.get_loc("close")] = 108
    parts = [_day("2026-11-05", base=98), fri,
             _day("2026-11-08", start="18:15", end="19:10", base=105),          # Muhurat (Sunday, ~1 hour)
             _day("2026-11-09", end="10:00", base=101)]                         # today, partly done
    return pd.concat(parts)


def test_muhurat_skipped_as_prior_day():
    c = _candles()
    now = datetime(2026, 11, 9, 10, 6, tzinfo=C.IST)
    today, hist = S.session_frames(c, now)
    assert today.index[-1].date().isoformat() == "2026-11-09"
    last = hist[hist.index.date == hist.index[-1].date()]
    assert last.index[0].date().isoformat() == "2026-11-06"           # Friday, not Sunday's Muhurat hour
    assert float(last["high"].max()) == 110 and float(last["low"].min()) == 90
    # wide CPR from Friday's range (would have looked narrow from the flat Muhurat hour)
    assert S.cpr_width(hist) > 1.0


def test_muhurat_day_itself_is_kept_when_it_is_today():
    c = _candles().loc[:"2026-11-08 19:10"]
    now = datetime(2026, 11, 8, 19, 20, tzinfo=C.IST)
    today, hist = S.session_frames(c, now)
    assert today.index[-1].date().isoformat() == "2026-11-08"
    assert hist.index[-1].date().isoformat() == "2026-11-06"


def test_full_saturday_session_kept():
    # Budget day 1 Feb 2025 (Saturday) was a full session: keep it
    c = pd.concat([_day("2025-01-31"), _day("2025-02-01"), _day("2025-02-03", end="09:30")])
    assert S.special_session_days(c, keep=None) == set()
    # a short weekend session (DR drill, 2 Mar 2024) is dropped
    c2 = pd.concat([_day("2024-03-01"), _day("2024-03-02", start="09:15", end="10:55"), _day("2024-03-04", end="09:30")])
    assert {d.isoformat() for d in S.special_session_days(c2)} == {"2024-03-02"}


def test_short_weekday_data_gap_not_dropped():
    # a normal weekday with partial data (late recorder) is a real session: keep it
    c = pd.concat([_day("2026-11-05", start="13:00"), _day("2026-11-06", end="09:30")])
    assert S.special_session_days(c, keep=None) == set()


if __name__ == "__main__":
    test_muhurat_skipped_as_prior_day()
    test_muhurat_day_itself_is_kept_when_it_is_today()
    test_full_saturday_session_kept()
    test_short_weekday_data_gap_not_dropped()
    print("muhurat / special-session tests passed")
