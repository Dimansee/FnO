"""Offline test: chart-pattern detector (fno/patterns.py) and /api/gbook/patterns. Run: python tests/test_patterns.py"""
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(port=6424)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)
os.environ.pop("RENDER", None)

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fno import patterns as PT  # noqa: E402


def _series(points, per=10):
    """Piecewise-linear close path through `points`, with small candles around it."""
    c = []
    for (a, b) in zip(points[:-1], points[1:]):
        c += list(np.linspace(a, b, per, endpoint=False))
    c.append(points[-1])
    c = np.array(c)
    return c + 0.2, c - 0.2, c


def test_double_bottom_found_and_breaks():
    # fall, bottom 90, bounce 100 (neckline), bottom 90, rally through 100
    h, l, c = _series([120, 90, 100, 90, 115], per=12)
    a = np.full(len(c), 1.0)
    pats = PT.find(h, l, c, a, k=2.0, names=["double_bottom"])
    assert pats, "double bottom not found"
    P = pats[-1]
    assert P["side"] == 1 and abs(P["up"][1] - 100.2) < 0.5
    w = PT.walk(P, h, l, c, len(c))
    assert w["status"] in ("signal", "in play", "target hit") and w["side"] == 1


def test_no_lookahead_ready_after_last_pivot():
    h, l, c = _series([120, 90, 100, 90, 115], per=12)
    a = np.full(len(c), 1.0)
    for P in PT.find(h, l, c, a, k=2.0):
        assert P["ready"] > P["pivots"][-1][0]


def test_guide_covers_all_names():
    assert set(PT.NAMES) == set(PT.GUIDE)
    for g in PT.GUIDE.values():
        assert g["group"] in PT.GROUPS and g["bias"] in ("bull", "bear", "both")


def test_for_chart_shapes():
    rng = np.random.default_rng(1)
    c = 100 + np.cumsum(rng.normal(0, 1, 800))
    h, l = c + rng.uniform(0.2, 1.5, 800), c - rng.uniform(0.2, 1.5, 800)
    times = list(range(1_700_000_000, 1_700_000_000 + 800 * 3600, 3600))
    rows = PT.for_chart(times, c, h, l, c, k=2.0, keep=12)
    assert len(rows) <= 12
    for r in rows:
        assert r["name"] in PT.GUIDE and r["lines"]
        for ln in r["lines"]:
            assert ln[0]["time"] <= ln[1]["time"]


def test_patterns_endpoint():
    import app as A
    from fno import gbook as GB
    from fno import markets as MK
    idx = pd.date_range("2026-01-01", periods=600, freq="1h", tz="UTC")
    rng = np.random.default_rng(2)
    c = 100 + np.cumsum(rng.normal(0, 1, 600))
    df = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 1.0}, index=idx)
    MK.candles = lambda *a, **k: df
    out = GB.chart_patterns("CRYPTO", "BTCUSDT", "1h")
    assert "patterns" in out and out["guide"]["patterns"]
    g = {p["id"]: p for p in out["guide"]["patterns"]}
    assert g["sym_tri"]["tested"] and "4h" in g["sym_tri"]["tested"]["by_tf"]
    cl = A.app.test_client()
    assert cl.post("/api/login", json={"password": "pw"}).status_code == 200
    r = cl.get("/api/gbook/patterns?market=CRYPTO&symbol=BTCUSDT&tf=1h")
    assert r.status_code == 200 and "patterns" in r.get_json(), r.get_json()
    assert cl.get("/api/gbook/patterns?market=CRYPTO&symbol=NOPE&tf=1h").status_code == 400
    assert cl.get("/api/patterns/guide").status_code == 200


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f(); print("ok", k)
    print("all pattern tests passed")
