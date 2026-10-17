"""One-off / on-demand: 6 years of daily history for EVERY investable NSE cash stock (EQ series of EQUITY_L), not only the
Nifty 500 -> research/work/all_6y.csv.gz, plus NSE's current price bands (sec_list.csv) -> research/work/price_bands.csv.
Used by all_stocks_research.py to check whether the stock rules hold outside the Nifty 500.
Survivorship: EQUITY_L lists today's companies, so stocks delisted since 2020 are missing (bigger bias in small caps).
"""
import io, os, time
import pandas as pd, requests, yfinance as yf

WORK = "research/work"; os.makedirs(WORK, exist_ok=True)
S = requests.Session(); S.headers.update({"User-Agent": "Mozilla/5.0"})


def nse_csv(path):
    for host in ("https://archives.nseindia.com", "https://nsearchives.nseindia.com"):
        try:
            r = S.get(host + path, timeout=30); r.raise_for_status()
            return pd.read_csv(io.StringIO(r.text))
        except Exception as e:
            print("fetch failed", host + path, e)
    return None


eq = nse_csv("/content/equities/EQUITY_L.csv")
eq.columns = [c.strip() for c in eq.columns]
eq = eq[eq["SERIES"].astype(str).str.strip() == "EQ"]
idx = {n: nse_csv(f"/content/indices/ind_nifty{n}list.csv") for n in (50, 100, 200, 500)}
members = {}
for n, df in idx.items():
    if df is not None:
        for s in df["Symbol"]:
            members.setdefault(str(s), []).append(f"N{n}")
lst = pd.DataFrame({"Symbol": eq["SYMBOL"].astype(str).str.strip(), "Company Name": eq["NAME OF COMPANY"].astype(str),
                    "Indices": [",".join(members.get(str(s).strip(), [])) for s in eq["SYMBOL"]]})
lst.to_csv(f"{WORK}/all_list.csv", index=False)
bands = nse_csv("/content/equities/sec_list.csv")
if bands is not None:
    bands.columns = [c.strip() for c in bands.columns]
    bands.to_csv(f"{WORK}/price_bands.csv", index=False)
    print("price bands:", len(bands))

syms = list(lst["Symbol"]); frames = []
for i in range(0, len(syms), 60):
    chunk = syms[i:i + 60]
    for attempt in range(2):
        try:
            df = yf.download([s + ".NS" for s in chunk], period="6y", interval="1d", auto_adjust=False, group_by="ticker", threads=True, progress=False)
            break
        except Exception as e:
            print("chunk failed", i, e); time.sleep(5); df = None
    if df is None:
        continue
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
    time.sleep(0.5)
out = pd.concat(frames)
out.to_csv(f"{WORK}/all_6y.csv.gz", index=False, compression="gzip")
print("all EQ 6y:", out.sym.nunique(), "stocks", len(out), "rows", out.date.min(), out.date.max())
