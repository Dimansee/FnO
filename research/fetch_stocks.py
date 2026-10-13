"""Daily history for NSE stocks (runs on GitHub Actions, open internet).

1. Nifty 500 (NSE list), 6 years, -> research/data/stocks_1d.csv.gz + stocks_list.csv   (the research universe, committed)
2. Every NSE cash stock you can buy in a normal demat account - the EQ series of NSE's EQUITY_L list, which excludes SME
   platforms, trade-to-trade (BE/BZ) and suspended names - 1 year, -> research/work/all_1y.csv.gz + all_list.csv (not committed;
   stock_market.py turns it into the app's market view). Index membership (Nifty 50/100/200/500) is recorded for filtering.
"""
import io, os, time
import pandas as pd, requests, yfinance as yf

OUT, WORK = "research/data", "research/work"
os.makedirs(OUT, exist_ok=True); os.makedirs(WORK, exist_ok=True)
S = requests.Session(); S.headers.update({"User-Agent": "Mozilla/5.0"})


def nse_csv(path):
    for host in ("https://archives.nseindia.com", "https://nsearchives.nseindia.com"):
        try:
            r = S.get(host + path, timeout=30); r.raise_for_status()
            return pd.read_csv(io.StringIO(r.text))
        except Exception as e:
            print("fetch failed", host + path, e)
    return None


def download(syms, period):
    frames = []
    for i in range(0, len(syms), 60):
        chunk = syms[i:i + 60]
        try:
            df = yf.download([s + ".NS" for s in chunk], period=period, interval="1d", auto_adjust=False, group_by="ticker", threads=True, progress=False)
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
        time.sleep(0.5)
    return pd.concat(frames) if frames else pd.DataFrame()


# 1. Nifty 500, 6 years (research universe)
idx = {n: nse_csv(f"/content/indices/ind_nifty{n}list.csv") for n in (50, 100, 200, 500)}
lst = idx[500]
if lst is None:
    raise SystemExit("no Nifty 500 list")
lst.to_csv(f"{OUT}/stocks_list.csv", index=False)
syms500 = [str(s) for s in lst["Symbol"]]
out = download(syms500, "6y")
out.to_csv(f"{OUT}/stocks_1d.csv.gz", index=False, compression="gzip")
print("Nifty 500:", out.sym.nunique(), "stocks", len(out), "rows", out.date.min(), out.date.max())

# 1b. F&O-eligible symbols (for the downside watch: puts / futures are the only way to act on a fall)
try:
    r = S.get("https://archives.nseindia.com/content/fo/fo_mktlots.csv", timeout=30); r.raise_for_status()
    fo = pd.read_csv(io.StringIO(r.text)); fo.columns = [c.strip() for c in fo.columns]
    col = next(c for c in fo.columns if c.upper().startswith("SYMBOL"))
    pd.DataFrame({"Symbol": fo[col].astype(str).str.strip()}).drop_duplicates().to_csv(f"{OUT}/fo_list.csv", index=False)
    print("F&O list:", fo[col].nunique())
except Exception as e:
    print("F&O list failed:", e)

# 2. all investable NSE cash stocks (EQ series), 1 year
eq = nse_csv("/content/equities/EQUITY_L.csv")
if eq is not None:
    eq.columns = [c.strip() for c in eq.columns]
    eq = eq[eq[" SERIES"].str.strip() == "EQ"] if " SERIES" in eq.columns else eq[eq["SERIES"].str.strip() == "EQ"]
    eq = eq.rename(columns=lambda c: c.strip())
    members = {}
    for n, df in idx.items():
        if df is not None:
            for s in df["Symbol"]:
                members.setdefault(str(s), []).append(f"N{n}")
    ind500 = dict(zip(lst["Symbol"].astype(str), lst["Industry"].astype(str)))
    all_list = pd.DataFrame({"Symbol": eq["SYMBOL"].astype(str), "Company Name": eq["NAME OF COMPANY"].astype(str),
                             "Listed": eq["DATE OF LISTING"].astype(str) if "DATE OF LISTING" in eq.columns else "",
                             "Industry": [ind500.get(s, "") for s in eq["SYMBOL"].astype(str)],
                             "Indices": [",".join(members.get(s, [])) for s in eq["SYMBOL"].astype(str)]})
    all_list.to_csv(f"{WORK}/all_list.csv", index=False)
    allsyms = sorted(set(all_list["Symbol"]) - set(syms500))
    rest = download(allsyms, "1y")
    recent = out[out["date"] >= out["date"].max() - pd.Timedelta(days=400)]
    allx = pd.concat([recent, rest]) if len(rest) else recent
    allx.to_csv(f"{WORK}/all_1y.csv.gz", index=False, compression="gzip")
    print("all EQ:", allx.sym.nunique(), "stocks", len(allx), "rows")
else:
    print("EQUITY_L unavailable - market view will use the Nifty 500 only")
