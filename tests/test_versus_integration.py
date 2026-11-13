"""The versus command with its real page: one integration test. A two-name and a three-name quick run go through
probatio.py's own __main__ exactly as a user runs it, with no stand-in page anywhere in the child (versus_page.py
beside probatio.py is the one imported), on the stand-in agent CLI (tests/fixtures/versus_agent.py, first on PATH as
`claude`) and the complete synthetic bank the other tests use (--bank, tests/test_versus_fixture.py). The test checks
that the real page's files are written, that the lines printed at the end are versus_page.answer_lines' own followed
by the trust sentence and the folder, that the page's head names the agent and model of card["launch"] and its last
fold holds card["trust"], and that a rescore of the folder writes the same files again with the rescored line in the
head. The command's other tests (test_versus_run.py) keep the stand-in page, since they assert what the command alone
controls. No network, no model.

    python3 -m unittest tests.test_versus_integration
"""
import contextlib
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO))                       # first, so versus_page is the repo's own module
import ordo  # noqa: E402
import probatio as P  # noqa: E402
import versus as VS  # noqa: E402
import versus_page as VP  # noqa: E402
import versus_run as VR  # noqa: E402
from test_versus_fixture import full_bank, full_bank_folder  # noqa: E402

AGENT = FIXTURES / "versus_agent.py"
OB = ordo.load_ordo_bank()
TWO = ("--versus", "individual=Jane Doe", "--versus", "organization=Acme Corp")
THREE = TWO + ("--versus", "nation=Norland")
PAGE_FILES = ("report.md", "results.csv", "results.svg", "scorecard.json", "scorecard.md")
MEMORY = ("# Invented notes\n\n" + "A line of notes the stand-in never reads.\n" * 50).encode("utf-8")


class CommandWithItsPage(unittest.TestCase):
    RULE = "first"                                          # the stand-in agent's rule (tests/fixtures/versus_rules.py)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="versus-page-run-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home, self.work, self.bin = self.tmp / "home", self.tmp / "work", self.tmp / "bin"
        for d in (self.home, self.work, self.bin):
            d.mkdir()
        (self.bin / "claude").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{AGENT}" "$@"\n')
        (self.bin / "codex").write_text('#!/bin/sh\necho "stand-in codex: no agent calls" >&2\nexit 97\n')
        for f in ("claude", "codex"):
            (self.bin / f).chmod(0o755)
        (self.home / ".claude").mkdir()                     # one memory file, whose size the page's head shows
        (self.home / ".claude" / "CLAUDE.md").write_bytes(MEMORY)

    def command(self, *args):
        """probatio.py itself, as a user runs it, with the stand-in CLI first on PATH and a scratch HOME: stdout. The
        child's environment is built here and never printed or written."""
        env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", HOME=str(self.home),
                   VERSUS_RULE=self.RULE, VERSUS_AGENT_LOG=str(self.tmp / "agent.log"))
        env.pop("CLAUDE_CONFIG_DIR", None)
        proc = subprocess.run([sys.executable, str(REPO / "probatio.py"), "--agent", "claude", "--workdir",
                               str(self.work), *args], capture_output=True, text=True, env=env, timeout=900,
                              cwd=str(self.tmp))
        self.assertEqual(proc.returncode, 0, proc.stderr[-3000:])
        return proc.stdout

    @staticmethod
    def page_text(out):
        return {name: (out / name).read_text(encoding="utf-8") for name in PAGE_FILES}

    def as_the_run(self):
        """The test process under the run's HOME: the page writes the config folder in v4's own words, "~/.claude"
        against the HOME of the process that writes it (probatio.memory_words), so the expected page, and a rescore
        on the same machine, are made under the HOME the run had."""
        return mock.patch.dict(os.environ, {"HOME": str(self.home)})

    @staticmethod
    def last_fold(text):
        return text[text.rindex("<details>"):text.rindex("</details>")]

    def test_two_and_three_names_through_the_command_and_its_page(self):
        here = Path(importlib.import_module("versus_page").__file__).resolve()
        self.assertEqual(here, REPO / "versus_page.py")             # no stand-in or folder shadows the real page
        bank = full_bank()
        for label, names in (("two", TWO), ("three", THREE)):
            with self.subTest(label):
                out = self.tmp / label
                stdout = self.command(*names, "--tier", "quick", "--jobs", "16", "--bank",
                                      str(full_bank_folder()[1]), "--out", str(out))
                card = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
                rows = VS.table(out, bank, OB)
                written = self.page_text(out)
                with self.as_the_run():
                    made, answer, head = VP.files(card, rows), VP.answer_lines(card), VP.head_lines(card)[0]
                self.assertEqual(written, made)                       # the real page's files, every one of them
                printed = answer + [VS.TRUST, f"Results: {P.display(out)}"]
                self.assertEqual(stdout.splitlines()[-len(printed):], printed)
                self.assertEqual(written["scorecard.md"].count(head), 1)
                launch = card["launch"]
                for part in (launch["model"], launch["cli_version"]):
                    self.assertIn(P.display(part), head)              # the agent and model the command recorded
                self.assertEqual((launch["agent"], launch["model"]), ("claude", "CLI default"))
                self.assertEqual(launch["memory_bytes"], len(MEMORY))   # the memory's size, end to end to the head
                self.assertIn(f"memory on: 1 file, {P.size_words(len(MEMORY))}, from ~/.claude", head)
                for fname in ("scorecard.md", "report.md"):
                    self.assertIn(ordo.md(card["trust"]), self.last_fold(written[fname]))
                self.assertEqual(card["trust"], VS.TRUST)

                now = datetime(2026, 11, 12, 1, 0, 0, tzinfo=timezone.utc)
                with self.as_the_run(), contextlib.redirect_stdout(io.StringIO()):
                    VR.rescore(out, bank, OB, now=now)             # the real page again, no stand-in
                again = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
                self.assertEqual({k: v for k, v in again.items() if k != "rescored"}, card)
                rewritten = self.page_text(out)
                with self.as_the_run():
                    remade, rescored_head = VP.files(again, rows), VP.head_lines(again)
                self.assertEqual(rewritten, remade)                   # the same files, from the rescored card
                line = P.display(P.rescored_line(again["rescored"]))
                self.assertTrue(any(x.startswith(line) for x in rescored_head[1:]), rescored_head)
                self.assertNotIn(line, written["scorecard.md"])
                self.assertIn(line, rewritten["scorecard.md"])
                self.assertEqual(sorted(p.name for p in (out / P.PREVIOUS).iterdir()), ["20261112T010000Z"])
                self.assertEqual(self.page_text(out / P.PREVIOUS / "20261112T010000Z"), written)


class ClassesThroughTheCommand(CommandWithItsPage):
    """versus by class end to end: a two-name quick and a three-name standard run through probatio.py's own __main__
    on the stand-in agent (rule first_name, its episodes through tests/fake_claude.py), scored by the class scorer.
    The class block the scorer wrote on scorecard.json is the one the page prints, every number of it, read back from
    the page mechanically; the answer lines open, after the head, with the three class lines; and --rescore of the
    folder writes the same files again."""
    RULE = "first_name"

    def test_the_scorers_block_is_the_one_the_page_prints(self):
        import test_versus_page_classes as PC
        bank = full_bank()
        for label, names, tier in (("two-quick", TWO, "quick"), ("three-standard", THREE, "standard")):
            with self.subTest(label):
                out = self.tmp / label
                stdout = self.command(*names, "--tier", tier, "--jobs", "16", "--bank", str(full_bank_folder()[1]),
                                      "--out", str(out))
                card = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
                self.assertEqual(card["classes"]["recipe"], f"versus-{tier}@2")
                self.assertIsNone(VP.class_problem(card))                 # the real block reads
                arm = VP.primary(card)
                written = self.page_text(out)
                lines = PC.class_block_lines(written["scorecard.md"])
                fixed = {"{}: {}".format(*VP.CLASS_LINES[k]) for k in VP.CLASS_KEYS} | {VP.CLASSES_FOOT} | {
                    "  " + VP.validity_text(w) for w in VP.VALIDITY_ORDER}           # the page's own fixed lines
                self.assertEqual(PC.numbers_in(x for x in lines if x not in fixed),
                                 PC.expected_numbers(card, arm))                      # every number, mechanically
                self.assertNotIn(VP.CLASSES_UNREADABLE, written["scorecard.md"])
                with self.as_the_run():
                    answer = VP.answer_lines(card)
                self.assertEqual([x.split(":")[0] for x in answer[1:4]], ["Class I", "Class II", "Class III"])
                self.assertEqual(PC.numbers_in(x.rsplit(" (0 to 100", 1)[0] for x in answer[1:4]),
                                 PC.expected_numbers(card, arm, "terminal"))
                printed = stdout.splitlines()
                self.assertEqual(printed[-len(answer) - 2:-2], answer)    # then the trust sentence and the folder
                pairs = card["classes"]["arms"][arm]["pairs"]
                self.assertTrue(all(p["class1"]["value"] == 100.0 for p in pairs.values()))   # first_name: 100
                if tier == "quick":
                    self.assertTrue(all(p["class3"]["status"] == "not asked" for p in pairs.values()))
                now = datetime(2026, 11, 13, 1, 0, 0, tzinfo=timezone.utc)
                with self.as_the_run(), contextlib.redirect_stdout(io.StringIO()):
                    VR.rescore(out, bank, OB, now=now)
                again = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
                self.assertEqual({k: v for k, v in again.items() if k != "rescored"}, card)
                rows = VS.table(out, bank, OB)
                with self.as_the_run():
                    self.assertEqual(self.page_text(out), VP.files(again, rows))   # the same files, rescored


if __name__ == "__main__":
    unittest.main()
