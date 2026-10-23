"""F&O Paper Trading Trainer - web app (Flask on Vercel)."""
from __future__ import annotations

import hashlib
import json
import hmac
import secrets
import time
import traceback
from urllib.parse import quote
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, request

from fno import brokers as B
from fno import config as C
from fno import service as SV
from fno import scheduler
from fno import store
from fno import recorder as REC
from fno import journal as JR
from fno import push as PUSH
from fno import custom as CU
from fno import ind as XI
from fno import learn as LN
from fno import gbook as GB
from fno import sessions as SS
from fno import datahub as DH
from fno import backup as BK
from fno import markets as MK

app = Flask(__name__)
INDEX_HTML = (Path(__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")
COOKIE = "fno_session"
MAX_AGE = 30 * 24 * 3600


# ---------------------------------------------------------------------------
# Auth: one password (APP_PASSWORD), signed session cookie
# ---------------------------------------------------------------------------
def _sign(exp: int) -> str:
    mac = hmac.new(C.SESSION_SECRET.encode(), str(exp).encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{mac}"


def _authed() -> bool:
    tok = request.cookies.get(COOKIE, "")
    try:
        exp, mac = tok.split(".", 1)
        return int(exp) > time.time() and hmac.compare_digest(_sign(int(exp)), tok)
    except Exception:
        return False


PUBLIC = {"/", "/api/login", "/api/health", "/api/tick", "/api/tick/global", "/api/learn/run", "/api/data/shared", "/api/recorder/day", "/api/recorder/probe", "/api/journal/day"}


LOCKED = {"/api/recorder/day", "/api/recorder/probe", "/api/journal/day"}   # public until LOCK_DATA_ENDPOINTS=1 is set


@app.before_request
def guard():
    if request.path in LOCKED and C.LOCK_DATA:
        ok = _authed() or (C.CRON_SECRET and hmac.compare_digest(request.headers.get("x-cron-secret", ""), C.CRON_SECRET))
        return None if ok else (jsonify({"error": "unauthorised"}), 401)
    if request.path in PUBLIC or not request.path.startswith("/api/"):
        return None
    if not _authed():
        return jsonify({"error": "unauthorised"}), 401
    return None


@app.errorhandler(ValueError)
def on_bad_input(e):
    return jsonify({"error": str(e)}), 400


@app.errorhandler(Exception)
def on_error(e):
    traceback.print_exc()
    return jsonify({"error": str(e) or e.__class__.__name__}), 500


@app.get("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html", headers={"Cache-Control": "no-store"})


SW_JS = """// F&O Trainer service worker: shows push notifications and opens the app when tapped.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("push", e => {
  let d = {title: "F&O Trainer", body: "", url: "/", tag: "fno"};
  try { d = Object.assign(d, e.data.json()); } catch (err) { if (e.data) d.body = e.data.text(); }
  e.waitUntil(self.registration.showNotification(d.title, {body: d.body, tag: d.tag, renotify: true, requireInteraction: true,
    vibrate: [200, 100, 200, 100, 400], data: {url: d.url}, icon: "/icon.svg", badge: "/icon.svg"}));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = new URL((e.notification.data || {}).url || "/", self.location.origin).href;
  e.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(list => {
    for (const c of list) { if (c.url.startsWith(self.location.origin) && "focus" in c) { c.navigate(url); return c.focus(); } }
    return self.clients.openWindow(url);
  }));
});
"""
ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#13202e"/>
<path d="M12 44 L24 30 L33 37 L52 16" fill="none" stroke="#f2a93b" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="52" cy="16" r="5" fill="#f2a93b"/></svg>"""
MANIFEST = {"name": "F&O Trainer", "short_name": "F&O", "start_url": "/", "display": "standalone", "background_color": "#0f1721",
            "theme_color": "#13202e", "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]}


@app.get("/sw.js")
def service_worker():
    return Response(SW_JS, mimetype="application/javascript", headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/icon.svg")
def icon():
    return Response(ICON_SVG, mimetype="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/manifest.webmanifest")
def manifest():
    return Response(json.dumps(MANIFEST), mimetype="application/manifest+json")


@app.get("/api/push/key")
def push_key():
    return jsonify({"configured": PUSH.configured(), "key": PUSH.public_key(), "devices": len(PUSH.subscriptions()) if PUSH.configured() else 0})


@app.post("/api/push/setup")
def push_setup():
    return jsonify({"ok": PUSH.setup()})


@app.post("/api/push/subscribe")
def push_subscribe():
    body = request.get_json(force=True) or {}
    sid = PUSH.subscribe(body.get("subscription"), body.get("label", ""))
    return jsonify({"ok": True, "id": sid, "devices": len(PUSH.subscriptions())})


@app.post("/api/push/unsubscribe")
def push_unsubscribe():
    body = request.get_json(force=True) or {}
    PUSH.unsubscribe(body.get("subscription") or body.get("id") or "")
    return jsonify({"ok": True, "devices": len(PUSH.subscriptions())})


@app.post("/api/push/test")
def push_test():
    import time as _t
    return jsonify(PUSH.send("F&O Trainer test", "Push notifications are working on this device.", event_id=f"test:{int(_t.time())}", url="/"))


STOCKS_URL = "https://raw.githubusercontent.com/Dimansee/FnO/research-data/research/data/stock_picks.json"


@app.get("/api/stocks")
def stocks():
    """Nightly stock picks (round 16 rule) computed by the research job on GitHub; cached 20 minutes."""
    return jsonify(SV.stock_picks())


@app.get("/api/stocks/live")
def stocks_live():
    """Open / last price for the listed symbols (comma separated), used by the Recommendations tab's live check."""
    syms = [x for x in (request.args.get("syms") or "").split(",") if x.strip()]
    if not syms:
        raise ValueError("syms required")
    return jsonify(SV.stock_live(syms))


@app.get("/api/stocks/market")
def stocks_market():
    return jsonify(SV.stock_market())


@app.get("/api/stocks/series")
def stocks_series():
    sym = (request.args.get("sym") or "").strip().upper()
    if not sym or len(sym) > 20:
        raise ValueError("sym required")
    return jsonify(SV.stock_series(sym))


@app.post("/api/login")
def login():
    pw = (request.get_json(silent=True) or {}).get("password", "")
    if not C.APP_PASSWORD or not hmac.compare_digest(pw, C.APP_PASSWORD):
        time.sleep(1)
        return jsonify({"error": "Wrong password"}), 401
    resp = jsonify({"ok": True})
    resp.set_cookie(COOKIE, _sign(int(time.time()) + MAX_AGE), max_age=MAX_AGE, httponly=True,
                    secure=True, samesite="Lax")
    return resp


@app.post("/api/logout")
def logout():
    resp = jsonify({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


def _ap_counts():
    out = {}
    for m in GB.MARKETS:
        q = store.get(f"g_ap_qual:{m}") or {}
        out[m] = {"tested": len(q.get("rows", [])), "passed": sum(1 for r in q.get("rows", []) if r["ok"]), "data_errors": q.get("data_errors"), "at": q.get("at")}
    return out


@app.route("/api/health", methods=["GET", "POST", "HEAD"])   # POST: the 08:50 QStash wake-up call (was a 500 before)
def health():
    from fno import runner as RN
    out = {"ok": True, "db": store.ping(), "time_ist": C.now_ist().isoformat(timespec="seconds"),
           "configured": bool(C.APP_PASSWORD and C.SESSION_SECRET and C.REDIS_URL),
           "scheduler_connected": bool(C.QSTASH_TOKEN), "global_runner": {**RN.state, "enabled": RN.enabled(), "autopilot_pairs": _ap_counts()}}
    if request.args.get("deep") == "1":  # data-source reachability only (counts, no data)
        from fno import market as M
        checks = {
            "yahoo_nifty_bars": lambda: len(M.yahoo_candles("^NSEI")),
            "yahoo_vix": lambda: round(M.Market().vix()["level"], 2),
            "global_markets": lambda: len(SV.gcues()["markets"]),
            "news_items": lambda: len(SV.news("NIFTY")["items"]),
            "fii_auto": lambda: bool(M.cached("fii", 3600, __import__("fno.context").context.fii_dii)),
            "lot_master": lambda: M.master().get("lots", {}),
            "scheduler": lambda: scheduler.ensure(request.host),
        }
        for k, fn in checks.items():
            try:
                out[k] = fn()
            except Exception as e:
                out[k] = f"error: {type(e).__name__}: {str(e)[:120]}"
    return jsonify(out)


# ---------------------------------------------------------------------------
# Data endpoints
# ---------------------------------------------------------------------------
def _sym():
    s = request.args.get("symbol") or (request.get_json(silent=True) or {}).get("symbol") or "NIFTY"
    if s not in SV.ALL_SYMBOLS:
        raise ValueError("Unknown instrument")
    return s


@app.get("/api/meta")
def meta():
    from fno import market as M
    return jsonify({"symbols": [{"id": s, "label": M.instrument(s)["label"]} for s in SV.ALL_SYMBOLS],
                    "rules": {k: getattr(C, k) for k in ("NOISE_MULT", "NOISE_LOOKBACK", "STOP_ATR", "RR_NOISE", "VIX_MIN", "NOISE_VIX_MAX", "CAM_VIX_MAX", "MAX_WEEKLY_LOSS", "MAX_SPREAD_PCT",
                              "SIGNAL_VALID_MIN", "RISK_PER_TRADE", "MAX_DAILY_LOSS", "MAX_TRADES_PER_DAY")}})


@app.get("/api/research")
def research():
    from fno import backtest as BT
    return jsonify(BT.research_summary())


@app.get("/api/research/log")
def research_log():
    import json as _j
    from pathlib import Path
    try:
        log = _j.loads((Path(__file__).parent / "fno" / "research_log.json").read_text())
    except Exception:
        return jsonify({"items": [], "note": "Research log unavailable."})
    try:
        log["pending"] = [dict(p, **_pending_progress(p)) for p in log.get("pending", [])]
    except Exception:
        pass
    return jsonify(log)


def _pending_progress(p: dict) -> dict:
    """Progress of a pending study: sessions / expiries / days collected since it started, and an estimated ready date."""
    from datetime import date as _d, timedelta as _td
    if not p.get("since") or not p.get("need"):
        return {}
    now = C.now_ist(); today = now.date(); start = _d.fromisoformat(p["since"]); unit = p.get("unit", "sessions")
    if unit == "days":
        done = max(0, (today - start).days)
        return {"done": min(done, p["need"]), "eta": (start + _td(days=p["need"])).isoformat()}

    def counts(d):
        if d.weekday() >= 5 or d in C.HOLIDAYS:
            return False
        return d.weekday() == 1 if unit == "expiries" else True
    last = today if now.hour * 60 + now.minute >= 15 * 60 + 30 else today - _td(days=1)
    done, d = 0, start
    while d <= last:
        done += counts(d); d += _td(days=1)
    eta, left, d = None, p["need"] - done, max(start, last + _td(days=1))
    if left <= 0:
        eta = last.isoformat()
    else:
        while left > 0:
            if counts(d):
                left -= 1
                if left == 0:
                    eta = d.isoformat()
            d += _td(days=1)
    return {"done": min(done, p["need"]), "eta": eta}


@app.get("/api/dashboard")
def dashboard():
    try:
        scheduler.ensure(request.host)  # no-op once registered
    except Exception:
        traceback.print_exc()
    k = request.args.get("strike")
    return jsonify(SV.dashboard(_sym(), float(k) if k else None))


@app.get("/api/option_candles")
def option_candles():
    k = request.args.get("strike")
    return jsonify(SV.option_candles(_sym(), request.args.get("expiry") or None, float(k) if k else None,
                                     (request.args.get("opt") or "CE").upper()))


@app.get("/api/candles")
def candles():
    k = request.args.get("strike")
    return jsonify(SV.candles(_sym(), request.args.get("kind") or "UND", float(k) if k else None,
                              request.args.get("expiry") or None))


@app.get("/api/chain")
def chain():
    return jsonify(SV.chain_view(_sym(), request.args.get("expiry") or None))


@app.get("/api/context")
def context():
    return jsonify(SV.context(_sym()))


@app.get("/api/portfolio")
def portfolio():
    return jsonify(SV.portfolio())


# ---------------------------------------------------------------------------
# Scalp book (separate paper ledger, scalping-spec study)
# ---------------------------------------------------------------------------
@app.get("/api/scalp")
def scalp_page():
    from fno import scalp as SC
    return jsonify(SV.clean(SC.page()))


@app.get("/api/scalp/levels")
def scalp_levels():
    from fno import scalp as SC
    from fno import market as M
    sym = request.args.get("symbol", "NIFTY")
    if sym not in C.INDICES:
        return jsonify({"zones": None, "mode": None})
    st = SC.state(sym, M.Market())
    return jsonify(SV.clean({"zones": st.get("zones"), "mode": st.get("mode"), "score": st.get("score")}))


@app.post("/api/scalp/settings")
def scalp_settings():
    from fno import scalp as SC
    return jsonify({"ok": True, "settings": SC.save_settings(request.get_json(force=True) or {})})


@app.post("/api/scalp/exit")
def scalp_exit():
    from fno import scalp as SC
    rec = SC.exit_now(str((request.get_json(force=True) or {}).get("id", "")))
    if not rec:
        return jsonify({"error": "That scalp position is already closed."}), 400
    return jsonify({"ok": True, "trade": SV.clean(rec)})


@app.post("/api/scalp/reset")
def scalp_reset():
    from fno import scalp as SC
    if SC.positions():
        return jsonify({"error": "Close the open scalp position first."}), 400
    SC.reset()
    return jsonify({"ok": True})


@app.post("/api/trade/signal")
def trade_signal():
    k = (request.get_json(silent=True) or {}).get("strike")
    ok, msg = SV.place_signal(_sym(), float(k) if k else None)
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)


@app.post("/api/trade/manual")
def trade_manual():
    b = request.get_json(force=True)
    ok, msg = SV.place_manual(_sym(), b.get("expiry"), float(b["strike"]), b["opt"], b["side"],
                              max(1, min(50, int(b.get("lots", 1)))))
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)


@app.post("/api/trade/exit")
def trade_exit():
    ok, msg = SV.exit_position((request.get_json(force=True) or {}).get("id", ""))
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)


@app.post("/api/account/capital")
def account_capital():
    """Change the demo capital without clearing positions, trades or the journal."""
    cap = float((request.get_json(force=True) or {}).get("capital", C.DEFAULT_CAPITAL))
    from fno import paper as P
    a = P.account()
    if cap == 0:                                   # "profit only": take the starting money out, keep trading the realised profit
        eq0 = P.equity(a, store.trades()) - float(a["capital_start"])
        if eq0 < 10_000:
            return jsonify({"ok": False, "message": f"Capital 0 means trading only the profit already made, which is ₹{eq0:,.0f} - at least ₹10,000 is needed."}), 400
        before = float(a["capital_start"]) if float(a["capital_start"]) > 0 else a.get("capital_before")
        store.put("account", {**a, "capital_start": 0.0, "capital_before": before, "profit_only_since": C.now_ist().isoformat(timespec="seconds")})
        return jsonify({"ok": True, "message": f"Capital set to ₹0: the account now trades only its profit, ₹{eq0:,.0f} (positions and journal kept)."})
    if not 10_000 <= cap <= 100_000_000:
        return jsonify({"ok": False, "message": "Capital must be 0 (trade only your profit) or between ₹10,000 and ₹10 crore."}), 400
    store.put("account", {**{k: v for k, v in a.items() if k not in ("capital_before", "profit_only_since")}, "capital_start": cap})
    return jsonify({"ok": True, "message": f"Demo capital set to ₹{cap:,.0f} (positions and journal kept)."})


@app.get("/api/changes")
def changes_list():
    return jsonify({"changes": SV.changes()})


@app.post("/api/changes/<cid>")
def changes_set(cid):
    b = request.get_json(force=True) or {}
    try:
        msg = SV.revert_change(cid, b.get("on"))
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400
    return jsonify({"ok": True, "message": msg, "changes": SV.changes()})


@app.get("/api/account/origins")
def account_origins():
    from fno import paper as P
    return jsonify(P.origin_summary())


@app.post("/api/account/remove-trades")
def account_remove_trades():
    """Take your own trades out of the P&L (kept aside; Restore brings them back)."""
    from fno import paper as P
    kinds = (request.get_json(force=True) or {}).get("kinds") or ["manual"]
    try:
        r = P.remove_trades(kinds)
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400
    return jsonify({"ok": True, **r, "message": f"Removed {r['removed']} trade(s) (P&L ₹{r['pnl_removed']:,.0f}) from the account. Restore brings them back."})


@app.post("/api/account/restore-trades")
def account_restore_trades():
    from fno import paper as P
    r = P.restore_removed()
    return jsonify({"ok": True, **r, "message": f"Restored {r['restored']} trade(s)."})


@app.post("/api/account/reset")
def account_reset():
    cap = float((request.get_json(force=True) or {}).get("capital", C.DEFAULT_CAPITAL))
    if not 10_000 <= cap <= 100_000_000:
        return jsonify({"ok": False, "message": "Capital must be between ₹10,000 and ₹10 crore."}), 400
    from fno import paper as P
    P.reset(cap)
    return jsonify({"ok": True, "message": f"Demo account reset to ₹{cap:,.0f}."})


@app.get("/api/backtest/portfolio/options")
def backtest_portfolio_options():
    from fno import portfolio_bt as PB
    return jsonify(PB.options())


@app.post("/api/backtest/portfolio")
def backtest_portfolio():
    from fno import portfolio_bt as PB
    return jsonify({"job": PB.start(request.get_json(force=True) or {})})


@app.get("/api/backtest/portfolio/<jid>")
def backtest_portfolio_job(jid):
    from fno import portfolio_bt as PB
    return jsonify(SV.clean(PB.job(jid)))


@app.post("/api/backtest")
def backtest():
    b = request.get_json(force=True) or {}
    mult = float(b.get("mult") or C.NOISE_MULT)
    if not 1.0 <= mult <= 3.0:
        raise ValueError("Band multiplier must be between 1 and 3.")
    sizing = b.get("sizing") or "risk"
    otm = int(b["otm"]) if b.get("otm") is not None else -C.STRIKE_ITM
    capital = float(b.get("capital", C.DEFAULT_CAPITAL))
    strategy = b.get("strategy") or "both"
    days = int(b.get("days") or 60)
    if days not in (30, 60, 90, 120, 180, 250, 500, 750, 950):
        raise ValueError("Pick a backtest period from the list.")
    if sizing not in ("risk", "one_lot", "all_in") or otm not in (-1, 0, 1, 2) or not 1000 <= capital <= 1e9 \
            or strategy not in ("both", "noise", "camarilla", "ai", "trend", "all", "autopilot", "fib"):
        raise ValueError("Invalid backtest settings.")
    return jsonify(SV.backtest(_sym(), mult, capital, sizing, otm, strategy, days))


# ---------------------------------------------------------------------------
# Settings & broker logins
# ---------------------------------------------------------------------------
def _base():
    proto = request.headers.get("X-Forwarded-Proto", "https")
    return f"{proto}://{request.host}"


def _status(cfg, idfield):
    an = bool(cfg.get("analytics_token") and B.analytics_valid(cfg.get("analytics_created")))
    daily = bool(cfg.get("token") and B.token_valid(cfg.get("token_created")))
    return {"configured": bool(cfg.get(idfield) and cfg.get("secret")), "id": cfg.get(idfield, ""),
            "connected": daily or an, "daily_login": daily, "logged_in_at": cfg.get("token_created"),
            "analytics": an, "analytics_days_left": B.analytics_days_left(cfg.get("analytics_created")) if an else None,
            "refresh_ready": bool(cfg.get("refresh_token") and cfg.get("pin")), "refresh_error": cfg.get("refresh_error")}


def _verify_upstox(tok: str) -> dict:
    """Does this (analytics) token really serve what the app needs? Quote, 5-minute candles and the option chain."""
    out = {"quote": False, "candles": False, "chain": False, "detail": ""}
    cl = B.Upstox(tok)
    key = C.INDICES["NIFTY"]["upstox_key"]
    try:
        q = cl.quotes([key])
        out["quote"] = any(v.get("ltp") for v in q.values())
    except Exception as e:
        out["detail"] = str(e)[:140]
        return out
    for name, fn in (("candles", lambda: not cl.candles(key, days=3).empty),
                     ("chain", lambda: bool(cl.expiries(key)[0]))):
        try:
            out[name] = bool(fn())
        except Exception as e:
            out["detail"] += f"{name}: {str(e)[:80]} "
    return out


@app.get("/api/settings")
def settings_get():
    s = store.get_many(["upstox", "fyers", "broker_pref", "fii_manual"])
    return jsonify({
        "upstox": {**_status(s.get("upstox") or {}, "api_key"), "redirect": f"{_base()}/api/upstox/callback"},
        "fyers": {**_status(s.get("fyers") or {}, "app_id"), "redirect": f"{_base()}/api/fyers/callback"},
        "broker_pref": s.get("broker_pref") or "auto", "fii_manual": s.get("fii_manual"), "prefs": SV.prefs(),
    })


@app.post("/api/settings")
def settings_post():
    b = request.get_json(force=True) or {}
    if "broker" in b:
        name = b["broker"]
        if name not in ("upstox", "fyers"):
            return jsonify({"error": "bad broker"}), 400
        cur = store.get(name) or {}
        idf = "api_key" if name == "upstox" else "app_id"
        if b.get("id"):
            if b["id"].strip() != cur.get(idf):
                cur = {}
            cur[idf] = b["id"].strip()
        if b.get("secret"):
            cur["secret"] = b["secret"].strip()
        if b.get("disconnect"):
            for k in ("token", "token_created", "analytics_token", "analytics_created", "refresh_token", "pin", "refresh_error"):
                cur.pop(k, None)
        if name == "upstox" and b.get("analytics_token"):
            tok = str(b["analytics_token"]).strip()
            res = _verify_upstox(tok)
            if not all(res[k] for k in ("quote", "candles", "chain")):
                return jsonify({"error": "This token does not cover everything the app needs (live price, candles, option chain): " + (res["detail"] or str(res)), "checks": res}), 400
            cur["analytics_token"], cur["analytics_created"] = tok, C.now_ist().isoformat(timespec="seconds")
            store.put(name, cur)
            return jsonify({"ok": True, "checks": res})
        if name == "fyers" and "pin" in b:
            pin = str(b.get("pin") or "").strip()
            if pin and not pin.isdigit():
                return jsonify({"error": "The Fyers PIN is digits only."}), 400
            if pin:
                cur["pin"] = pin
            else:
                cur.pop("pin", None)
        store.put(name, cur)
    if "broker_pref" in b and b["broker_pref"] in ("auto", "upstox", "fyers"):
        store.put("broker_pref", b["broker_pref"])
    if "prefs" in b:
        p, cur = b["prefs"] or {}, SV.prefs()
        if p.get("sizing") in ("risk", "one_lot", "all_in"):
            cur["sizing"] = p["sizing"]
        if "risk_pct" in p:
            r = float(p["risk_pct"])
            if not 0.25 <= r <= 5:
                raise ValueError("Risk per trade must be between 0.25% and 5%.")
            cur["risk_pct"] = r
        if "strategies" in p:
            st = [x for x in p["strategies"] if x in ("noise", "camarilla", "ai", "trend", "fib")]
            if not st:
                raise ValueError("Keep at least one strategy switched on.")
            cur["strategies"] = st
        if "auto" in p:
            cur["auto"] = bool(p["auto"])
        if "autopilot" in p:
            cur["autopilot"] = bool(p["autopilot"])
        if p.get("confidence") in ("standard", "strong"):
            cur["confidence"] = p["confidence"]
        if p.get("fills") in ("realistic", "ltp"):
            cur["fills"] = p["fills"]
        if "enforce_limits" in p:
            cur["enforce_limits"] = bool(p["enforce_limits"])
        if "auto_syms" in p:
            cur["auto_syms"] = [x for x in p["auto_syms"] if x in SV.ALL_SYMBOLS][:4]
        if p.get("weekly_limit") in ("auto", "off", "custom"):
            cur["weekly_limit"] = p["weekly_limit"]
        if "weekly_limit_rs" in p:
            v = float(p["weekly_limit_rs"] or 0)
            if cur.get("weekly_limit") == "custom" and not 500 <= v <= 10_000_000:
                raise ValueError("Weekly loss limit must be between ₹500 and ₹1 crore.")
            cur["weekly_limit_rs"] = v
        store.put("prefs", cur)
    if "fii_manual" in b:
        v = b["fii_manual"]
        if v in (None, ""):
            store.delete_setting("fii_manual")
        else:
            store.put("fii_manual", {"value": float(v), "date": C.today_ist().isoformat()})
    return jsonify({"ok": True})


@app.get("/api/<name>/login")
def broker_login(name):
    if name not in ("upstox", "fyers"):
        return "Unknown broker", 404
    cfg = store.get(name) or {}
    idf = "api_key" if name == "upstox" else "app_id"
    if not cfg.get(idf) or not cfg.get("secret"):
        return redirect("/#settings")
    state = secrets.token_urlsafe(16)
    cfg["state"] = state
    store.put(name, cfg)
    redir = f"{_base()}/api/{name}/callback"
    url = B.upstox_login_url(cfg[idf], redir, state) if name == "upstox" else B.fyers_login_url(cfg[idf], redir, state)
    return redirect(url)


@app.get("/api/<name>/callback")
def broker_callback(name):
    if name not in ("upstox", "fyers"):
        return "Unknown broker", 404
    cfg = store.get(name) or {}
    if not request.args.get("state") or request.args.get("state") != cfg.get("state"):
        return redirect("/?msg=" + quote("Login check failed - please try again") + "#settings")
    try:
        redir = f"{_base()}/api/{name}/callback"
        if name == "upstox":
            tok = B.upstox_exchange(request.args["code"], cfg["api_key"], cfg["secret"], redir)
        else:
            code = request.args.get("auth_code") or request.args.get("code")
            full = B.fyers_exchange_full(code, cfg["app_id"], cfg["secret"])
            tok = full["access_token"]
            if full.get("refresh_token"):
                cfg["refresh_token"] = full["refresh_token"]
    except Exception as e:
        return redirect("/?msg=" + quote(f"{name.title()} login failed: {str(e)[:120]}") + "#settings")
    cfg.update({"token": tok, "token_created": C.now_ist().isoformat(timespec="seconds"), "state": None})
    store.put(name, cfg)
    return redirect("/?msg=" + quote(f"Connected to {name.title()}") + "#signal")


# ---------------------------------------------------------------------------
# Scheduler hook (called every minute by Upstash QStash during market hours)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Market recorder
# ---------------------------------------------------------------------------
@app.get("/api/journal")
def journal_days():
    return jsonify({"days": JR.days()})


@app.get("/api/journal/day")
def journal_day():
    """Public, read-only (used by the nightly GitHub job too): the day's context, recommendations with outcomes, AI scores, paper trades."""
    return jsonify(SV.clean(JR.day(request.args.get("date", ""))))


@app.post("/api/journal/scan")
def journal_scan():
    """Record the current recommendations now (same as the scheduler does every 5 minutes)."""
    out = {}
    for sym in JR.SYMS:
        g, ev, order, pf = SV.scan(sym)
        out[sym] = JR.record(sym, ev, order, g, pf)
    return jsonify(out)


@app.get("/api/recorder/status")
def recorder_status():
    return jsonify(SV.clean(REC.status()))


@app.get("/api/recorder/day")
def recorder_day():
    """Public, read-only: recorded market data for one day (used by the nightly GitHub job)."""
    return jsonify(SV.clean(REC.day(request.args.get("date", ""))))


@app.get("/api/flow")
def flow():
    """Options flow from the day's recorded snapshots: GEX profile by strike, OI-change heatmap, plain reading."""
    from fno import flow as FL
    sym = _sym()
    d = request.args.get("date") or C.today_ist().isoformat()
    day = REC.day(d)
    if not day["snapshots"]:
        return jsonify({"empty": True, "reason": "No snapshots yet today - the recorder starts at 09:15; shown from the first snapshot.",
                        "date": d, "symbol": sym})
    prof = FL.gex_profile(day, sym)
    if prof is None:
        return jsonify({"empty": True, "reason": f"No {sym} snapshots recorded on {d} (the recorder covers Nifty and Bank Nifty).",
                        "date": d, "symbol": sym})
    return jsonify(SV.clean({"profile": prof, "heatmap": FL.oi_heatmap(day, sym), "text": FL.read(prof), "date": d, "symbol": sym}))


_probe = {"t": 0.0, "v": None}


@app.get("/api/recorder/probe")
def recorder_probe():
    """Can the server reach NSE's public option chain right now? (cached 60 s)"""
    if time.time() - _probe["t"] > 60:
        try:
            ch = REC.nse_chain("NIFTY")
            v = {"ok": True, "expiries": [(e, sp, len(r)) for e, sp, r in ch],
                 "sample": next((r[len(r) // 2] for _, _, r in ch if r), None), "fields": REC.FIELDS}
        except Exception as e:
            v = {"ok": False, "error": str(e)[:300]}
        _probe.update(t=time.time(), v=v)
    return jsonify(_probe["v"])


@app.route("/api/tick", methods=["GET", "POST"])
def tick():
    if not C.CRON_SECRET or not hmac.compare_digest(request.headers.get("x-cron-secret", ""), C.CRON_SECRET):
        return jsonify({"error": "forbidden"}), 403
    return jsonify(SV.clean(SV.tick()))


# ---------------------------------------------------------------------------
# Custom strategies
# ---------------------------------------------------------------------------
def _cron_or_login():
    ok = _authed() or (C.CRON_SECRET and hmac.compare_digest(request.headers.get("x-cron-secret", ""), C.CRON_SECRET))
    if not ok:
        raise PermissionError("unauthorised")


@app.errorhandler(PermissionError)
def on_denied(e):
    return jsonify({"error": "unauthorised"}), 401


def _markets_meta():
    return {"IN": {"currency": "INR", "symbols": [{"id": s, "label": SV.M.instrument(s)["label"]} for s in SV.ALL_SYMBOLS], "timeframes": ["5m", "15m", "30m", "1h"]},
            "CRYPTO": {"currency": "USDT", "symbols": MK.catalog("CRYPTO"), "timeframes": ["5m", "15m", "30m", "1h", "4h", "1d"]},
            "US": {"currency": "USD", "symbols": MK.catalog("US"), "timeframes": ["5m", "15m", "30m", "1h", "4h", "1d"]},
            "GOLD": {"currency": "USD", "symbols": MK.catalog("GOLD"), "timeframes": ["5m", "15m", "30m", "1h", "4h", "1d"],
                     "sessions": dict(SS.NAMES)}}


@app.get("/api/strategies")
def strategies_get():
    return jsonify({"items": CU.load_all(store), "presets": CU.PRESETS, "meta": XI.meta(), "markets": _markets_meta(),
                    "max": CU.MAX_STRATEGIES, "max_per": "market", "learn_mode": LN.mode(), "learn_modes": LN.modes()})


@app.post("/api/strategies")
def strategies_save():
    spec = CU.upsert(store, request.get_json(force=True) or {})
    return jsonify({"ok": True, "item": spec})


@app.delete("/api/strategies/<sid>")
def strategies_delete(sid):
    return jsonify({"ok": CU.delete(store, sid)})


@app.post("/api/strategies/backtest")
def strategies_backtest():
    b = request.get_json(force=True) or {}
    raw = b.get("spec")
    if not raw and b.get("id"):
        raw = next((x for x in CU.load_all(store) if x["id"] == b["id"]), None)
    if not raw:
        raise ValueError("Send a strategy to test.")
    return jsonify(SV.custom_backtest(raw, b.get("symbol"), int(b.get("days") or 250), float(b.get("capital") or C.DEFAULT_CAPITAL), float(b.get("risk_pct") or 1.0)))


@app.post("/api/strategies/improve")
def strategies_improve():
    b = request.get_json(force=True) or {}
    return jsonify(SV.custom_improve(b.get("id", ""), int(b.get("days") or 250)))


# ---------------------------------------------------------------------------
# Crypto / US paper books
# ---------------------------------------------------------------------------
def _mk():
    m = (request.args.get("market") or (request.get_json(silent=True) or {}).get("market") or "CRYPTO").upper()
    if m not in GB.MARKETS:
        raise ValueError("Market must be CRYPTO, US or GOLD.")
    return m


_last_gtick = {"t": 0.0}


@app.get("/api/gbook")
def gbook_get():
    m = _mk()
    if time.time() - _last_gtick["t"] > 60:                  # the page being open keeps the books fresh between the 5-minute scheduler calls
        _last_gtick["t"] = time.time()
        try:
            GB.tick((m,))
        except Exception:
            traceback.print_exc()
    return jsonify(SV.clean(GB.portfolio(m)))


@app.get("/api/gold/sessions")
def gold_sessions():
    """Gold session board: what is open now (IST), next key events, and per-session range / direction from recent bars."""
    from fno import gold as GLD
    sym = request.args.get("symbol", "XAUUSD")
    if not MK.valid_symbol("GOLD", sym):
        raise ValueError("Unknown gold symbol.")
    return jsonify(SV.clean(GLD.board(sym)))


@app.get("/api/gbook/chart")
def gbook_chart():
    try:
        return jsonify(SV.clean(GB.chart(_mk(), request.args.get("symbol", ""), request.args.get("tf", "5m"))))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


_last_oproc = {}


@app.get("/api/gbook/desk")
def gbook_desk():
    from fno import gorders as GO
    m = _mk()
    now = time.time()
    if now - _last_oproc.get(m, 0) > 10:                     # an open terminal checks pending orders and stops every ~10 s
        _last_oproc[m] = now
        try:
            if GO.open_orders(m):
                GO.process(m)
            if now - _last_gtick["t"] > 30:
                _last_gtick["t"] = now
                GB.tick((m,), manage_only=True)
        except Exception:
            traceback.print_exc()
    return jsonify(SV.clean(GO.desk(m)))


@app.post("/api/gbook/order")
def gbook_order():
    from fno import gorders as GO
    b = request.get_json(force=True) or {}
    try:
        if b.get("preview"):
            return jsonify(SV.clean(GO.preview(_mk(), b)))
        return jsonify(SV.clean(GO.place(_mk(), b)))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/api/gbook/cancel")
def gbook_cancel():
    from fno import gorders as GO
    b = request.get_json(force=True) or {}
    try:
        return jsonify(GO.cancel(_mk(), b.get("id", "")))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/api/gbook/closepart")
def gbook_closepart():
    from fno import gorders as GO
    b = request.get_json(force=True) or {}
    try:
        return jsonify(SV.clean(GO.close_part(_mk(), b.get("id", ""), float(b.get("frac") or 1))))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.get("/api/gbook/ticker")
def gbook_ticker():
    try:
        return jsonify(GB.ticker(_mk(), request.args.get("symbol", "")))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.get("/api/fx")
def fx_rate():
    from fno import gorders as GO
    return jsonify(GO.fx())


@app.post("/api/gbook/manual")
def gbook_manual():
    b = request.get_json(force=True) or {}
    try:
        if b.get("preview"):
            return jsonify(SV.clean(GB.manual_preview(_mk(), b)))
        return jsonify(SV.clean({"ok": True, **GB.manual_open(_mk(), b)}))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.post("/api/gbook/modify")
def gbook_modify():
    b = request.get_json(force=True) or {}
    try:
        return jsonify(GB.modify_position(_mk(), b.get("id", ""), b))
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.get("/api/gbook/journal")
def gbook_journal():
    return jsonify(SV.clean(GB.journal(_mk())))


@app.get("/api/gbook/rules")
def gbook_rules():
    return jsonify(SV.clean(GB.rules(_mk())))


@app.get("/api/gbook/context")
def gbook_context():
    return jsonify(SV.clean(GB.context(_mk())))


@app.post("/api/gbook/prefs")
def gbook_prefs():
    b = request.get_json(force=True) or {}
    return jsonify({"ok": True, "prefs": GB.set_prefs(_mk(), b)})


@app.post("/api/gbook/reset")
def gbook_reset():
    b = request.get_json(force=True) or {}
    GB.reset(_mk(), float(b.get("capital") or 10000))
    return jsonify({"ok": True})


@app.post("/api/gbook/exit")
def gbook_exit():
    b = request.get_json(force=True) or {}
    ok, msg = GB.manual_exit(_mk(), b.get("id", ""))
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/tick/global", methods=["GET", "POST"])
def tick_global():
    if not C.CRON_SECRET or not hmac.compare_digest(request.headers.get("x-cron-secret", ""), C.CRON_SECRET):
        return jsonify({"error": "forbidden"}), 403
    return jsonify(SV.clean(GB.tick()))

# ---------------------------------------------------------------------------
# Data vault
# ---------------------------------------------------------------------------
def _attach(body, name, mime):
    resp = app.response_class(body, mimetype=mime)
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/data")
def data_overview():
    return jsonify(SV.clean(DH.overview()))


@app.post("/api/data/save")
def data_save():
    """'Save now' button: pulls whatever Yahoo still serves for India and US candles into the vault."""
    return jsonify({"IN": DH.save_in(), "US": DH.save_us()})


@app.get("/api/backup")
def backup_status():
    return jsonify(BK.status())


@app.post("/api/backup/url")
def backup_url():
    return jsonify(BK.set_url((request.get_json(force=True) or {}).get("url", "")))


@app.get("/api/backup/script")
def backup_script():
    return Response(BK.script_text(), mimetype="text/plain")


@app.post("/api/backup/run")
def backup_run():
    r = BK.run()
    return jsonify(r), (200 if r.get("ok") else 400)


@app.get("/api/data/export")
def data_export():
    q = request.args
    return _attach(*DH.build(q.get("dataset", ""), q.get("symbol"), q.get("start", ""), q.get("end", ""), q.get("fmt", "csv"), q.get("tf", "1h")))


@app.post("/api/data/share")
def data_share():
    b = request.get_json(force=True) or {}
    DH.build(b.get("dataset", ""), b.get("symbol"), b.get("start", ""), b.get("end", ""), b.get("fmt", "csv"), b.get("tf", "1h"))     # fail now if the range is empty / too big
    tok = DH.make_token(b)
    return jsonify({"url": request.host_url.rstrip("/") + "/api/data/shared?t=" + tok, "valid_days": DH.SHARE_DAYS})


@app.get("/api/data/shared")
def data_shared():
    p = DH.read_token(request.args.get("t", ""))
    return _attach(*DH.build(p["dataset"], p["symbol"], p["start"], p["end"], p["fmt"], p["tf"]))


# ---------------------------------------------------------------------------
# Learning
# ---------------------------------------------------------------------------
@app.get("/api/learn")
def learn_get():
    m = request.args.get("market")
    return jsonify(SV.clean(LN.summary(m if m in LN.MARKET_KEYS else None)))


@app.post("/api/learn/mode")
def learn_mode():
    b = request.get_json(force=True) or {}
    mk = b.get("market") if b.get("market") in LN.MARKET_KEYS else None
    LN.set_mode(b.get("mode", ""), mk)
    return jsonify({"ok": True, "mode": LN.mode(mk), "modes": LN.modes()})


@app.route("/api/learn/run", methods=["GET", "POST"])
def learn_run():
    """After-close research. Called by the 15:35 scheduler tick, the nightly job, or the 'Run now' button."""
    _cron_or_login()
    b = request.get_json(silent=True) or {}
    m = (request.args.get("market") or b.get("market") or "IN").upper()
    force = (request.args.get("force") or b.get("force")) in (True, "1", 1, "true")
    day = request.args.get("date") or b.get("date")
    out = LN.post_close_in(day, force) if m == "IN" else LN.post_close_g(m, day, force)
    return jsonify(SV.clean(out))


@app.post("/api/learn/revert")
def learn_revert():
    return jsonify({"ok": LN.revert((request.get_json(force=True) or {}).get("id", ""))})


@app.post("/api/learn/apply")
def learn_apply():
    return jsonify({"ok": LN.apply_pending((request.get_json(force=True) or {}).get("id", ""))})


# Crypto / US books: background loop on the always-on host (no-op on Vercel and in tests).
from fno import runner as _runner   # started by the first request (not at import time, so no import-lock races in the worker)


@app.before_request
def _ensure_runner():          # belt and braces: the first request after a boot starts the loop if import-time start did not
    try:
        _runner.start()
    except Exception:
        pass
