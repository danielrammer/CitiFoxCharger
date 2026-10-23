"""Adapter for the verified MySkoda Public API 1.1.0 contract.

Citigo support is deliberately discovered per VIN; it is not assumed from the model name.
"""
import json
import math
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

BASE = "https://public.api.connect.skoda-auto.cz"
CHARGE_MODES = ("MANUAL","TIMER","TIMER_CHARGING_WITH_CLIMATISATION","PREFERRED_CHARGING_TIMES",
                "ONLY_OWN_CURRENT","IMMEDIATE_DISCHARGING","HOME_STORAGE_CHARGING")


class IntegrationError(Exception):
    def __init__(self, code, message, retry_seconds=None, uncertain=False, http_status=None, problem_type=None):
        super().__init__(message)
        self.code, self.retry_seconds, self.uncertain = code, retry_seconds, uncertain
        self.http_status, self.problem_type = http_status, problem_type


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_request(method, path, key, payload=None):
    # Fixed trusted origin, certificate validation, no redirects and no environment proxies.
    request = urllib.request.Request(BASE + path, method=method,
                                     headers={"X-API-Key": key, "Accept": "application/json", **({"Content-Type":"application/json"} if payload is not None else {})},
                                     data=json.dumps(payload).encode("utf-8") if payload is not None else b"" if method == "POST" else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        response = opener.open(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    except (OSError, TimeoutError):
        raise IntegrationError("network", "Škoda is unreachable. The outcome of a command is unknown.",
                               uncertain=method != "GET") from None
    with response:
        body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise IntegrationError("protocol", "The Škoda response is too large.", uncertain=method != "GET")
        return response.code, dict(response.headers.items()), body


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except ValueError:
        return None


def retry_seconds(value, fallback=300):
    try:
        seconds = float(value)
        if math.isfinite(seconds):
            return max(1, seconds)
    except (TypeError, ValueError):
        pass
    try:
        return max(1, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return fallback


class SkodaAdapter:
    def __init__(self, request=None, monotonic=None):
        self.request = request or https_request
        self.clock = monotonic or time.monotonic
        self.blocked_until = 0
        self.key_expires_at = None
        self.quota_remaining = None
        self.quota_reset_at = 0
        self.last_http_status = None

    def call(self, method, path, key, payload=None, allow_reserved=False, reserved_calls=2):
        self.last_http_status = None
        if self.clock() < self.blocked_until:
            wait = math.ceil(self.blocked_until - self.clock())
            raise IntegrationError("rate_limit", f"Škoda request limit: wait {wait} seconds.", wait)
        try:
            reserve = int(self.quota_remaining) <= reserved_calls
        except (ValueError, TypeError):
            reserve = False
        if method == "GET" and reserve and not allow_reserved and self.clock() < self.quota_reset_at:
            wait = math.ceil(self.quota_reset_at - self.clock())
            raise IntegrationError("quota_reserved", "Remaining Škoda requests are reserved for charging commands.", wait)
        status, headers, body = self.request(method, path, key, payload) if payload is not None else self.request(method, path, key)
        self.last_http_status = status
        # Retain only published problem identifiers, never detail, title, VIN or URLs supplied by a server.
        problem = None
        if status >= 400:
            try:
                value = json.loads(body)
                kind = value.get("type") if isinstance(value, dict) else None
                known = ("api-key-expired", "api-key-not-authorized", "operation-not-authorized",
                         "operation-not-supported", "operation-disabled", "rate-limit-exceeded",
                         "vehicle-not-accepting-requests")
                problem = next((name for name in known if kind == BASE+"/problems/"+name), None)
            except (ValueError, UnicodeError):
                pass
        headers = {k.lower(): v for k, v in headers.items()}
        self.key_expires_at = headers.get("x-api-key-expires-at", self.key_expires_at)
        self.quota_remaining = headers.get("ratelimit-remaining")
        if "ratelimit-reset" in headers:
            self.quota_reset_at = self.clock() + retry_seconds(headers["ratelimit-reset"])
        # A successful final quota request also requires waiting before the next request.
        if self.quota_remaining == "0":
            self.blocked_until = self.clock() + retry_seconds(headers.get("ratelimit-reset"))
        if status == 429:
            wait = retry_seconds(headers.get("retry-after"))
            self.blocked_until = max(self.blocked_until, self.clock() + wait)
            raise IntegrationError("rate_limit", "Škoda requires a request cooldown.", math.ceil(wait),
                                   http_status=status, problem_type=problem)
        messages = {
            401: ("auth", "The API key is invalid or expired. Renew it in MyŠkoda."),
            403: ("forbidden", "The API key or user is not allowed to access this VIN/action."),
            404: ("not_found", "Vehicle not found through this API. Citigo access is unconfirmed."),
            422: ("unsupported", "The vehicle does not support this command or the function is disabled."),
        }
        expected = 200 if method == "GET" else 202
        if status != expected:
            code, message = messages.get(status, ("upstream", f"Škoda-Schnittstelle meldet HTTP {status}."))
            # Never return upstream error details: they can contain VINs or other sensitive values.
            raise IntegrationError(code, message, uncertain=method != "GET" and status >= 500,
                                   http_status=status, problem_type=problem)
        if method != "GET":
            return None
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeError):
            raise IntegrationError("protocol", "Invalid Škoda JSON response.") from None

    def read(self, config, key, allow_reserved=False, reserved_calls=2):
        if not key or not config["vin"]:
            return {"available": False, "code": "not_configured", "soc": None,
                    "message": "Configure the VIN and API key. Citigo compatibility is not yet verified.",
                    "operations": [], "stale": True}
        vin = config["vin"]
        data = self.call("GET", f"/api/v1/vehicles/{vin}?include=charging,operations,chargingProfiles", key,
                         allow_reserved=allow_reserved, reserved_calls=reserved_calls)
        vehicle = data.get("vehicle")
        if not isinstance(vehicle, dict) or vehicle.get("vin") != vin:
            raise IntegrationError("protocol", "The Škoda response does not belong to the configured VIN.")
        errors = data.get("errors", [])
        if not isinstance(errors, list) or any(not isinstance(e, dict) for e in errors):
            raise IntegrationError("protocol", "Invalid Škoda error list.")
        codes = [e.get("type") for e in errors if isinstance(e.get("type"), str)]
        charging = vehicle.get("charging", {})
        operations = vehicle.get("operations", [])
        if not isinstance(charging, dict) or not isinstance(operations, list):
            raise IntegrationError("protocol", "Invalid Škoda vehicle format.")
        status = charging.get("status", {})
        if not isinstance(status, dict) or not isinstance(status.get("battery", {}), dict):
            raise IntegrationError("protocol", "Invalid Škoda charging format.")
        soc = status.get("battery", {}).get("stateOfChargeInPercent")
        if soc is not None and (type(soc) is not int or not 0 <= soc <= 100):
            raise IntegrationError("protocol", "Škoda reported an invalid battery level.")
        captured = charging.get("carCapturedTimestamp")
        captured_epoch = timestamp(captured)
        age = time.time() - captured_epoch if captured_epoch is not None else None
        stale = age is None or age > config["stale_seconds"] or age < -300
        supported = [o["name"] for o in operations if isinstance(o, dict) and o.get("name") in ("startCharging", "stopCharging")]
        affected = any(c.startswith(("CHARGING_", "OPERATIONS_")) and not c.startswith("CHARGING_PROFILES_") for c in codes)
        if affected or not charging:
            supported = []
        message = "Vehicle access confirmed; capabilities discovered for this VIN."
        if not charging:
            message = "No charging data. Citigo support through this API is unconfirmed."
        elif stale:
            message = "Vehicle data are stale or have no valid vehicle timestamp. Starting is blocked."
        elif not supported:
            message = "Charging data exist, but start/stop capability is not confirmed."
        if affected:
            message = "Charging data or the command list are unavailable, unsupported or disabled."
        # Read-only diagnostics from the existing GET. Never infer support or send a mode/limit write.
        settings = charging.get("settings", {})
        settings = settings if isinstance(settings, dict) else {}
        def percent(name):
            value = settings.get(name)
            return value if type(value) is int and 1 <= value <= 100 else None
        names = {o.get("name") for o in operations if isinstance(o,dict) and isinstance(o.get("name"),str)} if not affected and charging else set()
        available_modes = settings.get("availableChargeModes")
        available_modes = [mode for mode in CHARGE_MODES if mode in available_modes] if isinstance(available_modes,list) else []
        current_limit = settings.get("maxChargeCurrentAcAmpere")
        native_settings = {
            "target_percent":percent("targetStateOfChargeInPercent"),
            "battery_care_target_percent":percent("batteryCareModeTargetValueInPercent"),
            "preferred_mode":settings.get("preferredChargeMode") if settings.get("preferredChargeMode") in CHARGE_MODES else None,
            "available_modes":available_modes,
            "battery_care_mode":settings.get("chargingCareMode") if settings.get("chargingCareMode") in ("ACTIVATED","DEACTIVATED") else None,
            "max_current_a":current_limit if type(current_limit) is int and 5 <= current_limit <= 32 else None,
            "advertises_start_charging":"startCharging" in names,
            "advertises_stop_charging":"stopCharging" in names,
            "advertises_set_charge_mode":"setChargeMode" in names,
            "advertises_set_charging_limit":"setChargingLimit" in names}
        state = status.get("state")
        from .charging_profiles import summarize
        return {"available": bool(charging) and not affected, "code": "partial" if codes else "ok",
                "message": message, "soc": soc, "state": state if isinstance(state, str) else None,
                "charging_settings":native_settings,
                "charging_profiles":summarize(vehicle.get("chargingProfiles"), codes, "updateChargingProfile" in names),
                "plug_lock":status.get("plugLockState") if status.get("plugLockState") in ("LOCKED","UNLOCKED") else None,
                "plug": status.get("plugConnectionState") if isinstance(status.get("plugConnectionState"), str) else None,
                "captured_at": captured if captured_epoch is not None else None,
                "read_at": datetime.now(timezone.utc).isoformat(), "stale": stale,
                "operations": supported, "errors": [c for c in codes if c.startswith(("CHARGING_", "OPERATIONS_"))],
                "key_expires_at": self.key_expires_at, "quota_remaining": self.quota_remaining}

    def command(self, action, config, key):
        if action not in ("start", "stop"):
            raise ValueError("Unknown vehicle command.")
        self.call("POST", f"/api/v1/vehicles/{config['vin']}/charging/{action}", key)

    def set_charging_limit(self, target, config, key):
        if type(target) is not int or not 1 <= target <= 100:
            raise ValueError("Invalid vehicle charging limit.")
        self.call("PUT", f"/api/v1/vehicles/{config['vin']}/charging/limit", key,
                  {"targetStateOfChargeInPercent":target})
