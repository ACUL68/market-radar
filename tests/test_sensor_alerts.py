"""Test dei report autonomi. Eseguire: python -m unittest tests.test_sensor_alerts"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from sensor_alerts import mark_sensor_alert, pending_sensor_alerts

ROME = ZoneInfo("Europe/Rome")
NOW = datetime(2026, 10, 8, 15, 35, tzinfo=ROME)


class SensorAlertTests(unittest.TestCase):
    def test_wti_up_at_threshold(self):
        result = pending_sensor_alerts({}, {"change_pct": 2.0, "level": 91}, {}, NOW)
        self.assertEqual(len(result), 1)
        self.assertIn("WTI", result[0][0])
        self.assertIn("+2.00%", result[0][1])

    def test_wti_down_at_threshold(self):
        result = pending_sensor_alerts({}, {"change_pct": -2.0}, {}, NOW)
        self.assertEqual(len(result), 1)
        self.assertIn("-2.00%", result[0][1])

    def test_vstoxx_up_and_down_at_threshold(self):
        for change in (-5.0, 5.0):
            with self.subTest(change=change):
                result = pending_sensor_alerts({}, {}, {"change_pct": change}, NOW)
                self.assertEqual(len(result), 1)
                self.assertIn("VSTOXX", result[0][0])

    def test_below_threshold_no_report(self):
        result = pending_sensor_alerts({}, {"change_pct": 1.99}, {"change_pct": -4.99}, NOW)
        self.assertEqual(result, [])

    def test_dedupe_same_day_but_new_direction_allowed(self):
        state = {}
        first = pending_sensor_alerts(state, {"change_pct": 5.05}, {}, NOW)
        self.assertEqual(len(first), 1)
        mark_sensor_alert(state, first[0][0], NOW)
        repeated = pending_sensor_alerts(state, {"change_pct": 3.0}, {}, NOW)
        self.assertFalse(repeated)
        reversal = pending_sensor_alerts(state, {"change_pct": -2.1}, {}, NOW)
        self.assertEqual(len(reversal), 1)

    def test_next_day_resets(self):
        state = {}
        first = pending_sensor_alerts(state, {"change_pct": 2.1}, {}, NOW)
        mark_sensor_alert(state, first[0][0], NOW)
        next_day = NOW.replace(day=9)
        self.assertEqual(len(pending_sensor_alerts(state, {"change_pct": 2.1}, {}, next_day)), 1)

    def test_bad_data_does_not_raise_false_report(self):
        result = pending_sensor_alerts({}, {"change_pct": float("nan")}, {"change_pct": "n/d"}, NOW)
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
