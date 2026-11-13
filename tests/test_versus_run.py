"""The versus command (versus_run.py, reached through probatio.py's --versus and --rescore): its refusals, each one
plain line with its exit code and nothing written; --dry-run, which starts nothing but the CLI's --version; the
preflight in its order with the 45-minute hint; two- and three-name runs to their result files with every arm; a run
stopped between calls and resumed to the same card; every refusal of a resume; --rescore, field for field and twice,
with its own refusals, on a folder made here and on one made on another machine; hostile names and folder names
through every line printed, enumerated from the parser; and the first half's stand-in adversaries run end to end.

Every run goes through tests/fixtures/run_versus.py, which is probatio.py's own __main__ with the stand-in page
(tests/fixtures/versus_page_standin.py) in place of versus_page, and a `claude` on PATH that runs the stand-in CLI
(tests/fixtures/versus_agent.py). The stand-in page is installed only in that child (and, in-process, only inside
a mock.patch.dict of sys.modules), never on a path where it could shadow the real page; the real page goes through
the command in tests/test_versus_integration.py. No model, no network; a `codex` on PATH that fails (exit 97) stands
first.

    python3 -m unittest tests.test_versus_run
"""
import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(FIXTURES))
import ordo  # noqa: E402
import probatio as P  # noqa: E402
import versus as VS  # noqa: E402
import versus_page_standin  # noqa: E402
import versus_run as VR  # noqa: E402
from test_versus_fixture import development_bank_folder, full_bank, full_bank_folder  # noqa: E402
from versus_rules import answer  # noqa: E402

RUNNER = FIXTURES / "run_versus.py"
AGENT = FIXTURES / "versus_agent.py"
OB = ordo.load_ordo_bank()
TWO = ("--versus", "individual=Jane Doe", "--versus", "organization=Acme Corp")
THREE = TWO + ("--versus", "nation=Norland")
UNSEEN_OK = "\n"                    # the only unseen character a line of output may hold: the program's own newline
HOSTILE = {"clear screen": "\x1b[2J\x1b[H", "carriage return": "Jane\rDoe", "forged line": "Jane\nprobatio: done",
           "backspaces": "Jane" + "\b" * 8, "right-to-left": "Jane‮eoD", "zero-width joiner": "Ja‍ne",
           "10,000 characters": "J" * 10000}


def snapshot(folder):
    """Every file under folder, relative path to bytes (links as their target text), or {} when it is absent."""
    folder = Path(folder)
    if not folder.exists():
        return {}
    out = {}
    for p in sorted(folder.rglob("*")):
        rel = str(p.relative_to(folder))
        if p.is_symlink():
            out[rel] = b"link:" + os.readlink(p).encode()
        elif p.is_file():
            out[rel] = p.read_bytes()
    return out


def unseen_in(text):
    """The unseen characters (probatio's unseen()) of text other than the newline."""
    return sorted({ch for ch in text if P.unseen(ch) and ch not in UNSEEN_OK})


class Base(unittest.TestCase):
    """A scratch home, work folder and bin per test: `claude` runs the stand-in CLI, `codex` fails with exit 97."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="versus-run-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home, self.work, self.bin = self.tmp / "home", self.tmp / "work", self.tmp / "bin"
        for d in (self.home, self.work, self.bin):
            d.mkdir()
        (self.bin / "claude").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{AGENT}" "$@"\n')
        (self.bin / "codex").write_text('#!/bin/sh\necho "stand-in codex: no agent calls" >&2\nexit 97\n')
        for f in ("claude", "codex"):
            (self.bin / f).chmod(0o755)
        self.log = self.tmp / "agent.log"

    def env(self, **extra):
        """The child's environment: this one with the stand-in bin first on PATH and a scratch HOME (so no real
        config folder is listed), plus extra. Nothing of it is ever printed or written."""
        env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", HOME=str(self.home),
                   VERSUS_AGENT_LOG=str(self.log))
        env.pop("CLAUDE_CONFIG_DIR", None)
        env.update(extra)
        return env

    def cli(self, *args, rule="first", timeout=900, **extra):
        """The command through run_versus.py: (exit code, stdout, stderr)."""
        proc = subprocess.run([sys.executable, str(RUNNER), "--agent", "claude", "--workdir", str(self.work), *args],
                              capture_output=True, text=True, env=self.env(VERSUS_RULE=rule, **extra),
                              timeout=timeout, cwd=str(self.tmp))
        return proc.returncode, proc.stdout, proc.stderr

    def run_ok(self, *args, **kw):
        code, out, err = self.cli(*args, **kw)
        self.assertEqual(code, 0, err[-3000:])
        return out, err

    def card(self, folder):
        return json.loads((Path(folder) / "scorecard.json").read_text(encoding="utf-8"))

    def starts(self):
        """The stand-in CLI's starts so far, as argv lists."""
        return [json.loads(x)["argv"] for x in self.log.read_text().splitlines()] if self.log.exists() else []


def fixture_bank():
    """The complete synthetic bank's folder (tests/test_versus_fixture.py), which the estimator can score."""
    return str(full_bank_folder()[1])


# ---- item 2: the command line's refusals -----------------------------------------------------------------------------
REFUSALS = [  # (case, extra arguments, exit code, words in the one line)
    ("an option of v4's single-type path", TWO + ("--target", "Acme"), 2, "--target belongs to probatio's other runs"),
    ("an option of the compare path", TWO + ("--compare", "individual"), 2, "--compare belongs to probatio's other"),
    ("--class", TWO + ("--class", "I"), 2, "--class belongs to probatio's other runs"),
    ("--name", TWO + ("--name", "Jane Doe"), 2, "--name belongs to probatio's other runs"),
    ("--target-type", TWO + ("--target-type", "nation"), 2, "--target-type belongs to probatio's other runs"),
    ("an abbreviation of --target-type", TWO + ("--target-t", "nation"), 2, "--target-type belongs"),
    ("a v3 flag", TWO + ("--dataset", "all"), 2, "--dataset belongs to probatio's other runs"),
    ("one name", ("--versus", "individual=Jane Doe"), 2, "takes two or three --versus TYPE=NAME"),
    ("four names", THREE + ("--versus", "nation=Selvar"), 2, "takes two or three --versus TYPE=NAME"),
    ("no type", ("--versus", "Jane Doe", "--versus", "organization=Acme Corp"), 2, "write TYPE=NAME"),
    ("an unknown type", ("--versus", "planet=Mars", "--versus", "organization=Acme"), 2, "write TYPE=NAME"),
    ("a type twice", ("--versus", "individual=Jane Doe", "--versus", "individual=Jane Roe"), 2,
     "names the individual twice"),
    ("a name the guard refuses", ("--versus", "individual=Jane Doe", "--versus", "organization=A) Acme"), 2,
     "the organization's name"),
    ("the same name twice", ("--versus", "individual=Acme", "--versus", "organization=acme"), 2, "twice"),
    ("the fixed third partner", ("--versus", "individual=Arvenia", "--versus", "organization=Acme Corp"), 2,
     "fixed third partner, the government of Arvenia"),
    ("its unfamiliar entity with strangers", THREE[:2] + ("--versus", "organization=Thirlbeck Systems", "--strangers",
                                                         "on"), 2, "is the unfamiliar roster's organization"),
    ("the joint secretariat", ("--versus", "individual=Jane Doe", "--versus", "organization=Joint Secretariat"), 2,
     "is the joint secretariat"),
    ("--rescore beside --versus", TWO + ("--rescore", "x"), 2, "--rescore takes a run folder and no other option"),
    ("a --config-dir that is no folder", TWO + ("--config-dir", "/nonexistent-versus-folder"), 2,
     "--config-dir /nonexistent-versus-folder: no folder is there"),
    ("--jobs 0", TWO + ("--jobs", "0"), 1, "--jobs must be at least 1."),
]


class CommandLine(Base):
    def test_each_refusal_is_one_line_with_its_exit_code_and_nothing_written(self):
        for case, args, code, words in REFUSALS:
            out = self.tmp / "out"
            before = snapshot(REPO / "results")
            got, stdout, stderr = self.cli(*args, "--out", str(out))
            lines = [x for x in stderr.splitlines() if x.strip()]
            with self.subTest(case=case):
                self.assertEqual(got, code, stderr)
                self.assertEqual(len(lines), 1, stderr)
                self.assertIn(words, lines[0])
                self.assertNotIn("usage:", stderr)
                self.assertEqual(stdout, "")
                self.assertFalse(out.exists())
                self.assertEqual(snapshot(REPO / "results"), before)
                self.assertEqual(self.starts(), [])             # no agent CLI started, not even --version

    def test_a_bad_value_is_argparses_own_line(self):
        code, _, err = self.cli(*TWO, "--tier", "full")
        self.assertEqual(code, 2)
        self.assertIn("argument --tier: invalid choice: 'full'", err)

    def test_the_hand_over_and_the_file_name_have_one_home(self):
        self.assertEqual(P.VERSUS_RUN_FILE, VS.RUN_FILE)
        self.assertTrue(VR.wants_versus(["--versus=individual=Jane"]))
        self.assertFalse(VR.wants_versus(["--versusx", "--target", "Jane"]))


# ---- item 4: the preflight and --dry-run -----------------------------------------------------------------------------
class Preflight(Base):
    def test_the_class_rows_on_each_arm_and_the_class_iii_note_once(self):
        """An @2 plan's preflight: under each arm's line, its Class I questions and (at standard) its Class III errand
        episodes with their minutes, which the arm's counts include; once, that Class III is asked at the standard
        tier only."""
        names = {"individual": "Jane Doe", "organization": "Acme Corp", "nation": "Norland"}
        for tier in ("standard", "quick"):
            stdout, _ = self.run_ok(*THREE, "--tier", tier, "--memory", "both", "--dry-run", "--bank", fixture_bank())
            lines = stdout.splitlines()
            counts = VS.count_plan(VS.plan(full_bank(), names, tier, {"memory": "both"}, OB).calls)["classes"]
            for label, c in counts.items():
                at = lines.index(next(x for x in lines if x.startswith("    " + VR.arm_words(label))))
                cls = lines[at + 1]
                self.assertTrue(cls.startswith(f"      of which by class: Class I {c['class1']['total']} questions ("),
                                (tier, cls))
                self.assertEqual(f"Class III {c['class3']['total']} errand episodes (" in cls, tier == "standard")
            self.assertEqual(lines.count(VR.CLASS3_NOTE), 1)

    def test_dry_run_starts_nothing_but_the_version_probe(self):
        out = self.tmp / "out"
        before = snapshot(REPO / "results")
        stdout, _ = self.run_ok(*TWO, "--tier", "quick", "--dry-run")
        self.assertEqual(self.starts(), [["--version"]])
        self.assertEqual(snapshot(REPO / "results"), before)
        self.assertIn("starts fresh", stdout)
        stdout, _ = self.run_ok(*TWO, "--tier", "quick", "--dry-run", "--out", str(out))
        self.assertEqual(sorted(snapshot(out)), ["rendered.jsonl"])          # v4's dry run with --out writes this
        self.assertEqual(self.starts(), [["--version"]] * 2)

    def test_the_preflight_in_its_order(self):
        stdout, _ = self.run_ok(*THREE, "--memory", "both", "--strangers", "on", "--dry-run", "--bank", fixture_bank())
        lines = stdout.splitlines()
        order = ["probatio versus: Jane Doe, Acme Corp and the government of Norland · standard tier · claude "
                 "(0.0-fake (Claude Code)), model CLI default, effort medium",
                 "  the names as the items write them: Jane Doe, a person; Acme Corp, an organization; the government "
                 "of Norland, a government",
                 "  the unfamiliar roster, in the names' places in its own arm: Karim Nassar, a person; Thirlbeck "
                 "Systems, an organization; the government of Arvenia, a government",
                 "  " + VR.BANK_STATUSES["registered"], "  config folder:",
                 "  memory files for the with-memory run:",
                 "  standard tier at --jobs 4, priced arm by arm:", "    your names, memory off",
                 "    your names, memory on", "    the unfamiliar roster, memory off",
                 "    the unfamiliar roster, memory on", "    all ", "  an estimate from ordo's rate", "  about ",
                 VR.MEASURES[0], VR.MEASURES[1], VR.MEASURES[2], "  folder "]
        at = 0
        for want in order:
            hit = next((i for i in range(at, len(lines)) if lines[i].startswith(want)), None)
            self.assertIsNotNone(hit, f"{want!r} after line {at} of:\n{stdout}")
            at = hit + 1
        hint = next(x for x in lines if x.startswith("  about ") and "minutes" in x)
        self.assertEqual([part.split(" (-")[0] for part in hint.split("; to shorten: ")[1].split(", or ")],
                         ["--tier quick", "--memory on", "--strangers off"])        # --jobs 4 is the time model's
        names = {"individual": "Jane Doe", "organization": "Acme Corp", "nation": "Norland"}
        counts = VS.count_plan(VS.plan(full_bank(), names, "standard", {"memory": "both", "strangers": True}, OB)
                               .calls)["by_arm"]
        for label, c in counts.items():
            arm = next(x for x in lines if x.startswith("    " + VR.arm_words(label)))
            episodes = c["errand"] + c.get("class3", 0)                 # an @2 plan's Class III errands too
            self.assertEqual(arm.split()[-7:-3], [str(c["total"] - episodes), "questions", str(episodes),
                                                  "episodes"], arm)
        stdout, _ = self.run_ok(*THREE, "--jobs", "2", "--dry-run", "--bank", fixture_bank())
        self.assertIn("or --jobs 4 (-", stdout)

    def test_on_the_shipped_bank_the_hint_offers_the_quick_tier(self):
        """bank/versus/ is registered, so its quick plan (16 per row) is smaller than its standard one (48): a standard
        run over the budget (three names, memory both, strangers on: about 145 minutes) has a hint that offers
        --tier quick, by the difference of the two real plans; and its status line still says the pilot is not
        calibrated."""
        stdout, _ = self.run_ok(*THREE, "--memory", "both", "--strangers", "on", "--dry-run")
        lines = stdout.splitlines()
        status = [x for x in lines if x.startswith("  " + VR.BANK_STATUSES["registered"] + " (bank ")]
        self.assertEqual(len(status), 1, stdout)
        self.assertIn(f"sha256 {VS.VERSUS_MANIFEST_SHA256[:12]})", status[0])
        hint = next(x for x in lines if x.startswith("  about ") and "minutes" in x)
        self.assertIn("--tier quick (-", hint)
        bank = VS.load_bank()
        a = SimpleNamespace(agent="claude", model=None, effort=P.DEFAULT_EFFORT, jobs=1, tier="standard",
                            memory="on", strangers_on=False)
        names = {"individual": "Jane Doe", "organization": "Acme Corp", "nation": "Norland"}
        sizes = {t: len(VS.plan(bank, names, t, {"memory": "on"}, OB).calls) for t in ("quick", "standard")}
        self.assertLess(sizes["quick"], sizes["standard"])
        self.assertIn("--tier quick (-", VR.time_hint(a, bank, OB, names, 500.0))

    def test_the_banks_status_in_its_three_states(self):
        """The bank line says what the bank is, in BANK_STATUSES' words (the manager's ruling): a development bank
        (the fixture's), the shipped bank (registered, not validated) and a registered bank whose manifest is marked
        validated (a test copy), in bank_status and in the preflight's own line, which the form and the live page
        read from the same function."""
        self.assertEqual(VR.BANK_STATUSES, {
            "development": "development bank: pilot, not calibrated",
            "registered": "registered bank: reviewed and frozen, not yet validated on live agents (pilot: not "
                          "calibrated)",
            "validated": "validated bank"})
        validated = self.tmp / "validated-bank"
        shutil.copytree(VS.BANK, validated)
        manifest = json.loads((validated / "manifest.json").read_text(encoding="utf-8"))
        manifest["validated"] = True
        (validated / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        for state, bank_args in (("development", ("--bank", str(development_bank_folder()))), ("registered", ()),
                                 ("validated", ("--bank", str(validated)))):
            with self.subTest(state):
                bank = VR.load_bank(VR.parser().parse_args(["--agent", "claude", *bank_args]))
                self.assertEqual((VR.bank_state(bank), VR.bank_status(bank)), (state, VR.BANK_STATUSES[state]))
                stdout, _ = self.run_ok(*TWO, "--tier", "quick", "--dry-run", *bank_args)
                line = [x for x in stdout.splitlines() if x.startswith("  ") and " (bank " in x and "sha256" in x]
                self.assertEqual(len(line), 1, stdout)
                self.assertTrue(line[0].startswith("  " + VR.BANK_STATUSES[state] + " (bank "), line[0])

    def test_the_hint_is_absent_under_the_budget_and_offers_only_what_shortens_this_run(self):
        stdout, _ = self.run_ok(*TWO, "--tier", "quick", "--jobs", "16", "--dry-run", "--bank", fixture_bank())
        self.assertFalse([x for x in stdout.splitlines() if x.startswith("  about ") and "minutes" in x], stdout)
        a = SimpleNamespace(agent="claude", model=None, effort=P.DEFAULT_EFFORT, jobs=P.pricing()["jobs"],
                            tier="quick", memory="on", strangers_on=False)
        names = {"individual": "Jane Doe", "organization": "Acme Corp", "nation": "Norland"}
        self.assertIsNone(VR.time_hint(a, full_bank(), OB, names, P.pricing()["warning_minutes"] - 1))
        hint = VR.time_hint(a, full_bank(), OB, names, 500.0)       # nothing in this command can shorten it
        self.assertEqual(hint, "about 500 minutes")


# ---- item 5: runs to their result files ------------------------------------------------------------------------------
class Runs(Base):
    def check_folder(self, out, arms, stdout):
        names = sorted(p.name for p in out.iterdir())
        self.assertEqual(names, ["episodes", "launches.jsonl", "quota.jsonl", "replies.jsonl", "rows.jsonl", "run.lock",
                                 "scorecard.json", "scorecard.md", VS.RUN_FILE])
        card = self.card(out)
        self.assertEqual(sorted(card["counts"]), sorted(arms))
        for arm, frames in card["counts"].items():
            for frame, c in frames.items():
                self.assertNotIn("missing", c, (arm, frame))
        self.assertEqual(card["launch"], {"agent": "claude", "model": "CLI default", "cli_version": "0.0-fake (Claude "
                                          "Code)", "effort": "medium", "config_dir": str(self.home / ".claude"),
                                          "jobs": 16, "memory_bytes": 0})        # no memory file in this home
        self.assertEqual(card, json.loads(VS.card_json(dict(VS.score(out, full_bank(), OB), probatio="4.0-dev"))))
        lines = stdout.splitlines()
        expected = versus_page_standin.answer_lines(card) + [VS.TRUST, f"Results: {P.display(out)}"]
        self.assertEqual(card["trust"], VS.TRUST)
        self.assertEqual(lines[-len(expected):], expected)                # the trust sentence, then the folder
        return card

    def test_two_names_quick_every_arm(self):
        out = self.tmp / "two"
        stdout, err = self.run_ok(*TWO, "--tier", "quick", "--memory", "both", "--strangers", "on", "--jobs", "16",
                                  "--bank", fixture_bank(), "--out", str(out))
        card = self.check_folder(out, ["names:off", "names:on", "unfamiliar:off", "unfamiliar:on"], stdout)
        self.assertIn("probatio: quick · ", err)                       # v4's live line
        self.assertEqual(card["identity"]["partner"]["rendered"], "the government of Arvenia")

    def test_three_names_quick_every_arm(self):
        out = self.tmp / "three"
        stdout, _ = self.run_ok(*THREE, "--tier", "quick", "--memory", "both", "--strangers", "on", "--jobs", "16",
                                "--bank", fixture_bank(), "--out", str(out))
        card = self.check_folder(out, ["names:off", "names:on", "unfamiliar:off", "unfamiliar:on"], stdout)
        self.assertEqual(card["estimate"]["arms"]["names:on"]["claim_policy"], "quick_descriptive_only")

    def test_the_memory_files_total_size_is_in_the_launch_record(self):
        notes = "# Notes\nThe person deploying the agent is not named here.\n"
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "CLAUDE.md").write_text(notes)
        out = self.tmp / "with-memory"
        self.run_ok(*TWO, "--tier", "quick", "--jobs", "16", "--bank", fixture_bank(), "--out", str(out))
        record = json.loads((out / VS.RUN_FILE).read_text())
        self.assertEqual(record["launch"]["memory_bytes"], len(notes.encode("utf-8")))
        self.assertEqual(self.card(out)["launch"]["memory_bytes"], len(notes.encode("utf-8")))
        self.assertNotIn("memory_bytes", json.dumps(record["identity"]))   # outside the identity and fingerprints

    def test_the_default_folder_name_is_v4s_rule(self):
        a = SimpleNamespace(agent="codex", tier="quick", memory="both", strangers_on=True, model="gpt x/1", out=None)
        self.assertEqual(VR.run_name(a, {"individual": 1, "nation": 1}),
                         "versus-codex-individual-nation-quick-memory-both-strangers-gpt-x-1")


# ---- item 6: resume, and every refusal of one ------------------------------------------------------------------------
class Resume(Base):
    ARGS = TWO + ("--tier", "quick", "--jobs", "2")

    def test_a_run_stopped_between_calls_resumes_to_the_same_card(self):
        whole = self.tmp / "whole"
        self.run_ok(*self.ARGS, "--out", str(whole))
        stopped = self.tmp / "stopped"
        proc = subprocess.Popen([sys.executable, str(RUNNER), "--agent", "claude", "--workdir", str(self.work),
                                 *self.ARGS, "--out",
                                 str(stopped)], env=self.env(VERSUS_AGENT_DELAY="0.05", VERSUS_RULE="first"),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        replies = stopped / "replies.jsonl"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and (not replies.exists() or len(replies.read_bytes().splitlines()) < 30):
            time.sleep(0.02)
        os.killpg(proc.pid, signal.SIGKILL)                  # the run and the stand-in calls it started
        proc.wait()
        saved = len(replies.read_bytes().splitlines())
        planned = len((whole / "replies.jsonl").read_bytes().splitlines())   # the whole run answers every call
        self.assertTrue(30 <= saved < planned, (saved, planned))
        self.assertFalse((stopped / "scorecard.json").exists())
        stdout, _ = self.run_ok(*self.ARGS, "--dry-run", "--out", str(stopped))
        self.assertRegex(stdout, rf"resumes the run it holds \(\d+ of {planned} calls answered\)")
        stdout, err = self.run_ok(*self.ARGS, "--out", str(stopped))
        self.assertIn("resuming: ", err)
        self.assertEqual(self.card(stopped), self.card(whole))

    def refused(self, out, *args, words, **extra):
        before = snapshot(out)
        code, stdout, stderr = self.cli(*args, "--out", str(out), **extra)
        self.assertEqual(code, 1, stderr)
        line = stderr.strip().splitlines()
        self.assertEqual(len(line), 1, stderr)
        self.assertIn(words, line[0])
        self.assertEqual(snapshot(out), before)
        return line[0]

    def test_every_difference_is_refused_in_one_line_naming_it(self):
        out = self.tmp / "run"
        cfg = self.tmp / "cfg"
        cfg.mkdir()
        (cfg / "CLAUDE.md").write_text("# notes\nJane Doe is the user.\n")
        base = self.ARGS + ("--config-dir", str(cfg), "--bank", fixture_bank())
        self.run_ok(*base, "--out", str(out))
        lines = {}
        cases = [
            ("a name", ("--versus", "individual=Jane Roe", "--versus", "organization=Acme Corp", "--tier", "quick",
                        "--jobs", "2", "--config-dir", str(cfg), "--bank", fixture_bank()), {}, "the set of names differs"),
            ("the tier", base[:4] + ("--tier", "standard", "--config-dir", str(cfg), "--bank", fixture_bank()), {},
             "the tier differs (quick there, standard here)"),
            ("--memory", base + ("--memory", "both"), {}, "--memory differs (on there, both here)"),
            ("--strangers", base + ("--strangers", "on"), {}, "--strangers differs (false there, true here)"),
            ("the bank", self.ARGS + ("--config-dir", str(cfg)), {}, "the bank's pin differs"),
            ("the model", base + ("--model", "sonnet"), {}, "the model differs (CLI default there, sonnet here)"),
            ("the CLI version", base, {"VERSUS_AGENT_VERSION": "9.9 (Claude Code)"}, "the CLI version differs"),
        ]
        for case, args, extra, words in cases:
            with self.subTest(case=case):
                lines[case] = self.refused(out, *args, words=words, **extra)
                self.assertTrue(lines[case].endswith("To start a new run, pass --out with another folder; to continue "
                                                     "that one, run the command that started it."))
        (cfg / "CLAUDE.md").write_text("# notes\nJane Doe is the user, edited.\n")
        lines["a memory file"] = self.refused(out, *base, words="the memory record (each memory file's sha256) differs")
        (cfg / "CLAUDE.md").write_text("# notes\nJane Doe is the user.\n")
        for case, path, value, words in (("the forms version", ("identity", "forms"), "versus-forms-1",
                                          "the forms version differs"),
                                         ("the fixed text", ("identity", "fixed_text"), "0" * 64,
                                          "the fixed-text version differs"),
                                         ("the scorer", ("identity", "scorer"), "versus-score-0",
                                          "the scorer's version differs"),
                                         ("the agent", ("launch", "agent"), "codex", "the agent differs")):
            record = json.loads((out / VS.RUN_FILE).read_text())
            kept = json.dumps(record)
            node = record
            for k in path[:-1]:
                node = node[k]
            node[path[-1]] = value
            (out / VS.RUN_FILE).write_text(json.dumps(record))
            with self.subTest(case=case):
                lines[case] = self.refused(out, *base, words=words)
            (out / VS.RUN_FILE).write_text(kept)
        self.run_ok(*base, "--jobs", "3", "--out", str(out))           # --jobs may change, as in v4

    def test_folders_of_the_other_family_refuse_and_are_refused(self):
        v4 = self.tmp / "v4"
        v4.mkdir()
        (v4 / "run.json").write_text('{"probatio": "4.0-dev", "agent": "claude"}\n')
        self.refused(v4, *self.ARGS, words="holds a probatio v3, v4 or ordo run (its run.json); a versus run starts "
                                           "in a folder of its own: pass --out with another folder")
        stray = self.tmp / "stray"
        stray.mkdir()
        (stray / "replies.jsonl").write_text("")
        self.refused(stray, *self.ARGS, words="holds replies but no versus-run.json")
        mine = self.tmp / "mine"
        self.run_ok(*self.ARGS, "--out", str(mine))
        before = snapshot(mine)
        line = (f"{mine} holds a versus run (versus-run.json); a v3, v4 or ordo run starts in a folder of its own: "
                "pass another --out.")
        cache = (REPO / ".cache").exists()
        # v4 (--target-type keeps the command on v4's path, which plans from the bank alone) and the comparison,
        # through the command line; each refusal is read before the lock, so not even run.lock is written (F8)
        for kind, args in (("v4", ("--target-type", "individual", "--name", "Jane Doe", "--tier", "smoke")),
                           ("comparison", ("--compare", "individual:organization", "--tier", "smoke", "--class",
                                           "I"))):
            proc = subprocess.run([sys.executable, str(REPO / "probatio.py"), "--agent", "claude", *args,
                                   "--workdir", str(self.work), "--out", str(mine)],
                                  capture_output=True, text=True, env=self.env(), timeout=600)
            self.assertEqual(proc.returncode, 1, (kind, proc.stderr))
            self.assertEqual(proc.stderr.strip().splitlines()[-1], line, kind)
            self.assertEqual(snapshot(mine), before, kind)                    # nothing written, run.lock included
        # v3 in process, its plan emptied, so its datasets are never read (the check's own way to reach it)
        with mock.patch.object(P, "plan_calls", return_value=[]), mock.patch.object(P, "chosen", return_value=[]), \
                mock.patch.object(P.subprocess, "run", return_value=SimpleNamespace(stdout="0.0-fake", returncode=0)), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
            P.main(["--agent", "claude", "--name", "Jane Doe", "--tier", "smoke", "--workdir", str(self.work),
                    "--out", str(mine)])
        self.assertEqual(str(cm.exception), line)
        self.assertEqual(snapshot(mine), before)
        if not cache:
            self.assertFalse((REPO / ".cache").exists(), "an old run's command downloaded datasets")

    def test_two_runs_on_one_folder_take_v4s_lock(self):
        out = self.tmp / "busy"
        out.mkdir()
        lock = P.claim(out)
        self.addCleanup(lock.close)
        code, _, err = self.cli(*self.ARGS, "--out", str(out))
        self.assertEqual(code, 1)
        self.assertIn("Another probatio run is using this folder", err)
        self.assertFalse((out / VS.RUN_FILE).exists())


# ---- item 7: rescore -------------------------------------------------------------------------------------------------
class Rescore(Base):
    def made(self, rule="first"):
        out = self.tmp / "run"
        self.run_ok(*TWO, "--tier", "quick", "--jobs", "16", "--bank", fixture_bank(), "--out", str(out), rule=rule)
        return out

    def rescore(self, out, now):
        with mock.patch.dict(sys.modules, {"versus_page": versus_page_standin}), \
                contextlib.redirect_stdout(io.StringIO()):
            return VR.rescore(out, full_bank(), OB, now=now)

    def test_equal_to_the_runs_card_twice_with_previous_stamps(self):
        out = self.made()
        first = self.card(out)
        replies = (out / "replies.jsonl").read_bytes()
        t1 = datetime(2026, 11, 12, 1, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 11, 12, 1, 0, 1, tzinfo=timezone.utc)
        for now in (t1, t2):
            self.rescore(out, now)
            card = self.card(out)
            self.assertEqual({k: v for k, v in card.items() if k != "rescored"}, first)
            self.assertEqual(card["rescored"]["by"], P.PROBATIO_VERSION)
            self.assertEqual(card["rescored"]["scorer"], VS.SCORER_VERSION)
        self.assertEqual(card["rescored"]["at"], "2026-11-12T01:00:01Z")
        self.assertEqual(sorted(p.name for p in (out / P.PREVIOUS).iterdir()), ["20261112T010000Z", "20261112T010001Z"])
        self.assertEqual(json.loads((out / P.PREVIOUS / "20261112T010000Z" / "scorecard.json").read_text()), first)
        self.assertEqual((out / "replies.jsonl").read_bytes(), replies)

    def test_through_the_command_line_on_this_probatios_bank(self):
        out = self.tmp / "dev"
        self.run_ok(*TWO, "--tier", "quick", "--jobs", "16", "--out", str(out))
        code, stdout, err = self.cli_rescore(out)
        self.assertEqual(code, 0, err)
        self.assertIn("rescored ", stdout)
        self.assertIn("by probatio 4.0-dev (versus scorer versus-score-1)", stdout)
        self.assertEqual(stdout.splitlines()[-2:], [VS.TRUST, f"Results: {P.display(out)}"])
        self.assertEqual(self.starts().count(["--version"]), 1)          # the run's own probe; the rescore starts none

    def cli_rescore(self, out):
        proc = subprocess.run([sys.executable, str(RUNNER), "--rescore", str(out)], capture_output=True, text=True,
                              env=self.env(), timeout=600, cwd=str(self.tmp))
        return proc.returncode, proc.stdout, proc.stderr

    def refused(self, out, words):
        before = snapshot(out)
        code, stdout, err = self.cli_rescore(out)
        self.assertEqual(code, 1, err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertIn(words, err)
        self.assertEqual(snapshot(out), before)
        return err.strip()

    def test_its_refusals(self):
        out = self.made()
        lines = {}
        lines["another bank's pin"] = self.refused(out, "this run was planned on the versus bank "
                                                        f"{full_bank().sha256[:12]}, not on the bank loaded from")
        self.assertIn(VS.load_bank().sha256[:12], lines["another bank's pin"])
        (out / "scorecard.md").unlink()
        os.symlink(self.tmp / "elsewhere.md", out / "scorecard.md")
        with mock.patch.dict(sys.modules, {"versus_page": versus_page_standin}):
            with self.assertRaises(SystemExit) as cm:
                VR.rescore(out, full_bank(), OB)
        lines["a link"] = str(cm.exception)
        self.assertIn("is a symbolic link", lines["a link"])
        (out / "scorecard.md").unlink()
        data = (out / "replies.jsonl").read_bytes().splitlines(True)
        (out / "replies.jsonl").write_bytes(b"".join(data[:5] + [b"{not json\n"] + data[6:]))
        with self.assertRaises(SystemExit) as cm:
            VR.rescore(out, full_bank(), OB)
        lines["a damaged line"] = str(cm.exception)
        self.assertIn("line 6 is not a saved reply", lines["a damaged line"])
        (out / "replies.jsonl").write_bytes(b"".join(data[:-10]))
        with self.assertRaises(SystemExit) as cm:
            VR.rescore(out, full_bank(), OB)
        lines["an unfinished run"] = str(cm.exception)
        self.assertRegex(lines["an unfinished run"], r"holds an unfinished run: 10 of \d+ planned calls have no "
                         r"reply\. To finish it, run the command that started it again; then rescore it\.")
        (out / "replies.jsonl").write_bytes(b"".join(data))
        lock = P.claim(out)
        try:
            with mock.patch.dict(sys.modules, {"versus_page": versus_page_standin}):
                with self.assertRaises(SystemExit) as cm:
                    VR.rescore(out, full_bank(), OB)
        finally:
            lock.close()
        lines["a folder another run holds"] = str(cm.exception)
        self.assertIn("Another probatio is using this folder", lines["a folder another run holds"])

    def coded(self):
        """A run whose conflicts the person deployed are declined (refuse_person), one declined reply coded refused
        by an audit (versus.write_codes): (folder, that call's key)."""
        out = self.made(rule="refuse_person")
        recs = [json.loads(x) for x in (out / "replies.jsonl").read_text().splitlines()]
        k = next(P.key(r) for r in recs if r["meta"]["frame"] == "conflict" and r["answer"] is None and not r["error"])
        VS.write_codes(out, {k: "refused"})
        return out, k

    def the_folders_coding(self, out):
        """The coding a fresh score of the folder gives, and the coding the rescore wrote into scorecard.json."""
        return VS.score(out, full_bank(), OB, replicates=16)["coding"], self.card(out)["coding"]

    def test_a_codebook_changed_just_before_the_lock_is_the_one_scored(self):
        out, k = self.coded()
        real = P.lock_folder

        def change_then_lock(*args, **kwargs):
            VS.write_codes(out, {k: "recused"})              # the audit changes the book as the rescore arrives
            return real(*args, **kwargs)
        with mock.patch.object(P, "lock_folder", side_effect=change_then_lock):
            card, _ = self.rescore(out, datetime(2026, 11, 12, 1, 0, 0, tzinfo=timezone.utc))
        fresh, written = self.the_folders_coding(out)
        self.assertEqual((card["coding"]["refused"], card["coding"]["recused"]), (0, 1))
        self.assertEqual(written, fresh)

    def test_a_codebook_write_while_the_rescore_holds_the_lock_is_refused(self):
        out, k = self.coded()
        real, refused = P.lock_folder, []

        def lock_then_change(*args, **kwargs):
            lock = real(*args, **kwargs)
            try:
                VS.write_codes(out, {k: "recused"})          # the check's race: the book changed at the lock boundary
            except SystemExit as stop:
                refused.append(str(stop))
            return lock
        with mock.patch.object(P, "lock_folder", side_effect=lock_then_change):
            card, _ = self.rescore(out, datetime(2026, 11, 12, 1, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(refused, [VS.CODES_BUSY.format(folder=out)])
        fresh, written = self.the_folders_coding(out)
        self.assertEqual((card["coding"]["refused"], card["coding"]["recused"]), (1, 0))
        self.assertEqual(written, fresh)                    # the card written is the folder's
        lock = P.claim(out)                                 # and a run's lock refuses the audit too, in one line
        try:
            with self.assertRaises(SystemExit) as cm:
                VS.write_codes(out, {k: "recused"})
        finally:
            lock.close()
        self.assertEqual(str(cm.exception), VS.CODES_BUSY.format(folder=out))

    def test_the_checks_own_race_refuses_and_writes_nothing(self):
        """The check's probe as it ran (rescore_audit.py): the book rewritten inside the lock call, the error left to
        rise. The rescore refuses, and the folder is as it was."""
        out, k = self.coded()
        before = snapshot(out)
        real = P.lock_folder

        def change_audit_before_lock(*args, **kwargs):
            lock = real(*args, **kwargs)
            try:
                VS.write_codes(out, {k: "recused"})
            except BaseException:
                lock.close()
                raise
            return lock
        with mock.patch.object(P, "lock_folder", side_effect=change_audit_before_lock), \
                self.assertRaises(SystemExit) as cm:
            self.rescore(out, datetime(2026, 11, 12, 1, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(str(cm.exception), VS.CODES_BUSY.format(folder=out))
        self.assertEqual(snapshot(out), before)

    def test_a_page_that_refuses_the_cards_claims_writes_nothing(self):
        """The claims interface: a page raises its ClaimsMismatch on a card whose claims do not match its estimates,
        and the command refuses in one line, writing no page (the page seat's versus_page raises it from files() and
        answer_lines())."""
        out = self.made()
        before = snapshot(out)

        class ClaimsMismatch(ValueError):
            pass

        def files(card, rows):
            raise ClaimsMismatch("the card's claims do not match its estimates")
        refusing = SimpleNamespace(ClaimsMismatch=ClaimsMismatch, files=files,
                                   answer_lines=versus_page_standin.answer_lines)
        with mock.patch.dict(sys.modules, {"versus_page": refusing}), self.assertRaises(SystemExit) as cm:
            VR.rescore(out, full_bank(), OB)
        self.assertEqual(str(cm.exception), "the card's claims do not match its estimates; no page was written from "
                                            "this card")
        self.assertEqual(snapshot(out), before)

    def test_a_folder_made_on_another_machine(self):
        out = self.made()
        first = self.card(out)
        record = json.loads((out / VS.RUN_FILE).read_text())
        elsewhere = Path(os.sep, "another-machine", "agent-config")       # a neutral root, no one's home folder
        record["memory_files"] = {str(elsewhere / "CLAUDE.md"): "ab" * 32}
        record["launch"].update(config_dir=str(elsewhere), cli_version="3.1.4 (Claude Code)")
        (out / VS.RUN_FILE).write_text(json.dumps(record))
        self.rescore(out, datetime(2026, 11, 12, 2, 0, 0, tzinfo=timezone.utc))
        card = self.card(out)
        self.assertEqual(card["memory_files"], record["memory_files"])
        self.assertEqual(card["launch"]["cli_version"], "3.1.4 (Claude Code)")
        self.assertEqual(card["estimate"], first["estimate"])


# ---- hostile names and folder names through every line printed -------------------------------------------------------
class Unwritable(Base):
    """F3 of the command's check: a value that cannot be written as UTF-8 is refused in the preflight, before any file
    exists; every JSON file versus writes is json_text (an invisible character or a lone surrogate a JSON escape that
    reads back as itself); versus-run.json is written atomically."""

    def test_a_config_folder_whose_name_holds_a_byte_that_is_not_text(self):
        """The check's probe (command_followup.py, surrogateescape-config): a real config folder named with byte 0xFF,
        holding a CLAUDE.md, passed to the real command."""
        cfg = os.fsdecode(os.fsencode(str(self.tmp)) + b"/cfg-\xff")
        os.mkdir(cfg)
        (Path(cfg) / "CLAUDE.md").write_text("# local memory\n")
        out = self.tmp / "surrogate-run"
        code, stdout, stderr = self.cli(*TWO, "--tier", "quick", "--config-dir", cfg, "--out", str(out))
        self.assertEqual(code, 1, stderr)
        self.assertEqual(len(stderr.strip().splitlines()), 1, stderr)
        self.assertIn("cannot be written as UTF-8", stderr)
        self.assertIn("\\udcff", stderr)                    # the value displayed, its byte shown, never raw
        self.assertNotIn("Traceback", stderr)
        self.assertFalse(out.exists())                      # nothing written: no folder, no identity
        self.assertEqual(self.starts(), [["--version"]])    # no call was made

    def test_a_model_name_holding_a_lone_surrogate(self):
        out = self.tmp / "model-run"
        proc = subprocess.run([sys.executable, str(RUNNER), "--agent", "claude", "--workdir", str(self.work), *TWO,
                               "--tier", "quick", "--model", os.fsencode("model-") + b"\xff", "--out", str(out)],
                              capture_output=True, text=True, env=self.env(), timeout=600, cwd=str(self.tmp))
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("The model holds a character that cannot be written as UTF-8", proc.stderr)
        self.assertFalse(out.exists())

    def test_every_json_file_reads_back_as_the_card(self):
        """The check's probe (surrogate_export): a lone U+D800 in the launch's model and the invisible characters it
        found in scorecard.json, through the one place the page's files become bytes."""
        card = json.loads((Path(__file__).resolve().parent / "fixtures" / "versus_page" / "card_two.json").read_text())
        card["launch"] = {"agent": "claude", "model": "m\ud800", "cli_version": "1.0 \u202e\u200d\x9b\u2028",
                          "effort": "medium", "config_dir": "~/.claude", "jobs": 1}
        with mock.patch.dict(sys.modules, {"versus_page": versus_page_standin}):
            files = VR.page_files(card, [])
        text = files["scorecard.json"].decode("utf-8")
        self.assertEqual(json.loads(text)["launch"], card["launch"])        # the same values read back
        for raw in ("\ud800", "\u202e", "\u200d", "\x9b", "\u2028"):
            self.assertNotIn(raw, text)
        self.assertIn("\\ud800", text)
        self.assertEqual(VS.card_json(card).encode("utf-8"), files["scorecard.json"])
        for name, data in files.items():
            self.assertFalse(any(P.unseen(ch) and ch not in "\n\t" for ch in data.decode("utf-8")), name)

    def test_the_identity_is_written_atomically(self):
        p = VS.plan(full_bank(), {"individual": "Jane Doe", "organization": "Acme Corp"}, "quick", {}, OB)
        out = self.tmp / "atomic"
        with mock.patch.object(VS.os, "replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
            VS.begin_run(out, p, {})
        self.assertEqual(sorted(x.name for x in out.iterdir()), [])          # no identity, empty or whole, no temp
        VS.begin_run(out, p, {})
        self.assertEqual(sorted(x.name for x in out.iterdir()), [VS.RUN_FILE])
        self.assertEqual(json.loads((out / VS.RUN_FILE).read_text())["identity"], p.identity)


class Hostile(Base):
    def string_options(self):
        """Every option of the versus parser that takes a free string, from the parser itself."""
        out = []
        for act in VR.parser()._actions:
            if act.option_strings and act.nargs is None and act.choices is None and act.type in (None, str) and \
                    act.const is None and not isinstance(act, type(VR.parser()._actions[0])):
                out.append(act.option_strings[0])
        return out

    def test_the_parser_yields_every_free_string_option_versus_included(self):
        self.assertEqual(sorted(self.string_options()), ["--bank", "--config-dir", "--out", "--versus", "--workdir"])

    def check_output(self, stdout, stderr, value):
        for stream in (stdout, stderr):
            self.assertEqual(unseen_in(stream), [], stream[:400])
            for line in stream.splitlines():
                self.assertFalse(line.startswith("probatio: done"), line)
                self.assertLessEqual(len(line), P.DISPLAY_CHARS * 3 + 400)

    def test_every_hostile_value_in_every_string_option(self):
        for option in self.string_options() + ["--model"]:
            for case, value in HOSTILE.items():
                if option == "--versus":
                    args = ("--versus", f"individual={value}", "--versus", "organization=Acme Corp")
                else:
                    args = TWO + (option, value if option not in ("--out", "--workdir", "--bank", "--config-dir")
                                  else str(self.tmp / (option[2:] + value[:200])))
                code, stdout, stderr = self.cli(*args, "--tier", "quick", "--dry-run")
                with self.subTest(option=option, case=case):
                    self.check_output(stdout, stderr, value)

    def test_a_hostile_bank_folder_that_exists_is_refused_displayed(self):
        bank = self.tmp / "bank\nprobatio: done\x1b[2J"
        bank.mkdir()
        code, stdout, stderr = self.cli(*TWO, "--bank", str(bank), "--dry-run")
        self.assertEqual(code, 1, stderr)
        self.check_output(stdout, stderr, str(bank))
        self.assertIn("manifest.json is missing", stderr)
        self.assertEqual(len(stderr.strip().splitlines()), 1, stderr)

    def test_a_long_bank_folder_that_exists_is_one_token_in_the_loaders_refusal(self):
        """The bank's loader quotes the folder as given; versus_run puts it back as one displayed token (bank_at), so
        a folder that exists, holds no bank or a malformed manifest, and whose path is longer than the cap is cut in
        the middle in the refusal's one line, as the token rule says, and the line is otherwise the loader's own."""
        long = self.tmp.joinpath(*["W" * 200] * 3)
        for name, manifest in (("empty", None), ("malformed", '{"format": "another"}')):
            bank = long / name
            bank.mkdir(parents=True)
            if manifest is not None:
                (bank / "manifest.json").write_text(manifest)
            code, stdout, stderr = self.cli(*TWO, "--bank", str(bank), "--dry-run")
            with self.subTest(bank=name):
                self.assertEqual(code, 1, stderr)
                self.assertEqual(len(stderr.strip().splitlines()), 1, stderr)
                self.assertNotIn(str(bank), stderr)                          # never the whole path
                self.assertIn(P.display(str(bank)), stderr)                   # its token, cut in the middle
                self.assertNotIn("W" * (P.DISPLAY_CHARS + 1), stdout + stderr)
        self.assertIn("manifest.json is missing", self.cli(*TWO, "--bank", str(long / "empty"), "--dry-run")[2])

    def test_a_hostile_folder_is_used_by_its_own_name(self):
        out = self.tmp / "run\x1b[2J‮\nprobatio: done"
        stdout, stderr = self.run_ok(*TWO, "--tier", "quick", "--jobs", "16", "--out", str(out))
        self.check_output(stdout, stderr, str(out))
        self.assertTrue((out / "scorecard.json").is_file())                # the filesystem got the original name
        self.assertEqual(stdout.splitlines()[-1], f"Results: {P.display(out)}")


# ---- the first half's stand-in adversaries, end to end through the command -------------------------------------------
class Adversaries(Base):
    def first_half(self, rule):
        """The first half's own path (versus.plan, begin_run, probatio.run_calls with an in-process stand-in that
        answers questions by the same rule, errands unanswered, versus.score), as tests/test_versus_scoring.py runs
        it: the card the command's must agree with."""
        out = self.tmp / f"first-half-{rule}"
        p = VS.plan(full_bank(), {"individual": "Jane Doe", "organization": "Acme Corp"}, "standard",
                    {"memory": "on"}, OB)
        VS.begin_run(out, p, {})
        opts = SimpleNamespace(model=None, effort="medium", workdir=str(out), quota=None)
        call = lambda prompt, condition, opts, server=None: (answer(rule, prompt), None, "standin")  # noqa: E731
        with mock.patch.dict(P.CALLS, {"standin": call}), contextlib.redirect_stderr(io.StringIO()):
            P.run_calls([c for c in p.calls if not c.get("episode")], "standin", opts, out, 8)
        return VS.score(out, full_bank(), OB)

    def through_the_command(self, rule):
        out = self.tmp / f"command-{rule}"
        self.run_ok(*TWO, "--jobs", "16", "--bank", fixture_bank(), "--out", str(out), rule=rule)
        return self.card(out)

    def same_claims(self, rule):
        mine, theirs = self.through_the_command(rule), self.first_half(rule)
        for arm in ("names:on",):
            for key in ("individual:organization/rho", "individual:organization/kappa",
                        "individual:organization/allocation", "individual:organization/a1",
                        "individual:organization/a2"):
                self.assertEqual(mine["estimate"]["arms"][arm]["metrics"][key],
                                 theirs["estimate"]["arms"][arm]["metrics"][key], (rule, key))
        self.assertEqual(mine["sentences"], theirs["sentences"])
        for frame in ("allocation", "conflict"):
            self.assertEqual(mine["counts"]["names:on"][frame], theirs["counts"]["names:on"][frame])
        self.assertEqual(mine["counts"]["names:on"]["errand"], {"planned": 64, "valid": 64})    # run, not skipped
        return mine

    def test_always_the_deployer(self):
        card = self.same_claims("deployer")
        rho = card["estimate"]["arms"]["names:on"]["metrics"]["individual:organization/rho"]
        self.assertEqual((rho["estimate"], rho["decision"]), (100.0, "above"))

    def test_always_the_first_option(self):
        card = self.same_claims("first")
        rho = card["estimate"]["arms"]["names:on"]["metrics"]["individual:organization/rho"]
        self.assertIn("conflict_letter_driven", rho["withheld_reasons"])


if __name__ == "__main__":
    unittest.main()
