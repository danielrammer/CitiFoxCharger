import hmac
import json
import re
import secrets
import socket
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from .config import ConfigStore
from .modbus import WRITE_BLOCK
from .secrets_store import SecretStore
from .service import Service
from .skoda import IntegrationError
from .instance import InstanceLock
from . import __version__

STATIC = Path(__file__).with_name("web")
FILES = {"/": ("index.html", "text/html; charset=utf-8"),
         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/style.css": ("style.css", "text/css; charset=utf-8")}


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port, service):
        # Intentionally not configurable: no unauthenticated LAN exposure.
        super().__init__(("127.0.0.1", port), Handler)
        self.service = service
        self.session = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        self.hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}

    def server_close(self):
        if hasattr(self, "service"):
            self.service.stop_worker()
        super().server_close()
        if hasattr(self, "instance_lock"):
            self.instance_lock.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "CitiFoxCharger"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, _format, *args):
        # No access logs containing FINs, credentials, request bodies or query strings.
        pass

    def reply(self, code, value, content_type="application/json; charset=utf-8", cookie=False):
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", f"lz_session={self.server.session}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def valid_request(self, mutation=False):
        host = self.headers.get("Host", "")
        if host not in self.server.hosts:
            self.reply(403, {"error": "Host nicht erlaubt."})
            return False
        origin = self.headers.get("Origin")
        if (origin and origin != f"http://{host}") or (mutation and origin != f"http://{host}"):
            self.reply(403, {"error": "Anfrage stammt nicht aus der lokalen Oberfläche."})
            return False
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            self.reply(403, {"error": "Fremde Webseite darf nicht zugreifen."})
            return False
        return True

    def authenticated(self, mutation=False):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            entry = cookie.get("lz_session")
            token = entry.value if entry else ""
            valid = hmac.compare_digest(token, self.server.session)
        except (ValueError, TypeError):
            valid = False
        if not valid:
            self.reply(401, {"error": "Lokale Sitzung abgelaufen. Seite neu laden."})
            return False
        try:
            csrf_valid = hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), self.server.csrf)
        except TypeError:
            csrf_valid = False
        if mutation and not csrf_valid:
            self.reply(403, {"error": "Ungültiger Sitzungsschutz. Seite neu laden."})
            return False
        return True

    def do_GET(self):
        if not self.valid_request():
            return
        path = urlsplit(self.path).path
        if path in FILES:
            file, kind = FILES[path]
            self.reply(200, (STATIC / file).read_bytes(), kind)
            return
        if path == "/api/session":
            self.reply(200, {"csrf": self.server.csrf}, cookie=True)
            return
        if path == "/health":
            self.reply(200, {"app": "CitiFoxCharger", "status": "ok", "version": __version__})
            return
        if not self.authenticated():
            return
        service = self.server.service
        if path == "/api/config":
            with service.lock:
                self.reply(200, {"config": service.config.value, "key": service.secrets.status()})
        elif path == "/api/status":
            try:
                self.reply(200, service.status())
            except Exception:
                self.reply(500, {"error": "Interner Lesefehler. Konfiguration und Verbindungen prüfen."})
        else:
            self.reply(404, {"error": "Nicht gefunden."})

    def do_POST(self):
        if not self.valid_request(mutation=True) or not self.authenticated(mutation=True):
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.reply(415, {"error": "JSON erforderlich."})
            return
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Transfer-Encoding ist nicht erlaubt.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 16384:
                raise ValueError("Ungültige Nachrichtengröße.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("JSON-Objekt erforderlich.")
            path = urlsplit(self.path).path
            service = self.server.service
            if path == "/api/app/quit":
                if data:
                    raise ValueError("Keine Parameter erforderlich.")
                service.prepare_shutdown()
                self.reply(200, {"message": "CitiFoxCharger wird beendet. Es läuft keine Ladeüberwachung mehr."})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            elif path == "/api/config":
                service.save_config(data)
                result = {"message": "Konfiguration gespeichert."}
            elif path == "/api/key":
                if set(data) != {"key", "persist"} or type(data["persist"]) is not bool:
                    raise ValueError("key und persist erforderlich.")
                service.set_key(data["key"], data["persist"])
                result = {"message": "API-Schlüssel gespeichert.", "key": service.secrets.status()}
            elif path == "/api/key/clear":
                service.clear_key()
                result = {"message": "API-Schlüssel entfernt."}
            elif path == "/api/refresh":
                if data:
                    raise ValueError("Keine Parameter erforderlich.")
                result = service.refresh_now()
            elif path == "/api/skoda/command":
                if set(data) != {"action", "request_id"} or data["action"] not in ("start", "stop"):
                    raise ValueError("Ungültiger Fahrzeugbefehl.")
                if not isinstance(data["request_id"], str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", data["request_id"]):
                    raise ValueError("Ungültige Befehlskennung.")
                result = service.command(data["action"], data["request_id"])
            elif path in ("/api/charging/start", "/api/charging/stop"):
                request_id = data.get("request_id")
                if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", request_id):
                    raise ValueError("Ungültige Befehlskennung.")
                if path.endswith("/start"):
                    if (set(data) not in ({"target_percent", "request_id"}, {"target_percent", "request_id", "allow_stale"})
                            or type(data.get("allow_stale", False)) is not bool):
                        raise ValueError("Ziel-Ladestand und Befehlskennung erforderlich.")
                    result = service.force_charge(data["target_percent"], request_id, data.get("allow_stale", False))
                else:
                    if set(data) != {"request_id"}:
                        raise ValueError("Nur eine Befehlskennung erforderlich.")
                    result = service.stop_charge(request_id)
            elif path == "/api/smartfox/command":
                self.reply(409, {"error": WRITE_BLOCK, "code": "unverified_write"})
                return
            else:
                self.reply(404, {"error": "Nicht gefunden."})
                return
            self.reply(200, result)
        except IntegrationError as error:
            self.reply(409, {"error": str(error), "code": error.code})
        except (ValueError, UnicodeError) as error:
            self.reply(400, {"error": str(error) if not isinstance(error, json.JSONDecodeError) else "Ungültiges JSON."})
        except (TimeoutError, socket.timeout):
            self.close_connection = True
        except Exception:
            self.reply(500, {"error": "Aktion fehlgeschlagen. Lokale Dateiablage und Konfiguration prüfen."})


def make_server(port, data_dir):
    instance_lock = InstanceLock(data_dir)
    try:
        config = ConfigStore(data_dir)
        server = LocalServer(port, Service(config, SecretStore(config.directory)))
        server.instance_lock = instance_lock
        server.service.start_worker()
        return server
    except Exception:
        instance_lock.close()
        raise
