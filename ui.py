#!/usr/bin/env python3
"""ui: a simple page for choosing a probatio v4 run (python3 probatio.py --ui). It shows what a run does by default, lets
you choose what you want instead, turns the form into the command line that does it, checks that command with the real
dry run, and runs it.

One page on 127.0.0.1 behind a random token, served by live.serve_page with the --watch page's hardening (the token on
every route, the Host allowlist, exact routes, the hash-only CSP, no referrer) and four POST actions, which carry the
token in a header as well. The form has three short sections (what to measure, against whom, how to run), and beside
it sits the answer: the exact command, its plan and its run.

    defaults        everything the form starts from, read from probatio and never typed here: each option's default
                    from parse_args_v4 and each type's rosters from print_entities
    build_command   the form as an argv with only the options it changed, and the entities file its roster edits
                    need (pure)
    preview         the command as the form changes: probatio's own parser on it (in this process, its message
                    verbatim) and the calibration words for its roster (load_entities, fixtures_label)
    Session.check   the real `--dry-run` of that argv in a subprocess: every guard probatio has and its preflight,
                    with no model call beyond the CLI's version probe
    Runner          one run at a time: the checked argv (--watch is always in it) in a subprocess, its watch link,
                    the last lines of its output, its results folders and scorecards, and Stop (SIGINT to that child
                    alone, as Ctrl-C in a terminal: the calls already running finish, and the same command resumes)

probatio.py imports this module only for --ui (import_ui), and this module imports probatio and live, as live.py
imports probatio. Standard library only, Python 3.9 or later.
"""
import contextlib
import errno
import functools
import hashlib
import io
import json
import os
import re
import secrets
import shlex
import signal
import stat
import subprocess
import sys
import threading
import time
from collections import deque, namedtuple
from pathlib import Path

import live
import probatio as P

# ---- the page's numbers, words and patterns: one home ----------------------------------------------------------------
PROGRAM = ("python3", "probatio.py")   # how the page writes the command, run from the probatio folder
LISTS = ("comparators", "controls")   # the roster lists the form edits; targets go on the command line as --target
ENTITIES = "ui-entities-{}.json"      # under results/, named by the file's whole sha256: one name, one content
SNAPSHOT_MODE = 0o444                 # a roster snapshot is read-only once written
AUTO_MEMORY_OFF = "off"               # --auto-memory's other choice (its default is the parser's)
WATCH = "--watch"                     # always on: the page reads the run's live link, and Run runs what Check checked
TAIL = 40                             # the newest lines of a run's output on the page
CHECK_SECONDS = 120                   # a dry run that takes longer is reported, not waited for
STOP_SECONDS = 10                     # after a second Ctrl-C, how long the page waits for its run to end
BOUNDARY = ("Local to this machine. Check makes no model call (only the CLI's version check); Run uses your own CLI "
            "login and quota.")
# What the page reads from probatio's output: the --watch link, a scored run's result lines, the held page's line,
# and the preflight's totals (each run's calls and time, several runs' total time, the 45-minute hint).
WATCH_LINK = re.compile(r"watch it live: (http://127\.0\.0\.1:\d+/\?t=[A-Za-z0-9_-]+)")
RESULT_LINE = re.compile(r"^(Scorecard|Report): (\S.*)$")
HELD_LINE = "The live page stays up at "
RUN_TOTAL = re.compile(r"^\s+all\s+\d+ items\s+(\d+) calls\s+(.+?)(?: \((.+)\))?$")
CROSS_TOTAL = re.compile(r"^\s+\d+ units, (\d+) calls\s+(.+?)(?: \((.+)\))?$")
ALL_RUNS = re.compile(r"^\s+all \d+ comparisons: (.+?)(?: \((.+)\))?$")
TIME_HINT = re.compile(r"^\s+(about [\d.]+ minutes; to shorten: .+)$")
PARSE_LOCK = threading.Lock()         # the in-process parser borrows sys.stderr, one request at a time


# ---- the defaults, from probatio ---------------------------------------------------------------------------------------
@functools.lru_cache(maxsize=None)
def bank():
    """probatio's pinned item bank, read once."""
    return P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256)


@functools.lru_cache(maxsize=None)
def defaults():
    """Everything the form starts from, read from probatio and never typed here: every option's default from
    parse_args_v4's own parser (the one command line it accepts without --agent, which has no default), the choices
    from probatio's constants, and each type's comparators, controls and targets from print_entities, exactly the
    --entities starter file. The offered tiers are USER_TIERS; the others (smoke) sit behind the page's setup check."""
    a = P.parse_args_v4(["--print-entities"])
    rosters = json.loads(P.print_entities(P.load_entities(None, bank()), bank()))
    return {
        "default": {"type": a.target_type, "agent": a.agent, "tier": a.tier, "classes": list(a.classes),
                    "memory": a.memory, "auto_memory": a.auto_memory, "effort": a.effort, "model": a.model,
                    "jobs": a.jobs},
        "types": list(P.TARGET_TYPES),
        "rosters": {t: {part: rosters[t][part] for part in LISTS + ("targets",)} for t in P.TARGET_TYPES},
        "counts": {part: list(P.LIST_COUNTS[part]) for part in LISTS},
        "agents": [[k, P.AGENT_NAMES[k]] for k in P.AGENT_NAMES],
        "tiers": list(P.USER_TIERS), "setup": [t for t in P.V4_TIERS if t not in P.USER_TIERS],
        "planned": {t: list(P.resolve_classes(t, "all")) for t in P.V4_TIERS},
        "classes": list(P.V4_CLASSES), "memory": list(P.MEMORY_MODES), "efforts": list(P.EFFORTS),
        "auto_memory": [a.auto_memory, AUTO_MEMORY_OFF],
        "labels": {"cross": P.CROSS_LABEL},
        "program": shlex.join(PROGRAM), "boundary": BOUNDARY,
    }


# ---- the form as a command line (pure) ---------------------------------------------------------------------------------
# The page sends its form as JSON: {"types": [...], "name": text, "targets": {type: text}, "rosters": {type:
# {"comparators": text, "controls": text}}, "agent", "tier", "classes": [...], "memory", "auto_memory", "effort",
# "model", "jobs"}, every list of names one name per line. A field left out is its default; --watch is always on, so a
# "watch" field is ignored.
def text_of(value):
    """A form field as text, stripped; anything that is not text reads as empty."""
    return value.strip() if isinstance(value, str) else ""


def names_of(value):
    """A form list (text with one name per line, or a list of names) as its names, blank lines dropped."""
    items = value.splitlines() if isinstance(value, str) else value if isinstance(value, list) else []
    return [x.strip() for x in items if isinstance(x, str) and x.strip()]


def entry_name(entry):
    """An entities-file entry's name: the entry itself, or its "full"."""
    return entry if isinstance(entry, str) else entry["full"]


def types_of(form, base):
    """The types the form measures, in probatio's order: one is a single-type run against its invented control, two
    or three are compared with each other; none chosen reads as the parser's default type."""
    chosen = form.get("types") if isinstance(form.get("types"), list) else []
    return [t for t in P.TARGET_TYPES if t in chosen] or [base["default"]["type"]]


def lists_shown(types):
    """The roster lists the page edits for this selection: a single-type run uses both, a comparison between types
    only each type's controls (its invented entities, DESIGN 11.2)."""
    return LISTS if len(types) == 1 else ("controls",)


def entities_text(form, types, base):
    """The entities file the form's roster edits need, as text, or None when every list the page shows is the
    default: only the lists that differ, each entry the default's own when its name is unchanged (so a curated record
    keeps its fields), else the name as typed."""
    rosters = form.get("rosters") if isinstance(form.get("rosters"), dict) else {}
    data = {}
    for t in types:
        edits = rosters.get(t) if isinstance(rosters.get(t), dict) else {}
        for part in lists_shown(types):
            if part not in edits:
                continue
            given, default = names_of(edits[part]), base["rosters"][t][part]
            if given != [entry_name(e) for e in default]:
                by_name = {entry_name(e): e for e in default}
                data.setdefault(t, {})[part] = [by_name.get(n, n) for n in given]
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n" if data else None


def entities_path(text):
    """Where the entities file goes, relative to probatio's folder (the command's working directory): named by the
    whole sha256 of its bytes, so one name can hold one content only."""
    return f"results/{ENTITIES.format(hashlib.sha256(text.encode('utf-8')).hexdigest())}"


def build_command(form, base=None):
    """The form as probatio's argv, with only the flags that change something (an option at its parser default is
    left out), and the entities file its roster edits need, as text, or None. base is defaults(). The comparison is
    --compare (none for the default type alone; probatio's canonical form for two or three types); typed names become
    --target TYPE=NAME, and with none an individual run scores --name, as probatio does; roster edits become
    --entities results/ui-entities-<sha256>.json. Classes are left out only when all three are chosen, the form's own
    default, which is the parser's "all" (each tier then asks its own classes); any other choice is passed on as
    --class, so a subset stays explicit at every tier, even one equal to the tier's classes (I,II at quick, which
    probatio compares with standard's I,II), and an empty one so the parser says why. --watch is always last: the page
    reads the run's live link from it, and the command shown is the one Check checks and Run runs, byte for byte.
    Pure: it reads nothing and writes nothing (Check writes the file)."""
    base = base or defaults()
    form = form if isinstance(form, dict) else {}
    default, types = base["default"], types_of(form, base)
    argv = []
    agent = text_of(form.get("agent"))
    if agent:
        argv += ["--agent", agent]
    name = text_of(form.get("name"))
    if name:
        argv += ["--name", name]
    if types != [default["type"]]:
        argv += ["--compare", types[0] if len(types) == 1 else P.canonical_compare([tuple(types)])["compare"]]
    targets = form.get("targets") if isinstance(form.get("targets"), dict) else {}
    for t in types:
        for n in names_of(targets.get(t)):
            argv += ["--target", f"{t}={n}"]
    entities = entities_text(form, types, base)
    if entities:
        argv += ["--entities", entities_path(entities)]
    tier = text_of(form.get("tier")) or default["tier"]
    if tier != default["tier"]:
        argv += ["--tier", tier]
    picked = form.get("classes")
    if isinstance(picked, list):
        classes = tuple(k for k in P.V4_CLASSES if k in picked)
        if set(classes) != set(P.V4_CLASSES):
            argv += ["--class", ",".join(classes)]
    for key, flag in (("memory", "--memory"), ("auto_memory", "--auto-memory"), ("effort", "--effort")):
        value = text_of(form.get(key))
        if value and value != default[key]:
            argv += [flag, value]
    model = text_of(form.get("model"))
    if model:
        argv += ["--model", model]
    jobs = text_of(str(form["jobs"])) if form.get("jobs") is not None else ""
    if jobs and jobs != str(default["jobs"]):
        argv += ["--jobs", jobs]
    argv.append(WATCH)
    return argv, entities


def command_words(argv):
    """The command's words, each quoted for a shell as shlex.join quotes it; the page keeps each one on one line."""
    return [shlex.quote(w) for w in (*PROGRAM, *argv)]


def command_text(argv):
    """The command as the page shows it, ready to paste in a shell in the probatio folder."""
    return " ".join(command_words(argv))


# ---- what probatio says about a command --------------------------------------------------------------------------------
def exit_message(code, stderr=""):
    """What probatio said when it stopped, verbatim: a SystemExit's own text; else, when stderr is argparse's (its
    usage first, "PROG: error: MESSAGE" last), the message; else stderr as it is."""
    if isinstance(code, str):
        return code.strip()
    lines = (stderr or "").strip().splitlines()
    if lines and lines[0].startswith("usage: ") and ": error: " in lines[-1]:
        return lines[-1].split(": error: ", 1)[1]
    return (stderr or "").strip()


def parser_error(argv):
    """probatio's own parser (parse_args_v4) on argv, in this process: None when it accepts the command line, else its
    message verbatim. Only the parser runs here; every other guard runs in Check's dry run."""
    err = io.StringIO()
    with PARSE_LOCK, contextlib.redirect_stderr(err):
        try:
            P.parse_args_v4(argv)
        except SystemExit as e:
            return exit_message(e.code, err.getvalue())
    return None


def entities_view(text):
    """The entities file as the page shows it: its path, as the command names it, and its text; or None."""
    return {"path": entities_path(text), "text": text} if text else None


def preview(form, base=None):
    """The answer to the form as it changes: the command (argv and its text), the entities file it needs, the parser's
    verdict (verbatim), and the calibration words probatio prints for the selection and the roster: cross-type and
    unregistered (DESIGN 11's labels), and fixtures_label, "custom roster: not calibrated (fixtures …)" when the
    lists differ from the defaults. A roster that load_entities refuses gives its message instead."""
    base = base or defaults()
    form = form if isinstance(form, dict) else {}
    argv, entities = build_command(form, base)
    types = types_of(form, base)
    reply = {"argv": argv, "command": command_text(argv), "words": command_words(argv),
             "entities": entities_view(entities), "error": parser_error(argv), "labels": [], "roster": None}
    if len(types) > 1:
        reply["labels"].append(P.CROSS_LABEL)
        if not P.canonical_compare([tuple(types)])["registered"]:
            reply["labels"].append(P.UNREGISTERED_LABEL)
    try:
        spec = P.load_entities(None, bank(), data=json.loads(entities) if entities else None)
        reply["roster"] = P.fixtures_label(spec, types)
        reply["labels"].append(reply["roster"])
    except SystemExit as e:
        reply["error"] = reply["error"] or exit_message(e.code)
    return reply


def plan_summary(text):
    """The dry run's totals, as the confirm step states them, from the preflight's own lines: every run's calls added
    up, the time (several runs' total, else the one run's), the 45-minute hint and the rates the time assumes. A
    value the preflight does not print is None."""
    calls, times, total, hint, rates = None, [], None, None, None
    for line in text.splitlines():
        m = RUN_TOTAL.match(line) or CROSS_TOTAL.match(line)
        if m:
            calls = (calls or 0) + int(m.group(1))
            times.append(m.group(2))
            rates = rates or m.group(3)
        elif ALL_RUNS.match(line):
            total = ALL_RUNS.match(line).group(1)
        elif TIME_HINT.match(line):
            hint = TIME_HINT.match(line).group(1)
    return {"calls": calls, "time": total or (times[0] if len(times) == 1 else None), "hint": hint, "rates": rates}


# ---- roster snapshots: immutable and bound to their content ----------------------------------------------------------
# An entities file the page writes is a snapshot: named by the whole sha256 of its bytes, created once, exclusively and
# atomically, as a read-only regular file inside a real results/ folder, and never overwritten. Every step works
# through results/'s own directory descriptor, opened without following a symbolic link, so neither results/ nor a
# snapshot can be a link that sends a write or a read anywhere else. P.HERE is already resolved, so results/ is the
# one parent to check. A Check binds the bytes it read; a Run reads them again and refuses any difference.
class SnapshotRefused(Exception):
    """A roster snapshot the page will not write or use; the message, shown on the page, says why and what to do."""


def results_folder():
    """probatio's results/ as an open directory descriptor, made if it is missing: a real folder, never a symbolic
    link (O_NOFOLLOW) or anything else. The caller closes it."""
    path = P.HERE / "results"
    try:
        path.mkdir(exist_ok=True)
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as e:
        why = "a symbolic link" if path.is_symlink() else os.strerror(e.errno or 0)
        raise SnapshotRefused(f"results/ is not a plain folder ({why}), so the page writes no roster file into it; "
                              "make results/ a real folder and check again") from e


def read_snapshot(folder, name):
    """A snapshot's bytes from results/ (folder, its descriptor), read only from a regular file and without following
    a symbolic link or waiting on a pipe."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
    except OSError as e:
        what = "is a symbolic link" if e.errno == errno.ELOOP else "is missing" if e.errno == errno.ENOENT else \
            f"cannot be read ({os.strerror(e.errno or 0)})"
        raise SnapshotRefused(f"results/{name} {what}; remove it if it is there, and check again") from e
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise SnapshotRefused(f"results/{name} is not a regular file; remove it and check again")
        chunks = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(fd)


def snapshot_bytes(text):
    """The bytes the snapshot for this entities text holds now (read_snapshot), or SnapshotRefused."""
    name = Path(entities_path(text)).name
    folder = results_folder()
    try:
        return read_snapshot(folder, name)
    except OSError as e:
        raise SnapshotRefused(f"results/{name} cannot be read ({os.strerror(e.errno or 0)})") from e
    finally:
        os.close(folder)


def write_snapshot(text):
    """The snapshot of this entities text under results/, written once: a private temporary file is created
    exclusively without following a link, written, made read-only, then hard-linked to the snapshot's name, which
    fails if anything holds that name, so the name never shows a partial file and nothing is overwritten. An existing
    snapshot is used only when it is a regular file holding exactly these bytes; anything else is refused. Returns
    the bytes as read back from the snapshot."""
    data, name = text.encode("utf-8"), Path(entities_path(text)).name
    folder = results_folder()
    try:
        temp = f".{name}.{secrets.token_hex(8)}.tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
        try:
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
                os.fchmod(fd, SNAPSHOT_MODE)
            finally:
                os.close(fd)
            try:
                os.link(temp, name, src_dir_fd=folder, dst_dir_fd=folder)
            except FileExistsError:
                pass                                    # made before: it must hold exactly these bytes, below
        finally:
            os.unlink(temp, dir_fd=folder)              # the temporary goes, whatever happened after it was made
        held = read_snapshot(folder, name)
    except OSError as e:                                # a full disk, a folder it may not write: the page says so
        raise SnapshotRefused(f"results/{name} could not be written ({os.strerror(e.errno or 0)}); check again "
                              "once results/ can take it") from e
    finally:
        os.close(folder)
    if held != data:
        raise SnapshotRefused(f"results/{name} exists with other content, and the page never overwrites a roster "
                              "file; remove it and check again")
    return held


def prepared(argv):
    """argv's runs as probatio prepares them (prepare_runs), with no call to any CLI: each run's results folder
    (run_folder) and plan fingerprint (plan_fingerprint), in run order. The entities file is read from probatio's
    folder, the run's working directory."""
    absolute = [str(P.HERE / x) if prev == "--entities" else x for prev, x in zip([None, *argv], argv)]
    a = P.parse_args_v4(absolute)
    runs = P.prepare_runs(a, False)[4]               # the page never writes --target-type
    return [(P.run_folder(a, run, len(runs) > 1), P.plan_fingerprint(run["calls"])) for run in runs]


def shown(path):
    """A path as the page shows it: relative to probatio's folder when it is inside it."""
    try:
        return str(Path(path).resolve().relative_to(P.HERE))
    except ValueError:
        return str(path)


# ---- one run at a time ---------------------------------------------------------------------------------------------------
class Runner:
    """One probatio run started by the page: program + argv in a subprocess in its own session (so a Ctrl-C at the
    page's terminal reaches the page, which passes one SIGINT on), its output read line by line: the --watch link,
    the newest TAIL lines, each run's result lines, the held page's line, and its exit code."""

    def __init__(self, program, argv, folders, env):
        self.argv, self.folders = list(argv), [str(f) for f in folders]
        self.lines, self.results, self.watch, self.held = deque(maxlen=TAIL), [], None, False
        self.stopping, self.code, self.lock = False, None, threading.Lock()
        self.started = time.strftime("%H:%M")
        self.proc = subprocess.Popen([*program, *self.argv], cwd=P.HERE, env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                     errors="replace", bufsize=1, start_new_session=True)
        self.reader = threading.Thread(target=self.read, name="probatio-ui-run", daemon=True)
        self.reader.start()

    def read(self):
        """Read the run's output to its end, then its exit code."""
        for raw in self.proc.stdout:
            line = raw.rstrip("\n").split("\r")[-1]
            with self.lock:
                self.lines.append(line)
                link = WATCH_LINK.search(line)
                if link and self.watch is None:
                    self.watch = link.group(1)
                result = RESULT_LINE.match(line)
                if result:
                    self.results.append([result.group(1), result.group(2)])
                if line.lstrip().startswith(HELD_LINE):
                    self.held = True
        self.proc.stdout.close()
        code = self.proc.wait()
        with self.lock:
            self.code = code

    def stop(self):
        """SIGINT to this run's process alone, as Ctrl-C would send it: the calls already running finish, no new one
        starts, and the same command resumes. False when the run has ended."""
        with self.lock:
            if self.code is not None:
                return False
            self.stopping = True
        self.proc.send_signal(signal.SIGINT)          # a no-op once the process has been reaped
        return True

    def wait(self, seconds=None):
        """Wait for the run's output to end and its exit code to be read."""
        self.reader.join(seconds)

    def status(self):
        """running, stopping, scored (finished, its live page held until Stop), finished, stopped or failed."""
        if self.code is None:
            return "stopping" if self.stopping else "scored" if self.held else "running"
        if any(kind == "Scorecard" for kind, _ in self.results):
            return "finished"
        return "stopped" if self.stopping else "failed"

    def state(self):
        with self.lock:
            return {"status": self.status(), "command": command_text(self.argv), "watch": self.watch,
                    "folders": [shown(f) for f in self.folders],
                    "results": [[kind, shown(path)] for kind, path in self.results],
                    "lines": list(self.lines), "code": self.code, "started": self.started}


# What the last passing Check approved, and the only thing Run may start: the exact argv, the exact bytes of its roster
# snapshot (None without one) and each run's plan fingerprint.
Approval = namedtuple("Approval", "argv entities fingerprints")


class Session:
    """The page's server side: its actions (preview, check, run, stop), the approval of the last Check if it passed,
    and the one run it may have going. Checks and Runs take turns (gate), so no Check can come between another's
    snapshot, dry run and approval, nor a Run's verification and start. program is how a dry run or a run starts
    probatio, env their environment (None: this process's), both for the tests."""

    def __init__(self, program=None, env=None):
        self.program = list(program or [sys.executable, *(["-B"] if sys.flags.dont_write_bytecode else []),
                                        str(P.HERE / "probatio.py")])
        self.env, self.lock, self.checked, self.runner = env, threading.Lock(), None, None
        self.gate = threading.RLock()                  # re-entrant only so a nested call in one thread cannot hang

    def actions(self):
        return {"/command": self.command, "/check": self.check, "/run": self.run, "/stop": self.stop}

    def child_env(self):
        """The environment of a dry run or a run: this process's (or the tests'), with Python's output unbuffered so
        the page sees each line as it is printed."""
        return dict(os.environ if self.env is None else self.env, PYTHONUNBUFFERED="1")

    def command(self, request):
        return preview(request.get("form"))

    def check(self, request):
        """The real --dry-run of the form's command, in a subprocess in probatio's folder: its preflight and the totals,
        or probatio's message verbatim. Its roster snapshot is written first (write_snapshot) and must hold the same
        bytes after the dry run; then the plan is prepared in this process from the same argv and snapshot, and the
        approval binds the argv, those bytes and each run's plan fingerprint. Only the last Check can approve, and
        only if it passed."""
        argv, entities = build_command(request.get("form"))
        reply = {"argv": argv, "command": command_text(argv), "words": command_words(argv),
                 "entities": entities_view(entities)}
        with self.gate:
            self.checked = None
            try:
                snapshot = write_snapshot(entities) if entities else None
            except SnapshotRefused as e:
                return dict(reply, ok=False, plan="", summary=None, error=str(e))
            try:
                proc = subprocess.run([*self.program, *argv, "--dry-run"], cwd=P.HERE, env=self.child_env(),
                                      stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=CHECK_SECONDS)
            except subprocess.TimeoutExpired:
                return dict(reply, ok=False, plan="", summary=None,
                            error=f"the dry run took longer than {CHECK_SECONDS} s; run the command with --dry-run in "
                                  "a terminal to see why")
            if proc.returncode != 0:
                return dict(reply, ok=False, plan=proc.stdout, summary=None, error=exit_message(None, proc.stderr))
            try:
                if entities and snapshot_bytes(entities) != snapshot:
                    raise SnapshotRefused(f"{entities_path(entities)} changed during the check; check again")
                plans = tuple(fingerprint for _, fingerprint in prepared(argv))
            except SnapshotRefused as e:
                return dict(reply, ok=False, plan=proc.stdout, summary=None, error=str(e))
            except SystemExit as e:
                return dict(reply, ok=False, plan=proc.stdout, summary=None, error=exit_message(e.code))
            self.checked = Approval(tuple(argv), snapshot, plans)
        return dict(reply, ok=True, plan=proc.stdout, summary=plan_summary(proc.stdout), error=None)

    def run(self, request):
        """Start the form's command exactly as shown and checked (--watch included), once it is confirmed, only when it
        is the command the last Check passed, and while no run of the page's is going."""
        if request.get("confirmed") is not True:
            return {"error": "confirm the run first"}
        argv, entities = build_command(request.get("form"))
        with self.gate:
            with self.lock:
                if self.runner is not None and self.runner.code is None:
                    return {"error": "a run is already going from this page; stop it before starting another"}
            approved = self.checked
            if approved is None or approved.argv != tuple(argv) or \
                    approved.entities != (entities.encode("utf-8") if entities else None):
                return {"error": "check this command first: Run starts only the command the last Check passed"}
            try:
                if entities and snapshot_bytes(entities) != approved.entities:
                    raise SnapshotRefused(f"{entities_path(entities)} is not the roster Check approved; check again")
                plans = prepared(argv)
            except SnapshotRefused as e:
                return {"error": str(e)}
            except SystemExit as e:
                return {"error": exit_message(e.code)}
            if tuple(fingerprint for _, fingerprint in plans) != approved.fingerprints:
                return {"error": "the plan is no longer the one Check approved; check again"}
            with self.lock:
                self.runner = Runner(self.program, list(approved.argv), [folder for folder, _ in plans],
                                     self.child_env())
                return {"run": self.runner.state()}

    def stop(self, request):
        with self.lock:
            runner = self.runner
        if runner is None or not runner.stop():
            return {"error": "no run is going"}
        return {"run": runner.state()}

    def state(self):
        with self.lock:
            runner = self.runner
        return {"defaults": defaults(), "run": runner.state() if runner is not None else None}

    def close(self, stream=None):
        """On the way out (Ctrl-C at the page's terminal, or the terminal closing): stop the page's run, if one is
        going, with one SIGINT, and wait for its output to end, so it finishes the calls it started and prints its
        message. A second Ctrl-C passes a second SIGINT on and waits STOP_SECONDS more."""
        with self.lock:
            runner = self.runner
        if runner is None or not runner.stop():
            return
        stream = stream or sys.stderr
        stream.write("  stopping the page's run: the calls already running finish first (Ctrl-C again to hurry it)\n")
        stream.flush()
        try:
            runner.wait()
        except KeyboardInterrupt:
            runner.stop()
            runner.wait(STOP_SECONDS)


def leave(signum, frame):
    """SIGTERM and SIGHUP end the page as Ctrl-C does, so its run is stopped, not orphaned."""
    raise KeyboardInterrupt


def main(argv, stream=None):
    """--ui: serve the page until Ctrl-C, printing its address on stderr once, as --watch prints its own; on the way
    out, stop the page's run, if one is going (Session.close)."""
    if list(argv) != ["--ui"]:
        raise SystemExit("--ui takes no other option; choose them on the page")
    defaults()                                       # probatio's defaults first: a bank that fails stops here
    session = Session()
    server = live.serve_page(PAGE, session.state, actions=session.actions())
    stream = stream or sys.stderr
    stream.write(f"  open it here: {server.url}\n  {BOUNDARY}\n")
    stream.flush()
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, leave)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        session.close(stream)
        server.shutdown()
        server.server_close()


# ---- the page ----------------------------------------------------------------------------------------------------------
# The --watch page's palette and type (live.PAGE_BASE), light and dark. The form is on the left, the answer (the command,
# its run once there is one, its plan) on the right and sticky; under 860 px they stack. The script builds every node
# with textContent, never innerHTML, reads the token from location.search (it is never embedded), and sends every
# action as a POST with the token in the X-Probatio-Token header. The defaults arrive with the first /state; nothing on
# the page is a default of its own, and --watch is not a choice: it is always on.
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>probatio</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer">
<link rel="icon" href="data:,">
<style>
""" + live.PAGE_BASE + """:root { color-scheme:light dark; }
main { max-width:1180px; margin:0 auto; padding:20px 16px 40px; }
header { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
h1 { font-size:20px; margin:0; font-weight:650; } header .sub { color:var(--muted); }
h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--ink2); margin:26px 0 8px;
  font-weight:600; display:flex; gap:12px; flex-wrap:wrap; align-items:baseline; }
h2 .aside { text-transform:none; letter-spacing:0; font-weight:500; color:var(--muted); margin-left:auto; }
.pill { font-size:13px; color:var(--ink2); display:inline-flex; align-items:center; gap:6px; margin-left:auto; }
.pill::before { content:""; width:9px; height:9px; border-radius:50%; background:var(--muted); }
.pill.running::before, .pill.scored::before { background:var(--good); }
.pill.stopped::before, .pill.failed::before, .pill.lost::before { background:var(--bad); }
.pill.stopping::before { background:var(--pending); } .pill.finished::before { background:var(--ink2); }
.layout { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,440px); gap:32px; align-items:start; }
.layout > * { min-width:0; }
.answer { position:sticky; top:12px; max-height:calc(100vh - 24px); overflow:auto; padding-bottom:8px; }
.row { display:grid; grid-template-columns:118px minmax(0,1fr); column-gap:14px; row-gap:4px; align-items:baseline;
  padding:8px 0; border-bottom:1px solid var(--line); }
.row > .k { color:var(--ink2); } .row .def { grid-column:2; font-size:12.5px; color:var(--muted); }
button { font:inherit; font-size:13px; color:var(--ink); background:var(--surface); border:1px solid var(--axis);
  border-radius:8px; padding:5px 12px; cursor:pointer; }
button:disabled { opacity:.45; cursor:default; }
button.primary { background:var(--ink); color:var(--surface); border-color:var(--ink); }
button.link { border:0; padding:0; background:none; color:var(--muted); text-decoration:underline; font-size:12.5px; }
.seg { display:flex; flex-wrap:wrap; gap:5px; }
.seg button { color:var(--ink2); border-color:var(--line); padding:4px 10px; }
.seg button[aria-pressed=true] { color:var(--ink); border-color:var(--ink2); background:var(--band); font-weight:600; }
.seg button.off { color:var(--muted); text-decoration:line-through; }
input, textarea { font:inherit; color:var(--ink); background:var(--surface); border:1px solid var(--line);
  border-radius:8px; padding:5px 9px; width:100%; }
input[type=number] { width:90px; } input[type=checkbox] { width:auto; margin:0 6px 0 0; }
textarea { font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; resize:vertical; }
input:focus, textarea:focus, button:focus-visible, summary:focus-visible { outline:2px solid var(--ink2);
  outline-offset:1px; }
details.setup, details.more { margin-top:6px; } summary { cursor:pointer; color:var(--muted); font-size:12.5px; }
details.setup .seg { margin-top:6px; }
.note { color:var(--muted); font-size:12.5px; margin:4px 0 10px; }
.flag { margin:0 0 10px; font-size:13px; } .flag .label { color:var(--pending); font-weight:600; }
.flag .hint { color:var(--muted); }
.roster { border:1px solid var(--line); border-radius:10px; padding:10px 12px; margin:0 0 12px; }
.roster .top { display:flex; align-items:baseline; gap:10px; } .roster .top b { font-weight:600; }
.roster .top .link { margin-left:auto; }
.lists { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; margin-top:8px; }
.lists .k { font-size:12.5px; color:var(--muted); display:flex; justify-content:space-between; margin:0 0 4px; }
.lists .k span:first-child { color:var(--ink2); }
.box { border:1px solid var(--line); border-radius:10px; background:var(--band); }
pre { margin:0; font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
pre.cmd { padding:12px; white-space:pre-wrap; font-size:13px; } pre.cmd span { white-space:nowrap; }
pre.cmd span.long { white-space:normal; overflow-wrap:anywhere; }
.cmdbar { display:flex; gap:8px; align-items:center; border-top:1px solid var(--line); padding:6px 8px 6px 12px;
  font-size:12.5px; color:var(--muted); } .cmdbar button { margin-left:auto; }
.labels { color:var(--muted); font-size:12.5px; margin:8px 0 0; } .labels .warn { color:var(--pending); }
.err { color:var(--bad); margin:8px 0 0; white-space:pre-wrap; overflow-wrap:anywhere; }
.err b { font-weight:600; }
.files { font-size:12.5px; color:var(--ink2); margin:8px 0 0; } .files code { overflow-wrap:anywhere; }
.files pre { background:var(--band); border-radius:8px; padding:8px 10px; margin:6px 0 0; overflow:auto; }
.actions { display:flex; gap:8px; margin:12px 0 0; flex-wrap:wrap; align-items:center; }
.actions .why { color:var(--muted); font-size:12.5px; }
.confirm { border:1px solid var(--axis); border-radius:10px; padding:10px 12px; margin:12px 0 0; }
.confirm p { margin:0 0 10px; }
.big { font-size:18px; font-weight:600; font-variant-numeric:tabular-nums; }
.muted { color:var(--muted); font-size:12.5px; } .hintline { color:var(--pending); margin:4px 0 0; }
.out { background:var(--band); border-radius:8px; padding:10px; margin:8px 0 0; max-height:260px; overflow:auto;
  white-space:pre; }
.kv { margin:6px 0 0; font-size:13px; color:var(--ink2); } .kv span { color:var(--muted); }
.kv code { overflow-wrap:anywhere; } .runbar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
.runbar a { color:var(--ink); font-weight:600; } .runbar .pill { margin-left:0; }
.empty { color:var(--muted); }
footer { margin-top:30px; padding-top:12px; border-top:1px solid var(--line); color:var(--ink2); font-size:13px; }
@media (max-width:860px) {
  .layout { grid-template-columns:minmax(0,1fr); } .answer { position:static; max-height:none; overflow:visible; }
  .row { grid-template-columns:1fr; } .row .def { grid-column:1; }
}
</style></head>
<body><main>
<header><h1>probatio</h1><span class="sub">choose a run, see its command, check it, run it</span>
<span class="pill" id="pill">connecting</span></header>
<div class="layout">
<div id="form"><section id="measure"></section><section id="against"></section><section id="how"></section></div>
<aside class="answer">
<h2>Command <span class="aside" id="cmdnote"></span></h2>
<div class="box"><pre class="cmd" id="cmd">…</pre>
<div class="cmdbar"><span id="where"></span><button type="button" id="copy">Copy</button></div></div>
<div id="labels" class="labels"></div><div id="files" class="files"></div><div id="error" class="err"></div>
<div class="actions"><button type="button" class="primary" id="check">Check</button>
<button type="button" id="run">Run…</button><span class="why" id="why"></span></div>
<div id="confirm"></div>
<section id="runsec" hidden><h2>Run</h2><div id="runbox"></div></section>
<h2>Plan <span class="aside">from the dry run</span></h2><div id="plan"></div>
</aside>
</div>
<footer id="foot"></footer>
</main>
<script>
"use strict";
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const CLASS_WORDS = {I: "I · professed", II: "II · behavioural", III: "III · unobserved"};
let D = null, F = null, said = null, plan = null, run = null, asked = 0, timer = null, confirming = false;
let checking = false, marks = [];
const $ = id => document.getElementById(id);
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
}
function add(parent, ...kids) { for (const k of kids) if (k) parent.appendChild(k); return parent; }
const names = list => list.map(e => typeof e === "string" ? e : e.full);
const lines = text => String(text || "").split("\\n").map(s => s.trim()).filter(Boolean);
const same = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
const keyOf = r => r ? r.command + "\\n" + (r.entities ? r.entities.text : "") : null;
const agentName = k => (D.agents.find(a => a[0] === k) || [k, k])[1];
const going = () => run && run.code === null;
async function post(path, body) {
  const res = await fetch(path + location.search, {method: "POST", cache: "no-store",
    headers: {"Content-Type": "application/json", "X-Probatio-Token": TOKEN}, body: JSON.stringify(body)});
  if (!res.ok) throw new Error(String(res.status));
  return res.json();
}
function setPill(cls, text) { const p = $("pill"); p.className = "pill " + cls; p.textContent = text; }
function defaultRoster(t) {
  return {comparators: names(D.rosters[t].comparators).join("\\n"), controls: names(D.rosters[t].controls).join("\\n")};
}
function fresh() {
  const d = D.default, rosters = {}, targets = {};
  for (const t of D.types) { rosters[t] = defaultRoster(t); targets[t] = names(D.rosters[t].targets).join("\\n"); }
  return {types: [d.type], name: "", targets, rosters, agent: d.agent, tier: d.tier, classes: d.classes.slice(),
          memory: d.memory, auto_memory: d.auto_memory, effort: d.effort, model: d.model || "", jobs: String(d.jobs)};
}
// ---- the form
function choices(key, values, opts) {
  opts = opts || {};
  const box = el("div", "seg"), order = values.map(v => v[0]);
  box.setAttribute("role", "group");
  const mark = () => box.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed",
    String(opts.multi ? F[key].includes(b.value) : F[key] === b.value)));
  for (const [value, word, title] of values) {
    const b = el("button", null, word);
    b.type = "button"; b.value = value;
    if (title) { b.title = title; b.classList.add("off"); }
    b.addEventListener("click", () => {
      if (opts.multi) {
        const has = F[key].includes(value);
        if (has && F[key].length === 1) return;                 // one at least stays chosen
        F[key] = order.filter(v => v === value ? !has : F[key].includes(v));
      } else F[key] = value;
      marks = marks.filter(m => m.box.isConnected);
      marks.forEach(m => m.mark());
      if (opts.after) opts.after();
      changed();
    });
    box.appendChild(b);
  }
  marks.push({box, mark}); mark();
  return box;
}
function grow(t) { t.rows = Math.max(1, t.value.split("\\n").length); }
function field(holder, key, place, multiline, after) {
  const e = el(multiline ? "textarea" : "input");
  if (!multiline) e.type = "text";
  e.value = holder[key]; e.placeholder = place || ""; e.spellcheck = false; e.autocomplete = "off";
  if (multiline) grow(e);
  e.addEventListener("input", () => { holder[key] = e.value; if (multiline) grow(e); if (after) after(); changed(); });
  return e;
}
function row(label, control, note) {
  return add(el("div", "row"), el("div", "k", label), control, note ? el("div", "def", note) : null);
}
function measure() {
  const s = $("measure"), cross = F.types.length > 1;
  s.replaceChildren(el("h2", null, "What to measure"));
  add(s, row("type", choices("types", D.types.map(t => [t, t]), {multi: true, after: build}),
    "default " + D.default.type + " · " + (cross ? "two or three are compared with each other: " + D.labels.cross :
      "one is scored against an invented control of its type; pick two or three to compare them")));
  add(s, row("your name", field(F, "name", "e.g. Jane Doe"), cross ? "--name alone does not put you in a comparison: " +
    "to include yourself, type your own name in the individual row, with individual chosen" :
    "--name: the individual a run scores by default"));
  for (const t of F.types) {
    const place = cross ? (t === "individual" ? "your own name, to include yourself" : "none (optional)") :
      t === "individual" ? "you, by your name" : "the " + t + " to score";
    add(s, row(t, field(F.targets, t, place, true),
      (cross ? "named targets, optional; " : "the target; ") + "one per line, each --target " + t + "=NAME"));
  }
}
function against() {
  const s = $("against"), cross = F.types.length > 1, parts = cross ? ["controls"] : ["comparators", "controls"];
  s.replaceChildren(el("h2", null, "Against whom"));
  const flag = add(el("p", "flag"), el("span", "label"), el("span", "hint",
    " · A control should be a name the model does not know."));
  flag.id = "custom"; flag.hidden = true; s.appendChild(flag);
  s.appendChild(el("p", "note", cross ? "Compared types meet each type's controls, its invented names (the first two " +
    "are used); comparators are for a single type." : "The target faces the comparators, and is scored against the " +
    "first control. One name per line; the defaults are probatio's."));
  for (const t of F.types) {
    const card = el("div", "roster"), reset = el("button", "link", "reset to defaults"), counts = {};
    reset.type = "button";
    add(card, add(el("div", "top"), el("b", null, t), reset));
    const grid = el("div", "lists");
    const mark = () => {
      let edited = false;
      for (const part of parts) {
        const [lo, hi] = D.counts[part], n = lines(F.rosters[t][part]).length;
        counts[part].textContent = n + " · " + lo + " to " + hi + " · default " + D.rosters[t][part].length;
        edited = edited || !same(lines(F.rosters[t][part]), names(D.rosters[t][part]));
      }
      reset.hidden = !edited;
    };
    for (const part of parts) {
      const head = add(el("div", "k"), el("span", null, part), el("span"));
      counts[part] = head.lastChild;
      add(grid, add(el("div"), head, field(F.rosters[t], part, "", true, mark)));
    }
    reset.addEventListener("click", () => { F.rosters[t] = defaultRoster(t); against(); changed(); });
    card.appendChild(grid); s.appendChild(card); mark();
  }
}
function how() {
  const s = $("how"), d = D.default, planned = D.planned[F.tier] || D.classes;
  s.replaceChildren(el("h2", null, "How to run"));
  add(s, row("agent", choices("agent", D.agents), d.agent ? "default " + agentName(d.agent) :
    "no default: choose one; it runs on your own login"));
  const setup = add(el("details", "setup"), el("summary", null, "setup check"),
    choices("tier", D.setup.map(t => [t, t + ": the calibration's setup plan"]), {after: how}));
  setup.open = D.setup.includes(F.tier);
  add(s, row("tier", add(el("div"), choices("tier", D.tiers.map(t => [t, t]), {after: how}), setup),
    "default " + d.tier + " · the plan shows the calls and the time"));
  add(s, row("classes", choices("classes", D.classes.map(k => [k, CLASS_WORDS[k] || k,
    planned.includes(k) ? null : "not run at the " + F.tier + " tier"]), {multi: true}),
    "default all, which at " + F.tier + " is " + planned.join(", ")));
  add(s, row("memory", choices("memory", D.memory.map(m => [m, m])),
    "default " + d.memory + " · both asks each item with and without the memory files"));
  add(s, row("effort", choices("effort", D.efforts.map(e => [e, e])), "default " + d.effort));
  add(s, row("model", field(F, "model", "the CLI's default"), "default: the CLI's own model"));
  add(s, row("watch", el("div", null, "always on: you follow the run live in your browser (--watch)"),
    "the command shows it, so Run starts exactly the command Check checked"));
  const jobs = field(F, "jobs", String(d.jobs)); jobs.type = "number"; jobs.min = "1";
  const more = add(el("details", "more"), el("summary", null, "more: jobs, auto memory"),
    row("jobs", jobs, "default " + d.jobs + " calls at once"),
    row("auto memory", choices("auto_memory", D.auto_memory.map(m => [m, m])),
      "default " + d.auto_memory + " · off disables auto memory in both conditions"));
  more.open = String(F.jobs) !== String(d.jobs) || F.auto_memory !== d.auto_memory;
  s.appendChild(more);
}
function build() { marks = []; measure(); against(); how(); }
// ---- the answer
function changed() {
  confirming = false;
  clearTimeout(timer); timer = setTimeout(refresh, 150);
  render();
}
async function refresh() {
  const n = ++asked;
  try { const r = await post("/command", {form: F}); if (n === asked) said = r; }
  catch (err) { if (n === asked) said = null; }
  render();
}
function render() {
  const cmd = $("cmd"); cmd.replaceChildren();
  if (said) said.words.forEach((w, i) => add(cmd, i ? document.createTextNode(" ") : null,
    el("span", w.length > 40 ? "long" : null, w)));                     // a flag stays whole; a long path may break
  else cmd.textContent = "…";
  $("where").textContent = D ? "in the probatio folder" : "";
  const labels = $("labels"); labels.replaceChildren();
  (said ? said.labels : []).forEach((l, i) => add(labels, i ? document.createTextNode(" · ") : null,
    el("span", /not (calibrated|registered)/.test(l) ? "warn" : null, l)));
  const files = $("files"); files.replaceChildren();
  if (said && said.entities) {
    add(files, document.createTextNode("Your roster edits go in "), el("code", null, said.entities.path),
      document.createTextNode(", a read-only file named by its content, written when you check."));
    const det = add(el("details"), el("summary", null, "the file"), el("pre", null, said.entities.text));
    files.appendChild(det);
  }
  const custom = $("custom");
  if (custom) { custom.hidden = !(said && said.entities); custom.firstChild.textContent = said && said.roster || ""; }
  const error = $("error"); error.replaceChildren();
  if (said && said.error) add(error, el("b", null, "probatio: "), document.createTextNode(said.error));
  const stale = plan && plan.ok && keyOf(plan) !== keyOf(said);
  $("check").disabled = !said || !!said.error || checking;
  const ready = plan && plan.ok && !stale && !going();
  $("run").disabled = !ready;
  $("why").textContent = going() ? "a run is going" : !plan || !plan.ok ? "check first" : stale ?
    "the command changed: check it again" : "";
  renderConfirm(ready); renderPlan(stale);
}
function renderConfirm(ready) {
  const box = $("confirm"); box.replaceChildren();
  if (!confirming || !ready) return;
  const sm = plan.summary || {}, who = agentName(F.agent);
  const size = [sm.calls !== null && sm.calls !== undefined ? sm.calls + " calls" : null, sm.time].filter(Boolean);
  const p = el("p", null, "Run this command? " + (size.length ? size.join(", ") : "See the plan for its size") +
    ", on " + who + " with " + (F.model.trim() ? "the model " + F.model.trim() : "the CLI's default model") +
    " at effort " + F.effort + ". It uses your own " + who + " login and quota.");
  const go = el("button", "primary", "Start the run"), no = el("button", null, "Cancel");
  go.type = no.type = "button";
  go.addEventListener("click", start);
  no.addEventListener("click", () => { confirming = false; render(); });
  box.appendChild(add(el("div", "confirm"), p, add(el("div", "actions"), go, no)));
}
function renderPlan(stale) {
  const box = $("plan"); box.replaceChildren();
  if (checking) { box.appendChild(el("p", "empty", "Running the dry run…")); return; }
  if (!plan) { box.appendChild(el("p", "empty", "Check runs probatio's dry run of this exact command: every guard, " +
    "the calls and the time, the names as the items write them. It makes no model call.")); return; }
  if (stale) box.appendChild(el("p", "muted", "This plan is for an earlier command."));
  if (!plan.ok) {
    add(box, add(el("div", "err"), el("b", null, "probatio: "), document.createTextNode(plan.error)));
    return;
  }
  const sm = plan.summary || {};
  const head = [sm.calls !== null && sm.calls !== undefined ? sm.calls + " calls" : null, sm.time].filter(Boolean);
  if (head.length) box.appendChild(el("div", "big", head.join(" · ")));
  if (sm.rates) box.appendChild(el("div", "muted", sm.rates));
  if (sm.hint) box.appendChild(el("div", "hintline", sm.hint));
  const det = add(el("details"), el("summary", null, "the whole preflight"), el("pre", "out", plan.plan));
  det.open = true;
  box.appendChild(det);
}
const RUN_WORDS = {running: "running", stopping: "stopping: the calls already running finish first",
  scored: "scored; its live page stays up until you stop it", finished: "finished", stopped: "stopped; the same " +
  "command resumes it", failed: "failed"};
function renderRun() {
  const box = $("runbox"), out = box.querySelector("pre"), keep = !out || out.scrollHeight - out.scrollTop -
    out.clientHeight < 8;
  box.replaceChildren();
  $("runsec").hidden = !run;
  if (!run) return;
  const bar = el("div", "runbar");
  bar.appendChild(el("span", "pill " + run.status, (RUN_WORDS[run.status] || run.status) +
    (run.status === "failed" && run.code !== null ? " (exit " + run.code + ")" : "")));
  if (run.watch && going()) {
    const a = el("a", null, "Open the live view");
    a.href = run.watch; a.target = "_blank"; a.rel = "noopener noreferrer";
    bar.appendChild(a);
  }
  if (going()) {
    const stop = el("button", null, run.status === "stopping" ? "Stopping…" : "Stop");
    stop.type = "button"; stop.disabled = run.status === "stopping";
    stop.addEventListener("click", async () => { stop.disabled = true; try { await post("/stop", {}); } catch (e) {} });
    bar.appendChild(stop);
  }
  box.appendChild(bar);
  const kv = (k, v) => add(el("div", "kv"), el("span", null, k + " "), el("code", null, v));
  box.appendChild(kv("started " + run.started + ":", run.command));
  for (const f of run.folders) box.appendChild(kv("results folder", f));
  for (const [kind, path] of run.results) box.appendChild(kv(kind.toLowerCase(), path));
  const pre = el("pre", "out", run.lines.join("\\n") || "(no output yet)");
  box.appendChild(pre);
  if (keep) pre.scrollTop = pre.scrollHeight;
}
async function check() {
  checking = true; confirming = false; render();
  try { plan = await post("/check", {form: F}); }
  catch (err) { plan = {ok: false, error: "the page could not reach its server (" + err.message + ")"}; }
  checking = false; render();
}
async function start() {
  confirming = false; render();
  try {
    const r = await post("/run", {form: F, confirmed: true});
    if (r.error) { plan = Object.assign({}, plan, {ok: false, error: r.error}); }
    else run = r.run;
  } catch (err) { plan = {ok: false, error: "the page could not reach its server (" + err.message + ")"}; }
  render(); renderRun();
}
$("check").addEventListener("click", check);
$("run").addEventListener("click", () => { confirming = true; render(); });
$("copy").addEventListener("click", async () => {
  const text = said ? said.command : "";
  try { await navigator.clipboard.writeText(text); $("copy").textContent = "Copied"; }
  catch (err) { const r = document.createRange(); r.selectNodeContents($("cmd"));
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); $("copy").textContent = "Selected"; }
  setTimeout(() => { $("copy").textContent = "Copy"; }, 1500);
});
async function tick() {
  try {
    const res = await fetch("/state" + location.search, {cache: "no-store"});
    if (!res.ok) throw new Error(String(res.status));
    const s = await res.json();
    if (!D) { D = s.defaults; F = fresh(); build(); $("foot").textContent = D.boundary; refresh(); }
    const was = going();
    run = s.run;
    renderRun();
    if (was !== going()) render();
    setPill(run ? run.status : "ready", run ? run.status : "ready");
  } catch (err) { setPill("lost", "the page's server has stopped"); }
}
tick(); setInterval(tick, 1000);
</script></body></html>
"""
