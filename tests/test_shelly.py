"""No real network or output writes; simulated Shelly and temporary profiles only."""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from citifoxcharger.shelly import DEFAULT, MODEL, NoRedirect, ShellyOutput, ShellyRPC, ShellyOutputs, validate
from citifoxcharger.skoda import IntegrationError


class Device:
    def __init__(self):
        self.calls, self.on, self.timer, self.started = [], False, None, None
        self.model, self.gen, self.error, self.ignore_timer = MODEL, 4, None, False

    def call(self, method, params=None):
        self.calls.append((method, copy.deepcopy(params)))
        if self.error:
            raise IntegrationError('simulated', 'Simulated offline device.')
        if method == 'Shelly.GetDeviceInfo':
            return dict(model=self.model, gen=self.gen)
        if method == 'Switch.GetStatus':
            value = dict(id=0, output=self.on)
            if self.timer:
                value.update(timer_started_at=self.started, timer_duration=self.timer)
            return value
        if method == 'Switch.Set':
            self.on = params['on']
            self.timer = params.get('toggle_after') if not self.ignore_timer else None
            self.started = time.time() if self.timer else None
            return dict(was_on=False)
        raise AssertionError(method)

    @property
    def writes(self):
        return [params for method, params in self.calls if method == 'Switch.Set']


class ShellyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.device, self.factories = Device(), []
        def factory(host, password):
            self.factories.append((host, password))
            return self.device
        self.factory = factory
        self.output = ShellyOutput(self.temp.name, factory)

    def configure(self, **changes):
        return self.output.configure({**DEFAULT, 'enabled': True, 'host': '192.168.0.123',
                                      'password': '', 'clear_password': False, **changes})

    def test_disabled_installation_never_contacts_device(self):
        self.output.poll()
        self.assertFalse(self.output.state()['configured'])
        self.assertEqual(self.factories, [])

    def test_setup_does_not_switch_and_defaults_to_one_hour(self):
        self.configure()
        self.assertEqual(self.device.calls, [])
        self.assertEqual(self.output.state()['on_minutes'], 60)
        self.assertTrue(ShellyOutput(self.temp.name).config['enabled'])

    def test_on_and_off_are_explicit_not_toggle(self):
        self.configure()
        self.output.set('on', None, 'simulated-on-00000001')
        self.assertTrue(self.output.state()['on'])
        self.output.set('off', None, 'simulated-off-0000001')
        self.assertFalse(self.output.state()['on'])
        self.assertEqual(self.device.writes, [dict(id=0, on=True), dict(id=0, on=False)])

    def test_one_hour_timer_is_sent_atomically_to_device_and_verified(self):
        self.configure()
        self.output.set('timer', 60, 'simulated-timer-00001')
        self.assertEqual(self.device.writes, [dict(id=0, on=True, toggle_after=3600)])
        self.assertAlmostEqual(self.output.state()['timer_ends_at'], time.time()+3600, delta=1)

    def test_custom_duration_and_explicit_timer_restart(self):
        self.configure()
        self.output.set('timer', 30, 'simulated-timer-00001')
        self.output.set('timer', 90, 'simulated-timer-00002')
        self.assertEqual([v['toggle_after'] for v in self.device.writes], [1800, 5400])

    def test_poll_and_backend_restart_do_not_replay_or_extend_timer(self):
        self.configure()
        self.output.set('timer', 60, 'simulated-timer-00001')
        end = self.output.state()['timer_ends_at']
        restarted = ShellyOutput(self.temp.name, self.factory)
        restarted.poll()
        self.assertEqual(restarted.state()['timer_ends_at'], end)
        self.assertEqual(len(self.device.writes), 1)
        for _ in range(5):restarted.poll()
        self.assertEqual(len(self.device.writes), 1)
        with self.assertRaises(IntegrationError):restarted.set('timer', 60, 'simulated-timer-00001')
        self.assertEqual(len(self.device.writes), 1)

    def test_request_id_cannot_change_duration_or_action(self):
        self.configure()
        self.output.set('timer', 60, 'simulated-timer-00001')
        with self.assertRaises(ValueError):self.output.set('timer', 30, 'simulated-timer-00001')
        self.assertEqual(len(self.device.writes), 1)

    def test_invalid_commands_do_not_contact_device(self):
        self.configure()
        for action, minutes in [('toggle', None), ('on', 60), ('timer', None), ('timer', True),
                                ('timer', 0), ('timer', 1441), ('timer', 1.5), ('timer', '60')]:
            with self.subTest(action=action, minutes=minutes), self.assertRaises(ValueError):
                self.output.set(action, minutes, 'simulated-timer-00001')
        self.assertEqual(self.device.calls, [])

    def test_non_private_hosts_urls_ports_and_credentials_are_rejected(self):
        for host in ['127.0.0.1', '8.8.8.8', '169.254.1.2', 'localhost', 'http://192.168.0.1',
                     '192.168.0.1:80', 'user@192.168.0.1', '192.168.0.1/path', '::1']:
            with self.subTest(host=host), self.assertRaises(ValueError):self.configure(host=host)
        self.assertEqual(self.device.calls, [])

    def test_wrong_device_cannot_be_switched_including_after_ip_reassignment(self):
        self.configure()
        self.output.poll()
        self.device.model = 'S3PM-monitor-only'
        with self.assertRaises(IntegrationError):self.output.set('timer', 60, 'simulated-timer-00001')
        self.assertEqual(self.device.writes, [])

    def test_missing_timer_readback_requests_off_instead_of_claiming_success(self):
        self.configure()
        self.device.ignore_timer = True
        with self.assertRaises(IntegrationError):self.output.set('timer', 60, 'simulated-timer-00001')
        self.assertFalse(self.device.on)
        self.assertEqual(len(self.device.writes), 2)
        self.assertFalse(self.output.state()['available'])

    def test_write_timeout_is_not_retried_after_restart(self):
        self.configure()
        original = self.device.call
        def call(method, params=None):
            result = original(method, params)
            if method == 'Switch.Set':raise IntegrationError('simulated', 'Simulated lost reply.')
            return result
        self.device.call = call
        with self.assertRaises(IntegrationError):self.output.set('timer', 60, 'simulated-timer-00001')
        restarted = ShellyOutput(self.temp.name, self.factory)
        with self.assertRaises(IntegrationError):restarted.set('timer', 60, 'simulated-timer-00001')
        self.assertEqual(len(self.device.writes), 1)

    def test_unavailable_and_stale_status_never_invent_an_output_state(self):
        self.configure();self.output.poll()
        self.output.value['read_at'] -= 31
        self.assertIsNone(self.output.state()['on'])
        self.device.error = True;self.output.next_poll = 0;self.output.poll()
        self.assertFalse(self.output.state()['available'])
        self.assertIsNone(self.output.state()['on'])

    def test_storage_failure_prevents_energizing(self):
        self.configure()
        with patch('citifoxcharger.shelly.atomic_write', side_effect=OSError('simulated full disk')):
            with self.assertRaises(OSError):self.output.set('timer', 60, 'simulated-timer-00001')
        self.assertEqual(self.device.writes, [])

    def test_disable_does_not_switch_or_cancel_a_device_timer(self):
        self.configure();self.output.set('timer', 60, 'simulated-timer-00001')
        self.configure(enabled=False)
        self.output.poll()
        self.assertEqual(len(self.device.writes), 1)
        self.assertEqual(self.device.timer, 3600)

    def test_credentials_are_encrypted_bound_to_host_and_never_in_state(self):
        # Substitute a reversible envelope only for this isolated encryption-boundary test.
        def envelope(data, decrypt=False):
            return bytes(v ^ 0x55 for v in data)
        with patch('citifoxcharger.shelly.dpapi', side_effect=envelope):
            self.configure(password='synthetic-device-password')
            self.assertNotIn(b'synthetic-device-password', self.output.password_path.read_bytes())
            restarted = ShellyOutput(self.temp.name, self.factory)
            self.assertEqual(restarted.password, 'synthetic-device-password')
            self.assertNotIn('synthetic-device-password', json.dumps(restarted.state()))
            self.assertNotIn('synthetic-device-password', json.dumps(restarted.settings()))
            self.assertNotIn('host', restarted.state())
            self.configure(host='192.168.0.124')
            self.assertEqual(self.output.password, '')
            self.assertFalse(self.output.password_path.exists())

    def test_corrupt_profile_and_mismatched_credential_fail_closed(self):
        self.configure()
        self.output.commands_path.write_text('not json')
        restarted = ShellyOutput(self.temp.name, self.factory)
        restarted.poll()
        self.assertFalse(restarted.config['enabled'])
        self.assertEqual(self.device.calls, [])

    def test_redirects_are_not_followed_and_environment_proxy_is_disabled(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'http://example.com'))
        client = ShellyRPC('192.168.0.123')
        self.assertEqual(client.base, 'http://192.168.0.123')
        self.assertFalse(any(type(h).__name__ == 'ProxyHandler' for h in client.opener.handlers))

    def test_independent_devices_use_saved_toggle_behavior(self):
        light = Device()
        outputs = ShellyOutputs(self.temp.name, lambda host, password: self.device if host.endswith('.100') else light,
                                installation=True)
        outputs.preferences({'device': 'fan', 'on_minutes': 30})
        outputs.preferences({'device': 'light', 'on_minutes': 0})
        outputs.set('fan', True, 'simulated-fan-0000001')
        outputs.set('light', True, 'simulated-light-00001')
        self.assertEqual(self.device.writes, [dict(id=0, on=True, toggle_after=1800)])
        self.assertEqual(light.writes, [dict(id=0, on=True)])
        outputs.set('fan', False, 'simulated-fan-0000002')
        self.assertTrue(light.on)
        self.assertFalse(self.device.on)
        self.assertEqual(ShellyOutputs(self.temp.name).outputs['fan'].config['on_minutes'], 30)

    def test_behavior_preferences_cannot_change_addresses_or_passwords(self):
        outputs = ShellyOutputs(self.temp.name, self.factory, installation=True)
        for data in ({'device': 'light', 'on_minutes': 5}, {'device': 'light', 'on_minutes': True},
                     {'device': 'light', 'on_minutes': 60, 'host': '192.168.0.1'},
                     {'device': '../other', 'on_minutes': 60}):
            with self.assertRaises(ValueError):outputs.preferences(data)
        self.assertEqual(self.device.calls, [])
        self.assertEqual(outputs.settings(False), {'fan': {'on_minutes': 60}, 'light': {'on_minutes': 60}})

    def test_offline_fan_does_not_hide_or_block_light(self):
        light = Device()
        self.device.error = True
        outputs = ShellyOutputs(self.temp.name, lambda host, password: self.device if host.endswith('.100') else light,
                                installation=True)
        outputs.poll()
        self.assertFalse(outputs.state()['fan']['available'])
        self.assertTrue(outputs.state()['light']['available'])
        outputs.set('light', True, 'simulated-light-00001')
        self.assertTrue(light.on)
        self.assertEqual(self.device.writes, [])


if __name__ == '__main__':
    unittest.main()
