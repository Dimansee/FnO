"""Upstash -> Supabase copy follows the 31 Oct 2026 rules (portfolio from Upstash, newer rules / research / learning kept).
Needs a Postgres: TEST_DATABASE_URL=postgresql://... python tests/test_migration.py   (skipped otherwise)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
URL = os.environ.get("TEST_DATABASE_URL")
if not URL:
    print("skip: TEST_DATABASE_URL not set"); sys.exit(0)
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start()
os.environ.update(DATABASE_URL=URL, KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok, SESSION_SECRET="s")
from fno import pgkv, store  # noqa: E402

pgkv._q(f"drop table if exists {pgkv.TABLE}", fetch="count"); pgkv._reset()
U = upstash_shim.R
U.flushall()
J = lambda o: json.dumps(o)  # noqa: E731

# ---- Upstash (the old, real account) ----
U.hset("fno:settings", mapping={
    "account": J({"capital_start": 200000, "created": "2026-09-01"}),
    "prefs": J({"autopilot": True, "risk_pct": 1.0, "strategies": ["noise", "camarilla"], "old_only": 1}),
    "scalp": J({"capital": 50000, "sizing": "max"}),
    "learn_overlay": J({"rules": [{"id": "v1", "kind": "veto"}, {"id": "v2", "kind": "size"}]}),
    "custom_strategies": J([{"id": "a", "name": "old A"}, {"id": "b", "name": "B"}]),
    "g_account:CRYPTO": J({"capital_start": 100000}),
    "g_lab_started": J("2026-10-01"),
    "upstox": J({"token": "OLD"}),
    "only_old": J({"x": 1}),
})
U.hset("fno:trades", mapping={"t1": J({"id": "t1", "pnl": 1000}), "t2": J({"id": "t2", "pnl": -400})})
U.hset("fno:scalp:trades", mapping={"s1": J({"id": "s1", "pnl": 50})})
U.hset("fno:positions", mapping={"p_old": J({"id": "p_old", "opened": "2026-10-28T10:00"})})
U.hset("fno:g:pos:CRYPTO", mapping={"g_old": J({"id": "g_old"})})
U.hset("fno:ls:research", mapping={"r_old": J({"id": "r_old"})})
U.hset("fno:ls:st", mapping={"S|IN|noise|all": J({"n": 10, "s": 2.0, "r": [0.2] * 10}), "S|IN|cam|all": J({"n": 3, "s": 1.0, "r": [1, 0, 0]})})
U.zadd("fno:ls:lessons:idx", {"l_old": 1.0})
U.sadd("fno:jr:days", "2026-10-27")
U.hset("fno:jr:2026-10-27", mapping={"reco:x": J({"id": "x"})})
U.set("fno:last_tick", "2026-10-28T10:00")

# ---- Supabase (temporary account since 28 Oct) ----
store.hset("fno:settings", "account", J({"capital_start": 20000, "created": "2026-10-28"}))
store.hset("fno:settings", "prefs", J({"autopilot": True, "risk_pct": 2.0, "strategies": ["noise", "camarilla", "trend"]}))
store.hset("fno:settings", "learn_overlay", J({"rules": [{"id": "v2", "kind": "size", "new": 1}, {"id": "v3", "kind": "veto"}]}))
store.hset("fno:settings", "custom_strategies", J([{"id": "a", "name": "new A"}, {"id": "c", "name": "C"}]))
store.hset("fno:settings", "g_account:CRYPTO", J({"capital_start": 5000}))
store.hset("fno:settings", "upstox", J({"token": "NEW"}))
store.hset("fno:trades", "t_new", J({"id": "t_new", "pnl": -900}))
store.hset("fno:positions", "p_new", J({"id": "p_new"}))
store.hset("fno:scalp:positions", "sp_new", J({"id": "sp_new"}))
store.hset("fno:ls:research", "r_new", J({"id": "r_new"}))
store.hset("fno:ls:st", "S|IN|noise|all", J({"n": 2, "s": -1.0, "r": [-0.5, -0.5]}))
store._cmd("ZADD", "fno:ls:lessons:idx", 2.0, "l_new")
store._cmd("SADD", "fno:jr:days", "2026-10-29")

pv = store.migrate_from_upstash(dry_run=True)
assert pv["ok"] and pv["dry_run"], pv
assert store.get("account")["capital_start"] == 20000, "dry run changed data"
r = store.migrate_from_upstash()
assert r["ok"] and r["verified"], r
store.forget()
S = lambda f: store.get(f)  # noqa: E731

# portfolio: Upstash
assert S("account")["capital_start"] == 200000
assert S("g_account:CRYPTO")["capital_start"] == 100000
assert sorted(store.hvals("fno:trades") and [json.loads(v)["id"] for v in store.hvals("fno:trades")]) == ["t1", "t2"]
assert [json.loads(v)["id"] for v in store.hvals("fno:scalp:trades")] == ["s1"]
assert r["supabase_only_trades"] == 1
parked = dict(zip(*[iter(pgkv.execute("HGETALL", "fno:migration:supabase_only"))] * 2))
assert "fno:trades|t_new" in parked
# books start flat; stale open positions parked
assert store.hvals("fno:positions") == [] and store.hvals("fno:scalp:positions") == [] and store.hvals("fno:g:pos:CRYPTO") == []
stale = pgkv.execute("HKEYS", "fno:migration:stale_open")
assert {"upstash:fno:positions|p_old", "supabase:fno:positions|p_new", "supabase:fno:scalp:positions|sp_new", "upstash:fno:g:pos:CRYPTO|g_old"} <= set(stale), stale
# rules: Supabase on top, merged
pf = S("prefs")
assert pf["risk_pct"] == 2.0 and "trend" in pf["strategies"] and pf["old_only"] == 1, pf
ov = {x["id"]: x for x in S("learn_overlay")["rules"]}
assert set(ov) == {"v1", "v2", "v3"} and ov["v2"].get("new") == 1
cs = {x["id"]: x["name"] for x in S("custom_strategies")}
assert cs == {"a": "new A", "b": "B", "c": "C"}, cs
assert S("scalp")["capital"] == 50000 and S("only_old") == {"x": 1}            # only on Upstash -> copied
assert S("g_lab_started") == "2026-10-01"
assert S("upstox")["token"] == "NEW"                                          # live login kept
# learning: union + evidence added
assert set(json.loads(v)["id"] for v in store.hvals("fno:ls:research")) == {"r_old", "r_new"}
st = json.loads(pgkv.execute("HGET", "fno:ls:st", "S|IN|noise|all"))
assert st["n"] == 12 and abs(st["s"] - 1.0) < 1e-9 and len(st["r"]) == 12, st
assert json.loads(pgkv.execute("HGET", "fno:ls:st", "S|IN|cam|all"))["n"] == 3
assert set(pgkv.execute("ZRANGE", "fno:ls:lessons:idx", 0, -1)) == {"l_old", "l_new"}
assert set(pgkv.execute("SMEMBERS", "fno:jr:days")) == {"2026-10-27", "2026-10-29"}
assert pgkv.execute("HGET", "fno:jr:2026-10-27", "reco:x")
# replaced values kept aside
rep = pgkv.execute("HKEYS", "fno:migration:replaced")
assert "fno:settings|account" in rep and "fno:settings|prefs" in rep
# running again changes nothing important
r2 = store.migrate_from_upstash()
assert r2["ok"] and r2["verified"] and r2["supabase_only_trades"] == 0
assert json.loads(pgkv.execute("HGET", "fno:ls:st", "S|IN|noise|all"))["n"] == 12, "stats double-added on a re-run"
print("test_migration: all OK")
