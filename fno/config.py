"""Central settings for the F&O Paper Trading Trainer (web version).

Values marked VERIFY can change when NSE revises contract specs. When a broker
is connected, lot sizes and expiries come from the broker / exchange master and
these defaults are only a fallback.
"""
import os
from datetime import datetime, time
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
TICK_CRON = "* 4-9 * * 1-5"   # UTC = 09:30-15:29 IST, Mon-Fri (~360 calls/day; Vercel free QStash limit is 500/day)
PRODUCTION_HOST = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")

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
}

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
VIX_MIN = 11.0             # no trades when India VIX is below 11 (moves too small to pay for the option)
SIGNAL_VALID_MIN = 10      # a half-hour signal can still be taken for 10 minutes if price holds beyond the band
# Second strategy: Camarilla breakout (research round 5, research/README.md)
CAM_LAST_ENTRY = time(13, 0)
CAM_VIX_MAX = 22.0
CAM_BE_R = 1.0             # stop moves to entry after +1R
CAM_TIME_STOP = 45         # exit if +0.5R not reached within 45 minutes
CAM_RMIN, CAM_RMAX = 1.0, 3.0   # stop distance kept between 1 and 3 x ATR
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
MAX_TRADES_PER_DAY = 4   # two strategies x two indices; the 2-loser and 2% daily-loss stops still apply

DEFAULT_CAPITAL = 200_000
RISK_FREE = 0.065
