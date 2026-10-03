"""Nightly market recorder (GitHub Actions, free, no broker login).

For each trading day not yet saved (looks back RECORD_LOOKBACK calendar days), writes
research/data/live/<YYYY-MM-DD>/:
  candles_1m.csv.gz   1-minute OHLCV + open interest from Upstox's public API:
                      Nifty, Bank Nifty, Fin Nifty, India VIX, the near-month futures, and every
                      option of the nearest 2 expiries within +-STRIKE_WIDTH of the day's price
  bhav_fo.csv.gz      NSE end-of-day prices / OI / settlement of all their index options & futures
  participant_oi.csv  NSE: open interest held by FII / DII / Pro / Client (futures & options, long/short)
  participant_vol.csv NSE: the same split for traded volume
  snapshots.json.gz   the app's 5-minute option-chain snapshots (bid/ask, OI, IV) from Redis
  manifest.json       what was saved, row counts and errors

Why: expired options' intraday prices are only sold with paid plans, so the only free way to
study real premiums, spreads, OI build-up and expiry-day decay is to save them while they exist.

Usage: python research/record_day.py [YYYY-MM-DD ...]   (no args = catch up on recent days)
"""
import gzip, io, json, os, sys, time, urllib.parse, zipfile
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

ROOT = "research/data/live"
APP = os.environ.get("FNO_APP_URL", "https://fno-trainer.vercel.app")
LOOKBACK = int(os.environ.get("RECORD_LOOKBACK", "7"))
STRIKE_WIDTH = 0.04
INDEX = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank", "FINNIFTY": "NSE_INDEX|Nifty Fin Service"}
VIX = "NSE_INDEX|India VIX"
IST = timezone(timedelta(hours=5, minutes=30))
UP = requests.Session(); UP.headers.update({"Accept": "application/json"})
NSE = requests.Session()
NSE.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36",
                    "Accept": "*/*", "Referer": "https://www.nseindia.com/"})


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- Upstox public candles
def candles(key, d: date):
    """1-minute candles of one instrument for day d: [ts, o, h, l, c, vol, oi]."""
    k = urllib.parse.quote(key, safe="")
    urls = [f"https://api.upstox.com/v3/historical-candle/{k}/minutes/1/{d}/{d}"]
    if d == datetime.now(IST).date():
        urls.append(f"https://api.upstox.com/v3/historical-candle/intraday/{k}/minutes/1")
    err = "no data"
    for url in urls:
        for attempt in range(4):
            try:
                r = UP.get(url, timeout=30)
            except Exception:
                time.sleep(2); continue
            if r.status_code == 429:
                time.sleep(2 + attempt * 3); continue
            break
        else:
            continue
        if r.status_code == 200:
            cs = [c for c in (r.json().get("data") or {}).get("candles") or [] if c[0][:10] == d.isoformat()]
            if cs:
                return cs, None
        err = f"{r.status_code} {r.text[:100]}"
    return [], err


_master = None


def master():
    global _master
    if _master is None:
        raw = requests.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz", timeout=90).content
        _master = json.loads(gzip.decompress(raw))
    return _master


def exp_date(ms):
    return datetime.fromtimestamp(ms / 1000, IST).date()


def record_candles(d: date, out: str, man: dict):
    rows = []
    def add(sym, kind, expiry, strike, cs):
        rows.extend((c[0], sym, kind, expiry, strike, c[1], c[2], c[3], c[4], c[5], c[6] if len(c) > 6 else None) for c in cs)

    spot = {}
    for sym, key in INDEX.items():
        cs, err = candles(key, d)
        if not cs:
            man["errors"].append(f"{sym} index: {err}")
            continue
        add(sym, "IDX", None, None, cs)
        spot[sym] = sorted(c[4] for c in cs)[len(cs) // 2]
    if "NIFTY" not in spot:
        return False                                  # holiday, or data not out yet
    cs, err = candles(VIX, d)
    add("INDIAVIX", "IDX", None, None, cs)
    fo = [m for m in master() if m.get("segment") == "NSE_FO" and m.get("underlying_symbol") in spot]
    futs = sorted((m for m in fo if m.get("instrument_type") == "FUT" and exp_date(m["expiry"]) >= d), key=lambda m: m["expiry"])
    for sym in spot:
        f = next((m for m in futs if m["underlying_symbol"] == sym), None)
        if f:
            cs, err = candles(f["instrument_key"], d)
            add(sym, "FUT", exp_date(f["expiry"]).isoformat(), None, cs)
    opts = []
    for sym, sp in spot.items():
        mine = [m for m in fo if m["underlying_symbol"] == sym and m.get("instrument_type") in ("CE", "PE")
                and exp_date(m["expiry"]) >= d and abs(float(m["strike_price"]) / sp - 1) <= STRIKE_WIDTH]
        exps = sorted({m["expiry"] for m in mine})[:2]
        opts += [m for m in mine if m["expiry"] in exps]
        man["expiries"][sym] = [exp_date(e).isoformat() for e in exps]
    log(d, "options to fetch:", len(opts))
    empty = 0
    for i, m in enumerate(opts):
        cs, err = candles(m["instrument_key"], d)
        if not cs:
            empty += 1
        add(m["underlying_symbol"], m["instrument_type"], exp_date(m["expiry"]).isoformat(), float(m["strike_price"]), cs)
        time.sleep(0.12)
        if i % 100 == 0:
            log("  ", i, "rows", len(rows))
    df = pd.DataFrame(rows, columns=["ts", "sym", "kind", "expiry", "strike", "open", "high", "low", "close", "volume", "oi"])
    df["ts"] = df["ts"].str[:19]
    df = df.sort_values(["sym", "kind", "expiry", "strike", "ts"], na_position="first")
    df.to_csv(f"{out}/candles_1m.csv.gz", index=False, compression="gzip")
    man["candles"] = {"rows": len(df), "instruments": int(df.groupby(["sym", "kind", "expiry", "strike"], dropna=False).ngroups),
                      "options_without_data": empty}
    return True


# ---------------------------------------------------------------- NSE end-of-day files
def nse_get(url):
    for attempt in range(3):
        try:
            r = NSE.get(url, timeout=30)
            if r.status_code == 200 and len(r.content) > 100:
                return r.content
        except Exception:
            pass
        time.sleep(1 + attempt * 2)
    return None


def record_nse(d: date, out: str, man: dict):
    try:
        NSE.get("https://www.nseindia.com/", timeout=15)
    except Exception:
        pass
    if not os.path.exists(f"{out}/bhav_fo.csv.gz"):
        b = nse_get(f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip")
        if b and b[:2] == b"PK":
            z = zipfile.ZipFile(io.BytesIO(b))
            df = pd.read_csv(z.open(z.namelist()[0]))
            df = df[df["TckrSymb"].isin(list(INDEX) + ["MIDCPNIFTY"]) & df["FinInstrmTp"].isin(["IDO", "IDF"])]
            df.to_csv(f"{out}/bhav_fo.csv.gz", index=False, compression="gzip")
            man["bhav_fo_rows"] = len(df)
        else:
            man["errors"].append("bhavcopy not available yet")
    for name, fn in (("participant_oi", "fao_participant_oi"), ("participant_vol", "fao_participant_vol")):
        if not os.path.exists(f"{out}/{name}.csv"):
            b = nse_get(f"https://nsearchives.nseindia.com/content/nsccl/{fn}_{d:%d%m%Y}.csv")
            if b and b"Client" in b:
                open(f"{out}/{name}.csv", "wb").write(b)
                man[name] = True
            else:
                man["errors"].append(f"{name} not available yet")


def record_snapshots(d: date, out: str, man: dict):
    try:
        r = requests.get(f"{APP}/api/recorder/day", params={"date": d.isoformat()}, timeout=60)
        j = r.json()
        n = len(j.get("snapshots") or [])
        if n:
            with gzip.open(f"{out}/snapshots.json.gz", "wt") as f:
                json.dump(j, f, separators=(",", ":"))
        man["snapshots"] = n
        if not n:
            man["errors"].append("no chain snapshots for this day (app recorder off or NSE blocked and no broker login)")
    except Exception as e:
        man["errors"].append(f"snapshots: {e}")


def complete(out):
    return all(os.path.exists(f"{out}/{f}") for f in ("candles_1m.csv.gz", "bhav_fo.csv.gz", "participant_oi.csv", "snapshots.json.gz"))


def run(d: date):
    out = f"{ROOT}/{d}"
    if complete(out):
        return
    man_path = f"{out}/manifest.json"
    man = json.load(open(man_path)) if os.path.exists(man_path) else {"date": d.isoformat(), "expiries": {}}
    man["errors"] = []
    os.makedirs(out, exist_ok=True)
    if not os.path.exists(f"{out}/candles_1m.csv.gz"):
        if not record_candles(d, out, man):
            log(d, "no index candles (holiday or not published yet)", man["errors"][:2])
            if not os.listdir(out):
                os.rmdir(out)
            return
    record_nse(d, out, man)
    if not os.path.exists(f"{out}/snapshots.json.gz"):
        record_snapshots(d, out, man)
    man["updated"] = datetime.now(IST).isoformat(timespec="seconds")
    json.dump(man, open(man_path, "w"), indent=1)
    log(d, "saved:", {k: v for k, v in man.items() if k not in ("errors",)}, "| errors:", man["errors"])


if __name__ == "__main__":
    today = datetime.now(IST).date()
    days = [date.fromisoformat(a) for a in sys.argv[1:]] or \
           [today - timedelta(days=i) for i in range(LOOKBACK, -1, -1) if (today - timedelta(days=i)).weekday() < 5]
    if today in days and datetime.now(IST).hour < 16:
        days.remove(today)                            # day not finished
    for d in days:
        try:
            run(d)
        except Exception as e:
            log(d, "FAILED", repr(e))
