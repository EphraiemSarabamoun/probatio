"""The shim and a two-turn stream-json dialogue, on real Claude Code 2.1.309 streams (docs/ordo/PREREG.md, Amendment 4).

Claude Code 2.1.309 sends `system init` again, with the same session, at the start of every later turn of a stream-json
dialogue: an errand, then its after-errand report. The shim used to count that repeat as a protocol fault, so every
errand's report turn was withheld, killed and held for five minutes (the ordo pilot's Stage 1, 2026-11-08). The six
fixtures in fixtures/dialogue-2.1.309/ are those real streams (built by fixtures/make_dialogue_streams.py, which says
what was changed: host paths replaced, the shim's own error dropped, the withheld report result rebuilt).

Each test replays a fixture through the room's actual wrapper (bin/<run>/claude, the shim) with a fake Claude Code that
sends turn 1 when the first user line arrives and turn 2 when the second does, and drives the wrapper as probatio's
errand dialogue does: the errand on stdin, read to its result, then the report question, read to its result. An
identical repeated init passes and both results come back; a repeated init that differs in session, key source, build
or model is refused as before. No model is called.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))
import fakes  # noqa: E402
import isolate  # noqa: E402
import probatio  # noqa: E402
import shim  # noqa: E402

FIXTURES = HERE / "fixtures" / "dialogue-2.1.309"
MODEL = "claude-sonnet-5-5"
REPLAY = r'''#!PYTHON
"""A fake Claude Code that replays a recorded two-turn stream: --version and auth status as the pinned build; for a
print call, turn 1's events when the first user line arrives on stdin and the rest when the second does. The stream and
any change to the later init come from replay.json beside it."""
import json, os, sys
from pathlib import Path
here = Path(__file__).resolve().parent
args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("2.1.309 (Claude Code)"); sys.exit(0)
if args[:2] == ["auth", "status"]:
    ok = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
    print(json.dumps({"loggedIn": ok, "authMethod": "oauth_token" if ok else "none"})); sys.exit(0)
spec = json.loads((here / "replay.json").read_text())
with open(here / "replay-calls.jsonl", "a") as f:
    f.write(json.dumps({"args": args}) + "\n")
events = [json.loads(l) for l in open(spec["stream"]) if l.strip()]
first = next(i for i, e in enumerate(events) if e.get("type") == "result")
turns = [events[:first + 1], events[first + 1:]]
for k, v in spec.get("later_init", {}).items():
    later = next(e for e in turns[1] if e.get("type") == "system" and e.get("subtype") == "init")
    later[k] = v
for turn in turns:
    if not sys.stdin.readline():
        break
    for e in turn:
        sys.stdout.write(json.dumps(e) + "\n")
        sys.stdout.flush()
sys.stdin.read()
'''


class DialogueCase(unittest.TestCase):
    """A scratch host whose pinned build is the replay fake, and a memory-on room registered for MODEL."""

    def setUp(self):
        self.host = fakes.Host()
        self.addCleanup(self.host.close)
        fakes.script(self.host.claude, REPLAY)
        self.cfg = isolate.load_local(self.host.local)
        self.room = isolate.build(self.cfg, "dialogue", "claude", "on", "medium", b"# notes\n", model=MODEL)
        self.env = isolate.environment(self.room, self.cfg, base=fakes.clean_environ())
        cmd, changes = probatio.claude_command("with", MODEL, effort="medium", dialogue=True, auto_memory="off")
        self.env.update({k: v for k, v in changes.items() if v is not None})
        for k in [k for k, v in changes.items() if v is None]:
            self.env.pop(k, None)
        self.args = [str(self.room.bin_dir / "claude")] + cmd[1:]

    def replay(self, stream, later_init=None):
        """Drive the wrapper as probatio's dialogue does; returns (exit code, the result events in order, stdout)."""
        (self.host.claude.parent / "replay.json").write_text(json.dumps({"stream": str(stream),
                                                                          "later_init": later_init or {}}))
        proc = subprocess.Popen(self.args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, cwd=self.room.work_dir, env=self.env)
        results, lines = [], []
        proc.stdin.write(probatio.user_line("the errand"))
        proc.stdin.flush()
        for line in proc.stdout:
            lines.append(line)
            event = json.loads(line)
            if event.get("type") == "result":
                results.append(event)
                if len(results) == 1 and not event.get("is_error"):
                    proc.stdin.write(probatio.user_line("the report's questions"))
                    proc.stdin.flush()
                else:
                    break
        proc.stdin.close()
        rest = proc.stdout.read()
        proc.stdout.close()
        proc.stderr.read()
        proc.stderr.close()
        return proc.wait(timeout=30), results, "".join(lines) + rest

    def unverified(self):
        """The unverified markers the shim published to this host's quota log."""
        path = Path(self.room.quota_path)
        rows = [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
        return [r for r in rows if r.get("status") == "unverified"]


class TestRepeatedInit(DialogueCase):
    def test_every_real_stream_passes_with_its_report(self):
        streams = sorted(FIXTURES.glob("*.jsonl"))
        self.assertEqual(len(streams), 6)
        for stream in streams:
            with self.subTest(stream=stream.name):
                rc, results, _ = self.replay(stream)
                recorded = [json.loads(l) for l in stream.read_text().splitlines()]
                self.assertEqual(rc, 0)
                self.assertEqual([r["subtype"] for r in results], ["success", "success"])
                self.assertEqual(results[1]["result"], recorded[-1]["result"], "the report's result is returned")
                self.assertTrue(results[1]["result"].lstrip().startswith("1."))
        self.assertEqual(self.unverified(), [])
        self.assertFalse(self.room.stop_path.exists())

    def test_a_later_init_from_another_session_holds_the_report(self):
        stream = sorted(FIXTURES.glob("*.jsonl"))[0]
        rc, results, _ = self.replay(stream, {"session_id": "00000000-0000-0000-0000-000000000000"})
        self.assertEqual(rc, shim.UNVERIFIED)
        self.assertEqual(results[0]["subtype"], "success", "the errand's own result still passes")
        self.assertIn("a later init event does not repeat the first (session_id)", results[1]["result"])
        self.assertEqual(len(self.unverified()), 1)
        self.assertFalse(self.room.stop_path.exists())

    def test_a_later_init_without_a_session_holds_the_report(self):
        stream = sorted(FIXTURES.glob("*.jsonl"))[1]
        rc, results, _ = self.replay(stream, {"session_id": None})
        self.assertEqual(rc, shim.UNVERIFIED)
        self.assertIn("(session_id)", results[1]["result"])

    def test_a_later_init_naming_another_model_holds_the_report(self):
        stream = sorted(FIXTURES.glob("*.jsonl"))[2]
        rc, results, _ = self.replay(stream, {"model": "claude-opus-5-5"})
        self.assertEqual(rc, shim.UNVERIFIED)
        self.assertIn("init names model 'claude-opus-5-5'", results[1]["result"])
        self.assertIn("does not repeat the first (model)", results[1]["result"])
        self.assertFalse(self.room.stop_path.exists())

    def test_a_later_init_with_a_key_source_is_a_hard_stop(self):
        stream = sorted(FIXTURES.glob("*.jsonl"))[3]
        rc, results, _ = self.replay(stream, {"apiKeySource": "ANTHROPIC_API_KEY"})
        self.assertEqual(rc, shim.STOPPED)
        self.assertIn("apiKeySource was 'ANTHROPIC_API_KEY'", results[1]["result"])
        self.assertIn("apiKeySource was 'ANTHROPIC_API_KEY'", self.room.stop_path.read_text())

    def test_a_later_init_from_another_build_is_a_hard_stop(self):
        stream = sorted(FIXTURES.glob("*.jsonl"))[4]
        rc, results, _ = self.replay(stream, {"claude_code_version": "2.1.310"})
        self.assertEqual(rc, shim.STOPPED)
        self.assertIn("not the pinned 2.1.309", self.room.stop_path.read_text())


if __name__ == "__main__":
    unittest.main()
