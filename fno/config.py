"""Central settings for the F&O Paper Trading Trainer (web version).

Values marked VERIFY can change when NSE revises contract specs. When a broker
is connected, lot sizes and expiries come from the broker / exchange master and
these defaults are only a fallback.
"""
import os
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist():
    return now_ist().date()


# ---------------------------------------------------------------------------
# Environment (set in Vercel project settings)
# ---------------------------------------------------------------------------
# Upstash Redis (added via Vercel Storage / Marketplace - either name set works)
REDIS_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL", "")
REDIS_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
# Upstash QStash - calls /api/tick every minute in market hours
QSTASH_URL = (os.environ.get("QSTASH_URL") or "https://qstash.upstash.io").rstrip("/")
QSTASH_TOKEN = os.environ.get("QSTASH_TOKEN", "")
PRE_CRON = "30 3 * * 1-5"       # UTC = 09:00 IST, Mon-Fri: one pre-open call (stock picks refresh)
TICK_CRON = "* 4-9 * * 1-5"   # UTC = 09:30-15:29 IST, Mon-Fri (~360 calls/day; Vercel free QStash limit is 500/day)
POST_CRON = "5 10 * * 1-5"      # UTC = 15:35 IST: one call after the close that writes the day's research note and lessons
OPEN_CRON = "45-59 3 * * 1-5"  # UTC = 09:15-09:29 IST: 15 extra calls/day (total ~376, still under 500) used ONLY to record option-chain snapshots
# Public host the schedules call. APP_HOST wins; Render and Vercel set their own variables.
PRODUCTION_HOST = (os.environ.get("APP_HOST") or os.environ.get("RENDER_EXTERNAL_HOSTNAME") or os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "")).replace("https://", "").rstrip("/")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")
LOCK_DATA = os.environ.get("LOCK_DATA_ENDPOINTS", "") == "1"   # set to 1 (after adding the CRON_SECRET GitHub secret) to require login or the cron secret on the journal/recorder data endpoints

# ---------------------------------------------------------------------------
# Instruments
# ---------------------------------------------------------------------------
INDICES = {
    "NIFTY": {
        "label": "Nifty 50", "upstox_key": "NSE_INDEX|Nifty 50", "fyers": "NSE:NIFTY50-INDEX",
        "yahoo": "^NSEI", "lot": 65, "step": 50, "expiry": "weekly", "iv_mult": 1.0,
    },
    "BANKNIFTY": {
        "label": "Bank Nifty", "upstox_key": "NSE_INDEX|Nifty Bank", "fyers": "NSE:NIFTYBANK-INDEX",
        "yahoo": "^NSEBANK", "lot": 30, "step": 100, "expiry": "monthly", "iv_mult": 1.2,
    },
    # BSE Sensex (options on BSE / BFO). Lot 20 since BSE notice 20241021-13; weekly expiry Thursday since Sep 2025
    # (BSE notice 20250623-59). The Noise / Camarilla rules were researched on Nifty and Bank Nifty only - see UNTESTED.
    "SENSEX": {
        "label": "Sensex", "upstox_key": "BSE_INDEX|SENSEX", "fyers": "BSE:SENSEX-INDEX",
        "yahoo": "^BSESN", "lot": 20, "step": 100, "expiry": "weekly", "iv_mult": 1.0, "exchange": "BSE",
    },
}
UNTESTED = {"SENSEX"}   # indices where only part of the rulebook held up in backtests: the app says so on the Signal page
# Round 24 (Sensex 1-minute history 2023-01 -> 2026-10): trend-day catcher +35.8k on 2023-25 and +19.2k in 2026 (Rs 2L, 1% risk,
# never tuned on Sensex); Noise + Camarilla -11.5k on 2023-25 (Camarilla PF 0.97), Noise alone -14.0k in 2026. Autopilot: catcher only.
UNTESTED_NOTE = {"SENSEX": "Sensex backtest (round 24, 2023 - Oct 2026): only the trend-day catcher held up (+₹35.8k in 2023-25, +₹19.2k in 2026 "
                           "at ₹2 lakh / 1% risk). Noise and Camarilla did not (2023-25 -₹11.5k), so Autopilot trades Sensex with the "
                           "trend-day catcher only."}

# Stock lot sizes below are only demo fallbacks (VERIFY). The app reads the
# real lot sizes from the exchange instrument master once a day.
STOCKS = {
    "RELIANCE": {"isin": "INE002A01018", "lot": 500, "step": 10},
    "HDFCBANK": {"isin": "INE040A01034", "lot": 550, "step": 10},
    "ICICIBANK": {"isin": "INE090A01021", "lot": 700, "step": 10},
    "INFY": {"isin": "INE009A01021", "lot": 400, "step": 20},
    "TCS": {"isin": "INE467B01029", "lot": 175, "step": 20},
    "SBIN": {"isin": "INE062A01020", "lot": 750, "step": 5},
    "AXISBANK": {"isin": "INE238A01034", "lot": 625, "step": 10},
    "BHARTIARTL": {"isin": "INE397D01024", "lot": 475, "step": 20},
    "LT": {"isin": "INE018A01030", "lot": 175, "step": 20},
    "ITC": {"isin": "INE154A01025", "lot": 1600, "step": 5},
}
for _sym, _s in STOCKS.items():
    _s.update({
        "label": _sym, "yahoo": f"{_sym}.NS", "upstox_key": f"NSE_EQ|{_s['isin']}",
        "fyers": f"NSE:{_sym}-EQ", "expiry": "monthly", "iv_mult": None,
    })

INDIA_VIX_UPSTOX = "NSE_INDEX|India VIX"
INDIA_VIX_FYERS = "NSE:INDIAVIX-INDEX"
INDIA_VIX_YAHOO = "^INDIAVIX"

GLOBAL_TICKERS = {
    "S&P 500": "^GSPC", "Nasdaq": "^IXIC", "Dow Jones": "^DJI", "S&P Futures": "ES=F",
    "Nasdaq Futures": "NQ=F", "Nikkei 225": "^N225", "Hang Seng": "^HSI", "Kospi": "^KS11",
}
MACRO_TICKERS = {
    "Brent Crude": "BZ=F", "Dollar Index": "DX-Y.NYB", "USD/INR": "USDINR=X", "US 10Y Yield": "^TNX",
}

NEWS_FEEDS_MARKET = [
    "https://news.google.com/rss/search?q=(Nifty+OR+Sensex+OR+%22stock+market%22)+India+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.livemint.com/rss/markets",
]
NEWS_FEED_STOCK = "https://news.google.com/rss/search?q={q}+share+when:2d&hl=en-IN&gl=IN&ceid=IN:en"

# ---------------------------------------------------------------------------
# Strategy rules - change only after backtesting
# ---------------------------------------------------------------------------
MARKET_OPEN = time(9, 15)
ORB_END = time(9, 30)
LAST_ENTRY = time(14, 30)
SQUARE_OFF = time(15, 15)
MARKET_CLOSE = time(15, 30)
CANDLE_MIN = 5

# Noise-area momentum (chosen by the 2023-2026 research loop, see research/README.md)
NOISE_MULT = 1.75          # band = open x (1 +/- 1.75 x typical move-since-open at this time of day)
NOISE_LOOKBACK = 14        # past sessions used for the typical move
NOISE_MIN_SESSIONS = 10
CHECK_EVERY_MIN = 30       # look for entries/exits only on the half hour: 09:45, 10:15 ... 14:15
FIRST_CHECK = time(9, 45)
STOP_ATR = 2.0             # underlying stop = 2 x ATR(14, 5-min)
RR_NOISE = 4.0             # underlying target = 4 x risk
NOISE_VIX_MAX = 18.0       # round 18: Noise trades with VIX 16+ lost money (59 trades, net -22k); a ceiling of 18 raised the unseen-120-day profit 60.6k -> 77.5k
VIX_MIN = 11.0             # no trades when India VIX is below 11 (moves too small to pay for the option)
SIGNAL_VALID_MIN = 10      # a half-hour signal can still be taken for 10 minutes if price holds beyond the band
# Second strategy: Camarilla breakout (research round 5, research/README.md)
CAM_LAST_ENTRY = time(13, 0)
FIRST_ENTRY_ANY = time(9, 20)   # auto paper-trading looks for signals between these times
LAST_ENTRY_ANY = time(14, 40)
CAM_VIX_MAX = 20.0         # round 18: Camarilla trades with VIX 19+ lost (11 trades, PF 0.18); ceiling lowered from 22
CAM_BE_R = 1.0             # stop moves to entry after +1R
CAM_TIME_STOP = 45         # exit if +0.5R not reached within 45 minutes
CAM_RMIN, CAM_RMAX = 1.0, 3.0   # stop distance kept between 1 and 3 x ATR
# Third strategy: trend-day catcher (research round 23, Oct 2026 - research/trend_day.py, fno/research_log.json "r23").
# Half-hour checks 10:45-14:15: 5-min close beyond the 1.75x noise band AND beyond VWAP, with price on that side of VWAP for
# at least 80% of today's 5-min bars. Stop = distance to VWAP + 0.5 ATR (1-3 ATR), stop to entry at +1R, no target, hold to 15:15.
# Runs in its own slot (it may hold a position while a Noise / Camarilla trade is open in the same index). VIX >= 11, no ceiling.
TREND_FIRST_CHECK = time(10, 45)
TREND_LAST_CHECK = time(14, 15)
TREND_VWAP_SHARE = 0.80
TREND_BE_R = 1.0
TREND_MIN_EQUITY = 40_000   # round 24: Autopilot adds the trend slot only once equity is >= Rs 40k (1-year bootstrap from Rs 20k:
                            # 65.9% of paths ended unable to afford a lot vs 73.5% ungated and 68.5% without the catcher)
# Market recorder (fno/recorder.py): option-chain snapshots kept for later research
RECORD_EVERY_MIN = 5       # one snapshot every 5 minutes, 09:15-15:30
RECORD_STRIKES = 15        # strikes each side of the money
RECORD_KEEP_DAYS = 120     # Redis copy expires after this; the nightly job keeps a permanent copy (was 45; raised so exports can reach back 120 days)
# NSE trading holidays (weekdays only). SOURCE: published 2026 NSE list - verify against the NSE circular; add next year's list in December.
HOLIDAYS = {date(2026, 1, 15), date(2026, 1, 26), date(2026, 3, 3), date(2026, 3, 26), date(2026, 3, 31), date(2026, 4, 3),
            date(2026, 4, 14), date(2026, 5, 1), date(2026, 5, 28), date(2026, 6, 26), date(2026, 9, 14), date(2026, 10, 2),
            date(2026, 10, 20), date(2026, 11, 10), date(2026, 11, 24), date(2026, 12, 25)}
HOLIDAYS_YEAR = 2026     # the last year the list above covers; the app warns when today is past it
# Calendar rules (research round 6, research/README.md)
BUDGET_DAYS = {date(2023, 2, 1), date(2024, 2, 1), date(2024, 7, 23), date(2025, 2, 1), date(2026, 2, 1), date(2027, 2, 1)}   # Union Budget: no new signal trades (all 4 test-period Budget days lost). Update yearly.
SKIP_OWN_EXPIRY = ()              # indices to sit out on their own expiry day. Tested for Bank Nifty: the losses came from its old
                                  # weekly-expiry era (to Nov 2024) and an independent check found no clear effect since, so none.
STRIKE_ITM = 1             # buy 1 strike in-the-money: on real option prices it loses less to time decay than ATM

MIN_BIAS_SCORE = 3
ORB_MIN_PCT = 0.15
ORB_MAX_PCT = 1.20
VIX_SPREAD_LEVEL = 16.0
VIX_SPIKE_PCT = 8.0
RR_TARGET = 2.0
PREMIUM_HARD_SL = 0.30
TIME_STOP_MIN = 45
SPREAD_WIDTH_STRIKES = 2

RISK_PER_TRADE = 0.01
MAX_DAILY_LOSS = 0.02
MAX_WEEKLY_LOSS = 0.04   # round 18: no new signal trades for the rest of the week after -4% (profit up, deepest fall 59k -> 54k)
MAX_SPREAD_PCT = 0.10    # skip a strike whose live bid-ask spread is wider than 10% of the ask (protective, not backtested: no history of real quotes)
MAX_TRADES_PER_DAY = 4   # two strategies x two indices; the 2-loser and 2% daily-loss stops still apply

DEFAULT_CAPITAL = 200_000
RISK_FREE = 0.065


# Autopilot (round 20): one switch that chooses everything from the tested rules and the account size
AUTOPILOT_SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
AUTOPILOT_SYM_STRATEGIES = {"SENSEX": ["trend"]}   # round 24: on Sensex only the trend-day catcher held up
AUTOPILOT_STRATEGIES = ["noise", "camarilla", "trend"]   # trend added in round 23 (paper autopilot)
AUTOPILOT_RISK_PCT = 1.0            # lots = 1% of CURRENT equity / risk per lot (compounds as the account grows)
AUTOPILOT_MAX_TRADE_RISK = 0.15     # a 1-lot floor is allowed only while one lot risks at most 15% of equity (round 20: 83% -> 46% of years dipping below half the account)
AUTOPILOT_SMALL_ACCOUNT = 100_000   # below this equity the weekly loss cap is 15% (4% would block every trade after one loss)
AUTOPILOT_WEEKLY_SMALL = 0.15
