"""Exercise activation preflight over real TLS against simulated tablet methods."""
import importlib.util
import json
import os
import unittest
from pathlib import Path
import test_public_gateway as gateway_tests

if os.name == 'posix':
    spec = importlib.util.spec_from_file_location('gateway_activation', Path(__file__).resolve().parents[1]/'deploy/activate_omdev.py')
    activation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(activation)



@unittest.skipUnless(os.name == 'posix', 'omdev activation requires Linux')
class ActivationPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gateway_tests.GatewayHttpsTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        gateway_tests.GatewayHttpsTests.tearDownClass()

    def setUp(self):
        self.tablet = gateway_tests.GatewayHttpsTests()
        self.tablet.setUp()
        self.addCleanup(self.tablet.tearDown)
        self.headers = {'Host': 'citifox.example.test', 'Origin': 'https://citifox.example.test',
                        'X-CitiFox-Gateway': gateway_tests.TOKEN}

    def call(self, path, headers=None, data=None):
        return self.tablet.call(path, data, extra=headers)

    def test_preflight_accepts_restricted_policy_without_switching_any_devices(self):
        result = activation.validate_public(self.call, self.headers)
        self.assertTrue(result['Cookie'].startswith('lz_public_session='))
        for method in ('refresh_now', 'start_manual_charging', 'stop_charge', 'change_target',
                       'set_heater_mode', 'set_charger_mode'):
            getattr(self.tablet.service, method).assert_not_called()
        self.tablet.updater.launch.assert_not_called()

    def test_preflight_rejects_admin_or_update_permissions(self):
        for field in ('local_admin', 'can_update', 'can_limit_test'):
            def incorrect(path, headers=None, data=None):
                code, body, returned = self.call(path, headers, data)
                if path == '/api/config' and code == 200:
                    value = json.loads(body); value['access'][field] = True
                    body = json.dumps(value).encode()
                return code, body, returned
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'permission boundaries'):
                activation.validate_public(incorrect, self.headers)
