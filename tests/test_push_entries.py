"""Offline test: every Indian F&O entry / valid signal gets one phone alert. Run: python tests/test_push_entries.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(port=6429)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)
os.environ.pop("RENDER", None)

from datetime import datetime  # noqa: E402
from fno import push as PUSH, service as SV, store  # noqa: E402

SENT = []
PUSH.configured = lambda: True
PUSH.subscriptions = lambda: {"d1": {"endpoint": "https://x", "keys": {}}}
PUSH.deliver = lambda sub, payload: (SENT.append(__import__("json").loads(payload)) or (201, ""))

now = datetime(2026, 11, 2, 10, 1)
plan = {"strategy": "noise", "signal_bar": "10:00", "opt": "CE", "entry": 24100.0, "sl": 24050.0, "target": 24200.0, "signal": "BUY CALL"}

# 1. valid signal not placed -> one alert, repeated minutes with the same reason stay quiet
SV._LAST_PLAN[("NIFTY", "main")] = dict(plan)
SV._push_entry("NIFTY", "main", False, "Not enough cash for 1 lot.", now)
SV._push_entry("NIFTY", "main", False, "Not enough cash for 1 lot.", now.replace(minute=2))
assert len(SENT) == 1 and "not placed" in SENT[0]["title"] and "Not enough cash" in SENT[0]["body"], SENT
# 2. the same signal placed a minute later -> still alerted (different id), and the 5-min recommendation is then suppressed
SV._push_entry("NIFTY", "main", True, "Bought 1 lot 24100 CE @ 120", now.replace(minute=3))
assert len(SENT) == 2 and "placed" in SENT[1]["title"]
g = {"now": now.replace(minute=6)}
SV._push_signal("NIFTY", {"plan": plan, "signal": "BUY CALL", "score": 4}, None, g)
assert len(SENT) == 2
# 3. a new signal with no Autopilot attempt (manual mode) -> recommendation alert, also for trend / Fibonacci slots
p2 = {**plan, "strategy": "fib", "signal_bar": "11:00", "opt": "PE", "target": None}
SV._push_signal("BANKNIFTY", {"plan": None, "fib": {"plan": p2, "signal": "BUY PUT"}}, None, {"now": now}, plan=p2, slot="fib")
assert len(SENT) == 3 and SENT[2]["title"] == "BANKNIFTY BUY PUT" and "Fibonacci" in SENT[2]["body"]
# 4. no plan evaluated (blocked before the rules ran) -> nothing
SV._LAST_PLAN.pop(("SENSEX", "main"), None)
assert SV._push_entry("SENSEX", "main", False, "Weekly loss limit reached", now) is None and len(SENT) == 3
print("entry push tests passed")
