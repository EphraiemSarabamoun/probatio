"""The display boundary, checked by enumeration (README, "What reaches your terminal"): for the cases listed here,
nothing a user, a file or an agent CLI controls reaches probatio's terminal output, an exported text file or the data
the pages receive raw, whatever call site forgets.

The fields are listed, never sampled: every string-valued option FROM THE ARGUMENT PARSERS THEMSELVES (v4_parser and
v3_parser, so an option added later is covered without anyone remembering it), every string field of a saved run.json
(single-type and compare, found by walking the record), the agent CLI's own --version output, a memory file's path and
title, and a discovered config folder's name. Each takes each hostile value (HOSTILE: an escape sequence that clears the
screen, a carriage return, a newline followed by a line that looks like probatio's own, a backspace run, a right-to-left
override, a zero-width joiner, a 10,000-character string) through every path that can show it: --dry-run, a refused run,
a run with the repo's stand-in agent (tests/fake_claude.py) to its scorecard (single-type and compare), --rescore of the
folder it made, and the --ui form's preview. Every case runs through probatio's own entry point (filtered_streams and
run_command, as the command line runs) and asserts (problems): no control or invisible format character but newline and
tab on standard output, standard error or in any file the run wrote; the injected newline never begins a line; no shown
text holds more of the long value than DISPLAY_CHARS; no string of the --watch page's data or of the --ui form's preview
and memory block is longer than DISPLAY_CHARS either (FORM_WHOLE aside: argv, the command's Copy text, the roster file's
text), a value built around a displayed token included ("NAME (organization)", the line that a typed folder is missing),
while Check's preflight and a run's lines are not checked so; and the filesystem got the ORIGINAL string (a folder with
a hostile name is found and used). It checks what probatio prints, writes and sends to the pages, not what a page's
script builds from that data (TestLivePageInChrome reads the --watch page's text for eight cases, not its attributes,
and the --ui page is not rendered), and its long path is one 10,000-character name, which no filesystem accepts, so a
long folder that exists is not among the cases. The cases run in parallel (forked workers) with a small bootstrap, which
the display does not depend on. No agent or network is called, and nothing of the environment is printed: each case sets
HOME, PATH and the fake's own variables in memory and compares nothing else.

    python3 -m unittest tests.test_display_boundary
"""
import contextlib
import io
import json
import multiprocessing
import os
import re
import shutil
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import live as L  # noqa: E402
import ordo as O  # noqa: E402
import probatio as P  # noqa: E402
import ui as U  # noqa: E402

FAKE = REPO / "tests" / "fake_claude.py"
PERSON = "Wilhelmina Quarrender"                    # an invented name
FORGED = "probatio v4: claude (forged"             # a line that looks like probatio's own first line
HOSTILE = {
    "clear-screen": "x\x1b[2J\x1b[Hcleared",
    "carriage-return": "abc\rprobatio overwrote this",
    "newline-forged": "abc\n" + FORGED + "), model forged",
    "backspace-run": "abcdef" + "\b" * 6 + "XYZ",
    "right-to-left": "abc\u202efed",
    "zero-width-joiner": "ab\u200dcd",
    "long-10000": "W" * 10000,
}
LIVE_EXTRA = {"lone-surrogate": "ab\ud800" + FORGED, "line-separator": "ab\u2028" + FORGED}   # the --watch page's two more
LIVE_FIELDS = {"single": ("model", "cli", "target", "control", "name"), "cross": ("model", "cli", "compare", "named",
                                                                               "name")}
LONG_RUN = re.compile("W{%d,}" % (P.DISPLAY_CHARS + 1))   # more of the long value than the cap allows
SHOWN_SUFFIXES = (".md", ".svg", ".csv")                    # exported text a reader reads: the cap applies there too
RECORD_SKIP = ("transcript.jsonl",)                         # the CLI's own stream, kept verbatim as evidence
FAST_B = 40                                                 # the bootstrap's replicates in these cases
# Which options name a filesystem path, and what must stand there for a run to use it (the identity check).
PATH_OPTIONS = {"--workdir": "folder", "--local": "folder", "--config-dir": "config", "--bank": "bank",
                "--out": "out", "--entities": "entities", "--rescore": "run"}
# The form's strings that are not one displayed value, and why the complete-value cap does not apply to them.
FORM_WHOLE = {("argv",): "the command line Run starts, never shown",
              ("command",): "the command's words joined for Copy and for the page's check that the command is "
                            "unchanged; the page draws each word apart, and each is displayed and within the cap",
              ("entities", "text"): "the roster file's own text, shown as the file"}
FORM_FIELDS = {"--agent": "agent", "--tier": "tier", "--memory": "memory", "--auto-memory": "auto_memory",
               "--effort": "effort", "--model": "model", "--config-dir": "config_dir", "--target": "target",
               "--class": "classes", "--compare": "types", "--target-type": "types"}


# ---- what a case checks --------------------------------------------------------------------------------------------
def unseen_in(text):
    """The control and invisible format characters in text (probatio's own rule), newline and tab aside."""
    return sorted({hex(ord(ch)) for ch in text if P.unseen(ch) and ch not in "\n\t"})


def forged(text):
    """Whether a line of text begins with the forged line (after any indent)."""
    return any(line.lstrip().startswith(FORGED) for line in text.splitlines())


def problems(texts, shown, identity=None):
    """What is wrong in one case's outputs ({name: text}; shown names those read as text, where the cap applies), and
    whether the filesystem got the original string (identity: None when the case does not ask)."""
    found = []
    for name, text in texts.items():
        if unseen_in(text):
            found.append(f"{name}: raw {','.join(unseen_in(text))}")
        if forged(text):
            found.append(f"{name}: forged line")
        if name in shown and LONG_RUN.search(text):
            found.append(f"{name}: uncapped long value")
        if "Traceback (most recent call last)" in text:
            found.append(f"{name}: a traceback, not a plain refusal")
    if identity is False:
        found.append("the filesystem did not get the original string")
    return found


def over_cap(value, skip=(), path=()):
    """Every string in a JSON answer longer than DISPLAY_CHARS, as "path: length", the key paths in skip (and what is
    under them) left out. The enumeration asks it of the --watch page's data and of the form's preview and memory block
    (FORM_WHOLE aside), so for the cases enumerated a value built around a token is checked, not only the token."""
    if any(path[:len(s)] == s for s in skip):
        return []
    if isinstance(value, str):
        return [f"{'/'.join(map(str, path))}: {len(value)} characters"] if len(value) > P.DISPLAY_CHARS else []
    items = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
    return [x for k, v in items for x in over_cap(v, skip, path + (k,))]


def form_row(field, value_name, reply, memory):
    """One form case's row: the preview's and the memory block's strings (problems), and any value of them longer
    than the cap (over_cap), FORM_WHOLE aside."""
    texts = {"preview": json_strings(reply, skip=("argv",)), "memory": json_strings(memory)}
    found = problems(texts, set(texts)) + [f"value over the cap: preview {x}" for x in over_cap(reply, FORM_WHOLE)] \
        + [f"value over the cap: memory {x}" for x in over_cap(memory)]
    return {"field": field, "value": value_name, "path": "form preview", "exit": 0, "result": "FAIL" if found else
            "pass", "identity": None, "problems": found}


def folder_texts(folder):
    """Every file a run wrote under folder, as text (the CLI's own stream records aside), and the shown ones."""
    texts = {}
    if folder is not None and P.is_folder(folder):
        for p in sorted(Path(folder).rglob("*")):
            if p.is_file() and not p.is_symlink() and p.name not in RECORD_SKIP:
                texts[str(p.relative_to(folder))] = p.read_text(encoding="utf-8", errors="replace")
    return texts, {k for k in texts if k.endswith(SHOWN_SUFFIXES)}


def json_strings(value, skip=()):
    """Every string inside a JSON answer, one per line, keys in skip left out (the process's own argv)."""
    if isinstance(value, str):
        return value + "\n"
    if isinstance(value, dict):
        return "".join(json_strings(v, skip) for k, v in value.items() if k not in skip)
    if isinstance(value, list):
        return "".join(json_strings(v, skip) for v in value)
    return ""


# ---- the fields ----------------------------------------------------------------------------------------------------
def string_options(parser):
    """The string-valued options of an argument parser, read from the parser itself: every option that takes a value
    (nargs not 0) whose type is not a number."""
    return [a.option_strings[-1] for a in parser._actions
            if a.option_strings and a.nargs != 0 and a.type not in (int, float)]


def string_leaves(value, path=()):
    """Every string in a JSON record, as (path, value): dict values and list items, recursively."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from string_leaves(v, path + (k,))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from string_leaves(v, path + (i,))


def set_leaf(record, path, value):
    for step in path[:-1]:
        record = record[step]
    record[path[-1]] = value


# ---- one scratch world, and probatio's own entry point ---------------------------------------------------------------
def stand_ins(folder, version=None):
    """claude: the repo's stand-in (fake_claude.py, auto mode), whose --version prints version when given; codex: a
    stand-in that refuses every call. First on PATH."""
    folder.mkdir(parents=True, exist_ok=True)
    claude = folder / "claude"
    if version is None:
        claude.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    else:
        claude.write_text(f"#!{sys.executable}\nimport os, sys\nif sys.argv[1:] == ['--version']:\n"
                          f"    sys.stdout.write({version!r} + '\\n')\n    sys.exit(0)\n"
                          f"os.execv(sys.executable, [sys.executable, {str(FAKE)!r}] + sys.argv[1:])\n")
    (folder / "codex").write_text('#!/bin/sh\necho "stand-in: no agent call" >&2\nexit 97\n')
    for name in ("claude", "codex"):
        (folder / name).chmod(0o755)
    return folder


class Box:
    """One scratch world: a home with a default config folder, a working folder, the stand-ins, and the environment
    its cases run in (HOME, PATH and the fake's mode set; no config variable of this shell)."""

    def __init__(self, root, version=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.home = self.root / "home"
        (self.home / ".claude" / "projects").mkdir(parents=True, exist_ok=True)
        (self.home / ".claude" / ".claude.json").write_text("{}\n")
        (self.home / ".claude" / "CLAUDE.md").write_text("# Notes\nNothing about anyone.\n")
        self.work = self.root / "work"
        self.work.mkdir(exist_ok=True)
        self.bin = stand_ins(self.root / "bin", version)
        self.calls = self.root / "calls.jsonl"
        self.env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        self.env.update(HOME=str(self.home), PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                        FAKE_CLAUDE="auto", FAKE_CLAUDE_CALLS=str(self.calls))

    def run(self, argv):
        """argv through probatio's own entry point, as the command line runs it (filtered_streams, run_command):
        (exit code, stdout, stderr) as they reached the terminal. The fake's record of the calls starts empty."""
        if self.calls.exists():
            self.calls.unlink()
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with mock.patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err), P.filtered_streams():
            try:
                P.run_command(argv)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
            except Exception:           # recorded, so the case fails on its traceback rather than stopping the table
                code = "raised"
                err.write(traceback.format_exc())
        return code, out.getvalue(), err.getvalue()

    def call_folders(self):
        """The working folders the fake recorded for the run's calls (its --version probe aside)."""
        if not self.calls.exists():
            return set()
        return {r["cwd"] for r in map(json.loads, self.calls.read_text().splitlines()) if r["argv"] != ["--version"]}


def single_argv(box, folder, **given):
    """A single-type run's command (the pinned bank, smoke tier, Class I), given options replacing its own (an
    option's name without its dashes); a value of None drops the option."""
    opts = {"agent": "claude", "target-type": "organization", "target": "Mozilla", "tier": "smoke", "class": "I",
            "workdir": str(box.work), "jobs": "8", "out": str(folder)}
    opts.update(given)
    return [w for k, v in opts.items() if v is not None for w in (f"--{k}", v)]


def compare_argv(box, folder, **given):
    """A comparison's command (individual against organization, a named individual, quick tier, Class I)."""
    opts = {"agent": "claude", "compare": "individual:organization", "target": f"individual={PERSON}",
            "tier": "quick", "class": "I", "workdir": str(box.work), "jobs": "8", "out": str(folder)}
    opts.update(given)
    return [w for k, v in opts.items() if v is not None for w in (f"--{k}", v)]


def v3_argv(box, **given):
    """A v3 dry run's command, given options replacing its own. --local names an empty folder, so v3 reads its
    datasets from there (and refuses, as none is there) and never downloads them."""
    local = box.root / "no-datasets"
    local.mkdir(exist_ok=True)
    opts = {"agent": "claude", "name": PERSON, "tier": "smoke", "workdir": str(box.work), "local": str(local)}
    opts.update(given)
    return [w for k, v in opts.items() if v is not None for w in (f"--{k}", v)] + ["--dry-run"]


def outputs(code, out, err, folder=None):
    files, shown = folder_texts(folder)
    return dict({"stdout": out, "stderr": err}, **files), shown | {"stdout", "stderr"}


def row(field, value, path, code, texts, shown, identity=None):
    found = problems(texts, shown, identity)
    return {"field": field, "value": value, "path": path, "exit": code, "result": "FAIL" if found else "pass",
            "identity": identity, "problems": found}


# ---- the cases -------------------------------------------------------------------------------------------------------
def place(option, value, where, single_run):
    """The command-line value for option: for a path option a path under where whose last name holds the hostile
    value, made to exist (a folder, a config folder, a link to the pinned bank, an entities file, a copy of a finished
    run) when the filesystem takes the name; else the value itself. Returns (value, path or None)."""
    what = PATH_OPTIONS.get(option)
    if what is None:
        return value, None
    path = where / ("x" + value)
    try:
        if what in ("folder", "config"):
            path.mkdir(parents=True)
            if what == "config":
                (path / "projects").mkdir()
                (path / ".claude.json").write_text("{}\n")
                (path / "CLAUDE.md").write_text("# Chosen notes\n")
        elif what == "bank":
            path.symlink_to(P.BANK, target_is_directory=True)
        elif what == "entities":
            path.write_text(json.dumps({"organization": {"controls": ["Thirlbeck Systems"]}}))
        elif what == "run":
            shutil.copytree(single_run, path, symlinks=True)
    except OSError:                         # a name the filesystem refuses (too long): the refused path
        pass
    return str(path), path


def option_case(job):
    """Every path for one parser option with one hostile value: the dry run (or the refusal it is), a refusal for a
    missing path, for a value the run takes a run with the stand-in to its scorecard and --rescore of that folder,
    single-type and compare, and the form's preview for an option the form has."""
    parser_name, option, value_name, root, single_run = job
    value, rows = HOSTILE[value_name], []
    box = Box(Path(tempfile.mkdtemp(dir=root)))
    where = box.root / "paths"
    where.mkdir()
    given, path = place(option, value, where, single_run)
    made = path is not None and os.path.lexists(path)
    field, key = f"{parser_name} {option}", option[2:]
    if option == "--rescore":
        argv = ["--rescore", given]
    elif parser_name == "v3":
        argv = v3_argv(box, **{key: given})
    else:
        argv = single_argv(box, where / "out", **{key: given}) + ["--dry-run"]
    code, out, err = box.run(argv)
    texts, shown = outputs(code, out, err, path if option == "--rescore" and made else None)
    identity = None
    if option == "--rescore" and made:
        identity = code == 0 and (Path(given) / "scorecard.json").is_file() and (Path(given) / "previous").is_dir()
    elif PATH_OPTIONS.get(option) == "config" and made and code == 0:
        identity = P.display(Path(given) / "CLAUDE.md") in out
    rows.append(row(field, value_name, "refused" if code else "rescore" if option == "--rescore" else "dry-run",
                    code, texts, shown, identity))
    taken = code == 0                   # the dry run took the value: it is then run, and rescored
    if PATH_OPTIONS.get(option) in ("folder", "config", "bank", "entities") and made:
        gone = str(Path(given).parent / ("gone" + Path(given).name))
        code, out, err = box.run([gone if w == given else w for w in argv])
        texts, shown = outputs(code, out, err)
        rows.append(row(field, value_name, "refused", code, texts, shown, None if code else False))
    if taken and parser_name == "v4" and option != "--rescore":
        rows += run_paths(box, field, option, key, given, value_name, where)
    if option in FORM_FIELDS:
        rows.append(form_case(box, field, option, value, value_name))
    shutil.rmtree(box.root, ignore_errors=True)
    return rows


def run_paths(box, field, option, key, given, value_name, where):
    """A run with the stand-in to its scorecard, single-type and compare, then --rescore of the folder it made; a
    path option's run must use the original path."""
    rows = []
    for kind, build in (("single", single_argv), ("compare", compare_argv)):
        if option in ("--bank", "--target-type", "--target", "--target-short", "--target-possessive", "--name") \
                and kind == "compare":
            continue                        # single-type options (a comparison names its targets TYPE=NAME)
        out = Path(given + ("" if kind == "single" else "-compare")) if option == "--out" else where / f"{kind}-out"
        code, stdout, stderr = box.run(build(box, out, **{key: str(out) if option == "--out" else given}))
        texts, shown = outputs(code, stdout, stderr, out)
        identity = None
        if code == 0:
            record = json.loads((out / "run.json").read_text())
            identity = {"--workdir": lambda: box.call_folders() == {given},
                        "--out": lambda: (out / "run.json").is_file(),
                        "--config-dir": lambda: record["config_dir"] == given,
                        "--entities": lambda: "Thirlbeck Systems" in json.dumps(record.get("roster")
                                                                                or record.get("rosters")),
                        "--bank": lambda: record["bank_sha256"] == P.load_bank(P.BANK, None).sha256}.get(
                option, lambda: None)()
        rows.append(row(field, value_name, f"run {kind}", code, texts, shown, identity))
        if code == 0:
            code, stdout, stderr = box.run(["--rescore", str(out)])
            texts, shown = outputs(code, stdout, stderr, out)
            rows.append(row(field, value_name, f"rescore {kind}", code, texts, shown, code == 0 or None))
    return rows


def form_case(box, field, option, value, value_name):
    """The --ui form's preview (and its memory block) with the value in the option's field."""
    form = {"agent": "claude", "targets": {"individual": PERSON}, "watch": False}
    name = FORM_FIELDS[option]
    if name == "target":
        form["targets"] = {"individual": value}
    elif name == "classes":
        form["classes"] = [value]
    elif name == "types":
        form["types"] = [value, "organization"]
    else:
        form[name] = value
    with mock.patch.dict(os.environ, box.env, clear=True):
        reply = U.preview(form, env=box.env)
        memory = U.memory_answer(reply["argv"]) if reply.get("argv") else {}
    return form_row(field, value_name, reply, memory)


def record_case(job):
    """One string field of a finished run's run.json set to one hostile value, then --rescore of a copy."""
    kind, folder, path, value_name, root = job
    box = Box(Path(tempfile.mkdtemp(dir=root)))
    run = box.root / "run"
    shutil.copytree(folder, run, symlinks=True)
    meta = json.loads((run / "run.json").read_text())
    set_leaf(meta, path, HOSTILE[value_name])
    (run / "run.json").write_text(json.dumps(meta, indent=2))
    code, out, err = box.run(["--rescore", str(run)])
    texts, shown = outputs(code, out, err, run)
    shutil.rmtree(box.root, ignore_errors=True)
    return [row(f"run.json ({kind}) " + "/".join(map(str, path)), value_name, "rescore" if code == 0 else "refused",
                code, texts, shown)]


def outside_case(job):
    """The agent CLI's --version output (dry run, run and rescore, single-type and compare), a memory file's title
    and a rule file's path, and a discovered config folder's name (dry run and the form's memory block)."""
    value_name, root = job
    value, rows = HOSTILE[value_name], []
    box = Box(Path(tempfile.mkdtemp(dir=root)), version=value)
    for kind, build in (("single", single_argv), ("compare", compare_argv)):
        out = box.root / f"{kind}-out"
        code, stdout, stderr = box.run(build(box, out) + ["--dry-run"])
        rows.append(row("agent CLI --version", value_name, "dry-run", code, *outputs(code, stdout, stderr)))
        code, stdout, stderr = box.run(build(box, out))
        rows.append(row("agent CLI --version", value_name, f"run {kind}", code, *outputs(code, stdout, stderr, out)))
        if code == 0:
            code, stdout, stderr = box.run(["--rescore", str(out)])
            rows.append(row("agent CLI --version", value_name, f"rescore {kind}", code,
                            *outputs(code, stdout, stderr, out)))
    config = box.home / ".claude"
    (config / "CLAUDE.md").write_text(f"# {value}\nnotes\n")
    rule = config / "rules" / ((value[:200]) + ".md")
    found = box.home / "agents" / value[:200]
    made = {}
    for what, make in (("memory file path", lambda: (rule.parent.mkdir(), rule.write_text("# a rule\n"))),
                       ("discovered folder", lambda: ((found / "projects").mkdir(parents=True),
                                                      (found / ".claude.json").write_text("{}\n")))):
        try:
            make()
            made[what] = True
        except OSError:
            made[what] = False
    argv = single_argv(box, box.root / "m-out")
    code, stdout, stderr = box.run(argv + ["--dry-run"])
    texts, shown = outputs(code, stdout, stderr)
    with mock.patch.dict(os.environ, box.env, clear=True):
        memory = U.memory_answer(argv)
    checks = {"memory file title": None,
              "memory file path": (P.display(rule) in stdout) if made["memory file path"] else None,
              "discovered folder": (P.display(P.tilde(found, box.home)) in stdout) if made["discovered folder"]
              else None}
    for what, identity in checks.items():
        rows.append(row(what, value_name, "dry-run", code, texts, shown, identity))
        rows.append(form_row(what, value_name, {}, memory))
    shutil.rmtree(box.root, ignore_errors=True)
    return rows


def live_state(kind, folder, field, value):
    """The --watch page's data (live.RunView.state, as /state serves it: redacted, then JSON in ASCII) for a finished
    stand-in run whose run.json holds value in field, its final card published: the facts from live.run_facts, the
    entity names from live.roster_names, the rows from the card, all built by the page's own builders."""
    meta = json.loads((Path(folder) / "run.json").read_text())
    bank, ordo_bank = P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256), O.load_ordo_bank()
    plan = P.saved_plan(Path(folder), meta, bank, ordo_bank)
    card_file = Path(folder) / ("scorecard.json" if kind == "single" else "ordo/scorecard.json")
    card_text, name = card_file.read_text(), P.tab_label({"kind": kind, "roster": plan.roster,
                                                          "selection": {"compare": meta.get("compare")}})
    roster = json.loads(json.dumps(plan.roster)) if plan.roster else None
    rosters = json.loads(json.dumps(meta.get("rosters"))) if kind == "cross" else None
    if field in ("model", "cli", "compare"):
        meta[field] = value
    elif field == "target":
        card_text = card_text.replace(json.dumps(roster["T"]["full"])[1:-1], json.dumps(value)[1:-1])
        roster["T"]["full"] = value
    elif field == "control":
        card_text = card_text.replace(json.dumps(roster["X"]["full"])[1:-1], json.dumps(value)[1:-1])
        roster["X"]["full"] = value
    elif field == "named":
        named = next(r["named"][0] for r in rosters.values() if r["named"])
        card_text = card_text.replace(json.dumps(named["full"])[1:-1], json.dumps(value)[1:-1])
        named["full"] = value
    elif field == "name":
        name = value
    facts = L.run_facts(meta, kind, roster)
    names = L.roster_names(roster=roster) if kind == "single" else L.roster_names(rosters=rosters)
    rows = L.v4_rows if kind == "single" else L.ordo_rows(O)
    provisional = L.Provisional(lambda snap: None, rows, len(plan.calls))
    view = L.RunView(name, folder, plan.calls, facts, names, P.COMPONENT_NAMES if kind == "single" else
                     O.COMPONENT_NAMES, L.ViewRedactor(environ={}), provisional=provisional)
    view.status = "finished"
    provisional.finalize(json.loads(card_text))
    return json.loads(json.dumps(L.redact_all(view.state(), L.ViewRedactor(environ={})), ensure_ascii=True))


def live_case(job):
    """One live-page case: the page's data for one field holding one hostile value. No string of the run's facts,
    name and scorecard rows may hold a control or invisible character or a line break, nor more of the long value
    than the cap; the whole of the data holds no control or invisible character but newline and tab, and no value of
    it is longer than the cap as a whole (over_cap: a row's label joins the displayed name with its type)."""
    kind, folder, field, value_name = job
    value = dict(HOSTILE, **LIVE_EXTRA)[value_name]
    state = live_state(kind, folder, field, value)
    shown = json_strings({k: state[k] for k in ("name", "facts", "scorecard")})
    found = problems({"page data": json_strings(state)}, set())
    found += problems({"facts, name, rows": shown}, {"facts, name, rows"})
    values = [state["name"], *[v for v in state["facts"].values() if isinstance(v, str)],
              *json_strings(state["scorecard"]).splitlines()]
    if any("\n" in v or "\u2028" in v or "\u2029" in v for v in values[:-1] if isinstance(v, str)) or \
            any("\n" in v for v in [state["name"], *[v for v in state["facts"].values() if isinstance(v, str)]]):
        found.append("a line break inside a value")
    found += [f"value over the cap: {x}" for x in over_cap(state)]
    return [{"field": f"live page ({kind}) {field}", "value": value_name, "path": "live page", "exit": 0,
             "result": "FAIL" if found else "pass", "identity": None, "problems": found}]


REAL_SCORE = O.score


def fast_score(*args, **kwargs):
    """ordo.score with a small bootstrap: the display does not depend on the intervals' precision."""
    kwargs["B"] = FAST_B
    return REAL_SCORE(*args, **kwargs)


def finished_runs(root):
    """A finished single-type run and a finished comparison made through the entry point with the stand-in."""
    box = Box(Path(root) / "made")
    folders = {"single": box.root / "single-run", "compare": box.root / "compare-run"}
    for kind, build in (("single", single_argv), ("compare", compare_argv)):
        code, _, err = box.run(build(box, folders[kind]))
        assert code == 0, P.display(err)
    return folders


def jobs(root, folders):
    """Every case, as (worker, job)."""
    todo = []
    for parser_name, parser in (("v4", P.v4_parser()), ("v3", P.v3_parser())):
        for option in string_options(parser):
            todo += [(option_case, (parser_name, option, v, root, str(folders["single"]))) for v in HOSTILE]
    for kind, folder in folders.items():
        meta = json.loads((Path(folder) / "run.json").read_text())
        for path, _ in string_leaves(meta):
            todo += [(record_case, (kind, str(folder), path, v, root)) for v in HOSTILE]
    todo += [(outside_case, (v, root)) for v in HOSTILE]
    for kind, folder in folders.items():
        page = "single" if kind == "single" else "cross"
        todo += [(live_case, (page, str(folder), field, v)) for field in LIVE_FIELDS[page]
                 for v in dict(HOSTILE, **LIVE_EXTRA)]
    return todo


def run_job(item):
    worker, job = item
    return worker(job)


def no_network(*args, **kwargs):
    raise AssertionError("a display case reached for the network")


def enumerate_all(root, processes=None):
    """Every case run, in forked workers where the platform forks (else one by one): the rows of the table. No case
    can reach the network: every way probatio opens a connection fails the case instead."""
    with mock.patch.object(P, "BOOTSTRAP_B", FAST_B), mock.patch.object(O, "score", fast_score), \
            mock.patch.object(P.urllib.request, "urlopen", no_network), \
            mock.patch.object(P.http.client, "HTTPConnection", no_network), \
            mock.patch.object(P.http.client, "HTTPSConnection", no_network):
        folders = finished_runs(root)
        todo = jobs(root, folders)
        try:
            context = multiprocessing.get_context("fork")
        except ValueError:
            context = None
        if context is None or processes == 1:
            return [r for item in todo for r in run_job(item)]
        with context.Pool(processes or min(8, os.cpu_count() or 1)) as pool:
            return [r for rows in pool.map(run_job, todo, chunksize=1) for r in rows]


class TestBackstop(unittest.TestCase):
    """The backstop's parts on their own: the terminal filter, its one control channel, the JSON escape, the
    refusal line, the rendered card, and a path error at the entry point."""

    def test_the_terminal_filter(self):
        raw = io.StringIO()
        f = P.TerminalFilter(raw)
        f.write("ok\n\tline " + "".join(HOSTILE[k] for k in ("clear-screen", "carriage-return", "backspace-run",
                                                             "right-to-left", "zero-width-joiner")))
        f.control("\r")
        f.framed("", after="\x1b[K")
        for code in ("\x1b[2J", "\x08", "\r\n"):
            with self.assertRaises(ValueError):
                f.framed("x", before=code)              # only the live line's own two codes
        with self.assertRaises(AttributeError):
            f.buffer                                   # noqa: B018  (no way past the filter)
        text = raw.getvalue()
        self.assertTrue(text.startswith("ok\n\tline x\\x1b[2J"))
        self.assertTrue(text.endswith("\r\x1b[K"))
        self.assertEqual(unseen_in(text[:-4]), [])
        self.assertEqual(P.terminal_text("plain text\n\tindented"), "plain text\n\tindented")

    def test_installed_once_and_put_back(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), P.filtered_streams():
            P.install_terminal_filter()                # a second wrap changes nothing
            self.assertIsInstance(sys.stdout, P.TerminalFilter)
            self.assertNotIsInstance(sys.stdout.stream, P.TerminalFilter)
            print("a\x1b[31mb")
        self.assertNotIsInstance(sys.stdout, P.TerminalFilter)
        self.assertEqual(out.getvalue(), "a\\x1b[31mb\n")

    def test_json_text_escapes_and_reads_back(self):
        value = {"k\u202e": ["\x7f\x9b", "\u200d", "\U000e0041", "\x1b\n", "plain é 東"]}
        text = P.json_text(value, indent=1)
        self.assertEqual(unseen_in(text), [])
        self.assertEqual(json.loads(text), value)
        self.assertEqual(P.json_text({"a": "plain"}, indent=1), json.dumps({"a": "plain"}, ensure_ascii=False, indent=1))

    def test_a_refusal_is_one_line(self):
        self.assertEqual(P.refusal_text("\ninterrupted. Rerun."), "\ninterrupted. Rerun.")
        self.assertEqual(P.refusal_text("bad\n" + FORGED), "bad\\n" + FORGED)

    def test_the_rendered_card(self):
        card = {"agent": "claude", "n": 3, "x": 0.5, "names": {"Mozilla\n" + FORGED: ["W" * 1000]}, "ok": None}
        shown = P.shown_view(card)
        self.assertEqual((shown["agent"], shown["n"], shown["x"], shown["ok"]), ("claude", 3, 0.5, None))
        (key, value), = shown["names"].items()
        self.assertEqual((key, len(value[0])), ("Mozilla\\n" + FORGED, P.DISPLAY_CHARS))
        plain = {"a": ["b", {"c": "d"}], "e": 1}
        self.assertEqual(P.shown_view(plain), plain)

    def test_a_path_the_system_cannot_use_is_one_line(self):
        box = Box(Path(tempfile.mkdtemp(prefix="probatio-path-")))
        self.addCleanup(shutil.rmtree, box.root, True)
        code, out, err = box.run(single_argv(box, box.root / ("x" * 300)))      # the preflight, then the folder
        self.assertEqual(code, 1)
        self.assertEqual(len(err.splitlines()), 1)
        self.assertIn("File name too long", err)
        self.assertNotIn("Traceback", err)
        self.assertLessEqual(len(err), P.DISPLAY_CHARS + 200)


class TestLivePageInChrome(unittest.TestCase):
    """The --watch page in a real headless Chrome (test_ui's harness, every host but 127.0.0.1 unresolvable): the
    page's own script renders the data live.RunView builds for runs whose facts and names hold hostile values; the
    page shows each as one displayed token, as text, never markup, and no script of theirs runs."""

    @classmethod
    def setUpClass(cls):
        import test_ui as TU
        if not TU.CHROME:
            raise unittest.SkipTest("needs a headless Chrome on PATH")
        cls.TU = TU
        cls.tmp = Path(tempfile.mkdtemp(prefix="probatio-live-"))
        with mock.patch.object(P, "BOOTSTRAP_B", FAST_B), mock.patch.object(O, "score", fast_score):
            cls.folders = finished_runs(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_hostile_facts_and_names_render_as_text(self):
        markup = '<img src=x onerror="window.BAD=1"><script>window.BAD=2</script>'
        cases = [("single", "model", HOSTILE["newline-forged"]), ("single", "target", HOSTILE["right-to-left"]),
                 ("single", "cli", LIVE_EXTRA["line-separator"]), ("single", "control", markup),
                 ("cross", "compare", HOSTILE["long-10000"]), ("cross", "named", LIVE_EXTRA["lone-surrogate"]),
                 ("cross", "model", HOSTILE["clear-screen"]), ("cross", "name", HOSTILE["zero-width-joiner"])]
        chrome = self.TU.Chrome(self.tmp / "chrome")
        try:
            frame = chrome.call("Page.getFrameTree")["frameTree"]["frame"]["id"]
            chrome.call("Page.setDocumentContent", frameId=frame, html=L.VIEW_V4)
            chrome.js("window.BAD = 0; window.setInterval = () => 0; "
                      "window.fetch = () => Promise.reject(new Error('no network in this test'));")
            for kind, field, value in cases:
                folder = self.folders["single" if kind == "single" else "compare"]
                state = live_state(kind, folder, field, value)
                data = {"runs": [state], "boundary": L.BOUNDARY, "moves": L.MOVES, "midline": L.MIDLINE_NOTE}
                chrome.js("render(" + json.dumps(data) + ")")
                text = chrome.js("document.body.textContent")              # the tabs, the facts, the card
                self.assertEqual(unseen_in(text), [], (kind, field))
                self.assertNotIn("\n" + FORGED, text)
                self.assertIsNone(LONG_RUN.search(text), (kind, field))
                if field != "name":                     # a page with one run draws no tab, so no name
                    self.assertIn(P.display(value), text, (kind, field))      # the token, literally, as text
                self.assertEqual(chrome.js("window.BAD"), 0)
                self.assertEqual(chrome.js('document.querySelectorAll("#run img, #run script").length'), 0)
        finally:
            chrome.close()


class TestEnumeratedBoundary(unittest.TestCase):
    """Every field, every hostile value, every path that can show it: no case may fail."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="probatio-display-")
        cls.rows = enumerate_all(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_option_of_both_parsers_is_a_field(self):
        fields = {r["field"] for r in self.rows}
        v4, v3 = string_options(P.v4_parser()), string_options(P.v3_parser())
        self.assertGreaterEqual(len(v4), 20)
        self.assertEqual([o for o in v4 if f"v4 {o}" not in fields] + [o for o in v3 if f"v3 {o}" not in fields], [])

    def test_every_path_is_exercised(self):
        paths = {r["path"] for r in self.rows}
        self.assertLessEqual({"dry-run", "refused", "run single", "run compare", "rescore single", "rescore compare",
                              "rescore", "form preview", "live page"}, paths)
        self.assertEqual({r["value"] for r in self.rows}, set(HOSTILE) | set(LIVE_EXTRA))

    def test_no_case_fails(self):
        failed = [(r["field"], r["value"], r["path"], r["problems"]) for r in self.rows if r["result"] != "pass"]
        self.assertEqual(failed, [], f"{len(failed)} of {len(self.rows)} cases failed")


if __name__ == "__main__":
    unittest.main()
