"""Offline end-to-end test: in-memory DB, synthetic prices, fake brokers."""
import os, sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402  local stand-in for Upstash Redis
_url, _tok = upstash_shim.start()
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)

import numpy as np
import pandas as pd

from fno import config as C, store, market as M, context as X, brokers as B, strategy as S

# ---------- synthetic market ----------
rng = np.random.default_rng(5)
NOW = [datetime(2026, 10, 1, 10, 52, tzinfo=C.IST)]  # a Thursday, 7 min after the 10:45 check
C.now_ist = lambda: NOW[0]
C.today_ist = lambda: NOW[0].date()


def fake(start, end_dt, trend=0.0006):
    rows, px = [], start
    d = end_dt.date() - timedelta(days=30)   # the noise band needs 14+ past sessions
    while d <= end_dt.date():
        if d.weekday() < 5:
            t = datetime(d.year, d.month, d.day, 9, 15, tzinfo=C.IST)
            for _ in range(75):
                if t > end_dt:
                    break
                o = px
                px *= 1 + (trend if d == end_dt.date() else 0) + rng.normal(0, 0.0008)
                rows.append((t, o, max(o, px) * 1.0003, min(o, px) * 0.9997, px, 0))
                t += timedelta(minutes=5)
        d += timedelta(days=1)
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")


CANDLES = {}
M.yahoo_candles = lambda t, period="5d", interval="5m": CANDLES.setdefault(t, fake(1300 if ".NS" in t else 24800, NOW[0]))
M.yahoo_daily = lambda t, period="1mo": pd.DataFrame({"close": [14.0, 13.2]}, index=pd.to_datetime(["2026-09-30", "2026-10-01"]))
M.master = lambda: {"lots": {"NIFTY": 65}, "eq_keys": {}}
M.upstox_public_history = lambda key, cal_days: pd.DataFrame()   # offline: backtest falls back to the (fake) Yahoo data
X.global_cues = lambda: {"markets": {"S&P 500": {"last": 6000, "chg_pct": 0.8, "date": "2026-10-01"}}, "avg_equity_chg": 0.8, "notes": []}
X.fetch_news = lambda s=None: {"items": [{"title": "Nifty rallies <b>", "link": "http://x", "when": None, "score": 4}], "score": 4, "events": [], "notes": []}
X.fii_dii = lambda: None
from fno import recorder as R  # noqa: E402
REC_CALLS = []
def _fake_nse(sym, n_exp=2):
    REC_CALLS.append(sym)
    spot = 24800 if sym == "NIFTY" else 52000
    step = 50 if sym == "NIFTY" else 100
    rows = [[spot + k * step, 120.5 - k, 120, 121, 1000 + k, 50, 13.1, 110.25 + k, 110, 111, 900, 40, 13.4] for k in range(-30, 31)]
    return [("2026-10-06", spot, rows), ("2026-10-13", spot, rows)]
R.nse_chain = _fake_nse

from app import app  # noqa: E402

c = app.test_client()


def j(r):
    assert r.status_code < 500, r.get_data(as_text=True)[:500]
    return r.get_json()


assert c.get("/api/dashboard").status_code == 401
assert c.post("/api/login", json={"password": "bad"}).status_code == 401
assert j(c.post("/api/login", json={"password": "pw"}))["ok"]
assert "F&amp;O" in c.get("/").get_data(as_text=True)

d = j(c.get("/api/dashboard?symbol=NIFTY"))
print("signal:", d["signal"]["signal"], "score", d["signal"]["score"], "source", d["source"], "lot", d["lot"], "cash", d["cash"])
for ok, t in d["signal"]["checks"]:
    print("   ", ok, t)
print("order:", d["order"] and {k: d["order"][k] for k in ("type", "entry_prem", "sl_prem", "target_prem", "lots")})
assert len(d["chart"]) > 20

# strike choice + alternatives + option candles
print("alternatives:", [(a["strike"], a["moneyness"], a["cost_per_lot"], a["lots"], a["affordable"]) for a in d["alternatives"]])
assert d["chain"] and d["atm"]
otm = [a for a in d["alternatives"] if a["moneyness"].endswith("OTM")][-1]
d2 = j(c.get(f"/api/dashboard?symbol=NIFTY&strike={otm['strike']}"))
print("chosen strike order:", {k: d2["order"][k] for k in ("strike", "moneyness", "ltp", "entry_prem", "sl_prem", "target_prem", "lots", "cost_per_lot", "affordable")})
assert d2["order"]["strike"] == otm["strike"] and not d2["order"]["is_default"]
oc = j(c.get(f"/api/option_candles?symbol=NIFTY&strike={d['atm']}&opt=CE"))
print("option candles:", oc["source"], len(oc["candles"]), oc["candles"][-1])
assert len(oc["candles"]) == len(d["chart"])
assert c.get("/api/option_candles?symbol=NIFTY&strike=1&opt=CE").status_code == 400
# chart panes: any instrument, price or option, with a strike list
cu = j(c.get("/api/candles?symbol=BANKNIFTY&kind=UND"))
assert cu["kind"] == "UND" and cu["candles"] and "vwap" in cu["candles"][-1] and cu["atm"] in cu["strikes"]
cp = j(c.get("/api/candles?symbol=RELIANCE&kind=PE"))
assert cp["kind"] == "PE" and cp["strike"] == cp["atm"] and len(cp["strikes"]) > 5 and cp["candles"]
cs = j(c.get(f"/api/candles?symbol=NIFTY&kind=CE&strike={d['atm']}&expiry={d['expiry']}"))
assert cs["strike"] == d["atm"] and len(cs["candles"]) == len(d["chart"])
assert c.get("/api/candles?symbol=NIFTY&kind=XX").status_code == 400
print("pane candles OK:", cu["label"], len(cu["candles"]), cp["label"], cp["strike"])
# cash check: shrink the account so the ATM lot is unaffordable
store.put("account", {"capital_start": 5000, "created": "x"})
d3 = j(c.get("/api/dashboard?symbol=NIFTY"))
print("small account:", d3["order"]["lots"], d3["order"]["affordable"], d3["order"]["note"])
print("cheaper affordable strikes:", [a["strike"] for a in d3["alternatives"] if a["affordable"]])
store.put("account", {"capital_start": 200000, "created": "x"})

r = c.post("/api/trade/signal", json={"symbol": "NIFTY", "strike": otm["strike"]})
print("place signal:", r.status_code, r.get_json())
assert r.status_code == 200
# one trade per instrument per day
d_after = j(c.get("/api/dashboard?symbol=NIFTY"))
assert any("one trade per day" in t for _, t in d_after["signal"]["checks"]), d_after["signal"]["checks"]
assert d_after["signal"]["signal"] in ("WAIT", "NO NEW ENTRIES", "BUY CALL", "BUY PUT")
assert d_after["signal"]["camarilla"]["levels"]["h4"] > d_after["signal"]["camarilla"]["levels"]["l4"]
# camarilla plan + exits: breakeven after +1R, 45-min time stop, no premium target exit
pc = {"opened": "2026-10-01T10:00:00+05:30", "sl_prem": 1, "target_prem": 2,
      "plan": {"strategy": "camarilla", "opt": "CE", "entry": 100.0, "risk_pts": 2.0, "sl": 98.0, "target": None,
               "breakeven_at": 102.0, "time_stop_min": 45}}
why, upd = S.exit_check(pc, 102.5, 50, datetime(2026, 10, 1, 10, 20, tzinfo=C.IST))
assert why is None and upd.get("at_breakeven") and upd.get("live_sl") == 100.0, (why, upd)
why, _ = S.exit_check({**pc, **upd}, 99.9, 50, datetime(2026, 10, 1, 10, 25, tzinfo=C.IST))
assert why.startswith("Breakeven"), why
why, _ = S.exit_check(pc, 100.5, 50, datetime(2026, 10, 1, 10, 50, tzinfo=C.IST))
assert why and why.startswith("Time stop"), why
print("camarilla exits OK")
assert d_after["signal"]["bands"] and len(d_after["signal"]["bands"]["series"]) > 10
# noise-band trailing exit fires only on a half-hour check bar, after entry
pz = {"opened": "2026-10-01T10:52:00+05:30", "sl_prem": 1, "target_prem": 999,
      "plan": {"strategy": "noise", "opt": "CE", "entry": 100.0, "risk_pts": 2.0, "sl": 98.0, "target": 108.0, "rr": 4}}
t1 = datetime(2026, 10, 1, 11, 15, tzinfo=C.IST)
st_in = {"is_check": True, "bar_end": t1, "close": 101.0, "vwap": 100.5, "up": 101.5, "lo": 97.0}
why, upd = S.exit_check(pz, 101.0, 50, t1, st_in)
assert why and why.startswith("Trailing exit"), why
why, _ = S.exit_check(pz, 101.0, 50, t1, {**st_in, "is_check": False})
assert why is None
why, _ = S.exit_check(pz, 101.0, 50, t1, {**st_in, "close": 102.0})
assert why is None
why, _ = S.exit_check(pz, 97.9, 50, t1, None)
assert why.startswith("Index stop-loss")
why, _ = S.exit_check(pz, 108.1, 50, t1, None)
assert why.startswith("Index target")
assert S._is_check(datetime(2026, 10, 1, 10, 40, tzinfo=C.IST)) and not S._is_check(datetime(2026, 10, 1, 10, 45, tzinfo=C.IST))
print("noise exits OK")

ch = j(c.get("/api/chain?symbol=NIFTY"))
print("chain rows", len(ch["rows"]), "atm", ch["atm"], "expiries", ch["expiries"][:2], "src", ch["source"])
r = j(c.post("/api/trade/manual", json={"symbol": "NIFTY", "expiry": ch["expiry"], "strike": ch["atm"], "opt": "PE", "side": "BUY", "lots": 1}))
print("manual:", r)

p = j(c.get("/api/portfolio"))
print("portfolio cash", p["cash"], "positions", len(p["positions"]), "unreal", round(p["unrealised"], 2))

# advance time 50 minutes -> signal trade should hit target / breakeven / time stop
NOW[0] = NOW[0] + timedelta(minutes=50)
CANDLES.clear()
assert c.post("/api/tick").status_code == 403
t = j(c.post("/api/tick", headers={"x-cron-secret": "c"}))
print("tick:", t)
# square-off
NOW[0] = NOW[0].replace(hour=15, minute=16)
CANDLES.clear()
print("tick sq-off:", j(c.post("/api/tick", headers={"x-cron-secret": "c"})))
p = j(c.get("/api/portfolio"))
print("after: positions", len(p["positions"]), "trades", len(p["history"]), "stats", {k: v for k, v in p["stats"].items() if k != "equity"})
pid = None

print("context:", list(j(c.get("/api/context?symbol=RELIANCE")).keys()))
print("stock dash:", j(c.get("/api/dashboard?symbol=RELIANCE"))["signal"]["signal"])

# settings + broker login redirects
j(c.post("/api/settings", json={"broker": "upstox", "id": "KEY", "secret": "SEC"}))
s = j(c.get("/api/settings"))
print("settings:", s["upstox"], s["fyers"]["configured"])
r = c.get("/api/upstox/login")
print("upstox login ->", r.status_code, r.headers["Location"][:90])
r = c.get("/api/upstox/callback?code=x&state=WRONG")
print("bad state ->", r.headers["Location"])
B.upstox_exchange = lambda *a: "TOKEN123"
st = store.get("upstox")["state"]
r = c.get(f"/api/upstox/callback?code=x&state={st}")
print("good callback ->", r.headers["Location"], store.get("upstox").get("token"))

# with a 'live' (fake) Upstox broker
class FakeUp(B.Upstox):
    def __init__(self, tok): self.name = "upstox"
    def candles(self, key): return M.yahoo_candles("^NSEI")
    def quotes(self, keys): return {k: {"ltp": 13.0 if "VIX" in k else 101.5, "prev_close": 12.5} for k in keys}
    def expiries(self, key): return ([C.today_ist() + timedelta(days=5), C.today_ist() + timedelta(days=12)], 65)
    def chain(self, key, exp):
        ch = M.theoretical_chain("NIFTY", exp, 24900, 13)
        ch["ce_oi"] = np.linspace(1e5, 5e5, len(ch)); ch["pe_oi"] = np.linspace(5e5, 1e5, len(ch))
        ch["ce_key"] = ["NSE_FO|C%d" % i for i in range(len(ch))]; ch["pe_key"] = ["NSE_FO|P%d" % i for i in range(len(ch))]
        return ch
B.Upstox = FakeUp
NOW[0] = NOW[0].replace(hour=11, minute=0) + timedelta(days=0)
CANDLES.clear()
d = j(c.get("/api/dashboard?symbol=NIFTY"))
print("live dash:", d["source"], d["live"], "PCR factor", [f for f in d["signal"]["factors"] if "Put" in f["factor"]][0]["value"])
ch = j(c.get("/api/chain?symbol=NIFTY"))
print("live chain stats", ch["stats"])
r = j(c.post("/api/trade/manual", json={"symbol": "NIFTY", "expiry": ch["expiry"], "strike": ch["atm"], "opt": "CE", "side": "BUY", "lots": 1}))
print(r)
p = j(c.get("/api/portfolio"))
print("live portfolio:", [(x["legs"][0]["src"], x["current_prem"]) for x in p["positions"]])

bt = j(c.post("/api/backtest", json={"symbol": "NIFTY", "mult": 1.75, "capital": 200000}))
for st_ in ("noise", "camarilla"):
    assert "summary" in j(c.post("/api/backtest", json={"symbol": "NIFTY", "capital": 200000, "strategy": st_}))
assert c.post("/api/backtest", json={"symbol": "NIFTY", "capital": 200000, "strategy": "magic"}).status_code == 400
assert c.post("/api/backtest", json={"symbol": "NIFTY", "mult": 9, "capital": 200000}).status_code == 400
small = j(c.post("/api/backtest", json={"symbol": "NIFTY", "mult": 1.75, "capital": 15000}))
assert "skipped" in small["summary"], small["summary"]
small1 = j(c.post("/api/backtest", json={"symbol": "NIFTY", "mult": 1.75, "capital": 15000, "sizing": "one_lot", "otm": 2}))
print("small account:", small["summary"].get("trades"), small["summary"]["skipped"], "| one lot 2 OTM:", small1["summary"].get("trades"))
assert c.post("/api/backtest", json={"symbol": "NIFTY", "capital": 15000, "sizing": "all-in"}).status_code == 400
rs_ = j(c.get("/api/research"))
assert rs_["new"]["NIFTY"]["windows"]["120"]["net"] > 0 and len(rs_["families"]) >= 8
print("backtest keys", list(bt.keys()), bt.get("summary", {}).get("trades"))
print("reset", j(c.post("/api/account/reset", json={"capital": 300000})))
# preferences: validation, 1-lot sizing, strategies on/off, auto paper-trading by the scheduler
assert c.post("/api/settings", json={"prefs": {"risk_pct": 9}}).status_code == 400
assert c.post("/api/settings", json={"prefs": {"strategies": []}}).status_code == 400
assert j(c.post("/api/settings", json={"prefs": {"sizing": "one_lot", "risk_pct": 0.5, "strategies": ["noise"], "auto": True, "auto_syms": ["NIFTY", "XYZ"]}}))["ok"]
pf_ = j(c.get("/api/settings"))["prefs"]
assert pf_["sizing"] == "one_lot" and pf_["auto_syms"] == ["NIFTY"] and pf_["strategies"] == ["noise"], pf_
NOW[0] = datetime(2026, 10, 1, 10, 52, tzinfo=C.IST)
dd_ = j(c.get("/api/dashboard?symbol=NIFTY"))
assert "camarilla" not in " ".join(t for _, t in dd_["signal"]["checks"]).lower()
if dd_["order"]:
    assert dd_["order"]["lots"] == 1 and "1-lot mode" in (dd_["order"]["note"] or ""), dd_["order"]
tk = j(c.post("/api/tick", headers={"x-cron-secret": "c"}))
print("auto tick:", tk.get("placed"))
pos_ = store.positions()
assert any((p_.get("plan") or {}).get("auto") for p_ in pos_) == bool(tk.get("placed") and "message" in tk["placed"][0]), (tk, pos_)
assert j(c.post("/api/settings", json={"prefs": {"auto": False, "sizing": "risk", "risk_pct": 1, "strategies": ["noise", "camarilla"]}}))["ok"]
assert c.post("/api/backtest", json={"symbol": "NIFTY", "capital": 200000, "days": 45}).status_code == 400
b30 = j(c.post("/api/backtest", json={"symbol": "NIFTY", "capital": 200000, "days": 30}))
assert b30["summary"].get("source") and "period" in b30["summary"], b30["summary"]
print("prefs / auto / periods OK")

# ---------- Redis store edge cases ----------
store.insert_position({"id": "race1", "symbol": "NIFTY", "opened": "2026-10-02T10:00:00+05:30"})
first = store.take_position("race1")
second = store.take_position("race1")
assert first and first["id"] == "race1" and second is None, "double close must be impossible"
store.update_position({"id": "race1", "symbol": "NIFTY", "opened": "x"})
assert all(p["id"] != "race1" for p in store.positions()), "update must not resurrect a closed position"
assert store.ping()
# ---------- AI strategy (round 8) ----------
from fno import ai as AI, ai_model as AM  # noqa: E402
assert AI.available() and AI.train_until().isoformat() == "2026-04-09"
j(c.post("/api/account/reset", json={"capital": 200000}))
store.clear_positions(); store.clear_trades()
assert j(c.post("/api/settings", json={"prefs": {"sizing": "risk", "risk_pct": 2, "strategies": ["ai"], "auto": True, "auto_syms": ["NIFTY"]}}))["ok"]
NOW[0] = datetime(2026, 10, 1, 10, 36, tzinfo=C.IST)        # 1 minute after the 10:35 close (an AI decision bar)
CANDLES.clear(); M._cache.clear()
dd_ = j(c.get("/api/dashboard?symbol=NIFTY"))
ai_ = dd_["signal"]["ai"]
assert ai_ and len(ai_["candidates"]) == 6 and ai_["bar_end"] == "10:35" and len(ai_["reasons"]) >= 3, dd_["signal"]["checks"]
assert all(abs(x["exp_r"]) < 3 and 0 <= x["p_win"] <= 1 for x in ai_["candidates"])
assert "Camarilla" not in " ".join(t for _, t in dd_["signal"]["checks"])
print("AI scores:", [(x["side"], x["stop_k"], round(x["exp_r"], 2), round(x["p_win"], 2)) for x in ai_["candidates"]], "best", ai_["best"])
# force a signal by lowering the threshold, place it, then stop it out on the index level
AM.load()["meta"]["threshold"] = -9.0
dd_ = j(c.get("/api/dashboard?symbol=NIFTY"))
pl = dd_["signal"]["plan"]
assert pl and pl["strategy"] == "ai" and pl["rr"] == 2 and dd_["signal"]["signal"] in ("BUY CALL", "BUY PUT") and dd_["order"], dd_["signal"]["checks"]
assert abs(abs(pl["target"] - pl["entry"]) / abs(pl["entry"] - pl["sl"]) - 2) < 1e-6
tk = j(c.post("/api/tick", headers={"x-cron-secret": "c"}))
assert tk.get("placed") and tk["placed"][0].get("message"), tk
pos_ = store.positions()
assert len(pos_) == 1 and pos_[0]["plan"]["strategy"] == "ai" and pos_[0]["plan"]["auto"]
dd_ = j(c.get("/api/dashboard?symbol=NIFTY"))
assert "already took" not in " ".join(t for _, t in dd_["signal"]["checks"])      # 1 of 2 allowed today
pa = pos_[0]
sign_ = 1 if pa["plan"]["opt"] == "CE" else -1
why, upd = S.exit_check(pa, pa["plan"]["entry"] + sign_ * 0.5 * pa["plan"]["risk_pts"], 10, datetime(2026, 10, 1, 10, 50, tzinfo=C.IST))
assert why is None and not upd, (why, upd)                                            # no premium exits, no breakeven, no time stop
why, _ = S.exit_check(pa, pa["plan"]["sl"] - sign_ * 0.1, 10, datetime(2026, 10, 1, 10, 50, tzinfo=C.IST))
assert why and "stop-loss" in why.lower() and "ATR" in why, why
why, _ = S.exit_check(pa, pa["plan"]["target"] + sign_ * 0.1, 10, datetime(2026, 10, 1, 10, 50, tzinfo=C.IST))
assert why and "2R" in why, why
hist_ = [{"reason": why}]
AM.load()["meta"]["threshold"] = 0.3
bt_ai = j(c.post("/api/backtest", json={"symbol": "NIFTY", "capital": 200000, "strategy": "ai", "days": 30}))
assert "summary" in bt_ai and bt_ai["summary"].get("train_until") == "2026-04-09" and bt_ai["summary"]["note"], bt_ai
print("AI OK: exit", hist_[-1]["reason"], "| backtest days", bt_ai["summary"]["days"], "trades", bt_ai["summary"]["trades"])
assert j(c.post("/api/settings", json={"prefs": {"strategies": ["noise", "camarilla"], "auto": False}}))["ok"]
store.clear_positions(); store.clear_trades()
# ---------- session journal ----------
from fno import journal as JR  # noqa: E402
store.clear_positions(); store.clear_trades()
assert j(c.post("/api/settings", json={"prefs": {"sizing": "risk", "risk_pct": 2, "strategies": ["noise", "camarilla", "ai"], "auto": False}}))["ok"]
store._cmd("DEL", JR._key("2026-10-01"))
NOW[0] = datetime(2026, 10, 1, 9, 17, tzinfo=C.IST); CANDLES.clear(); M._cache.clear()
tk = j(c.post("/api/tick", headers={"x-cron-secret": "c"}))
assert tk["journal"].get("ctx:NIFTY") == "written" and tk["journal"].get("ctx:BANKNIFTY") == "written", tk["journal"]
jd = j(c.get("/api/journal/day?date=2026-10-01"))
cx = jd["context"]["NIFTY"]
assert cx and cx["prev_close"] and cx["camarilla"]["h4"] > cx["camarilla"]["l4"] and cx["cpr"]["tc"] >= cx["cpr"]["bc"] and cx["expiry"], cx
assert cx["noise_band_at"] and "09:45" in cx["noise_band_at"], cx["noise_band_at"]
assert "ctx:NIFTY" not in j(c.post("/api/tick", headers={"x-cron-secret": "c"}))["journal"]            # context not rewritten
NOW[0] = datetime(2026, 10, 1, 10, 51, tzinfo=C.IST); CANDLES.clear(); M._cache.clear()         # a :01 minute -> scan
AM.load()["meta"]["threshold"] = -9.0
tk = j(c.post("/api/tick", headers={"x-cron-secret": "c"}))
assert isinstance(tk["journal"].get("NIFTY"), int) and tk["journal"]["NIFTY"] >= 1, tk["journal"]
jd = j(c.get("/api/journal/day?date=2026-10-01"))
assert jd["recommendations"] and jd["recommendations"][0]["basis"]["checks"] and jd["recommendations"][0]["option"]["strike"] and jd["ai"]["NIFTY"], jd["summary"]
n_before = len(jd["recommendations"])
tk = j(c.post("/api/tick", headers={"x-cron-secret": "c"}))                                       # same candle -> no duplicate
assert len(j(c.get("/api/journal/day?date=2026-10-01"))["recommendations"]) == n_before
assert j(c.post("/api/journal/scan", json={}))
NOW[0] = datetime(2026, 10, 1, 15, 17, tzinfo=C.IST); CANDLES.clear(); M._cache.clear()
tk = j(c.post("/api/tick", headers={"x-cron-secret": "c"}))
assert tk["journal"].get("settled", 0) >= 1, tk["journal"]
jd = j(c.get("/api/journal/day?date=2026-10-01"))
r0 = jd["recommendations"][0]["result"]
assert r0 and r0["index_outcome"] in ("target", "stop", "square-off") and "R" in r0 and jd["summary"]["settled"] >= 1, r0
assert "2026-10-01" in j(c.get("/api/journal"))["days"]
assert c.get("/api/journal/day?date=nope").status_code == 400
print("journal OK:", jd["summary"])
AM.load()["meta"]["threshold"] = 0.3
assert j(c.post("/api/settings", json={"prefs": {"strategies": ["noise", "camarilla"], "auto": False}}))["ok"]
# ---------- calendar rules (round 6) ----------
from fno import service as SV, indicators as II  # noqa: E402
from datetime import date as _d
assert SV.calendar_guard("BANKNIFTY", [_d(2026, 10, 27)], _d(2026, 10, 27)) is None      # rule tested and dropped
C.SKIP_OWN_EXPIRY = ("BANKNIFTY",)
assert SV.calendar_guard("BANKNIFTY", [_d(2026, 10, 27)], _d(2026, 10, 27))["blocked"]          # mechanism still works
C.SKIP_OWN_EXPIRY = ()
assert SV.calendar_guard("NIFTY", [_d(2026, 10, 27)], _d(2026, 10, 27)) is None
assert SV.calendar_guard("NIFTY", [], _d(2027, 2, 1))["blocked"]
assert II.pick_trading_expiry([_d(2026, 10, 6), _d(2026, 10, 13)], "weekly", _d(2026, 10, 5)) == _d(2026, 10, 13)
assert II.pick_trading_expiry([_d(2026, 10, 6), _d(2026, 10, 13)], "weekly", _d(2026, 10, 3)) == _d(2026, 10, 6)
assert II.demo_expiries("weekly", _d(2025, 6, 2), 1) == [_d(2025, 6, 5)]          # Thursday before Sep 2025
print("calendar rules OK")
# ---------- market recorder ----------
NOW[0] = datetime(2026, 10, 1, 10, 55, tzinfo=C.IST)
assert R.snapshot(NOW[0] .replace(minute=56))["skipped"] == "not a snapshot minute"
st = R.snapshot(NOW[0])                                   # no broker -> NSE path
assert st["saved"] == 4 and st["src"] == ["nse"], st
dd = j(c.get("/api/recorder/day?date=2026-10-01"))
snaps = dd["snapshots"]
assert len(snaps) >= 4 and len(snaps[-1]["rows"]) == 2 * C.RECORD_STRIKES + 1 and len(snaps[-1]["rows"][0]) == len(R.FIELDS)
assert c.get("/api/recorder/day?date=bad").status_code == 400
rs = j(c.get("/api/recorder/status"))
assert rs["days"][-1]["date"] == "2026-10-01" and rs["last_ok"].startswith("2026-10-01T10:55"), rs
R.nse_chain = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("NSE 403"))
st2 = R.snapshot(NOW[0].replace(minute=0, hour=11))
assert st2["saved"] == 0 and st2["errors"] and st2["last_ok"].startswith("2026-10-01T10:55"), st2
assert R.snapshot(datetime(2026, 10, 3, 11, 0, tzinfo=C.IST))["skipped"] == "market closed"
R.nse_chain = _fake_nse
print("recorder OK:", len(snaps), "snapshots,", rs["days"])
print("redis edge cases OK; positions", len(store.positions()), "trades", len(store.trades()))
print("ALL OK")
