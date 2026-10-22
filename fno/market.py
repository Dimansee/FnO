"""Unified market-data layer: picks the connected broker (Upstox or Fyers),
falls back to Yahoo Finance + theoretical (Black-Scholes) option prices."""
from __future__ import annotations

import gzip
import json
import time as _t
from datetime import date, datetime, timedelta

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


def upstox_public_history(key: str, cal_days: int) -> pd.DataFrame:
    """Completed 5-minute candles from Upstox's public historical API (no login needed),
    asked one calendar month at a time (the API rejects some cross-month ranges)."""
    import requests
    from urllib.parse import quote
    end = C.today_ist() - timedelta(days=1)
    start = end - timedelta(days=cal_days)
    rows, a = [], start
    sess = requests.Session()
    while a <= end:
        b = min(date(a.year + (a.month == 12), a.month % 12 + 1, 1) - timedelta(days=1), end)
        r = sess.get(f"https://api.upstox.com/v3/historical-candle/{quote(key, safe='')}/minutes/5/{b}/{a}",
                     headers={"Accept": "application/json"}, timeout=20)
        if r.status_code == 200:
            rows += r.json().get("data", {}).get("candles", [])
        a = b + timedelta(days=1)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([x[:6] for x in rows], columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(IST)
    df = df.drop_duplicates("ts").set_index("ts").sort_index().astype(float)
    t = df.index.time
    return df[(t >= C.MARKET_OPEN) & (t < C.MARKET_CLOSE)]


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
def slip_pct(premium: float) -> float:
    """Typical half-spread cost when no bid/ask is available: cheap options are proportionally the most expensive to trade."""
    return 0.02 if premium < 20 else 0.012 if premium < 60 else 0.006


class Market:
    def __init__(self):
        s = store.get_many(["upstox", "fyers", "broker_pref"])
        self.errors: list[str] = []
        up, fy = s.get("upstox") or {}, s.get("fyers") or {}
        pref = (s.get("broker_pref") or "auto")
        clients = {}
        self.token_kind: dict[str, str] = {}
        self.ba: dict[str, tuple] = {}                       # option key -> (best bid, best ask) from the last quote call
        if up.get("token") and B.token_valid(up.get("token_created")):
            clients["upstox"] = B.Upstox(up["token"])
            self.token_kind["upstox"] = "daily"
        elif up.get("analytics_token") and B.analytics_valid(up.get("analytics_created")):
            clients["upstox"] = B.Upstox(up["analytics_token"])      # one-year, read-only: all the paper trading needs
            self.token_kind["upstox"] = "analytics"
        fy = self._fyers_refresh_if_needed(fy)
        if fy.get("token") and fy.get("app_id") and B.token_valid(fy.get("token_created")):
            clients["fyers"] = B.Fyers(fy["app_id"], fy["token"])
            self.token_kind["fyers"] = "daily"
        self.clients = clients
        self.br = clients.get(pref) or clients.get("upstox") or clients.get("fyers")
        self.connected = {k: True for k in clients}

    def _fyers_refresh_if_needed(self, fy: dict) -> dict:
        """Today's Fyers token is missing/expired but a refresh token + PIN were saved: get a new one silently."""
        try:
            if fy.get("token") and B.token_valid(fy.get("token_created")):
                return fy
            if not (fy.get("refresh_token") and fy.get("pin") and fy.get("app_id") and fy.get("secret")):
                return fy
            last = fy.get("refresh_tried")
            if last and (C.now_ist() - datetime.fromisoformat(last)).total_seconds() < 600:     # do not hammer a failing refresh
                return fy
            fy = dict(fy)
            fy["refresh_tried"] = C.now_ist().isoformat(timespec="seconds")
            try:
                fy["token"] = B.fyers_refresh(fy["app_id"], fy["secret"], fy["refresh_token"], fy["pin"])
                fy["token_created"] = C.now_ist().isoformat(timespec="seconds")
                fy["refresh_error"] = None
            except Exception as e:
                fy["refresh_error"] = str(e)[:120]
            store.put("fyers", fy)
        except Exception:
            pass
        return fy

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
                    return self._fill_gaps(df, symbol)
            except Exception as e:
                self._fail("Candles", e)
        # 30 days: the noise band needs the last 14 sessions
        return cached(f"c:yahoo:{symbol}", 45, lambda: yahoo_candles(instrument(symbol)["yahoo"], period="30d"))

    def vix_candles(self) -> pd.Series | None:
        """Today's 5-minute India VIX closes (the AI judge's per-bar VIX). Broker first, else Yahoo; None if unavailable."""
        def get():
            df = None
            if self.br:
                try:
                    df = self.br.candles(self.br.vix_key(), days=2)
                except Exception:
                    df = None
            if df is None or df.empty:
                df = yahoo_candles(C.INDIA_VIX_YAHOO, period="1d", interval="5m")
            return df
        try:
            df = cached(f"vixc:{self.br.name if self.br else 'yahoo'}", 60, get)
            if df is None or df.empty:
                return None
            today = C.today_ist()
            s = df["close"][df.index.date == today]
            return s if len(s) else None
        except Exception:
            return None

    def candles1(self, symbol) -> pd.DataFrame:
        """Today's 1-minute candles (scalp book): broker first, else Yahoo (delayed)."""
        if self.br and hasattr(self.br, "candles1"):
            try:
                df = cached(f"c1:{self.br.name}:{symbol}", 20, lambda: self.br.candles1(self.key(symbol)))
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                self._fail("1-minute candles", e)
        try:
            df = cached(f"c1:yahoo:{symbol}", 45, lambda: yahoo_candles(instrument(symbol)["yahoo"], period="1d", interval="1m"))
            return df if df is not None else pd.DataFrame()
        except Exception as e:
            self.errors.append(f"1-minute candles (Yahoo) failed: {e}")
            return pd.DataFrame()

    def _fill_gaps(self, df: pd.DataFrame, symbol) -> pd.DataFrame:
        """The broker's history can lag a session behind (it showed Wednesday's close on Friday night).
        Any recent session it lacks is filled from Yahoo; broker bars always win where both exist."""
        try:
            now = C.now_ist()
            have = set(df.index.date)
            yh = cached(f"c:yahoo5:{symbol}", 300, lambda: yahoo_candles(instrument(symbol)["yahoo"], period="7d"))
            if yh is None or yh.empty:
                return df
            miss = [d for d in set(yh.index.date) - have
                    if d > min(have) and (d < now.date() or now.time() >= C.MARKET_CLOSE)]
            if not miss:
                return df
            add = yh[[d in miss for d in yh.index.date]]
            add = add[(add.index.time >= C.MARKET_OPEN) & (add.index.time < C.MARKET_CLOSE)]
            out = pd.concat([df, add[df.columns.intersection(add.columns)]])
            return out[~out.index.duplicated(keep="first")].sort_index()
        except Exception:
            return df

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
        return I.demo_expiries(meta["expiry"], exchange=meta.get("exchange", "NSE")), lot_size(symbol)

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

    def exit_fills(self, positions, prices: dict, mode: str = "realistic") -> dict:
        """What closing would really fetch: sell a bought option at the bid, buy back a sold one at the ask.
        If the broker gave no depth, fall back to a spread-sized haircut. Only legs priced from the live broker are touched."""
        if mode != "realistic":
            return prices
        out = dict(prices)
        for p in positions:
            for lg in p["legs"]:
                k = lg["key"]
                ltp = prices.get(k)
                if ltp is None or lg.get("src", "demo") not in self.clients or lg.get("price_src") == "formula":
                    continue
                bid, ask = self.ba.get(k, (None, None))
                s = slip_pct(ltp)
                if lg["side"] == "BUY":
                    fill = min(ltp, bid) if bid and bid >= 0.5 * ltp else ltp * (1 - s)
                else:
                    fill = max(ltp, ask) if ask and ask <= 1.5 * ltp else ltp * (1 + s)
                out[k] = round(max(fill, 0.05), 2)
        return out

    def leg_prices(self, positions, spots: dict, vix_level: float) -> dict:
        """Current price of each leg. Live broker LTP when the leg came from the
        connected broker; otherwise a Black-Scholes estimate."""
        prices, by_broker = {}, {}
        if self.br:                                          # legs opened on theoretical prices: switch them to the live contract
            for p in positions:
                for lg in p["legs"]:
                    if lg.get("src", "demo") in self.clients:
                        continue
                    try:
                        ch = self.chain(p["symbol"], date.fromisoformat(p["expiry"]), spots.get(p["symbol"]), vix_level)
                        if ch is None or ch.empty or ch.attrs.get("source") != "live":
                            continue
                        row = ch[ch["strike"] == float(lg["strike"])]
                        key = row.iloc[0].get(f"{lg['opt'].lower()}_key") if len(row) else None
                        if key:
                            lg["key"], lg["src"] = key, self.br.name
                            p["_relinked"] = True
                    except Exception as e:
                        self.errors.append(f"Could not link {p['symbol']} {lg['strike']:g}{lg['opt']} to the live contract: {e}")
        for p in positions:
            for lg in p["legs"]:
                src = lg.get("src", "demo")
                if src in self.clients:
                    by_broker.setdefault(src, set()).add(lg["key"])
        for name, keys in by_broker.items():
            try:
                q = self.clients[name].quotes(sorted(keys))
                prices.update({k: v["ltp"] for k, v in q.items() if v.get("ltp") is not None})
                self.ba.update({k: (v.get("bid"), v.get("ask")) for k, v in q.items()})
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
                lg["price_src"] = "formula"
        for p in positions:
            if p.pop("_relinked", False):
                try:
                    store.update_position(p)
                except Exception:
                    pass
        return prices
