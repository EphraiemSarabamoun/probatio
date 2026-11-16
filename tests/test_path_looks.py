"""Tests that probatio's yes/no looks at a path answer alike on every Python (probatio's there, file_there,
folder_there and link_there; Human.md, "A look at a path").

Python 3.14 changed Path.exists(), is_dir(), is_file() and their siblings: they answer False for EVERY OSError, where
Python 3.13 and earlier raised for most of them (a name too long, a folder that may not be searched). Code that
refused on that error stopped refusing on 3.14 (a results folder whose name cannot exist passed the form's Check; a
replies file that could not be looked at read as no replies), and code that ended a search on it ended it only
before 3.14 (one folder that may not be searched emptied the form's list of other config folders).

Five things are checked here: probatio.there itself; the four helpers on a table of path kinds, against the answers
written in it and, before Python 3.14, against Path's own methods; refusals that hang on them, the six places the
second review found among them; the config-folder search beside a folder that may not be searched; and, by reading
the code (ast, nothing is run), that the tool's own modules (the top-level *.py) call no yes/no method of Path at all,
and that in calibration/, tools/ and docs/, which are not converted, none sits inside a `try` that catches an
OSError, where the two families of Python part. No test calls an agent or the network.

    python3 -m unittest discover -s tests
"""
import ast
import contextlib
import errno
import io
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import probatio as P  # noqa: E402
import ui as U  # noqa: E402
import versus as VS  # noqa: E402
import versus_run as R  # noqa: E402

LONG = "m" * 300                        # a name no file system probatio runs on takes (255 bytes is the limit)
TOO_LONG = os.strerror(errno.ENAMETOOLONG)


class Scratch(unittest.TestCase):
    """A scratch folder per test, removed after it (permissions restored first, so the removal can enter)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="probatio-looks-"))
        self.locked = []
        self.addCleanup(self.clean)

    def clean(self):
        for folder in self.locked:
            folder.chmod(0o700)
        shutil.rmtree(self.tmp, True)

    def lock(self, folder):
        """Make folder one that may not be searched or listed; skip the test where that binds nobody (root)."""
        if os.geteuid() == 0:
            self.skipTest("root may search any folder")
        folder.chmod(0)
        self.locked.append(folder)
        return folder


# ---- probatio.there ----------------------------------------------------------------------------------------------------
class TestThere(Scratch):
    def test_what_is_there_and_what_is_not(self):
        """True for a file, a folder and a link to either; False for nothing there, a name under a file, a link to
        nothing, a link loop and a path no system call takes."""
        (self.tmp / "file").write_text("x")
        (self.tmp / "folder").mkdir()
        os.symlink(self.tmp / "file", self.tmp / "to-file")
        os.symlink(self.tmp / "nothing", self.tmp / "to-nothing")
        os.symlink(self.tmp / "loop", self.tmp / "loop")
        for name, answer in (("file", True), ("folder", True), ("to-file", True), ("nothing", False),
                             ("file/under", False), ("to-nothing", False), ("loop", False),
                             ("nul\0in-it", False)):
            self.assertIs(P.there(self.tmp / name), answer, name)
        self.assertIs(P.there(str(self.tmp / "file")), True)                # a str is a path too

    def test_a_path_the_system_cannot_look_at_raises_its_own_error(self):
        """A name too long, and a name under a folder that may not be searched: the system's OSError, on every
        Python, for the caller to refuse with."""
        with self.assertRaises(OSError) as long_name:
            P.there(self.tmp / LONG)
        self.assertEqual(long_name.exception.errno, errno.ENAMETOOLONG)
        with self.assertRaises(OSError) as long_parent:
            P.there(self.tmp / LONG / "run.json")
        self.assertEqual(long_parent.exception.errno, errno.ENAMETOOLONG)
        (self.tmp / "locked").mkdir()
        inside = self.lock(self.tmp / "locked")
        with self.assertRaises(PermissionError):
            P.there(inside / "file")


# ---- the four helpers, against pathlib ---------------------------------------------------------------------------------
T, F = True, False
LONG_ERROR = (OSError, errno.ENAMETOOLONG)          # an outcome that raised: the exception's class and its errno
DENIED = (PermissionError, errno.EACCES)
NOT_A_PATH = (TypeError, None)                      # Path refuses a bytes path before anything is looked at
# Each helper, with the method of Path whose answer through Python 3.13 it gives on every Python.
HELPERS = (("there", "exists"), ("file_there", "is_file"), ("folder_there", "is_dir"), ("link_there", "is_symlink"))
# Each kind of path, named under the scratch folder, with what there, file_there, folder_there and link_there answer.
KINDS = (
    ("a file", "file", (T, T, F, F)),
    ("a folder", "folder", (T, F, T, F)),
    ("a link to a file", "to-file", (T, T, F, T)),
    ("a link to a folder", "to-folder", (T, F, T, T)),
    ("nothing there", "nothing", (F, F, F, F)),
    ("a name under a file", "file/under", (F, F, F, F)),
    ("a link to nothing", "to-nothing", (F, F, F, T)),
    ("a link loop", "loop", (F, F, F, T)),
    ("a link to a name too long", "to-long", (LONG_ERROR, LONG_ERROR, LONG_ERROR, T)),
    ("a name too long", LONG, (LONG_ERROR,) * 4),
    ("a name under a name too long", LONG + "/run.json", (LONG_ERROR,) * 4),
    ("a name under a folder that may not be searched", "locked/file", (DENIED,) * 4),
    ("a NUL in the name", "nul\0in-it", (F, F, F, F)),
    ("a file with a trailing slash", "file/", (T, T, F, F)),
    ("a file with a trailing slash and dot", "file/.", (T, T, F, F)),
)
LOCKED_ROW = "a name under a folder that may not be searched"


def outcome(look, path):
    """What look(path) does, as the table writes it: True, False, or the exception's (class, errno)."""
    try:
        return look(path)
    except Exception as e:
        return type(e), getattr(e, "errno", None)


class TestHelpersAgainstPathlib(Scratch):
    def plant(self):
        """The files, folders and links the table names, in the scratch folder; whether the folder that may not be
        searched binds this user (it binds nobody as root, so that row is left out there)."""
        (self.tmp / "file").write_text("x")
        (self.tmp / "folder").mkdir()
        os.symlink(self.tmp / "file", self.tmp / "to-file")
        os.symlink(self.tmp / "folder", self.tmp / "to-folder")
        os.symlink(self.tmp / "nothing", self.tmp / "to-nothing")
        os.symlink(self.tmp / "loop", self.tmp / "loop")
        os.symlink(LONG, self.tmp / "to-long")
        (self.tmp / "locked").mkdir()
        (self.tmp / "locked" / "file").write_text("x")
        if os.geteuid() == 0:
            return False
        (self.tmp / "locked").chmod(0)
        self.locked.append(self.tmp / "locked")
        return True

    def rows(self, locked):
        """(row, the path as given, the four answers): each kind as a str and as a Path, "" both ways (the folder it
        names is the scratch folder, the working directory while the test runs), and a bytes path."""
        for row, name, answers in KINDS:
            if row == LOCKED_ROW and not locked:
                continue
            given = f"{self.tmp}/{name}"
            yield f"{row} (str)", given, answers
            yield f"{row} (Path)", Path(given), answers
        yield '"" (str)', "", (T, F, T, F)
        yield '"" (Path)', Path(""), (T, F, T, F)
        yield "a bytes path", os.fsencode(f"{self.tmp}/file"), (NOT_A_PATH,) * 4

    def test_each_helper_answers_what_its_path_method_answered_through_python_3_13(self):
        """Every row, every helper: the answer written in the table on every Python; and before Python 3.14 the very
        outcome of the matching Path method on the same input in the same process (pathlib is the answer sheet
        there). From 3.14 pathlib answers False at the rows that raise, so only the table is asked."""
        locked = self.plant()
        here = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, here)
        for row, given, answers in self.rows(locked):
            for (helper, method), answer in zip(HELPERS, answers):
                with self.subTest(row=row, helper=helper):
                    got = outcome(getattr(P, helper), given)
                    self.assertEqual(got, answer)
                    self.assertIs(type(got), type(answer))          # True and False, never 1 and 0
                    if sys.version_info < (3, 14):
                        self.assertEqual(got, outcome(lambda p: getattr(Path(p), method)(), given))


# ---- the refusals that hang on it --------------------------------------------------------------------------------------
class TestRefusals(Scratch):
    def unlookable_replies(self):
        """A run folder whose replies.jsonl is a link to a name too long: a look at it is the system's refusal."""
        run = self.tmp / "run"
        run.mkdir()
        os.symlink(LONG, run / "replies.jsonl")
        return run

    def test_rescore_refuses_replies_it_cannot_look_at(self):
        """strict_replies: one plain refusal with the system's reason, never "no replies" (what Python 3.14's
        Path.exists() made of it); a folder with no replies file is still no replies."""
        with self.assertRaises(SystemExit) as refused:
            P.strict_replies(self.unlookable_replies())
        line = str(refused.exception)
        self.assertIn(f"replies.jsonl cannot be read ({TOO_LONG})", line)
        self.assertIn("--rescore cannot score the run", line)
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertEqual(P.strict_replies(empty), {})

    def test_a_comparison_refuses_replies_it_cannot_look_at(self):
        """versus.read_reply_lines: the same refusal for a head-to-head run's replies; no file is no records."""
        run = self.unlookable_replies()
        with self.assertRaises(VS.VersusError) as refused:
            VS.read_reply_lines(run / "replies.jsonl")
        self.assertIn(f"cannot be read ({TOO_LONG})", str(refused.exception))
        self.assertEqual(VS.read_reply_lines(self.tmp / "nothing" / "replies.jsonl"), [])

    def test_a_comparison_refuses_a_results_folder_whose_name_cannot_exist(self):
        """versus_run.run_folder: --out with a name too long is one line on stderr and exit 2 (FOLDER_UNUSABLE),
        and so is such a name one folder up; a folder that is only not there yet is returned, resolved."""
        for given in (self.tmp / LONG, self.tmp / LONG / "run"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as refused:
                R.run_folder(types.SimpleNamespace(out=str(given)), {})
            self.assertEqual(refused.exception.code, 2)
            self.assertEqual(len(err.getvalue().splitlines()), 1, err.getvalue())
            self.assertIn(f"cannot be used ({TOO_LONG}); pass --out with another folder", err.getvalue())
        fresh = self.tmp / "not-yet"
        self.assertEqual(R.run_folder(types.SimpleNamespace(out=str(fresh)), {}), fresh.resolve())


# ---- six places the review found, where Python 3.14 read the error as "not there" --------------------------------------
class TestFilesThatCannotBeLookedAt(Scratch):
    """Six places where, with the published code, a file that cannot be looked at was the system's OSError through
    Python 3.13 and "not there" from 3.14 (the second review of the 3.14 fix, findings 1 to 6). Each asserts what 3.13
    did, on every Python: the OSError with errno ENAMETOOLONG. The file is a link to a name too long, so every look
    that follows the link meets that error."""

    def unlookable(self, folder, name):
        """A folder holding name as a link to a name too long."""
        out = self.tmp / folder
        out.mkdir()
        os.symlink(LONG, out / name)
        return out

    def assertTooLong(self, call):
        """call raises the system's OSError for a name too long."""
        with self.assertRaises(OSError) as raised:
            call()
        self.assertEqual(raised.exception.errno, errno.ENAMETOOLONG, raised.exception)

    def test_a_comparison_folder_whose_run_record_cannot_be_looked_at_is_not_fresh(self):
        """versus_run.folder_state: a run.json that cannot be looked at is the error, never a fresh folder to start a
        comparison in (finding 1)."""
        out = self.unlookable("v4-record", "run.json")
        self.assertTooLong(lambda: R.folder_state(out, {}))

    def test_a_run_refuses_a_versus_record_it_cannot_look_at_before_writing_anything(self):
        """refuse_versus_folder, in the order execute_run takes the folder (the refusal by reading, the lock, then
        check_resume): a versus-run.json that cannot be looked at is the error before anything is written, so the
        folder holds the link alone, no run.lock and no run.json (finding 2)."""
        out = self.unlookable("versus-record", P.VERSUS_RUN_FILE)

        def take_the_folder():
            P.refuse_versus_folder(out)
            lock = P.claim(out)
            try:
                P.check_resume(out, {})
            finally:
                lock.close()

        self.assertTooLong(take_the_folder)
        self.assertEqual(sorted(p.name for p in out.iterdir()), [P.VERSUS_RUN_FILE])

    def test_a_resume_refuses_replies_it_cannot_look_at(self):
        """read_replies, through run_calls, the runner's resume: replies.jsonl that cannot be looked at is the error,
        never no replies, which would make every call again (finding 3). The call list is empty, so nothing is
        asked of any agent."""
        out = self.unlookable("replies", "replies.jsonl")
        self.assertTooLong(lambda: P.run_calls([], "claude", None, out, 1))

    def test_codex_settings_refuse_a_config_they_cannot_look_at(self):
        """codex_settings: a config.toml that cannot be looked at is the error, never a config with no model and no
        effort (finding 4). Asked directly, so no CLI is started."""
        config = self.unlookable("codex-config", "config.toml")
        (config / "auth.json").write_text("{}")
        self.assertTooLong(lambda: P.codex_settings(config))

    def test_the_run_time_refuses_a_launch_log_it_cannot_look_at(self):
        """read_launches: a launches.jsonl that cannot be looked at is the error, never zero launches and no run
        time on the card (finding 5)."""
        out = self.unlookable("launches", "launches.jsonl")
        self.assertTooLong(lambda: P.read_launches(out))

    def test_the_forms_resume_check_refuses_a_run_record_it_cannot_look_at(self):
        """ui.resume_refusals, the form's Check: a run.json that cannot be looked at is the error, never a folder
        with no saved run to compare (finding 6). It raises before its child process would start."""
        out = self.unlookable("ui-record", "run.json")
        self.assertTooLong(lambda: U.resume_refusals([], [(out, "fixture")], {}))


# ---- the config-folder search ------------------------------------------------------------------------------------------
class TestCandidatesBesideAFolderThatMayNotBeSearched(Scratch):
    def test_the_other_config_folders_are_still_found(self):
        """One folder under home that may not be searched is only not a candidate: the config folders beside it are
        found, before it and after it in the listing, on every Python (through 3.13 it emptied the list)."""
        home = self.tmp / "home"
        for name in ("a-config", "z-config"):
            (home / name / P.CANDIDATE_MARKS[1]).mkdir(parents=True)
            (home / name / P.CANDIDATE_MARKS[0]).write_text("{}")
        (home / "locked").mkdir()
        self.lock(home / "locked")
        found = P.config_candidates(home=home, seconds=60)
        self.assertEqual(sorted(Path(c["path"]).name for c in found), ["a-config", "z-config"])


# ---- no yes/no method of Path in the tool's own modules ----------------------------------------------------------------
# The methods whose errors Python 3.14 reads as "no". os.path's functions of the same names always read them so, and
# os.DirEntry's methods did not change, so neither is looked for.
PATH_LOOKS = frozenset({"exists", "is_dir", "is_file", "is_symlink", "is_mount", "is_junction", "is_socket", "is_fifo",
                        "is_block_device", "is_char_device"})
CATCHES_OSERROR = frozenset({"OSError", "IOError", "EnvironmentError", "Exception", "BaseException"})


def caught(handler):
    """The names an `except` clause lists that would catch an OSError; a bare `except:` catches everything."""
    if handler.type is None:
        return {"BaseException"}
    listed = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return {n.attr if isinstance(n, ast.Attribute) else getattr(n, "id", "") for n in listed} & CATCHES_OSERROR


def is_path_look(node):
    """Whether node calls one of PATH_LOOKS as a method, and not os.path's function (`os.path.exists(p)`, os.path by
    that name: a Path held as `x.path` is still looked for) nor a directory entry's method, which is told by its
    follow_symlinks keyword (`e.is_dir(follow_symlinks=False)`)."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in PATH_LOOKS):
        return False
    owner = node.func.value
    if isinstance(owner, ast.Attribute) and owner.attr == "path" and isinstance(owner.value, ast.Name) and \
            owner.value.id == "os":
        return False
    return not (isinstance(owner, ast.Name) and any(k.arg == "follow_symlinks" for k in node.keywords))


def path_looks(source):
    """Every (line, method) where source calls a PATH_LOOKS method, wherever the call sits: a function, a lambda, a
    comprehension, a handler or the module's own top level."""
    return sorted((n.lineno, n.func.attr) for n in ast.walk(ast.parse(source)) if is_path_look(n))


def looks_under_a_try(source):
    """Every (line, method, function) where source calls a PATH_LOOKS method inside the body of a `try`, in the same
    function, one of whose handlers would catch an OSError. A nested function starts clear: it runs when it is called,
    not where it is written (a lambda keeps the try it is written in, being called at once as a rule)."""
    found = []

    def walk(node, function, under):
        for field, value in ast.iter_fields(node):
            for child in (value if isinstance(value, list) else [value]):
                if not isinstance(child, ast.AST):
                    continue
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    walk(child, child.name, False)
                    continue
                inner = under or (isinstance(node, ast.Try) and field == "body"
                                  and any(caught(h) for h in node.handlers))
                if inner and is_path_look(child):
                    found.append((child.lineno, child.func.attr, function))
                walk(child, function, inner)

    walk(ast.parse(source), "<module>", False)
    return found


def own_modules():
    """The tool's own modules: the top-level Python files of the checkout (probatio.py, versus.py, ui.py and the
    rest), the files these rules convert."""
    return sorted(REPO.glob("*.py"))


def maintainer_files():
    """Every Python file in a folder of the checkout (the maintainer's calibration/, tools/ and docs/, which are not
    converted), outside its tests and its hidden and results folders."""
    for path in sorted(REPO.rglob("*.py")):
        parts = path.relative_to(REPO).parts
        if len(parts) > 1 and not any(p == "tests" or p == "results" or p.startswith(".") for p in parts[:-1]):
            yield path


class TestNoPathLook(unittest.TestCase):
    def test_the_whole_search_finds_every_call(self):
        """path_looks on a planted source: a call anywhere is found (at the module's top, in a function, on an
        expression, on a new Path, in a lambda, in a comprehension, in a handler, on a Path held as `x.path`, and a
        sibling method of Path), and os.path's functions and the directory entry's method are left."""
        planted = (
            "import os\n"
            "from pathlib import Path\n"
            "HERE = Path('.')\n"
            "HERE.exists()\n"                                              # 4: found, at the module's top
            "def looks(p, e, q, run):\n"
            "    p.is_file()\n"                                            # 6: found
            "    (p / 'a').is_dir()\n"                                     # 7: found, on an expression
            "    Path(q).is_symlink()\n"                                   # 8: found, on a new Path
            "    later = lambda: p.exists()\n"                             # 9: found, in a lambda
            "    [x for x in p.iterdir() if x.is_mount()]\n"               # 10: found, a sibling method
            "    try:\n"
            "        pass\n"
            "    except OSError:\n"
            "        p.is_socket()\n"                                      # 14: found, in a handler
            "    run.path.exists()\n"                                      # 15: found, a Path held as x.path
            "    os.path.exists(p)\n"                                      # os.path's function: left
            "    os.path.isdir(p)\n"                                       # left
            "    e.is_dir(follow_symlinks=False)\n"                        # a directory entry's method: left
        )
        self.assertEqual(path_looks(planted), [(4, "exists"), (6, "is_file"), (7, "is_dir"), (8, "is_symlink"),
                                               (9, "exists"), (10, "is_mount"), (14, "is_socket"), (15, "exists")])

    def test_the_search_under_a_try_finds_what_it_looks_for(self):
        """looks_under_a_try on a planted source: the three shapes it must find, and the six it must leave."""
        planted = (
            "import os\n"
            "from pathlib import Path\n"
            "def refuses(p):\n"
            "    try:\n"
            "        p.exists()\n"                                          # 5: found
            "    except (OSError, ValueError):\n"
            "        raise SystemExit('no')\n"
            "def nested_blocks(p):\n"
            "    try:\n"
            "        if (p / 'a').is_file() and os.path.isdir(p):\n"        # 10: found (is_file); os.path left
            "            pass\n"
            "    except Exception:\n"
            "        return None\n"
            "def bare_except(p):\n"
            "    try:\n"
            "        return [q for q in p.iterdir() if q.is_dir()]\n"       # 16: found
            "    except:\n"
            "        return []\n"
            "def left_alone(p, e):\n"
            "    p.exists()\n"                                              # no try
            "    try:\n"
            "        p.exists()\n"                                          # a handler that catches no OSError
            "    except (FileNotFoundError, ValueError):\n"
            "        pass\n"
            "    try:\n"
            "        os.path.exists(p)\n"                                   # os.path's function
            "        e.is_dir(follow_symlinks=False)\n"                     # a directory entry's method
            "        def later():\n"
            "            return p.is_dir()\n"                               # a nested function starts clear
            "    except OSError:\n"
            "        pass\n"
            "    try:\n"
            "        pass\n"
            "    except OSError:\n"
            "        p.exists()\n"                                          # in the handler, not the body
        )
        self.assertEqual(looks_under_a_try(planted),
                         [(5, "exists", "refuses"), (10, "is_file", "nested_blocks"), (16, "is_dir", "bare_except")])

    def test_the_tools_own_modules_call_no_yes_no_method_of_path(self):
        """The tool's own modules call no yes/no method of Path at all, the directory entry's apart: each look is
        probatio's there, file_there, folder_there or link_there on the same path, which answers on every Python what
        the method answered through 3.13. No place is allowed one."""
        modules = own_modules()
        self.assertIn(REPO / "probatio.py", modules)                    # the walk found the checkout
        found = [f"{m.name}:{line} {method}()" for m in modules
                 for line, method in path_looks(m.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "Path's yes/no methods answer False for every OSError from Python 3.14 and raise "
                                    "for most through 3.13: call probatio's there, file_there, folder_there or "
                                    "link_there on the same path instead")

    def test_no_look_of_paths_sits_under_a_try_in_the_maintainers_trees(self):
        """In calibration/, tools/ and docs/, which are not converted, the weaker rule: no yes/no method of Path inside
        a `try` that catches an OSError, where the handler runs through Python 3.13 and never from 3.14."""
        files = list(maintainer_files())
        self.assertEqual({f.relative_to(REPO).parts[0] for f in files}, {"calibration", "docs", "tools"})
        found = [f"{f.relative_to(REPO)}:{line} {method}() in {function}" for f in files
                 for line, method, function in looks_under_a_try(f.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "Path's yes/no methods answer False for every OSError from Python 3.14 and raise "
                                    "for most through 3.13: use probatio's helpers, or os.path's exists, isfile or "
                                    "isdir when every error reads as \"no\"")


if __name__ == "__main__":
    unittest.main()
