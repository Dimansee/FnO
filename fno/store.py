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
    """A copy of the hash (callers may iterate while another thread writes). The database read happens outside the
    lock so a slow query in one thread does not freeze every other request."""
    with _lock:
        hit = _fresh(("h", key))
        if hit:
            return dict(hit[1])
    flat = _cmd("HGETALL", key) or []
    d = dict(zip(flat[::2], flat[1::2])) if isinstance(flat, list) else dict(flat)
    with _lock:
        hit = _fresh(("h", key))
        if hit:                                             # another thread filled (and maybe updated) it meanwhile
            return dict(hit[1])
        _mem[("h", key)] = (time.time(), d)
    return dict(d)


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


# ---------------- secret settings: encrypted at rest ----------------
# Broker tokens, the Drive link and the push keys are stored encrypted (review 28 Oct): anyone who reads the database
# (a leaked DATABASE_URL, a Supabase dashboard session, a backup) sees only "enc:..." for these. The key comes from
# SESSION_SECRET, so changing SESSION_SECRET makes them unreadable - the app then simply asks you to reconnect.
SECRET_KEYS = {"upstox", "fyers", "vapid", "drive"}
_fernet = [None, None]


def _fk():
    sec = C.SESSION_SECRET or ""
    if not sec:
        return None
    if _fernet[0] != sec:
        import base64, hashlib
        from cryptography.fernet import Fernet
        _fernet[:] = [sec, Fernet(base64.urlsafe_b64encode(hashlib.sha256(("fno-settings-v1:" + sec).encode()).digest()))]
    return _fernet[1]


def _enc(key, raw):
    f = _fk() if key in SECRET_KEYS else None
    return "enc:" + f.encrypt(raw.encode()).decode() if f else raw


def _dec(key, raw):
    """Returns (json text or None, was_plain)."""
    if raw is None or not str(raw).startswith("enc:"):
        return raw, True
    f = _fk()
    try:
        return f.decrypt(raw[4:].encode()).decode(), False
    except Exception:
        print(f"[store] could not decrypt setting {key!r} (SESSION_SECRET changed?) - treating it as not set", flush=True)
        return None, False


def _load_setting(key, raw, default=None):
    txt, plain = _dec(key, raw)
    if txt is None:
        return default
    v = json.loads(txt)
    if plain and key in SECRET_KEYS and _fk():           # an older plaintext secret: encrypt it in place
        try:
            put(key, v)
        except Exception:
            pass
    return v


# ---------------- settings (name -> json) ----------------
def get(key, default=None):
    with _lock:
        hit = _fresh(("s", key))
    if hit:
        v = hit[1]
    else:
        v = _cmd("HGET", SETTINGS, key)
        with _lock:
            if not _fresh(("s", key)):
                _mem[("s", key)] = (time.time(), v)
    return _load_setting(key, v, default) if v is not None else default


def get_many(keys) -> dict:
    keys = list(keys)
    out, miss = {}, []
    with _lock:
        for k in keys:
            hit = _fresh(("s", k))
            if hit:
                if hit[1] is not None:
                    out[k] = hit[1]
            else:
                miss.append(k)
        if miss:
            vals = _cmd("HMGET", SETTINGS, *miss) or []
            now = time.time()
            for k, v in zip(miss, vals):
                _mem[("s", k)] = (now, v)
                if v is not None:
                    out[k] = v
    res = {}
    for k, v in out.items():                               # decrypt / parse outside the lock
        x = _load_setting(k, v)
        if x is not None:
            res[k] = x
    return res


def put(key, value):
    raw = _enc(key, _dump(value))
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


def close_position_into_trades(pos_id, build) -> dict | None:
    return close_into(POSITIONS, TRADES, pos_id, build)


def take_position(pos_id) -> dict | None:
    """Atomically remove and return a position (only one closer can win)."""
    with _lock:
        v = _cmd("EVAL", _TAKE, 1, POSITIONS, pos_id)
        _hset_mem(POSITIONS, pos_id, None)
    return json.loads(v) if v else None


def close_into(src, dst, field, build):
    """Close a record in ONE database step: read the open record, build the closed record from it (build(dict) -> dict),
    then remove it from `src` and save it into `dst` atomically. Returns the closed record, or None if it was already
    closed by someone else. On Supabase this is a single SQL statement (a crash or deploy can no longer lose a trade
    between the delete and the insert); on the old Upstash path it falls back to take + insert."""
    v = _cmd("HGET", src, field)
    if not v:
        with _lock:
            _hset_mem(src, field, None)
        return None
    rec = build(json.loads(v))
    raw = _dump(rec)
    with _lock:
        if pgkv.enabled():
            ok = pgkv.move(src, field, dst, field, raw)
        else:
            ok = bool(_cmd("EVAL", _TAKE, 1, src, field))
            if ok:
                _cmd("HSET", dst, field, raw)
        _hset_mem(src, field, None)
        if ok:
            _hset_mem(dst, field, raw)
    return rec if ok else None


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
# Rules (revised 31 Oct 2026 - the user: "everything like Upstash, except keep any new research, any rule change and the
# learning of the new days; portfolio and scalp book like Upstash so I continue from the previous portfolio"):
#  PORTFOLIO   paper account(s), closed trades and order history (F&O, scalp, crypto/US/gold): Upstash WINS.
#              Supabase-only closed trades are moved to fno:migration:supabase_only (nothing is deleted).
#              Open positions / pending orders: Upstash's are 4+ days stale (options expired, prices moved) and Supabase's
#              belong to the temporary account, so every book restarts flat; both sets are parked in
#              fno:migration:stale_open and listed in the report.
#  RULES       settings / preferences / strategies / switches: the Supabase value WINS where it exists (changes made since
#              28 Oct are kept); dicts are merged key by key (Supabase on top), lists of {id: ...} merged by id.
#              Values that are only on Upstash are copied.
#  LEARNING    lessons, research notes, adopted changes, journal, lag reviews, recorder, saved data: union of both.
#              Evidence counters (fno:ls:st) are ADDED together so neither side's days are lost.
#  LOGINS      broker tokens, Drive link, push keys, scheduler: the live Supabase value is kept.
#  Every Supabase value that gets replaced is kept in fno:migration:replaced ("<key>|<field>" -> old value).
#  Expiries (TTL) are carried over; keys already expired in Upstash are not copied.
KEEP_CURRENT = {"upstox", "fyers", "drive", "scheduler", "vapid", "instrument_master", "g_last_tick", "upstash_migration",
                "upstash_migration_preview"}
PORTFOLIO_FIELDS = ("account", "g_account:", "g_ordhist:")               # settings fields: Upstash wins
UPSTASH_FIRST = ("g_lab_started",)                                        # settings fields: the original value wins
CLOSED_BOOKS = ("fno:trades", "fno:trades:removed", "fno:scalp:trades", "fno:g:trades:")     # hashes: Upstash wins
OPEN_BOOKS = ("fno:positions", "fno:scalp:positions", "fno:g:pos:", "fno:g:orders:")       # hashes: start flat
ADD_STATS = ("fno:ls:st",)                                                # evidence counters: n / s / r added
SKIP_STRINGS = ("fno:last_tick", "fno:rec:status", "fno:data:in_saved:", "fno:data:us_saved:", "fno:stocks:", "fno:scalp:day:",
                "fno:lock:", "fno:login_fail:")
MIG = {"running": False, "started": None, "progress": None, "result": None}
MIG_FROM = "2026-11-01T00:00:00+00:00"          # Upstash's monthly quota resets on the 1st (UTC)


def _is(k, fams) -> bool:
    return any(k == f or (f.endswith(":") and k.startswith(f)) for f in fams)


def _jl(v):
    try:
        return json.loads(v)
    except Exception:
        return None


def merge_setting(name: str, ups_raw: str, sup_raw: str) -> tuple[str, str]:
    """(merged raw value, how) for one fno:settings field present on both sides (rules: Supabase on top)."""
    if ups_raw == sup_raw:
        return sup_raw, "same"
    if name in KEEP_CURRENT:
        return sup_raw, "kept_current"
    if _is(name, PORTFOLIO_FIELDS) or name in UPSTASH_FIRST:
        return ups_raw, "upstash"
    u, s = _jl(ups_raw), _jl(sup_raw)
    if name == "learn_overlay" and isinstance(u, dict) and isinstance(s, dict):
        by = {r.get("id"): r for r in (u.get("rules") or []) if isinstance(r, dict)}
        by.update({r.get("id"): r for r in (s.get("rules") or []) if isinstance(r, dict)})
        return _dump({**u, **s, "rules": list(by.values())}), "merged"
    if isinstance(u, dict) and isinstance(s, dict):
        return _dump({**u, **s}), "merged"
    if isinstance(u, list) and isinstance(s, list) and all(isinstance(x, dict) and "id" in x for x in u + s) and (u or s):
        by = {x["id"]: x for x in u}
        by.update({x["id"]: x for x in s})
        return _dump(list(by.values())), "merged"
    return sup_raw, "supabase"


def add_stats(ups_raw: str, sup_raw: str) -> str:
    """Learning evidence bucket {n, s, r}: both databases' trades / signals counted together."""
    u, s = _jl(ups_raw) or {}, _jl(sup_raw) or {}
    try:
        from .learn import R_KEEP
    except Exception:
        R_KEEP = 120
    return _dump({"n": int(u.get("n", 0)) + int(s.get("n", 0)), "s": round(float(u.get("s", 0)) + float(s.get("s", 0)), 4),
                  "r": ((u.get("r") or []) + (s.get("r") or []))[-R_KEEP:]})


def _ups_read(k, typ):
    if typ == "hash":
        flat = _upstash("HGETALL", k) or []
        return dict(flat.items() if isinstance(flat, dict) else zip(flat[::2], flat[1::2]))
    if typ == "string":
        return _upstash("GET", k)
    if typ == "set":
        return sorted(_upstash("SMEMBERS", k) or [])
    if typ == "zset":
        flat = _upstash("ZRANGE", k, 0, -1, "WITHSCORES") or []
        if flat and isinstance(flat[0], list):
            flat = [y for pair in flat for y in pair]
        return {m: float(sc) for m, sc in zip(flat[::2], flat[1::2])}
    if typ == "list":
        return _upstash("LRANGE", k, 0, -1) or []
    return None


def migrate_from_upstash(dry_run: bool = False) -> dict:
    """Copy every key from Upstash into Supabase by the rules above. dry_run=True only reports what would happen."""
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
    keys = sorted(set(keys))
    rep = {"ok": True, "dry_run": dry_run, "rules": "2026-10-31", "keys": len(keys), "by_type": {}, "rows_copied": 0, "rows_already_there": 0,
           "settings_from_upstash": [], "settings_kept_supabase": [], "settings_merged": [], "settings_added": [], "settings_kept_current": [],
           "portfolio": {}, "stale_open": [], "supabase_only_trades": 0, "stats_added": 0,
           "skipped_caches": 0, "lists_skipped_exist": [], "families": {}, "missing_after": [], "verified": None}

    def w(sql, params):
        if not dry_run:
            pgkv._q(sql, params, "count")

    def keep_old(tag, k, f, v):
        w(f"insert into {T} (k, f, v, kind) values (%s, %s, %s, 'h') on conflict (k, f) do update set v = excluded.v",
          (tag, f"{k}|{f}", v))

    def sup_hash(k):
        return {f: v for f, v in pgkv._q(f"select f, v from {T} where k = %s and kind = 'h' and (exp is null or exp > now())", (k,))}

    def hset(k, f, v, exp_sql="null", exp_p=()):
        w(f"insert into {T} (k, f, v, kind, exp) values (%s, %s, %s, 'h', {exp_sql}) on conflict (k, f) do update set v = excluded.v, kind = 'h', exp = excluded.exp",
          (k, f, v) + exp_p)

    def hdel(k, f):
        w(f"delete from {T} where k = %s and f = %s", (k, f))

    seen_open = set()
    for n, k in enumerate(keys):
        MIG["progress"] = f"{n + 1} / {len(keys)} keys"
        typ = _upstash("TYPE", k)
        if typ == "none":
            continue
        rep["by_type"][typ] = rep["by_type"].get(typ, 0) + 1
        fam = ":".join(k.split(":")[:2]) if k.count(":") >= 1 else k
        rep["families"][fam] = rep["families"].get(fam, 0) + 1
        if typ == "string" and k.startswith(SKIP_STRINGS):
            rep["skipped_caches"] += 1
            continue
        ttl = _upstash("TTL", k)
        exp_sql, exp_p = ("now() + make_interval(secs => %s)", (int(ttl),)) if isinstance(ttl, int) and ttl > 0 else ("null", ())
        data = _ups_read(k, typ)
        if data is None:
            continue
        if typ == "hash" and k == SETTINGS:
            have = sup_hash(k)
            for f, v in data.items():
                if f not in have:
                    rep["rows_copied"] += 1
                    rep["settings_added"].append(f)
                    hset(k, f, v)
                    continue
                merged, how = merge_setting(f, v, have[f])
                if how == "same":
                    rep["rows_already_there"] += 1
                    continue
                {"kept_current": rep["settings_kept_current"], "upstash": rep["settings_from_upstash"],
                 "merged": rep["settings_merged"], "supabase": rep["settings_kept_supabase"]}[how].append(f)
                if merged != have[f]:
                    keep_old("fno:migration:replaced", k, f, have[f])
                    hset(k, f, merged)
        elif typ == "hash" and _is(k, OPEN_BOOKS):
            seen_open.add(k)
            have = sup_hash(k)
            for f, v in data.items():                                    # Upstash's stale open positions: parked, not reopened
                keep_old("fno:migration:stale_open", f"upstash:{k}", f, v)
            for f, v in have.items():                                    # the temporary account's open positions: parked too
                keep_old("fno:migration:stale_open", f"supabase:{k}", f, v)
                hdel(k, f)
            if data or have:
                rep["stale_open"].append({"key": k, "upstash": len(data), "supabase": len(have)})
        elif typ == "hash" and _is(k, CLOSED_BOOKS):
            have = sup_hash(k)
            for f, v in have.items():
                if f not in data:                                        # trades of the temporary Supabase account
                    keep_old("fno:migration:supabase_only", k, f, v)
                    hdel(k, f)
                    rep["supabase_only_trades"] += 1
            for f, v in data.items():
                if have.get(f) != v:
                    if f in have:
                        keep_old("fno:migration:replaced", k, f, have[f])
                    hset(k, f, v, exp_sql, exp_p)
                    rep["rows_copied"] += 1
                else:
                    rep["rows_already_there"] += 1
            rep["portfolio"][k] = len(data)
        elif typ == "hash":
            have = sup_hash(k)
            add = _is(k, ADD_STATS)
            done = set(pgkv.execute("SMEMBERS", "fno:migration:stats_added") or []) if add else set()
            for f, v in data.items():
                if f not in have:
                    rep["rows_copied"] += 1
                    hset(k, f, v, exp_sql, exp_p)
                    if add:
                        w(f"insert into {T} (k, f, v, kind) values ('fno:migration:stats_added', %s, '', 'S') on conflict (k, f) do nothing", (f"{k}|{f}",))
                elif add and have[f] != v and f"{k}|{f}" not in done:   # added once only (a re-run must not count twice)
                    w(f"insert into {T} (k, f, v, kind) values ('fno:migration:stats_added', %s, '', 'S') on conflict (k, f) do nothing", (f"{k}|{f}",))
                    rep["stats_added"] += 1
                    keep_old("fno:migration:replaced", k, f, have[f])
                    hset(k, f, add_stats(v, have[f]), exp_sql, exp_p)
                else:
                    rep["rows_already_there"] += 1
        elif typ == "string":
            if pgkv.execute("GET", k) is None:
                rep["rows_copied"] += 1
                w(f"insert into {T} (k, f, v, kind, exp) values (%s, '', %s, 's', {exp_sql}) on conflict (k, f) do update set v = excluded.v, kind = 's', exp = excluded.exp",
                  (k, data) + exp_p)
            else:
                rep["rows_already_there"] += 1
        elif typ in ("set", "zset"):
            kind = "S" if typ == "set" else "z"
            have = {r[0] for r in pgkv._q(f"select f from {T} where k = %s and kind = %s and (exp is null or exp > now())", (k, kind))}
            items = data.items() if typ == "zset" else [(m, None) for m in data]
            for m, sc in items:
                if m in have:
                    rep["rows_already_there"] += 1
                    continue
                rep["rows_copied"] += 1
                w(f"insert into {T} (k, f, v, kind, score, exp) values (%s, %s, %s, %s, %s, {exp_sql}) on conflict (k, f) do nothing",
                  (k, m, None if typ == "zset" else "", kind, sc) + exp_p)
        elif typ == "list":
            if pgkv.execute("LLEN", k):
                rep["lists_skipped_exist"].append(k)
            else:
                rep["rows_copied"] += len(data)
                for i, v in enumerate(data):
                    w(f"insert into {T} (k, f, v, kind, exp) values (%s, %s, %s, 'l', {exp_sql}) on conflict (k, f) do nothing",
                      (k, f"{i:012d}", v) + exp_p)
    # open books that exist only on Supabase (no Upstash key at all) also restart flat
    for k in [r[0] for r in pgkv._q(f"select distinct k from {T} where kind = 'h'")]:
        if k in seen_open or not _is(k, OPEN_BOOKS):
            continue
        have = sup_hash(k)
        for f, v in have.items():
            keep_old("fno:migration:stale_open", f"supabase:{k}", f, v)
            hdel(k, f)
        if have:
            rep["stale_open"].append({"key": k, "upstash": 0, "supabase": len(have)})
    if not dry_run:
        forget()
        # verification: every Upstash field / member / string that should be on Supabase now is
        for k in keys:
            typ = _upstash("TYPE", k)
            if (typ == "string" and k.startswith(SKIP_STRINGS)) or (typ == "hash" and _is(k, OPEN_BOOKS)):
                continue
            data = _ups_read(k, typ)
            if data is None:
                continue
            if typ in ("hash", "set", "zset"):
                have = set(r[0] for r in pgkv._q(f"select f from {T} where k = %s and (exp is null or exp > now())", (k,)))
                miss = [f for f in data if f not in have]
            elif typ == "string":
                miss = [] if pgkv.execute("GET", k) is not None else [""]
            else:
                miss = [] if (pgkv.execute("LLEN", k) or 0) >= len(data) else ["(list shorter)"]
            if miss:
                rep["missing_after"].append({"key": k, "missing": len(miss), "sample": miss[:3]})
        rep["verified"] = not rep["missing_after"]
    for x in ("settings_from_upstash", "settings_kept_supabase", "settings_merged", "settings_added", "settings_kept_current"):
        rep[x].sort()
    return rep


def start_migration(dry_run: bool = False) -> dict:
    """Run the copy in a background thread (it can take minutes); progress and the report are in MIG and in settings."""
    if MIG["running"]:
        return {"ok": True, "running": True, "progress": MIG["progress"]}
    MIG.update(running=True, started=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), progress="starting", result=None)

    def job():
        try:
            r = migrate_from_upstash(dry_run=dry_run)
        except Exception as e:
            r = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
        r["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        MIG.update(running=False, result=r, progress=None)
        print("[migration]", "preview" if dry_run else "copy", {k: r.get(k) for k in ("ok", "error", "keys", "rows_copied", "rows_already_there",
              "settings_from_upstash", "settings_merged", "settings_kept_supabase", "stale_open", "supabase_only_trades", "verified")}, "missing:", [x["key"] for x in r.get("missing_after") or []][:20], flush=True)
        try:
            put("upstash_migration_preview" if dry_run else "upstash_migration", r)
        except Exception:
            pass
    threading.Thread(target=job, name="upstash-migration", daemon=True).start()
    return {"ok": True, "running": True, "started": MIG["started"]}


def migration_status() -> dict:
    return {"running": MIG["running"], "progress": MIG["progress"], "started": MIG["started"],
            "last": MIG["result"] or get("upstash_migration"), "preview": get("upstash_migration_preview"),
            "upstash_configured": bool(C.REDIS_URL and C.REDIS_TOKEN), "auto_from": MIG_FROM}


def auto_migrate_if_due() -> str:
    """Called by the background runner about once an hour: on/after 1 Nov, once Upstash answers again, copy everything once."""
    from datetime import datetime, timezone
    if backend() != "supabase" or not (C.REDIS_URL and C.REDIS_TOKEN) or MIG["running"]:
        return "not applicable"
    if datetime.now(timezone.utc).isoformat() < MIG_FROM:
        return "waiting for 1 Nov"
    done = get("upstash_migration") or {}
    if done.get("ok") and done.get("verified"):
        return "done"
    try:
        _upstash("PING")
    except Exception as e:
        return f"Upstash not answering yet: {str(e)[:80]}"
    start_migration(dry_run=False)
    return "started"
