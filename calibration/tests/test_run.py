"""One run end to end against a fake probatio (fakes.py): the spec's checks, the dry run's memory list and its
refusal when it differs from the run's file, the decision after each attempt, and the pause and resume on the quota
with an injected clock, so the waits take no time. No test calls a model.

    python3 -m unittest discover -s calibration/tests
"""
import json
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fakes  # noqa: E402
import isolate as I  # noqa: E402
import quota as Q  # noqa: E402
import run as R  # noqa: E402

V3_PREFLIGHT = """probatio: claude (2.1.309 (Claude Code)), model claude-sonnet-5-5
  name 'Jane Doe', workdir /w
  smoke tier at --jobs 4, each item asked once per condition:
    sacramentum       21 items     42 calls   about 1 min
  memory files for the with-memory run:
    /w/CLAUDE.md
    /c/rules/x.md  (a rule: may load)
  labores and anulus: prompt arm neutral
"""


class Clock:
    """A clock that sleep() moves forward, starting at the real time (the fake probatio stamps real times)."""

    def __init__(self):
        import time
        self.offset, self.slept, self._time = 0.0, [], time.time

    def now(self):
        return self._time() + self.offset

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.offset += seconds


class Parsing(unittest.TestCase):
    def test_listed_memory_reads_v3s_preflight(self):
        self.assertEqual(R.listed_memory(V3_PREFLIGHT), [Path("/w/CLAUDE.md"), Path("/c/rules/x.md")])
        self.assertEqual(R.listed_memory("  memory files for the with-memory run: none found (the CLI may...)"), [])
        with self.assertRaises(I.Refusal):
            R.listed_memory("no list here")

    def test_preflight_problems(self):
        self.assertEqual(R.preflight_problems([Path("/w/CLAUDE.md")], [Path("/w/CLAUDE.md")]), [])
        self.assertTrue(R.preflight_problems([Path("/w/CLAUDE.md")], [Path("/w/CLAUDE.md"), Path("/u/CLAUDE.md")]))
        self.assertTrue(R.preflight_problems([], [Path("/w/CLAUDE.md")]))
        self.assertTrue(R.preflight_problems([Path("/w/CLAUDE.md")], []))

    def test_next_step(self):
        cases = [((0, 0, False, None, 1), "done"), ((1, 8, True, None, 1), "stop"),
                 ((1, 8, False, 123.0, 1), "pause"), ((0, 3, False, None, 1), "retry"),
                 ((1, 8, False, None, R.MAX_ATTEMPTS), "fail"), ((2, 0, False, None, 1), "fail"),
                 ((0, 0, False, None, 1, False), "retry"),                 # exit 0 but planned calls missing
                 ((0, 0, False, None, R.MAX_ATTEMPTS, False), "fail")]
        for args, want in cases:
            with self.subTest(args=args):
                self.assertEqual(R.next_step(*args), want)

    def test_call_errors_counts_each_calls_latest_record(self):
        host = fakes.Host()
        try:
            out = host.dir / "out"
            out.mkdir()
            rows = [{"condition": "with", "dataset": "d", "id": 1, "error": "timeout"},
                    {"condition": "with", "dataset": "d", "id": 2, "error": None},
                    {"condition": "with", "dataset": "d", "id": 1, "error": None}]
            (out / "replies.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows) + '{"cut')
            self.assertEqual(R.call_errors(out), (2, 0))
        finally:
            host.close()


class Specs(unittest.TestCase):
    def setUp(self):
        self.host = fakes.Host()

    def tearDown(self):
        self.host.close()

    def spec(self, **over):
        (self.host.dir / "mem.md").write_bytes(b"# Notes for Claude\n")
        data = dict({"run_id": "r1", "agent": "claude", "model": "claude-sonnet-5-5", "memory": "on",
                     "memory_file": "mem.md", "probatio_args": ["--target", "Kelthorne Labs"]}, **over)
        path = self.host.dir / "spec.json"
        path.write_text(json.dumps(data))
        return path

    def test_defaults_and_memory_bytes(self):
        spec = R.load_spec(self.spec())
        self.assertEqual((spec["jobs"], spec["cli"], spec["effort"]), (4, "v4", "medium"))
        self.assertEqual(spec["memory_bytes"], b"# Notes for Claude\n")

    def test_refusals(self):
        for over in ({"model": ""}, {"cli": "v5"}, {"jobs": 0}, {"probatio_args": ["--out", "/x"]},
                     {"probatio_args": ["--workdir=/home"]}, {"probatio_args": ["--memory", "both"]}):
            with self.subTest(over=over), self.assertRaises(I.Refusal):
                R.load_spec(self.spec(**over))

    def test_command(self):
        spec = R.load_spec(self.spec())
        cfg = I.load_local(self.host.local)
        room = I.build(cfg, "r1", "claude", "on", I.EFFORT, spec["memory_bytes"])
        cmd = R.command(spec, room, cfg, Path("/o"))
        self.assertEqual(cmd[2:4], ["--agent", "claude"])
        self.assertEqual(cmd[cmd.index("--memory") + 1], "on")
        self.assertEqual(cmd[-2:], ["--target", "Kelthorne Labs"])
        self.assertNotIn("--memory", R.command(dict(spec, cli="v3"), room, cfg, Path("/o")))

    def test_v4_gets_the_harness_owned_auto_memory_and_effort(self):
        spec = R.load_spec(self.spec(effort="low"))
        cfg = I.load_local(self.host.local)
        room = I.build(cfg, "r1", "claude", "on", "low", spec["memory_bytes"])
        cmd = R.command(spec, room, cfg, Path("/o"))
        self.assertEqual(cmd[cmd.index("--auto-memory") + 1], "off")
        self.assertEqual(cmd[cmd.index("--effort") + 1], "low")
        v3 = R.command(dict(spec, cli="v3"), room, cfg, Path("/o"))
        self.assertNotIn("--auto-memory", v3)
        self.assertEqual(v3[v3.index("--dataset") + 1], "all")         # v3 is chosen by one of its own flags
        given = R.command(dict(spec, cli="v3", probatio_args=["--dataset", "labores"]), room, cfg, Path("/o"))
        self.assertEqual(given.count("--dataset"), 1)
        with self.assertRaises(I.Refusal):
            R.load_spec(self.spec(effort="auto"))                       # probatio v4 has no auto effort
        settings = R.preflight_settings("probatio v4: claude (2.1.309), model m, effort high\n"
                                        "  standard tier, memory on, at --jobs 4:\n    all   3 items   3 calls   x\n")
        self.assertEqual(settings["effort"], "high")
        self.assertTrue(any("effort" in p for p in R.settings_problems(dict(spec, model="m", jobs=4), settings)))


class Runs(unittest.TestCase):
    def setUp(self):
        self.host = fakes.Host()
        self.cfg = I.load_local(self.host.local)
        self.clock = Clock()
        self.plan = self.host.dir / "plan.json"
        self.root = Path(self.cfg["root"])
        self.root.mkdir(parents=True)
        env = dict(fakes.clean_environ(), FAKE_PLAN=str(self.plan), FAKE_QUOTA=str(self.root / "quota.jsonl"),
                   FAKE_STOP=str(self.root / "STOP"))
        self.patch = unittest.mock.patch.dict(os.environ, env, clear=True)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.host.close()

    def spec(self, memory="on"):
        return {"run_id": "r1", "agent": "claude", "model": "claude-sonnet-5-5", "memory": memory, "jobs": 4,
                "cli": "v4", "effort": "medium", "probatio_args": [], "memory_bytes": b"# Notes for Claude\n"}

    def go(self, steps, spec=None):
        self.plan.write_text(json.dumps(steps))
        return R.run(spec or self.spec(), self.cfg, sleep=self.clock.sleep, now=self.clock.now, log=lambda m: None)

    def test_clean_run_records_the_memory_hash(self):
        record = self.go([{"records": [{"condition": "with", "class": "I", "id": 1, "error": None}], "rc": 0}])
        self.assertEqual(record["outcome"], "done")
        self.assertEqual(len(record["memory_sha256"]), 64)
        saved = json.loads((Path(self.cfg["results_dir"]) / "r1" / "calibration.json").read_text())
        self.assertEqual(saved["memory_sha256"], record["memory_sha256"])
        self.assertEqual(saved["effort"], "medium")
        self.assertEqual(self.clock.slept, [])

    def test_pause_until_reset_then_resume(self):
        err = {"condition": "with", "class": "I", "id": 2, "error": "probatio-cal: paused for quota"}
        steps = [{"quota": {"status": "allowed", "five_hour": 0.86, "five_hour_resets": 600, "seven_day": 0.2,
                            "seven_day_resets": 86400}, "records": [err] * 8, "rc": 1},
                 {"records": [dict(err, error=None)], "rc": 0}]
        record = self.go(steps)
        self.assertEqual(record["outcome"], "done")
        self.assertEqual([a["step"] for a in record["attempts"]], ["pause", "done"])
        self.assertEqual(len(record["pauses"]), 1)
        self.assertIn("five_hour 0.86", record["pauses"][0]["why"])
        self.assertAlmostEqual(sum(self.clock.slept), 600 + Q.RESUME_MARGIN_S, delta=5)

    def test_a_pause_already_in_force_waits_before_the_first_attempt(self):
        Q.append(self.root / "quota.jsonl", Q.rejected(self.clock.now(), "elsewhere"))
        record = self.go([{"records": [{"condition": "with", "class": "I", "id": 1, "error": None}], "rc": 0}])
        self.assertEqual(record["outcome"], "done")
        self.assertAlmostEqual(sum(self.clock.slept), Q.REJECTED_BACKOFF_S + Q.RESUME_MARGIN_S, delta=5)

    def test_long_pauses_sleep_in_steps(self):
        Q.append(self.root / "quota.jsonl", Q.rejected(self.clock.now(), "elsewhere", self.clock.now() + 3 * 86400))
        self.go([{"records": [{"condition": "with", "class": "I", "id": 1, "error": None}], "rc": 0}])
        self.assertTrue(all(s <= R.STOP_POLL_S for s in self.clock.slept))
        self.assertAlmostEqual(sum(self.clock.slept), 3 * 86400 + Q.RESUME_MARGIN_S, delta=5)

    def test_the_weekly_threshold_stops_the_run_instead_of_waiting(self):
        # CALIBRATION 7: at seven-day 0.60 the study stops and waits for Ephraiem's go; it does not sleep to the reset
        Q.append(self.root / "quota.jsonl", Q.reading({"status": "allowed", "unifiedWindows": {"seven_day": {
            "utilization": 0.81, "resetsAt": self.clock.now() + 3 * 86400}}}, self.clock.now()))
        record = self.go([{"records": [{"condition": "with", "class": "I", "id": 1, "error": None}], "rc": 0}])
        self.assertTrue(record["outcome"].startswith("stopped"), record["outcome"])
        self.assertIn("weekly threshold", (self.root / "STOP").read_text())
        self.assertEqual(record["attempts"], [])
        self.assertFalse(Path(str(self.plan) + ".count").exists(), "probatio never ran")

    def test_failed_calls_are_retried_then_the_run_fails(self):
        bad = {"condition": "with", "class": "I", "id": 1, "error": "timeout"}
        record = self.go([{"records": [bad], "rc": 0}])
        self.assertEqual(record["outcome"], "failed")
        self.assertEqual(len(record["attempts"]), R.MAX_ATTEMPTS)
        self.assertEqual(len(self.clock.slept), R.MAX_ATTEMPTS - 1)

    def test_probatio_refusing_to_start_fails_at_once(self):
        record = self.go([{"records": [], "rc": 2}])
        self.assertEqual((record["outcome"], len(record["attempts"])), ("failed", 1))

    def test_hard_stop_ends_the_run(self):
        record = self.go([{"records": [{"condition": "with", "class": "I", "id": 1, "error": "x"}], "rc": 1,
                           "stop": True}])
        self.assertTrue(record["outcome"].startswith("stopped"))

    def test_a_room_spoiled_between_attempts_is_refused_before_the_rerun(self):
        bad = {"condition": "with", "class": "I", "id": 1, "error": "timeout"}
        plan = [{"records": [bad], "rc": 0}]
        self.plan.write_text(json.dumps(plan))

        def spoil(seconds):                                 # during the wait, something plants a memory file above
            (self.root / "work" / "CLAUDE.md").write_text("persona")
            self.clock.sleep(seconds)

        record = R.run(self.spec(), self.cfg, sleep=spoil, now=self.clock.now, log=lambda m: None)
        self.assertTrue(record["outcome"].startswith("refused"))
        self.assertIn("CLAUDE.md", record["outcome"])
        self.assertEqual(len(record["attempts"]), 1)

    def test_preflight_with_an_extra_memory_file_is_refused(self):
        os.environ["FAKE_EXTRA_MEMORY"] = "/home/someone/.claude/CLAUDE.md"
        with self.assertRaises(I.Refusal) as e:
            self.go([{"records": [], "rc": 0}])
        self.assertIn("/home/someone/.claude/CLAUDE.md", str(e.exception))
        self.assertFalse((Path(self.cfg["results_dir"]) / "r1" / "harness.log").exists())   # nothing ran

    def test_off_run_expects_no_memory_in_the_preflight(self):
        with self.assertRaises(I.Refusal):                  # the fake lists the file, but an off run loads none
            self.go([{"records": [], "rc": 0}], spec=self.spec(memory="off"))
        os.environ["FAKE_NO_MEMORY"] = "1"
        ok = [{"records": [{"condition": "without", "class": "I", "id": 1, "error": None}], "rc": 0}]
        self.assertEqual(self.go(ok, spec=self.spec(memory="off"))["outcome"], "done")

    def test_a_resumed_run_keeps_its_history_and_its_memory(self):
        self.go([{"records": [{"condition": "with", "class": "I", "id": 1, "error": None}], "rc": 0}])
        record = self.go([{"records": [], "rc": 0}])
        self.assertEqual(len(record["attempts"]), 2)
        with self.assertRaises(I.Refusal):
            self.go([{"records": [], "rc": 0}], spec=dict(self.spec(), memory_bytes=b"# changed\n"))

    def test_an_api_key_refuses_before_anything_runs(self):
        os.environ["ANTHROPIC_API_KEY"] = "test-api-key-not-real"
        with self.assertRaises(I.Refusal):
            self.go([{"records": [], "rc": 0}])
        self.assertFalse(Path(str(self.plan) + ".count").exists())


if __name__ == "__main__":
    unittest.main()
