import copy
import math
import threading
import time
from .config import validate
from .modbus import SmartfoxAdapter, utcnow
from .skoda import SkodaAdapter, IntegrationError, timestamp
from .smartfox_web import SmartfoxWeb, SmartfoxControlError
from .charging import ChargingSession


class Service:
    def __init__(self, config, secrets, smartfox=None, skoda=None, web=None):
        self.config, self.secrets = config, secrets
        self.sf, self.car = smartfox or SmartfoxAdapter(), skoda or SkodaAdapter()
        # User explicitly authorized the local vendor-web control on 2026-10-16.
        self.web = web if web is not None else (SmartfoxWeb(allow_undocumented_control=True) if smartfox is None else None)
        self.lock = threading.RLock()
        self.cache, self.next_due, self.last_success = {}, {}, {}
        self.command_results = {}
        self.last_command = None
        self.command_after = 0
        self.events = []
        self.charging = ChargingSession(self, self.web)
        self.worker_stop = threading.Event()
        self.worker = None
        self.last_attempt = {}
        self.manual_refresh_after = 0

    def poll_interval(self, source):
        configured = self.config.value[source]["poll_seconds"]
        if source == "smartfox":
            return configured
        car = self.cache.get("skoda", {})
        charger = self.cache.get("smartfox", {}).get("charger", {})
        power = charger.get("power_kw")
        charging = (self.charging.active or car.get("state") == "CHARGING" or
                    (charger.get("available") and isinstance(power, (int, float)) and power > 0.1))
        try:
            remaining = int(self.car.quota_remaining)
            reset_in = self.car.quota_reset_at - self.car.clock()
        except (TypeError, ValueError, AttributeError):
            remaining, reset_in = 0, 0
        if remaining > 2 and reset_in > 0:
            # Keep two calls available for commands; use spare quota for faster charging updates.
            budget_spacing = math.ceil(reset_in / (remaining - 2))
            return max(60 if charging else max(600, configured), budget_spacing)
        return max(180, configured) if charging else max(600, configured)

    def start_worker(self):
        if self.worker is not None:
            return
        def run():
            while not self.worker_stop.is_set():
                try:
                    self.status()
                except Exception:
                    # Never log credentials or declare monitoring successful after an internal error.
                    with self.lock:
                        if self.charging.active:
                            try:
                                self.charging.save(status="paused", message="Überwachung unterbrochen. Daten und lokale Speicherung prüfen.")
                            except OSError:
                                self.charging.value.update(status="paused", message="Überwachung unterbrochen. Lokale Speicherung prüfen.")
                self.worker_stop.wait(2)
        self.worker = threading.Thread(target=run, daemon=True, name="charging-monitor")
        self.worker.start()

    def stop_worker(self):
        self.worker_stop.set()
        if self.worker is not None:
            self.worker.join(timeout=25)

    def event(self, message):
        self.events.insert(0, {"at": utcnow(), "message": message})
        del self.events[30:]

    def reset(self):
        self.cache.clear()
        self.next_due.clear()
        self.last_success.clear()
        self.last_command = None
        self.command_results.clear()
        # Do not reset upstream rate-limit timers or command cooldowns on config changes.

    def save_config(self, value):
        with self.lock:
            if self.charging.active:
                raise ValueError("Bitte die laufende Ladeüberwachung zuerst beenden.")
            self.config.save(validate(value))
            self.reset()
            self.event("Konfiguration gespeichert.")

    def set_key(self, key, persist):
        with self.lock:
            self.secrets.set(key, persist)
            self.reset()
            self.event("API-Schlüssel hinterlegt.")

    def clear_key(self):
        with self.lock:
            if self.charging.active:
                raise ValueError("Bitte die laufende Ladeüberwachung zuerst beenden. Einen abgelaufenen Schlüssel kannst du ersetzen.")
            self.secrets.clear()
            self.reset()
            self.event("API-Schlüssel entfernt.")

    def update(self, source):
        now = time.monotonic()
        if now < self.next_due.get(source, 0):
            return
        config = self.config.value[source]
        self.last_attempt[source] = now
        try:
            value = self.sf.read(config) if source == "smartfox" else self.car.read(config, self.secrets.key)
            if source == "smartfox":
                try:
                    value["charger"] = self.web.read(config) if self.web else {"available": False, "control_available": False}
                except SmartfoxControlError as error:
                    value["charger"] = {"available": False, "control_available": False, "message": str(error)}
            self.cache[source] = value
            if value.get("available"):
                self.last_success[source] = time.monotonic()
        except IntegrationError as error:
            previous = self.cache.get(source, {})
            if error.code == "quota_reserved" and previous.get("available"):
                self.cache[source] = {**previous, "code": error.code, "message": str(error),
                                      "retry_seconds": error.retry_seconds}
            else:
                self.cache[source] = {**previous, "available": False, "code": error.code,
                                      "message": str(error), "stale": True, "operations": [],
                                      "retry_seconds": error.retry_seconds}
            if error.retry_seconds:
                self.next_due[source] = time.monotonic() + error.retry_seconds
        self.next_due[source] = max(self.next_due.get(source, 0), time.monotonic() + self.poll_interval(source))

    def can_command(self, action):
        current = self.cache.get("skoda", {})
        if not self.secrets.key or not self.config.value["skoda"]["vin"]:
            return False
        expiry = timestamp(self.car.key_expires_at)
        if expiry is not None and expiry <= time.time():
            return False
        if not current.get("available"):
            return False
        captured = timestamp(current.get("captured_at"))
        age = time.time() - captured if captured is not None else None
        if action == "start" and (current.get("stale", True) or age is None or not -300 <= age <= self.config.value["skoda"]["stale_seconds"]):
            return False
        if action == "start" and time.monotonic() - self.last_success.get("skoda", 0) > max(1200, 2 * self.config.value["skoda"]["poll_seconds"]):
            return False
        if time.monotonic() < self.car.blocked_until:
            return False
        if time.monotonic() < self.command_after and (action == "start" or (self.last_command and self.last_command["action"] == "stop")):
            return False
        return {"start": "startCharging", "stop": "stopCharging"}[action] in current.get("operations", [])

    def check_command_observation(self):
        command = self.last_command
        if not command or command["status"] not in ("accepted", "unknown"):
            return
        car = self.cache.get("skoda", {})
        captured = timestamp(car.get("captured_at"))
        # Only newer vehicle telemetry may confirm an operation; never infer state from HTTP 202.
        state = car.get("state")
        matches = state == "CHARGING" if command["action"] == "start" else state in (
            "CONNECT_CABLE", "CONSERVING", "READY_FOR_CHARGING", "CHARGING_INTERRUPTED")
        if car.get("available") and not car.get("stale") and captured is not None and captured >= command["requested_epoch"] and matches:
            command["status"] = "observed"
            command["message"] = "Passender Fahrzeugstatus nach dem Befehl empfangen."
        elif time.time() - command["requested_epoch"] > 600:
            command["status"] = "unconfirmed"
            command["message"] = "Kein bestätigender Fahrzeugstatus innerhalb von 10 Minuten. Bitte Fahrzeug/App prüfen."

    def status(self):
        with self.lock:
            self.update("smartfox")
            self.update("skoda")
            car = self.cache["skoda"]
            captured = timestamp(car.get("captured_at"))
            if captured is None or not -300 <= time.time() - captured <= self.config.value["skoda"]["stale_seconds"]:
                car["stale"] = True
            self.check_command_observation()
            self.charging.tick()
            return {"smartfox": copy.deepcopy(self.cache["smartfox"]),
                    "skoda": copy.deepcopy(self.cache["skoda"]),
                    "controls": {a: self.can_command(a) for a in ("start", "stop")},
                    "last_command": copy.deepcopy(self.last_command), "events": copy.deepcopy(self.events),
                    "charging": self.charging.state(),
                    "server_at": utcnow(), "next_skoda_seconds": max(0, int(self.next_due.get("skoda", 0) - time.monotonic()))}

    def force_charge(self, target, request_id):
        with self.lock:
            return self.charging.start(target, request_id)

    def refresh_now(self):
        with self.lock:
            if not self.secrets.key or not self.config.value["skoda"]["vin"]:
                return {"message": "Bitte zuerst den Fahrzeugzugang einrichten.", "status": self.status()}
            now = time.monotonic()
            if now < self.manual_refresh_after:
                wait = math.ceil(self.manual_refresh_after - now)
                return {"message": f"Erneute Fahrzeugabfrage in {wait} Sekunden möglich.", "status": self.status()}
            self.manual_refresh_after = now + 60
            if now - self.last_attempt.get("skoda", 0) < 60:
                wait = math.ceil(60 - (now - self.last_attempt.get("skoda", 0)))
                self.manual_refresh_after = now + wait
                return {"message": f"Fahrzeug wurde gerade abgefragt. Erneut in {wait} Sekunden.", "status": self.status()}
            if self.car.clock() < self.car.blocked_until:
                return {"message": "Škoda verlangt noch eine Abfragepause.", "status": self.status()}
            self.next_due["skoda"] = 0
            self.update("skoda")
            self.event("Fahrzeugdaten erneut bei Škoda abgefragt.")
            return {"message": "Verfügbare Fahrzeugdaten neu abgefragt.", "status": self.status()}

    def stop_charge(self, request_id):
        with self.lock:
            self.update("skoda")
            self.update("smartfox")
            if self.web is None:
                raise IntegrationError("blocked", "Smartfox-Anbindung ist nicht eingerichtet.")
            return self.charging.stop(request_id)

    def command(self, action, request_id):
        with self.lock:
            if request_id in self.command_results:
                return copy.deepcopy(self.command_results[request_id])
            self.update("skoda")
            if not self.can_command(action):
                raise IntegrationError("blocked", "Befehl gesperrt: aktuelle Fahrzeugdaten, unterstützte Aktion und gültiger API-Zugriff erforderlich. Nach einem Befehl gilt eine Pause von 30 Sekunden.")
            self.command_after = time.monotonic() + 30
            command = {"action": action, "requested_at": utcnow(), "requested_epoch": time.time(),
                       "status": "accepted", "message": "Škoda hat den Befehl angenommen. Fahrzeugstatus noch unbestätigt."}
            try:
                self.car.command(action, self.config.value["skoda"], self.secrets.key)
            except IntegrationError as error:
                command.update(status="unknown" if error.uncertain else "rejected", message=str(error))
                # Rejection invalidates controls until fresh telemetry/capabilities can be retrieved.
                self.cache["skoda"] = {**self.cache.get("skoda", {}), "available": False,
                                       "message": str(error), "operations": [], "stale": True}
            self.last_command = command
            if command["status"] in ("accepted", "unknown"):
                # One prompt read after an explicit command; later reads follow the quota budget.
                self.next_due["skoda"] = min(self.next_due.get("skoda", float("inf")), time.monotonic() + 15)
            self.command_results[request_id] = copy.deepcopy(command)
            if len(self.command_results) > 100:
                del self.command_results[next(iter(self.command_results))]
            self.event(("Start" if action == "start" else "Stopp") + ": " + command["message"])
            return copy.deepcopy(command)
