"""F&O Paper Trading Trainer - web app (Flask on Vercel)."""
from __future__ import annotations

import hashlib
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


PUBLIC = {"/", "/api/login", "/api/health", "/api/tick"}


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
    if sizing not in ("risk", "one_lot") or otm not in (-1, 0, 1, 2) or not 1000 <= capital <= 1e9:
        raise ValueError("Invalid backtest settings.")
    return jsonify(SV.backtest(_sym(), mult, capital, sizing, otm))


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
        "broker_pref": s.get("broker_pref") or "auto", "fii_manual": s.get("fii_manual"),
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
@app.route("/api/tick", methods=["GET", "POST"])
def tick():
    if not C.CRON_SECRET or not hmac.compare_digest(request.headers.get("x-cron-secret", ""), C.CRON_SECRET):
        return jsonify({"error": "forbidden"}), 403
    return jsonify(SV.clean(SV.tick()))
