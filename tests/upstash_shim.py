"""Tiny local stand-in for the Upstash Redis REST API (for offline tests).

POST /  with a JSON array command  ->  {"result": ...}
Backed by fakeredis, so real Redis semantics (incl. EVAL/Lua) are used.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import fakeredis

R = fakeredis.FakeRedis(decode_responses=True)
TOKEN = "test-token"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, {"error": "Unauthorized"})
        cmd = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        try:
            res = R.execute_command(*cmd)
            self._send(200, {"result": res})
        except Exception as e:
            self._send(400, {"error": str(e)})

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def start(port=6399):
    srv = HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}", TOKEN
