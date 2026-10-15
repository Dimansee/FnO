"""Backup of everything the app stores to the user's own Google Drive.

How: a tiny Google Apps Script web app lives in the user's Drive account (script text from script_text()). The app zips its
data and POSTs it there; the script saves the zip in a Drive folder and keeps the newest KEEP files. No Google Cloud project,
no OAuth: the shared secret in the script is generated here and checked on every upload.

What goes in the zip: trades, open positions, preferences, custom strategies, learning state, journal, crypto and US books and
the candle vault. What never does: broker keys, secrets, tokens, PIN, browser push subscriptions and plain caches.
"""
from __future__ import annotations

import base64
import io
import json
import re
import secrets
import zipfile
from datetime import datetime

import requests

from . import config as C
from . import store

KEEP = 20
MAX_ZIP = 30_000_000           # Apps Script accepts about 50 MB; stay well under
SECRET_SETTINGS = {"upstox", "fyers", "scheduler", "instrument_master", "drive"}
SKIP_PREFIX = ("fno:push:", "fno:ls:seen:", "fno:stocks:", "fno:last_tick", "fno:rec:", "fno:data:in_saved:", "fno:data:us_saved:")

SCRIPT = r"""/* F&O Trainer backup receiver. Paste into script.google.com, then Deploy > New deployment > Web app
   (Execute as: Me, Who has access: Anyone). Copy the Web app URL into the app's Saved data tab. */
const SECRET = '__SECRET__';
const FOLDER_NAME = 'F&O Trainer backups';
const KEEP = __KEEP__;

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    if (body.secret !== SECRET) return out({ ok: false, error: 'bad secret' });
    const it = DriveApp.getFoldersByName(FOLDER_NAME);
    const folder = it.hasNext() ? it.next() : DriveApp.createFolder(FOLDER_NAME);
    const blob = Utilities.newBlob(Utilities.base64Decode(body.data), 'application/zip', body.name);
    const f = folder.createFile(blob);
    const files = [], fi = folder.getFiles();
    while (fi.hasNext()) files.push(fi.next());
    files.sort(function (a, b) { return b.getDateCreated() - a.getDateCreated(); });
    files.slice(KEEP).forEach(function (x) { x.setTrashed(true); });
    return out({ ok: true, name: body.name, size: f.getSize(), url: f.getUrl(), folder: folder.getUrl() });
  } catch (err) {
    return out({ ok: false, error: String(err) });
  }
}

function doGet() { return out({ ok: true, hello: 'F&O Trainer backup receiver is running' }); }

function out(o) { return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON); }
"""


def cfg() -> dict:
    return store.get("drive") or {}


def script_text() -> str:
    """The Apps Script, with this app's own secret filled in (created on first use)."""
    c = cfg()
    if not c.get("secret"):
        c["secret"] = secrets.token_urlsafe(24)
        store.put("drive", c)
    return SCRIPT.replace("__SECRET__", c["secret"]).replace("__KEEP__", str(KEEP))


def status() -> dict:
    c = cfg()
    return {"url_set": bool(c.get("url")), "has_secret": bool(c.get("secret")), "last_ok": c.get("last_ok"), "last_name": c.get("last_name"),
            "last_bytes": c.get("last_bytes"), "last_error": c.get("last_error"), "folder": c.get("folder"), "keep": KEEP}


def set_url(url: str) -> dict:
    url = (url or "").strip()
    if url and not re.match(r"^https://script\.google(usercontent)?\.com/macros/s/[\w\-]+/exec$", url):
        raise ValueError("That does not look like an Apps Script web app URL (it should start with https://script.google.com/macros/s/ and end with /exec).")
    c = cfg()
    if url:
        c["url"] = url
    else:
        c.pop("url", None)
    c["last_error"] = None
    store.put("drive", c)
    return status()


def _keys() -> list[str]:
    out, cur = [], "0"
    for _ in range(200):
        cur, batch = store._cmd("SCAN", cur, "MATCH", "fno:*", "COUNT", 500)
        out += batch
        if str(cur) == "0":
            break
    return sorted(set(out))


def _dump_key(key: str):
    t = store._cmd("TYPE", key)
    if t == "string":
        return store._cmd("GET", key)
    if t == "hash":
        flat = store._cmd("HGETALL", key) or []
        return flat if isinstance(flat, dict) else dict(zip(flat[0::2], flat[1::2]))      # Upstash REST returns a flat list
    if t == "list":
        return store._cmd("LRANGE", key, 0, -1)
    if t == "set":
        return store._cmd("SMEMBERS", key)
    if t == "zset":
        return store._cmd("ZRANGE", key, 0, -1, "WITHSCORES")
    return None


def build_zip(now: datetime | None = None) -> tuple[bytes, dict]:
    now = now or C.now_ist()
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for key in _keys():
            if key.startswith(SKIP_PREFIX):
                continue
            data = _dump_key(key)
            if data is None:
                continue
            if key == store.SETTINGS and isinstance(data, dict):
                data = {k: v for k, v in data.items() if k not in SECRET_SETTINGS}
            z.writestr(re.sub(r"[^A-Za-z0-9_.-]+", "_", key[4:] or "root") + ".json", json.dumps({"key": key, "data": data}, separators=(",", ":")))
            n += 1
        z.writestr("manifest.json", json.dumps({"created": now.isoformat(timespec="seconds"), "keys": n, "app": "fno-trainer",
                                                "note": "Broker keys, tokens, PIN and push subscriptions are never included."}, indent=1))
    return buf.getvalue(), {"keys": n}


def run(now: datetime | None = None) -> dict:
    """Zip and upload. Never raises: the result (and any error) is also saved for the Saved data tab."""
    now = now or C.now_ist()
    c = cfg()
    if not c.get("url") or not c.get("secret"):
        return {"ok": False, "error": "Google Drive is not connected yet."}
    try:
        blob, info = build_zip(now)
        if len(blob) > MAX_ZIP:
            raise RuntimeError(f"Backup is {len(blob) / 1e6:.0f} MB, above the {MAX_ZIP / 1e6:.0f} MB limit of the Drive receiver.")
        name = f"fno-backup-{now:%Y-%m-%d_%H%M}.zip"
        r = requests.post(c["url"], data=json.dumps({"secret": c["secret"], "name": name, "data": base64.b64encode(blob).decode()}),
                          headers={"Content-Type": "text/plain;charset=utf-8"}, timeout=60)
        try:
            j = r.json()
        except Exception:
            raise RuntimeError(f"Drive receiver answered {r.status_code} without JSON. Check that the web app is deployed for 'Anyone'.")
        if not j.get("ok"):
            raise RuntimeError(f"Drive receiver: {j.get('error') or j}")
        c.update({"last_ok": now.isoformat(timespec="seconds"), "last_name": name, "last_bytes": len(blob), "last_error": None, "folder": j.get("folder")})
        store.put("drive", c)
        return {"ok": True, "name": name, "bytes": len(blob), "keys": info["keys"], "folder": j.get("folder")}
    except Exception as e:
        c = cfg()
        c["last_error"] = str(e)[:200]
        store.put("drive", c)
        return {"ok": False, "error": str(e)[:200]}
