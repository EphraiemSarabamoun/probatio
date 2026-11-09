"""The quota rules: reading a rate_limit_event, when to pause and until when, and quota.jsonl's append and latest.
No test calls a model.

    python3 -m unittest discover -s calibration/tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meter as M  # noqa: E402
import quota as Q  # noqa: E402

NOW = 1_800_000_000.0


def info(five=0.10, seven=0.05, five_resets=NOW + 3600, seven_resets=NOW + 86400, status="allowed"):
    """A rate_limit_info as Claude Code 2.1.309 prints it."""
    return {"status": status, "resetsAt": five_resets, "rateLimitType": "five_hour",
            "unifiedWindows": {"five_hour": {"utilization": five, "resetsAt": five_resets},
                               "seven_day": {"utilization": seven, "resetsAt": seven_resets}}}


class Reading(unittest.TestCase):
    def test_fields(self):
        row = Q.reading(info(0.13, 0.08), NOW, "run-1")
        self.assertEqual((row["five_hour"], row["seven_day"], row["run_id"], row["status"]),
                         (0.13, 0.08, "run-1", "allowed"))
        self.assertEqual((row["five_hour_resets"], row["seven_day_resets"]), (NOW + 3600, NOW + 86400))

    def test_a_reading_without_windows_is_unusable_and_holds_briefly(self):
        # part 1 let this run (Codex verification, finding 4); unusable telemetry is now a bounded hold
        row = Q.reading({"status": "allowed"}, NOW)
        self.assertIsNone(row["five_hour"])
        self.assertFalse(row["valid"])
        until, why = Q.pause_until(row, NOW)
        self.assertEqual(until, NOW + M.METER_UNKNOWN_S)
        self.assertIn("unknown", why)
        self.assertEqual(Q.pause_until(row, NOW + M.METER_UNKNOWN_S + 1), (None, None))

    def test_limit_error(self):
        self.assertTrue(Q.limit_error({"is_error": True, "api_error_status": 429, "result": "x"}))
        self.assertTrue(Q.limit_error({"is_error": True, "result": "You've hit your session limit"}))
        self.assertFalse(Q.limit_error({"is_error": True, "result": "timeout"}))
        self.assertFalse(Q.limit_error({"is_error": False, "result": "rate limit"}))
        self.assertFalse(Q.limit_error(None))


OLD = {"five_hour": 0.85, "seven_day": 0.80}      # the part-1 limits: the threshold tests below are stated against them


class Limits(unittest.TestCase):
    def test_defaults_are_lower_than_the_part_one_limits(self):
        self.assertEqual(Q.DEFAULT_LIMITS, {"five_hour": 0.60, "seven_day": 0.60})
        self.assertTrue(all(Q.DEFAULT_LIMITS[w] < OLD[w] for w in Q.WINDOWS))

    def test_defaults_apply_when_none_are_given(self):
        self.assertEqual(Q.pause_until(Q.reading(info(five=0.59, seven=0.59), NOW), NOW), (None, None))
        self.assertIsNotNone(Q.pause_until(Q.reading(info(five=0.60), NOW), NOW)[0])
        self.assertIsNotNone(Q.pause_until(Q.reading(info(seven=0.60), NOW), NOW)[0])

    def test_configured_limits_are_used(self):
        row = Q.reading(info(five=0.65, seven=0.10), NOW)
        self.assertIsNotNone(Q.pause_until(row, NOW)[0])
        self.assertEqual(Q.pause_until(row, NOW, {"five_hour": 0.70, "seven_day": 0.60}), (None, None))

    def test_limits_are_validated(self):
        for bad in ({"five_hour": 0}, {"five_hour": 1.5}, {"seven_day": "0.6"}, {"seven_day": True}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Q.check_limits(bad)
        self.assertEqual(Q.check_limits({"five_hour": 1}), {"five_hour": 1.0, "seven_day": 0.60})

    def test_newest(self):
        a, b = {"t": 5, "host": "a"}, {"t": 9, "host": "b"}
        self.assertEqual(Q.newest(a, None, b)["host"], "b")
        self.assertIsNone(Q.newest(None, {"x": 1}))


class Pause(unittest.TestCase):
    def until(self, **kw):
        return Q.pause_until(Q.reading(info(**kw), NOW), NOW, OLD)

    def test_below_both_limits_runs(self):
        self.assertEqual(self.until(five=0.84, seven=0.79), (None, None))

    def test_five_hour_at_limit_pauses_until_its_reset(self):
        until, why = self.until(five=0.85)
        self.assertEqual(until, NOW + 3600 + Q.RESUME_MARGIN_S)
        self.assertIn("five_hour 0.85", why)

    def test_seven_day_at_limit_pauses_until_its_reset(self):
        until, why = self.until(seven=0.80)
        self.assertEqual(until, NOW + 86400 + Q.RESUME_MARGIN_S)
        self.assertIn("seven_day", why)

    def test_both_wait_for_the_later_reset(self):
        until, why = self.until(five=0.90, seven=0.95)
        self.assertEqual(until, NOW + 86400 + Q.RESUME_MARGIN_S)
        self.assertIn("five_hour", why)
        self.assertIn("seven_day", why)

    def test_a_reset_that_has_passed_clears_the_pause(self):
        row = Q.reading(info(five=0.99, five_resets=NOW - 120), NOW)
        self.assertEqual(Q.pause_until(row, NOW), (None, None))

    def test_the_margin_holds_just_after_the_reset(self):
        row = Q.reading(info(five=0.99, five_resets=NOW - 10), NOW)
        self.assertEqual(Q.pause_until(row, NOW)[0], NOW - 10 + Q.RESUME_MARGIN_S)

    def test_rejected_without_reset_backs_off(self):
        until, why = Q.pause_until(Q.rejected(NOW), NOW)
        self.assertEqual(until, NOW + Q.REJECTED_BACKOFF_S + Q.RESUME_MARGIN_S)
        self.assertEqual(why, "limit reached")
        self.assertEqual(Q.pause_until(Q.rejected(NOW), NOW + Q.REJECTED_BACKOFF_S + 61), (None, None))

    def test_rejected_status_uses_the_event_reset(self):
        until, _ = self.until(five=0.5, status="rejected", five_resets=NOW + 600)
        self.assertEqual(until, NOW + 600 + Q.RESUME_MARGIN_S)

    def test_no_reading_runs(self):
        self.assertEqual(Q.pause_until(None, NOW), (None, None))


class File(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "quota.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_latest_is_the_newest_and_skips_broken_lines(self):
        Q.append(self.path, Q.reading(info(0.50), NOW + 5))
        Q.append(self.path, Q.reading(info(0.90), NOW + 9))
        Q.append(self.path, Q.reading(info(0.20), NOW + 1))    # written late by a slower call: older reading
        with open(self.path, "a") as f:
            f.write('{"t": 1, "five_h')                         # a line cut short
        self.assertEqual(Q.latest(self.path)["five_hour"], 0.90)
        self.assertEqual(Q.pause_state(self.path, NOW + 10)[0], NOW + 3600 + Q.RESUME_MARGIN_S)

    def test_missing_file_means_no_pause(self):
        self.assertIsNone(Q.latest(self.path))
        self.assertEqual(Q.pause_state(self.path, NOW), (None, None))

    def test_append_writes_whole_lines(self):
        for i in range(50):
            Q.append(self.path, Q.reading(info(i / 100), NOW + i, f"r{i}"))
        lines = self.path.read_text().splitlines()
        self.assertEqual([json.loads(line)["run_id"] for line in lines], [f"r{i}" for i in range(50)])


if __name__ == "__main__":
    unittest.main()
