"""Persistent manual charging session; polled by the backend, never by browser timers."""
import copy
import json
import math
import time
from .config import atomic_write
from .modbus import utcnow
from .skoda import IntegrationError, timestamp
from .smartfox_web import SmartfoxControlError

ACTIVE = {"starting", "monitoring", "stop_pending", "restore_pending", "paused", "interrupted"}


class ChargingSession:
    def __init__(self, service, web):
        self.service, self.web = service, web
        self.path = service.config.directory / "charging-session.json"
        self.next_attempt = 0
        self.value = {"status": "idle", "target_percent": 80, "message": None}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or type(data.get("target_percent")) is not int or not 1 <= data["target_percent"] <= 100:
                    raise ValueError()
                self.value = data
                # Restart never repeats a start. Recover through current telemetry and stop/restore only.
                if data.get("status") == "starting":
                    self.value.update(status="interrupted", message="Startstatus nach Neustart wird geprüft. Kein erneuter Startbefehl.")
                elif data.get("status") in ACTIVE:
                    self.value["message"] = "Überwachung nach Neustart wird wieder aufgenommen."
            except (ValueError, UnicodeError):
                self.value.update(status="error", message="Gespeicherte Ladeüberwachung ist ungültig. Bitte prüfen.")

    @property
    def active(self):
        return self.value.get("status") in ACTIVE

    @property
    def normal_mode(self):
        return "OFF" if self.value.get("user_charger_mode") == "OFF" else "A+"

    @property
    def normal_name(self):
        return "Aus" if self.normal_mode == "OFF" else "A+"

    def save(self, replace=False, **changes):
        candidate = {**({} if replace else self.value), **changes}
        atomic_write(self.path, json.dumps(candidate, ensure_ascii=False, indent=2).encode("utf-8"))
        self.value = candidate

    def state(self):
        value = copy.deepcopy(self.value)
        value["can_shutdown"] = not self.active or self.local_stop_allows_shutdown()
        web = self.service.cache.get("smartfox", {}).get("charger", {})
        retry = self.can_retry_start()
        value["can_retry_start"] = retry
        value["can_adjust_target"] = self.value.get("status") in ("monitoring", "paused", "interrupted") and not self.value.get("stop_requested", False)
        value["can_force_with_stale"] = self.can_start_with_stale()
        value["can_retry_start_with_stale"] = self.active and value["can_force_with_stale"]
        value["can_force"] = ((not self.active or retry) and web.get("available", False)
                              and web.get("control_available", False)
                              and self.service.can_command("start") and self.service.can_command("stop"))
        value["force_reason"] = web.get("control_reason")
        car = self.service.cache.get("skoda", {})
        if not value["can_force"] and not value["force_reason"]:
            if self.active and self.value.get("stop_requested"):
                value["force_reason"] = "Ein Stopp wird noch bestätigt."
            elif self.active and type(web.get("power_kw")) in (int, float) and web["power_kw"] > 0.1:
                pass
            elif time.monotonic() < self.service.command_after:
                value["force_reason"] = "Der gerade gesendete Befehl wird geprüft. Bitte kurz warten."
            elif value["can_force_with_stale"]:
                value["force_reason"] = "Zum Start bitte den zuletzt gemeldeten Ladestand ausdrücklich freigeben."
            elif car.get("stale", True):
                value["force_reason"] = "Aktueller Fahrzeug-Ladestand fehlt. Bitte neue Fahrzeugdaten abwarten."
            elif not self.service.can_command("start"):
                value["force_reason"] = "Startzugriff oder Abfragekontingent ist noch nicht verfügbar."
            elif self.active and not self.value.get("stop_requested"):
                value["force_reason"] = "Der vorherige Start ist noch nicht abschließend geprüft oder die Wallbox meldet keine frische Ladebereitschaft."
        return value

    def local_stop_allows_shutdown(self):
        """User may close a stopped session without claiming cloud confirmation."""
        if not self.value.get("stop_requested") or not self.value.get("auto_restored"):
            return False
        intent = self.value.get("stop_intent_epoch")
        if type(intent) not in (int,float) or not math.isfinite(intent) or time.time()-intent<5:
            return False
        charger = self.service.cache.get("smartfox",{}).get("charger",{})
        captured,power = timestamp(charger.get("read_at")),charger.get("power_kw")
        return bool(charger.get("available") and charger.get("telemetry_available") is True
                    and charger.get("mode") in ("A+","OFF") and charger.get("status_code") in (0,1,2,5,6,255)
                    and type(power) in (int,float) and math.isfinite(power) and 0<=power<=.1
                    and captured is not None and captured>=intent and -5<=time.time()-captured<=30)

    def wallbox_ready(self):
        charger = self.service.cache.get("smartfox", {}).get("charger", {})
        read_at = timestamp(charger.get("read_at"))
        power = charger.get("power_kw")
        return bool(charger.get("available") and charger.get("control_available")
                    and charger.get("telemetry_available") and charger.get("status_code") == 2
                    and type(power) in (int, float) and 0 <= power <= 0.1
                    and read_at is not None and -5 <= time.time() - read_at <= 30)

    def can_start_with_stale(self):
        car = self.service.cache.get("skoda", {})
        captured = timestamp(car.get("captured_at"))
        actually_stale = captured is not None and time.time() - captured > self.service.config.value["skoda"]["stale_seconds"]
        return bool(actually_stale and not (self.active and self.value.get("stop_requested")) and self.wallbox_ready()
                    and self.service.can_command("start", allow_stale=True) and self.service.can_command("stop")
                    and (not self.active or self.can_retry_start(allow_stale=True)))

    def can_retry_start(self, allow_stale=False):
        if (self.value.get("status") not in ("monitoring", "paused", "interrupted")
                or self.value.get("stop_requested") or self.value.get("awaiting_fresh_soc")
                or not self.service.can_command("start", allow_stale)
                or not self.service.can_command("stop")):
            return False
        if not self.wallbox_ready():
            return False
        last_start = self.value.get("last_start_epoch") or timestamp(self.value.get("started_at"))
        if last_start is None or time.time() - last_start < 30:
            return False
        car = self.service.cache.get("skoda", {})
        captured = timestamp(car.get("captured_at"))
        noncharging = car.get("state") in ("READY_FOR_CHARGING", "CHARGING_INTERRUPTED", "CONSERVING")
        # A newer non-charging report or an unconfirmed ten-minute attempt permits an explicit retry.
        return bool((captured is not None and captured >= last_start and noncharging)
                    or time.time() - last_start >= 600)

    def start(self, target, request_id, allow_stale=False):
        if type(allow_stale) is not bool:
            raise ValueError("Freigabe für ältere Fahrzeugdaten muss Ja oder Nein sein.")
        if type(target) is not int or not 1 <= target <= 100:
            raise ValueError("Ziel-Ladestand muss zwischen 1 und 100 % liegen.")
        if request_id == self.value.get("request_id"):
            if target != self.value.get("target_percent"):
                raise ValueError("Diese Befehlskennung gehört zu einem anderen Ziel.")
            return self.state()
        retrying = self.active
        if retrying and (not self.can_retry_start(allow_stale) or target != self.value["target_percent"]):
            raise IntegrationError("session_active", "Eine manuelle Ladeüberwachung läuft bereits.")
        self.service.update("skoda")
        if not self.service.can_command("start", allow_stale) or not self.service.can_command("stop"):
            raise IntegrationError("blocked", "Aktuelle Fahrzeugdaten und Start/Stopp-Zugriff erforderlich.")
        captured = timestamp(self.service.cache.get("skoda", {}).get("captured_at"))
        using_stale = captured is not None and time.time() - captured > self.service.config.value["skoda"]["stale_seconds"]
        if using_stale and (not allow_stale or not self.can_start_with_stale()):
            raise IntegrationError("blocked", "Start mit älterem Ladestand erfordert eine frische, bestätigte Ladebereitschaft.")
        soc = self.service.cache.get("skoda", {}).get("soc")
        if type(soc) is not int or soc >= target:
            raise IntegrationError("target_reached", "Der Ziel-Ladestand muss über dem aktuellen Akkustand liegen.")
        charger = self.web.read(self.service.config.value["smartfox"])
        self.service.cache.setdefault("smartfox", {})["charger"] = charger
        if not charger["control_available"]:
            raise IntegrationError("smartfox_unverified", charger["control_reason"])
        if using_stale and not self.wallbox_ready():
            raise IntegrationError("blocked", "Smartfox meldet keine frische Ladebereitschaft.")
        # Persist intent before any hardware effect, bound to this vehicle.
        self.save(replace=True, status="starting", target_percent=target, vin=self.service.config.value["skoda"]["vin"],
                  request_id=request_id, started_at=utcnow(), last_start_epoch=time.time(),
                  message="Smartfox-Ladefreigabe wird geprüft. Fahrzeugstart wurde noch nicht gesendet.")
        try:
            self.service.cache.setdefault("smartfox", {})["charger"] = self.web.set_mode(self.service.config.value["smartfox"], "M+")
            self.service.cache["smartfox"]["charger"] = self.web.wait_ready(self.service.config.value["smartfox"])
            started = time.time()
            self.save(ready_at=utcnow(), last_start_epoch=started, awaiting_fresh_soc=using_stale,
                      fresh_soc_deadline=started + 300 if using_stale else None)
            result = self.service.command("start", request_id, allow_stale)
            if result["status"] != "accepted":
                if result["status"] == "unknown" and using_stale:
                    self.save(status="paused", message="Start unbestätigt. Fünf-Minuten-Prüfung auf neue Fahrzeugdaten läuft.")
                    try:
                        self.restore_auto()
                    except SmartfoxControlError:
                        pass
                    return self.state()
                raise IntegrationError("start_failed", result["message"])
            self.save(status="monitoring", message=("Start mit älterem Ladestand angefordert. Neue Fahrzeugdaten werden innerhalb von fünf Minuten erwartet."
                                                    if using_stale else f"Start angefordert. Ladestart noch nicht bestätigt; Ziel {target} % wird überwacht."))
        except (SmartfoxControlError, IntegrationError) as error:
            # Try restoring normal mode; do not pretend rollback or the vehicle start succeeded.
            try:
                self.service.cache.setdefault("smartfox", {})["charger"] = self.web.set_mode(self.service.config.value["smartfox"], "A+")
                self.save(status="error", message=str(error))
            except SmartfoxControlError:
                self.save(status="restore_pending", message="Start fehlgeschlagen. Rückkehr zu A+ ist noch nicht bestätigt.")
            raise IntegrationError("start_failed", str(error)) from None
        return self.state()

    def change_target(self, target):
        if type(target) is not int or not 1 <= target <= 100:
            raise ValueError("Ziel-Ladestand muss zwischen 1 und 100 % liegen.")
        if not self.state()["can_adjust_target"]:
            raise IntegrationError("target_locked", "Das Ziel kann nur während einer laufenden Überwachung ohne angeforderten Stopp geändert werden.")
        self.save(target_percent=target, message=f"Neues Ziel {target} % gespeichert.")
        self.next_attempt = 0
        self.tick()  # A lower target may request stop on fresh SOC; never starts the car.
        return self.state()

    def restore_auto(self):
        if self.normal_mode == "OFF":
            value = self.web.set_charger_mode(self.service.config.value["smartfox"], "OFF")
        else:
            value = self.web.set_mode(self.service.config.value["smartfox"], "A+")
        self.service.cache.setdefault("smartfox", {})["charger"] = value
        self.save(auto_restored=True)

    def stop(self, request_id, automatic=False):
        # Durable stop intent before the upstream call. Also protects a user-requested stop below target.
        self.save(status="paused", stop_requested=True, awaiting_fresh_soc=False, stop_intent_epoch=time.time(),
                  vin=self.service.config.value["skoda"]["vin"], message="Stopp wird angefordert.")
        try:
            result = self.service.command("stop", request_id)
            if result["status"] != "accepted":
                raise IntegrationError("stop_unconfirmed", result["message"])
        except IntegrationError as error:
            self.save(status="paused", stop_failures=self.value.get("stop_failures", 0) + 1,
                      last_stop_failure_epoch=time.time(), message="Stopp nicht bestätigt. Überwachung wartet auf neue Fahrzeugdaten.")
            self.next_attempt = time.monotonic() + 60
            raise IntegrationError("stop_unconfirmed", str(error)) from None
        finally:
            # End M+ even if the cloud call is rejected, unavailable or rate limited.
            try:
                self.restore_auto()
            except SmartfoxControlError:
                self.save(auto_restored=False)
        self.save(status="stop_pending", stop_id=request_id, stop_requested_at=result["requested_at"],
                  stop_requested_epoch=result["requested_epoch"],
                  message="Ziel erreicht. Laden wird beendet." if automatic else "Laden wird beendet.")
        # Restore A+ immediately after accepted stop; completion still needs newer non-charging telemetry.
        if not self.value.get("auto_restored"):
            self.save(message=f"Stopp angefordert. Rückkehr zu {self.normal_name} ist noch nicht bestätigt.")
        return self.state()

    def tick(self):
        if time.monotonic() < self.next_attempt:
            return
        self.next_attempt = time.monotonic() + 10
        if not self.active:
            if self.value.get("user_charger_mode") == "OFF":
                self.value["normalization_pending"] = False
                return
            charger = self.service.cache.get("smartfox", {}).get("charger", {})
            if charger.get("available") and charger.get("mode") != "A+":
                if charger.get("control_available"):
                    try:
                        self.web.set_mode(self.service.config.value["smartfox"], "A+")
                        self.value["normalization_pending"] = False
                    except SmartfoxControlError:
                        self.value["normalization_pending"] = True
                else:
                    self.value["normalization_pending"] = True
            else:
                self.value["normalization_pending"] = False
            return
        car = self.service.cache.get("skoda", {})
        if self.value.get("vin") and self.value["vin"] != self.service.config.value["skoda"]["vin"]:
            self.save(status="error", message="FIN wurde geändert. Überwachung wurde nicht auf ein anderes Fahrzeug übertragen.")
            return
        status = self.value["status"]
        if self.value.get("stop_requested") and not self.value.get("auto_restored"):
            try:
                self.restore_auto()
            except SmartfoxControlError:
                self.value["message"] = f"Stopp unbestätigt. Auch die Rückkehr zu {self.normal_name} steht noch aus."
        if status == "restore_pending":
            try:
                self.restore_auto()
                self.save(status="error", message=f"Modus {self.normal_name} bestätigt. Fahrzeugstatus bitte prüfen.")
            except SmartfoxControlError:
                pass
            return
        if status == "stop_pending":
            captured = timestamp(car.get("captured_at"))
            state = car.get("state")
            stopped = state in ("CONNECT_CABLE", "CONSERVING", "READY_FOR_CHARGING", "CHARGING_INTERRUPTED")
            if car.get("available") and not car.get("stale", True) and captured is not None and captured >= self.value.get("stop_requested_epoch", float("inf")) and stopped:
                try:
                    self.restore_auto()
                    self.save(status="complete", message=f"Laden beendet. Smartfox ist auf {self.normal_name}.", completed_at=utcnow())
                except SmartfoxControlError:
                    self.save(message=f"Fahrzeug hat gestoppt. Rückkehr zu {self.normal_name} steht aus.")
            elif time.time() - self.value.get("stop_requested_epoch", time.time()) > 600:
                self.save(status="paused", last_stop_failure_epoch=self.value.get("stop_requested_epoch"),
                          stop_failures=self.value.get("stop_failures", 0) + 1,
                          message="Stopp noch nicht bestätigt. Warte auf neuen Fahrzeugstatus.")
            return
        if self.value.get("awaiting_fresh_soc"):
            captured = timestamp(car.get("captured_at"))
            if (car.get("available") and not car.get("stale", True) and type(car.get("soc")) is int
                    and captured is not None and self.value["last_start_epoch"] <= captured <= time.time() + 5):
                self.save(awaiting_fresh_soc=False, status="monitoring", message="Neue Fahrzeugdaten empfangen. Ziel wird überwacht.")
                status = "monitoring"
            elif time.time() >= self.value["fresh_soc_deadline"]:
                try:
                    self.stop("stale-stop-" + self.value["request_id"])
                except IntegrationError:
                    pass
                restored = f"Smartfox ist auf {self.normal_name}." if self.value.get("auto_restored") else f"Rückkehr zu {self.normal_name} steht aus."
                self.save(message="Keine neuen Fahrzeugdaten innerhalb von fünf Minuten. Stopp angefordert, noch unbestätigt. " + restored)
                return
            else:
                self.value["message"] = "Start angefordert. Warte auf einen neuen Fahrzeug-Ladestand; ohne neue Daten wird nach fünf Minuten Stopp angefordert."
                return
        if not car.get("available") or car.get("stale", True) or type(car.get("soc")) is not int:
            if status != "paused":
                self.save(status="paused", message="Warte auf aktuelle Fahrzeugdaten. Zielüberwachung ist vorübergehend eingeschränkt.")
            return
        if status == "starting":
            # A process/file error after a start may leave an uncertain result: do not resend a start.
            self.save(status="interrupted", message="Startstatus wird anhand der Fahrzeugdaten geprüft.")
            return
        if status == "interrupted" and car.get("state") in ("CONNECT_CABLE", "CONSERVING", "READY_FOR_CHARGING", "CHARGING_INTERRUPTED"):
            try:
                self.restore_auto()
                self.save(status="error", message=f"Unterbrochener Start: Fahrzeug lädt nicht. Smartfox ist auf {self.normal_name}.")
            except SmartfoxControlError:
                self.save(status="restore_pending", message=f"Unterbrochener Start. Rückkehr zu {self.normal_name} steht aus.")
            return
        captured = timestamp(car.get("captured_at"))
        failure_epoch = self.value.get("last_stop_failure_epoch")
        if (failure_epoch and captured is not None and captured > failure_epoch and
                car.get("state") in ("CONNECT_CABLE", "CONSERVING", "READY_FOR_CHARGING", "CHARGING_INTERRUPTED")):
            try:
                self.restore_auto()
                self.save(status="complete", message=f"Fahrzeugstopp bestätigt. Smartfox ist auf {self.normal_name}.", completed_at=utcnow())
            except SmartfoxControlError:
                self.value["message"] = f"Fahrzeug hat gestoppt. Rückkehr zu {self.normal_name} steht aus."
            return
        if failure_epoch and (captured is None or captured <= failure_epoch or car.get("state") != "CHARGING"):
            return  # Never blindly replay an uncertain command; fresh charging telemetry is required.
        if self.value.get("stop_failures", 0) >= 3:
            self.value["message"] = "Laden konnte nicht bestätigt gestoppt werden. Bitte Fahrzeug und Smartfox prüfen."
            return
        if car["soc"] >= self.value["target_percent"] or self.value.get("stop_requested"):
            if self.service.can_command("stop"):
                try:
                    request_id = "limit-stop-" + self.value.get("request_id", "recovered-session") + "-" + str(self.value.get("stop_failures", 0))
                    self.stop(request_id, automatic=True)
                except IntegrationError:
                    self.next_attempt = time.monotonic() + 60
            return
        charger = self.service.cache.get("smartfox", {}).get("charger", {})
        if charger.get("available") and charger.get("mode") != "M+":
            self.value["message"] = f"Ziel {self.value['target_percent']} % wird weiter überwacht. Smartfox ist auf {charger.get('mode') or 'unbekannt'} gewechselt."
        elif charger.get("available") and charger.get("mode") == "M+":
            if self.can_retry_start():
                self.value["message"] = f"Keine Ladung bestätigt. Du kannst den Start erneut freigeben; Ziel {self.value['target_percent']} % bleibt erhalten."
            elif type(charger.get("power_kw")) in (int, float) and charger["power_kw"] > 0.1:
                self.value["message"] = f"Manuelles Laden bis {self.value['target_percent']} % wird überwacht."
            else:
                self.value["message"] = f"Smartfox ist auf M+. Fahrzeugstart noch nicht bestätigt; Ziel {self.value['target_percent']} % wird überwacht."
        if status in ("paused", "interrupted"):
            self.save(status="monitoring", message=f"Manuelles Laden bis {self.value['target_percent']} % wird überwacht.")
