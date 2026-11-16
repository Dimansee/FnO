"""16 Nov 2026: Fyers was logged out at the open. Autopilot bought NIFTY 25300CE at a FORMULA price (Rs 241.2) dated 24 Nov
(a market holiday - the estimated expiry list did not know). When Fyers connected, the 24 Nov date was unknown to it, Fyers
answered with the nearest expiry (17 Nov) and the position was linked to that contract (Rs 117): a false loss of Rs 8,000.

1. Estimated expiries move off holidays / weekends to the previous trading day.
2. Fyers.chain returns nothing for an expiry it does not list (never another expiry's chain).
3. With a broker set up, no order (Autopilot, signal, manual, scalp, carry) is placed while option prices are not live.
   Without any broker set up (pure demo) the app works as before.
4. An open position dated on a non-existent expiry is repaired: real contract, real entry price from the 1-minute candle.

Run: PYTHONPATH=. python tests/test_no_live_price.py   (needs `pip install "fakeredis[lua]"`)
"""
import os, sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(6419)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)

import pandas as pd

from fno import config as C, store, indicators as I, market as M, brokers as B, paper as P, service as SV

IST = C.IST
# ---------------------------------------------------------------- 1. estimated expiries and holidays
assert date(2026, 11, 24) in C.HOLIDAYS
w = I.demo_expiries("weekly", date(2026, 11, 16))
assert w[:3] == [date(2026, 11, 17), date(2026, 11, 23), date(2026, 12, 1)], w
assert I.demo_expiries("monthly", date(2026, 11, 16))[0] == date(2026, 11, 23)
assert all(d.weekday() < 5 and d not in C.HOLIDAYS for d in I.demo_expiries("weekly", date(2026, 1, 1), n=60))
print("expiries OK")

# ---------------------------------------------------------------- 2. Fyers.chain and an unknown expiry
calls = []


class FakeFyers(B.Fyers):
    def __init__(self):
        self.name = "fyers"
        self._exp_ts = {}

    def _raw_chain(self, key, ts="", strikecount=12, greeks=True):
        calls.append(ts)
        rows = [{"option_type": "", "strike_price": -1, "ltp": 25300.0}]
        if ts in ("", "T17"):                                # no timestamp = the nearest expiry, as the real API answers
            rows += [{"option_type": "CE", "strike_price": 25300, "symbol": "NSE:NIFTY26N1725300CE", "ltp": 117.0, "bid": 116.5, "ask": 117.5}]
        elif ts == "T23":
            rows += [{"option_type": "CE", "strike_price": 25300, "symbol": "NSE:NIFTY26NOV25300CE", "ltp": 236.0, "bid": 235.5, "ask": 236.5}]
        return {"expiryData": [{"date": "17-11-2026", "expiry": "T17"}, {"date": "23-11-2026", "expiry": "T23"}], "optionsChain": rows}


fy = FakeFyers()
assert fy.chain("NSE:NIFTY50-INDEX", date(2026, 11, 24)).empty, "an unlisted expiry must give no chain, not the nearest expiry"
assert "" not in calls[1:], "the chain itself must never be asked without an expiry"
c23 = fy.chain("NSE:NIFTY50-INDEX", date(2026, 11, 23))
assert c23.iloc[0]["ce_key"] == "NSE:NIFTY26NOV25300CE" and c23.iloc[0]["ce_ltp"] == 236.0
print("Fyers unknown expiry OK")

# ---------------------------------------------------------------- 3. no order without live prices when a broker is set up
class Ch(pd.DataFrame):
    pass


def chain(src):
    ch = M.theoretical_chain("NIFTY", date(2026, 11, 23), 25264.0, 15.7, None)
    ch.attrs["source"] = src
    return ch


class Mk:
    def __init__(self, setup, br):
        self.broker_setup, self.br = setup, br


assert M.live_needed(Mk(True, None), chain("demo")) is True, "broker set up, logged out: refuse"
assert M.live_needed(Mk(True, object()), chain("demo")) is True, "broker connected but the chain is a formula: refuse"
assert M.live_needed(Mk(True, object()), chain("live")) is False
assert M.live_needed(Mk(False, None), chain("demo")) is False, "pure demo (no broker ever set up) keeps working"
assert "estimated price" in M.NO_LIVE_PRICE
import inspect
for fn, mod in ((SV._place_signal, "service"), (SV.place_manual, "service")):
    assert "live_needed" in inspect.getsource(fn), f"{fn.__name__} must refuse orders without live prices"
from fno import scalp as SC, carry as CY, journal as JR
assert "live_needed" in inspect.getsource(SC._open) and "live_needed" in inspect.getsource(CY._open)
assert JR.AUTO_CATS[[c[0] for c in JR.AUTO_CATS].index("no_live_price")][2].startswith("Broker not connected")
store.put("fyers", {"app_id": "X", "token": None})          # a broker is set up but logged out -> Market.broker_setup
mk = M.Market()
assert mk.broker_setup is True and mk.br is None
store.put("fyers", {})
assert M.Market().broker_setup is False
print("no-live-price guard OK")

# ---------------------------------------------------------------- 4. repair of the 16 Nov position
NOW = datetime(2026, 11, 16, 9, 50, 5, tzinfo=IST)
C.now_ist = lambda: NOW
C.today_ist = lambda: NOW.date()
P.reset(25000) if hasattr(P, "reset") else None
pos = {"id": "48545ca9", "symbol": "NIFTY", "opened": "2026-11-16T09:35:03+05:30", "expiry": "2026-11-24", "lot": 65, "lots": 1, "qty": 65,
       "entry_prem": 241.2, "margin": 15678.0, "mode": "signal", "sl_prem": 207.17, "target_prem": 343.3, "spot_at_entry": 25264.1,
       "legs": [{"key": "NSE:NIFTY26N1725300CE", "opt": "CE", "price": 241.2, "side": "BUY", "src": "fyers", "strike": 25300.0}]}
store.insert_position(dict(pos))


class Br:
    name = "fyers"

    def underlying_key(self, meta):
        return "NSE:NIFTY50-INDEX"

    def expiries(self, key):
        return [date(2026, 11, 17), date(2026, 11, 23), date(2026, 12, 1)], 65

    def chain(self, key, expiry):
        px = {date(2026, 11, 17): ("NSE:NIFTY26N1725300CE", 117.7), date(2026, 11, 23): ("NSE:NIFTY26NOV25300CE", 252.0)}.get(expiry)
        if px is None:
            return pd.DataFrame()
        return pd.DataFrame([{"strike": 25300.0, "spot": 25316.0, "ce_key": px[0], "ce_ltp": px[1], "ce_bid": px[1] - 0.5, "ce_ask": px[1] + 0.5,
                              "pe_key": "X", "pe_ltp": 200.0, "pe_bid": 199.5, "pe_ask": 200.5}])

    def candles1(self, key):
        assert key == "NSE:NIFTY26NOV25300CE"
        t = pd.date_range("2026-11-16 09:15", periods=36, freq="1min", tz=IST)
        return pd.DataFrame({"open": [230.0 + i * 0.2 for i in range(36)], "high": 260.0, "low": 220.0, "close": 240.0, "volume": 1.0}, index=t)

    def quotes(self, keys):
        return {k: {"ltp": {"NSE:NIFTY26NOV25300CE": 252.0, "NSE:NIFTY26N1725300CE": 117.7}[k], "bid": None, "ask": None} for k in keys}


m = M.Market.__new__(M.Market)
_b = Br()
m.errors, m.ba, m.br, m.clients, m.broker_setup = [], {}, _b, {"fyers": _b}, True
m.key = lambda sym: "NSE:NIFTY50-INDEX"
M._CACHE.clear() if hasattr(M, "_CACHE") else None
assert m.real_expiry("NIFTY", date(2026, 11, 24)) == date(2026, 11, 23) and m.real_expiry("NIFTY", date(2026, 11, 17)) == date(2026, 11, 17)
assert m.real_expiry("NIFTY", date(2027, 3, 30)) is None, "nothing within 3 days: leave the position alone"
ps = store.positions()
prices = m.leg_prices(ps, {"NIFTY": 25316.0}, 15.7)
p = store.positions()[0]
lg = p["legs"][0]
assert lg["key"] == "NSE:NIFTY26NOV25300CE" and p["expiry"] == "2026-11-23", p
assert prices[lg["key"]] == 252.0, prices
real = round(234.0 * (1 + M.slip_pct(234.0)), 2)             # 09:35 candle open 230 + 20 * 0.2 = 234, plus the usual slippage
assert p["entry_prem"] == real and lg["price"] == real and p["margin"] == round(real * 65), p
assert abs(p["sl_prem"] - 207.17 * real / 241.2) < 0.02 and abs(p["target_prem"] - 343.3 * real / 241.2) < 0.02
cf = p["contract_fixed"]
assert cf["expiry_was"] == "2026-11-24" and cf["entry_was"] == 241.2 and cf["entry"] == real, cf
again = store.positions()
m.leg_prices(again, {"NIFTY": 25316.0}, 15.7)
assert store.positions()[0]["entry_prem"] == real, "the repair runs once"
good = {**pos, "id": "ok1", "expiry": "2026-11-23", "legs": [{**pos["legs"][0], "key": "NSE:NIFTY26NOV25300CE"}]}
assert m.fix_contract(dict(good), {"NIFTY": 25316.0}, 15.7) is False, "a position on a real expiry is never touched"
print(f"repair OK: 24 Nov -> 23 Nov contract, entry 241.2 -> {real}, marked at 252.0 instead of 117.7")

# the data broker (Upstox analytics) cannot list expiries: the other connected broker repairs the position
class Deaf(Br):
    name = "upstox"

    def expiries(self, key):
        raise RuntimeError("not allowed")


store.hdrop(store.POSITIONS) if hasattr(store, "hdrop") else None
store.insert_position(dict(pos, id="p2"))
m2 = M.Market.__new__(M.Market)
m2.errors, m2.ba, m2.br, m2.clients, m2.broker_setup = [], {}, Deaf(), {"upstox": Deaf(), "fyers": _b}, True
m2.key = lambda sym: "X"
m2.leg_prices([x for x in store.positions() if x["id"] == "p2"], {"NIFTY": 25316.0}, 15.7)
p2 = [x for x in store.positions() if x["id"] == "p2"][0]
assert p2["expiry"] == "2026-11-23" and p2["legs"][0]["key"] == "NSE:NIFTY26NOV25300CE" and p2["legs"][0]["src"] == "fyers", p2
print("repair through the second broker OK")
print("ALL OK")
