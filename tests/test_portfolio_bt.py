"""Offline test: portfolio backtest job (3 indices, compounding, exit comparison) on synthetic 5-minute data. Run: python tests/test_portfolio_bt.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(port=6441)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)
import numpy as np, pandas as pd
from fno import portfolio_bt as PB, market as D, config as C

D.lot_size = lambda s: {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}[s]


def synth(start_px, seed):
    r = np.random.default_rng(seed)
    days = pd.bdate_range("2026-03-02", "2026-08-28")
    idx = pd.DatetimeIndex([pd.Timestamp(d) + pd.Timedelta(minutes=9 * 60 + 15 + 5 * k) for d in days for k in range(75)]).tz_localize("Asia/Kolkata")
    trend = np.repeat(r.choice([0.0006, -0.0006, 0.0], size=len(days)), 75)
    px = start_px * np.cumprod(1 + trend + r.normal(0, 0.0012, len(idx)))
    o = np.concatenate([[start_px], px[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, px) * 1.0008, "low": np.minimum(o, px) * 0.9992, "close": px, "volume": 1000.0}, index=idx)


DATA = {"NIFTY": synth(22000, 1), "BANKNIFTY": synth(50000, 2), "SENSEX": synth(72000, 3)}
VIX = pd.Series(14.0, index=DATA["NIFTY"].index)
PB._history = lambda sym, start: (DATA[sym], VIX)

assert PB.market_busy(pd.Timestamp("2026-10-21 11:00", tz="Asia/Kolkata")) and not PB.market_busy(pd.Timestamp("2026-10-24 11:00", tz="Asia/Kolkata"))
for bad in ({"indices": ["FOO"]}, {"capital": 5}, {"start": "2026-08-01", "end": "2026-08-05"}, {"otm": 5}):
    try:
        PB._validate(bad); raise SystemExit(f"accepted {bad}")
    except ValueError:
        pass
j = PB.start({"start": "2026-04-01", "end": "2026-08-28", "capital": 200000, "sizing": "compound", "confidence": "strong", "exit": "t123_253050",
              "compare": True, "big_run_skip": True, "max_lots": 50, "_test": True})
t0 = time.time()
while PB.job(j)["status"] in ("queued", "running") and time.time() - t0 < 600:
    time.sleep(2)
r = PB.job(j)
assert r["status"] == "done", r
m, c = r["result"]["main"], r["result"]["compare"]
assert c and c["label"].startswith("Current") and m["label"].startswith("Book 20%")
for x in (m, c):
    if x["trades"]:
        assert x["years"] and x["months_table"] and x["curve"] and x["by_index"] and "max_dd_pct" in x, x.keys()
        assert x["final"] == x["curve"][-1]["equity"] and x["max_lots"] <= 50
print("portfolio job OK:", m["trades"], "trades, end", m["final"], "| current exits", c["trades"], "trades, end", c["final"])
j2 = PB.start({"start": "2026-04-01", "end": "2026-08-28", "indices": ["NIFTY"], "sizing": "fixed", "lots": 2, "exit": "current", "_test": True})
while PB.job(j2)["status"] in ("queued", "running"):
    time.sleep(1)
r2 = PB.job(j2)
assert r2["status"] == "done" and r2["result"]["compare"] is None
assert not r2["result"]["main"]["trades"] or set(t["lots"] for t in r2["result"]["main"]["trades_list"]) <= {1, 2}
print("fixed lots OK")

# saved strategies + Pine Script
it = PB.save_setup("Strong + skip", {"confidence": "strong", "exit": "t123_ema9", "max_lots": 50}, {"final": 1, "multiple": 2})
assert PB.setups()[0]["name"] == "Strong + skip" and PB.setups()[0]["params"]["exit"] == "t123_ema9"
PB.save_setup("strong + SKIP", {"exit": "current"})                      # same name replaces
assert len(PB.setups()) == 1 and PB.setups()[0]["params"]["exit"] == "current"
for idx in ("NIFTY", "BANKNIFTY", "SENSEX"):
    for ex in PB.EXITS:
        o = PB.pine_for({"exit": ex, "confidence": "strong", "sizing": "fixed", "lots": 4, "otm": 1}, idx)
        c = o["code"]
        assert "{{" not in c and "//@version=5" in c and o["tv_symbol"] in c and "fixedLots = input.int(4" in c
        assert c.count("strategy.entry(") == 1 and "process_orders_on_close=true" in c
        # every block opener is followed by an indented line (cheap structure check)
        lines = c.splitlines()
        for n, ln in enumerate(lines[:-1]):
            st = ln.strip()
            if st.startswith("//") or not st:
                continue
            if (st.startswith(("if ", "for ", "else")) or st.endswith("=>")) and not st.startswith("else if") or st.startswith("else if"):
                nxt = next(x for x in lines[n + 1:] if x.strip() and not x.strip().startswith("//"))
                assert len(nxt) - len(nxt.lstrip()) > len(ln) - len(ln.lstrip()), (idx, ex, n, ln)
c = PB.pine_for({"exit": "current"}, "BANKNIFTY")["code"]
assert "useFib    = input.bool(false" in c and "useTrend  = input.bool(false" in c and "lotSize   = input.int(30" in c
c = PB.pine_for({"exit": "t123_253050"}, "NIFTY")["code"]
assert "t1P       = input.float(20.0" in c and "t3P       = input.float(50.0" in c
assert PB.delete_setup(PB.setups()[0]["id"]) and PB.setups() == []
print("setups + pine OK")
print("ALL PORTFOLIO BT OK")
