"""Storage: Supabase Postgres (DATABASE_URL, fno/pgkv.py) or Upstash Redis through its REST API.

Since 28 Oct 2026 the app uses Supabase when DATABASE_URL is set (Upstash's free 500k-requests-a-month cap was hit and
stopped trades and the journal). Upstash stays as the fallback and as the source for the one-time copy
(migrate_from_upstash). Every caller keeps using the Redis-style commands below; pgkv.py runs them on Postgres.

Layout (all keys prefixed "fno:"):
  fno:settings   hash  name -> JSON   (account, broker keys/tokens, preferences)
  fno:positions  hash  id   -> JSON   (open paper positions)
  fno:trades     hash  id   -> JSON   (closed trades / journal)

The Redis credentials are only ever used on the server (Vercel env vars), never
sent to the browser.
"""
from __future__ import annotations

import json
import threading
import time

import requests

from . import config as C
from . import pgkv

SETTINGS, POSITIONS, TRADES = "fno:settings", "fno:positions", "fno:trades"
_s = requests.Session()

# Lua scripts so concurrent requests (the page + the every-minute scheduler)
# can never resurrect or double-close a position.
_UPDATE_IF_EXISTS = ("if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 1 then "
                     "return redis.call('HSET', KEYS[1], ARGV[1], ARGV[2]) end return -1")
_TAKE = ("local v = redis.call('HGET', KEYS[1], ARGV[1]) "
         "if v then redis.call('HDEL', KEYS[1], ARGV[1]) end return v")


# Command counter (what uses the Upstash quota) and an in-process read cache.
# Free tier = 500k commands a month; Oct 2026 used ~18k a day, mostly the same settings / positions / trades read again on
# every page refresh and every scheduler tick. All writes go through this one app process (single gunicorn worker), so
# reads are served from memory and every write updates memory too. Atomic operations (take_position, update_position)
# still run in Redis, so two closers can never both win.
_counts: dict = {}
_since = time.time()
_mem: dict = {}            # ("s", name) -> (time, raw json or None) ; ("h", hash) -> (time, {id: raw})
_lock = threading.RLock()
CACHE_TTL = 900            # safety net: re-read from Redis at least every 15 min


def _count(args):
    k = f"{args[0]} {args[1] if len(args) > 1 else ''}"
    if args[0] in ("HGET", "HSET", "HDEL") and len(args) > 2 and args[1] == SETTINGS:
        k += " " + str(args[2])
    _counts[k] = _counts.get(k, 0) + 1


def usage() -> dict:
    hrs = max((time.time() - _since) / 3600, 1e-6)
    top = sorted(_counts.items(), key=lambda x: -x[1])[:40]
    total = sum(_counts.values())
    return {"since_hours": round(hrs, 2), "total": total, "per_day_rate": round(total / hrs * 24), "top": top}


def _fresh(k):
    hit = _mem.get(k)
    return hit if hit and time.time() - hit[0] < CACHE_TTL else None


def backend() -> str:
    return "supabase" if pgkv.enabled() else ("upstash" if C.REDIS_URL and C.REDIS_TOKEN else "none")


def _cmd(*args):
    _count(args)
    if pgkv.enabled():
        try:
            if args[0] == "EVAL" and args[1] == _UPDATE_IF_EXISTS:
                return pgkv.update_if_exists(str(args[3]), str(args[4]), str(args[5]))
            if args[0] == "EVAL" and args[1] == _TAKE:
                return pgkv.take(str(args[3]), str(args[4]))
            return pgkv.execute(*args)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Database error: {type(e).__name__}: {str(e)[:200]}")
    return _upstash(*args)


def _upstash(*args):
    if not C.REDIS_URL or not C.REDIS_TOKEN:
        raise RuntimeError("Database not connected - add DATABASE_URL (Supabase) in the Render environment.")
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


def _hash(key) -> dict:
    with _lock:
        hit = _fresh(("h", key))
        if hit:
            return hit[1]
        flat = _cmd("HGETALL", key) or []
        d = dict(zip(flat[::2], flat[1::2])) if isinstance(flat, list) else dict(flat)
        _mem[("h", key)] = (time.time(), d)
        return d


def _hset_mem(key, field, raw):
    with _lock:
        hit = _mem.get(("h", key))
        if hit:
            if raw is None:
                hit[1].pop(field, None)
            else:
                hit[1][field] = raw


def _load_all(key, sort_field):
    rows = [json.loads(x) for x in _hash(key).values()]
    return sorted(rows, key=lambda d: d.get(sort_field) or "")


# ---------------- settings (name -> json) ----------------
def get(key, default=None):
    with _lock:
        hit = _fresh(("s", key))
        if hit:
            v = hit[1]
        else:
            v = _cmd("HGET", SETTINGS, key)
            _mem[("s", key)] = (time.time(), v)
    return json.loads(v) if v is not None else default


def get_many(keys) -> dict:
    keys = list(keys)
    out, miss = {}, []
    with _lock:
        for k in keys:
            hit = _fresh(("s", k))
            if hit:
                if hit[1] is not None:
                    out[k] = json.loads(hit[1])
            else:
                miss.append(k)
        if miss:
            vals = _cmd("HMGET", SETTINGS, *miss) or []
            now = time.time()
            for k, v in zip(miss, vals):
                _mem[("s", k)] = (now, v)
                if v is not None:
                    out[k] = json.loads(v)
    return out


def put(key, value):
    raw = _dump(value)
    with _lock:
        _cmd("HSET", SETTINGS, key, raw)
        _mem[("s", key)] = (time.time(), raw)


def delete_setting(key):
    with _lock:
        _cmd("HDEL", SETTINGS, key)
        _mem[("s", key)] = (time.time(), None)


# ---------------- positions ----------------
def positions() -> list[dict]:
    return _load_all(POSITIONS, "opened")


def insert_position(pos: dict):
    raw = _dump(pos)
    with _lock:
        _cmd("HSET", POSITIONS, pos["id"], raw)
        _hset_mem(POSITIONS, pos["id"], raw)


def update_position(pos: dict):
    """Update only if the position is still open (never re-creates a closed one)."""
    raw = _dump(pos)
    with _lock:
        r = _cmd("EVAL", _UPDATE_IF_EXISTS, 1, POSITIONS, pos["id"], raw)
        _hset_mem(POSITIONS, pos["id"], None if r == -1 else raw)


def take_position(pos_id) -> dict | None:
    """Atomically remove and return a position (only one closer can win)."""
    with _lock:
        v = _cmd("EVAL", _TAKE, 1, POSITIONS, pos_id)
        _hset_mem(POSITIONS, pos_id, None)
    return json.loads(v) if v else None


def clear_positions():
    with _lock:
        _cmd("DEL", POSITIONS)
        _mem[("h", POSITIONS)] = (time.time(), {})


# ---------------- trades ----------------
def trades() -> list[dict]:
    return _load_all(TRADES, "closed")


def insert_trade(rec: dict):
    raw = _dump(rec)
    with _lock:
        _cmd("HSET", TRADES, rec["id"], raw)
        _hset_mem(TRADES, rec["id"], raw)


def clear_trades():
    with _lock:
        _cmd("DEL", TRADES)
        _mem[("h", TRADES)] = (time.time(), {})


# ---------------- generic cached hashes (crypto / US / gold books and their orders) ----------------
def hvals(key) -> list:
    return list(_hash(key).values())


def hset(key, field, raw):
    with _lock:
        _cmd("HSET", key, field, raw)
        _hset_mem(key, field, raw)


def htake(key, field):
    """Atomic HGET + HDEL in Redis; returns the raw value or None."""
    with _lock:
        v = _cmd("EVAL", _TAKE, 1, key, field)
        _hset_mem(key, field, None)
    return v


def hupdate(key, field, raw):
    """Update a field only if it still exists (atomic in Redis)."""
    with _lock:
        r = _cmd("EVAL", _UPDATE_IF_EXISTS, 1, key, field, raw)
        _hset_mem(key, field, None if r == -1 else raw)
    return r


def hdrop(key):
    with _lock:
        _cmd("DEL", key)
        _mem[("h", key)] = (time.time(), {})


# ---------------- "heartbeat" values written often, read rarely ----------------
_lazy: dict = {}           # key -> (value, last time written to Redis)


def put_lazy(key, value, every=300):
    """A settings value that changes every minute (last tick time): kept in memory at once, written to Redis at most
    every `every` seconds (so a restart loses at most that much)."""
    raw = _dump(value)
    with _lock:
        _mem[("s", key)] = (time.time(), raw)
        last = _lazy.get(key, (None, 0))[1]
        if time.time() - last >= every:
            _cmd("HSET", SETTINGS, key, raw)
            _lazy[key] = (raw, time.time())


def set_raw_lazy(key, value: str, ex: int, every=300):
    with _lock:
        _mem[("r", key)] = (time.time(), value)
        last = _lazy.get(key, (None, 0))[1]
        if time.time() - last >= every:
            _cmd("SET", key, value, "EX", ex)
            _lazy[key] = (value, time.time())


def get_raw(key):
    with _lock:
        hit = _mem.get(("r", key))
        if hit:
            return hit[1]
        v = _cmd("GET", key)
        _mem[("r", key)] = (time.time(), v)
        return v


def forget():
    """Drop the read cache (after a restore from backup or any write made outside these functions)."""
    with _lock:
        _mem.clear()


def ping() -> bool:
    try:
        return _cmd("PING") in ("PONG", True)
    except Exception:
        return False


# ---------------- one-time copy Upstash -> Supabase ----------------
def migrate_from_upstash() -> dict:
    """Copy every fno:* key from Upstash into Postgres, keeping anything Postgres already has (new data wins).
    Costs one SCAN pass plus about two Upstash commands per key, so it fits easily in a fresh monthly quota."""
    if not pgkv.enabled():
        return {"ok": False, "error": "DATABASE_URL is not set"}
    if not (C.REDIS_URL and C.REDIS_TOKEN):
        return {"ok": False, "error": "Upstash credentials are not set"}
    T = pgkv.TABLE
    keys, cur = [], "0"
    while True:
        cur, batch = _upstash("SCAN", cur, "MATCH", "*", "COUNT", 1000)
        keys += batch or []
        if str(cur) == "0":
            break
    done = {"keys": 0, "rows": 0, "skipped_existing": 0, "types": {}}
    for k in sorted(set(keys)):
        typ = _upstash("TYPE", k)
        ttl = _upstash("TTL", k)
        exp_sql, exp_p = ("now() + make_interval(secs => %s)", (int(ttl),)) if isinstance(ttl, int) and ttl > 0 else ("null", ())
        rows = []
        if typ == "hash":
            flat = _upstash("HGETALL", k) or []
            pairs = flat.items() if isinstance(flat, dict) else zip(flat[::2], flat[1::2])
            rows = [(f, v, "h", None) for f, v in pairs]
        elif typ == "string":
            rows = [("", _upstash("GET", k), "s", None)]
        elif typ == "set":
            rows = [(m, "", "S", None) for m in (_upstash("SMEMBERS", k) or [])]
        elif typ == "zset":
            flat = _upstash("ZRANGE", k, 0, -1, "WITHSCORES") or []
            if flat and isinstance(flat[0], list):
                flat = [y for pair in flat for y in pair]
            rows = [(m, None, "z", float(sc)) for m, sc in zip(flat[::2], flat[1::2])]
        elif typ == "list":
            rows = [(f"{i:012d}", v, "l", None) for i, v in enumerate(_upstash("LRANGE", k, 0, -1) or [])]
        else:
            continue
        done["types"][typ] = done["types"].get(typ, 0) + 1
        done["keys"] += 1
        for f, v, kind, sc in rows:
            n = pgkv._q(f"insert into {T} (k, f, v, kind, score, exp) values (%s, %s, %s, %s, %s, {exp_sql}) on conflict (k, f) do nothing",
                        (k, f, v, kind, sc) + exp_p, "count")
            done["rows"] += n
            done["skipped_existing"] += 0 if n else 1
    forget()
    return {"ok": True, **done}
