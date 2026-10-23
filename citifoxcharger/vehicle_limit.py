"""Persisted native charge-limit preflight and restoration. Never edits profiles.

A 202 is only an accepted request. A newer vehicle capture must read back the
requested setting before M+/Start. Uncertain writes are observed, not replayed.
"""
import copy
import math
import time
import uuid
from .skoda import IntegrationError, timestamp
from .smartfox_web import SmartfoxControlError


class VehicleLimit:
    def __init__(self, service):
        self.service = service
        record = self.record
        if record.get("phase") == "verified" and timestamp(record.get("verified_at")) is None:
            previous = record.get("restore_percent")
            self.save(phase="attention", restore_percent=previous if type(previous) is int and 1 <= previous <= 100 else 80)
            self.session.save(status="restore_pending", stop_guard=True,
                              message="Saved vehicle-limit proof is invalid. Charger stays Off; restore the normal limit in Settings.")
            return
        if record.get("phase") == "setting":
            # Restart cancels a pending start, even if its limit is later observed.
            self.save(phase="restore_needed", cancelled=True)
            self.session.save(status="restore_pending", stop_guard=True,
                              message="Start cancelled after restart. Restoring the vehicle limit; charger stays Off.")

    @property
    def session(self):
        return self.service.charging

    @property
    def record(self):
        value = self.session.value.get("vehicle_limit")
        return value if isinstance(value, dict) else {}

    @property
    def busy(self):
        return self.record.get("phase") in ("setting", "restore_needed", "restoring", "attention")

    @property
    def protected(self):
        return (self.record.get("phase") == "verified" and
                timestamp(self.record.get("verified_at")) is not None and
                self.record.get("target_percent") == self.session.value.get("target_percent") and
                self.session.value.get("vin") == self.service.config.value["skoda"]["vin"] and not self.drifted)

    @property
    def drifted(self):
        car = self.service.cache.get("skoda", {})
        target = car.get("charging_settings", {}).get("target_percent")
        captured, verified = timestamp(car.get("captured_at")), timestamp(self.record.get("verified_at"))
        return (self.record.get("phase") == "verified" and type(target) is int and
                self.session.value.get("vin") == self.service.config.value["skoda"]["vin"] and
                target != self.record.get("target_percent") and captured is not None and verified is not None and
                verified < captured <= time.time()+5)

    def save(self, **changes):
        self.session.save(vehicle_limit={**self.record, **changes})

    def budget(self):
        car = self.service.car
        if car.clock() < car.blocked_until:
            return False
        try:
            return car.quota_reset_at <= car.clock() or int(car.quota_remaining) >= 8
        except (TypeError, ValueError):
            return True

    def available(self):
        return bool(self.service.cache.get("skoda", {}).get("charging_settings", {}).get("advertises_set_charging_limit"))

    def readable(self):
        target = self.service.cache.get("skoda", {}).get("charging_settings", {}).get("target_percent")
        return type(target) is int and 1 <= target <= 100

    @staticmethod
    def validate(target):
        if type(target) is not int or not 1 <= target <= 100:
            raise ValueError("Vehicle targets must be an integer from 1 to 100%. The vehicle must confirm the exact value; no automatic rounding.")

    def secure_off(self):
        if self.service.web is None:
            raise SmartfoxControlError("Local wallbox control is required.")
        charger = self.service.web.set_charger_mode(self.service.config.value["smartfox"], "OFF")
        self.service.cache.setdefault("smartfox", {})["charger"] = charger

    def write(self, target, stage="target"):
        prefix = stage+"_write_"
        self.save(**{prefix+"outcome":"unknown", prefix+"at":time.time(),
                     prefix+"http_status":None, prefix+"code":None, prefix+"problem_type":None})
        try:
            self.service.car.set_charging_limit(target, self.service.config.value["skoda"], self.service.secrets.key)
            self.save(**{prefix+"outcome":"accepted", prefix+"http_status":self.service.car.last_http_status})
        except IntegrationError as error:
            self.save(**{prefix+"outcome":"unknown" if error.uncertain else "rejected",
                         prefix+"http_status":error.http_status, prefix+"code":error.code,
                         prefix+"problem_type":error.problem_type})
            raise
        finally:
            car = self.service.car
            try:
                remaining = int(car.quota_remaining)
            except (TypeError, ValueError):
                remaining = None
            self.save(**{prefix+"quota_remaining":remaining,
                         prefix+"cooldown_seconds":max(0, math.ceil(car.blocked_until-car.clock())),
                         prefix+"quota_reset_seconds":max(0, math.ceil(car.quota_reset_at-car.clock()))})
            self.service.save_rate_state()

    def begin(self, target, request_id, allow_stale=False, retarget=False):
        self.validate(target)
        if type(allow_stale) is not bool:
            raise ValueError("Invalid older-data permission.")
        if self.session.value.get("request_id") == request_id:
            if target != self.session.value.get("target_percent"):
                raise ValueError("This request ID belongs to a different target.")
            return self.session.state()
        if self.busy or self.session.value.get("stop_requested") and self.session.active:
            raise IntegrationError("limit_pending", "Vehicle limit verification or restoration is pending.")
        if self.drifted and self.session.active:
            self.session.automatic_stop("The vehicle charging limit changed unexpectedly.", cause="vehicle_limit_lost")
            raise IntegrationError("limit_changed", "The vehicle charging limit changed. Charging is stopping.")
        if self.session.active and not retarget:
            if not self.protected or not self.session.can_retry_start(allow_stale) or target != self.session.value["target_percent"]:
                raise IntegrationError("session_active", "A manual charging session is already being monitored.")
            # Explicit Retry reuses the verified limit and the original restore value.
            return self.session.start(target, request_id, allow_stale, vehicle_limit=copy.deepcopy(self.record))
        self.service.update("skoda")
        if not self.available():
            raise IntegrationError("limit_unsupported", "The vehicle does not advertise native charging-limit control. No charging start was sent.")
        if not self.readable():
            raise IntegrationError("limit_unreadable", "The vehicle does not return its charging limit. Automatic target control is unavailable; no wallbox or vehicle setting was changed.")
        if not self.budget():
            raise IntegrationError("limit_quota", "Not enough Škoda requests remain to verify and restore the vehicle limit. Wait for the quota reset.")
        if (not self.service.can_command("start", allow_stale, prepare_limit=True) or not self.service.can_command("stop")
                or self.service.app_update_pending):
            raise IntegrationError("blocked", "Current vehicle access and start/stop permission are required.")
        car = self.service.cache["skoda"]
        if type(car.get("soc")) is not int or car["soc"] >= target:
            if retarget and type(car.get("soc")) is int:
                self.session.save(target_percent=target)
                self.session.automatic_stop("The new target is already reached according to the vehicle report.")
                return self.session.state()
            raise IntegrationError("target_reached", "The target must be above the reported battery level.")
        previous = self.record.get("restore_percent") if retarget else car.get("charging_settings", {}).get("target_percent")
        known = type(previous) is int and 1 <= previous <= 100
        prior = self.session.value
        charger = self.service.cache.get("smartfox", {}).get("charger", {})
        power, read_at = charger.get("power_kw"), timestamp(charger.get("read_at"))
        resume = not retarget or (prior.get("user_charger_mode") != "OFF" and charger.get("mode") == "M+" and
                 charger.get("available") and charger.get("telemetry_available") and read_at is not None and
                 -5 <= time.time()-read_at <= 30 and type(power) in (int,float) and math.isfinite(power) and power > .1)
        # User explicitly chose 80% on 2026-10-22 if the prior setting is absent.
        record = {"phase":"setting", "target_percent":target, "restore_percent":previous if known else 80,
                  "restore_source":self.record.get("restore_source", "reported") if retarget else "reported" if known else "user_default", "allow_stale":allow_stale,
                  "put_epoch":time.time(), "baseline_capture":car.get("captured_at"),
                  "deadline_epoch":time.time()+180, "next_probe_epoch":time.time()+15,
                  "probe_count":0, "outcome":"not_sent", "retarget":retarget, "resume_after_verification":bool(resume)}
        self.session.save(replace=True, status="starting", target_percent=target,
                          vin=self.service.config.value["skoda"]["vin"], request_id=request_id,
                          vehicle_limit=record, stop_guard=True,
                          last_start_epoch=prior.get("last_start_epoch") if retarget else None,
                          started_at=prior.get("started_at") if retarget else None,
                          message=f"Verifying vehicle limit {target} %. Charger stays Off until confirmation.")
        try:
            self.secure_off()
        except SmartfoxControlError:
            self.session.save(status="error", message="Wallbox Off is unconfirmed. No vehicle-limit write or start was sent.")
            self.save(phase="not_sent")
            raise IntegrationError("smartfox_unverified", self.session.value["message"]) from None
        # Persist the possibly uncertain write before issuing it. No blind replay.
        self.save(outcome="unknown")
        try:
            self.write(target)
            self.save(outcome="accepted")
        except IntegrationError as error:
            if not error.uncertain:
                self.save(phase="rejected", outcome="rejected")
                # A retarget rejection must not release an already charging session.
                if retarget:
                    self.session.save(status="restore_pending", stop_requested=True)
                    self.save(phase="restore_needed")
                else:
                    self.session.save(status="error", stop_guard=False,
                                      message="Vehicle limit was rejected. No charging start was sent.")
                    try:
                        self.session.restore_auto()
                    except SmartfoxControlError:
                        self.session.save(status="restore_pending", message="Vehicle limit was rejected. Return to normal wallbox mode is pending.")
                raise IntegrationError("limit_rejected", "Vehicle limit was rejected. No charging start was sent.") from None
        return self.session.state()

    def confirmed(self, target):
        car = self.service.cache.get("skoda", {})
        captured = timestamp(car.get("captured_at"))
        before = timestamp(self.record.get("baseline_capture"))
        return (car.get("available") and captured is not None and
                self.record.get("put_epoch", math.inf)-1 <= captured <= time.time()+5 and
                (before is None or captured > before) and
                car.get("charging_settings", {}).get("target_percent") == target)

    def start_restore(self):
        if not self.record or self.record.get("phase") in ("restored", "rejected", "not_sent"):
            return False
        self.session.save(status="restore_pending", stop_guard=True,
                          message="Restoring the previous vehicle limit. Charger stays Off until confirmation.")
        self.save(phase="restore_needed")
        return True

    def retry_restore(self, request_id):
        if self.service.stopping or self.service.app_update_pending:
            raise IntegrationError("blocked", "The app is restarting or updating.")
        if self.record.get("restore_request_id") == request_id:
            return self.session.state()
        if self.record.get("phase") != "attention":
            raise IntegrationError("blocked", "No failed vehicle-limit restoration is pending.")
        self.save(phase="restore_needed", restore_request_id=request_id)
        self.tick()
        return self.session.state()

    def confirm_restore_manually(self, target, request_id):
        """Explicit human proof when a vehicle omits its limit from the API.

        This never asserts API confirmation and never sends a charging start.
        The GUI asks the user to check the actual vehicle setting, not a timer.
        """
        if self.service.stopping or self.service.app_update_pending:
            raise IntegrationError("blocked", "The app is restarting or updating.")
        if self.record.get("manual_restore_request_id") == request_id:
            return self.session.state()
        if (self.record.get("phase") != "attention" or type(target) is not int
                or target != self.record.get("restore_percent")
                or self.session.value.get("vin") != self.service.config.value["skoda"]["vin"]):
            raise IntegrationError("blocked", "No matching vehicle-limit restoration requires manual confirmation.")
        try:
            self.secure_off()
        except SmartfoxControlError:
            raise IntegrationError("smartfox_unverified", "Wallbox Off is unconfirmed. Manual restoration is blocked.") from None
        charger = self.service.cache.get("smartfox", {}).get("charger", {})
        captured, power = timestamp(charger.get("read_at")), charger.get("power_kw")
        if (not charger.get("available") or charger.get("telemetry_available") is not True
                or charger.get("mode") != "OFF" or captured is None or not -5 <= time.time()-captured <= 30
                or type(power) not in (int, float) or not math.isfinite(power) or not 0 <= power <= .1
                or charger.get("status_code") not in (0, 1, 2, 5, 6, 255)):
            raise IntegrationError("blocked", "Fresh wallbox Off with no charging power is required.")
        self.save(phase="restored", restoration_confirmation="user", manual_restore_request_id=request_id)
        self.session.save(status="restore_pending", stop_guard=False)
        self.finish_restoration()
        if self.session.value.get("status") != "restore_pending":
            self.session.save(message="Normal vehicle limit manually confirmed. No new charging start was sent.")
        return self.session.state()

    def can_release_guard(self):
        # Only an aborted preparation that NEVER issued vehicle Start can be handed back.
        return bool(self.record.get("phase") in ("restore_needed", "restoring", "attention")
                    and self.record.get("cancelled") is True and not self.record.get("retarget")
                    and self.session.value.get("last_start_epoch") is None
                    and not self.session.value.get("started_at")
                    and self.session.value.get("vin") == self.service.config.value["skoda"]["vin"]
                    and not self.service.stopping and not self.service.app_update_pending)

    def release_guard(self, request_id):
        if self.record.get("release_request_id") == request_id and self.record.get("phase") == "released":
            return self.session.state()
        if not self.can_release_guard():
            raise IntegrationError("blocked", "Only an aborted preparation without a vehicle start can return to A+ this way.")
        # Reuse the strict local stopped proof. Do not interpret a stale cloud report as proof.
        try:
            self.secure_off()
        except SmartfoxControlError:
            raise IntegrationError("smartfox_unverified", "Wallbox Off is unconfirmed. Normal-mode release is blocked.") from None
        charger = self.service.cache.get("smartfox", {}).get("charger", {})
        captured, power = timestamp(charger.get("read_at")), charger.get("power_kw")
        if (charger.get("available") is not True or charger.get("telemetry_available") is not True
                or charger.get("mode") != "OFF" or captured is None or not -5 <= time.time()-captured <= 30
                or type(power) not in (int, float) or not math.isfinite(power) or not 0 <= power <= .1
                or charger.get("status_code") not in (0, 1, 2, 5, 6, 255)):
            raise IntegrationError("blocked", "Fresh wallbox Off with no charging power is required.")
        # Persist cancellation before changing hardware. If A+ fails, keep the guard and allow retry.
        self.save(resume_after_verification=False)
        try:
            result = self.service.web.set_charger_mode(self.service.config.value["smartfox"], "A+")
        except SmartfoxControlError:
            raise IntegrationError("smartfox_unverified", "Return to A+ is unconfirmed. The Off guard remains active.") from None
        self.service.cache.setdefault("smartfox", {})["charger"] = result
        self.session.save(vehicle_limit={**self.record, "phase":"released", "release_request_id":request_id, "released_at":time.time()},
                          status="closed", stop_guard=False, phase_start_pending=False, user_charger_mode=None,
                          message="Automatic target control ended. A+ is released; the vehicle limit remains unconfirmed. No vehicle start was sent.")
        return self.session.state()

    def tick(self):
        if self.record.get("phase") == "restored" and self.session.value.get("status") == "restore_pending":
            self.finish_restoration()
            return True
        if self.drifted and self.session.active and not self.session.value.get("stop_requested"):
            self.session.automatic_stop("The vehicle charging limit changed unexpectedly.", cause="vehicle_limit_lost")
            return True
        if not self.busy:
            return False
        if self.session.value.get("vin") != self.service.config.value["skoda"]["vin"]:
            self.session.save(status="error", message="Vehicle changed. No limit write or start was sent to the new vehicle.")
            return True
        try:
            self.secure_off()
        except SmartfoxControlError:
            self.session.value["message"] = "Wallbox Off is unconfirmed. Vehicle-limit control is paused."
            return True
        phase = self.record.get("phase")
        if phase == "attention":
            return True
        if phase == "setting" and self.session.value.get("stop_requested"):
            return False  # The ordinary stop path waits for newer stopped telemetry first.
        now = time.time()
        if phase == "restore_needed":
            if self.service.car.clock() < self.service.car.blocked_until:
                return True
            self.save(phase="restoring", put_epoch=now, baseline_capture=self.service.cache.get("skoda", {}).get("captured_at"),
                      deadline_epoch=now+180, next_probe_epoch=now+15, probe_count=0, outcome="unknown")
            try:
                self.write(self.record["restore_percent"], stage="restore")
                self.save(outcome="accepted")
            except IntegrationError as error:
                if not error.uncertain:
                    self.attention()
            return True
        target = self.record["target_percent"] if phase == "setting" else self.record["restore_percent"]
        if self.confirmed(target):
            if phase == "restoring":
                self.save(phase="restored", restoration_confirmation="api")
                self.finish_restoration()
            else:
                self.save(phase="verified", verified_at=self.service.cache["skoda"]["captured_at"])
                if not self.record.get("resume_after_verification", True):
                    self.session.save(status="monitoring", stop_guard=False, user_charger_mode="OFF",
                                      message="New vehicle limit confirmed. The charger remains Off; no new start was sent.")
                    return True
                record = copy.deepcopy(self.record)
                try:
                    self.session.start(target, self.session.value["request_id"], self.record.get("allow_stale", False), vehicle_limit=record)
                except IntegrationError:
                    self.session.automatic_stop("Vehicle start failed after limit verification.", cause="vehicle_limit_start")
            return True
        if now >= self.record["deadline_epoch"]:
            if phase == "setting":
                self.save(phase="restore_needed", cancelled=True, target_probe_count=self.record.get("probe_count"),
                          target_verification="unconfirmed", target_deadline_epoch=self.record.get("deadline_epoch"))
                self.session.save(status="restore_pending", message="Vehicle limit was not confirmed. No start was sent; restoring the normal limit.")
            else:
                self.attention()
            return True
        if (now >= self.record["next_probe_epoch"] and self.record["probe_count"] < 3
                and (self.service.vehicle_read_budget_available() or phase == "restoring" and
                     self.service.car.clock() >= self.service.car.blocked_until)):
            self.save(probe_count=self.record["probe_count"]+1, next_probe_epoch=now+60)
            self.service.next_due["skoda"] = 0
            # Only restoration under guarded OFF may use the command reserve.
            self.service.update("skoda", verify_restoration=phase == "restoring")
        return True

    def finish_restoration(self):
        try:
            if self.session.value.get("vehicle_stop_confirmed"):
                self.session.finish_stop("Charging ended.")
            else:
                self.session.save(status="error", stop_guard=False,
                                  message="Vehicle limit restored. The manual start was not completed.")
                self.session.restore_auto()
        except SmartfoxControlError:
            self.session.save(status="restore_pending", message="Vehicle limit restored. Return to normal wallbox mode is pending.")

    def attention(self):
        self.save(phase="attention")
        self.session.save(status="restore_pending", stop_guard=True,
                          message="Vehicle-limit restoration is unconfirmed. Charger stays Off. Retry restoration in Settings.")

    def retarget(self, target):
        self.validate(target)
        if not self.session.state()["can_adjust_target"]:
            raise IntegrationError("target_locked", "The charging target cannot change while a stop or verification is pending.")
        soc = self.service.cache.get("skoda", {}).get("soc")
        if type(soc) is int and soc >= target:
            self.session.save(target_percent=target)
            self.session.automatic_stop("The new target is already reached according to the vehicle report.")
            return self.session.state()
        if target < self.session.value["target_percent"] and not self.budget():
            self.session.save(target_percent=target)
            self.session.automatic_stop("The lower vehicle limit cannot be verified. Charging stopped as a precaution.", cause="vehicle_limit_lost")
            return self.session.state()
        return self.begin(target, "native-target-"+uuid.uuid4().hex, retarget=True)
