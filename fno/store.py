"""Supabase storage (PostgREST). Every request carries a secret header; the
database's row-level-security policies reject anything without it, so the
public anon key alone can't read or write this data."""
from __future__ import annotations

import json

import requests

from . import config as C

_s = requests.Session()


def _h(extra=None):
    h = {
        "apikey": C.SUPABASE_KEY,
        "Authorization": f"Bearer {C.SUPABASE_KEY}",
        "x-app-secret": C.DB_SECRET,
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _url(table):
    return f"{C.SUPABASE_URL}/rest/v1/{table}"


def _check(r):
    if r.status_code >= 400:
        raise RuntimeError(f"Database error {r.status_code}: {r.text[:200]}")
    return r


# ---------------- settings (key -> json) ----------------
def get(key, default=None):
    r = _check(_s.get(_url("fno_settings"), params={"key": f"eq.{key}", "select": "value"}, headers=_h(), timeout=10))
    rows = r.json()
    return rows[0]["value"] if rows else default


def get_many(keys) -> dict:
    r = _check(_s.get(_url("fno_settings"), params={"key": f"in.({','.join(keys)})", "select": "key,value"},
                      headers=_h(), timeout=10))
    return {row["key"]: row["value"] for row in r.json()}


def put(key, value):
    _check(_s.post(_url("fno_settings"), params={"on_conflict": "key"},
                   data=json.dumps({"key": key, "value": value, "updated_at": "now()"}, default=str),
                   headers=_h({"Prefer": "resolution=merge-duplicates,return=minimal"}), timeout=10))


def delete_setting(key):
    _check(_s.delete(_url("fno_settings"), params={"key": f"eq.{key}"}, headers=_h(), timeout=10))


# ---------------- positions ----------------
def positions() -> list[dict]:
    r = _check(_s.get(_url("fno_positions"), params={"select": "data", "order": "opened_at.asc"}, headers=_h(), timeout=10))
    return [row["data"] for row in r.json()]


def insert_position(pos: dict):
    _check(_s.post(_url("fno_positions"),
                   data=json.dumps({"id": pos["id"], "symbol": pos["symbol"], "opened_at": pos["opened"], "data": pos},
                                   default=str),
                   headers=_h({"Prefer": "return=minimal"}), timeout=10))


def update_position(pos: dict):
    _check(_s.patch(_url("fno_positions"), params={"id": f"eq.{pos['id']}"},
                    data=json.dumps({"data": pos}, default=str), headers=_h({"Prefer": "return=minimal"}), timeout=10))


def take_position(pos_id) -> dict | None:
    """Atomically delete and return a position (only one closer can win)."""
    r = _check(_s.delete(_url("fno_positions"), params={"id": f"eq.{pos_id}"},
                         headers=_h({"Prefer": "return=representation"}), timeout=10))
    rows = r.json()
    return rows[0]["data"] if rows else None


def clear_positions():
    _check(_s.delete(_url("fno_positions"), params={"id": "neq.__none__"}, headers=_h(), timeout=10))


# ---------------- trades ----------------
def trades() -> list[dict]:
    r = _check(_s.get(_url("fno_trades"), params={"select": "data", "order": "closed_at.asc"}, headers=_h(), timeout=10))
    return [row["data"] for row in r.json()]


def insert_trade(rec: dict):
    _check(_s.post(_url("fno_trades"),
                   data=json.dumps({"id": rec["id"], "symbol": rec["symbol"], "opened_at": rec["opened"],
                                    "closed_at": rec["closed"], "pnl": rec["pnl"], "data": rec}, default=str),
                   headers=_h({"Prefer": "return=minimal"}), timeout=10))


def clear_trades():
    _check(_s.delete(_url("fno_trades"), params={"id": "neq.__none__"}, headers=_h(), timeout=10))


def ping() -> bool:
    try:
        get("ping")
        return True
    except Exception:
        return False
