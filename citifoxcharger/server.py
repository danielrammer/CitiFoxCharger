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
from .diagnostics import report as diagnostic_report
from .skoda import IntegrationError
from .instance import InstanceLock
from .app_update import AppUpdater
from .network import NetworkAuth,private_host,read_network,tls_context
from . import __version__,release_info

STATIC = Path(__file__).with_name("web")
FILES = {"/": ("index.html", "text/html; charset=utf-8"),
         "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/style.css": ("style.css", "text/css; charset=utf-8"),
         "/icon.png": ("brand/icon.png", "image/png"),
         "/logo.png": ("brand/logo.png", "image/png"),
         "/charging-car.png": ("brand/charging-car.png", "image/png")}


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port, service, *, address="127.0.0.1", network_auth=None, tls=None, updater=None):
        if bool(network_auth) != bool(tls):
            raise ValueError("Network access requires HTTPS and a configured access policy.")
        if address != "127.0.0.1":
            private_host(address)
            if not network_auth:raise ValueError("Unprotected network access is not allowed.")
        self.network_auth,self.tls = network_auth,tls
        self.scheme = "https" if tls else "http"
        self.remote_server,self.remote_thread = None,None
        super().__init__((address, port), Handler)
        self.service = service
        self.release = release_info()
        self.updater = updater if updater is not None else AppUpdater(Path(__file__).resolve().parent.parent,service.config.directory,self.server_port)
        self.update_state()
        self.session = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        self.instance_id = secrets.token_urlsafe(12)
        self.restart_requested = False
        self.hosts = {f"{address}:{self.server_port}"} if network_auth else {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}

    def update_state(self):
        with self.service.lock:
            value=self.updater.state()
            self.service.app_update_pending=value['phase'] not in ('idle','complete','failed')
            return value

    def server_close(self):
        if self.remote_server is not None:
            self.remote_server.shutdown()
            self.remote_server.server_close()
            self.remote_thread.join(3)
            self.remote_server = None
        if hasattr(self, "service") and not self.network_auth:
            self.service.stop_worker()
        super().server_close()
        if hasattr(self, "instance_lock"):
            self.instance_lock.close()

    def handle_error(self, request, client_address):
        if not self.network_auth:super().handle_error(request,client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "CitiFoxCharger"
    sys_version = ""

    def setup(self):
        if self.server.tls:
            self.request.settimeout(5)
            try:self.request = self.server.tls.wrap_socket(self.request,server_side=True)
            except OSError:
                self.request.close()
                raise
        super().setup()
        self.connection.settimeout(15)
        self.body_consumed = False

    def finish(self):
        try:super().finish()
        finally:
            if self.server.tls:self.request.close()

    def log_message(self, _format, *args):
        # No access logs containing FINs, credentials, request bodies or query strings.
        pass

    def reply(self, code, value, content_type="application/json; charset=utf-8", cookie=False):
        # Drain small rejected POSTs so Windows can deliver the error before socket close.
        # No request content is acted on before Host/Origin/session/CSRF validation.
        if code >= 400 and self.command == "POST" and not self.body_consumed:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not self.headers.get("Transfer-Encoding") and 0 < length <= 16384:
                    self.rfile.read(length)
            except (ValueError, OSError):
                pass
            self.body_consumed = True
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
            secure = "; Secure" if self.server.tls else ""
            self.send_header("Set-Cookie", f"lz_session={self.server.session}; HttpOnly; SameSite=Strict; Path=/{secure}")
        if self.server.network_auth and self.server.network_auth.config.get('authentication','password') != 'none' and code == 401:
            self.send_header("WWW-Authenticate", 'Basic realm="CitiFoxCharger", charset="UTF-8"')
        if self.server.network_auth and code == 429:
            self.send_header("Retry-After", "60")
        self.end_headers()
        self.wfile.write(body)

    def valid_request(self, mutation=False):
        host = self.headers.get("Host", "")
        if host not in self.server.hosts:
            self.reply(403, {"error": "Host is not allowed."})
            return False
        origin = self.headers.get("Origin")
        if (origin and origin != f"{self.server.scheme}://{host}") or (mutation and origin != f"{self.server.scheme}://{host}"):
            self.reply(403, {"error": "The request did not originate from this dashboard."})
            return False
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            self.reply(403, {"error": "Access from another website is not allowed."})
            return False
        if self.server.network_auth:
            if self.client_address[0] != "127.0.0.1":
                try:private_host(self.client_address[0])
                except ValueError:
                    self.reply(403,{"error":"Only private network devices are allowed."})
                    return False
            code = self.server.network_auth.check(self.headers.get("Authorization"),self.client_address[0])
            if code != 200:
                self.reply(code,{"error":"Network authentication is required." if code==401 else "Too many sign-in attempts. Wait one minute."})
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
            self.reply(401, {"error": "Local session expired. Reload the page."})
            return False
        try:
            csrf_valid = hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), self.server.csrf)
        except TypeError:
            csrf_valid = False
        if mutation and not csrf_valid:
            self.reply(403, {"error": "Invalid session protection. Reload the page."})
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
            self.reply(200, {"app": "CitiFoxCharger", "status": "ok", "version": __version__, "instance_id": self.server.instance_id,"release":self.server.release})
            return
        if not self.authenticated():
            return
        service = self.server.service
        if path == "/api/config":
            with service.lock:
                self.reply(200, {"config": service.config.value, "key": service.secrets.status(),
                                 "access":{"local_admin":self.server.network_auth is None,"can_update":True},"network":service.network})
        elif path == "/api/diagnostics":
            snapshot = service.diagnostic_snapshot()
            snapshot["backend_version"] = __version__
            self.reply(200, diagnostic_report(snapshot, live=True))
        elif path == "/api/status":
            try:
                self.server.update_state()
                self.reply(200, service.status())
            except Exception:
                self.reply(500, {"error": "Internal read error. Check configuration and connections."})
        elif path == "/api/app/update/status":
            self.reply(200,self.server.update_state())
        else:
            self.reply(404, {"error": "Not found."})

    def do_POST(self):
        if not self.valid_request(mutation=True) or not self.authenticated(mutation=True):
            return
        remote_actions = {"/api/app/update","/api/refresh","/api/charging/start","/api/charging/stop","/api/charging/target",
                          "/api/smartfox/heater","/api/smartfox/charger-mode"}
        if self.server.network_auth and urlsplit(self.path).path not in remote_actions:
            self.reply(403,{"error":"Configure device access and the backend directly on the tablet."})
            return
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.reply(415, {"error": "JSON is required."})
            return
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Transfer-Encoding is not allowed.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 16384:
                raise ValueError("Invalid message size.")
            self.body_consumed = True
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("A JSON object is required.")
            path = urlsplit(self.path).path
            service = self.server.service
            if (service.app_update_pending and path not in ("/api/app/update","/api/app/quit","/api/charging/stop")
                    and not (path=="/api/smartfox/charger-mode" and data.get("mode")=="OFF")):
                raise ValueError("An update is running. Charging starts and settings changes are temporarily blocked.")
            if path == "/api/app/update":
                if data:raise ValueError("No parameters are required.")
                self.reply(202,self.server.updater.launch(service))
                return
            if path == "/api/app/restart":
                if data:
                    raise ValueError("No parameters are required.")
                service.prepare_restart()
                self.server.restart_requested = True
                self.reply(200, {"message":"CitiFoxCharger is restarting; no new vehicle start."})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path == "/api/app/quit":
                if data:
                    raise ValueError("No parameters are required.")
                service.prepare_shutdown()
                self.reply(200, {"message": "CitiFoxCharger is stopping. No charging monitoring remains active."})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            elif path == "/api/config":
                service.save_config(data)
                result = {"message": "Configuration saved."}
            elif path == "/api/key":
                if set(data) != {"key", "persist"} or type(data["persist"]) is not bool:
                    raise ValueError("key and persist are required.")
                service.set_key(data["key"], data["persist"])
                result = {"message": "API key saved.", "key": service.secrets.status()}
            elif path == "/api/key/clear":
                service.clear_key()
                result = {"message": "API key removed."}
            elif path == "/api/refresh":
                if data:
                    raise ValueError("No parameters are required.")
                result = service.refresh_now()
            elif path == "/api/skoda/command":
                if set(data) != {"action", "request_id"} or data["action"] not in ("start", "stop"):
                    raise ValueError("Invalid vehicle command.")
                if not isinstance(data["request_id"], str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", data["request_id"]):
                    raise ValueError("Invalid request ID.")
                result = service.command(data["action"], data["request_id"])
            elif path == "/api/charging/target":
                if set(data) != {"target_percent"}:
                    raise ValueError("Only target charge level is required.")
                result = service.change_target(data["target_percent"])
            elif path in ("/api/charging/start", "/api/charging/stop"):
                request_id = data.get("request_id")
                if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", request_id):
                    raise ValueError("Invalid request ID.")
                if path.endswith("/start"):
                    if (set(data) not in ({"target_percent", "request_id"}, {"target_percent", "request_id", "allow_stale"})
                            or type(data.get("allow_stale", False)) is not bool):
                        raise ValueError("Target charge level and request ID are required.")
                    result = service.force_charge(data["target_percent"], request_id, data.get("allow_stale", False))
                else:
                    if set(data) != {"request_id"}:
                        raise ValueError("Only a request ID is required.")
                    result = service.stop_charge(request_id)
            elif path == "/api/smartfox/charger-mode":
                if (set(data) != {"mode","request_id"} or not isinstance(data["request_id"],str)
                        or not re.fullmatch(r"[a-zA-Z0-9-]{16,80}",data["request_id"])):
                    raise ValueError("Invalid charger request.")
                result = service.set_charger_mode(data["mode"],data["request_id"])
            elif path == "/api/smartfox/heater":
                if (set(data) != {"device","mode","request_id"} or not isinstance(data["request_id"],str)
                        or not re.fullmatch(r"[a-zA-Z0-9-]{16,80}",data["request_id"])):
                    raise ValueError("Invalid heater request.")
                result = service.set_heater_mode(data["device"],data["mode"],data["request_id"])
            elif path == "/api/smartfox/command":
                self.reply(409, {"error": WRITE_BLOCK, "code": "unverified_write"})
                return
            else:
                self.reply(404, {"error": "Not found."})
                return
            self.reply(200, result)
        except IntegrationError as error:
            self.reply(409, {"error": str(error), "code": error.code})
        except (ValueError, UnicodeError) as error:
            self.reply(400, {"error": str(error) if not isinstance(error, json.JSONDecodeError) else "Invalid JSON."})
        except (TimeoutError, socket.timeout):
            self.close_connection = True
        except Exception:
            self.reply(500, {"error": "Action failed. Check local storage and configuration."})


def make_server(port, data_dir):
    instance_lock = InstanceLock(data_dir)
    try:
        config = ConfigStore(data_dir)
        server = LocalServer(port, Service(config, SecretStore(config.directory)))
        server.instance_lock = instance_lock
        try:
            network = read_network(data_dir)
            if network:
                remote = LocalServer(network["port"],server.service,address=network["host"],
                                     network_auth=NetworkAuth(network),tls=tls_context(data_dir),updater=server.updater)
                server.remote_server = remote
                server.remote_thread = threading.Thread(target=remote.serve_forever,kwargs={"poll_interval":.25},daemon=True)
                server.remote_thread.start()
                server.service.network = {"enabled":True,"url":f'https://{network["host"]}:{remote.server_port}/',
                                          "certificate_expires":network["certificate_expires"],
                                          "authentication":network.get('authentication','password'),
                                          "message":("HTTPS home network access without a password is active. All reachable home network devices can operate charging and heaters."
                                                     if network.get('authentication')=='none' else "Authenticated HTTPS home network access is active.")}
        except (OSError,ValueError,TypeError,KeyError):
            server.service.network = {"enabled":False,"message":"Home network access could not start. Check the private tablet IP, certificate and Setup-Network.cmd. Local monitoring continues."}
        server.service.start_worker()
        return server
    except Exception:
        instance_lock.close()
        raise
