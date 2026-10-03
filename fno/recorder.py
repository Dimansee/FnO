"""Market recorder: saves option-chain snapshots during market hours so the research can
later study things free history doesn't keep - bid/ask spreads, the whole chain's OI and
IV at a moment, how they shift through the day.

Runs from the every-minute scheduler tick; takes a snapshot every RECORD_EVERY_MIN minutes.
Source: the connected broker (Upstox/Fyers) when logged in, otherwise NSE's public option
chain. Snapshots live in Redis for RECORD_KEEP_DAYS days; the nightly GitHub job
(research/record_day.py) copies each day into the research-data branch for good.

Redis layout:
  fno:rec:<YYYY-MM-DD>  list of JSON snapshots {t, sym, src, spot, exp, rows:[[strike, ce..., pe...]]}
  fno:rec:days          set of dates that have snapshots
  fno:rec:status        JSON {last, src, err, ...}
"""
from __future__ import annotations

import json
import time as _t
from datetime import date, datetime

import requests

from . import config as C
from . import store

FIELDS = ["strike", "ce_ltp", "ce_bid", "ce_ask", "ce_oi", "ce_vol", "ce_iv",
          "pe_ltp", "pe_bid", "pe_ask", "pe_oi", "pe_vol", "pe_iv"]
SYMS = ["NIFTY", "BANKNIFTY"]
NSE_NAME = {"NIFTY": "NIFTY", "BANKNIFTY": "BANKNIFTY", "FINNIFTY": "FINNIFTY"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36")

_nse = {"s": None, "warm": 0.0}


# ---------------------------------------------------------------------------
# NSE public option chain (no login). NSE needs a cookie from its home page first.
# ---------------------------------------------------------------------------
def _session(force=False):
    if force or _nse["s"] is None or _t.time() - _nse["warm"] > 240:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                          "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/option-chain"})
        s.get("https://www.nseindia.com/option-chain", timeout=6)
        _nse.update(s=s, warm=_t.time())
    return _nse["s"]


def _nse_get(url):
    for attempt in range(2):
        s = _session(force=attempt > 0)
        r = s.get(url, timeout=6)
        if r.status_code == 200 and r.text.strip().startswith("{"):
            j = r.json()
            if j:
                return j
        _t.sleep(0.4)
    raise RuntimeError(f"NSE {r.status_code}: {r.text[:80]!r}")


def _f(d, *names):
    for n in names:
        v = d.get(n)
        if v not in (None, "", "-"):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def _nse_rows(data):
    rows = []
    for d in data:
        ce, pe = d.get("CE") or {}, d.get("PE") or {}
        row = [_f(d, "strikePrice") or _f(ce, "strikePrice") or _f(pe, "strikePrice")]
        for o in (ce, pe):
            row += [_f(o, "lastPrice"), _f(o, "bidprice", "buyPrice1", "bidPrice"), _f(o, "askPrice", "sellPrice1", "askprice"),
                    _f(o, "openInterest"), _f(o, "totalTradedVolume"), _f(o, "impliedVolatility")]
        rows.append(row)
    return rows


def nse_chain(sym, n_exp=2):
    """[(expiry, spot, rows)] for the nearest n_exp expiries from NSE's public site."""
    name = NSE_NAME[sym]
    out = []
    try:                                   # current site (2025+): expiry list + one call per expiry
        info = _nse_get(f"https://www.nseindia.com/api/option-chain-contract-info?symbol={name}")
        exps = info.get("expiryDates") or []
        for e in exps[:n_exp]:
            j = _nse_get(f"https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol={name}&expiry={e}")
            rec = j.get("records") or {}
            out.append((datetime.strptime(e, "%d-%b-%Y").date().isoformat(), rec.get("underlyingValue"), _nse_rows(rec.get("data") or [])))
        if out and any(r for _, _, r in out):
            return out
    except Exception as e:
        first_err = e
    else:
        first_err = None
    j = _nse_get(f"https://www.nseindia.com/api/option-chain-indices?symbol={name}")   # older endpoint
    rec = j.get("records") or {}
    exps = rec.get("expiryDates") or []
    out = []
    for e in exps[:n_exp]:
        rows = _nse_rows([d for d in rec.get("data") or [] if d.get("expiryDate") == e])
        out.append((datetime.strptime(e, "%d-%b-%Y").date().isoformat(), rec.get("underlyingValue"), rows))
    if not any(r for _, _, r in out) and first_err:
        raise first_err
    return out


# ---------------------------------------------------------------------------
# Broker chain (when the user is logged in to Upstox / Fyers)
# ---------------------------------------------------------------------------
def broker_chain(m, sym, n_exp=2):
    exps, _ = m.expiries_and_lot(sym)
    if not m.live:
        return None
    out = []
    for e in [x for x in exps if x >= C.today_ist()][:n_exp]:
        ch = m.br.chain(m.key(sym), e)
        if ch is None or ch.empty:
            continue
        rows = [[r.get(k) for k in FIELDS] for r in ch.to_dict("records")]
        out.append((e.isoformat(), float(ch["spot"].dropna().iloc[0]) if ch["spot"].notna().any() else None, rows))
    return out or None


def _trim(rows, spot, width):
    """Keep `width` strikes each side of the money (enough for any strategy we test)."""
    rows = [r for r in rows if r[0]]
    if not spot or not rows:
        return rows
    rows.sort(key=lambda r: r[0])
    i = min(range(len(rows)), key=lambda k: abs(rows[k][0] - spot))
    return rows[max(0, i - width): i + width + 1]


def _clean(v):
    return None if v is None else (round(v, 2) if isinstance(v, float) else v)


def snapshot(now=None, market=None, force=False):
    """Take one snapshot of every recorded symbol (if it's time). Returns a short summary."""
    now = now or C.now_ist()
    t = now.time()
    if not force:
        if now.weekday() >= 5 or not (C.MARKET_OPEN <= t <= C.MARKET_CLOSE):
            return {"skipped": "market closed"}
        if (now.hour * 60 + now.minute - (C.MARKET_OPEN.hour * 60 + C.MARKET_OPEN.minute)) % C.RECORD_EVERY_MIN:
            return {"skipped": "not a snapshot minute"}
    day = now.date().isoformat()
    key = f"fno:rec:{day}"
    saved, errs, src_used, nse_down = 0, [], set(), False
    for sym in SYMS:
        chains, src = None, None
        if market is not None and market.live:
            try:
                chains, src = broker_chain(market, sym), market.br.name
            except Exception as e:
                errs.append(f"{sym} broker: {str(e)[:80]}")
        if not chains and not nse_down:
            try:
                chains, src = nse_chain(sym), "nse"
            except Exception as e:
                errs.append(f"{sym} nse: {str(e)[:80]}")
                nse_down = True                    # don't spend the tick's time retrying a blocked site
        for exp, spot, rows in chains or []:
            rows = [[_clean(v) for v in r] for r in _trim(rows, spot, C.RECORD_STRIKES)]
            if not rows:
                continue
            snap = {"t": now.strftime("%H:%M"), "sym": sym, "src": src, "spot": spot, "exp": exp, "rows": rows}
            store._cmd("RPUSH", key, json.dumps(snap, separators=(",", ":")))
            saved += 1
            src_used.add(src)
    if saved:
        store._cmd("EXPIRE", key, C.RECORD_KEEP_DAYS * 86400)
        store._cmd("SADD", "fno:rec:days", day)
    st = {"last_try": now.isoformat(timespec="seconds"), "saved": saved, "src": sorted(src_used), "errors": errs[:4]}
    if saved:
        st["last_ok"] = st["last_try"]
    prev = status_raw()
    if not saved and prev.get("last_ok"):
        st["last_ok"] = prev["last_ok"]
    store._cmd("SET", "fno:rec:status", json.dumps(st))
    return st


def status_raw() -> dict:
    try:
        v = store._cmd("GET", "fno:rec:status")
        return json.loads(v) if v else {}
    except Exception:
        return {}


def status() -> dict:
    days = sorted(store._cmd("SMEMBERS", "fno:rec:days") or [])
    live_days = []
    for d in days[-C.RECORD_KEEP_DAYS:]:
        n = store._cmd("LLEN", f"fno:rec:{d}") or 0
        if n:
            live_days.append({"date": d, "snapshots": int(n)})
        else:
            store._cmd("SREM", "fno:rec:days", d)            # expired
    return {"every_min": C.RECORD_EVERY_MIN, "strikes_each_side": C.RECORD_STRIKES, "keep_days": C.RECORD_KEEP_DAYS,
            "symbols": SYMS, "fields": FIELDS, "days": live_days, **status_raw()}


def day(d: str) -> dict:
    date.fromisoformat(d)                                      # validates (ValueError -> 400)
    raw = store._cmd("LRANGE", f"fno:rec:{d}", 0, -1) or []
    return {"date": d, "fields": FIELDS, "snapshots": [json.loads(x) for x in raw]}
