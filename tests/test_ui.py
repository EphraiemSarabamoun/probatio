"""Tests for the --ui page (ui.py): the form's defaults are probatio's own; build_command's command lines parse to the
options the form chose, with only the flags that change something; the entities file loads and round-trips; the
parser's and the dry run's messages reach the page verbatim; the server's refusals (the token, the Host, the CSP, POST
only, CSRF, the exact routes); Check and Run with stub CLIs and a stub run, one run at a time, and Stop reaching the
page's own child only; and no secret, and nothing of calibration/local.json, on the page. No test calls a model: the
CLIs are the guard's stubs, which answer --version and refuse anything else, and a run is a stub program.

    python3 -m unittest discover -s tests
"""
import argparse
import hashlib
import http.client
import io
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "tests")]
import live as L  # noqa: E402
import probatio as P  # noqa: E402
import ui as U  # noqa: E402
from test_user_options import STUB_SH  # noqa: E402  (the guard's stub CLIs, one home)

# A run, for the tests: a dry run is the real probatio.py; anything else prints what a run prints (the watch link, a
# progress line, any text in STUB_SAY, the result lines when STUB_FINISH is set), writes its pid, and holds until
# SIGINT, which it answers as probatio does.
STUB_RUN = '''import os, secrets, sys, time
real, args = sys.argv[1], sys.argv[2:]
if "--dry-run" in args:
    os.execv(sys.executable, [sys.executable, "-B", real, *args])
open(os.environ["STUB_PID"], "w").write(str(os.getpid()))
link = "http://127.0.0.1:45871/?t=" + secrets.token_urlsafe(24)
sys.stderr.write("  watch it live: " + link + "\\n")
print("args: " + " ".join(args))
print("probatio: quick . 36/360 . 35 valid")
if os.environ.get("STUB_SAY"):
    print(os.environ["STUB_SAY"])
if os.environ.get("STUB_FINISH"):
    print("Scorecard: " + os.path.join(os.getcwd(), "results", "stub-run", "scorecard.json"))
    print("Report: " + os.path.join(os.getcwd(), "results", "stub-run", "report.md"))
    sys.stderr.write("\\nThe live page stays up at " + link + " until you press Ctrl-C.\\n")
try:
    while True:
        time.sleep(0.2)
except KeyboardInterrupt:
    sys.stderr.write("\\ninterrupted. Rerun the same command to resume.\\n")
    sys.exit(1)
'''
JANE = {"agent": "claude", "name": "Jane Doe", "watch": True}


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
        self.server = L.serve_page(U.PAGE, session.state, actions=session.actions())
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
        self.assertEqual(set(base["tiers"]) | set(base["setup"]), set(acts["--tier"].choices))
        self.assertEqual(base["setup"], ["smoke"])         # smoke only behind the page's setup check
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
    def test_the_default_form_is_the_plain_default_command(self):
        argv, entities = U.build_command(JANE)
        self.assertEqual((argv, entities), (["--agent", "claude", "--name", "Jane Doe", "--watch"], None))
        a = P.parse_args_v4(argv)
        self.assertFalse(P.uses_options(a))               # v4's own path (requirement 1's)
        self.assertEqual((a.target, a.tier, a.memory, a.effort, a.jobs), (None, "standard", "on", "medium", 4))
        self.assertEqual(U.build_command(dict(JANE, watch=False))[0], ["--agent", "claude", "--name", "Jane Doe"])

    def test_a_default_spelled_out_adds_no_flag(self):
        base = U.defaults()
        spelled = dict(JANE, types=["individual"], targets={"individual": ""}, tier="standard", memory="on",
                       effort="medium", jobs="4", auto_memory="as-configured", classes=["I", "II", "III"], model=" ",
                       rosters={t: {p: "\n".join(U.entry_name(e) for e in base["rosters"][t][p]) for p in U.LISTS}
                                for t in P.TARGET_TYPES})
        self.assertEqual(U.build_command(spelled), U.build_command(JANE))

    def test_forms_parse_to_the_options_they_chose(self):
        table = [
            (MOZILLA, dict(compare="organization", target=["organization=Mozilla"], tier="quick",
                           classes=("I", "II"))),
            (dict(MOZILLA, classes=["I", "II"]), dict(tier="quick", classes=("I", "II"))),
            (dict(JANE, targets={"individual": "Ada Lovelace\n\nGrace Hopper\n"}),
             dict(compare=None, target=["individual=Ada Lovelace", "individual=Grace Hopper"], name="Jane Doe")),
            ({"agent": "codex", "types": ["nation", "individual", "organization"],
              "targets": {"nation": "France", "organization": ""}, "memory": "both", "effort": "high",
              "model": "gpt-x", "jobs": 2, "auto_memory": "off", "classes": ["I"], "tier": "full"},
             dict(agent="codex", compare="individual:organization:nation", target=["nation=France"],
                  memory="both", effort="high", model="gpt-x", jobs=2, auto_memory="off", classes=("I",),
                  tier="full", watch=False)),
            ({"agent": "claude", "types": ["nation", "individual"]}, dict(compare="individual:nation", target=None)),
            ({"agent": "claude", "classes": ["II", "III"]}, dict(classes=("II", "III"), tier="standard")),
            ({"agent": "claude", "tier": "smoke"}, dict(tier="smoke")),
        ]
        for form, want in table:
            argv, _ = U.build_command(form)
            a = P.parse_args_v4(argv)
            for key, value in want.items():
                self.assertEqual(getattr(a, key), value, (form, argv, key))
            self.assertEqual(shlex.split(U.command_text(argv)), ["python3", "probatio.py", *argv])

    def test_an_edited_roster_is_an_entities_file_that_round_trips(self):
        form = dict(MOZILLA, rosters={"organization": {"comparators": "Anthropic\nOpenAI\nMozilla Foundation",
                                                       "controls": "Thirlbeck Systems\nVarrow Analytics"}})
        argv, text = U.build_command(form)
        path = argv[argv.index("--entities") + 1]
        self.assertEqual(path, f"results/ui-entities-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:8]}.json")
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


# ---- probatio's messages, verbatim -----------------------------------------------------------------------------------------
class TestVerbatim(unittest.TestCase):
    def parser_says(self, argv):
        err = io.StringIO()
        with mock.patch("sys.stderr", err), self.assertRaises(SystemExit) as caught:
            P.parse_args_v4(argv)
        return caught.exception.code if isinstance(caught.exception.code, str) else \
            err.getvalue().strip().splitlines()[-1].split(": error: ", 1)[1]

    def test_the_parsers_errors_reach_the_page_verbatim(self):
        for form in ({"name": "Jane Doe"}, dict(MOZILLA, classes=["I", "III"]), dict(JANE, jobs="x"),
                     dict(JANE, classes=[]), dict(JANE, tier="bogus")):
            argv, _ = U.build_command(form)
            self.assertEqual(U.preview(form)["error"], self.parser_says(argv), form)
        self.assertEqual(U.preview(dict(MOZILLA, classes=["I", "III"]))["error"], P.QUICK_NO_III)

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


# ---- Check and Run, with stubs ---------------------------------------------------------------------------------------------
class TestCheckAndRun(Stubbed):
    def setUp(self):
        super().setUp()
        self.s = Served(U.Session(program=self.program, env=self.env))
        self.made = []                                  # entities files a test wrote under results/, removed after

    def tearDown(self):
        self.s.close()
        for f in self.made:
            f.unlink(missing_ok=True)
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
        self.assertIn('organization "Mozilla": Mozilla', reply["plan"])          # the names as the items write them
        self.assertEqual(reply["command"], U.command_text(argv))

    def test_a_refused_check_shows_probatios_message_verbatim(self):
        for form in (dict(MOZILLA, targets={"organization": "Moz{illa"}), {"agent": "claude", "types": ["nation"]}):
            reply = self.s.post("/check", {"form": form})
            argv = U.build_command(form)[0]
            direct = subprocess.run([sys.executable, "-B", str(REPO / "probatio.py"), *argv, "--dry-run"], cwd=REPO,
                                    env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            self.assertFalse(reply["ok"])
            self.assertEqual(reply["error"], direct.stderr.strip())
        self.assertIn("--target is needed for an organization or a nation", reply["error"])

    def test_check_writes_the_entities_file_it_names(self):
        form = dict(MOZILLA, rosters={"organization": {"controls": "Thirlbeck Systems\nVarrow Analytics"}})
        reply = self.s.post("/check", {"form": form})
        file = REPO / reply["entities"]["path"]
        self.made.append(file)
        self.assertTrue(reply["ok"], reply["error"])
        self.assertEqual(file.read_text(encoding="utf-8"), reply["entities"]["text"])
        self.assertIn('custom control "Varrow Analytics": not calibrated', reply["plan"])

    def test_run_needs_a_confirmed_and_checked_command(self):
        self.assertIn("confirm", self.s.post("/run", {"form": MOZILLA})["error"])
        self.assertIn("check this command first", self.s.post("/run", {"form": MOZILLA, "confirmed": True})["error"])
        self.assertTrue(self.s.post("/check", {"form": MOZILLA})["ok"])
        changed = dict(MOZILLA, tier="standard")
        self.assertIn("check this command first", self.s.post("/run", {"form": changed, "confirmed": True})["error"])
        self.assertIsNone(self.s.state()["run"])

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
            self.assertEqual(run["command"], U.command_text([*argv, "--watch"]))
            self.assertTrue(wait_for(lambda: any(x.startswith("args: ") for x in self.s.state()["run"]["lines"])))
            self.assertIn("args: " + " ".join([*argv, "--watch"]), self.s.state()["run"]["lines"])
            self.assertRegex(run["watch"], r"^http://127\.0\.0\.1:45871/\?t=[A-Za-z0-9_-]{32}$")
            self.assertEqual(run["folders"], [U.shown(f) for f in U.run_folders(argv)])
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

    def test_closing_the_page_stops_its_run(self):
        _, pid = self.start()
        out = io.StringIO()
        self.s.session.close(out)
        self.assertIsNotNone(self.s.session.runner.code)
        self.assertIn("stopping the page's run", out.getvalue())


class TestRunFolders(unittest.TestCase):
    """run_folders names the folder probatio's own main makes for the run: v4's path and the options path."""

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

    def test_the_folders_are_probatios(self):
        for form in (dict(JANE, watch=False), dict(MOZILLA, targets={"organization": "Mozilla\nWikimedia"}),
                     {"agent": "claude", "types": ["organization", "nation"], "tier": "quick"}):
            with tempfile.TemporaryDirectory() as d, mock.patch.object(P, "HERE", Path(d)):
                argv = U.build_command(form)[0] + ["--workdir", d]
                want = [Path(f) for f in U.run_folders(argv)]
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
