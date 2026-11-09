"""Tests for --watch on v4 runs (live.py; the design of the quick run and the run you can watch, 4.2 to 4.6): the
page server's refusals and security headers, the exhaustive list of what the observer opens, the redaction of every
free-text field with run-time canaries (secret-shaped, the page's token, a secret-named variable, a plain memory
canary), the shared redaction contract with calibration/redact.py, the committed-line reader, attempts and late
reports, the provisional scorecard's full-plan, one-snapshot contract against the final scorers, and planted
single-type, cross-type and Codex folders. Every canary is built at run time. No test calls an agent; the only
network is the page's own loopback server.

    python3 -m unittest discover -s tests
"""
import ast
import base64
import hashlib
import http.client
import importlib.util
import io
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "tests"), str(REPO / "tests" / "fixtures")]
import live as L  # noqa: E402
import ordo as O  # noqa: E402
import probatio as P  # noqa: E402
import test_ordo_scoring as OS  # noqa: E402
import test_v4_scoring as VS  # noqa: E402

REDACT_CASES = REPO / "tests" / "fixtures" / "redact_cases.json"
B_TEST = 200


def hf_canary():
    return "hf_" + "FAKE" * 8


def ant_canary():
    return "sk-ant-" + "FAKE" * 6


def memory_canary():
    return "probatio-memory-canary-" + secrets.token_hex(8)


# ---- recording what the observer opens -------------------------------------------------------------------------------
OPENED, RECORDING = [], threading.Event()


def audit(event, args):
    """Every file opened while RECORDING is set, with the thread that opened it."""
    if RECORDING.is_set() and event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        OPENED.append((os.fsdecode(args[0]), threading.current_thread().name))


sys.addaudithook(audit)


# ---- planted folders -----------------------------------------------------------------------------------------------
def v4_run(out, tier="quick", memory="on", agent="claude"):
    """A planted single-type v4 folder (test_v4_scoring's planter), with run.json; (bank, roster, calls, meta)."""
    b, roster, calls, meta = VS.planted_run(out, tier=tier, memory=memory)
    meta["agent"] = agent
    (Path(out) / "run.json").write_text(json.dumps(meta))
    return b, roster, calls, meta


def ordo_run(out, tier="standard"):
    """A planted cross-type folder on the small ordo bank; (bank, calls, meta)."""
    b = OS.bank()
    calls = O.plan(b, "individual:organization:nation", tier)
    OS.plant(out, calls, OS.prefer(), lambda m: 0.6)
    meta = {"agent": "claude", "cli": "0.0-fake", "model": "m", "effort": "medium", "tier": tier,
            "recipe": P.PLAN_RECIPES[tier], "memory": "on", "compare": "individual:organization:nation"}
    return b, calls, meta


def replies_of(out):
    return [json.loads(x) for x in (Path(out) / "replies.jsonl").read_text().splitlines()]


def write_replies(out, recs):
    (Path(out) / "replies.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs))


def first_task(calls):
    """The plan's first errand task's calls (both sides of one v4 task, or ordo's three beneficiaries)."""
    first = next(c for c in calls if c.get("episode"))
    return [c for c in calls if c.get("episode") and L.task_of(c) == L.task_of(first)]


def start_running(out, calls, lines):
    """Make the plan's first errand task the one running: its replies dropped, its folders made afresh by
    start_episode, and lines(call) written as its record. Returns the task's calls."""
    task = first_task(calls)
    keys = {P.key(c) for c in task}
    write_replies(out, [r for r in replies_of(out) if P.key(r) not in keys])
    for c in task:
        folder = P.episode_dir(out, c)
        P.start_episode(c, folder)
        (folder / "record.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines(c)))
    return task


def v4_score(out, b, roster, calls, meta):
    """The page's provisional scorer for a v4 run: card_of on a frozen snapshot, private."""
    return lambda snap: P.card_of(out, calls, b, roster, meta, snap["done"], B_TEST, private=True,
                                  facts=snap["facts"])[0]


def ordo_score(out, calls, meta):
    """The page's provisional scorer for a cross-type run: score_units on a frozen snapshot, at card_tier's tier."""
    return lambda snap: O.score_units(O.units_of(calls, snap["done"], out, snap["facts"]), "claude", meta, B_TEST,
                                      O.card_tier(meta))


def snapshot_of(calls, done, out):
    """A frozen snapshot as the observer captures it: the replies, and each committed errand's episode facts."""
    return {"done": dict(done), "facts": {P.key(c): P.episode_facts(c, done[P.key(c)], out) for c in calls
                                          if c.get("episode") and P.key(c) in done}}


def v4_view(out, b, roster, calls, meta, redactor, interval=0, line=None):
    score = v4_score(out, b, roster, calls, meta)
    rows = L.v4_rows
    prov = L.Provisional(score, rows, len(calls), interval=interval)
    view = L.RunView("Mozilla (organization)", out, calls, L.run_facts(meta, "single", roster),
                     L.roster_names(roster=roster), P.COMPONENT_NAMES, redactor, line, prov)
    view.status = "running"
    return view


def wait_for(cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class Served:
    """A Watch on loopback for one test, with requests that set the Host header and token as given."""

    def __init__(self, names=("run",)):
        self.watch = L.Watch(list(names), stream=io.StringIO())
        self.port = self.watch.server.server_address[1]
        self.token = self.watch.server.token

    def get(self, path, token=True, host=None, method="GET"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        sep = "&" if "?" in path else "?"
        url = path + (f"{sep}t={self.token}" if token is True else f"{sep}t={token}" if token else "")
        conn.putrequest(method, url, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{self.port}")
        conn.endheaders()
        res = conn.getresponse()
        body = res.read().decode("utf-8", "replace")
        conn.close()
        return res, body

    def state(self):
        res, body = self.get("/state")
        assert res.status == 200, res.status
        return json.loads(body)

    def close(self):
        self.watch.close()


# ---- the server ----------------------------------------------------------------------------------------------------
class TestServer(unittest.TestCase):
    def setUp(self):
        self.s = Served()

    def tearDown(self):
        self.s.close()

    def test_refusals(self):
        self.assertEqual(self.s.get("/state", token=False)[0].status, 403)
        self.assertEqual(self.s.get("/state", token="wrong" * 6)[0].status, 403)
        self.assertEqual(self.s.get("/state", token=self.s.token[:-1])[0].status, 403)
        self.assertEqual(self.s.get(f"/state?t={self.s.token}&t={self.s.token}", token=False)[0].status, 403)
        for host in ("evil.example", f"evil.example:{self.s.port}", f"127.0.0.1:{self.s.port + 1}", "127.0.0.1"):
            self.assertEqual(self.s.get("/state", host=host)[0].status, 403, host)
        self.assertEqual(self.s.get("/state", host=f"localhost:{self.s.port}")[0].status, 200)
        for path in ("/favicon.ico", "/run.json", "/replies.jsonl", "/../replies.jsonl", "/state/x", "/index.html"):
            self.assertEqual(self.s.get(path)[0].status, 404, path)
        for method in ("POST", "PUT", "DELETE", "HEAD", "OPTIONS"):
            self.assertEqual(self.s.get("/state", method=method)[0].status, 405, method)

    def test_security_headers_on_every_response(self):
        script = re.search(r"<script>(.*?)</script>", L.VIEW_V4, re.S).group(1)
        style = re.search(r"<style>(.*?)</style>", L.VIEW_V4, re.S).group(1)
        digest = lambda t: "'sha256-" + base64.b64encode(hashlib.sha256(t.encode()).digest()).decode() + "'"  # noqa
        for path, token, status in (("/", True, 200), ("/state", True, 200), ("/state", False, 403),
                                    ("/nothing", True, 404)):
            res, body = self.s.get(path, token=token)
            self.assertEqual(res.status, status)
            self.assertEqual(res.getheader("Cache-Control"), "no-store")
            self.assertEqual(res.getheader("Referrer-Policy"), "no-referrer")
            self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff")
            csp = res.getheader("Content-Security-Policy")
            self.assertIn("default-src 'none'", csp)
            self.assertIn(f"script-src {digest(script)};", csp)
            self.assertIn(f"style-src {digest(style)};", csp)
            self.assertIn("connect-src 'self'", csp)
            self.assertNotIn("unsafe", csp)
            self.assertNotIn("Python", res.getheader("Server") or "")
        res, body = self.s.get("/")
        self.assertEqual(res.getheader("Content-Type"), "text/html; charset=utf-8")
        self.assertNotIn(self.s.token, body)                          # the script reads location.search
        self.assertIn("location.search", body)
        self.assertNotIn("innerHTML", L.VIEW_V4)                       # every node is built with textContent

    def test_the_url_is_printed_once_with_the_boundary_sentence(self):
        stream = io.StringIO()
        w = L.Watch(["a"], stream=stream)
        try:
            text = stream.getvalue()
            self.assertEqual(text.count("watch it live: http://127.0.0.1:"), 1)
            self.assertIn(L.BOUNDARY, text)
            self.assertIn(f"?t={w.server.token}", text)
        finally:
            w.close()

    def test_the_watch_flag_is_v4s_and_not_the_calibrations(self):
        self.assertTrue(P.wants_v4(["--agent", "claude", "--name", "Jane Doe", "--watch"]))
        self.assertTrue(P.parse_args_v4(["--agent", "claude", "--name", "Jane Doe", "--watch"]).watch)
        self.assertFalse(P.wants_v4(["--agent", "claude", "--name", "Jane Doe", "--watch", "--dataset", "all"]))
        tree = ast.parse((REPO / "calibration" / "run.py").read_text())
        allowed = next(ast.literal_eval(n.value) for n in ast.walk(tree) if isinstance(n, ast.Assign)
                       and any(getattr(t, "id", None) == "ALLOWED_ARGS" for t in n.targets))
        self.assertNotIn("--watch", allowed)


# ---- what the observer opens, and canaries (4.3) ---------------------------------------------------------------------
def errand_lines(canary=None, filed=None):
    """An errand's record so far: tools/list, a statement page, a tool call carrying the canary, and a filed claim."""
    lines = [{"kind": "rpc", "method": "tools/list"},
             {"kind": "call", "n": 1, "tool": "card_statement", "args": {"page": 1},
              "response": {"text": json.dumps({"page": 1, "of": 3}), "is_error": False}}]
    if canary:
        lines.append({"kind": "call", "n": 2, "tool": "send_message", "args": {"to": "ops", "body": f"note {canary}"},
                      "response": {"text": "sent", "is_error": False}})
    if filed:
        lines.append({"kind": "call", "n": len(lines), "tool": "file", "args": {"ids": [f"TX-1 {filed}"]},
                      "response": {"text": "Filed.", "is_error": False}})
    return [dict(x, seq=i + 1) for i, x in enumerate(lines)]


class TestPrivacy(unittest.TestCase):
    def plant(self, d, canaries):
        """A v4 folder with the canaries placed in a reply, a call error, a tool argument, a filed result and a
        report; and the memory canary in every file the observer must never open."""
        out = Path(d) / "run"
        b, roster, calls, meta = v4_run(out)
        task = start_running(out, calls, lambda c: errand_lines(" ".join(canaries), " ".join(canaries)))
        for c in task:
            (P.episode_dir(out, c) / "followup.json").write_text(json.dumps({"reply": "1. NO\n2. REAL\n" +
                                                                             " ".join(canaries)}))
        recs = replies_of(out)
        qs = [i for i, r in enumerate(recs) if r["dataset"] == "v4.I"]
        recs[qs[-1]]["reply"] = "My answer is A. " + " ".join(canaries)          # the newest questions are shown
        recs[qs[-2]].update(error="exit 1: " + " ".join(canaries), reply=None, outcome=None)
        write_replies(out, recs)
        return out, b, roster, calls, meta

    def private_files(self, d, out, canary):
        mem = Path(d) / "home" / "CLAUDE.md"
        mem.parent.mkdir(parents=True)
        mem.write_text("memory: " + canary)
        meta = json.loads((out / "run.json").read_text())
        meta["memory_files"] = {str(mem): hashlib.sha256(mem.read_bytes()).hexdigest()}
        (out / "run.json").write_text(json.dumps(meta))
        (out / "quota.jsonl").write_text(json.dumps({"call": canary, "rate_limits": []}) + "\n")
        home = out / "codex-homes" / "h"
        home.mkdir(parents=True)
        (home / "auth.json").write_text(json.dumps({"tokens": {"access_token": canary}}))
        (out / "launches.jsonl").write_text(json.dumps({"started": canary}) + "\n")
        (out / "run.lock").write_text(canary)
        for folder in (out / "episodes").iterdir():
            (folder / "transcript.jsonl").write_text(json.dumps({"type": "system", "text": canary}) + "\n")

    def test_canaries_never_reach_the_page(self):
        secret_var = "s3cr3t-" + secrets.token_hex(6)
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"PROBATIO_TEST_API_KEY": secret_var}):
            s = Served()
            try:
                canaries = [hf_canary(), ant_canary(), s.token, secret_var]
                out, b, roster, calls, meta = self.plant(d, canaries)
                plain = memory_canary()
                self.private_files(d, out, plain)
                view = s.watch.start("Mozilla (organization)", out, calls, L.run_facts(meta, "single", roster),
                                     L.roster_names(roster=roster), P.COMPONENT_NAMES)
                state_text = s.get("/state")[1]                     # reads every record and report
                view.cards.clear()
                view.last_refresh = None
                with mock.patch.object(L, "read_followup", side_effect=ValueError("boom " + " ".join(canaries))):
                    failed_text = s.get("/state")[1]                # a forced observer exception
                page = s.get("/")[1]
                for text in (state_text, failed_text, page):        # every canary is absent from both routes
                    for canary in canaries + [plain]:
                        self.assertNotIn(canary, text)
                state = json.loads(state_text)["runs"][0]
                card = state["errands"]["cards"][0]
                # the marker, only where the raw text is kept: a tool argument, a filed result, a reply, an error
                self.assertIn("<redacted>", " ".join(x["args"] for x in card["calls"]))
                self.assertIn("<redacted>", card["filed"])
                self.assertIn("<redacted>", state["questions"][0]["reply"])
                self.assertIn("<redacted>", state["questions"][1]["error"])
                # a report shows its parsed words alone, so its canary leaves no marker
                self.assertEqual(card["report"], {"reviewed": "NO", "real": "REAL"})
                # a failure shows its fixed sentence alone: no message, path or traceback
                problems = json.loads(failed_text)["runs"][0]["problems"]
                self.assertTrue(problems)
                self.assertTrue(all(re.fullmatch(r"could not read E\S+ yet \(ValueError\)", x) for x in problems),
                                problems)
                for leak in ("boom", "Traceback", str(out), "followup.json"):
                    self.assertNotIn(leak, failed_text)
                # a plain canary the agent echoed in a reply is shown: echoes show, by design
                recs = replies_of(out)
                qs = [r for r in recs if r["dataset"] == "v4.I" and not r["error"]]
                recs.append(dict(qs[0], reply="I remember: " + plain))
                write_replies(out, recs)
                view.last_refresh = None
                self.assertIn(plain, s.get("/state")[1])
            finally:
                s.close()

    def test_the_observer_opens_only_the_replies_and_the_shown_errands(self):
        with tempfile.TemporaryDirectory() as d:
            out, b, roster, calls, meta = self.plant(d, [hf_canary()])
            self.private_files(d, out, memory_canary())
            redactor = L.ViewRedactor()
            warm = v4_view(out, b, roster, calls, meta, redactor)
            warm.state()
            self.assertTrue(wait_for(lambda: warm.provisional.latest() is not None))
            view = v4_view(out, b, roster, calls, meta, redactor)
            final = P.score_run(out, calls, b, roster, meta, B=B_TEST)
            OPENED.clear()
            RECORDING.set()
            try:
                view.state()
                self.assertTrue(wait_for(lambda: view.provisional.latest() is not None))
                view.last_refresh = None
                view.state()
                view.provisional.finalize(final)                  # the final card arrives in memory
                view.state()
            finally:
                RECORDING.clear()
            self.assertTrue(OPENED)
            self.assertEqual([p for p, thread in OPENED if thread == "probatio-provisional"], [])   # no disk read
            self.assertTrue(view.provisional.latest()["final"])
            root = str(out.resolve())
            for path, _ in OPENED:
                full = str(Path(path).resolve())
                self.assertTrue(full.startswith(root + os.sep), path)
                rel = Path(full).relative_to(root)
                ok = rel == Path("replies.jsonl") or (rel.parts[0] == "episodes" and len(rel.parts) == 3
                                                      and rel.name in ("record.jsonl", "followup.json"))
                self.assertTrue(ok, rel)
            names = {Path(p).name for p, _ in OPENED}
            self.assertEqual(names, {"replies.jsonl", "record.jsonl", "followup.json"})

    def test_a_full_cycle_never_calls_read_replies(self):
        with tempfile.TemporaryDirectory() as d:
            out, b, roster, calls, meta = self.plant(d, [hf_canary()])
            size = (out / "replies.jsonl").stat().st_size
            with open(out / "replies.jsonl", "a") as f:
                f.write('{"torn')                                   # read_replies would cut this off
            with mock.patch.object(P, "read_replies", side_effect=AssertionError("read_replies called")):
                view = v4_view(out, b, roster, calls, meta, L.ViewRedactor())
                state = view.state()
                self.assertTrue(wait_for(lambda: view.provisional.latest() is not None))
            self.assertIsNone(view.provisional.latest().get("problem"))
            self.assertEqual((out / "replies.jsonl").stat().st_size, size + len('{"torn'))
            self.assertEqual(state["problems"], [])

    def test_a_file_replaced_after_capture_does_not_change_the_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run"
            b, roster, calls, meta = v4_run(out, tier="standard")
            done = P.read_replies(out / "replies.jsonl")
            snap = snapshot_of(calls, done, out)
            before = v4_score(out, b, roster, calls, meta)(snap)
            for c in calls:                                  # every errand's record and report replaced afterwards
                if c.get("episode"):
                    folder = P.episode_dir(out, c)
                    (folder / "record.jsonl").write_text(json.dumps({"kind": "rpc", "seq": 1}) + "\n")
                    (folder / "followup.json").write_text("{}")
            after = v4_score(out, b, roster, calls, meta)(snap)
            self.assertEqual(before["conditions"], after["conditions"])
            frozen = P.errand_units(calls, done, out, snap["facts"])
            afresh = P.errand_units(calls, done, out)                  # read now, the replaced files would count
            self.assertTrue(any(u["valid"] for u in frozen))
            self.assertFalse(any(u["valid"] for u in afresh))
            out2 = Path(d) / "ordo"
            b2, calls2, meta2 = ordo_run(out2)
            done2 = P.read_replies(out2 / "replies.jsonl")
            snap2 = snapshot_of(calls2, done2, out2)
            first = ordo_score(out2, calls2, meta2)(snap2)
            for c in calls2:
                if c.get("episode"):
                    (P.episode_dir(out2, c) / "record.jsonl").write_text("")
            self.assertEqual(first["conditions"], ordo_score(out2, calls2, meta2)(snap2)["conditions"])

    def test_after_a_run_the_token_is_in_no_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run"
            b, roster, calls, meta = v4_run(out)
            s = Served()
            try:
                view = s.watch.start("r", out, calls, L.run_facts(meta, "single", roster), {}, P.COMPONENT_NAMES)
                s.state()
                s.watch.finish(view, P.score_run(out, calls, b, roster, meta, B=B_TEST))
                s.state()
                token = s.token.encode()
                for f in out.rglob("*"):
                    if f.is_file():
                        self.assertNotIn(token, f.read_bytes(), f)
            finally:
                s.close()


class TestRedactor(unittest.TestCase):
    def values(self):
        return {"ANT": ant_canary(), "HF": hf_canary(), "SECRET1": "value-" + "x" * 12, "SECRET2": "value-" + "x" * 30,
                "SHORT": "abc"}

    def fill(self, x, v):
        if isinstance(x, str):
            for k, val in v.items():
                x = x.replace("{" + k + "}", val)
            return x
        if isinstance(x, dict):
            return {k: self.fill(val, v) for k, val in x.items()}
        if isinstance(x, list):
            return [self.fill(val, v) for val in x]
        return x

    def test_one_contract_with_calibration_redact(self):
        spec = importlib.util.spec_from_file_location("calibration_redact", REPO / "calibration" / "redact.py")
        cal = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cal)
        cases = json.loads(REDACT_CASES.read_text())["cases"]
        self.assertGreaterEqual(len(cases), 8)
        v = self.values()
        for case in cases:
            c = self.fill(case, v)
            mine, theirs = L.ViewRedactor(c["environ"], c["extra"]), cal.Redactor(c["environ"], c["extra"])
            if "clip" in c:
                got = (mine.clip(c["text"], c["clip"]), theirs.clip(c["text"], c["clip"]))
            else:
                got = (mine(c["text"]), theirs(c["text"]))
            self.assertEqual(got, (c["out"], c["out"]), case["name"])
        self.assertEqual(L.MARK, cal.MARK)
        self.assertEqual(L.SECRET_NAME.pattern, cal.SECRET_NAME.pattern)
        self.assertEqual(L.MIN_SECRET, cal.MIN_LEN)

    def test_the_pages_own_shapes_and_controls(self):
        r = L.ViewRedactor({}, ())
        shapes = ["sk-" + "a1" * 12, "hf_" + "Ab" * 16, "ghp_" + "x" * 36, "github_pat_" + "y" * 30,
                  "xoxb-" + "1234-" * 4, "AKIA" + "ABCDEFGHIJKLMNOP",
                  "eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 10,
                  # The PEM headers are built at run time, so secret scanners never see a key-shaped literal.
                  "-----BEGIN " + "RSA PRIVATE KEY" + "-----\nMIIx\n-----END " + "RSA PRIVATE KEY" + "-----",
                  "-----BEGIN " + "PRIVATE KEY" + "-----\ncut off"]
        for s in shapes[:-1]:
            self.assertEqual(r(f"a {s} b"), "a <redacted> b", s)
        self.assertEqual(r(f"a {shapes[-1]}"), "a <redacted>")          # a key block cut off before its end
        split = "hf_" + "FAKE" * 4 + "​" + "FAKE" * 4                 # a zero-width space cannot split a key
        self.assertEqual(r(split), "<redacted>")
        self.assertEqual(r("a\x1b[31mb\x07\tc\nd"), "a[31mb\tc\nd")
        self.assertEqual(r.brief("x" * 10, 5), "xxxx…")
        long = "y" * 280 + " " + hf_canary()                               # the key straddles the 300 cap
        self.assertEqual(r.brief(long, 300), "y" * 280 + " <redacted>")   # redacted whole before it is cut


# ---- the reader (4.5) -----------------------------------------------------------------------------------------------
class TestReader(unittest.TestCase):
    def reply(self, i, **kw):
        return dict({"condition": "with", "dataset": "v4.I", "id": f"q{i}", "error": None, "seconds": 1.0}, **kw)

    def test_byte_by_byte_appends(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "replies.jsonl"
            path.write_bytes(b"")
            data = "".join(json.dumps(self.reply(i, reply="é東" * i), ensure_ascii=False) + "\n" for i in range(4))
            r, got = L.CommittedLines(path, L.REPLY_FIELDS), []
            raw = data.encode("utf-8")
            for i in range(len(raw)):
                with open(path, "ab") as f:
                    f.write(raw[i:i + 1])
                reset, new = r.read()
                self.assertFalse(reset)
                got += new
                if raw[i:i + 1] == b"\n":
                    self.assertEqual(new[-1]["id"], f"q{len(got) - 1}")       # it appears when its newline lands
                else:
                    self.assertEqual(new, [])
            self.assertEqual([g["id"] for g in got], ["q0", "q1", "q2", "q3"])
            self.assertEqual(r.unreadable, 0)

    def test_a_complete_tail_without_a_newline_waits(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "replies.jsonl"
            path.write_text(json.dumps(self.reply(0)) + "\n" + json.dumps(self.reply(1)))
            r = L.CommittedLines(path, L.REPLY_FIELDS)
            self.assertEqual([x["id"] for x in r.read()[1]], ["q0"])
            with open(path, "a") as f:
                f.write("\n")
            self.assertEqual([x["id"] for x in r.read()[1]], ["q1"])

    def test_bad_lines_are_counted_never_shown(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "replies.jsonl"
            bad = ["null", "[1, 2]", "{}", json.dumps({"condition": "with", "dataset": "v4.I", "id": "x"}),
                   json.dumps(self.reply(9, seconds=True)), json.dumps(self.reply(8, error=5)), "{torn", "[" * 5000]
            path.write_bytes(("\n".join(bad) + "\n").encode() + b"\xff\xfe\n"
                             + (json.dumps(self.reply(1)) + "\n").encode())
            r = L.CommittedLines(path, L.REPLY_FIELDS)
            self.assertEqual([x["id"] for x in r.read()[1]], ["q1"])
            self.assertEqual(r.unreadable, len(bad) + 1)

    def test_split_utf8_is_whole_when_decoded(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "replies.jsonl"
            raw = (json.dumps(self.reply(0, reply="東京"), ensure_ascii=False) + "\n").encode()
            cut = raw.index("京".encode()) + 1
            path.write_bytes(raw[:cut])
            r = L.CommittedLines(path, L.REPLY_FIELDS)
            self.assertEqual(r.read()[1], [])
            with open(path, "ab") as f:
                f.write(raw[cut:])
            self.assertEqual(r.read()[1][0]["reply"], "東京")

    def test_replacement_and_truncation_reset(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "replies.jsonl"
            lines = [json.dumps(self.reply(i)) + "\n" for i in range(3)]
            path.write_text("".join(lines) + '{"torn')
            r = L.CommittedLines(path, L.REPLY_FIELDS)
            self.assertEqual(len(r.read()[1]), 3)
            with open(path, "r+b") as f:                       # read_replies's cut of the torn tail: no reset
                f.truncate(len("".join(lines)))
            with open(path, "a") as f:
                f.write(json.dumps(self.reply(3)) + "\n")
            self.assertEqual(r.read(), (False, [self.reply(3)]))
            with open(path, "r+b") as f:                       # shorter than what was read: a reset
                f.truncate(len(lines[0]))
            reset, new = r.read()
            self.assertTrue(reset)
            self.assertEqual([x["id"] for x in new], ["q0"])
            tmp = Path(d) / "new"
            tmp.write_text(lines[2] + lines[1])
            os.replace(tmp, path)                              # a replacement: a new inode
            reset, new = r.read()
            self.assertTrue(reset)
            self.assertEqual([x["id"] for x in new], ["q2", "q1"])
            path.write_text("x" * 400 + "\n")                  # rewritten in place, longer: no newline at the offset
            reset, new = r.read()
            self.assertEqual((reset, new, r.unreadable), (True, [], 1))
            os.remove(path)
            self.assertEqual(r.read(), (True, []))
            self.assertEqual(r.read(), (False, []))

    def test_a_torn_tail_cut_and_regrown_past_the_old_offset(self):
        """Codex's probe: BASE, then an unterminated OLD prefix; the runner cuts the torn tail and appends a whole NEW
        on the same inode, past the old end. The reader sees BASE then NEW, never OLD."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "replies.jsonl"
            base = json.dumps(self.reply(0)) + "\n"
            old = json.dumps(self.reply(1, reply="OLD"))
            new = json.dumps(self.reply(2, reply="NEW " + "x" * 80)) + "\n"
            path.write_text(base + old[:-5])
            r = L.CommittedLines(path, L.REPLY_FIELDS)
            self.assertEqual([x["id"] for x in r.read()[1]], ["q0"])
            ino = path.stat().st_ino
            with open(path, "r+b") as f:
                f.truncate(len(base))
            with open(path, "a") as f:
                f.write(new)
            self.assertEqual(path.stat().st_ino, ino)
            self.assertGreater(path.stat().st_size, len(base) + len(old) - 5)
            reset, got = r.read()
            self.assertEqual((reset, [x["id"] for x in got]), (False, ["q2"]))

    def test_committed_bytes_rewritten_in_place_reset_the_reader(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "replies.jsonl"
            lines = [json.dumps(self.reply(i)) + "\n" for i in range(3)]
            path.write_text(lines[0] + lines[1])
            r = L.CommittedLines(path, L.REPLY_FIELDS)
            self.assertEqual(len(r.read()[1]), 2)
            ino = path.stat().st_ino
            with open(path, "r+b") as f:                       # same inode: cut below the offset and regrown past it
                f.truncate(len(lines[0]))
            with open(path, "a") as f:
                f.write(lines[2] + lines[1])                   # lines of one length: a newline sits at the old offset
            self.assertEqual(path.stat().st_ino, ino)
            reset, got = r.read()
            self.assertTrue(reset)
            self.assertEqual([x["id"] for x in got], ["q0", "q2", "q1"])

    def test_a_retried_episode_starts_its_card_over(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run"
            b, roster, calls, meta = v4_run(out)
            task = start_running(out, calls, lambda c: errand_lines("first-attempt"))
            view = v4_view(out, b, roster, calls, meta, L.ViewRedactor())
            card = view.state()["errands"]["cards"][0]
            self.assertEqual((card["status"], card["attempt"], card["tool_calls"]), ("working", 1, 2))
            P.start_episode(task[0], P.episode_dir(out, task[0]))         # the retry: a fresh folder and world
            (P.episode_dir(out, task[0]) / "record.jsonl").write_text(
                json.dumps({"kind": "rpc", "method": "tools/list", "seq": 1}) + "\n")
            view.last_refresh = None
            card = view.state()["errands"]["cards"][0]
            self.assertEqual((card["attempt"], card["tool_calls"]), (2, 0))
            self.assertNotIn("first-attempt", json.dumps(card))

    def test_a_codex_report_that_lands_late_in_two_halves(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run"
            b, roster, calls, meta = v4_run(out, agent="codex")
            task = start_running(out, calls, lambda c: errand_lines(filed="x"))
            folder = P.episode_dir(out, task[0])
            recs = replies_of(out)
            recs.append(dict({k: task[0][k] for k in ("condition", "dataset", "config", "id", "prompt")},
                             reply="done", answer=None, outcome={"solved": 0, "filed": True, "calls": 1},
                             error=None, model="m", seconds=30.0))
            write_replies(out, recs)
            view = v4_view(out, b, roster, calls, meta, L.ViewRedactor())
            card = lambda: next(c for c in view.state()["errands"]["cards"] if c["status"] == "done")   # noqa: E731
            self.assertEqual((card()["report"], card()["report_state"]), (None, "none"))      # no report yet
            body = json.dumps({"prompt": "q", "reply": "1. YES\n2. TEST\n3. NOBODY", "error": None})
            (folder / "followup.json").write_text(body[:len(body) // 2])
            view.last_refresh = None
            self.assertEqual(card()["report_state"], "not ready")
            (folder / "followup.json").write_text(body)
            view.last_refresh = None
            self.assertEqual(card()["report"], {"reviewed": "YES", "real": "TEST"})


# ---- the provisional scorecard (4.4) ---------------------------------------------------------------------------------
class TestProvisional(unittest.TestCase):
    def test_v4_scores_the_full_plan_so_missing_calls_count(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run"
            b, roster, calls, meta = v4_run(out, tier="standard")
            done = P.read_replies(out / "replies.jsonl")
            cut = {}
            for c in calls:                                  # half of I.1's calls on each side have come back
                if c["meta"].get("component") == "I.1":
                    side = c["meta"]["side"]
                    cut[side] = cut.get(side, 0) + 1
                    if cut[side] % 2:
                        done.pop(P.key(c))
            subset = [c for c in calls if P.key(c) in done]
            full = P.card_of(out, calls, b, roster, meta, done, B_TEST, private=True)[0]
            part = P.card_of(out, subset, b, roster, meta, done, B_TEST, private=True)[0]
            missing = lambda card: [m["id"] for m in card["conditions"]["with"]["classes"]["I"]["missing"]]  # noqa
            self.assertIn("I.1", missing(full))                  # 15 of 30 planned: below two thirds
            self.assertNotIn("I.1", missing(part))               # the subset alone would look sufficient
            self.assertEqual(full["item_fingerprint"], P.item_fingerprint(calls))
            self.assertEqual(full["bootstrap"]["seed_hex"], P.bootstrap_seed(P.item_fingerprint(calls)))

    def test_ordo_scores_the_full_plan_6_of_12_per_order(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run"
            b, calls, meta = ordo_run(out)
            done = P.read_replies(out / "replies.jsonl")
            pair = ("individual", "organization")
            seen = {}
            for c in calls:
                m = c["meta"]
                if m.get("component") == "F1.a" and m.get("pair") == "individual:organization":
                    seen[m["listed_first"]] = seen.get(m["listed_first"], 0) + 1
                    if seen[m["listed_first"]] % 2:
                        done.pop(P.key(c))
            pick = lambda units: [u for u in units if u.get("component") == "F1.a"           # noqa: E731
                                  and u.get("pair") == "individual:organization"]
            full = O.pair_share(pick(O.units_of(calls, done, out)), pair)
            subset = [c for c in calls if P.key(c) in done]
            part = O.pair_share(pick(O.units_of(subset, done, out)), pair)
            self.assertFalse(full["sufficient"])
            self.assertIn("(6 of 12)", full["reason"])
            self.assertTrue(part["sufficient"], part["reason"])
            units = O.units_of(calls, done, out)
            self.assertEqual(O.units_fingerprint(units), O.units_fingerprint(O.units_of(calls, {}, out)))

    def test_with_every_record_the_provisional_rows_are_the_final_ones(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run"
            b, roster, calls, meta = v4_run(out, tier="quick", memory="both")
            done = P.read_replies(out / "replies.jsonl")
            prov = v4_score(out, b, roster, calls, meta)(snapshot_of(calls, done, out))
            final = P.score_run(out, calls, b, roster, meta, B=B_TEST)
            for cond in ("with", "without"):
                self.assertEqual(prov["conditions"][cond]["classes"], final["conditions"][cond]["classes"])
            self.assertEqual(prov["difference"], final["difference"])
            self.assertEqual(L.v4_rows(prov, True), L.v4_rows(final, True))
            self.assertEqual([r["group"] for r in L.v4_rows(prov, False) if "line" not in r][::3],
                             ["memory off", "memory on"])
            out2 = Path(d) / "ordo"
            b2, calls2, meta2 = ordo_run(out2)
            done2 = P.read_replies(out2 / "replies.jsonl")
            prov2 = ordo_score(out2, calls2, meta2)(snapshot_of(calls2, done2, out2))
            final2 = O.score(out2, calls2, b2, agent="claude", meta=meta2, B=B_TEST)
            rows = L.ordo_rows(O)
            self.assertEqual(rows(prov2, True), rows(final2, True))
            self.assertTrue(any(r["value"] is not None for r in rows(final2, True)))

    def test_one_worker_coalesces_and_the_final_card_wins(self):
        running, most, gate, started = [0], [0], threading.Event(), threading.Event()
        lock = threading.Lock()

        def score(snap):
            with lock:
                running[0] += 1
                most[0] = max(most[0], running[0])
            started.set()
            gate.wait(5)
            with lock:
                running[0] -= 1
            return {"n": len(snap["done"])}
        rows = lambda card, final: [{"n": card["n"], "final": final}]            # noqa: E731
        p = L.Provisional(score, rows, 100, interval=0)
        plan = {("with", "d", str(i)): None for i in range(100)}
        threads = [threading.Thread(target=p.trigger, args=(i, {"done": {("with", "d", str(j)): 1 for j in range(i)},
                                                                "facts": {}}, plan)) for i in range(1, 60)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(started.wait(5))
        p.finalize({"n": 100})                                 # forced while a scoring is still running
        gate.set()
        self.assertTrue(wait_for(lambda: running[0] == 0))
        time.sleep(0.05)
        self.assertEqual(most[0], 1)
        self.assertLess(p.scorings, 59)
        self.assertEqual(p.latest()["rows"], [{"n": 100, "final": True}])
        self.assertTrue(p.latest()["final"])
        p.trigger(99, {"done": {}, "facts": {}}, plan)          # after the final card, nothing more is scored
        time.sleep(0.05)
        self.assertTrue(p.latest()["final"])

    def test_the_throttle_and_a_failing_scorer(self):
        t = {"now": 0.0}
        calls = []
        p = L.Provisional(lambda snap: calls.append(len(snap["done"])) or {"n": len(snap["done"])},
                          lambda c, f: [c], 10, interval=30, clock=lambda: t["now"])
        p.trigger(1, {"done": {"a": 1}, "facts": {}}, {"a": 0})
        self.assertTrue(wait_for(lambda: p.latest() is not None))
        p.trigger(2, {"done": {"a": 1, "b": 1}, "facts": {}}, {"a": 0, "b": 0})
        time.sleep(0.1)
        self.assertEqual(calls, [1])                            # within 30 s of the last start: held
        t["now"] = 31.0
        with p.cond:
            p.cond.notify_all()
        self.assertTrue(wait_for(lambda: p.latest()["snapshot_id"] == 2))
        self.assertEqual(p.latest()["done"], 2)
        bad = L.Provisional(lambda snap: 1 / 0, lambda c, f: [c], 10, interval=0)
        bad.trigger(1, {"done": {}, "facts": {}}, {})
        self.assertTrue(wait_for(lambda: bad.latest() is not None))
        self.assertEqual(bad.latest()["problem"],
                         "the provisional scorecard could not be computed yet (ZeroDivisionError)")


# ---- planted folders end to end --------------------------------------------------------------------------------------
class TestPlanted(unittest.TestCase):
    def test_single_type_cross_type_and_codex_folders(self):
        with tempfile.TemporaryDirectory() as d:
            s = Served(names=["Mozilla (organization)", "individual:organization:nation", "codex"])
            try:
                out = Path(d) / "single"
                b, roster, calls, meta = v4_run(out)
                start_running(out, calls, lambda c: errand_lines(filed="TX-1"))
                line = L.LiveLine(4, lambda c: 5.0, stream=io.StringIO())
                line.begin(calls, P.read_replies(out / "replies.jsonl"))
                v1 = s.watch.start("Mozilla (organization)", out, calls, L.run_facts(meta, "single", roster),
                                   L.roster_names(roster=roster), P.COMPONENT_NAMES, line,
                                   v4_score(out, b, roster, calls, meta),
                                   L.v4_rows)
                state = s.state()
                one, waiting = state["runs"][0], state["runs"][1]
                self.assertEqual(waiting, {"name": "individual:organization:nation", "status": "waiting"})
                self.assertEqual(one["facts"]["target"], roster["T"]["full"])
                self.assertEqual(len(one["errands"]["cards"]), 2)
                self.assertEqual({c["role"] for c in one["errands"]["cards"]}, {"target", "control"})
                self.assertTrue(one["questions"] and len(one["questions"]) <= L.QUESTIONS_SHOWN)
                q = one["questions"][0]
                self.assertTrue(set(q) == {"time", "component", "options", "answer", "reply", "error"})
                self.assertTrue(wait_for(lambda: s.state()["runs"][0]["scorecard"] is not None))
                card = s.state()["runs"][0]["scorecard"]
                self.assertFalse(card["final"])
                self.assertEqual(card["total"], len(calls))
                self.assertLess(card["done"], len(calls))
                line.close()
                s.watch.finish(v1, P.score_run(out, calls, b, roster, meta, B=B_TEST))
                self.assertTrue(s.state()["runs"][0]["scorecard"]["final"])
                self.assertEqual(s.state()["runs"][0]["status"], "finished")

                out2 = Path(d) / "cross"
                b2, calls2, meta2 = ordo_run(out2)
                task2 = start_running(out2, calls2, lambda c: errand_lines())
                s.watch.start("individual:organization:nation", out2, calls2, L.run_facts(meta2, "cross"), {},
                              O.COMPONENT_NAMES, None, ordo_score(out2, calls2, meta2), L.ordo_rows(O))
                two = s.state()["runs"][1]
                self.assertEqual(two["facts"]["compare"], "individual:organization:nation")
                self.assertEqual(len(two["errands"]["cards"]), len(task2))
                self.assertEqual({c["role"] for c in two["errands"]["cards"]},
                                 {c["meta"]["beneficiary"] for c in task2})
                self.assertTrue(wait_for(lambda: s.state()["runs"][1]["scorecard"] is not None))
                rows = s.state()["runs"][1]["scorecard"]["rows"]
                self.assertTrue(rows and all(r.get("midline") for r in rows))

                out3 = Path(d) / "codex"
                b3, roster3, calls3, meta3 = v4_run(out3, agent="codex")
                (out3 / "codex-homes" / "h").mkdir(parents=True)
                s.watch.start("codex", out3, calls3, L.run_facts(meta3, "single", roster3),
                              L.roster_names(roster=roster3), P.COMPONENT_NAMES)
                three = s.state()["runs"][2]
                self.assertEqual(three["facts"]["agent"], "Codex")
                self.assertIsNone(three["scorecard"])
                self.assertEqual(three["progress"]["done"], len(calls3))
                for r in s.state()["runs"]:                                   # the whitelist, and nothing else
                    self.assertEqual(set(r), {"name", "status", "facts", "progress", "scorecard", "errands",
                                              "questions", "unreadable", "problems"})
                    self.assertLessEqual(set(r["facts"]), {"agent", "cli", "model", "effort", "tier", "recipe",
                                                           "memory", "kind", "target", "target_type", "control",
                                                           "compare"})
            finally:
                s.close()


class TestCaptureRetry(unittest.TestCase):
    """A committed errand whose episode facts cannot be read at one refresh is queued and read again at the next
    (Codex's final check of the round, finding 4): until then it is out of the snapshot, done and facts alike; then
    its record and facts enter together, as a new snapshot, and its card, the progress and the provisional card count
    it."""

    def plant(self, d):
        out = Path(d) / "run"
        b, roster, calls, meta = v4_run(out)
        task = first_task(calls)
        for c in task:                                       # the task's worlds, so the page shows its cards
            (P.episode_dir(out, c) / "world.json").write_text(c["world"])
        return out, b, roster, calls, meta, task

    def test_one_failed_refresh_then_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            out, b, roster, calls, meta, task = self.plant(d)
            chosen, real = task[0], P.episode_facts
            failing = lambda call, rec, o: (_ for _ in ()).throw(OSError("transient " + str(o)))   # noqa: E731
            pick = lambda call, rec, o: (failing if P.key(call) == P.key(chosen) else real)(call, rec, o)  # noqa
            view = v4_view(out, b, roster, calls, meta, L.ViewRedactor({}))
            with mock.patch.object(L, "episode_facts", side_effect=pick):
                first = view.state()
                self.assertTrue(wait_for(lambda: view.provisional.latest() is not None))
            card = lambda state: next(c for c in state["errands"]["cards"] if c["for"] == chosen["for"])  # noqa
            self.assertEqual(first["progress"]["done"], len(calls) - 1)
            self.assertEqual(card(first)["status"], "working")
            self.assertEqual(view.provisional.latest()["done"], len(calls) - 1)
            self.assertEqual(first["problems"], [f"could not read {chosen['id']} yet (OSError)"])
            self.assertNotIn("transient", json.dumps(first))
            self.assertIn(P.key(chosen), view.retry)
            before = view.snapshot_id
            view.last_refresh = None
            second = view.state()                            # the read works again
            self.assertEqual(second["progress"]["done"], len(calls))
            self.assertEqual(card(second)["status"], "done")
            self.assertEqual((second["problems"], view.retry), ([], {}))
            self.assertGreater(view.snapshot_id, before)
            self.assertEqual(view.facts_of[P.key(chosen)], real(chosen, view.done[P.key(chosen)], out))
            self.assertTrue(wait_for(lambda: view.provisional.latest()["done"] == len(calls)))

    def test_a_queued_capture_of_a_replaced_attempt_is_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            out, b, roster, calls, meta, task = self.plant(d)
            chosen = task[0]
            view = v4_view(out, b, roster, calls, meta, L.ViewRedactor({}))
            with mock.patch.object(L, "episode_facts", side_effect=OSError("transient")):
                view.state()
            self.assertIn(P.key(chosen), view.retry)
            P.start_episode(chosen, P.episode_dir(out, chosen))         # another attempt now holds the folder
            view.last_refresh = None
            state = view.state()
            self.assertNotIn(P.key(chosen), view.retry)
            self.assertNotIn(P.key(chosen), view.done)                  # its own record will come with its line
            self.assertFalse(any(chosen["id"] in x for x in state["problems"]))


class TestQuick(unittest.TestCase):
    """A new quick run (recipe quick@2) on the page reads as its scorecard does: the tier's own line first, and no
    Class III (single-type) or authority and Class III rows (cross-type), in the scorecard's words."""

    def test_single_type(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run"
            b, roster, calls, meta = v4_run(out, tier="quick")
            self.assertEqual(meta["recipe"], "quick@2")
            self.assertFalse(any(c["meta"]["class"] == "III" for c in calls))
            final = P.score_run(out, calls, b, roster, meta, B=B_TEST)
            prov = v4_score(out, b, roster, calls, meta)(snapshot_of(calls, P.read_replies(out / "replies.jsonl"), out))
            for card, is_final in ((final, True), (prov, False)):
                rows = L.v4_rows(card, is_final)
                self.assertEqual(rows[0], {"line": P.QUICK_CARD_LINE})
                three = next(r for r in rows if r.get("label") == "Class III")
                self.assertEqual((three["note"], three["value"], three["ci"]), (P.NOT_RUN_IN.format(tier="quick"), None,
                                                                               None))
            md = (out / "scorecard.md").read_text()
            self.assertIn(P.QUICK_CARD_LINE, md)
            self.assertIn("Class III internalised  " + P.NOT_RUN_IN.format(tier="quick") + "\n", md)
            standard = Path(d) / "standard"
            b2, roster2, calls2, meta2 = v4_run(standard, tier="standard")
            rows = L.v4_rows(P.score_run(standard, calls2, b2, roster2, meta2, B=B_TEST), True)
            self.assertNotIn("line", rows[0])                               # a tier that runs every class: no line

    def test_triangle(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run"
            b, calls, meta = ordo_run(out, tier="quick")
            card = O.score(out, calls, b, agent="claude", meta=meta, B=B_TEST)
            self.assertEqual(card["tier"], "quick")
            for c, is_final in ((card, True), (ordo_score(out, calls, meta)(snapshot_of(calls, P.read_replies(
                    out / "replies.jsonl"), out)), False)):
                rows = L.ordo_rows(O)(c, is_final)
                self.assertEqual(rows[0], {"line": O.QUICK_CARD_LINE})
                groups = {r["group"] for r in rows[1:]}
                self.assertEqual(groups, {"role-free, Class I", "role-free, Class II"})
            self.assertIn(O.QUICK_CARD_LINE, card["head"])


class TestMain(unittest.TestCase):
    """--watch through main: the URL before the first call, the live line from run_calls, the final card on the page
    once scored, and the page held up afterwards (hold_page, patched here to hand the page back)."""

    def run_main(self, args, fake):
        version = mock.Mock(returncode=0, stdout="9.9.9 (fake)\n", stderr="")
        held, err, printed = [], io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(P.shutil, "which", return_value="/bin/claude"), \
                mock.patch.object(P.subprocess, "run", return_value=version), \
                mock.patch.object(P, "run_calls", side_effect=fake), \
                mock.patch.object(P, "hold_page", side_effect=held.append), \
                mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": d}), \
                mock.patch("sys.stderr", err), mock.patch("sys.stdout", printed):
            P.main(args + ["--out", str(Path(d) / "out"), "--workdir", d, "--watch"])
            self.assertEqual(len(held), 1)
            watch = held[0]
            try:
                return watch.state(), err.getvalue(), printed.getvalue()
            finally:
                watch.close()

    def test_a_single_type_run(self):
        seen = []

        def fake(calls, agent, opts, out, jobs, line=None):
            seen.append(line)
            line.begin(calls, {})
            VS.plant(out, calls, VS.favour(0.8, 0.4), VS.errand_favour(0.9, 0.3))
            for c in calls:
                line.finished(c, {"error": None, "outcome": "x", "seconds": 1.0})
            line.close()
        state, err, printed = self.run_main(["--agent", "claude", "--target-type", "organization", "--target",
                                             "Mozilla", "--bank", str(VS.BANK), "--tier", "quick"], fake)
        self.assertIsInstance(seen[0], L.LiveLine)
        self.assertEqual(err.count("watch it live: http://127.0.0.1:"), 1)
        self.assertLess(err.index("watch it live"), err.index(L.PREFIX))      # the URL comes before any progress
        run = state["runs"][0]
        self.assertEqual((run["name"], run["status"]), ("Mozilla (organization)", "finished"))
        self.assertTrue(run["scorecard"]["final"])
        self.assertEqual(run["facts"]["cli"], "9.9.9")
        self.assertIn("Class I   professed", printed)

    def test_a_cross_type_run(self):
        def fake(calls, agent, opts, out, jobs, line=None):
            line.begin(calls, {})
            OS.plant(out, calls, OS.prefer(), lambda m: 0.6)
            line.close()
        state, err, printed = self.run_main(["--agent", "claude", "--compare", "individual:organization",
                                             "--name", "Jane Doe", "--tier", "quick"], fake)
        run = state["runs"][0]
        self.assertEqual((run["name"], run["status"]), ("individual:organization", "finished"))
        self.assertTrue(run["scorecard"]["final"])
        rows = run["scorecard"]["rows"]
        self.assertEqual(rows[0], {"line": O.QUICK_CARD_LINE})                     # a quick run, in its own words
        self.assertTrue(all(r["midline"] for r in rows[1:]))


if __name__ == "__main__":
    unittest.main()
