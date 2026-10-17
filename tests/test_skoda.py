import copy
import json
import time
import unittest
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from citifoxcharger.config import DEFAULT
from citifoxcharger.skoda import SkodaAdapter, IntegrationError, NoRedirect, retry_seconds

VIN = "TMBJB9NY5RF999999"
KEY = "test-key-not-a-real-credential"


def payload():
    return {"vehicle": {"vin": VIN,
            "charging": {"carCapturedTimestamp": datetime.now(timezone.utc).isoformat(),
                         "status": {"state": "READY_FOR_CHARGING", "plugConnectionState": "CONNECTED",
                                    "battery": {"stateOfChargeInPercent": 71}}},
            "operations": [{"name": "startCharging"}, {"name": "stopCharging"}]}, "errors": []}


class SkodaTests(unittest.TestCase):
    def setUp(self):
        self.config = {**DEFAULT["skoda"], "vin": VIN}
        self.data = payload()
        self.calls = []
        self.status, self.headers, self.now = 200, {}, 100
        def request(method, path, key):
            self.calls.append((method, path, key))
            return self.status, self.headers, json.dumps(self.data).encode()
        self.adapter = SkodaAdapter(request, lambda: self.now)

    def read(self): return self.adapter.read(self.config, KEY)

    def test_contract_fields_zero_soc_and_unknown_state(self):
        self.data["vehicle"]["charging"]["status"]["battery"]["stateOfChargeInPercent"] = 0
        self.data["vehicle"]["charging"]["status"]["state"] = "NEW_FUTURE_STATE"
        result = self.read()
        self.assertEqual(result["soc"], 0)
        self.assertEqual(result["state"], "NEW_FUTURE_STATE")
        self.assertEqual(self.calls[0][1], f"/api/v1/vehicles/{VIN}?include=charging,operations")
        self.assertEqual(result["operations"], ["startCharging", "stopCharging"])

    def test_no_credentials_means_no_network(self):
        result = self.adapter.read(self.config, "")
        self.assertEqual(result["code"], "not_configured")
        self.assertEqual(self.calls, [])

    def test_wrong_vin_and_invalid_soc_rejected(self):
        self.data["vehicle"]["vin"] = "OTHER"
        with self.assertRaises(IntegrationError): self.read()
        self.data = payload()
        for soc in (-1, 101, True, "71", 71.5):
            self.data["vehicle"]["charging"]["status"]["battery"]["stateOfChargeInPercent"] = soc
            with self.subTest(soc=soc), self.assertRaises(IntegrationError): self.read()

    def test_no_charging_no_operations_and_explicit_unsupported(self):
        self.data["vehicle"].pop("charging")
        self.data["errors"] = [{"type": "CHARGING_UNSUPPORTED"}]
        result = self.read()
        self.assertFalse(result["available"])
        self.assertEqual(result["operations"], [])
        self.data = payload()
        self.data["vehicle"].pop("operations")
        self.assertEqual(self.read()["operations"], [])

    def test_partial_failure_disables_capabilities(self):
        for code in ("CHARGING_DISABLED", "CHARGING_UNAVAILABLE", "OPERATIONS_UNAVAILABLE"):
            self.data["errors"] = [{"type": code}]
            result = self.read()
            self.assertFalse(result["available"])
            self.assertEqual(result["operations"], [])

    def test_missing_state_never_means_disconnected(self):
        self.data["vehicle"]["charging"]["status"].pop("state")
        result = self.read()
        self.assertIsNone(result["state"])
        self.assertEqual(result["plug"], "CONNECTED")

    def test_stale_missing_future_and_naive_timestamp(self):
        for stamp in (None, "bad", "2026-01-01T00:00:00", (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                      (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()):
            self.data["vehicle"]["charging"]["carCapturedTimestamp"] = stamp
            with self.subTest(stamp=stamp): self.assertTrue(self.read()["stale"])

    def test_auth_and_server_errors_do_not_echo_upstream_data(self):
        self.data = {"detail": "private " + KEY + VIN}
        for status, code in [(401,"auth"),(403,"forbidden"),(404,"not_found"),(422,"unsupported"),(503,"upstream")]:
            self.status = status
            with self.subTest(status=status), self.assertRaises(IntegrationError) as caught: self.read()
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn(KEY, str(caught.exception))
            self.assertNotIn(VIN, str(caught.exception))

    def test_rate_limit_prevents_even_manual_requests_until_retry_after(self):
        self.status, self.headers = 429, {"Retry-After": "123"}
        with self.assertRaises(IntegrationError): self.read()
        self.status = 200
        with self.assertRaises(IntegrationError): self.read()
        self.assertEqual(len(self.calls), 1)
        self.now += 124
        self.assertTrue(self.read()["available"])

    def test_last_quota_response_honours_reset(self):
        self.headers = {"RateLimit-Remaining": "0", "RateLimit-Reset": "42", "X-API-Key-Expires-At": "2027-01-01T00:00:00Z"}
        self.assertEqual(self.read()["key_expires_at"], "2027-01-01T00:00:00Z")
        with self.assertRaises(IntegrationError): self.read()
        self.assertEqual(len(self.calls), 1)

    def test_two_calls_are_reserved_for_commands(self):
        self.headers = {"RateLimit-Remaining": "2", "RateLimit-Reset": "180"}
        self.assertTrue(self.read()["available"])
        with self.assertRaises(IntegrationError) as caught: self.read()
        self.assertEqual(caught.exception.code, "quota_reserved")
        self.status = 202
        self.adapter.command("stop", self.config, KEY)
        self.assertEqual([call[0] for call in self.calls], ["GET", "POST"])

    def test_commands_are_empty_post_with_202_required(self):
        self.status = 202
        self.adapter.command("start", self.config, KEY)
        self.assertEqual(self.calls[-1][:2], ("POST", f"/api/v1/vehicles/{VIN}/charging/start"))
        self.adapter.command("stop", self.config, KEY)
        self.assertTrue(self.calls[-1][1].endswith("/charging/stop"))
        self.status = 200
        with self.assertRaises(IntegrationError): self.adapter.command("start", self.config, KEY)

    def test_post_5xx_outcome_is_uncertain_no_automatic_retry(self):
        self.status = 503
        with self.assertRaises(IntegrationError) as caught: self.adapter.command("start", self.config, KEY)
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(len(self.calls), 1)

    def test_redirects_are_disabled(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))

    def test_retry_after_http_date_and_invalid(self):
        value = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=100))
        self.assertGreater(retry_seconds(value), 90)
        self.assertEqual(retry_seconds("nonsense"), 300)
        self.assertEqual(retry_seconds("nan"), 300)


if __name__ == "__main__": unittest.main()
