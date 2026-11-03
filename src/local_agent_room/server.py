"""Loopback-only HTTP UI/API. No execution, file uploads, or outbound requests."""
import hmac
import json
import secrets
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .store import InputError

STATIC = Path(__file__).with_name("static")
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
MAX_REQUEST = 65536


class RoomServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, store, port):
        self.store = store
        self.browser_token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.origins = {f"http://127.0.0.1:{self.server_port}", f"http://localhost:{self.server_port}"}
        self.hosts = {urlsplit(o).netloc for o in self.origins}

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(10)
        return sock, address


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalAgentRoom"
    sys_version = ""

    def log_message(self, *_):
        pass  # Do not log credentials, URLs, or room content.

    def send(self, status, payload, content_type="application/json; charset=utf-8"):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def gate(self):
        if self.headers.get("Host") not in self.server.hosts:
            self.send(403, {"error": "Invalid Host."})
            return False
        origin = self.headers.get("Origin")
        if (origin is not None and origin not in self.server.origins) or self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.send(403, {"error": "Cross-origin access denied."})
            return False
        return True

    def actor(self):
        value = self.headers.get("Authorization", "")
        token = value[7:] if value.startswith("Bearer ") else ""
        if hmac.compare_digest(token.encode(), self.server.browser_token.encode()):
            return "human"
        actor = self.server.store.authenticate(token)
        if not actor:
            self.send(401, {"error": "Credential missing, expired, or revoked."})
        return actor

    def do_GET(self):
        if not self.gate():
            return
        url = urlsplit(self.path)
        if url.path in ASSETS:
            name, mime = ASSETS[url.path]
            self.send(200, (STATIC / name).read_bytes(), mime)
            return
        if not url.path.startswith("/api/"):
            self.send(404, {"error": "Not found."})
            return
        actor = self.actor()
        if not actor:
            return
        try:
            if url.path == "/api/me":
                data = {"id": actor, "role": "human" if actor == "human" else "agent"}
            elif url.path == "/api/rooms":
                data = {"rooms": self.server.store.rooms()}
            elif url.path == "/api/participants":
                data = {"participants": self.server.store.participants()}
            elif url.path == "/api/automation":
                data = self.server.store.automation()
            elif url.path == "/api/messages":
                q = parse_qs(url.query)
                data = self.server.store.read(q.get("room", ["general"])[0],
                    int(q.get("after", ["0"])[0]), int(q.get("limit", ["100"])[0]),
                    q.get("tail", ["0"])[0] == "1")
            else:
                self.send(404, {"error": "Not found."})
                return
            self.send(200, data)
        except (InputError, ValueError):
            self.send(400, {"error": "Invalid room or pagination parameters."})

    def do_POST(self):
        if not self.gate():
            return
        actor = self.actor()
        if not actor:
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.send(415, {"error": "Use application/json."})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if self.headers.get("Transfer-Encoding") or not 0 <= length <= MAX_REQUEST:
            self.send(413, {"error": "Invalid or oversized request."})
            return
        try:
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise InputError("Expected a JSON object.")
            path = urlsplit(self.path).path
            if path == '/api/automation':
                if actor != 'human':
                    self.send(403, {'error': 'Only the human can control automation.'})
                    return
                if set(data) != {'enabled'}:
                    raise InputError('Expected enabled.')
                result = self.server.store.automation(data['enabled'])
            elif path == "/api/messages":
                result = self.server.store.post(actor, data)
            elif path == "/api/rooms":
                if actor != "human":
                    self.send(403, {"error": "Only the human can create rooms."})
                    return
                if set(data) != {"id", "title"}:
                    raise InputError("Expected id and title.")
                result = self.server.store.create_room(data['id'], data['title'])
            else:
                self.send(404, {"error": "Not found."})
                return
            self.send(200, result)
        except (InputError, ValueError, UnicodeDecodeError) as e:
            self.send(400, {"error": str(e) if isinstance(e, InputError) else "Invalid JSON."})
        except (TimeoutError, socket.timeout):
            self.send(408, {"error": "Request timed out."})

    def do_OPTIONS(self):
        self.send(403, {"error": "Cross-origin access is not supported."})
