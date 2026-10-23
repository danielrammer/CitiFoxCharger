"""Serialized native-session I/O. The machine is the only lifecycle authority.

Service holds its existing lock around public methods. Each intent is written
before I/O; a process restart reconciles it instead of replaying a command.
The legacy-shaped value property is an HTTP compatibility view, not storage.
"""
import copy
import json
import math
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone

from ..config import atomic_write
from ..skoda import IntegrationError, timestamp
from ..smartfox_web import SmartfoxControlError
from .machine import Session, State, Step, Facts, begin, transition, stopped, retry_allowed


MESSAGES = {
    "ready":"Vehicle-controlled charging: the exact limit must be confirmed before Start.",
    "preparing_limit":"Preparing the vehicle charging limit. No vehicle Start has been sent.",
    "waiting_for_local_stop":"Waiting for SMARTFOX Off with zero charging power before changing the vehicle limit.",
    "local_stop_power_unconfirmed":"SMARTFOX has not confirmed zero charging power. Vehicle setting changes are paused.",
    "limit_confirmed":"Vehicle limit confirmed. Preparing SMARTFOX M+.",
    "waiting_for_charger":"Waiting for SMARTFOX charging permission.",
    "phase_switching":"Phase switching in progress. Vehicle Start waits for confirmed readiness.",
    "submitting_start":"Submitting the vehicle Start with a confirmed charging limit.",
    "waiting_for_power":"Vehicle Start submitted. Waiting for measured charging power.",
    "charging_confirmed":"Charging confirmed by SMARTFOX. The vehicle controls the verified target.",
    "checking_charging_power":"Checking measured charging power after restart. No repeated Start.",
    "user_stop":"Stopping locally first, then confirming the vehicle Stop.",
    "target_reported":"Vehicle target reported reached. Confirming Stop and restoring the normal limit.",
    "stop_confirmed":"Vehicle Stop confirmed. Restoring the normal charging limit.",
    "restoring_limit":"Restoring the normal vehicle charging limit. Waiting for exact readback.",
    "limit_restored":"Normal vehicle charging limit confirmed. Returning to normal SMARTFOX mode.",
    "session_ended":"Charging session ended. Normal operation is released.",
    "limit_rejected":"The vehicle rejected the exact target. No rounded target or vehicle Start was sent.",
    "limit_unconfirmed":"The vehicle limit was not confirmed. Start cancelled; restoring the normal limit.",
    "start_rejected":"The vehicle rejected Start. Stopping and restoring normal settings.",
    "start_power_timeout":"No charging power was confirmed after Start. Stopping; no automatic Start retry.",
    "charger_ready_timeout":"SMARTFOX readiness was not confirmed in time. Cancelling the start.",
    "start_access_unavailable":"Vehicle Start access or request budget is unavailable. Cancelling the start.",
    "manual_mode_unconfirmed":"SMARTFOX M+ was not confirmed. Cancelling the start.",
    "local_off_unconfirmed":"Local Off is unconfirmed. Check SMARTFOX; no further automatic command is sent.",
    "vehicle_stop_rejected":"Vehicle Stop was rejected. Local Off is retained; retry cleanup explicitly.",
    "vehicle_stop_unconfirmed":"Vehicle Stop is unconfirmed. Local Off is retained; retry cleanup explicitly.",
    "restore_rejected":"Restoring the normal vehicle limit was rejected. Local Off is retained.",
    "restore_unconfirmed":"The normal vehicle limit is unconfirmed. Check it in the vehicle before releasing normal operation.",
    "normal_mode_unconfirmed":"Return to normal SMARTFOX mode is unconfirmed. Retry cleanup explicitly.",
    "power_stopped_target_unconfirmed":"Charging power stopped. The target is not confirmed reached. Check vehicle status or stop the session.",
    "local_data_unavailable":"SMARTFOX data are unavailable. Charging state is unknown; the confirmed vehicle limit remains the target.",
    "vehicle_limit_changed":"The confirmed vehicle limit changed. Stopping and restoring normal settings.",
    "checking_previous_session":"Checking the saved session. No automatic Start replay.",
    "recovery_needs_local_data":"Current SMARTFOX data are required to recover the previous session.",
    "cancelled_after_restart":"The pending operation was cancelled after restart. Checking Stop and restoring settings.",
    "retrying_cleanup":"Retrying Stop and restoration by explicit request.",
    "restore_confirmed_by_user":"Normal vehicle limit confirmed by the user. No new Start will be sent.",
    "internal_error":"Charging control needs attention. Check diagnostics and local storage.",
    "vehicle_changed":"The configured vehicle changed. No vehicle command will be sent.",
}


def native_start_reason(service):
    """Preflight is read-only. An unreadable limit cannot strand SMARTFOX Off."""
    car = service.cache.get("skoda", {})
    settings = car.get("charging_settings", {})
    charger = service.cache.get("smartfox", {}).get("charger", {})
    limit = settings.get("target_percent")
    if service.stopping or service.app_update_pending:
        return "The app is restarting or updating."
    if not settings.get("advertises_set_charging_limit"):
        return "The vehicle does not advertise native charging-limit control. No device was changed."
    if type(limit) is not int or not 1 <= limit <= 100:
        return "The vehicle does not return its charging limit. Native target control cannot be verified. No device was changed."
    if not (service.can_command("start", True, prepare_limit=True) and service.can_command("stop")):
        return "Vehicle access, a known battery level and Start/Stop permission are required."
    try:
        if service.car.quota_reset_at > service.car.clock() and int(service.car.quota_remaining) < 10:
            return "At least ten available Škoda requests are required for limit verification, Start and cleanup."
    except (TypeError, ValueError):
        pass
    captured = timestamp(charger.get("read_at"))
    power = charger.get("power_kw")
    if not (service.web and charger.get("available") and charger.get("control_available")
            and charger.get("telemetry_available") is True and captured is not None
            and -5 <= time.time()-captured <= 30
            and type(power) in (int,float) and math.isfinite(power) and power >= 0):
        return "Fresh SMARTFOX telemetry is required before preparation."
    return None


class NativeCharging:
    native_fsm = True

    def __init__(self, service, saved=None, clock=None):
        self.service, self.clock = service, clock or time.time
        self.path = service.config.directory / "charging-session.json"
        self.session = Session()
        self.history = []
        if saved is not None:
            data = saved["session"]
            self.session = Session(**{**data, "state":State(data["state"]), "step":Step(data["step"])})
            self._validate_saved()
            history = saved.get("transitions")
            for item in history[-60:] if isinstance(history,list) else []:
                if not isinstance(item,dict) or timestamp(item.get("at")) is None:
                    continue
                choices = {"from":tuple(s.value for s in State), "to":tuple(s.value for s in State),
                    "step":tuple(s.value for s in Step), "reason":tuple(MESSAGES),
                    "event":("start_requested","tick","restart","stop","accepted","unknown","rejected","confirmed","retry_cleanup","manual_restore","retry_start","retarget"),
                    "effect":(None,"off","set_limit","manual","start","stop","restore","normal","read_vehicle")}
                if all(key in item and item[key] in allowed for key,allowed in choices.items()):
                    self.history.append({"at":item["at"], **{key:item[key] for key in choices}})
            self.dispatch("restart")

    def _validate_saved(self):
        s = self.session
        for name, value in asdict(s).items():
            default = getattr(Session(), name)
            if type(default) in (int,float):
                if type(value) not in (int,float) or not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid native charging session.")
            elif isinstance(default, str) and not isinstance(value, str):
                raise ValueError("Invalid native charging session.")
        if type(s.target) is not int or type(s.restore) is not int or not 1 <= s.target <= 100 or not 1 <= s.restore <= 100:
            raise ValueError("Invalid native target.")
        if s.state in (State.STARTING, State.CHARGING) and not s.verified:
            raise ValueError("Missing native charging-limit proof.")
        if s.reason not in MESSAGES or s.result_reason and s.result_reason not in MESSAGES:
            raise ValueError("Invalid saved reason.")
        if s.verified and s.limit_outcome != "confirmed":
            raise ValueError("Invalid saved limit proof.")
        if s.normal_mode not in ("OFF","A+"):
            raise ValueError("Invalid saved normal mode.")
        legal_steps = {
            State.IDLE:{Step.NONE}, State.PREPARING:{Step.OFF,Step.SET_LIMIT,Step.WAIT_LIMIT,Step.MANUAL,Step.WAIT_READY},
            State.STARTING:{Step.START,Step.WAIT_POWER}, State.CHARGING:{Step.NONE},
            State.STOPPING:{Step.OFF,Step.STOP,Step.WAIT_STOP,Step.RESTORE,Step.WAIT_RESTORE,Step.AUTO},
            State.RECOVERING:set(Step), State.ATTENTION:set(Step)}
        if s.step not in legal_steps[s.state] or type(s.probes) is not int or not 0 <= s.probes <= 3:
            raise ValueError("Invalid saved charging step.")

    @property
    def active(self):
        return self.session.state != State.IDLE

    @property
    def protected(self):
        s = self.session
        return bool(s.verified and s.state in (State.PREPARING, State.STARTING, State.CHARGING, State.ATTENTION)
                    and s.vin == self.service.config.value["skoda"]["vin"])

    @property
    def busy(self):
        return self.session.state in (State.PREPARING, State.STOPPING, State.RECOVERING)

    @property
    def record(self):
        s = self.session
        phase = "restored" if s.restore_outcome in ("confirmed","user_confirmed") else (
            "rejected" if s.state == State.IDLE and s.limit_outcome == "rejected" else
            "not_sent" if s.state == State.IDLE else
            "attention" if s.state == State.ATTENTION else
            "restoring" if s.state == State.STOPPING else "verified" if s.verified else "setting")
        return {"phase":phase, "target_percent":s.target, "restore_percent":s.restore,
                "verified_at":self._date(s.verified), "target_write_outcome":s.limit_outcome,
                "restore_write_outcome":s.restore_outcome, "probe_count":s.probes,
                "restoration_confirmation":"user" if s.restore_outcome == "user_confirmed" else "api" if s.restore_outcome == "confirmed" else None}

    @staticmethod
    def _date(epoch):
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch else None

    @property
    def value(self):
        """Compatibility projection. No derived lifecycle flags are saved to disk."""
        s = self.session
        status = {State.IDLE:"complete" if s.result else "idle", State.PREPARING:"starting",
                  State.STARTING:"starting", State.CHARGING:"monitoring", State.STOPPING:"stop_pending",
                  State.RECOVERING:"interrupted", State.ATTENTION:"paused"}[s.state]
        holding = s.state in (State.PREPARING, State.STOPPING) or s.state == State.ATTENTION and s.step not in (Step.NONE,Step.AUTO)
        return {"status":status, "machine_state":s.state.value, "machine_step":s.step.value,
                "reason_code":s.reason, "target_percent":s.target, "vin":s.vin,
                "request_id":s.request_id, "message":None if s.state == State.IDLE and not s.result else MESSAGES[s.result_reason] if s.state == State.IDLE and s.result == "failed" else MESSAGES[s.reason], "result":s.result,
                "result_reason":s.result_reason,
                "vehicle_limit":self.record, "last_start_epoch":s.start_epoch or None,
                "started_at":self._date(s.start_epoch), "stop_requested":s.state == State.STOPPING or s.state == State.ATTENTION and holding,
                "stop_guard":holding, "phase_start_pending":s.step == Step.WAIT_READY,
                "vehicle_stop_confirmed":s.stop_outcome == "confirmed",
                "auto_restored":s.state == State.IDLE, "stop_intent_epoch":s.stop_epoch or None,
                "user_charger_mode":"OFF" if s.normal_mode == "OFF" else None,
                "start_confirmed_at":self._date(s.entered) if s.state == State.CHARGING else None,
                "start_confirmation_source":"smartfox_power" if s.state == State.CHARGING else None}

    def state(self):
        value = self.value
        retry = self.can_retry_start()
        reason = native_start_reason(self.service) if not self.active else None if retry else "End the current native session before starting another."
        allowed = retry or not self.active and reason is None
        return {**value, "target_control":"vehicle", "active":self.active,
                "can_start_manual":allowed, "can_start_manual_with_stale":allowed,
                "can_force":allowed, "can_force_native":allowed, "can_force_native_with_stale":allowed,
                "can_adjust_target":self.session.state == State.CHARGING,
                "can_stop":bool(self.service.web and not self.service.stopping),
                "can_shutdown":not self.active, "can_retry_start":retry,
                "can_confirm_restored_limit":self.session.state == State.ATTENTION and self.session.step == Step.WAIT_RESTORE,
                "can_release_native_guard":False, "manual_start_reason":reason, "force_reason":reason,
                "native_fsm":True, "transition_log":copy.deepcopy(self.history)}

    def persist(self):
        atomic_write(self.path, json.dumps({"schema_version":2, "controller":"native",
                     "session":asdict(self.session), "transitions":self.history}, indent=2).encode())

    def save(self, **changes):
        # Existing lifecycle hooks may persist or report an internal failure only.
        if changes:
            self.session = replace(self.session, state=State.ATTENTION, step=Step.NONE, reason="internal_error")
        self.persist()

    def facts(self):
        car = self.service.cache.get("skoda", {})
        charger = self.service.cache.get("smartfox", {}).get("charger", {})
        captured = timestamp(charger.get("read_at")) or 0
        power = charger.get("power_kw")
        power = power if type(power) in (int,float) and math.isfinite(power) and power >= 0 else None
        vehicle_capture = timestamp(car.get("captured_at")) or 0
        soc, limit = car.get("soc"), car.get("charging_settings", {}).get("target_percent")
        return Facts(local_fresh=bool(charger.get("available") and charger.get("telemetry_available") is True
                        and -5 <= self.clock()-captured <= 30), mode=charger.get("mode", ""),
                     power=power, local_capture=captured, switching=charger.get("phase_switching") is True,
                     ready=charger.get("ready_for_start") is True, vehicle_available=bool(car.get("available")
                        and 0 < vehicle_capture <= self.clock()+5), vehicle_capture=vehicle_capture,
                     vehicle_state=car.get("state", ""), soc=soc if type(soc) is int and 0 <= soc <= 100 else None,
                     limit=limit if type(limit) is int and 1 <= limit <= 100 else None,
                     start_allowed=self.service.can_command("start", True))

    def dispatch(self, event, **payload):
        before = self.session
        decision = transition(before, event, self.facts(), self.clock(), **payload)
        if decision.session != before or decision.effect:
            self.session = decision.session
            self.history.append({"at":self._date(self.clock()), "from":before.state.value,
                                 "event":event, "to":self.session.state.value,
                                 "step":self.session.step.value, "reason":self.session.reason,
                                 "effect":decision.effect})
            self.history = self.history[-60:]
            self.persist()  # Must succeed before any effect is sent.
        if decision.effect:
            self.execute(decision.effect)

    def execute(self, effect):
        s, service = self.session, self.service
        if s.vin and s.vin != service.config.value["skoda"]["vin"]:
            self.session = replace(s, state=State.ATTENTION, step=Step.NONE, reason="vehicle_changed")
            self.persist()
            return
        if effect == "read_vehicle":
            service.next_due["skoda"] = 0
            service.update("skoda", verify_restoration=s.state == State.STOPPING)
            return
        try:
            if effect in ("off", "manual", "normal"):
                mode = {"off":"OFF", "manual":"M+", "normal":s.normal_mode}[effect]
                charger = service.web.set_charger_mode(service.config.value["smartfox"], mode)
                service.cache.setdefault("smartfox", {})["charger"] = charger
                f = self.facts()
                if not f.local_fresh or f.mode != mode:
                    raise SmartfoxControlError("Local mode was not confirmed.")
                outcome = "confirmed"
            elif effect in ("set_limit", "restore"):
                service.car.set_charging_limit(s.target if effect == "set_limit" else s.restore,
                                               service.config.value["skoda"], service.secrets.key)
                service.save_rate_state()
                outcome = "accepted"
            else:
                result = service.command(effect, "native-"+s.request_id+"-"+effect+"-"+str(int(s.entered)), allow_stale=True)
                outcome = result["status"]
            self.dispatch(outcome)
        except IntegrationError as error:
            service.save_rate_state()
            self.dispatch("unknown" if error.uncertain else "rejected")
        except SmartfoxControlError:
            self.dispatch("unknown")

    def start(self, target, request_id, allow_stale=True):
        if type(target) is not int or not 1 <= target <= 100:
            raise ValueError("Invalid vehicle target.")
        if type(allow_stale) is not bool:
            raise ValueError("Invalid older-data permission.")
        if self.service.stopping or self.service.app_update_pending:
            raise IntegrationError("blocked", "The app is restarting or updating.")
        if self.session.request_id == request_id:
            if target != self.session.target:
                raise ValueError("This request ID belongs to a different target.")
            return self.state()
        if self.active:
            if target == self.session.target and self.can_retry_start():
                self.dispatch("retry_start", request_id=request_id)
                return self.state()
            raise IntegrationError("session_active", "End the current session before starting another.")
        reason = native_start_reason(self.service)
        if reason:
            raise IntegrationError("native_unavailable", reason)
        soc = self.service.cache["skoda"].get("soc")
        if type(soc) is not int or target <= soc:
            raise IntegrationError("target_reached", "The target must exceed the last reported battery level.")
        settings = self.service.cache["skoda"]["charging_settings"]
        care = settings.get("battery_care_target_percent")
        if settings.get("battery_care_mode") == "ACTIVATED" and type(care) is int and target > care:
            raise IntegrationError("battery_care_limit", "The requested target exceeds the active battery-care limit. Check the vehicle setting first.")
        self.session = begin(target, self.service.cache["skoda"]["charging_settings"]["target_percent"],
                             request_id, self.service.config.value["skoda"]["vin"], self.clock())
        if self.facts().power and self.facts().power > .1:
            self.session = replace(self.session, start_outcome="external")
        self.history.append({"at":self._date(self.clock()),"from":"idle","event":"start_requested",
            "to":"preparing","step":"off","reason":"preparing_limit","effect":None})
        self.history=self.history[-60:]
        self.persist()
        self.tick()
        return self.state()

    def tick(self):
        if self.session.vin and self.session.vin != self.service.config.value["skoda"]["vin"]:
            if self.session.reason != "vehicle_changed":
                self.session = replace(self.session, state=State.ATTENTION, step=Step.NONE, reason="vehicle_changed")
                self.persist()
            return
        self.dispatch("tick")

    def can_retry_start(self):
        return retry_allowed(self.session, self.facts())

    def stop(self, request_id):
        if self.session.stop_request_id == request_id:
            return self.state()
        if not self.active:
            self.session = Session(vin=self.service.config.value["skoda"]["vin"], request_id=request_id,
                                   normal_mode=self.session.normal_mode, start_outcome="external")
        self.session = replace(self.session, stop_request_id=request_id)
        self.dispatch("stop")
        self.tick()
        return self.state()

    def change_target(self, target):
        if type(target) is not int or not 1 <= target <= 100:
            raise ValueError("Invalid vehicle target.")
        if self.session.state != State.CHARGING:
            raise IntegrationError("target_locked", "Change the target after charging is confirmed.")
        settings = self.service.cache.get("skoda", {}).get("charging_settings", {})
        care = settings.get("battery_care_target_percent")
        if settings.get("battery_care_mode") == "ACTIVATED" and type(care) is int and target > care:
            raise IntegrationError("battery_care_limit", "The requested target exceeds the active battery-care limit. Check the vehicle setting first.")
        soc = self.service.cache.get("skoda", {}).get("soc")
        if type(soc) is int and target <= soc:
            self.dispatch("retarget", target=target)
            self.tick()
            return self.state()
        # Reuse the original restore value. No global/profile setting is forgotten.
        if not self.service.can_command("start", True):
            raise IntegrationError("blocked", "Vehicle access is required to change the target.")
        try:
            if self.service.car.quota_reset_at > self.service.car.clock() and int(self.service.car.quota_remaining) < 10:
                raise IntegrationError("limit_quota", "At least ten available Škoda requests are required for limit verification, Start and cleanup.")
        except (TypeError, ValueError):
            pass
        self.dispatch("retarget", target=target)
        self.tick()
        return self.state()

    def retry_restore(self, request_id):
        if self.service.stopping or self.service.app_update_pending:
            raise IntegrationError("blocked", "The app is restarting or updating.")
        if self.session.cleanup_request_id == request_id:
            return self.state()
        if self.session.state != State.ATTENTION:
            raise IntegrationError("blocked", "No cleanup requires attention.")
        self.session = replace(self.session, cleanup_request_id=request_id)
        self.dispatch("retry_cleanup")
        self.tick()
        return self.state()

    def confirm_restore_manually(self, target, request_id):
        if self.service.stopping or self.service.app_update_pending:
            raise IntegrationError("blocked", "The app is restarting or updating.")
        if self.session.manual_restore_request_id == request_id:
            return self.state()
        if (target != self.session.restore or self.session.step != Step.WAIT_RESTORE
                or self.session.state != State.ATTENTION):
            raise IntegrationError("blocked", "No matching restoration needs manual confirmation.")
        if not stopped(self.facts()):
            raise IntegrationError("blocked", "Fresh local Off and zero power are required.")
        self.session = replace(self.session, manual_restore_request_id=request_id)
        self.dispatch("manual_restore")
        self.tick()
        return self.state()

    def can_release_guard(self):
        return False

    def stop_if_target_reached(self):
        pass  # The native machine evaluates target evidence exactly once per tick.

    def local_stop_allows_shutdown(self):
        return not self.active  # Do not discard an unresolved vehicle-setting restoration.
