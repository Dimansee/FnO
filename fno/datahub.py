"""Data vault: what the app saves, how far back it goes, custom-range exports and share links.

Saved by the app itself (Redis):
  * Nifty / Bank Nifty and US 5-minute candles, one day at a time. Yahoo only serves ~60 days, so saving every
    day is what builds a 120+ day history.
  * Option-chain snapshots (the recorder), kept RECORD_KEEP_DAYS days.
  * Paper trades, learning lessons and the after-close research notes.
Saved by GitHub Actions into the research-data branch (permanent): 1-minute option candles, NSE end-of-day files,
participant open interest and a copy of the snapshots.
Crypto candles are not stored: Binance serves the full history on request, so exports fetch it live.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from . import config as C
from . import store

KEEP_DAYS = 120
CK = "fno:data:c:"                     # candles hash per MARKET:SYMBOL, field = date
META = "fno:data:meta"
REPO = os.environ.get("GITHUB_REPO", "Dimansee/fno")
ARCHIVE_BRANCH = "research-data"
ARCHIVE_PATH = "research/data/live"
MAX_BYTES = 4_000_000                  # Vercel serverless responses are capped near 4.5 MB
SHARE_DAYS = 7
BACKFILL_UNDER = 110                   # days saved below this -> backfill from Upstox
BACKFILL_CAL_DAYS = 185                # about 125 trading sessions
IN_SYMS = ["NIFTY", "BANKNIFTY"]
CRYPTO_TFS = ["5m", "15m", "30m", "1h", "4h", "1d"]

SCHEDULES = [
    {"name": "Pre-open refresh", "where": "QStash", "when": "09:00 IST, Mon-Fri", "what": "Refreshes the stock picks before the open."},
    {"name": "Option-chain recorder (opening)", "where": "QStash", "when": "09:15-09:29 IST, every minute", "what": "Saves option-chain snapshots every 5 minutes (strikes around the money, bid/ask, open interest, IV)."},
    {"name": "Market-hours tick", "where": "QStash", "when": "09:30-15:29 IST, every minute", "what": "Checks signals and open trades; saves a chain snapshot every 5 minutes."},
    {"name": "After-close research + data save", "where": "QStash", "when": "15:35 IST, Mon-Fri", "what": "Writes the day's research note and lessons, and saves the day's Nifty / Bank Nifty 5-minute candles."},
    {"name": "Crypto and US books", "where": "GitHub Actions", "when": "Every 5 minutes, all day", "what": "Runs the crypto and US paper accounts; after the US close it saves the day's US candles."},
    {"name": "Permanent market archive", "where": "GitHub Actions", "when": "19:10 IST and again 08:50 IST next morning", "what": "Saves 1-minute option candles, NSE end-of-day files, FII/DII participant data and a copy of the day's snapshots into the research-data branch. Kept forever."},
    {"name": "Google Drive backup", "where": "QStash (after-close run)", "when": "Fridays after 15:35 IST", "what": "Zips trades, journal, settings, strategies, lessons and saved candles into your Google Drive (once connected below). Broker keys are never included."},
    {"name": "Stock research", "where": "GitHub Actions", "when": "19:45 IST and 06:00 IST next morning", "what": "Refreshes the stock research data and picks."},
    {"name": "AI model retrain", "where": "GitHub Actions", "when": "08:00 IST on the 1st of each month", "what": "Retrains the optional AI model."},
]


# ------------------------------------------------------------------ candle saving
def _rows(g: pd.DataFrame, dec: int = 2) -> list:
    return [[ts.strftime("%H:%M"), round(float(r.open), dec), round(float(r.high), dec), round(float(r.low), dec), round(float(r.close), dec), int(r.volume or 0)]
            for ts, r in g.iterrows()]


def _store_days(key: str, df: pd.DataFrame, cutoff: date) -> int:
    """Save every complete day (date < cutoff) in df under key. Returns the number of days written."""
    if df is None or df.empty:
        return 0
    args = []
    for d, g in df.groupby(df.index.date):
        if d < cutoff and len(g) >= 10:
            args += [d.isoformat(), json.dumps(_rows(g), separators=(",", ":"))]
    for i in range(0, len(args), 40):
        store._cmd("HSET", key, *args[i:i + 40])
    return len(args) // 2


def _meta_update(ds: str, extra: dict | None = None):
    cur = {}
    try:
        cur = json.loads(store._cmd("HGET", META, ds) or "{}")
    except Exception:
        pass
    cur.update({"last_update": datetime.now(timezone.utc).isoformat(timespec="seconds"), **(extra or {})})
    store._cmd("HSET", META, ds, json.dumps(cur))


def save_in(now: datetime | None = None) -> dict:
    """Nifty and Bank Nifty 5-minute candles for every complete session Yahoo still serves (~60 days). Idempotent."""
    from . import market as M
    now = now or C.now_ist()
    cutoff = now.date() + timedelta(days=1) if now.time() >= C.MARKET_CLOSE else now.date()
    out = {}
    for sym in IN_SYMS:
        key = f"{CK}IN:{sym}"
        try:
            if (store._cmd("HLEN", key) or 0) < BACKFILL_UNDER:          # first runs: pull ~180 calendar days of history from Upstox's public API
                hist = M.upstox_public_history(C.INDICES[sym]["upstox_key"], BACKFILL_CAL_DAYS)
                out[sym + "_backfill"] = _store_days(key, hist, now.date())
        except Exception as e:
            out[sym + "_backfill"] = f"error: {str(e)[:100]}"
        try:
            df = M.yahoo_candles(C.INDICES[sym]["yahoo"], period="60d", interval="5m")
            out[sym] = _store_days(key, df, cutoff)
        except Exception as e:
            out[sym] = f"error: {str(e)[:100]}"
    _meta_update("in_candles", {"note": "saved " + json.dumps(out)})
    return out


def save_us(now: datetime | None = None) -> dict:
    from . import markets as MK
    now = now or datetime.now(timezone.utc)
    ny = now.astimezone(MK.TZ["US"])
    cutoff = ny.date() + timedelta(days=1) if (ny.hour, ny.minute) >= (16, 5) else ny.date()
    out = {}
    for sym in MK.DEFAULTS["US"]:
        try:
            df = MK.candles("US", sym, "5m", 59, ttl=0)
            out[sym] = _store_days(f"{CK}US:{sym}", df, cutoff)
        except Exception as e:
            out[sym] = f"error: {str(e)[:100]}"
    _meta_update("us_candles", {"note": "saved " + json.dumps(out)})
    return out


def daily_us():
    """Called by the 5-minute global tick: after the US close, once per day."""
    from . import markets as MK
    ny = datetime.now(timezone.utc).astimezone(MK.TZ["US"])
    if ny.weekday() >= 5 or (ny.hour, ny.minute) < (16, 20) or ny.hour >= 20:
        return None
    flag = f"fno:data:us_saved:{ny.date().isoformat()}"
    if store._cmd("SET", flag, "1", "NX", "EX", 86400 * 2) is None:
        return None
    return save_us()


# ------------------------------------------------------------------ coverage
def _days(key: str) -> list[str]:
    return sorted(store._cmd("HKEYS", key) or [])


def _meta(ds: str) -> dict:
    try:
        return json.loads(store._cmd("HGET", META, ds) or "{}")
    except Exception:
        return {}


def catalog() -> list[dict]:
    from . import gbook as GB
    from . import learn as LN
    from . import markets as MK
    from . import recorder as REC
    out = []
    for ds, mk, syms, name, about in [
        ("in_candles", "IN", IN_SYMS, "Nifty & Bank Nifty 5-minute candles", "Open, high, low, close, volume for every session, saved by this app each day after the close."),
        ("us_candles", "US", MK.DEFAULTS["US"], "US 5-minute candles", "SPY, QQQ, AAPL, NVDA (New York time), saved each day after the US close."),
    ]:
        per = {s: _days(f"{CK}{mk}:{s}") for s in syms}
        allv = sorted({d for v in per.values() for d in v})
        out.append({"id": ds, "name": name, "about": about, "symbols": syms, "formats": ["csv", "json"], "first": allv[0] if allv else None, "last": allv[-1] if allv else None,
                    "days": len(allv), "last_update": _meta(ds).get("last_update"), "stored": "This app (Redis)", "max_days": 120, "needs_symbol": True})
    st = REC.status()
    days = [d["date"] for d in st.get("days", [])]
    out.append({"id": "chain_snapshots", "name": "Option-chain snapshots", "about": "Every 5 minutes: spot, and per strike bid/ask/last, open interest, volume and IV for the nearest expiries (Nifty, Bank Nifty).",
                "symbols": REC.SYMS, "formats": ["csv", "json"], "first": days[0] if days else None, "last": days[-1] if days else None, "days": len(days),
                "last_update": st.get("last_ok"), "stored": f"This app for {C.RECORD_KEEP_DAYS} days, plus a permanent copy on GitHub", "max_days": 10, "needs_symbol": True})
    out.append({"id": "crypto_candles", "name": "Crypto candles", "about": "Fetched live from Binance for any range you choose, so history is unlimited.", "symbols": MK.catalog_ids("CRYPTO"),
                "formats": ["csv", "json"], "first": None, "last": None, "days": None, "last_update": None, "stored": "Binance (fetched when you export)", "max_days": 120, "needs_symbol": True, "timeframes": CRYPTO_TFS})
    from . import store as S
    tin = S.trades()
    out.append({"id": "trades_in", "name": "Paper trades - India F&O", "about": "Every closed paper trade: strategy, option legs, entry/exit premium, P&L and exit reason.", "symbols": [], "formats": ["csv", "json"],
                "first": (tin[0]["closed"][:10] if tin else None), "last": (tin[-1]["closed"][:10] if tin else None), "days": len({t["closed"][:10] for t in tin}),
                "last_update": tin[-1]["closed"] if tin else None, "stored": "This app (Redis)", "max_days": 3650, "needs_symbol": False})
    for m, label in (("CRYPTO", "Crypto"), ("US", "US")):
        tr = GB.trades(m)
        out.append({"id": f"trades_{m.lower()}", "name": f"Paper trades - {label}", "about": "Every closed trade with R multiple, fees, P&L and reason.", "symbols": [], "formats": ["csv", "json"],
                    "first": (tr[0]["closed"][:10] if tr else None), "last": (tr[-1]["closed"][:10] if tr else None), "days": len({t["closed"][:10] for t in tr}),
                    "last_update": tr[-1]["closed"] if tr else None, "stored": "This app (Redis)", "max_days": 3650, "needs_symbol": False})
    ls = LN.lessons(2000)
    out.append({"id": "lessons", "name": "Lessons from trades", "about": "The plain-English lesson written for each closed trade, with tags and R.", "symbols": [], "formats": ["csv", "json"],
                "first": (min(x["closed"][:10] for x in ls) if ls else None), "last": (max(x["closed"][:10] for x in ls) if ls else None), "days": len({x["closed"][:10] for x in ls}),
                "last_update": max((x["closed"] for x in ls), default=None), "stored": "This app (Redis)", "max_days": 3650, "needs_symbol": False})
    rs = LN.research_list(None, 200)
    out.append({"id": "research_notes", "name": "After-close research notes", "about": "Each day's regime, levels for tomorrow and findings.", "symbols": [], "formats": ["csv", "json"],
                "first": (min(x["date"] for x in rs) if rs else None), "last": (max(x["date"] for x in rs) if rs else None), "days": len(rs),
                "last_update": max((x.get("generated") for x in rs if x.get("generated")), default=None), "stored": "This app (Redis)", "max_days": 3650, "needs_symbol": False})
    return out


def archive() -> dict:
    """Days kept permanently in the research-data branch on GitHub (public repo, no key needed)."""
    from . import market as M

    def load():
        r = requests.get(f"https://api.github.com/repos/{REPO}/contents/{ARCHIVE_PATH}", params={"ref": ARCHIVE_BRANCH}, timeout=12,
                         headers={"Accept": "application/vnd.github+json", "User-Agent": "fno-trainer"})
        if r.status_code != 200:
            raise RuntimeError(f"GitHub {r.status_code}")
        return sorted(x["name"] for x in r.json() if x["type"] == "dir")
    try:
        names = M.cached("data:archive", 3600, load)
    except Exception as e:
        return {"ok": False, "error": str(e)[:100], "days": [], "repo": REPO, "branch": ARCHIVE_BRANCH, "path": ARCHIVE_PATH}
    return {"ok": True, "days": names, "repo": REPO, "branch": ARCHIVE_BRANCH, "path": ARCHIVE_PATH,
            "files": ["candles_1m.csv.gz", "bhav_fo.csv.gz", "participant_oi.csv", "participant_vol.csv", "snapshots.json.gz", "manifest.json"]}


def overview() -> dict:
    return {"datasets": catalog(), "schedules": SCHEDULES, "archive": archive(), "keep_days": KEEP_DAYS, "share_days": SHARE_DAYS}


# ------------------------------------------------------------------ export
def _d(s: str, name: str) -> date:
    try:
        return date.fromisoformat(s)
    except Exception:
        raise ValueError(f"{name} must look like 2026-10-15.")


def _candles_rows(mk: str, sym: str, a: date, b: date) -> list[dict]:
    key = f"{CK}{mk}:{sym}"
    days = [d for d in _days(key) if a.isoformat() <= d <= b.isoformat()]
    if not days:
        return []
    out = []
    raw = store._cmd("HMGET", key, *days) or []
    for d, v in zip(days, raw):
        for t, o, h, l, c, vol in json.loads(v or "[]"):
            out.append({"datetime": f"{d} {t}", "open": o, "high": h, "low": l, "close": c, "volume": vol})
    return out


def _snap_rows(sym: str, a: date, b: date) -> list[dict]:
    from . import recorder as REC
    out = []
    d = a
    while d <= b:
        if d.weekday() < 5:
            for s in REC.day(d.isoformat())["snapshots"]:
                if s["sym"] != sym:
                    continue
                for r in s["rows"]:
                    out.append({"date": d.isoformat(), "time": s["t"], "symbol": s["sym"], "expiry": s["exp"], "spot": s["spot"], "source": s["src"], **dict(zip(REC.FIELDS, r))})
        d += timedelta(days=1)
    return out


def _flat(v):
    if isinstance(v, (list, tuple)):
        return "; ".join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, separators=(",", ":"))
    return v


def build(dataset: str, symbol: str | None, start: str, end: str, fmt: str = "csv", tf: str = "1h") -> tuple[bytes, str, str]:
    """(bytes, filename, mimetype) for the chosen dataset and date range."""
    from . import gbook as GB
    from . import learn as LN
    from . import markets as MK
    from . import store as S
    a, b = _d(start, "Start date"), _d(end, "End date")
    if b < a:
        raise ValueError("The end date is before the start date.")
    if fmt not in ("csv", "json"):
        raise ValueError("Format must be csv or json.")
    cat = {c["id"]: c for c in catalog()} if dataset not in ("crypto_candles",) else {}
    span = (b - a).days + 1
    rows: list[dict]
    if dataset in ("in_candles", "us_candles"):
        mk = "IN" if dataset == "in_candles" else "US"
        if symbol not in (IN_SYMS if mk == "IN" else MK.DEFAULTS["US"]):
            raise ValueError("Choose a symbol.")
        rows = _candles_rows(mk, symbol, a, b)
    elif dataset == "chain_snapshots":
        from . import recorder as REC
        if symbol not in REC.SYMS:
            raise ValueError("Choose Nifty or Bank Nifty.")
        if span > 10:
            raise ValueError("Option-chain snapshots are large: pick at most 10 days at a time (older days are in the permanent GitHub archive listed below).")
        rows = _snap_rows(symbol, a, b)
    elif dataset == "crypto_candles":
        if not MK.valid_symbol("CRYPTO", symbol or ""):
            raise ValueError("Choose a coin.")
        if tf not in CRYPTO_TFS:
            raise ValueError("Unknown timeframe.")
        if span > 366:
            raise ValueError("Pick at most 366 days.")
        if tf == "5m" and span > 120:
            raise ValueError("5-minute crypto data: pick at most 120 days.")
        df = MK.candles("CRYPTO", symbol, tf, (C.today_ist() - a).days + 2, ttl=600)
        df = df[(df.index.date >= a) & (df.index.date <= b)]
        rows = [{"datetime": ts.strftime("%Y-%m-%d %H:%M"), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for ts, r in df.iterrows()]
    elif dataset == "trades_in":
        rows = []
        for t in S.trades():
            if a.isoformat() <= t["closed"][:10] <= b.isoformat():
                plan = t.get("plan") or {}
                rows.append({"id": t["id"], "symbol": t["symbol"], "mode": t.get("mode"), "strategy": plan.get("strategy") or ("manual" if t.get("mode") != "signal" else "noise"),
                             "opened": t["opened"], "closed": t["closed"], "lots": t.get("lots"), "legs": "; ".join(f"{l['side']} {l['strike']}{l['opt']}" for l in t.get("legs", [])),
                             "entry_prem": t.get("entry_prem"), "exit_prem": t.get("exit_prem"), "charges": t.get("charges"), "pnl": t.get("pnl"), "reason": t.get("reason")})
    elif dataset in ("trades_crypto", "trades_us"):
        m = dataset.split("_")[1].upper()
        rows = [{k: _flat(t.get(k)) for k in ("id", "symbol", "side", "strategy_name", "opened", "closed", "qty", "entry", "exit", "leverage", "fees", "pnl", "R", "mfe_R", "mae_R", "reason")}
                for t in GB.trades(m) if a.isoformat() <= t["closed"][:10] <= b.isoformat()]
    elif dataset == "lessons":
        rows = [{k: _flat(x.get(k)) for k in ("closed", "market", "symbol", "strategy_name", "side", "R", "pnl", "reason", "kind", "mfe_R", "mae_R", "held_min", "tags", "text")}
                for x in LN.lessons(2000) if a.isoformat() <= x["closed"][:10] <= b.isoformat()]
        rows.sort(key=lambda r: r["closed"])
    elif dataset == "research_notes":
        rows = [{"date": x["date"], "market": x.get("market"), "headline": x.get("headline"), "insights": _flat(x.get("insights")), "levels_for_tomorrow": _flat(x.get("levels_for_tomorrow")),
                 "regimes": _flat({k: v.get("regime") for k, v in (x.get("symbols") or {}).items()})} for x in LN.research_list(None, 200) if a.isoformat() <= x["date"] <= b.isoformat()]
        rows.sort(key=lambda r: r["date"])
    else:
        raise ValueError("Unknown dataset.")
    if not rows:
        raise ValueError("No saved data in that range. Pick dates inside the 'first - last' coverage shown for this dataset.")
    name = f"{dataset}{'_' + symbol if symbol else ''}{'_' + tf if dataset == 'crypto_candles' else ''}_{a}_{b}.{fmt}"
    if fmt == "json":
        body = json.dumps({"dataset": dataset, "symbol": symbol, "from": a.isoformat(), "to": b.isoformat(), "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "rows": rows}, separators=(",", ":"), default=str).encode()
        mime = "application/json"
    else:
        keys = list(dict.fromkeys(k for r in rows for k in r))
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
        body, mime = buf.getvalue().encode(), "text/csv"
    if len(body) > MAX_BYTES:
        raise ValueError(f"That file would be {len(body) / 1e6:.1f} MB, over the 4 MB download limit. Pick a shorter date range.")
    return body, name, mime


# ------------------------------------------------------------------ share links
def _key() -> bytes:
    if not C.SESSION_SECRET:
        raise ValueError("Sharing needs SESSION_SECRET to be set.")
    return ("share:" + C.SESSION_SECRET).encode()


def make_token(p: dict) -> str:
    body = {"d": p["dataset"], "s": p.get("symbol"), "a": p["start"], "b": p["end"], "f": p.get("fmt", "csv"), "t": p.get("tf", "1h"), "x": int(time.time()) + SHARE_DAYS * 86400}
    raw = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).decode().rstrip("=")
    return raw + "." + hmac.new(_key(), raw.encode(), hashlib.sha256).hexdigest()[:32]


def read_token(tok: str) -> dict:
    try:
        raw, sig = tok.rsplit(".", 1)
        if not hmac.compare_digest(sig, hmac.new(_key(), raw.encode(), hashlib.sha256).hexdigest()[:32]):
            raise ValueError
        body = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except ValueError:
        raise ValueError("This link is not valid.")
    except Exception:
        raise ValueError("This link is not valid.")
    if body["x"] < time.time():
        raise ValueError("This link has expired. Ask for a new one.")
    return {"dataset": body["d"], "symbol": body["s"], "start": body["a"], "end": body["b"], "fmt": body["f"], "tf": body["t"], "expires": body["x"]}
