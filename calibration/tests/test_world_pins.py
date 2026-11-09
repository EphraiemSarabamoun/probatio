"""The one MCP server a subject call may name: probatio's own world.py, pinned in room.json by its interpreter, its
path and sha256, and the run's episodes/ folder (guard.world_server_problems; the integration's item 4, W4-e's open
question 2). Claude's --mcp-config and --allowedTools, and Codex's mcp_servers.<name>.* overrides, are refused for any
other value. Fake CLIs only, no model.

    python3 -m unittest discover -s calibration/tests
"""
import json
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import fakes  # noqa: E402
import isolate as I  # noqa: E402
import test_regressions as TR  # noqa: E402

CODEX_BASE = ["exec", "--skip-git-repo-check", "-s", "read-only", "--ignore-user-config", "--ignore-rules",
              "-m", "fake-model"]


class WorldServer(TR.Base):
    def setUp(self):
        super().setUp()
        self.world_py = I.world_script(self.cfg)
        self.world_py.write_text("# synthetic world.py\n")
        self.results = Path(self.cfg["results_dir"])

    def room(self, run, agent="claude"):
        return I.build(self.cfg, run, agent, "on", memory_bytes=b"# synthetic\n", model="fake-model",
                       out_dir=self.results / run)

    def args(self, run, episode="with-anulus-3", script=None, root=None):
        folder = (root or self.results / run / "episodes") / episode
        return ["-I", "-B", str(script or self.world_py), str(folder / "world.json"), str(folder / "record.jsonl"),
                str(folder / "close")]

    def claude_args(self, server="anulus", command=None, args=None, tools=None, extra_servers=()):
        servers = {server: {"type": "stdio", "command": command or sys.executable, "args": args}}
        servers.update({n: {"type": "stdio", "command": sys.executable, "args": args} for n in extra_servers})
        tools = tools if tools is not None else f"mcp__{server}__card_statement,mcp__{server}__file_refund_claim"
        return TR.PRINT + ["--mcp-config", json.dumps({"mcpServers": servers}), "--max-turns", "30",
                           "--allowedTools", tools]

    def codex_args(self, run, name="anulus", command=None, args=None, approval="approve", second=False):
        args = args if args is not None else self.args(run)
        out = CODEX_BASE + ["-c", f"mcp_servers.{name}.command={json.dumps(command or sys.executable)}",
                            "-c", f"mcp_servers.{name}.args={json.dumps(args)}",
                            "-c", f'mcp_servers.{name}.default_tools_approval_mode="{approval}"']
        if second:
            out += ["-c", f"mcp_servers.other.command={json.dumps(sys.executable)}",
                    "-c", f"mcp_servers.other.args={json.dumps(args)}"]
        return out + ["-o", str(self.host.dir / "reply.txt"), "-"]

    def codex_calls(self):
        path = self.host.bin / "codex-calls.jsonl"
        return path.read_text().splitlines() if path.exists() else []

    def test_the_world_server_passes_for_claude(self):
        room = self.room("claude-ok")
        p = self.cli(room, self.claude_args(args=self.args("claude-ok")))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_the_world_server_passes_for_codex(self):
        room = self.room("codex-ok", "codex")
        p = self.cli(room, self.codex_args("codex-ok"), which="codex")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(self.codex_calls()), 1)

    def test_claude_refuses_any_other_server(self):
        other_script = self.host.dir / "world.py"
        other_script.write_text("# synthetic world.py\n")             # same bytes, another path
        cases = {
            "another interpreter": dict(command="/bin/sh"),
            "another script": dict(args=self.args("c", script=other_script)),
            "an episode of another run": dict(args=self.args("c", root=self.results / "other-run" / "episodes")),
            "a folder name with a slash": dict(args=self.args("c", episode="a/b")),
            "extra arguments": dict(args=self.args("c") + ["--debug"]),
            "a second server": dict(extra_servers=("other",)),
            "a tool of another server": dict(tools="mcp__other__read_file"),
            "a built-in tool": dict(tools="Bash"),
        }
        for i, (name, kw) in enumerate(cases.items()):
            with self.subTest(case=name):
                run = f"claude-bad-{i}"
                room = self.room(run)
                kw.setdefault("args", self.args(run))
                if "root" in name or "another run" in name:
                    kw["args"] = self.args(run, root=self.results / "other-run" / "episodes")
                p = self.cli(room, self.claude_args(**kw))
                self.assertNotEqual(p.returncode, 0, name)
                self.assertEqual(self.calls(), [], name)
                room.stop_path.unlink()                                # each case starts without the last one's STOP

    def test_claude_refuses_allowed_tools_without_the_world(self):
        room = self.room("claude-tools-only")
        p = self.cli(room, TR.PRINT + ["--allowedTools", "mcp__anulus__card_statement"])
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_a_changed_world_py_is_refused(self):
        room = self.room("claude-changed")
        self.world_py.write_text("# a different world.py\n")
        p = self.cli(room, self.claude_args(args=self.args("claude-changed")))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("world.py registered", room.stop_path.read_text())
        self.assertEqual(self.calls(), [])

    def test_a_room_without_a_run_folder_refuses_every_server(self):
        room = I.build(self.cfg, "no-out", "claude", "on", memory_bytes=b"# synthetic\n", model="fake-model")
        p = self.cli(room, self.claude_args(args=self.args("no-out")))
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_codex_refuses_any_other_server(self):
        cases = {
            "another interpreter": dict(command="/bin/sh"),
            "another script": dict(args=["-I", "-B", "/tmp/world.py"] + self.args("x")[3:]),
            "approval": dict(approval="never"),
            "a second server": dict(second=True),
            "unparsable args": dict(args="not json"),
        }
        for i, (name, kw) in enumerate(cases.items()):
            with self.subTest(case=name):
                run = f"codex-bad-{i}"
                room = self.room(run, "codex")
                if "args" not in kw:
                    kw["args"] = self.args(run)
                elif kw["args"] == "not json":
                    kw["args"] = None
                args = self.codex_args(run, **kw)
                if name == "unparsable args":
                    at = next(i for i, a in enumerate(args) if a.startswith("mcp_servers.anulus.args="))
                    args[at] = "mcp_servers.anulus.args=[not json"
                p = self.cli(room, args, which="codex")
                self.assertNotEqual(p.returncode, 0, name)
                self.assertEqual(self.codex_calls(), [], name)
                room.stop_path.unlink()


class ProbatiosOwnCommands(TR.Base):
    """probatio v4's real errand command lines, built by its own start_episode, claude_command, codex_command and
    codex_resume_command around the repository's world.py, pass the guard unchanged."""

    def test_probatio_errand_commands_pass_the_guard(self):
        import guard
        sys.path.insert(0, str(HERE.parent.parent))
        import probatio as P
        self.cfg["probatio"] = str(HERE.parent.parent / "probatio.py")       # world.py is the repository's own
        results = Path(self.cfg["results_dir"])
        world = json.dumps({"tools": [{"name": "card_statement"}, {"name": "transaction_detail"},
                                      {"name": "file_refund_claim"}, {"name": "activity_log"}]})
        call = {"world": world, "row": {"server": "anulus", "max_turns": 30}, "condition": "with",
                "dataset": "anulus", "id": "U-17"}
        for agent in ("claude", "codex"):
            with self.subTest(agent=agent):
                run = f"real-{agent}"
                room = I.build(self.cfg, run, agent, "on", memory_bytes=b"# synthetic\n", model="claude-sonnet-5-5",
                               out_dir=results / run)
                spec = json.loads((room.bin_dir / "room.json").read_text())
                server = P.start_episode(call, P.episode_dir(results / run, call))
                if agent == "claude":
                    cmd, _ = P.claude_command("with", "claude-sonnet-5-5", server, "medium", dialogue=True,
                                              auto_memory="off")
                    self.assertEqual(guard.claude_args_problems(spec, cmd[1:]), [])
                else:
                    reply = str(results / run / "reply.txt")
                    for cmd in (P.codex_command("with", "claude-sonnet-5-5", "medium", ["plugins"], reply, server,
                                                keep_session=True),
                                P.codex_resume_command("with", "claude-sonnet-5-5", "medium", ["plugins"], reply,
                                                       "0199a2b4-session")):
                        self.assertEqual(guard.codex_args_problems(spec, cmd[1:]), [], cmd)


class Registration(TR.Base):
    def test_world_py_is_part_of_the_fingerprint(self):
        import run as R
        spec = {"run_id": "fp", "agent": "claude", "model": "fake-model", "memory": "on", "jobs": 1, "cli": "v4",
                "effort": "medium", "probatio_args": [], "memory_bytes": b"# synthetic\n"}
        world_py = I.world_script(self.cfg)
        world_py.write_text("# one\n")
        before = R.fingerprint(spec, self.cfg)[0]
        world_py.write_text("# two\n")
        self.assertNotEqual(R.fingerprint(spec, self.cfg)[0], before)


if __name__ == "__main__":
    unittest.main()
