"""The room's claude and codex commands (shim.py), run through the wrappers isolate.py writes, against fake CLIs: a
json call comes back as one JSON result while every rate_limit_event lands in quota.jsonl; the room's rules hold
whatever the caller's environment says; a paused quota or a hard stop refuses without calling the model; an API key
anywhere stops everything; and the Codex arm stops at its cap. No test calls a model.

    python3 -m unittest discover -s calibration/tests
"""
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fakes  # noqa: E402
import isolate as I  # noqa: E402
import quota as Q  # noqa: E402
import shim as S  # noqa: E402

FAKE_API_KEY = "-".join(("test", "api", "key", "not", "real"))  # built at run time, so secret scanners never see a key-shaped literal

PRINT = ["-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence", "--output-format", "json",
         "--model", "claude-sonnet-5-5"]


class Base(unittest.TestCase):
    def setUp(self):
        self.host = fakes.Host()
        self.cfg = I.load_local(self.host.local)
        self.room = I.build(self.cfg, "r1", "claude", "on", I.EFFORT, b"# memory\n")

    def tearDown(self):
        self.host.close()

    def claude_script(self, **script):
        (self.host.bin / "claude-script.json").write_text(json.dumps(script))

    def calls(self, name="claude-calls.jsonl"):
        path = self.host.bin / name
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def run_cli(self, cli, args, room=None, **extra_env):
        room = room or self.room
        env = dict(I.environment(room, self.cfg, base=fakes.clean_environ()), **extra_env)
        return subprocess.run([str(room.bin_dir / cli), *args], input="the prompt", capture_output=True, text=True,
                              env=env, timeout=60, cwd=room.work_dir)


class Claude(Base):
    def test_json_call_returns_one_result_and_logs_the_quota(self):
        self.claude_script(rate_limit_info={"status": "allowed", "unifiedWindows": {
            "five_hour": {"utilization": 0.15, "resetsAt": time.time() + 3600},
            "seven_day": {"utilization": 0.08, "resetsAt": time.time() + 86400}}})
        p = self.run_cli("claude", PRINT)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)                               # exactly one JSON object, as probatio reads it
        self.assertEqual((out["result"], out["is_error"]), ("OK", False))
        row = Q.latest(self.room.quota_path)
        self.assertEqual((row["five_hour"], row["seven_day"], row["run_id"]), (0.15, 0.08, "r1"))
        call = self.calls()[0]
        i = call["args"].index("--output-format")
        self.assertEqual(call["args"][i + 1], "stream-json")
        self.assertIn("--verbose", call["args"])
        self.assertEqual(call["stdin"], "the prompt")

    def test_room_rules_override_the_caller(self):
        # probatio v3's with-memory condition removes the auto-memory switch; the shim puts it back
        env = I.environment(self.room, self.cfg, base=fakes.clean_environ())
        env.pop("CLAUDE_CODE_DISABLE_AUTO_MEMORY")
        env["CLAUDE_CODE_EFFORT_LEVEL"] = "max"
        subprocess.run([str(self.room.bin_dir / "claude"), *PRINT], input="", capture_output=True, text=True,
                       env=env, timeout=60, cwd=self.room.work_dir)
        seen = self.calls()[0]["env"]
        self.assertEqual(seen["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")
        self.assertEqual(seen["CLAUDE_CODE_EFFORT_LEVEL"], "medium")
        self.assertEqual(seen["DISABLE_AUTOUPDATER"], "1")
        self.assertNotIn("CLAUDE_CODE_DISABLE_CLAUDE_MDS", seen)

    def test_off_room_forces_claude_md_off(self):
        room = I.build(self.cfg, "r-off", "claude", "off", I.EFFORT, b"# memory\n")
        self.run_cli("claude", PRINT, room=room)
        self.assertEqual(self.calls()[0]["env"]["CLAUDE_CODE_DISABLE_CLAUDE_MDS"], "1")

    def test_text_mode_prints_the_reply(self):
        p = self.run_cli("claude", [a for a in PRINT if a not in ("--output-format", "json")])
        self.assertEqual(p.stdout.strip(), "OK")

    def test_version_passes_straight_through(self):
        p = self.run_cli("claude", ["--version"])
        self.assertEqual(p.stdout.strip(), "2.1.309 (Claude Code)")
        self.assertEqual(self.calls(), [])

    def test_paused_quota_refuses_without_calling(self):
        Q.append(self.room.quota_path, Q.reading({"status": "allowed", "unifiedWindows": {
            "five_hour": {"utilization": 0.86, "resetsAt": time.time() + 3600}}}, time.time(), "other-run"))
        p = self.run_cli("claude", PRINT)
        self.assertEqual(p.returncode, S.PAUSED)
        out = json.loads(p.stdout)
        self.assertTrue(out["is_error"])
        self.assertIn("paused for quota", out["result"])
        self.assertEqual(self.calls(), [])

    def test_hard_stop_refuses_without_calling(self):
        self.room.stop_path.write_text("planted\n")
        p = self.run_cli("claude", PRINT)
        self.assertEqual(p.returncode, S.STOPPED)
        self.assertIn("stopped", json.loads(p.stdout)["result"])
        self.assertEqual(self.calls(), [])

    def test_api_key_source_other_than_none_stops_everything(self):
        self.claude_script(api_key_source="ANTHROPIC_API_KEY")
        p = self.run_cli("claude", PRINT)
        self.assertEqual(p.returncode, S.STOPPED)
        self.assertIn("apiKeySource", json.loads(p.stdout)["result"])
        self.assertIn("apiKeySource", self.room.stop_path.read_text())
        self.assertEqual(self.run_cli("claude", PRINT).returncode, S.STOPPED)   # and the next call too

    def test_api_key_in_the_call_environment_stops(self):
        p = self.run_cli("claude", PRINT, **{"ANTHROPIC_API_KEY": FAKE_API_KEY})
        self.assertEqual(p.returncode, S.STOPPED)
        self.assertTrue(self.room.stop_path.exists())
        self.assertNotIn("test-api-key-not-real", self.room.stop_path.read_text() + p.stdout + p.stderr)
        self.assertEqual(self.calls(), [])

    def test_wrong_config_dir_stops(self):
        p = self.run_cli("claude", PRINT, CLAUDE_CONFIG_DIR=str(self.host.dir))
        self.assertEqual(p.returncode, S.STOPPED)
        self.assertEqual(self.calls(), [])

    def test_limit_error_without_an_event_records_a_rejection(self):
        self.claude_script(result={"is_error": True, "api_error_status": 429, "result": "rate limited"})
        self.run_cli("claude", PRINT)
        row = Q.latest(self.room.quota_path)
        self.assertEqual(row["status"], "rejected")
        self.assertIsNotNone(Q.pause_state(self.room.quota_path)[0])


class RealStream(Base):
    def test_a_recorded_real_stream_is_accepted(self):
        # tests/fixtures/stream-2.1.309.jsonl is a real Claude Code 2.1.309 stream (2026-11-07), cut to the fields the
        # shim reads: a ui_invalidate event before init, init with model and build, the reply, the meter, the result
        room = I.build(self.cfg, "real", "claude", "on", I.EFFORT, b"# memory\n", model="claude-sonnet-5-5")
        fixture = Path(__file__).resolve().parent / "fixtures" / "stream-2.1.309.jsonl"
        fakes.script(self.host.claude, f"#!PYTHON\nimport sys\nsys.stdout.write(open({str(fixture)!r}).read())\n")
        p = self.run_cli("claude", PRINT, room=room)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout)["result"], "OK")
        self.assertFalse(room.stop_path.exists())


CLEAN_EXEC = ["exec", "--ignore-user-config", "--ignore-rules"]     # the two flags the Codex contract requires


class Codex(Base):
    def setUp(self):
        super().setUp()
        self.room = I.build(self.cfg, "r-codex", "codex", "on", I.EFFORT, b"# memory\n")
        spec = json.loads((self.room.bin_dir / "room.json").read_text())
        spec["codex_cap"] = 2
        (self.room.bin_dir / "room.json").write_text(json.dumps(spec))

    def test_exec_is_counted_capped_and_given_the_effort(self):
        for _ in range(2):
            self.assertEqual(self.run_cli("codex", [*CLEAN_EXEC, "--ephemeral", "-"], OPENAI_API_KEY="x").returncode,
                             0)
        calls = self.calls("codex-calls.jsonl")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["args"][:3], ["exec", "-c", 'model_reasoning_effort="medium"'])
        self.assertFalse(calls[0]["openai_key"])
        p = self.run_cli("codex", [*CLEAN_EXEC, "-"])
        self.assertEqual(p.returncode, S.STOPPED)
        self.assertIn("cap", self.room.stop_path.read_text())
        self.assertEqual(len(self.calls("codex-calls.jsonl")), 2)

    def test_the_registered_effort_is_kept_and_other_commands_pass_through(self):
        self.run_cli("codex", [*CLEAN_EXEC, "-c", 'model_reasoning_effort="medium"', "-"])
        self.assertEqual(self.calls("codex-calls.jsonl")[0]["args"], [*CLEAN_EXEC, "-c",
                                                                       'model_reasoning_effort="medium"', "-"])
        self.run_cli("codex", ["features", "list"])
        self.assertEqual(self.calls("codex-calls.jsonl")[1]["args"], ["features", "list"])
        ledger = json.loads(Path(self.room.root / "account" / "account.json").read_text())
        self.assertEqual(ledger["stages"]["C"]["charged_requests"], 1)     # only the exec was reserved

    def test_a_contradicting_effort_is_refused_and_stops(self):
        p = self.run_cli("codex", [*CLEAN_EXEC, "-c", 'model_reasoning_effort="low"', "-"])
        self.assertEqual(p.returncode, S.STOPPED)
        self.assertIn("reasoning effort low", self.room.stop_path.read_text())
        self.assertEqual(self.calls("codex-calls.jsonl"), [])


if __name__ == "__main__":
    unittest.main()
