"""Postgres (Supabase) backend that speaks the Redis commands the app uses.

Why: Upstash's free tier caps requests at 500,000 a month and the app hit it on 28 Oct 2026, which stopped trades and the
journal from loading or saving. Supabase's free Postgres has no request cap (500 MB storage), and the server (Render,
Singapore) sits next to a Singapore Supabase project, so a round trip is a couple of milliseconds.

How: one table holds every key. A row is (key, field) -> value, with a kind (h hash, s string, S set, z sorted set,
l list), an optional score (sorted sets) and an optional expiry. fno/store.py keeps its in-memory cache and calls
`execute(*args)` with the same command lists it used to POST to Upstash, so nothing else in the app changes. The two Lua
scripts store.py used for atomic "update only if it still exists" and "take" are done as single SQL statements.

Connection: DATABASE_URL (Supabase -> Connect -> Session pooler string), one connection shared by the app's threads
behind a lock, reconnected on failure. Expired rows are hidden at once and deleted about once an hour.
"""
from __future__ import annotations

import fnmatch
import os
import threading
import time

import psycopg

URL = os.environ.get("DATABASE_URL", "")
TABLE = "fno_kv"
_conn = None
_lock = threading.RLock()
_last_purge = [0.0]

DDL = f"""
create table if not exists {TABLE} (
  k     text not null,
  f     text not null default '',
  v     text,
  kind  char(1) not null,
  score double precision,
  exp   timestamptz,
  primary key (k, f)
);
create index if not exists {TABLE}_exp on {TABLE} (exp) where exp is not null;
alter table {TABLE} enable row level security;
"""
LIVE = "(exp is null or exp > now())"


def enabled() -> bool:
    return bool(URL)


def _connect():
    global _conn
    c = psycopg.connect(URL, autocommit=True, connect_timeout=10, prepare_threshold=None)
    with c.cursor() as cur:
        cur.execute(DDL)
    _conn = c
    return c


def _q(sql, params=(), fetch="all"):
    """Run one statement; reconnect once if the connection dropped."""
    global _conn
    with _lock:
        for attempt in (1, 2):
            try:
                c = _conn if (_conn is not None and not _conn.closed) else _connect()
                with c.cursor() as cur:
                    cur.execute(sql, params)
                    if fetch == "all":
                        return cur.fetchall() if cur.description else []
                    if fetch == "one":
                        return cur.fetchone() if cur.description else None
                    return cur.rowcount
            except (psycopg.OperationalError, psycopg.InterfaceError):
                try:
                    if _conn is not None:
                        _conn.close()
                except Exception:
                    pass
                _conn = None
                if attempt == 2:
                    raise


def _purge():
    if time.time() - _last_purge[0] > 3600:
        _last_purge[0] = time.time()
        try:
            _q(f"delete from {TABLE} where exp is not null and exp <= now()", fetch="count")
        except Exception:
            pass


def _s(x):
    return None if x is None else str(x)


def _exp_of(k):
    r = _q(f"select exp from {TABLE} where k = %s and {LIVE} and exp is not null limit 1", (k,), "one")
    return r[0] if r else None


def _hset(k, pairs, kind="h", only_new=False):
    exp = _exp_of(k)
    added = 0
    for i in range(0, len(pairs), 2):
        f, v = str(pairs[i]), str(pairs[i + 1])
        if only_new:
            n = _q(f"insert into {TABLE} (k, f, v, kind, exp) values (%s, %s, %s, %s, %s) on conflict (k, f) do update set v = excluded.v, kind = excluded.kind, exp = excluded.exp "
                   f"where {TABLE}.exp is not null and {TABLE}.exp <= now()", (k, f, v, kind, exp), "count")
            added += n
            continue
        r = _q(f"insert into {TABLE} (k, f, v, kind, exp) values (%s, %s, %s, %s, %s) on conflict (k, f) do update set v = excluded.v, kind = excluded.kind, "
               f"exp = case when {TABLE}.exp is not null and {TABLE}.exp <= now() then excluded.exp else {TABLE}.exp end "
               f"returning (xmax = 0) as inserted", (k, f, v, kind, exp), "one")
        added += 1 if (r and r[0]) else 0
    return added


def execute(*args):
    """Run one Redis-style command (strings in, Upstash-style JSON results out)."""
    _purge()
    a = [str(x) for x in args]
    cmd = a[0].upper()
    k = a[1] if len(a) > 1 else None

    if cmd == "PING":
        _q("select 1")
        return "PONG"
    # ---- hashes
    if cmd == "HSET":
        return _hset(k, a[2:])
    if cmd == "HSETNX":
        return _hset(k, a[2:4], only_new=True)
    if cmd == "HGET":
        r = _q(f"select v from {TABLE} where k = %s and f = %s and {LIVE}", (k, a[2]), "one")
        return r[0] if r else None
    if cmd == "HMGET":
        fs = a[2:]
        rows = dict(_q(f"select f, v from {TABLE} where k = %s and f = any(%s) and {LIVE}", (k, fs)))
        return [rows.get(f) for f in fs]
    if cmd == "HGETALL":
        out = []
        for f, v in _q(f"select f, v from {TABLE} where k = %s and kind = 'h' and {LIVE}", (k,)):
            out += [f, v]
        return out
    if cmd == "HKEYS":
        return [r[0] for r in _q(f"select f from {TABLE} where k = %s and kind = 'h' and {LIVE}", (k,))]
    if cmd == "HLEN":
        return _q(f"select count(*) from {TABLE} where k = %s and kind = 'h' and {LIVE}", (k,), "one")[0]
    if cmd == "HDEL":
        return _q(f"delete from {TABLE} where k = %s and f = any(%s)", (k, a[2:]), "count")
    if cmd == "HEXISTS":
        return 1 if _q(f"select 1 from {TABLE} where k = %s and f = %s and {LIVE}", (k, a[2]), "one") else 0
    # ---- strings
    if cmd == "SET":
        v = a[2]
        opts = [x.upper() for x in a[3:]]
        ex = None
        if "EX" in opts:
            ex = int(a[3 + opts.index("EX") + 1])
        expr = "now() + make_interval(secs => %s)" if ex is not None else "null"
        p = (k, v) + ((ex,) if ex is not None else ())
        if "NX" in opts:
            r = _q(f"insert into {TABLE} (k, f, v, kind, exp) values (%s, '', %s, 's', {expr}) on conflict (k, f) do update set v = excluded.v, kind = 's', exp = excluded.exp "
                   f"where {TABLE}.exp is not null and {TABLE}.exp <= now() returning 1", p, "one")
            return "OK" if r else None
        _q(f"delete from {TABLE} where k = %s and f <> ''", (k,), "count")
        _q(f"insert into {TABLE} (k, f, v, kind, exp) values (%s, '', %s, 's', {expr}) on conflict (k, f) do update set v = excluded.v, kind = 's', exp = excluded.exp", p, "count")
        return "OK"
    if cmd == "GET":
        r = _q(f"select v from {TABLE} where k = %s and f = '' and kind = 's' and {LIVE}", (k,), "one")
        return r[0] if r else None
    # ---- keys
    if cmd == "DEL":
        n = _q(f"select count(distinct k) from {TABLE} where k = any(%s) and {LIVE}", (a[1:],), "one")[0]
        _q(f"delete from {TABLE} where k = any(%s)", (a[1:],), "count")
        return n
    if cmd == "EXPIRE":
        n = _q(f"update {TABLE} set exp = now() + make_interval(secs => %s) where k = %s and {LIVE}", (int(a[2]), k), "count")
        return 1 if n else 0
    if cmd == "TTL":
        r = _q(f"select extract(epoch from (exp - now()))::bigint from {TABLE} where k = %s and {LIVE} limit 1", (k,), "one")
        if not r:
            return -2
        return -1 if r[0] is None else int(r[0])
    if cmd == "TYPE":
        r = _q(f"select kind from {TABLE} where k = %s and {LIVE} limit 1", (k,), "one")
        return {"h": "hash", "s": "string", "S": "set", "z": "zset", "l": "list"}.get(r[0] if r else "", "none")
    if cmd == "SCAN":
        pat = a[a.index("MATCH") + 1] if "MATCH" in a else "*"
        like = pat.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("*", "%").replace("?", "_")
        keys = [r[0] for r in _q(f"select distinct k from {TABLE} where k like %s and {LIVE} order by k", (like,))]
        return ["0", [x for x in keys if fnmatch.fnmatchcase(x, pat)]]
    # ---- sets
    if cmd == "SADD":
        return _sadd(k, a[2:])
    if cmd == "SREM":
        return _q(f"delete from {TABLE} where k = %s and kind = 'S' and f = any(%s)", (k, a[2:]), "count")
    if cmd == "SMEMBERS":
        return sorted(r[0] for r in _q(f"select f from {TABLE} where k = %s and kind = 'S' and {LIVE}", (k,)))
    if cmd == "SISMEMBER":
        return 1 if _q(f"select 1 from {TABLE} where k = %s and kind = 'S' and f = %s and {LIVE}", (k, a[2]), "one") else 0
    # ---- sorted sets
    if cmd == "ZADD":
        exp = _exp_of(k); added = 0
        for i in range(2, len(a), 2):
            r = _q(f"insert into {TABLE} (k, f, v, kind, score, exp) values (%s, %s, null, 'z', %s, %s) on conflict (k, f) do update set score = excluded.score, kind = 'z' "
                   f"returning (xmax = 0)", (k, a[i + 1], float(a[i]), exp), "one")
            added += 1 if (r and r[0]) else 0
        return added
    if cmd in ("ZRANGE", "ZREVRANGE"):
        start, stop = int(a[2]), int(a[3])
        ws = "WITHSCORES" in [x.upper() for x in a[4:]]
        rows = _q(f"select f, score from {TABLE} where k = %s and kind = 'z' and {LIVE} order by score {'desc' if cmd == 'ZREVRANGE' else 'asc'}, f {'desc' if cmd == 'ZREVRANGE' else 'asc'}", (k,))
        n = len(rows)
        s = start + n if start < 0 else start
        e = stop + n if stop < 0 else stop
        rows = rows[max(0, s): e + 1] if e >= 0 else []
        if not ws:
            return [r[0] for r in rows]
        out = []
        for f, sc in rows:
            out += [f, (str(int(sc)) if float(sc).is_integer() else repr(float(sc)))]
        return out
    if cmd == "ZCARD":
        return _q(f"select count(*) from {TABLE} where k = %s and kind = 'z' and {LIVE}", (k,), "one")[0]
    if cmd == "ZREM":
        return _q(f"delete from {TABLE} where k = %s and kind = 'z' and f = any(%s)", (k, a[2:]), "count")
    # ---- lists (field = zero-padded position)
    if cmd == "RPUSH":
        exp = _exp_of(k)
        r = _q(f"select coalesce(max(f::bigint), -1) from {TABLE} where k = %s and kind = 'l'", (k,), "one")
        nxt = int(r[0]) + 1
        for i, v in enumerate(a[2:]):
            _q(f"insert into {TABLE} (k, f, v, kind, exp) values (%s, %s, %s, 'l', %s)", (k, f"{nxt + i:012d}", v, exp), "count")
        return _q(f"select count(*) from {TABLE} where k = %s and kind = 'l' and {LIVE}", (k,), "one")[0]
    if cmd == "LRANGE":
        rows = [r[0] for r in _q(f"select v from {TABLE} where k = %s and kind = 'l' and {LIVE} order by f", (k,))]
        start, stop = int(a[2]), int(a[3])
        n = len(rows)
        s = start + n if start < 0 else start
        e = stop + n if stop < 0 else stop
        return rows[max(0, s): e + 1] if e >= 0 else []
    if cmd == "LLEN":
        return _q(f"select count(*) from {TABLE} where k = %s and kind = 'l' and {LIVE}", (k,), "one")[0]
    if cmd == "LTRIM":
        rows = [r[0] for r in _q(f"select f from {TABLE} where k = %s and kind = 'l' and {LIVE} order by f", (k,))]
        start, stop = int(a[2]), int(a[3]); n = len(rows)
        s = start + n if start < 0 else start
        e = stop + n if stop < 0 else stop
        keep = set(rows[max(0, s): e + 1]) if e >= 0 else set()
        drop = [f for f in rows if f not in keep]
        if drop:
            _q(f"delete from {TABLE} where k = %s and kind = 'l' and f = any(%s)", (k, drop), "count")
        return "OK"
    # ---- counters
    if cmd in ("INCR", "INCRBY"):
        by = int(a[2]) if cmd == "INCRBY" else 1
        r = _q(f"insert into {TABLE} (k, f, v, kind) values (%s, '', %s, 's') on conflict (k, f) do update set v = "
               f"(case when {TABLE}.exp is not null and {TABLE}.exp <= now() then 0 else coalesce({TABLE}.v, '0')::bigint end + %s)::text returning v", (k, str(by), by), "one")
        return int(r[0])
    raise RuntimeError(f"Database: command {cmd} is not supported by the Postgres store")


def _sadd(k, members):
    exp = _exp_of(k); added = 0
    for m in members:
        r = _q(f"insert into {TABLE} (k, f, v, kind, exp) values (%s, %s, '', 'S', %s) on conflict (k, f) do update set kind = 'S', exp = excluded.exp "
               f"where {TABLE}.exp is not null and {TABLE}.exp <= now() returning 1", (k, m, exp), "one")
        added += 1 if r else 0
    return added


# ---- the two atomic operations store.py used Lua for
def update_if_exists(k, field, raw):
    n = _q(f"update {TABLE} set v = %s where k = %s and f = %s and {LIVE}", (raw, k, field), "count")
    return 0 if n else -1


def take(k, field):
    r = _q(f"delete from {TABLE} where k = %s and f = %s and {LIVE} returning v", (k, field), "one")
    return r[0] if r else None


def size_bytes() -> int:
    r = _q(f"select pg_total_relation_size('{TABLE}')", fetch="one")
    return int(r[0]) if r else 0
