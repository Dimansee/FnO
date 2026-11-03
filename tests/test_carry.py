"""Overnight carry slot tests (round 77).

1. The strong-close signal on hand-made 5-minute bars.
2. Through the every-minute tick: OFF does nothing; ON buys 1 ATM lot at 15:20 after a strong close, sells it at the next
   open, uses its own ledger (main paper account and scalp book untouched), respects the 50% premium guard and the weekly budget.
3. Signal parity with research/round75b (1-minute bars) on real history, when research/data is present.

Run: PYTHONPATH=. python tests/test_carry.py   (needs `pip install "fakeredis[lua]"`)
"""
import os, sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(6413)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)

import pandas as pd

from fno import config as C, store, indicators as I, market as M
from fno import carry as CY, scalp as SC

IST = C.IST
D1, D2 = date(2026, 10, 19), date(2026, 10, 20)          # Monday, Tuesday


def day_bars(d, start, end, low_dip=0.0):
    """5-min bars 09:15-15:25 moving linearly from start to end (a small dip early so the range is not degenerate)."""
    rows, t = [], datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST)
    n = 75
    for k in range(n):
        o = start + (end - start) * k / n
        c = start + (end - start) * (k + 1) / n
        lo = min(o, c) - (low_dip if k == 2 else 1)
        rows.append((t, o, max(o, c) + 1, lo, c, 0))
        t += timedelta(minutes=5)
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")


# ---------------------------------------------------------------- 1. signal
up = day_bars(D1, 24800, 25000, low_dip=20)                # +0.8%, closes at the high
s = CY.signal(up[up.index.time <= pd.Timestamp("15:15").time()], D1)
assert s["side"] == 1 and s["move_pct"] >= 0.5, s
dn = day_bars(D1, 25000, 24800)
assert CY.signal(dn, D1)["side"] == -1
flat = day_bars(D1, 24800, 24850)                           # +0.2%: no signal
assert CY.signal(flat, D1)["side"] == 0
early = up[up.index.time <= pd.Timestamp("14:00").time()]   # before the 15:15 bar exists: no decision
assert CY.signal(early, D1)["side"] == 0 and "not complete" in CY.signal(early, D1)["why"]
print("signal OK")

# ---------------------------------------------------------------- 2. tick
NOW = [None]
BARS = pd.concat([up, day_bars(D2, 25060, 25100)])
EXP_LIST = [D1, D2 + timedelta(days=7)]                     # today's expiry is excluded (must be alive tomorrow)


class FakeMarket:
    br, source, clients = None, "TEST · synthetic", {}

    def __init__(self):
        self.errors = []

    def candles(self, sym):
        return BARS[BARS.index + timedelta(minutes=5) <= NOW[0]] if sym == "NIFTY" else pd.DataFrame()

    def spot(self, sym):
        c = self.candles(sym)
        return float(c["close"].iloc[-1]) if len(c) else None

    def vix(self):
        return {"level": 14.0, "chg_pct": 0.0}

    def expiries_and_lot(self, sym):
        return EXP_LIST, 65

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
at = lambda d, h, mi: datetime(d.year, d.month, d.day, h, mi, 5, tzinfo=IST)

NOW[0] = at(D1, 15, 21)
assert CY.tick(NOW[0], fm) == {"off": True} and not CY.positions()              # OFF by default
CY.save_settings({"on": True, "syms": ["NIFTY", "BANKNIFTY", "XYZ"], "capital": 50_000})
assert CY.settings()["syms"] == ["NIFTY", "BANKNIFTY"]
NOW[0] = at(D1, 15, 0)
assert not CY.tick(NOW[0], fm).get("opened")                                     # outside the 15:20-15:28 window
NOW[0] = at(D1, 15, 21)
r = CY.tick(NOW[0], fm)
assert len(r["opened"]) == 1 and r["opened"][0]["symbol"] == "NIFTY", r
p = CY.positions()[0]
assert p["legs"][0]["opt"] == "CE" and p["qty"] == 65 and p["expiry"] == str(EXP_LIST[1]) and p["legs"][0]["strike"] % 50 == 0, p
assert abs(p["legs"][0]["strike"] - p["entry_spot"]) <= 25, p                       # ATM
NOW[0] = at(D1, 15, 24)
assert not CY.tick(NOW[0], fm).get("opened") and len(CY.positions()) == 1           # once a day
NOW[0] = at(D2, 9, 10)
assert not CY.tick(NOW[0], fm).get("closed")                                     # not before the open
NOW[0] = at(D2, 9, 21)
r = CY.tick(NOW[0], fm)
assert len(r["closed"]) == 1 and not CY.positions(), r
t = CY.trades()[0]
assert t["reason"] == "Sold at the next open" and t["gap_pct"] > 0 and t["charges"] > 0 and t["pnl"] > 0, t
assert store.positions() == [] and store.trades() == [] and SC.trades() == [], "carry must never write to the main ledger or the scalp book"
print(f"tick OK: bought {t['strike']:g}{t['opt']} at {t['entry_prem']}, sold at {t['exit_prem']} after a {t['gap_pct']}% gap, net ₹{t['pnl']:,.0f}")

# guards: premium > 50% of equity; weekly budget used up
CY.reset()
CY.save_settings({"capital": 5_000})
NOW[0] = at(D1, 15, 21)
r = CY.tick(NOW[0], fm)
assert not r["opened"] and any("50%" in x["why"] for x in r["skipped"]), r
CY.reset()
CY.save_settings({"capital": 50_000})
store.hset(CY.TRADES, "x1", store._dump({"id": "x1", "symbol": "NIFTY", "opened": "2026-10-16T15:21:00+05:30",
                                         "closed": at(D1, 9, 20).isoformat(), "pnl": -1_200.0}))
NOW[0] = at(D1, 15, 22)
r = CY.tick(NOW[0], fm)
assert not r["opened"] and any("weekly budget" in x["why"] for x in r["skipped"]), r
print("guards OK")

# the API
from app import app  # noqa: E402
c = app.test_client()
assert c.post("/api/login", json={"password": "pw"}).status_code == 200
d = c.get("/api/carry").get_json()
assert d["settings"]["on"] and d["backtest"]["rows"] and d["stats"]["trades"] == 1, d
assert c.post("/api/carry/settings", json={"on": False}).get_json()["settings"]["on"] is False
assert c.post("/api/carry/exit", json={"id": "nope"}).status_code == 400
print("api OK")

# ---------------------------------------------------------------- 3. parity with round 75b on real history
DATA = os.path.join(ROOT, "research", "data")
trf = os.path.join(ROOT, "research", "round75b_trades_0.01.csv.gz")
if os.path.exists(os.path.join(DATA, "NIFTY_1m_upstox.csv.gz")) and os.path.exists(trf):
    sys.path.insert(0, os.path.join(ROOT, "research"))
    import round25_harness as H
    x = H.load("NIFTY")
    x = x[x.index >= "2023-10-01"]
    c5 = x.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    if c5.index.tz is None:
        c5.index = c5.index.tz_localize(IST)
    T = pd.read_csv(trf)
    T = T[(T.sym == "NIFTY") & (T.exit == "next_open")]
    want = {(r.date, r.side) for r in T[T.sig == "strong"].itertuples()}
    days = sorted(set(T.date))
    agree = n = 0
    for dd in days:
        dt = date.fromisoformat(dd)
        sd = c5[c5.index.date == dt]
        got = CY.signal(sd, dt)["side"]
        exp_ = 1 if (dd, 1) in want else (-1 if (dd, -1) in want else 0)
        agree += got == exp_
        n += 1
    rate = agree / n
    assert rate >= 0.95, rate
    print(f"parity OK: the live signal matches round 75b on {agree} of {n} Nifty days ({rate:.1%}; 5-min vs 1-min bars)")
else:
    print("parity skipped (no research data)")
