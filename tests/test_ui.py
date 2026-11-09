"""Tests for the --ui page (ui.py): the form's defaults are probatio's own; build_command's command lines parse to the
options the form chose, with only the flags that change something, and the one argv shown, checked and run; the
entities file loads and round-trips; a person is the individual row's target, never --name, and plans as --name does;
the model choices, the watch choice and the hints under "more"; the parser's and the dry run's messages reach the page
verbatim; the server's refusals (the token, the Host, the CSP, POST only, CSRF, the exact routes, malformed lengths
and deep JSON); the roster snapshots (named by their whole sha256, never overwritten, no symbolic link followed,
concurrent Checks that cannot cross); Check and Run with stub CLIs and a stub run, one run at a time, and Stop reaching
the page's own child only; and no secret, and nothing of calibration/local.json, on the page. No test calls a model:
the CLIs are the guard's stubs, which answer --version and refuse anything else, and a run is a stub program.

    python3 -m unittest discover -s tests
"""
import argparse
import errno
import hashlib
import http.client
import io
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
sys.path[:0] = [str(REPO), str(REPO / "tests")]
import live as L  # noqa: E402
import probatio as P  # noqa: E402
import ui as U  # noqa: E402
from test_user_options import STUB_SH  # noqa: E402  (the guard's stub CLIs, one home)

# A run, for the tests: a dry run is the real probatio.py; anything else prints what a run prints (the watch link with
# --watch, a progress line, any text in STUB_SAY, the result lines when STUB_FINISH is set), writes its pid, and holds
# until SIGINT, which it answers as probatio does; a finished run without --watch ends, as probatio's does.
STUB_RUN = '''import os, secrets, sys, time
real, args = sys.argv[1], sys.argv[2:]
if "--dry-run" in args:
    os.execv(sys.executable, [sys.executable, "-B", real, *args])
open(os.environ["STUB_PID"], "w").write(str(os.getpid()))
link = "http://127.0.0.1:45871/?t=" + secrets.token_urlsafe(24)
if "--watch" in args:
    sys.stderr.write("  watch it live: " + link + "\\n")
print("args: " + " ".join(args))
print("probatio: quick . 36/360 . 35 valid")
if os.environ.get("STUB_SAY"):
    print(os.environ["STUB_SAY"])
if os.environ.get("STUB_FINISH"):
    print("Scorecard: " + os.path.join(os.getcwd(), "results", "stub-run", "scorecard.json"))
    print("Report: " + os.path.join(os.getcwd(), "results", "stub-run", "report.md"))
    if "--watch" not in args:
        sys.exit(0)
    sys.stderr.write("\\nThe live page stays up at " + link + " until you press Ctrl-C.\\n")
try:
    while True:
        time.sleep(0.2)
except KeyboardInterrupt:
    sys.stderr.write("\\ninterrupted. Rerun the same command to resume.\\n")
    sys.exit(1)
'''
JANE = {"agent": "claude", "targets": {"individual": "Jane Doe"}, "watch": True}


def record_opens(event, args):
    """An audit hook (it cannot be removed, so it records only while RECORDING holds a list): every file opened, by
    any route, pathlib's included."""
    if event == "open" and RECORDING:
        RECORDING[-1].append(str(args[0]))


RECORDING = []
sys.addaudithook(record_opens)
MOZILLA = {"agent": "claude", "types": ["organization"], "targets": {"organization": "Mozilla"}, "tier": "quick"}


def wait_for(cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def parser_actions():
    """parse_args_v4's own ArgumentParser actions by option, caught as it parses (tests only)."""
    caught, real = [], argparse.ArgumentParser.parse_args

    def catch(parser, args=None, namespace=None):
        caught.append(parser)
        return real(parser, args, namespace)
    with mock.patch.object(argparse.ArgumentParser, "parse_args", catch):
        P.parse_args_v4(["--print-entities"])
    return {opt: act for act in caught[0]._actions for opt in act.option_strings}


class Stubbed(unittest.TestCase):
    """A scratch folder with the guard's stub claude and codex first on PATH, a home of its own and the forbidden-call
    log, the environment a Session's children get; the log must stay empty."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="probatio-ui-"))
        (self.tmp / "bin").mkdir()
        (self.tmp / "home").mkdir()
        for name, text in STUB_SH.items():
            (self.tmp / "bin" / name).write_text(text)
            (self.tmp / "bin" / name).chmod(0o755)
        (self.tmp / "run.py").write_text(STUB_RUN)
        self.env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        self.env.update(PATH=f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}", GUARD_DIR=str(self.tmp),
                        HOME=str(self.tmp / "home"), STUB_PID=str(self.tmp / "pid"), PYTHONDONTWRITEBYTECODE="1")
        self.program = [sys.executable, "-B", str(self.tmp / "run.py"), str(REPO / "probatio.py")]

    def tearDown(self):
        log = self.tmp / "forbidden-calls.log"
        self.assertFalse(log.exists() and log.read_text().strip(), "a CLI was asked for more than its version")
        for f in sorted(self.tmp.rglob("*"), reverse=True):
            f.rmdir() if f.is_dir() else f.unlink()
        self.tmp.rmdir()


class Served:
    """The page on loopback for one test (a Session, or the given actions), with requests that set the Host, the
    token and the headers as given."""

    def __init__(self, session):
        self.session = session
        self.server = U.serve(session)                     # as main serves it: the session gets the server's redactor
        self.port, self.token = self.server.server_address[1], self.server.token

    def request(self, method, path, body=None, token=True, header=True, host=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        sep = "&" if "?" in path else "?"
        url = path + (f"{sep}t={self.token}" if token is True else f"{sep}t={token}" if token else "")
        conn.putrequest(method, url, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{self.port}")
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8") if body is not None else None
        sent = {"Content-Type": "application/json", "Content-Length": str(len(data))} if data is not None else {}
        if header:
            sent[L.TOKEN_HEADER] = self.token if header is True else header
        sent.update(headers or {})
        for k, v in sent.items():
            conn.putheader(k, v)
        conn.endheaders(data)
        res = conn.getresponse()
        text = res.read().decode("utf-8", "replace")
        conn.close()
        return res, text

    def post(self, path, body):
        res, text = self.request("POST", path, body)
        assert res.status == 200, (res.status, text)
        return json.loads(text)

    def state(self):
        res, text = self.request("GET", "/state")
        assert res.status == 200, res.status
        return json.loads(text)

    def close(self):
        self.session.close(io.StringIO())
        self.server.shutdown()
        self.server.server_close()


# ---- the defaults are probatio's -----------------------------------------------------------------------------------------
class TestDefaults(unittest.TestCase):
    def test_every_default_is_the_parsers(self):
        a, d = P.parse_args_v4(["--print-entities"]), U.defaults()["default"]
        self.assertEqual((d["type"], d["agent"], d["tier"], tuple(d["classes"]), d["memory"], d["auto_memory"],
                          d["effort"], d["model"], d["jobs"]),
                         (a.target_type, a.agent, a.tier, a.classes, a.memory, a.auto_memory, a.effort, a.model,
                          a.jobs))
        self.assertIsNone(d["agent"])                     # --agent has no default, so the page chooses none

    def test_every_choice_is_one_the_parser_takes(self):
        acts, base = parser_actions(), U.defaults()
        self.assertEqual([k for k, _ in base["agents"]], list(acts["--agent"].choices))
        self.assertLess(set(base["tiers"]), set(acts["--tier"].choices))   # the user tiers; smoke is a terminal's
        self.assertEqual(base["memory"], list(acts["--memory"].choices))
        self.assertEqual(base["efforts"], list(acts["--effort"].choices))
        self.assertEqual(base["auto_memory"], list(acts["--auto-memory"].choices))
        self.assertEqual(base["types"], list(acts["--target-type"].choices))

    def test_the_rosters_are_print_entities(self):
        printed = json.loads(P.print_entities(P.load_entities(None, U.bank()), U.bank()))
        for t in P.TARGET_TYPES:
            for part in ("comparators", "controls", "targets"):
                self.assertEqual(U.defaults()["rosters"][t][part], printed[t][part])
        self.assertEqual(U.defaults()["counts"], {k: list(v) for k, v in P.LIST_COUNTS.items()})

    def test_the_page_types_no_default(self):
        base = U.defaults()
        typed = [base["default"]["tier"], base["default"]["effort"], base["default"]["auto_memory"]]
        typed += [U.entry_name(e) for t in P.TARGET_TYPES for part in U.LISTS for e in base["rosters"][t][part]]
        for word in typed:
            self.assertNotIn(word, U.PAGE, word)


# ---- the form as a command line --------------------------------------------------------------------------------------------
class TestBuildCommand(unittest.TestCase):
    def test_a_person_is_the_individual_rows_target(self):
        """A person, you or anyone, is typed in the individual row and becomes --target NAME, on v4's own path; the page
        never writes --name, so a "name" field (an older page's) changes nothing, in a single run or a comparison."""
        argv, entities = U.build_command(JANE)
        self.assertEqual((argv, entities), (["--agent", "claude", "--target", "Jane Doe", "--watch"], None))
        a = P.parse_args_v4(argv)
        self.assertEqual((a.target, a.name, a.tier, a.memory, a.effort, a.jobs),
                         (["Jane Doe"], None, "standard", "on", "medium", 4))
        self.assertFalse(P.uses_options(a))
        for form in (dict(JANE, watch="on"), dict(JANE, name="Someone Else")):
            self.assertEqual(U.build_command(form), (argv, None))
        for form in ({"agent": "claude", "name": "Jane Doe"}, dict(MOZILLA, name="Jane Doe"),
                     {"agent": "claude", "types": ["individual", "organization"], "name": "Jane Doe"}):
            self.assertEqual(U.build_command(form), U.build_command({k: v for k, v in form.items() if k != "name"}))
            self.assertNotIn("--name", U.build_command(form)[0], form)

    def test_a_default_spelled_out_adds_no_flag(self):
        base = U.defaults()
        spelled = dict(JANE, types=["individual"], tier="standard", memory="on",
                       effort="medium", jobs="4", auto_memory="as-configured", classes=["I", "II", "III"], model=" ",
                       rosters={t: {p: "\n".join(U.entry_name(e) for e in base["rosters"][t][p]) for p in U.LISTS}
                                for t in P.TARGET_TYPES})
        self.assertEqual(U.build_command(spelled), U.build_command(JANE))

    def test_forms_parse_to_the_options_they_chose(self):
        table = [
            (MOZILLA, dict(compare=None, target_type="organization", target=["Mozilla"], tier="quick",
                           classes=("I", "II"))),
            (dict(MOZILLA, classes=["I", "II"]), dict(tier="quick", classes=("I", "II"))),
            (dict(JANE, targets={"individual": "Ada Lovelace\n\nGrace Hopper\n"}),
             dict(compare=None, target_type="individual", target=["Ada Lovelace", "Grace Hopper"], name=None)),
            ({"agent": "codex", "types": ["nation", "individual", "organization"],
              "targets": {"nation": "France", "organization": ""}, "memory": "both", "effort": "high",
              "model": "gpt-x", "jobs": 2, "auto_memory": "off", "classes": ["I"], "tier": "full"},
             dict(agent="codex", compare="individual:organization:nation", target=["nation=France"],
                  memory="both", effort="high", model="gpt-x", jobs=2, auto_memory="off", classes=("I",),
                  tier="full", watch=True)),
            ({"agent": "claude", "types": ["nation", "individual"]}, dict(compare="individual:nation", target=None)),
            ({"agent": "claude", "classes": ["II", "III"]}, dict(classes=("II", "III"), tier="standard")),
            ({"agent": "claude", "tier": "full"}, dict(tier="full")),
        ]
        for form, want in table:
            argv, _ = U.build_command(form)
            a = P.parse_args_v4(argv)
            for key, value in want.items():
                self.assertEqual(getattr(a, key), value, (form, argv, key))
            self.assertEqual(shlex.split(U.command_text(argv)), ["python3", "probatio.py", *argv])
            self.assertEqual(argv[-1], U.WATCH)

    def test_readme_says_only_the_default_folder_is_shared(self):
        """The page writes no --out, so README says only the default results folder is shared with a terminal, that
        Check names a folder an earlier run holds, and how a row's names are written."""
        text = README.read_text(encoding="utf-8")
        for words in ("The page writes no `--out`, so only the default results folder is shared",
                      "a terminal run with `--out` is not reachable from the page",
                      "Check names that folder and approves nothing", "written `--target=-NAME`, as one word",
                      'a "=" in a name, since the type buttons choose the type'):
            self.assertIn(words, text)
        for form in (JANE, MOZILLA, dict(MOZILLA, targets={"organization": "Mozilla\nWikimedia"}),
                     {"agent": "claude", "types": list(P.TARGET_TYPES)}):
            self.assertFalse([w for w in U.build_command(form)[0] if w.partition("=")[0] == "--out"], form)

    def test_a_single_type_run_is_the_command_readme_teaches(self):
        """A single-type run is written as README writes it in a terminal (--target-type for an organization or a
        nation, plain --target NAME), so it takes v4's own path; --compare and TYPE=NAME appear only when two or three
        types are compared. Several targets of one type, or a roster edit, take the options path whatever the form,
        and keep --target-type."""
        block = README.read_text(encoding="utf-8").split("cd probatio\n", 1)[1].split("```", 1)[0]
        taught = [shlex.split(line)[2:] for line in block.splitlines() if "--target-type" in line]
        self.assertEqual(len(taught), 2)
        for words in taught:
            a = P.parse_args_v4(words)
            form = {"agent": a.agent, "types": [a.target_type], "targets": {a.target_type: a.target[0]},
                    "memory": a.memory}
            self.assertEqual(U.build_command(form)[0], [*words, U.WATCH])
            self.assertFalse(P.uses_options(a))
        self.assertEqual(U.build_command(JANE)[0][2:4], ["--target", "Jane Doe"])
        two = dict(MOZILLA, targets={"organization": "Mozilla\nWikimedia"})
        argv = U.build_command(two)[0]
        self.assertEqual(argv[2:8], ["--target-type", "organization", "--target", "Mozilla", "--target", "Wikimedia"])
        self.assertTrue(P.uses_options(P.parse_args_v4(argv)))
        edited = U.build_command(dict(MOZILLA, rosters={"organization": {"controls": "Varrow Analytics"}}))[0]
        self.assertEqual(edited[2:6], ["--target-type", "organization", "--target", "Mozilla"])
        for form in (MOZILLA, two, JANE, {"agent": "claude", "types": ["nation"]}):
            self.assertNotIn("--compare", U.build_command(form)[0])
            self.assertFalse([w for w in U.build_command(form)[0] if "=" in w], form)
        cross = U.build_command({"agent": "claude", "types": ["organization", "nation"],
                                 "targets": {"organization": "Mozilla"}})[0]
        self.assertEqual(cross[2:6], ["--compare", "organization:nation", "--target", "organization=Mozilla"])

    def test_an_edited_roster_is_an_entities_file_that_round_trips(self):
        form = dict(MOZILLA, rosters={"organization": {"comparators": "Anthropic\nOpenAI\nMozilla Foundation",
                                                       "controls": "Thirlbeck Systems\nVarrow Analytics"}})
        argv, text = U.build_command(form)
        path = argv[argv.index("--entities") + 1]
        self.assertEqual(path, f"results/ui-entities-{hashlib.sha256(text.encode('utf-8')).hexdigest()}.json")
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "roster.json"
            file.write_text(text, encoding="utf-8")
            spec = P.load_entities(str(file), U.bank())
            self.assertEqual([r["full"] for r in spec["organization"]["comparators"]],
                             ["Anthropic", "OpenAI", "Mozilla Foundation"])
            self.assertEqual([r["full"] for r in spec["organization"]["controls"]],
                             ["Thirlbeck Systems", "Varrow Analytics"])
            printed = json.loads(P.print_entities(spec, U.bank()))      # the file, printed, gives the form back
        again = {"organization": {p: "\n".join(map(U.entry_name, printed["organization"][p])) for p in U.LISTS}}
        self.assertEqual(U.build_command(dict(MOZILLA, rosters=again)), (argv, text))
        self.assertIn("custom roster: not calibrated (fixtures", U.preview(form)["roster"])

    def test_a_comparison_edits_controls_only(self):
        form = {"agent": "claude", "types": ["organization", "nation"],
                "rosters": {"organization": {"comparators": "Anthropic\nOpenAI"}}}
        self.assertEqual(U.build_command(form)[1], None)                # comparators are a single type's
        form["rosters"]["nation"] = {"controls": "Arvenia"}
        self.assertEqual(json.loads(U.build_command(form)[1]), {"nation": {"controls": ["Arvenia"]}})

    def test_build_command_is_pure(self):
        before = sorted((REPO / "results").iterdir())
        form = dict(MOZILLA, rosters={"organization": {"controls": "Varrow Analytics"}})
        self.assertEqual(U.build_command(form), U.build_command(json.loads(json.dumps(form))))
        self.assertEqual(sorted((REPO / "results").iterdir()), before)

    def test_the_page_offers_the_user_tiers_alone(self):
        """smoke, the calibration's setup plan, is a terminal's (--tier smoke): the page offers quick, standard and
        full, and build_command refuses any other tier, with no command (on the default type, smoke routed to v3 and
        could never run). Every tier it writes, on every type, is v4's (wants_v4)."""
        base = U.defaults()
        self.assertEqual((base["tiers"], list(base["planned"])), (list(P.USER_TIERS), list(P.USER_TIERS)))
        self.assertNotIn("setup", base)
        for words in ("setup plan", "D.setup", "smoke"):
            self.assertNotIn(words, U.PAGE)
        for tier in [t for t in P.V4_TIERS if t not in P.USER_TIERS] + ["slim", "bogus"]:
            for form in (dict(JANE, tier=tier), dict(MOZILLA, tier=tier)):
                with self.assertRaises(U.FormRefused) as caught:
                    U.build_command(form)
                self.assertEqual(str(caught.exception), U.TIER_REFUSED)
                self.assertEqual(U.preview(form), U.refused(U.TIER_REFUSED))
        for t in P.TARGET_TYPES:
            for tier in base["tiers"]:
                argv = U.build_command({"agent": "claude", "types": [t], "targets": {t: "Jane Doe"}, "tier": tier})[0]
                self.assertTrue(P.wants_v4(argv), argv)

    def test_row_names_pass_probatios_name_guard(self):
        """Each row line goes through probatio's own name guard before it is written: a "=" is refused in the page's
        words (the type is the type buttons', never TYPE=NAME in a row), anything else the guard refuses gets the
        guard's own message under the row's label, a name is written in the guard's form, and a name the guard
        allows that starts with "-" is written --target=NAME, one word, so argparse reads it as the value."""
        rows = [("individual", JANE), ("organization", MOZILLA),
                ("nation", {"agent": "claude", "types": ["individual", "nation"]})]
        for t, base in rows:
            for line in ("Jane=Doe", f"{t}=Jane Doe", "organization=Mozilla", "a=b"):
                form = dict(base, targets={t: "Fine Name\n" + line})
                with self.assertRaises(U.FormRefused) as caught:
                    U.build_command(form)
                self.assertEqual(str(caught.exception), U.NAME_EQUALS.format(t), (t, line))
                self.assertEqual(U.preview(form), U.refused(U.NAME_EQUALS.format(t)))
            for line in ("Moz{illa", "Real", "A) Foo", "x" * 61):
                with self.assertRaises(P.NameRefused) as guard:
                    P.check_name(line, U.ROW_LABEL.format(t))
                with self.assertRaises(U.FormRefused) as caught:
                    U.build_command(dict(base, targets={t: line}))
                self.assertEqual(str(caught.exception), str(guard.exception), (t, line))
        argv = U.build_command(dict(JANE, targets={"individual": "Jane   Doe\nZoe\u0308 Smith"}))[0]
        self.assertEqual(argv[2:6], ["--target", "Jane Doe", "--target", "Zo\u00eb Smith"])
        argv = U.build_command(dict(JANE, targets={"individual": "-Jane"}))[0]
        self.assertEqual(argv, ["--agent", "claude", "--target=-Jane", "--watch"])
        a = P.parse_args_v4(argv)
        self.assertEqual((a.target, P.uses_options(a)), (["-Jane"], False))
        self.assertIsNone(U.preview(dict(JANE, targets={"individual": "-Jane"}))["error"])
        self.assertEqual(U.prepared(argv), U.prepared(["--agent", "claude", "--target=-Jane"]))
        cross = U.build_command({"agent": "claude", "types": ["individual", "nation"],
                                 "targets": {"individual": "-Jane"}})[0]
        self.assertEqual(cross[4:6], ["--target", "individual=-Jane"])
        self.assertIsNone(U.parser_error(cross))

    def test_a_nul_is_refused_before_any_command(self):
        """No command line can carry a NUL (subprocess raises ValueError on one), so a target or a model holding one
        gets a bounded refusal, with no command, and never reaches a dry run."""
        for form in (dict(JANE, targets={"individual": "Jane\x00Doe"}), dict(JANE, model="x\x00y"),
                     dict(MOZILLA, targets={"organization": "Moz\x00illa"}),
                     {"agent": "claude", "types": ["individual", "nation"], "targets": {"nation": "Fr\x00ance"}}):
            with self.assertRaises(U.FormRefused) as caught:
                U.build_command(form)
            self.assertEqual(str(caught.exception), U.NUL_REFUSED)
            self.assertEqual(U.preview(form), U.refused(U.NUL_REFUSED))


class TestOneCommand(unittest.TestCase):
    """The command shown is the one Check checks and the one Run runs, byte for byte, with --watch or without it
    (finding 3): a fake dry run and a fake Runner record what they are given, and no process starts."""

    def test_shown_checked_and_run_are_one_argv(self):
        started = []

        class Recorder:
            def __init__(self, program, argv, folders, env):
                self.argv, self.code = list(argv), 0
                started.append(self.argv)

            def state(self):
                return {"argv": self.argv}
        dry = SimpleNamespace(returncode=0, stdout="    all   1 items   1 calls   under a minute (r)\n", stderr="")
        for form in (JANE, dict(JANE, watch="off"), {"agent": "claude"}, dict(MOZILLA, watch=False),
                     {"agent": "claude", "types": ["organization", "nation"], "watch": "on"}):
            with tempfile.TemporaryDirectory() as d, mock.patch.object(P, "HERE", Path(d)), \
                    mock.patch.object(U.subprocess, "run", return_value=dry) as checked, \
                    mock.patch.object(U, "prepared", return_value=[(Path(d) / "run", "plan")]), \
                    mock.patch.object(U, "Runner", Recorder):
                shown = U.preview(form)
                session = U.Session(program=["FAKE"])
                self.assertTrue(session.check({"form": form})["ok"])
                self.assertNotIn("error", session.run({"form": form, "confirmed": True}))
            argv = shown["argv"]
            self.assertEqual(U.WATCH in argv, form.get("watch") not in (False, "off"), form)
            self.assertEqual(argv.count(U.WATCH), int(argv[-1] == U.WATCH), form)          # last, when it is there
            self.assertEqual(checked.call_args.args[0], ["FAKE", *argv, "--dry-run"], form)
            self.assertEqual(started[-1], argv, form)
            self.assertEqual(shown["command"], U.command_text(started[-1]), form)


# ---- probatio's messages, verbatim -----------------------------------------------------------------------------------------
class TestVerbatim(unittest.TestCase):
    def parser_says(self, argv):
        err = io.StringIO()
        with mock.patch("sys.stderr", err), self.assertRaises(SystemExit) as caught:
            P.parse_args_v4(argv)
        return caught.exception.code if isinstance(caught.exception.code, str) else \
            err.getvalue().strip().splitlines()[-1].split(": error: ", 1)[1]

    def test_the_parsers_errors_reach_the_page_verbatim(self):
        for form in ({"targets": {"individual": "Jane Doe"}}, dict(MOZILLA, classes=["I", "III"]),
                     dict(JANE, jobs="x"), dict(JANE, classes=[]), dict(JANE, model="-x y")):
            argv, _ = U.build_command(form)
            self.assertEqual(U.preview(form)["error"], self.parser_says(argv), form)
        self.assertEqual(U.preview(dict(MOZILLA, classes=["I", "III"]))["error"], P.QUICK_NO_III)

    def test_a_model_is_one_word_in_a_terminal_and_on_the_page(self):
        """--model takes one word, in parse_args_v4, so a terminal's command line is held to it as the page's is: not
        empty, no leading "-" (an option to the CLI), no whitespace and no control or format character, with one
        message naming the rule and never the value. Every model the page lists passes."""
        rule = "argument --model: " + P.MODEL_RULE
        for model in ("-x y", "-s danger-full-access", "--demo=x y", "a\nb", "a b", "\u202eabc", "x\x00y", "a\u200bb",
                      ""):
            self.assertEqual(self.parser_says(["--agent", "codex", "--model", model]), rule, repr(model))
        for model in ("-x y", "-s danger-full-access", "a\nb", "\u202eabc"):
            self.assertEqual(U.preview(dict(JANE, model=model))["error"], rule, repr(model))
        err = io.StringIO()
        with mock.patch("sys.stderr", err), self.assertRaises(SystemExit) as caught:
            P.main(["--agent", "codex", "--model", "-s danger-full-access", "--dry-run"])     # a terminal's
        self.assertEqual((caught.exception.code, err.getvalue().strip().splitlines()[-1].split(": error: ")[1]),
                         (2, rule))
        for agent, models in U.MODELS.items():
            for model in models:
                self.assertEqual(P.parse_args_v4(["--agent", agent, "--model", model]).model, model)
                self.assertIsNone(U.preview({"agent": agent, "model": model, "targets": JANE["targets"]})["error"])

    def test_a_command_the_reply_would_change_is_refused(self):
        """Every reply passes the server's redactor, which drops control and format characters and writes secrets,
        key shapes and the page's token as <redacted>; a value it would change is refused, naming its option, so the
        command shown is always byte for byte what runs. A row's name with a control or format character never gets
        that far: the name guard refuses it first (test_row_names_pass_probatios_name_guard)."""
        token = "t" + secrets.token_hex(16)              # a name the guard lets through, as a token could be one
        redactor = L.ViewRedactor(extra=(token,))
        key = "sk-ant-" + secrets.token_hex(12)                                  # a fake key, built at run time
        for name in (token, key, "Jane " + key, "-" + key):
            form = dict(JANE, targets={"individual": name})
            self.assertIsNone(U.parser_error(U.build_command(form)[0]))
            self.assertEqual(U.preview(form, redactor=redactor)["error"], U.UNSHOWN.format("the value of --target"))
        for name in ("Jane\u202eDoe", "Jane\u200bDoe"):
            with self.assertRaises(U.FormRefused):
                U.build_command(dict(JANE, targets={"individual": name}))
        self.assertIsNone(U.preview(JANE, redactor=redactor)["error"])
        self.assertIsNone(U.unshown(U.build_command(JANE)[0], redactor))

    def test_refusals_the_page_cannot_follow_are_in_its_words(self):
        """probatio's refusals whose advice names what the page does not show get the page's words, from probatio's
        own messages: two names sharing a key (run them separately, or rename one), two runs on one folder, and a
        type the run does not compare (pick the type). A message the page can follow is shown as it is (None)."""
        def said(call, *args):
            with self.assertRaises(SystemExit) as caught:
                call(*args)
            return caught.exception.code
        a = P.parse_args_v4(["--agent", "claude", "--target", "Jane Doe", "--target", "Jane Do\u00e9"])
        key = said(P.prepare_runs, a, False)
        self.assertIn("--entities file", key)
        self.assertEqual(U.page_words(key), 'the individual targets "Jane Doe" and "Jane Do\u00e9" both have the key '
                         '"janedoe", which names a run\'s results folder, so they cannot run together: run them '
                         "separately (the second once the first's folder is moved or renamed), or rename one")
        runs = [{"kind": "single", "type": "individual", "name": "v4-x", "roster": {"T": {"full": n}}}
                for n in ("Jane Doe", "Jane Roe")]
        folders = said(P.check_run_folders, SimpleNamespace(out=None), runs)
        self.assertEqual(U.page_words(folders), 'the individual run for "Jane Doe" and the individual run for '
                         '"Jane Roe" would both write to results/v4-x, so they cannot run together: run them '
                         "separately, or rename one")
        compare = said(P.parse_targets, ["organization=Mozilla"], ["individual"], U.bank())
        self.assertIn("add organization to --compare", compare)
        self.assertEqual(U.page_words(compare), "a name in a row names an organization, which this run does not "
                         "measure: pick organization with the type buttons and type the name in its row")
        for message in ("--target is needed for an organization or a nation (and --name or --target for an "
                        "individual).", P.QUICK_NO_III, "", None):
            self.assertIsNone(U.page_words(message))

    def test_a_refused_roster_gives_load_entities_message(self):
        form = dict(JANE, rosters={"individual": {"controls": ""}})
        with self.assertRaises(SystemExit) as caught:
            P.load_entities(None, U.bank(), data=json.loads(U.build_command(form)[1]))
        self.assertEqual(U.preview(form)["error"], caught.exception.code)

    def test_labels_are_probatios(self):
        self.assertEqual(U.preview(JANE)["labels"], [P.fixtures_label(P.load_entities(None, U.bank()), ["individual"])])
        pair = U.preview({"agent": "claude", "types": ["organization", "nation"]})["labels"]
        self.assertEqual(pair[:2], [P.CROSS_LABEL, P.UNREGISTERED_LABEL])
        self.assertNotIn(P.UNREGISTERED_LABEL, U.preview({"agent": "claude", "types": P.TARGET_TYPES})["labels"])

    def test_the_plan_summary_reads_the_preflights_own_lines(self):
        single = ("    all          360 items        360 calls   about 14 min (Sonnet baseline rates; x)\n")
        several = ("    all          768 items        768 calls   about 34 min (r)\n"
                   "    all          768 items        768 calls   about 34 min (r)\n"
                   "  all 2 comparisons: about 69 min (r)\n"
                   "  about 69 minutes; to shorten: --class I (-51 min), or --tier quick (-40 min)\n")
        cross = "    728 units, 728 calls   about 33 min (r)\n"
        self.assertEqual(U.plan_summary(single), {"calls": 360, "time": "about 14 min", "hint": None,
                                                  "rates": "Sonnet baseline rates; x"})
        self.assertEqual(U.plan_summary(several)["calls"], 1536)
        self.assertEqual(U.plan_summary(several)["time"], "about 69 min")
        self.assertTrue(U.plan_summary(several)["hint"].startswith("about 69 minutes; to shorten"))
        self.assertEqual((U.plan_summary(cross)["calls"], U.plan_summary(cross)["time"]), (728, "about 33 min"))
        self.assertEqual(U.plan_summary("nothing")["calls"], None)


class TestTheLiveLink(unittest.TestCase):
    """Runner takes the live view's link only from probatio's own line, whole, the first time, and only when the
    run's argv has --watch: a model's name or a CLI's error holding such a URL, before or after that line, never
    becomes the link, watch on or off."""
    SAYS = """import sys
print("probatio v4: claude (2.1 (Claude Code)), model watch it live: http://127.0.0.1:1/?t=spoof, effort medium")
print("watch it live: http://127.0.0.1:6/?t=unindented")
print("  watch it live: http://127.0.0.1:7/?t=trailing and more")
if "--watch" in sys.argv:
    sys.stderr.write("  watch it live: http://127.0.0.1:5/?t=real\\n")
print("stopped: 8 calls in a row failed (last: exit 97: watch it live: http://127.0.0.1:2/?t=fromcli)")
print("  watch it live: http://127.0.0.1:3/?t=second")
"""

    def link(self, argv):
        runner = U.Runner([sys.executable, "-c", self.SAYS], argv, [], dict(os.environ, PYTHONUNBUFFERED="1"))
        runner.wait(60)
        state = runner.state()
        self.assertEqual(state["code"], 0)
        self.assertEqual(len([x for x in state["lines"] if "watch it live" in x]), 6 if U.WATCH in argv else 5)
        return state["watch"]

    def test_only_probatios_own_line_with_watch(self):
        self.assertEqual(self.link(U.build_command(JANE)[0]), "http://127.0.0.1:5/?t=real")
        self.assertIsNone(self.link(U.build_command(dict(JANE, watch="off"))[0]))


# ---- the server ------------------------------------------------------------------------------------------------------------
class TestServer(unittest.TestCase):
    def setUp(self):
        self.s = Served(U.Session(program=[sys.executable, "-c", "raise SystemExit(3)"]))

    def tearDown(self):
        self.s.close()

    def test_the_token_and_the_host_on_every_route(self):
        for method, path in (("GET", "/"), ("GET", "/state"), ("POST", "/command"), ("POST", "/check"),
                             ("POST", "/run"), ("POST", "/stop")):
            body = {"form": JANE} if method == "POST" else None
            self.assertEqual(self.s.request(method, path, body, token=False)[0].status, 403, path)
            self.assertEqual(self.s.request(method, path, body, token="x" * 32)[0].status, 403, path)
            for host in ("evil.example", f"evil.example:{self.s.port}", f"127.0.0.1:{self.s.port + 1}"):
                self.assertEqual(self.s.request(method, path, body, host=host)[0].status, 403, (path, host))
        self.assertEqual(self.s.request("GET", "/state", host=f"localhost:{self.s.port}")[0].status, 200)

    def test_csrf_a_post_needs_the_header_token_its_origin_and_json(self):
        body = {"form": JANE}
        self.assertEqual(self.s.request("POST", "/command", body, header=False)[0].status, 403)
        self.assertEqual(self.s.request("POST", "/command", body, header="x" * 32)[0].status, 403)
        for origin in ("http://evil.example", "null", f"http://127.0.0.1:{self.s.port + 1}"):
            self.assertEqual(self.s.request("POST", "/command", body, headers={"Origin": origin})[0].status, 403)
        for origin in (f"http://127.0.0.1:{self.s.port}", f"http://localhost:{self.s.port}"):
            self.assertEqual(self.s.request("POST", "/command", body, headers={"Origin": origin})[0].status, 200)
        plain = {"Content-Type": "text/plain"}                     # a form post needs no preflight: refused
        self.assertEqual(self.s.request("POST", "/command", body, headers=plain)[0].status, 415)
        too_long = {"Content-Length": str(L.MAX_BODY + 1)}             # refused before a byte of it is read
        self.assertEqual(self.s.request("POST", "/command", b"", headers=too_long)[0].status, 413)
        self.assertEqual(self.s.request("POST", "/command", b"[1, 2]")[0].status, 400)
        self.assertEqual(self.s.request("POST", "/command", b"\xff")[0].status, 400)

    def test_exact_routes_and_post_only_actions(self):
        for path in ("/favicon.ico", "/index.html", "/state/x", "/command/", "/../state", "/results"):
            self.assertEqual(self.s.request("GET", path)[0].status, 404, path)
        self.assertEqual(self.s.request("POST", "/nothing", {})[0].status, 404)
        for path in ("/command", "/check", "/run", "/stop"):
            res = self.s.request("GET", path)[0]
            self.assertEqual((res.status, res.getheader("Allow")), (405, "POST"), path)
        for path in ("/", "/state"):
            res = self.s.request("POST", path, {})[0]
            self.assertEqual((res.status, res.getheader("Allow")), (405, "GET"), path)
        for method in ("PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
            self.assertEqual(self.s.request(method, "/run", header=False)[0].status, 405, method)

    def test_the_csp_and_the_headers(self):
        for method, path, body in (("GET", "/", None), ("GET", "/state", None), ("POST", "/command", {"form": {}}),
                                   ("GET", "/nothing", None), ("GET", "/state", None)):
            res, _ = self.s.request(method, path, body)
            self.assertEqual(res.getheader("Content-Security-Policy"), L.content_policy(U.PAGE), path)
            self.assertEqual(res.getheader("Referrer-Policy"), "no-referrer")
            self.assertEqual(res.getheader("X-Frame-Options"), "DENY")
        policy = L.content_policy(U.PAGE)
        self.assertNotIn("unsafe", policy)
        for tag in ("script", "style"):
            self.assertEqual(len(re.findall(rf"<{tag}>", U.PAGE)), 1)
            self.assertIn(L.inline_hashes(U.PAGE, tag), policy)
        self.assertIn('<meta name="referrer" content="no-referrer">', U.PAGE)
        self.assertNotIn("innerHTML", U.PAGE)
        page = self.s.request("GET", "/")[1]
        self.assertNotIn(self.s.token, page)                           # the script reads it from the address

    def test_malformed_lengths_and_deep_json_are_refused_cleanly(self):
        """Finding 4: a Content-Length that is not ASCII decimal is 411 (str.isdigit takes "²"), one too large 413,
        and JSON nested too deep to parse 400, each with the security headers, no traceback and no action run."""
        called, err = [], io.StringIO()
        server = L.serve_page(U.PAGE, lambda: {}, actions={"/run": lambda request: called.append(request) or {}})
        port, token = server.server_address[1], server.token

        def post(length, body=b""):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.putrequest("POST", f"/run?t={token}", skip_host=True)
            conn.putheader("Host", f"127.0.0.1:{port}")
            conn.putheader(L.TOKEN_HEADER, token)
            conn.putheader("Content-Type", "application/json")
            if length is not None:
                conn.putheader("Content-Length", length)
            conn.endheaders(body or None)
            res = conn.getresponse()
            res.read()
            conn.close()
            return res
        deep = b'{"x":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"
        try:
            with mock.patch("sys.stderr", err):
                for length, body, want in ((b"\xb2", b"", 411), (b"\xb9\xb2", b"", 411), ("1_0", b"", 411),
                                           ("+2", b"", 411), ("-1", b"", 411), ("0x2", b"", 411), (None, b"", 411),
                                           ("9" * 40, b"", 413), (str(len(deep)), deep, 400)):
                    res = post(length, body)
                    self.assertEqual(res.status, want, length)
                    self.assertEqual(res.getheader("Content-Security-Policy"), L.content_policy(U.PAGE), length)
                    self.assertEqual(res.getheader("Referrer-Policy"), "no-referrer")
                    self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff")
                self.assertEqual(post("2", b"{}").status, 200)              # a well-formed request still works
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(called, [{}])                                      # the well-formed one alone
        self.assertNotIn("Traceback", err.getvalue())

    def test_the_watch_page_still_refuses_post(self):
        server = L.serve_page(L.VIEW_V4, lambda: {})
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            conn.putrequest("POST", f"/state?t={server.token}", skip_host=True)
            conn.putheader("Host", f"127.0.0.1:{server.server_address[1]}")
            conn.putheader(L.TOKEN_HEADER, server.token)
            conn.putheader("Content-Length", "0")
            conn.endheaders()
            res = conn.getresponse()
            self.assertEqual((res.status, res.getheader("Allow")), (405, "GET"))
            conn.close()
        finally:
            server.shutdown()
            server.server_close()


# ---- roster snapshots ------------------------------------------------------------------------------------------------------
class TestSnapshots(unittest.TestCase):
    """Roster snapshots (findings 0 and 2): named by the whole sha256 of their bytes, created once as read-only regular
    files, never overwritten, and never written or read through a symbolic link, in results/ or as results/."""
    TEXT = '{\n  "organization": {\n    "controls": [\n      "Varrow Analytics"\n    ]\n  }\n}\n'

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.here = self.scratch / "probatio"
        self.here.mkdir()
        here = mock.patch.object(P, "HERE", self.here)
        here.start()
        self.addCleanup(here.stop)
        self.path = self.here / U.entities_path(self.TEXT)

    def test_a_snapshot_is_content_bound_read_only_and_reused(self):
        data = self.TEXT.encode("utf-8")
        self.assertEqual(U.write_snapshot(self.TEXT), data)
        self.assertEqual(self.path.name, f"ui-entities-{hashlib.sha256(data).hexdigest()}.json")
        made = self.path.lstat()
        self.assertTrue(stat.S_ISREG(made.st_mode))
        self.assertEqual(stat.S_IMODE(made.st_mode), U.SNAPSHOT_MODE)
        self.assertEqual(self.path.read_bytes(), data)
        self.assertEqual(U.write_snapshot(self.TEXT), data)               # the same content: the same file, reused
        self.assertEqual(self.path.lstat().st_ino, made.st_ino)
        self.assertEqual([f.name for f in self.path.parent.iterdir()], [self.path.name])    # no temporary left
        self.assertEqual(U.snapshot_bytes(self.TEXT), data)

    def test_other_content_under_the_name_is_refused_never_overwritten(self):
        self.path.parent.mkdir()
        self.path.write_text("something else\n")
        with self.assertRaises(U.SnapshotRefused) as caught:
            U.write_snapshot(self.TEXT)
        self.assertIn("exists with other content", str(caught.exception))
        self.assertEqual(self.path.read_text(), "something else\n")

    def test_a_symbolic_link_under_results_is_refused_and_its_target_untouched(self):
        victim = self.scratch / "victim.txt"
        victim.write_text("original")
        self.path.parent.mkdir()
        self.path.symlink_to(victim)
        for attempt in (U.write_snapshot, U.snapshot_bytes):
            with self.assertRaises(U.SnapshotRefused) as caught:
                attempt(self.TEXT)
            self.assertIn("is a symbolic link", str(caught.exception))
        self.assertEqual(victim.read_text(), "original")
        self.assertTrue(self.path.is_symlink())

    def test_results_as_a_link_or_a_file_is_refused(self):
        elsewhere, results = self.scratch / "elsewhere", self.here / "results"
        elsewhere.mkdir()
        results.symlink_to(elsewhere)
        with self.assertRaises(U.SnapshotRefused) as caught:
            U.write_snapshot(self.TEXT)
        self.assertIn("results/ is not a plain folder (a symbolic link)", str(caught.exception))
        self.assertEqual(list(elsewhere.iterdir()), [])
        results.unlink()
        results.write_text("a file")
        with self.assertRaises(U.SnapshotRefused):
            U.write_snapshot(self.TEXT)
        self.assertEqual(results.read_text(), "a file")

    def test_an_os_error_is_a_refusal_and_leaves_no_temporary(self):
        def full(fd, data):
            raise OSError(28, os.strerror(28))                             # ENOSPC, as a full disk raises it
        with mock.patch.object(U.os, "write", full), self.assertRaises(U.SnapshotRefused) as caught:
            U.write_snapshot(self.TEXT)
        self.assertIn("could not be written", str(caught.exception))
        self.assertEqual(list(self.path.parent.iterdir()), [])            # no snapshot, no temporary

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no named pipes on this system")
    def test_a_pipe_under_the_name_is_refused_without_waiting(self):
        self.path.parent.mkdir()
        os.mkfifo(self.path)
        with self.assertRaises(U.SnapshotRefused) as caught:
            U.write_snapshot(self.TEXT)
        self.assertIn("not a regular file", str(caught.exception))


# ---- Check and Run, with stubs ---------------------------------------------------------------------------------------------
class TestCheckAndRun(Stubbed):
    """probatio's folder is the scratch folder here (P.HERE), so an entities file, a dry run's working directory and
    a run's results stay in it: no test writes to, or removes from, the repository's results/."""

    def setUp(self):
        super().setUp()
        here = mock.patch.object(P, "HERE", self.tmp)
        here.start()
        self.addCleanup(here.stop)
        self.s = Served(U.Session(program=self.program, env=self.env))

    def tearDown(self):
        self.s.close()
        super().tearDown()

    def test_check_is_the_real_dry_run(self):
        reply = self.s.post("/check", {"form": MOZILLA})
        argv = U.build_command(MOZILLA)[0]
        direct = subprocess.run([sys.executable, "-B", str(REPO / "probatio.py"), *argv, "--dry-run"], cwd=REPO,
                                env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        self.assertTrue(reply["ok"], reply["error"])
        self.assertEqual(reply["plan"], direct.stdout)
        self.assertEqual(reply["summary"]["calls"], int(re.search(r"(\d+) calls\s+about", re.findall(
            r"\n\s+all .*", direct.stdout)[0]).group(1)))
        self.assertIn("target Mozilla (organization)", reply["plan"])           # v4's own preflight, as a terminal's
        self.assertIn("pairwise option:  A) Mozilla", reply["plan"])          # the names as the items write them
        self.assertEqual(reply["command"], U.command_text(argv))

    def test_a_refused_check_shows_probatios_message_verbatim(self):
        """A refusal only the dry run makes, whose advice the page can follow (its lengths and classes are on the
        page), is probatio's message, verbatim."""
        named_at_quick = {"agent": "claude", "types": ["individual", "organization"],
                          "targets": {"organization": "Mozilla"}, "tier": "quick", "classes": ["II"]}
        for form in (named_at_quick, {"agent": "claude", "types": ["nation"]}):
            reply = self.s.post("/check", {"form": form})
            argv = U.build_command(form)[0]
            direct = subprocess.run([sys.executable, "-B", str(REPO / "probatio.py"), *argv, "--dry-run"], cwd=REPO,
                                    env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertFalse(reply["ok"])
            self.assertEqual(reply["error"], direct.stderr.strip())
        self.assertIn("--target is needed for an organization or a nation", reply["error"])

    def test_a_key_collision_is_worded_for_the_page_with_probatios_own_in_the_preflight(self):
        form = dict(JANE, targets={"individual": "Jane Doe\nJane Do\u00e9"})
        reply = self.s.post("/check", {"form": form})
        direct = subprocess.run([sys.executable, "-B", str(REPO / "probatio.py"), *reply["argv"], "--dry-run"],
                                cwd=REPO, env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"], U.page_words(direct.stderr.strip()))
        self.assertNotIn("--entities", reply["error"])
        self.assertEqual(reply["plan"], direct.stdout + direct.stderr.strip() + "\n")       # probatio's own, verbatim
        page = self.s.request("GET", "/")[1]
        self.assertIn("if (plan.plan) box.appendChild(preflight(plan.plan));", page)      # shown under a refusal

    def earlier_run(self, argv):
        """A folder an earlier run of argv holds: run.json written by probatio's own check_resume, with the record the
        run writes, built in the run's environment (the stub CLIs), as the run's first step does."""
        code = f"import json, sys; sys.path.insert(0, {str(REPO)!r}); import ui; print(json.dumps(ui.would_be_metas(" \
               f"{U.absolute(argv)!r})))"
        metas = json.loads(subprocess.run([sys.executable, "-I", "-B", "-c", code], cwd=self.tmp, env=self.env,
                                          capture_output=True, text=True, check=True, timeout=120).stdout)
        folders = [folder for folder, _ in U.prepared(argv)]
        for folder, meta in zip(folders, metas):
            folder.mkdir(parents=True)
            P.check_resume(folder, meta)
        return folders

    def test_check_names_a_folder_an_earlier_run_holds(self):
        """A folder that already holds a run with other settings is named at Check, with what to do from the page,
        and nothing is approved: Run would stop on it at once. probatio's own words follow the preflight; the folder
        is only read. The same settings resume; a moved folder starts fresh."""
        terminal = ["--agent", "claude", "--target-type", "organization", "--target", "Mozilla", "--tier", "quick"]
        [folder] = self.earlier_run(terminal)
        held = sorted((p.name, p.read_bytes()) for p in folder.iterdir())
        self.assertTrue(self.s.post("/check", {"form": MOZILLA})["ok"])             # the same run: it resumes
        for form, what in ((dict(MOZILLA, effort="high"), "effort"), (dict(MOZILLA, memory="off"), None)):
            reply = self.s.post("/check", {"form": form})
            if what is None:                                 # another folder (memory names it): nothing to compare
                self.assertTrue(reply["ok"], reply["error"])
                continue
            self.assertFalse(reply["ok"])
            self.assertEqual(reply["error"], U.BLOCKED.format(folder=U.shown(folder),
                                                              changed=f" with other settings ({what} changed)"))
            self.assertTrue(reply["plan"].endswith(f"{folder} holds a run with other settings ({what} changed). Pass "
                                                   "another --out to start a new run, or undo the change to resume "
                                                   "this one.\n"), reply["plan"][-300:])
            self.assertIn("check this command first", self.s.post("/run", {"form": form, "confirmed": True})["error"])
        two = dict(MOZILLA, targets={"organization": "Mozilla\nWikimedia Foundation"}, effort="high")
        reply = self.s.post("/check", {"form": two})                    # the options path: two runs, one folder held
        self.assertFalse(reply["ok"])
        self.assertTrue(reply["error"].startswith(f"{U.shown(folder)} holds an earlier run with other settings ("))
        self.assertIn("effort", reply["error"])
        self.assertNotIn("wikimedia", reply["error"])
        self.assertEqual(sorted((p.name, p.read_bytes()) for p in folder.iterdir()), held)        # only read
        folder.rename(folder.with_name(folder.name + "-moved"))
        self.assertTrue(self.s.post("/check", {"form": dict(MOZILLA, effort="high")})["ok"])

    def test_a_run_record_check_cannot_read_blocks_the_run(self):
        """A run.json the run could not read blocks the run too, named, and is left as it was."""
        [folder] = self.earlier_run(["--agent", "claude", "--target-type", "organization", "--target", "Mozilla",
                                     "--tier", "quick"])
        (folder / "run.json").write_text("{", encoding="utf-8")
        reply = self.s.post("/check", {"form": MOZILLA})
        self.assertEqual((reply["ok"], reply["error"]), (False, U.BLOCKED.format(folder=U.shown(folder), changed="")))
        self.assertTrue(reply["plan"].endswith(f"{folder / 'run.json'} is not a run record probatio can read "
                                               "(JSONDecodeError)\n"))
        self.assertEqual((folder / "run.json").read_text(encoding="utf-8"), "{")

    def test_check_writes_the_entities_file_it_names(self):
        form = dict(MOZILLA, rosters={"organization": {"controls": "Thirlbeck Systems\nVarrow Analytics"}})
        before = sorted((REPO / "results").iterdir())
        reply = self.s.post("/check", {"form": form})
        file = self.tmp / reply["entities"]["path"]
        self.assertTrue(reply["ok"], reply["error"])
        self.assertEqual(sorted((REPO / "results").iterdir()), before)
        self.assertEqual(file.read_text(encoding="utf-8"), reply["entities"]["text"])
        self.assertEqual(stat.S_IMODE(file.lstat().st_mode), U.SNAPSHOT_MODE)       # a read-only regular file
        self.assertTrue(stat.S_ISREG(file.lstat().st_mode))
        self.assertIn('custom control "Varrow Analytics": not calibrated', reply["plan"])

    def test_run_needs_a_confirmed_and_checked_command(self):
        self.assertIn("confirm", self.s.post("/run", {"form": MOZILLA})["error"])
        self.assertIn("check this command first", self.s.post("/run", {"form": MOZILLA, "confirmed": True})["error"])
        self.assertTrue(self.s.post("/check", {"form": MOZILLA})["ok"])
        changed = dict(MOZILLA, tier="standard")
        self.assertIn("check this command first", self.s.post("/run", {"form": changed, "confirmed": True})["error"])
        self.assertIsNone(self.s.state()["run"])

    def test_concurrent_checks_never_cross(self):
        """Codex's interleaving (finding 0): Check B runs inside Check A's real dry run. Each reports its own roster's
        plan, Run A executes exactly the plan Check A approved, and only the last Check decides what may run."""
        def form(controls):
            return {"agent": "claude", "types": list(P.TARGET_TYPES), "tier": "quick",
                    "rosters": {"organization": {"controls": "\n".join(controls)}}}
        fa, fb = form(["Cedar Lab", "Willow Lab"]), form(["Cedar Lab"])
        executed = []

        class Recorder:
            def __init__(self, program, argv, folders, env):
                self.code = 0
                absolute = [str(P.HERE / x) if prev == "--entities" else x for prev, x in zip([None, *argv], argv)]
                executed.append(sum(len(r["calls"]) for r in P.prepare_runs(P.parse_args_v4(absolute), False)[4]))

            def state(self):
                return {}
        session, real, seen = U.Session(program=self.program, env=self.env), U.subprocess.run, {}

        def interleave(*args, **kwargs):
            if "b" not in seen:
                seen["b"] = None                                 # once: Check B's own dry run is not interleaved
                seen["b"] = session.check({"form": fb})
            return real(*args, **kwargs)
        with mock.patch.object(U.subprocess, "run", side_effect=interleave):
            a = session.check({"form": fa})
        self.assertTrue(a["ok"] and seen["b"]["ok"], (a.get("error"), seen["b"].get("error")))
        self.assertNotEqual(a["summary"]["calls"], seen["b"]["summary"]["calls"])
        with mock.patch.object(U, "Runner", Recorder):
            self.assertIn("check this command first", session.run({"form": fb, "confirmed": True})["error"])
            self.assertNotIn("error", session.run({"form": fa, "confirmed": True}))
            self.assertEqual(executed, [a["summary"]["calls"]])                 # approved and executed agree
            self.assertTrue(session.check({"form": fa})["ok"] and session.check({"form": fb})["ok"])
            self.assertIn("check this command first", session.run({"form": fa, "confirmed": True})["error"])

    def test_an_explicit_class_subset_stays_explicit_at_quick(self):
        """I and II chosen at quick are sent as --class I,II, never as "all", so the real dry run offers the shorter
        standard run (probatio's STANDARD_SHORTER line), and the argv shown is the one Check checks and Run runs.
        Classes left at all three send no --class, and their quick run (I and II, against a standard with III) gets
        no such line."""
        form = {"agent": "claude", "types": ["organization"], "targets": {"organization": "Mozilla"}, "tier": "quick",
                "classes": ["I", "II"], "model": "claude-sonnet-5-5"}
        shown = self.s.post("/command", {"form": form})
        self.assertEqual(shown["argv"][shown["argv"].index("--class") + 1], "I,II")
        checked, started, real = [], [], U.subprocess.run

        def spy(cmd, *args, **kwargs):
            checked.append(list(cmd))
            return real(cmd, *args, **kwargs)

        class Recorder:
            def __init__(self, program, argv, folders, env):
                self.code = 0
                started.append(list(argv))

            def state(self):
                return {}

            def stop(self):                                     # it never ran, so the page's close has nothing to stop
                return False
        line = P.STANDARD_SHORTER.split("{}")[0]
        with mock.patch.object(U.subprocess, "run", side_effect=spy), mock.patch.object(U, "Runner", Recorder):
            reply = self.s.post("/check", {"form": form})
            self.assertTrue(reply["ok"], reply["error"])
            self.assertIn(line, reply["plan"])
            self.assertNotIn("error", self.s.post("/run", {"form": form, "confirmed": True}))
            untouched = self.s.post("/check", {"form": dict(form, classes=["I", "II", "III"])})
        self.assertEqual(reply["argv"], shown["argv"])
        self.assertEqual(checked[0], [*self.program, *shown["argv"], "--dry-run"])
        self.assertEqual(started, [shown["argv"]])
        self.assertTrue(untouched["ok"], untouched["error"])
        self.assertNotIn("--class", untouched["argv"])
        self.assertNotIn(line, untouched["plan"])

    def test_the_individual_row_is_the_one_place_for_a_person(self):
        """The page has no your-name field: the individual row takes a person, you or anyone, in every run, and says
        that your own name scores you. In a comparison the name typed there is a named target in the real dry run."""
        page = self.s.request("GET", "/")[1]
        self.assertIn('"your own name, or anyone\'s"', page)
        self.assertIn("type your own name to score yourself", page)
        for gone in ('"your name"', 'field(F, "name"', "--name"):
            self.assertNotIn(gone, page)
        form = {"agent": "claude", "types": ["individual", "organization"], "tier": "quick",
                "targets": {"individual": "Jane Doe"}}
        named = self.s.post("/check", {"form": form})
        self.assertTrue(named["ok"], named["error"])
        self.assertIn("individual=Jane Doe", named["argv"])
        self.assertIn("named targets: individual Jane Doe", named["plan"])

    def test_an_empty_target_row_gets_probatios_own_message(self):
        """A single-type run with its row empty passes the parser and stops at Check with probatio's own message,
        word for word, the individual's the same as every other type's: the page adds no --name and no words of its
        own."""
        said = {}
        for t in P.TARGET_TYPES:
            form = {"agent": "claude", "types": [t], "tier": "quick", "name": "Jane Doe"}
            self.assertIsNone(self.s.post("/command", {"form": form})["error"])
            reply = self.s.post("/check", {"form": form})
            self.assertFalse(reply["ok"], t)
            self.assertNotIn("--name", reply["argv"])
            said[t] = reply["error"]
        direct = subprocess.run([sys.executable, "-B", str(REPO / "probatio.py"), "--agent", "claude", "--tier",
                                 "quick", "--dry-run"], cwd=REPO, env=self.env, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=120)
        self.assertEqual(said, {t: direct.stderr.strip() for t in P.TARGET_TYPES})
        self.assertIn("--target is needed", said["individual"])

    def planned(self, argv, out, metas=None):
        """argv's real --dry-run in this process (main_v4, with the stub CLIs first on PATH), writing rendered.jsonl to
        out: the plan fingerprint of each run, as its preflight is handed the run, and rendered.jsonl's bytes; with
        metas, a list, also each run's meta, the record check_resume compares."""
        seen, single, several = [], P.preflight_v4, P.preflight_runs
        metas = [] if metas is None else metas

        def one(a, opts, bank, roster, calls):
            seen.append(P.plan_fingerprint(calls))
            metas.append(single(a, opts, bank, roster, calls))
            return metas[-1]

        def each(a, opts, bank, ordo_bank, selection, spec, runs):
            seen.extend(P.plan_fingerprint(run["calls"]) for run in runs)
            metas.extend(several(a, opts, bank, ordo_bank, selection, spec, runs))
            return metas[-len(runs):]
        with mock.patch.object(P, "preflight_v4", one), mock.patch.object(P, "preflight_runs", each), \
                mock.patch.dict(os.environ, self.env, clear=True), mock.patch("sys.stdout", io.StringIO()):
            P.main_v4([*argv, "--dry-run", "--out", str(out)])
        return seen, (out / "rendered.jsonl").read_bytes()

    def test_a_typed_person_plans_what_name_plans(self):
        """--name's one job, an individual run's target when none is given, is the individual row's now: the page's
        --target NAME and --name NAME (both v4's own path) plan one run, with the same plan fingerprint and
        byte-identical rendered.jsonl, and Check's approval binds that fingerprint. Both tiers, all three memory
        settings and both CLIs were compared the same way when the your-name field went."""
        for memory in ("both", "off"):
            form = dict(JANE, tier="quick", memory=memory)
            argv = [x for x in self.s.post("/command", {"form": form})["argv"] if x != U.WATCH]
            at = argv.index("--target")
            self.assertEqual(argv[at + 1], "Jane Doe")
            named = argv[:at] + ["--name", "Jane Doe"] + argv[at + 2:]
            page, typed = self.planned(argv, self.tmp / f"page-{memory}")
            plain, given = self.planned(named, self.tmp / f"name-{memory}")
            self.assertEqual(len(page), 1)
            self.assertEqual(page, plain, memory)
            self.assertEqual(typed, given, memory)
            checked = self.s.post("/check", {"form": form})
            self.assertTrue(checked["ok"], checked["error"])
            self.assertEqual(self.s.session.checked.fingerprints, tuple(plain), memory)

    def test_page_and_terminal_folders_resume_each_other(self):
        """A folder a terminal started with README's command resumes from the page's command, and the reverse, for each
        type (probatio's own check_resume on the real dry runs' metas and folders); so does a folder the earlier page
        started with --compare TYPE --target TYPE=NAME. All three plan the same calls, item for item. (Codex was
        checked the same way by hand: the guard's stub refuses Codex's feature probe.)"""
        readme = {"individual": ["--name", "Jane Doe"], "organization": ["--target-type", "organization", "--target",
                  "Mozilla"], "nation": ["--target-type", "nation", "--target", "France"]}
        for t, words in readme.items():
            name = words[-1]
            page = self.s.post("/command", {"form": {"agent": "claude", "types": [t], "targets": {t: name},
                                                     "tier": "quick", "watch": "off"}})["argv"]
            earlier = ["--agent", "claude", "--compare", t, "--target", f"{t}={name}", "--tier", "quick"]
            forms = {"readme": ["--agent", "claude", *words, "--tier", "quick"], "page": page, "earlier page": earlier}
            got = {}
            for key, argv in forms.items():
                metas = []
                fingerprints, items = self.planned(argv, self.tmp / t / key, metas)
                got[key] = (fingerprints, items, metas[0], self.tmp / t / key)
            self.assertEqual(len({(tuple(f), i) for f, i, _, _ in got.values()}), 1, t)
            for first, then in (("readme", "page"), ("page", "readme"), ("earlier page", "page")):
                folder = self.tmp / t / f"{first} then {then}"
                shutil.copytree(got[first][3], folder)
                P.check_resume(folder, got[first][2])                       # run.json, as the first run writes it
                P.check_resume(folder, got[then][2])                        # refuses with SystemExit if they differ
                self.assertTrue((folder / "run.json").exists(), (t, first, then))

    def test_the_model_is_a_choice_per_cli(self):
        """The model is the CLI's default, one of its models in MODELS (as of 2026-11-09), or any other name: the page
        offers each CLI's own list, resets to the default on another CLI, and says the time estimate is Sonnet's (Opus
        estimated) for Claude Code and assumed for Codex. A listed or typed name is --model NAME, and each of Claude
        Code's passes the real dry run (Codex's were dry-run by hand, as the guard's stub refuses Codex's feature
        probe)."""
        base = self.s.state()["defaults"]
        self.assertEqual(base["models"], {"claude": ["fable", "opus", "sonnet", "haiku"],
                                          "codex": ["gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-sol", "gpt-6-astra",
                                                    "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]})
        self.assertEqual(list(base["models"]), list(parser_actions()["--agent"].choices))
        self.assertRegex(Path(U.__file__).read_text(encoding="utf-8"), r"as of 2026-11-09[^\n]*\n(#[^\n]*\n)*MODELS = ")
        page = self.s.request("GET", "/")[1]
        for words in ('["", DEFAULT_MODEL[F.agent] || "default"]', '[OTHER, "other…"]',
                      'F.model = ""; otherModel = false; how();', "the time estimate is measured on Sonnet (Opus "
                      "estimated); other models show Sonnet's time", "the time estimate for Codex is assumed, not "
                      "measured"):
            self.assertIn(words, page)
        self.assertNotIn("--model", self.s.post("/command", {"form": {"agent": "codex", "model": ""}})["argv"])
        typed = self.s.post("/command", {"form": {"agent": "codex", "model": " my-model "}})["argv"]
        self.assertEqual(typed[typed.index("--model") + 1], "my-model")
        for model in base["models"]["claude"]:
            reply = self.s.post("/check", {"form": dict(MOZILLA, model=model)})
            self.assertTrue(reply["ok"], (model, reply["error"]))
            self.assertEqual(reply["argv"][reply["argv"].index("--model") + 1], model)
            self.assertIn(f"model {model}, effort medium", reply["plan"])

    def test_watch_off_leaves_it_out_of_the_one_command(self):
        """Watch off: no --watch in the command shown, checked or run (finding 3 holds), and the run's state has its
        output, folder and scorecard, with no live link and no error. Watch on (the default) is unchanged."""
        self.env["STUB_FINISH"] = "1"
        form = dict(MOZILLA, watch="off")
        shown = self.s.post("/command", {"form": form})
        self.assertNotIn(U.WATCH, shown["argv"])
        for on in (MOZILLA, dict(MOZILLA, watch="on")):                         # on, the default, is unchanged
            self.assertEqual(self.s.post("/command", {"form": on})["argv"], shown["argv"] + [U.WATCH])
        checked, real = [], U.subprocess.run

        def spy(cmd, *args, **kwargs):
            checked.append(list(cmd))
            return real(cmd, *args, **kwargs)
        with mock.patch.object(U.subprocess, "run", side_effect=spy):
            reply = self.s.post("/check", {"form": form})
        self.assertTrue(reply["ok"], reply["error"])
        self.assertEqual(checked, [[*self.program, *shown["argv"], "--dry-run"]])
        self.assertNotIn("error", self.s.post("/run", {"form": form, "confirmed": True}))
        self.assertTrue(wait_for(lambda: self.s.state()["run"]["code"] is not None))
        run = self.s.state()["run"]
        self.assertEqual((run["status"], run["code"], run["watch"]), ("finished", 0, None))
        self.assertEqual(run["command"], shown["command"])
        self.assertIn("args: " + " ".join(shown["argv"]), run["lines"])
        self.assertEqual(run["folders"], [U.shown(f) for f, _ in U.prepared(shown["argv"])])
        self.assertEqual(run["results"], [["Scorecard", "results/stub-run/scorecard.json"],
                                          ["Report", "results/stub-run/report.md"]])
        page = self.s.request("GET", "/")[1]
        self.assertIn("if (run.watch && going())", page)                 # the live link only when there is one

    def test_the_hints_under_more(self):
        """Jobs and auto memory each get one plain hint: calls at once, faster but heavier on the rate limit, with the
        estimate scaling from the jobs its rates were measured at; auto memory is Claude Code's own saved notes, left
        as configured to measure the agent as it is, off being the calibration's setting."""
        self.assertEqual(self.s.state()["defaults"]["measured_jobs"], P.pricing()["measured"]["jobs"])
        page = self.s.request("GET", "/")[1]
        for words in ("Calls at once; ", "more is faster but heavier on your rate limit; the ",
                      "time estimate scales with it, from rates measured at ",
                      "Claude Code's own saved notes (Claude Code only): "
                      "leave as configured to measure your agent as it is; ", "off, in both conditions, is the "
                      "calibration's setting"):
            self.assertIn(words, page)

    def test_the_page_keeps_its_ids_and_the_watch_page_its_palette(self):
        """Every id the tests and scripts read is on the page; the --ui page's tokens are only its own (the watch page
        reads none of them); and an untouched page with no agent chosen asks for one calmly, keeping probatio's own
        message, in red, for after a change."""
        for i in ("pill", "form", "measure", "against", "how", "cmd", "where", "copy", "labels", "error", "files",
                  "check", "run", "why", "confirm", "runsec", "runbox", "plan", "foot", "custom", "modelnote"):
            self.assertTrue(f'id="{i}"' in U.PAGE or f'.id = "{i}"' in U.PAGE, i)
        for token in ("--field", "--track", "--accent"):
            self.assertIn(token + ":", L.PAGE_BASE)
            self.assertNotIn(f"var({token}", L.VIEW_V4)
        for words in ("calm = !touched && F && !F.agent", "Choose an agent to begin: ", "touched = true;",
                      'el("b", null, "probatio: "), document.createTextNode(said.error)'):
            self.assertIn(words, U.PAGE)

    def test_run_refuses_what_check_did_not_approve(self):
        form = dict(MOZILLA, rosters={"organization": {"controls": "Thirlbeck Systems\nVarrow Analytics"}})
        file = self.tmp / U.entities_path(U.build_command(form)[1])
        session, started = U.Session(program=self.program, env=self.env), []

        def recorder(*args):
            started.append(args)
            return SimpleNamespace(code=0, state=dict)
        with mock.patch.object(U, "Runner", recorder):
            self.assertTrue(session.check({"form": form})["ok"])
            file.chmod(0o644)
            file.write_text(file.read_text() + " ")
            self.assertIn("not the roster Check approved", session.run({"form": form, "confirmed": True})["error"])
            file.unlink()
            self.assertIn("is missing", session.run({"form": form, "confirmed": True})["error"])
            self.assertTrue(session.check({"form": form})["ok"])                   # made again, approved again
            with mock.patch.object(U, "prepared", return_value=[(self.tmp / "run", "another plan")]):
                self.assertIn("no longer the one Check approved",
                              session.run({"form": form, "confirmed": True})["error"])
            self.assertFalse(session.check({"form": dict(form, targets={"organization": "Moz{illa"})})["ok"])
            self.assertIn("check this command first", session.run({"form": form, "confirmed": True})["error"])
            self.assertTrue(session.check({"form": form})["ok"])
            self.assertNotIn("error", session.run({"form": form, "confirmed": True}))
        self.assertEqual(len(started), 1)

    def start(self, form=MOZILLA):
        self.assertTrue(self.s.post("/check", {"form": form})["ok"])
        reply = self.s.post("/run", {"form": form, "confirmed": True})
        self.assertNotIn("error", reply)
        self.assertTrue(wait_for(lambda: (self.tmp / "pid").exists() and self.s.state()["run"]["watch"]))
        return reply, int((self.tmp / "pid").read_text())

    def test_run_starts_the_checked_command_with_watch_and_stop_reaches_only_its_child(self):
        sibling = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            reply, pid = self.start()
            argv = U.build_command(MOZILLA)[0]
            run = self.s.state()["run"]
            self.assertEqual(run["status"], "running")
            self.assertEqual(run["command"], U.command_text(argv))
            self.assertTrue(wait_for(lambda: any(x.startswith("args: ") for x in self.s.state()["run"]["lines"])))
            self.assertIn("args: " + " ".join(argv), self.s.state()["run"]["lines"])
            self.assertRegex(run["watch"], r"^http://127\.0\.0\.1:45871/\?t=[A-Za-z0-9_-]{32}$")
            self.assertEqual(run["folders"], [U.shown(f) for f, _ in U.prepared(argv)])
            self.assertEqual(os.getsid(pid), pid)                          # its own session: Ctrl-C is the page's
            self.assertIn("a run is already going", self.s.post("/run", {"form": MOZILLA, "confirmed": True})["error"])
            self.assertIn("run", self.s.post("/stop", {}))
            self.assertTrue(wait_for(lambda: self.s.state()["run"]["code"] is not None))
            run = self.s.state()["run"]
            self.assertEqual((run["status"], run["code"]), ("stopped", 1))
            self.assertIn("interrupted. Rerun the same command to resume.", run["lines"])
            self.assertIsNone(sibling.poll())                              # SIGINT went to the run alone
            self.assertIn("no run is going", self.s.post("/stop", {})["error"])
        finally:
            sibling.kill()
            sibling.wait()

    def test_a_scored_run_shows_its_scorecard_and_stop_closes_its_page(self):
        self.env["STUB_FINISH"] = "1"
        _, pid = self.start()
        self.assertTrue(wait_for(lambda: self.s.state()["run"]["status"] == "scored"))
        run = self.s.state()["run"]
        self.assertEqual(run["results"], [["Scorecard", "results/stub-run/scorecard.json"],
                                          ["Report", "results/stub-run/report.md"]])
        self.s.post("/stop", {})
        self.assertTrue(wait_for(lambda: self.s.state()["run"]["code"] is not None))
        self.assertEqual(self.s.state()["run"]["status"], "finished")

    def test_the_live_link_is_probatios_own_line_with_watch_only(self):
        """Over the page: a CLI's error text printed as a whole line that looks like probatio's never becomes the
        link. With watch off there is no link at all; with watch on, the link is probatio's own, the first."""
        planted = "  watch it live: http://127.0.0.1:2/?t=fromcli"
        self.env.update(STUB_SAY=planted, STUB_FINISH="1")
        form = dict(MOZILLA, watch="off")
        self.assertTrue(self.s.post("/check", {"form": form})["ok"])
        self.assertNotIn("error", self.s.post("/run", {"form": form, "confirmed": True}))
        self.assertTrue(wait_for(lambda: self.s.state()["run"]["code"] is not None))
        run = self.s.state()["run"]
        self.assertEqual((run["status"], run["watch"]), ("finished", None))
        self.assertIn(planted, run["lines"])
        self.start()
        self.assertTrue(wait_for(lambda: planted in self.s.state()["run"]["lines"]))
        self.assertRegex(self.s.state()["run"]["watch"], r"^http://127\.0\.0\.1:45871/\?t=[A-Za-z0-9_-]{32}$")
        self.s.post("/stop", {})
        self.assertTrue(wait_for(lambda: self.s.state()["run"]["code"] is not None))

    def test_a_form_the_page_refuses_never_reaches_a_dry_run(self):
        """A tier the page does not offer, a NUL, a name the guard refuses (a bidi control) and a value the reply's
        redactor would change (a key shape, the page's own token) are refused by /command, /check and /run with a
        bounded message: Check writes nothing, starts no dry run and leaves no approval, and Run starts nothing."""
        key = "sk-ant-" + secrets.token_hex(12)                                  # a fake key, built at run time
        cases = [(dict(MOZILLA, tier="smoke"), U.TIER_REFUSED), (dict(JANE, tier="smoke"), U.TIER_REFUSED),
                 (dict(MOZILLA, targets={"organization": "Moz\x00illa"}), U.NUL_REFUSED),
                 (dict(MOZILLA, model="x\x00y"), U.NUL_REFUSED)]
        with self.assertRaises(P.NameRefused) as guard:
            P.check_name("Moz\u202eilla", U.ROW_LABEL.format("organization"))
        cases += [(dict(MOZILLA, targets={"organization": "Moz\u202eilla"}), str(guard.exception)),
                  (dict(MOZILLA, targets={"organization": key}), U.UNSHOWN.format("the value of --target")),
                  (dict(MOZILLA, model="x" + self.s.token), U.UNSHOWN.format("the value of --model"))]
        spied, real = [], U.subprocess.run

        def spy(cmd, *args, **kwargs):
            spied.append(list(cmd))
            return real(cmd, *args, **kwargs)
        for form, error in cases:
            self.assertTrue(self.s.post("/check", {"form": MOZILLA})["ok"])         # an approval the refusal revokes
            with mock.patch.object(U.subprocess, "run", side_effect=spy):
                self.assertEqual(self.s.post("/command", {"form": form})["error"], error, form)
                reply = self.s.post("/check", {"form": form})
            self.assertEqual((reply["ok"], reply["error"], reply["plan"]), (False, error, ""), form)
            self.assertIn(self.s.post("/run", {"form": form, "confirmed": True})["error"],
                          (error, "check this command first: Run starts only the command the last Check passed"))
            again = self.s.post("/run", {"form": MOZILLA, "confirmed": True})
            self.assertIn("check this command first", again["error"])
        self.assertEqual(spied, [])
        self.assertFalse(list((self.tmp / "results").glob("ui-entities-*")))
        self.assertIsNone(self.s.state()["run"])

    def test_a_dry_run_or_a_run_that_cannot_start_is_a_refusal(self):
        """subprocess's ValueError (a NUL in the command line) and OSError (no such program) from Check's dry run or
        Run's process are replies with ok false and a bounded message, never a dropped connection."""
        session, program = self.s.session, self.s.session.program
        missing = [str(self.tmp / "no-such-probatio")]
        try:
            for broken, why in ((missing, os.strerror(errno.ENOENT)),
                                ([sys.executable + "\x00"], "its command line holds what no program can take")):
                session.program = broken
                reply = self.s.post("/check", {"form": MOZILLA})
                self.assertEqual((reply["ok"], reply["error"]), (False, f"the dry run could not start ({why})"))
                again = self.s.post("/run", {"form": MOZILLA, "confirmed": True})
                self.assertIn("check this command first", again["error"])
            for broken, why in ((missing, os.strerror(errno.ENOENT)),
                                ([sys.executable + "\x00"], "its command line holds what no program can take")):
                session.program = program
                self.assertTrue(self.s.post("/check", {"form": MOZILLA})["ok"])
                session.program = broken
                self.assertEqual(self.s.post("/run", {"form": MOZILLA, "confirmed": True}),
                                 {"ok": False, "error": f"the run could not start ({why})"})
                self.assertIsNone(self.s.state()["run"])
        finally:
            session.program = program
        self.assertEqual(self.s.state()["run"], None)

    def test_closing_the_page_stops_its_run(self):
        _, pid = self.start()
        out = io.StringIO()
        self.s.session.close(out)
        self.assertIsNotNone(self.s.session.runner.code)
        self.assertIn("stopping the page's run", out.getvalue())


class TestRunFolders(unittest.TestCase):
    """prepared names the folder probatio's own main makes for the run: v4's path and the options path."""

    def folder_main_uses(self, argv, home):
        """The folder probatio's main hands run_calls first, with every CLI faked and run_calls stopped there."""
        made = []

        class Stop(Exception):
            pass

        def fake(calls, agent, opts, out, jobs, line=None):
            made.append(out)
            raise Stop
        version = mock.Mock(returncode=0, stdout="9.9.9 (fake)\n", stderr="")
        with mock.patch.object(P.subprocess, "run", return_value=version), \
                mock.patch.object(P.shutil, "which", return_value="/bin/claude"), \
                mock.patch.object(P, "run_calls", side_effect=fake), \
                mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": home}), \
                mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(Stop):
                P.main(argv)
        return made

    def test_a_typed_person_gets_the_folder_name_gets(self):
        """The page's --target NAME runs in the folder --name NAME runs in."""
        folders, page = [], [x for x in U.build_command(JANE)[0] if x != U.WATCH]
        for argv in (page, ["--agent", "claude", "--name", "Jane Doe"]):
            with tempfile.TemporaryDirectory() as d, mock.patch.object(P, "HERE", Path(d)):
                [folder] = self.folder_main_uses([*argv, "--workdir", d], d)
                folders.append(folder.relative_to(Path(d).resolve()))
        self.assertEqual(folders[0], folders[1])
        self.assertEqual(folders[0].parent.name, "results")

    def test_the_folders_are_probatios(self):
        for form in (JANE, dict(MOZILLA, targets={"organization": "Mozilla\nWikimedia"}),
                     {"agent": "claude", "types": ["organization", "nation"], "tier": "quick"}):
            with tempfile.TemporaryDirectory() as d, mock.patch.object(P, "HERE", Path(d)):
                # --watch names no folder, and here it would start main's page, so it is left out
                argv = [x for x in U.build_command(form)[0] if x != U.WATCH] + ["--workdir", d]
                want = [Path(f) for f, _ in U.prepared(argv)]
                self.assertEqual(self.folder_main_uses(argv, d), want[:1], form)      # the first run starts first
                self.assertTrue(all(f.is_relative_to(Path(d).resolve() / "results") for f in want), want)


# ---- nothing secret on the page --------------------------------------------------------------------------------------------
class TestPrivacy(Stubbed):
    def test_secrets_never_reach_the_page(self):
        value = "v" + secrets.token_hex(16)
        key = "sk-ant-" + "A" * 24                                         # a fake key, built at run time
        self.env["STUB_SAY"] = f"leak {value} {key}"
        with mock.patch.dict(os.environ, {"PROBATIO_TEST_API_KEY": value}):
            s = Served(U.Session(program=self.program, env=self.env))
            try:
                self.assertTrue(s.post("/check", {"form": MOZILLA})["ok"])
                s.post("/run", {"form": MOZILLA, "confirmed": True})
                self.assertTrue(wait_for(lambda: any("leak" in x for x in s.state()["run"]["lines"])))
                text = json.dumps(s.state()) + s.request("GET", "/")[1] + json.dumps(s.post("/command", {"form": JANE}))
                self.assertIn(f"leak {L.MARK} {L.MARK}", text)
                self.assertNotIn(value, text)
                self.assertNotIn(key, text)
            finally:
                s.close()

    def test_a_secret_in_a_field_is_refused_never_shown_as_something_else(self):
        """A target holding the value of a secret-named variable is refused, not shown as <redacted> while the run
        would carry the value: the page's command is always the one that runs."""
        value = "v" + secrets.token_hex(16)
        with mock.patch.dict(os.environ, {"PROBATIO_TEST_API_KEY": value}):
            s = Served(U.Session(program=self.program, env=self.env))
            try:
                form = dict(MOZILLA, targets={"organization": "Moz " + value})
                unshown = U.UNSHOWN.format("the value of --target")
                self.assertEqual(s.post("/command", {"form": form})["error"], unshown)
                reply = s.post("/check", {"form": form})
                self.assertEqual((reply["ok"], reply["error"], reply["plan"]), (False, unshown, ""))
                self.assertNotIn(value, json.dumps(reply))
                self.assertTrue(s.post("/check", {"form": MOZILLA})["ok"])
            finally:
                s.close()

    def test_the_page_never_opens_local_json(self):
        opened = []
        U.defaults.cache_clear()
        U.bank.cache_clear()
        RECORDING.append(opened)
        try:
            s = Served(U.Session(program=self.program, env=self.env))
            try:
                s.state()
                s.post("/command", {"form": dict(MOZILLA, rosters={"organization": {"controls": "Arvenia Ltd"}})})
                s.post("/check", {"form": MOZILLA})
                s.request("GET", "/")
            finally:
                s.close()
        finally:
            RECORDING.remove(opened)
            U.defaults.cache_clear()
            U.bank.cache_clear()
        self.assertTrue(opened)                                            # the spy saw the bank being read
        self.assertFalse([f for f in opened if Path(f).name == "local.json"], opened)
        local = REPO / "calibration" / "local.json"
        if local.exists():                                                 # a host with one: none of its values
            values = re.findall(r'"([^"]{6,})"', local.read_text(encoding="utf-8"))
            page = json.dumps(U.defaults()) + U.PAGE
            self.assertFalse([v for v in values if v in page])


# ---- the flag --------------------------------------------------------------------------------------------------------------
class TestFlag(unittest.TestCase):
    def test_ui_needs_no_agent_and_dispatches_to_ui(self):
        self.assertTrue(P.wants_v4(["--ui"]))
        self.assertTrue(P.parse_args_v4(["--ui"]).ui)
        with mock.patch.object(U, "main") as main:
            P.main(["--ui"])
        main.assert_called_once_with(["--ui"])

    def test_ui_takes_no_other_option(self):
        with self.assertRaises(SystemExit) as caught:
            P.main(["--ui", "--agent", "claude"])
        self.assertEqual(caught.exception.code, "--ui takes no other option; choose them on the page")


if __name__ == "__main__":
    unittest.main()
