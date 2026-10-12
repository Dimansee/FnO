"""Web Push notifications - the scheduler's tick tells your phone and laptop about signals, paper
trades and exits even when the app is closed.

Standard Web Push (VAPID), no third-party service: the browser gives us a subscription endpoint,
we sign messages with the VAPID key pair and post them to the browser vendor's push service.
Subscriptions live in Redis (fno:push:subs, one per device); dead ones (404/410) are removed when a
send fails. A notification is sent at most once per event id (fno:push:sent:<id>, 1-day expiry).

Env: VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY (base64url raw P-256 keys, made once with
python -c "from fno import push; print(push.generate_keys())"), VAPID_SUBJECT (mailto:you@example.com).
No third-party package: the encryption and signing are done with `cryptography` below.
"""
from __future__ import annotations

import json
import os

from . import store

SUBS = "fno:push:subs"
PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")


def configured() -> bool:
    return bool(PUBLIC_KEY and PRIVATE_KEY)


def subscriptions() -> dict:
    raw = store._cmd("HGETALL", SUBS) or []
    if isinstance(raw, dict):
        return {k: json.loads(v) for k, v in raw.items()}
    return {raw[i]: json.loads(raw[i + 1]) for i in range(0, len(raw), 2)}


def _sid(sub: dict) -> str:
    import hashlib
    return hashlib.sha256(sub["endpoint"].encode()).hexdigest()[:24]


def subscribe(sub: dict, label: str = "") -> str:
    if not isinstance(sub, dict) or not sub.get("endpoint", "").startswith("https://") or not (sub.get("keys") or {}).get("p256dh"):
        raise ValueError("Not a valid push subscription.")
    sid = _sid(sub)
    store._cmd("HSET", SUBS, sid, json.dumps({"endpoint": sub["endpoint"], "keys": sub["keys"], "label": str(label)[:60]}))
    return sid


def unsubscribe(sub_or_id) -> None:
    sid = sub_or_id if isinstance(sub_or_id, str) else _sid(sub_or_id)
    store._cmd("HDEL", SUBS, sid)


def send(title: str, body: str, event_id: str | None = None, url: str = "/", tag: str | None = None) -> dict:
    """Send to every device. event_id makes the send idempotent across ticks."""
    if not configured():
        return {"skipped": "push not configured"}
    if event_id and store._cmd("SET", f"fno:push:sent:{event_id}", "1", "NX", "EX", 86400) is None:
        return {"skipped": "already sent"}
    subs = subscriptions()
    if not subs:
        return {"sent": 0}
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag or event_id or "fno"}).encode()
    sent, gone, errors = 0, 0, []
    for sid, s in subs.items():
        try:
            code, text = deliver(s, payload)
            if code in (201, 200, 202):
                sent += 1
            elif code in (404, 410):
                store._cmd("HDEL", SUBS, sid)
                gone += 1
            else:
                errors.append(f"{s.get('label') or sid}: HTTP {code} {text[:80]}")
        except Exception as e:
            errors.append(f"{s.get('label') or sid}: {str(e)[:80]}")
    return {"sent": sent, "removed": gone, "errors": errors}


# ---------------------------------------------------------------------------
# Web Push on the standard library + cryptography (RFC 8291 aes128gcm, RFC 8292 VAPID)
def _b64d(s: str) -> bytes:
    import base64
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def encrypt(payload: bytes, p256dh: str, auth: str) -> bytes:
    """RFC 8291: ECDH with the browser's key, HKDF, one AES-128-GCM record with the aes128gcm header."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    ua_pub = _b64d(p256dh)
    auth_secret = _b64d(auth)
    local = ec.generate_private_key(ec.SECP256R1())
    local_pub = _pub_bytes(local.public_key())
    shared = local.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    ikm = HKDF(hashes.SHA256(), 32, salt=auth_secret, info=b"WebPush: info\x00" + ua_pub + local_pub).derive(shared)
    salt = os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    record = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)          # 0x02 = last record delimiter
    rs = (len(payload) + 1 + 16).to_bytes(4, "big")
    return salt + rs + bytes([len(local_pub)]) + local_pub + record


def _pub_bytes(pub) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def vapid_headers(endpoint: str) -> dict:
    """RFC 8292: a short-lived ES256 JWT signed with our private key, audience = the push service origin."""
    import time
    from urllib.parse import urlparse
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    u = urlparse(endpoint)
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": SUBJECT}
    header = _b64e(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    body = _b64e(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()
    priv = ec.derive_private_key(int.from_bytes(_b64d(PRIVATE_KEY), "big"), ec.SECP256R1())
    r, s_ = decode_dss_signature(priv.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    jwt = f"{header}.{body}.{_b64e(r.to_bytes(32, 'big') + s_.to_bytes(32, 'big'))}"
    pub = _b64e(_pub_bytes(priv.public_key()))
    return {"Authorization": f"vapid t={jwt}, k={pub}"}


def deliver(sub: dict, payload: bytes, ttl: int = 600) -> tuple[int, str]:
    import requests
    body = encrypt(payload, sub["keys"]["p256dh"], sub["keys"]["auth"])
    headers = {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(ttl), "Urgency": "high",
               **vapid_headers(sub["endpoint"])}
    r = requests.post(sub["endpoint"], data=body, headers=headers, timeout=10)
    return r.status_code, r.text


def generate_keys() -> dict:
    """One-off: make a VAPID key pair for the environment variables."""
    from cryptography.hazmat.primitives.asymmetric import ec
    k = ec.generate_private_key(ec.SECP256R1())
    return {"VAPID_PRIVATE_KEY": _b64e(k.private_numbers().private_value.to_bytes(32, "big")),
            "VAPID_PUBLIC_KEY": _b64e(_pub_bytes(k.public_key()))}
