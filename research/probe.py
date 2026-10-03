import requests, urllib.parse
S = requests.Session(); S.headers.update({"Accept": "application/json"})
k = urllib.parse.quote("NSE_INDEX|Nifty 50", safe="")
tests = [("minutes/5", "2026-03-06", "2026-03-01"), ("minutes/5", "2026-02-27", "2026-02-20"), ("minutes/5", "2025-12-31", "2025-12-24"),
         ("minutes/5", "2025-06-30", "2025-06-23"), ("minutes/15", "2026-02-27", "2026-02-01"), ("minutes/15", "2025-10-31", "2025-10-01"),
         ("minutes/30", "2025-10-31", "2025-10-01"), ("minutes/1", "2025-10-07", "2025-10-01"), ("hours/1", "2025-10-31", "2025-07-01"),
         ("minutes/5", "2026-03-31", "2026-03-02")]
out = []
for unit, to, fr in tests:
    r = S.get(f"https://api.upstox.com/v3/historical-candle/{k}/{unit}/{to}/{fr}", timeout=30)
    n = len(r.json().get("data", {}).get("candles", [])) if r.status_code == 200 else 0
    out.append(f"{unit} {fr}..{to}: {r.status_code} n={n} {'' if r.status_code == 200 else r.text[:120]}")
open("research/probe_log.txt", "w").write("\n".join(out) + "\n"); print("\n".join(out))
