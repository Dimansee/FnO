"""Offline test: Upstox 429 -> cooldown, friendly message, last good candles kept; finished days fetched once. Run: python tests/test_ratelimit.py"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fno import brokers as B, market as M


class R:
    def __init__(self, code, data=None, text=""):
        self.status_code, self._d, self.text = code, data, text

    def json(self):
        return {"data": self._d}


calls = []
mode = {"code": 200}


def fake_get(url, params=None, timeout=None):
    calls.append(url)
    if mode["code"] != 200:
        return R(mode["code"], text='{"title":"Error 1015: You are being rate limited"}')
    return R(200, {"candles": [["2026-10-23T09:15:00+05:30", 1, 2, 0.5, 1.5, 10]]})


u = B.Upstox("tok"); u.s.get = fake_get
B.UPSTOX_GAP = 0
df = u.candles("NSE_INDEX|Nifty 50"); n1 = len(calls)
u.candles("NSE_INDEX|Nifty 50"); n2 = len(calls)
assert not df.empty and n2 - n1 == 1, (n1, n2)          # second call: only today's intraday request, history from cache
print("history cached OK:", n1, "calls first time, 1 after")

mode["code"] = 429
try:
    u.candles1("NSE_INDEX|Nifty 50"); raise SystemExit("no error")
except B.RateLimited:
    pass
before = len(calls)
try:
    u.candles1("NSE_INDEX|Nifty 50"); raise SystemExit("no error")
except B.RateLimited:
    pass
assert len(calls) == before and B.upstox_cooldown_left() > 100   # during the cooldown nothing is sent
print("cooldown OK")

m = M.Market.__new__(M.Market); m.errors = []; m.br = u
M._cache["c:upstox:NIFTY"] = (time.time() - 100, df)              # last good candles, past the 25 s TTL
m.key = lambda s: "NSE_INDEX|Nifty 50"; m._fill_gaps = lambda d, s: d
out = m.candles("NIFTY")
assert out is df and len(m.errors) == 1 and "limiting requests" in m.errors[0] and "429" not in m.errors[0], m.errors
print("stale fallback + friendly message OK:", m.errors[0])
print("ALL RATE LIMIT OK")
