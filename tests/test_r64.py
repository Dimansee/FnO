"""Offline test: round-64 crypto book change (Donchian 55 4h retired, 2x leverage) and its switch. Run: python tests/test_r64.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(port=6425)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)
os.environ.pop("RENDER", None)

from fno import gautopilot as AP, gbook as GB, service as SV, store  # noqa: E402

ids = lambda m: {c["id"] for c in AP.candidates(m)}          # noqa: E731
# 1. default: Donchian 55 4h not traded on crypto, still known for exits; US / gold untouched
assert "s_a10002" not in ids("CRYPTO") and {"s_a10001", "s_a10003", "s_a10004"} <= ids("CRYPTO")
assert "s_a10002" in {s["id"] for s in AP.all_specs("CRYPTO")}
assert "s_a10011" in ids("US") and "s_a10022" in ids("GOLD")
# 2. leverage: 2x by default on crypto, 1x elsewhere; a stored 1x from before the change is lifted to 2x
assert GB.prefs("CRYPTO")["max_leverage"] == 2.0 and GB.prefs("US")["max_leverage"] == 1.0
store.put("g_prefs:CRYPTO", {"risk_pct": 1.0, "max_leverage": 1.0, "max_open": 4, "auto": True})
assert GB.prefs("CRYPTO")["max_leverage"] == 2.0
# 3. changing another setting does not freeze the 2x default into storage
GB.set_prefs("CRYPTO", {"max_open": 5})
assert (store.get("g_prefs:CRYPTO") or {}).get("max_leverage") == 1.0 and GB.prefs("CRYPTO")["max_leverage"] == 2.0
# 4. leverage chosen by the user after the change wins
GB.set_prefs("CRYPTO", {"max_leverage": 1})
assert GB.prefs("CRYPTO")["max_leverage"] == 1.0
GB.set_prefs("CRYPTO", {"max_leverage": 3})
assert GB.prefs("CRYPTO")["max_leverage"] == 3.0
store.put("g_prefs:CRYPTO", {})
# 5. switch it back in Research -> Recent changes
row = next(c for c in SV.changes() if c["id"] == "crypto_book_r64")
assert row["on"] is True and row["kind"] == "switch"
SV.revert_change("crypto_book_r64", False)
assert "s_a10002" in ids("CRYPTO") and GB.prefs("CRYPTO")["max_leverage"] == 1.0
SV.revert_change("crypto_book_r64", True)
assert "s_a10002" not in ids("CRYPTO") and GB.prefs("CRYPTO")["max_leverage"] == 2.0
# 6. the weekly re-test sees the changed rule list as stale (re-tests at once)
store.put("g_ap_qual:CRYPTO", {"t": 9e18, "ids": ["s_a10001", "s_a10002", "s_a10003", "s_a10004"], "rows": [{"id": "x"}], "ok": {}})
assert not AP.has_result("CRYPTO")
print("round-64 crypto book tests passed")
