"""Tests for the form's share of versus (ui.py; docs/versus/DESIGN.md 9): the question appears exactly when the rows
hold two or three names of different types; its first answer, the default, writes versus_run's command
(`--versus TYPE=NAME` in the rows' order) and its second writes what the form writes today; what belongs only to the
other runs (classes, rosters, auto memory, the comparison) is never sent; a versus run's own lengths, defaults and
needs; the form's guards on versus rows with the hostile values the form's tests use; Check runs the versus dry run
and shows its preflight; Run starts it and the run ends in a finished versus folder, through the stand-in agent; with
watch on, the form links the versus live page; the command's refusals as the form shows refusals, in the page's own
words where the command's advice names --out; and the question in a real headless Chrome.

No test calls a model or the network. Every child is started by tests/fixtures/run_versus_here.py (probatio.py's entry
point with the real page, and the scratch folder as probatio's folder), with a `claude` on PATH that runs
tests/fixtures/versus_agent.py and a `codex` that fails (exit 97). A Run ends with the real page's files; a card the
page refuses (planted by the fixture's VERSUS_PLANT=claims) shows on the form, and on the live page, as its one line.

    python3 -m unittest tests.test_versus_ui
"""
import errno
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"
sys.path[:0] = [str(REPO), str(REPO / "tests"), str(FIXTURES)]
import live as L  # noqa: E402
import probatio as P  # noqa: E402
import test_ui as TU  # noqa: E402  (the form's helpers: page_form, Served, Chrome, PAGE_HANDS, wait_for)
import ui as U  # noqa: E402
import versus as VS  # noqa: E402
import versus_page as VP  # noqa: E402
import versus_run as VR  # noqa: E402

RUNNER = FIXTURES / "run_versus_here.py"
AGENT = FIXTURES / "versus_agent.py"
TWO = {"individual": "Jane Doe", "organization": "Acme Corp"}
SECRET_VAR = "PROBATIO_TEST_API_KEY"


def versus_form(**changes):
    """The page's fresh form with Claude Code and two names of different types typed, changes on top."""
    changes.setdefault("types", ["individual", "organization"])
    changes.setdefault("agent", "claude")
    changes["targets"] = dict(TWO, **changes.get("targets", {}))
    return TU.page_form(**changes)


def three(**changes):
    """versus_form with a third name, a nation."""
    changes["targets"] = dict({"nation": "Norland"}, **changes.get("targets", {}))
    return versus_form(types=["individual", "organization", "nation"], **changes)


def unseen(text):
    """The control and format characters in text (none may reach the page raw)."""
    import unicodedata
    return [ch for ch in text if unicodedata.category(ch) in ("Cc", "Cf")]


# ---- the question and the command it writes (pure) -------------------------------------------------------------------
class TestQuestion(unittest.TestCase):
    def test_the_question_shows_exactly_for_two_or_three_names_of_different_types(self):
        """Two or three names of different types ask the question; one name, two in one row, four names, or a name
        in a row whose type is not chosen keep today's form, whose reply is exactly today's (no "question")."""
        asks = [versus_form(), three(), versus_form(types=["organization", "nation"],
                                                    targets={"individual": "", "nation": "Norland"})]
        for form in asks:
            self.assertEqual(U.question_of(form, U.defaults()), {"answer": "names"})
            self.assertEqual(U.preview(form)["question"], {"answer": "names"})
        never = [TU.page_form(agent="claude", targets={"individual": "Jane Doe"}),
                 TU.page_form(agent="claude", targets={"individual": "Jane Doe\nZoe Smith"}),
                 versus_form(targets={"organization": ""}),
                 versus_form(targets={"individual": "Jane Doe\nZoe Smith"}),
                 three(targets={"individual": "Jane Doe\nZoe Smith"}),
                 TU.page_form(agent="claude", targets=dict(TWO))]          # the organization row is not chosen
        for form in never:
            self.assertIsNone(U.question_of(form, U.defaults()))
            self.assertIsNone(U.versus_chosen(form, U.defaults()))
            self.assertNotIn("question", U.preview(form))
            self.assertNotIn(U.VERSUS_FLAG, U.build_command(form)[0])

    def test_each_answer_writes_its_command(self):
        """The first answer (the default, also for a value that is not an answer) writes --versus TYPE=NAME for each
        row in the rows' order, which versus_run reads back as the same names; the second writes today's comparison,
        --compare and --target TYPE=NAME."""
        for form in (versus_form(), versus_form(versus="names"), versus_form(versus="bogus"),
                     versus_form(versus=["names"])):
            self.assertEqual(U.build_command(form), (["--agent", "claude", "--versus", "individual=Jane Doe",
                                                      "--versus", "organization=Acme Corp", "--watch"], None))
        argv = U.build_command(three())[0]
        self.assertEqual(argv[2:8], ["--versus", "individual=Jane Doe", "--versus", "organization=Acme Corp",
                                     "--versus", "nation=Norland"])
        a = VR.parse(argv)
        self.assertEqual(VR.versus_names(a.versus), {"individual": "Jane Doe", "organization": "Acme Corp",
                                                     "nation": "Norland"})
        self.assertIsNone(U.versus_parser_error(argv))
        types = U.build_command(versus_form(versus="types"))
        self.assertEqual(types, (["--agent", "claude", "--compare", "individual:organization", "--target",
                                  "individual=Jane Doe", "--target", "organization=Acme Corp", "--watch"], None))
        self.assertEqual(U.preview(versus_form(versus="types"))["question"], {"answer": "types"})
        self.assertEqual(U.build_command(versus_form(watch="off"))[0][-1], "organization=Acme Corp")
        self.assertEqual(U.VERSUS_FLAG, VR.VERSUS_OPTION)

    def test_what_belongs_to_the_other_runs_is_never_sent(self):
        """With the first answer, classes, an edited roster, auto memory and the compare options are left out
        whatever the form holds (the page hides them); the options a versus run takes are written when they differ
        from its own defaults. The same form answered "types" sends today's options, and no --strangers."""
        busy = dict(classes=["I"], auto_memory="off", tier="quick", memory="both", strangers="on", effort="high",
                    model="opus", jobs="8", config_dir="/tmp", rosters={"individual": {"controls": "Zed Quill"}})
        argv, entities = U.build_command(versus_form(**busy))
        self.assertIsNone(entities)
        self.assertEqual(argv, ["--agent", "claude", "--versus", "individual=Jane Doe", "--versus",
                                "organization=Acme Corp", "--tier", "quick", "--memory", "both", "--strangers", "on",
                                "--effort", "high", "--model", "opus", "--config-dir", "/tmp", "--jobs", "8",
                                "--watch"])
        for word in ("--class", "--auto-memory", "--entities", "--compare", "--target"):
            self.assertNotIn(word, argv)
        self.assertIsNone(VR.check_words(argv))
        reply = U.preview(versus_form(**busy))
        self.assertEqual((reply["entities"], reply["roster"], reply["names_said"]), (None, None, []))
        self.assertEqual(reply["labels"], [VR.BANK_STATUSES["registered"]])    # the shipped bank, as the preflight
        other, entities = U.build_command(versus_form(versus="types", **busy))
        for word in ("--class", "--auto-memory", "--entities", "--compare"):
            self.assertIn(word, other)
        self.assertNotIn("--strangers", other)
        self.assertIsNotNone(entities)

    def test_a_versus_run_has_its_own_lengths_defaults_and_needs(self):
        """The defaults are versus_run's parser's; quick and standard are its lengths, full is one calm missing piece
        (the length chosen before the question stays in the form), smoke the page refuses as today; a versus run
        needs an agent and nothing of a comparison (no class, no roster minimum)."""
        v, a = U.defaults()["versus"], VR.parser().parse_args(["--agent", "claude"])
        self.assertEqual(v["default"], {"tier": a.tier, "memory": a.memory, "strangers": a.strangers,
                                        "effort": a.effort, "jobs": a.jobs})
        self.assertEqual((v["tiers"], v["memory"], v["strangers"]), (list(VS.TIERS), ["on", "off", "both"],
                                                                     ["off", "on"]))
        self.assertEqual(U.required(versus_form(tier="full")), [U.NEEDS_VERSUS_TIER])
        self.assertEqual(U.preview(versus_form(tier="full"))["needs"], U.needs_line([U.NEEDS_VERSUS_TIER]))
        self.assertEqual(U.required(versus_form(agent=None, classes=[])), [U.NEEDS_AGENT])
        self.assertEqual(U.required(versus_form(rosters={"organization": {"controls": ""}})), [])
        with self.assertRaises(U.FormRefused) as caught:
            U.build_command(versus_form(tier="smoke"))
        self.assertEqual(str(caught.exception), U.TIER_REFUSED)
        self.assertIn("--tier", U.build_command(versus_form(tier="quick"))[0])
        self.assertIn("argument --tier: invalid choice: 'full'",
                      U.versus_parser_error(U.build_command(versus_form(tier="full"))[0]))

    def test_versus_rows_meet_the_forms_own_guards_with_its_hostile_values(self):
        """A versus row is a row like any other: a "=" gets the page's NAME_EQUALS for its row, a name the guard
        refuses its own words under the row's label (the escape, CR, backspace, right-to-left, zero-width and long
        names among them), a NUL NUL_REFUSED, a lone surrogate TEXT_REFUSED; a line break makes two names in one row,
        so the question is not asked. No refusal or command repeats a hostile character raw."""
        for t in ("individual", "organization"):
            for line in ("Jane=Doe", f"{t}=Jane Doe", "a=b"):
                form = versus_form(targets={t: line})
                with self.assertRaises(U.FormRefused) as caught:
                    U.build_command(form)
                self.assertEqual(str(caught.exception), U.NAME_EQUALS.format(t))
                self.assertEqual(U.preview(form)["error"], U.NAME_EQUALS.format(t))
                self.assertEqual(U.preview(form)["question"], {"answer": "names"})
            for line in ("Moz{illa", "A) Foo", "x" * 61, "\x1b[2J\x1b[H", "Jane" + "\b" * 8,
                         "Jane‮eoD", "Ja‍ne", "J" * 10000):
                with self.assertRaises(P.NameRefused) as guard:
                    P.check_name(line, U.ROW_LABEL.format(t))
                reply = U.preview(versus_form(targets={t: line}))
                self.assertEqual(reply["error"], str(guard.exception), (t, line))
                self.assertEqual(reply["argv"], [])
                self.assertEqual(unseen(json.dumps(reply, ensure_ascii=False)), [])
        for form in (versus_form(targets={"organization": "Acme\x00Corp"}), versus_form(model="x\x00y")):
            self.assertEqual(U.preview(form)["error"], U.NUL_REFUSED)
        self.assertEqual(U.preview(versus_form(targets={"individual": "Jane\ud800"}))["error"],
                         U.TEXT_REFUSED.format("the individual row"))
        for split in ("Acme\nprobatio: done", "Acme\rCorp"):           # a line break makes two names in one row
            self.assertIsNone(U.question_of(versus_form(targets={"organization": split}), U.defaults()))
        names = U.build_command(versus_form(targets={"individual": "Jane   Doe", "organization": "Zoë Corp"}))
        self.assertEqual(names[0][2:6], ["--versus", "individual=Jane Doe", "--versus", "organization=Zoë Corp"])

    def test_a_secret_in_a_versus_field_is_refused_never_shown_as_something_else(self):
        """The reply's redactor would change a secret-named variable's value or a key's shape, so the command shown
        would not be the one that runs: refused, the variable named by its name, never its value."""
        value = "Velt " + os.urandom(8).hex()
        redactor = L.ViewRedactor({SECRET_VAR: value})
        reply = U.preview(versus_form(targets={"organization": value}), redactor=redactor)
        self.assertEqual(reply["error"], U.UNSHOWN_VARIABLE.format("the value of --versus", SECRET_VAR))
        self.assertNotIn(value, reply["error"])
        key = "sk-" + os.urandom(12).hex()
        self.assertEqual(U.preview(versus_form(model=key), redactor=redactor)["error"],
                         U.UNSHOWN.format("the value of --model"))

    def test_the_summary_and_the_folder_line_are_read_with_the_commands_own_words(self):
        """versus_summary reads the preflight's total (questions and episodes added up as calls, the time, the rates)
        and its hint; versus_folder reads the preflight's last word on the folder, from versus_run's own templates,
        and never approves a folder it did not see."""
        total = VR.TOTAL_LINE.format(arm="all", questions=134, episodes=64, time="about 9 min", label="Sonnet rates")
        hint = "  about 50 minutes; to shorten: --tier quick (-30 min)"
        self.assertEqual(U.versus_summary("\n".join([total, hint])),
                         {"calls": 198, "time": "about 9 min", "hint": hint.strip(), "rates": "Sonnet rates"})
        folder = Path("/x/results/versus-claude-individual-organization-standard-memory-on")
        for line, said in ((VR.FOLDER_FRESH.format(folder=folder), ("fresh", None)),
                           (VR.FOLDER_RESUME.format(folder=folder, n=3, total=9), ("resume", None)),
                           (VR.FOLDER_REFUSED.format(folder=folder, why="it differs"), ("refused", "it differs")),
                           (VR.FOLDER_FRESH.format(folder="/elsewhere"), ("unread", U.FOLDER_UNREAD)),
                           ("", ("unread", U.FOLDER_UNREAD))):
            self.assertEqual(U.versus_folder("  head\n" + line + "\n", folder), said)

    def test_the_commands_advice_to_pass_out_is_in_the_pages_words(self):
        """Every versus_run refusal whose advice is --out (the page writes none) reads in the page's words, from the
        command's own template; any other message is shown as it is."""
        values = {"folder": str(P.HERE / "results" / "versus-x"), "why": "File name too long", "what": "the effort",
                  "theirs": "medium", "mine": "high"}
        for name, words in U.VERSUS_PAGE_WORDS.items():
            line = getattr(VR, name).format(**values)
            self.assertIn("--out", line)
            mine = U.versus_page_words(line)
            self.assertEqual(mine, words.format(**dict(values, folder="results/versus-x")))
            self.assertNotIn("pass --out", mine)
        self.assertIsNone(U.versus_page_words("versus: 'Joint Secretariat' is the joint secretariat"))


# ---- Check and Run through the stand-in agent ---------------------------------------------------------------------------
class Standin(unittest.TestCase):
    """A scratch folder that is probatio's folder (P.HERE), a home of its own, `claude` running the stand-in CLI
    (its starts logged, argv only) and a failing `codex`, and the page on loopback for a Session whose children are
    run_versus_here.py. Nothing is written under the repository's results/."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pv-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for d in ("bin", "home"):
            (self.tmp / d).mkdir()
        (self.tmp / "bin" / "claude").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{AGENT}" "$@"\n')
        (self.tmp / "bin" / "codex").write_text('#!/bin/sh\necho "stand-in codex: no agent calls" >&2\nexit 97\n')
        for f in ("claude", "codex"):
            (self.tmp / "bin" / f).chmod(0o755)
        self.log = self.tmp / "agent.log"
        self.env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        self.env.update(PATH=f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}", HOME=str(self.tmp / "home"),
                        VERSUS_AGENT_LOG=str(self.log), PYTHONDONTWRITEBYTECODE="1")
        here = mock.patch.object(P, "HERE", self.tmp)
        here.start()
        self.addCleanup(here.stop)
        self.results_before = sorted((REPO / "results").glob("*")) if (REPO / "results").exists() else []
        self.cache_before = (REPO / ".cache").exists()
        self.s = TU.Served(U.Session(program=[sys.executable, "-B", str(RUNNER)], env=self.env))
        self.addCleanup(self.s.close)

    def tearDown(self):
        now = sorted((REPO / "results").glob("*")) if (REPO / "results").exists() else []
        self.assertEqual(now, self.results_before, "a test wrote under the repository's results/")
        self.assertEqual((REPO / ".cache").exists(), self.cache_before, "a test downloaded into .cache")

    def as_the_run(self):
        """This process under the run's HOME (the page's head writes the config folder with ~ against HOME)."""
        return mock.patch.dict(os.environ, {"HOME": self.env["HOME"]})

    def planted(self):
        """The page served for a Session whose children plant a card the page refuses (VERSUS_PLANT=claims), in
        place of setUp's (which its own cleanup closes)."""
        self.s = TU.Served(U.Session(program=[sys.executable, "-B", str(RUNNER)],
                                     env=dict(self.env, VERSUS_PLANT="claims")))
        self.addCleanup(self.s.close)
        return self.s

    def starts(self):
        """The stand-in CLI's starts so far, as argv lists."""
        return [json.loads(x)["argv"] for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def terminal(self, *args):
        """The command run as in a terminal in probatio's folder (the scratch one): (exit code, stdout, stderr)."""
        proc = subprocess.run([sys.executable, "-B", str(RUNNER), *args], cwd=self.tmp, env=self.env,
                              capture_output=True, text=True, timeout=600)
        return proc.returncode, proc.stdout, proc.stderr

    def finish(self, timeout=300):
        """Wait for the page's run to end; its state."""
        self.assertTrue(TU.wait_for(lambda: self.s.state()["run"]["code"] is not None, timeout))
        return self.s.state()["run"]


class TestCheckAndRun(Standin):
    def test_check_is_the_versus_dry_run_and_shows_its_preflight(self):
        """Check runs versus_run's own --dry-run of the command shown: its preflight, its totals (questions and
        episodes as calls), and no start of the CLI beyond its version."""
        form = versus_form(tier="quick", watch="off")
        shown = self.s.post("/command", {"form": form})
        reply = self.s.post("/check", {"form": form})
        self.assertTrue(reply["ok"], reply["error"])
        self.assertEqual(reply["command"], shown["command"])
        self.assertIn("probatio versus: Jane Doe and Acme Corp · quick tier", reply["plan"])
        self.assertIn(": starts fresh", reply["plan"])
        total = next(U.VERSUS_TOTAL.match(x) for x in reply["plan"].splitlines() if U.VERSUS_TOTAL.match(x))
        self.assertEqual(reply["summary"]["calls"], int(total.group(1)) + int(total.group(2)))
        self.assertEqual(self.starts(), [["--version"]])
        self.assertFalse((self.tmp / "results").exists())

    def test_run_from_the_form_ends_in_a_finished_versus_folder(self):
        """Run starts exactly the command shown and checked; the run ends cleanly and the page reads its one result
        line, the folder, inside the run's own folder, so the run counts as finished; the folder holds the run."""
        form = versus_form(tier="quick", watch="off", jobs="8")
        self.assertTrue(self.s.post("/check", {"form": form})["ok"])
        started = self.s.post("/run", {"form": form, "confirmed": True})["run"]
        self.assertEqual(started["command"], self.s.post("/command", {"form": form})["command"])
        run = self.finish()
        folder = self.tmp / "results" / "versus-claude-individual-organization-quick-memory-on"
        self.assertEqual((run["status"], run["code"]), ("finished", 0), run["lines"][-5:])
        self.assertEqual(run["results"], [["Results", U.shown(folder)], ["Page", U.shown(folder / VP.HTML_FILE)]])
        self.assertEqual(run["folders"], [U.shown(folder)])
        for f in (VS.RUN_FILE, "replies.jsonl"):
            self.assertTrue((folder / f).is_file(), f)
        card = json.loads((folder / "scorecard.json").read_text(encoding="utf-8"))
        with self.as_the_run():
            page = VP.files(card)
            own = VP.answer_lines(card)
        self.assertEqual(sorted(page), ["report.md", "results.csv", "results.svg", "scorecard.json", "scorecard.md"])
        self.assertEqual([f for f in page if not (folder / f).is_file()], [])      # the real page's files, every one
        self.assertEqual(run["lines"][-len(own) - 3:], own + [P.display(card["trust"]), f"Results: {folder}",
                                                              f"Page: {folder / VP.HTML_FILE}"])
        self.assertTrue(run["lines"][-len(own) - 3].startswith("versus: Jane Doe and Acme Corp · quick tier"))
        self.assertFalse(any(VR.PAGE_MISSING.split("(")[0] in x for x in run["lines"]))
        self.assertIsNone(run["refusal"])
        res, served = self.s.request("GET", "/results.html?folder=" + quote(U.shown(folder), safe=""), header=False)
        self.assertEqual(res.status, 200)                          # the results page, from the form's own server
        self.assertEqual(served.encode("utf-8"), (folder / VP.HTML_FILE).read_bytes())
        self.assertEqual(res.getheader("Content-Security-Policy"), U.RESULTS_PAGE_CSP)
        self.assertEqual(res.getheader("Cache-Control"), "no-store")

    def test_with_watch_the_form_links_the_versus_live_page(self):
        """Watch on: the run prints the same "watch it live" line v4's does, the page reads the link from it, the
        link serves the versus page's data, the run is scored and its page held, and Stop ends it cleanly."""
        form = versus_form(tier="quick", jobs="8")
        self.assertTrue(self.s.post("/check", {"form": form})["ok"])
        self.s.post("/run", {"form": form, "confirmed": True})
        self.assertTrue(TU.wait_for(lambda: self.s.state()["run"]["watch"] is not None, 120))
        link = self.s.state()["run"]["watch"]
        self.assertTrue(link.startswith("http://127.0.0.1:") and "/?t=" in link, link)
        self.assertTrue(TU.wait_for(lambda: self.s.state()["run"]["status"] == "scored", 300),
                        self.s.state()["run"]["lines"][-5:])
        port, query = link.split(":")[2].split("/")[0], link.split("?", 1)[1]
        conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=30)
        conn.request("GET", "/state?" + query)
        state = json.loads(conn.getresponse().read())
        conn.close()
        self.assertEqual(state["run"]["status"], "finished")
        folder = self.tmp / "results" / "versus-claude-individual-organization-quick-memory-on"
        self.assertEqual(state["run"]["answer"][-2:], [f"Results: {folder}", f"Page: {folder / VP.HTML_FILE}"])
        card = json.loads((folder / "scorecard.json").read_text(encoding="utf-8"))
        with self.as_the_run():
            own = VP.answer_lines(card)                      # the live page's end lines are the real page's own
        self.assertEqual(state["run"]["answer"], own + [P.display(card["trust"]), f"Results: {folder}",
                                                        f"Page: {folder / VP.HTML_FILE}"])
        self.assertIsNone(state["run"]["arms"][0]["rows"][0].get("estimate"))
        self.s.post("/stop", {})
        run = self.finish(60)
        self.assertEqual((run["status"], run["code"]), ("finished", 0))

    def test_a_card_the_page_refuses_is_its_one_line_on_the_form(self):
        """A card whose claims do not match its estimates (planted): the run ends with the page's one line, exit 1,
        and the form shows that line as the run's refusal under its failed pill; no page was written."""
        s = self.planted()
        form = versus_form(tier="quick", watch="off", jobs="8")
        self.assertTrue(s.post("/check", {"form": form})["ok"])
        s.post("/run", {"form": form, "confirmed": True})
        run = self.finish()
        line = "the card's claims do not match its estimates; no page was written from this card"
        self.assertEqual((run["status"], run["code"], run["refusal"]), ("failed", 1, line), run["lines"][-5:])
        self.assertEqual(run["lines"][-1], line)
        self.assertEqual(run["results"], [])
        folder = self.tmp / "results" / "versus-claude-individual-organization-quick-memory-on"
        self.assertTrue((folder / "replies.jsonl").is_file())
        self.assertEqual([f for f in ("scorecard.md", "scorecard.json", "report.md") if (folder / f).exists()], [])

    def test_with_watch_a_card_the_page_refuses_is_its_one_line_on_both_pages(self):
        """Watch on and a planted card: the live page shows the refused pill's state with that one line and no
        answer, held; the form reads the run as refused with the same line, and after Stop still refused, exit 1."""
        s = self.planted()
        form = versus_form(tier="quick", jobs="8")
        self.assertTrue(s.post("/check", {"form": form})["ok"])
        s.post("/run", {"form": form, "confirmed": True})
        self.assertTrue(TU.wait_for(lambda: s.state()["run"]["status"] == "refused", 300),
                        s.state()["run"]["lines"][-5:])
        line = "the card's claims do not match its estimates; no page was written from this card"
        self.assertEqual(s.state()["run"]["refusal"], line)
        link = s.state()["run"]["watch"]
        port, query = link.split(":")[2].split("/")[0], link.split("?", 1)[1]
        conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=30)
        conn.request("GET", "/state?" + query)
        state = json.loads(conn.getresponse().read())["run"]
        conn.close()
        self.assertEqual((state["status"], state["refusal"], state["answer"]), ("refused", line, None))
        s.post("/stop", {})
        run = self.finish(60)
        self.assertEqual((run["status"], run["code"], run["refusal"]), ("refused", 1, line))

    def test_a_refusal_of_the_command_shows_as_the_form_shows_refusals(self):
        """A name the command refuses is its own line, verbatim, in red, with nothing run: the joint secretariat,
        and a two-name run's fixed third partner."""
        for name, words in (("Joint Secretariat", "is the joint secretariat"),
                            ("Arvenia", "fixed third partner, the government of Arvenia")):
            reply = self.s.post("/check", {"form": versus_form(targets={"organization": name})})
            self.assertFalse(reply["ok"])
            self.assertIn(words, reply["error"])
            self.assertNotIn("\n", reply["error"])
        self.assertEqual(self.starts(), [])                    # refused before the CLI's version is asked

    def test_a_folder_the_command_would_refuse_is_named_in_the_pages_words(self):
        """A results folder holding a versus run with other settings: Check names it in the page's words (no
        --out), keeps the command's own line in the whole preflight, and approves nothing; a folder that took such a
        run after Check refuses the Run the same way, just before it would start."""
        code, _, err = self.terminal("--agent", "claude", "--versus", "individual=Jane Doe", "--versus",
                                     "organization=Acme Corp", "--tier", "quick", "--effort", "low", "--jobs", "8")
        self.assertEqual(code, 0, err[-2000:])
        folder = self.tmp / "results" / "versus-claude-individual-organization-quick-memory-on"
        reply = self.s.post("/check", {"form": versus_form(tier="quick", watch="off")})
        page = U.VERSUS_PAGE_WORDS["RESUME_LINE"].format(folder=U.shown(folder), what="the effort", theirs="low",
                                                         mine="medium")
        self.assertEqual((reply["ok"], reply["error"]), (False, page))
        self.assertIn("pass --out with another folder", reply["plan"])
        self.assertIn(": refused: ", reply["plan"])
        form = versus_form(tier="quick", watch="off", memory="off")
        self.assertTrue(self.s.post("/check", {"form": form})["ok"])
        code, _, err = self.terminal("--agent", "claude", "--versus", "individual=Jane Doe", "--versus",
                                     "organization=Acme Corp", "--tier", "quick", "--memory", "off", "--effort",
                                     "high", "--jobs", "8")
        self.assertEqual(code, 0, err[-2000:])
        refused = self.s.post("/run", {"form": form, "confirmed": True})
        off = self.tmp / "results" / "versus-claude-individual-organization-quick-memory-off"
        self.assertEqual(refused["error"], U.VERSUS_PAGE_WORDS["RESUME_LINE"].format(
            folder=U.shown(off), what="the effort", theirs="high", mine="medium"))
        self.assertIn("pass --out with another folder", refused["plan"])
        self.assertIsNone(self.s.state()["run"])

    def test_a_run_folder_that_cannot_be_resolved_is_a_refusal(self):
        """The run's folder turned into a link to itself just before Run starts the run (the race made certain: at
        Run's last look at the folder, versus_refusal) cannot be resolved: Run answers as it answers a process that
        cannot start, with ELOOP's words, and starts nothing; never a connection closed unanswered."""
        form = versus_form(tier="quick", watch="off")
        self.assertTrue(self.s.post("/check", {"form": form})["ok"])
        folder = self.tmp / "results" / "versus-claude-individual-organization-quick-memory-on"
        session = self.s.session
        real = session.versus_refusal

        def looping(argv, at):
            said = real(argv, at)
            folder.parent.mkdir(parents=True, exist_ok=True)
            folder.symlink_to(folder)
            return said
        with mock.patch.object(session, "versus_refusal", looping):
            reply = self.s.post("/run", {"form": form, "confirmed": True})
        self.assertEqual(reply, {"ok": False, "error": f"the run could not start ({os.strerror(errno.ELOOP)})"})
        self.assertIsNone(self.s.state()["run"])
        self.assertEqual(self.starts(), [["--version"]])            # the Check's version; no run started
        folder.unlink()

    def test_a_link_loop_already_at_the_run_folder_is_a_refusal(self):
        """The run's folder made a link to itself after Check passed, already there when Run is pressed: preparing
        the run again (versus_prepared, through versus_run's own run_folder) cannot resolve it, and Run answers as for
        a run that cannot start, with ELOOP's words; nothing starts, and the form's server answers afterwards."""
        form = versus_form(tier="quick", watch="off")
        self.assertTrue(self.s.post("/check", {"form": form})["ok"])
        folder = self.tmp / "results" / "versus-claude-individual-organization-quick-memory-on"
        folder.parent.mkdir(parents=True, exist_ok=True)
        folder.symlink_to(folder)
        reply = self.s.post("/run", {"form": form, "confirmed": True})
        self.assertEqual(reply, {"error": f"the run could not start ({os.strerror(errno.ELOOP)})"})
        self.assertIsNone(self.s.state()["run"])
        self.assertEqual(self.starts(), [["--version"]])            # the Check's version; no run started
        self.assertIn("command", self.s.post("/command", {"form": form}))      # the server still answers
        folder.unlink()

    def test_a_long_chain_of_links_at_the_run_folder_is_a_refusal(self):
        """The run's folder made the last of a chain of 1,100 links (no loop) after Check passed: Run answers as for a
        run that cannot start, with ELOOP's words, on every Python; nothing starts, and the server answers after."""
        form = versus_form(tier="quick", watch="off")
        self.assertTrue(self.s.post("/check", {"form": form})["ok"])
        folder = self.tmp / "results" / "versus-claude-individual-organization-quick-memory-on"
        TU.link_chain(folder, 1100, self.tmp / "chain")
        reply = self.s.post("/run", {"form": form, "confirmed": True})
        self.assertEqual(reply, {"error": f"the run could not start ({os.strerror(errno.ELOOP)})"})
        self.assertIsNone(self.s.state()["run"])
        self.assertEqual(self.starts(), [["--version"]])            # the Check's version; no run started
        self.assertIn("command", self.s.post("/command", {"form": form}))      # the server still answers

    def test_a_folder_name_too_long_is_in_the_pages_words(self):
        """A model name long enough that the results folder's name cannot exist: the command's own line names
        --out, so the page puts it in its words and keeps the command's line in the whole preflight. (The name is
        too long to look up only once results/ exists; before that the lookup stops at the missing folder.)"""
        (self.tmp / "results").mkdir()
        reply = self.s.post("/check", {"form": versus_form(model="m" * 300)})
        self.assertFalse(reply["ok"])
        self.assertTrue(reply["error"].startswith("the results folder results/versus-claude-"), reply["error"])
        self.assertIn("and the page always writes there", reply["error"])
        self.assertIn("pass --out with another folder", reply["plan"])


# ---- the question in a real browser --------------------------------------------------------------------------------------
@unittest.skipUnless(TU.CHROME, "needs a headless Chrome on PATH")
class TestQuestionInChrome(Standin):
    def test_the_question_in_a_real_browser(self):
        """Typing a second name of another type shows the question with its first answer chosen; the command becomes
        --versus, and the rosters, the classes, auto memory and the full length leave the page while the comparison
        choice joins it; the second answer brings today's comparison and its options back; a second name in one row
        takes the question away; Check passes on the versus command; nothing is drawn in red."""
        chrome = TU.Chrome(self.tmp / "chrome")
        try:
            def settle():
                self.assertTrue(TU.wait_for(lambda: chrome.js(
                    "window.__inflight === 0 && Date.now() - window.__last > 400 && !checking"), 60))
            visible = lambda i: chrome.js(                                      # noqa: E731
                f"(() => {{ const e = document.getElementById('{i}'); return !!e && e.getClientRects().length > 0; }})()")
            text = lambda i: chrome.js(f"document.getElementById('{i}').textContent")       # noqa: E731
            rows = lambda *names: chrome.js(                                    # noqa: E731
                "(() => { const t = document.querySelectorAll('#measure textarea'); " + " ".join(
                    f"t[{i}].value = {json.dumps(n)}; t[{i}].dispatchEvent(new Event('input'));"
                    for i, n in enumerate(names)) + " window.__last = Date.now(); })()")
            chrome.call("Page.navigate", url=f"http://127.0.0.1:{self.s.port}/?t={self.s.token}")
            self.assertTrue(TU.wait_for(lambda: chrome.js("typeof said !== 'undefined' && said !== null"), 60))
            chrome.js(TU.PAGE_HANDS)
            for step in ("__h.click('claude')", "__h.click('organization')"):
                chrome.js(step)
                settle()
            chrome.js("document.getElementById('more').open = true")
            rows("Jane Doe")
            settle()
            self.assertFalse(visible("question"))
            rows("Jane Doe", "Acme Corp")
            settle()
            self.assertTrue(visible("question"))
            self.assertEqual(chrome.js("document.getElementById('answer-names').getAttribute('aria-checked')"), "true")
            words = U.VERSUS_WORDS
            self.assertIn(words["names"], text("answer-names"))
            self.assertIn(words["names_line"], text("answer-names"))
            self.assertIn(words["types_line"], text("answer-types"))
            self.assertEqual(text("cmd"), U.command_text(U.build_command(versus_form())[0]))
            for gone in ("against", "row-classes", "row-auto"):
                self.assertFalse(visible(gone), gone)
            self.assertTrue(visible("row-strangers"))
            self.assertEqual(chrome.js("[...document.querySelectorAll('#tiers button')].filter(b => "
                                       "b.getClientRects().length).map(b => b.value)"), ["quick", "standard"])
            chrome.js("document.getElementById('answer-types').click(); window.__last = Date.now()")
            settle()
            self.assertIn("--compare individual:organization", text("cmd"))
            for back in ("against", "row-classes", "row-auto", "namesnote"):
                self.assertTrue(visible(back), back)
            self.assertFalse(visible("row-strangers"))
            chrome.js("document.getElementById('answer-names').click(); window.__last = Date.now()")
            settle()
            self.assertIn("--versus", text("cmd"))
            chrome.js("document.getElementById('check').click()")
            settle()
            self.assertTrue(chrome.js("plan && plan.ok"), chrome.js("JSON.stringify(plan)"))
            self.assertIn("probatio versus: Jane Doe and Acme Corp", chrome.js("plan.plan"))
            rows("Jane Doe\nZoe Smith", "Acme Corp")
            settle()
            self.assertFalse(visible("question"))
            self.assertIn("--compare", text("cmd"))
            self.assertEqual(chrome.js("__h.reds()"), [])
        finally:
            chrome.close()


if __name__ == "__main__":
    unittest.main()
