"""Local session/CSRF-protected backend control. Never kills Python processes."""
import json
import time
import uuid
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from .background import spawn_backend, wait_until_ready
from .skoda import timestamp


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
                message = json.loads(error.read(16384)).get("error", 'Local action failed.')
            except (ValueError, UnicodeError):
                message = 'Local action failed.'
            finally:
                error.close()
            raise ControlError(message) from None
        except (OSError, ValueError):
            raise ControlError("No valid response from CitiFoxCharger. Check Start.cmd or the background log.") from None

    def health(self):
        try:
            # Windows can take about two seconds to report a refused loopback connection.
            with self.client.open(self.base+"/health", timeout=3) as response:
                result = json.loads(response.read(2048))
        except urllib.error.URLError as error:
            if isinstance(error.reason, ConnectionRefusedError):
                return None
            raise BackendUnavailable("The backend is not responding. No process was terminated.") from None
        except OSError:
            raise BackendUnavailable("The backend is not responding. No process was terminated.") from None
        except ValueError:
            raise ControlError("This port is not serving a confirmed CitiFoxCharger instance.") from None
        if not isinstance(result, dict) or result.get("app") != "CitiFoxCharger":
            raise ControlError("This port is not serving a confirmed CitiFoxCharger instance.")
        return result

    def stop_charging(self):
        """Use existing local endpoints, including on older running backends."""
        if self.health() is None:
            raise ControlError("The backend is not running. Charging was not stopped by this script.")
        csrf = self.request("/api/session")["csrf"]
        # Explicit Off persists on old backends even if their vehicle stop fails.
        for path,data in (("/api/smartfox/charger-mode", {"mode":"OFF","request_id":"local-off-"+uuid.uuid4().hex}),
                          ("/api/charging/stop", {"request_id":"manual-stop-"+uuid.uuid4().hex})):
            try:
                self.request(path,data,csrf)
            except ControlError:
                # The independent stop still matters if either upstream rejects a command.
                pass
        status = self.request("/api/status")
        if not isinstance(status,dict):
            raise ControlError("Stop status is unavailable. Check the dashboard; the backend remains running.")
        session = status.get("charging", {})
        smartfox = status.get("smartfox", {})
        charger = smartfox.get("charger", {}) if isinstance(smartfox,dict) else None
        if not isinstance(session,dict) or not isinstance(charger,dict):
            raise ControlError("Stop status is unavailable. Check the dashboard; the backend remains running.")
        captured = timestamp(charger.get("read_at"))
        if session.get("stop_requested") is not True:
            raise ControlError("The stop request is unconfirmed. Check the dashboard and Smartfox; the backend remains running.")
        if not (charger.get("available") and charger.get("mode") == "OFF" and captured is not None and -5 <= time.time()-captured <= 30):
            raise ControlError("Charging stop requested, but Smartfox Off is unconfirmed. Check Smartfox; the backend remains running.")
        return ("Charging stop requested. Smartfox Off is confirmed; vehicle confirmation may still be pending. "
                "The backend remains running. After at least five seconds, use Stop.cmd to stop the backend. "
                "The charger stays explicitly Off until you choose A+ or start a new charging session.")

    def action(self, action, data_dir, port, no_browser=False, timeout=45):
        if action == "stop-charging":
            return self.stop_charging()
        if action not in ("stop", "restart"):
            raise ControlError("Unknown backend action.")
        previous = self.health()
        if previous is None:
            if action == "stop":
                return "CitiFoxCharger is already stopped."
            child = spawn_backend(["--port",str(port),"--data-dir",str(data_dir),"--no-browser"],data_dir)
            if not wait_until_ready(child,port):
                raise ControlError("Startup is unconfirmed. Please check Start.cmd or background.log.")
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
                    return "CitiFoxCharger stopped. A browser window may remain open."
                if action == "restart" and current and current.get("instance_id") != previous.get("instance_id"):
                    break
                time.sleep(.2)
            else:
                raise ControlError("Stop/restart was not confirmed. Check the status; no forced shutdown was performed.")
        if action == "restart" and not no_browser:
            from .browser import open_dashboard
            open_dashboard(self.base, "brave", True)
        return "CitiFoxCharger restarted. Existing monitoring resumes; no new charging start."
