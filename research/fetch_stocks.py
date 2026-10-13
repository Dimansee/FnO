"""Daily history for NSE stocks (Nifty 500 list from NSE archives, yfinance .NS tickers), for swing / positional
stock research. Runs on GitHub Actions (open internet). Output: research/data/stocks_1d.csv.gz + stocks_list.csv"""
import io, os, time
import pandas as pd, requests, yfinance as yf

OUT = "research/data"; os.makedirs(OUT, exist_ok=True)
S = requests.Session(); S.headers.update({"User-Agent": "Mozilla/5.0"})
lst = None
for url in ("https://archives.nseindia.com/content/indices/ind_nifty500list.csv", "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"):
    try:
        r = S.get(url, timeout=30); r.raise_for_status(); lst = pd.read_csv(io.StringIO(r.text)); break
    except Exception as e:
        print("list fetch failed", url, e)
if lst is None:
    raise SystemExit("no constituent list")
lst.to_csv(f"{OUT}/stocks_list.csv", index=False)
syms = [s for s in lst["Symbol"].astype(str) if s.isalnum() or "-" in s or "&" in s]
print(len(syms), "symbols")
frames = []
for i in range(0, len(syms), 50):
    chunk = syms[i:i + 50]
    try:
        df = yf.download([s + ".NS" for s in chunk], period="6y", interval="1d", auto_adjust=False, group_by="ticker", threads=True, progress=False)
    except Exception as e:
        print("chunk failed", i, e); continue
    for s in chunk:
        try:
            x = df[s + ".NS"].dropna(subset=["Close"]).copy()
        except Exception:
            continue
        if x.empty:
            continue
        x = x.rename(columns=str.lower)[["open", "high", "low", "close", "adj close", "volume"]]
        x.insert(0, "sym", s); x.index.name = "date"
        frames.append(x.reset_index())
    time.sleep(1)
out = pd.concat(frames)
out.to_csv(f"{OUT}/stocks_1d.csv.gz", index=False, compression="gzip")
print(out.sym.nunique(), "stocks", len(out), "rows", out.date.min(), out.date.max())
