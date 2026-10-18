"""Scalp book tests (scalping-spec study, Oct 2026).

1. Governor rules on hand-made trade lists.
2. A whole synthetic trend day through the every-minute tick: the scalp book trades on its own ledger, squares off,
   and never touches the main paper account.
3. Parity with research/scalp_spec.py on real 1-minute history (runs only when research/data is present):
   the live functions must reproduce the backtest's day type, setup, stop and exit for every backtest trade.

Run: PYTHONPATH=. python tests/test_scalp.py   (needs `pip install "fakeredis[lua]"`)
"""
import os, sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(6411)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)

import numpy as np
import pandas as pd

from fno import config as C, store, indicators as I, market as M
from fno import scalp as SC

IST = C.IST

# ---------------------------------------------------------------- 1. governor
def T(minute, pnl, tag="B", gross=None, chg=70.0):
    return {"closed": f"2026-10-19T{minute}:00+05:30", "pnl": pnl, "charges": chg, "gross": pnl + chg if gross is None else gross, "tag": tag}

g = SC.governor([T("10:00", -100), T("10:10", -100), T("10:20", -100)], 20_000)
assert g["pause_until"] == "2026-10-19T10:50:00+05:30" and not g["stopped"], g
g = SC.governor([T("10:00", -250), T("10:10", -200)], 20_000)
assert g["stopped"] and "Day loss" in g["stopped"], g
g = SC.governor([T("10:00", 350), T("10:10", -200)], 20_000)            # peak 350 >= 300, now 150 <= 60% of 350
assert g["stopped"] and "Give-back" in g["stopped"], g
g = SC.governor([T("10:00", 400), T("10:10", -50)], 20_000)             # peak 400, now 350: still allowed
assert not g["stopped"], g
g = SC.governor([T(f"10:{k}0", 5, gross=75) for k in range(5)], 20_000)   # fees 350 > 30% of gross 375
assert g["stopped"] and "Charges" in g["stopped"], g
g = SC.governor([T("10:00", -10, tag="C"), T("10:30", 50, tag="B"), T("11:00", -10, tag="C")], 20_000)
assert g["setup_losses"]["C"] == 2 and not g["stopped"], g
print("governor OK")


# ---------------------------------------------------------------- 2. a synthetic trend day through the tick
rng = np.random.default_rng(21)
TODAY = date(2026, 10, 19)                                               # a Monday


def history():
    rows, px, d = [], 24800.0, TODAY - timedelta(days=35)
    while d < TODAY:
        if d.weekday() < 5:
            t = datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST)
            for _ in range(75):
                o = px
                px *= 1 + rng.normal(0, 0.0009)
                rows.append((t, o, max(o, px) * 1.0002, min(o, px) * 0.9998, px, 0))
                t += timedelta(minutes=5)
        d += timedelta(days=1)
    return rows, px


ROWS5, LAST = history()
ONE = []
px = LAST * 1.002
t = datetime(TODAY.year, TODAY.month, TODAY.day, 9, 15, tzinfo=IST)
for k in range(375):                                                     # 09:15-15:29: a steady up-trend with pull-backs
    o = px
    px *= 1 + 0.00005 + 0.0004 * np.sin(k / 6.0) / 6 + rng.normal(0, 0.00025)
    ONE.append((t, o, max(o, px) + 1.5, min(o, px) - 1.5, px, 0))
    t += timedelta(minutes=1)
C1 = pd.DataFrame(ONE, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")
c5_today = C1.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
C5_HIST = pd.concat([pd.DataFrame(ROWS5, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts"), c5_today])
EXP = date(2026, 10, 27)
NOW = [None]


class FakeMarket:
    br, source, clients = None, "TEST · synthetic", {}

    def __init__(self):
        self.errors = []

    def _now(self):
        return NOW[0]

    def candles(self, sym):                                              # 5-minute bars up to the current minute
        n = self._now()
        return C5_HIST[C5_HIST.index + timedelta(minutes=5) <= n] if sym == "NIFTY" else pd.DataFrame()

    def candles1(self, sym):
        return C1[C1.index <= self._now()] if sym == "NIFTY" else pd.DataFrame()

    def spot(self, sym):
        c = self.candles1(sym)
        return float(c["close"].iloc[-1]) if len(c) else None

    def vix(self):
        return {"level": 14.0, "chg_pct": 0.0}

    def expiries_and_lot(self, sym):
        return [date(2026, 10, 20), EXP], 65

    def chain(self, sym, exp, spot, vix, candles=None):
        return M.theoretical_chain(sym, exp, spot, vix, None)

    def leg_prices(self, positions, spots, vix_level):
        out = {}
        for p in positions:
            for lg in p["legs"]:
                out[lg["key"]] = round(I.bs_price(spots[p["symbol"]], lg["strike"], I.years_to_expiry(date.fromisoformat(p["expiry"])),
                                                  0.14, lg["opt"]), 2)
        return out

    def exit_fills(self, positions, prices, mode="realistic"):
        return prices


C.now_ist = lambda: NOW[0]
C.today_ist = lambda: NOW[0].date()
SC.save_settings({"on": True, "syms": ["NIFTY"], "capital": 20_000})
fm = FakeMarket()
opened = closed = 0
for minute in range(9 * 60 + 31, 15 * 60 + 21):
    NOW[0] = datetime(TODAY.year, TODAY.month, TODAY.day, minute // 60, minute % 60, 5, tzinfo=IST)
    r = SC.tick(NOW[0], fm)
    opened += len(r.get("opened", []))
    closed += len(r.get("closed", []))
    assert not [s for s in r.get("skipped", []) if "error" in s["why"]], r
trs = SC.trades()
assert opened >= 1 and closed == opened == len(trs), (opened, closed, len(trs))
assert not SC.positions(), "every scalp position must be closed by 15:15"
assert all(t["charges"] > 40 and t["qty"] % 65 == 0 and t["qty"] >= 65 and t["tag"] in "ABC" for t in trs), trs[:2]
assert all(t["entry_prem"] * t["qty"] <= 0.95 * (20_000 + sum(u["pnl"] for u in trs if u["closed"] < t["opened"])) + 1 for t in trs)
assert any(t["lots"] > 1 for t in trs), [t["lots"] for t in trs]          # max-lot sizing actually buys several lots
assert store.positions() == [] and store.trades() == [], "the scalp book must never write to the main ledger"
gv = SC.governor(SC._today_trades("NIFTY", NOW[0]), 20_000)
assert abs(gv["net"] - sum(t["pnl"] for t in trs)) < 1e-6
st = SC.state("NIFTY", fm, NOW[0])
assert st["mode"] in ("trend", "mid", "none") and st["zones"]["above"] and st["zones"]["below"], st.get("error")
print(f"tick OK: {len(trs)} scalp trades on the synthetic day, net ₹{gv['net']:,.0f}, fees ₹{gv['charges']:,.0f}; main ledger untouched")

# strike chooser: best of 2 ITM .. 2 OTM with max lots vs the old 1 lot / 1 ITM
ch = M.theoretical_chain("NIFTY", EXP, 25000.0, 14.0)
at = datetime(TODAY.year, TODAY.month, TODAY.day, 10, 0, tzinfo=IST)
pk, why = SC.pick_strike(ch, "NIFTY", 1, 25000.0, 30.0, EXP, at, 20_000, {"sizing": "max", "strike": "best", "lot": 65}, False)
assert pk and pk["qty"] % 65 == 0 and pk["price"] * pk["qty"] <= 19_000 and pk["itm"] in SC.STRIKES_BEST, (pk, why)
one, _ = SC.pick_strike(ch, "NIFTY", -1, 25000.0, 30.0, EXP, at, 20_000, {"sizing": "1lot", "strike": "itm1", "lot": 65}, False)
assert one["itm"] == 1 and one["qty"] == 65 and one["strike"] == 25050 and one["opt"] == "PE", one
none, why = SC.pick_strike(ch, "NIFTY", 1, 25000.0, 30.0, EXP, at, 500, {"sizing": "max", "strike": "best", "lot": 65}, False)
assert none is None and "costs" in why, why
print(f"strike chooser OK: picked {pk['strike']:g}{pk['opt']} ({SC.moneyness(pk['itm'])}) x {pk['lots']} lots, expected ₹{pk['expected']:,.0f}")

# OFF: no new trades, nothing open
SC.reset()
SC.save_settings({"on": False})
NOW[0] = datetime(TODAY.year, TODAY.month, TODAY.day, 10, 30, 5, tzinfo=IST)
assert SC.tick(NOW[0], fm) == {"off": True} and not SC.trades()

# the API
from app import app  # noqa: E402
c = app.test_client()
assert c.post("/api/login", json={"password": "pw"}).status_code == 200
import fno.market as MM  # noqa: E402
MM.Market = FakeMarket
r = c.get("/api/scalp")
assert r.status_code == 200 and "NIFTY" in r.get_json()["state"] and r.get_json()["backtest"]["rows"], r.get_data(as_text=True)[:300]
r = c.post("/api/scalp/settings", json={"on": True, "syms": ["NIFTY", "BANKNIFTY", "XYZ"], "conf": 70, "setups": "ab"})
s = r.get_json()["settings"]
assert s["on"] and s["syms"] == ["NIFTY", "BANKNIFTY"] and s["conf"] == 70 and s["setups"] == "AB", s
assert c.get("/api/scalp/levels?symbol=NIFTY").get_json()["zones"]["above"]
assert SC.save_settings({"syms": ["SENSEX", "NIFTY"]})["syms"] == ["SENSEX", "NIFTY"] and "SENSEX" in c.get("/api/scalp").get_json()["state"]
assert c.post("/api/scalp/reset").status_code == 200
print("api OK")


# ---------------------------------------------------------------- 3. parity with the backtest on real history
RD = os.path.join(ROOT, "research")
if os.path.exists(os.path.join(RD, "data", "NIFTY_1m_upstox.csv.gz")):
    os.chdir(RD)
    sys.path.insert(0, RD)
    import scalp_spec as R  # noqa: E402
    WHY = {"stop": "Stop", "trail": "Trail", "time": "Time stop", "vwap": "Closed back through VWAP", "level": "Next level", "eod": "Square-off"}
    for sym in ("NIFTY", "BANKNIFTY"):
        data = R.prep(sym)[-150:]
        tr = R.run(data, sym, R.CFGS["spec_full"])
        by = {x["dd"].d.isoformat(): x for x in data}
        nc = ns = ne = 0
        def live_x(xr):
            d5, dd = xr["d5"], xr["dd"]
            return SC.build(sym, {"t": np.array(d5.t), "c": np.array(d5.c), "vwap": np.array(d5.vwap), "e9": np.array(d5.e9),
                                  "e21": np.array(d5.e21), "e50": np.array(d5.x["e50"]), "adx": np.array(d5.adx), "atr": np.array(d5.atr),
                                  "ph": dd.ph, "pl": dd.pl, "pc": dd.pc, "narrow": xr["narrow"], "cpr_w": d5.cpr_w},
                            {"m": dd.m, "o": dd.o, "h": dd.h, "l": dd.l, "c": dd.c, "twap": dd.twap, "e9": xr["e9"], "e21": xr["e21"],
                             "a1": xr["a1"], "ts": [""] * len(dd.c)})

        for xr in data[-40:]:                                           # day type on every bar of 40 days
            xl = live_x(xr)
            for i in range(15, len(xr["dd"].c) - 1):
                assert R.classify(xr, i) == SC.classify(xl, i)[:2], (sym, xr["dd"].d, i)
                nc += 1
        for t in tr:
            xr = by[t["d"]]
            dd = xr["dd"]
            xl = live_x(xr)
            i, k = t["i"], t["k"]
            orb = {"1": False, "-1": False} if t["tag"] == "A" else {"1": True, "-1": True}
            sig = SC.find_signal(xl, i, t["mode"], t["side"], orb, "ABC")
            assert sig is not None and sig[0] == t["tag"] and sig[1] == t["side"], (sym, t, sig)
            pl = SC.plan(xl, i, sig, t["mode"], sym, C.INDICES[sym]["lot"], dd.scale)
            assert abs(pl["risk"] - t["risk"]) < 1e-6 and abs(pl["conf"] - t["conf"]) < 0.11, (sym, t, pl)
            ns += 1
            p = {**pl, "mode": t["mode"], "entry_m": int(dd.m[i]), "hi_fav": pl["entry"], "trail_on": False, "cp": R.cost_points(dd, sym)}
            res = None
            for kk in range(i + 1, len(dd.c)):
                res = SC.exit_step(p, xl["d1"], kk)
                if res:
                    break
            assert res is not None and kk == k and res[1].startswith(WHY[t["why"]]), (sym, t["d"], i, k, kk, t["why"], res)
            assert abs(SC.cost_points(sym, dd.c[0], C.INDICES[sym]["lot"]) - R.cost_points(dd, sym)) < 1e-6
            ne += 1
        print(f"parity OK {sym}: {nc} bars of day type, {ns} setups + stops and {ne} exits identical to the backtest")
else:
    print("parity skipped (no research/data)")
print("ALL SCALP OK")
