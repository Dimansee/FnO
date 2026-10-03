"""Broker market-data clients (read-only - this app never places real orders).

Upstox : https://upstox.com/developer/api-documentation
Fyers  : API v3 (same endpoints as the official fyers-apiv3 SDK)

Both normalise to the same shapes:
  quotes(keys)        -> {key: {"ltp", "prev_close"}}
  candles(key)        -> DataFrame[open, high, low, close, volume] (5-min, IST)
  expiries(key)       -> (list[date], lot or None)
  chain(key, expiry)  -> DataFrame with strike, ce_*/pe_* columns
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlencode

import pandas as pd
import requests

from . import config as C

IST = "Asia/Kolkata"


class BrokerError(RuntimeError):
    pass


class AuthExpired(BrokerError):
    pass


def token_valid(created_iso: str | None) -> bool:
    """Broker tokens die around 03:30 IST every morning."""
    if not created_iso:
        return False
    try:
        created = datetime.fromisoformat(created_iso)
        if created.tzinfo is None:
            created = created.replace(tzinfo=C.IST)
        now = C.now_ist()
        cutoff = now.replace(hour=3, minute=30, second=0, microsecond=0)
        if now < cutoff:
            cutoff -= timedelta(days=1)
        return created >= cutoff
    except Exception:
        return False


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# =============================================================================
# Upstox
# =============================================================================
UPSTOX = "https://api.upstox.com"


def upstox_login_url(api_key, redirect_uri, state):
    q = urlencode({"response_type": "code", "client_id": api_key, "redirect_uri": redirect_uri, "state": state})
    return f"{UPSTOX}/v2/login/authorization/dialog?{q}"


def upstox_exchange(code, api_key, api_secret, redirect_uri) -> str:
    r = requests.post(f"{UPSTOX}/v2/login/authorization/token",
                      headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                      data={"code": code, "client_id": api_key, "client_secret": api_secret,
                            "redirect_uri": redirect_uri, "grant_type": "authorization_code"}, timeout=15)
    if r.status_code >= 400:
        raise BrokerError(f"Upstox token exchange failed: {r.text[:200]}")
    return r.json()["access_token"]


class Upstox:
    name = "upstox"

    def __init__(self, token):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def _get(self, path, params=None):
        r = self.s.get(UPSTOX + path, params=params, timeout=15)
        if r.status_code == 401:
            raise AuthExpired("Upstox session expired - log in again in Settings.")
        if r.status_code >= 400:
            raise BrokerError(f"Upstox {r.status_code}: {r.text[:160]}")
        return r.json().get("data")

    def underlying_key(self, meta):
        return meta["upstox_key"]

    def vix_key(self):
        return C.INDIA_VIX_UPSTOX

    def quotes(self, keys):
        out = {}
        for i in range(0, len(keys), 450):
            data = self._get("/v2/market-quote/quotes", {"instrument_key": ",".join(keys[i:i + 450])}) or {}
            for v in data.values():
                k = v.get("instrument_token") or ""
                last = _num(v.get("last_price"))
                chg = _num(v.get("net_change")) or 0
                out[k] = {"ltp": last, "prev_close": (last - chg) if last is not None else None}
        return out

    def expiries(self, key):
        data = self._get("/v2/option/contract", {"instrument_key": key}) or []
        exps = sorted({date.fromisoformat(d["expiry"]) for d in data if d.get("expiry")})
        lot = next((d.get("lot_size") for d in data if d.get("lot_size")), None)
        return exps, lot

    def chain(self, key, expiry: date):
        data = self._get("/v2/option/chain", {"instrument_key": key, "expiry_date": expiry.isoformat()}) or []
        rows = []
        for d in data:
            row = {"strike": _num(d["strike_price"]), "spot": _num(d.get("underlying_spot_price"))}
            for side, tag in (("call_options", "ce"), ("put_options", "pe")):
                o = d.get(side) or {}
                md, g = o.get("market_data") or {}, o.get("option_greeks") or {}
                oi, poi = _num(md.get("oi")) or 0, _num(md.get("prev_oi")) or 0
                row.update({
                    f"{tag}_key": o.get("instrument_key"), f"{tag}_ltp": _num(md.get("ltp")),
                    f"{tag}_bid": _num(md.get("bid_price")), f"{tag}_ask": _num(md.get("ask_price")),
                    f"{tag}_oi": oi, f"{tag}_chg_oi": oi - poi, f"{tag}_vol": _num(md.get("volume")),
                    f"{tag}_iv": _num(g.get("iv")), f"{tag}_delta": _num(g.get("delta")),
                })
            rows.append(row)
        return pd.DataFrame(rows).sort_values("strike").reset_index(drop=True) if rows else pd.DataFrame()

    def candles(self, key, days=24):
        enc = quote(key, safe="")
        rows = []
        to_d = C.today_ist() - timedelta(days=1)
        fr_d = to_d - timedelta(days=days + 6)
        # Upstox rejects some 5-minute ranges that span two months, so ask month by month
        a = fr_d
        while a <= to_d:
            b = min(date(a.year + (a.month == 12), a.month % 12 + 1, 1) - timedelta(days=1), to_d)
            past = self._get(f"/v3/historical-candle/{enc}/minutes/5/{b}/{a}") or {}
            rows += past.get("candles", [])
            a = b + timedelta(days=1)
        today = self._get(f"/v3/historical-candle/intraday/{enc}/minutes/5") or {}
        rows += today.get("candles", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([r[:6] for r in rows], columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(IST)
        return df.drop_duplicates("ts").set_index("ts").sort_index().astype(float)


# =============================================================================
# Fyers (API v3)
# =============================================================================
FYERS_API = "https://api-t1.fyers.in/api/v3"
FYERS_DATA = "https://api-t1.fyers.in/data"


def fyers_login_url(app_id, redirect_uri, state):
    q = urlencode({"client_id": app_id, "redirect_uri": redirect_uri, "response_type": "code", "state": state})
    return f"{FYERS_API}/generate-authcode?{q}"


def fyers_exchange(auth_code, app_id, secret) -> str:
    h = hashlib.sha256(f"{app_id}:{secret}".encode()).hexdigest()
    r = requests.post(f"{FYERS_API}/validate-authcode",
                      json={"grant_type": "authorization_code", "appIdHash": h, "code": auth_code}, timeout=15)
    j = r.json() if r.content else {}
    if j.get("s") != "ok" or not j.get("access_token"):
        raise BrokerError(f"Fyers token exchange failed: {j.get('message') or r.text[:200]}")
    return j["access_token"]


class Fyers:
    name = "fyers"

    def __init__(self, app_id, token):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"{app_id}:{token}", "version": "3"})
        self._exp_ts: dict[str, dict[date, str]] = {}

    def _get(self, path, params=None):
        r = self.s.get(FYERS_DATA + path, params=params, timeout=15)
        try:
            j = r.json()
        except Exception:
            raise BrokerError(f"Fyers {r.status_code}: {r.text[:160]}")
        msg = str(j.get("message", "")).lower()
        if r.status_code == 401 or j.get("code") in (-8, -15, -16, -17) or (j.get("s") != "ok" and "token" in msg):
            raise AuthExpired("Fyers session expired - log in again in Settings.")
        if j.get("s") not in ("ok", None):
            raise BrokerError(f"Fyers: {j.get('message') or j}")
        return j

    def underlying_key(self, meta):
        return meta["fyers"]

    def vix_key(self):
        return C.INDIA_VIX_FYERS

    def quotes(self, keys):
        out = {}
        for i in range(0, len(keys), 50):
            j = self._get("/quotes", {"symbols": ",".join(keys[i:i + 50])})
            for d in j.get("d", []):
                v = d.get("v") or {}
                out[d.get("n")] = {"ltp": _num(v.get("lp")), "prev_close": _num(v.get("prev_close_price"))}
        return out

    def _raw_chain(self, key, ts="", strikecount=12, greeks=True):
        p = {"symbol": key, "strikecount": strikecount, "timestamp": ts}
        if greeks:
            p["greeks"] = 1
        try:
            return self._get("/options-chain-v3", p).get("data") or {}
        except AuthExpired:
            raise
        except BrokerError:
            if greeks:
                return self._raw_chain(key, ts, strikecount, greeks=False)
            raise

    def expiries(self, key):
        data = self._raw_chain(key, strikecount=1)
        m = {}
        for e in data.get("expiryData", []):
            try:
                m[datetime.strptime(e["date"], "%d-%m-%Y").date()] = str(e["expiry"])
            except Exception:
                continue
        self._exp_ts[key] = m
        lot = None
        for o in data.get("optionsChain", []):
            lot = lot or o.get("lot_size") or o.get("lotsize") or o.get("min_lot_size")
        return sorted(m), (int(lot) if lot else None)

    def chain(self, key, expiry: date):
        if key not in self._exp_ts:
            self.expiries(key)
        ts = self._exp_ts.get(key, {}).get(expiry, "")
        data = self._raw_chain(key, ts=ts, strikecount=12)
        spot, by_strike = None, {}
        for o in data.get("optionsChain", []):
            ot = (o.get("option_type") or "").upper()
            k = _num(o.get("strike_price"))
            if ot not in ("CE", "PE") or k is None or k <= 0:
                spot = _num(o.get("ltp")) or spot
                continue
            t = ot.lower()
            row = by_strike.setdefault(k, {"strike": k})
            oi = _num(o.get("oi")) or 0
            chg = _num(o.get("oich"))
            if chg is None and o.get("prev_oi") is not None:
                chg = oi - (_num(o.get("prev_oi")) or 0)
            g = o.get("greeks") if isinstance(o.get("greeks"), dict) else o
            row.update({
                f"{t}_key": o.get("symbol"), f"{t}_ltp": _num(o.get("ltp")), f"{t}_bid": _num(o.get("bid")),
                f"{t}_ask": _num(o.get("ask")), f"{t}_oi": oi, f"{t}_chg_oi": chg, f"{t}_vol": _num(o.get("volume")),
                f"{t}_iv": _num(g.get("iv")), f"{t}_delta": _num(g.get("delta")),
            })
        if not by_strike:
            return pd.DataFrame()
        df = pd.DataFrame(sorted(by_strike.values(), key=lambda r: r["strike"]))
        df["spot"] = spot
        for c in ("ce", "pe"):
            for f in ("key", "ltp", "bid", "ask", "oi", "chg_oi", "vol", "iv", "delta"):
                if f"{c}_{f}" not in df:
                    df[f"{c}_{f}"] = None
        return df.reset_index(drop=True)

    def candles(self, key, days=24):
        to_d = C.today_ist()
        fr_d = to_d - timedelta(days=days + 4)
        j = self._get("/history", {"symbol": key, "resolution": "5", "date_format": "1",
                                   "range_from": fr_d.isoformat(), "range_to": to_d.isoformat(), "cont_flag": "1"})
        rows = j.get("candles") or []
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([r[:6] for r in rows], columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(IST)
        return df.drop_duplicates("ts").set_index("ts").sort_index().astype(float)
