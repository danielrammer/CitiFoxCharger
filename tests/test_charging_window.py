import unittest
from datetime import datetime, timezone, timedelta
from citifoxcharger.charging_window import in_window, parse_time


class ChargingWindowTests(unittest.TestCase):
    def local(self, hour, minute=0):
        return datetime(2026, 10, 28, hour, minute, tzinfo=timezone(timedelta(hours=1)))

    def test_overnight_boundaries(self):
        for hour, minute, expected in [(21,59,False),(22,0,True),(0,0,True),(4,59,True),(5,0,False)]:
            with self.subTest(hour=hour, minute=minute):
                self.assertEqual(in_window(self.local(hour, minute), '22:00', '05:00'), expected)

    def test_daytime_and_invalid_input(self):
        self.assertTrue(in_window(self.local(12), '08:00', '17:00'))
        self.assertFalse(in_window(self.local(17), '08:00', '17:00'))
        for value in ['24:00', '7:00', '12:60', '', None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_time(value)
        with self.assertRaises(ValueError):
            in_window(self.local(12), '12:00', '12:00')
        with self.assertRaises(ValueError):
            in_window(datetime(2026, 1, 1, 12), '08:00', '17:00')

    def test_daylight_saving_fall_back(self):
        for offset, fold in ((2, 0), (1, 1)):
            zone = timezone(timedelta(hours=offset))
            self.assertTrue(in_window(datetime(2026, 10, 25, 2, 30, tzinfo=zone, fold=fold), '22:00', '05:00'))


if __name__ == '__main__':
    unittest.main()
