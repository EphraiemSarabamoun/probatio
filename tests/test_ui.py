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
import ast
import contextlib
import errno
import fcntl
import functools
import hashlib
import http.client
import http.server
import io
import json
import os
import re
import secrets
import select
import shlex
import shutil
import socket
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
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
# --watch, a progress line, any text in STUB_SAY, the result lines when STUB_FINISH is set, in results/<STUB_FOLDER>,
# the run's own folder), writes its pid, and holds until SIGINT, which it answers as probatio does (1, or 0 when it
# held a scored run's page); a finished run without --watch ends with 0, as probatio's does.
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
held = False
if os.environ.get("STUB_FINISH"):
    out = os.path.join(os.getcwd(), "results", os.environ.get("STUB_FOLDER", "stub-run"))
    print("Scorecard: " + os.path.join(out, "scorecard.json"))
    print("Report: " + os.path.join(out, "report.md"))
    if "--watch" not in args:
        sys.exit(0)
    sys.stderr.write("\\nThe live page stays up at " + link + " until you press Ctrl-C.\\n")
    held = True
try:
    while True:
        time.sleep(0.2)
except KeyboardInterrupt:
    if held:
        sys.exit(0)
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


def page_form(**changes):
    """The form the page sends before anything is chosen (its fresh()): every default, no agent, the rows empty,
    the rosters the defaults; changes applied on top."""
    d = U.defaults()
    form = {"types": [d["default"]["type"]], "targets": {t: "" for t in d["types"]},
            "rosters": {t: {p: "\n".join(U.entry_name(e) for e in d["rosters"][t][p]) for p in U.LISTS}
                        for t in d["types"]},
            "agent": d["default"]["agent"], "tier": d["default"]["tier"], "classes": list(d["default"]["classes"]),
            "memory": d["default"]["memory"], "auto_memory": d["default"]["auto_memory"],
            "effort": d["default"]["effort"], "model": "", "watch": d["watch"][0], "jobs": str(d["default"]["jobs"])}
    for key, value in changes.items():
        form[key] = dict(form[key], **value) if isinstance(value, dict) and key in ("targets", "rosters") else value
    return form


# The headless Chrome the browser tests drive: only one found on PATH by these names, never an application folder or
# any other place, so a test never starts a browser nobody put on PATH. None skips every test that needs one, with
# the reason (each such test or class carries skipUnless(CHROME, ...); TestBrowserSkips holds the suite to that).
CHROME = next((shutil.which(n) for n in ("google-chrome", "chromium", "chromium-browser") if shutil.which(n)), None)


def browser_env(profile):
    """The browser's environment: this one, with its home and every cache it writes (font and dconf caches, the GPU
    drivers' shader caches) inside a folder beside its throwaway profile, so a test's browser leaves nothing under the
    user's HOME. Only the browser gets it; the test process's own environment is unchanged."""
    home = Path(str(profile) + "-home")
    for sub in (".cache", ".config", ".local/share"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    return dict(os.environ, HOME=str(home), XDG_CACHE_HOME=str(home / ".cache"),
                XDG_CONFIG_HOME=str(home / ".config"), XDG_DATA_HOME=str(home / ".local/share"),
                MESA_SHADER_CACHE_DIR=str(home / ".cache" / "mesa"),
                __GL_SHADER_DISK_CACHE_PATH=str(home / ".cache" / "nvidia"))


class Chrome:
    """A headless Chrome driven over --remote-debugging-pipe: CDP's JSON messages, each ended by a NUL, on the
    browser's fds 3 (commands) and 4 (replies). Standard library only, a throwaway profile, every host but 127.0.0.1
    unresolvable and a dead proxy, so the page is the only thing it can reach. One page, attached flat."""

    def __init__(self, profile):
        high = lambda fd: fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 100)        # never 3 or 4 before the dup2s
        cmd_r, cmd_w = map(high, os.pipe())
        res_r, res_w = map(high, os.pipe())
        null = os.open(os.devnull, os.O_RDWR)
        args = [CHROME, "--headless=new", "--remote-debugging-pipe", f"--user-data-dir={profile}", "--no-first-run",
                "--no-default-browser-check", "--disable-extensions", "--disable-background-networking",
                "--disable-component-update", "--disable-sync", "--proxy-server=http://127.0.0.1:9",
                "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1", "--window-size=1280,1000", "about:blank"]
        self.pid = os.posix_spawn(CHROME, args, browser_env(profile), file_actions=[
            (os.POSIX_SPAWN_DUP2, cmd_r, 3), (os.POSIX_SPAWN_DUP2, res_w, 4), (os.POSIX_SPAWN_DUP2, null, 0),
            (os.POSIX_SPAWN_DUP2, null, 1), (os.POSIX_SPAWN_DUP2, null, 2)])
        for fd in (cmd_r, res_w, null):
            os.close(fd)
        self.cmd, self.res, self.buf, self.seq, self.session = cmd_w, res_r, b"", 0, None
        target = self.call("Target.createTarget", url="about:blank")["targetId"]
        self.session = self.call("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]

    def call(self, method, **params):
        self.seq += 1
        msg = dict(id=self.seq, method=method, params=params, **({"sessionId": self.session} if self.session else {}))
        os.write(self.cmd, json.dumps(msg).encode("utf-8") + b"\0")
        end = time.time() + 60
        while True:
            while b"\0" not in self.buf:
                if not select.select([self.res], [], [], max(0.0, end - time.time()))[0]:
                    raise TimeoutError(method)
                chunk = os.read(self.res, 1 << 16)
                if not chunk:
                    raise RuntimeError("Chrome closed its pipe")
                self.buf += chunk
            raw, self.buf = self.buf.split(b"\0", 1)
            reply = json.loads(raw)
            if reply.get("id") == self.seq:
                if "error" in reply:
                    raise RuntimeError(reply["error"])
                return reply.get("result", {})

    def js(self, expression):
        """The value of expression in the page, awaited."""
        r = self.call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"])[:500])
        return r["result"].get("value")

    def close(self):
        self.session = None
        try:
            self.call("Browser.close")
        except (OSError, RuntimeError, TimeoutError):
            pass
        for _ in range(100):
            if os.waitpid(self.pid, os.WNOHANG)[0]:
                break
            time.sleep(0.1)
        else:
            os.kill(self.pid, signal.SIGKILL)                         # the test's own child, past ten seconds
            os.waitpid(self.pid, 0)
        os.close(self.cmd)
        os.close(self.res)


# The page's own controls, for a test that drives it: every fetch counted (so a step can wait for the page to settle,
# whatever version of the page), a row typed into, a choice clicked, and every visible text drawn in --bad (red).
PAGE_HANDS = r"""(() => {
  if (window.__h) return true;
  window.__inflight = 0; window.__last = Date.now();
  const f = window.fetch.bind(window);
  window.fetch = (...a) => { window.__inflight++;
    return f(...a).finally(() => { window.__inflight--; window.__last = Date.now(); }); };
  window.__h = {
    click(value) { [...document.querySelectorAll('.seg button')].find(b => b.value === value).click();
                   window.__last = Date.now(); },
    type(text) { const t = document.querySelector('#measure textarea'); t.value = text;
                 t.dispatchEvent(new Event('input')); window.__last = Date.now(); },
    start() { const b = [...document.querySelectorAll('#confirm button')].find(b => b.textContent === 'Start the run');
              if (b) b.click(); return !!b; },
    reds() { const p = document.createElement('span'); p.style.color = 'var(--bad)'; document.body.appendChild(p);
             const red = getComputedStyle(p).color; p.remove(); const out = [];
             const walk = n => { for (const c of n.childNodes) {
               if (c.nodeType === 3 && c.textContent.trim()) { const e = c.parentElement;
                 if (e && e.getClientRects().length && getComputedStyle(e).color === red)
                   out.push(c.textContent.trim()); }
               else if (c.nodeType === 1) walk(c); } };
             walk(document.body); return out; },
  };
  return true;
})()"""


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


def link_chain(at, n, where):
    """at made the last of a chain of n links with no loop, each to the one before and the first to a folder that is
    not there, the others in where: a path the system will not follow past its limit (ELOOP), and one that Python's
    resolve before 3.13 cannot follow either (RecursionError, past about a thousand)."""
    where.mkdir(parents=True, exist_ok=True)
    before = where / "the-end"
    for i in range(n - 1):
        link = where / f"link-{i}"
        link.symlink_to(before)
        before = link
    at.parent.mkdir(parents=True, exist_ok=True)
    at.symlink_to(before)


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


def not_origin_form(port, route):
    """Every shape of request target that is not origin-form (a path beginning with a slash), each aimed at route on
    the server at port where it has a path, by name: what allowed() refuses before anything splits it. Served.request
    adds the token to each (?t= or &t=), so each carries it as the page's own requests do."""
    rest = route[1:] or "x"
    return {"absolute, this server": f"http://127.0.0.1:{port}{route}",
            "absolute, localhost": f"http://localhost:{port}{route}",
            "absolute, upper-case scheme": f"HTTP://127.0.0.1:{port}{route}",
            "absolute, https": f"https://127.0.0.1:{port}{route}",
            "absolute, another host": f"http://example.com{route}",
            "absolute, no path": f"http://127.0.0.1:{port}",
            "bracketed host, well formed": f"http://[::1]:{port}{route}",
            "bracketed host, IPvFuture": f"http://[v1.x]{route}",
            "bracketed host, unclosed": f"http://[{route}",
            "bracketed host, unclosed, nothing after": "http://[",
            "bracketed host, not an address": f"a://[x]{route}",
            "bracketed host, closing bracket only": f"http://a]{route}",
            "bracketed host, unclosed address": f"http://[::1{route}",
            "a scheme and no host": f"a:{route}",
            "authority, this server": f"127.0.0.1:{port}",
            "authority, localhost": f"localhost:{port}",
            "asterisk": "*",
            "a path with no leading slash": rest,
            "a backslash first": "\\" + rest}


RFC3986 = re.compile(r"(?:([^:/?#]+):)?(?://([^/?#]*))?([^?#]*)(?:\?([^#]*))?(?:#(.*))?", re.S)


REAL_URLSPLIT = urllib.parse.urlsplit


def macos_urlsplit(url, scheme="", allow_fragments=True):
    """A stand-in for urlsplit as macOS's own 3.9.6 splits a target, built from the three targets measured on the Mac
    mini (Apple's /usr/bin/python3): a://[x]/ splits with the host [x], where the Pythons here raise, and http://[/
    and http://a]/ raise "Invalid IPv6 URL". It is this Python's own split wherever this Python splits; elsewhere a
    netloc with an unmatched bracket raises as 3.9.6's bracket rule does (http://[::1/ follows from that rule; it was
    not measured), and any other target is split. So wherever it differs from the Mac it is laxer: it also splits
    targets this Python refuses for another reason (a netloc that changes under NFKC), which 3.9.6 refuses too."""
    try:
        return REAL_URLSPLIT(url, scheme, allow_fragments)
    except ValueError:
        found, netloc, path, query, fragment = RFC3986.fullmatch(url).groups()
        netloc = netloc or ""
        if ("[" in netloc) != ("]" in netloc):
            raise ValueError("Invalid IPv6 URL") from None
        return urllib.parse.SplitResult((found or scheme).lower(), netloc, path, query or "", fragment or "")


def never_raising_urlsplit(url, scheme="", allow_fragments=True):
    """A urlsplit that splits every target and raises for none (RFC 3986's own expression, its appendix B): stronger
    than any Python measured, the Mac's included (macOS's 3.9.6 still raises for http://[/ and http://a]/)."""
    found, netloc, path, query, fragment = RFC3986.fullmatch(url).groups()
    return urllib.parse.SplitResult((found or scheme).lower(), netloc or "", path, query or "", fragment or "")


def keeping_slashes(real):
    """http.server's parse_request as CPython had it before gh-87389 (3.9 before its 2022 security releases, 3.10
    before 3.10.6): real, this Python's own, then the target put back as the request line sent it, its leading slashes
    not reduced to one. Stronger than any Python measured: macOS's own 3.9.6 (Apple's build carries the fix), 3.9.25,
    3.10.12 and 3.13.15 all reduce a leading // themselves."""
    def parse_request(handler):
        ok = real(handler)
        if ok:
            handler.path = handler.requestline.split()[1]
        return ok
    return parse_request


# Each way a page is served for a test of its targets: (name, urlsplit standing as, http.server keeping leading
# slashes or not). This Python as it is; macOS's own 3.9.6 as measured on the Mac mini (its urlsplit, and its
# http.server reducing a leading // as this one does); and three ways stronger than any Python measured: a urlsplit
# that never raises, an http.server from before gh-87389 that keeps leading slashes, and both together.
TARGET_PYTHONS = (("this Python", None, False),
                  ("macOS's 3.9.6 as measured: its urlsplit, leading slashes reduced", macos_urlsplit, False),
                  ("stronger than any Python measured: a urlsplit that never raises", never_raising_urlsplit, False),
                  ("stronger than any Python measured: http.server keeping leading slashes", None, True),
                  ("stronger than any Python measured: both of those together", never_raising_urlsplit, True))


def raw_reply(port, data, timeout=30):
    """What the server at port answers data with, sent as it is on one connection whose write side is then closed:
    (the reply's bytes, read to the end; whether the server closed the connection before timeout). A server that
    refuses a request before reading all of it (a header line it never reads) may close while data is still being
    sent; the send's error is ignored and the reply, already received, is read."""
    conn = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        try:
            conn.sendall(data)
            conn.shutdown(socket.SHUT_WR)
        except OSError:                 # the server answered and closed before reading it all (EPIPE, ECONNRESET,
            pass                        # ENOTCONN): its reply is still there to read
        got = b""
        while True:
            try:
                chunk = conn.recv(65536)
            except socket.timeout:
                return got, False
            except ConnectionResetError:        # closed with unread bytes behind the line: what came first is kept
                return got, True
            if not chunk:
                return got, True
            got += chunk
    finally:
        conn.close()


def refused_lines(port, token, post):
    """Request lines that are not HTTP/1.x, by name: (the bytes each sends, the status line that begins its reply on
    every Python from 3.9, or b"" for no reply at all). post is a target of the page that takes POST (the form's /run
    with the token); each POST carries every proof a POST needs (Host, the token in the target and its header, a
    valid JSON body). parse_request's own rule refuses a line with no version 505, a last word that is not HTTP/ and
    two numbers of 1 to 10 digits 400, and a major number other than 1 505, before any header is read (so HTTP/0.9
    followed by a header line of 70,000 bytes or by 101 headers is 505, not http.server's 431); with the token or
    without. Lines http.server itself rejects are 400 or 431 with a status line (send_response_only); a line of
    whitespace only gets no reply. One outcome on every Python."""
    host = f"Host: 127.0.0.1:{port}\r\n"
    proofs = f"{host}{L.TOKEN_HEADER}: {token}\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{{}}"
    state, bare = f"/state?t={token}", "/state"
    long, many = "X: " + "a" * 70000 + "\r\n", "".join(f"X{i}: y\r\n" for i in range(101))
    lines = {"a method alone": ("GET\r\n\r\n", b"HTTP/1.0 400 "),
             "four words, the last not a version": (f"GET {state} HTTP/1.1 x\r\n{host}\r\n", b"HTTP/1.0 400 "),
             "four words, the last a version": (f"GET {state} x HTTP/1.1\r\n{host}\r\n", b"HTTP/1.0 400 "),
             "a version it cannot read": (f"GET {state} HTTP/x\r\n{host}\r\n", b"HTTP/1.0 400 "),
             "HTTP/2.0": (f"GET {state} HTTP/2.0\r\n{host}\r\n", b"HTTP/1.0 505 "),
             "HTTP/3.0": (f"GET {state} HTTP/3.0\r\n{host}\r\n", b"HTTP/1.0 505 "),
             "no version, GET, a Host and the token after it": (f"GET {state}\r\n{host}\r\n", b"HTTP/1.0 505 "),
             "no version, GET, nothing after it": (f"GET {state}\r\n", b"HTTP/1.0 505 "),
             "no version, POST, every proof after it": (f"POST {post}\r\n{proofs}", b"HTTP/1.0 505 "),
             "HTTP/0.9, GET, every proof": (f"GET {state} HTTP/0.9\r\n{host}\r\n", b"HTTP/1.0 505 "),
             "HTTP/0.9, GET, no token": (f"GET {bare} HTTP/0.9\r\n{host}\r\n", b"HTTP/1.0 505 "),
             "HTTP/0.9, POST, every proof and a valid body": (f"POST {post} HTTP/0.9\r\n{proofs}", b"HTTP/1.0 505 "),
             "HTTP/0.9, POST, no token": (f"POST {post.split('?')[0]} HTTP/0.9\r\n{host}Content-Length: 2\r\n\r\n{{}}",
                                          b"HTTP/1.0 505 "),
             "HTTP/0.5, GET, every proof": (f"GET {state} HTTP/0.5\r\n{host}\r\n", b"HTTP/1.0 505 "),
             "four words ending in HTTP/0.9, GET": (f"GET {state} x HTTP/0.9\r\n{host}\r\n", b"HTTP/1.0 505 "),
             "five words ending in HTTP/0.9, GET": (f"GET {state} x y HTTP/0.9\r\n{host}\r\n", b"HTTP/1.0 505 "),
             "four words ending in HTTP/0.9, POST": (f"POST {post} x HTTP/0.9\r\n{proofs}", b"HTTP/1.0 505 "),
             "five words ending in HTTP/0.9, POST": (f"POST {post} x y HTTP/0.9\r\n{proofs}", b"HTTP/1.0 505 "),
             "HTTP/0.9, then a header line of 70,000 bytes": (f"GET {state} HTTP/0.9\r\n{host}{long}\r\n",
                                                              b"HTTP/1.0 505 "),
             "HTTP/0.9, then 101 headers": (f"GET {state} HTTP/0.9\r\n{host}{many}\r\n", b"HTTP/1.0 505 "),
             "HTTP/1.1, then a header line of 70,000 bytes": (f"GET {state} HTTP/1.1\r\n{host}{long}\r\n",
                                                              b"HTTP/1.0 431 "),
             "HTTP/1.1, then 101 headers": (f"GET {state} HTTP/1.1\r\n{host}{many}\r\n", b"HTTP/1.0 431 "),
             "an empty line": ("\r\n", b""),
             "spaces only": ("   \r\n", b""),
             "a tab only": ("\t\r\n", b""),
             "0x1c only": ("\x1c\r\n", b""),
             "0x1f only": ("\x1f\r\n", b""),
             "NEL (0x85) only": ("\x85\r\n", b""),
             "NBSP (0xa0) only": ("\xa0\r\n", b"")}
    return {name: (data.encode("latin-1"), want) for name, (data, want) in lines.items()}


def spoken(method, target, version, port, token, body=None):
    """A request as the pages send it, in the HTTP version given: the token in the target, a Host naming the server,
    and for a POST the token in its header and a JSON body."""
    sep = "&" if "?" in target else "?"
    head = f"{method} {target}{sep}t={token} {version}\r\nHost: 127.0.0.1:{port}\r\n"
    if body is None:
        return (head + "\r\n").encode("latin-1")
    data = json.dumps(body).encode("utf-8")
    head += f"{L.TOKEN_HEADER}: {token}\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n\r\n"
    return head.encode("latin-1") + data


def first_answer(port, data, timeout=10):
    """The status line and headers that answer data, read with the client's side still open (nothing more is sent):
    the bytes up to the blank line after the headers, or what came before timeout."""
    conn = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    got = b""
    try:
        conn.sendall(data)
        while b"\r\n\r\n" not in got:
            chunk = conn.recv(65536)
            if not chunk:
                break
            got += chunk
    except socket.timeout:
        pass
    finally:
        conn.close()
    return got


def splitting(split):
    """urlsplit standing as split while a page's server is made (serve_page binds it then), or as it is."""
    return mock.patch("urllib.parse.urlsplit", split) if split else contextlib.nullcontext()


def reading_slashes(keep):
    """http.server keeping a target's leading slashes as CPython before gh-87389 does (keeping_slashes), while the
    requests are made, or as it is."""
    real = http.server.BaseHTTPRequestHandler.parse_request
    return (mock.patch.object(http.server.BaseHTTPRequestHandler, "parse_request", keeping_slashes(real)) if keep
            else contextlib.nullcontext())


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

    def test_the_config_folder_is_one_option_and_empty_adds_none(self):
        """A config folder typed in its field is --config-dir FOLDER, as typed but stripped; empty, or blank, is the
        CLI's own folder and adds nothing, as every other field does. probatio's parser takes it."""
        self.assertEqual(U.build_command(dict(JANE, config_dir=" ~/agents/work "))[0],
                         ["--agent", "claude", "--target", "Jane Doe", "--config-dir", "~/agents/work", "--watch"])
        for blank in ("", "  ", None):
            self.assertEqual(U.build_command(dict(JANE, config_dir=blank)), U.build_command(JANE))
        self.assertEqual(P.parse_args_v4(U.build_command(dict(JANE, config_dir="/c"))[0]).config_dir, "/c")
        self.assertEqual(U.FIELD_WORDS["config_dir"], "the config folder")

    def test_a_config_folder_that_is_not_one_is_a_calm_missing_piece(self):
        """A folder that is not there is what the run still needs, in plain words (folder_needs, read as probatio
        reads --config-dir: ~ is the run's HOME, a relative path is from probatio's folder); required stays pure."""
        with tempfile.TemporaryDirectory() as d:
            env = {"HOME": d}
            (Path(d) / "agents").mkdir()
            self.assertEqual(U.folder_needs(dict(JANE, config_dir="~/agents"), env), [])
            self.assertEqual(U.folder_needs(dict(JANE, config_dir="~/nowhere"), env),
                             [U.NEEDS_FOLDER.format("~/nowhere")])
            self.assertEqual(U.folder_needs(dict(JANE, config_dir=""), env), [])
            said = U.preview(dict(JANE, config_dir="~/nowhere"), env=env)
            self.assertEqual((said["missing"], said["needs"]), ([U.NEEDS_FOLDER.format("~/nowhere")],
                                                                U.needs_line([U.NEEDS_FOLDER.format("~/nowhere")])))
            self.assertEqual(U.required(dict(JANE, config_dir="~/nowhere")), [])

    def test_a_comparison_says_what_a_name_does(self):
        """In a comparison the page's note under the rows is probatio's role sentence, and at quick the preview adds
        the scorecard's own quick-tier sentence (names_said: one wording, ordo's quick_names_line), in ordo's roster
        words: invented only when every control is one of the bank's invented entities."""
        self.assertEqual(U.defaults()["names_role"], P.names_said(None, "standard")[0])
        cross = {"agent": "codex", "types": ["individual", "organization"], "targets": {"individual": "Jane Doe"}}
        self.assertEqual(U.preview(dict(cross, tier="quick"))["names_said"], P.names_said("codex", "quick"))
        self.assertEqual(U.preview(dict(cross, tier="quick"))["names_said"][1],
                         P.import_ordo().QUICK_NAMES_LINE.format(agent="codex") + ".")
        self.assertEqual(U.preview(cross)["names_said"], P.names_said("codex", "standard"))
        self.assertEqual(U.preview(JANE)["names_said"], [])
        self.assertIn("invented entities", U.preview(cross)["names_said"][0])
        # a control of the user's own may be real: the note then uses the roster's words, never "invented"
        custom = dict(cross, rosters={"organization": {"controls": "NASA\nThirlbeck Systems"}})
        role = U.preview(custom)["names_said"][0]
        self.assertEqual(role, P.names_said("codex", "standard", "custom")[0])
        self.assertIn("the roster's entities", role)
        self.assertNotIn("invented", role)
        for words in ('role.id = "namesnote"', 'quick.id = "quicknote"', "coded(quick, more.join(\" \"))",
                      "role.textContent = said.names_said[0]"):
            self.assertIn(words, U.PAGE)

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
        Check shows the selected defaults, and how a row's names are written."""
        text = README.read_text(encoding="utf-8")
        for words in ("The page writes no `--out`, so only the default results folder is shared",
                      "a terminal run with `--out` is not reachable from the page",
                      "Check shows the selected folders before anything runs", "written `--target=-NAME`, as one word",
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
                self.assertEqual(U.preview(form), U.refused(U.TIER_REFUSED, U.required(form)))
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
                self.assertEqual(U.preview(form), U.refused(U.NAME_EQUALS.format(t), U.required(form)))
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

    def test_text_that_is_not_utf8_is_refused_before_any_command(self):
        """A lone surrogate (JSON can carry one; UTF-8 cannot) in a roster line, the model, a row, a list of names or a
        key is refused with the field named in the page's words, before any command or file name is made."""
        sur = "\ud800"
        cases = [(dict(MOZILLA, rosters={"organization": {"controls": f"Velt{sur} Holdings"}}),
                  "the organization controls"),
                 (dict(MOZILLA, rosters={"organization": {"comparators": [f"x{sur}"]}}),
                  "the organization comparators"),
                 (dict(MOZILLA, model=f"x{sur}"), "the model"), (dict(JANE, targets={"individual": f"Jane{sur}"}),
                                                                 "the individual row"),
                 (dict(JANE, targets={f"bogus{sur}": "x"}), "a row"), ({f"bogus{sur}": 1}, "a field"),
                 (dict(JANE, effort=[f"x{sur}"]), "the effort")]
        for form, field in cases:
            with self.assertRaises(U.FormRefused) as caught:
                U.build_command(form)
            self.assertEqual(str(caught.exception), U.TEXT_REFUSED.format(field), field)
            self.assertEqual(U.preview(form), U.refused(U.TEXT_REFUSED.format(field), U.required(form)))
        self.assertNotIn("bogus", U.TEXT_REFUSED.format("a row"))

    def test_a_roster_name_the_reply_would_change_is_refused(self):
        """The roster file the page shows passes the reply's redactor too, so a name in it that the redactor would
        change (a key shape, a secret-named variable's value) is refused, its list named, and for a variable its name,
        never its value: the file shown is always the file that runs."""
        key = "sk-" + secrets.token_hex(12)                                     # a fake key, built at run time
        value = "Velt " + secrets.token_hex(8)
        redactor = L.ViewRedactor({"PROBATIO_TEST_API_KEY": value})
        for names, error in ((f"Thirlbeck Systems\n{key}", U.UNSHOWN.format("a name in the organization controls")),
                             (f"{value}\nThirlbeck Systems", U.UNSHOWN_VARIABLE.format(
                                 "a name in the organization controls", "PROBATIO_TEST_API_KEY"))):
            form = dict(MOZILLA, rosters={"organization": {"controls": names}})
            self.assertEqual(U.preview(form, redactor=redactor)["error"], error)
            self.assertNotIn(value, error)
        self.assertIsNone(U.preview(dict(MOZILLA, rosters={"organization": {"controls": "Thirlbeck Systems\nVelt"}}),
                                    redactor=redactor)["error"])
        self.assertEqual(U.unshown(["--target", "Jane " + value], redactor),
                         U.UNSHOWN_VARIABLE.format("the value of --target", "PROBATIO_TEST_API_KEY"))

    def test_a_nul_is_refused_before_any_command(self):
        """No command line can carry a NUL (subprocess raises ValueError on one), so a target or a model holding one
        gets a bounded refusal, with no command, and never reaches a dry run."""
        for form in (dict(JANE, targets={"individual": "Jane\x00Doe"}), dict(JANE, model="x\x00y"),
                     dict(MOZILLA, targets={"organization": "Moz\x00illa"}),
                     {"agent": "claude", "types": ["individual", "nation"], "targets": {"nation": "Fr\x00ance"}}):
            with self.assertRaises(U.FormRefused) as caught:
                U.build_command(form)
            self.assertEqual(str(caught.exception), U.NUL_REFUSED)
            self.assertEqual(U.preview(form), U.refused(U.NUL_REFUSED, U.required(form)))


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
        for form in (JANE, dict(JANE, watch="off"), {"agent": "claude", "targets": {"individual": "Ada Lovelace"}},
                     dict(MOZILLA, watch=False),
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

    def test_a_model_holds_no_invisible_character(self):
        """The Hangul fillers, the braille blank and the rest of Unicode's default-ignorable set show as nothing or a
        blank, so "sonnet" with one in it would read as sonnet: each is refused in a terminal and on the page, with the
        rule, while a visible non-ASCII name passes."""
        rule = "argument --model: " + P.MODEL_RULE
        for ch in ("\u115f", "\u1160", "\u3164", "\uffa0", "\u2800", "\u00ad", "\u034f", "\u180b", "\u2065",
                   "\ufe0f", "\U000e0100", "\U0001d173"):
            model = "son" + ch + "net"
            self.assertEqual(self.parser_says(["--agent", "claude", "--model", model]), rule, repr(ch))
            self.assertEqual(U.preview(dict(JANE, model=model))["error"], rule, repr(ch))
            self.assertTrue(P.invisible(ch), repr(ch))
        for model in ("sonnet", "gpt-6.1-sol", "modèle-ä", "模型"):
            self.assertEqual(P.parse_args_v4(["--agent", "claude", "--model", model]).model, model)

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

    def test_your_own_name_in_gits_author_name_is_not_a_secret(self):
        """GIT_AUTHOR_NAME, XAUTHORITY and SSH_AUTH_SOCK are not secret-named (live.secret_named), so a name one of them
        holds is shown and runs as typed: the user's own name in GIT_AUTHOR_NAME is not refused, nor a longer name
        holding it, as a secret-named variable's value still is."""
        for name in ("GIT_AUTHOR_NAME", "XAUTHORITY", "SSH_AUTH_SOCK"):
            redactor = L.ViewRedactor({name: "Jane Doe"})
            for typed in ("Jane Doe", "Jane Doesmith"):
                self.assertIsNone(U.preview(dict(JANE, targets={"individual": typed}), redactor=redactor)["error"],
                                  (name, typed))
        with mock.patch.dict(os.environ, {"GIT_AUTHOR_NAME": "Jane Doe"}):
            self.assertIsNone(U.preview(JANE)["error"])                 # the redactor this process's environment gives
        secret = L.ViewRedactor({"PROBATIO_TEST_API_KEY": "Jane Doe"})
        self.assertEqual(U.preview(JANE, redactor=secret)["error"],
                         U.UNSHOWN_VARIABLE.format("the value of --target", "PROBATIO_TEST_API_KEY"))
        self.assertEqual(secret.matched_names("--target Jane Doe"), ["PROBATIO_TEST_API_KEY"])     # the page can say why

    def test_refusals_the_page_cannot_follow_are_in_its_words(self):
        """probatio's refusals whose advice names what the page does not show get the page's words, from probatio's
        own messages: two names sharing a key (run them separately, or rename one), two runs on one folder, and a
        type the run does not compare (pick the type). Exhausted defaults give page advice even with a long path.
        A message the page can follow is shown as it is (None)."""
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

        long_path = "/" + "long-install/" * 100 + "results/v4-test"
        exhausted = (f"All 99 default results folders for {long_path} are taken; pass --out to choose another "
                     "folder or clear old folders.")
        for words in (U.page_words, U.versus_page_words):
            answer = words(exhausted)
            self.assertIn("move or rename old run folders under results/", answer)
            self.assertNotIn("--out", answer)
            self.assertLessEqual(len(answer), P.DISPLAY_CHARS)

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


class TestRunResults(unittest.TestCase):
    """A result line counts only when its path lies inside the run's own folders, and a run is finished only on exit 0
    with a scorecard there: a CLI's text in the output (a reply's model name holding newlines, say) names no result,
    finishes nothing and holds no page."""
    SAYS = """import os, sys, time
folder = os.environ["RUN_FOLDER"]
print("Scorecard: /tmp/planted-scorecard.json")
print("Report: " + folder + "/../elsewhere/report.md")
print("Scorecard: " + folder + "x/scorecard.json")
if os.environ.get("OWN"):
    print("Scorecard: " + folder + "/scorecard.json")
    print("Report: " + folder + "/ordo/report.md")
print("The live page stays up at http://127.0.0.1:1/?t=x until you press Ctrl-C.")
if os.environ.get("HOLD"):
    try:
        time.sleep(60)
    except KeyboardInterrupt:
        sys.exit(1)
sys.exit(int(os.environ["RUN_CODE"]))
"""

    def run_says(self, folder, code, own=True, hold=False):
        env = dict(os.environ, PYTHONUNBUFFERED="1", RUN_FOLDER=str(folder), RUN_CODE=str(code),
                   OWN="1" if own else "", HOLD="1" if hold else "")
        return U.Runner([sys.executable, "-c", self.SAYS], [], [folder], env)

    def test_results_only_from_the_runs_own_folders_and_finished_only_on_exit_0(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "results" / "v4-claude-organization-mozilla-quick-memory-on"
            mine = [["Scorecard", str(folder / "scorecard.json")], ["Report", str(folder / "ordo" / "report.md")]]
            for code, own, status, results in ((0, True, "finished", mine), (1, True, "failed", mine),
                                               (0, False, "failed", [])):
                runner = self.run_says(folder, code, own)
                runner.wait(60)
                self.assertEqual((runner.code, runner.status(), runner.results), (code, status, results))
            runner = self.run_says(folder, 0, own=False, hold=True)          # a planted held line, no scorecard
            self.assertTrue(wait_for(lambda: any("stays up" in x for x in runner.state()["lines"])))
            self.assertEqual(runner.status(), "running")
            runner.stop()
            runner.wait(60)
            self.assertEqual(runner.status(), "stopped")


# ---- the server ------------------------------------------------------------------------------------------------------------
def parsed_depth(value):
    """How deep a parsed JSON value nests its lists and dicts: 0 for a scalar, an empty one 1."""
    if isinstance(value, (list, dict)):
        return 1 + max(map(parsed_depth, value.values() if isinstance(value, dict) else value), default=0)
    return 0


def text_depth(data):
    """How deep a JSON text nests, as json_too_deep reads it: the least bound it does not exceed."""
    return next(bound for bound in range(len(data) + 1) if not L.json_too_deep(data, bound))


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

    def test_json_is_measured_on_its_bytes(self):
        """json_too_deep counts the nesting of the text, on the bytes alone: brackets inside a string are not
        nesting, a backslash escapes the byte after it, and over many generated texts, which repeat no key, it
        agrees with the depth of the value they encode (test_the_depth_is_the_texts holds a repeated key)."""
        too_deep = lambda value, bound: L.json_too_deep(json.dumps(value).encode("utf-8"), bound)  # noqa: E731
        self.assertFalse(too_deep({"a": "[[[[[[[[{{{{{{{{"}, 1))
        self.assertFalse(too_deep(["\\", '"', '\\"[[[', "]]]"], 1))
        self.assertFalse(L.json_too_deep(rb'["\\", "\"[[["]', 1))
        self.assertTrue(L.json_too_deep(b"[[0]]", 1))
        self.assertFalse(L.json_too_deep(b"[[0]]", 2))
        self.assertFalse(L.json_too_deep(b"", 1))
        rng = __import__("random").Random(20261114)
        texts = ["", "x", "[", "]", "{", "}", '"', "\\", "\\\"", "é", " "]

        def value(depth):
            if depth == 0:
                return rng.choice([rng.choice(texts) * rng.randint(0, 3), 1, None, True])
            inner = [value(depth - 1)] + [value(rng.randint(0, depth - 1)) for _ in range(rng.randint(0, 2))]
            rng.shuffle(inner)
            if rng.random() < 0.5:
                return inner
            return {rng.choice(texts) + str(i): v for i, v in enumerate(inner)}

        for _ in range(400):
            depth = rng.randint(1, 24)
            text = json.dumps(value(depth), ensure_ascii=rng.random() < 0.5).encode("utf-8")
            self.assertEqual(parsed_depth(json.loads(text)), depth, text[:80])     # the generator's own depth
            for bound in (depth - 1, depth, depth + 1):
                self.assertEqual(L.json_too_deep(text, bound), depth > bound, (depth, bound, text[:80]))

    def test_the_depth_is_the_texts(self):
        """The bound is on the nesting of the text, not of the value it parses to. Where an object repeats a key the
        parser keeps the last value, so the text's depth is at least the parsed value's and greater where a deeper
        earlier value is replaced; a text past MAX_JSON_DEPTH is refused 400 though its parsed value is shallow, and
        reaches no action. Without a repeated key the two depths are equal."""
        self.assertEqual((text_depth(b'{"x":[0],"x":0}'), parsed_depth(json.loads(b'{"x":[0],"x":0}'))), (2, 1))
        rng = __import__("random").Random(20261115)

        def written(depth, repeat):
            """(a JSON text written by hand, the depth it nests in its text); repeat lets an object repeat a key."""
            if depth == 0:
                return rng.choice(['0', '"["', '"{\\"}"', 'null', '"\\\\"']), 0
            parts = [written(depth - 1, repeat)] + [written(rng.randint(0, depth - 1), repeat)
                                                    for _ in range(rng.randint(0, 2))]
            rng.shuffle(parts)
            if rng.random() < 0.5:
                return "[" + ",".join(p for p, _ in parts) + "]", depth
            keys = [rng.choice("ab") if repeat else f"k{i}" for i in range(len(parts))]
            return "{" + ",".join(f'"{k}":{p}' for k, (p, _) in zip(keys, parts)) + "}", depth

        shallower = 0
        for repeat in (True, False):
            for _ in range(400):
                text, depth = written(rng.randint(1, 20), repeat)
                data = text.encode("utf-8")
                parsed = parsed_depth(json.loads(data))
                self.assertEqual(text_depth(data), depth, text[:80])           # json_too_deep reads the text's
                if repeat:
                    self.assertGreaterEqual(depth, parsed, text[:80])
                    shallower += depth > parsed
                else:
                    self.assertEqual(depth, parsed, text[:80])
        self.assertGreater(shallower, 0)                     # the repeated keys did replace deeper values

        called, err = [], io.StringIO()
        server = L.serve_page(U.PAGE, lambda: {}, actions={"/run": lambda request: called.append(request) or {}})
        try:
            client = Served.__new__(Served)
            client.port, client.token = server.server_address[1], server.token
            n = L.MAX_JSON_DEPTH + 1
            past = b'{"x":' + b"[" * (n - 1) + b"0" + b"]" * (n - 1) + b',"x":0}'     # the text 17 deep, its value 1
            self.assertEqual((text_depth(past), parsed_depth(json.loads(past))), (n, 1))
            with mock.patch("sys.stderr", err):
                self.assertEqual(client.request("POST", "/run", past)[0].status, 400)
                self.assertEqual(called, [])
                self.assertEqual(client.request("POST", "/run", b'{"x":0}')[0].status, 200)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(called, [{"x": 0}])
        self.assertNotIn("Traceback", err.getvalue())

    def test_json_nested_past_the_bound_is_refused_on_every_python(self):
        """A body nested deeper than MAX_JSON_DEPTH is 400 before any parse, so it never reaches an action, whatever
        this Python's json accepts; one at the bound does. The form's own actions answer such a body 400 too, where a
        value about 900 deep inside the form used to close the connection unanswered (ui.unencodable recursing)."""
        called, err = [], io.StringIO()
        server = L.serve_page(U.PAGE, lambda: {}, actions={"/run": lambda request: called.append(request) or {}})
        try:
            client = Served.__new__(Served)
            client.port, client.token = server.server_address[1], server.token
            at, past = (b'{"x":' + b"[" * (n - 1) + b"0" + b"]" * (n - 1) + b"}"
                        for n in (L.MAX_JSON_DEPTH, L.MAX_JSON_DEPTH + 1))
            with mock.patch("sys.stderr", err):
                self.assertEqual(client.request("POST", "/run", past)[0].status, 400)
                self.assertEqual(called, [])
                self.assertEqual(client.request("POST", "/run", at)[0].status, 200)
                self.assertEqual(len(called), 1)
        finally:
            server.shutdown()
            server.server_close()
        self.assertNotIn("Traceback", err.getvalue())
        with mock.patch("sys.stderr", err):
            for n in (L.MAX_JSON_DEPTH + 1, 900, 2000):
                deep = b"[" * n + b'"claude"' + b"]" * n
                for field in (b'{"form":{"agent":%s}}', b'{"form":{"targets":%s}}', b'{"x":%s}'):
                    for path in ("/command", "/memory", "/check", "/run", "/stop"):
                        res, _ = self.s.request("POST", path, field % deep)
                        self.assertEqual(res.status, 400, (n, field, path))
            self.assertEqual(self.s.request("POST", "/command", {"form": JANE})[0].status, 200)
        self.assertNotIn("Traceback", err.getvalue())

    def test_an_error_that_escapes_is_answered_500_and_printed(self):
        """The last resort: an exception from an action, from encoding its reply, from the state or from a file
        route is answered 500 with a short plain message and the security headers, its traceback printed to stderr;
        a SystemExit too, which in a request's thread would only end it; the server answers the next request."""
        def boom(request):
            raise RuntimeError("an action that fails")

        def leave(request):
            raise SystemExit("an action that exits")

        def broken_file(query):
            raise OSError("a file route that fails")
        states, err = iter([{}]), io.StringIO()            # the first /state answers, the second raises StopIteration
        server = L.serve_page(U.PAGE, lambda: next(states),
                              actions={"/boom": boom, "/exit": leave, "/odd": lambda request: {"x": object()},
                                       "/fine": lambda request: {"ok": True}},
                              files={"/file": broken_file})
        try:
            client = Served.__new__(Served)
            client.port, client.token = server.server_address[1], server.token
            with mock.patch("sys.stderr", err):
                for method, path, body, want in (("POST", "/boom", {}, 500), ("POST", "/exit", {}, 500),
                                                  ("POST", "/odd", {}, 500), ("GET", "/state", None, 200),
                                                  ("GET", "/state", None, 500), ("GET", "/file", None, 500)):
                    res, text = client.request(method, path, body)
                    self.assertEqual(res.status, want, path)
                    self.assertEqual(res.getheader("Content-Security-Policy"), L.content_policy(U.PAGE), path)
                    self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff", path)
                    if want == 500:
                        self.assertEqual(text, "500 Internal Server Error\n", path)
                        self.assertEqual(res.getheader("Content-Type"), "text/plain; charset=utf-8", path)
                res, text = client.request("POST", "/fine", {})
                self.assertEqual((res.status, json.loads(text)), (200, {"ok": True}))
        finally:
            server.shutdown()
            server.server_close()
        said = err.getvalue()
        for words in ("an action that fails", "an action that exits", "is not JSON serializable", "StopIteration",
                      "a file route that fails"):
            self.assertIn(words, said)
        self.assertEqual(said.count("Exception occurred during processing of request"), 5)
        self.assertEqual(said.count("Traceback (most recent call last)"), 5)

    def test_a_file_routes_malformed_result_is_answered_500_and_printed(self):
        """A file route whose callback returns a malformed result, a tuple of the wrong length, something that is not
        a tuple, or a part of the wrong kind (a str body, a header value latin-1 cannot carry or one holding CR LF),
        is the last resort's 500 with the page's headers and the traceback printed, where its unpacking or its
        writing used to leave the request unanswered (or, with CR LF, write a header of the result's own); a
        well-formed one is served with its own policy, and the server answers the next request."""
        policy = "default-src 'none'"
        bad = {"/two": (b"a", "text/plain"), "/four": (b"a", "text/plain", policy, None),
               "/empty": (), "/bare": b"the body alone", "/number": 5, "/list": [b"a", "text/plain", policy],
               "/text": ("a str body", "text/plain", policy), "/kind": (b"a", "text/plain; →", policy),
               "/crlf": (b"a", "text/plain", policy + "\r\nX-Injected: 1"), "/nokind": (b"a", None, policy)}
        routes = {path: (lambda result: lambda query: result)(result) for path, result in bad.items()}
        routes["/good"] = lambda query: (b"fine", "text/plain; charset=utf-8", policy)
        err = io.StringIO()
        server = L.serve_page(U.PAGE, lambda: {}, files=routes)
        try:
            client = Served.__new__(Served)
            client.port, client.token = server.server_address[1], server.token
            with mock.patch("sys.stderr", err):
                for path in bad:
                    res, text = client.request("GET", path)
                    self.assertEqual((res.status, text), (500, "500 Internal Server Error\n"), path)
                    self.assertEqual(res.getheader("Content-Type"), "text/plain; charset=utf-8", path)
                    self.assertEqual(res.getheader("Content-Security-Policy"), L.content_policy(U.PAGE), path)
                    self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff", path)
                    self.assertIsNone(res.getheader("X-Injected"), path)
                res, text = client.request("GET", "/good")
                self.assertEqual((res.status, text, res.getheader("Content-Security-Policy")), (200, "fine", policy))
                self.assertEqual(client.request("GET", "/state")[0].status, 200)
        finally:
            server.shutdown()
            server.server_close()
        said = err.getvalue()
        self.assertEqual(said.count("Exception occurred during processing of request"), len(bad))
        self.assertEqual(said.count("Traceback (most recent call last)"), len(bad))
        for words in ("not tuple of length 2", "not tuple of length 4", "not tuple of length 0", "not bytes",
                      "not int", "not list", "body is bytes, not str", "content type is a header value", "policy is a "
                      "header value", "not None"):
            self.assertIn(words, said)

    def test_a_file_routes_result_is_used_only_as_plain_copies_made_in_the_guard(self):
        """file_answer takes a file route's result apart once and copies each part to plain bytes or str inside the
        guard, and only the copies make the reply. Of the checker's three subclasses, a tuple that fails on a second
        iteration and a bytes whose length raises are served from their plain copies (the tuple is iterated once,
        and only the plain copy's length is asked), and a str whose formatting raises fails while it is copied, in
        the guard. That one, a bytes whose copy raises, copies that come back as subclasses again, and a tuple
        subclass whose iteration raises at once or yields the wrong count are each the last resort's 500 with one
        traceback; none leaves the request unanswered. A good route is unchanged, and the server answers on."""
        policy = "default-src 'none'"

        class Twice(tuple):                      # the checker's: fails on a second iteration
            def __iter__(self):
                self.iterations = getattr(self, "iterations", 0) + 1
                if self.iterations > 1:
                    raise RuntimeError("a second iteration")
                return tuple.__iter__(self)

        class BrokenLength(bytes):               # the checker's: its length raises
            def __len__(self):
                raise RuntimeError("a body's length")

        class BrokenFormat(str):                 # the checker's: its formatting raises
            def __str__(self):
                raise RuntimeError("a header's formatting")

        class BrokenCopy(bytes):                 # raises while being copied
            def __bytes__(self):
                raise RuntimeError("a body's copy")

        class StillBytes(bytes):                 # copies to a subclass again
            def __bytes__(self):
                return StillBytes(b"again")

        class StillText(str):
            def __str__(self):
                return StillText("again")

        class AtOnce(tuple):
            def __iter__(self):
                raise RuntimeError("a first iteration")

        class Endless(tuple):
            def __iter__(self):
                while True:
                    yield b"x"

        twice = Twice((b"x", "text/plain", policy))
        served = {"/twice": twice, "/length": (BrokenLength(b"x"), "text/plain", policy)}
        refused = {"/format-kind": ((b"x", BrokenFormat("text/plain"), policy), "a header's formatting"),
                   "/format-policy": ((b"x", "text/plain", BrokenFormat(policy)), "a header's formatting"),
                   "/copy": ((BrokenCopy(b"x"), "text/plain", policy), "a body's copy"),
                   "/still-bytes": ((StillBytes(b"x"), "text/plain", policy), "does not copy to plain bytes"),
                   "/still-text": ((b"x", StillText("text/plain"), policy), "does not copy to plain text"),
                   "/at-once": (AtOnce((b"x", "text/plain", policy)), "a first iteration"),
                   "/endless": (Endless(), "too many values to unpack"),
                   "/short": (Twice((b"x", "text/plain")), "not enough values to unpack")}
        routes = {path: (lambda result: lambda query: result)(result) for path, result in served.items()}
        routes.update({path: (lambda result: lambda query: result)(result) for path, (result, _) in refused.items()})
        routes["/good"] = lambda query: (b"fine", "text/plain; charset=utf-8", policy)
        err = io.StringIO()
        server = L.serve_page(U.PAGE, lambda: {}, files=routes)
        try:
            client = Served.__new__(Served)
            client.port, client.token = server.server_address[1], server.token
            with mock.patch("sys.stderr", err):
                for path in served:
                    res, text = client.request("GET", path)
                    self.assertEqual((res.status, text, res.getheader("Content-Length")), (200, "x", "1"), path)
                    self.assertEqual(res.getheader("Content-Security-Policy"), policy, path)
                self.assertEqual(twice.iterations, 1)
                for path, (_, words) in refused.items():
                    before = len(err.getvalue())
                    res, text = client.request("GET", path)
                    said = err.getvalue()[before:]
                    self.assertEqual((res.status, text), (500, "500 Internal Server Error\n"), path)
                    self.assertEqual(res.getheader("Content-Security-Policy"), L.content_policy(U.PAGE), path)
                    self.assertEqual(said.count("Traceback (most recent call last)"), 1, path)
                    self.assertIn(words, said, path)
                res, text = client.request("GET", "/good")
                self.assertEqual((res.status, text, res.getheader("Content-Security-Policy")), (200, "fine", policy))
                self.assertEqual(client.request("GET", "/state")[0].status, 200)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(err.getvalue().count("Traceback (most recent call last)"), len(refused))
        made = L.file_answer(Twice((BrokenLength(b"x"), "text/plain", policy)))
        self.assertEqual([type(part) for part in (made, *made)], [tuple, bytes, str, str])

    def test_the_state_and_an_actions_reply_leave_the_guard_as_plain_bytes(self):
        """json_reply makes the state's and an action's reply inside the guard and hands back plain bytes, whatever
        subclasses the value held; a value whose own code raises while it is written (a mapping whose items raise)
        is the last resort's 500 with its traceback, for the state and for an action alike."""
        class Text(str):
            pass

        class Items(dict):
            def items(self):
                raise RuntimeError("a mapping's items")
        reply = L.json_reply({"a": Text("x"), "b": [Text("y")]}, L.ViewRedactor())
        self.assertIs(type(reply), bytes)
        self.assertEqual(json.loads(reply), {"a": "x", "b": ["y"]})
        err = io.StringIO()
        server = L.serve_page(U.PAGE, lambda: Items(a=1), actions={"/run": lambda request: Items(a=1)})
        try:
            client = Served.__new__(Served)
            client.port, client.token = server.server_address[1], server.token
            with mock.patch("sys.stderr", err):
                self.assertEqual(client.request("GET", "/state")[0].status, 500)
                self.assertEqual(client.request("POST", "/run", {})[0].status, 500)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(err.getvalue().count("RuntimeError: a mapping's items"), 2)

    def test_the_500_is_sent_when_stderr_cannot_take_the_traceback(self):
        """attempt prints the traceback through socketserver's handle_error; a stderr that cannot take it (closed
        here) makes that print raise, and the 500 is still sent rather than the request left unanswered."""
        def boom(request):
            raise RuntimeError("an action that fails")
        closed = io.StringIO()
        closed.close()
        server = L.serve_page(U.PAGE, lambda: {}, actions={"/boom": boom, "/fine": lambda request: {"ok": True}})
        try:
            client = Served.__new__(Served)
            client.port, client.token = server.server_address[1], server.token
            with mock.patch("sys.stderr", closed):
                res, text = client.request("POST", "/boom", {})
                self.assertEqual((res.status, text), (500, "500 Internal Server Error\n"))
                self.assertEqual(client.request("POST", "/fine", {})[0].status, 200)
        finally:
            server.shutdown()
            server.server_close()

    def test_a_target_urlsplit_cannot_split_is_refused_403(self):
        """An absolute-form request target with a malformed bracketed host (http://[/, a://[x]/, http://a]/) made
        urlsplit raise in allowed(), before any answer, and the request closed unanswered. It is not origin-form, so
        it is refused before anything splits it, on a Python whose urlsplit accepts it too (macOS's 3.9.6 takes
        a://[x]/): 403 with the page's headers, GET and POST alike, nothing is printed, and the server answers the
        next request."""
        called, err = [], io.StringIO()
        server = L.serve_page(U.PAGE, lambda: {}, actions={"/run": lambda request: called.append(request) or {}},
                              files={"/file": lambda query: (b"fine", "text/plain", "default-src 'none'")})
        try:
            client = Served.__new__(Served)
            client.port, client.token = server.server_address[1], server.token
            with mock.patch("sys.stderr", err):
                for target in ("http://[/", "a://[x]/", "http://a]/", "http://[/file", "http://[/run"):
                    for method, body in (("GET", None), ("POST", {})):
                        res, _ = client.request(method, target, body)
                        self.assertEqual(res.status, 403, (method, target))
                        self.assertEqual(res.getheader("Content-Security-Policy"), L.content_policy(U.PAGE), target)
                        self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff", target)
                self.assertEqual(client.request("GET", "/state")[0].status, 200)
                self.assertEqual(client.request("POST", "/run", {})[0].status, 200)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(called, [{}])
        self.assertEqual(err.getvalue(), "")

    def recording(self, reached, split=None):
        """The form's page served with each of its own routes (the session's actions and its file route, by path) and
        its state only recording, in reached, that it was reached; its server made with urlsplit standing as split
        (else this Python's own). A client for it, as Served's."""
        actions = {path: (lambda request, path=path: reached.append(path) or {}) for path in self.s.session.actions()}
        files = {path: (lambda query, path=path: reached.append(path) or (b"fine", "text/plain", "default-src 'none'"))
                 for path in self.s.session.files()}
        with splitting(split):
            server = L.serve_page(U.PAGE, lambda: reached.append("/state") or {}, actions=actions, files=files)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = Served.__new__(Served)
        client.port, client.token = server.server_address[1], server.token
        return client

    def test_a_target_not_in_origin_form_is_refused_403_on_every_python(self):
        """allowed() takes only an origin-form target, a path beginning with a slash, which is all the form sends, and
        refuses every other shape before anything splits it (not_origin_form: an absolute URL with this server's host,
        another host, a bracketed host well formed or not, a scheme and no host; an authority; an asterisk; a path
        with no leading slash), each carrying the token and aimed at every route of the form: 403 with the page's
        headers, GET and POST alike, no route reached and nothing printed. The same with urlsplit standing as macOS's
        3.9.6 splits (as measured on the Mac mini), and in the three ways stronger than any Python measured: a
        urlsplit that never raises, http.server keeping a target's leading slashes, and both (TARGET_PYTHONS); and then
        the form answers each of its own requests."""
        routes = ("/", "/state", *self.s.session.actions(), *self.s.session.files())
        self.assertEqual(len(routes), 8)
        for name, split, keep in TARGET_PYTHONS:
            with self.subTest(name), reading_slashes(keep):
                reached, err = [], io.StringIO()
                client = self.recording(reached, split)
                with mock.patch("sys.stderr", err):
                    for route in routes:
                        for shape, target in not_origin_form(client.port, route).items():
                            for method, body in (("GET", None), ("POST", {})):
                                res, text = client.request(method, target, body)
                                where = (name, method, shape, target)
                                self.assertEqual((res.status, text), (403, "403 Forbidden\n"), where)
                                self.assertEqual(res.getheader("Content-Security-Policy"), L.content_policy(U.PAGE),
                                                 where)
                                for header, value in L.SECURITY_HEADERS:
                                    self.assertEqual(res.getheader(header), value, where)
                    self.assertEqual(reached, [])
                    self.assertEqual(client.request("GET", "/")[0].status, 200)
                    for route in routes[1:]:
                        method = "POST" if route in self.s.session.actions() else "GET"
                        self.assertEqual(client.request(method, route, {} if method == "POST" else None)[0].status,
                                         200, (name, route))
                self.assertEqual(reached, list(routes[1:]))
                self.assertEqual(err.getvalue(), "")

    def test_the_served_form_refuses_a_target_not_in_origin_form(self):
        """The form as main serves it (U.serve, its own session's routes): every shape not_origin_form names, aimed at
        every route, is 403 by GET and POST alike, and the form answers its own state after."""
        routes = ("/", "/state", *self.s.session.actions(), *self.s.session.files())
        for route in routes:
            for shape, target in not_origin_form(self.s.port, route).items():
                for method, body in (("GET", None), ("POST", {"form": JANE})):
                    self.assertEqual(self.s.request(method, target, body)[0].status, 403, (method, shape, target))
        self.assertEqual(self.s.request("GET", "/state")[0].status, 200)

    def test_a_target_beginning_with_two_slashes_is_read_as_one_on_every_python(self):
        """A target that begins with two slashes or more is read with one, as http.server reads it since gh-87389 and
        as the earlier reviews recorded (//results.html served as /results.html): //state is the state and //x/state the
        path /x/state, 404, never the host x with the path /state. So on this Python and as macOS's 3.9.6 splits; and
        parse_request reads it so where http.server keeps the slashes too (CPython before that fix, stronger than any
        Python measured), with a urlsplit that never raises or this Python's own (TARGET_PYTHONS)."""
        cases = (("GET", "//", 200, []), ("GET", "//state", 200, ["/state"]), ("GET", "///state", 200, ["/state"]),
                 ("GET", "//results.html", 200, ["/results.html"]), ("GET", "//x/state", 404, []),
                 ("GET", "//x/results.html", 404, []), ("GET", "//[x]/state", 404, []), ("GET", "//[", 404, []),
                 ("GET", "//127.0.0.1/state", 404, []), ("POST", "//run", 200, ["/run"]),
                 ("POST", "//x/run", 404, []), ("POST", "//[x]/run", 404, []))
        for name, split, keep in TARGET_PYTHONS:
            with self.subTest(name), reading_slashes(keep):
                reached = []
                client = self.recording(reached, split)
                for method, target, want, reaches in cases:
                    reached.clear()
                    res, _ = client.request(method, target, {} if method == "POST" else None)
                    self.assertEqual((res.status, reached), (want, reaches), (name, method, target))

    def test_the_guard_for_a_target_urlsplit_cannot_split_is_kept(self):
        """An origin-form target urlsplit cannot split is still refused in allowed() (no origin-form target makes
        the Pythons here raise; urlsplit stands as one that raises for every target): 403 with the page's headers,
        GET and POST alike, no route reached and nothing printed."""
        def refusing(url, scheme="", allow_fragments=True):
            raise ValueError("a urlsplit that splits nothing")
        reached, err = [], io.StringIO()
        client = self.recording(reached, refusing)
        with mock.patch("sys.stderr", err):
            for method, target in (("GET", "/"), ("GET", "/state"), ("GET", "/results.html"), ("POST", "/run")):
                res, _ = client.request(method, target, {} if method == "POST" else None)
                self.assertEqual(res.status, 403, (method, target))
                self.assertEqual(res.getheader("Content-Security-Policy"), L.content_policy(U.PAGE), target)
        self.assertEqual((reached, err.getvalue()), ([], ""))

    def test_the_target_stand_ins_split_as_named(self):
        """TARGET_PYTHONS' urlsplit stand-ins are what their names say. macos_urlsplit splits a://[x]/ with the host
        [x] and raises "Invalid IPv6 URL" for http://[/ and http://a]/ (the three targets measured on the Mac mini) and
        for http://[::1/ (implied by 3.9.6's bracket rule, not measured), and splits every target this Python splits
        as this Python does; never_raising_urlsplit raises for none of them."""
        self.assertEqual(tuple(macos_urlsplit("a://[x]/state?t=1"))[:4], ("a", "[x]", "/state", "t=1"))
        for url in ("http://[/", "http://a]/", "http://[::1/"):
            with self.assertRaisesRegex(ValueError, "Invalid IPv6 URL"):
                macos_urlsplit(url)
            self.assertEqual(never_raising_urlsplit(url).path, "/", url)
        for url in ("/state?t=1", "//x/state", "http://127.0.0.1:8/state", "http://[::1]:8/x", "a:/state", "*", "x"):
            self.assertEqual(macos_urlsplit(url), urllib.parse.urlsplit(url), url)

    def test_a_request_line_that_is_not_http_1_is_refused_with_a_status_line(self):
        """The form speaks HTTP/1.x. Each line refused_lines names, with one outcome on every Python from 3.9: parse_
        request's rule answers a line with no version, or a version whose major is not 1, 505 (four and five words
        ending in HTTP/0.9; HTTP/0.9 followed by a header line of 70,000 bytes or by 101 headers, refused before any
        header is read), and a version it cannot read 400; http.server's own refusals are 400 or 431 with a status
        line (send_response_only; HTTP/1.1 followed by those headers is its 431); a line of whitespace only (empty,
        spaces, a tab, 0x1c, 0x1f, NEL, NBSP) gets no reply. No route is reached and no action runs, nothing is
        printed, the server closes the connection; then the form answers its own requests."""
        reached, err = [], io.StringIO()
        client = self.recording(reached)
        with mock.patch("sys.stderr", err):
            for name, (data, want) in refused_lines(client.port, client.token, f"/run?t={client.token}").items():
                reached.clear()
                reply, closed = raw_reply(client.port, data)
                self.assertTrue(closed, name)
                self.assertEqual(reached, [], name)
                if want:
                    self.assertEqual(reply[:len(want)], want, (name, reply[:80]))
                else:
                    self.assertEqual(reply, b"", name)
            self.assertEqual(client.request("GET", "/state")[0].status, 200)
        self.assertEqual((reached, err.getvalue()), (["/state"], ""))

    def test_a_line_with_no_version_is_answered_before_its_headers(self):
        """A line of two words, HTTP/0.9's own form, sent alone by a client that then waits with its side still open
        (as an HTTP/0.9 client does), is answered 505 with a status line at once: parse_request refuses it before
        http.server reads any header. No route is reached and nothing is printed."""
        reached, err = [], io.StringIO()
        client = self.recording(reached)
        with mock.patch("sys.stderr", err):
            for line in (f"GET /state?t={client.token}\r\n", f"POST /run?t={client.token}\r\n"):
                got = first_answer(client.port, line.encode("latin-1"), timeout=10)
                self.assertEqual(got[:13], b"HTTP/1.0 505 ", (line, got[:80]))
        self.assertEqual((reached, err.getvalue()), ([], ""))

    def test_the_requests_the_form_sends_are_answered_in_http_1_1_and_1_0(self):
        """The form's own requests (its page, its state, an action by POST with every proof, its results route), each
        sent as HTTP/1.1, as HTTP/1.0 and as HTTP/01.1 (a version http.server reads as 1.1 on every Python): 200 with a
        status line, the route reached once, as before the rule."""
        reached, err = [], io.StringIO()
        client = self.recording(reached)
        with mock.patch("sys.stderr", err):
            for version in ("HTTP/1.1", "HTTP/1.0", "HTTP/01.1"):
                for method, target, body, route, reply in (
                        ("GET", "/", None, None, None), ("GET", "/state", None, "/state", b"{}"),
                        ("POST", "/run", {}, "/run", b"{}"), ("GET", "/results.html", None, "/results.html", b"fine")):
                    reached.clear()
                    data = spoken(method, target, version, client.port, client.token, body)
                    got, closed = raw_reply(client.port, data)
                    head, _, text = got.partition(b"\r\n\r\n")
                    self.assertTrue(head.startswith(b"HTTP/1.0 200 OK\r\n"), (version, target, head[:60]))
                    self.assertEqual(reached, [route] if route else [], (version, target))
                    self.assertEqual(text, reply if reply is not None else U.PAGE.encode("utf-8"), (version, target))
                    self.assertTrue(closed)
        self.assertEqual(err.getvalue(), "")

    def test_every_version_spelling_has_one_answer_on_every_python(self):
        """The pages' own version rule (parse_request, HTTP_VERSION) gives every spelling one answer on every Python
        from 3.9, where http.server's own reading differs (3.9 and 3.10 take a sign, an underscore or more than 10
        digits). Each spelling, GET /state with every proof: 400 when the last word is not HTTP/ and two numbers of 1
        to 10 plain digits; 505 when its major is not 1; served (200, the state reached) when it is 1 however it is
        written. The reviewer's longer lines (four and five words ending in HTTP/0.9, HTTP/0.9 followed by a 70,000-byte
        header or 101 headers: 505) are in refused_lines. No refusal reaches a route; nothing is printed."""
        refused = ("HTTP/+1.1", "HTTP/1.+1", "HTTP/1.-1", "HTTP/0_1.1", "HTTP/1.1_0", "HTTP/00000000001.1",
                   "HTTP/1.00000000000", "HTTP/-0.9", "HTTP/+0.9", "HTTP/99999999999.0", "http/1.1", "HTTP/1",
                   "HTTP/1.", "HTTP/.9", "HTTP/1.1.1")
        unsupported = ("HTTP/0.9", "HTTP/0.5", "HTTP/0.0", "HTTP/00.9", "HTTP/0.10", "HTTP/2.0", "HTTP/3.0",
                       "HTTP/99.0")
        served = ("HTTP/01.1", "HTTP/1.01", "HTTP/1.9", "HTTP/1.10", "HTTP/0000000001.1")
        spellings = {**{v: "400" for v in refused}, **{v: "505" for v in unsupported}, **{v: "200" for v in served}}
        reached, err = [], io.StringIO()
        client = self.recording(reached)
        with mock.patch("sys.stderr", err):
            for version, want in spellings.items():
                reached.clear()
                got, closed = raw_reply(client.port, spoken("GET", "/state", version, client.port, client.token))
                self.assertEqual(got[:13], f"HTTP/1.0 {want} ".encode(), (version, got[:60]))
                self.assertEqual(reached, ["/state"] if want == "200" else [], version)
                self.assertTrue(closed, version)
                if want != "200":
                    self.assertNotIn(client.token.encode(), got, version)      # nothing of the line is echoed
        self.assertEqual(err.getvalue(), "")

    def test_the_watch_page_still_refuses_post(self):
        server = L.serve_page(L.VIEW_LIVE, lambda: {})
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
        [(self.folder, _)] = U.prepared(U.build_command(MOZILLA)[0])         # the stub's results go in Mozilla's
        self.env["STUB_FOLDER"] = self.folder.name
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

    def test_check_runs_your_own_name_when_git_holds_it(self):
        """Served as main serves it, the session's redactor is built from this process's environment: with the typed
        name in GIT_AUTHOR_NAME, the command and its Check go through, and the plan shows the name."""
        self.s.close()
        with mock.patch.dict(os.environ, {"GIT_AUTHOR_NAME": "Jane Doe"}):
            self.s = Served(U.Session(program=self.program, env=self.env))
        form = dict(JANE, watch="off")
        self.assertIsNone(self.s.post("/command", {"form": form})["error"])
        reply = self.s.post("/check", {"form": form})
        self.assertTrue(reply["ok"], reply["error"])
        self.assertIn("target Jane Doe (individual)", reply["plan"])

    def test_a_refused_check_shows_probatios_message_verbatim(self):
        """A refusal only the dry run makes, whose advice the page can follow (its lengths and classes are on the
        page), is probatio's message, verbatim."""
        named_at_quick = {"agent": "claude", "types": ["individual", "organization"],
                          "targets": {"organization": "Mozilla"}, "tier": "quick", "classes": ["II"]}
        reply = self.s.post("/check", {"form": named_at_quick})
        argv = U.build_command(named_at_quick)[0]
        direct = subprocess.run([sys.executable, "-B", str(REPO / "probatio.py"), *argv, "--dry-run"], cwd=REPO,
                                env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"], direct.stderr.strip())
        self.assertIn("at --tier quick a named target is measured in Class I only", reply["error"])

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

    def started_folders(self, form):
        """The actual roots Run gives Runner after Check, without starting a process. Success alone no longer
        proves an existing run resumed: a broken selector can approve a fresh sibling just as readily."""
        folders = []
        def recorder(program, argv, selected, env):
            folders.extend(selected)
            return SimpleNamespace(code=0, state=dict, stop=lambda: False)
        with mock.patch.object(U, "Runner", recorder):
            reply = self.s.post("/run", {"form": form, "confirmed": True})
        self.assertNotIn("error", reply, reply)
        return folders

    def test_check_chooses_a_sibling_for_an_incompatible_default(self):
        """Check uses the command's sibling rule for changed effort or fixture identity, reads the old run only,
        and leaves identical runs in their original folder."""
        terminal = ["--agent", "claude", "--target-type", "organization", "--target", "Mozilla", "--tier", "quick"]
        [folder] = self.earlier_run(terminal)
        held = sorted((p.name, p.read_bytes()) for p in folder.iterdir())
        checked = self.s.post("/check", {"form": MOZILLA})
        self.assertTrue(checked["ok"], checked["error"])
        self.assertEqual(checked["notices"], [])
        self.assertEqual(self.started_folders(MOZILLA), [folder])
        row = dict(MOZILLA, targets={"organization": "Wikimedia Foundation\nMozilla"})
        checked = self.s.post("/check", {"form": row})
        self.assertTrue(checked["ok"], checked["error"])
        expected = [out for out, _ in U.prepared(checked["argv"])]
        self.assertIn(folder, expected)
        self.assertEqual(self.started_folders(row), expected)
        off = dict(MOZILLA, memory="off")
        checked = self.s.post("/check", {"form": off})
        self.assertTrue(checked["ok"], checked["error"])
        off_folder = U.prepared(checked["argv"])[0][0]
        self.assertNotEqual(off_folder, folder)
        self.assertEqual(self.started_folders(off), [off_folder])
        edited = dict(MOZILLA, rosters={"organization": {"controls": "Thirlbeck Systems\nKelthorne Labs"}})
        for form, what in ((dict(MOZILLA, effort="high"), "effort"), (edited, "fixtures_sha256")):
            reply = self.s.post("/check", {"form": form})
            self.assertTrue(reply["ok"], reply["error"])
            self.assertIn(U.shown(folder.with_name(folder.name + "-2")), reply["plan"])
            self.assertIn(what, reply["plan"])
            self.assertEqual(len(reply["notices"]), 1)
            self.assertIn("results/" + folder.name + "-2", reply["notices"][0])
            self.assertNotIn(str(self.tmp), reply["notices"][0])
            self.assertNotIn("move or rename", reply["plan"])
        two = dict(MOZILLA, targets={"organization": "Mozilla\nWikimedia Foundation"}, effort="high")
        reply = self.s.post("/check", {"form": two})
        self.assertTrue(reply["ok"], reply["error"])
        self.assertIn(U.shown(folder.with_name(folder.name + "-2")), reply["plan"])
        self.assertEqual(sorted((p.name, p.read_bytes()) for p in folder.iterdir()), held)
        moved = folder.with_name(folder.name + "-moved")
        folder.rename(moved)
        checked = self.s.post("/check", {"form": MOZILLA})
        self.assertTrue(checked["ok"], checked["error"])
        self.assertEqual(checked["notices"], [])
        self.assertEqual(self.started_folders(MOZILLA), [folder])          # original default is free again

    def test_a_row_edit_with_custom_controls_selects_compatible_siblings(self):
        """A row edit changes recognition identity in its other runs: those folders move to compatible siblings,
        while a row with the same recognition identity resumes its existing folders."""
        def row(names):
            return dict(MOZILLA, targets={"organization": names},
                        rosters={"organization": {"controls": "Velt Holdings\nIngle Works"}})
        first = row("Mozilla\nWikimedia Foundation")
        self.assertTrue(self.s.post("/check", {"form": first})["ok"])
        mozilla, wikimedia = self.earlier_run(U.build_command(first)[0])
        reply = self.s.post("/check", {"form": row("Python Software Foundation\nWikimedia Foundation")})
        self.assertTrue(reply["ok"], reply["error"])
        self.assertIn("recognition", reply["plan"])
        self.assertIn(U.shown(wikimedia.with_name(wikimedia.name + "-2")), reply["plan"])
        reply = self.s.post("/check", {"form": row("Wikimedia Foundation\nMozilla")})
        self.assertTrue(reply["ok"], reply["error"])
        for folder in (mozilla, wikimedia):
            self.assertIn(U.shown(folder.with_name(folder.name + "-2")), reply["plan"])
        for same, expected in ((first, [mozilla, wikimedia]), (row("Mozilla"), [mozilla])):
            checked = self.s.post("/check", {"form": same})
            self.assertTrue(checked["ok"], checked["error"])
            self.assertEqual(checked["notices"], [])
            self.assertEqual(self.started_folders(same), expected)

    def test_custom_control_dependents_follow_the_selected_asking_folder(self):
        """When the asker redirects, dependent metadata must name that sibling before comparing an earlier run.
        Check and Run both choose the dependent's sibling rather than reusing a record naming the original asker."""
        form = dict(MOZILLA, targets={"organization": "Mozilla\nWikimedia Foundation"},
                    rosters={"organization": {"controls": "Velt Holdings\nIngle Works"}})
        self.assertTrue(self.s.post("/check", {"form": form})["ok"])
        argv = U.build_command(form)[0]
        asker, dependent = self.earlier_run(argv)
        asking_path, dependent_path = asker / "run.json", dependent / "run.json"
        old = json.loads(asking_path.read_text(encoding="utf-8"))
        old["effort"] = "low"
        asking_path.write_text(json.dumps(old), encoding="utf-8")
        held = dependent_path.read_bytes()
        checked = self.s.post("/check", {"form": form})
        self.assertTrue(checked["ok"], checked["error"])
        expected = [folder.with_name(folder.name + "-2") for folder in (asker, dependent)]
        self.assertEqual(len(checked["notices"]), 2)
        for folder in expected:
            self.assertIn(U.shown(folder), checked["plan"])
        self.assertEqual(dependent_path.read_bytes(), held)                # selection writes no run record
        chosen = []
        def recorder(program, argv, folders, env):
            chosen.extend(folders)
            return SimpleNamespace(code=0, state=dict, stop=lambda: False)
        with mock.patch.object(U, "Runner", recorder):
            self.assertIn("run", self.s.post("/run", {"form": form, "confirmed": True}))
        self.assertEqual(chosen, expected)

    def test_run_selects_a_sibling_when_the_base_is_taken_after_check(self):
        """A competing run arriving after Check changes the selected folder, not the approved argv or fingerprint;
        Runner must own that sibling so its result links are accepted."""
        form = dict(MOZILLA, effort="high")
        checked = self.s.post("/check", {"form": form})
        self.assertTrue(checked["ok"], checked["error"])
        [folder] = self.earlier_run(["--agent", "claude", "--target-type", "organization", "--target", "Mozilla",
                                    "--tier", "quick"])
        started = []
        def recorder(program, argv, folders, env):
            started.append((argv, folders))
            return SimpleNamespace(code=0, state=dict, stop=lambda: False)
        with mock.patch.object(U, "Runner", recorder):
            self.assertNotIn("error", self.s.post("/run", {"form": form, "confirmed": True}))
        self.assertEqual(started, [(checked["argv"], [folder.with_name(folder.name + "-2")])])

    def test_an_unreadable_run_record_uses_a_sibling_and_leaves_the_record(self):
        """Malformed JSON is incompatible rather than resumable, so Check passes with the next default folder."""
        [folder] = self.earlier_run(["--agent", "claude", "--target-type", "organization", "--target", "Mozilla",
                                    "--tier", "quick"])
        (folder / "run.json").write_text("{", encoding="utf-8")
        reply = self.s.post("/check", {"form": MOZILLA})
        self.assertTrue(reply["ok"], reply["error"])
        self.assertIn(U.shown(folder.with_name(folder.name + "-2")), reply["plan"])
        self.assertEqual((folder / "run.json").read_text(encoding="utf-8"), "{")

    def test_check_resumes_the_first_compatible_sibling(self):
        """Check and Run retain a sibling's compatible record, rather than moving on to an absent third folder."""
        argv = U.build_command(dict(MOZILLA, effort="high"))[0]
        [folder] = self.earlier_run(argv)
        sibling = folder.with_name(folder.name + "-2")
        folder.rename(sibling)
        folder.mkdir()
        (folder / "replies.jsonl").write_text("stray", encoding="utf-8")
        reply = self.s.post("/check", {"form": dict(MOZILLA, effort="high")})
        self.assertTrue(reply["ok"], reply["error"])
        plans = U.prepared(argv)
        U.resume_refusals(argv, plans, self.env)
        self.assertEqual(plans[0][0], sibling)
        self.assertNotIn(U.shown(folder.with_name(folder.name + "-3")), reply["plan"])

    def test_check_refuses_when_all_default_siblings_are_taken(self):
        self.assertTrue(self.s.post("/check", {"form": MOZILLA})["ok"])
        for folder in P.default_folder_names(self.folder):
            folder.mkdir(parents=True)
            (folder / "replies.jsonl").write_text("stray", encoding="utf-8")
        blocked = self.s.post("/run", {"form": MOZILLA, "confirmed": True})
        self.assertIn("move or rename old run folders under results/", blocked["error"])
        self.assertNotIn("--out", blocked["error"])
        self.assertIn("pass --out", blocked["plan"])
        reply = self.s.post("/check", {"form": MOZILLA})
        self.assertFalse(reply["ok"])
        self.assertIn("99", reply["error"])
        self.assertIn(U.shown(self.folder), reply["error"])
        self.assertIn("move or rename old run folders under results/", reply["error"])
        self.assertNotIn("--out", reply["error"])
        self.assertIn("pass --out", reply["plan"])                       # terminal advice stays in whole preflight

    def test_a_dangling_default_link_follows_its_free_target(self):
        """A dangling default link is free under the original command rules: Runner owns its resolved target."""
        self.folder.parent.mkdir(parents=True)
        target = self.tmp / "absent-target"
        self.folder.symlink_to(target)
        checked = self.s.post("/check", {"form": MOZILLA})
        self.assertTrue(checked["ok"], checked["error"])
        self.assertEqual(checked["notices"], [])
        self.assertEqual(self.started_folders(MOZILLA), [target.resolve()])
        self.folder.unlink()

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

    def test_an_empty_target_row_is_what_the_run_still_needs(self):
        """A single-type run with its row empty passes the parser, and probatio would stop it for want of a target
        (its own dry run says so, the individual's as every other type's); the page names that row as what the run
        still needs, the same for every type, writes no --name (an older page's name field is ignored), and Check
        refuses it before any dry run."""
        for t in P.TARGET_TYPES:
            form = {"agent": "claude", "types": [t], "tier": "quick", "name": "Jane Doe"}
            said = self.s.post("/command", {"form": form})
            line = U.NEEDS_LINE.format(U.NEEDS_NAME.format(t))
            self.assertEqual((said["error"], said["needs"]), (None, line), t)
            self.assertNotIn("--name", said["argv"])
            reply = self.s.post("/check", {"form": form})
            self.assertEqual((reply["ok"], reply["error"]), (False, line), t)
            direct = subprocess.run([sys.executable, "-B", str(REPO / "probatio.py"), *said["argv"], "--dry-run"],
                                    cwd=REPO, env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                    timeout=120)
            self.assertIn("--target is needed", direct.stderr, t)

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
        self.assertEqual(run["results"], [["Scorecard", U.shown(self.folder / "scorecard.json")],
                                          ["Report", U.shown(self.folder / "report.md")]])
        page = self.s.request("GET", "/")[1]
        self.assertIn("live: () => run && run.watch", page)             # the live link only when there is one: the
        self.assertNotIn('"live": true', json.dumps(run["card"]))      # run card (live.run_card) holds none here

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

    def test_a_disabled_run_looks_like_a_disabled_check(self):
        """Every disabled button, Run included, takes the plain button's fill and the muted ink at full opacity (no
        accent, no fade), so a disabled Run reads as a disabled Check; the enabled Run stays the one accent button. Its
        label is still readable: muted on the track, and the accent ink on the accent, reach WCAG AA (4.5:1) in the
        light and the dark palettes of PAGE_BASE."""
        self.assertIn("button:disabled, button.primary:disabled { background:var(--track); color:var(--muted); "
                      "cursor:default; }", U.PAGE)
        self.assertNotIn("button:disabled { opacity", U.PAGE)
        self.assertLess(U.PAGE.index("button.primary {"), U.PAGE.index("button.primary:disabled"))   # it wins

        def lum(hexa):
            rgb = [int(hexa[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            rgb = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
            return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
        light, dark = L.PAGE_BASE.split("@media (prefers-color-scheme: dark)")[:2]
        for scheme in (light, dark):
            tokens = dict(re.findall(r"--([a-z0-9-]+):(#[0-9a-f]{6})", scheme))
            for ink, fill in (("muted", "track"), ("accent-ink", "accent")):
                hi, lo = sorted((lum(tokens[ink]), lum(tokens[fill])), reverse=True)
                self.assertGreaterEqual((hi + 0.05) / (lo + 0.05), 4.5, (ink, fill))

    def test_the_page_keeps_its_ids_and_the_watch_page_its_palette(self):
        """Every id the tests and scripts read is on the page; the --ui page's tokens are only its own (the watch page
        reads none of them); and while the form still needs a field, the page shows that one calm line (preview's
        "needs") and waits, keeping probatio's own message, in red, for a complete form; no "touched" rule."""
        for i in ("pill", "form", "measure", "against", "how", "cmd", "where", "copy", "labels", "error", "files",
                  "check", "run", "why", "confirm", "runsec", "runbox", "plan", "foot", "custom", "modelnote",
                  "memsec", "memory", "namesnote", "quicknote"):
            self.assertTrue(f'id="{i}"' in U.PAGE or f'.id = "{i}"' in U.PAGE, i)
        for token in ("--field", "--track", "--accent"):
            self.assertIn(token + ":", L.PAGE_BASE)
            self.assertNotIn(f"var({token}", L.VIEW_LIVE)
        for words in ('needs = said && said.needs;', 'error.classList.toggle("calm", !!needs);',
                      "if (needs) error.textContent = needs;",
                      '$("check").disabled = !said || updating() || !!needs ||', 'updating() ? "updating"',
                      'needs ? said.missing[0] + " first"',
                      'el("b", null, "probatio: "), document.createTextNode(said.error)'):
            self.assertIn(words, U.PAGE)
        self.assertNotIn("touched", U.PAGE)

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
        self.assertEqual(run["results"], [["Scorecard", U.shown(self.folder / "scorecard.json")],
                                          ["Report", U.shown(self.folder / "report.md")]])
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

    def test_text_that_is_not_utf8_and_a_roster_the_reply_would_change_never_reach_a_snapshot(self):
        """Over the page: a lone surrogate in a roster line, and a key-shaped roster name, get a bounded refusal from
        /command, /check and /run, never a dropped connection; Check writes no snapshot and starts no dry run."""
        key = "sk-" + secrets.token_hex(12)                                     # a fake key, built at run time
        cases = [(dict(MOZILLA, rosters={"organization": {"controls": "Velt\ud800 Holdings"}}),
                  U.TEXT_REFUSED.format("the organization controls")),
                 (dict(MOZILLA, rosters={"organization": {"controls": f"Thirlbeck Systems\n{key}"}}),
                  U.UNSHOWN.format("a name in the organization controls"))]
        spied, real = [], U.subprocess.run

        def spy(cmd, *args, **kwargs):
            spied.append(list(cmd))
            return real(cmd, *args, **kwargs)
        for form, error in cases:
            with mock.patch.object(U.subprocess, "run", side_effect=spy):
                self.assertEqual(self.s.post("/command", {"form": form})["error"], error)
                reply = self.s.post("/check", {"form": form})
            self.assertEqual((reply["ok"], reply["error"], reply["plan"]), (False, error, ""))
            self.assertIn(self.s.post("/run", {"form": form, "confirmed": True})["error"],
                          (error, "check this command first: Run starts only the command the last Check passed"))
        self.assertEqual(spied, [])
        self.assertFalse(list((self.tmp / "results").glob("ui-entities-*")))

    def test_the_memory_block_is_the_preflights_own_report(self):
        """/memory answers with probatio's own memory_report for the form's command, read in a child process with the
        run's environment (its HOME here): the folder and where it came from, each file's path, size and title, the
        total, the name note, and the other Claude Code config folders; the config folder field moves it, a folder
        that is not there is said calmly (preview's needs, and Check refuses it before any dry run), and a form with
        no agent shows nothing yet. No CLI is started for it."""
        home = Path(self.env["HOME"])
        (home / ".claude").mkdir()
        (home / ".claude" / "CLAUDE.md").write_text("# Old notes\nnothing here\n")
        work = home / "agents" / "work"
        (work / "projects").mkdir(parents=True)
        (work / ".claude.json").write_text("{}")
        (work / "CLAUDE.md").write_text("# Working agent\nfor Jane Doe\n")
        plain = self.s.post("/memory", {"form": JANE})
        report = P.memory_report("claude", str(home.resolve()), "on", "as-configured", home / ".claude", "default",
                                 [P.entity("individual", "Jane Doe")])
        self.assertEqual(plain["report"]["folder"], "~/.claude")                     # ~ is the run's HOME
        self.assertEqual(plain["report"]["source"], P.source_words("claude", "default"))
        self.assertEqual([(f["path"], f["size"], f["title"]) for f in plain["report"]["files"]],
                         [(f["path"], P.size_words(f["bytes"]), f["title"]) for f in report["files"]])
        self.assertEqual(report["notes"], [P.MEMORY_NAME_NOTE.format(name="Jane Doe")])
        self.assertEqual(plain["report"]["notes"], report["notes"])
        self.assertEqual([c["path"] for c in plain["candidates"]], [str(work)])
        self.assertEqual(plain["candidates"][0]["claude_md"]["title"], "Working agent")
        self.assertNotIn("sha256", json.dumps(plain))
        chosen = self.s.post("/memory", {"form": dict(JANE, config_dir=str(work))})
        self.assertEqual([f["title"] for f in chosen["report"]["files"]][:1], ["Working agent"])
        self.assertEqual((chosen["report"]["notes"], chosen["candidates"]), ([], []))
        off = self.s.post("/memory", {"form": dict(JANE, memory="off")})
        self.assertEqual((off["report"]["files"], off["empty"]), ([], P.MEMORY_OFF))
        gone = dict(JANE, config_dir="~/nowhere")
        self.assertEqual(self.s.post("/memory", {"form": gone}), {"report": None, "missing": "~/nowhere"})
        need = [U.NEEDS_FOLDER.format("~/nowhere")]
        said = self.s.post("/command", {"form": gone})
        self.assertEqual((said["missing"], said["needs"], said["error"]), (need, U.needs_line(need), None))
        self.assertEqual(self.s.post("/check", {"form": gone})["error"], U.needs_line(need))
        self.assertEqual(self.s.post("/memory", {"form": dict(JANE, agent="")}), {"report": None})
        self.assertTrue(self.s.post("/check", {"form": dict(JANE, config_dir=str(work))})["ok"])

    def test_what_a_run_still_needs_is_one_calm_line_and_nothing_runs(self):
        """Through the page's own actions: an untouched page, a name typed before an agent, an agent with an empty row,
        empty organization and nation rows, and no class each get required's pieces in the page's order as "missing"
        and one calm line as "needs", with the command so far; Check and Run refuse such a form with that line, before
        anything is written or run. A comparison's names are optional, so it needs nothing; a complete form needs
        nothing, and a wrong value in it is probatio's to refuse, in red."""
        agent, name = U.NEEDS_AGENT, U.NEEDS_NAME.format
        cases = [(page_form(), [agent, name("individual")]),
                 (page_form(targets={"individual": "Jane Doe"}), [agent]),
                 (page_form(agent="claude"), [name("individual")]),
                 (page_form(agent="claude", targets={"individual": " \n "}), [name("individual")]),
                 (page_form(agent="claude", types=["organization"]), [name("organization")]),
                 (page_form(agent="codex", types=["nation"], targets={"individual": "Jane Doe"}), [name("nation")]),
                 (page_form(types=["nation"]), [agent, name("nation")]),
                 (page_form(agent="claude", targets={"individual": "Jane Doe"}, classes=[]), [U.NEEDS_CLASS])]
        spied, real = [], U.subprocess.run

        def spy(cmd, *args, **kwargs):
            spied.append(list(cmd))
            return real(cmd, *args, **kwargs)
        for form, missing in cases:
            line = "To check a run: " + " · ".join(missing)
            with mock.patch.object(U.subprocess, "run", side_effect=spy):
                said = self.s.post("/command", {"form": form})
                self.assertEqual((said["missing"], said["needs"]), (missing, line), form)
                self.assertTrue(said["command"].startswith("python3 probatio.py"))          # the command so far
                checked = self.s.post("/check", {"form": form})
            self.assertEqual((checked["ok"], checked["error"], checked["plan"], checked["missing"]),
                             (False, line, "", missing))
            self.assertEqual(self.s.post("/run", {"form": form, "confirmed": True})["error"], line)
        self.assertEqual(spied, [])                                          # no dry run, no resume check
        self.assertFalse(list((self.tmp / "results").glob("ui-entities-*")))
        self.assertIsNone(self.s.state()["run"])
        for form in (page_form(agent="claude", types=["individual", "organization"]),
                     page_form(agent="claude", types=["individual", "organization", "nation"]),
                     page_form(agent="claude", targets={"individual": "Jane Doe"})):
            said = self.s.post("/command", {"form": form})
            self.assertEqual((said["missing"], said["needs"], said["error"]), ([], None, None), form)
        wrong = self.s.post("/command", {"form": page_form(agent="claude", targets={"individual": "Jane Doe"},
                                                           jobs="x")})
        self.assertEqual((wrong["needs"], wrong["error"]), (None, "argument --jobs: invalid int value: 'x'"))
        self.assertTrue(self.s.post("/check", {"form": page_form(agent="claude",
                                                                 targets={"individual": "Jane Doe"})})["ok"])

    @unittest.skipUnless(CHROME, "needs a headless Chrome on PATH")
    def test_the_memory_block_in_a_real_browser(self):
        """In a real browser, before any Check: the "Memory this run tests" block shows the config folder, the file
        with its size and title, and the name note; a click on another config folder found on this machine fills the
        field, the command gains --config-dir and the block shows that folder's file; a folder that is not there is the
        calm line, never red."""
        home = Path(self.env["HOME"])
        (home / ".claude").mkdir()
        (home / ".claude" / "CLAUDE.md").write_text("# Old notes\nnothing here\n")
        work = home / "agents" / "work"
        (work / "projects").mkdir(parents=True)
        (work / ".claude.json").write_text("{}")
        (work / "CLAUDE.md").write_text("# Working agent\nfor Jane Doe\n")
        chrome = Chrome(self.tmp / "chrome")
        try:
            def settle():
                self.assertTrue(wait_for(lambda: chrome.js(
                    "window.__inflight === 0 && Date.now() - window.__last > 400 && !checking"), 60))
            text = lambda i: chrome.js(f"document.getElementById('{i}').textContent")       # noqa: E731
            chrome.call("Page.navigate", url=f"http://127.0.0.1:{self.s.port}/?t={self.s.token}")
            self.assertTrue(wait_for(lambda: chrome.js("typeof said !== 'undefined' && said !== null"), 60))
            chrome.js(PAGE_HANDS)
            self.assertIn("Choose an agent to see the memory it tests.", text("memory"))
            for step in ("__h.click('claude')", "__h.type('Jane Doe')"):
                chrome.js(step)
                settle()
            shown = text("memory")
            for words in ("config folder ~/.claude", "Old notes", P.size_words(len("# Old notes\nnothing here\n")),
                          "Note: none of these memory files mentions Jane Doe", "~/agents/work", "Working agent"):
                self.assertIn(words, shown)
            chrome.js("[...document.querySelectorAll('#memory .cands button')][0].click(); window.__last = Date.now()")
            settle()
            self.assertEqual(chrome.js("document.querySelector('#memsec input').value"), str(work))
            self.assertIn(f"--config-dir {work}", text("cmd"))
            self.assertIn("config folder ~/agents/work", text("memory"))
            self.assertNotIn("Note: none of these memory files", text("memory"))
            chrome.js("const i = document.querySelector('#memsec input'); i.value = '~/nowhere'; "
                      "i.dispatchEvent(new Event('input')); window.__last = Date.now()")
            settle()
            self.assertEqual(text("error"), U.needs_line([U.NEEDS_FOLDER.format("~/nowhere")]))
            self.assertIn("No folder at ~/nowhere", text("memory"))
            self.assertTrue(chrome.js("document.getElementById('check').disabled"))
            self.assertEqual(chrome.js("__h.reds()"), [])
        finally:
            chrome.close()

    @unittest.skipUnless(CHROME, "needs a headless Chrome on PATH")
    def test_an_edit_and_a_click_at_once_never_turn_red(self):
        """In a real browser: clearing the row and clicking Check in the same moment, and, after a passed Check,
        clearing the row and clicking Run and Start the run, never show red text. The page acts only on a form the
        latest /command has answered (Check and Run wait while it is updating), and a refusal that carries what is
        still needed is drawn calm; nothing is checked or run."""
        chrome = Chrome(self.tmp / "chrome")
        try:
            def settle():
                self.assertTrue(wait_for(lambda: chrome.js(
                    "window.__inflight === 0 && Date.now() - window.__last > 400 && !checking"), 60))
            chrome.call("Page.navigate", url=f"http://127.0.0.1:{self.s.port}/?t={self.s.token}")
            self.assertTrue(wait_for(lambda: chrome.js("typeof said !== 'undefined' && said !== null"), 60))
            chrome.js(PAGE_HANDS)
            for step in ("__h.click('claude')", "__h.type('Jane Doe')"):
                chrome.js(step)
                settle()
            self.assertFalse(chrome.js("document.getElementById('check').disabled"))
            chrome.js("__h.type(''); document.getElementById('check').click()")         # in one moment
            settle()
            self.assertEqual(chrome.js("__h.reds()"), [])
            self.assertEqual(chrome.js("document.getElementById('error').textContent"),
                             U.NEEDS_LINE.format(U.NEEDS_NAME.format("individual")))
            self.assertFalse(list((self.tmp / "results").glob("*")) if (self.tmp / "results").exists() else [])
            chrome.js("__h.type('Jane Doe')")
            settle()
            chrome.js("document.getElementById('check').click()")
            settle()
            self.assertTrue(chrome.js("plan && plan.ok && !document.getElementById('run').disabled"))
            chrome.js("__h.type(''); document.getElementById('run').click(); __h.start()")   # in one moment
            settle()
            self.assertEqual(chrome.js("__h.reds()"), [])
            self.assertIsNone(self.s.state()["run"])
            self.assertFalse((self.tmp / "pid").exists())
        finally:
            chrome.close()

    def test_an_emptied_roster_list_is_what_the_run_still_needs(self):
        """A roster list the form shows, emptied or below its minimum, is a missing piece in plain words, so the
        page stays calm and Check waits: Check and Run refuse it before any snapshot or dry run. A comparison shows
        only the controls, so its comparators never count. An over-full list is a wrong value, probatio's, in red,
        and only then is the list's count drawn red."""
        spied, real = [], U.subprocess.run

        def spy(cmd, *args, **kwargs):
            spied.append(list(cmd))
            return real(cmd, *args, **kwargs)
        cases = [(page_form(agent="claude", targets={"individual": "Jane Doe"},
                            rosters={"individual": {"comparators": "Sam Altman\nElon Musk", "controls": ""}}),
                  ["add a control to the individual controls"]),
                 (page_form(agent="claude", targets={"individual": "Jane Doe"},
                            rosters={"individual": {"comparators": " ", "controls": "Karim Nassar"}}),
                  ["add 2 comparators to the individual comparators"]),
                 (page_form(agent="claude", types=["organization"], targets={"organization": "Mozilla"},
                            rosters={"organization": {"comparators": "Anthropic", "controls": "Thirlbeck Systems"}}),
                  ["add another comparator to the organization comparators"]),
                 (page_form(types=["individual", "organization"],
                            rosters={"organization": {"comparators": "", "controls": ""}}),
                  ["choose an agent", "add a control to the organization controls"])]
        for form, missing in cases:
            with mock.patch.object(U.subprocess, "run", side_effect=spy):
                said = self.s.post("/command", {"form": form})
                self.assertEqual(said["missing"], missing, form)
                checked = self.s.post("/check", {"form": form})
            self.assertEqual((checked["ok"], checked["error"]), (False, U.needs_line(missing)))
            run = self.s.post("/run", {"form": form, "confirmed": True})
            self.assertEqual((run["error"], run["needs"]), (U.needs_line(missing), U.needs_line(missing)))
        self.assertEqual(spied, [])
        self.assertFalse(list((self.tmp / "results").glob("ui-entities-*")))
        full = self.s.post("/command", {"form": page_form(agent="claude", targets={"individual": "Jane Doe"},
                                                          rosters={"individual": {"controls": "A B\nC D\nE F\nG H"}})})
        self.assertEqual(full["missing"], [])
        self.assertIn("individual controls: 4 given; give 1 to 3", full["error"])
        page = self.s.request("GET", "/")[1]
        self.assertIn('counts[part].classList.toggle("bad", n > hi);', page)

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

    def test_a_run_folder_that_cannot_be_resolved_is_a_refusal(self):
        """The run's folder turned into a link to itself just before Run starts the run (the race made certain: at
        Run's last look at its folders, resume_refusals) cannot be resolved (Runner, through resolved): Run answers
        as it answers a process that cannot start, with ELOOP's words, and starts no process; never a connection
        closed unanswered."""
        self.assertTrue(self.s.post("/check", {"form": MOZILLA})["ok"])
        real = U.resume_refusals

        def looping(argv, plans, env):
            said = real(argv, plans, env)
            self.folder.parent.mkdir(parents=True, exist_ok=True)
            self.folder.symlink_to(self.folder)
            return said
        with mock.patch.object(U, "resume_refusals", looping):
            reply = self.s.post("/run", {"form": MOZILLA, "confirmed": True})
        self.assertEqual(reply, {"ok": False, "error": f"the run could not start ({os.strerror(errno.ELOOP)})"})
        self.assertIsNone(self.s.state()["run"])
        self.assertFalse((self.tmp / "pid").exists())                           # no process started
        with self.assertRaises(OSError) as raised:                              # Runner itself, as the review called it
            U.Runner([sys.executable, "-c", "raise SystemExit(5)"], [], [self.folder], self.env)
        self.assertEqual(raised.exception.errno, errno.ELOOP)
        self.folder.unlink()

    def test_a_link_loop_already_at_the_run_folder_is_a_refusal(self):
        """The run's folder made a link to itself after Check passed, already there when Run is pressed: preparing
        the run again (prepared, through probatio's own run_folder) cannot resolve it, and Run answers as for a run
        that cannot start, with ELOOP's words; nothing starts, and the form's server answers afterwards."""
        self.assertTrue(self.s.post("/check", {"form": MOZILLA})["ok"])
        self.folder.parent.mkdir(parents=True, exist_ok=True)
        self.folder.symlink_to(self.folder)
        reply = self.s.post("/run", {"form": MOZILLA, "confirmed": True})
        self.assertEqual(reply, {"error": f"the run could not start ({os.strerror(errno.ELOOP)})"})
        self.assertIsNone(self.s.state()["run"])
        self.assertFalse((self.tmp / "pid").exists())                           # no process started
        self.assertIn("command", self.s.post("/command", {"form": MOZILLA}))   # the server still answers
        self.folder.unlink()

    def test_a_long_chain_of_links_at_the_run_folder_is_a_refusal(self):
        """The run's folder made the last of a chain of 1,100 links (no loop) after Check passed: Run answers as for a
        run that cannot start, with ELOOP's words, on every Python (resolve raising RecursionError before 3.13, a look
        at the folder as named on any); nothing starts, and the form's server answers afterwards."""
        self.assertTrue(self.s.post("/check", {"form": MOZILLA})["ok"])
        link_chain(self.folder, 1100, self.tmp / "chain")
        reply = self.s.post("/run", {"form": MOZILLA, "confirmed": True})
        self.assertEqual(reply, {"error": f"the run could not start ({os.strerror(errno.ELOOP)})"})
        self.assertIsNone(self.s.state()["run"])
        self.assertFalse((self.tmp / "pid").exists())                           # no process started
        self.assertIn("command", self.s.post("/command", {"form": MOZILLA}))   # the server still answers

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


class TestUnresolvable(unittest.TestCase):
    """The one test of a run's folder that cannot be resolved (cannot_resolve), on every Python: resolve failing, or a
    look at the folder (os.stat) meeting ELOOP; and what the form's prepare paths do with it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="probatio-ui-unresolvable-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_what_cannot_be_resolved(self):
        folder, other = self.tmp / "a-folder", self.tmp / "another"
        folder.mkdir()
        other.mkdir()
        (self.tmp / "a-link").symlink_to(folder, target_is_directory=True)
        (self.tmp / "loop").symlink_to(self.tmp / "loop")
        link_chain(self.tmp / "chain-41", 41, self.tmp / "c41")
        link_chain(self.tmp / "chain-1100", 1100, self.tmp / "c1100")
        link_chain(self.tmp / "chain-30", 30, self.tmp / "c30")           # under every system's limit (macOS 32, Linux 40)
        for path, unresolvable in ((folder, False), (self.tmp / "not-there", False),
                                   (self.tmp / "no" / "parent", False), (self.tmp / "a-link", False),
                                   (self.tmp / "chain-30", False),
                                   (Path(tempfile.gettempdir()), False), (P.HERE, False),
                                   (self.tmp / "loop", True), (self.tmp / "chain-41", True),
                                   (self.tmp / "chain-1100", True)):
            with self.subTest(path=path.name):
                self.assertEqual(U.cannot_resolve(path), unresolvable)
        self.assertEqual(U.looping(self.tmp / "loop"), True)                  # the look alone sees a loop
        self.assertEqual(U.looping(self.tmp / "not-there"), False)

    def test_preparing_raises_what_does_not_come_from_resolving_a_folder(self):
        """A RuntimeError or RecursionError raised anywhere but in a command's run_folder is raised as before."""
        def raising(error):
            raise error
        for error in (RecursionError("deep"), RuntimeError("Symlink loop from 'not a folder'")):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)):
                    U.preparing(raising, error)
        with self.assertRaises(SystemExit) as stop:                             # an OSError is answered, by its words
            U.preparing(raising, OSError(errno.EACCES, os.strerror(errno.EACCES)))
        self.assertEqual(stop.exception.code, f"the run could not start ({os.strerror(errno.EACCES)})")

    def test_a_value_error_from_the_prepare_step_is_said_as_what_it_is(self):
        """A ValueError the prepare step raises (a file it reads that it cannot parse, a value it cannot take) is said
        by its own message, displayed, or its type's name when it has none; never as a command line no program can
        take, which is what subprocess's own ValueError means (not_started, unchanged)."""
        def raising(error):
            raise error
        parsed = "Expecting value: line 1 column 1 (char 0)"                  # json's own words for a file it cannot read
        for error, words in ((json.JSONDecodeError("Expecting value", "", 0), parsed),
                             (ValueError("embedded null byte"), "embedded null byte"),
                             (ValueError("a value\nwith a line break"), "a value\\nwith a line break"),
                             (ValueError(), "ValueError")):
            with self.subTest(words=words):
                with self.assertRaises(SystemExit) as stop:
                    U.preparing(raising, error)
                self.assertEqual(stop.exception.code, f"the run could not start ({words})")
                self.assertNotIn("no program can take", stop.exception.code)
        self.assertEqual(U.not_started("run", ValueError("embedded null byte")),
                         "the run could not start (its command line holds what no program can take)")
        with mock.patch.object(P, "HERE", self.tmp):                           # through the form's own prepare path
            argv = U.build_command(MOZILLA)[0]
            with mock.patch.object(P, "prepare_runs", side_effect=json.JSONDecodeError("Expecting value", "", 0)):
                with self.assertRaises(SystemExit) as stop:
                    U.prepared(argv)
        self.assertEqual(stop.exception.code, "the run could not start (Expecting value: line 1 column 1 (char 0))")

    def test_a_patched_run_folder_does_not_make_the_recogniser_raise(self):
        """resolving reads the commands' run_folder code (folder_codes) without ever raising itself: a run_folder
        replaced by a mock, which has no code, is simply not on the traceback, so the error it raises is raised as
        any other, never an AttributeError from the recogniser; a run_folder wrapped by functools.wraps is still
        recognised by the function it wraps."""
        VR = U.versus_command()
        for owner in (P, VR):
            for error in (RecursionError("deep"), RuntimeError("Symlink loop from 'x'")):
                with self.subTest(owner=owner.__name__, error=type(error).__name__):
                    with mock.patch.object(owner, "run_folder", mock.Mock(side_effect=error)):
                        self.assertTrue(U.folder_codes())               # the other command's code, still read
                        with self.assertRaises(type(error)) as raised:
                            U.preparing(owner.run_folder, None)
                    self.assertIs(raised.exception, error)

        def inner(*args):
            raise RecursionError("resolving, deep")

        @functools.wraps(inner)
        def wrapper(*args):
            return inner(*args)
        with mock.patch.object(P, "run_folder", wrapper):
            self.assertIn(inner.__code__, U.folder_codes())
            with self.assertRaises(SystemExit) as stop:
                U.preparing(P.run_folder, None)
        self.assertEqual(stop.exception.code, f"the run could not start ({os.strerror(errno.ELOOP)})")

    def test_runner_resolves_each_folder_once_and_refuses_what_it_cannot(self):
        """Runner's roots are its folders resolved once (resolved_folder), and a folder that cannot be resolved at
        that one moment (resolve failing, or a look meeting ELOOP) refuses the run before any process starts: never
        a run started with a root of None, as a folder changed between a look and a second resolve could leave."""
        folder = self.tmp / "run"
        program = [sys.executable, "-c", "pass"]
        calls = []
        real = U.resolved

        def counting(path):
            calls.append(str(path))
            return real(path)
        with mock.patch.object(U, "resolved", counting):
            runner = U.Runner(program, [], [folder], None)
        runner.wait(30)
        self.assertEqual(calls, [str(folder)])                               # once, in __init__
        self.assertEqual(runner.roots, [folder])
        for patch in (mock.patch.object(U, "resolved", lambda path: None),
                      mock.patch.object(U, "looping", lambda path: True)):
            with self.subTest(patch=patch.attribute), patch, \
                    mock.patch.object(U.subprocess, "Popen", side_effect=AssertionError("a process started")):
                with self.assertRaises(OSError) as raised:
                    U.Runner(program, [], [folder], None)
                self.assertEqual(raised.exception.errno, errno.ELOOP)

    def test_a_run_folder_too_long_a_chain_is_refused_where_the_form_prepares_it(self):
        """prepared, through probatio's prepare_runs and run_folder, on every Python."""
        with mock.patch.object(P, "HERE", self.tmp):
            argv = U.build_command(MOZILLA)[0]
            [(folder, _)] = U.prepared(argv)
            link_chain(folder, 1100, self.tmp / "chain")
            with self.assertRaises(SystemExit) as stop:
                U.prepared(argv)
        self.assertEqual(stop.exception.code, f"the run could not start ({os.strerror(errno.ELOOP)})")

    def test_the_folder_as_named_is_the_one_run_folder_resolves(self):
        """folder_named holds the commands' own folder rule: resolved, it is run_folder's folder, for v4 with and
        without --out, one run or several, and for versus with and without --out."""
        VR = U.versus_command()
        with mock.patch.object(P, "HERE", self.tmp):
            for form in (MOZILLA, dict(MOZILLA, targets={"organization": "Mozilla\nWikimedia"})):
                for out in ([], ["--out", str(self.tmp / "elsewhere")]):
                    a = P.parse_args_v4(U.absolute(U.build_command(form)[0] + out))
                    runs = P.prepare_runs(a, False)[4]
                    many = len(runs) > 1
                    for run in runs:
                        with self.subTest(form=form["targets"], out=bool(out), run=run["name"]):
                            self.assertEqual(U.folder_named(a, run["name"], many).resolve(),
                                             P.run_folder(a, run, many))
            for out in ([], ["--out", str(self.tmp / "versus-elsewhere")]):
                a = VR.parse(["--agent", "claude", "--versus", "individual=Jane Doe", "--versus",
                              "organization=Acme Corp", "--tier", "quick", *out])
                names = VR.versus_names(a.versus)
                with self.subTest(versus=True, out=bool(out)):
                    self.assertEqual(U.folder_named(a, VR.run_name(a, names)).resolve(), VR.run_folder(a, names))


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
                unshown = U.UNSHOWN_VARIABLE.format("the value of --target", "PROBATIO_TEST_API_KEY")
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


class TestBrowserSkips(unittest.TestCase):
    """Every test that starts a browser skips, with its reason, when no Chrome is on PATH (CHROME is None), so the
    suite runs on a machine without one, a Mac whose Chrome is an application and not a command included. Read from
    every test module's source: a test function that calls Chrome(...) (test_ui's, or another module's TU.Chrome)
    must carry skipUnless(...CHROME...) itself or on its class, or its class must raise SkipTest from setUpClass when
    CHROME is None."""

    @staticmethod
    def names_chrome(node):
        return any(isinstance(n, ast.Name) and n.id == "CHROME" or isinstance(n, ast.Attribute) and n.attr == "CHROME"
                   for n in ast.walk(node))

    def skips(self, decorators):
        return any(isinstance(d, ast.Call) and getattr(d.func, "attr", getattr(d.func, "id", None)) == "skipUnless"
                   and d.args and self.names_chrome(d.args[0]) for d in decorators)

    def starts_chrome(self, node):
        return any(isinstance(n, ast.Call) and (isinstance(n.func, ast.Name) and n.func.id == "Chrome" or
                                                isinstance(n.func, ast.Attribute) and n.func.attr == "Chrome")
                   for n in ast.walk(node))

    def test_every_browser_test_skips_without_chrome(self):
        found, bare = [], []
        for path in sorted((REPO / "tests").glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
                guarded = self.skips(cls.decorator_list) or any(
                    isinstance(f, ast.FunctionDef) and f.name == "setUpClass" and self.names_chrome(f) and
                    any(isinstance(r, ast.Raise) and "SkipTest" in ast.dump(r) for r in ast.walk(f)) for f in cls.body)
                for f in (n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test")):
                    if self.starts_chrome(f):
                        found.append(f"{path.name}:{cls.name}.{f.name}")
                        if not (guarded or self.skips(f.decorator_list)):
                            bare.append(found[-1])
        self.assertGreaterEqual(len(found), 6, found)          # it sees the browser tests it is meant to hold
        self.assertEqual(bare, [])

    def test_it_sees_a_browser_test_without_its_skip(self):
        tree = ast.parse("class T:\n    def test_x(self):\n        chrome = TU.Chrome(self.tmp)\n")
        f = tree.body[0].body[0]
        self.assertTrue(self.starts_chrome(f))
        self.assertFalse(self.skips(f.decorator_list) or self.skips(tree.body[0].decorator_list))


if __name__ == "__main__":
    unittest.main()
