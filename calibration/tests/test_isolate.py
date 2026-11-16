"""The clean room: what it builds, the environment it gives probatio, every refusal (an API key, a parent memory
file, an unclean config dir, an AGENTS file for Codex, a stray file in the working directory, the wrong build, a login
that is not the token, a hard stop), and that the token never reaches a file or the output. The CLIs are fakes
(fakes.py); no test calls a model.

    python3 -m unittest discover -s calibration/tests
"""
import contextlib
import io
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

FAKE_API_KEY = "-".join(("test", "api", "key", "not", "real"))  # built at run time, so secret scanners never see a key-shaped literal

MEMORY = b"# Notes for Claude\n\n- A test memory.\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.host = fakes.Host()
        self.cfg = I.load_local(self.host.local)
        self.environ = fakes.clean_environ()

    def tearDown(self):
        self.host.close()

    def room(self, run_id="r1", agent="claude", memory="on", data=MEMORY):
        return I.build(self.cfg, run_id, agent, memory, I.EFFORT, data)

    def problems(self, room, environ=None):
        env = I.environment(room, self.cfg, base=self.environ)
        return I.problems(room, self.cfg, env, environ if environ is not None else self.environ)


class Build(Base):
    def test_layout_and_memory_hash(self):
        room = self.room()
        for d in (room.config_dir, room.codex_dir, room.work_dir, room.bin_dir):
            self.assertTrue(d.is_dir(), d)
        self.assertEqual(room.memory_path.read_bytes(), MEMORY)
        self.assertEqual(len(room.memory_sha256), 64)
        self.assertEqual(sorted(p.name for p in room.config_dir.iterdir()), [])
        self.assertEqual([p.name for p in room.codex_dir.iterdir()], ["auth.json"])
        self.assertEqual(os.readlink(room.codex_dir / "auth.json"), str(self.host.auth))
        spec = json.loads((room.bin_dir / "room.json").read_text())
        self.assertEqual((spec["memory"], spec["effort"], spec["codex_cap"]), ("on", "medium", I.CODEX_CAP))

    def test_codex_memory_is_agents_md(self):
        self.assertEqual(self.room(agent="codex").memory_path.name, "AGENTS.md")

    def test_reopening_with_another_memory_is_refused(self):
        self.room()
        self.room()                                         # the same bytes: a resume
        with self.assertRaises(I.Refusal):
            self.room(data=b"# other\n")

    def test_bad_ids_and_values_are_refused(self):
        for kw in ({"run_id": "../x"}, {"run_id": ""}, {"agent": "gemini"}, {"memory": "both"}):
            with self.subTest(kw=kw), self.assertRaises(I.Refusal):
                self.room(**kw)
        with self.assertRaises(I.Refusal):
            I.build(self.cfg, "r2", "claude", "on", "ultracode")

    def test_a_clean_room_passes(self):
        self.assertEqual(self.problems(self.room()), [])
        self.assertEqual(self.problems(self.room("r-off", memory="off")), [])
        self.assertEqual(self.problems(self.room("r-codex", agent="codex")), [])


class Environment(Base):
    def test_rules_in_force_and_caller_variables_dropped(self):
        room = self.room()
        base = dict(self.environ, ANTHROPIC_API_KEY="", CLAUDE_CONFIG_DIR="/home/someone/.claude", OPENAI_API_KEY="x",
                    CLAUDE_CODE_OAUTH_TOKEN_BACKUP="other", CLAUDECODE="1", TMUX="/tmp/t", CLAUDE_EFFORT="xhigh")
        env = I.environment(room, self.cfg, base=base)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(room.config_dir))
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], fakes.TOKEN)
        self.assertEqual(env["CLAUDE_CODE_EFFORT_LEVEL"], "medium")
        self.assertEqual(env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")
        self.assertEqual(env["DISABLE_AUTOUPDATER"], "1")
        self.assertEqual(env["CODEX_HOME"], str(room.codex_dir))
        self.assertTrue(env["PATH"].startswith(str(room.bin_dir) + os.pathsep))
        self.assertNotIn("CLAUDE_CODE_DISABLE_CLAUDE_MDS", env)
        for gone in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN_BACKUP", "CLAUDECODE", "TMUX",
                     "CLAUDE_EFFORT"):
            self.assertNotIn(gone, env)

    def test_off_condition_switches_claude_md_off_and_auto_memory_stays_off(self):
        env = I.environment(self.room(memory="off"), self.cfg, base=self.environ)
        self.assertEqual(env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"], "1")
        self.assertEqual(env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")


class Token(Base):
    def write(self, text):
        self.host.secrets.write_text(text)

    def test_forms(self):
        for text in (f"export {fakes.TOKEN_NAME}={fakes.TOKEN}\n", f'{fakes.TOKEN_NAME}="{fakes.TOKEN}"\n',
                     f"export {fakes.TOKEN_NAME}=old\nexport {fakes.TOKEN_NAME}='{fakes.TOKEN}'  # newest\n"):
            with self.subTest(text=text.split("=")[0]):
                self.write(text)
                self.assertEqual(I.read_token(self.host.secrets, fakes.TOKEN_NAME), fakes.TOKEN)

    def test_refusals_never_show_the_value(self):
        cases = (f"export {fakes.TOKEN_NAME}=\n", "export OTHER=1\n", f"export {fakes.TOKEN_NAME}=$(cat {fakes.TOKEN})\n")
        for text in cases:
            with self.subTest(text=text):
                self.write(text)
                with self.assertRaises(I.Refusal) as e:
                    I.read_token(self.host.secrets, fakes.TOKEN_NAME)
                self.assertNotIn(fakes.TOKEN, str(e.exception))
        with self.assertRaises(I.Refusal):
            I.read_token(self.host.dir / "missing.sh", fakes.TOKEN_NAME)

    def test_token_never_reaches_a_file_or_the_output(self):
        out, err = io.StringIO(), io.StringIO()
        with unittest.mock.patch.dict(os.environ, self.environ, clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = I.main(["--local", str(self.host.local), "--check"])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("check passed", out.getvalue())
        self.assertNotIn(fakes.TOKEN, out.getvalue() + err.getvalue())
        self.assertEqual(self.host.files_containing(fakes.TOKEN), [])


class Refusals(Base):
    def test_api_key_in_the_environment(self):
        room = self.room()
        found = self.problems(room, dict(self.environ, **{"ANTHROPIC_API_KEY": FAKE_API_KEY}))
        self.assertTrue(any("ANTHROPIC_API_KEY" in p for p in found), found)
        self.assertNotIn("test-api-key-not-real", " ".join(found))
        self.assertEqual(self.problems(room, dict(self.environ, ANTHROPIC_API_KEY="")), [])
        for var in ("ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "ANTHROPIC_AUTH_TOKEN"):
            self.assertTrue(self.problems(room, dict(self.environ, **{var: "1"})), var)

    def test_api_key_refuses_through_main(self):
        err = io.StringIO()
        with unittest.mock.patch.dict(os.environ, dict(self.environ, **{"ANTHROPIC_API_KEY": FAKE_API_KEY}), clear=True), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(I.main(["--local", str(self.host.local), "--check"]), 2)
        self.assertIn("ANTHROPIC_API_KEY", err.getvalue())

    def test_planted_parent_memory_files(self):
        room = self.room()
        for name in ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "AGENTS.override.md", ".claude"):
            with self.subTest(name=name):
                planted = room.root / "work" / name                 # a parent of work/<run>
                planted.mkdir() if name == ".claude" else planted.write_text("persona")
                found = self.problems(room)
                self.assertTrue(any(str(planted) in p for p in found), found)
                planted.rmdir() if name == ".claude" else planted.unlink()
        self.assertEqual(self.problems(room), [])

    def test_unclean_config_dir(self):
        room = self.room()
        plants = {"CLAUDE.md": "x", "settings.json": "{}", "rules/a.md": "x", ".credentials.json": "{}",
                  "projects/-k/memory/MEMORY.md": "index", "plugins/p/plugin.json": "{}"}
        for rel, text in plants.items():
            with self.subTest(rel=rel):
                path = room.config_dir / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                self.assertTrue(self.problems(room))
                path.unlink()
                for d in path.relative_to(room.config_dir).parents:      # the folders this plant made, deepest first
                    if d != Path(".") and not any((room.config_dir / d).iterdir()):
                        (room.config_dir / d).rmdir()
        (room.config_dir / "link").symlink_to(self.host.secrets)
        self.assertTrue(any("symlink" in p for p in self.problems(room)))
        (room.config_dir / "link").unlink()
        (room.config_dir / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "x"}}))
        self.assertTrue(any("stored login" in p for p in self.problems(room)))
        (room.config_dir / ".claude.json").write_text(json.dumps({"userID": "u", "cachedGrowthBookFeatures": {}}))
        (room.config_dir / "projects" / "-k" / "memory").mkdir(parents=True, exist_ok=True)   # empty dirs are state
        self.assertEqual(self.problems(room), [])

    def test_operator_config_dir_is_refused(self):
        found = I.config_problems(self.host.root, [self.host.root])
        self.assertTrue(any("operator" in p for p in found))

    def test_codex_user_dir_with_an_agents_file(self):
        room = self.room(agent="codex")
        (room.codex_dir / "AGENTS.md").write_text("persona port")
        self.assertTrue(any("AGENTS.md" in p for p in self.problems(room)))

    def test_codex_auth_must_link_to_the_login(self):
        room = self.room(agent="codex")
        (room.codex_dir / "auth.json").unlink()
        (room.codex_dir / "auth.json").write_text("{}")             # a copy, not the link
        self.assertTrue(any("must be a link" in p for p in self.problems(room)))

    def test_stray_file_in_the_working_directory(self):
        room = self.room()
        (room.work_dir / "notes.txt").write_text("x")
        self.assertTrue(any("notes.txt" in p for p in self.problems(room)))

    def test_wrong_build_and_wrong_login(self):
        room = self.room()
        env = I.environment(room, self.cfg, base=dict(self.environ, FAKE_CLAUDE_VERSION="2.1.312 (Claude Code)"))
        self.assertTrue(any("not Claude Code 2.1.309" in p for p in I.problems(room, self.cfg, env, self.environ)))
        env = I.environment(room, self.cfg, token="", base=self.environ)
        self.assertTrue(any("not the subscription token" in p for p in I.problems(room, self.cfg, env, self.environ)))

    def test_hard_stop_in_force(self):
        room = self.room()
        room.stop_path.write_text("apiKeySource was 'ANTHROPIC_API_KEY'\n")
        self.assertTrue(any("hard stop" in p for p in self.problems(room)))

    def test_missing_local_json(self):
        with self.assertRaises(I.Refusal):
            I.load_local(self.host.dir / "nope.json")


if __name__ == "__main__":
    unittest.main()
