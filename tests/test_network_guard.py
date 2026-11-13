"""The suite's network contract (README, "Testing"): no test reaches the network beyond this machine. tests/offline/
guard.py audits it with one audit hook, over the socket calls Python reports as audit events, in the test process
and in every Python child that inherits its environment with site enabled; these tests show it is installed however the suite is run, refuses a lookup of a public name (and a
connection to a public address) in this process and in a Python child, naming the host, lets loopback through (the
form's and the live page's tests serve and fetch on 127.0.0.1 by design), and would have caught the two downloads of
public dataset files a v3 dry run without --local made on 2026-11-11 (it is that dry run, in a child, with only its
cache folder moved into a temporary folder). ChildFlags is a static tripwire for the guard's known gap (a Python child
started with -I, -E or -S runs without the hook): it reads every test file of the three suites and fails on an argv
literal, or a shell command line in a script's text, holding such a flag as an unquoted option word next to a Python
interpreter, but for those UNGUARDED_CHILDREN names with their reasons; it misses a quoted flag, an interpreter alias,
a starred or variable option list and a cleaned environment.

Nothing here can reach the network even if the guard were broken: every check of a refusal first asserts that the
guard is installed in the process that will look the name up, and stops there if it is not. The public name is
RFC 2606's documentation name and the public address RFC 5737's documentation block.

    python3 -m unittest tests.test_network_guard
"""
import ast
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEST_SOURCES = ("tests", "calibration/tests", "docs/versus/sim")    # every suite's folder, read by ChildFlags
OUTSIDE_FLAGS = frozenset("IES")            # -I, -E and -S: a Python child so started skips PYTHONPATH or site
# The test files' Python command lines with -I, -E or -S, each named once with its reason: (file, function) -> reason.
UNGUARDED_CHILDREN = {
    ("tests/test_ui.py", "earlier_run"):
        "an isolated interpreter (-I -B -c) that builds the would-be run record from the repository's ui.py alone, "
        "with nothing from the scratch folder or the test's PYTHON variables on its path; it calls only the "
        "stand-in CLIs' --version and looks nothing up",
}
# A Python command in a script's text: the interpreter (sys.executable, written PYTHON here, or a python command)
# and the options after it.
SCRIPT_COMMAND = re.compile(r"(?:PYTHON|\bpython[0-9.]*)\"?((?:[ \t]+-[^\s\"']+)+)")
PUBLIC_NAME = "example.com"                 # RFC 2606
PUBLIC_ADDRESS = "192.0.2.1"                # RFC 5737, TEST-NET-1
# A child's first act: stop (exit 3, nothing looked up) unless its own guard is installed.
GUARDED = ("import sys\n"
           "if not getattr(sys, '_probatio_test_guard', False) or 'probatio_test_guard' not in sys.modules:\n"
           "    sys.exit(3)\n")
V3_DRY_RUN = GUARDED + (                    # the dry run that downloaded, its cache folder moved (argv[1], argv[2])
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import probatio as P\n"
    "P.CACHE = Path(sys.argv[2])\n"
    "P.main(['--agent', 'claude', '--name', 'Wilhelmina Quarrender', '--tier', 'smoke', '--dry-run'])\n")


def guard():
    """The installed guard module, or None."""
    return sys.modules.get("probatio_test_guard") if getattr(sys, "_probatio_test_guard", False) else None


class Guard(unittest.TestCase):
    def setUp(self):
        self.g = guard()
        if self.g is None:
            self.fail("the suite's network guard is not installed in this process, so nothing here looks anything up")
        self.tmp = Path(tempfile.mkdtemp(prefix="probatio-guard-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def child(self, code, *args, **env):
        """A Python child of this process, with this process's environment (so its guard) plus env."""
        self.assertIn(str(self.g.CHILD), os.environ.get("PYTHONPATH", "").split(os.pathsep))
        return subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True, timeout=300,
                              cwd=str(self.tmp), env=dict(os.environ, **env))

    def test_a_public_name_is_refused_naming_the_host(self):
        for look_up in (lambda: socket.getaddrinfo(PUBLIC_NAME, 443), lambda: socket.gethostbyname(PUBLIC_NAME),
                        lambda: socket.gethostbyaddr(PUBLIC_ADDRESS)):
            with self.assertRaises(self.g.NetworkRefused) as cm:
                look_up()
            self.assertRegex(str(cm.exception), r"'(example\.com|192\.0\.2\.1)' was refused")
        with socket.socket() as s, self.assertRaises(self.g.NetworkRefused) as cm:
            s.connect((PUBLIC_ADDRESS, 443))            # the hook runs before the system call: nothing is sent
        self.assertIn(repr(PUBLIC_ADDRESS), str(cm.exception))

    def test_it_is_not_an_error_the_code_under_test_can_swallow(self):
        self.assertFalse(issubclass(self.g.NetworkRefused, Exception))
        with self.assertRaises(self.g.NetworkRefused):
            try:
                socket.getaddrinfo(PUBLIC_NAME, 80)
            except (OSError, Exception):
                pass

    def test_loopback_passes(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            with socket.create_connection(("127.0.0.1", port), timeout=10):
                pass
            with socket.create_connection(("localhost", port), timeout=10):
                pass
        self.assertTrue(socket.getaddrinfo("127.0.0.1", 80))
        self.assertTrue(socket.getaddrinfo(None, 80, flags=socket.AI_PASSIVE))
        for host in ("127.0.0.1", "127.1.2.3", "::1", "localhost", "LOCALHOST.", "[::1]", "0.0.0.0", None, ""):
            self.assertTrue(self.g.is_local(host), host)
        for host in (PUBLIC_NAME, PUBLIC_ADDRESS, "10.0.0.1", "huggingface.co", "localhost.example.com"):
            self.assertFalse(self.g.is_local(host), host)

    def test_a_python_child_carries_it(self):
        proc = self.child(GUARDED + f"import socket\nsocket.getaddrinfo({PUBLIC_NAME!r}, 443)\n")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("NetworkRefused", proc.stderr)
        self.assertIn(f"{PUBLIC_NAME!r} was refused", proc.stderr)

    def test_it_would_have_caught_the_v3_dry_run_that_downloaded(self):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "claude").write_text('#!/bin/sh\necho "0.0-fake (Claude Code)"\n')
        (bin_dir / "claude").chmod(0o755)
        cache, home = self.tmp / "cache", self.tmp / "home"
        home.mkdir()
        before = (REPO / ".cache").exists()
        proc = self.child(V3_DRY_RUN, str(REPO), str(cache), HOME=str(home),
                          PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        self.assertNotEqual(proc.returncode, 3, "the child's guard was not installed; nothing was looked up")
        self.assertNotEqual(proc.returncode, 0, proc.stdout[-2000:])
        self.assertIn("NetworkRefused", proc.stderr)
        self.assertIn("'huggingface.co' was refused", proc.stderr)
        self.assertEqual([p for p in cache.rglob("*") if p.is_file()], [])     # nothing downloaded
        self.assertEqual((REPO / ".cache").exists(), before)


def outside_flag(options):
    """The first of -I, -E or -S among options, the arguments written after a Python interpreter, or None. Options
    end at the first argument that is not one (a script, or anything not written out), at -c and -m (what follows is
    the program), and a -X or -W takes its value with it."""
    options = list(options)
    i = 0
    while i < len(options):
        word = options[i]
        if not isinstance(word, str) or not word.startswith("-") or word == "-":
            return None
        if word.startswith("--"):
            i += 1
            continue
        for at, letter in enumerate(word[1:]):
            if letter in OUTSIDE_FLAGS:
                return "-" + letter
            if letter in "cm":
                return None
            if letter in "XW":
                i += 1 if at + 2 < len(word) else 2                 # -Xdev holds its value, -X dev has it next
                break
        else:
            i += 1
            continue
        continue
    return None


def is_interpreter(node):
    """Whether a list's item names a Python interpreter: sys.executable, or a string that is a python command."""
    if isinstance(node, ast.Attribute):
        return node.attr == "executable" and isinstance(node.value, ast.Name) and node.value.id == "sys"
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and \
        re.fullmatch(r"python[0-9.]*", Path(node.value).name) is not None


def script_text(node):
    """A string's text as a script would hold it: an f-string's sys.executable written PYTHON, any other value a
    break; None for a node that is not a string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else
                       "PYTHON" if is_interpreter(part.value) else "\0" for part in node.values)
    return None


def outside_children(path):
    """Where one test file holds a shape this recognises with -I, -E or -S: (function, line, flag) for an argument
    list whose item names the interpreter with such an option after it, and for a script's text (a shell line, a
    shebang) with such an unquoted option word after it. A quoted flag in a script's text, an interpreter alias, a
    starred or variable option list and a flag built by probatio's own code are not seen."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    found = []

    def visit(node, where):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            where = node.name
        flag = None
        if isinstance(node, (ast.List, ast.Tuple)):
            for at, item in enumerate(node.elts):
                if is_interpreter(item):
                    flag = flag or outside_flag(getattr(e, "value", None) if isinstance(e, ast.Constant) else e
                                                for e in node.elts[at + 1:])
        text = script_text(node)
        if text is not None:
            for match in SCRIPT_COMMAND.finditer(text):
                flag = flag or outside_flag(match.group(1).split())
        if flag:
            found.append((where, node.lineno, flag))
        for child in ast.iter_child_nodes(node):
            if not (isinstance(node, ast.JoinedStr) and isinstance(child, ast.Constant)):
                visit(child, where)

    visit(tree, "<module>")
    return found


class ChildFlags(unittest.TestCase):
    """A static tripwire for what the guard cannot see: a Python child started with -I, -E or -S skips PYTHONPATH or
    site, so it runs without the hook (guard.py). This reads every test file of the three suites and names each shape
    it recognises, an argv literal, or a shell command line in a script's text (a shebang included), holding such a
    flag as an unquoted option word next to a Python interpreter; only UNGUARDED_CHILDREN's, each with its reason, may
    stand. It misses a quoted flag, an interpreter alias, a starred or variable option list, a cleaned environment
    and world.py's episodes (started with -I by the stand-in CLIs and tests/test_episodes.py, from probatio's own
    configuration)."""

    def test_no_test_file_starts_a_python_child_that_skips_the_guard(self):
        found = {}
        for folder in TEST_SOURCES:
            for path in sorted((REPO / folder).rglob("*.py")):
                if any("_sealed" in part for part in path.parts) or "__pycache__" in path.parts:
                    continue
                name = path.relative_to(REPO).as_posix()
                for where, line, flag in outside_children(path):
                    found.setdefault((name, where), []).append(f"line {line}: {flag}")
        self.assertEqual(sorted(found), sorted(UNGUARDED_CHILDREN), found)

    def test_it_sees_the_shapes_it_names(self):
        for code, seen in (('subprocess.run([sys.executable, "-I", "x.py"])', True),
                           ('subprocess.run([sys.executable, "-B", "-E", "-c", code])', True),
                           ('subprocess.run(["python3", "-BS", "x.py"])', True),
                           ('script.write_text(f\'exec "{sys.executable}" -I "{x}"\')', True),
                           ('FAKE = "#!PY" "THON -S\\nimport sys\\n"', True),     # one string, a shebang
                           ('subprocess.run([sys.executable, "-B", "x.py", "-I"])', False),
                           ('subprocess.run([sys.executable, "-c", "-I"])', False),
                           ('subprocess.run([sys.executable, "-X", "importtime", "x.py"])', False),
                           ('script.write_text(f\'exec "{sys.executable}" "{x}" "$@"\')', False),
                           ('args = ["-I", "-B", "world.py"]', False)):
            with self.subTest(code=code):
                path = Path(tempfile.mkdtemp(prefix="probatio-flags-")) / "t.py"
                self.addCleanup(shutil.rmtree, path.parent, True)
                path.write_text(f"def f():\n    {code}\n", encoding="utf-8")
                self.assertEqual(bool(outside_children(path)), seen)


if __name__ == "__main__":
    unittest.main()
