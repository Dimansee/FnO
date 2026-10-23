"""Long daily history of the three traded indices (Yahoo, max period) for multi-day studies. Runs on GitHub Actions.
Usage: python research/fetch_index_daily.py <out_dir>  ->  idx_daily_NIFTY.csv.gz, idx_daily_BANKNIFTY.csv.gz, idx_daily_SENSEX.csv.gz"""
import os, sys
import yfinance as yf
OUT = sys.argv[1] if len(sys.argv) > 1 else "research/data"
os.makedirs(OUT, exist_ok=True)
for name, tk in (("NIFTY", "^NSEI"), ("BANKNIFTY", "^NSEBANK"), ("SENSEX", "^BSESN")):
    df = yf.Ticker(tk).history(period="max", interval="1d", auto_adjust=False)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close"]].dropna()
    df = df[(df.high > df.low) | (df.open != df.close)]          # drop flat placeholder rows
    df.index = df.index.tz_localize(None) if df.index.tz is not None else df.index
    df.index.name = "ts"
    df.reset_index().to_csv(f"{OUT}/idx_daily_{name}.csv.gz", index=False)
    print(name, len(df), df.index[0].date(), df.index[-1].date())
