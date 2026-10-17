import copy
import socket
import struct
import threading
import unittest
from citifoxcharger.config import DEFAULT
from citifoxcharger.modbus import ModbusReader, ModbusError, SmartfoxAdapter, unsigned


class ModbusTests(unittest.TestCase):
    def exchange(self, responder, address=40999, count=4):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        captured = []
        def server():
            try:
                with listener.accept()[0] as connection:
                    data = b""
                    while len(data) < 12:
                        data += connection.recv(12 - len(data))
                    captured.append(data)
                    response = responder(data)
                    # Deliberately split MBAP and PDU into TCP fragments.
                    try:
                        for start in range(0, len(response), 2):
                            connection.sendall(response[start:start + 2])
                    except (ConnectionResetError, BrokenPipeError):
                        pass  # Expected when the reader rejects a malformed header early.
            finally:
                listener.close()
        thread = threading.Thread(target=server, daemon=True)
        thread.start()
        try:
            return ModbusReader().read("127.0.0.1", port, 1, address, count, 1), captured
        finally:
            thread.join(timeout=2)

    def frame(self, request, pdu, **overrides):
        tid, proto, _, unit = struct.unpack(">HHHB", request[:7])
        return struct.pack(">HHHB", overrides.get("tid", tid), overrides.get("proto", proto),
                           overrides.get("length", len(pdu) + 1), overrides.get("unit", unit)) + pdu

    def test_fragmented_frame_and_exact_pdu_address(self):
        words, requests = self.exchange(lambda r: self.frame(r, b"\x03\x08" + struct.pack(">4H", 0, 0, 38, 22886)))
        self.assertEqual(words, [0, 0, 38, 22886])
        self.assertEqual(struct.unpack(">BHH", requests[0][7:]), (3, 40999, 4))

    def test_reject_wrong_transaction_protocol_and_unit(self):
        for field, value in [("tid", 70000 % 65536), ("proto", 1), ("unit", 2)]:
            with self.subTest(field=field):
                def response(r):
                    changed = (struct.unpack(">H", r[:2])[0] + 1) % 65536 if field == "tid" else value
                    return self.frame(r, b"\x03\x08" + bytes(8), **{field: changed})
                with self.assertRaises(ModbusError):
                    self.exchange(response)

    def test_exception(self):
        with self.assertRaisesRegex(ModbusError, "exception 2"):
            self.exchange(lambda r: self.frame(r, b"\x83\x02"))

    def test_short_wrong_length_and_wrong_function(self):
        for pdu in (b"\x03\x02\x00\x01", b"\x04\x08" + bytes(8), b"\x03\x07" + bytes(8)):
            with self.subTest(pdu=pdu), self.assertRaises(ModbusError):
                self.exchange(lambda r: self.frame(r, pdu))

    def test_truncated_response_and_oversized_header(self):
        for response in (lambda r: self.frame(r, b"\x03", length=11),
                         lambda r: self.frame(r, b"", length=65535)):
            with self.assertRaises(ModbusError):
                self.exchange(response)

    def test_invalid_ranges_never_connect(self):
        for address, count in [(-1, 1), (65535, 2), (1, 126), (1, 0)]:
            with self.assertRaises(ValueError):
                ModbusReader().read("invalid", 1, 1, address, count)

    def test_word_order_and_uint64_precision(self):
        self.assertEqual(unsigned([1, 2]), 65538)
        self.assertEqual(unsigned([1, 2], "little"), 131073)
        self.assertEqual(unsigned([65535] * 4), 2**64 - 1)

    def test_observed_registers_are_never_physical_values(self):
        calls = []
        class Reader:
            def read(self, *args):
                calls.append(args)
                return [62] * args[4]
        config = copy.deepcopy(DEFAULT["smartfox"])
        config["interpretation_confirmed"] = True
        result = SmartfoxAdapter(Reader()).read(config)
        self.assertEqual([c[3] for c in calls], [40999, 41015, 42111])
        self.assertTrue(result["available"])
        self.assertTrue(all(r["value"] is None and r["unit"] is None for r in result["registers"]))
        self.assertFalse(result["write_supported"])

    def test_manufacturer_profile_requires_explicit_confirmation(self):
        class Reader:
            def read(self, *args): return [0] * (args[4] - 1) + [12]
        config = copy.deepcopy(DEFAULT["smartfox"])
        config["profile"] = "manufacturer_v22e"
        adapter = SmartfoxAdapter(Reader())
        before = adapter.read(config)
        self.assertTrue(all(row["value"] is None for row in before["registers"]))
        config["interpretation_confirmed"] = True
        after = adapter.read(config)
        self.assertEqual([(r["address"], r["unit"]) for r in after["registers"]], [(41599, "Wh"), (41605, "W"), (41607, None)])
        self.assertIsNone(after["registers"][2]["value"])

    def test_partial_errors_visible_and_no_write_path(self):
        class Reader:
            def read(self, *args):
                if args[3] == 42111: raise ModbusError("Testausfall")
                return [0] * args[4]
        adapter = SmartfoxAdapter(Reader())
        result = adapter.read(DEFAULT["smartfox"])
        self.assertFalse(result["available"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["registers"][-1]["error"], "Testausfall")
        with self.assertRaises(ModbusError): adapter.command("manual")


if __name__ == "__main__": unittest.main()
