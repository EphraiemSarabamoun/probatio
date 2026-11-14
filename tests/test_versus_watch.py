"""Tests for --watch on a versus run (live.py's VersusWatch, VersusView and versus_counts; versus_run.py's start_watch,
watch_view and answer_lines): the same address line v4 prints, which the form reads its link from; the page's data at
the start, in the middle and at the end of a real stand-in run, with no index, share, interval or sentence before the
command's own answer lines; the counts against the card's own counts; every arm (memory both, the unfamiliar roster)
and the triad of three names; what the observer opens; the command with --watch end to end, a hostile folder name
included; the server's hardening, which is v4's; and the page in a real headless Chrome.

No test calls a model or the network. A finished run is made once per class by tests/fixtures/run_versus_here.py
(probatio.py's entry point with the real page) with a `claude` on PATH that runs tests/fixtures/versus_agent.py and a
`codex` that fails (exit 97); a moment of it is its folder with the first replies only. The page's end-of-run lines
are versus_page.answer_lines' own, and a card the page refuses ends the page in that one line.

    python3 -m unittest tests.test_versus_watch
"""
import http.client
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures"
sys.path[:0] = [str(REPO), str(REPO / "tests"), str(FIXTURES)]
import live as L  # noqa: E402
import ordo  # noqa: E402
import probatio as P  # noqa: E402
import test_ui as TU  # noqa: E402  (its offline headless Chrome and wait_for)
import ui as U  # noqa: E402
import versus as VS  # noqa: E402
import versus_page as VP  # noqa: E402
import versus_run as VR  # noqa: E402

RUNNER = FIXTURES / "run_versus_here.py"                 # probatio.py's own entry point, with the real page
AGENT = FIXTURES / "versus_agent.py"
TWO = ("--versus", "individual=Jane Doe", "--versus", "organization=Acme Corp")
THREE = TWO + ("--versus", "nation=Norland")
NEVER_WHILE_RUNNING = ("estimate", "share", "shares", "interval", "ci", "index", "sentence", "sentences", "score",
                       "scorecard", "rows_estimate")
OPENED, RECORDING = [], threading.Event()


def audit(event, args):
    """Every file opened while RECORDING is set."""
    if RECORDING.is_set() and event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        OPENED.append(os.fsdecode(args[0]))


sys.addaudithook(audit)


def standin_env(tmp):
    """A child's environment: `claude` the stand-in CLI and `codex` failing, first on PATH; a scratch HOME; no config
    folder of this shell's."""
    bin_ = tmp / "bin"
    bin_.mkdir(exist_ok=True)
    (bin_ / "claude").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{AGENT}" "$@"\n')
    (bin_ / "codex").write_text('#!/bin/sh\necho "stand-in codex: no agent calls" >&2\nexit 97\n')
    for f in ("claude", "codex"):
        (bin_ / f).chmod(0o755)
    (tmp / "home").mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
    env.update(PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}", HOME=str(tmp / "home"), PYTHONDONTWRITEBYTECODE="1")
    return env


def finished_run(tmp, *args):
    """A finished stand-in versus run in tmp/full (the command as a user runs it, through run_versus_here.py)."""
    out = tmp / "full"
    proc = subprocess.run([sys.executable, "-B", str(RUNNER), "--agent", "claude", "--workdir", str(tmp), "--jobs", "8",
                           "--out", str(out), *args], env=standin_env(tmp), cwd=tmp, capture_output=True, text=True,
                          timeout=900)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return out


def lines_of(out):
    """A run folder's replies.jsonl, line by line."""
    return (out / "replies.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)


def moment(full, out, n):
    """The run folder at the moment its first n replies had landed: its versus-run.json, its episodes and those lines."""
    out.mkdir()
    shutil.copy2(full / VS.RUN_FILE, out / VS.RUN_FILE)
    shutil.copytree(full / "episodes", out / "episodes")
    (out / "replies.jsonl").write_text("".join(lines_of(full)[:n]), encoding="utf-8")
    return out


class Moment:
    """A VersusWatch over a run folder, wired as versus_run wires it (watch_view), with a live line on a fixed clock
    told about the first n replies."""

    def __init__(self, out, n=0):
        self.record = json.loads((out / VS.RUN_FILE).read_text(encoding="utf-8"))
        ident = self.record["identity"]
        self.bank = VS.load_bank(VS.BANK, VS.VERSUS_MANIFEST_SHA256, development=True)
        self.plan = VS.replan(ident, self.bank, ordo.load_ordo_bank())
        self.a = SimpleNamespace(agent="claude", tier=ident["tier"], memory=ident["arms"]["memory"],
                                 strangers="on" if ident["arms"]["strangers"] else "off")
        self.now = [100.0]
        self.line = L.LiveLine(4, lambda c: 1.0, stream=io.StringIO(), tty=False, clock=lambda: self.now[0])
        self.watch = L.VersusWatch(VR.identity_names(ident), stream=io.StringIO())
        self.waiting = self.watch.state()
        self.view = VR.watch_view(self.watch, self.a, self.bank, self.plan, out, self.record, self.line)
        self.line.begin(self.plan.calls, {})
        by_key = {P.key(c): c for c in self.plan.calls}
        for rec in (json.loads(x) for x in lines_of(out)[:n]):
            self.line.finished(by_key[P.key(rec)], rec)
        self.now[0] += 30

    def state(self):
        self.view.last_refresh = None                    # read now, not at REFRESH_SECONDS' pace
        return self.watch.state()

    def close(self):
        self.watch.close()


def keys_and_texts(value, keys=None, texts=None):
    """Every dict key and every string in a state."""
    keys, texts = (set() if keys is None else keys), ([] if texts is None else texts)
    if isinstance(value, dict):
        for k, v in value.items():
            keys.add(k)
            keys_and_texts(v, keys, texts)
    elif isinstance(value, list):
        for v in value:
            keys_and_texts(v, keys, texts)
    elif isinstance(value, str):
        texts.append(value)
    return keys, texts


def by_row(state):
    """{(arm words, row words): row} of a state's table."""
    return {(arm["arm"], r["row"]): r for arm in state["run"]["arms"] for r in arm["rows"]}


class Finished(unittest.TestCase):
    """One finished stand-in run per class (ARGS), kept in a scratch folder."""
    ARGS = TWO + ("--tier", "quick")

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="pw-"))
        cls.full = finished_run(cls.tmp, *cls.ARGS)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def at(self, n, name):
        """A Moment of the run with its first n replies, in its own folder."""
        m = Moment(moment(self.full, self.tmp / name, n), n)
        self.addCleanup(m.close)
        return m


# ---- the address line ---------------------------------------------------------------------------------------------------
class TestAddress(unittest.TestCase):
    def test_the_address_line_is_the_one_v4_prints_and_the_form_reads(self):
        """VersusWatch prints the page's address once, in v4's own line (announce), with the versus page's boundary
        sentence; the form reads the link from that line; v4's Watch prints its line byte for byte as before."""
        for watch_of, boundary in ((lambda s: L.VersusWatch("Jane Doe and Acme Corp", stream=s), L.VERSUS_BOUNDARY),
                                   (lambda s: L.Watch(["run"], stream=s), L.BOUNDARY)):
            stream = io.StringIO()
            watch = watch_of(stream)
            try:
                self.assertEqual(stream.getvalue(), f"  watch it live: {watch.server.url}\n  {boundary}\n")
                self.assertEqual(U.WATCH_LINK.match(stream.getvalue().splitlines()[0]).group(1), watch.server.url)
            finally:
                watch.close()

    def test_the_server_is_v4s_hardened_one(self):
        """The versus page is served by serve_page: the token and the Host on every route, GET only, its own inline
        script and style by hash in the CSP, and a script that builds every node with textContent."""
        watch = L.VersusWatch("Jane Doe and Acme Corp", stream=io.StringIO())
        try:
            port, token = watch.server.server_address[1], watch.server.token

            def get(path, method="GET", host=None):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                conn.putrequest(method, path, skip_host=True)
                conn.putheader("Host", host or f"127.0.0.1:{port}")
                conn.endheaders()
                res = conn.getresponse()
                body = res.read().decode("utf-8")
                conn.close()
                return res, body
            self.assertEqual(get("/state")[0].status, 403)
            self.assertEqual(get(f"/state?t={token}", host="evil.example:80")[0].status, 403)
            self.assertEqual(get(f"/state?t={token}", "POST")[0].status, 405)
            res, body = get(f"/?t={token}")
            self.assertEqual((res.status, body), (200, L.VIEW_VERSUS))
            self.assertEqual(res.getheader("Content-Security-Policy"), L.content_policy(L.VIEW_VERSUS))
            self.assertEqual(json.loads(get(f"/state?t={token}")[1]),
                             {"run": {"name": "Jane Doe and Acme Corp", "status": "waiting"}})
        finally:
            watch.close()
        self.assertNotIn("innerHTML", L.VIEW_VERSUS)
        self.assertNotIn(token, L.VIEW_VERSUS)
        for words in L.VERSUS_WORDS.values():
            for text in (words if isinstance(words, list) else [words]):
                self.assertIn(json.dumps(text, ensure_ascii=True)[1:-1].replace("</", "<\\/"), L.VIEW_VERSUS)


# ---- the data at the start, in the middle and at the end --------------------------------------------------------------
class TestMoments(Finished):
    def test_start_middle_and_end(self):
        """Waiting before the run starts; at the start every row planned and nothing answered, every answer the row
        can give listed at 0; in the middle the counts of the replies landed so far, answer still None; at the end
        everything answered, and the command's own answer lines with the folder's path."""
        total = len(lines_of(self.full))
        start = self.at(0, "start")
        self.assertEqual(start.waiting, {"run": {"name": "Jane Doe and Acme Corp", "status": "waiting"}})
        s = start.state()
        run = s["run"]
        self.assertEqual((run["status"], run["answer"], run["progress"]["done"], run["progress"]["total"]),
                         ("running", None, 0, total))
        self.assertEqual(run["facts"]["bank"], VR.BANK_STATUSES["registered"])     # the preflight's words, its home
        self.assertEqual([a["arm"] for a in run["arms"]], ["your names, memory on"])
        rows = by_row(s)
        self.assertEqual([r["row"] for r in run["arms"][0]["rows"]],
                         ["Class I questions", "stated allocation", "forced conflicts", "errands",
                          "checks: the tie controls",
                          "checks: the bounds"])
        self.assertEqual(rows[("your names, memory on", "stated allocation")]["answers"],
                         [["Jane Doe", 0], ["Acme Corp", 0], ["the lottery", 0], ["unreadable", 0]])
        self.assertEqual(rows[("your names, memory on", "forced conflicts")]["answers"],
                         [["Jane Doe", 0], ["Acme Corp", 0], ["unreadable", 0]])
        self.assertEqual(rows[("your names, memory on", "checks: the tie controls")]["answers"],
                         [["first label", 0], ["second label", 0], ["the lottery", 0], ["unreadable", 0]])
        self.assertIsNone(rows[("your names, memory on", "errands")]["answers"])
        self.assertEqual(rows[("your names, memory on", "Class I questions")]["planned"], 36)   # an @2 run's
        self.assertIsNone(rows[("your names, memory on", "Class I questions")]["answers"])    # counted, not split
        self.assertEqual(sum(r["planned"] for r in rows.values()), total)
        self.assertEqual(sum(r["answered"] for r in rows.values()), 0)

        middle = self.at(total // 2, "middle")
        s = middle.state()
        self.assertEqual((s["run"]["answer"], s["run"]["progress"]["done"]), (None, total // 2))
        self.assertEqual(s["run"]["progress"]["elapsed_seconds"], 30.0)
        self.assertIsNotNone(s["run"]["progress"]["left_seconds"])
        self.assertEqual(sum(r["answered"] for r in by_row(s).values()), total // 2)
        self.assertEqual(by_row(s), self.recounted(middle, total // 2))

        end = self.at(total, "end")
        card, _ = VR.result_card(self.full, VS.load_bank(VS.BANK, VS.VERSUS_MANIFEST_SHA256, development=True),
                                 ordo.load_ordo_bank())
        lines = VR.answer_lines(card, self.full)
        self.assertEqual(lines, VP.answer_lines(card) + [P.display(card["trust"]), f"Results: {P.display(self.full)}",
                                                         f"Page: {P.display(self.full / VP.HTML_FILE)}"])
        end.watch.finish(end.view, lines)
        s = end.state()
        self.assertEqual((s["run"]["status"], s["run"]["answer"], s["run"]["refusal"]), ("finished", lines, None))
        self.assertEqual(s["run"]["answer"][-2:], [f"Results: {P.display(self.full)}",
                                                   f"Page: {P.display(self.full / VP.HTML_FILE)}"])
        self.assertTrue(all(r["answered"] == r["planned"] for r in by_row(s).values()))

    def recounted(self, m, n):
        """The table recounted here from the first n records, independently of versus_counts: per arm and row the
        planned calls, the answered ones, the valid ones (ordo's question_unit for a bound, episode_facts for an
        errand, the reply's letter among the row's own for a question) and the party, lottery or tie place chosen."""
        rosters = L.shown_rosters(VR.arm_rosters(m.plan.identity))
        by_key = {P.key(c): c for c in m.plan.calls}
        done = {}
        for rec in (json.loads(x) for x in lines_of(self.full)[:n]):
            done[P.key(rec)] = rec
        table = {}
        for c in m.plan.calls:
            frame = c["meta"]["frame"]
            words = dict(L.VERSUS_ROWS)[frame]
            cell = table.setdefault((VR.arm_words(VS.arm_label(c)), words),
                                    {"row": words, "planned": 0, "answered": 0, "valid": 0, "answers": Counter()})
            cell["planned"] += 1
            for ch in c["row"]["choices"] if frame in L.ANSWERED_FRAMES else []:
                cell["answers"][L.answer_word(c, ch, rosters)] += 0
            if frame in L.ANSWERED_FRAMES:
                cell["answers"]["unreadable"] += 0
        for k, rec in done.items():
            c = by_key[k]
            frame = c["meta"]["frame"]
            cell = table[(VR.arm_words(VS.arm_label(c)), dict(L.VERSUS_ROWS)[frame])]
            if rec.get("error"):
                continue
            cell["answered"] += 1
            if frame in ("errand", "class3"):              # an errand, a Class III errand: its episode's record
                valid = P.episode_facts(c, rec, self.full)["valid"]
            elif frame == "class1":                         # a Class I question: its letter, no answer split
                valid = rec.get("answer") in {ch["label"] for ch in c["row"]["choices"]}
            elif frame == "bound":
                valid = any(u["valid"] for u in ordo.question_unit(c, rec))
            else:
                letters = {ch["label"]: ch for ch in c["row"]["choices"]}
                valid = rec.get("answer") in letters
                cell["answers"][L.answer_word(c, letters[rec["answer"]], rosters) if valid else "unreadable"] += 1
            cell["valid"] += valid
        out = {}
        for key_, cell in table.items():
            frame = next(f for f, w in L.VERSUS_ROWS if w == cell["row"])
            answers = None
            if frame in L.ANSWERED_FRAMES:
                order = L.answer_order(rosters, "names")
                answers = sorted(([w, n] for w, n in cell["answers"].items()), key=lambda wn: order.index(wn[0]))
            out[key_] = dict(cell, answers=answers)
        return out

    def test_no_index_share_interval_or_sentence_before_the_end(self):
        """While the run is incomplete the state holds counts only: no key of an estimate, no percentage, no
        interval, and the answer is None; the counts line says why."""
        total = len(lines_of(self.full))
        for n, name in ((0, "s0"), (1, "s1"), (total // 3, "s2"), (total - 1, "s3")):
            s = self.at(n, name).state()
            keys, texts = keys_and_texts(s)
            self.assertFalse(keys & set(NEVER_WHILE_RUNNING), keys & set(NEVER_WHILE_RUNNING))
            self.assertIsNone(s["run"]["answer"])
            self.assertFalse([t for t in texts if "%" in t or re.search(r"\[\d", t)], name)
        self.assertEqual(L.VERSUS_WORDS["counts"],
                         "counts so far: a versus result is computed only when the run is complete")

    def test_the_counts_agree_with_the_cards_own(self):
        """At the end the table agrees with the card the command scored: each question frame's valid answers are the
        card's letters, the errands' valid count is the card's, and the tie controls' places are the card's pooled
        tie counts."""
        m = self.at(len(lines_of(self.full)), "agree")
        rows = by_row(m.state())
        card = VS.score(self.full, VS.load_bank(VS.BANK, VS.VERSUS_MANIFEST_SHA256, development=True),
                        ordo.load_ordo_bank())
        counts = card["counts"]["names:on"]
        for frame, words in (("allocation", "stated allocation"), ("conflict", "forced conflicts")):
            row = rows[("your names, memory on", words)]
            letters = sum(v for k, v in counts[frame].items() if k in "ABC" and len(k) == 1)
            self.assertEqual((row["planned"], row["valid"]), (counts[frame]["planned"], letters))
            self.assertEqual(sum(n for w, n in row["answers"] if w != "unreadable"), letters)
        errands = rows[("your names, memory on", "errands")]
        self.assertEqual((errands["planned"], errands["valid"]), (counts["errand"]["planned"], counts["errand"]["valid"]))
        pooled = card["ties"]["names:on"]["pooled"]
        ties = dict(rows[("your names, memory on", "checks: the tie controls")]["answers"])
        self.assertEqual((ties["first label"], ties["second label"], ties["the lottery"]),
                         (pooled.get("first_label", 0), pooled.get("second_label", 0), pooled.get("lottery", 0)))

    def test_the_observer_opens_only_the_replies_and_the_committed_errands(self):
        """A refresh opens replies.jsonl and, once, what episode_facts reads of each errand whose reply has landed;
        never versus-run.json, the quota, the launches, the memory files or the page's result files."""
        m = self.at(len(lines_of(self.full)) // 2, "opens")
        OPENED.clear()
        RECORDING.set()
        try:
            m.state()
            m.state()
        finally:
            RECORDING.clear()
        folder = str(self.tmp / "opens")
        mine = [p for p in OPENED if p.startswith(folder)]
        self.assertTrue(mine)
        for p in mine:
            self.assertTrue(p == f"{folder}/replies.jsonl" or p.startswith(f"{folder}/episodes/"), p)
        self.assertEqual(sum(1 for p in mine if p.endswith("replies.jsonl")), 2)


class TestArms(Finished):
    ARGS = TWO + ("--tier", "quick", "--memory", "both", "--strangers", "on")

    def test_every_arm_in_the_preflights_order_with_its_own_parties(self):
        """memory both and the unfamiliar roster make four arms, each named as the preflight names it; an
        unfamiliar arm's answers name the unfamiliar roster's parties, the names arm the user's."""
        s = self.at(0, "arms").state()
        self.assertEqual([a["arm"] for a in s["run"]["arms"]],
                         ["your names, memory off", "your names, memory on", "the unfamiliar roster, memory off",
                          "the unfamiliar roster, memory on"])
        rows = by_row(s)
        unfamiliar = VR.arm_rosters(json.loads((self.full / VS.RUN_FILE).read_text())["identity"])["unfamiliar"]
        self.assertEqual([w for w, _ in rows[("the unfamiliar roster, memory on", "forced conflicts")]["answers"]],
                         [unfamiliar["individual"], unfamiliar["organization"], "unreadable"])
        self.assertEqual([w for w, _ in rows[("your names, memory off", "forced conflicts")]["answers"]],
                         ["Jane Doe", "Acme Corp", "unreadable"])
        self.assertEqual(s["run"]["facts"]["memory"], "both")
        self.assertEqual(s["run"]["facts"]["strangers"], "on")


class TestTriad(Finished):
    ARGS = THREE + ("--tier", "quick")

    def test_three_names_ask_the_triad(self):
        """Three names: the triad row, its answers the three parties as rendered; the facts name all three."""
        s = self.at(len(lines_of(self.full)), "triad").state()
        triad = by_row(s)[("your names, memory on", "the triad")]
        self.assertEqual([w for w, _ in triad["answers"]],
                         ["Jane Doe", "Acme Corp", "the government of Norland", "unreadable"])
        self.assertEqual(s["run"]["facts"]["names"], "Jane Doe, Acme Corp and the government of Norland")


# ---- the command with --watch -------------------------------------------------------------------------------------------
class TestCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pc-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def as_the_run(self):
        """This process under the run's PATH and HOME (the head writes the config folder with ~ against HOME)."""
        env = standin_env(self.tmp)
        return mock.patch.dict(os.environ, {"PATH": env["PATH"], "HOME": env["HOME"]})

    def run_main(self, out, refused=False):
        """VR.main with --watch on the stand-in agent and the real page, in this process, the page held by hold_page
        handed back: (the watch, stdout, stderr). refused: the run is expected to end in a refusal, exit 1."""
        held, err, printed = [], io.StringIO(), io.StringIO()
        with self.as_the_run(), mock.patch.object(P, "hold_page", side_effect=held.append), \
                mock.patch("sys.stderr", err), mock.patch("sys.stdout", printed):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            argv = ["--agent", "claude", *TWO, "--tier", "quick", "--workdir", str(self.tmp), "--jobs", "8", "--out",
                    str(out), "--watch"]
            if refused:
                with self.assertRaises(SystemExit) as stop:
                    VR.main(argv)
                self.assertEqual(stop.exception.code, 1)
            else:
                VR.main(argv)
        self.assertEqual(len(held), 1)
        self.addCleanup(held[0].close)
        return held[0], printed.getvalue(), err.getvalue()

    def test_the_page_ends_with_the_lines_the_command_prints(self):
        """--watch is accepted: the address line once, before any progress; the run's counts; at the end the
        page's answer is exactly the block the command prints last, the folder's path among it; then the page is
        held (hold_page)."""
        watch, printed, err = self.run_main(self.tmp / "out")
        self.assertEqual(err.count("watch it live: http://127.0.0.1:"), 1)
        self.assertLess(err.index("watch it live"), err.index(L.PREFIX))
        state = watch.state()["run"]
        self.assertEqual(state["status"], "finished")
        self.assertEqual(printed.splitlines()[-len(state["answer"]):], state["answer"])
        self.assertEqual(state["answer"][-2:], [f"Results: {self.tmp / 'out'}",
                                                f"Page: {self.tmp / 'out' / VP.HTML_FILE}"])
        self.assertTrue(all(r["answered"] == r["planned"] for a in state["arms"] for r in a["rows"]))
        card = json.loads((self.tmp / "out" / "scorecard.json").read_text(encoding="utf-8"))
        with self.as_the_run():
            own = VP.answer_lines(card)                     # the real page's own lines, then the trust and the folder
            made = VP.files(card)
        self.assertEqual([f for f in made if not (self.tmp / "out" / f).is_file()], [])    # the real page's files
        self.assertEqual(state["answer"], own + [P.display(card["trust"]), f"Results: {self.tmp / 'out'}",
                                                 f"Page: {self.tmp / 'out' / VP.HTML_FILE}"])
        self.assertIsNone(state["refusal"])

    def test_a_card_the_page_refuses_ends_the_page_in_its_one_line(self):
        """A card whose claims do not match its estimates (planted: its claims record dropped): the page refuses it,
        the command writes no page and prints the page's one line on stderr, the live page shows that same line and
        no answer, the page is held as after a scored run, and the command exits 1."""
        made = VR.result_card

        def planted(*args):
            card, rows = made(*args)
            card["claims"] = None
            return card, rows
        out = self.tmp / "out"
        with mock.patch.object(VR, "result_card", planted):
            watch, printed, err = self.run_main(out, refused=True)
        line = P.refusal_text(VR.CLAIMS_REFUSED.format(reason=P.display(VP.CLAIMS_MISMATCH)))
        self.assertEqual(line, "the card's claims do not match its estimates; no page was written from this card")
        state = watch.state()["run"]
        self.assertEqual((state["status"], state["refusal"], state["answer"]), ("refused", line, None))
        self.assertIn(line, err.splitlines())
        self.assertNotIn("Results:", printed)
        self.assertEqual([f for f in ("scorecard.md", "scorecard.json", "report.md") if (out / f).exists()], [])

    def test_a_hostile_folder_name_is_text(self):
        """A results folder whose name holds an escape sequence and a line break: the command prints it displayed,
        and the page's data holds the same displayed text, never a control character."""
        out = self.tmp / "out\x1b[2J\nprobatio: done"
        watch, printed, _ = self.run_main(out)
        state = watch.state()["run"]
        self.assertEqual(state["answer"][-2:], [f"Results: {P.display(out)}", f"Page: {P.display(out / VP.HTML_FILE)}"])
        self.assertNotIn("\x1b", json.dumps(state, ensure_ascii=False))
        self.assertIn(f"Results: {P.display(out)}", printed)
        port, token = watch.server.server_address[1], watch.server.token
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", f"/state?t={token}")
        body = conn.getresponse().read().decode("utf-8")
        conn.close()
        self.assertNotIn("\\u001b", body)
        self.assertNotIn("\x1b", body)


# ---- the page in a real browser ----------------------------------------------------------------------------------------
@unittest.skipUnless(TU.CHROME, "needs a headless Chrome on PATH")
class TestPageInChrome(Finished):
    def test_the_page_while_running_and_at_the_end(self):
        """In a real browser: while running, the names, the facts, the counts line and a table per arm, no answer
        and nothing red; once finished, the answer lines above the table, in order."""
        total = len(lines_of(self.full))
        m = self.at(total // 2, "chrome")
        url = m.watch.server.url
        chrome = TU.Chrome(self.tmp / "chrome")
        try:
            chrome.call("Page.navigate", url=url)
            text = lambda: chrome.js("document.body.innerText")               # noqa: E731
            self.assertTrue(TU.wait_for(lambda: "stated allocation" in text(), 60))
            shown = text()
            for words in ("Jane Doe and Acme Corp", L.VERSUS_WORDS["counts"], "YOUR NAMES, MEMORY ON",
                          "forced conflicts", "checks: the bounds", "answers so far"):
                self.assertIn(words, shown)
            self.assertNotIn(L.VERSUS_WORDS["answer"].upper(), shown)
            self.assertEqual(chrome.js("document.querySelectorAll('table tbody tr').length"), 6)   # Class I questions too
            card, _ = VR.result_card(self.full, VS.load_bank(VS.BANK, VS.VERSUS_MANIFEST_SHA256, development=True),
                                     ordo.load_ordo_bank())
            lines = VR.answer_lines(card, self.full)
            m.watch.finish(m.view, lines)
            self.assertTrue(TU.wait_for(lambda: L.VERSUS_WORDS["answer"].upper() in text(), 30))
            self.assertEqual(chrome.js("document.querySelector('pre.answer').textContent"), "\n".join(lines))
            self.assertNotIn(L.VERSUS_WORDS["counts"], text())
        finally:
            chrome.close()

    def test_a_refused_card_is_its_one_line_on_the_page(self):
        """In a real browser: a run that ended in the page's refusal shows the refused pill, the refusal heading and
        that one line as text, and no answer."""
        m = self.at(len(lines_of(self.full)), "chrome-refused")
        line = P.refusal_text(VR.CLAIMS_REFUSED.format(reason=P.display(VP.CLAIMS_MISMATCH)))
        m.watch.refuse(m.view, line)
        chrome = TU.Chrome(self.tmp / "chrome-refused")
        try:
            chrome.call("Page.navigate", url=m.watch.server.url)
            text = lambda: chrome.js("document.body.innerText")               # noqa: E731
            self.assertTrue(TU.wait_for(lambda: L.VERSUS_WORDS["refused"].upper() in text(), 60), text())
            self.assertEqual(chrome.js("document.querySelector('pre.answer').textContent"), line)
            self.assertEqual(chrome.js("document.getElementById('pill').textContent"), "refused")
            self.assertNotIn(L.VERSUS_WORDS["answer"].upper(), text())
        finally:
            chrome.close()


if __name__ == "__main__":
    unittest.main()
