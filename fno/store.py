"""Storage on Upstash Redis (free tier) through its REST API - no driver needed.

Layout (all keys prefixed "fno:"):
  fno:settings   hash  name -> JSON   (account, broker keys/tokens, preferences)
  fno:positions  hash  id   -> JSON   (open paper positions)
  fno:trades     hash  id   -> JSON   (closed trades / journal)

The Redis credentials are only ever used on the server (Vercel env vars), never
sent to the browser.
"""
from __future__ import annotations

import json

import requests

from . import config as C

SETTINGS, POSITIONS, TRADES = "fno:settings", "fno:positions", "fno:trades"
_s = requests.Session()

# Lua scripts so concurrent requests (the page + the every-minute scheduler)
# can never resurrect or double-close a position.
_UPDATE_IF_EXISTS = ("if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 1 then "
                     "return redis.call('HSET', KEYS[1], ARGV[1], ARGV[2]) end return -1")
_TAKE = ("local v = redis.call('HGET', KEYS[1], ARGV[1]) "
         "if v then redis.call('HDEL', KEYS[1], ARGV[1]) end return v")


def _cmd(*args):
    if not C.REDIS_URL or not C.REDIS_TOKEN:
        raise RuntimeError("Database not connected - add Upstash Redis to the Vercel project.")
    r = _s.post(C.REDIS_URL, data=json.dumps([str(a) for a in args]),
                headers={"Authorization": f"Bearer {C.REDIS_TOKEN}"}, timeout=10)
    try:
        j = r.json()
    except Exception:
        raise RuntimeError(f"Database error {r.status_code}: {r.text[:200]}")
    if r.status_code >= 400 or "error" in j:
        raise RuntimeError(f"Database error: {j.get('error') or r.status_code}")
    return j.get("result")


def _dump(v):
    return json.dumps(v, default=str, separators=(",", ":"))


def _load_all(key, sort_field):
    raw = _cmd("HVALS", key) or []
    rows = [json.loads(x) for x in raw]
    return sorted(rows, key=lambda d: d.get(sort_field) or "")


# ---------------- settings (name -> json) ----------------
def get(key, default=None):
    v = _cmd("HGET", SETTINGS, key)
    return json.loads(v) if v is not None else default


def get_many(keys) -> dict:
    keys = list(keys)
    vals = _cmd("HMGET", SETTINGS, *keys) or []
    return {k: json.loads(v) for k, v in zip(keys, vals) if v is not None}


def put(key, value):
    _cmd("HSET", SETTINGS, key, _dump(value))


def delete_setting(key):
    _cmd("HDEL", SETTINGS, key)


# ---------------- positions ----------------
def positions() -> list[dict]:
    return _load_all(POSITIONS, "opened")


def insert_position(pos: dict):
    _cmd("HSET", POSITIONS, pos["id"], _dump(pos))


def update_position(pos: dict):
    """Update only if the position is still open (never re-creates a closed one)."""
    _cmd("EVAL", _UPDATE_IF_EXISTS, 1, POSITIONS, pos["id"], _dump(pos))


def take_position(pos_id) -> dict | None:
    """Atomically remove and return a position (only one closer can win)."""
    v = _cmd("EVAL", _TAKE, 1, POSITIONS, pos_id)
    return json.loads(v) if v else None


def clear_positions():
    _cmd("DEL", POSITIONS)


# ---------------- trades ----------------
def trades() -> list[dict]:
    return _load_all(TRADES, "closed")


def insert_trade(rec: dict):
    _cmd("HSET", TRADES, rec["id"], _dump(rec))


def clear_trades():
    _cmd("DEL", TRADES)


def ping() -> bool:
    try:
        return _cmd("PING") in ("PONG", True)
    except Exception:
        return False
