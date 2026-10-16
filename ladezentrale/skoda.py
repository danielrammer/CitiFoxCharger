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


class IntegrationError(Exception):
    def __init__(self, code, message, retry_seconds=None, uncertain=False):
        super().__init__(message)
        self.code, self.retry_seconds, self.uncertain = code, retry_seconds, uncertain


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_request(method, path, key):
    # Fixed trusted origin, certificate validation, no redirects and no environment proxies.
    request = urllib.request.Request(BASE + path, method=method,
                                     headers={"X-API-Key": key, "Accept": "application/json"},
                                     data=b"" if method == "POST" else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        response = opener.open(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    except (OSError, TimeoutError):
        raise IntegrationError("network", "Škoda ist nicht erreichbar. Bei einem Befehl ist der Ausgang unbekannt.",
                               uncertain=method == "POST") from None
    with response:
        body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise IntegrationError("protocol", "Škoda-Antwort ist zu groß.", uncertain=method == "POST")
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

    def call(self, method, path, key):
        if self.clock() < self.blocked_until:
            wait = math.ceil(self.blocked_until - self.clock())
            raise IntegrationError("rate_limit", f"Škoda-Abfragelimit: noch {wait} Sekunden warten.", wait)
        try:
            reserve = int(self.quota_remaining) <= 2
        except (ValueError, TypeError):
            reserve = False
        if method == "GET" and reserve and self.clock() < self.quota_reset_at:
            wait = math.ceil(self.quota_reset_at - self.clock())
            raise IntegrationError("quota_reserved", "Die verbleibenden Škoda-Anfragen sind für Ladebefehle reserviert.", wait)
        status, headers, body = self.request(method, path, key)
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
            raise IntegrationError("rate_limit", "Škoda verlangt eine Abfragepause.", math.ceil(wait))
        messages = {
            401: ("auth", "API-Schlüssel ungültig oder abgelaufen. In MyŠkoda erneuern."),
            403: ("forbidden", "API-Schlüssel oder Benutzer darf diese FIN/Aktion nicht verwenden."),
            404: ("not_found", "Fahrzeug über diese API nicht gefunden. Citigo-Zugriff nicht bestätigt."),
            422: ("unsupported", "Fahrzeug unterstützt diesen Befehl nicht oder die Funktion ist deaktiviert."),
        }
        expected = 200 if method == "GET" else 202
        if status != expected:
            code, message = messages.get(status, ("upstream", f"Škoda-Schnittstelle meldet HTTP {status}."))
            # Never return upstream error details: they can contain VINs or other sensitive values.
            raise IntegrationError(code, message, uncertain=method == "POST" and status >= 500)
        if method == "POST":
            return None
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeError):
            raise IntegrationError("protocol", "Ungültige Škoda-JSON-Antwort.") from None

    def read(self, config, key):
        if not key or not config["vin"]:
            return {"available": False, "code": "not_configured", "soc": None,
                    "message": "FIN und API-Schlüssel einrichten. Citigo-Kompatibilität noch ungeprüft.",
                    "operations": [], "stale": True}
        vin = config["vin"]
        data = self.call("GET", f"/api/v1/vehicles/{vin}?include=charging,operations", key)
        vehicle = data.get("vehicle")
        if not isinstance(vehicle, dict) or vehicle.get("vin") != vin:
            raise IntegrationError("protocol", "Škoda-Antwort gehört nicht zur konfigurierten FIN.")
        errors = data.get("errors", [])
        if not isinstance(errors, list) or any(not isinstance(e, dict) for e in errors):
            raise IntegrationError("protocol", "Ungültige Škoda-Fehlerliste.")
        codes = [e.get("type") for e in errors if isinstance(e.get("type"), str)]
        charging = vehicle.get("charging", {})
        operations = vehicle.get("operations", [])
        if not isinstance(charging, dict) or not isinstance(operations, list):
            raise IntegrationError("protocol", "Ungültiges Škoda-Fahrzeugformat.")
        status = charging.get("status", {})
        if not isinstance(status, dict) or not isinstance(status.get("battery", {}), dict):
            raise IntegrationError("protocol", "Ungültiges Škoda-Ladeformat.")
        soc = status.get("battery", {}).get("stateOfChargeInPercent")
        if soc is not None and (type(soc) is not int or not 0 <= soc <= 100):
            raise IntegrationError("protocol", "Škoda meldet einen ungültigen Akkustand.")
        captured = charging.get("carCapturedTimestamp")
        captured_epoch = timestamp(captured)
        age = time.time() - captured_epoch if captured_epoch is not None else None
        stale = age is None or age > config["stale_seconds"] or age < -300
        supported = [o["name"] for o in operations if isinstance(o, dict) and o.get("name") in ("startCharging", "stopCharging")]
        affected = any(c.startswith(("CHARGING_", "OPERATIONS_")) for c in codes)
        if affected or not charging:
            supported = []
        message = "Fahrzeugzugriff bestätigt; Funktionen anhand der FIN ermittelt."
        if not charging:
            message = "Keine Ladedaten. Citigo-Unterstützung über diese API nicht bestätigt."
        elif stale:
            message = "Fahrzeugdaten veraltet oder ohne gültigen Fahrzeugzeitstempel. Steuerung gesperrt."
        elif not supported:
            message = "Ladedaten vorhanden, aber Start/Stopp nicht als Fahrzeugfunktion bestätigt."
        if affected:
            message = "Ladefunktion oder Befehlsliste ist nicht verfügbar, nicht unterstützt oder deaktiviert."
        state = status.get("state")
        return {"available": bool(charging) and not affected, "code": "partial" if codes else "ok",
                "message": message, "soc": soc, "state": state if isinstance(state, str) else None,
                "plug": status.get("plugConnectionState") if isinstance(status.get("plugConnectionState"), str) else None,
                "captured_at": captured if captured_epoch is not None else None,
                "read_at": datetime.now(timezone.utc).isoformat(), "stale": stale,
                "operations": supported, "errors": [c for c in codes if c.startswith(("CHARGING_", "OPERATIONS_"))],
                "key_expires_at": self.key_expires_at, "quota_remaining": self.quota_remaining}

    def command(self, action, config, key):
        if action not in ("start", "stop"):
            raise ValueError("Unbekannter Fahrzeugbefehl.")
        self.call("POST", f"/api/v1/vehicles/{config['vin']}/charging/{action}", key)
