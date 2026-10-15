"""Every-minute trade checker via Upstash QStash (free tier).

QStash calls POST /api/tick on the cron below and forwards our secret header.
The schedule is registered by the app itself (idempotent, fixed schedule id),
so there is nothing to configure by hand.
"""
from __future__ import annotations

import hashlib
from urllib.parse import quote

import requests

from . import config as C
from . import store

SCHEDULE_ID = "fno-tick"
PRE_ID = "fno-pre-open"
OPEN_ID = "fno-open-record"
POST_ID = "fno-post-close"


def _dest(host: str | None) -> str | None:
    host = C.PRODUCTION_HOST or host
    return f"https://{host}/api/tick" if host else None


def _register(dest, cron, sid):
    r = requests.post(
        f"{C.QSTASH_URL}/v2/schedules/{quote(dest, safe=':/')}",
        headers={
            "Authorization": f"Bearer {C.QSTASH_TOKEN}",
            "Upstash-Cron": cron,
            "Upstash-Schedule-Id": sid,
            "Upstash-Retries": "0",
            "Upstash-Timeout": "55s",
            "Upstash-Forward-X-Cron-Secret": C.CRON_SECRET,
            "Content-Type": "application/json",
        },
        data="{}", timeout=15,
    )
    if r.status_code >= 400:
        return {"ok": False, "reason": f"QStash {r.status_code}: {r.text[:160]}"}
    return {"ok": True, "schedule_id": (r.json() or {}).get("scheduleId", sid)}


def ensure(host: str | None = None, force: bool = False) -> dict:
    """Create/update the QStash schedules (market-hours tick + the 09:00 pre-open call) if not registered with the current settings."""
    if not C.QSTASH_TOKEN:
        return {"ok": False, "reason": "QStash not connected"}
    dest = _dest(host)
    if not dest:
        return {"ok": False, "reason": "Unknown production host"}
    want = {"dest": dest, "cron": C.TICK_CRON, "pre_cron": C.PRE_CRON, "open_cron": C.OPEN_CRON, "post_cron": C.POST_CRON,
            "secret": hashlib.sha256(C.CRON_SECRET.encode()).hexdigest()[:12]}   # a changed CRON_SECRET re-registers the schedules by itself
    cur = store.get("scheduler") or {}
    if not force and all(cur.get(k) == v for k, v in want.items()):
        return {"ok": True, "schedule_id": cur.get("schedule_id"), "cached": True}
    a = _register(dest, C.TICK_CRON, SCHEDULE_ID)
    if not a["ok"]:
        return a
    b = _register(dest, C.PRE_CRON, PRE_ID)
    if not b["ok"]:
        store.put("scheduler", {"dest": dest, "cron": C.TICK_CRON, "schedule_id": a["schedule_id"]})   # tick is up; retry the pre-open one next time
        return b
    c = _register(dest, C.OPEN_CRON, OPEN_ID)
    if not c["ok"]:
        store.put("scheduler", {"dest": dest, "cron": C.TICK_CRON, "pre_cron": C.PRE_CRON, "schedule_id": a["schedule_id"], "pre_id": b["schedule_id"]})   # retry the 09:15 one next time
        return c
    d = _register(dest, C.POST_CRON, POST_ID)
    if not d["ok"]:
        store.put("scheduler", {"dest": dest, "cron": C.TICK_CRON, "pre_cron": C.PRE_CRON, "open_cron": C.OPEN_CRON, "schedule_id": a["schedule_id"], "pre_id": b["schedule_id"], "open_id": c["schedule_id"]})
        return d
    store.put("scheduler", {**want, "schedule_id": a["schedule_id"], "pre_id": b["schedule_id"], "open_id": c["schedule_id"], "post_id": d["schedule_id"]})
    return {"ok": True, "schedule_id": a["schedule_id"], "cached": False}
