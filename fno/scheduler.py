"""Every-minute trade checker via Upstash QStash (free tier).

QStash calls POST /api/tick on the cron below and forwards our secret header.
The schedule is registered by the app itself (idempotent, fixed schedule id),
so there is nothing to configure by hand.
"""
from __future__ import annotations

from urllib.parse import quote

import requests

from . import config as C
from . import store

SCHEDULE_ID = "fno-tick"


def _dest(host: str | None) -> str | None:
    host = C.PRODUCTION_HOST or host
    return f"https://{host}/api/tick" if host else None


def ensure(host: str | None = None, force: bool = False) -> dict:
    """Create/update the QStash schedule if it isn't registered with the current settings."""
    if not C.QSTASH_TOKEN:
        return {"ok": False, "reason": "QStash not connected"}
    dest = _dest(host)
    if not dest:
        return {"ok": False, "reason": "Unknown production host"}
    want = {"dest": dest, "cron": C.TICK_CRON}
    cur = store.get("scheduler") or {}
    if not force and cur.get("dest") == dest and cur.get("cron") == C.TICK_CRON:
        return {"ok": True, "schedule_id": cur.get("schedule_id"), "cached": True}
    r = requests.post(
        f"{C.QSTASH_URL}/v2/schedules/{quote(dest, safe=':/')}",
        headers={
            "Authorization": f"Bearer {C.QSTASH_TOKEN}",
            "Upstash-Cron": C.TICK_CRON,
            "Upstash-Schedule-Id": SCHEDULE_ID,
            "Upstash-Retries": "0",
            "Upstash-Timeout": "55s",
            "Upstash-Forward-X-Cron-Secret": C.CRON_SECRET,
            "Content-Type": "application/json",
        },
        data="{}", timeout=15,
    )
    if r.status_code >= 400:
        return {"ok": False, "reason": f"QStash {r.status_code}: {r.text[:160]}"}
    sid = (r.json() or {}).get("scheduleId", SCHEDULE_ID)
    store.put("scheduler", {**want, "schedule_id": sid})
    return {"ok": True, "schedule_id": sid, "cached": False}
