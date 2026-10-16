"""Read-only energy display from the installed manufacturer's values.xml and JS.

House consumption follows the vendor's production + grid - battery formula.
Relay outputs report switching state, never an invented electrical power.
"""
import html
import math
import re


def quantity(value, unit, low, high):
    text = html.unescape(value or "").replace("<span>", "").replace("</span>", "")
    match = re.fullmatch(r"\s*([-+]?\d+(?:[.,]\d+)?)\s*" + re.escape(unit) + r"\s*", text)
    if not match:
        return None
    number = float(match[1].replace(",", "."))
    return number if math.isfinite(number) and low <= number <= high else None


def energy_display(fields, read_at):
    grid_w = quantity(fields.get("hidPower"), "W", -10000000, 10000000)
    grid = grid_w / 1000 if grid_w is not None else None
    production = quantity(fields.get("hidProduction"), "kW", -10000, 10000)
    battery = quantity(fields.get("batteryPower"), "kW", -10000, 10000)
    house = max(0, production + grid - battery) if all(x is not None for x in (grid, production, battery)) else None

    def temperature(sensor):
        if not re.fullmatch(r"[1-9]", fields.get(sensor, "")):
            return None
        # Keep invalid sensor slots in place; never assign sensor 2's value to sensor 1.
        readings = html.unescape(fields.get("analogOutTemp", "")).split("°C")[:-1]
        index = int(fields[sensor]) - 1
        if index >= len(readings):
            return None
        raw = readings[index].strip().lstrip(",;").strip()
        return quantity(raw+" °C", "°C", -50, 150)  # 850 is the vendor's missing-sensor sentinel.

    percent = quantity(fields.get("analogOutPercent"), "%", 0, 100)
    aout_states = {0: "Automatik" if percent is None or percent > 0 else "Kein Überschuss", 1: "Schaltzeit aktiv",
                   2: "Mindesttemperatur", 3: "Temperatur erreicht", 4: "Hygieneprogramm", 5: "Gesperrt",
                   6: "Überwachung aktiv", 7: "Tarifsteuerung", 8: "Manuell ein", 9: "Aus", 10: "Timer aktiv",
                   11: "Deaktiviert", 12: "Extern gesperrt", 13: "Extern freigegeben", 14: "Einschaltverzögerung",
                   15: "Gesperrt", 16: "Temperatur erreicht", 17: "Leistungsgrenze", 18: "Tarifsteuerung"}
    relay_states = {0: "Kein Überschuss", 1: "Manuell ein", 2: "Schaltzeit aktiv", 3: "Nachlauf",
                    4: "Einschaltverzögerung", 5: "Ausschaltverzögerung", 6: "Automatik", 7: "Zweiter Heizstab",
                    8: "Timer aktiv", 9: "Deaktiviert", 10: "Manuell aus", 11: "Extern gesperrt",
                    12: "Extern freigegeben", 13: "Schaltgrenze", 14: "Gesperrt", 15: "Tarifsteuerung",
                    16: "Tarifsteuerung", 17: "Booster ein", 18: "Booster aus"}

    def status(name, mapping):
        code = fields.get(name, "")
        return mapping.get(int(code), "Status unbekannt") if re.fullmatch(r"\d{1,3}", code) else "Status unbekannt"

    analog_enabled = fields.get("hidAoutActivated") == "1"
    manual_raw = fields.get("hidAoutPercentage", "")
    manual_percent = int(manual_raw) if re.fullmatch(r"\d{1,3}", manual_raw) and int(manual_raw) <= 100 else None
    heater = {"name": "Heizstab", "enabled":analog_enabled,
              "mode":{"0":"OFF","1":"M","2":"A"}.get(fields.get("hidAoutMode")),
              "manual_percent":manual_percent,
              "power_kw": quantity(fields.get("analogOutPower"), "kW", 0, 1000) if analog_enabled else None,
              "temperature_c": temperature("hidAoutTemperatureSensors") if analog_enabled else None,
              "percent": percent if analog_enabled else None,
              "status": status("hidAoutStatus", aout_states) if analog_enabled else
              "Deaktiviert" if fields.get("hidAoutActivated") == "0" else "Nicht verfügbar"}
    relays = []
    for number, name in ((1, "Heizstab 2"), (2, "Warmwasser")):
        enabled = fields.get(f"hidRelay{number}Activated") == "1"
        switch = fields.get(f"hidR{number}Status")
        relays.append({"name": name, "enabled":enabled,
                       "mode":{"0":"A","m":"M","x":"OFF"}.get(fields.get(f"hidR{number}Mode")),
                       "on": switch == "1" if enabled and switch in ("0", "1") else None,
                       "temperature_c": temperature(f"hidR{number}TempSensor") if enabled else None,
                       "status": status(f"hidR{number}Info", relay_states) if enabled else
                       "Deaktiviert" if fields.get(f"hidRelay{number}Activated") == "0" else "Nicht verfügbar"})
    return {"read_at": read_at, "house_kw": house, "grid_kw": grid, "heater": heater,
            "heater2": relays[0], "hot_water": relays[1]}
