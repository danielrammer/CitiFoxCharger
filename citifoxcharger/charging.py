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
        self.next_guard_attempt = 0
        self.value = {"status": "idle", "target_percent": 80, "message": None}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or type(data.get("target_percent")) is not int or not 1 <= data["target_percent"] <= 100:
                    raise ValueError()
                self.value = data
                restored = {'complete':'The previous charging session completed.',
                            'closed':'The previous monitoring session was explicitly closed.',
                            'error':'The previous session needs attention. Please check the vehicle and Smartfox.'}
                if data.get('status') in restored:
                    self.value['message'] = self.safety_stop_message() if self.safety_stopped else restored[data['status']]
                    if self.safety_stopped and data.get('status') == 'complete':
                        self.value['status'] = 'error'
                # Restart never repeats a start. Recover through current telemetry and stop/restore only.
                if data.get("status") == "starting":
                    self.value.update(status="interrupted", phase_start_pending=False,
                                      message="Checking the start state after restart. No repeated start command.")
                elif data.get("status") in ACTIVE:
                    self.value["message"] = "Monitoring is resuming after restart."
            except (ValueError, UnicodeError):
                self.value.update(status="error", message="The saved charging session is invalid. Please check it.")

    @property
    def active(self):
        return self.value.get("status") in ACTIVE

    @property
    def normal_mode(self):
        return "OFF" if self.value.get("stop_guard") or self.value.get("user_charger_mode") == "OFF" else "A+"

    @property
    def normal_name(self):
        return "Off" if self.normal_mode == "OFF" else "A+"

    def save(self, replace=False, **changes):
        candidate = {**({} if replace else self.value), **changes}
        atomic_write(self.path, json.dumps(candidate, ensure_ascii=False, indent=2).encode("utf-8"))
        self.value = candidate

    def state(self):
        value = copy.deepcopy(self.value)
        value["can_shutdown"] = not self.active or self.local_stop_allows_shutdown()
        web = self.service.cache.get("smartfox", {}).get("charger", {})
        # A local stop must remain available when the vehicle/cloud is unreachable.
        value["can_stop"] = bool(not self.service.stopping and self.web is not None and
                                 (self.active or web.get("control_available") or self.service.can_command("stop")))
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
                value["force_reason"] = "A stop is awaiting confirmation."
            elif self.active and type(web.get("power_kw")) in (int, float) and web["power_kw"] > 0.1:
                pass
            elif time.monotonic() < self.service.command_after:
                value["force_reason"] = "The last command is being checked. Please wait."
            elif value["can_force_with_stale"]:
                value["force_reason"] = "To start, explicitly allow the last reported charge level."
            elif car.get("stale", True):
                value["force_reason"] = "Current vehicle charge level is unavailable. Please wait for new vehicle data."
            elif not self.service.can_command("start"):
                value["force_reason"] = "Start access or request quota is not available yet."
            elif self.active and not self.value.get("stop_requested"):
                value["force_reason"] = "The previous start is not confirmed or the wallbox has no fresh readiness report."
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
                    and charger.get("mode") in (("OFF",) if self.value.get("stop_guard") else ("A+","OFF"))
                    and charger.get("status_code") in (0,1,2,5,6,255)
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
            raise ValueError("Permission to use older vehicle data must be true or false.")
        if type(target) is not int or not 1 <= target <= 100:
            raise ValueError("Target charge level must be between 1 and 100%.")
        if request_id == self.value.get("request_id"):
            if target != self.value.get("target_percent"):
                raise ValueError("This request ID belongs to a different target.")
            return self.state()
        retrying = self.active
        if retrying and (not self.can_retry_start(allow_stale) or target != self.value["target_percent"]):
            raise IntegrationError("session_active", "A manual charging session is already being monitored.")
        self.service.update("skoda")
        if not self.service.can_command("start", allow_stale) or not self.service.can_command("stop"):
            raise IntegrationError("blocked", "Current vehicle data and start/stop access are required.")
        captured = timestamp(self.service.cache.get("skoda", {}).get("captured_at"))
        using_stale = captured is not None and time.time() - captured > self.service.config.value["skoda"]["stale_seconds"]
        if using_stale and (not allow_stale or not self.can_start_with_stale()):
            raise IntegrationError("blocked", "Starting with older data requires fresh, confirmed wallbox readiness.")
        soc = self.service.cache.get("skoda", {}).get("soc")
        if type(soc) is not int or soc >= target:
            raise IntegrationError("target_reached", "The target must be above the current battery level.")
        charger = self.web.read(self.service.config.value["smartfox"])
        self.service.cache.setdefault("smartfox", {})["charger"] = charger
        if not charger["control_available"]:
            raise IntegrationError("smartfox_unverified", charger["control_reason"])
        if using_stale and not self.wallbox_ready():
            raise IntegrationError("blocked", "Smartfox has no fresh readiness report.")
        # Persist intent before any hardware effect, bound to this vehicle.
        self.save(replace=True, status="starting", target_percent=target, vin=self.service.config.value["skoda"]["vin"],
                  request_id=request_id, started_at=utcnow(), last_start_epoch=time.time(),
                  message="Checking Smartfox charging permission. No vehicle start has been sent yet.")
        try:
            self.service.cache.setdefault("smartfox", {})["charger"] = self.web.set_mode(self.service.config.value["smartfox"], "M+")
            self.service.cache["smartfox"]["charger"] = self.web.wait_ready(self.service.config.value["smartfox"])
            charger = self.service.cache["smartfox"]["charger"]
            if charger.get("phase_switching"):
                self.save(phase_start_pending=True, phase_start_deadline_epoch=time.time()+360,
                          phase_allow_stale=allow_stale, phase_ready_since_epoch=None,
                          message=self.phase_message(charger))
                self.next_attempt = 0
                return self.state()
            self.start_vehicle(using_stale)
        except (SmartfoxControlError, IntegrationError) as error:
            # Try restoring normal mode; do not pretend rollback or the vehicle start succeeded.
            try:
                self.service.cache.setdefault("smartfox", {})["charger"] = self.web.set_mode(self.service.config.value["smartfox"], "A+")
                self.save(status="error", message=str(error))
            except SmartfoxControlError:
                self.save(status="restore_pending", message="Start failed. Return to A+ is not confirmed yet.")
            raise IntegrationError("start_failed", str(error)) from None
        return self.state()

    def start_vehicle(self, using_stale):
        # Clear the deferred intent durably before a possibly uncertain network call.
        started = time.time()
        self.save(phase_start_pending=False, ready_at=utcnow(), last_start_epoch=started,
                  awaiting_fresh_soc=using_stale, fresh_soc_deadline=started+300 if using_stale else None)
        result = self.service.command("start", self.value["request_id"], using_stale)
        if result["status"] != "accepted":
            if result["status"] == "unknown" and using_stale:
                self.save(status="paused", message="Start is unconfirmed. The five-minute check for new vehicle data is active.")
                try:self.restore_auto()
                except SmartfoxControlError:pass
                return
            raise IntegrationError("start_failed", result["message"])
        target = self.value["target_percent"]
        self.save(status="monitoring", message=("Start requested with an older charge level. New vehicle data are expected within five minutes."
                                                if using_stale else f"Start requested. Charging is unconfirmed; target {target} % is monitored."))

    @staticmethod
    def phase_message(charger):
        seconds = charger.get("phase_switch_seconds")
        if type(seconds) is int and 0 <= seconds <= 3600:
            return f"Phase switching in progress. Smartfox reports {seconds} s. Waiting for charging permission."
        return "Phase switching in progress. Waiting for charging permission."

    def tick_phase_start(self, charger):
        now = time.time()
        deadline = self.value.get("phase_start_deadline_epoch")
        car = self.service.cache.get("skoda", {})
        if (type(deadline) not in (int,float) or not math.isfinite(deadline) or now >= deadline):
            self.automatic_stop("Phase switching timed out. No vehicle start was sent.", cause="phase_wait")
            return
        if type(car.get("soc")) is int and car["soc"] >= self.value["target_percent"]:
            self.automatic_stop("Target reached while waiting for phase switching. No vehicle start was sent.", cause="target")
            return
        allow_stale = self.value.get("phase_allow_stale") is True
        if not self.service.can_command("start",allow_stale) or not self.service.can_command("stop"):
            self.automatic_stop("Vehicle access was lost during phase switching. No vehicle start was sent.", cause="phase_wait")
            return
        read_at = timestamp(charger.get("read_at"))
        live = charger.get("available") and charger.get("telemetry_available") and read_at is not None and -5 <= now-read_at <= 30
        if not live:
            self.save(phase_ready_since_epoch=None, message="Waiting for fresh Smartfox data after phase switching.")
            return
        if charger.get("mode") != "M+" or charger.get("status_code") == 4 or charger.get("info_code") == 14:
            self.automatic_stop("Charging permission was lost during phase switching. No vehicle start was sent.", cause="phase_wait")
            return
        if charger.get("phase_switching"):
            self.save(phase_ready_since_epoch=None, message=self.phase_message(charger))
            return
        if not charger.get("ready_for_start"):
            self.save(phase_ready_since_epoch=None, message="Phase switching completed. Waiting for charging permission.")
            return
        since = self.value.get("phase_ready_since_epoch")
        if type(since) not in (int,float) or not math.isfinite(since):
            self.save(phase_ready_since_epoch=now, message="Phase switching completed. Checking charging permission.")
            return
        if read_at < since+3:
            return  # A newer local report must confirm stable readiness; no browser timers.
        captured = timestamp(car.get("captured_at"))
        using_stale = captured is not None and now-captured > self.service.config.value["skoda"]["stale_seconds"]
        try:
            self.start_vehicle(using_stale)
            if self.value["status"] != "monitoring":
                self.automatic_stop("Vehicle start is unconfirmed after phase switching.", cause="phase_wait")
        except IntegrationError:
            self.automatic_stop("Vehicle start is unconfirmed after phase switching.", cause="phase_wait")

    def change_target(self, target):
        if type(target) is not int or not 1 <= target <= 100:
            raise ValueError("Target charge level must be between 1 and 100%.")
        if not self.state()["can_adjust_target"]:
            raise IntegrationError("target_locked", "The target can only change during active monitoring before a stop is requested.")
        self.save(target_percent=target, message=f"New target {target} % gespeichert.")
        self.next_attempt = 0
        self.tick()  # A lower target may request stop on fresh SOC; never starts the car.
        return self.state()

    def restore_auto(self):
        if self.web is None:
            raise SmartfoxControlError("Lokale Smartfox-Steuerung fehlt.")
        if self.normal_mode == "OFF":
            value = self.web.set_charger_mode(self.service.config.value["smartfox"], "OFF")
        else:
            value = self.web.set_mode(self.service.config.value["smartfox"], "A+")
        self.service.cache.setdefault("smartfox", {})["charger"] = value
        self.save(auto_restored=True)

    def stop(self, request_id, automatic=False):
        # Durable stop intent before the upstream call. Also protects a user-requested stop below target.
        self.save(status="paused", stop_requested=True, awaiting_fresh_soc=False, stop_intent_epoch=time.time(),
                  stop_guard=True, phase_start_pending=False, phase_ready_since_epoch=None,
                  vin=self.service.config.value["skoda"]["vin"], message="Requesting a stop.")
        if self.value.get("stop_guard"):
            # A+ can supply power according to schedules; secure the local stop before a cloud timeout.
            try:
                self.restore_auto()
            except SmartfoxControlError:
                self.save(auto_restored=False)
        failure = None
        try:
            result = self.service.command("stop", request_id)
            if result["status"] != "accepted":
                raise IntegrationError("stop_unconfirmed", result["message"])
        except IntegrationError as error:
            failure = error
            self.save(status="paused", stop_failures=self.value.get("stop_failures", 0) + 1,
                      last_stop_failure_epoch=time.time(), message="Stop is unconfirmed. Monitoring is waiting for new vehicle data.")
            self.next_attempt = time.monotonic() + 60
        finally:
            # End M+ even if the cloud call is rejected, unavailable or rate limited.
            try:
                self.restore_auto()
            except SmartfoxControlError:
                self.save(auto_restored=False)
        if failure is not None:
            message = ("Smartfox is Off. Vehicle stop confirmation is pending." if self.value.get("auto_restored")
                       else "Wallbox Off is unconfirmed. Please check charging.")
            self.save(message=message)
            raise IntegrationError("stop_unconfirmed", message) from None
        self.save(status="stop_pending", stop_id=request_id, stop_requested_at=result["requested_at"],
                  stop_requested_epoch=result["requested_epoch"],
                  message="Target reached. Charging is stopping." if automatic else "Charging is stopping.")
        # Keep Off until newer non-charging telemetry confirms the vehicle stop.
        if not self.value.get("auto_restored"):
            self.save(message=f"Stop requested. Return to {self.normal_name} is not confirmed yet.")
        return self.state()

    def automatic_stop(self, reason, cause="target"):
        """A blocked cloud command must never bypass the independent local stop."""
        car = self.service.cache.get("skoda", {})
        self.save(stop_trigger_soc=car.get("soc"), stop_trigger_captured_at=car.get("captured_at"),
                  stop_trigger_read_at=car.get("read_at"), stop_trigger_at=utcnow())
        request_id = "limit-stop-" + self.value.get("request_id", "recovered-session") + "-" + str(self.value.get("stop_failures", 0))
        try:
            self.stop(request_id, automatic=True)
        except IntegrationError:
            self.next_attempt = time.monotonic() + 60
        secured = "Smartfox is Off. Vehicle stop confirmation is pending." if self.value.get("auto_restored") else "Wallbox Off is unconfirmed. Please check charging."
        self.save(stop_reason=reason, stop_cause=cause, message=reason + " " + secured)

    @property
    def safety_stopped(self):
        return self.value.get("stop_cause") in ("missing_soc","missing_initial_soc","phase_wait")

    def safety_stop_message(self):
        reason = {"missing_soc":"The target cannot be monitored without current vehicle data.",
                  "missing_initial_soc":"No new vehicle data within five minutes.",
                  "phase_wait":"Charging preparation failed before the target."}.get(self.value.get("stop_cause"),"")
        return f"Target {self.value['target_percent']} % was not confirmed reached. " + reason

    def finish_stop(self, message):
        # Release the temporary OFF only after newer vehicle telemetry confirms stopped.
        self.save(stop_guard=False)
        self.restore_auto()
        self.save(status="error" if self.safety_stopped else "complete",
                  message=self.safety_stop_message() if self.safety_stopped else message + f" Smartfox is set to {self.normal_name}.",
                  completed_at=utcnow(),vehicle_stop_confirmed=True)

    def stop_if_target_reached(self):
        car = self.service.cache.get("skoda", {})
        captured = timestamp(car.get("captured_at"))
        if (not self.service.stopping and self.active and not self.value.get("stop_requested")
                and self.value.get("vin") == self.service.config.value["skoda"]["vin"]
                and type(car.get("soc")) is int and captured is not None and captured <= time.time()+5
                and car["soc"] >= self.value["target_percent"]):
            self.automatic_stop(f"Ziel {self.value['target_percent']}% reached according to the last vehicle report.")
            return True
        return False

    def tick(self):
        # A newly received target report must not wait for the ten-second housekeeping tick.
        if self.stop_if_target_reached():
            return
        # Local OFF retries remain independent of any one-minute cloud backoff.
        if (self.active and self.value.get("stop_guard") and
                self.value.get("vin") == self.service.config.value["skoda"]["vin"] and
                time.monotonic() >= self.next_guard_attempt):
            self.next_guard_attempt = time.monotonic() + 10
            charger = self.service.cache.get("smartfox", {}).get("charger", {})
            if not self.value.get("auto_restored") or not charger.get("available") or charger.get("mode") != "OFF":
                try:
                    self.restore_auto()
                except SmartfoxControlError:
                    self.save(auto_restored=False, message="Automatic stop: wallbox Off is unconfirmed. Please check charging.")
        if time.monotonic() < self.next_attempt:
            return
        self.next_attempt = time.monotonic() + (2 if self.value.get("phase_start_pending") else 10)
        if not self.active:
            if self.normal_mode == "OFF":
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
            self.save(status="error", message="The VIN changed. Monitoring was not transferred to another vehicle.")
            return
        status = self.value["status"]
        charger = self.service.cache.get("smartfox", {}).get("charger", {})
        if status == "starting" and self.value.get("phase_start_pending") and not self.value.get("stop_requested"):
            self.tick_phase_start(charger)
            return
        if self.value.get("stop_requested") and (not self.value.get("auto_restored") or
                (self.value.get("stop_guard") and charger.get("mode") != "OFF")):
            try:
                self.restore_auto()
            except SmartfoxControlError:
                self.value["message"] = f"Stop is unconfirmed. Return to {self.normal_name} is still pending."
        if status == "restore_pending":
            try:
                self.restore_auto()
                self.save(status="error", message=f"Mode {self.normal_name} confirmed. Please check the vehicle status.")
            except SmartfoxControlError:
                pass
            return
        if status == "stop_pending":
            captured = timestamp(car.get("captured_at"))
            state = car.get("state")
            stopped = state in ("CONNECT_CABLE", "CONSERVING", "READY_FOR_CHARGING", "CHARGING_INTERRUPTED")
            if car.get("available") and not car.get("stale", True) and captured is not None and self.value.get("stop_requested_epoch", float("inf")) <= captured <= time.time() + 5 and stopped:
                try:
                    self.finish_stop("Charging ended.")
                except SmartfoxControlError:
                    self.save(message=f"The vehicle stopped. Return to {self.normal_name} is pending.")
            elif time.time() - self.value.get("stop_requested_epoch", time.time()) > 600:
                self.save(status="paused", last_stop_failure_epoch=self.value.get("stop_requested_epoch"),
                          stop_failures=self.value.get("stop_failures", 0) + 1,
                          message="Stop is still unconfirmed. Waiting for a newer vehicle status.")
            return
        captured = timestamp(car.get("captured_at"))
        # An old report at/above the goal is sufficient for a conservative stop, never for a start.
        if (not self.value.get("stop_requested") and type(car.get("soc")) is int and
                captured is not None and captured <= time.time() + 5 and car["soc"] >= self.value["target_percent"]):
            self.automatic_stop(f"Ziel {self.value['target_percent']}% reached according to the last vehicle report.")
            return
        if self.value.get("awaiting_fresh_soc"):
            captured = timestamp(car.get("captured_at"))
            if (car.get("available") and not car.get("stale", True) and type(car.get("soc")) is int
                    and captured is not None and self.value["last_start_epoch"] <= captured <= time.time() + 5):
                self.save(awaiting_fresh_soc=False, status="monitoring", message="New vehicle data received. The target is monitored.")
                status = "monitoring"
            elif time.time() >= self.value["fresh_soc_deadline"]:
                self.automatic_stop("No new vehicle data within five minutes.", cause="missing_initial_soc")
                return
            else:
                self.value["message"] = "Start requested. Waiting for a new vehicle charge level; a stop will be requested after five minutes without new data."
                return
        max_age = min(900, self.service.config.value["skoda"]["stale_seconds"])
        current_soc = (car.get("available") and not car.get("stale", True) and type(car.get("soc")) is int
                       and captured is not None and -5 <= time.time() - captured <= max_age)
        if not current_soc:
            if not self.value.get("stop_requested"):
                missing_since = self.value.get("data_missing_since")
                if type(missing_since) not in (int, float) or not math.isfinite(missing_since):
                    missing_since = time.time()
                    self.save(data_missing_since=missing_since)
                # Also bound later data loss, rather than leaving M+ active indefinitely.
                deadline = missing_since + 300
                if captured is not None and captured <= time.time() + 5:
                    deadline = min(deadline, captured + max_age)
                if time.time() >= deadline:
                    self.automatic_stop("The target cannot be monitored without current vehicle data.", cause="missing_soc")
                    return
            if status != "paused":
                self.save(status="paused", message="Waiting for current vehicle data. Charging will stop as a precaution if no new data arrive.")
            return
        if self.value.get("data_missing_since") is not None:
            self.save(data_missing_since=None)
        if status == "starting":
            # A process/file error after a start may leave an uncertain result: do not resend a start.
            self.save(status="interrupted", message="Checking the start state against vehicle data.")
            return
        if status == "interrupted" and car.get("state") in ("CONNECT_CABLE", "CONSERVING", "READY_FOR_CHARGING", "CHARGING_INTERRUPTED"):
            try:
                self.restore_auto()
                self.save(status="error", message=f"Interrupted start: the vehicle is not charging. Smartfox is set to {self.normal_name}.")
            except SmartfoxControlError:
                self.save(status="restore_pending", message=f"Interrupted start. Return to {self.normal_name} is pending.")
            return
        captured = timestamp(car.get("captured_at"))
        failure_epoch = self.value.get("last_stop_failure_epoch")
        if (failure_epoch and captured is not None and failure_epoch < captured <= time.time() + 5 and
                car.get("state") in ("CONNECT_CABLE", "CONSERVING", "READY_FOR_CHARGING", "CHARGING_INTERRUPTED")):
            try:
                self.finish_stop("Vehicle stop confirmed.")
            except SmartfoxControlError:
                self.value["message"] = f"The vehicle stopped. Return to {self.normal_name} is pending."
            return
        if failure_epoch and (captured is None or captured <= failure_epoch or car.get("state") != "CHARGING"):
            return  # Never blindly replay an uncertain command; fresh charging telemetry is required.
        if self.value.get("stop_failures", 0) >= 3:
            self.value["message"] = "Charging could not be confirmed stopped. Please check the vehicle and Smartfox."
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
            self.value["message"] = f"Ziel {self.value['target_percent']} % is still monitored. Smartfox changed to {charger.get('mode') or 'unbekannt'} gewechselt."
        elif charger.get("available") and charger.get("mode") == "M+":
            if self.can_retry_start():
                self.value["message"] = f"Charging is unconfirmed. You can explicitly retry the start; target {self.value['target_percent']} % bleibt erhalten."
            elif type(charger.get("power_kw")) in (int, float) and charger["power_kw"] > 0.1:
                self.value["message"] = f"Manual charging to {self.value['target_percent']} % is monitored."
            else:
                self.value["message"] = f"Smartfox is on M+. Vehicle start is unconfirmed; target {self.value['target_percent']} % is monitored."
        if status in ("paused", "interrupted"):
            self.save(status="monitoring", message=f"Manual charging to {self.value['target_percent']} % is monitored.")
