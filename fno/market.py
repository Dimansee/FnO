"""Unified market-data layer: picks the connected broker (Upstox or Fyers),
falls back to Yahoo Finance + theoretical (Black-Scholes) option prices."""
from __future__ import annotations

import gzip
import json
import time as _t
from datetime import date

import numpy as np
import pandas as pd
import requests

from . import brokers as B
from . import config as C
from . import indicators as I
from . import store

IST = "Asia/Kolkata"

# ---------------------------------------------------------------------------
# tiny in-process TTL cache (survives between requests on a warm instance)
# ---------------------------------------------------------------------------
_cache: dict = {}


def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and _t.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (_t.time(), val)
    return val


def instrument(symbol: str) -> dict:
    meta = dict(C.INDICES.get(symbol) or C.STOCKS[symbol])
    meta["symbol"] = symbol
    meta["kind"] = "index" if symbol in C.INDICES else "stock"
    return meta


# ---------------------------------------------------------------------------
# Yahoo Finance
# ---------------------------------------------------------------------------
def yahoo_candles(ticker: str, period="5d", interval="5m") -> pd.DataFrame:
    import yfinance as yf
    df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
    if df.empty:
        return df
    df.index = df.index.tz_convert(IST) if df.index.tz is not None else df.index.tz_localize("UTC").tz_convert(IST)
    return df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()


def yahoo_daily(ticker: str, period="1mo") -> pd.DataFrame:
    import yfinance as yf
    return yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False).rename(columns=str.lower)


# ---------------------------------------------------------------------------
# Exchange instrument master (real lot sizes + Upstox stock keys), daily
# ---------------------------------------------------------------------------
MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


def master() -> dict:
    def load():
        today = C.today_ist().isoformat()
        m = store.get("instrument_master")
        if m and m.get("date") == today:
            return m
        try:
            r = requests.get(MASTER_URL, timeout=25)
            r.raise_for_status()
            rows = json.loads(gzip.decompress(r.content))
            wanted = set(C.STOCKS) | {"NIFTY", "BANKNIFTY"}
            lots, eq = {}, {}
            for d in rows:
                seg = d.get("segment")
                if seg == "NSE_FO" and d.get("instrument_type") in ("CE", "PE"):
                    u = d.get("underlying_symbol") or d.get("name")
                    if u in wanted and d.get("lot_size"):
                        lots.setdefault(u, int(d["lot_size"]))
                elif seg == "NSE_EQ" and d.get("instrument_type") == "EQ" and d.get("trading_symbol") in C.STOCKS:
                    eq[d["trading_symbol"]] = d["instrument_key"]
            del rows
            m = {"date": today, "lots": lots, "eq_keys": eq}
            store.put("instrument_master", m)
            return m
        except Exception:
            return m or {"date": None, "lots": {}, "eq_keys": {}}
    return cached("master", 3600, load)


def lot_size(symbol) -> int:
    return int(master().get("lots", {}).get(symbol) or instrument(symbol)["lot"])


# ---------------------------------------------------------------------------
# Option maths helpers
# ---------------------------------------------------------------------------
def realised_vol(candles: pd.DataFrame | None) -> float | None:
    if candles is None or len(candles) < 30:
        return None
    r = np.log(candles["close"]).diff().dropna()
    return float(r.std() * np.sqrt(75 * 252))


def demo_iv(symbol, strike, spot, vix_level, candles=None):
    meta = instrument(symbol)
    base = (vix_level / 100) * meta["iv_mult"] if meta["iv_mult"] else (realised_vol(candles) or 0.25)
    base = min(max(base, 0.08), 0.9)
    return base * (1 + 2.5 * abs(strike / spot - 1))


def theoretical_chain(symbol, expiry, spot, vix, candles=None, width=12) -> pd.DataFrame:
    step = instrument(symbol)["step"]
    atm = round(spot / step) * step
    t = I.years_to_expiry(expiry)
    rows = []
    for k in range(-width, width + 1):
        strike = atm + k * step
        iv = demo_iv(symbol, strike, spot, vix, candles)
        row = {"strike": float(strike), "spot": spot}
        for tag, opt in (("ce", "CE"), ("pe", "PE")):
            p = I.bs_price(spot, strike, t, iv, opt)
            g = I.bs_greeks(spot, strike, t, iv, opt)
            p = max(round(p / 0.05) * 0.05, 0.05)
            spr = max(0.05, round(p * 0.004 / 0.05) * 0.05)
            row.update({
                f"{tag}_key": f"DEMO|{symbol}|{expiry}|{strike:g}|{opt}", f"{tag}_ltp": round(p, 2),
                f"{tag}_bid": round(max(0.05, p - spr), 2), f"{tag}_ask": round(p + spr, 2),
                f"{tag}_oi": None, f"{tag}_chg_oi": None, f"{tag}_vol": None,
                f"{tag}_iv": round(iv * 100, 2), f"{tag}_delta": round(g["delta"], 3),
            })
        rows.append(row)
    df = pd.DataFrame(rows)
    df.attrs["source"] = "theoretical"
    return df


def chain_stats(ch: pd.DataFrame, spot: float) -> dict:
    out = {"pcr": None, "support": None, "resistance": None, "max_pain": None}
    if ch is None or ch.empty or pd.to_numeric(ch["ce_oi"], errors="coerce").fillna(0).sum() <= 0:
        return out
    ce_oi = pd.to_numeric(ch["ce_oi"], errors="coerce").fillna(0)
    pe_oi = pd.to_numeric(ch["pe_oi"], errors="coerce").fillna(0)
    out["pcr"] = round(float(pe_oi.sum() / ce_oi.sum()), 2)
    above, below = ch.strike >= spot, ch.strike <= spot
    if above.any():
        out["resistance"] = float(ch.strike[above].iloc[int(ce_oi[above].values.argmax())])
    if below.any():
        out["support"] = float(ch.strike[below].iloc[int(pe_oi[below].values.argmax())])
    strikes = ch["strike"].values.astype(float)
    pain = [((np.maximum(0, s - strikes) * ce_oi.values).sum() + (np.maximum(0, strikes - s) * pe_oi.values).sum())
            for s in strikes]
    out["max_pain"] = float(strikes[int(np.argmin(pain))])
    return out


# ---------------------------------------------------------------------------
# Market facade
# ---------------------------------------------------------------------------
class Market:
    def __init__(self):
        s = store.get_many(["upstox", "fyers", "broker_pref"])
        self.errors: list[str] = []
        up, fy = s.get("upstox") or {}, s.get("fyers") or {}
        pref = (s.get("broker_pref") or "auto")
        clients = {}
        if up.get("token") and B.token_valid(up.get("token_created")):
            clients["upstox"] = B.Upstox(up["token"])
        if fy.get("token") and fy.get("app_id") and B.token_valid(fy.get("token_created")):
            clients["fyers"] = B.Fyers(fy["app_id"], fy["token"])
        self.clients = clients
        self.br = clients.get(pref) or clients.get("upstox") or clients.get("fyers")
        self.connected = {k: True for k in clients}

    @property
    def live(self):
        return self.br is not None

    @property
    def source(self):
        return f"LIVE · {self.br.name.title()}" if self.br else "DEMO · Yahoo + theoretical prices"

    def _fail(self, what, e):
        if isinstance(e, B.AuthExpired):
            self.errors.append(str(e))
            self.br = None
        else:
            self.errors.append(f"{what} via {self.br.name.title() if self.br else 'broker'} failed: {e}")

    def key(self, symbol):
        meta = instrument(symbol)
        if self.br and self.br.name == "upstox" and meta["kind"] == "stock":
            return master().get("eq_keys", {}).get(symbol) or meta["upstox_key"]
        return self.br.underlying_key(meta) if self.br else None

    def candles(self, symbol) -> pd.DataFrame:
        if self.br:
            try:
                df = cached(f"c:{self.br.name}:{symbol}", 25, lambda: self.br.candles(self.key(symbol)))
                if not df.empty:
                    return df
            except Exception as e:
                self._fail("Candles", e)
        return cached(f"c:yahoo:{symbol}", 45, lambda: yahoo_candles(instrument(symbol)["yahoo"]))

    def vix(self) -> dict:
        if self.br:
            try:
                q = cached(f"vix:{self.br.name}", 25, lambda: self.br.quotes([self.br.vix_key()]))
                v = next(iter(q.values()))
                if v["ltp"] and v["prev_close"]:
                    return {"level": v["ltp"], "chg_pct": (v["ltp"] / v["prev_close"] - 1) * 100}
            except Exception as e:
                self._fail("VIX", e)

        def yv():
            d = yahoo_daily(C.INDIA_VIX_YAHOO, "5d")["close"].dropna()
            return {"level": float(d.iloc[-1]), "chg_pct": float((d.iloc[-1] / d.iloc[-2] - 1) * 100)}
        try:
            return cached("vix:yahoo", 120, yv)
        except Exception:
            return {"level": 14.0, "chg_pct": 0.0, "assumed": True}

    def expiries_and_lot(self, symbol):
        meta = instrument(symbol)
        if self.br:
            try:
                exps, lot = cached(f"exp:{self.br.name}:{symbol}", 3600, lambda: self.br.expiries(self.key(symbol)))
                if exps:
                    return exps, int(lot or lot_size(symbol))
            except Exception as e:
                self._fail("Expiry list", e)
        return I.demo_expiries(meta["expiry"]), lot_size(symbol)

    def chain(self, symbol, expiry: date, spot, vix, candles=None) -> pd.DataFrame:
        if self.br:
            try:
                ch = cached(f"ch:{self.br.name}:{symbol}:{expiry}", 15, lambda: self.br.chain(self.key(symbol), expiry))
                if ch is not None and not ch.empty:
                    ch = ch.copy()
                    ch.attrs["source"] = "live"
                    return ch
            except Exception as e:
                self._fail("Option chain", e)
        return theoretical_chain(symbol, expiry, spot, vix, candles)

    def spot(self, symbol) -> float | None:
        try:
            c = self.candles(symbol)
            return float(c["close"].iloc[-1]) if not c.empty else None
        except Exception:
            return None

    def leg_prices(self, positions, spots: dict, vix_level: float) -> dict:
        """Current price of each leg. Live broker LTP when the leg came from the
        connected broker; otherwise a Black-Scholes estimate."""
        prices, by_broker = {}, {}
        for p in positions:
            for lg in p["legs"]:
                src = lg.get("src", "demo")
                if src in self.clients:
                    by_broker.setdefault(src, set()).add(lg["key"])
        for name, keys in by_broker.items():
            try:
                q = self.clients[name].quotes(sorted(keys))
                prices.update({k: v["ltp"] for k, v in q.items() if v.get("ltp") is not None})
            except Exception as e:
                self.errors.append(f"Live option prices via {name.title()} failed: {e}")
        for p in positions:
            sp = spots.get(p["symbol"])
            for lg in p["legs"]:
                if lg["key"] in prices or sp is None:
                    continue
                exp = date.fromisoformat(p["expiry"])
                iv = demo_iv(p["symbol"], lg["strike"], sp, vix_level)
                prices[lg["key"]] = round(I.bs_price(sp, lg["strike"], I.years_to_expiry(exp), iv, lg["opt"]), 2)
        return prices
