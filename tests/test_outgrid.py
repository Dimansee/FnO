"""Offline test: every-combination query + Pine for any outcome row. Run: python tests/test_outgrid.py"""
import os, sys, json, gzip, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__))); sys.path.insert(0, os.path.dirname(__file__))
import upstash_shim  # noqa: E402
_url, _tok = upstash_shim.start(port=6521)
os.environ.update(APP_PASSWORD="pw", SESSION_SECRET="s", CRON_SECRET="c", KV_REST_API_URL=_url, KV_REST_API_TOKEN=_tok)
from fno import outgrid as OG, market as D  # noqa: E402
D.lot_size = lambda s: {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}[s]
F = [{"name": "Lots / risk", "options": ["compound 1%", "compound 2%"], "changes": [{}, {"risk_pct": 2.0}]},
     {"name": "Exit", "options": ["current exits", "lock +1R at +2R"], "changes": [{}, {"exit": "lock_2r"}]},
     {"name": "Indices", "options": ["all three", "Nifty only"], "changes": [{}, {"indices": ["NIFTY"]}]}]
runs = ["200000_full", "200000_y2026", "20000_full", "20000_y2026"]
rows = []
for a in range(2):
    for b in range(2):
        for c in range(2):
            v = 1_000_000 + a * 500_000 - b * 100_000 - c * 300_000
            rows.append([a, b, c] + sum([[v, -20 - a * 10, 33.0, 1.4, 900] for _ in runs], []))
tmp = os.path.join(tempfile.mkdtemp(), "g.json.gz")
with gzip.open(tmp, "wt") as f:
    json.dump({"generated": "2026-10-26", "periods": {}, "runs": runs, "metrics": ["final", "max_dd_pct", "win_rate", "profit_factor", "trades"],
               "factors": F, "baseline": rows[0], "rows": rows, "notes": ["a", "b", "c"]}, f)
OG.PATH = tmp
m = OG.meta(); assert m["ok"] and m["count"] == 8 and m["baseline"]["changes"] == [], m
q = OG.query({}, "200000_full", "final"); assert q["total"] == 8 and q["rows"][0]["idx"] == [1, 0, 0], q["rows"][0]
q = OG.query({"1": 1}, "200000_full", "final"); assert q["total"] == 4 and all(r["idx"][1] == 1 for r in q["rows"])
q = OG.query({}, "200000_full", "final", max_dd=25); assert q["total"] == 4 and all(r["idx"][0] == 0 for r in q["rows"])
q = OG.query({}, "200000_full", "final", both_better=True); assert q["total"] == 4, q["total"]
p = OG.params([1, 1, 1]); assert p["risk_pct"] == 2.0 and p["exit"] == "lock_2r" and p["indices"] == ["NIFTY"] and p["dd_half"] is True
print("grid query OK")
import app as A  # noqa: E402
c = A.app.test_client(); c.post("/api/login", json={"password": "pw"})
r = c.post("/api/outcomes/pine", json={"idx": [1, 1, 1], "capital": 20000}).get_json()
assert len(r["scripts"]) == 1 and r["scripts"][0]["index"] == "NIFTY" and "riskPct   = input.float(2.0" in r["scripts"][0]["code"] and "initial_capital=20000" in r["scripts"][0]["code"], r.get("params")
r = c.post("/api/outcomes/pine", json={"changes": {"max_loss_lot": 2200, "otm": 2}}).get_json()
assert len(r["scripts"]) == 3 and "input.float(2200.0" in r["scripts"][0]["code"] and "input.int(2," in r["scripts"][0]["code"]
r = c.post("/api/outcomes/grid/query", json={"filters": {"2": 1}, "run": "20000_y2026", "sort": "max_dd_pct"}).get_json(); assert r["total"] == 4
print("endpoints OK")
print("ALL OUTGRID OK")
