import copy
import ipaddress
import json
import os
import re
from pathlib import Path


DEFAULT = {
    "smartfox": {
        "host": "192.168.0.81", "port": 502, "unit_id": 1,
        "firmware": "EM3 00.01.16.08", "poll_seconds": 10,
        "timeout_seconds": 2, "profile": "observed", "word_order": "big",
        "interpretation_confirmed": False,
    },
    "skoda": {"vin": "", "poll_seconds": 180, "stale_seconds": 900},
}


def validate(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError("Configuration must contain smartfox and skoda.")
    result = copy.deepcopy(value)
    for section, template in DEFAULT.items():
        if not isinstance(result[section], dict) or set(result[section]) != set(template):
            raise ValueError("Unknown or missing configuration fields.")
    sf, car = result["smartfox"], result["skoda"]
    try:
        ip = ipaddress.IPv4Address(sf["host"])
    except (ValueError, TypeError):
        raise ValueError("Smartfox requires a private IPv4 address.") from None
    if not any(ip in ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        raise ValueError("Smartfox must be on the private local network.")
    for section, field, low, high in [
        (sf, "port", 1, 65535), (sf, "unit_id", 1, 247),
        (sf, "poll_seconds", 5, 3600), (sf, "timeout_seconds", 1, 10),
        (car, "poll_seconds", 60, 3600), (car, "stale_seconds", 300, 86400),
    ]:
        if type(section[field]) is not int or not low <= section[field] <= high:
            raise ValueError(f"Invalid value for {field} ({low}–{high}).")
    if sf["profile"] not in ("observed", "manufacturer_v22e"):
        raise ValueError("Unknown Smartfox read profile.")
    if sf["word_order"] not in ("big", "little"):
        raise ValueError("Invalid word order.")
    if type(sf["interpretation_confirmed"]) is not bool:
        raise ValueError("interpretation_confirmed muss boolesch sein.")
    if not isinstance(sf["firmware"], str) or len(sf["firmware"]) > 80:
        raise ValueError("Invalid firmware identifier.")
    if not isinstance(car["vin"], str):
        raise ValueError("Invalid VIN.")
    car["vin"] = car["vin"].strip().upper()
    if car["vin"] and not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", car["vin"]):
        raise ValueError("The VIN must contain 17 valid characters.")
    return result


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        with temp.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


class ConfigStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "config.json"
        self.value = validate(json.loads(self.path.read_text(encoding="utf-8"))) if self.path.exists() else copy.deepcopy(DEFAULT)

    def save(self, value):
        value = validate(value)
        atomic_write(self.path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))
        self.value = value
