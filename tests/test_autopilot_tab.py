"""31 Oct 2026 features: journal autopilot decisions, risk status / weekly reset / P&L reset, capital shield, live chart,
weekly improvement review, Autopilot overview. Offline: in-memory DB, synthetic prices."""
import json, os, sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start()
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)
os.environ.pop("DATABASE_URL", None)
import numpy as np, pandas as pd  # noqa: E402
from fno import config as C, store, market as M, context as X  # noqa: E402

rng = np.random.default_rng(7)
NOW = [datetime(2026, 10, 29, 11, 2, tzinfo=C.IST)]   # a Thursday
C.now_ist = lambda: NOW[0]
C.today_ist = lambda: NOW[0].date()


def fake(start, end_dt):
    rows, px, d = [], start, end_dt.date() - timedelta(days=30)
    while d <= end_dt.date():
        if d.weekday() < 5:
            t = datetime(d.year, d.month, d.day, 9, 15, tzinfo=C.IST)
            for _ in range(75):
                if t > end_dt:
                    break
                o = px; px *= 1 + rng.normal(0, 0.0009)
                rows.append((t, o, max(o, px) * 1.0003, min(o, px) * 0.9997, px, 0)); t += timedelta(minutes=5)
        d += timedelta(days=1)
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")


CANDLES = {}
M.yahoo_candles = lambda t, period="5d", interval="5m": CANDLES.setdefault(t, fake(1300 if ".NS" in t else 24800, NOW[0]))
M.yahoo_daily = lambda t, period="1mo": pd.DataFrame({"close": [14.0, 13.2]}, index=pd.to_datetime(["2026-10-27", "2026-10-28"]))
M.master = lambda: {"lots": {"NIFTY": 65}, "eq_keys": {}}
M.upstox_public_history = lambda key, cal_days: pd.DataFrame()
X.global_cues = lambda: {"markets": {}, "avg_equity_chg": 0.2, "notes": []}
X.fetch_news = lambda s=None: {"items": [], "score": 0, "events": [], "notes": []}
X.fii_dii = lambda: None

from app import app  # noqa: E402
from fno import journal as J, service as SV, paper as P, lag as LG  # noqa: E402

c = app.test_client()


def j(r):
    assert r.status_code < 500, r.get_data(as_text=True)[:800]
    return r.get_json()


assert j(c.post("/api/login", json={"password": "pw"}))["ok"]
store.put("account", {"capital_start": 200000, "created": "2026-10-01"})
store.put("prefs", {**SV.PREF_DEFAULTS, "autopilot": True})
day = NOW[0].date().isoformat()

# ---- 1. journal: every automatic attempt, folded into episodes, linked to recommendations
t0 = NOW[0]
plan = {"strategy": "noise", "signal_bar": "10:55", "opt": "CE", "entry": 24850.0, "sl": 24800.0, "target": 25000.0, "risk_pts": 50}
for k in range(3):                                    # the same skip three minutes in a row -> one episode, 3 attempts
    SV._LAST_PLAN[("NIFTY", "main")] = plan
    SV._note_auto_safe("NIFTY", "main", False, "Not enough cash: 1 lot needs ₹9,000, you have ₹2,000.", t0 + timedelta(minutes=k))
eps = J._get(day, "auto:NIFTY:main")
assert len(eps) == 1 and eps[0]["n"] == 3 and eps[0]["cat"] == "money" and eps[0]["label"] == "Not enough money", eps
assert J._get(day, "reco:NIFTY:noise:10:55")["source"] == "autopilot"        # a skipped plan becomes a recommendation
SV._LAST_PLAN.pop(("NIFTY", "main"), None)
SV._note_auto_safe("NIFTY", "main", False, "No valid signal right now (WAIT).", t0)     # no setup: not logged
SV._note_auto_safe("BANKNIFTY", "main", False, "Weekly loss limit hit (₹-9,000 this week, limit 4%). No new trades until Monday.", t0)
assert len(J._get(day, "auto:NIFTY:main")) == 1
SV._note_auto_safe("NIFTY", "scalp", False, "setup B confidence 55 < 65", t0)
# an executed trade that matches a second recommendation
J._put(day, "reco:SENSEX:trend:10:40", {"id": "SENSEX:trend:10:40", "symbol": "SENSEX", "time": "10:41:00", "signal_bar": "10:40", "strategy": "trend",
                                         "opt": "PE", "index": {"entry": 80000, "sl": 80200, "target": None, "risk_pts": 200}, "option": {}, "basis": {"checks": []},
                                         "auto_enabled": True, "result": None})
store.insert_position({"id": "pos1", "symbol": "SENSEX", "legs": [{"side": "BUY", "strike": 80000, "opt": "PE", "key": "k", "price": 300}], "lots": 1, "lot": 20,
                       "qty": 20, "expiry": "2026-10-30", "entry_prem": 300, "margin": 6000, "opened": f"{day}T10:41:30+05:30", "spot_at_entry": 80000,
                       "source": "demo", "mode": "signal", "plan": {"strategy": "trend", "signal_bar": "10:40", "opt": "PE", "entry": 80000, "sl": 80200, "auto": True},
                       "sl_prem": 250, "target_prem": 450})
jd = j(c.get(f"/api/journal/day?date={day}"))
rows = jd["decisions"]["rows"]
st = {(r["symbol"], r["strategy"]): r["status"] for r in rows}
assert st[("NIFTY", "noise")]["state"] == "skipped" and st[("NIFTY", "noise")]["cat"] == "money", st
assert st[("SENSEX", "trend")]["state"] == "executed" and st[("SENSEX", "trend")]["trade"]["by"] == "autopilot", st
assert any(b["cat"] == "weekly_limit" for b in jd["decisions"]["blocks"])
assert any(e["cat"] == "scalp_filter" for e in jd["decisions"]["scalp"])
assert jd["summary"]["executed"] == 1 and jd["summary"]["skipped"] >= 1
print("1 journal decisions OK:", jd["decisions"]["summary"])

# ---- 2. risk status, weekly limit banner, weekly counter reset, P&L reset with history kept
store.delete_position("pos1") if hasattr(store, "delete_position") else store.htake(store.POSITIONS, "pos1")
store.insert_trade({"id": "L1", "symbol": "NIFTY", "pnl": -40000.0, "opened": f"{day}T09:40:00+05:30", "closed": f"{day}T10:10:00+05:30", "mode": "signal",
                    "plan": {"strategy": "noise", "auto": True}, "legs": [], "lots": 1, "reason": "Index stop-loss"}) if hasattr(store, "insert_trade") else \
    store.hset(store.TRADES, "L1", json.dumps({"id": "L1", "symbol": "NIFTY", "pnl": -40000.0, "opened": f"{day}T09:40:00+05:30", "closed": f"{day}T10:10:00+05:30",
                                                "mode": "signal", "plan": {"strategy": "noise", "auto": True}, "legs": [], "lots": 1, "reason": "Index stop-loss"}))
store.forget()
s = j(c.get("/api/autopilot/status"))
assert s["weekly"]["blocked"] and s["blocked"] and any(b["kind"] == "weekly" for b in s["banners"]), s
NOW[0] = NOW[0] + timedelta(minutes=30)
r = j(c.post("/api/account/week-reset", json={}))
assert r["ok"] and not r["status"]["weekly"]["blocked"], r["status"]["weekly"]
print("2a weekly limit banner + reset OK")
r = j(c.post("/api/account/reset", json={"capital": 150000}))
assert r["ok"] and r["archived"] == 1, r
assert store.trades() == [] and P.account()["capital_start"] == 150000 and len(P.archived()) == 1
jd = j(c.get(f"/api/journal/day?date={day}"))
assert any(t["id"] == "L1" for t in jd["paper_trades"]), "archived trade missing from the journal"
print("2b P&L reset keeps history OK")

# ---- 3. capital shield
sh = SV.shield_state()
assert sh["on"] and sh["floor"] == 90000 and sh["open_cap"] == 75000
lots, why = SV._shield_lots({"lots": 10, "cost_per_lot": 10000, "risk_per_lot": 2000}, sh)
assert lots == 7 and "cut to 7" in why, (lots, why)
lots, why = SV._shield_lots({"lots": 10, "cost_per_lot": 1000, "risk_per_lot": 4000}, sh)
assert lots == 5, (lots, why)                       # 15% of 150k = 22.5k / 4k = 5 lots
low = {**sh, "below_floor": True}
lots, why = SV._shield_lots({"lots": 1, "cost_per_lot": 1000, "risk_per_lot": 100}, low)
assert lots == 0 and "below the floor" in why
assert J.auto_cat(why)[0] == "shield"
j(c.post("/api/settings", json={"prefs": {"shield_open_pct": 30}}))
assert SV.shield_state()["open_cap"] == 45000
assert c.post("/api/settings", json={"prefs": {"shield_floor_pct": 99}}).status_code == 400
print("3 capital shield OK")

# ---- 4. live chart
lc = j(c.get("/api/chart/live?symbol=NIFTY"))
assert lc["candles"] and lc["levels"]["pivot"] and lc["levels"]["cam_h4"] > lc["levels"]["cam_l4"] and lc["date"] == day
assert lc["bands"] and isinstance(lc["zones"], list) and lc["days"]
assert any(x["strategy"] == "noise" for x in lc["setups"]), lc["setups"]
prev = lc["days"][-3]
lc2 = j(c.get(f"/api/chart/live?symbol=NIFTY&date={prev}"))
assert lc2["date"] == prev and lc2["candles"] and not lc2["live_day"]
assert c.get("/api/chart/live?symbol=XX").status_code == 400
print("4 live chart OK:", len(lc["candles"]), "candles,", len(lc["zones"]), "zones")

# ---- 5. weekly review + overview
w = j(c.get("/api/autopilot/weekly"))
assert w["week"] == "2026-W44" and w["sessions"] and w["fixes"], w
assert any(f["cat"] == "money" for f in w["fixes"])
ov = j(c.get("/api/autopilot/overview"))
assert ov["status"] and ov["settings"]["autopilot"] and "outcomes" in ov and "learning" in ov
assert ov["settings"]["shield"]["shield_open_pct"] == 30
print("5 weekly review + overview OK:", w["headline"])

# ---- 6. settle a skipped autopilot plan (what would have happened)
c5 = M.Market().candles("NIFTY")
n = J.settle(day, {"NIFTY": c5[c5.index.date == NOW[0].date()]})
rec = J._get(day, "reco:NIFTY:noise:10:55")
assert rec["result"] and rec["result"]["index_outcome"] in ("stop", "target", "square-off")
jd = j(c.get(f"/api/journal/day?date={day}"))
row = next(r for r in jd["decisions"]["rows"] if r["reco_id"] == "NIFTY:noise:10:55")
assert row["what_happened"].startswith("If taken"), row["what_happened"]
print("6 skipped-plan outcome OK:", row["what_happened"])

assert "Autopilot" in c.get("/").get_data(as_text=True)
print("test_autopilot_tab: all OK")
