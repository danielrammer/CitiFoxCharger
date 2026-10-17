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
            raise ModbusError("Read timed out.")
        sock.settimeout(remaining)
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise ModbusError("Connection closed before the complete response.")
        data.extend(chunk)
    return bytes(data)


class ModbusReader:
    def read(self, host, port, unit, address, count, timeout=2):
        if not 0 <= address <= 65535 or not 1 <= count <= 125 or address + count > 65536:
            raise ValueError("Invalid read range.")
        transaction = secrets.randbelow(65536)
        request = struct.pack(">HHHBBHH", transaction, 0, 6, unit, 3, address, count)
        deadline = time.monotonic() + timeout
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                sock.sendall(request)
                header = receive(sock, 7, deadline)
                tid, protocol, length, response_unit = struct.unpack(">HHHB", header)
                if (tid, protocol, response_unit) != (transaction, 0, unit) or not 2 <= length <= 254:
                    raise ModbusError("Invalid Modbus response header.")
                pdu = receive(sock, length - 1, deadline)
                if pdu[0] == 0x83 and len(pdu) == 2:
                    raise ModbusError(f"Modbus exception {pdu[1]} (for example, an invalid address).")
                if pdu[0] != 3 or len(pdu) != 2 + count * 2 or pdu[1] != count * 2:
                    raise ModbusError("Function code or data length does not match.")
                return list(struct.unpack(">" + "H" * count, pdu[2:]))
        except (OSError, TimeoutError):
            raise ModbusError("Smartfox is unreachable or the read timed out.") from None


def unsigned(words, order="big"):
    result = 0
    for word in (words if order == "big" else reversed(words)):
        result = (result << 16) | word
    return result


OBSERVED = [
    ("energy", 40999, 4, "Total energy · previous assignment", None),
    ("power", 41015, 2, "Charging power · previous assignment", None),
    ("mode", 42111, 1, "Charging mode · previous assignment", None),
]
MANUFACTURER = [
    ("energy", 41599, 4, 'Charger 1 · total energy', "Wh"),
    ("power", 41605, 2, 'Charger 1 · power', "W"),
    ("mode", 41607, 1, "Charger 1 · mode code", None),
]
WRITE_BLOCK = ('Smartfox Modbus writes are blocked: EM3 00.01.16.08 has no verified write registers or mode codes. The older v22e list does not fully match.')


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
        result["notice"] = ('Older manufacturer list v22e. Verify meaning and word order against this firmware. Mode codes remain unknown.'
                            if manufacturer else
                            'Raw values at the requested PDU addresses. In the older list, 41015 is daily energy and 42111 is absent. Charging power is not confirmed.')
        return result

    def command(self, _action):
        raise ModbusError(WRITE_BLOCK)
