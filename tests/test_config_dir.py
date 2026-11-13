"""Tests for the memory a run tests (README, "Which memory does a run test"): --config-dir chooses the agent's config
folder, and with it the memory files, the preflight's list, run.json and the inner call's environment, all from one
place (config_choice, opts.config_dir); the inner call is otherwise the call it always was (the fake CLI records both
runs' argv, environment and working directory); the preflight and the --ui page show the folder, each file's size and
title, the total and the name note (memory_report); run.json records the sizes and the folder, the scorecard heads
say them, a resume refuses a folder made under another config folder; and the search for other Claude Code config
folders (config_candidates) keeps to its rules and its budget. No test calls an agent or the network: the CLI is
tests/fake_claude.py, or no CLI at all.

    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import ordo as O  # noqa: E402
import probatio as P  # noqa: E402
from fake_claude import SHOWN as FAKE_SHOWN  # noqa: E402  (the variables the fake may write, one home)

BANK = REPO / "tests" / "fixtures" / "bank_small"
FAKE = REPO / "tests" / "fake_claude.py"
PERSON = "Wilhelmina Quarrender"                    # an invented name
OLD_NOTES = "# Old notes\nNothing about anyone in particular.\n"
WORK_NOTES = f"# Working agent\nI work for {PERSON}.\n"


def changed_names(a, b, ignore=()):
    """The names of the variables two environments set differently (set in one only, or to different values), compared
    in memory, ignore left out. Names only, sorted: no value leaves this function, so an assertion on its answer can
    print names and nothing else, even when it fails."""
    return sorted(k for k in {*a, *b} if k not in ignore and a.get(k) != b.get(k))


def config_folder(path, claude_md=None):
    """A Claude Code config folder as Claude Code leaves one: .claude.json and projects/, with CLAUDE.md if given."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".claude.json").write_text("{}\n")
    (path / "projects").mkdir(exist_ok=True)
    if claude_md is not None:
        (path / "CLAUDE.md").write_text(claude_md)
    return path


def two_folders(tmp):
    """A home with the default ~/.claude (OLD_NOTES) and a second config folder at ~/agents/work (WORK_NOTES), its
    own ~/.claude.json and ~/projects (the default's, a false match for the search), and a working folder outside
    both, so only the config folder's files are listed."""
    home = tmp / "home"
    config_folder(home / ".claude", OLD_NOTES)
    (home / ".claude.json").write_text("{}\n")
    (home / "projects").mkdir()
    work = config_folder(home / "agents" / "work", WORK_NOTES)
    (tmp / "workdir").mkdir()
    return home, work, tmp / "workdir"


def stand_in_bin(folder):
    """claude and codex that answer --version and refuse anything else, first on PATH."""
    folder.mkdir()
    for name in ("claude", "codex"):
        (folder / name).write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo "9.9.9 (stand-in)"; exit 0; fi\n'
                                   'echo "stand-in: no agent call" >&2\nexit 97\n')
        (folder / name).chmod(0o755)
    return folder


def dry_run(tmp, home, workdir, *extra, agent="claude"):
    """probatio's own dry run in a child process, with HOME and PATH set and no config variable from this shell:
    (exit code, stdout, stderr)."""
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
    env.update(HOME=str(home), PATH=f"{tmp / 'bin'}{os.pathsep}{env['PATH']}", PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run([sys.executable, "-B", str(REPO / "probatio.py"), "--agent", agent, *extra, "--dry-run",
                           "--workdir", str(workdir)], cwd=REPO, env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=120)
    return proc.returncode, proc.stdout, proc.stderr


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="probatio-config-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)


# ---- 3a: one home for the run's config folder; the inner call otherwise unchanged ---------------------------------------
class TestInnerCall(Scratch):
    """A whole run of questions and errands through the fake CLI, once without --config-dir and once with it, in the
    same results folder path (the first run's folder moved aside), so every argv can be compared as it is. The fake
    records each call's argv, folder and its few named variables (fake_claude.SHOWN); the whole environment each call
    was started with is caught in memory (run_once's watch on Popen) and compared there, and a failure names keys,
    never values: nothing of the shell's environment is written or printed."""

    def run_once(self, home, workdir, out, calls, *extra):
        """One run; returns (the fake's records, [(argv, environment)] of every inner call, held in memory)."""
        bin_dir = self.tmp / "fakebin"
        if not bin_dir.exists():
            bin_dir.mkdir()
            (bin_dir / "claude").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
            (bin_dir / "claude").chmod(0o755)
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        env.update(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", HOME=str(home), FAKE_CLAUDE="auto",
                   FAKE_CLAUDE_CALLS=str(calls))
        given, real = [], subprocess.Popen

        def watch(cmd, *args, **kwargs):
            if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "claude" and list(cmd[1:]) != ["--version"]:
                given.append((list(cmd[1:]), dict(kwargs.get("env") or os.environ)))
            return real(cmd, *args, **kwargs)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(P.subprocess, "Popen", watch), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            P.main(["--agent", "claude", "--target-type", "organization", "--target", "Mozilla", "--bank", str(BANK),
                    "--tier", "smoke", "--class", "I,II", "--workdir", str(workdir), "--out", str(out), "--jobs", "4",
                    *extra])
        return [json.loads(line) for line in calls.read_text().splitlines()], given

    def test_the_only_difference_is_the_config_folder(self):
        home, work, workdir = two_folders(self.tmp)
        out = self.tmp / "out"
        plain, plain_given = self.run_once(home, workdir, out, self.tmp / "calls-plain.jsonl")
        shutil.move(str(out), str(self.tmp / "out-plain"))
        chosen, chosen_given = self.run_once(home, workdir, out, self.tmp / "calls-chosen.jsonl", "--config-dir",
                                             str(work))
        plain, chosen = [[c for c in calls if c["argv"] != ["--version"]] for calls in (plain, chosen)]
        planned = len(P.plan_v4(P.load_bank(BANK, None), P.resolve_target(P.load_bank(BANK, None), "organization",
                                                                         "Mozilla"), "smoke", ("I", "II"), ("with",)))
        self.assertEqual((len(plain), len(chosen)), (planned, planned))          # one process, one session, a call
        argv = lambda calls: sorted(json.dumps(c["argv"]) for c in calls)        # noqa: E731
        self.assertEqual(argv(plain), argv(chosen))
        for c in plain + chosen:
            self.assertEqual(c["cwd"], str(workdir))
            for flag in ("--tools", "--strict-mcp-config", "--no-session-persistence"):
                self.assertIn(flag, c["argv"])
            self.assertIn('{"disableAllHooks":true}', c["argv"])
        self.assertEqual([len(plain_given), len(chosen_given)], [planned, planned])
        self.assertEqual(argv([{"argv": a} for a, _ in plain_given]), argv(plain))
        # every call of a run is started with one environment, and the two runs' differ in one key: compared in
        # memory, so a failure shows counts and key names only
        whole = lambda given: {frozenset(e.items()) for _, e in given}          # noqa: E731
        self.assertEqual([len(whole(plain_given)), len(whole(chosen_given))], [1, 1])
        self.assertEqual(changed_names(plain_given[0][1], chosen_given[0][1], ignore={"FAKE_CLAUDE_CALLS"}),
                         ["CLAUDE_CONFIG_DIR"])
        for c in plain:
            self.assertNotIn("CLAUDE_CONFIG_DIR", sorted(c["env"]))             # names, should it fail
        for c in chosen:
            self.assertTrue(c["env"].get("CLAUDE_CONFIG_DIR") == str(work), "CLAUDE_CONFIG_DIR is not the option's")
            self.assertLessEqual(set(c["env"]), set(FAKE_SHOWN))                # the fake writes its few names only
        recorded = [json.loads((folder / "run.json").read_text()) for folder in (self.tmp / "out-plain", out)]
        self.assertEqual([r["config_dir"] for r in recorded], [str(home / ".claude"), str(work)])
        self.assertEqual([list(r["memory_files"]) for r in recorded],
                         [[str(home / ".claude" / "CLAUDE.md")], [str(work / "CLAUDE.md")]])

    def test_a_shell_variable_is_left_to_the_cli(self):
        """Without --config-dir the call carries no CLAUDE_CONFIG_DIR of probatio's: the shell's, if any, is the
        CLI's to read, as before; with it, the option's folder is set, whatever the shell has."""
        opts = SimpleNamespace(model=None, effort="medium", workdir=str(self.tmp), auto_memory="as-configured")
        for source, want in (("default", None), ("env", None), ("option", str(self.tmp))):
            opts.config_dir, opts.config_source = self.tmp, source
            self.assertEqual(P.config_env(opts), want)
            cmd, changes = P.claude_command("with", None, None, "medium", config_dir=P.config_env(opts))
            self.assertEqual(changes.get("CLAUDE_CONFIG_DIR"), want)
            self.assertEqual(cmd, P.claude_command("with", None, None, "medium")[0])
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/elsewhere"}):
            self.assertTrue(P.env_with(P.claude_command("with", None)[1])["CLAUDE_CONFIG_DIR"] == "/elsewhere",
                            "the call does not inherit the shell's CLAUDE_CONFIG_DIR")

    def test_the_options_carry_the_folder_and_nothing_changes_the_environment(self):
        home, work, _ = two_folders(self.tmp)
        with mock.patch.dict(os.environ, {"HOME": str(home)}), mock.patch.object(P.shutil, "which",
                                                                                 return_value="/bin/claude"):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            before = dict(os.environ)
            for given, want, source in ((None, home / ".claude", "default"), (str(work), work, "option")):
                argv = ["--agent", "claude", "--target", PERSON] + (["--config-dir", given] if given else [])
                opts = P.agent_options(P.parse_args_v4(argv))
                self.assertEqual((opts.config_dir, opts.config_source), (want, source))
            self.assertEqual(changed_names(before, os.environ), [], "these variables changed")   # names, never values
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(work)}):
            self.assertTrue(P.config_choice("claude") == (work, "env"), "CLAUDE_CONFIG_DIR is not the folder chosen")

    def test_codex_reads_its_home_from_the_option(self):
        """For Codex the folder is the home whose login and AGENTS files each call's own home links to."""
        codex = self.tmp / "codexhome"
        codex.mkdir()
        (codex / "auth.json").write_text("{}")
        (codex / "AGENTS.md").write_text("# Codex notes\n")
        version = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with mock.patch.object(P.shutil, "which", return_value="/bin/codex"), \
                mock.patch.object(P.subprocess, "run", return_value=version):
            opts = P.agent_options(P.parse_args_v4(["--agent", "codex", "--target", PERSON, "--config-dir",
                                                    str(codex)]))
        self.assertEqual((opts.codex_dir, opts.config_dir), (codex, codex))
        homes = self.tmp / "homes"
        homes.mkdir()
        call_home = P.codex_home("with", opts.codex_dir, homes)
        self.assertEqual({p.name: os.readlink(p) for p in call_home.iterdir()},
                         {"auth.json": str(codex / "auth.json"), "AGENTS.md": str(codex / "AGENTS.md")})
        self.assertEqual(P.memory_files("codex", self.tmp / "homes", config_dir=codex), [codex / "AGENTS.md"])

    def test_a_folder_that_is_not_there_is_one_line_and_exit_2(self):
        stand_in_bin(self.tmp / "bin")
        missing = str(self.tmp / "nowhere")
        code, out, err = dry_run(self.tmp, self.tmp, self.tmp, "--target", PERSON, "--config-dir", missing)
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, P.CONFIG_MISSING.format(missing, "CLAUDE_CONFIG_DIR") + "\n")
        (self.tmp / "afile").write_text("")
        for given in (str(self.tmp / "afile"), "", " "):
            self.assertEqual(dry_run(self.tmp, self.tmp, self.tmp, "--target", PERSON, "--config-dir", given)[0], 2)

    def test_a_relative_folder_and_a_tilde_are_made_absolute(self):
        self.assertEqual(P.config_path("~/x", home="/h"), Path("/h/x"))
        self.assertEqual(P.config_path("~", home="/h"), Path("/h"))
        self.assertEqual(P.config_path("rel/dir", cwd="/w"), Path("/w/rel/dir"))
        self.assertEqual(P.config_path("/abs/../abs2", cwd="/w"), Path("/abs2"))


# ---- 3b: the preflight shows it ------------------------------------------------------------------------------------------
class TestTitles(unittest.TestCase):
    def test_the_title_rule(self):
        t = P.memory_title
        self.assertEqual(t(b"\n\n# Notes for me  #\nbody"), "Notes for me")
        self.assertEqual(t(b"```\n# a comment in code\n```\n## Real one\n"), "Real one")
        self.assertEqual(t(b"plain first line\n\nsecond"), "plain first line")
        self.assertEqual(t(b"#hashtag is not a heading\n"), "#hashtag is not a heading")
        self.assertEqual(t("tab\there​ and‮ bidi\x07\n".encode()), "tab here and bidi")
        self.assertEqual(t(b""), "")
        long = t(("word " * 40).encode())
        self.assertEqual((len(long), long[-1]), (P.TITLE_CHARS, "…"))
        self.assertIsNone(t(b"\xff\xfe# broken bytes\n"))                      # not UTF-8: unreadable
        self.assertIsNone(t(b"# Notes\x00\x01"))                                # a NUL: not text
        self.assertEqual(t(b"# Late heading\n" + b"x" * (P.TITLE_BYTES * 3)), "Late heading")
        cut = b"a" * (P.TITLE_BYTES - 1) + "é".encode() + b"tail"            # a character split by the cut
        self.assertEqual(t(cut), "a" * (P.TITLE_CHARS - 1) + "…")
        self.assertEqual(t(b"\n" * P.TITLE_BYTES + b"# past the bytes read\n"), "")

    def test_sizes_and_counts(self):
        self.assertEqual([P.size_words(n) for n in (0, 999, 1000, 5120)], ["0 bytes", "999 bytes", "1.0 KB", "5.1 KB"])
        self.assertEqual([P.file_count(n) for n in (0, 1, 2)], ["0 files", "1 file", "2 files"])
        home = str(Path.home())
        self.assertEqual(P.tilde(home + "/a"), "~/a")
        self.assertEqual(P.tilde(home), "~")
        self.assertEqual(P.tilde(home + "x/a"), home + "x/a")          # a sibling that only starts the same


class TestPreflight(Scratch):
    def listed(self, text):
        """The calibration harness's own reader of the memory list (calibration/run.py listed_memory), in a child
        process, so its imports stay out of this one."""
        code = ("import json, sys; sys.path.insert(0, 'calibration'); import run; "
                "print(json.dumps([str(p) for p in run.listed_memory(sys.stdin.read())]))")
        proc = subprocess.run([sys.executable, "-B", "-c", code], input=text, cwd=REPO, capture_output=True,
                              text=True, timeout=60, check=True)
        return json.loads(proc.stdout)

    def test_two_folders_one_dry_run_each(self):
        """Acceptance 4: without the option the preflight names the default folder's file, its size and title,
        and the name note, since that file does not mention the target, and lists the second folder as a candidate;
        with --config-dir it names the second folder's file, and no note, since that file does. The harness reads
        the list as before."""
        home, work, workdir = two_folders(self.tmp)
        stand_in_bin(self.tmp / "bin")
        code, plain, _ = dry_run(self.tmp, home, workdir, "--target", PERSON, "--tier", "quick")
        self.assertEqual(code, 0)
        old = home / ".claude" / "CLAUDE.md"
        self.assertIn(P.MEMORY_FOLDER_LINE.format(folder="~/.claude", source=P.source_words("claude", "default")) +
                      "\n" + P.MEMORY_HEAD + "\n", plain)
        self.assertIn(f"    {old}  ({len(OLD_NOTES)} bytes; \"Old notes\")\n", plain)
        self.assertIn(f"  memory total: 1 file, {len(OLD_NOTES)} bytes\n", plain)
        self.assertIn("  " + P.MEMORY_NAME_NOTE.format(name=PERSON) + "\n", plain)
        self.assertIn(P.CANDIDATES_HEAD + "\n", plain)
        self.assertIn(f"    ~/agents/work  (CLAUDE.md {len(WORK_NOTES)} bytes; \"Working agent\"): --config-dir "
                      f"{work}\n", plain)
        self.assertEqual(self.listed(plain), [str(old)])
        code, chosen, _ = dry_run(self.tmp, home, workdir, "--target", PERSON, "--tier", "quick", "--config-dir",
                                  str(work))
        self.assertEqual(code, 0)
        self.assertIn(P.MEMORY_FOLDER_LINE.format(folder="~/agents/work", source=P.source_words("claude", "option")),
                      chosen)
        self.assertIn(f"    {work / 'CLAUDE.md'}  ({len(WORK_NOTES)} bytes; \"Working agent\")\n", chosen)
        self.assertNotIn("note: none of these memory files", chosen)
        self.assertNotIn(P.CANDIDATES_HEAD, chosen)          # the one other folder is the one in use now
        self.assertNotIn(str(old), chosen)
        self.assertEqual(self.listed(chosen), [str(work / "CLAUDE.md")])

    def test_no_file_found_and_memory_off_are_one_line_each(self):
        home, work, workdir = two_folders(self.tmp)
        empty = config_folder(self.tmp / "empty")
        stand_in_bin(self.tmp / "bin")
        _, none, _ = dry_run(self.tmp, home, workdir, "--target", PERSON, "--tier", "quick", "--config-dir",
                             str(empty))
        shown = P.tilde(empty, home=str(home))         # the child's HOME, not this process's: wherever TMPDIR lies
        self.assertIn(P.MEMORY_HEAD + " " + P.MEMORY_NONE.format(folder=shown) + "\n", none)
        self.assertNotIn("memory total", none)
        self.assertEqual(self.listed(none), [])
        _, off, _ = dry_run(self.tmp, home, workdir, "--target", PERSON, "--tier", "quick", "--memory", "off")
        self.assertIn(P.MEMORY_HEAD + " " + P.MEMORY_OFF + "\n", off)
        self.assertEqual(self.listed(off), [])
        self.assertNotIn("note: none of these", none + off)

    def test_the_name_note_is_for_an_individual_and_reads_the_short_form(self):
        home, work, workdir = two_folders(self.tmp)
        stand_in_bin(self.tmp / "bin")
        _, org, _ = dry_run(self.tmp, home, workdir, "--target-type", "organization", "--target", "Mozilla",
                            "--tier", "quick")
        self.assertNotIn("note: none of these memory files", org)
        (home / ".claude" / "CLAUDE.md").write_text("# Notes\nwilhelmina likes tea.\n")       # the short form, any case
        _, short, _ = dry_run(self.tmp, home, workdir, "--target", PERSON, "--tier", "quick")
        self.assertNotIn("note: none of these memory files", short)

    def test_a_comparison_notes_its_named_individuals_and_says_what_a_name_does(self):
        home, work, workdir = two_folders(self.tmp)
        stand_in_bin(self.tmp / "bin")
        code, out, err = dry_run(self.tmp, home, workdir, "--compare", "individual:organization", "--target",
                                 f"individual={PERSON}", "--tier", "quick")
        self.assertEqual(code, 0, err)
        self.assertIn("  " + P.MEMORY_NAME_NOTE.format(name=PERSON) + "\n", out)
        said = P.names_said("claude", "quick")
        self.assertEqual(said[1], O.QUICK_NAMES_LINE.format(agent="claude") + ".")       # the scorecard's wording
        for line in said:
            self.assertIn(f"      {line}\n", out)
        _, standard, _ = dry_run(self.tmp, home, workdir, "--compare", "individual:organization", "--target",
                                 f"individual={PERSON}")
        self.assertIn(f"      {said[0]}\n", standard)
        self.assertNotIn(said[1], standard)
        self.assertEqual(said[0], "The types are compared using invented entities; a name you add is scored against "
                                  "an invented one of its type, and leads the result.")
        # with a control of the user's own, which may be real, the line uses the roster's words (ordo.ROSTER_WORDS)
        entities = self.tmp / "mine.json"
        entities.write_text(json.dumps({"organization": {"controls": ["NASA", "Thirlbeck Systems"]}}))
        _, custom, _ = dry_run(self.tmp, home, workdir, "--compare", "individual:organization", "--target",
                               f"individual={PERSON}", "--entities", str(entities))
        role = P.names_said("claude", "standard", "custom")[0]
        self.assertEqual(role, "The types are compared using the roster's entities; a name you add is scored against "
                               "the roster's entity of its type, and leads the result.")
        self.assertIn(f"      {role}\n", custom)
        self.assertNotIn("invented", custom.split("named targets:")[1].split("\n")[1])

    def test_a_working_folder_in_home_still_reads_the_default_claude_md(self):
        """README's paragraph on the working folder: with --config-dir, a working folder at or inside the home folder
        still lists ~/.claude/CLAUDE.md (the home folder's own project memory), and one outside home lists the chosen
        folder's memory alone; the memory block shows exactly that list."""
        home, work, _ = two_folders(self.tmp)
        old = home / ".claude" / "CLAUDE.md"
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        (home / "projects" / "x").mkdir(parents=True)
        for workdir, want in ((home, [work / "CLAUDE.md", old]), (home / "projects" / "x", [work / "CLAUDE.md", old]),
                              (outside, [work / "CLAUDE.md"])):
            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                self.assertEqual(P.memory_files("claude", workdir, config_dir=work), want, workdir)
                report = P.memory_report("claude", workdir, "on", "as-configured", work, "option")
            self.assertEqual([f["path"] for f in report["files"]], [str(f) for f in want])
        self.assertIn("pass `--workdir` with a folder outside your home folder",
                      " ".join((REPO / "README.md").read_text(encoding="utf-8").split()))

    def test_the_report_reads_each_file_once_and_holds_no_content(self):
        home, work, workdir = two_folders(self.tmp)
        report = P.memory_report("claude", workdir, "on", "as-configured", work, "option",
                                 [P.entity("individual", PERSON)])
        self.assertEqual([f["title"] for f in report["files"]], ["Working agent"])
        self.assertEqual(report["notes"], [])
        self.assertNotIn(PERSON, json.dumps(report))            # the content is read for the note, never kept
        self.assertEqual(P.memory_record(report), {"memory_files": {str(work / "CLAUDE.md"): P.sha256_text(WORK_NOTES)},
                                                   "memory_bytes": {str(work / "CLAUDE.md"): len(WORK_NOTES)},
                                                   "config_dir": str(work)})


# ---- 3d: recorded, and said in the heads ---------------------------------------------------------------------------
class TestRecorded(Scratch):
    META = {"probatio": "4.0-dev", "agent": "claude", "memory": "on", "tier": "standard", "recipe": "standard@1",
            "target_type": "organization", "plan_fingerprint": "f" * 64}

    def test_the_head_words(self):
        home = str(Path.home())
        w = P.memory_words
        self.assertEqual(w(dict(self.META, memory_files={"a": "x", "b": "y"}, memory_bytes={"a": 3000, "b": 4100},
                                config_dir=home + "/agents/work"), "memory on"),
                         "memory on: 2 files, 7.1 KB, from ~/agents/work")
        self.assertEqual(w(dict(self.META, memory="both", memory_files={}, memory_bytes={}, config_dir="/c"),
                           "memory both"), "memory both: 0 files, 0 bytes, from /c")
        self.assertEqual(w(dict(self.META, memory="off", memory_files={}, config_dir="/c"), "memory off"),
                         "memory off")
        self.assertEqual(w(dict(self.META, memory_files={"a": "x", "b": "y"}), "memory on"), "memory on: 2 files")
        self.assertEqual(w({}, "memory on"), "memory on")                   # a card scored without a run record

    def test_the_cross_type_head_says_it_and_titles_stay_out(self):
        meta = dict(self.META, memory_files={"/c/CLAUDE.md": "x"}, memory_bytes={"/c/CLAUDE.md": 2500},
                    config_dir="/c")
        first = O.head_lines(meta, "individual:organization", False, "standard", "claude")[0]
        self.assertIn(" · memory on: 1 file, 2.5 KB, from /c · ", first)
        self.assertIn(" · memory on · ", O.head_lines({}, "individual:organization", False, "standard", "claude")[0])

    def test_a_resume_refuses_another_config_folder(self):
        out = self.tmp / "run"
        out.mkdir()
        meta = dict(self.META, memory_files={}, memory_bytes={}, config_dir="/one")
        P.check_resume(out, meta)
        P.check_resume(out, dict(meta))                                         # the same folder resumes
        P.check_resume(out, dict(meta, memory_bytes={"/x": 1}))                 # sizes follow the sha256s
        with self.assertRaises(SystemExit) as stop:
            P.check_resume(out, dict(meta, config_dir="/two"))
        self.assertIn("(config_dir changed)", str(stop.exception))

    def test_a_folder_recorded_before_the_config_folder_resumes_on_its_memory_record(self):
        out = self.tmp / "run"
        out.mkdir()
        old = dict(self.META, memory_files={"/c/CLAUDE.md": "a" * 64})
        (out / "run.json").write_text(json.dumps(old))
        P.check_resume(out, dict(old, memory_bytes={"/c/CLAUDE.md": 10}, config_dir="/c"))
        with self.assertRaises(SystemExit) as stop:
            P.check_resume(out, dict(old, memory_files={"/d/CLAUDE.md": "a" * 64}, config_dir="/d"))
        self.assertIn("(memory_files changed)", str(stop.exception))

    def test_a_cross_type_record_resumes_by_the_same_rule(self):
        out = self.tmp / "run"
        out.mkdir()
        cross = {k: v for k, v in self.META.items() if k != "target_type"}
        meta = dict(cross, compare="individual:organization", memory_files={}, memory_bytes={}, config_dir="/one")
        P.check_resume(out, meta)
        P.check_resume(out, dict(meta, memory_bytes={"/x": 1}))
        with self.assertRaises(SystemExit):
            P.check_resume(out, dict(meta, config_dir="/two"))


# ---- 3f: other Claude Code config folders on this machine ----------------------------------------------------------
class TestCandidates(Scratch):
    def test_the_rules(self):
        home = self.tmp / "home"
        (home / "projects").mkdir(parents=True)
        (home / ".claude.json").write_text("{}")                                # the default's: never home itself
        found = config_folder(home / "agents" / "work", WORK_NOTES)              # depth 2
        config_folder(home / "deep" / "er" / "still")                           # depth 3, no CLAUDE.md
        config_folder(home / "a" / "b" / "c" / "d")                             # depth 4: too deep
        config_folder(home / ".hidden-agent", OLD_NOTES)                        # hidden
        config_folder(home / "code" / "node_modules" / "pkg")                   # skipped by name
        venv = config_folder(home / "tools" / "env1")
        (venv / "pyvenv.cfg").write_text("")                                    # a virtual environment
        config_folder(home / "Library" / "thing")
        in_use = config_folder(home / "agents" / "current")
        (home / "links").mkdir()
        os.symlink(found, home / "links" / "to-work")                           # never followed
        got = P.config_candidates(home, in_use=in_use)
        self.assertEqual([c["path"] for c in got], [str(found), str(home / "deep" / "er" / "still")])
        self.assertEqual(got[0]["claude_md"], {"bytes": len(WORK_NOTES), "title": "Working agent"})
        self.assertIsNone(got[1]["claude_md"])
        self.assertEqual(P.config_candidates(home / "missing"), [])

    def test_a_hidden_claude_folder_directly_under_home_is_looked_at(self):
        """A hidden folder directly under home whose name begins ".claude" (~/.claude-work) is a candidate when it
        holds both marks; the default ~/.claude itself, the folder in use, a hidden folder of any other name and a
        ".claude" folder deeper down are not, and the search never goes into a hidden folder."""
        home = self.tmp / "home"
        work = config_folder(home / ".claude-work", WORK_NOTES)
        config_folder(home / ".claude", OLD_NOTES)                              # the default: never offered
        config_folder(home / ".agents", WORK_NOTES)                             # hidden, another name: skipped
        config_folder(home / "code" / ".claude-deep")                           # hidden, not directly under home
        config_folder(home / ".claude-work" / "inner")                          # inside a hidden folder: not searched
        (home / ".claude-empty").mkdir()                                        # no marks: not a config folder
        (home / ".claude.json").write_text("{}")                                # a file, not a folder
        got = P.config_candidates(home)
        self.assertEqual([c["path"] for c in got], [str(work)])
        self.assertEqual(got[0]["claude_md"]["title"], "Working agent")
        self.assertEqual(P.config_candidates(home, in_use=work), [])
        self.assertEqual([P.hidden_candidate(n) for n in (".claude-work", ".claude2", ".claude", ".agents", "claude")],
                         [True, True, False, False, False])

    def test_the_budget_stops_a_deep_tree_and_says_nothing(self):
        home = self.tmp / "home"
        config_folder(home / "agents" / "work")
        for i in range(60):
            (home / f"wide{i:02}" / "x").mkdir(parents=True)
        self.assertEqual(len(P.config_candidates(home)), 1)
        self.assertEqual(P.config_candidates(home, entries=50), [])
        self.assertEqual(P.config_candidates(home, seconds=0), [])
        start = time.monotonic()
        P.config_candidates(home)
        self.assertLess(time.monotonic() - start, P.CANDIDATE_SECONDS + 0.5)
        self.assertEqual((P.CANDIDATE_DEPTH, P.CANDIDATE_SECONDS, P.CANDIDATE_ENTRIES), (3, 0.3, 20000))

    def test_nothing_about_them_reaches_a_result_file(self):
        """The candidates and the titles are the preflight's and the page's: run.json's record of the memory holds
        the paths, hashes, sizes and folder only (memory_record), and the scorecard heads the count, size and folder."""
        home, work, workdir = two_folders(self.tmp)
        report = P.memory_report("claude", workdir, "on", "as-configured", home / ".claude", "default", [])
        record = P.memory_record(report)
        self.assertEqual(set(record), {"memory_files", "memory_bytes", "config_dir"})
        self.assertNotIn("Old notes", json.dumps(record))
        self.assertNotIn("Old notes", P.memory_words(dict(TestRecorded.META, **record), "memory on"))


# ---- what reaches the terminal and the page: display, one home (the final review's F1) ------------------------------
HOSTILE = "evil\x1b[31mRED‮txt.exe\nforged line"     # an escape sequence, a right-to-left override, a newline


def unseen_in(text):
    """The unseen characters (probatio's own rule) in output, as code points, its own line breaks aside."""
    return [hex(ord(ch)) for ch in text if P.unseen(ch) and ch != "\n"]


class TestDisplay(unittest.TestCase):
    def test_the_rule(self):
        d = P.display
        self.assertEqual(d("/home/me/.claude"), "/home/me/.claude")              # a plain path is itself
        self.assertEqual(d(HOSTILE), "evil\\x1b[31mRED\\u202etxt.exe\\nforged line")
        for ch in "\x00\x07\t\r\x7f\x9b​‍⁦﻿  ㅤ\U000e0041":
            self.assertEqual(unseen_in(d(f"a{ch}b")), [], hex(ord(ch)))
            self.assertNotIn(ch, d(f"a{ch}b"))
        self.assertEqual(d("naïve 東京 ü"), "naïve 東京 ü")                       # letters of any script stay
        long = d("/x/" + "y" * 2000 + "/CLAUDE.md")
        self.assertEqual((len(long), long[:3], long[-10:], long.count("…")),
                         (P.DISPLAY_CHARS, "/x/", "/CLAUDE.md", 1))
        self.assertEqual(len(d("z" * 2000, cap=None)), 2000)
        self.assertEqual(d("a\nb\x1b", cap=None, keep="\n"), "a\nb\\x1b")
        self.assertEqual(P.refusal_text("\ninterrupted ‮"), "\ninterrupted \\u202e")
        self.assertEqual(P.quoted("a‮b\x1b"), '"a\\u202eb\\u001b"')        # quoted escapes what JSON leaves
        self.assertEqual(P.plain_text("tab\thereㅤ x"), "tab here x")          # titles drop them, one rule

    def test_the_page_draws_text_never_markup(self):
        page = (REPO / "ui.py").read_text(encoding="utf-8")
        for markup in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            self.assertNotIn(markup + "(" if markup == "document.write" else markup + " =", page)


class TestHostileNames(Scratch):
    """A folder whose name holds an escape sequence, a right-to-left override and a newline reaches no terminal and no
    page raw: through --config-dir, through the search for other config folders, through a refusal, through the
    scorecard head and through the --ui page's JSON."""

    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        config_folder(self.home / ".claude", OLD_NOTES)
        self.hostile = config_folder(self.home / "agents" / HOSTILE, WORK_NOTES)
        self.work = self.tmp / "workdir"
        self.work.mkdir()
        stand_in_bin(self.tmp / "bin")

    def assertClean(self, *texts):
        for text in texts:
            self.assertEqual(unseen_in(text), [])
            self.assertNotIn("\nforged line", text)                    # the name's newline forged no line

    def test_through_config_dir(self):
        code, out, err = dry_run(self.tmp, self.home, self.work, "--target", PERSON, "--tier", "quick",
                                 "--config-dir", str(self.hostile))
        self.assertEqual(code, 0, P.display(err))
        self.assertClean(out, err)
        self.assertIn(P.MEMORY_FOLDER_LINE.format(folder=P.display("~/agents/" + HOSTILE),
                                                  source=P.source_words("claude", "option")), out)
        self.assertIn(f"    {P.display(self.hostile / 'CLAUDE.md')}  ({len(WORK_NOTES)} bytes; \"Working agent\")\n",
                      out)

    def test_through_discovery(self):
        code, out, err = dry_run(self.tmp, self.home, self.work, "--target", PERSON, "--tier", "quick")
        self.assertEqual(code, 0, P.display(err))
        self.assertClean(out, err)
        self.assertIn(f"    {P.display('~/agents/' + HOSTILE)}  (CLAUDE.md {len(WORK_NOTES)} bytes; "
                      f"\"Working agent\"): {P.CANDIDATE_ESCAPED}\n", out)    # no command naming another folder
        plain = config_folder(self.home / "agents" / "plain")
        self.assertIn(f"--config-dir {plain}\n", dry_run(self.tmp, self.home, self.work, "--target", PERSON)[1])

    def test_through_refusals(self):
        gone = str(self.home / ("gone" + HOSTILE))
        code, out, err = dry_run(self.tmp, self.home, self.work, "--target", PERSON, "--config-dir", gone)
        self.assertEqual((code, out, err),
                         (2, "", P.CONFIG_MISSING.format(P.display(gone), "CLAUDE_CONFIG_DIR") + "\n"))
        (self.hostile / "CLAUDE.md").unlink()
        (self.hostile / "CLAUDE.md").symlink_to("CLAUDE.md")               # a link that loops: a memory refusal
        code, out, err = dry_run(self.tmp, self.home, self.work, "--target", PERSON, "--config-dir", str(self.hostile))
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, str(P.memory_refusal(self.hostile / "CLAUDE.md", "loop").code) + "\n")
        self.assertClean(err)
        code, out, err = dry_run(self.tmp, self.home, self.work, "--target", "Wilhelmina\x1b[2JQuarrender")
        # a name's refusal quotes the character it refuses
        self.assertEqual(code, 1)
        self.assertClean(out, err)
        code, out, err = dry_run(self.tmp, self.home, self.work, "--target", PERSON, "stray" + HOSTILE)  # argparse's
        self.assertEqual(code, 2)
        self.assertClean(out, err)
        self.assertIn(": error: unrecognized arguments: " + P.display("stray" + HOSTILE, cap=None) + "\n", err)
        entities = self.tmp / ("mine" + HOSTILE + ".json")
        entities.write_text("not json")
        code, out, err = dry_run(self.tmp, self.home, self.work, "--compare", "individual:organization", "--entities",
                                 str(entities))
        self.assertEqual(code, 1)
        self.assertClean(out, err)
        proc = subprocess.run([sys.executable, "-B", str(REPO / "probatio.py"), "--rescore", gone], cwd=REPO,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual((proc.returncode, proc.stdout), (1, ""))
        self.assertEqual(proc.stderr, f"{P.display(gone)} is not a probatio run folder: it holds no run.json.\n")
        self.assertClean(proc.stderr)

    def test_through_the_scorecard_head(self):
        """The head writes the folder from its home folder, so the test sets HOME to its own home: the words are the
        same wherever TMPDIR lies."""
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            words = P.memory_words(dict(TestRecorded.META, memory_files={}, config_dir=str(self.hostile)), "memory on")
        self.assertClean(words)
        self.assertTrue(words.endswith("from " + P.display("~/agents/" + HOSTILE)), words)

    def test_through_the_page(self):
        sys.path.insert(0, str(REPO))
        import ui as U
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            chosen = U.memory_answer(["--agent", "claude", "--target", PERSON, "--workdir", str(self.work),
                                      "--config-dir", str(self.hostile)])
            found = U.memory_answer(["--agent", "claude", "--target", PERSON, "--workdir", str(self.work)])
            gone = U.memory_answer(["--agent", "claude", "--target", PERSON, "--workdir", str(self.work),
                                    "--config-dir", str(self.home / ("gone" + HOSTILE))])
        for answer in (chosen, found, gone):
            self.assertClean(json.dumps(answer, ensure_ascii=False), *strings(answer))
        self.assertEqual(chosen["report"]["folder"], P.display("~/agents/" + HOSTILE))
        self.assertEqual(chosen["report"]["files"][0]["path"], P.display(self.hostile / "CLAUDE.md"))
        hostile = [c for c in found["candidates"] if c["folder"] == P.display("~/agents/" + HOSTILE)]
        self.assertEqual([c["path"] for c in hostile], [None])                 # never put into the field by a click
        self.assertEqual(found["escaped"], P.CANDIDATE_ESCAPED)
        self.assertEqual(gone["missing"], P.display("~/gone" + HOSTILE))
        self.assertEqual(U.folder_needs({"config_dir": "~/x" + HOSTILE}, {"HOME": str(self.home)}),
                         [U.NEEDS_FOLDER.format(P.display("~/x" + HOSTILE))])
        self.assertClean(U.command_text(["--config-dir", HOSTILE]))


def strings(value):
    """Every string inside a JSON answer, keys included."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in strings(k) + strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return []


# ---- a memory file the run cannot read: one plain line, before any call (F4) -----------------------------------------
class TestUnreadableMemory(Scratch):
    """Where a memory file is read, a file this user cannot read, a link that loops (where a memory file is, or named
    by an @import), a folder and a FIFO each stop the run in one plain line (memory_refusal) before any call; a file
    whose first bytes are not text is listed with "title unreadable" and the run goes on; a link to nothing, a folder
    under rules/ and an @word naming a folder are no memory file and change nothing."""

    def setUp(self):
        super().setUp()
        self.conf = config_folder(self.tmp / "config")
        self.md = self.conf / "CLAUDE.md"
        self.work = self.tmp / "workdir"
        self.work.mkdir()
        stand_in_bin(self.tmp / "bin")

    def run_dry(self):
        return dry_run(self.tmp, self.tmp, self.work, "--target", PERSON, "--tier", "quick", "--config-dir",
                       str(self.conf))

    def refused(self, path, problem, detail=""):
        code, out, err = self.run_dry()
        self.assertEqual((code, out), (1, ""), P.display(err))
        self.assertEqual(err, str(P.memory_refusal(path, problem, detail).code) + "\n")
        self.assertNotIn("Traceback", err)
        return err

    def test_an_unreadable_file(self):
        if os.geteuid() == 0:
            self.skipTest("access checks do not bind root")
        self.md.write_text("# Private\n")
        self.md.chmod(0)
        self.addCleanup(self.md.chmod, 0o600)
        self.assertIn("it cannot be read (Permission denied)", self.refused(self.md, "unread", "Permission denied"))
        sys.path.insert(0, str(REPO))
        import ui as U
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp)}):
            said = U.memory_answer(["--agent", "claude", "--target", PERSON, "--workdir", str(self.work),
                                    "--config-dir", str(self.conf)])
        self.assertEqual(said, {"error": str(P.memory_refusal(self.md, "unread", "Permission denied").code)})

    def test_a_link_that_loops_a_folder_and_a_fifo(self):
        self.md.symlink_to("CLAUDE.md")
        self.refused(self.md, "loop")
        self.md.unlink()
        self.md.mkdir()
        self.refused(self.md, "folder")
        self.md.rmdir()
        os.mkfifo(self.md)
        self.refused(self.md, "other")

    def test_an_import_that_loops(self):
        self.md.write_text("# Notes\n@loop.md\n")
        (self.conf / "loop.md").symlink_to("loop.md")
        self.refused(self.conf / "loop.md", "loop")

    def test_a_binary_file_is_listed_with_its_title_unreadable(self):
        self.md.write_bytes(b"\x00\xff\xfe\x1b[31m")
        code, out, err = self.run_dry()
        self.assertEqual(code, 0, P.display(err))
        self.assertIn(f"    {self.md}  (8 bytes; {P.TITLE_UNREADABLE})\n", out)
        self.assertEqual(unseen_in(out), [])
        self.assertEqual(TestPreflight.listed(self, out), [str(self.md)])         # the harness reads it as before

    def test_what_is_no_memory_file(self):
        self.md.symlink_to("nowhere.md")                                        # a link to nothing
        (self.conf / "rules" / "folder.md").mkdir(parents=True)                 # a folder under rules/
        (self.conf / "rules" / "folder.md" / "inner.md").write_text("# Inner rule\n")
        (self.conf / "rules" / "real.md").write_text("# Real rule\nSee @docs and @gone.md.\n")
        (self.conf / "rules" / "docs").mkdir()                                  # an @word naming a folder
        code, out, err = self.run_dry()
        self.assertEqual(code, 0, P.display(err))
        self.assertEqual(TestPreflight.listed(self, out), [str(self.conf / "rules" / "folder.md" / "inner.md"),
                                                           str(self.conf / "rules" / "real.md")])


# ---- the search's budgets are spent while it reads (F3) --------------------------------------------------------------
class Counted:
    """A stand-in for os.scandir that counts each entry taken and may sleep before each one (a slow listing)."""
    taken, pause = 0, 0.0

    def __init__(self, path):
        self.it = REAL_SCANDIR(path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.it.close()

    def __iter__(self):
        return self

    def __next__(self):
        if Counted.pause:
            time.sleep(Counted.pause)
        entry = next(self.it)
        Counted.taken += 1
        return entry


REAL_SCANDIR = os.scandir


class TestBudgets(Scratch):
    def setUp(self):
        super().setUp()
        Counted.taken, Counted.pause = 0, 0.0
        self.home = self.tmp / "home"
        config_folder(self.home / "agents" / "work", WORK_NOTES)

    def search(self, **budget):
        with mock.patch.object(P.os, "scandir", Counted):
            start = time.monotonic()
            got = P.config_candidates(self.home, **budget)
            return got, time.monotonic() - start

    def test_a_tree_larger_than_the_entry_budget(self):
        wide = self.home / "wide"
        for i in range(400):
            (wide / f"d{i:03}").mkdir(parents=True)
        self.assertEqual(len(self.search()[0]), 1)                              # within the default budget: found
        Counted.taken = 0
        got, _ = self.search(entries=100)
        self.assertEqual(got, [])
        self.assertEqual(Counted.taken, 100)                                    # never one past its allowance

    def test_a_slow_listing_stops_inside_the_time_budget(self):
        for i in range(200):
            (self.home / f"slow{i:03}").mkdir()
        Counted.pause = 0.01                                                    # two seconds to read it all
        got, took = self.search(seconds=0.1)
        self.assertEqual(got, [])
        self.assertLess(took, 1.0)                                              # ten times the budget, at most
        self.assertLess(Counted.taken, 50)

    def test_a_huge_or_slow_title_stays_inside_the_budget(self):
        big = config_folder(self.home / ".claude-big")
        with (big / "CLAUDE.md").open("wb") as f:                               # 200 MB, mostly a hole on disk
            f.write(b"# Large notes\n" + b"text\n" * P.TITLE_BYTES)              # text where the title is read
            f.seek(200 * 1000 * 1000 - 1)
            f.write(b"\n")
        read, real = [0], os.read

        def counted_read(fd, n):
            got = real(fd, n)
            read[0] += len(got)
            return got
        with mock.patch.object(P.os, "read", counted_read):
            got, took = self.search()
        self.assertEqual([(Path(c["path"]).name, c["claude_md"]) for c in got],
                         [(".claude-big", {"bytes": 200 * 1000 * 1000, "title": "Large notes"}),
                          ("work", {"bytes": len(WORK_NOTES), "title": "Working agent"})])
        self.assertLessEqual(read[0], P.TITLE_BYTES + len(WORK_NOTES))           # a title is never the whole file
        self.assertLess(took, P.CANDIDATE_SECONDS * 5)

        def slow_read(fd, n):
            time.sleep(0.05)
            return real(fd, n)
        with mock.patch.object(P.os, "read", slow_read):
            got, took = self.search(seconds=0.1)
        self.assertEqual(got, [])
        self.assertLess(took, 1.0)

    def test_the_last_blocking_read_cannot_return_a_late_answer(self):
        """The re-check's R2: the deadline is checked again after each blocking read and before the search answers,
        so a title read that blocks past the deadline (here the last read of the only candidate's CLAUDE.md, which
        returns its end) ends the search with nothing, though it is not cut short."""
        config_folder(self.home / ".claude-late", "# Late\n")
        reads, real = [0], os.read

        def last_read_blocks(fd, n):
            got = real(fd, n)
            reads[0] += 1
            if not got:                                                         # the read that finds the end
                time.sleep(0.15)
            return got
        with mock.patch.object(P.os, "read", last_read_blocks):
            got, took = self.search(seconds=0.1)
        self.assertEqual(got, [])
        self.assertGreater(reads[0], 0)
        self.assertLess(took, 1.0)

    def test_the_allowance_is_taken_before_the_entry_is_read(self):
        """Budget.take refuses before the iterator advances: a budget of n reads n entries at most, for any n."""
        for i in range(30):
            (self.home / f"d{i:02}").mkdir()
        for budget in (0, 1, 5, 12):
            Counted.taken = 0
            self.assertEqual(self.search(entries=budget)[0], [])
            self.assertEqual(Counted.taken, budget)


# ---- the stand-in records five named values and nothing else (F6); no test prints an environment (F2) ----------------
PLANTED = "PROBATIO_TEST_PLANTED_API_TOKEN"            # an invented variable, a secret's kind of name
FAKE_SECRET = "planted-fake-secret-4f7c2e91"            # an invented value: it must appear in no output


class TestNoEnvironmentIsShown(Scratch):
    def test_the_stand_in_records_its_five_values_and_nothing_else(self):
        self.assertEqual(set(FAKE_SHOWN), {"CLAUDE_CONFIG_DIR", "CODEX_HOME", "HOME", "CLAUDE_CODE_DISABLE_CLAUDE_MDS",
                                           "CLAUDE_CODE_DISABLE_AUTO_MEMORY"})
        calls = self.tmp / "calls.jsonl"
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp), "FAKE_CLAUDE_CALLS": str(calls),
               "CLAUDE_CONFIG_DIR": str(self.tmp / "conf"), PLANTED: FAKE_SECRET,
               "ANTHROPIC_API_KEY": FAKE_SECRET + "-2"}                          # invented too
        proc = subprocess.run([sys.executable, "-B", str(FAKE), "--version"], env=env, capture_output=True, text=True,
                              timeout=60)
        self.assertEqual(proc.returncode, 0)
        text = calls.read_text(encoding="utf-8")
        self.assertNotIn(FAKE_SECRET, text + proc.stdout + proc.stderr)
        self.assertNotIn(PLANTED, text)
        record = json.loads(text)
        self.assertEqual(sorted(record["env"]), ["CLAUDE_CONFIG_DIR", "HOME"])     # the set ones of the five

    def test_a_failing_environment_comparison_names_variables_only(self):
        """The environment check of test_the_options_carry_the_folder_and_nothing_changes_the_environment, made to
        fail on purpose: an invented variable holding an invented value is planted, and the code under test changes
        it; the failure report names the variable and holds neither value."""
        real = P.agent_options

        def changing(a):
            got = real(a)
            os.environ[PLANTED] = FAKE_SECRET + "-changed"
            return got
        case = TestInnerCall("test_the_options_carry_the_folder_and_nothing_changes_the_environment")
        result = unittest.TestResult()
        with mock.patch.dict(os.environ, {PLANTED: FAKE_SECRET}), mock.patch.object(P, "agent_options", changing):
            case.run(result)
        report = "".join(text for _, text in result.failures + result.errors)
        self.assertEqual((len(result.failures), len(result.errors)), (1, 0))
        self.assertIn(PLANTED, report)
        self.assertNotIn(FAKE_SECRET, report)
        self.assertEqual(changed_names({"A": "1", "B": "2"}, {"A": "1", "B": "3", "C": ""}), ["B", "C"])


if __name__ == "__main__":
    unittest.main()
