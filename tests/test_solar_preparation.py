import copy
import math
import unittest
from datetime import date

import test_phase_preparation as phase_tests
from citifoxcharger.solar_preparation import SolarPreparation, sun_times


class SolarCalculationTests(unittest.TestCase):
    def test_equinox_at_equator_is_approximately_six_to_eighteen_utc(self):
        rise, setting = sun_times(date(2026, 3, 20), 0, 0)
        self.assertLess(abs(rise.hour*60+rise.minute-360), 15)
        self.assertLess(abs(setting.hour*60+setting.minute-1080), 15)
        self.assertIsNotNone(rise.tzinfo)

    def test_longitude_moves_solar_day_without_fixed_night_clock(self):
        for day in (date(2026, 6, 21), date(2026, 12, 21), date(2028, 2, 29)):
            first = sun_times(day, 40, 0)
            east = sun_times(day, 40, 15)
            self.assertAlmostEqual((first[0]-east[0]).total_seconds(), 3600)
            self.assertLess(first[0], first[1])

    def test_polar_day_does_not_invent_sunrise(self):
        self.assertEqual(sun_times(date(2026, 6, 21), 89, 0), (None, None))


class SolarPolicyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = phase_tests.PhaseFlowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.s, self.solar = self.fixture.s, self.fixture.s.solar
        self.night = True
        self.solar.configure({'latitude': 40.0, 'longitude': 15.0})
        self.solar.state = self.state

    def state(self):
        return {'configured': True, 'sunrise_at': '2026-10-27T06:00:00+00:00',
                'sunset_at': '2026-10-27T17:00:00+00:00', 'night': self.night}

    def tick(self):
        self.fixture.now += 65
        self.fixture.refresh_local()
        self.solar.tick()

    def test_night_selects_once_and_day_restores_original_policy(self):
        self.tick()
        self.assertEqual(self.fixture.settings['auto_mode'], 2)
        for _ in range(5): self.tick()
        self.assertEqual(self.fixture.case.effects, ['phase-auto_mode-2'])
        self.night = False
        self.tick()
        self.assertEqual(self.fixture.settings['auto_mode'], 0)
        self.assertFalse(self.solar.record['override'])

    def test_daytime_does_not_change_original_single_phase_choice(self):
        self.night = False
        self.fixture.settings['auto_mode'] = 1
        self.tick()
        self.assertEqual(self.fixture.case.effects, [])
        self.night = True
        self.tick()
        self.night = False
        self.tick()
        self.assertEqual(self.fixture.settings['auto_mode'], 1)

    def test_no_interference_with_manual_session_external_mplus_off_or_power(self):
        for mode, power, status in (('M+', 0, 'idle'), ('OFF', 0, 'idle'), ('A+', 2, 'idle'), ('A+', 0, 'starting')):
            with self.subTest(mode=mode, status=status):
                self.fixture.case.web.mode = mode
                self.fixture.power = power
                self.s.charging.value['status'] = status
                self.tick()
                self.assertEqual(self.fixture.case.effects, [])

    def test_external_phase_choice_is_respected_until_next_solar_day(self):
        self.tick()
        self.fixture.settings['auto_mode'] = 1
        self.tick()
        self.tick()
        self.assertEqual(self.fixture.settings['auto_mode'], 1)
        self.assertTrue(self.solar.record['suppressed'])
        self.night = False
        self.tick()
        self.night = True
        self.tick()
        self.assertEqual(self.fixture.settings['auto_mode'], 2)

    def test_restoration_survives_backend_restart(self):
        self.tick()
        restored = SolarPreparation(self.s)
        self.assertTrue(restored.record['override'])
        restored.restore()
        self.assertEqual(self.fixture.settings['auto_mode'], 0)

    def test_state_does_not_expose_local_location_or_promise_physical_switch(self):
        self.tick()
        result = SolarPreparation(self.s).state()
        self.assertTrue(result['configured'])
        self.assertNotIn('latitude', result)
        self.assertNotIn('longitude', result)
        self.assertNotIn('physical_ready', result)

    def test_bad_location_is_rejected_without_overwriting_profile(self):
        original = copy.deepcopy(self.solar.record)
        for value in ({'latitude': math.nan, 'longitude': 0}, {'latitude': True, 'longitude': 0},
                      {'latitude': 91, 'longitude': 0}, {'latitude': 0, 'longitude': 181},
                      {'latitude': 0, 'longitude': 0, 'night_start': '18:00'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.solar.configure(value)
        self.assertEqual(self.solar.record, original)

    def test_night_preselection_does_not_disable_manual_start(self):
        self.tick()
        self.assertTrue(self.s.charging.state()['can_start_manual'])
        self.night = False
        self.assertTrue(self.s.charging.state()['can_start_manual'])


if __name__ == '__main__':
    unittest.main()
