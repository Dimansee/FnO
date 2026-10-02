"""Outside-market context: global cues, macro, news headlines, FII/DII flows.

All sources are free and public. Each function fails soft (returns what it
could get plus a note) so the dashboard keeps working if one site is down.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import requests

from . import config as C

# --- simple, transparent news lexicon (no black box) -----------------------
POSITIVE = {
    "rally": 2, "rallies": 2, "surge": 2, "surges": 2, "soar": 2, "soars": 2, "record high": 2,
    "jumps": 1.5, "gains": 1, "gain": 1, "rises": 1, "rise": 1, "climbs": 1, "higher": 1,
    "upgrade": 1.5, "beats": 1.5, "strong": 1, "bullish": 1.5, "inflow": 1.5, "inflows": 1.5,
    "buying": 1, "rate cut": 1.5, "eases": 1, "ceasefire": 1.5, "deal": 0.5, "recovery": 1,
    "outperform": 1, "boost": 1, "optimism": 1, "green": 0.5,
}
NEGATIVE = {
    "crash": 2.5, "plunge": 2, "plunges": 2, "slump": 2, "selloff": 2, "sell-off": 2, "tumble": 2,
    "tumbles": 2, "falls": 1, "fall": 1, "drops": 1, "slips": 1, "lower": 1, "declines": 1,
    "downgrade": 1.5, "misses": 1.5, "weak": 1, "bearish": 1.5, "outflow": 1.5, "outflows": 1.5,
    "selling": 1, "war": 2, "attack": 1.5, "tariff": 1, "tariffs": 1, "sanction": 1, "sanctions": 1,
    "recession": 2, "inflation": 0.5, "rate hike": 1.5, "fear": 1, "volatile": 0.5, "red": 0.5,
    "fraud": 2, "probe": 1, "default": 2,
}
EVENTS = {
    "RBI policy": r"\b(rbi|mpc)\b.*\b(policy|repo|rate)",
    "US Fed": r"\b(fed|fomc|powell)\b",
    "Budget": r"\bunion budget\b|\bbudget 20\d\d\b",
    "Inflation data": r"\b(cpi|wpi|inflation data)\b",
    "Election results": r"\belection results?\b|\bcounting day\b",
    "Results / earnings": r"\b(q[1-4] results?|earnings|quarterly results?)\b",
}


def score_headline(text: str) -> float:
    t = text.lower()
    s = 0.0
    for w, v in POSITIVE.items():
        if re.search(rf"\b{re.escape(w)}\b", t):
            s += v
    for w, v in NEGATIVE.items():
        if re.search(rf"\b{re.escape(w)}\b", t):
            s -= v
    return s


def fetch_news(stock: str | None = None, limit: int = 25) -> dict:
    import feedparser
    feeds = [C.NEWS_FEED_STOCK.format(q=stock)] if stock else C.NEWS_FEEDS_MARKET
    items, seen, notes = [], set(), []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=36 if stock else 18)
    for url in feeds:
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            f = feedparser.parse(r.content)
            for e in f.entries:
                title = re.sub(r"\s+-\s+[^-]+$", "", e.get("title", "")).strip()  # drop " - Source"
                key = title.lower()[:80]
                if not title or key in seen:
                    continue
                pub = e.get("published_parsed") or e.get("updated_parsed")
                when = datetime(*pub[:6], tzinfo=timezone.utc) if pub else None
                if when and when < cutoff:
                    continue
                seen.add(key)
                items.append({"title": title, "link": e.get("link"), "when": when,
                              "score": score_headline(title)})
        except Exception as ex:
            notes.append(f"News feed unavailable: {url.split('/')[2]} ({type(ex).__name__})")
    items.sort(key=lambda x: x["when"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    items = items[:limit]
    total = sum(i["score"] for i in items)
    events = sorted({name for name, rx in EVENTS.items() for i in items if re.search(rx, i["title"].lower())})
    return {"items": items, "score": round(total, 1), "events": events, "notes": notes}


def global_cues() -> dict:
    """% change of each market's latest session vs the previous close."""
    import yfinance as yf
    out, notes = {}, []
    tickers = {**C.GLOBAL_TICKERS, **C.MACRO_TICKERS}
    for name, t in tickers.items():
        try:
            h = yf.Ticker(t).history(period="7d", interval="1d")["Close"].dropna()
            if len(h) >= 2:
                out[name] = {"last": float(h.iloc[-1]), "chg_pct": float((h.iloc[-1] / h.iloc[-2] - 1) * 100),
                             "date": h.index[-1].date().isoformat()}
        except Exception as ex:
            notes.append(f"{name}: {type(ex).__name__}")
    eq = [out[k]["chg_pct"] for k in C.GLOBAL_TICKERS if k in out]
    avg = sum(eq) / len(eq) if eq else 0.0
    return {"markets": out, "avg_equity_chg": round(avg, 2), "notes": notes}


def fii_dii() -> dict | None:
    """Previous day's FII/DII cash-market net (₹ crore) from NSE. NSE often
    blocks automated requests; when it does, the app asks you to type it in."""
    try:
        s = requests.Session()
        h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json",
             "Referer": "https://www.nseindia.com/reports/fii-dii"}
        s.get("https://www.nseindia.com", headers=h, timeout=8)
        r = s.get("https://www.nseindia.com/api/fiidiiTradeReact", headers=h, timeout=8)
        rows = r.json()
        res = {}
        for row in rows:
            cat = row.get("category", "").upper()
            net = float(str(row.get("netValue", "0")).replace(",", ""))
            if "FII" in cat or "FPI" in cat:
                res["fii"] = net
            elif "DII" in cat:
                res["dii"] = net
            res["date"] = row.get("date")
        return res if "fii" in res else None
    except Exception:
        return None
