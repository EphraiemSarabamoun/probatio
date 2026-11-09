"""The quota meter's rules (meter.py) directly: per-window state, the high-water mark within a window, a new window as
the only way a known high meter falls, reset-less readings and their bounded recovery, rejections, the unknown mark
and its stop, and combining the ledger's state with a host's own. Pure functions; no model, no files.

    python3 -m unittest discover -s calibration/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meter as M  # noqa: E402

T0 = 1_800_000_000.0
LIMITS = {"five_hour": 0.60, "seven_day": 0.60}


def row(t, five=None, five_r=None, seven=None, seven_r=None, status="allowed", host="h"):
    return {"t": t, "status": status, "host": host, "five_hour": five, "five_hour_resets": five_r,
            "seven_day": seven, "seven_day_resets": seven_r}


def full(t, five, seven=0.1, five_r=T0 + 3600, seven_r=T0 + 5e5, host="h"):
    return row(t, five, five_r, seven, seven_r, host=host)


def fold(*rows):
    state = None
    for r in rows:
        state = M.meter_observe(state, r)
    return state


class Windows(unittest.TestCase):
    def test_a_lower_reading_of_the_same_window_keeps_the_mark(self):
        s = fold(full(T0, 0.70), full(T0 + 10, 0.30))
        self.assertEqual(s["windows"]["five_hour"]["u"], 0.70)
        self.assertIsNotNone(M.meter_hold(s, T0 + 20, LIMITS)[0])

    def test_a_reset_within_the_tolerance_is_the_same_window(self):
        s = fold(full(T0, 0.70), full(T0 + 10, 0.30, five_r=T0 + 3600 + M.METER_SAME_WINDOW_S - 1))
        self.assertEqual(s["windows"]["five_hour"]["u"], 0.70)

    def test_a_later_window_replaces_the_mark(self):
        s = fold(full(T0, 0.70), full(T0 + 10, 0.05, five_r=T0 + 3600 + 18000))
        self.assertEqual(s["windows"]["five_hour"]["u"], 0.05)
        self.assertIsNone(M.meter_hold(s, T0 + 20, LIMITS)[0])

    def test_a_reading_taken_after_the_window_ended_starts_a_new_one(self):
        s = fold(full(T0, 0.70), row(T0 + 4000, five=0.02))           # no reset given, but taken after the end
        self.assertEqual(s["windows"]["five_hour"]["u"], 0.02)

    def test_an_earlier_window_changes_nothing(self):
        s = fold(full(T0, 0.70), full(T0 + 10, 0.01, five_r=T0 - 20000))
        self.assertEqual(s["windows"]["five_hour"]["u"], 0.70)

    def test_a_window_left_out_changes_nothing(self):
        s = fold(full(T0, 0.20, seven=0.90), row(T0 + 5, five=0.25, five_r=T0 + 3600))
        self.assertEqual(s["windows"]["seven_day"]["u"], 0.90)
        until, why, _, _ = M.meter_hold(s, T0 + 6, LIMITS)
        self.assertEqual(until, T0 + 5e5 + M.METER_MARGIN_S)
        self.assertIn("seven_day", why)


class Holds(unittest.TestCase):
    def test_a_high_meter_without_reset_holds_then_allows_a_probe(self):
        s = fold(row(T0, five=0.99))
        until, why, _, _ = M.meter_hold(s, T0, LIMITS)
        self.assertEqual(until, T0 + M.METER_HIGH_NO_RESET_S)
        self.assertIn("no reset time", why)
        # after the recovery (the reading's unknown mark expired sooner), one call may probe
        self.assertIsNone(M.meter_hold(s, T0 + M.METER_HIGH_NO_RESET_S + 1, LIMITS)[0])

    def test_a_reset_less_mark_gives_way_after_its_recovery(self):
        s = fold(row(T0, five=0.99), full(T0 + M.METER_HIGH_NO_RESET_S + 5, 0.10, five_r=T0 + 9000))
        self.assertEqual(s["windows"]["five_hour"]["u"], 0.10)

    def test_a_rejection_holds_until_its_reset_and_survives_a_later_reading(self):
        s = fold(row(T0, status="rejected") | {"resets": T0 + 600}, full(T0 + 5, 0.10))
        self.assertEqual(M.meter_hold(s, T0 + 10, LIMITS)[0], T0 + 600 + M.METER_MARGIN_S)

    def test_unknown_telemetry_holds_and_three_in_a_row_stop(self):
        s = fold(row(T0, status="unverified"))
        until, _, stop, _ = M.meter_hold(s, T0, LIMITS)
        self.assertEqual((until, stop), (T0 + M.METER_UNKNOWN_S, False))
        s = fold(row(T0, status="unverified"), row(T0 + 1, status="unverified"), row(T0 + 2, status="unverified"))
        self.assertTrue(M.meter_hold(s, T0 + 3, LIMITS)[2])
        s = M.meter_observe(s, full(T0 + 4, 0.1))                       # a usable reading clears the mark
        self.assertEqual(M.meter_hold(s, T0 + 5, LIMITS)[:3], (None, None, False))

    def test_an_older_valid_reading_does_not_clear_a_newer_unknown(self):
        s = fold(row(T0 + 10, status="unverified"), full(T0, 0.1))
        self.assertIsNotNone(s["unknown"])

    def test_the_host_of_the_hold_is_reported(self):
        s = fold(full(T0, 0.30, host="host-a"), full(T0 + 5, 0.70, host="host-b"))
        self.assertEqual(M.meter_hold(s, T0 + 6, LIMITS)[3], "host-b")


class Combine(unittest.TestCase):
    def test_combining_keeps_the_mark_and_the_later_rejection(self):
        a = fold(full(T0, 0.70, host="host-a"))
        b = fold(full(T0 + 5, 0.40, host="host-b"), row(T0 + 6, status="rejected") | {"resets": T0 + 900})
        c = M.meter_combine(a, b)
        self.assertEqual(c["windows"]["five_hour"]["u"], 0.70)
        self.assertEqual(c["rejected"]["until"], T0 + 900)
        self.assertTrue(M.meter_same(M.meter_combine(c, a), c))

    def test_combining_with_nothing_is_the_state(self):
        a = fold(full(T0, 0.70))
        self.assertTrue(M.meter_same(M.meter_combine(a, None), a))
        self.assertTrue(M.meter_same(M.meter_combine(None, a), a))


if __name__ == "__main__":
    unittest.main()
