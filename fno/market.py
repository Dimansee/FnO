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
import threading as _th
_klocks: dict = {}
_klock = _th.Lock()


def _fresh(hit, ttl, minute):
    now = _t.time()
    return hit and now - hit[0] < ttl and (not minute or int(hit[0] // 60) == int(now // 60))


def cached(key, ttl, fn, minute=False):
    """minute=True: also refetch as soon as a new clock minute starts, so the every-minute tick always sees this minute's
    data, while page refreshes inside the same minute reuse it (no extra broker call)."""
    hit = _cache.get(key)
    if _fresh(hit, ttl, minute):
        return hit[1]
    with _klock:
        lk = _klocks.setdefault(key, _th.Lock())
    with lk:                                   # one fetch per key at a time: other threads wait and reuse it
        hit = _cache.get(key)
        if _fresh(hit, ttl, minute):
            return hit[1]
        val = fn()
        _cache[key] = (_t.time(), val)
        if len(_cache) > 1500:                 # long-running server: drop entries untouched for 6 h so memory stays flat
            cut = _t.time() - 6 * 3600
            for k in [k for k, v in list(_cache.items()) if v[0] < cut]:
                _cache.pop(k, None)
        return val


def cached_soft(key, ttl, fn, fail_ttl=300):
    """Like cached(), but a failure is remembered too (re-raised for fail_ttl seconds) so a dead source isn't retried on
    every page load."""
    hit = _cache.get("!" + key)
    if hit and _t.time() - hit[0] < fail_ttl:
        raise hit[1]
    try:
        return cached(key, ttl, fn)
    except Exception as e:
        _cache["!" + key] = (_t.time(), e)
        raise


def stale(key, max_age=1800):
    """The last value fetched for a key, even past its TTL (used when the source fails), or None."""
    hit = _cache.get(key)
    return hit[1] if hit and _t.time() - hit[0] < max_age else None


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


NO_LIVE_PRICE = ("No live option prices from the broker right now, so no order is placed on an estimated price. "
                 "Log in to Fyers / Upstox (Settings) - orders resume by themselves once live prices are back.")


def live_needed(m, chain) -> bool:
    """True when a new order must be refused: a broker is set up on this app but this option chain is not the broker's."""
    return bool(getattr(m, "broker_setup", False)) and not (chain is not None and chain.attrs.get("source") == "live" and getattr(m, "br", None))


class Market:
    def __init__(self):
        s = store.get_many(["upstox", "fyers", "broker_pref"])
        self.errors: list[str] = []
        up, fy = s.get("upstox") or {}, s.get("fyers") or {}
        pref = (s.get("broker_pref") or "auto")
        clients = {}
        # A broker has been set up on this app: new orders then need the broker's live option prices (never a formula price).
        self.broker_setup = bool(up.get("token") or up.get("analytics_token") or up.get("api_key") or fy.get("app_id") or fy.get("token"))
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
        if self.br is not None and B.limited(self.br.name):      # paused / daily budget used: the other broker serves data
            other = next((c for n, c in clients.items() if n != self.br.name and not B.limited(n)), None)
            if other is not None:
                self.br = other
        self.connected = {k: True for k in clients}

    def _switch(self) -> bool:
        """After a rate-limit reply: move this request to the other connected broker (if it isn't limited too)."""
        if self.br is None or getattr(self, "_switched", False):     # once per request: never ping-pong between two busy brokers
            return False
        other = next((c for n, c in (getattr(self, "clients", None) or {}).items() if n != self.br.name and not B.limited(n)), None)
        if other is None:
            return False
        self._switched = True
        self.br = other
        return True

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
        elif isinstance(e, B.RateLimited):
            nm = self.br.name.title() if self.br else "The broker"
            left = B.upstox_cooldown_left() if nm == "Upstox" else B.fyers_cooldown_left()
            when = (f"retrying in about {max(1, round(left / 60))} min" if left < 3 * 3600 else "back tomorrow (daily limit)")
            msg = (f"{nm} is limiting requests right now - showing the last fetched data (or Yahoo) and {when}. "
                   f"Nothing to do on your side.")
            if msg not in self.errors:
                self.errors.append(msg)
        else:
            self.errors.append(f"{what} via {self.br.name.title() if self.br else 'broker'} failed: {e}")

    def key(self, symbol):
        meta = instrument(symbol)
        if self.br and self.br.name == "upstox" and meta["kind"] == "stock":
            return master().get("eq_keys", {}).get(symbol) or meta["upstox_key"]
        return self.br.underlying_key(meta) if self.br else None

    def candles(self, symbol) -> pd.DataFrame:
        if self.br:
            ck = f"c:{self.br.name}:{symbol}"
            try:
                df = cached(ck, 25, lambda: self.br.candles(self.key(symbol)), minute=True)
                if not df.empty:
                    return self._fill_gaps(df, symbol)
            except Exception as e:
                if isinstance(e, B.RateLimited) and self._switch():
                    return self.candles(symbol)
                self._fail("Candles", e)
                old = stale(ck, 120)                  # bridge a short broker hiccup only; after 2 min use Yahoo (moving, if delayed)
                if old is not None and not old.empty:
                    return self._fill_gaps(old, symbol)
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
            df = cached(f"vixc:{self.br.name if self.br else 'yahoo'}", 60, get, minute=True)
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
                if isinstance(e, B.RateLimited) and self._switch():
                    return self.candles1(symbol)
                self._fail("1-minute candles", e)
                old = stale(f"c1:{self.br.name}:{symbol}", 600)
                if old is not None and not old.empty:
                    return old
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
                q = cached(f"vix:{self.br.name}", 30, lambda: self.br.quotes([self.br.vix_key()]), minute=True)
                v = next(iter(q.values()))
                if v["ltp"] and v["prev_close"]:
                    return {"level": v["ltp"], "chg_pct": (v["ltp"] / v["prev_close"] - 1) * 100}
            except Exception as e:
                if isinstance(e, B.RateLimited) and self._switch():
                    return self.vix()
                old = stale(f"vix:{self.br.name}", 600) if self.br else None
                if old:
                    v = next(iter(old.values()))
                    if v.get("ltp") and v.get("prev_close"):
                        self._fail("VIX", e)
                        return {"level": v["ltp"], "chg_pct": (v["ltp"] / v["prev_close"] - 1) * 100}
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
                if isinstance(e, B.RateLimited) and self._switch():
                    return self.expiries_and_lot(symbol)
                self._fail("Expiry list", e)
        return I.demo_expiries(meta["expiry"], exchange=meta.get("exchange", "NSE")), lot_size(symbol)

    def _real_contract(self, symbol, exp: date):
        """(broker client, its contract date for `exp`) from the first connected broker that can say: the same date when it
        is listed, else the listed expiry within 3 days of it (an expiry on a holiday moves to the previous trading day)."""
        meta = instrument(symbol)
        for c in [self.br] + [x for x in (getattr(self, "clients", None) or {}).values() if x is not self.br]:
            if c is None or B.limited(c.name):
                continue
            try:
                exps, _ = cached(f"exp:{c.name}:{symbol}", 3600, lambda c=c: c.expiries(c.underlying_key(meta)))
            except Exception:
                continue
            if not exps:
                continue
            if exp in exps:
                return c, exp
            near = [e for e in exps if abs((e - exp).days) <= 3]
            return c, (min(near, key=lambda e: (abs((e - exp).days), e)) if near else None)
        return None, None

    def real_expiry(self, symbol, exp: date) -> date | None:
        return self._real_contract(symbol, exp)[1]

    def fix_contract(self, p: dict, spots: dict, vix_level: float) -> bool:
        """An open position whose expiry is not a real contract date (opened on formula prices with an estimated expiry
        list, 16 Nov 2026: dated 24 Nov, a holiday): link every leg to the real contract (23 Nov), and take the entry
        price from the broker's 1-minute candle of that contract at the minute it was opened."""
        exp = date.fromisoformat(p["expiry"])
        c, real = self._real_contract(p["symbol"], exp)
        if c is None or real is None or real == exp:
            return False
        try:
            ch = cached(f"ch:{c.name}:{p['symbol']}:{real}", 30, lambda: c.chain(c.underlying_key(instrument(p["symbol"])), real), minute=True)
        except Exception:
            return False
        if ch is None or ch.empty:
            return False
        changed = False
        for lg in p["legs"]:
            row = ch[ch["strike"] == float(lg["strike"])]
            key = row.iloc[0].get(f"{lg['opt'].lower()}_key") if len(row) else None
            if not key:
                return False
            if lg.get("key") != key or lg.get("src") != c.name:
                lg["key"], lg["src"] = key, c.name
                changed = True
        p["contract_fixed"] = {"at": C.now_ist().isoformat(timespec="seconds"), "expiry_was": p["expiry"], "expiry": str(real),
                               "why": "The trade was opened on an estimated price while no broker option prices were available; "
                                      "it is now linked to the real contract."}
        p["expiry"] = str(real)
        try:                                                 # the real price at the minute of entry (single bought option only)
            if len(p["legs"]) == 1 and p["legs"][0]["side"] == "BUY" and p["opened"][:10] == C.today_ist().isoformat():
                lg = p["legs"][0]
                bars = c.candles1(lg["key"])
                t0 = pd.Timestamp(p["opened"]).floor("min")
                at = bars[bars.index >= t0] if bars is not None and len(bars) else None
                if at is not None and len(at) and (at.index[0] - t0) <= pd.Timedelta(minutes=5):
                    px = float(at["open"].iloc[0])
                    if px > 0:
                        old = float(lg["price"])
                        new = round(px * (1 + slip_pct(px)), 2)
                        k = new / old if old > 0 else 1.0
                        lg["price"] = new
                        p["entry_prem"] = new
                        p["margin"] = round(new * p["qty"], 0)
                        for f in ("sl_prem", "target_prem", "m_sl", "m_target"):
                            v = p.get(f)
                            if isinstance(v, (int, float)) and 0 < abs(v) < 1e8:
                                p[f] = round(v * k, 2)
                        p["contract_fixed"].update(entry_was=old, entry=new, entry_from=f"{c.name.title()} 1-minute candle {at.index[0]:%H:%M} open + usual slippage")
        except Exception as e:
            self.errors.append(f"Could not read the real entry price of {p['symbol']}: {e}")
        return True

    def chain(self, symbol, expiry: date, spot, vix, candles=None) -> pd.DataFrame:
        if self.br:
            try:
                ck = f"ch:{self.br.name}:{symbol}:{expiry}"
                ch = cached(ck, 30, lambda: self.br.chain(self.key(symbol), expiry), minute=True)
                if ch is not None and not ch.empty:
                    ch = ch.copy()
                    ch.attrs["source"] = "live"
                    return ch
            except Exception as e:
                if isinstance(e, B.RateLimited) and self._switch():
                    return self.chain(symbol, expiry, spot, vix, candles)
                self._fail("Option chain", e)
                old = stale(f"ch:{self.br.name}:{symbol}:{expiry}", 90) if self.br else None
                if old is not None and not old.empty:          # a short pause: last minute's live chain beats the formula
                    ch = old.copy()
                    ch.attrs["source"] = "live"
                    return ch
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
                tol = max(0.15 * ltp, 2.0)                   # a quote far from the last trade is stale / thin: use the spread estimate
                if lg["side"] == "BUY":
                    fill = min(ltp, bid) if bid and bid >= ltp - tol else ltp * (1 - s)
                else:
                    fill = max(ltp, ask) if ask and ask <= ltp + tol else ltp * (1 + s)
                out[k] = round(max(fill, 0.05), 2)
        return out

    def leg_prices(self, positions, spots: dict, vix_level: float) -> dict:
        """Current price of each leg. Live broker LTP when the leg came from the
        connected broker; otherwise a Black-Scholes estimate."""
        prices, by_broker = {}, {}
        for p in positions:
            for lg in p["legs"]:
                lg.pop("price_src", None)                    # worked out afresh on every call
        if self.br or getattr(self, "clients", None):        # a position dated on an expiry that is not a real contract: repair it first
            for p in positions:
                try:
                    if self.fix_contract(p, spots, vix_level):
                        p["_relinked"] = True
                except Exception as e:
                    self.errors.append(f"Could not check the contract of {p.get('symbol')}: {e}")
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
                ks = sorted(keys)                    # the page refresh and the minute tick ask for the same legs: reuse for 10 s
                q = cached(f"q:{name}:{hash(tuple(ks))}", 20, lambda: self.clients[name].quotes(ks))
                prices.update({k: v["ltp"] for k, v in q.items() if v.get("ltp") is not None})
                self.ba.update({k: (v.get("bid"), v.get("ask")) for k, v in q.items()})
            except Exception as e:
                self.errors.append(f"Live option prices via {name.title()} failed: {e}")
        # 26 Oct 2026: a leg whose own broker gave no price (rate limit, session ended, key from the other broker) was priced
        # by the formula but still shown as "live broker price" (Rs 116 vs a real ~Rs 170). Next best: the LTP of the same
        # strike in the connected broker's live option chain; the formula is the last resort and is labelled as such.
        for p in positions:
            sp = spots.get(p["symbol"])
            for lg in p["legs"]:
                if lg["key"] in prices or not self.br:
                    continue
                try:
                    ch = self.chain(p["symbol"], date.fromisoformat(p["expiry"]), sp, vix_level)
                    if ch is not None and not ch.empty and ch.attrs.get("source") == "live":
                        row = ch[ch["strike"] == float(lg["strike"])]
                        col = f"{lg['opt'].lower()}_ltp"
                        v = float(row.iloc[0][col]) if len(row) and col in row and row.iloc[0][col] == row.iloc[0][col] else None
                        if v and v > 0:
                            prices[lg["key"]] = v
                            lg["price_src"] = "chain"
                            b, a = row.iloc[0].get(f"{lg['opt'].lower()}_bid"), row.iloc[0].get(f"{lg['opt'].lower()}_ask")
                            self.ba[lg["key"]] = (b if b == b else None, a if a == a else None)
                except Exception as e:
                    self.errors.append(f"Option chain price for {p['symbol']} {lg['strike']:g}{lg['opt']} failed: {e}")
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
