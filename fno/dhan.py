"""Dhan (DhanHQ v2) data adapter - same methods as brokers.Upstox / brokers.Fyers.

STATUS (21 Nov 2026): written from Dhan's published docs and tested only against mocked responses. It is NOT wired into
Market yet; run `python tools/dhan_check.py` with your client id + access token to verify every endpoint first.
Limits (Dhan docs): data APIs 5 requests/s, option chain 1 unique request per 3 s per underlying, intraday history 90 days
per call. WebSocket (5 connections x 5,000 instruments) is a later step: its packet offsets must be checked on a live feed.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta
import time as _tm
import pandas as pd
import requests

from . import config as C
from . import brokers as B

BASE = "https://api.dhan.co/v2"
IST = "Asia/Kolkata"
# Index security ids (exchange segment IDX_I). Verify with tools/dhan_check.py (they come from Dhan's instrument list).
SEC = {"NIFTY": 13, "BANKNIFTY": 25, "SENSEX": 51, "INDIAVIX": 21}
SEG_FNO = {"NIFTY": "NSE_FNO", "BANKNIFTY": "NSE_FNO", "SENSEX": "BSE_FNO"}
CHAIN_GAP = 3.2          # seconds between option-chain requests for the same underlying + expiry
_chain_last: dict = {}
_chain_cache: dict = {}


def _num(v):
    try:
        return None if v is None else float(v)
    except Exception:
        return None


class Dhan:
    name = "dhan"

    def __init__(self, client_id, token):
        self.s = requests.Session()
        self.s.headers.update({"access-token": token, "client-id": str(client_id), "Content-Type": "application/json", "Accept": "application/json"})

    def _post(self, path, body):
        B._acquire("dhan")
        r = self.s.post(BASE + path, json=body, timeout=15)
        if r.status_code == 429 or r.status_code == 805:
            secs = B._on_429("dhan", 60)
            raise B.RateLimited(f"Dhan asked us to slow down - pausing Dhan calls for {max(1, secs // 60)} min")
        if r.status_code in (401, 403):
            raise B.AuthExpired("Dhan session expired - generate a new access token in Settings.")
        if r.status_code >= 400:
            raise B.BrokerError(f"Dhan {r.status_code}: {r.text[:160]}")
        return r.json()

    # ----- keys: our "instrument key" for an index is "IDX_I|<id>", for an option "<NSE_FNO|BSE_FNO>|<security id>" -----
    def underlying_key(self, meta):
        return f"IDX_I|{SEC[meta['symbol']]}"

    def vix_key(self):
        return f"IDX_I|{SEC['INDIAVIX']}"

    @staticmethod
    def _split(key):
        seg, sid = key.split("|", 1)
        return seg, int(sid)

    # ----- candles -----
    @staticmethod
    def _frame(d) -> pd.DataFrame:
        ts = (d or {}).get("timestamp") or []
        if not ts:
            return pd.DataFrame()
        df = pd.DataFrame({"open": d["open"], "high": d["high"], "low": d["low"], "close": d["close"], "volume": d.get("volume") or [0] * len(ts)},
                          index=pd.to_datetime(ts, unit="s", utc=True).tz_convert(IST))
        df.index.name = "ts"
        return df[~df.index.duplicated(keep="last")].sort_index().astype(float)

    def _intraday(self, key, interval, frm: datetime, to: datetime):
        seg, sid = self._split(key)
        d = self._post("/charts/intraday", {"securityId": str(sid), "exchangeSegment": seg, "instrument": "INDEX" if seg == "IDX_I" else "EQUITY",
                                           "interval": str(interval), "oi": False,
                                           "fromDate": frm.strftime("%Y-%m-%d %H:%M:%S"), "toDate": to.strftime("%Y-%m-%d %H:%M:%S")})
        return self._frame(d)

    def candles1(self, key):
        def fetch():
            t = C.today_ist()
            return self._intraday(key, 1, datetime(t.year, t.month, t.day, 9, 0), datetime(t.year, t.month, t.day, 15, 31))
        return B._today_bars("dhan", key, fetch)

    def candles(self, key, days=24):
        to_d = C.today_ist() - timedelta(days=1)
        fr_d = to_d - timedelta(days=days + 6)
        hk = (key, to_d, days)
        hit = B._hist.get(hk)
        if hit and _tm.time() - hit[0] < B.HIST_TTL:
            past = hit[1]
        else:
            past = self._intraday(key, 5, datetime(fr_d.year, fr_d.month, fr_d.day, 9, 0), datetime(to_d.year, to_d.month, to_d.day, 15, 31))
            if not past.empty:
                B._hist[hk] = (_tm.time(), past)
        today5 = B._to5(self.candles1(key))
        if past is None or (hasattr(past, "empty") and past.empty):
            return today5.astype(float) if not today5.empty else pd.DataFrame()
        df = past[past.index.date != C.today_ist()]
        if not today5.empty:
            df = pd.concat([df, today5.astype(float)])
        return df[~df.index.duplicated(keep="last")].sort_index()

    # ----- option chain -----
    def expiries(self, key):
        seg, sid = self._split(key)
        d = self._post("/optionchain/expirylist", {"UnderlyingScrip": sid, "UnderlyingSeg": seg})
        exps = sorted(date.fromisoformat(x) for x in (d.get("data") or []))
        return exps, None                      # lot size comes from config.INDICES (Dhan's chain has no lot size)

    def chain(self, key, expiry: date):
        seg, sid = self._split(key)
        ck = (key, expiry)
        now = _tm.time()
        if ck in _chain_cache and now - _chain_last.get(ck, 0) < CHAIN_GAP:
            return _chain_cache[ck]            # same minute-tick asks twice: reuse, never break "1 request per 3 s"
        d = self._post("/optionchain", {"UnderlyingScrip": sid, "UnderlyingSeg": seg, "Expiry": expiry.isoformat()}).get("data") or {}
        _chain_last[ck] = now
        spot = _num(d.get("last_price"))
        fno = "BSE_FNO" if seg == "IDX_I" and sid == SEC["SENSEX"] else "NSE_FNO"
        rows = []
        for k, v in (d.get("oc") or {}).items():
            row = {"strike": float(k), "spot": spot}
            for tag in ("ce", "pe"):
                o = v.get(tag) or {}
                g = o.get("greeks") or {}
                oi, poi = _num(o.get("oi")) or 0, _num(o.get("previous_oi")) or 0
                row.update({f"{tag}_key": f"{fno}|{o['security_id']}" if o.get("security_id") is not None else None,
                            f"{tag}_ltp": _num(o.get("last_price")), f"{tag}_bid": _num(o.get("top_bid_price")) or None, f"{tag}_ask": _num(o.get("top_ask_price")) or None,
                            f"{tag}_oi": oi, f"{tag}_chg_oi": oi - poi, f"{tag}_vol": _num(o.get("volume")),
                            f"{tag}_iv": _num(o.get("implied_volatility")), f"{tag}_delta": _num(g.get("delta")),
                            f"{tag}_bq": _num(o.get("top_bid_quantity")), f"{tag}_aq": _num(o.get("top_ask_quantity"))})
            rows.append(row)
        df = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True) if rows else pd.DataFrame()
        _chain_cache[ck] = df
        return df

    # ----- quotes (index / VIX / option last price, best bid-ask) -----
    def quotes(self, keys):
        out = {}
        by_seg: dict = {}
        for k in keys:
            seg, sid = self._split(k)
            by_seg.setdefault(seg, []).append(sid)
        d = self._post("/marketfeed/quote", by_seg).get("data") or {}
        for seg, ids in (d or {}).items():
            for sid, v in (ids or {}).items():
                last = _num(v.get("last_price"))
                ohlc = v.get("ohlc") or {}
                dep = v.get("depth") or {}
                bid = _num(((dep.get("buy") or [{}])[0]).get("price")) or None
                ask = _num(((dep.get("sell") or [{}])[0]).get("price")) or None
                out[f"{seg}|{sid}"] = {"ltp": last, "prev_close": _num(ohlc.get("close")), "bid": bid, "ask": ask}
        return out

    def depth(self, keys):
        return {}                              # 5-level depth comes with the WebSocket step (Full packet) - not polled
