"""Regression tests for Codex's third re-verification of the clean room (4e5bd0e, three medium findings): a long secret
cut by a diagnostic before redaction, an oversized integer in quota telemetry, and an unregistered MCP server in a
Codex call. Each probe test failed on 4e5bd0e and passes now. Fake CLIs only (fakes.py), wholly synthetic secrets, and
no model or network call.

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
import account as A  # noqa: E402
import fakes  # noqa: E402
import isolate as I  # noqa: E402
import meter as M  # noqa: E402
import quota as Q  # noqa: E402
import redact  # noqa: E402
import scheduler as SC  # noqa: E402
import shim as S  # noqa: E402
import test_regressions as TR  # noqa: E402
import test_world_pins as WP  # noqa: E402

# A wholly synthetic 144-character secret, far longer than any diagnostic's cut.
LONG = "SYNTHETIC-LONG-SECRET-" + "".join(chr(ord("a") + i % 26) for i in range(122))
assert len(LONG) == 144


def fragments(text, size=12):
    """Every piece of LONG at least size characters long that appears in text."""
    return [LONG[i:i + size] for i in range(len(LONG) - size + 1) if LONG[i:i + size] in text]


class LongSecret(TR.Base):
    """Finding 1: every output is redacted whole before any slice, tail or repr."""

    def setUp(self):
        super().setUp()
        self.host.secrets.write_text(f"export {fakes.TOKEN_NAME}='{LONG}'\n")

    def test_a_version_line_holding_the_secret_is_redacted_before_it_is_cut(self):   # probe version truncation
        fakes.script(self.host.claude, '#!PYTHON\nimport os, sys\nprint(os.environ["CLAUDE_CODE_OAUTH_TOKEN"])\n')
        p = subprocess.run([sys.executable, "-B", str(HERE.parent / "isolate.py"), "--check", "--local",
                            str(self.host.local)], capture_output=True, text=True, env=fakes.clean_environ(),
                           timeout=60)
        self.assertEqual(p.returncode, 2)
        self.assertIn("not Claude Code", p.stderr)
        self.assertEqual(fragments(p.stdout + p.stderr), [])

    def test_the_codex_version_line_too(self):
        fakes.script(self.host.codex, '#!PYTHON\nimport os\nprint("codex-cli " + os.environ["CLAUDE_CODE_OAUTH_TOKEN"])\n')
        room = I.build(self.cfg, "codex-version", "codex", "on", memory_bytes=b"# synthetic\n")
        found = I.binary_problems(self.cfg, I.environment(room, self.cfg), "codex")
        self.assertTrue(any("not codex-cli" in m for m in found))
        self.assertEqual(fragments(" ".join(found)), [])

    def test_a_canary_stderr_tail_is_redacted_whole(self):
        room = I.build(self.cfg, "canary-tail", "claude", "on", memory_bytes=b"# synthetic\n", model="fake-model")
        tail = "x" * 50 + LONG + "y" * 200                    # the last 300 characters cut the secret's head off
        done = subprocess.CompletedProcess([], 1, stdout="not json", stderr=tail)
        with unittest.mock.patch.object(I.subprocess, "run", return_value=done):
            with self.assertRaises(I.Refusal) as e:
                I.canary_call(room, self.cfg, "fake-model")
        self.assertIn("gave no result", str(e.exception))
        self.assertEqual(fragments(str(e.exception)), [])

    def test_a_failed_preflight_tail_is_redacted_whole(self):
        fakes.script(self.host.probatio, '#!PYTHON\nimport os, sys\n'
                     'sys.stderr.write(os.environ["CLAUDE_CODE_OAUTH_TOKEN"] + "z" * 300)\nsys.exit(2)\n')
        spec = self.host.dir / "spec.json"
        spec.write_text(json.dumps({"run_id": "preflight-tail", "agent": "claude", "model": "fake-model",
                                    "memory": "off", "jobs": 1}))
        p = subprocess.run([sys.executable, "-B", str(HERE.parent / "run.py"), str(spec), "--local",
                            str(self.host.local)], capture_output=True, text=True, env=fakes.clean_environ(),
                           timeout=60)
        self.assertEqual(p.returncode, 2)
        self.assertIn("dry-run failed", p.stderr)
        self.assertEqual(fragments(p.stdout + p.stderr), [])
        self.assertEqual([f for f in self.host.dir.rglob("*") if f.is_file() and f != self.host.secrets
                          and fragments(f.read_text(errors="replace"))], [])

    def test_an_unreachable_ledgers_stderr_tail_is_redacted_whole(self):
        down = fakes.script(self.host.bin / "ssh-leaky", '#!PYTHON\nimport os, sys\n'
                            'sys.stderr.write("x" * 20 + os.environ["CLAUDE_CODE_OAUTH_TOKEN"] + "y" * 150)\n'
                            'sys.exit(255)\n')
        conf = {"home": "user@fakehome", "home_dir": "/nowhere", "ssh": str(down), "ssh_timeout": 10}
        with unittest.mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": LONG}):
            with self.assertRaises(A.Unreachable) as e:
                A.call(conf, "read")
        self.assertIn("exited 255", str(e.exception))
        self.assertEqual(fragments(str(e.exception)), [])

    def test_clip_redacts_before_it_cuts(self):
        scrub = redact.Redactor({"X_TOKEN": LONG})
        text = "head " + LONG + " tail"
        self.assertEqual(fragments(scrub.clip(text, 40)), [])
        self.assertEqual(fragments(scrub.clip(text, 60, tail=True)), [])
        self.assertTrue(fragments(scrub(text[:40])), "cutting first would have left a prefix")


OVERSIZED_CLAUDE = '''#!PYTHON
import json, sys, time
from pathlib import Path
args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("2.1.309 (Claude Code)"); sys.exit(0)
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "oauth_token"})); sys.exit(0)
script = json.loads((Path(__file__).resolve().parent / "oversized.json").read_text())
now = time.time()
windows = {"five_hour": {"utilization": 0.1, "resetsAt": now + 3600}, "seven_day": {"utilization": 0.1, "resetsAt": now + 9e4}}
windows[script["window"]][script["key"]] = 10 ** 1000
print(json.dumps({"type": "system", "subtype": "init", "apiKeySource": "none", "model": "fake-model",
                  "claude_code_version": "2.1.309"}))
print(json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": windows}}))
print(json.dumps({"type": "result", "is_error": False, "result": "OK", "modelUsage": {"fake-model": {}}}))
'''


class OversizedTelemetry(TR.Base):
    """Finding 3: an integer too large for a float is unusable telemetry, which holds; nothing raises."""

    def test_pure_numbers_and_readings_are_total(self):
        for value in (10 ** 1000, -10 ** 1000, 10 ** 300, 2 * M.METER_NUM_MAX):
            with self.subTest(value=str(value)[:12]):
                self.assertFalse(M.meter_num(value))
                now = time.time()
                row = Q.reading({"status": "allowed", "unifiedWindows": {
                    "five_hour": {"utilization": value, "resetsAt": now + 3600},
                    "seven_day": {"utilization": .1, "resetsAt": now + 9e4}}}, now)
                self.assertFalse(row["valid"])
                self.assertIsNotNone(Q.pause_until(row, now)[0], "unusable telemetry holds")
        self.assertTrue(M.meter_num(1794110400))                   # a real reset time is still a number

    def oversized(self, window, key):
        """One print call whose rate_limit_event carries 10**1000 in one field, through the real shim."""
        fakes.script(self.host.claude, OVERSIZED_CLAUDE)
        (self.host.bin / "oversized.json").write_text(json.dumps({"window": window, "key": key}))
        room = I.build(self.cfg, "oversized", "claude", "on", memory_bytes=b"# synthetic\n", model="fake-model")
        p = self.cli(room, TR.PRINT)
        self.assertEqual(p.returncode, S.UNVERIFIED, p.stderr)
        self.assertNotIn("OverflowError", p.stdout + p.stderr)
        self.assertIn("no usable quota telemetry", p.stdout)
        until, why, info = SC.Scheduler(SC.conf_from(self.cfg, room.root)).pause()
        self.assertIsNotNone(until, "dispatch is held")
        self.assertIn("unknown", why)
        self.assertFalse(info["stop"])
        rows = Q.rows(room.quota_path)
        self.assertTrue(rows and not any(r.get("valid") for r in rows))
        calls = len(self.calls())                               # the next call is refused by that hold
        self.assertEqual(self.cli(room, TR.PRINT).returncode, S.PAUSED)
        self.assertEqual(len(self.calls()), calls)

    def test_the_full_shim_holds_on_an_oversized_utilization(self):        # probe oversized_integer_quota
        self.oversized("five_hour", "utilization")

    def test_the_full_shim_holds_on_an_oversized_reset(self):
        self.oversized("seven_day", "resetsAt")

    def test_a_fault_while_reading_the_stream_still_holds(self):
        room = I.build(self.cfg, "fault", "claude", "on", memory_bytes=b"# synthetic\n", model="fake-model")
        spec = json.loads((room.bin_dir / "room.json").read_text())
        env = I.environment(room, self.cfg, base=fakes.clean_environ())
        popen = subprocess.Popen

        def quiet_popen(*args, **kwargs):                       # the in-process shim's child gets an empty stdin,
            return popen(*args, **dict(kwargs, stdin=subprocess.DEVNULL))   # not this test runner's own

        cwd = os.getcwd()
        os.chdir(room.work_dir)
        try:
            with unittest.mock.patch.dict(os.environ, env, clear=True), \
                    unittest.mock.patch.object(S.subprocess, "Popen", quiet_popen), \
                    unittest.mock.patch.object(S.Stream, "event", side_effect=RuntimeError("planted fault")):
                with self.assertRaises(RuntimeError):
                    S.claude(spec, TR.PRINT, redact.Redactor(env))
        finally:
            os.chdir(cwd)
        until, why, _ = SC.Scheduler(spec["quota"]).pause()
        self.assertIsNotNone(until)
        self.assertIn("the shim failed reading the stream", why)

    def test_when_even_the_hold_cannot_be_recorded_the_run_stops(self):
        room = I.build(self.cfg, "fault-stop", "claude", "on", memory_bytes=b"# synthetic\n", model="fake-model")
        spec = json.loads((room.bin_dir / "room.json").read_text())

        class Broken:
            def publish(self, row):
                raise OSError("disk full")

        S.hold_after_fault(spec, Broken(), "planted fault", redact.Redactor({}))
        self.assertIn("could not be recorded", room.stop_path.read_text())


class UnregisteredServer(TR.Base):
    """Finding 2: Codex's exact probe, an unregistered MCP server with an arbitrary command and arguments."""
    room = WP.WorldServer.room
    args = WP.WorldServer.args
    codex_args = WP.WorldServer.codex_args
    codex_calls = WP.WorldServer.codex_calls

    def setUp(self):
        super().setUp()
        self.world_py = I.world_script(self.cfg)
        self.world_py.write_text("# synthetic world.py\n")
        self.results = Path(self.cfg["results_dir"])

    def test_the_probe_is_refused_before_codex_runs(self):                  # probe unregistered_context_mcp
        room = self.room("probe", "codex")
        args = WP.CODEX_BASE + ["-c", 'mcp_servers.unregistered_context.command="/bin/true"',
                                "-c", 'mcp_servers.unregistered_context.args=["--synthetic", "context"]',
                                "-c", 'mcp_servers.unregistered_context.default_tools_approval_mode="approve"',
                                "-o", str(self.host.dir / "reply.txt"), "-"]
        p = self.cli(room, args, which="codex")
        self.assertEqual(p.returncode, S.STOPPED)
        self.assertEqual(self.codex_calls(), [])
        self.assertIn("not the registered interpreter", room.stop_path.read_text())

    def test_the_world_server_under_another_name_runs_the_same_world(self):
        # A name is only a label: with the registered interpreter, world.py and episode files, the server is the
        # run's own world whatever it is called, so the guard accepts it; any other command or argument is refused.
        room = self.room("renamed", "codex")
        p = self.cli(room, self.codex_args("renamed", name="unregistered_context"), which="codex")
        self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == "__main__":
    unittest.main()
