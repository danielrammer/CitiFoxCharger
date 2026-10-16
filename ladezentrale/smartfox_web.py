"""Narrow local display adapter for the user's EM3 00.01.16.08.

The CGI command path is verified in that device's vendor JavaScript, but has no
public vendor API contract. Writes require explicit separate opt-in; Modbus stays read-only.
"""
import json
import math
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from .modbus import utcnow

FIRMWARE = "EM3 00.01.16.08"
MODES = {0: "A", 1: "M", 2: "A+", 3: "OFF", 4: "M+"}


class SmartfoxControlError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_local(host, path, timeout):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(f"http://{host}{path}", timeout=timeout) as response:
            body = response.read(512001)
            if response.code != 200 or len(body) > 512000:
                raise SmartfoxControlError("Ungültige Smartfox-Antwort.")
            return body
    except (OSError, urllib.error.URLError):
        raise SmartfoxControlError("Smartfox-Webanzeige ist nicht erreichbar.") from None


class SmartfoxWeb:
    # Deliberate gate: do not silently replace the user's manufacturer-documentation requirement.
    def __init__(self, request=None, allow_undocumented_control=False, clock=None, sleep=None):
        self.request = request or request_local
        self.allow_control = allow_undocumented_control
        self.clock, self.sleep = clock or time.monotonic, sleep or time.sleep

    def read(self, config):
        body = self.request(config["host"], "/values.xml", config["timeout_seconds"])
        try:
            if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
                raise ValueError()
            root = ET.fromstring(body)
            fields = {item.attrib.get("id"): item.text or "" for item in root.iter("value")}
            firmware = " ".join(fields.get("version", "").split())
            if firmware != FIRMWARE:
                raise SmartfoxControlError("Smartfox-Firmware passt nicht zum geprüften Webprofil.")
            raw_mode = int(fields["hidCcMode1"])
            power_match = re.fullmatch(r"\s*([0-9]+(?:[.,][0-9]+)?)\s*<span>kW</span>\s*", fields.get("cc1Power", ""))
            power = float(power_match[1].replace(",", ".")) if power_match else None
            if power is not None and (not math.isfinite(power) or not 0 <= power <= 1000):
                power = None
            def integer(name):
                value = fields.get(name, "")
                return int(value) if re.fullmatch(r"[0-9]{1,3}", value) else None
            state_code, info_code = integer("hidCcSt1"), integer("hidCc1Info")
            connection = integer("hidCcConnTimeout1")
            try:
                currents = json.loads(fields.get("hidCcControlCurrent", "[]"))
                current = currents[0] if isinstance(currents, list) and currents else None
                if type(current) not in (int, float) or not math.isfinite(current) or not 0 <= current <= 100:
                    current = None
            except (ValueError, TypeError):
                current = None
            telemetry = connection is not None and connection > 3
            connected = telemetry and state_code in (2, 3, 5, 6)
            states = {0: "Kein Fahrzeug", 1: "Kein Fahrzeug", 2: "Ladebereit", 3: "Lädt",
                      4: "Wallbox-Fehler", 5: "Laden unterbrochen", 6: "An Wallbox gestoppt", 255: "Kein Fahrzeug"}
            # These codes are verified in the installed vendor JavaScript, not in Modbus 42111.
            ready = (telemetry and state_code in (2, 3) and info_code in (2, 8, 10)
                     and current is not None and current >= 6)
            # Existing M+ timers are never changed or inferred from a SOC target.
            mplus_modes = json.loads(fields.get("hidCcMplusMode", "[]"))
            return {"available": True, "mode": MODES.get(raw_mode), "mode_code": raw_mode,
                    "power_kw": power, "read_at": utcnow(), "firmware": firmware,
                    "status_code": state_code, "status_text": states.get(state_code, "Status unbekannt"),
                    "info_code": info_code, "offered_current_a": current, "telemetry_available": telemetry,
                    "connected": connected, "ready_for_start": ready,
                    "mplus_configuration": mplus_modes[0] if mplus_modes else None,
                    "control_available": self.allow_control,
                    "control_reason": None if self.allow_control else
                    "M+/A+-Umschaltung wartet auf Freigabe der nicht öffentlich dokumentierten Smartfox-Websteuerung."}
        except (ET.ParseError, ValueError, KeyError, TypeError, IndexError):
            raise SmartfoxControlError("Smartfox-Webanzeige enthält keine gültigen Charger-1-Daten.") from None

    def set_mode(self, config, mode):
        if mode not in ("M+", "A+"):
            raise ValueError("Nur M+ und A+ sind vorgesehen.")
        # Reading back an already selected A+ requires no write and works even with the gate closed.
        before = self.read(config)
        if before["mode"] == mode:
            return before
        if not self.allow_control:
            raise SmartfoxControlError(before["control_reason"])
        number = 4 if mode == "M+" else 2
        self.request(config["host"], f"/setswcc.cgi?num=0&mode={number}&value=100", config["timeout_seconds"])
        after = self.read(config)
        if after["mode"] != mode:
            raise SmartfoxControlError(f"Smartfox hat {mode} noch nicht bestätigt. Fahrzeugstart wird nicht ausgelöst.")
        return after

    def wait_ready(self, config, timeout=15, settle_seconds=3):
        """Wait for the vendor's live readiness after M+, never send another mode or car command."""
        began = self.clock()
        while True:
            value = self.read(config)
            if value.get("mode") != "M+":
                raise SmartfoxControlError("Smartfox bleibt nicht auf M+. Fahrzeugstart wurde nicht gesendet.")
            if value.get("info_code") == 14:
                raise SmartfoxControlError("Die Wallbox verlangt eine lokale Ladefreigabe. Fahrzeugstart wurde nicht gesendet.")
            if value.get("status_code") == 4:
                raise SmartfoxControlError("Die Wallbox meldet einen Fehler. Fahrzeugstart wurde nicht gesendet.")
            elapsed = self.clock() - began
            if value.get("ready_for_start") and elapsed >= settle_seconds:
                return value
            if elapsed >= timeout:
                raise SmartfoxControlError("Smartfox-Ladefreigabe noch nicht bestätigt. Fahrzeugstart wurde nicht gesendet.")
            self.sleep(min(1, timeout - elapsed))
