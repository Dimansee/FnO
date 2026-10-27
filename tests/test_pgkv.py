"""fno/pgkv.py must answer every Redis command the app uses exactly like Redis (fakeredis) does.
Needs a Postgres: set TEST_DATABASE_URL (e.g. a local postgres) - skipped otherwise.
Run: TEST_DATABASE_URL=postgresql://... python tests/test_pgkv.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
URL = os.environ.get("TEST_DATABASE_URL")
if not URL:
    print("skip: TEST_DATABASE_URL not set"); sys.exit(0)
os.environ["DATABASE_URL"] = URL
import fakeredis
from fno import pgkv

pgkv._q(f"drop table if exists {pgkv.TABLE}", fetch="count"); pgkv._conn = None
R = fakeredis.FakeRedis(decode_responses=True)


def norm(x):
    if isinstance(x, (set, frozenset)):
        return sorted(x)
    if x == "OK":
        return 1
    if isinstance(x, bool):
        return int(x)
    if isinstance(x, list):
        return [norm(i) for i in x]
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return x


def both(*cmd, sort=False):
    a = norm(R.execute_command(*[str(c) for c in cmd]))
    b = norm(pgkv.execute(*cmd))
    if cmd[0] == "HGETALL":
        a = a if isinstance(a, dict) else dict(zip(a[::2], a[1::2])); b = dict(zip(b[::2], b[1::2]))
    if cmd[0] == "ZRANGE" and "WITHSCORES" in cmd:
        if a and isinstance(a[0], (list, tuple)):
            a = [y for pair in a for y in pair]
        a = [str(float(x)) if i % 2 else x for i, x in enumerate(a)]; b = [str(float(x)) if i % 2 else x for i, x in enumerate(b)]
    if sort and isinstance(a, list) and isinstance(b, list):
        a, b = sorted(a), sorted(b)
    assert a == b, f"{cmd}: redis {a!r} vs pg {b!r}"
    return b


checks = [
    ("HSET", "h1", "a", "1", "b", "2"), ("HSET", "h1", "a", "9"), ("HGET", "h1", "a"), ("HGET", "h1", "zz"),
    ("HMGET", "h1", "a", "x", "b"), ("HGETALL", "h1"), ("HLEN", "h1"), ("HSETNX", "h1", "a", "7"), ("HSETNX", "h1", "c", "3"),
    ("HDEL", "h1", "b", "nope"), ("HEXISTS", "h1", "a"), ("HEXISTS", "h1", "b"),
    ("SET", "s1", "v"), ("GET", "s1"), ("SET", "s1", "w", "NX"), ("SET", "s2", "x", "NX", "EX", 100), ("GET", "s2"),
    ("SET", "s3", "y", "EX", 50), ("GET", "s3"), ("GET", "missing"), ("TYPE", "h1"), ("TYPE", "s1"), ("TYPE", "nokey"),
    ("SADD", "S1", "a", "b", "a"), ("SADD", "S1", "b", "c"), ("SMEMBERS", "S1"), ("SREM", "S1", "a"), ("SMEMBERS", "S1"),
    ("ZADD", "z1", 3, "c", 1, "a", 2, "b"), ("ZADD", "z1", 5, "a"), ("ZRANGE", "z1", 0, -1), ("ZREVRANGE", "z1", 0, 1),
    ("ZRANGE", "z1", 0, -1, "WITHSCORES"), ("ZCARD", "z1"), ("ZREM", "z1", "b", "x"), ("ZRANGE", "z1", 0, 0),
    ("RPUSH", "l1", "x", "y"), ("RPUSH", "l1", "z"), ("LRANGE", "l1", 0, -1), ("LRANGE", "l1", 1, 1), ("LLEN", "l1"),
    ("EXPIRE", "h1", 100), ("EXPIRE", "nokey", 100), ("DEL", "s1", "S1", "nokey"), ("GET", "s1"), ("SMEMBERS", "S1"),
    ("HKEYS", "h1"), ("INCR", "cnt"), ("INCRBY", "cnt", 5),
]
for c in checks:
    both(*c, sort=c[0] in ("HKEYS", "HGETALL", "SMEMBERS"))
assert pgkv.execute("PING") == "PONG"
assert sorted(pgkv.execute("SCAN", "0", "MATCH", "h*", "COUNT", 500)[1]) == ["h1"]
# expiry
pgkv.execute("SET", "e1", "v", "EX", 1); pgkv.execute("HSET", "eh", "f", "1"); pgkv.execute("EXPIRE", "eh", 1)
time.sleep(1.6)
assert pgkv.execute("GET", "e1") is None and pgkv.execute("HGET", "eh", "f") is None and pgkv.execute("HGETALL", "eh") == []
assert pgkv.execute("SET", "e1", "new", "NX") == "OK", "SET NX on an expired key must succeed"
# atomic helpers
pgkv.execute("HSET", "pos", "p1", "a")
assert pgkv.update_if_exists("pos", "p1", "b") == 0 and pgkv.execute("HGET", "pos", "p1") == "b"
assert pgkv.update_if_exists("pos", "gone", "x") == -1 and pgkv.execute("HGET", "pos", "gone") is None
assert pgkv.take("pos", "p1") == "b" and pgkv.take("pos", "p1") is None
print("pgkv: all", len(checks), "Redis comparisons + expiry + atomic ops OK")
