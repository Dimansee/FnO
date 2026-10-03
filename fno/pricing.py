"""Option pricing corrected against REAL option prices (see research/calib.py, intraday_fit.py).

- IV level: India VIX x the median real-IV / VIX ratio that NSE's official closing prices
  of Nifty / Bank Nifty options showed (Oct 2023 - Oct 2026) for that days-to-expiry and
  moneyness (fno/iv_calibration.csv).
- Intraday: real 5-minute option candles lost a little more value per hour than the formula
  (more on range days and near expiry), so exit prices are bent toward that.
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

EXTRA_DECAY = [(2, 0.0126), (7, 0.0063), (14, 0.0016), (999, 0.0010)]   # share of premium lost per hour held
CHG_SCALE = 0.97


@lru_cache(maxsize=1)
def _table() -> dict:
    p = Path(__file__).with_name("iv_calibration.csv")
    try:
        with p.open() as f:
            return {(r["sym"], r["dteb"], int(float(r["otm"]))): float(r["ratio"]) for r in csv.DictReader(f)}
    except Exception:
        return {}


def _dteb(days: float) -> str:
    return "1" if days <= 1 else "2" if days <= 2 else "3-4" if days <= 4 else "5-7" if days <= 7 else "8-14" if days <= 14 else "15+"


def iv(symbol: str, vix: float, spot: float, strike: float, opt: str, dte_days: float, step: float, fallback_mult: float) -> float:
    m = int(round((strike - spot) / step)) * (1 if opt == "CE" else -1)
    m = max(-4, min(4, m))
    t = _table()
    r = t.get((symbol, _dteb(dte_days), m)) or t.get((symbol, _dteb(dte_days), 0)) or fallback_mult
    return min(max(vix / 100 * r, 0.06), 0.9)


def real_adjust(prem_in: float, prem_out: float, dte_days: float, hours: float) -> float:
    rate = next(r for lim, r in EXTRA_DECAY if dte_days <= lim)
    return max(prem_in + CHG_SCALE * (prem_out - prem_in) - prem_in * rate * hours, 0.05)
