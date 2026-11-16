"""Regression tests for Codex's verification of W4 part 1 (bb38240): one test per probe in its probes.py, plus the
fixtures its findings asked for. Each failed on bb38240 and passes on the fixed harness. Fake CLIs only (fakes.py),
a clearly fake token, and no model or network call.

    python3 -m unittest discover -s calibration/tests
"""
import json
import os
import subprocess
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import fakes  # noqa: E402
import isolate as I  # noqa: E402
import quota as Q  # noqa: E402
import run as R  # noqa: E402

PRINT = ["-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence", "--settings",
         '{"disableAllHooks":true}', "--output-format", "json", "--model", "fake-model"]
OK_RECORD = {"condition": "with", "class": "I", "id": 1, "error": None}


class Base(unittest.TestCase):
    def setUp(self):
        self.host = fakes.Host()
        self.cfg = I.load_local(self.host.local)
        self.patch = unittest.mock.patch.dict(os.environ, fakes.clean_environ(), clear=True)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.host.close()

    def cli(self, room, args, which="claude", extra=None, cwd=None):
        env = I.environment(room, self.cfg, base=fakes.clean_environ())
        env.update(extra or {})
        return subprocess.run([str(room.bin_dir / which), *args], input="synthetic prompt", capture_output=True,
                              text=True, env=env, cwd=cwd or room.work_dir, timeout=30)

    def problems(self, room):
        return I.problems(room, self.cfg, I.environment(room, self.cfg), fakes.clean_environ())

    def calls(self):
        path = self.host.bin / "claude-calls.jsonl"
        return path.read_text().splitlines() if path.exists() else []


class Finding1Memory(Base):
    def test_changed_memory_is_caught(self):                             # probe changed_memory
        room = I.build(self.cfg, "changed", "claude", "on", memory_bytes=b"# SYNTHETIC ORIGINAL\n")
        room.memory_path.write_text("# OPERATOR-DECOY-MEMORY\n")
        self.assertTrue(any("sha256" in p for p in self.problems(room)))
        p = self.cli(room, PRINT)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.calls(), [], "no subject call after the memory changed")

    def test_external_memory_symlink_is_caught(self):                    # probe external_memory_symlink
        room = I.build(self.cfg, "linked", "claude", "on", memory_bytes=None)
        decoy = self.host.dir / "OPERATOR-DECOY.md"
        decoy.write_text("# Operator persona decoy\n")
        room.memory_path.symlink_to(decoy)
        self.assertTrue(self.problems(room))

    def test_approved_file_swapped_for_a_symlink_is_caught(self):
        room = I.build(self.cfg, "swapped", "claude", "on", memory_bytes=b"# synthetic\n")
        I.check(room, self.cfg, I.environment(room, self.cfg), fakes.clean_environ())
        decoy = self.host.dir / "decoy.md"
        decoy.write_bytes(b"# synthetic\n")                            # same bytes, but not the room's own file
        room.memory_path.unlink()
        room.memory_path.symlink_to(decoy)
        self.assertTrue(any("symlink" in p for p in self.problems(room)))

    def test_an_import_in_a_memory_file_is_refused(self):
        with self.assertRaises(I.Refusal):
            I.build(self.cfg, "imports", "claude", "on", memory_bytes=b"# notes\n@~/.claude/CLAUDE.md\n")


class Finding2CallContext(Base):
    def test_dirty_room_after_check_is_refused_per_call(self):           # probe dirty_room_per_call
        room = I.build(self.cfg, "dirty-after-check", "claude", "on", memory_bytes=b"# synthetic\n")
        I.check(room, self.cfg, I.environment(room, self.cfg), fakes.clean_environ())
        (room.root / "work" / "CLAUDE.md").write_text("# PARENT-PERSONA-DECOY\n")
        (room.config_dir / "CLAUDE.md").write_text("# CONFIG-PERSONA-DECOY\n")
        p = self.cli(room, PRINT)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertTrue(room.stop_path.exists())

    def test_a_call_from_another_directory_is_refused(self):
        room = I.build(self.cfg, "elsewhere", "claude", "on", memory_bytes=b"# synthetic\n")
        p = self.cli(room, PRINT, cwd=self.host.dir)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_codex_foreign_home_is_refused(self):                        # probe codex_foreign_home_and_effort
        room = I.build(self.cfg, "codex-env", "codex", "off", memory_bytes=b"# synthetic\n")
        foreign = self.host.dir / "foreign-codex"
        foreign.mkdir()
        (foreign / "AGENTS.md").write_text("# GLOBAL-PERSONA-DECOY\n")
        p = self.cli(room, ["exec", "-c", 'model_reasoning_effort="low"', "-"], which="codex",
                     extra={"CODEX_HOME": str(foreign)})
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse((self.host.bin / "codex-calls.jsonl").exists(), "the foreign home never reached Codex")


class Finding3Protocol(Base):
    def script(self, body):
        fakes.script(self.host.claude, "#!PYTHON\nimport json, time\n" + body)

    def test_no_init_and_no_meter_is_unverified(self):                    # probe missing_init_and_quota
        room = I.build(self.cfg, "missing-events", "claude", "on", memory_bytes=b"# synthetic\n")
        self.script("print(json.dumps({'type': 'result', 'is_error': False, 'result': 'OK WITHOUT INIT OR METER'}))\n")
        p = self.cli(room, PRINT)
        self.assertNotEqual(p.returncode, 0)
        self.assertNotEqual(json.loads(p.stdout).get("result"), "OK WITHOUT INIT OR METER")
        self.assertTrue(json.loads(p.stdout)["is_error"])
        self.assertEqual(Q.latest(room.quota_path)["status"], "unverified")

    def test_out_of_order_init_missing_meter_and_malformed_meter_are_unverified(self):
        init = "{'type': 'system', 'subtype': 'init', 'apiKeySource': 'none', 'model': 'fake-model', " \
               "'claude_code_version': '2.1.309'}"
        result = "{'type': 'result', 'is_error': False, 'result': 'OK', 'modelUsage': {'fake-model': {}}}"
        good_meter = ("{'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed', 'unifiedWindows': "
                      "{'five_hour': {'utilization': 0.1, 'resetsAt': time.time() + 3600}, "
                      "'seven_day': {'utilization': 0.1, 'resetsAt': time.time() + 9e4}}}}")
        bad_meter = ("{'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed', 'unifiedWindows': "
                     "{'five_hour': {'utilization': 0.1}}}}")
        cases = {"result before init": [result, init, good_meter], "no meter": [init, result],
                 "malformed meter": [init, bad_meter, result]}
        for i, (name, events) in enumerate(cases.items()):
            with self.subTest(case=name):
                room = I.build(self.cfg, f"proto-{i}", "claude", "on", memory_bytes=b"# synthetic\n")
                self.script("".join(f"print(json.dumps({e}))\n" for e in events))
                p = self.cli(room, PRINT)
                self.assertNotEqual(p.returncode, 0, name)
                self.assertTrue(json.loads(p.stdout)["is_error"], name)

    def test_unverified_calls_hold_and_then_stop(self):
        room = I.build(self.cfg, "unverified-streak", "claude", "on", memory_bytes=b"# synthetic\n")
        self.script("print(json.dumps({'type': 'result', 'is_error': False, 'result': 'x'}))\n")
        first = self.cli(room, PRINT)
        self.assertNotEqual(first.returncode, 0)
        second = self.cli(room, PRINT)                                  # held: no call reaches the CLI
        self.assertIn("paused", json.loads(second.stdout)["result"])
        for _ in range(2):                                              # two more unverified readings: a stop
            Q.append(room.quota_path, Q.unverified(time.time(), "r", "no init event"))
        third = self.cli(room, PRINT)
        self.assertNotEqual(third.returncode, 0)
        self.assertTrue(room.stop_path.exists())


class Finding4Quota(unittest.TestCase):
    def setUp(self):
        self.host = fakes.Host()
        self.host.root.mkdir()
        self.path = self.host.root / "quota.jsonl"
        self.now = time.time()

    def tearDown(self):
        self.host.close()

    def info(self, **windows):
        return {"status": "allowed", "unifiedWindows": windows}

    def test_a_partial_reading_does_not_clear_a_weekly_hold(self):       # probe quota_fail_open, part 1
        Q.append(self.path, Q.reading(self.info(seven_day={"utilization": .95, "resetsAt": self.now + 86400}),
                                      self.now))
        self.assertIsNotNone(Q.pause_state(self.path, self.now)[0])
        Q.append(self.path, Q.reading({"status": "allowed"}, self.now + 1))
        self.assertIsNotNone(Q.pause_state(self.path, self.now + 1)[0])

    def test_a_high_meter_without_reset_holds(self):                     # probe quota_fail_open, part 2
        row = Q.reading(self.info(five_hour={"utilization": .99}), self.now)
        self.assertIsNotNone(Q.pause_until(row, self.now)[0])

    def test_complete_high_then_sparse_rejection_or_stale_reading_keeps_the_hold(self):
        high = Q.reading(self.info(five_hour={"utilization": .9, "resetsAt": self.now + 3600},
                                   seven_day={"utilization": .3, "resetsAt": self.now + 9e4}), self.now)
        stale = Q.reading(self.info(five_hour={"utilization": .1, "resetsAt": self.now - 20000},
                                    seven_day={"utilization": .1, "resetsAt": self.now - 20000}), self.now + 5)
        lower = Q.reading(self.info(five_hour={"utilization": .2, "resetsAt": self.now + 3600},
                                    seven_day={"utilization": .3, "resetsAt": self.now + 9e4}), self.now - 10)
        for later in (Q.rejected(self.now + 2), stale, lower):
            with self.subTest(later=later.get("status"), t=later["t"]):
                state = Q.fold([high, later])
                self.assertIsNotNone(Q.meter.meter_hold(state, self.now + 6, Q.DEFAULT_LIMITS)[0])

    def test_a_valid_new_window_clears_the_hold(self):
        high = Q.reading(self.info(five_hour={"utilization": .9, "resetsAt": self.now + 3600},
                                   seven_day={"utilization": .3, "resetsAt": self.now + 9e4}), self.now)
        fresh = Q.reading(self.info(five_hour={"utilization": .05, "resetsAt": self.now + 3600 + 18000},
                                    seven_day={"utilization": .3, "resetsAt": self.now + 9e4}), self.now + 3700)
        state = Q.fold([high, fresh])
        self.assertIsNone(Q.meter.meter_hold(state, self.now + 3700, Q.DEFAULT_LIMITS)[0])


class Finding5Redaction(Base):
    def test_token_never_reaches_shim_output(self):                       # probe fake_token_output
        room = I.build(self.cfg, "token-output", "claude", "on", memory_bytes=b"# synthetic\n")
        fakes.script(self.host.claude, '''#!PYTHON
import json, os, sys, time
t = os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
print(json.dumps({"type": "system", "subtype": "init", "apiKeySource": "none", "model": "fake-model",
                  "claude_code_version": "2.1.309"}))
print("FAKE diagnostic " + t, file=sys.stderr)
print(json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {
    "five_hour": {"utilization": 0.1, "resetsAt": time.time() + 3600},
    "seven_day": {"utilization": 0.1, "resetsAt": time.time() + 9e4}}}}))
print(json.dumps({"type": "result", "is_error": True, "result": "FAKE failure " + t}))
''')
        p = self.cli(room, PRINT)
        self.assertNotIn(fakes.TOKEN, p.stdout)
        self.assertNotIn(fakes.TOKEN, p.stderr)
        self.assertEqual(self.host.files_containing(fakes.TOKEN), [])

    def test_token_never_reaches_the_harness_log(self):                   # probe fake_token_in_harness_log
        diagnostic = fakes.FAKE_CLAUDE.replace(
            'print(json.dumps(dict({"type": "result",',
            'print("FAKE diagnostic " + os.environ["CLAUDE_CODE_OAUTH_TOKEN"], file=sys.stderr)\n'
            'print(json.dumps(dict({"type": "result",'.replace("\n", "\n"))
        fakes.script(self.host.claude, diagnostic)
        fakes.script(self.host.probatio, '''#!PYTHON
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
opt = lambda name: args[args.index(name) + 1]
if "--dry-run" in args:
    print("probatio: claude (2.1.309 (Claude Code)), model " + opt("--model"))
    print("  standard tier at --jobs " + opt("--jobs") + ", each item asked once:")
    print("    fake         1 items     1 calls   under a minute")
    print("  memory files for the with-memory run:\\n    " + str(Path.cwd() / "CLAUDE.md"))
else:
    subprocess.run(["claude", "-p", "--output-format", "json", "--model", opt("--model")], input="fake prompt",
                   text=True)
    print("probatio echo " + os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", ""), file=sys.stderr)
    out = Path(opt("--out")); out.mkdir(parents=True, exist_ok=True)
    (out / "replies.jsonl").write_text(json.dumps({"condition": "with", "class": "I", "id": 1, "error": None}) + "\\n")
''')
        spec = {"run_id": "log-diagnostic", "agent": "claude", "model": "fake-model", "memory": "on", "jobs": 1,
                "cli": "v4", "effort": "medium", "probatio_args": [], "memory_bytes": b"# synthetic\n"}
        record = R.run(spec, self.cfg, sleep=lambda n: None, log=lambda m: None)
        log = (Path(self.cfg["results_dir"]) / spec["run_id"] / "harness.log").read_text()
        self.assertIn("FAKE diagnostic", log)
        self.assertNotIn(fakes.TOKEN, log)
        self.assertEqual(record["outcome"], "done")
        self.assertEqual(self.host.files_containing(fakes.TOKEN), [])


class Finding6Arguments(Base):
    def spec_file(self, **over):
        data = dict({"run_id": "flag-alias", "agent": "claude", "model": "fake-model-REGISTERED", "memory": "off",
                     "cli": "v3", "jobs": 1, "probatio_args": ["--name", "Synthetic Person"]}, **over)
        path = self.host.dir / "spec.json"
        path.write_text(json.dumps(data))
        return path

    def test_abbreviations_of_harness_flags_are_refused(self):           # probe harness_flag_abbreviations
        for args in (["--mod", "fake-model-OVERRIDE"], ["--jo", "123"], ["--work", "/tmp"], ["--mem", "both"],
                     ["-m", "x"], ["--name", "A", "--name", "B"], ["--name", "--model"], ["--name=X", "--o", "/y"]):
            with self.subTest(args=args), self.assertRaises(I.Refusal):
                R.load_spec(self.spec_file(probatio_args=["--name", "Synthetic Person"] + args
                                           if args[0] != "--name" else args))

    def test_the_real_v3_parser_sees_the_registered_model_and_jobs(self):
        sys.path.insert(0, str(HERE.parent.parent))
        import probatio as P
        spec = R.load_spec(self.spec_file())
        room = I.build(self.cfg, spec["run_id"], spec["agent"], spec["memory"])
        parsed = P.parse_args(R.command(spec, room, self.cfg, self.host.root / "out")[2:])
        self.assertEqual((parsed.model, parsed.jobs), ("fake-model-REGISTERED", 1))

    def test_preflight_refuses_a_model_or_jobs_the_spec_did_not_register(self):
        plan = self.host.dir / "plan.json"
        plan.write_text(json.dumps([{"records": [OK_RECORD], "rc": 0}]))
        spec = {"run_id": "settings", "agent": "claude", "model": "fake-model", "memory": "on", "jobs": 1,
                "cli": "v4", "effort": "medium", "probatio_args": [], "memory_bytes": b"# synthetic\n"}
        for var, value in (("FAKE_DRY_MODEL", "another-model"), ("FAKE_DRY_JOBS", "9")):
            with self.subTest(var=var), unittest.mock.patch.dict(os.environ, {var: value, "FAKE_PLAN": str(plan)}):
                with self.assertRaises(I.Refusal):
                    R.run(dict(spec, run_id=f"settings-{var.lower()}"), self.cfg, sleep=lambda n: None,
                          log=lambda m: None)


class Finding7Resume(Base):
    def go(self, spec, steps):
        plan = self.host.dir / "plan.json"
        plan.write_text(json.dumps(steps))
        env = {"FAKE_PLAN": str(plan), "FAKE_QUOTA": str(self.host.root / "quota.jsonl"),
               "FAKE_STOP": str(self.host.root / "STOP")}
        with unittest.mock.patch.dict(os.environ, env):
            return R.run(spec, self.cfg, sleep=lambda n: None, log=lambda m: None)

    def test_a_resume_with_another_spec_is_refused_and_the_registration_kept(self):   # probe resume_changed_spec
        spec = {"run_id": "resume", "agent": "claude", "model": "fake-model-A", "memory": "on", "jobs": 1,
                "cli": "v4", "effort": "medium", "probatio_args": ["--target", "Original Target"],
                "memory_bytes": b"# synthetic\n"}
        self.assertEqual(self.go(spec, [{"records": [OK_RECORD], "rc": 0}])["outcome"], "done")
        with self.assertRaises(I.Refusal):
            self.go(dict(spec, model="fake-model-B", probatio_args=["--target", "Other Target"]),
                    [{"records": [OK_RECORD], "rc": 0}])
        out = Path(self.cfg["results_dir"]) / "resume"
        record = json.loads((out / "calibration.json").read_text())
        self.assertEqual(record["spec"]["model"], "fake-model-A")
        self.assertEqual(record["spec"]["probatio_args"], ["--target", "Original Target"])
        self.assertEqual(len(record["attempts"]), 1)
        self.assertEqual(len((out / "rejected-resumes.jsonl").read_text().splitlines()), 1)

    def test_a_zero_exit_with_no_records_is_not_done(self):
        spec = {"run_id": "empty", "agent": "claude", "model": "fake-model", "memory": "on", "jobs": 1,
                "cli": "v4", "effort": "medium", "probatio_args": [], "memory_bytes": b"# synthetic\n"}
        self.assertNotEqual(self.go(spec, [{"records": [], "rc": 0}])["outcome"], "done")


class Finding8Stop(Base):
    def test_stop_during_a_quota_wait_is_seen_at_once(self):            # probe stop_during_quota_wait
        self.host.root.mkdir()
        plan = self.host.dir / "plan.json"
        plan.write_text(json.dumps([{"records": [OK_RECORD], "rc": 0}]))
        clock, sleeps = [time.time()], []
        Q.append(self.host.root / "quota.jsonl", Q.reading({"status": "allowed", "unifiedWindows": {
            "seven_day": {"utilization": .9, "resetsAt": clock[0] + 86400},
            "five_hour": {"utilization": .1, "resetsAt": clock[0] + 3600}}}, clock[0]))

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds
            (self.host.root / "STOP").write_text("Synthetic operator stop during pause\n")

        spec = {"run_id": "wait-stop", "agent": "claude", "model": "fake-model", "memory": "on", "jobs": 1,
                "cli": "v4", "effort": "medium", "probatio_args": [], "memory_bytes": b"# synthetic\n"}
        env = {"FAKE_PLAN": str(plan), "FAKE_QUOTA": str(self.host.root / "quota.jsonl"),
               "FAKE_STOP": str(self.host.root / "STOP")}
        with unittest.mock.patch.dict(os.environ, env):
            record = R.run(spec, self.cfg, sleep=sleep, now=lambda: clock[0], log=lambda m: None)
        self.assertTrue(record["outcome"].startswith("stopped"))
        self.assertLessEqual(sum(sleeps), 60)
        self.assertEqual(record["attempts"], [])


if __name__ == "__main__":
    unittest.main()
