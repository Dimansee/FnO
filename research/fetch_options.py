"""Real option prices for the research (runs on GitHub Actions).

1. NSE F&O bhavcopy (official end-of-day prices of every option, free) for every trading
   day 2023-01 -> today: Nifty & Bank Nifty options, nearest 3 expiries, strikes within +-6%.
2. Upstox public 5-minute candles of every Nifty / Bank Nifty option contract that is
   still listed (expired contracts need the paid Upstox Plus plan), strikes within +-6%.
"""
import gzip, io, json, os, time, urllib.parse, zipfile
from datetime import date, datetime, timedelta

import pandas as pd
import requests

OUT = "research/data"
os.makedirs(OUT, exist_ok=True)
LOG = []
def L(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); LOG.append(s)

UND = {"NIFTY": pd.read_csv(f"{OUT}/NIFTY_1d.csv.gz", parse_dates=["ts"]).set_index("ts")["close"],
       "BANKNIFTY": pd.read_csv(f"{OUT}/BANKNIFTY_1d.csv.gz", parse_dates=["ts"]).set_index("ts")["close"]}
WIDTH = 0.06

# ------------------------------------------------------------------ 1. NSE bhavcopy
NSE = requests.Session()
NSE.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
                    "Accept": "*/*", "Referer": "https://www.nseindia.com/"})
try:
    NSE.get("https://www.nseindia.com/", timeout=15)
except Exception as e:
    L("nse home", e)


def bhav(d: date):
    if d >= date(2024, 7, 8):
        url = f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    else:
        mon = d.strftime("%b").upper()
        url = f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{d.year}/{mon}/fo{d:%d}{mon}{d.year}bhav.csv.zip"
    for attempt in range(3):
        try:
            r = NSE.get(url, timeout=30)
        except Exception as e:
            r = None; err = str(e)
        if r is not None and r.status_code == 200 and r.content[:2] == b"PK":
            break
        time.sleep(1 + attempt * 2)
    else:
        return None, (r.status_code if r is not None else err)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    df = pd.read_csv(z.open(z.namelist()[0]))
    if "TckrSymb" in df.columns:            # UDiFF format
        df = df[df["FinInstrmTp"].isin(["IDO"]) & df["TckrSymb"].isin(["NIFTY", "BANKNIFTY"])]
        out = pd.DataFrame({"date": d, "sym": df["TckrSymb"], "expiry": pd.to_datetime(df["XpryDt"]).dt.date,
                            "strike": df["StrkPric"].astype(float), "opt": df["OptnTp"], "open": df["OpnPric"],
                            "high": df["HghPric"], "low": df["LwPric"], "close": df["ClsPric"], "settle": df["SttlmPric"],
                            "oi": df["OpnIntrst"], "volume": df["TtlTradgVol"], "und": df.get("UndrlygPric")})
    else:
        df.columns = [c.strip() for c in df.columns]
        df = df[(df["INSTRUMENT"] == "OPTIDX") & df["SYMBOL"].isin(["NIFTY", "BANKNIFTY"])]
        out = pd.DataFrame({"date": d, "sym": df["SYMBOL"], "expiry": pd.to_datetime(df["EXPIRY_DT"], format="%d-%b-%Y").dt.date,
                            "strike": df["STRIKE_PR"].astype(float), "opt": df["OPTION_TYP"], "open": df["OPEN"],
                            "high": df["HIGH"], "low": df["LOW"], "close": df["CLOSE"], "settle": df["SETTLE_PR"],
                            "oi": df["OPEN_INT"], "volume": df["CONTRACTS"], "und": None})
    keep = []
    for s, g in out.groupby("sym"):
        u = UND[s][UND[s].index.date == d]
        spot = float(u.iloc[0]) if len(u) else float(g["strike"].median())
        exps = sorted(e for e in g["expiry"].unique() if e >= d)[:3]
        keep.append(g[g["expiry"].isin(exps) & ((g["strike"] / spot - 1).abs() <= WIDTH)])
    return pd.concat(keep), None


days = [d.date() for d in UND["NIFTY"].index if d.date() >= date(2023, 1, 2)]
frames, fails = [], 0
for i, d in enumerate(days):
    f, err = bhav(d)
    if f is None:
        fails += 1
        if fails <= 10 or fails % 50 == 0:
            L("bhavcopy fail", d, err)
        if i == 5 and fails == 6:
            L("NSE archives unreachable from here - skipping bhavcopy"); break
    else:
        frames.append(f)
    time.sleep(0.25)
if frames:
    b = pd.concat(frames)
    b.to_csv(f"{OUT}/OPT_bhavcopy.csv.gz", index=False, compression="gzip")
    L(f"bhavcopy: {len(b)} rows, {b['date'].nunique()} days, fails {fails}")

# ------------------------------------------------------------------ 2. Upstox live-contract 5m candles
S = requests.Session(); S.headers.update({"Accept": "application/json"})
raw = requests.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz", timeout=60).content
master = json.loads(gzip.decompress(raw))
spot = {"NIFTY": float(UND["NIFTY"].iloc[-1]), "BANKNIFTY": float(UND["BANKNIFTY"].iloc[-1])}
contracts = [m for m in master if m.get("segment") == "NSE_FO" and m.get("instrument_type") in ("CE", "PE")
             and m.get("underlying_symbol") in spot and abs(float(m["strike_price"]) / spot[m["underlying_symbol"]] - 1) <= WIDTH]
exp_by = {}
for m in contracts:
    exp_by.setdefault(m["underlying_symbol"], set()).add(m["expiry"])
nearest = {s: sorted(v)[:5] for s, v in exp_by.items()}
contracts = [m for m in contracts if m["expiry"] in nearest[m["underlying_symbol"]]]
L("live contracts to fetch:", len(contracts), {s: [datetime.fromtimestamp(e / 1000).date().isoformat() for e in v] for s, v in nearest.items()})
rows = []
today = date.today()
for n, m in enumerate(contracts):
    key = urllib.parse.quote(m["instrument_key"], safe="")
    y, mo = 2026, 5
    while (y, mo) <= (today.year, today.month):
        fr = date(y, mo, 1)
        to = min(date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1), today)
        for attempt in range(4):
            r = S.get(f"https://api.upstox.com/v3/historical-candle/{key}/minutes/5/{to}/{fr}", timeout=30)
            if r.status_code == 429:
                time.sleep(2 + attempt * 2); continue
            break
        if r.status_code == 200:
            for c in r.json().get("data", {}).get("candles", []):
                rows.append((c[0], m["underlying_symbol"], datetime.fromtimestamp(m["expiry"] / 1000).date().isoformat(),
                             float(m["strike_price"]), m["instrument_type"], c[1], c[2], c[3], c[4], c[5], c[6] if len(c) > 6 else None))
        elif n < 3:
            L("upstox fail", m["instrument_key"], fr, r.status_code, r.text[:120])
        y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
        time.sleep(0.25)
    if n % 100 == 0:
        L("contracts done", n, "rows", len(rows))
if rows:
    o = pd.DataFrame(rows, columns=["ts", "sym", "expiry", "strike", "opt", "open", "high", "low", "close", "volume", "oi"])
    o["ts"] = pd.to_datetime(o["ts"]).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    o.to_csv(f"{OUT}/OPT_live5m.csv.gz", index=False, compression="gzip")
    L(f"live option candles: {len(o)} rows, {o['ts'].min()} -> {o['ts'].max()}")
# probe the paid expired-contract API (expect: needs Upstox Plus)
r = S.get("https://api.upstox.com/v2/expired-instruments/expiries?instrument_key=" + urllib.parse.quote("NSE_INDEX|Nifty 50", safe=""), timeout=30)
L("expired-instruments API without login:", r.status_code, r.text[:150])
open("research/options_log.txt", "w").write("\n".join(LOG) + "\n")
