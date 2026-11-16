"""Regression tests for Codex's re-verification of the clean room at c804818 (four high findings and one medium): one
test per probe in its new_probes.py and extra_checks.py, plus the fixtures its fixes asked for. Each probe test failed
on c804818 and passes on the fixed harness. Fake CLIs only (fakes.py), a clearly fake token, and no model or network
call.

    python3 -m unittest discover -s calibration/tests
"""
import hashlib
import json
import math
import os
import subprocess
import sys
import threading
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
import run as R  # noqa: E402
import test_regressions as TR  # noqa: E402

PRINT = TR.PRINT
STREAM = [("stream-json" if a == "json" else a) for a in PRINT]
INIT = {"type": "system", "subtype": "init", "apiKeySource": "none", "model": "fake-model",
        "claude_code_version": "2.1.309"}


def meter_event():
    return {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {
        "five_hour": {"utilization": 0.1, "resetsAt": time.time() + 3600},
        "seven_day": {"utilization": 0.1, "resetsAt": time.time() + 9e4}}}}


def events(stdout):
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


# ---- finding 1: startup and refusal fields are scrubbed --------------------------------------------------------------
TOKEN_AUTH_STATUS = '''#!PYTHON
import json, os, sys
if "--version" in sys.argv: print("2.1.309 (Claude Code)")
else: print(json.dumps({"loggedIn": True, "authMethod": os.environ["CLAUDE_CODE_OAUTH_TOKEN"]}))
'''


class Finding1StartupRedaction(TR.Base):
    def test_an_auth_status_field_holding_the_token_is_scrubbed(self):   # probe auth_status_redaction, part 1
        room = I.build(self.cfg, "auth-redact", "claude", "on", memory_bytes=b"# synthetic\n")
        fakes.script(self.host.claude, TOKEN_AUTH_STATUS)
        found = self.problems(room)
        self.assertTrue(any("auth status" in p for p in found), "the bad login is still reported")
        self.assertFalse(any(fakes.TOKEN in p for p in found))

    def test_the_public_check_never_prints_the_token(self):              # probe auth_status_redaction, part 2
        fakes.script(self.host.claude, TOKEN_AUTH_STATUS)
        p = subprocess.run([sys.executable, "-B", str(HERE.parent / "isolate.py"), "--check", "--local",
                            str(self.host.local)], capture_output=True, text=True, env=fakes.clean_environ(),
                           timeout=60)
        self.assertEqual(p.returncode, 2)
        self.assertIn("auth status", p.stderr)
        self.assertNotIn(fakes.TOKEN, p.stdout + p.stderr)

    def test_the_public_run_never_prints_the_token(self):
        fakes.script(self.host.claude, TOKEN_AUTH_STATUS)
        spec = self.host.dir / "spec.json"
        (self.host.dir / "memory.md").write_text("# synthetic\n")
        spec.write_text(json.dumps({"run_id": "auth-run", "agent": "claude", "model": "fake-model", "memory": "on",
                                    "memory_file": "memory.md", "jobs": 1}))
        p = subprocess.run([sys.executable, "-B", str(HERE.parent / "run.py"), str(spec), "--local",
                            str(self.host.local)], capture_output=True, text=True, env=fakes.clean_environ(),
                           timeout=60)
        self.assertEqual(p.returncode, 2)
        self.assertNotIn(fakes.TOKEN, p.stdout + p.stderr)
        self.assertEqual(self.host.files_containing(fakes.TOKEN), [])

    def test_a_stop_reason_holding_the_token_is_scrubbed(self):
        room = I.build(self.cfg, "stop-redact", "claude", "on", memory_bytes=b"# synthetic\n")
        room.stop_path.write_text(f"planted stop quoting {fakes.TOKEN}\n")
        found = self.problems(room)
        self.assertTrue(any("hard stop" in p for p in found))
        self.assertFalse(any(fakes.TOKEN in p for p in found))


# ---- finding 2: the memory switch and the Codex command contract ----------------------------------------------------
CANARY_CLAUDE = '''#!PYTHON
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("2.1.309 (Claude Code)"); sys.exit(0)
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "oauth_token"})); sys.exit(0)
sys.stdin.read()
memory = Path.cwd() / "CLAUDE.md"
loaded = memory.exists() and os.environ.get("CLAUDE_CODE_DISABLE_CLAUDE_MDS") != "1"
reply = "\\n".join(x for x in memory.read_text().splitlines() if "CANARY" in x) if loaded else "NONE"
model = args[args.index("--model") + 1]
now = time.time()
print(json.dumps({"type": "system", "subtype": "init", "apiKeySource": "none", "model": model,
                  "claude_code_version": "2.1.309"}))
print(json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {
    "five_hour": {"utilization": 0.1, "resetsAt": now + 3600}, "seven_day": {"utilization": 0.1, "resetsAt": now + 5e5}}}}))
print(json.dumps({"type": "result", "is_error": False, "result": reply, "modelUsage": {model: {}},
                  "usage": {"input_tokens": 2000 + (180 if loaded else 0)}}))
'''


class Finding2CallContract(TR.Base):
    def codex_calls(self):
        path = self.host.bin / "codex-calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_an_on_room_refuses_the_memory_disable_switch(self):          # probe on_memory_disabled
        room = I.build(self.cfg, "on-disabled", "claude", "on", memory_bytes=b"# SYNTHETIC APPROVED\n",
                       model="fake-model")
        p = self.cli(room, PRINT, extra={"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"})
        self.assertNotEqual(p.returncode, 0)
        self.assertTrue(json.loads(p.stdout)["is_error"])
        self.assertEqual(self.calls(), [], "no subject call with its memory switched off")

    def test_an_off_room_refuses_a_memory_enable_value(self):
        room = I.build(self.cfg, "off-enabled", "claude", "off", memory_bytes=None, model="fake-model")
        p = self.cli(room, PRINT, extra={"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "0"})
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_the_rooms_own_switch_reaches_the_child(self):
        for memory, want in (("on", None), ("off", "1")):
            with self.subTest(memory=memory):
                room = I.build(self.cfg, f"switch-{memory}", "claude", memory,
                               memory_bytes=b"# synthetic\n" if memory == "on" else None, model="fake-model")
                p = self.cli(room, PRINT)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertEqual(json.loads(self.calls()[-1])["env"].get("CLAUDE_CODE_DISABLE_CLAUDE_MDS"), want)

    def test_claude_options_outside_the_contract_are_refused(self):
        room_args = {
            "settings with env": ["--settings", '{"disableAllHooks":true,"env":{"CLAUDE_CODE_DISABLE_CLAUDE_MDS":"1"}}'],
            "settings file": ["--settings", "/tmp/elsewhere.json"],
            "setting sources": ["--setting-sources", "user,project"],
            "unknown flag": ["--dangerously-skip-permissions"],
            "add dir": ["--add-dir", "/tmp"],
            "tools": ["--tools", "Bash"],
        }
        for i, (name, extra) in enumerate(room_args.items()):
            with self.subTest(case=name):
                room = I.build(self.cfg, f"claude-opt-{i}", "claude", "on", memory_bytes=b"# synthetic\n",
                               model="fake-model")
                args = list(PRINT)
                if extra[0] in args:                                    # replace the contract's own value
                    args[args.index(extra[0]) + 1] = extra[1]
                else:
                    args += extra
                p = self.cli(room, args)
                self.assertNotEqual(p.returncode, 0, name)
                self.assertEqual(self.calls(), [], name)

    def test_codex_directory_and_instruction_overrides_are_refused(self):  # probe codex_effective_context_overrides
        room = I.build(self.cfg, "codex-cd", "codex", "off", memory_bytes=None, model="fake-model")
        other = self.host.dir / "other"
        other.mkdir()
        (other / "AGENTS.md").write_text("# SYNTHETIC FOREIGN CONTEXT\n")
        args = ["exec", "-C", str(other), "-m", "fake-model", "-c", "project_doc_max_bytes=9000",
                "-c", 'developer_instructions="SYNTHETIC EXTRA INSTRUCTIONS"', "-"]
        p = self.cli(room, args, which="codex")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.codex_calls(), [], "nothing reached Codex")

    def test_each_codex_override_alone_is_refused(self):
        base = ["exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "--ignore-user-config",
                "--ignore-rules", "-m", "fake-model"]
        cases = {"-C": ["-C", "/tmp"], "--cd": ["--cd=/tmp"], "add dir": ["--add-dir", "/tmp"],
                 "profile": ["-p", "other"], "doc bytes": ["-c", "project_doc_max_bytes=9000"],
                 "developer instructions": ["-c", 'developer_instructions="x"'],
                 "instructions file": ["-c", 'model_instructions_file="/tmp/x.md"'],
                 "sandbox": ["-s", "danger-full-access"], "web search": ["-c", 'web_search="live"'],
                 "no ignore-user-config": None, "positional prompt": ["do something"], "unknown": ["--oss"]}
        for i, (name, extra) in enumerate(cases.items()):
            with self.subTest(case=name):
                room = I.build(self.cfg, f"codex-opt-{i}", "codex", "off", memory_bytes=None, model="fake-model")
                args = [a for a in base if a != "--ignore-user-config"] if extra is None else base + extra
                p = self.cli(room, args + ["-"], which="codex")
                self.assertNotEqual(p.returncode, 0, name)
                self.assertEqual(self.codex_calls(), [], name)

    def test_an_on_room_refuses_a_codex_docs_setting(self):
        room = I.build(self.cfg, "codex-on-docs", "codex", "on", memory_bytes=b"# synthetic\n", model="fake-model")
        p = self.cli(room, ["exec", "--ignore-user-config", "--ignore-rules", "-c", "project_doc_max_bytes=0", "-"],
                     which="codex")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.codex_calls(), [])

    def test_the_canary_asks_both_conditions_without_contradicting_a_room(self):
        """The canary used to ask memory off inside its memory-on room; with the switch enforced it uses one room per
        condition. A fake CLI that loads CLAUDE.md unless the switch is set stands in for the model."""
        fakes.script(self.host.claude, CANARY_CLAUDE)
        lines = I.canary(self.cfg, "fake-model")
        self.assertIn("canary passed", lines[0])
        self.assertFalse((self.host.root / "STOP").exists())

    def test_probatios_own_codex_commands_pass(self):
        """The exact shapes probatio v3 and v4 build (codex_command, its errand variant and codex_resume_command)."""
        out = str(self.host.dir / "reply.txt")
        world_py = I.world_script(self.cfg)
        world_py.write_text("# synthetic world.py\n")
        results = Path(self.cfg["results_dir"])
        episode = lambda run: results / run / "episodes" / "with-anulus-1"                  # noqa: E731
        world = lambda run: ["-c", f"mcp_servers.world.command={json.dumps(sys.executable)}",   # noqa: E731
                             "-c", "mcp_servers.world.args=" + json.dumps(
                                 ["-I", "-B", str(world_py), str(episode(run) / "world.json"),
                                  str(episode(run) / "record.jsonl"), str(episode(run) / "close")]),
                             "-c", 'mcp_servers.world.default_tools_approval_mode="approve"']
        shared = ["--disable", "plugins", "-m", "fake-model", "-c", 'model_reasoning_effort="medium"']
        shapes = {
            "question": ["exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "--ignore-user-config",
                         "--ignore-rules", "--color", "never", "-c", 'web_search="disabled"', *shared, "-o", out, "-"],
            "errand": ["exec", "--skip-git-repo-check", "-s", "read-only", "--ignore-user-config", "--ignore-rules",
                       "--color", "never", "-c", 'web_search="disabled"', *shared, "WORLD", "--json", "-o", out, "-"],
            "report": ["exec", "resume", "0199a2b4-session", "--skip-git-repo-check", "--ignore-user-config",
                       "--ignore-rules", "-c", 'sandbox_mode="read-only"', "-c", 'web_search="disabled"', *shared,
                       "-o", out, "-"],
        }
        for memory in ("on", "off"):
            for name, args in shapes.items():
                with self.subTest(memory=memory, shape=name):
                    run = f"codex-{memory}-{name}"
                    room = I.build(self.cfg, run, "codex", memory, memory_bytes=b"# synthetic\n" if memory == "on"
                                   else None, model="fake-model", out_dir=results / run)
                    if "WORLD" in args:
                        at = args.index("WORLD")
                        args = args[:at] + world(run) + args[at + 1:]
                    if memory == "off":
                        args = args[:-3] + ["-c", "project_doc_max_bytes=0"] + args[-3:]
                    before = len(self.codex_calls())
                    p = self.cli(room, args, which="codex")
                    self.assertEqual(p.returncode, 0, p.stderr)
                    self.assertEqual(len(self.codex_calls()), before + 1)


# ---- finding 3: native stream release, the model set and exact versions ---------------------------------------------
class Finding3Attestation(TR.Base):
    def script(self, body):
        fakes.script(self.host.claude, "#!PYTHON\nimport json, sys, time\n" + body)

    def write(self, room_id, script):
        room = I.build(self.cfg, room_id, "claude", "on", memory_bytes=b"# synthetic\n", model="fake-model")
        (self.host.bin / "claude-script.json").write_text(json.dumps(script))
        return room

    def test_native_stream_forwards_no_unverified_success(self):         # probe unverified_stream_success_forwarded
        room = self.write("stream-unverified", {"no_init": True, "no_rate": True})
        p = self.cli(room, STREAM)
        results = [e for e in events(p.stdout) if e.get("type") == "result"]
        self.assertEqual(p.returncode, 76)
        self.assertEqual([r for r in results if not r.get("is_error")], [])
        self.assertEqual(len(results), 1)

    def test_native_stream_without_a_meter_forwards_no_success(self):
        room = self.write("stream-no-meter", {"no_rate": True})
        p = self.cli(room, STREAM)
        self.assertEqual(p.returncode, 76)
        self.assertFalse(any(e.get("type") == "result" and not e.get("is_error") for e in events(p.stdout)))

    def test_native_stream_passes_a_verified_result(self):
        room = self.write("stream-ok", {})
        p = self.cli(room, STREAM)
        self.assertEqual(p.returncode, 0, p.stderr)
        kinds = [e.get("type") for e in events(p.stdout)]
        self.assertEqual(kinds, ["system", "rate_limit_event", "result"])

    def test_a_dialogue_gets_its_first_result_before_it_answers(self):
        """probatio's errand dialogue writes the report question only after the first result arrives, so a verified
        result must be released at once, not held until the CLI exits."""
        room = I.build(self.cfg, "dialogue", "claude", "on", memory_bytes=b"# synthetic\n", model="fake-model")
        result = {"type": "result", "is_error": False, "result": "ERRAND", "modelUsage": {"fake-model": {}}}
        self.script(f"print(json.dumps({INIT!r}), flush=True)\n"
                    f"print(json.dumps({meter_event()!r}), flush=True)\n"
                    f"print(json.dumps({result!r}), flush=True)\n"
                    "line = sys.stdin.readline()\n"
                    f"print(json.dumps(dict({result!r}, result='REPORT ' + line.strip())), flush=True)\n")
        env = I.environment(room, self.cfg, base=fakes.clean_environ())
        proc = subprocess.Popen([str(room.bin_dir / "claude"), *STREAM, "--input-format", "stream-json"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=env, cwd=room.work_dir)
        seen = []

        def converse():
            for line in proc.stdout:
                seen.append(json.loads(line))
                if seen[-1].get("type") == "result" and len([e for e in seen if e.get("type") == "result"]) == 1:
                    proc.stdin.write("FOLLOW-UP\n")
                    proc.stdin.close()

        t = threading.Thread(target=converse)
        t.start()
        t.join(timeout=20)
        if t.is_alive():
            proc.kill()                                                 # this test's own child
            t.join()
            self.fail("the first result was held back; the dialogue could not continue")
        proc.wait(timeout=10)
        proc.stdout.close()
        proc.stderr.close()
        results = [e["result"] for e in seen if e.get("type") == "result"]
        self.assertEqual(results, ["ERRAND", "REPORT FOLLOW-UP"])
        self.assertEqual(proc.returncode, 0)

    def test_a_result_with_an_extra_model_is_unverified(self):           # probe additional_result_model
        room = self.write("mixed-model", {"result": {"modelUsage": {"fake-model": {}, "other-model": {}}}})
        p = self.cli(room, PRINT)
        self.assertEqual(p.returncode, 76)
        self.assertTrue(json.loads(p.stdout)["is_error"])

    def test_a_registered_auxiliary_model_is_allowed(self):
        self.cfg["auxiliary_models"] = ["helper-model"]
        room = self.write("aux-model", {"result": {"modelUsage": {"fake-model": {}, "helper-model": {}}}})
        p = self.cli(room, PRINT)
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_a_same_prefix_build_is_refused(self):                       # probe wrong_version_prefix
        room = self.write("version-prefix", {"version": "2.1.3090"})
        p = self.cli(room, PRINT)
        self.assertNotEqual(p.returncode, 0)
        self.assertTrue(json.loads(p.stdout)["is_error"])
        self.assertTrue(room.stop_path.exists())

    def test_the_binary_version_checks_are_exact(self):
        room = I.build(self.cfg, "binary-prefix", "codex", "off", memory_bytes=None)
        fakes.script(self.host.codex, fakes.FAKE_CODEX.replace("codex-cli 0.159.2", "codex-cli 0.159.20"))
        env = dict(I.environment(room, self.cfg), FAKE_CLAUDE_VERSION="2.1.3090 (Claude Code)")
        found = I.problems(room, self.cfg, env, fakes.clean_environ())
        self.assertTrue(any("2.1.3090" in p for p in found))
        self.assertTrue(any("0.159.20" in p for p in found))


# ---- finding 4: non-finite meter values ------------------------------------------------------------------------------
class Finding4NonFinite(unittest.TestCase):
    BAD = (float("nan"), float("inf"), float("-inf"))

    def event(self, now, override=None):
        windows = {"five_hour": {"utilization": .99, "resetsAt": now + 3600},
                   "seven_day": {"utilization": .1, "resetsAt": now + 86400}}
        for (w, key), value in (override or {}).items():
            windows[w][key] = value
        return {"status": "allowed", "unifiedWindows": windows}

    def test_non_finite_resets_hold(self):                               # probe quota_nan, quota_negative_infinity
        now = time.time()
        for value in self.BAD:
            with self.subTest(value=value):
                row = Q.reading(self.event(now, {("five_hour", "resetsAt"): value}), now)
                self.assertFalse(row["valid"])
                self.assertIsNotNone(Q.pause_until(row, now)[0])

    def test_every_non_finite_field_is_unusable_and_holds(self):
        now = time.time()
        for w in ("five_hour", "seven_day"):
            for key in ("utilization", "resetsAt"):
                for value in self.BAD:
                    with self.subTest(window=w, key=key, value=value):
                        row = Q.reading(self.event(now, {(w, key): value}), now)
                        self.assertFalse(row["valid"])
                        self.assertIsNotNone(Q.pause_until(row, now)[0])
                        json.dumps(row, allow_nan=False)            # no non-finite value is kept in the row

    def test_a_non_finite_time_changes_nothing(self):
        now = time.time()
        known = M.meter_observe(None, Q.reading(self.event(now), now))
        for value in self.BAD:
            with self.subTest(value=value):
                row = dict(Q.reading(self.event(now + 10, {("five_hour", "utilization"): .05}), now + 10), t=value)
                self.assertEqual(M.meter_observe(known, row), known)

    def test_a_malformed_reading_keeps_a_known_hold(self):
        now = time.time()
        state = M.meter_observe(None, Q.reading(self.event(now), now))
        for value in self.BAD:
            with self.subTest(value=value):
                low = Q.reading(self.event(now + 60, {("five_hour", "utilization"): .05,
                                                         ("five_hour", "resetsAt"): value}), now + 60)
                after = M.meter_observe(state, low)
                self.assertEqual(after["windows"]["five_hour"]["u"], .99)
                until, _, _, _ = M.meter_hold(after, now + 61, Q.DEFAULT_LIMITS)
                self.assertGreaterEqual(until, now + 3600)

    def test_a_rejection_with_a_non_finite_reset_holds_for_the_backoff(self):
        now = time.time()
        for value in self.BAD:
            with self.subTest(value=value):
                row = Q.reading({"status": "rejected", "resetsAt": value}, now)
                until, _ = Q.pause_until(row, now)
                self.assertIsNotNone(until)
                self.assertTrue(math.isfinite(until))

    def test_a_state_from_elsewhere_with_non_finite_values_is_cleaned(self):
        now = time.time()
        good = M.meter_observe(None, Q.reading(self.event(now), now))
        bad = {"windows": {"five_hour": {"u": float("nan"), "resets": float("inf"), "t": now, "host": "x"},
                           "seven_day": {"u": .5, "resets": float("nan"), "t": float("nan"), "host": "x"}},
               "rejected": {"until": float("nan"), "t": now, "host": "x"}, "unknown": None, "valid_t": float("inf")}
        out = M.meter_combine(good, bad)
        self.assertEqual(out["windows"]["five_hour"]["u"], .99)
        json.dumps(out, allow_nan=False)
        self.assertIsNotNone(M.meter_hold(out, now + 1, Q.DEFAULT_LIMITS)[0])

    def test_quota_log_lines_with_a_non_finite_time_are_skipped(self):
        host = fakes.Host()
        try:
            path = host.dir / "quota.jsonl"
            path.write_text('{"t": NaN, "status": "allowed"}\n{"t": Infinity, "status": "allowed"}\n')
            self.assertEqual(Q.rows(path), [])
        finally:
            host.close()

    def test_the_ledger_refuses_a_reading_with_a_non_finite_time(self):
        host = fakes.Host()
        try:
            with self.assertRaises(ValueError):
                A.apply(str(host.dir), "publish", {"reading": {"t": float("nan"), "status": "allowed"},
                                                   "host": "h"}, now=time.time())
        finally:
            host.close()


# ---- finding 5: the Codex executable's bytes are part of the registration --------------------------------------------
class Finding5CodexIdentity(TR.Base):
    SPEC = {"run_id": "binary-resume", "agent": "codex", "model": "fake-model", "memory": "off", "jobs": 1,
            "cli": "v4", "effort": "medium", "probatio_args": [], "memory_bytes": None}

    def test_changed_codex_bytes_change_the_fingerprint(self):           # probe codex_binary_mutation
        before = R.fingerprint(self.SPEC, self.cfg)[0]
        fakes.script(self.host.codex, fakes.FAKE_CODEX + "\n# Different synthetic binary bytes\n")
        self.assertNotEqual(R.fingerprint(self.SPEC, self.cfg)[0], before)

    def test_a_codex_command_name_is_resolved_on_path_and_hashed(self):
        self.cfg["codex_binary"] = "codex"
        with unittest.mock.patch.dict(os.environ, {"PATH": f"{self.host.bin}{os.pathsep}{os.defpath}"}):
            identity = R.fingerprint(self.SPEC, self.cfg)[1]["codex"]
        self.assertEqual(identity["sha256"], hashlib.sha256(self.host.codex.read_bytes()).hexdigest())
        self.assertEqual(Path(identity["path"]), self.host.codex.resolve())

    def test_a_resume_after_the_codex_bytes_change_is_refused(self):
        out = Path(self.cfg["results_dir"]) / "binary-resume"
        out.mkdir(parents=True)
        digest, canonical = R.fingerprint(self.SPEC, self.cfg)
        (out / "calibration.json").write_text(json.dumps({"registration": {"fingerprint": digest,
                                                                           "canonical": canonical}}))
        fakes.script(self.host.codex, fakes.FAKE_CODEX + "\n# Different synthetic binary bytes\n")
        with self.assertRaises(I.Refusal) as caught:
            R.registered(out, *R.fingerprint(self.SPEC, self.cfg), time.time())
        self.assertIn("codex", str(caught.exception))

    def test_a_binary_changed_between_attempts_stops_the_run(self):
        plan = self.host.dir / "plan.json"
        plan.write_text(json.dumps([{"records": [dict(TR.OK_RECORD, error="boom")], "rc": 1},
                                    {"records": [TR.OK_RECORD], "rc": 0}]))
        spec = {"run_id": "binary-swap", "agent": "claude", "model": "fake-model", "memory": "on", "jobs": 1,
                "cli": "v4", "effort": "medium", "probatio_args": [], "memory_bytes": b"# synthetic\n"}

        def sleep(seconds):                                              # between the attempts
            fakes.script(self.host.claude, fakes.FAKE_CLAUDE + "\n# swapped synthetic build\n")

        env = {"FAKE_PLAN": str(plan), "FAKE_QUOTA": str(self.host.root / "quota.jsonl"),
               "FAKE_STOP": str(self.host.root / "STOP")}
        with unittest.mock.patch.dict(os.environ, env):
            record = R.run(spec, self.cfg, sleep=sleep, log=lambda m: None)
        self.assertTrue(record["outcome"].startswith("refused"), record["outcome"])
        self.assertIn("claude", record["outcome"])
        self.assertEqual(len(record["attempts"]), 1)


if __name__ == "__main__":
    unittest.main()
