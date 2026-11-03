"""Backfill for round 76 (run by the backfill-history GitHub job; NSE / Yahoo are not reachable from the research sandbox).

Writes into <out> (research/data on the research-data branch):
  participant_oi_hist.csv.gz   NSE participant-wise open interest, one row per day x client type (Client, DII, FII, Pro, TOTAL),
                               every trading day since START (fao_participant_oi_DDMMYYYY.csv from nsearchives)
  global_long_1d.csv.gz        daily OHLC since START for ^DJI ^IXIC ^GSPC ^N225 ^HSI (Yahoo), column 'sym'
Resumable: days already in the participant file are skipped.
Usage: python research/fetch_backfill.py research/data [start=2022-12-01]
"""
import io, os, sys, time
from datetime import date, timedelta
import pandas as pd, requests

out = sys.argv[1] if len(sys.argv) > 1 else "research/data"
START = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2022, 12, 1)
NSE = requests.Session()
NSE.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36",
                    "Accept": "*/*", "Referer": "https://www.nseindia.com/"})


def participant():
    f = os.path.join(out, "participant_oi_hist.csv.gz")
    old = pd.read_csv(f) if os.path.exists(f) else pd.DataFrame()
    have = set(old["date"]) if len(old) else set()
    try:
        NSE.get("https://www.nseindia.com/", timeout=15)
    except Exception:
        pass
    rows, d, miss = [], START, 0
    while d <= date.today():
        if d.weekday() < 5 and d.isoformat() not in have:
            url = f"https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv"
            b = None
            for a in range(3):
                try:
                    r = NSE.get(url, timeout=20)
                    if r.status_code == 200:
                        b = r.content
                    break
                except Exception:
                    time.sleep(2 + a * 2)
            if b and b"Client" in b:
                t = pd.read_csv(io.BytesIO(b), skiprows=1)
                t.columns = [str(c).strip() for c in t.columns]
                t["Client Type"] = t["Client Type"].astype(str).str.strip()
                t.insert(0, "date", d.isoformat())
                rows.append(t)
            else:
                miss += 1
            time.sleep(0.35)
        d += timedelta(days=1)
    new = pd.concat([old] + rows, ignore_index=True) if rows else old
    if len(new):
        new = new.sort_values(["date", "Client Type"])
        new.to_csv(f, index=False, compression="gzip")
    print(f"participant OI: +{len(rows)} days, {miss} weekdays without a file (holidays), total {new['date'].nunique() if len(new) else 0}", flush=True)


def global_long():
    import yfinance as yf
    parts = []
    for s in ("^DJI", "^IXIC", "^GSPC", "^N225", "^HSI"):
        x = yf.download(s, start=START.isoformat(), interval="1d", auto_adjust=False, progress=False)
        if isinstance(x.columns, pd.MultiIndex):
            x.columns = x.columns.get_level_values(0)
        x = x.reset_index().rename(columns={"Date": "ts", "Open": "open", "High": "high", "Low": "low", "Close": "close"})
        x["ts"] = pd.to_datetime(x["ts"]).dt.strftime("%Y-%m-%d")
        x["sym"] = s
        parts.append(x[["ts", "sym", "open", "high", "low", "close"]])
        print(s, len(x), x.ts.min(), x.ts.max(), flush=True)
    pd.concat(parts).to_csv(os.path.join(out, "global_long_1d.csv.gz"), index=False, compression="gzip")


if __name__ == "__main__":
    os.makedirs(out, exist_ok=True)
    try:
        global_long()
    except Exception as e:
        print("global failed:", e, flush=True)
    participant()
