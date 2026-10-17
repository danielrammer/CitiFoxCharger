import copy
import math
import json
import hashlib
import threading
import time
from .config import validate, atomic_write
from .modbus import SmartfoxAdapter, utcnow
from .skoda import SkodaAdapter, IntegrationError, timestamp
from .smartfox_web import SmartfoxWeb, SmartfoxControlError
from .charging import ChargingSession
from .power import PowerGuard


class Service:
    def __init__(self, config, secrets, smartfox=None, skoda=None, web=None, power=None):
        self.config, self.secrets = config, secrets
        self.sf, self.car = smartfox or SmartfoxAdapter(), skoda or SkodaAdapter()
        # User explicitly authorized the local vendor-web control on 2026-10-16.
        self.web = web if web is not None else (SmartfoxWeb(allow_undocumented_control=True, allow_heater_control=True) if smartfox is None else None)
        self.lock = threading.RLock()
        self.cache, self.next_due, self.last_success = {}, {}, {}
        self.command_results = {}
        self.heater_results = {}
        self.last_command = None
        self.command_after = 0
        self.events = []
        self.network = {"enabled":False,"message":"Home network access is disabled. Run Setup-Network.cmd on the tablet."}
        self.charging = ChargingSession(self, self.web)
        self.worker_stop = threading.Event()
        self.worker = None
        self.last_attempt = {}
        self.manual_refresh_after = 0
        self.power = power if power is not None else PowerGuard()
        self.stopping = False
        self.restore_rate_state()
        self.restore_vehicle_cache()

    def restore_vehicle_cache(self):
        try:
            data = json.loads((self.config.directory / "vehicle-cache.json").read_text(encoding="utf-8"))
            if (not self.secrets.key or data.get("vin") != self.config.value["skoda"]["vin"]
                    or data.get("key_hash") != hashlib.sha256(self.secrets.key.encode()).hexdigest()):
                return
            car = data["status"]
            read_at, captured = timestamp(car.get("read_at")), timestamp(car.get("captured_at"))
            age = time.time()-read_at if read_at is not None else None
            if (not car.get("available") or type(car.get("soc")) is not int or not 0 <= car["soc"] <= 100
                    or age is None or not 0 <= age <= 86400 or captured is None
                    or not isinstance(car.get("operations"), list)
                    or any(action not in ("startCharging", "stopCharging") for action in car["operations"])):
                return
            car["stale"] = not -300 <= time.time()-captured <= self.config.value["skoda"]["stale_seconds"]            car['message'] = 'Restored previous vehicle data with their original timestamps.'
            self.cache["skoda"] = car
            self.last_success["skoda"] = time.monotonic()-age
            self.car.key_expires_at = car.get("key_expires_at")
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass

    def restore_rate_state(self):
        try:
            data = json.loads((self.config.directory / "rate-limits.json").read_text(encoding="utf-8"))
            if data.get("vin") != self.config.value["skoda"]["vin"]:
                return
            now, clock = time.time(), self.car.clock()
            for name in ("blocked_until", "quota_reset_at"):
                deadline = data.get(name)
                if type(deadline) in (int, float) and math.isfinite(deadline) and 0 <= deadline-now <= 7*86400:
                    setattr(self.car, name, clock+deadline-now)
            quota = data.get("quota_remaining")
            if isinstance(quota, str) and quota.isdecimal() and len(quota) <= 6:
                self.car.quota_remaining = quota
            cooldown = data.get("command_after")
            if type(cooldown) in (int, float) and math.isfinite(cooldown) and 0 <= cooldown-now <= 60:
                self.command_after = time.monotonic()+cooldown-now
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save_rate_state(self):
        now, clock = time.time(), self.car.clock()
        data = {"vin":self.config.value["skoda"]["vin"], "quota_remaining":self.car.quota_remaining,
                "blocked_until":now+max(0,self.car.blocked_until-clock),
                "quota_reset_at":now+max(0,self.car.quota_reset_at-clock),
                "command_after":now+max(0,self.command_after-time.monotonic())}
        atomic_write(self.config.directory / "rate-limits.json",json.dumps(data).encode())
        snapshot = {"vin":self.config.value["skoda"]["vin"],
                    "key_hash":hashlib.sha256(self.secrets.key.encode()).hexdigest(),
                    "status":self.cache.get("skoda", {})}
        atomic_write(self.config.directory / "vehicle-cache.json",json.dumps(snapshot).encode())

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
                                self.charging.save(status="paused", message="Monitoring interrupted. Check data and local storage.")
                            except OSError:
                                self.charging.value.update(status="paused", message="Monitoring interrupted. Check local storage.")
                self.worker_stop.wait(2)
        self.worker = threading.Thread(target=run, daemon=True, name="charging-monitor")
        self.worker.start()

    def stop_worker(self):
        self.worker_stop.set()
        if self.worker is not None:
            self.worker.join(timeout=25)
        self.power.close()

    def prepare_shutdown(self):
        with self.lock:
            if self.charging.active:
                if not self.charging.value.get("stop_requested"):
                    raise ValueError("Charging monitoring is active. Stop charging first and wait for confirmation.")
                if self.web is None:
                    raise ValueError("Local Smartfox access is missing for the stop check.")
                if self.web is not None:
                    try:self.cache.setdefault("smartfox",{})["charger"] = self.web.read(self.config.value["smartfox"])
                    except SmartfoxControlError:
                        raise ValueError("Smartfox cannot confirm that charging stopped. Please check the connection.") from None
                if not self.charging.local_stop_allows_shutdown():
                    raise ValueError("The stop is being checked. Exit is allowed once Smartfox confirms A+ or Off with no charging power. Please wait.")
                self.charging.save(status="closed",ended_by_user=True,vehicle_stop_confirmed=False,
                                   message="Monitoring explicitly closed: Smartfox reports no charging power. Vehicle stop confirmation is still pending.",closed_at=utcnow())
            self.save_rate_state()
            self.stopping = True

    def prepare_restart(self):
        with self.lock:
            if self.stopping:
                raise ValueError("The app is already stopping.")
            if (self.secrets.key or self.charging.active) and not self.secrets.persisted:
                raise ValueError("Before restarting, save the API key with Windows encryption in Settings.")
            self.charging.save()
            self.save_rate_state()
            self.stopping = True

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
                raise ValueError("Please end the active charging session first.")
            self.config.save(validate(value))
            self.reset()
            self.event("Configuration saved.")

    def set_key(self, key, persist):
        with self.lock:
            self.secrets.set(key, persist)
            self.reset()
            self.event("API key configured.")

    def clear_key(self):
        with self.lock:
            if self.charging.active:
                raise ValueError("Please end the active charging session first. An expired key can be replaced.")
            self.secrets.clear()
            self.reset()
            self.event("API key removed.")

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

    def can_command(self, action, allow_stale=False):
        current = self.cache.get("skoda", {})
        if self.stopping:
            return False
        if not self.secrets.key or not self.config.value["skoda"]["vin"]:
            return False
        expiry = timestamp(self.car.key_expires_at)
        if expiry is not None and expiry <= time.time():
            return False
        if not current.get("available"):
            return False
        captured = timestamp(current.get("captured_at"))
        age = time.time() - captured if captured is not None else None
        if action == "start":
            if allow_stale:
                # Explicit manual-session exception; never extend the displayed freshness.
                if age is None or not -300 <= age <= 86400 or type(current.get("soc")) is not int or not 0 <= current["soc"] <= 100:
                    return False
                max_read_age = 300
            else:
                if current.get("stale", True) or age is None or not -300 <= age <= self.config.value["skoda"]["stale_seconds"]:
                    return False
                max_read_age = max(1200, 2 * self.config.value["skoda"]["poll_seconds"])
            if time.monotonic() - self.last_success.get("skoda", 0) > max_read_age:
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
            command["message"] = "Matching vehicle status received after the command."
        elif time.time() - command["requested_epoch"] > 600:
            command["status"] = "unconfirmed"
            command["message"] = "No confirming vehicle status within 10 minutes. Please check the vehicle/app."

    def status(self):
        with self.lock:
            if not self.stopping:
                self.update("smartfox")
                self.update("skoda")
            car = self.cache.get("skoda", {"available":False,"stale":True})
            captured = timestamp(car.get("captured_at"))
            if captured is None or not -300 <= time.time() - captured <= self.config.value["skoda"]["stale_seconds"]:
                car["stale"] = True
            self.check_command_observation()
            if not self.stopping:
                self.charging.tick()
            self.power.update(self.charging.active and not self.worker_stop.is_set() and not self.stopping)
            return {"smartfox": copy.deepcopy(self.cache.get("smartfox", {"available":False})),
                    "skoda": copy.deepcopy(car),
                    "controls": {a: self.can_command(a) for a in ("start", "stop")},
                    "last_command": copy.deepcopy(self.last_command), "events": copy.deepcopy(self.events),
                    "charging": self.charging.state(),
                    "monitoring": {"running": self.worker is not None and self.worker.is_alive(),
                                   "power": self.power.state()},
                    "server_at": utcnow(), "next_skoda_seconds": max(0, int(self.next_due.get("skoda", 0) - time.monotonic()))}

    def force_charge(self, target, request_id, allow_stale=False):
        with self.lock:
            if self.stopping:raise IntegrationError("blocked","The app is restarting or stopping.")
            self.power.update(not self.stopping)
            try:
                return self.charging.start(target, request_id, allow_stale)
            finally:
                self.power.update(self.charging.active and not self.worker_stop.is_set())

    def refresh_now(self):
        with self.lock:
            if self.stopping:raise IntegrationError("blocked","The app is restarting or stopping.")
            if not self.secrets.key or not self.config.value["skoda"]["vin"]:
                return {"message": "Please configure vehicle access first.", "status": self.status()}
            now = time.monotonic()
            if now < self.manual_refresh_after:
                wait = math.ceil(self.manual_refresh_after - now)
                return {"message": f"Next vehicle query in {wait} seconds.", "status": self.status()}
            self.manual_refresh_after = now + 60
            if now - self.last_attempt.get("skoda", 0) < 60:
                wait = math.ceil(60 - (now - self.last_attempt.get("skoda", 0)))
                self.manual_refresh_after = now + wait
                return {"message": f"The vehicle was queried recently. Try again in {wait} seconds.", "status": self.status()}
            if self.car.clock() < self.car.blocked_until:
                return {"message": "Škoda still requires a request cooldown.", "status": self.status()}
            self.next_due["skoda"] = 0
            self.update("skoda")
            self.event("Vehicle data queried again from Škoda.")
            return {"message": "Available vehicle data queried again.", "status": self.status()}

    def stop_charge(self, request_id):
        with self.lock:
            if self.stopping:raise IntegrationError("blocked","The app is restarting or stopping.")
            self.update("skoda")
            self.update("smartfox")
            if self.web is None:
                raise IntegrationError("blocked", "Smartfox integration is not configured.")
            return self.charging.stop(request_id)

    def change_target(self, target):
        with self.lock:
            if self.stopping:
                raise IntegrationError("blocked", "The app is restarting or stopping.")
            result = self.charging.change_target(target)
            self.event(f"Manual charge target changed to {target}%.")
            return result

    def set_heater_mode(self, device, mode, request_id):
        with self.lock:
            if device not in ("heater", "heater2", "hot_water") or mode not in ("auto", "manual", "off"):
                raise ValueError("Invalid heater mode.")
            if request_id in self.heater_results:
                previous = self.heater_results[request_id]
                if (previous["device"],previous["mode"]) != (device,mode):
                    raise ValueError("Befehlskennung bereits anderweitig verwendet.")
                return copy.deepcopy(previous)
            if self.stopping or self.web is None:
                raise IntegrationError("blocked", "Heater control is currently unavailable.")
            # Keep even uncertain outcomes idempotent; a repeat requires a deliberate new request.
            result = {"device":device,"mode":mode,"status":"unconfirmed","message":"Heater mode change is unconfirmed."}
            self.heater_results[request_id] = result
            if len(self.heater_results) > 100:
                del self.heater_results[next(iter(self.heater_results))]
            try:
                self.cache.setdefault("smartfox", {})["charger"] = self.web.set_heater_mode(self.config.value["smartfox"],device,mode)
            except SmartfoxControlError as error:
                self.next_due["smartfox"] = 0
                result["message"] = str(error)
                raise IntegrationError("heater_unconfirmed", str(error)) from None
            name = {"heater":"Heater","heater2":"Heater 2","hot_water":"Hot water"}[device]
            result.update(status="confirmed",message=name+" is set to "+{"auto":"Automatic.","manual":"Manual.","off":"Off."}[mode])
            self.event(result["message"])
            return copy.deepcopy(result)

    def set_charger_mode(self, mode, request_id):
        with self.lock:
            if mode not in ("OFF","A+","M+"):
                raise ValueError("Only Off, A+ and M+ are supported here.")
            if self.stopping or self.web is None:
                raise IntegrationError("blocked","Charger control is currently unavailable.")
            if mode != "OFF" and self.charging.value.get("stop_guard") and self.charging.active:
                raise IntegrationError("blocked","Automatic stop is being checked. The charger stays Off until the vehicle confirms the stop.")
            if mode == "M+" and (not self.charging.active or self.charging.value.get("stop_requested")):
                raise IntegrationError("blocked","Use Force charge with an active percentage target to select M+.")
            if mode == "M+" and (not self.can_command("start") or not self.can_command("stop")):
                raise IntegrationError("blocked","M+ requires current vehicle data and start/stop access.")
            if request_id in self.heater_results:
                result = self.heater_results[request_id]
                if (result["device"],result["mode"]) != ("charger",mode):
                    raise ValueError("Befehlskennung bereits anderweitig verwendet.")
                return copy.deepcopy(result)
            result = {"device":"charger","mode":mode,"status":"unconfirmed","message":"Wallbox mode is unconfirmed."}
            self.heater_results[request_id] = result
            if len(self.heater_results)>100:
                del self.heater_results[next(iter(self.heater_results))]
            # Explicit OFF must survive the ordinary A+ normalization and a pending target stop.
            self.charging.save(user_charger_mode="OFF" if mode == "OFF" else None,
                               stop_guard=self.charging.value.get("stop_guard", False) if self.charging.active else False)
            try:
                self.cache.setdefault("smartfox",{})["charger"] = self.web.set_charger_mode(self.config.value["smartfox"],mode)
            except SmartfoxControlError as error:
                self.next_due["smartfox"] = 0
                result["message"] = str(error)
                raise IntegrationError("charger_unconfirmed",str(error)) from None
            result.update(status="confirmed",message="The charger is set to "+("Off." if mode == "OFF" else mode+"."))
            self.event(result["message"])
            return copy.deepcopy(result)

    def command(self, action, request_id, allow_stale=False):
        with self.lock:
            if self.stopping:raise IntegrationError("blocked","The app is restarting or stopping.")
            if request_id in self.command_results:
                return copy.deepcopy(self.command_results[request_id])
            self.update("skoda")
            if not self.can_command(action, allow_stale):
                raise IntegrationError("blocked", "Command blocked: current vehicle data, a supported action and valid API access are required. Commands have a 30-second cooldown.")
            self.command_after = time.monotonic() + 30
            command = {"action": action, "requested_at": utcnow(), "requested_epoch": time.time(),
                       "status": "accepted", "message": "Škoda accepted the command. Vehicle status is not confirmed yet."}
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
            self.event(("Start" if action == "start" else "Stop") + ": " + command["message"])
            return copy.deepcopy(command)
