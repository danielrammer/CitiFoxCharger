"""Local session/CSRF-protected backend control. Never kills Python processes."""
import json
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from .background import spawn_backend, wait_until_ready


class ControlError(Exception):
    pass


class BackendUnavailable(ControlError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LocalControl:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                                 urllib.request.HTTPCookieProcessor(CookieJar()))

    def request(self, path, data=None, csrf=None, timeout=35):
        headers = {"Accept": "application/json"}
        if data is not None:
            headers.update({"Content-Type":"application/json", "Origin":self.base, "X-CSRF-Token":csrf or ""})
        request = urllib.request.Request(self.base+path, headers=headers,
                                         data=json.dumps(data).encode() if data is not None else None)
        try:
            with self.client.open(request, timeout=timeout) as response:
                return json.loads(response.read(1048577))
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read(16384)).get("error", "Lokale Aktion fehlgeschlagen.")
            except (ValueError, UnicodeError):
                message = "Lokale Aktion fehlgeschlagen."
            raise ControlError(message) from None
        except (OSError, ValueError):
            raise ControlError("Keine gültige Antwort von CitiFoxCharger. Start.cmd oder Hintergrundprotokoll prüfen.") from None

    def health(self):
        try:
            # Windows can take about two seconds to report a refused loopback connection.
            with self.client.open(self.base+"/health", timeout=3) as response:
                result = json.loads(response.read(2048))
        except urllib.error.URLError as error:
            if isinstance(error.reason, ConnectionRefusedError):
                return None
            raise BackendUnavailable("Backend antwortet nicht. Es wurde kein Prozess beendet.") from None
        except OSError:
            raise BackendUnavailable("Backend antwortet nicht. Es wurde kein Prozess beendet.") from None
        except ValueError:
            raise ControlError("An diesem Port läuft keine bestätigte CitiFoxCharger-Instanz.") from None
        if not isinstance(result, dict) or result.get("app") != "CitiFoxCharger":
            raise ControlError("An diesem Port läuft keine bestätigte CitiFoxCharger-Instanz.")
        return result

    def action(self, action, data_dir, port, no_browser=False, timeout=45):
        previous = self.health()
        if previous is None:
            if action == "stop":
                return "CitiFoxCharger ist bereits beendet."
            child = spawn_backend(["--port",str(port),"--data-dir",str(data_dir),"--no-browser"],data_dir)
            if not wait_until_ready(child,port):
                raise ControlError("Start nicht bestätigt. Bitte Start.cmd oder background.log prüfen.")
        else:
            csrf = self.request("/api/session")["csrf"]
            path = "/api/app/quit" if action == "stop" else "/api/app/restart"
            self.request(path, {}, csrf)
            deadline = time.monotonic()+timeout
            while time.monotonic() < deadline:
                try:
                    current = self.health()
                except BackendUnavailable:
                    # The port may remain bound briefly while worker/lock cleanup finishes.
                    time.sleep(.2)
                    continue
                if action == "stop" and current is None:
                    return "CitiFoxCharger beendet. Ein Browserfenster kann offen bleiben."
                if action == "restart" and current and current.get("instance_id") != previous.get("instance_id"):
                    break
                time.sleep(.2)
            else:
                raise ControlError("Beenden/Neustart nicht bestätigt. Bitte den Status prüfen; kein erzwungenes Beenden durchgeführt.")
        if action == "restart" and not no_browser:
            from .browser import open_dashboard
            open_dashboard(self.base, "brave", True)
        return "CitiFoxCharger neu gestartet. Vorhandene Überwachung wird fortgesetzt; kein neuer Ladestart."
