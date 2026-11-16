"""An installed runner starts outside its folder under isolated and safe-path Python startup.

Each case copies only public runtime files into its own scratch installation and puts a deterministic Claude
stand-in, plus a fail-closed Codex stand-in, first on PATH. No user configuration or provider command is read.
"""
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BANK = REPO / "tests" / "fixtures" / "bank_small"


class TestIsolatedStartup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-startup-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.install, self.other, self.bin = (self.root / name for name in ("install", "other", "bin"))
        for folder in (self.install, self.other, self.bin, self.root / "home", self.root / "tmp"):
            folder.mkdir()
        for source in REPO.glob("*.py"):
            if "_sealed" not in source.name:
                shutil.copyfile(source, self.install / source.name)
        budget = self.install / "docs" / "ordo" / "budget.json"
        budget.parent.mkdir(parents=True)
        shutil.copyfile(REPO / "docs" / "ordo" / "budget.json", budget)
        fake = self.install / "standin_claude.py"
        shutil.copyfile(REPO / "tests" / "fake_claude.py", fake)
        (self.bin / "claude").write_text(
            "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -B " + shlex.quote(str(fake)) + ' "$@"\n',
            encoding="utf-8")
        (self.bin / "codex").write_text(
            '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "0.0 unused-stand-in"; exit 0; fi\n'
            'echo "unexpected Codex invocation" >&2\nexit 97\n', encoding="utf-8")
        for name in ("claude", "codex"):
            (self.bin / name).chmod(0o755)
        self.env = {"PATH": str(self.bin) + ":/usr/bin:/bin", "HOME": str(self.root / "home"),
                    "TMPDIR": str(self.root / "tmp"), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                    "FAKE_CLAUDE": "single", "FAKE_CLAUDE_LOG": str(self.root / "question-calls.jsonl")}

    def modes(self):
        """Safe-path flags and environment support begin in Python 3.11; isolation is supported everywhere."""
        result = [("isolated", ["-I"], {})]
        if sys.version_info >= (3, 11):
            result += [("safe-flag", ["-P"], {}), ("safe-env", [], {"PYTHONSAFEPATH": "1"})]
        return result

    def command(self, flags, extra_env, words):
        """Run an absolute script path from a distinct cwd with no inherited Python or agent environment."""
        proc = subprocess.run([sys.executable, *flags, "-B", str(self.install / "probatio.py"), *words],
                              cwd=self.other, env=dict(self.env, **extra_env), capture_output=True, text=True,
                              timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stdout + proc.stderr)
        return proc

    def arguments(self, out):
        return ["--agent", "claude", "--target-type", "organization", "--target", "Mozilla",
                "--tier", "smoke", "--class", "I", "--bank", str(BANK), "--memory", "off",
                "--jobs", "4", "--workdir", str(self.other), "--out", str(out)]

    def test_help_from_another_folder(self):
        for name, flags, extra_env in self.modes():
            with self.subTest(mode=name):
                proc = self.command(flags, extra_env, ["--help"])
                self.assertIn("--agent", proc.stdout)
                self.assertIn("{claude,codex}", proc.stdout)

    def test_dry_run_from_another_folder(self):
        for name, flags, extra_env in self.modes():
            with self.subTest(mode=name):
                out = self.root / ("dry-" + name)
                proc = self.command(flags, extra_env, self.arguments(out) + ["--dry-run"])
                self.assertIn("Claude Code", proc.stdout)
                self.assertIn("calls", proc.stdout)
                self.assertFalse((self.root / "question-calls.jsonl").exists())

    def test_finished_class_i_standin_from_another_folder(self):
        for name, flags, extra_env in self.modes():
            with self.subTest(mode=name):
                out = self.root / ("finished-" + name)
                self.command(flags, extra_env, self.arguments(out))
                record = json.loads((out / "run.json").read_text(encoding="utf-8"))
                self.assertEqual(record["classes"], ["I"])
                replies = [json.loads(line) for line in (out / "replies.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertTrue(replies)
                self.assertTrue(all(reply.get("reply") == "A" for reply in replies))
                self.assertTrue((out / "scorecard.json").is_file())
                self.assertTrue((out / "results.html").is_file())


if __name__ == "__main__":
    unittest.main()
