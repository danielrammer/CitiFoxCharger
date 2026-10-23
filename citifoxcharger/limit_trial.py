"""Supervised experiment: one Start after an unconfirmed limit write.

This is deliberately separate from percentage-controlled charging. It observes
power, never stops at the target SOC, and never calls a power drop proof of a
vehicle limit. Local fallback stops have explicit causes and invalidate that
interpretation. No limit or Start is replayed, including after a restart.
"""
import copy
import math
import time

from .modbus import utcnow
from .skoda import IntegrationError, timestamp
from .smartfox_web import SmartfoxControlError


class LimitTrial:
    RUNNING = ("waiting_ready", "observing", "stopping")

    def __init__(self, service):
        self.service = service
        self.next_off = 0
        if self.active:
            # Never resume a deferred Start after restart or process failure.
            self.save(phase="stopping", stop_cause="backend_restart", stop_at=utcnow())

    @property
    def record(self):
        value = self.service.limit_test.record.get("trial", {})
        return value if isinstance(value, dict) else {}

    @property
    def active(self):
        return self.record.get("phase") in self.RUNNING

    def save(self, **changes):
        test = copy.deepcopy(self.service.limit_test.record)
        test["trial"] = {**self.record, **changes}
        self.service.limit_test.persist(test)

    def reason(self):
        s, test = self.service, self.service.limit_test
        if self.active:
            return "A supervised vehicle-stop trial is already running."
        if s.stopping or s.app_update_pending or s.charging.active or s.vehicle_limit.busy:
            return "End charging, restoration and updates before the supervised trial."
        if not test.active or test.record.get("invalid") or test.record.get("vehicle") != test.identity():
            return "Send an explicit test limit while the charger is Off first."
        attempt = test.record["attempts"][-1] if test.record["attempts"] else {}
        if attempt.get("outcome") not in ("accepted", "unknown"):
            return "A rejected limit cannot be used for this trial."
        if self.record.get("limit_requested_at") == attempt.get("requested_at"):
            return "This limit attempt already had a trial. No automatic or repeated Start."
        requested = timestamp(attempt.get("requested_at"))
        if requested is None or not 0 <= time.time()-requested <= 600:
            return "The explicit test-limit attempt must be no older than ten minutes."
        soc = s.cache.get("skoda", {}).get("soc")
        target = attempt.get("target_percent")
        if type(soc) is not int or type(target) is not int or not 1 <= target-soc <= 5:
            return "For this short trial choose a target 1 to 5 percentage points above the reported SOC."
        if not s.can_command("start", allow_stale=True) or not s.can_command("stop"):
            return "Start/stop access and request budget are required for the supervised trial."
        return test.off_reason()

    def state(self):
        # No VIN, key, raw upstream messages or request identifiers in public data.
        result = {key:copy.deepcopy(self.record.get(key)) for key in (
            "phase", "target_percent", "initial_soc", "created_at", "start_at",
            "start_outcome", "start_http_status", "power_confirmed_at", "power_stopped_at",
            "stop_at", "stop_cause", "stop_outcome", "stop_http_status", "finished_at")}
        result.update(active=self.active, can_start=self.reason() is None, reason=self.reason())
        return result

    def start(self, request_id, target, acknowledge):
        if acknowledge is not True:
            raise ValueError("Explicit acceptance of an unconfirmed vehicle limit is required.")
        if self.record.get("request_id") == request_id:
            if target != self.record.get("target_percent"):
                raise ValueError("This request ID belongs to a different target.")
            return self.state()
        reason = self.reason()
        if reason:
            raise IntegrationError("limit_trial_blocked", reason)
        s = self.service
        attempt = s.limit_test.record["attempts"][-1]
        if type(target) is not int or target != attempt["target_percent"]:
            raise ValueError("The trial must use the exact last explicitly sent test limit.")
        try:
            s.cache.setdefault("smartfox", {})["charger"] = s.web.read(s.config.value["smartfox"])
        except SmartfoxControlError:
            raise IntegrationError("limit_trial_blocked", "SMARTFOX Off cannot be checked. No Start was sent.") from None
        if s.limit_test.off_reason():
            raise IntegrationError("limit_trial_blocked", s.limit_test.off_reason())
        # Write the intent before enabling M+. An uncertain local write is not retried.
        test = copy.deepcopy(s.limit_test.record)
        test["trial"] = {"phase":"waiting_ready", "request_id":request_id,
                         "target_percent":target, "initial_soc":s.cache["skoda"]["soc"],
                         "limit_requested_at":attempt["requested_at"], "created_at":utcnow(),
                         "ready_deadline":time.time()+360}
        s.limit_test.persist(test)
        s.power.update(True)
        try:
            s.cache["smartfox"]["charger"] = s.web.set_charger_mode(s.config.value["smartfox"], "M+")
        except SmartfoxControlError:
            self.stop("local_mode_failed")
        return self.state()

    @staticmethod
    def fresh(charger):
        read = timestamp(charger.get("read_at"))
        power = charger.get("power_kw")
        return bool(charger.get("available") and charger.get("telemetry_available") is True
                    and read is not None and -5 <= time.time()-read <= 30
                    and type(power) in (int,float) and math.isfinite(power) and power >= 0)

    def stop(self, cause="manual"):
        if not self.active:
            return self.state()
        if self.record.get("phase") != "stopping":
            self.save(phase="stopping", stop_cause=cause, stop_at=utcnow())
        self.tick_stop()
        return self.state()

    def tick_stop(self):
        s = self.service
        if time.monotonic() < self.next_off:
            return
        self.next_off = time.monotonic()+10
        try:
            charger = s.web.set_charger_mode(s.config.value["smartfox"], "OFF")
            s.cache.setdefault("smartfox", {})["charger"] = charger
        except SmartfoxControlError:
            charger = {}  # Still try the one vehicle Stop; keep retrying local Off.
        if not self.record.get("stop_outcome"):
            # Durable uncertain intent also prevents replay after a crash.
            self.save(stop_outcome="unknown")
            try:
                s.car.command("stop", s.config.value["skoda"], s.secrets.key)
                self.save(stop_outcome="accepted", stop_http_status=s.car.last_http_status)
            except IntegrationError as error:
                self.save(stop_outcome="unknown" if error.uncertain else "rejected", stop_http_status=error.http_status)
            finally:
                s.save_rate_state()
        if (self.fresh(charger) and charger.get("mode") == "OFF" and charger["power_kw"] <= .1
                and timestamp(charger["read_at"]) >= timestamp(self.record["stop_at"])):
            self.save(phase="finished", finished_at=utcnow())

    def tick(self):
        if not self.active:
            return
        s, r, now = self.service, self.record, time.time()
        if r["phase"] == "stopping":
            self.tick_stop()
            return
        if s.limit_test.record.get("vehicle") != s.limit_test.identity():
            # Do not address a newly configured car with the old trial's command.
            self.save(stop_outcome="not_sent")
            self.stop("vehicle_changed")
            return
        charger = s.cache.get("smartfox", {}).get("charger", {})
        fresh = self.fresh(charger)
        if not fresh:
            since = r.get("telemetry_missing_since")
            if since is None:
                self.save(telemetry_missing_since=now)
            elif now-since >= 30:
                self.stop("local_data_missing")
            return
        if r.get("telemetry_missing_since") is not None:
            self.save(telemetry_missing_since=None)
        if charger.get("mode") != "M+":
            self.stop("charger_mode_changed")
            return
        if r["phase"] == "waiting_ready":
            if now >= r["ready_deadline"]:
                self.stop("phase_timeout")
                return
            if charger.get("phase_switching") or not charger.get("ready_for_start"):
                if r.get("ready_since") is not None:
                    self.save(ready_since=None)
                return
            if r.get("ready_since") is None:
                self.save(ready_since=now)
                return
            if now-r["ready_since"] < 3:
                return
            if not s.can_command("start", allow_stale=True) or not s.can_command("stop"):
                self.stop("start_access_lost")
                return
            # Clear deferred intent before POST; never resend an uncertain Start.
            self.save(phase="observing", start_at=utcnow(), start_outcome="unknown", runtime_deadline=now+600)
            s.command_after = time.monotonic()+30
            try:
                s.car.command("start", s.config.value["skoda"], s.secrets.key)
                self.save(start_outcome="accepted", start_http_status=s.car.last_http_status)
            except IntegrationError as error:
                self.save(start_outcome="unknown" if error.uncertain else "rejected", start_http_status=error.http_status)
                if not error.uncertain:
                    self.stop("start_rejected")
            finally:
                s.save_rate_state()
            return
        # SOC at the requested target does NOT stop this experiment. The +2 guard
        # and ten-minute deadline are fallbacks, explicitly reported as app stops.
        car = s.cache.get("skoda", {})
        captured = timestamp(car.get("captured_at"))
        if (type(car.get("soc")) is int and captured is not None
                and timestamp(r["start_at"]) <= captured <= now+5 and car["soc"] >= r["target_percent"]+2):
            self.stop("overshoot_guard")
            return
        if now >= r["runtime_deadline"]:
            self.stop("time_guard")
            return
        power, read = charger["power_kw"], timestamp(charger["read_at"])
        if power >= .5:
            if r.get("high_since") is None:
                self.save(high_since=read, low_since=None)
            elif read-r["high_since"] >= 3 and not r.get("power_confirmed_at"):
                self.save(power_confirmed_at=charger["read_at"], low_since=None)
            elif r.get("low_since") is not None:
                self.save(low_since=None)
        elif power <= .1 and r.get("power_confirmed_at"):
            if r.get("low_since") is None:
                self.save(low_since=read, high_since=None)
            elif read-r["low_since"] >= 30:
                self.save(power_stopped_at=charger["read_at"])
                self.stop("external_power_stop")  # A power drop is not proof of target completion.
        else:
            if r.get("high_since") is not None or r.get("low_since") is not None:
                self.save(high_since=None, low_since=None)
        if not r.get("power_confirmed_at") and now-timestamp(r["start_at"]) >= 180:
            self.stop("no_charging_power")
