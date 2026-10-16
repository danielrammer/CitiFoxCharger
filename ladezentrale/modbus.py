"""Strict Modbus TCP reader. Intentionally no write function codes or raw PDU API."""
import secrets
import socket
import struct
import time
from datetime import datetime, timezone


class ModbusError(Exception):
    pass


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def receive(sock, length, deadline):
    data = bytearray()
    while len(data) < length:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ModbusError("Zeitüberschreitung beim Lesen.")
        sock.settimeout(remaining)
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise ModbusError("Verbindung vor vollständiger Antwort beendet.")
        data.extend(chunk)
    return bytes(data)


class ModbusReader:
    def read(self, host, port, unit, address, count, timeout=2):
        if not 0 <= address <= 65535 or not 1 <= count <= 125 or address + count > 65536:
            raise ValueError("Ungültiger Lesebereich.")
        transaction = secrets.randbelow(65536)
        request = struct.pack(">HHHBBHH", transaction, 0, 6, unit, 3, address, count)
        deadline = time.monotonic() + timeout
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                sock.sendall(request)
                header = receive(sock, 7, deadline)
                tid, protocol, length, response_unit = struct.unpack(">HHHB", header)
                if (tid, protocol, response_unit) != (transaction, 0, unit) or not 2 <= length <= 254:
                    raise ModbusError("Ungültiger Modbus-Antwortkopf.")
                pdu = receive(sock, length - 1, deadline)
                if pdu[0] == 0x83 and len(pdu) == 2:
                    raise ModbusError(f"Modbus-Ausnahme {pdu[1]} (z. B. ungültige Adresse).")
                if pdu[0] != 3 or len(pdu) != 2 + count * 2 or pdu[1] != count * 2:
                    raise ModbusError("Funktionscode oder Datenlänge stimmt nicht.")
                return list(struct.unpack(">" + "H" * count, pdu[2:]))
        except (OSError, TimeoutError):
            raise ModbusError("Smartfox nicht erreichbar oder Lesezeit überschritten.") from None


def unsigned(words, order="big"):
    result = 0
    for word in (words if order == "big" else reversed(words)):
        result = (result << 16) | word
    return result


OBSERVED = [
    ("energy", 40999, 4, "Gesamtenergie · bisherige Zuordnung", None),
    ("power", 41015, 2, "Ladeleistung · bisherige Zuordnung", None),
    ("mode", 42111, 1, "Lademodus · bisherige Zuordnung", None),
]
MANUFACTURER = [
    ("energy", 41599, 4, "Charger 1 · Gesamtenergie", "Wh"),
    ("power", 41605, 2, "Charger 1 · Leistung", "W"),
    ("mode", 41607, 1, "Charger 1 · Moduscode", None),
]
WRITE_BLOCK = ("Smartfox-Steuerung gesperrt: Für EM3 00.01.16.08 fehlen bestätigte "
               "Schreibregister und Moduscodes. Die alte Liste v22e passt nicht vollständig.")


class SmartfoxAdapter:
    def __init__(self, reader=None):
        self.reader = reader or ModbusReader()

    def read(self, config):
        manufacturer = config["profile"] == "manufacturer_v22e"
        result = {"available": False, "read_at": utcnow(), "registers": [],
                  "profile": config["profile"], "write_supported": False,
                  "write_reason": WRITE_BLOCK, "firmware": config["firmware"]}
        for key, address, count, label, unit in (MANUFACTURER if manufacturer else OBSERVED):
            item = {"key": key, "address": address, "count": count, "label": label,
                    "words": None, "candidate": None, "value": None, "unit": None}
            try:
                words = self.reader.read(config["host"], config["port"], config["unit_id"],
                                         address, count, config["timeout_seconds"])
                item.update(words=words, candidate=str(unsigned(words, config["word_order"])))
                # A label is not proof of semantics. Never decode observed addresses as physical values.
                if manufacturer and config["interpretation_confirmed"] and key != "mode":
                    item.update(value=str(unsigned(words, config["word_order"])), unit=unit)
                item["error"] = None
            except ModbusError as error:
                item["error"] = str(error)
            result["registers"].append(item)
        result["available"] = all(item["error"] is None for item in result["registers"])
        result["partial"] = any(item["error"] is None for item in result["registers"]) and not result["available"]
        result["notice"] = ("Alte Herstellerliste v22e. Die Bedeutung und Wortreihenfolge müssen mit "
                            "dieser Firmware verglichen werden. Moduscodes bleiben unbekannt."
                            if manufacturer else
                            "Rohwerte der gewünschten PDU-Adressen. 41015 ist laut alter Liste Tagesenergie, "
                            "42111 ist dort nicht enthalten. Keine bestätigte Ladeleistung.")
        return result

    def command(self, _action):
        raise ModbusError(WRITE_BLOCK)
