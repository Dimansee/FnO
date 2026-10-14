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


PUBLIC = {"/", "/api/login", "/api/health", "/api/tick", "/api/recorder/day", "/api/recorder/probe", "/api/journal/day"}


@app.before_request
def guard():
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
    return jsonify({"configured": PUSH.configured(), "key": PUSH.PUBLIC_KEY, "devices": len(PUSH.subscriptions()) if PUSH.configured() else 0})


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


@app.get("/api/health")
def health():
    out = {"ok": True, "db": store.ping(), "time_ist": C.now_ist().isoformat(timespec="seconds"),
           "configured": bool(C.APP_PASSWORD and C.SESSION_SECRET and C.REDIS_URL),
           "scheduler_connected": bool(C.QSTASH_TOKEN)}
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
                    "rules": {k: getattr(C, k) for k in ("NOISE_MULT", "NOISE_LOOKBACK", "STOP_ATR", "RR_NOISE", "VIX_MIN",
                              "SIGNAL_VALID_MIN", "RISK_PER_TRADE", "MAX_DAILY_LOSS", "MAX_TRADES_PER_DAY")}})


@app.get("/api/research")
def research():
    from fno import backtest as BT
    return jsonify(BT.research_summary())


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
    if not 10_000 <= cap <= 100_000_000:
        return jsonify({"ok": False, "message": "Capital must be between ₹10,000 and ₹10 crore."}), 400
    from fno import paper as P
    a = P.account()
    store.put("account", {**a, "capital_start": cap})
    return jsonify({"ok": True, "message": f"Demo capital set to ₹{cap:,.0f} (positions and journal kept)."})


@app.post("/api/account/reset")
def account_reset():
    cap = float((request.get_json(force=True) or {}).get("capital", C.DEFAULT_CAPITAL))
    if not 10_000 <= cap <= 100_000_000:
        return jsonify({"ok": False, "message": "Capital must be between ₹10,000 and ₹10 crore."}), 400
    from fno import paper as P
    P.reset(cap)
    return jsonify({"ok": True, "message": f"Demo account reset to ₹{cap:,.0f}."})


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
    if days not in (30, 60, 90, 120, 180, 250, 500):
        raise ValueError("Pick a backtest period from the list.")
    if sizing not in ("risk", "one_lot", "all_in") or otm not in (-1, 0, 1, 2) or not 1000 <= capital <= 1e9 \
            or strategy not in ("both", "noise", "camarilla", "ai"):
        raise ValueError("Invalid backtest settings.")
    return jsonify(SV.backtest(_sym(), mult, capital, sizing, otm, strategy, days))


# ---------------------------------------------------------------------------
# Settings & broker logins
# ---------------------------------------------------------------------------
def _base():
    proto = request.headers.get("X-Forwarded-Proto", "https")
    return f"{proto}://{request.host}"


def _status(cfg, idfield):
    return {"configured": bool(cfg.get(idfield) and cfg.get("secret")), "id": cfg.get(idfield, ""),
            "connected": bool(cfg.get("token") and B.token_valid(cfg.get("token_created"))),
            "logged_in_at": cfg.get("token_created")}


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
            cur.pop("token", None)
            cur.pop("token_created", None)
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
            st = [x for x in p["strategies"] if x in ("noise", "camarilla", "ai")]
            if not st:
                raise ValueError("Keep at least one strategy switched on.")
            cur["strategies"] = st
        if "auto" in p:
            cur["auto"] = bool(p["auto"])
        if "enforce_limits" in p:
            cur["enforce_limits"] = bool(p["enforce_limits"])
        if "auto_syms" in p:
            cur["auto_syms"] = [x for x in p["auto_syms"] if x in SV.ALL_SYMBOLS][:4]
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
            tok = B.fyers_exchange(code, cfg["app_id"], cfg["secret"])
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
