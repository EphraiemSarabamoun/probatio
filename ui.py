#!/usr/bin/env python3
"""ui: a simple page for choosing a probatio v4 run (python3 probatio.py --ui). It shows what a run does by default, lets
you choose what you want instead, turns the form into the command line that does it, checks that command with the real
dry run, and runs it.

One page on 127.0.0.1 behind a random token, served by live.serve_page with the --watch page's hardening (the token on
every route, the Host allowlist, exact routes, the hash-only CSP, no referrer) and four POST actions, which carry the
token in a header as well. The form shows who is measured, the agent and model, and the length; the rosters and the
other options wait in two folds whose closed lines say what differs from the defaults. Beside it sits the answer:
the exact command, its plan and its run.

    defaults        everything the form starts from, read from probatio and never typed here: each option's default
                    from parse_args_v4 and each type's rosters from print_entities
    build_command   the form as an argv with only the options it changed, and the entities file its roster edits
                    need (pure)
    preview         the command as the form changes: probatio's own parser on it (in this process, its message
                    verbatim) and the calibration words for its roster (load_entities, fixtures_label)
    Session.check   the real `--dry-run` of that argv in a subprocess: every guard probatio has and its preflight,
                    with no model call beyond the CLI's version probe; then probatio's own resume check, read only,
                    for each results folder that already holds a run (resume_refusals)
    Runner          one run at a time: the checked argv (with --watch unless it is chosen off) in a subprocess,
                    its watch link (probatio's own line, and only with --watch), the last lines of its output, its
                    results folders and scorecards, and Stop
                    (SIGINT to that child alone, as Ctrl-C in a terminal: the calls already running finish, and the
                    same command resumes)

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
import tempfile
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
# The models the page offers per CLI, as of 2026-11-09: the aliases Claude Code accepts, and Codex's model names (Codex
# has no aliases). Each passed a real --dry-run for its CLI that day. The page's "other" takes any name, so a newer
# model needs no change here; the CLI's own default is the page's first choice.
MODELS = {"claude": ("fable", "opus", "sonnet", "haiku"),
          "codex": ("gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-sol", "gpt-6-astra", "gpt-6-luna", "gpt-5.6-sol",
                    "gpt-5.6-terra", "gpt-5.6-luna")}
WATCH = "--watch"                     # the run's live view; the page reads its link from the run's output
WATCH_CHOICES = ("on", "off")         # the page's watch choice, its default first: on (the parser's own is off)
TAIL = 40                             # the newest lines of a run's output on the page
CHECK_SECONDS = 120                   # a dry run that takes longer is reported, not waited for
STOP_SECONDS = 10                     # after a second Ctrl-C, how long the page waits for its run to end
BOUNDARY = ("Local to this machine. Check makes no model call (only the CLI's version check); Run uses your own CLI "
            "login and quota.")
# What the page reads from probatio's output: the --watch link, a scored run's result lines, the held page's line,
# and the preflight's totals (each run's calls and time, several runs' total time, the 45-minute hint). The link is
# probatio's own line, whole (live.Watch prints it before any call), read only when the run's argv has --watch and
# only the first time: text elsewhere in the output (a model's name, a CLI's error) never becomes the link.
WATCH_LINK = re.compile(r"^  watch it live: (http://127\.0\.0\.1:\d+/\?t=[A-Za-z0-9_-]+)$")
RESULT_LINE = re.compile(r"^(Scorecard|Report): (\S.*)$")
HELD_LINE = "The live page stays up at "
RUN_TOTAL = re.compile(r"^\s+all\s+\d+ items\s+(\d+) calls\s+(.+?)(?: \((.+)\))?$")
CROSS_TOTAL = re.compile(r"^\s+\d+ units, (\d+) calls\s+(.+?)(?: \((.+)\))?$")
ALL_RUNS = re.compile(r"^\s+all \d+ comparisons: (.+?)(?: \((.+)\))?$")
TIME_HINT = re.compile(r"^\s+(about [\d.]+ minutes; to shorten: .+)$")
PARSE_LOCK = threading.Lock()         # the in-process parser borrows sys.stderr, one request at a time
# What a run needs before it can be checked (required): each piece a field the form leaves empty, in plain words, in
# the page's order, shown together as one calm line (NEEDS_LINE). A value given but wrong is probatio's to refuse.
NEEDS_AGENT = "choose an agent"
NEEDS_NAME = "type a name in the {} row"
NEEDS_CLASS = "choose at least one class"
NEEDS_LINE = "To check a run: {}"            # the pieces, joined by " · "
# What the page refuses before probatio sees the command, each bounded (it never repeats a value): a length it does not
# offer (smoke is the calibration's setup plan, for a terminal), a NUL, which no command line can carry, a "=" in a
# row's name (the type is the type buttons', never TYPE=NAME in a row; any other name the guard refuses gets the
# guard's own words, under ROW_LABEL), and a value the reply's redactor would change, so the command shown would not
# be the one that runs.
TIER_REFUSED = (f"the page runs {', '.join(P.USER_TIERS[:-1])} or {P.USER_TIERS[-1]}; smoke, the calibration's "
                "setup plan, runs from a terminal (--tier smoke)")
NUL_REFUSED = "a name or the model holds a NUL character, which no command line can carry; remove it"
NAME_EQUALS = ('a name in the {} row holds "=", which no name can hold; the type is chosen with the type buttons, so '
               "pick it there and type the bare name in its row")
ROW_LABEL = "a name in the {} row"     # check_name's label for a row's name, so its message says where to look
UNSHOWN = ("{} holds text the page would not show as it is (a control or format character, or the shape of a key, "
           "a secret or the page's token), so the command shown would not be the one that runs; remove it")
# unshown's words when the text holds a secret-named variable's value: the variable's name (matched_names), never the
# value, so the user can see why; at most SHOWN_NAMES names, each cut to NAME_CHARS.
UNSHOWN_VARIABLE = ("{} holds the value of {}, a secret-named variable in probatio's environment, which every reply "
                    "writes as <redacted>, so the command shown would not be the one that runs; remove it")
SHOWN_NAMES, NAME_CHARS = 3, 64
# Form text that does not encode to UTF-8 (a lone surrogate, which JSON can carry) can reach no command line and no
# file; the field is named by the page's words for it, never by a key or value the request chose.
TEXT_REFUSED = ("there is a character that is not text (a lone surrogate) in {}, which no command or file can "
                "carry; remove it")
FIELD_WORDS = {"agent": "the agent", "model": "the model", "tier": "the length", "classes": "the classes",
               "memory": "the memory choice", "auto_memory": "the auto memory choice", "effort": "the effort",
               "jobs": "the jobs", "watch": "the watch choice", "types": "the types"}


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
    --entities starter file. The page offers USER_TIERS alone: smoke, the calibration's setup plan, is a terminal's
    (--tier smoke)."""
    a = P.parse_args_v4(["--print-entities"])
    rosters = json.loads(P.print_entities(P.load_entities(None, bank()), bank()))
    return {
        "default": {"type": a.target_type, "agent": a.agent, "tier": a.tier, "classes": list(a.classes),
                    "memory": a.memory, "auto_memory": a.auto_memory, "effort": a.effort, "model": a.model,
                    "jobs": a.jobs},
        "types": list(P.TARGET_TYPES),
        "rosters": {t: {part: rosters[t][part] for part in LISTS + ("targets",)} for t in P.TARGET_TYPES},
        "counts": {part: list(P.LIST_COUNTS[part]) for part in LISTS},
        "agents": [[k, P.AGENT_NAMES[k]] for k in P.AGENT_NAMES], "models": {k: list(v) for k, v in MODELS.items()},
        "watch": list(WATCH_CHOICES), "measured_jobs": P.pricing()["measured"]["jobs"],
        "tiers": list(P.USER_TIERS), "planned": {t: list(P.resolve_classes(t, "all")) for t in P.USER_TIERS},
        "classes": list(P.V4_CLASSES), "memory": list(P.MEMORY_MODES), "efforts": list(P.EFFORTS),
        "auto_memory": [a.auto_memory, AUTO_MEMORY_OFF],
        "labels": {"cross": P.CROSS_LABEL},
        "program": shlex.join(PROGRAM), "boundary": BOUNDARY,
    }


# ---- the form as a command line (pure) ---------------------------------------------------------------------------------
# The page sends its form as JSON: {"types": [...], "targets": {type: text}, "rosters": {type: {"comparators": text,
# "controls": text}}, "agent", "tier", "classes": [...], "memory", "auto_memory", "effort", "model", "jobs"}, every
# list of names one name per line, and "watch": "on" or "off". A field left out is its default (watch's is on); a
# person is a target like any other, so a "name" field is ignored.
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


def unencodable(value):
    """Whether value (text, or lists and dicts of it, keys included) holds text that does not encode to UTF-8."""
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return True
        return False
    if isinstance(value, dict):
        return any(unencodable(k) or unencodable(v) for k, v in value.items())
    return isinstance(value, list) and any(unencodable(v) for v in value)


def not_text(form):
    """The page's words for the first field of the form holding text that does not encode to UTF-8 (a row, a roster
    list, or another field, by FIELD_WORDS), or None. A key the request chose is never repeated."""
    for key, value in form.items():
        if not (unencodable(key) or unencodable(value)):
            continue
        if key in ("targets", "rosters") and isinstance(value, dict):
            for kind, part in value.items():
                if not (unencodable(kind) or unencodable(part)):
                    continue
                kind = kind if kind in P.TARGET_TYPES else None
                if key == "targets":
                    return f"the {kind} row" if kind else "a row"
                lists = [n for n, text in part.items() if unencodable(text)] if isinstance(part, dict) else []
                return f"the {kind} {lists[0]}" if kind and lists and lists[0] in LISTS else "a roster list"
        return FIELD_WORDS.get(key, "a field")
    return None


def required(form, base=None):
    """What the form still needs before a run can be checked, in plain words and in the page's order (NEEDS_*): an
    agent, the parser's one required option; for a single-type run, a name in that type's row, since probatio needs a
    target (checked_names, plan_runs) and the page never writes --name; and at least one class, since an empty
    --class is refused. A comparison's named targets are optional. Only fields left empty count: a value given but
    wrong is probatio's to refuse, in its own words. Pure; base is defaults()."""
    base = base or defaults()
    form = form if isinstance(form, dict) else {}
    needs = [] if text_of(form.get("agent")) else [NEEDS_AGENT]
    types = types_of(form, base)
    targets = form.get("targets") if isinstance(form.get("targets"), dict) else {}
    if len(types) == 1 and not names_of(targets.get(types[0])):
        needs.append(NEEDS_NAME.format(types[0]))
    picked = form.get("classes")
    if isinstance(picked, list) and not [k for k in P.V4_CLASSES if k in picked]:
        needs.append(NEEDS_CLASS)
    return needs


def needs_line(needs):
    """required's pieces as the page shows them: one calm line, or None when nothing is missing."""
    return NEEDS_LINE.format(" · ".join(needs)) if needs else None


class FormRefused(ValueError):
    """A form the page writes no command for; the message, shown on the page, says why (TIER_REFUSED, NUL_REFUSED,
    NAME_EQUALS, or the name guard's own words)."""


def row_name(name, kind):
    """A row's name as the command writes it: probatio's name guard's own form of it (check_name: NFC, one space
    between words), or FormRefused. A NUL gets NUL_REFUSED and a "=" the page's NAME_EQUALS (probatio would read
    TYPE=NAME, or an unknown type); anything else the guard refuses gets its own message, under the row's label."""
    if "\x00" in name:
        raise FormRefused(NUL_REFUSED)
    if "=" in name:
        raise FormRefused(NAME_EQUALS.format(kind))
    try:
        return P.check_name(name, ROW_LABEL.format(kind))
    except P.NameRefused as e:
        raise FormRefused(str(e)) from None


def target_words(value):
    """--target and its value as the command writes them: two words, or one, --target=VALUE, when the value starts
    with "-", which argparse would otherwise read as an option (a name such as "-Jane" passes the guard)."""
    return [f"--target={value}"] if value.startswith("-") else ["--target", value]


def build_command(form, base=None):
    """The form as probatio's argv, with only the flags that change something (an option at its parser default is
    left out), and the entities file its roster edits need, as text, or None. base is defaults(). A single-type run
    is written as README teaches it, so it takes v4's own path and shares the default results folder with a
    terminal's runs (any single-type form of the same plan continues the other's folder, both ways; a custom
    roster's folder and the baseline's refuse each other): --target-type TYPE for an organization or a nation (none
    for the default individual) and each target as --target NAME. Two or three types compared are --compare,
    probatio's canonical form, with each target as --target TYPE=NAME, which is the only place the type is needed. A
    person (you or anyone) is a target like any other, typed in the individual row, so the page never writes --name:
    --target NAME plans the same run, item for item. Each name goes through probatio's name guard first and is
    written in its form (row_name), as --target=NAME when it starts with "-" (target_words). With no target, a
    single-type run stops with probatio's own message. Several targets of one type, or roster edits, take probatio's
    options path whatever the form, and --target-type keeps their comparison. Roster edits become --entities
    results/ui-entities-<sha256>.json. Classes are left out only when all three are chosen, the form's own default,
    which is the parser's "all" (each tier then asks its own classes); any other choice is passed on as --class, so
    a subset stays explicit at every tier, even one equal to the tier's classes (I,II at quick, which probatio
    compares with standard's I,II), and an empty one so the parser says why. --watch comes last unless the form's
    watch is off (the page then shows the run's output without a live link); either way the command shown is the one
    Check checks and Run runs, byte for byte (preview and Check refuse a command the reply's redactor would change:
    unshown). FormRefused, with no command, for a tier outside USER_TIERS (smoke is a terminal's), a NUL in any
    argument, or a row's name the guard or the page refuses (row_name). Pure: it reads nothing and writes nothing
    (Check writes the file)."""
    base = base or defaults()
    form = form if isinstance(form, dict) else {}
    field = not_text(form)
    if field:
        raise FormRefused(TEXT_REFUSED.format(field))
    default, types = base["default"], types_of(form, base)
    argv = []
    agent = text_of(form.get("agent"))
    if agent:
        argv += ["--agent", agent]
    single = len(types) == 1
    if not single:
        argv += ["--compare", P.canonical_compare([tuple(types)])["compare"]]
    elif types != [default["type"]]:
        argv += ["--target-type", types[0]]
    targets = form.get("targets") if isinstance(form.get("targets"), dict) else {}
    for t in types:
        for n in names_of(targets.get(t)):
            name = row_name(n, t)
            argv += target_words(name if single else f"{t}={name}")
    entities = entities_text(form, types, base)
    if entities:
        argv += ["--entities", entities_path(entities)]
    tier = text_of(form.get("tier")) or default["tier"]
    if tier not in P.USER_TIERS:
        raise FormRefused(TIER_REFUSED)
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
    if form.get("watch") not in (False, "off"):
        argv.append(WATCH)
    if any("\x00" in word for word in argv):
        raise FormRefused(NUL_REFUSED)
    return argv, entities


def unshown(argv, redactor):
    """Why the page would show argv other than as it runs, or None: every reply passes the server's redactor
    (live.ViewRedactor: control and format characters removed, every secret value and key shape <redacted>), so a
    word it would change shows as something it is not. The message names the word's option, never the word
    (unshown_words)."""
    for i, word in enumerate(argv):
        if redactor(word) != word:
            flag = word.partition("=")[0] if word.startswith("--") and "=" in word else \
                argv[i - 1] if i and argv[i - 1].startswith("--") else None
            return unshown_words(f"the value of {flag}" if flag else "an option", word, redactor)
    return None


def strings(value):
    """Every piece of text in value: the text itself, or the texts in its lists and in its dicts' values."""
    if isinstance(value, str):
        return [value]
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    return [text for item in items for text in strings(item)]


def roster_unshown(entities, redactor):
    """unshown for the roster file (entities, its text, or None): the first list holding a name, or a field of a
    curated record, that the redactor would change, named as "a name in the <type> <list>", or None. The file the page
    shows passes the same redactor, so it would not be the file that runs."""
    for kind, lists in (json.loads(entities) if entities else {}).items():
        for part, entries in lists.items():
            for text in strings(entries):
                if redactor(text) != text:
                    return unshown_words(f"a name in the {kind} {part}", text, redactor)
    return None


def unshown_words(where, text, redactor):
    """unshown's message for text at where: the secret-named variables whose values it holds, by name alone
    (ViewRedactor.matched_names), when there are any, else UNSHOWN's list of what the redactor changes."""
    names = [n[:NAME_CHARS] for n in redactor.matched_names(text)[:SHOWN_NAMES]]
    return UNSHOWN_VARIABLE.format(where, P.in_words(names)) if names else UNSHOWN.format(where)


def refused(error, missing=()):
    """A reply with no command, only why (preview's to a form FormRefused, Check's to one that is refused before
    anything is run), with what the form still needs (required), as preview gives it."""
    return {"argv": [], "command": "", "words": [], "entities": None, "error": error, "labels": [], "roster": None,
            "missing": list(missing), "needs": needs_line(missing)}


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


# probatio's refusals whose advice names what the page does not show (an entities file's keys, --compare), each as a
# pattern of probatio's message and the page's words for it: run the names separately, rename one, pick the type. The
# page shows its words; probatio's own message stays, verbatim, in the whole preflight (told).
PAGE_WORDS = (
    (re.compile(r"^(?P<who>.+) both have the key (?P<key>\".*\"), and a target's key names its results; "
                r"give one its own key under \w+ targets in the --entities file"),
     "{who} both have the key {key}, which names a run's results folder, so they cannot run together: run them "
     "separately (the second once the first's folder is moved or renamed), or rename one"),
    (re.compile(r"^(?P<runs>.+) would both write to (?P<folder>/.+); give the target its own key in the --entities "
                r"file$"),
     "{runs} would both write to {folder}, so they cannot run together: run them separately, or rename one"),
    (re.compile(r"^--target .+ names (?P<what>an? \w+), which this run does not compare; add (?P<kind>\w+) to "
                r"--compare$"),
     "a name in a row names {what}, which this run does not measure: pick {kind} with the type buttons and type the "
     "name in its row"),
)


def page_words(message):
    """The page's words for a refusal of probatio's whose advice the page cannot follow (PAGE_WORDS), or None when the
    message is one the page shows as it is. A folder is named as the page names it (shown)."""
    for pattern, words in PAGE_WORDS:
        m = pattern.match(message or "")
        if m:
            return words.format(**{k: shown(v) if k == "folder" else v for k, v in m.groupdict().items()})
    return None


def told(preflight, *messages):
    """The whole preflight with probatio's own messages after it, as a terminal shows them: stdout, then each
    refusal."""
    return preflight + "".join(m.rstrip("\n") + "\n" for m in messages if m)


def refusal(said, preflight):
    """A refusal of probatio's as Check shows it, (error, plan): the page's words with probatio's own message kept in
    the whole preflight (page_words), or the message itself."""
    words = page_words(said)
    return (words, told(preflight, said)) if words else (said, preflight)


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


def preview(form, base=None, redactor=None):
    """The answer to the form as it changes: the command (argv and its text), the entities file it needs, the parser's
    verdict (verbatim), and the calibration words probatio prints for the selection and the roster: cross-type and
    unregistered (DESIGN 11's labels), and fixtures_label, "custom roster: not calibrated (fixtures …)" when the
    lists differ from the defaults. A roster that load_entities refuses gives its message instead. A form
    build_command refuses has no command, only why; a command the reply's redactor (the server's; None: one built
    from this process's environment) would change gets unshown's message once the parser accepts it, and a roster
    file it would change roster_unshown's once probatio accepts the file. What the form still needs (required) comes
    as "missing" and "needs", its line; the page shows that line, calm, instead of any error until nothing is
    missing, and the command so far all the while."""
    base = base or defaults()
    form = form if isinstance(form, dict) else {}
    missing = required(form, base)
    try:
        argv, entities = build_command(form, base)
    except FormRefused as e:
        return refused(str(e), missing)
    types, redactor = types_of(form, base), redactor or live.ViewRedactor()
    reply = {"argv": argv, "command": command_text(argv), "words": command_words(argv),
             "entities": entities_view(entities), "labels": [], "roster": None, "missing": missing,
             "needs": needs_line(missing), "error": parser_error(argv) or unshown(argv, redactor)}
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
    reply["error"] = reply["error"] or roster_unshown(entities, redactor)
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


def absolute(argv):
    """argv with its entities file named from probatio's folder, the run's working directory, so another process or
    folder reads the same file."""
    return [str(P.HERE / x) if prev == "--entities" else x for prev, x in zip([None, *argv], argv)]


def prepared(argv):
    """argv's runs as probatio prepares them (prepare_runs), with no call to any CLI: each run's results folder
    (run_folder) and plan fingerprint (plan_fingerprint), in run order. The entities file is read from probatio's
    folder, the run's working directory."""
    a = P.parse_args_v4(absolute(argv))
    runs = P.prepare_runs(a, "--target-type" in argv)[4]     # as main_v4 tells it; the page writes it as one word
    return [(P.run_folder(a, run, len(runs) > 1), P.plan_fingerprint(run["calls"])) for run in runs]


def shown(path):
    """A path as the page shows it: relative to probatio's folder when it is inside it."""
    try:
        return str(Path(path).resolve().relative_to(P.HERE))
    except ValueError:
        return str(path)


# ---- a folder an earlier run holds: probatio's own resume check, read only ------------------------------------------
# A run goes into its default results folder (the page writes no --out), and probatio refuses there, before any call,
# when the folder's run.json records other settings (check_resume). The dry run never reads run.json, so Check asks
# check_resume itself, for each prepared folder that already holds one, with the record the run would write. That
# record holds the CLI's version and the memory files' hashes, so it is built where the run's are, in a child process
# with the run's environment (resume_child: probatio's own preflight, as main_v4 calls it under --dry-run, its printing
# discarded), and checked against a copy of run.json in a temporary folder, so the run's folder is never written.
# Check approves only when every such run would go on.
RESUME_CHILD = "import sys; sys.path.insert(0, {!r}); import ui; ui.resume_child()"
BLOCKED = ("{folder} holds an earlier run{changed} that this command cannot continue: to resume that run, set the "
           "page back to its settings; to start a new one, move or rename that folder (probatio's own words are in "
           "the whole preflight)")
CHANGED = re.compile(r"\(([^()]*) changed\)")          # check_resume's list of what differs


class ResumeUnread(Exception):
    """Check could not compare a prepared run with the run its folder already holds; the message says why, bounded."""


def resume_refusals(argv, plans, env):
    """probatio's refusal for each prepared run (plans: prepared's (folder, fingerprint) pairs) whose folder already
    holds a run.json and would refuse it, as (folder, probatio's message), in run order; [] when every run would go
    on, and at once when no folder holds one. The record is built in a child process (RESUME_CHILD) with env, the
    run's environment; ResumeUnread when that fails or its plans are not the page's."""
    if not any((Path(folder) / "run.json").exists() for folder, _ in plans):
        return []
    request = {"argv": absolute(argv), "folders": [str(folder) for folder, _ in plans]}
    try:
        proc = subprocess.run([sys.executable, "-I", "-B", "-c", RESUME_CHILD.format(str(Path(__file__).parent))],
                              input=json.dumps(request), cwd=P.HERE, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=CHECK_SECONDS)
    except subprocess.TimeoutExpired:
        raise ResumeUnread(f"comparing with the runs already in the results folders took longer than {CHECK_SECONDS} "
                           "s; check again") from None
    except (ValueError, OSError) as e:
        raise ResumeUnread(not_started("check of the results folders", e)) from None
    try:
        answer = json.loads(proc.stdout) if proc.returncode == 0 else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict) or answer.get("fingerprints") != [fp for _, fp in plans] or \
            not isinstance(answer.get("refusals"), list) or len(answer["refusals"]) != len(plans):
        why = (proc.stderr.strip().splitlines() or ["no answer"])[-1][:200]
        raise ResumeUnread(f"Check could not compare this command with the runs already in its results folders "
                           f"({why}); check again")
    return [(folder, said) for (folder, _), said in zip(plans, answer["refusals"]) if said]


def blocked_words(refusals):
    """The page's words for each folder that blocks the run (resume_refusals): which folder, what differs when
    probatio names it, and what to do from the page."""
    words = []
    for folder, said in refusals:
        changed = CHANGED.search(said)
        words.append(BLOCKED.format(folder=shown(folder),
                                    changed=f" with other settings ({changed.group(1)} changed)" if changed else ""))
    return " ".join(words)


def would_be_metas(argv):
    """The run.json record each run of argv would write, in run order: probatio's own preflight (preflight_v4 or
    preflight_runs) as main_v4 calls it under --dry-run, its printing discarded. It asks the CLI its version and
    reads the memory files, as the run will, so it runs in resume_child's process, with the run's environment."""
    metas, v4, several = [], P.preflight_v4, P.preflight_runs

    def one(*args):
        metas.append(v4(*args))
        return metas[-1]

    def each(*args):
        got = several(*args)
        metas.extend(got)
        return got
    P.preflight_v4, P.preflight_runs = one, each
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            P.main_v4([*argv, "--dry-run"])
    finally:
        P.preflight_v4, P.preflight_runs = v4, several
    return metas


def resume_refusal(folder, meta):
    """probatio's own check_resume for meta against a copy of folder's run.json in a temporary folder, so the run's
    folder is never written: its refusal, with the folder's own path in it, or None when the run would go on (or
    run.json is gone, so the run starts afresh). A run.json that is not a regular file, or not a record probatio can
    read, refuses too, as the run would stop on it."""
    path = folder / "run.json"
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return f"{path} is not a regular file"
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as e:
        return f"{path} cannot be read ({os.strerror(e.errno or 0)})"
    with tempfile.TemporaryDirectory(prefix="probatio-ui-resume-") as d:
        (Path(d) / "run.json").write_bytes(data)
        try:
            P.check_resume(Path(d), meta)
        except SystemExit as e:
            return str(e.code).replace(d, str(folder))
        except Exception as e:                      # noqa: BLE001 (not JSON, not a record: the run stops on it too)
            return f"{path} is not a run record probatio can read ({type(e).__name__})"
    return None


def resume_child():
    """resume_refusals' child process: reads {"argv", "folders"} on stdin and writes {"fingerprints", "refusals"} on
    stdout, one refusal (or null) per run, for the folders that hold a run.json."""
    request = json.load(sys.stdin)
    metas = would_be_metas(request["argv"])
    json.dump({"fingerprints": [m.get("plan_fingerprint") for m in metas],
               "refusals": [resume_refusal(Path(f), m) if (Path(f) / "run.json").exists() else None
                            for f, m in zip(request["folders"], metas)]}, sys.stdout)


# ---- one run at a time ---------------------------------------------------------------------------------------------------
def not_started(what, error):
    """Why a dry run or a run did not start, bounded: subprocess refused its command line (ValueError) or the system
    could not start it (OSError, by its errno's words); never the command."""
    why = os.strerror(error.errno) if isinstance(error, OSError) and error.errno else \
        "its command line holds what no program can take"
    return f"the {what} could not start ({why})"


class Runner:
    """One probatio run started by the page: program + argv in a subprocess in its own session (so a Ctrl-C at the
    page's terminal reaches the page, which passes one SIGINT on), its output read line by line: the --watch link
    (WATCH_LINK, only when argv has --watch, the first), the newest TAIL lines, each run's result lines, the held
    page's line, and its exit code. A result line counts only when its path lies inside one of the run's own folders
    (own), and the run is finished only on exit 0 with a scorecard: a CLI's text in the output names no result. A
    process that cannot start raises subprocess's ValueError or OSError."""

    def __init__(self, program, argv, folders, env):
        self.argv, self.folders = list(argv), [str(f) for f in folders]
        self.roots = [Path(f).resolve() for f in folders]
        self.watching = WATCH in self.argv
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
                link = WATCH_LINK.match(line) if self.watching and self.watch is None else None
                if link:
                    self.watch = link.group(1)
                result = RESULT_LINE.match(line)
                if result and self.own(result.group(2)):
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

    def own(self, path):
        """Whether path lies inside one of this run's own results folders, where probatio writes its scorecard and
        report; any other path on a result line is not this run's."""
        try:
            where = Path(path).resolve()
        except (OSError, RuntimeError, ValueError):
            return False
        return any(where == root or root in where.parents for root in self.roots)

    def status(self):
        """running, stopping, scored (scored, its live page held until Stop), finished (exit 0 with a scorecard),
        stopped or failed."""
        scored = any(kind == "Scorecard" for kind, _ in self.results)
        if self.code is None:
            return "stopping" if self.stopping else "scored" if self.held and scored else "running"
        if self.code == 0 and scored:
            return "finished"
        return "stopped" if self.stopping else "failed"

    def state(self):
        with self.lock:
            return {"status": self.status(), "command": command_text(self.argv), "watch": self.watch,
                    "folders": [shown(f) for f in self.folders],
                    "results": [[kind, shown(path)] for kind, path in self.results],
                    "lines": list(self.lines), "code": self.code, "started": self.started}


# What the last passing Check approved, and the only thing Run may start: the exact argv, the exact bytes of its roster
# snapshot (None without one) and each run's plan fingerprint; and its preflight, which a refused Run shows again.
Approval = namedtuple("Approval", "argv entities fingerprints preflight")


class Session:
    """The page's server side: its actions (preview, check, run, stop), the approval of the last Check if it passed,
    and the one run it may have going. Checks and Runs take turns (gate), so no Check can come between another's
    snapshot, dry run and approval, nor a Run's verification and start. program is how a dry run or a run starts
    probatio, env their environment (None: this process's), both for the tests. redactor is the one every reply
    passes (serve gives it the server's, which knows the token), so preview and Check can refuse a command it would
    change (unshown)."""

    def __init__(self, program=None, env=None):
        self.program = list(program or [sys.executable, *(["-B"] if sys.flags.dont_write_bytecode else []),
                                        str(P.HERE / "probatio.py")])
        self.env, self.lock, self.checked, self.runner = env, threading.Lock(), None, None
        self.redactor = live.ViewRedactor()
        self.gate = threading.RLock()                  # re-entrant only so a nested call in one thread cannot hang

    def actions(self):
        return {"/command": self.command, "/check": self.check, "/run": self.run, "/stop": self.stop}

    def child_env(self):
        """The environment of a dry run or a run: this process's (or the tests'), with Python's output unbuffered so
        the page sees each line as it is printed."""
        return dict(os.environ if self.env is None else self.env, PYTHONUNBUFFERED="1")

    def command(self, request):
        return preview(request.get("form"), redactor=self.redactor)

    def check(self, request):
        """The real --dry-run of the form's command, in a subprocess in probatio's folder: its preflight and the totals,
        or probatio's message verbatim. Its roster snapshot is written first (write_snapshot) and must hold the same
        bytes after the dry run; then the plan is prepared in this process from the same argv and snapshot, and the
        approval binds the argv, those bytes and each run's plan fingerprint. Only the last Check can approve, and
        only if it passed. A form that still needs a field (required) is refused first, with nothing written and no
        dry run. A form build_command refuses, a command the reply's redactor would change (unshown) and a
        dry run that cannot start are refusals too, before anything is written or after nothing ran. A refusal whose
        advice the page cannot follow is in the page's words, probatio's own kept in the whole preflight (refusal).
        A results folder that already holds a run this command would refuse to continue (resume_refusals) is named,
        with the page's fix, and approves nothing, since the run would stop on it at once."""
        with self.gate:
            self.checked = None
            missing = required(request.get("form"))
            if missing:                     # nothing written, no dry run: the page's own line, as preview gives it
                return dict(refused(needs_line(missing), missing), ok=False, plan="", summary=None)
            try:
                argv, entities = build_command(request.get("form"))
            except FormRefused as e:
                return dict(refused(str(e)), ok=False, plan="", summary=None)
            reply = {"argv": argv, "command": command_text(argv), "words": command_words(argv),
                     "entities": entities_view(entities)}
            why = unshown(argv, self.redactor) or roster_unshown(entities, self.redactor)
            if why:                         # before any snapshot is written
                return dict(reply, ok=False, plan="", summary=None, error=why)
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
            except (ValueError, OSError) as e:
                return dict(reply, ok=False, plan="", summary=None, error=not_started("dry run", e))
            if proc.returncode != 0:
                error, plan = refusal(exit_message(None, proc.stderr), proc.stdout)
                return dict(reply, ok=False, plan=plan, summary=None, error=error)
            try:
                if entities and snapshot_bytes(entities) != snapshot:
                    raise SnapshotRefused(f"{entities_path(entities)} changed during the check; check again")
                plans = prepared(argv)
                refusals = resume_refusals(argv, plans, self.child_env())
            except (SnapshotRefused, ResumeUnread) as e:
                return dict(reply, ok=False, plan=proc.stdout, summary=None, error=str(e))
            except SystemExit as e:
                error, plan = refusal(exit_message(e.code), proc.stdout)
                return dict(reply, ok=False, plan=plan, summary=None, error=error)
            if refusals:                    # the run would stop at once on a folder an earlier run holds
                return dict(reply, ok=False, plan=told(proc.stdout, *(said for _, said in refusals)), summary=None,
                            error=blocked_words(refusals))
            self.checked = Approval(tuple(argv), snapshot, tuple(fingerprint for _, fingerprint in plans), proc.stdout)
        return dict(reply, ok=True, plan=proc.stdout, summary=plan_summary(proc.stdout), error=None)

    def run(self, request):
        """Start the form's command exactly as shown and checked (--watch included unless chosen off), once it is
        confirmed, only when it is the command the last Check passed, while no run of the page's is going, and only
        when no results folder has taken a run since that it would refuse to continue: resume_refusals again, just
        before the run starts, refused as Check refuses it, with probatio's own words after the Check's preflight."""
        if request.get("confirmed") is not True:
            return {"error": "confirm the run first"}
        missing = required(request.get("form"))
        if missing:
            return {"error": needs_line(missing), "missing": missing}
        try:
            argv, entities = build_command(request.get("form"))
        except FormRefused as e:
            return {"error": str(e)}
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
            try:
                refusals = resume_refusals(argv, plans, self.child_env())
            except ResumeUnread as e:
                return {"error": str(e)}
            if refusals:                    # a folder that took a run since Check: the run would stop on it at once
                return {"error": blocked_words(refusals),
                        "plan": told(approved.preflight, *(said for _, said in refusals))}
            with self.lock:
                try:
                    self.runner = Runner(self.program, list(approved.argv), [folder for folder, _ in plans],
                                         self.child_env())
                except (ValueError, OSError) as e:
                    return {"ok": False, "error": not_started("run", e)}
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


def serve(session):
    """The page for this session on loopback (live.serve_page), its token in the address, with the session given the
    server's redactor, the one every reply passes."""
    server = live.serve_page(PAGE, session.state, actions=session.actions())
    session.redactor = server.redactor
    return server


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
    server = serve(session)
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
# The --watch page's palette and type (live.PAGE_BASE), light and dark, with its one accent: filled on Run, tinted on a
# selected choice. One system font; monospace only for probatio's own text (the command, the preflight, a run's
# output). The form shows who is measured, the agent and model, and the length; the rest waits in two folds whose
# closed lines say what differs from the defaults. The command, Check, Run and the plan sit on the right, sticky; under
# 880 px they follow the form. The script builds every node with textContent, never innerHTML, reads the token from
# location.search (it is never embedded), and sends every action as a POST with the token in the X-Probatio-Token
# header. The defaults arrive with the first /state; nothing on the page is a default of its own (watch's, on, arrives
# with them, its choices default first). A model is the CLI's default, one of MODELS for the chosen CLI, or any other
# name; choosing another CLI resets it to the default. While the form still needs a field (preview's "needs", from
# required), that one line shows, calm, and Check and Run wait; red is only probatio's own refusal of a complete form,
# word for word.
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>probatio</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer">
<link rel="icon" href="data:,">
<style>
""" + live.PAGE_BASE + """:root { color-scheme:light dark; }
[hidden] { display:none !important; }
main { max-width:1160px; margin:0 auto; padding:28px 24px 56px; }
header { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin:0 0 24px; }
h1 { font-size:20px; margin:0; font-weight:650; letter-spacing:-.01em; } header .sub { color:var(--muted); }
h2 { font-size:14px; font-weight:600; margin:0 0 6px; display:flex; gap:10px; align-items:baseline; }
h2 .aside { font-size:12.5px; font-weight:400; color:var(--muted); margin-left:auto; }
#how { margin-top:28px; }
.pill { font-size:12.5px; color:var(--ink2); display:inline-flex; align-items:center; gap:6px; margin-left:auto; }
.pill::before { content:""; width:8px; height:8px; border-radius:50%; background:var(--muted); }
.pill.running::before, .pill.scored::before { background:var(--good); }
.pill.stopped::before, .pill.failed::before, .pill.lost::before { background:var(--bad); }
.pill.finished::before { background:var(--ink2); }
.layout { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,420px); gap:56px; align-items:start; }
.layout > * { min-width:0; }
.answer { position:sticky; top:20px; max-height:calc(100vh - 40px); overflow:auto; padding:0 2px 8px; }
.answer h2 { margin-top:28px; } .answer > h2:first-child { margin-top:0; }
.row { display:grid; grid-template-columns:104px minmax(0,1fr); column-gap:16px; row-gap:5px; align-items:baseline;
  padding:7px 0; }
.row > .k { color:var(--ink2); } .row > .k::first-letter { text-transform:uppercase; }
.row .def { grid-column:2; font-size:12.5px; color:var(--muted); }
#modelnote::first-letter { text-transform:uppercase; }
button { font:inherit; color:var(--ink); background:var(--track); border:0; border-radius:8px; padding:7px 14px;
  font-weight:500; cursor:pointer; }
button:hover:not(:disabled) { background:var(--line); }
button:disabled { opacity:.4; cursor:default; }
button.primary { background:var(--accent); color:var(--accent-ink); font-weight:600; }
button.primary:hover:not(:disabled) { background:var(--accent-text); }
button.quiet, button.link { background:none; padding:2px 4px; color:var(--muted); font-size:12.5px; }
button.quiet:hover:not(:disabled), button.link:hover:not(:disabled) { background:none; color:var(--ink); }
button.link { text-decoration:underline; text-underline-offset:2px; }
.seg { display:inline-flex; flex-wrap:wrap; gap:2px; padding:3px; background:var(--track); border-radius:10px;
  justify-self:start; }
.seg button { background:none; color:var(--ink2); padding:5px 12px; border-radius:7px; }
.seg button:hover:not(:disabled) { background:var(--line); color:var(--ink); }
.seg button[aria-pressed=true], .seg button[aria-pressed=true]:hover { background:var(--accent-soft);
  color:var(--accent-text); font-weight:600; }
.seg button.off { text-decoration:line-through; } .seg button.off:not([aria-pressed=true]) { color:var(--muted); }
.tag { font-size:11.5px; font-weight:500; color:var(--muted); }
.seg button[aria-pressed=true] .tag { color:var(--accent-text); }
input, textarea, select { font:inherit; color:var(--ink); background-color:var(--field); border:0; border-radius:8px;
  padding:7px 10px; width:100%; }
input::placeholder, textarea::placeholder { color:var(--ink2); opacity:1; }    /* AA on the field fill */
textarea { resize:none; display:block; }
input[type=number] { width:96px; }
select { width:auto; min-width:200px; justify-self:start; padding-right:32px; appearance:none; -webkit-appearance:none;
  background-image:linear-gradient(45deg,transparent 50%,var(--muted) 50%),
    linear-gradient(135deg,var(--muted) 50%,transparent 50%);
  background-position:calc(100% - 17px) 55%, calc(100% - 12px) 55%; background-size:5px 5px;
  background-repeat:no-repeat; }
select:disabled { opacity:.55; cursor:default; }
input:focus-visible, textarea:focus-visible, select:focus-visible, button:focus-visible, summary:focus-visible {
  outline:2px solid var(--accent); outline-offset:1px; }
.model { display:flex; gap:8px; flex-wrap:wrap; } .model input { flex:1 1 180px; width:auto; }
.inline { display:flex; gap:10px; align-items:center; }
details > summary { list-style:none; cursor:pointer; } details > summary::-webkit-details-marker { display:none; }
details > summary::before { content:""; display:inline-block; width:6px; height:6px; margin:0 9px 2px 2px;
  border-right:1.5px solid var(--muted); border-bottom:1.5px solid var(--muted); transform:rotate(-45deg);
  transition:transform .15s; }
details[open] > summary::before { transform:rotate(45deg); }
details.fold { margin:14px 0 0; } #how details.fold { margin-top:10px; }
details.fold > summary { padding:6px 0; font-weight:600; }
details.fold > summary .sum { font-weight:400; font-size:12.5px; color:var(--muted); margin-left:10px; }
details.fold > summary .sum.changed { color:var(--ink2); }
details.small > summary { color:var(--muted); font-size:12.5px; margin:10px 0 0; }
.note { color:var(--muted); font-size:12.5px; margin:4px 0 0; }
.flag { margin:4px 0 0; font-size:12.5px; } .flag .label { color:var(--pending); font-weight:600; }
.flag .hint { color:var(--muted); }
.roster { margin:14px 0 4px; } .roster .top { display:flex; align-items:baseline; gap:10px; margin:0 0 6px; }
.roster .top b { font-weight:500; } .roster .top b::first-letter { text-transform:uppercase; }
.lists { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; }
.lists .k { font-size:12.5px; color:var(--ink2); display:flex; justify-content:space-between; margin:0 0 4px; }
.lists .k .n { color:var(--muted); font-variant-numeric:tabular-nums; } .lists .k .n.bad { color:var(--bad); }
.cmdbox { background:var(--band); border-radius:12px; padding:14px 14px 10px; }
pre { margin:0; font:12.5px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
pre.cmd { white-space:pre-wrap; font-size:13px; } pre.cmd span { white-space:nowrap; }
pre.cmd span.long { white-space:normal; overflow-wrap:anywhere; }
.cmdbar { display:flex; gap:8px; align-items:center; margin:8px 0 0; font-size:12.5px; color:var(--muted); }
.cmdbar button { margin-left:auto; }
.labels, .files { font-size:12.5px; color:var(--muted); margin:10px 0 0; } .labels .warn { color:var(--pending); }
.files code { font:inherit; color:var(--ink2); overflow-wrap:anywhere; }
.files pre { background:var(--band); border-radius:8px; padding:8px 10px; margin:6px 0 0; overflow:auto; }
.err { color:var(--bad); margin:10px 0 0; white-space:pre-wrap; overflow-wrap:anywhere; } .err b { font-weight:600; }
.err.calm { color:var(--ink2); }
.labels:empty, .files:empty, .err:empty { display:none; }
.actions { display:flex; gap:8px; margin:18px 0 0; flex-wrap:wrap; align-items:center; }
.actions .why { color:var(--muted); font-size:12.5px; margin-left:4px; }
.confirm { background:var(--band); border-radius:12px; padding:14px; margin:18px 0 0; }
.confirm p { margin:0 0 12px; } .confirm .actions { margin:0; }
.big { font-size:20px; font-weight:600; font-variant-numeric:tabular-nums; letter-spacing:-.01em; }
.muted { color:var(--muted); font-size:12.5px; } .hintline { color:var(--ink2); font-size:12.5px; margin:6px 0 0; }
.out { background:var(--band); border-radius:10px; padding:10px 12px; margin:8px 0 0; max-height:340px; overflow:auto;
  white-space:pre-wrap; overflow-wrap:anywhere; }
.out div { padding-left:2ch; text-indent:-2ch; } .out .h1 { padding-left:4ch; } .out .h2 { padding-left:6ch; }
.out .h3 { padding-left:8ch; } .out .h4 { padding-left:10ch; } .out .h5 { padding-left:12ch; }
.out .h6 { padding-left:14ch; }
.kv { margin:6px 0 0; font-size:12.5px; color:var(--ink2); overflow-wrap:anywhere; } .kv span { color:var(--muted); }
.kv code { font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
.runbar { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin:0 0 4px; }
.runbar a { color:var(--accent-text); font-weight:600; text-underline-offset:2px; } .runbar .pill { margin-left:0; }
.empty { color:var(--muted); margin:0; }
footer { margin-top:48px; color:var(--muted); font-size:12.5px; }
@media (max-width:880px) {
  main { padding:20px 16px 40px; }
  .layout { grid-template-columns:minmax(0,1fr); gap:36px; }
  .answer { position:static; max-height:none; overflow:visible; padding:0; }
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
<div class="cmdbox"><pre class="cmd" id="cmd">…</pre>
<div class="cmdbar"><span id="where"></span><button type="button" class="quiet" id="copy">Copy</button></div></div>
<div id="labels" class="labels"></div><div id="error" class="err"></div><div id="files" class="files"></div>
<div class="actions" id="actions"><button type="button" id="check">Check</button>
<button type="button" class="primary" id="run">Run…</button><span class="why" id="why"></span></div>
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
const OTHER = "(other)";                                 // the model menu's last entry; no model has this name
const DEFAULT_MODEL = {codex: "default (config.toml)"};  // a CLI's default model, where "default" says too little
const TIME_NOTE = {codex: "the time estimate for Codex is assumed, not measured."};    // else the Sonnet line below
let D = null, F = null, said = null, plan = null, run = null, asked = 0, timer = null, confirming = false;
let checking = false, marks = [], otherModel = false;
const opened = {roster: false, more: false, preflight: true};   // folds stay as left when the form is rebuilt
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
const shownLists = () => F.types.length > 1 ? ["controls"] : ["comparators", "controls"];
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
  return {types: [d.type], targets, rosters, agent: d.agent, tier: d.tier, classes: d.classes.slice(),
          memory: d.memory, auto_memory: d.auto_memory, effort: d.effort, model: d.model || "", watch: D.watch[0],
          jobs: String(d.jobs)};                    // watch's choices come default first
}
// ---- the form
function choices(key, values, opts) {      // a segmented choice; opts: multi, after (on click), def (tagged "default")
  opts = opts || {};
  const box = el("div", "seg"), order = values.map(v => v[0]);
  box.setAttribute("role", "group");
  const mark = () => box.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed",
    String(opts.multi ? F[key].includes(b.value) : F[key] === b.value)));
  for (const [value, word, unavailable] of values) {
    const b = el("button", null, word);
    b.type = "button"; b.value = value;
    if (value === opts.def) add(b, document.createTextNode(" "), el("span", "tag", "default"));
    if (unavailable) { b.title = unavailable; b.classList.add("off"); }
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
function lined(box, text) {                // probatio's text a line per row; a wrapped line hangs under its indent
  for (const line of String(text).replace(/\\n$/, "").split("\\n")) {
    const lead = line.length - line.trimStart().length;
    box.appendChild(el("div", "h" + Math.min(lead >> 1, 6), line.trimStart() || " "));
  }
  return box;
}
function row(label, control, note) {
  return add(el("div", "row"), el("div", "k", label), control, note ? el("div", "def", note) : null);
}
function fold(id, key, title) {            // a fold that stays as the user left it; its closed line is .sum
  const box = el("details", "fold"), sum = el("span", "sum");
  box.id = id; box.open = opened[key];
  box.addEventListener("toggle", () => { opened[key] = box.open; });
  add(box, add(el("summary"), document.createTextNode(title), sum));
  return [box, sum];
}
function measure() {
  const s = $("measure"), cross = F.types.length > 1;
  s.replaceChildren(el("h2", null, "What to measure"));
  add(s, row("type", choices("types", D.types.map(t => [t, t]), {multi: true, after: build, def: D.default.type}),
    cross ? "Compared with each other." : "Scored against an invented control; pick two or three to compare them."));
  for (const t of F.types) {                         // a person, you or anyone, is the individual row's, in every run
    const person = t === "individual";
    const place = person ? "your own name, or anyone's" : cross ? "none (optional)" : "the " + t + " to score";
    add(s, row(t, field(F.targets, t, place, true), (cross ? "Optional, one per line" : "One per line") +
      (person ? (cross ? "; type your own name to include yourself." : "; type your own name to score yourself.") :
        ".")));
  }
}
function against() {
  const s = $("against"), cross = F.types.length > 1, shown = shownLists();
  const [box, sum] = fold("rosters", "roster", cross ? "Controls" : "Comparators and controls");
  const flag = add(el("p", "flag"), el("span", "label"), el("span", "hint",
    " · A control should be a name the model does not know."));
  flag.id = "custom"; flag.hidden = true;
  add(box, flag, el("p", "note", cross ? "Each compared type meets its own invented controls; the first two are used." :
    "The target faces the comparators and is scored against the first control."));
  const summarize = () => {
    const edited = [];
    for (const t of F.types) for (const part of shown)
      if (!same(lines(F.rosters[t][part]), names(D.rosters[t][part]))) edited.push(t + " " + part);
    sum.textContent = edited.length ? "edited: " + edited.join(", ") : "defaults";
    sum.classList.toggle("changed", edited.length > 0);
  };
  for (const t of F.types) {
    const block = el("div", "roster"), reset = el("button", "link", "reset"), counts = {};
    reset.type = "button";
    add(block, add(el("div", "top"), el("b", null, t), reset));
    const grid = el("div", "lists");
    const mark = () => {
      let edited = false;
      for (const part of shown) {
        const [lo, hi] = D.counts[part], n = lines(F.rosters[t][part]).length;
        counts[part].textContent = n + " (" + lo + " to " + hi + ")";
        counts[part].classList.toggle("bad", n < lo || n > hi);
        edited = edited || !same(lines(F.rosters[t][part]), names(D.rosters[t][part]));
      }
      reset.hidden = !edited;
      summarize();
    };
    for (const part of shown) {
      const head = add(el("div", "k"), el("span", null, part), el("span", "n"));
      counts[part] = head.lastChild;
      add(grid, add(el("div"), head, field(F.rosters[t], part, "", true, mark)));
    }
    reset.addEventListener("click", () => { F.rosters[t] = defaultRoster(t); against(); changed(); });
    add(box, add(block, grid)); mark();
  }
  summarize();
  s.replaceChildren(box);
}
function modelChoice() {                   // the chosen CLI's model names: its default first, "other…" (typed) last
  const box = el("div", "model"), menu = el("select");
  const list = F.agent ? (D.models[F.agent] || []).filter(m => m !== "default") : [];
  menu.id = "model"; menu.disabled = !F.agent; menu.setAttribute("aria-label", "model");
  for (const [value, word] of [["", DEFAULT_MODEL[F.agent] || "default"], ...list.map(m => [m, m]),
                                ...(F.agent ? [[OTHER, "other…"]] : [])]) {
    const o = el("option", null, word);
    o.value = value; menu.appendChild(o);
  }
  otherModel = otherModel || (F.model !== "" && !list.includes(F.model));
  const typed = field(F, "model", "a model name");
  typed.hidden = !otherModel;
  menu.value = otherModel ? OTHER : F.model;
  menu.addEventListener("change", () => {
    otherModel = menu.value === OTHER;
    typed.hidden = !otherModel;
    if (otherModel) { typed.value = ""; F.model = ""; typed.focus(); } else F.model = menu.value;
    changed();
  });
  return add(box, menu, typed);
}
function modelNote() {                    // what the time estimate assumes: probatio's own label when the server
  const box = $("modelnote");             // sends one (preview's rates), else the chosen CLI's line
  if (box) box.textContent = said && said.rates ? "time estimate: " + said.rates : TIME_NOTE[F.agent] ||
    "the time estimate is measured on Sonnet (Opus estimated); other models show Sonnet's time.";
}
function jobsField() {                     // a number, tagged "default" while it is the default
  const d = String(D.default.jobs), tag = el("span", "tag", "default");
  const show = () => { tag.hidden = String(F.jobs).trim() !== d; };
  const jobs = field(F, "jobs", d, false, show);
  jobs.type = "number"; jobs.min = "1"; show();
  return add(el("div", "inline"), jobs, tag);
}
function how() {
  const s = $("how"), d = D.default, planned = D.planned[F.tier] || D.classes;
  s.replaceChildren(el("h2", null, "How to run"));
  add(s, row("agent", choices("agent", D.agents, {after: () => { F.model = ""; otherModel = false; how(); }}),
    F.agent ? "Runs on your own login and quota." : "Choose one; it runs on your own login and quota."));
  const note = el("div", "def");
  note.id = "modelnote";
  add(s, add(row("model", modelChoice()), note)); modelNote();
  add(s, row("length", choices("tier", D.tiers.map(t => [t, t]), {after: how, def: d.tier}),
    "Check shows the calls and the time."));
  const [more, sum] = fold("more", "more", "More options");
  sum.id = "moresum";
  add(more,
    row("classes", choices("classes", D.classes.map(k => [k, CLASS_WORDS[k] || k,
      planned.includes(k) ? null : "not asked at " + F.tier]), {multi: true}),
      planned.length < D.classes.length ? "At " + F.tier + ", only " + planned.join(" and ") + "." : null),
    row("memory", choices("memory", D.memory.map(m => [m, m]), {def: d.memory}),
      "Both asks each item with and without your memory files."),
    row("effort", choices("effort", D.efforts.map(e => [e, e]), {def: d.effort})),
    row("watch", choices("watch", D.watch.map(w => [w, w]), {def: D.watch[0]}),
      "On opens a live view in your browser; off still shows the run's output here."),
    row("jobs", jobsField(), D.measured_jobs ? "Calls at once; more is faster but heavier on your rate limit; the " +
      "time estimate scales with it, from rates measured at " + D.measured_jobs + "." :
      "Calls at once; more is faster but heavier on your rate limit."),
    row("auto memory", choices("auto_memory", D.auto_memory.map(m => [m, m]), {def: d.auto_memory}),
      "Claude Code's own saved notes (Claude Code only): leave as configured to measure your agent as it is; " +
      "off, in both conditions, is the calibration's setting."));
  s.appendChild(more); moreSummary();
}
function moreSummary() {                   // More options' closed line: what differs from the defaults
  const box = $("moresum");
  if (!box) return;
  const d = D.default, diff = [];
  if (!same(F.classes, D.classes)) diff.push("classes " + F.classes.join(", "));
  for (const [key, word, base] of [["memory", "memory", d.memory], ["effort", "effort", d.effort],
                                   ["watch", "watch", D.watch[0]], ["auto_memory", "auto memory", d.auto_memory]])
    if (F[key] !== base) diff.push(word + " " + F[key]);
  if (String(F.jobs).trim() !== String(d.jobs)) diff.push("jobs " + String(F.jobs).trim());
  box.textContent = diff.length ? diff.join(" · ") : "defaults";
  box.classList.toggle("changed", diff.length > 0);
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
  modelNote();
  const labels = $("labels"); labels.replaceChildren();
  (said ? said.labels : []).forEach((l, i) => add(labels, i ? document.createTextNode(" · ") : null,
    el("span", /not (calibrated|registered)/.test(l) ? "warn" : null, l)));
  const files = $("files"); files.replaceChildren();
  if (said && said.entities) {
    add(files, document.createTextNode("Roster edits go in "), el("code", null, said.entities.path),
      document.createTextNode(", a read-only file named by its content, written when you check."));
    files.appendChild(add(el("details", "small"), el("summary", null, "The file"),
      el("pre", null, said.entities.text)));
  }
  const custom = $("custom");
  if (custom) { custom.hidden = !(said && said.entities); custom.firstChild.textContent = said && said.roster || ""; }
  const error = $("error"), needs = said && said.needs;    // a field still empty: one calm line, never an error
  error.replaceChildren(); error.classList.toggle("calm", !!needs);
  if (needs) error.textContent = needs;
  else if (said && said.error) add(error, el("b", null, "probatio: "), document.createTextNode(said.error));
  const stale = plan && plan.ok && keyOf(plan) !== keyOf(said);
  $("check").disabled = !said || !!needs || !!said.error || checking;
  const ready = plan && plan.ok && !stale && !going() && !checking;     // not while a Check is in flight
  $("run").disabled = !ready;
  $("why").textContent = going() ? "a run is going" : needs ? said.missing[0] + " first" :
    !plan || !plan.ok ? "check first" : stale ?
    "the command changed: check it again" : "";
  $("actions").hidden = confirming && ready;                 // while confirming, Start the run is the one primary
  if (D) moreSummary();
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
  const go = el("button", "primary", "Start the run"), no = el("button", "quiet", "Cancel");
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
  if (!plan.ok) {                         // a refusal: the page's words, and probatio's own in the whole preflight
    add(box, add(el("div", "err"), el("b", null, "probatio: "), document.createTextNode(plan.error)));
    if (plan.plan) box.appendChild(preflight(plan.plan));
    return;
  }
  const sm = plan.summary || {};
  const head = [sm.calls !== null && sm.calls !== undefined ? sm.calls + " calls" : null, sm.time].filter(Boolean);
  if (head.length) box.appendChild(el("div", "big", head.join(" · ")));
  if (sm.hint) box.appendChild(el("div", "hintline", sm.hint));
  box.appendChild(preflight(plan.plan));
}
function preflight(text) {                // the dry run's whole output, in a fold that stays as the user left it
  const det = add(el("details", "small"), el("summary", null, "The whole preflight"), lined(el("pre", "out"), text));
  det.open = opened.preflight;
  det.addEventListener("toggle", () => { opened.preflight = det.open; });
  return det;
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
  const pre = lined(el("pre", "out"), run.lines.join("\\n") || "(no output yet)");
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
    if (r.error)                          // a refused Run may bring the preflight, probatio's own words after it
      plan = Object.assign({}, plan, {ok: false, error: r.error}, r.plan === undefined ? {} : {plan: r.plan});
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
