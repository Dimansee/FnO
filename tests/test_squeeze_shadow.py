"""Squeeze-release shadow slot tests (round 101).

1. The rule on synthetic 5-minute bars: release bar -> entry signal only in the first minutes of the next bar.
2. Through its every-minute tick: records ONE shadow trade a day, closes it by stop / momentum turn / 15:15, flags a trade the
   app left open overnight, respects the off switch - and never writes to the paper account, the scalp book or the carry slot.
3. The API and the nightly journal export.
4. On real history (when research/data is present): a 12-session window of candles (what the live feed gives) produces the same
   signals as the full history. (Trade-by-trade parity with the backtest: research/round101/n_shadow_parity.py, 569 of 569.)

Run: PYTHONPATH=. python tests/test_squeeze_shadow.py   (needs `pip install "fakeredis[lua]"`)
"""
import os, sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(6417)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)

import numpy as np
import pandas as pd

from fno import config as C, store, indicators as I, market as M
from fno import squeeze as SQ, scalp as SC, carry as CY, journal as JR

IST = C.IST
MIN15 = timedelta(minutes=15)


def history(seed, last_day, sessions=14):
    """Quiet random walk with a burst now and then (so that squeezes form and release)."""
    rng = np.random.default_rng(seed)
    days, d = [], last_day
    while len(days) < sessions:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    rows, px = [], 80_000.0
    for d in sorted(days):
        t = datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST)
        vol = 0.0004
        for k in range(75):
            if rng.random() < 0.04:
                vol = 0.0025 if vol < 0.001 else 0.0002
            o = px
            px *= 1 + rng.normal(0, vol)
            rows.append((t, o, max(o, px) * 1.0001, min(o, px) * 0.9999, px, 0))
            t += timedelta(minutes=5)
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")


# ---------------------------------------------------------------- 1. the rule
DAY = date(2026, 10, 20)                                    # Tuesday
BARS = REL = None
for seed in range(200):
    h = history(seed, DAY)
    f = SQ.frame(h)
    today = f[(f.index.date == DAY) & (f["long"] | f["short"])]
    today = today[[time_ok for time_ok in ((ts + MIN15).time() >= SQ.FIRST_BAR and (ts + MIN15).hour < 12 for ts in today.index)]]
    if len(today) and float(today["risk"].iloc[0]) > 0:
        BARS, REL = h, today.index[0]
        break
assert BARS is not None, "no synthetic release found"
ENTRY = (REL + MIN15).to_pydatetime()
FULL = SQ.frame(BARS)
row = FULL.loc[REL]
SIDE = 1 if row["long"] else -1
assert bool(FULL["on"].shift(1).loc[REL]) and not bool(row["on"]) and row["rel"], "a release is the first bar out of a squeeze"
assert SQ.RULE == {"tf": 15, "n": 14, "bb": 2.0, "kc": 1.5, "atr_kc": 10, "atr_stop": 14, "stop_k": 1.5, "per_day": 1}, "the tested settings"


def view(now):
    """What the feed shows at `now`: completed 5-minute candles plus the one forming (flat at its open)."""
    done = BARS[BARS.index + timedelta(minutes=5) <= now]
    nxt = BARS[(BARS.index <= now) & (BARS.index + timedelta(minutes=5) > now)]
    if len(nxt):
        o = float(nxt["open"].iloc[0])
        done = pd.concat([done, pd.DataFrame([{"open": o, "high": o, "low": o, "close": o, "volume": 0}], index=nxt.index[:1])])
    return done


sg = SQ.entry_check(SQ.frame(view(ENTRY + timedelta(seconds=40)), ENTRY + timedelta(seconds=40)), ENTRY + timedelta(seconds=40))
assert sg and sg["side"] == SIDE and sg["entry_bar"] == pd.Timestamp(ENTRY).isoformat() and sg["risk_pts"] > 0, sg
early = ENTRY - timedelta(minutes=1)
assert SQ.entry_check(SQ.frame(view(early), early), early) is None, "the release bar is not complete yet"
late = ENTRY + timedelta(minutes=SQ.GRACE_MIN, seconds=5)
assert SQ.entry_check(SQ.frame(view(late), late), late) is None, "too late into the bar"
assert len(SQ.bars15(view(ENTRY + timedelta(minutes=7)), ENTRY + timedelta(minutes=7))) == len(SQ.bars15(view(ENTRY), ENTRY)), "a forming bar is dropped"
print(f"rule OK: release bar {REL:%H:%M}, entry {ENTRY:%H:%M}, side {'call' if SIDE > 0 else 'put'}, stop {sg['risk_pts']} pts")

# ---------------------------------------------------------------- 2. the tick
NOW = [None]
EXP_LIST = [DAY + timedelta(days=2), DAY + timedelta(days=9)]


class FakeMarket:
    br, source, clients = None, "TEST · synthetic", {}

    def __init__(self):
        self.errors, self.calls = [], 0

    def candles(self, sym):
        self.calls += 1
        return view(NOW[0]) if sym == "SENSEX" else pd.DataFrame()

    def spot(self, sym):
        c = view(NOW[0])
        return float(c["close"].iloc[-1]) if len(c) else None

    def vix(self):
        return {"level": 14.0, "chg_pct": 0.0}

    def expiries_and_lot(self, sym):
        return EXP_LIST, 20

    def chain(self, sym, exp, spot, vix, candles=None):
        return M.theoretical_chain(sym, exp, spot, vix, None)

    def leg_prices(self, positions, spots, vix_level):
        return {lg["key"]: round(I.bs_price(spots[p["symbol"]], lg["strike"], I.years_to_expiry(date.fromisoformat(p["expiry"])), 0.14, lg["opt"]), 2)
                for p in positions for lg in p["legs"]}

    def exit_fills(self, positions, prices, mode="realistic"):
        return prices


C.now_ist = lambda: NOW[0]
C.today_ist = lambda: NOW[0].date()
fm = FakeMarket()
assert SQ.settings()["on"] is True, "the shadow is on by default: it only records"

NOW[0] = ENTRY - timedelta(minutes=8)
assert SQ.tick(NOW[0], fm) == {} and fm.calls == 0, "between bar closes with nothing open it reads no prices"
NOW[0] = ENTRY + timedelta(seconds=40)
r = SQ.tick(NOW[0], fm)
assert len(r["opened"]) == 1 and "no order placed" in r["opened"][0]["message"], r
p = SQ.positions()[0]
lg = p["legs"][0]
assert lg["opt"] == ("CE" if SIDE > 0 else "PE") and p["qty"] == 20 and p["shadow"] and abs(lg["strike"] - p["entry_spot"]) <= 50, p
assert abs(p["sl"] - (p["entry_spot"] - SIDE * p["risk_pts"])) < 0.02 and p["bar_open"] == round(float(BARS.loc[pd.Timestamp(ENTRY), "open"]), 2), p
assert store.positions() == [] and store.trades() == [] and SC.positions() == [] and CY.positions() == [], "a shadow trade must never reach a ledger"
NOW[0] = ENTRY + timedelta(seconds=100)
assert not SQ.tick(NOW[0], fm).get("opened") and len(SQ.positions()) == 1, "one a day"
closed = None
t = ENTRY + timedelta(minutes=2, seconds=5)
while t.time() <= C.MARKET_CLOSE and closed is None:
    NOW[0] = t
    r = SQ.tick(t, fm)
    closed = (r.get("closed") or [None])[0]
    t += timedelta(minutes=1)
assert closed and not SQ.positions(), "the shadow trade must be closed by 15:15"
assert closed["reason"] in ("Stop (1.5 x ATR)", "Momentum turned", "15:15 square-off"), closed
assert closed["charges"] > 0 and closed["r_live"] is not None and closed["r_rule"] is not None and closed["shadow"], closed
if closed["reason"].startswith("Stop"):
    assert closed["rule_exit"] == closed["sl"] and closed["r_rule"] <= -0.9, closed
while t.time() <= C.MARKET_CLOSE:                           # the rest of the day: no second trade
    NOW[0] = t
    assert not SQ.tick(t, fm).get("opened")
    t += timedelta(minutes=1)
assert len(SQ.trades()) == 1 and store.positions() == [] and store.trades() == [] and SC.trades() == [] and CY.trades() == []
print(f"tick OK: {closed['strike']:g}{closed['opt']} {closed['entry_prem']} -> {closed['exit_prem']}, {closed['reason']}, "
      f"R live {closed['r_live']}, R rule {closed['r_rule']}, net ₹{closed['pnl']:,.0f} per lot (recorded only)")

# the 15:15 square-off and a trade the app left open overnight
SQ.reset()
NOW[0] = ENTRY + timedelta(seconds=40)
assert SQ.tick(NOW[0], fm)["opened"]
p = SQ.positions()[0]
far = dict(p, sl=p["entry_spot"] - SIDE * 1e6)                 # a stop that cannot be hit
flat = SQ.frame(view(NOW[0]), NOW[0]).assign(mom=float(SIDE))  # momentum never turns
sq_t = datetime(DAY.year, DAY.month, DAY.day, 15, 15, 20, tzinfo=IST)
assert SQ.exit_check(flat, view(sq_t), far, sq_t - timedelta(minutes=10), 80_000.0) is None
assert SQ.exit_check(flat, view(sq_t), far, sq_t, 80_000.0)[0] == "15:15 square-off"
nxt = datetime(DAY.year, DAY.month, DAY.day, 9, 16, 5, tzinfo=IST) + timedelta(days=1)
assert SQ.exit_check(flat, view(sq_t), far, nxt, 80_000.0)[0].startswith("Closed late")
NOW[0] = sq_t
stale = SQ.tick(nxt, fm)
assert stale["closed"] and not SQ.positions(), stale
print("square-off and late close OK")

# the off switch: nothing new is recorded
SQ.reset()
SQ.save_settings({"on": False})
NOW[0] = ENTRY + timedelta(seconds=40)
assert SQ.tick(NOW[0], fm) == {"off": True} and not SQ.positions()
SQ.save_settings({"on": True})
assert SQ.tick(NOW[0], fm)["opened"]

# ---------------------------------------------------------------- 3. API and journal
from app import app  # noqa: E402
c = app.test_client()
assert c.post("/api/login", json={"password": "pw"}).status_code == 200
d = c.get("/api/squeeze").get_json()
assert d["settings"]["on"] and d["symbol"] == "SENSEX" and len(d["positions"]) == 1 and d["backtest"]["rows"] and "Nothing is bought" in d["rule"], d
assert d["log"] and d["log"][0]["event"] == "opened", d["log"]
assert c.post("/api/squeeze/settings", json={"on": False}).get_json()["settings"]["on"] is False
assert c.post("/api/squeeze/settings", json={"on": True}).get_json()["settings"]["on"] is True
jd = JR.day(DAY.isoformat())
assert jd["squeeze_shadow"]["open"] and jd["squeeze_shadow"]["rule"]["n"] == 14 and jd["squeeze_shadow"]["log"], jd.get("squeeze_shadow")
print("API and journal OK")

# ---------------------------------------------------------------- 4. real history: a short window gives the same signals
P1 = os.path.join(ROOT, "research", "data", "SENSEX_1m_upstox.csv.gz")
if os.path.exists(P1):
    m1 = pd.read_csv(P1)
    tcol = next(k for k in m1.columns if k.lower() in ("ts", "time", "datetime", "date", "timestamp"))
    m1[tcol] = pd.to_datetime(m1[tcol])
    m1 = m1.set_index(tcol).sort_index()
    if m1.index.tz is None:
        m1.index = m1.index.tz_localize(IST)
    else:
        m1.index = m1.index.tz_convert(IST)
    m1 = m1[(m1.index.time >= C.MARKET_OPEN) & (m1.index.time < C.MARKET_CLOSE)].iloc[-75 * 5 * 120:]      # last ~120 sessions
    parts = []
    for _d, g in m1.groupby(m1.index.date):
        r5 = g.resample("5min", origin=g.index[0].replace(hour=9, minute=15, second=0, microsecond=0), label="left", closed="left")
        parts.append(r5.agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna())
    m5 = pd.concat(parts)
    full = SQ.frame(m5)
    days = sorted(set(m5.index.date))
    n_sig = bad = 0
    for k in range(40, len(days), 4):
        w = m5[(m5.index.date >= days[k - 11]) & (m5.index.date <= days[k])]
        fw = SQ.frame(w)
        fw = fw[fw.index.date == days[k]]
        g = full.loc[fw.index]
        bad += int(((fw["long"] != g["long"]) | (fw["short"] != g["short"]) | ((fw["mom"] < 0) != (g["mom"] < 0))).sum())
        n_sig += int((g["long"] | g["short"]).sum())
        assert float((fw["risk"] - g["risk"]).abs().max()) < 0.5, "the stop distance must not depend on how much history is loaded"
    assert bad == 0 and n_sig > 5, (bad, n_sig)
    print(f"real history OK: {n_sig} entry signals identical on a 12-session window")
else:
    print("real history: research/data not present - skipped")
print("ALL OK")
