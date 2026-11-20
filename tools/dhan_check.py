"""Run once with your Dhan details:  DHAN_CLIENT_ID=... DHAN_TOKEN=... python tools/dhan_check.py
Checks every call the app needs and prints PASS/FAIL. Nothing is traded."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fno.dhan import Dhan, SEC
from fno import config as C
cid, tok = os.environ.get("DHAN_CLIENT_ID"), os.environ.get("DHAN_TOKEN")
if not (cid and tok):
    sys.exit("Set DHAN_CLIENT_ID and DHAN_TOKEN first.")
d = Dhan(cid, tok)
def run(name, fn):
    try:
        r = fn(); print("PASS" if r else "FAIL", name, "" if r is True else r)
    except Exception as e:
        print("FAIL", name, str(e)[:200])
for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
    k = f"IDX_I|{SEC[sym]}"
    run(f"{sym} 1-min bars (first bar must be 09:15 IST)", lambda: (lambda df: (not df.empty) and f"first {df.index[0]:%H:%M}, rows {len(df)}, last close {df.close.iloc[-1]}")(d.candles1(k)))
    run(f"{sym} 5-min history", lambda: (lambda df: (not df.empty) and f"rows {len(df)} from {df.index[0]:%Y-%m-%d}")(d.candles(k, days=10)))
    run(f"{sym} expiries", lambda: (lambda e: bool(e[0]) and f"next {e[0][0]}")(d.expiries(k)))
    run(f"{sym} option chain", lambda: (lambda df: (not df.empty) and f"{len(df)} strikes; ATM-ish ce_ltp {df.iloc[len(df)//2].ce_ltp}, bid/ask {df.iloc[len(df)//2].ce_bid}/{df.iloc[len(df)//2].ce_ask}, key {df.iloc[len(df)//2].ce_key}")(d.chain(k, d.expiries(k)[0][0])))
run("quotes: 3 indices + VIX", lambda: (lambda q: len(q) == 4 and {k: v["ltp"] for k, v in q.items()})(d.quotes([f"IDX_I|{SEC[x]}" for x in ("NIFTY", "BANKNIFTY", "SENSEX", "INDIAVIX")])))
