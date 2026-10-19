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
    aout_states = {0: "Automatic" if percent is None or percent > 0 else "No surplus", 1: "Schedule active",
                   2: "Minimum temperature", 3: "Temperature reached", 4: "Hygiene cycle", 5: "Blocked",
                   6: "Monitoring active", 7: "Tariff control", 8: "Manual on", 9: "Off", 10: "Timer active",
                   11: "Disabled", 12: "Externally blocked", 13: "Externally enabled", 14: "Switch-on delay",
                   15: "Blocked", 16: "Temperature reached", 17: "Power limit", 18: "Tariff control"}
    relay_states = {0: "No surplus", 1: "Manual on", 2: "Schedule active", 3: "Overrun",
                    4: "Switch-on delay", 5: "Switch-off delay", 6: "Automatic", 7: "Second heater",
                    8: "Timer active", 9: "Disabled", 10: "Manual off", 11: "Externally blocked",
                    12: "Externally enabled", 13: "Switching limit", 14: "Blocked", 15: "Tariff control",
                    16: "Tariff control", 17: "Booster on", 18: "Booster off"}

    def status(name, mapping):
        code = fields.get(name, "")
        return mapping.get(int(code), "Status unbekannt") if re.fullmatch(r"\d{1,3}", code) else "Status unbekannt"

    analog_enabled = fields.get("hidAoutActivated") == "1"
    manual_raw = fields.get("hidAoutPercentage", "")
    manual_percent = int(manual_raw) if re.fullmatch(r"\d{1,3}", manual_raw) and int(manual_raw) <= 100 else None
    # The vendor UI prioritizes actuation percentage in manual/forced operation;
    # its power estimate can be zero despite a fully opened analogue output.
    status_code = fields.get("hidAoutStatus")
    percent_primary = fields.get("hidAoutMode") == "1" or status_code in ("2","4","7","8","10","12","13","15","18")
    if fields.get("hidR1Type") == "4" or fields.get("hidR3Type") == "4":
        percent_primary = False
    heater = {"name": "Heater", "enabled":analog_enabled,
              "mode":{"0":"OFF","1":"M","2":"A"}.get(fields.get("hidAoutMode")),
              "manual_percent":manual_percent,
              "percent_primary":percent_primary,
              "power_kw": quantity(fields.get("analogOutPower"), "kW", 0, 1000) if analog_enabled else None,
              "temperature_c": temperature("hidAoutTemperatureSensors") if analog_enabled else None,
              "percent": percent if analog_enabled else None,
              "status": status("hidAoutStatus", aout_states) if analog_enabled else
              "Disabled" if fields.get("hidAoutActivated") == "0" else "Unavailable"}
    relays = []
    for number, name in ((1, "Heater 2"), (2, "Hot water")):
        enabled = fields.get(f"hidRelay{number}Activated") == "1"
        switch = fields.get(f"hidR{number}Status")
        relays.append({"name": name, "enabled":enabled,
                       "mode":{"0":"A","1":"A","m":"M","x":"OFF"}.get(fields.get(f"hidR{number}Mode")),
                       "on": switch == "1" if enabled and switch in ("0", "1") else None,
                       "temperature_c": temperature(f"hidR{number}TempSensor") if enabled else None,
                       "status": status(f"hidR{number}Info", relay_states) if enabled else
                       "Disabled" if fields.get(f"hidRelay{number}Activated") == "0" else "Unavailable"})
    return {"read_at": read_at, "house_kw": house, "grid_kw": grid, "heater": heater,
            "heater2": relays[0], "hot_water": relays[1]}
