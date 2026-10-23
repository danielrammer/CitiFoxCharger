"""Explicit, isolated native-limit probe. Never controls the charger or starts a car.

One user request sends one PUT. Existing scheduled vehicle reads may observe its
result, but this module never polls or retries. Ending isolation leaves user Off
selected; normal operation needs a separate, deliberate mode selection.
"""
import copy
import hashlib
import json
import math
import time

from .config import atomic_write
from .modbus import utcnow
from .skoda import IntegrationError, timestamp
from .smartfox_web import SmartfoxControlError


class LimitTest:
    def __init__(self, service):
        self.service = service
        self.path = service.config.directory / "vehicle-limit-test.json"
        self.record = {"version":1,"active":False,"attempts":[]}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if (data.get("version") != 1 or type(data.get("active")) is not bool
                        or not isinstance(data.get("attempts"),list) or len(data["attempts"]) > 10
                        or any(not isinstance(item,dict) for item in data["attempts"])):
                    raise ValueError()
                self.record = data
            except (OSError, ValueError, TypeError, AttributeError):
                self.record.update(active=True,invalid=True)
            if self.active and not service.charging.active:
                service.charging.save(user_charger_mode="OFF")  # No hardware write.

    @property
    def active(self):
        return self.record["active"]

    def identity(self):
        return hashlib.sha256(self.service.config.value["skoda"]["vin"].encode()).hexdigest()

    def persist(self, record):
        atomic_write(self.path, json.dumps(record,indent=2).encode())
        self.record = record

    def access_reason(self, restoring=False):
        s = self.service
        if getattr(s, "limit_trial", None) and s.limit_trial.active:
            return "Stop the supervised vehicle-stop trial before changing its limit."
        if s.stopping or s.app_update_pending:
            return "The app is restarting or updating."
        if s.charging.active or s.vehicle_limit.busy:
            return "End the current charging session and restoration before testing a vehicle limit."
        if self.record.get("invalid"):
            return "The saved limit test is invalid. End the test before starting a new one."
        if self.active and self.record.get("vehicle") != self.identity():
            return "The limit test belongs to another vehicle. End it without sending another command."
        if not s.secrets.key or not s.config.value["skoda"]["vin"]:
            return "Configure vehicle access before testing a limit."
        expiry = timestamp(s.car.key_expires_at)
        if expiry is not None and expiry <= time.time():
            return "Renew the API key before testing a limit."
        # Restoring 80% remains possible after a rejected/uncertain test invalidates reads.
        car = s.cache.get("skoda",{})
        if not restoring and (not car.get("available") or not car.get("charging_settings",{}).get("advertises_set_charging_limit")):
            return "Vehicle access and advertised charging-limit support are required for this test."
        if s.car.clock() < s.car.blocked_until:
            return "Škoda still requires a request cooldown."
        try:
            if s.car.quota_reset_at > s.car.clock() and int(s.car.quota_remaining) < (1 if restoring else 4):
                return "Not enough Škoda requests remain. Wait for the quota reset."
        except (TypeError,ValueError):
            pass
        if s.web is None:
            return "Local SMARTFOX access is required for the test."
        last = self.record["attempts"][-1] if self.record["attempts"] else {}
        requested = timestamp(last.get("requested_at"))
        if requested is not None and time.time()-requested < 30 and (not restoring or last.get("target_percent") == 80):
            return "Wait 30 seconds before another vehicle-limit test. No automatic retry is performed."
        return None

    def off_reason(self):
        charger = self.service.cache.get("smartfox",{}).get("charger",{})
        read_at, power = timestamp(charger.get("read_at")), charger.get("power_kw")
        if not (charger.get("available") and charger.get("telemetry_available") is True
                and charger.get("mode") == "OFF" and read_at is not None
                and -5 <= time.time()-read_at <= 30
                and type(power) in (int,float) and math.isfinite(power) and 0 <= power <= .1):
            return "In CitiFoxCharger select Charger → Off, then wait for Off and 0 kW before testing."
        return None

    def state(self):
        reason = self.access_reason() or self.off_reason()
        restore_reason = (self.access_reason(restoring=True) or self.off_reason()) if self.active else "No limit test is active."
        # The public view is allowlisted; local identifiers never leave the data file.
        attempts = []
        for item in self.record["attempts"]:
            attempts.append({key:copy.deepcopy(item.get(key)) for key in (
                "target_percent","requested_at","outcome","http_status","code","problem_type",
                "verification","verified_at","reported_limit","quota_remaining","cooldown_seconds")})
        return {"active":self.active,"can_set":reason is None,"reason":reason,
                "can_restore":self.active and restore_reason is None,"restore_reason":restore_reason,
                "attempts":attempts}

    def send(self, target, request_id):
        if type(target) is not int or not 1 <= target <= 100:
            raise ValueError("Choose an explicit integer test limit from 1 to 100%. The vehicle may reject it. No rounding.")
        previous = next((x for x in self.record["attempts"] if x.get("request_id") == request_id), None)
        if previous:
            if previous.get("target_percent") != target:
                raise ValueError("This request ID belongs to a different target.")
            return self.state()  # Persisted uncertain outcomes must never be replayed.
        restoring = self.active and target == 80
        reason = self.access_reason(restoring)
        if reason:
            raise IntegrationError("limit_test_blocked",reason)
        s = self.service
        try:
            s.cache.setdefault("smartfox",{})["charger"] = s.web.read(s.config.value["smartfox"])
        except SmartfoxControlError:
            raise IntegrationError("limit_test_blocked","SMARTFOX Off cannot be checked. No limit command was sent.") from None
        reason = self.off_reason()
        if reason:
            raise IntegrationError("limit_test_blocked",reason)
        attempt = {"request_id":request_id,"target_percent":target,"requested_at":utcnow(),
                   "baseline_capture":s.cache.get("skoda",{}).get("captured_at"),
                   "outcome":"unknown","http_status":None,"code":None,"problem_type":None,
                   "verification":"not_returned" if s.cache.get("skoda",{}).get("charging_settings",{}).get("target_percent") is None else "awaiting_new_capture"}
        # Pin explicit user Off BEFORE arming isolation. Neither operation sends CGI.
        s.charging.save(user_charger_mode="OFF")
        record = {"version":1,"active":True,"vehicle":self.identity(),
                  "attempts":(self.record["attempts"]+[attempt])[-10:]}
        if self.record.get("vehicle") == self.identity() and isinstance(self.record.get("trial"),dict):
            record["trial"] = copy.deepcopy(self.record["trial"])  # Keep evidence when requesting normal 80%.
        self.persist(record)  # Must succeed before a possibly uncertain PUT.
        try:
            s.car.set_charging_limit(target,s.config.value["skoda"],s.secrets.key)
            attempt.update(outcome="accepted",http_status=s.car.last_http_status)
        except IntegrationError as error:
            attempt.update(outcome="unknown" if error.uncertain else "rejected",http_status=error.http_status,
                           code=error.code,problem_type=error.problem_type)
        finally:
            s.save_rate_state()
        try:
            attempt["quota_remaining"] = int(s.car.quota_remaining)
        except (TypeError,ValueError):
            attempt["quota_remaining"] = None
        attempt["cooldown_seconds"] = max(0,math.ceil(s.car.blocked_until-s.car.clock()))
        self.persist(record)
        return self.state()

    def observe(self, car):
        """Use existing successful GETs only. No request or wait is initiated here."""
        if (not self.active or not self.record["attempts"] or self.record.get("vehicle") != self.identity()
                or not car.get("available")):
            return
        record = copy.deepcopy(self.record)
        item = record["attempts"][-1]
        if item.get("outcome") not in ("accepted","unknown") or item.get("verification") == "confirmed":
            return
        capture, read_at = timestamp(car.get("captured_at")), timestamp(car.get("read_at"))
        requested, baseline = timestamp(item.get("requested_at")), timestamp(item.get("baseline_capture"))
        if requested is None or read_at is None or read_at < requested:
            return
        limit = car.get("charging_settings",{}).get("target_percent")
        if type(limit) is not int:
            item["verification"] = "not_returned"
        elif capture is not None and requested <= capture <= time.time()+5 and (baseline is None or capture > baseline):
            item.update(reported_limit=limit,verification="confirmed" if limit == item["target_percent"] else "different")
            if limit == item["target_percent"]:
                item["verified_at"] = car["captured_at"]
        if record != self.record:
            self.persist(record)

    def end(self, acknowledge):
        if getattr(self.service, "limit_trial", None) and self.service.limit_trial.active:
            raise IntegrationError("limit_trial_active", "Stop the supervised vehicle-stop trial before ending isolation.")
        if acknowledge is not True:
            raise ValueError("Explicit acknowledgement of an unconfirmed vehicle limit is required.")
        record = {"version":1,"active":False,"attempts":[]} if self.record.get("invalid") else {**self.record,"active":False}
        self.persist(record)
        # No A+, M+, Stop, Start, restore PUT or hidden retry on exit.
        return self.state()
