"""The registered quota policy (CALIBRATION.md 7) as Codex's final verification of 74d439e asked for it, one regression
per finding: the weekly 0.60 threshold latches a study stop that no reset, resume or new run lifts, only an operator's
release; after a five-hour pause the study resumes only on a fresh post-reset reading below 0.40; and a stage's burn is
the chronological sum of positive increments, apart from the high-water marks that drive the pauses. The end-to-end
tests run a Stage A spec through run.py and the room's shim on the fakes (test_stages.Plan). No test calls a model.

    python3 -m unittest discover -s calibration/tests
"""
import itertools
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import account as A  # noqa: E402
import fakes  # noqa: E402
import isolate as I  # noqa: E402
import meter as M  # noqa: E402
import quota as Q  # noqa: E402
import scheduler as SC  # noqa: E402
import stages as ST  # noqa: E402
import test_stages as TS  # noqa: E402

T0 = time.time()
LIMITS = {"five_hour": 0.6, "seven_day": 0.6}


def reading(t, five=0.10, five_resets=None, seven=0.05, seven_resets=None):
    """One usable reading at time t (reset times default to an hour and five days after T0)."""
    info = {"status": "allowed", "unifiedWindows": {
        "five_hour": {"utilization": five, "resetsAt": five_resets or T0 + 3600},
        "seven_day": {"utilization": seven, "resetsAt": seven_resets or T0 + 5 * 86400}}}
    return Q.reading(info, t, "run-x")


class Account(unittest.TestCase):
    """A local ledger and one host's scheduler with an injected clock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "account"
        self.now = T0
        self.sched = SC.Scheduler({"home": "local", "home_dir": str(self.home), "host": "host-a", "limits": LIMITS,
                                   "log_path": str(root / "quota.jsonl"), "cache_path": str(root / "cache.json"),
                                   "control_dir": str(root), "cache_seconds": 0, "ssh_timeout": 15, "ssh": "ssh"},
                                  clock=lambda: self.now)
        A.apply(self.home, "init", {"limits": LIMITS})

    def tearDown(self):
        self.tmp.cleanup()

    def at(self, t, **values):
        """Publish one reading taken at t, with the clock moved there."""
        self.now = t
        self.sched.publish(reading(t, **values))

    def ledger(self):
        return A.apply(self.home, "read", {})


# ---- finding 1: the weekly threshold latches a study stop -----------------------------------------------------------
class WeeklyStop(Account):
    def test_reaching_the_weekly_limit_latches_a_stop(self):
        self.at(T0, seven=0.59)
        self.assertFalse(self.sched.pause()[2]["stop"])
        self.at(T0 + 10, seven=0.60)
        until, why, info = self.sched.pause()
        self.assertTrue(info["stop"])
        self.assertIn("weekly threshold", why)
        self.assertEqual(self.ledger()["study_stop"]["u"], 0.60)

    def test_no_reset_or_later_window_lifts_it(self):
        self.at(T0, seven=0.60)
        later = T0 + 6 * 86400                                           # past the weekly window's reset
        self.at(later, seven=0.02, seven_resets=later + 7 * 86400)
        until, why, info = self.sched.pause()
        self.assertTrue(info["stop"], "a new weekly window never authorizes a call")
        self.assertIsNone(until, "it is a stop, not a timed pause")
        problems = self.sched.check()
        self.assertTrue(any("release-study-stop" in p for p in problems), problems)

    def test_only_a_release_with_a_reason_lifts_it(self):
        self.at(T0, seven=0.60)
        with self.assertRaises(ValueError):
            A.apply(self.home, "release-study-stop", {"reason": " "})
        later = T0 + 6 * 86400
        self.at(later, seven=0.02, seven_resets=later + 7 * 86400)
        A.apply(self.home, "release-study-stop", {"reason": "Ephraiem's go (test)"})
        state = self.ledger()
        self.assertIsNone(state["study_stop"])
        self.assertEqual(state["study_releases"][0]["reason"], "Ephraiem's go (test)")
        self.now += 1                                                    # hosts see it once their cache is stale
        self.assertFalse(self.sched.pause()[2]["stop"])
        self.assertEqual(self.sched.check(), [])
        with self.assertRaises(ValueError):                              # nothing latched any more
            A.apply(self.home, "release-study-stop", {"reason": "again"})

    def test_a_release_covers_its_window_and_the_pause_still_holds_there(self):
        self.at(T0, seven=0.60)
        A.apply(self.home, "release-study-stop", {"reason": "go"})
        self.sched.ledger(fresh=True)
        self.at(T0 + 10, seven=0.62)                                    # the released window: no new latch
        until, why, info = self.sched.pause()
        self.assertFalse(info["stop"])
        self.assertIsNotNone(until, "the 0.60 pause holds to the weekly reset unless the limits are raised")
        later = T0 + 6 * 86400
        self.at(later, seven=0.61, seven_resets=later + 7 * 86400)       # a later window at the limit: latched again
        self.assertTrue(self.sched.pause()[2]["stop"])

    def test_the_release_is_an_operators_command(self):
        self.at(T0, seven=0.60)
        self.assertNotIn("release-study-stop", A.OPERATIONS)             # never run by a host's harness over ssh
        p = subprocess.run([sys.executable, "-B", str(HERE.parent / "account.py"), "--home-dir", str(self.home),
                            "--release-study-stop", "Ephraiem's go, 2026-11-08"], capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIsNone(json.loads(p.stdout)["study_stop"])


class WeeklyStopEndToEnd(TS.Plan):
    def test_passing_the_reset_never_authorizes_a_call_after_the_weekly_threshold(self):
        # Stage A has used 1 of its 5 weekly points when the account reaches 0.60, so its cap does not mask this.
        (self.host.bin / "claude-script.json").write_text(json.dumps({"seven_day_steps": [0.59, 0.60]}))
        record = self.go(TS.FIRST_A, [{"claude_calls": 4}])
        self.assertTrue(record["outcome"].startswith("stopped"), record["outcome"])
        self.assertIn("weekly threshold", (self.root / "STOP").read_text())
        self.assertEqual(len((self.host.bin / "claude-calls.jsonl").read_text().splitlines()), 2)
        self.assertEqual(ST.usage(self.ledger(), "A")[0]["seven_day"], 1.0)
        # the weekly window resets and the account reads low again; a person removes STOP: still no call
        later = time.time() + 6 * 86400
        SC.Scheduler(self.conf).publish(reading(later, seven=0.01, seven_resets=later + 7 * 86400), now=later)
        (self.root / "STOP").unlink()
        room = I.build(self.cfg, "after-reset", "claude", "off", model="fake-model")
        p = subprocess.run([str(room.bin_dir / "claude"), *TS.fakes_print()], input="x", capture_output=True,
                           text=True, env=I.environment(room, self.cfg, base=fakes.clean_environ()),
                           cwd=room.work_dir, timeout=60)
        self.assertEqual(p.returncode, 3)
        self.assertEqual(len((self.host.bin / "claude-calls.jsonl").read_text().splitlines()), 2)
        (self.root / "STOP").unlink()
        with self.assertRaises(I.Refusal) as e:                         # a resume or new run is refused too
            self.go(TS.FIRST_A, [{"claude_calls": 1}])
        self.assertIn("release-study-stop", str(e.exception))
        # the operator's release after his go: the next call goes through
        A.apply(self.conf["home_dir"], "release-study-stop", {"reason": "Ephraiem's go (test)"})
        SC.Scheduler(self.conf).ledger(fresh=True)                      # hosts see it within cache_seconds
        p = subprocess.run([str(room.bin_dir / "claude"), *TS.fakes_print()], input="x", capture_output=True,
                           text=True, env=I.environment(room, self.cfg, base=fakes.clean_environ()),
                           cwd=room.work_dir, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


# ---- finding 2: after a five-hour pause, resume only below 0.40 -----------------------------------------------------
class FiveHourResume(Account):
    def pause_at(self, t):
        self.now = t
        return self.sched.pause()

    def test_codex_probe_a_later_window_at_050_still_holds(self):
        self.at(T0, five=0.60, five_resets=T0 + 1000)
        until, _, info = self.pause_at(T0 + 10)
        self.assertEqual(until, T0 + 1000 + M.METER_MARGIN_S)
        self.assertTrue(info["recovering"])
        self.assertIsNone(self.pause_at(T0 + 1100)[0], "after the reset, a call may probe")
        self.at(T0 + 1100, five=0.50, five_resets=T0 + 19000)
        until, why, _ = self.pause_at(T0 + 1200)
        self.assertEqual(until, T0 + 19000 + M.METER_MARGIN_S)
        self.assertIn("after a five-hour pause", why)

    def test_exactly_040_holds_and_just_below_resumes(self):
        self.at(T0, five=0.60, five_resets=T0 + 1000)
        self.at(T0 + 1100, five=0.40, five_resets=T0 + 19000)
        self.assertEqual(self.pause_at(T0 + 1200)[0], T0 + 19000 + M.METER_MARGIN_S)
        self.assertIsNotNone(self.ledger()["recovery"])
        self.at(T0 + 19100, five=0.39, five_resets=T0 + 37000)
        until, why, info = self.pause_at(T0 + 19200)
        self.assertIsNone(until, why)
        self.assertIsNone(self.ledger()["recovery"])
        self.assertFalse(info["recovering"])

    def test_once_resumed_the_ordinary_limit_applies_again(self):
        self.at(T0, five=0.60, five_resets=T0 + 1000)
        self.at(T0 + 1100, five=0.20, five_resets=T0 + 19000)            # resumed
        self.at(T0 + 1200, five=0.55, five_resets=T0 + 19000)
        self.assertIsNone(self.pause_at(T0 + 1300)[0], "0.55 is under the 0.60 limit once resumed")
        self.at(T0 + 1400, five=0.60, five_resets=T0 + 19000)
        self.assertIsNotNone(self.pause_at(T0 + 1500)[0])
        self.assertIsNotNone(self.ledger()["recovery"])

    def test_a_lower_reading_of_the_paused_window_does_not_release_it(self):
        self.at(T0, five=0.60, five_resets=T0 + 1000)
        self.at(T0 + 50, five=0.30, five_resets=T0 + 1000)              # the same window, read lower
        self.assertIsNotNone(self.ledger()["recovery"])
        self.assertEqual(self.pause_at(T0 + 60)[0], T0 + 1000 + M.METER_MARGIN_S)


# ---- finding 3: the stage burn is the sum of positive increments ----------------------------------------------------
class Burn(unittest.TestCase):
    def add(self, burn, t, u, resets=5000.0):
        return M.meter_burn_add(burn, {"t": t, "five_hour": u, "five_hour_resets": resets})

    def test_codex_sequence_counts_every_rise(self):
        burn = M.meter_burn_empty()
        for i, u in enumerate((0.10, 0.25, 0.15, 0.25)):
            burn = self.add(burn, 100 + i, u)
        self.assertEqual(burn["total"]["five_hour"], 25.0)              # the high-water mark saw 15

    def test_any_arrival_order_counts_the_same(self):
        sequence = [(100, 0.10), (101, 0.25), (102, 0.15), (103, 0.25), (104, 0.30)]
        for order in itertools.permutations(sequence):
            burn = M.meter_burn_empty()
            for t, u in order:
                burn = self.add(burn, t, u)
            self.assertEqual(burn["total"]["five_hour"], 30.0, order)

    def test_a_duplicate_adds_nothing_and_a_new_window_counts_whole(self):
        burn = self.add(self.add(M.meter_burn_empty(), 100, 0.10), 101, 0.20)
        self.assertEqual(self.add(burn, 101, 0.20)["total"]["five_hour"], 10.0)
        self.assertEqual(self.add(burn, 9000, 0.05, resets=30000.0)["total"]["five_hour"], 15.0)

    def test_a_reading_too_late_to_place_is_bounded_from_above(self):
        burn = M.meter_burn_empty()
        for i in range(M.METER_BURN_HISTORY + 1):                       # the oldest kept reading is now t = 1001
            burn = self.add(burn, 1000 + i, 0.50)
        self.assertTrue(burn["dropped"]["five_hour"])
        late = self.add(burn, 10, 0.40)                                 # its predecessor is unknown
        self.assertEqual(late["total"]["five_hour"], 40.0 + 10.0)       # counted from zero, plus its rise to 0.50


class BurnEndToEnd(TS.Plan):
    def test_the_shim_stops_codex_sequence_at_the_cap(self):
        # .10 .25 .15 .25: 25 points of positive increments, over Stage A's 20. The high-water mark would read 15 and
        # let the fifth call run; the summed increments stop it before Claude Code is reached.
        os.environ["FAKE_PLANNED"] = "5"
        (self.host.bin / "claude-script.json").write_text(json.dumps({"five_hour_steps": [0.10, 0.25, 0.15, 0.25,
                                                                                          0.26]}))
        record = self.go(TS.FIRST_A, [{"claude_calls": 5}])
        self.assertIn("stage cap reached: five_hour", record["outcome"])
        self.assertEqual(len((self.host.bin / "claude-calls.jsonl").read_text().splitlines()), 4)
        self.assertEqual(ST.usage(self.ledger(), "A")[0]["five_hour"], 25.0)
        self.assertEqual(self.ledger()["meter"]["windows"]["five_hour"]["u"], 0.25, "the pause mark is unchanged")


if __name__ == "__main__":
    unittest.main()
