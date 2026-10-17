"""Background loop that keeps the crypto and US paper books running on the always-on host (Render).

Before this, those books were only checked by a GitHub Actions job that still pointed at the old Vercel address
(and GitHub runs "every 5 minutes" jobs only every few hours), or while the page was open. So nothing traded.

While the server is up this thread:
  - ticks the crypto + US books once a minute
  - runs the weekly autopilot qualification test when it is due
  - pings the app's own public address every 10 minutes so the free Render instance does not fall asleep
A QStash schedule (fno/scheduler.py) also calls /api/tick/global every 10 minutes outside Indian market hours,
which wakes the server if it ever does sleep (restart, deploy), and this thread starts again with it.
"""
from __future__ import annotations

import os
import threading
import time
import traceback

import requests

from . import config as C

TICK_EVERY = 60
PING_EVERY = 600
_started = {"v": False}
_lock = threading.Lock()
state = {"started": None, "ticks": 0, "last_tick": None, "last_error": None, "last_ping": None, "last_qualify": None}


def enabled() -> bool:
    if os.environ.get("FNO_RUNNER", "").lower() in ("0", "off", "false"):
        return False
    if os.environ.get("FNO_RUNNER") or os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID") or os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
        return True
    return C.PRODUCTION_HOST.endswith(".onrender.com")   # never on Vercel / in tests unless asked


def start() -> bool:
    with _lock:
        if _started["v"] or not enabled():
            return False
        _started["v"] = True
    threading.Thread(target=_loop, name="gbook-runner", daemon=True).start()
    return True


def _ping():
    host = C.PRODUCTION_HOST
    if not host:
        return
    try:
        requests.get(f"https://{host}/api/health", timeout=20)
        state["last_ping"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    except Exception as e:
        state["last_error"] = f"ping: {str(e)[:100]}"


def _loop():
    from . import gautopilot as AP
    from . import gbook as GB
    from . import scheduler
    state["started"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    time.sleep(5)
    try:
        scheduler.ensure(C.PRODUCTION_HOST or None)   # (re)registers the QStash schedules, including the new wake-up one
    except Exception:
        traceback.print_exc()
    last_ping = 0.0
    while True:
        t0 = time.time()
        for m in ("CRYPTO", "US"):
            try:
                q = AP.qualify(m)                        # no-op unless the weekly test is due
                state["last_qualify"] = q.get("at")
            except Exception as e:
                state["last_error"] = f"qualify {m}: {str(e)[:100]}"
        try:
            GB.tick()
            state["ticks"] += 1
            state["last_tick"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        except Exception as e:
            state["last_error"] = f"tick: {str(e)[:100]}"
            traceback.print_exc()
        if time.time() - last_ping > PING_EVERY:
            last_ping = time.time()
            _ping()
        time.sleep(max(5.0, TICK_EVERY - (time.time() - t0)))
