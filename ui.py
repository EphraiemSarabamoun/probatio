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
                    with no model call beyond the CLI's version probe; then the command's default-folder selector,
                    read only with the run's metadata (resume_refusals), for Runner's actual results roots
    versus          once the rows hold two or three names of different types, one question: your names against
                    each other (versus_run's command, `--versus TYPE=NAME`, the default) or the types in general
                    (today's comparison). build_versus_command, versus_preview, Session.check_versus and
                    Session.run_versus are its own small functions beside v4's; nothing of v4's path changes
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
import html
import inspect
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
import types
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
# has no aliases). The registry owns those choices; this public view keeps callers that read MODELS working.
# The page's "other" takes any name, and each agent's own default is the page's first choice.
MODELS = P.AgentTable(lambda spec: spec.models)
WATCH = "--watch"                     # the run's live view; the page reads its link from the run's output
WATCH_CHOICES = ("on", "off")         # the page's watch choice, its default first: on (the parser's own is off)
TAIL = 40                             # the newest lines of a run's output on the page
CHECK_SECONDS = 120                   # a dry run that takes longer is reported, not waited for
MEMORY_SECONDS = 30                   # the memory block's child process; past this the block says it could not look
STOP_SECONDS = 10                     # after a second Ctrl-C, how long the page waits for its run to end
BOUNDARY = ("Local to this machine. Check makes no model call (only the CLI's version check); Run uses your own CLI "
            "login and quota.")
# What the page reads from probatio's output: the --watch link, a scored run's result lines, the held page's line,
# and the preflight's totals (each run's calls and time, several runs' total time, the 45-minute hint). The link is
# probatio's own line, whole (live.Watch prints it before any call), read only when the run's argv has --watch and
# only the first time: text elsewhere in the output (a model's name, a CLI's error) never becomes the link.
WATCH_LINK = re.compile(r"^  watch it live: (http://127\.0\.0\.1:\d+/\?t=[A-Za-z0-9_-]+)$")
RESULT_LINE = re.compile(r"^(Scorecard|Report|Page): (\S.*)$")   # a v4 run's files, then its results page
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
NEEDS_LIST = "add {} to the {} {}"          # e.g. "add a control to the individual controls"
NEEDS_CLASS = "choose at least one class"
NEEDS_FOLDER = "type a config folder that exists, or leave it empty for the default ({} is not a folder)"
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
               "config_dir": "the config folder",
               "jobs": "the jobs", "watch": "the watch choice", "types": "the types", "versus": "the question",
               "strangers": "the comparison choice"}
# versus (docs/versus/DESIGN.md 9): once the rows hold two or three names of different types, the form asks one
# question, its first answer the default: the names against each other (versus_run's command) or the types in general
# (today's comparison, unchanged). Every sentence of it is here, and the page reads them with the defaults.
VERSUS_FIELD = "versus"                     # the form's field for the question's answer
VERSUS_ANSWERS = ("names", "types")         # the answers, the default first
VERSUS_FLAG = "--versus"                    # versus_run.VERSUS_OPTION, as build_versus_command writes it, one word
VERSUS_WORDS = {
    "question": "compare",
    "names": "Your names against each other",
    "names_line": "asks questions in which your names want the same thing, and shows which one your agent sided with",
    "types": "The types in general, with invented entities",
    "types_line": ("scores how your agent treats people, organizations and governments as kinds; your names are not "
                   "compared with each other"),
    "strangers": "comparison",
    "strangers_line": "Also ask about an unfamiliar roster, for comparison; on doubles the questions.",
    "length_line": "Check shows the calls and the time; a versus run is quick or standard, with no full length.",
}
NEEDS_VERSUS_TIER = "choose quick or standard (a versus run has no full length)"
# What the page reads from a versus run's output: the preflight's total (its questions and episodes, the time and the
# rates) and its result line, the run's folder (versus_run's TOTAL_LINE and RESULTS_LINE).
VERSUS_TOTAL = re.compile(r"^\s+all\s+(\d+) questions\s+(\d+) episodes\s+(.+?)(?: \((.+)\))?$")
VERSUS_RESULT_LINE = re.compile(r"^(Results|Page): (\S.*)$")   # the folder, then its results page (results.html)
FOLDER_UNREAD = "Check could not read where the run would write from the dry run's preflight; check again"


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
    answer = {
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
        "labels": {"cross": P.CROSS_LABEL}, "names_role": P.names_said(None, "standard")[0],
        "program": shlex.join(PROGRAM), "boundary": BOUNDARY, "versus": versus_defaults(),
    }
    if len(P.agents()) > 2:
        answer["availability"] = agent_availability()
    return answer


def agent_availability():
    """Capabilities for the form when an extension adds choices. The shipped two-agent reply stays unchanged;
    menus annotate unsupported requests while Check keeps the command's own refusal authoritative."""
    found = P.agents()
    return {id_: {"families": list(found[id_].families), "classes": list(found[id_].classes),
                  "memory": list(found[id_].memory), "efforts": list(found[id_].efforts),
                  "default_model_label": found[id_].default_model_label, "time_note": found[id_].time_note,
                  "timing": P.agent_callback(id_, "rates_profile", None,
                                              P.DEFAULT_EFFORT if found[id_].efforts else None)[0] is not None}
            for id_ in found}


def agent_page_words(field):
    """Registry display words as script JSON, without changing the shipped defaults reply. Escaping '<' keeps a
    display label from ending the script element; the browser still reads its original text."""
    found = P.agents()
    return json.dumps({id_: getattr(found[id_], field) for id_ in found}).replace("<", "\\u003c")


@functools.lru_cache(maxsize=None)
def versus_command():
    """versus_run.py, the versus command (probatio's import_versus_run), imported once: its parser, its words and
    the functions the page asks about a versus command."""
    return P.import_versus_run()


def versus_defaults():
    """What a versus run starts from, read from versus_run's own parser and never typed here: each option's default
    (the parser with the one option it requires, an agent, which this never sends), its lengths, memory and strangers
    choices (each default first), the question's answers and the page's words for it (VERSUS_WORDS)."""
    VR = versus_command()
    a = VR.parser().parse_args(["--agent", next(iter(P.AGENT_NAMES))])
    acts = {x.dest: x for x in VR.parser()._actions}
    first = lambda dest: [getattr(a, dest)] + [c for c in acts[dest].choices if c != getattr(a, dest)]  # noqa: E731
    return {"default": {k: getattr(a, k) for k in ("tier", "memory", "strangers", "effort", "jobs")},
            "tiers": list(acts["tier"].choices), "memory": first("memory"), "strangers": first("strangers"),
            "answers": list(VERSUS_ANSWERS), "words": VERSUS_WORDS}


# ---- the form as a command line (pure) ---------------------------------------------------------------------------------
# The page sends its form as JSON: {"types": [...], "targets": {type: text}, "rosters": {type: {"comparators": text,
# "controls": text}}, "agent", "tier", "classes": [...], "memory", "auto_memory", "effort", "model", "jobs",
# "config_dir"}, every list of names one name per line, and "watch": "on" or "off". A field left out is its default
# (watch's is on; config_dir's, empty, is the CLI's own folder); a person is a target like any other, so a "name" field
# is ignored.
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
    return P.json_text(data, indent=2, sort_keys=True) + "\n" if data else None


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
    target (checked_names, plan_runs) and the page never writes --name; enough names in each roster list the form
    shows (lists_shown: a comparison shows only the controls), since an edited list below its LIST_COUNTS minimum is
    refused (an over-full list is a wrong value, probatio's); and at least one class, since an empty --class is
    refused. A comparison's named targets are optional. Only fields left empty count: a value given but
    wrong is probatio's to refuse, in its own words. A versus run needs an agent and one of its own lengths
    (versus_required). Pure; base is defaults()."""
    base = base or defaults()
    form = form if isinstance(form, dict) else {}
    if versus_chosen(form, base):
        return versus_required(form, base)
    needs = [] if text_of(form.get("agent")) else [NEEDS_AGENT]
    types = types_of(form, base)
    targets = form.get("targets") if isinstance(form.get("targets"), dict) else {}
    if len(types) == 1 and not names_of(targets.get(types[0])):
        needs.append(NEEDS_NAME.format(types[0]))
    rosters = form.get("rosters") if isinstance(form.get("rosters"), dict) else {}
    for kind in types:
        edits = rosters.get(kind) if isinstance(rosters.get(kind), dict) else {}
        for part in lists_shown(types):
            given, least = len(names_of(edits.get(part))), P.LIST_COUNTS[part][0]
            if part in edits and given < least:
                needs.append(NEEDS_LIST.format(too_few(least - given, given, P.LIST_ROLE[part]), kind, part))
    picked = form.get("classes")
    if isinstance(picked, list) and not [k for k in P.V4_CLASSES if k in picked]:
        needs.append(NEEDS_CLASS)
    return needs


def versus_required(form, base):
    """What a versus form still needs: an agent, and a length the versus run has (quick or standard), since the form
    keeps the length chosen before the question and a versus run has no full (NEEDS_VERSUS_TIER). Pure."""
    needs = [] if text_of(form.get("agent")) else [NEEDS_AGENT]
    tier = text_of(form.get("tier")) or base["versus"]["default"]["tier"]
    if tier in P.USER_TIERS and tier not in base["versus"]["tiers"]:
        needs.append(NEEDS_VERSUS_TIER)
    return needs


def too_few(short, given, role):
    """How many more names a roster list needs, in words: "a control", "another comparator", "2 comparators"."""
    return f"{'another' if given else 'a'} {role}" if short == 1 else f"{short} {role}s"


def needs_line(needs):
    """required's pieces as the page shows them: one calm line, within the display cap as a whole (capped), or None
    when nothing is missing."""
    return P.capped(NEEDS_LINE.format(" · ".join(needs))) if needs else None


def folder_needs(form, env=None):
    """The config folder field as a missing piece (NEEDS_FOLDER) when it names no folder, read as probatio reads
    --config-dir in the run (config_path: ~ is the run's HOME, a relative path is from probatio's folder, where the run
    starts); [] when it is empty or a folder. env is the run's environment (None: this process's). The page calls it
    beside required, which stays pure."""
    folder = text_of(form.get("config_dir")) if isinstance(form, dict) else ""
    if not folder or "\x00" in folder:
        return []
    home = (os.environ if env is None else env).get("HOME")
    return [] if P.is_folder(P.config_path(folder, home=home, cwd=P.HERE)) else \
        [P.capped(NEEDS_FOLDER.format(P.display(folder)))]         # the whole sentence within the cap


def form_needs(form, base=None, env=None):
    """Everything the form still needs, in the page's order: required's pieces, then the config folder's
    (folder_needs)."""
    return required(form, base) + folder_needs(form, env)


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


def versus_rows(form, base):
    """The names the form's rows hold when they ask the question: {type: name} in probatio's type order when the
    chosen types' rows hold two or three names of different types (no row more than one), else None. Rows of one
    type, a single name and four names keep today's form."""
    targets = form.get("targets") if isinstance(form.get("targets"), dict) else {}
    rows = {t: names_of(targets.get(t)) for t in types_of(form, base)}
    if any(len(names) > 1 for names in rows.values()):
        return None
    named = {t: names[0] for t, names in rows.items() if names}
    return named if len(named) in (2, 3) else None


def versus_answer(form):
    """The question's answer: the form's VERSUS_FIELD when it is one of VERSUS_ANSWERS, else the default, the
    first."""
    value = text_of(form.get(VERSUS_FIELD))
    return value if value in VERSUS_ANSWERS else VERSUS_ANSWERS[0]


def question_of(form, base):
    """The question's state for the page, {"answer": ...}, while the rows ask it (versus_rows), else None."""
    return {"answer": versus_answer(form)} if versus_rows(form, base) else None


def versus_chosen(form, base):
    """The names a versus run compares, when the rows ask the question and its answer is the first (your names
    against each other); else None, and the form writes what it writes today."""
    names = versus_rows(form, base)
    return names if names and versus_answer(form) == VERSUS_ANSWERS[0] else None


def build_versus_command(form, names, base):
    """A versus run's argv, pure, with only the options that change something (versus_run's own defaults, read in
    versus_defaults): --agent; --versus TYPE=NAME for each row in the rows' order, each name through probatio's name
    guard in its form (row_name, as today's rows); --tier quick (a length outside the page's own is refused as
    today, TIER_REFUSED, and full is what required asks to change); --memory, --strangers on, --effort, --model,
    --config-dir and --jobs as chosen; --watch unless the watch choice is off. Nothing that belongs only to the
    other runs is written, whatever the form holds: classes, rosters, auto memory. FormRefused for a NUL."""
    v, argv = base["versus"]["default"], []
    agent = text_of(form.get("agent"))
    if agent:
        argv += ["--agent", agent]
    for t, n in names.items():
        argv += [VERSUS_FLAG, f"{t}={row_name(n, t)}"]
    tier = text_of(form.get("tier")) or v["tier"]
    if tier not in P.USER_TIERS:
        raise FormRefused(TIER_REFUSED)
    if tier != v["tier"]:
        argv += ["--tier", tier]
    for key, flag in (("memory", "--memory"), ("strangers", "--strangers"), ("effort", "--effort")):
        value = text_of(form.get(key))
        if value and value != v[key]:
            argv += [flag, value]
    model = text_of(form.get("model"))
    if model:
        argv += ["--model", model]
    folder = text_of(form.get("config_dir"))
    if folder:
        argv += ["--config-dir", folder]
    jobs = text_of(str(form["jobs"])) if form.get("jobs") is not None else ""
    if jobs and jobs != str(v["jobs"]):
        argv += ["--jobs", jobs]
    if form.get("watch") not in (False, "off"):
        argv.append(WATCH)
    if any("\x00" in word for word in argv):
        raise FormRefused(NUL_REFUSED)
    return argv


def is_versus(argv):
    """Whether an argv the page built is a versus run's (build_versus_command writes VERSUS_FLAG as its own word)."""
    return VERSUS_FLAG in argv


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
    compares with standard's I,II), and an empty one so the parser says why. A config folder typed in its field is
    --config-dir FOLDER (empty: the CLI's own folder, and no option). --watch comes last unless the form's
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
    names = versus_chosen(form, base)
    if names:                                       # the question answered "your names": versus_run's command
        return build_versus_command(form, names, base), None
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
    folder = text_of(form.get("config_dir"))
    if folder:
        argv += ["--config-dir", folder]
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
    """The command's words, each quoted for a shell as shlex.join quotes it and displayed (probatio's display),
    so a word typed with an escape or an invisible character shows it escaped; the page keeps each one on one line.
    They are for the eye and for pasting: Run starts argv itself, never these words, so a word display changes (one
    longer than DISPLAY_CHARS, cut, or one holding a character it escapes) is pasted as shown, not as Run starts it."""
    return [P.display(shlex.quote(w)) for w in (*PROGRAM, *argv)]


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


DEFAULT_FOLDERS_TAKEN = re.compile(
    r"^All (?P<count>\d+) default results folders for (?P<folder>.+) are taken; pass --out to choose another "
    r"folder or clear old folders\.$")


def exhausted_words(message):
    """When all default siblings are occupied, name what can be done from either form, retaining the CLI line
    separately in the whole preflight. Both forms always use results/ and cannot choose --out."""
    match = DEFAULT_FOLDERS_TAKEN.match(message or "")
    if match:
        prefix = f"All {match.group('count')} default results folders for "
        advice = " are taken; move or rename old run folders under results/, then check again."
        # The action stays whole even when the displayed install path uses the entire token cap.
        room = max(1, P.DISPLAY_CHARS - len(prefix) - len(advice))
        return prefix + P.capped(shown(match.group('folder')), room) + advice
    return None


def page_words(message):
    """The page's words for a refusal of probatio's whose advice the page cannot follow (PAGE_WORDS), or None when the
    message is one the page shows as it is. A folder is named as the page names it (shown); the words are one value,
    within the display cap as a whole (capped)."""
    exhausted = exhausted_words(message)
    if exhausted:
        return exhausted
    for pattern, words in PAGE_WORDS:
        m = pattern.match(message or "")
        if m:
            return P.capped(words.format(**{k: shown(v) if k == "folder" else v for k, v in m.groupdict().items()}))
    return None


def template_pattern(template):
    """A line template of versus_run's ("{folder} holds ...") as a pattern of the whole line, each {field} a named
    group, so the page reads the command's own words from their one home."""
    parts = re.split(r"\{(\w+)\}", template)
    return re.compile("^" + "".join(re.escape(x) if i % 2 == 0 else f"(?P<{x}>.+?)" for i, x in enumerate(parts))
                      + "$")


# A folder the system cannot look at still refuses before selecting a sibling. The form cannot write --out, so
# explain that refusal in its own words, retaining the command's line in the whole preflight.
VERSUS_PAGE_WORDS = {
    "FOLDER_UNUSABLE": ("the results folder {folder} cannot be used ({why}), and the page always writes there: make "
                        "it usable, or run the command from a terminal with --out"),
}


def versus_page_words(message):
    """The page's words for a versus refusal whose advice names --out (VERSUS_PAGE_WORDS), read with the command's
    own templates (template_pattern), or None for a message the page shows as it is. A folder is named as the page
    names it (shown), and every other part as the command quoted it."""
    exhausted = exhausted_words(message)
    if exhausted:
        return exhausted
    VR = versus_command()
    for name, words in VERSUS_PAGE_WORDS.items():
        m = template_pattern(getattr(VR, name)).match(message or "")
        if m:
            return words.format(**{k: shown(v) if k == "folder" else v for k, v in m.groupdict().items()})
    return None


def versus_folder(preflight, folder):
    """What a versus dry run's preflight says of the results folder (its last folder line, versus_run's FOLDER_FRESH,
    FOLDER_RESUME or FOLDER_REFUSED): (state, why), why the command's refusal or None; FOLDER_UNREAD as the why of
    "unread" when no such line names folder, the one the page has prepared, so Check never approves a folder it did
    not see."""
    VR, said = versus_command(), None
    for state, template in (("fresh", VR.FOLDER_FRESH), ("resume", VR.FOLDER_RESUME), ("refused", VR.FOLDER_REFUSED)):
        for line in preflight.splitlines():
            m = template_pattern(template).match(line)
            if m and m.group("folder") == P.display(folder):
                said = (state, m.groupdict().get("why"))
    return said or ("unread", FOLDER_UNREAD)


def refusal_in_words(said, preflight, words):
    """A refusal as Check shows it, (error, plan), with the page's words when words (page_words or
    versus_page_words) has some, probatio's own message kept in the whole preflight; else the message itself, cut
    as a whole (capped) and then kept whole in the preflight too."""
    mine = words(said)
    if mine:
        return mine, told(preflight, said)
    error = P.capped(said)
    return error, (preflight if error == said else told(preflight, said))


def told(preflight, *messages):
    """The whole preflight with probatio's own messages after it, as a terminal shows them: stdout, then each
    refusal."""
    return preflight + "".join(m.rstrip("\n") + "\n" for m in messages if m)


def refusal(said, preflight):
    """A refusal of probatio's as Check shows it, (error, plan): the page's words with probatio's own message kept in
    the whole preflight (page_words), or the message itself, cut as a whole (capped) and then kept whole in the
    preflight too (refusal_in_words)."""
    return refusal_in_words(said, preflight, page_words)


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


def preview(form, base=None, redactor=None, env=None):
    """The answer to the form as it changes: the command (argv and its text), the entities file it needs, the parser's
    verdict (verbatim), and the calibration words probatio prints for the selection and the roster: cross-type and
    unregistered (DESIGN 11's labels), and fixtures_label, "custom roster: not calibrated (fixtures …)" when the
    lists differ from the defaults. A roster that load_entities refuses gives its message instead. A form
    build_command refuses has no command, only why; a command the reply's redactor (the server's; None: one built
    from this process's environment) would change gets unshown's message once the parser accepts it, and a roster
    file it would change roster_unshown's once probatio accepts the file. What the form still needs (required) comes
    as "missing" and "needs", its line; the page shows that line, calm, instead of any error until nothing is
    missing, and the command so far all the while; a config folder that is not one counts (folder_needs, read with
    env, the run's environment). In a comparison of types, "names_said" is what a name typed there does (probatio's
    names_said: the role sentence in the roster's words, custom_controls deciding whether "invented" is true, and at
    quick the scorecard's own quick-tier sentence)."""
    base = base or defaults()
    form = form if isinstance(form, dict) else {}
    missing, question = form_needs(form, base, env), question_of(form, base)
    try:
        argv, entities = build_command(form, base)
    except FormRefused as e:
        return with_question(refused(str(e), missing), question)
    redactor = redactor or live.ViewRedactor()
    if versus_chosen(form, base):
        return versus_preview(argv, missing, question, redactor)
    types = types_of(form, base)
    reply = {"argv": argv, "command": command_text(argv), "words": command_words(argv),
             "entities": entities_view(entities), "labels": [], "roster": None, "missing": missing,
             "needs": needs_line(missing), "error": parser_error(argv) or unshown(argv, redactor)}
    if len(types) > 1:
        reply["labels"].append(P.CROSS_LABEL)
        if not P.canonical_compare([tuple(types)])["registered"]:
            reply["labels"].append(P.UNREGISTERED_LABEL)
    kind = "custom"                      # ordo's roster words: "invented" only when every control is known to be
    try:
        spec = P.load_entities(None, bank(), data=json.loads(entities) if entities else None)
        reply["roster"] = P.fixtures_label(spec, types)
        reply["labels"].append(reply["roster"])
        kind = "custom" if P.custom_controls(spec, bank(), types) else "baseline"
    except SystemExit as e:
        reply["error"] = reply["error"] or exit_message(e.code)
    reply["names_said"] = P.names_said(text_of(form.get("agent")), text_of(form.get("tier")) or
                                       base["default"]["tier"], kind) if len(types) > 1 else []
    reply["error"] = reply["error"] or roster_unshown(entities, redactor)
    if reply["error"]:                   # probatio's message or the page's: one value, within the cap as a whole
        reply["error"] = P.capped(reply["error"])
    return with_question(reply, question)


def with_question(reply, question):
    """A reply with the question's state for the page (question_of) while the rows ask it; any other reply is
    exactly today's."""
    return reply if question is None else dict(reply, question=question)


def versus_preview(argv, missing, question, redactor):
    """preview's answer for a versus form: the command, versus_run's own parser on it (versus_parser_error, verbatim)
    or unshown's message, the bank's status as the preflight prints it (versus_label), what the form still needs, and
    the question's state. A versus command has no roster file and no comparison sentence."""
    return {"argv": argv, "command": command_text(argv), "words": command_words(argv), "entities": None,
            "labels": [versus_label()], "roster": None, "missing": missing, "needs": needs_line(missing),
            "error": versus_parser_error(argv) or unshown(argv, redactor), "names_said": [], "question": question}


def versus_parser_error(argv):
    """versus_run's own parser on argv, in this process, and its reading of the --versus values (parse,
    versus_names): None when it accepts the command line, else its message verbatim. Every other guard runs in
    Check's dry run."""
    VR, err = versus_command(), io.StringIO()
    with PARSE_LOCK, contextlib.redirect_stderr(err):
        try:
            VR.versus_names(VR.parse(argv).versus)
        except SystemExit as e:
            return exit_message(e.code, err.getvalue())
    return None


@functools.lru_cache(maxsize=None)
def versus_label():
    """The versus bank's status as the preflight prints it (versus_run.bank_status, whose BANK_STATUSES are the
    words: the shipped bank's is "registered bank: reviewed and frozen, not yet validated on live agents (pilot: not
    calibrated)" until its manifest is marked validated), for the labels under the command; read once."""
    VR = versus_command()
    return VR.bank_status(VR.load_bank(VR.parser().parse_args(["--agent", next(iter(P.AGENT_NAMES))])))


def versus_summary(text):
    """A versus dry run's totals as the confirm step states them, from its own preflight lines: the questions and
    episodes of every arm added up as calls, the time, the 45-minute hint and the rates. None for a value it does
    not print."""
    calls, time_, hint, rates = None, None, None, None
    for line in text.splitlines():
        m = VERSUS_TOTAL.match(line)
        if m:
            calls, time_, rates = int(m.group(1)) + int(m.group(2)), m.group(3), m.group(4)
        elif TIME_HINT.match(line):
            hint = TIME_HINT.match(line).group(1)
    return {"calls": calls, "time": time_, "hint": hint, "rates": rates}


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
        why = "a symbolic link" if P.link_there(path) else os.strerror(e.errno or 0)
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


def prepared(argv, named=False):
    """argv's runs as probatio prepares them (prepare_runs), with no call to any CLI: each run's results folder
    (run_folder) and plan fingerprint (plan_fingerprint), in run order. The entities file is read from probatio's
    folder, the run's working directory. A folder that cannot be resolved is the refusal of a run that cannot start
    (preparing, prepared_folder), as SystemExit like probatio's own refusals. named keeps each unresolved spelling
    after the same checks, so a dangling default link still counts as occupied when choosing siblings."""
    a = P.parse_args_v4(absolute(argv))
    runs = preparing(P.prepare_runs, a, "--target-type" in argv)[4]     # as main_v4 tells it: written as one word
    many = len(runs) > 1
    plans = []
    for run in runs:
        raw = folder_named(a, run["name"], many)
        folder = prepared_folder(raw, P.run_folder, a, run, many)
        plans.append((raw if named else folder, P.plan_fingerprint(run["calls"])))
    return plans


def preparing(prepare, *args):
    """prepare(*args), a command's own step that resolves a run's results folder (probatio's prepare_runs, through
    check_run_folders, and run_folder; versus_run's run_folder), left as it is, or SystemExit with the form's refusal
    of a run that cannot start (not_started) when the folder cannot be resolved: a RuntimeError raised while a
    command's run_folder resolves it (resolving: before Python 3.13, a link loop, or a chain of links too long for
    resolve's recursion, RecursionError; from 3.13 on resolve raises for neither, and the look at the folder finds
    both before this step runs), said as unresolvable says it, or an OSError or ValueError, each as what it is
    (prepare_failed). A RuntimeError from anywhere else, a RecursionError included, is not this and is raised as
    before.
    The form's two prepare paths (prepared, versus_prepared) go through it, so a Check or a Run whose folder is a link
    loop or a long chain of links is always answered."""
    try:
        return prepare(*args)
    except RuntimeError as e:                   # RecursionError is one
        if not resolving(e):
            raise
        raise SystemExit(not_started("run", unresolvable())) from None
    except (OSError, ValueError) as e:
        raise SystemExit(prepare_failed(e)) from None


def prepare_failed(error):
    """Why a run cannot start when a command's own prepare step (preparing) raised an OSError or a ValueError, as what
    it is: an OSError by its errno's words (not_started, "Permission denied"), a ValueError by its own message,
    displayed and bounded (a file the step reads that it cannot parse, a value it cannot take: "Expecting value: line
    1 column 1 (char 0)"), or its type's name when it has none. Never not_started's words for a ValueError, which are
    subprocess's, of a command line no program can take: the prepare step starts no program."""
    if isinstance(error, OSError):
        return not_started("run", error)
    return f"the run could not start ({P.display(str(error).strip() or type(error).__name__)})"


def resolving(error):
    """Whether error was raised while a command's own run_folder (probatio's or versus_run's) was resolving a run's
    folder: one of their frames on its traceback. What resolving a folder raises, told from the same error raised
    anywhere else. It never raises itself (folder_codes): a run_folder replaced by something with no code of its own
    (a test's mock) is simply not on the traceback, and the error is raised as any other."""
    codes = folder_codes()
    tb = error.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code in codes:
            return True
        tb = tb.tb_next
    return False


def folder_codes():
    """The code objects of probatio's and versus_run's run_folder, as resolving looks for them on a traceback: each
    function's own, and the function it wraps (inspect.unwrap, for a functools.wraps wrapper), and nothing for a
    stand-in with no __code__ (a mock) or a versus command that cannot be imported."""
    functions = [P.run_folder]
    try:
        functions.append(versus_command().run_folder)
    except Exception:                       # noqa: BLE001  (no versus command: probatio's alone)
        pass
    codes = set()
    for f in functions:
        try:
            inner = inspect.unwrap(f)
        except Exception:                   # noqa: BLE001  (a wrapper chain that loops: the function alone)
            inner = f
        for g in (f, inner):
            code = getattr(g, "__code__", None)
            if isinstance(code, types.CodeType):
                codes.add(code)
    return codes


def folder_named(a, run_name, many=False):
    """A run's results folder as its command names it, before anything resolves it: --out (each of many runs in its
    own folder inside it), or results/<run name> in probatio's folder. probatio's and versus_run's run_folder resolve
    exactly this (a test holds them together); the form looks at it as named (cannot_resolve) because resolving it
    follows its links, a chain of any length on Python 3.13, where a look stops at the system's limit."""
    if a.out and not many:
        return Path(a.out)
    return (Path(a.out) if a.out else P.HERE / "results") / run_name


def prepared_folder(named, run_folder, *args):
    """A run's folder as its command resolves it (run_folder(*args), through preparing), once a look at it as named
    (cannot_resolve) finds it can be resolved; else SystemExit with the refusal of a run that cannot start."""
    if cannot_resolve(named):
        raise SystemExit(not_started("run", unresolvable()))
    return preparing(run_folder, *args)


def run_card_facts(argv, count):
    """Each run's facts for its run card, in run order, as the --watch page has them (live.run_facts' fields,
    displayed): the kind, the target and its type or the comparison and its types (the card's title is the results
    page's, live.card_title, from these), the agent, the model as the command names it (none: the CLI's default),
    the tier and memory. From the command's own parse and plan (probatio's prepare_runs; no CLI is called), so they
    are the facts of the command Run started, not of the form as it is now: count of them, the command's own facts
    for every run when its runs cannot be told apart, nothing when it cannot be read (the card shows what it has)."""
    try:
        with PARSE_LOCK, contextlib.redirect_stderr(io.StringIO()):
            a = P.parse_args_v4(absolute(argv))
            runs = preparing(P.prepare_runs, a, "--target-type" in argv)[4]
    except (SystemExit, Exception):                    # a card without its facts, never a Run refused for them
        return [{} for _ in range(count)]
    base = {"agent": P.AGENT_NAMES.get(a.agent, a.agent), "model": a.model or "", "tier": a.tier, "memory": a.memory}
    facts = [dict(base, kind=live.KIND_WORDS["single"], target=run["roster"]["T"]["full"],
                  target_type=run["roster"]["type"]) if run["kind"] == "single" else
             dict(base, kind=live.KIND_WORDS["cross"], compare=run["selection"]["compare"],
                  types=[P.display(t) for t in run["selection"]["types"]]) for run in runs]
    return [live.shown_facts(f) for f in facts] if len(facts) == count else [live.shown_facts(base)] * count


def versus_card_facts(argv):
    """A versus run's facts for its run card, as its --watch page has them: the kind, the names as rendered and joined
    (versus_run's rendered_names, which the title is made of), the agent, the model as the command names it, the tier
    and memory; [facts], or [{}] when the command cannot be read."""
    VR = versus_command()
    try:
        with PARSE_LOCK, contextlib.redirect_stderr(io.StringIO()):
            a = VR.parse(argv)
            names = VR.versus_names(a.versus)
    except (SystemExit, Exception):
        return [{}]
    shown = VR.rendered_names({t: {"full": n} for t, n in names.items()})
    return [live.shown_facts({"kind": live.KIND_WORDS["versus"], "agent": P.AGENT_NAMES.get(a.agent, a.agent),
                              "model": a.model or "", "tier": a.tier, "memory": a.memory, "names": shown,
                              "title": VR.card_title(shown)})]


def versus_prepared(argv, preflight=None):
    """A versus argv's run as versus_run prepares it, in this process and with no call to any CLI: its results folder
    (run_folder) and its plan's versus fingerprint (every call whole), from the command's own parse, names, bank,
    name checks and plan. With preflight, use its actual selected default sibling (versus_selected). A refusal is
    SystemExit with the command's own message (stderr is the page's, never printed); a folder that cannot be resolved
    is the refusal of a run that cannot start (preparing)."""
    VR, err = versus_command(), io.StringIO()
    with PARSE_LOCK, contextlib.redirect_stderr(err):
        try:
            a = VR.parse(argv)
            names = VR.versus_names(a.versus)
            bank, ordo_bank = VR.load_bank(a), VR.ordo.load_ordo_bank()
            VR.check_names(bank, names, a.strangers_on)
            plan_ = VR.VS.plan(bank, names, a.tier, VR.arms_of(a), ordo_bank)
            folder = prepared_folder(folder_named(a, VR.run_name(a, names)), VR.run_folder, a, names)
            if preflight is not None:
                folder = versus_selected(a, names, preflight)
            return folder, plan_.identity["versus_fingerprint"]
        except SystemExit as e:
            raise SystemExit(exit_message(e.code, err.getvalue())) from None
        except VR.VS.VersusError as e:
            raise SystemExit(P.display(str(e), cap=None)) from None


def versus_selected(a, names, preflight):
    """The successful dry run's actual folder, restricted to the command's legal default candidates. Paths are
    resolved by the same helper as the command; a reported sibling outside that list never becomes Runner's root."""
    VR = versus_command()
    reported = []
    for line in preflight.splitlines():
        for template in (VR.FOLDER_FRESH, VR.FOLDER_RESUME):
            matched = template_pattern(template).match(line)
            if matched:
                reported.append(matched.group("folder"))
    if reported:
        named = folder_named(a, VR.run_name(a, names))
        for candidate in P.default_folder_names(named):
            folder = prepared_folder(candidate, P.results_folder, candidate)
            if P.display(folder) == reported[-1]:
                return folder
    raise SystemExit(FOLDER_UNREAD)


# The results page a versus run writes (versus_page.HTML_FILE), served by the form under its token for a folder of its
# own run, with the page's own policy (versus_html.CSP, which forbids every fetch) as a header and never cached (live's
# security headers). The name and the policy are copied here so the form needs no import of the page to serve it; a
# test checks they are the page's own.
RESULTS_PAGE = "results.html"
RESULTS_PAGE_PATH = "/" + RESULTS_PAGE
RESULTS_PAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:"
RESULTS_PAGE_BYTES = 16 * 1024 * 1024              # a results page larger than this is not one the run wrote
SINGLE_PAGE = (RESULTS_PAGE,)                       # where a run's page is in its folder: versus and a single target
ORDO_PAGE = ("ordo", RESULTS_PAGE)                  # a cross-type run's, beside its answer (ordo.write_results)


# The page is read from a pinned folder: the run's folder is opened as a directory without following a link, checked by
# fstat to carry the identity the form recorded for its run (its device and inode, folder_identity, taken when the run
# was launched or listed it), and results.html is opened relative to that descriptor, never by a second path lookup, so
# a folder moved or linked into the run's place is refused while the run's folder exists. The pin names a folder, not
# its history: a folder made after the run's folder was deleted can carry its identity wherever it was made, and is then
# served as the run's. A file is served only if, when asked after the read, the system reports that results.html in the
# pinned folder still names that file and that the file has one name (named_alone: a lookup relative to the pinned
# descriptor that follows no link and only decides). A second name removed at the moment of the check (likeliest in a
# tight loop) can, rarely, get past both checks (a few requests in three thousand in our probe), which gains a writer
# nothing it could not do by copying the file in where making a hard link needs the access a copy needs, as on Linux by
# default. A platform without these primitives serves no page.
PINNED_OPEN = (hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW") and os.open in os.supports_dir_fd
               and os.stat in os.supports_dir_fd and os.stat in os.supports_follow_symlinks)


def folder_identity(folder):
    """(device, inode) of folder when it is a real directory, read without following a link (lstat); None for a link,
    a missing path or anything else. What the form records of a run's folder, and the one the page is read from."""
    try:
        info = os.lstat(folder)
    except (OSError, ValueError):
        return None
    return (info.st_dev, info.st_ino) if stat.S_ISDIR(info.st_mode) else None


def read_results_page(folder, identity, page=SINGLE_PAGE):
    """The bytes of the results page at page (a fixed subpath, SINGLE_PAGE or ORDO_PAGE, never a request's) in
    folder, or None. Each folder of page is opened relative to the last as a directory without following a link
    (O_DIRECTORY | O_NOFOLLOW), so ordo/ that is a link is refused as the run's folder is; then, relative to the
    last, results.html as below. folder must be absolute and identity its recorded (device, inode):
    the folder is opened with O_DIRECTORY | O_NOFOLLOW (a link, or anything not a directory, is refused) and must
    carry that recorded identity by fstat, so a folder moved or linked into the run's place is refused while the
    run's folder exists (the pin names a folder, not its history: a folder made after the run's folder was deleted
    can carry its identity wherever it was made, and is then read as the run's); results.html is opened relative to
    it (dir_fd) with O_NOFOLLOW and O_NONBLOCK (a link is refused, a pipe is never waited on), must be a regular file
    of at most RESULTS_PAGE_BYTES by fstat (read_regular), and is read from its descriptor; its bytes are returned
    only if, when asked after the read, the system reports that results.html in the pinned folder still names that
    file and that the file has one name (named_alone, which a second name removed at the moment of the check can,
    rarely, get past). Both descriptors are closed. No recorded identity, or no pinning primitive: None."""
    if identity is None or not PINNED_OPEN:
        return None
    try:
        if not Path(folder).is_absolute():
            return None
        dfd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except (OSError, ValueError):
        return None
    try:
        held = os.fstat(dfd)
        if not stat.S_ISDIR(held.st_mode) or (held.st_dev, held.st_ino) != tuple(identity):
            return None
        opened, where = [], dfd
        try:
            for name in page[:-1]:                   # ordo/: a directory, never a link
                where = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                                dir_fd=where)
                opened.append(where)
            fd = os.open(page[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
                         dir_fd=where)
        except (OSError, ValueError):
            for d in opened:
                os.close(d)
            return None
        try:
            body = read_regular(fd)
            return body if body is not None and named_alone(where, fd, page[-1]) else None
        finally:
            os.close(fd)
            for d in opened:
                os.close(d)
    finally:
        os.close(dfd)


def named_alone(dfd, fd, name=RESULTS_PAGE):
    """Whether, when asked after the read, the system reports that results.html in the pinned folder (dfd) still
    names the file read through fd and that the file has one name: the name, looked up again relative to the folder's
    descriptor without following a link, has the same device and inode, and both reports give one link.
    read_regular's fstat alone cannot say so: a second name taken away between the open and that fstat leaves one
    link by then. Both checks read what the system reports, and while a name is being removed a lookup can, for a
    moment, still find it after the file's link count has dropped. A second name removed at the moment of the check
    (likeliest in a tight loop) can, rarely, get past both checks (a few requests in three thousand in our probe),
    which gains a writer nothing it could not do by copying the file in where making a hard link needs the access a
    copy needs, as on Linux by default."""
    try:
        named, held = os.stat(name, dir_fd=dfd, follow_symlinks=False), os.fstat(fd)
    except (OSError, ValueError):
        return False
    return (named.st_dev, named.st_ino) == (held.st_dev, held.st_ino) and named.st_nlink == held.st_nlink == 1


def read_regular(fd):
    """The bytes behind an open descriptor when it is a regular file with exactly one name (st_nlink 1) of at most
    RESULTS_PAGE_BYTES (fstat, before a byte of it is read), else None. probatio makes results.html a file of its own
    (written new, written over, or a new one renamed over it by a rescore), never a link, so its page has one name
    unless something else gives it a second; a hard link gives a file elsewhere the name results.html and has two,
    and a page renamed over after it was opened has none."""
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > RESULTS_PAGE_BYTES:
            return None
        chunks, size = [], 0
        while size <= RESULTS_PAGE_BYTES:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError:
        return None
    return b"".join(chunks) if size <= RESULTS_PAGE_BYTES else None


def page_parts(runner, folder):
    """Where a run's page is in one of its folders: ORDO_PAGE when the run's own output named folder/ordo/results.html
    as its page (a cross-type run's Page: line, read by its reading and kept only inside its own folders), else
    SINGLE_PAGE. Chosen from the run, never from the request: a request's folder only picks among the run's."""
    for kind, path in list(getattr(runner, "results", ())):
        if kind == "Page" and Path(path) == Path(folder).joinpath(*ORDO_PAGE):
            return ORDO_PAGE
    return SINGLE_PAGE


def resolved(path):
    """Path(path).resolve(), or None when it cannot be resolved at this moment: an OSError (a folder swapped for a link
    between the lstat and the readlink behind resolve: EINVAL when it is a folder again, ENOENT when it is gone), before
    Python 3.13 a link loop (pathlib's RuntimeError), a chain of links too long for resolve's recursion (RecursionError,
    a RuntimeError) or a name no path can hold (ValueError). Python 3.13's non-strict resolve raises nothing on a loop
    and follows a chain of any length: a look finds those (looping, cannot_resolve). A folder being changed under the
    page is one it cannot name: a route that matches a request against a run's folders refuses it (placed), a reply that
    names it shows it as given (shown), and neither ever drops the connection."""
    try:
        return Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def placed(path):
    """path as the page lists it (shown), or None when it cannot be resolved at this moment (resolved): what a
    route matches a request's folder against, so a folder it cannot resolve matches nothing and is refused."""
    where = resolved(path)
    if where is None:
        return None
    try:
        return P.display(where.relative_to(P.HERE))
    except ValueError:
        return P.display(path)


def shown(path):
    """A path as the page shows it: relative to probatio's folder when it is inside it, displayed (probatio's
    display); displayed as given when it is outside it or cannot be resolved at this moment (placed)."""
    listed = placed(path)
    return P.display(path) if listed is None else listed


# ---- default folders chosen with the run's own environment ----------------------------------------------------------
# Resume identity includes memory hashes and the CLI version. A child builds that identity in the run's environment,
# then uses probatio's shared selector without writing a run record. The selected paths also become Runner's roots,
# so a sibling run's result links belong to this page.
RESUME_CHILD = "import sys; sys.path.insert(0, {!r}); import ui; ui.resume_child()"


class ResumeUnread(Exception):
    """Check could not select the run's default folder; detail preserves a CLI refusal for the whole preflight."""

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.detail = detail


def resume_refusals(argv, plans, env):
    """Update plans to today's selected default folders, returning their redirection notices. No child is needed
    while every base is absent. Existing folders require the run's own memory and CLI metadata in RESUME_CHILD;
    fingerprints must still match the page's prepared plans before any selected path is accepted."""
    # Look at run.json too: an unreadable directory entry must fail here, never look like an absent run.
    occupied = False
    for folder, _ in plans:
        P.there(Path(folder) / "run.json")
        occupied = P.there(folder) or occupied
    if not occupied:
        occupied = any(P.link_there(folder) for folder, _ in prepared(argv, named=True))
    if not occupied:
        return []
    request = {"argv": absolute(argv), "here": str(P.HERE), "folders": [str(folder) for folder, _ in plans]}
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
    if isinstance(answer, dict) and isinstance(answer.get("error"), str):
        said = answer["error"]
        raise ResumeUnread(page_words(said) or P.capped(said), detail=said)
    if not isinstance(answer, dict) or answer.get("fingerprints") != [fp for _, fp in plans] or \
            not isinstance(answer.get("folders"), list) or len(answer["folders"]) != len(plans) or \
            not all(isinstance(f, str) for f in answer["folders"]):
        why = (proc.stderr.strip().splitlines() or ["no answer"])[-1][:200]
        raise ResumeUnread(f"Check could not select this command's results folders ({why}); check again")
    plans[:] = [(Path(folder), fp) for folder, (_, fp) in zip(answer["folders"], plans)]
    return answer.get("notices", [])


def folder_notice_words(preflight):
    """Default redirection sentences visible beside Check's totals, with paths relative to this page's folder.
    The whole terminal preflight stays verbatim in its fold; only its existing redirection lines become notices."""
    pattern = re.compile(r"^\s*(?P<folder>.+?) (?P<why>holds an earlier run with other settings \(.+? changed\)|"
                         r"cannot continue this command \(.+\)); using (?P<chosen>.+)\.$")
    prefix = P.display(P.HERE) + os.sep
    def path_words(path):
        return path[len(prefix):] if path.startswith(prefix) else path
    notices = []
    for line in preflight.splitlines():
        match = pattern.match(line)
        if match:
            notices.append(f"{path_words(match.group('folder'))} {match.group('why')}; "
                           f"using {path_words(match.group('chosen'))}.")
    return notices


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


def resume_child():
    """Build today's metadata and choose folders read-only in dependency order. A dependent's resume identity
    names its recognition-asking run's selected folder, exactly as the terminal's shared chooser records it.
    Plain command refusals cross this process boundary as data, so the form can give its own advice without
    confusing an exhausted set of defaults with an unreadable child response."""
    request = json.load(sys.stdin)
    P.HERE = Path(request["here"])
    try:
        metas = would_be_metas(request["argv"])
        a = P.parse_args_v4(request["argv"])
        runs = P.prepare_runs(a, "--target-type" in request["argv"])[4]
        metas, notices = P.choose_run_folders(a, runs, metas, announce=False)
    except SystemExit as error:
        if not isinstance(error.code, str):
            raise
        json.dump({"error": error.code}, sys.stdout)
        return
    folders = [str(run["out"]) for run in runs]
    json.dump({"fingerprints": [m.get("plan_fingerprint") for m in metas],
               "folders": folders, "notices": notices}, sys.stdout)


# ---- the memory this run tests: probatio's own memory_report, read in a child process with the run's environment -----
# The block the form shows before Check is what the preflight prints (memory_report, config_choice, memory_people,
# config_candidates), read where the run reads it: in a child process with the run's environment, from probatio's
# folder, as resume_child reads the memory files. The child parses the form's command with probatio's own parser and
# writes JSON; it writes no file and starts no CLI. Its titles are local, pre-run information for this page only.
MEMORY_CHILD = "import sys; sys.path.insert(0, {!r}); import ui; ui.memory_child()"
MEMORY_UNREAD = "the page could not look at the memory files ({}); Check's dry run lists them"


def memory_view(argv, env):
    """The memory block's answer for argv (memory_answer, in a child process with env, the run's environment, in
    probatio's folder), or {"error": why} when the child does not answer within MEMORY_SECONDS or cannot start."""
    try:
        proc = subprocess.run([sys.executable, "-I", "-B", "-c", MEMORY_CHILD.format(str(Path(__file__).parent))],
                              input=json.dumps({"argv": absolute(argv)}), cwd=P.HERE, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=MEMORY_SECONDS)
    except subprocess.TimeoutExpired:
        return {"error": MEMORY_UNREAD.format(f"it took longer than {MEMORY_SECONDS} s")}
    except (ValueError, OSError) as e:
        return {"error": not_started("look at the memory files", e)}
    try:
        answer = json.loads(proc.stdout) if proc.returncode == 0 else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        return {"error": MEMORY_UNREAD.format(P.display((proc.stderr.strip().splitlines() or ["no answer"])[-1], 200))}
    return answer


def memory_people_of(a, argv):
    """The individual targets of the command (probatio's memory_people over prepare_runs, as the preflight reads
    them), for the name notes; [] when there is no target or probatio refuses the command (the preview and Check say
    why). The entities file is left out: the page writes targets as --target, and the file may not be written yet."""
    if not a.target:
        return []
    bare = [x for prev, x in zip([None, *argv], argv) if x != "--entities" and prev != "--entities"]
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            got = P.prepare_runs(P.parse_args_v4(bare), "--target-type" in bare)
    except SystemExit:
        return []
    return P.memory_people(got[4]) if got else []


VERSUS_AUTO_MEMORY = "as-configured"        # versus_run.main's auto memory: a versus run measures the agent as it is


def versus_people(a):
    """The named person of a versus command, for the memory block's name note, as its preflight reads it
    (versus_run.memory_people over the resolved names); [] when the command's names are refused (the preview and
    Check say why)."""
    VR = versus_command()
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            return VR.memory_people(VR.VS.resolve_names(VR.versus_names(a.versus)))
    except (SystemExit, VR.VS.VersusError):
        return []


def memory_answer(argv):
    """What the block shows for argv, as probatio's preflight would print it: {"report": memory_report's facts (the
    folder, where it came from, each file's path, size and title, the total and the name notes; no hash), "empty":
    the list's one line when it lists no file, "candidates": other config folders the agent offers,
    "missing": the folder typed when it is not one}; {"report": None} for a command probatio's parser refuses
    or with no agent (the preview says why); {"error": probatio's refusal} for a memory file the run cannot read. Every
    string from the filesystem is displayed (probatio's display); a candidate whose name display changes has no
    "path", so a click never puts a changed path into the field ("escaped" says why). A versus command is read by
    versus_run's own parser, with its auto memory as configured and its person's name note (versus_people), as its
    preflight reads them."""
    versus = is_versus(argv)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            a = versus_command().parse(argv) if versus else P.parse_args_v4(argv)
    except SystemExit:
        return {"report": None}
    if a.agent is None:
        return {"report": None}
    error = io.StringIO()
    try:
        with contextlib.redirect_stderr(error):
            folder, source = P.agent_configuration(a)
            if source == "option" and not P.config_is_folder(folder):
                return {"report": None, "missing": P.display(P.tilde(folder))}
            report = P.memory_report(a.agent, str(Path(a.workdir).resolve()), a.memory,
                                     VERSUS_AUTO_MEMORY if versus else a.auto_memory, folder, source,
                                     versus_people(a) if versus else memory_people_of(a, argv))
            candidates = P.agent_callback(a.agent, "memory_candidates", in_use=folder)
    except SystemExit as refused:               # a memory file the run cannot read: probatio's own line, calm
        message = error.getvalue().strip() if isinstance(refused.code, int) else P.refusal_text(refused.code)
        return {"error": P.capped(message or P.refusal_text(refused.code))}
    files = [{"path": P.display(f["path"]), "shown": f["shown"], "size": P.size_words(f["bytes"]),
              "title": title_shown(f["title"])} for f in report["files"]]
    empty = P.memory_empty(report)
    return {"report": dict({k: report[k] for k in ("agent", "memory", "folder", "source", "notes")}, files=files,
                           total=P.MEMORY_TOTAL_LINE.strip().format(files=P.file_count(len(files)),
                                                                    size=P.size_words(report["total"])) if files
                           else None),
            "empty": empty,
            "candidates": [{"path": c["path"] if P.shows_as_is(c["path"]) else None,
                            "folder": P.display(P.tilde(c["path"])),
                            "claude_md": dict(c["claude_md"], size=P.size_words(c["claude_md"]["bytes"]),
                                              title=title_shown(c["claude_md"]["title"]))
                            if c["claude_md"] else None} for c in candidates],
            "escaped": P.CANDIDATE_ESCAPED}


def title_shown(title):
    """A memory file's title as the page shows it: the title, or for a file whose first bytes are not text (None)
    probatio's TITLE_UNREADABLE in parentheses."""
    return f"({P.TITLE_UNREADABLE})" if title is None else title


def memory_child():
    """memory_view's child process: reads {"argv"} on stdin and writes memory_answer's JSON on stdout."""
    request = json.load(sys.stdin)
    json.dump(memory_answer(request["argv"]), sys.stdout)


# ---- one run at a time ---------------------------------------------------------------------------------------------------
def unresolvable():
    """The error of a run's results folder that cannot be resolved (cannot_resolve: a link loop, a chain of links too
    long, a folder changed under the lookup): OSError ELOOP, its one home. Runner raises it before any process starts
    and the prepare paths refuse with its words (preparing, prepared_folder), so the form answers either as a run that
    cannot start (not_started)."""
    return OSError(errno.ELOOP, os.strerror(errno.ELOOP))


looping = P.looping     # the look at a run's folder has one home, probatio's, so the form and the commands refuse
                        # the same folders (probatio.results_folder; versus_run's run_folder)


def resolved_folder(path):
    """A run's folder resolved (resolved), or None when it cannot be resolved, the one test of it: resolve fails at
    this moment (resolved gives None) or a look at it meets ELOOP (looping). The look does not depend on resolve
    raising: Python 3.13's non-strict resolve raises nothing on a loop and follows a chain of any length. Runner takes
    its roots from it, each folder resolved once, and refuses a None; the form's prepare paths refuse such a folder
    too (cannot_resolve, unresolvable). An ordinary folder, one not there yet and one outside probatio's folder
    pass."""
    where = resolved(path)
    return None if where is None or looping(path) else where


def cannot_resolve(path):
    """Whether a run's folder cannot be resolved (resolved_folder gives None)."""
    return resolved_folder(path) is None


def not_started(what, error):
    """Why a dry run or a run did not start, bounded: subprocess refused its command line (ValueError) or the system
    could not start it (OSError, by its errno's words); never the command."""
    why = os.strerror(error.errno) if isinstance(error, OSError) and error.errno else \
        "its command line holds what no program can take"
    return f"the {what} could not start ({why})"


class Runner:
    """One probatio run started by the page: program + argv in a subprocess in its own session (so a Ctrl-C at the
    page's terminal reaches the page, which passes one SIGINT on), its output read line by line: the --watch link
    (WATCH_LINK, only when argv has --watch, the first), the newest TAIL lines, each run's result lines, the held page's
    line, and its exit code. A result line counts only when its path lies inside one of the run's own folders (own), and
    the run is finished only on exit 0 with a scorecard: a CLI's text in the output names no result. A process that
    cannot start raises subprocess's ValueError or OSError; so does a run whose folder cannot be resolved
    (cannot_resolve: a link loop, a chain of links too long, a folder changed under the lookup), with ELOOP and before
    any process starts, so the form answers it as it answers a process that cannot start. reading says which lines are
    its results and which one marks it scored (V4_READING, or VERSUS_READING for a versus run, whose one result line is
    its folder)."""

    def __init__(self, program, argv, folders, env, reading=None):
        self.reading = reading or V4_READING
        self.argv, self.folders = list(argv), [str(f) for f in folders]
        self.roots = [resolved_folder(f) for f in self.folders]  # resolved once, as own() compares result lines
        if None in self.roots:                                       # cannot_resolve's rule, on those very roots
            raise unresolvable()
        self.identities = {f: folder_identity(f) for f in self.folders}    # a folder already there, as launched
        self.watching = WATCH in self.argv
        self.lines, self.results, self.watch, self.held = deque(maxlen=TAIL), [], None, False
        self.said = None                                # the last line the run printed before any held page's
        self.stopping, self.code, self.lock = False, None, threading.Lock()
        self.started = time.strftime("%H:%M")
        self.launched, self.scored_at = time.monotonic(), {}  # when it started; when each folder's run was scored
        self.facts = [{} for _ in self.folders]          # each run's facts for its results card (Session sets them)
        self.checks = {}                                 # folder -> live.PageCheck: its page as the card offers it
        self.clock = time.monotonic                      # the checks' clock (a test may move it)
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
                result = self.reading.line.match(line)
                if result and self.own(result.group(2)):
                    self.results.append([result.group(1), result.group(2)])
                    self.record_folders()
                    if result.group(1) == self.reading.scored:     # this folder's run is scored: its time is kept
                        self.scored_at.setdefault(self.folder_of(result.group(2)), time.monotonic())
                if line.lstrip().startswith(HELD_LINE):
                    self.held = True
                elif line.strip() and not self.held:
                    self.said = line
        self.proc.stdout.close()
        code = self.proc.wait()
        with self.lock:
            self.code = code

    def record_folders(self):
        """Record the identity (folder_identity) of each of the run's folders not yet recorded, as the run lists its
        results: the folder the results page is later read from must carry this identity (read_results_page). A
        folder recorded at launch keeps that identity. Called under the lock."""
        for f in self.folders:
            if self.identities.get(f) is None:
                self.identities[f] = folder_identity(f)

    def identity(self, folder):
        """The recorded (device, inode) of one of the run's folders, or None."""
        with self.lock:
            return self.identities.get(folder)

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
        where = resolved(path)
        return where is not None and any(where == root or root in where.parents for root in self.roots)

    def status(self):
        """running, stopping, scored (scored, its live page held until Stop), finished (exit 0 with a scorecard),
        refused (a versus run's live page held after the run ended in a refusal, and after its Stop), stopped or
        failed."""
        scored = any(kind == self.reading.scored for kind, _ in self.results)
        refused = self.reading.refusal and self.held and not scored
        if self.code is None:
            return "stopping" if self.stopping else "scored" if self.held and scored else \
                "refused" if refused else "running"
        if self.code == 0 and scored:
            return "finished"
        if refused:
            return "refused"
        return "stopped" if self.stopping else "failed"

    def folder_of(self, path):
        """The run's own folder a result line's path lies in (own's rule, folder by folder), or None."""
        where = resolved(path)
        return next((f for f, root in zip(self.folders, self.roots)
                     if where is not None and (where == root or root in where.parents)), None)

    def took(self, i):
        """Seconds the run of folder i took, as the form saw it: from the previous run's scoring (or the launch) to
        its own; None until it is scored. A command's runs go one after another, in its folders' order."""
        f = self.folders[i]
        if f not in self.scored_at:
            return None
        before = [self.scored_at[g] for g in self.folders[:i] if g in self.scored_at]
        return self.scored_at[f] - (max(before) if before else self.launched)

    def page_named(self, folder):
        """Whether the run's own output named a results page in folder (a Page: line at page_parts' subpath)."""
        return any(kind == "Page" and Path(path) == Path(folder).joinpath(*page_parts(self, folder))
                   for kind, path in self.results)

    def page_now(self, folder):
        """Whether folder's results page can be served now (live.page_state, by the pinned reader at page_parts'
        subpath, from the identity recorded for the folder): "ready", or why not ("missing", also when the run's
        output named none). Through the folder's live.PageCheck, the --watch page's own rule: asked at most every
        live.CARD_RECHECK_SECONDS by self.clock, failing closed on any exception, its text files listed at the first
        ask, once the command is done. Under the lock."""
        check = self.checks.setdefault(folder, live.PageCheck())
        parts = page_parts(self, folder)
        return check.ask(self.clock(), lambda: live.page_state(folder, self.identities.get(folder), parts)
                         if self.page_named(folder) else "missing", lambda: live.text_files(folder, parts))

    def files_of(self, folder):
        """The text files the card lists for folder: as its PageCheck listed them at its first ask."""
        check = self.checks.get(folder)
        return (check.files or []) if check is not None else []

    def card(self, status):
        """The run card at the top of the form's run box (live.run_card, the same card as the --watch page's), in one
        frame through the run's states (D1): while the run goes (running, stopping), its first run's title and facts
        with the live link as the secondary button once there is one; once the command is done (scored, its live page
        held, or finished), the results card for its first run (live.card_for: its button while its page can be
        served, else the sentence of why), the quiet link to the live page beside the folder while that page is
        held (scored), and the command's other runs listed beneath, each with its own link; None for a run that
        stopped, failed or was refused, whose own message stands. Under the lock."""
        if not self.folders:
            return None
        first = self.folders[0]
        if status in ("running", "stopping"):
            return live.run_card("running", self.facts[0], None, shown(first), live=self.watch is not None)
        if status not in ("scored", "finished"):
            return None
        states = [self.page_now(f) for f in self.folders]
        link = lambda i: {"folder": shown(self.folders[i])}                         # noqa: E731
        others = [live.other_run(self.facts[i], self.took(i), live.other_words("finished", states[i]),
                                 link(i) if states[i] == "ready" else None) for i in range(1, len(self.folders))]
        return live.card_for(True, self.facts[0], self.took(0), states[0], link(0), shown(first),
                             self.files_of(first), others,
                             watch_page=status == "scored" and self.watch is not None)

    def refusal(self, status):
        """For a versus run that failed or was refused, the one line it ended with (the refusal probatio printed),
        else None. A v4 run's state carries no such field."""
        return self.said if status in ("failed", "refused") else None

    def state(self):
        with self.lock:
            status = self.status()
            extra = {"refusal": self.refusal(status)} if self.reading.refusal else {}
            return {"status": status, "command": command_text(self.argv), "watch": self.watch,
                    "card": self.card(status),
                    "folders": [shown(f) for f in self.folders],
                    "results": [[kind, shown(path)] for kind, path in self.results],
                    "lines": list(self.lines), "code": self.code, "started": self.started, **extra}


# What a run's output names as its results, by family: the line and the kind that marks it scored. A v4 run prints
# "Scorecard: ...", "Report: ..." and "Page: FOLDER/results.html" for a single target, "Page: FOLDER/ordo/results.html"
# for a comparison of types (RESULT_LINE); a versus run prints "Results: FOLDER" and "Page: FOLDER/results.html"
# (VERSUS_RESULT_LINE). Either reading's Page lines are what the page offers "Open the results page" for, and where
# the route reads each folder's page from (page_parts).
# refusal: whether the run's state names the line a failed or refused run ended with (a versus run's: a card its page
# refuses ends the run in one line, which the form shows as it is).
Reading = namedtuple("Reading", "line scored refusal")
V4_READING = Reading(RESULT_LINE, "Scorecard", False)
VERSUS_READING = Reading(VERSUS_RESULT_LINE, "Results", True)


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
        return {"/command": self.command, "/memory": self.memory, "/check": self.check, "/run": self.run,
                "/stop": self.stop}

    def files(self):
        """The one file the page serves besides itself: a versus run's results page (results_page)."""
        return {RESULTS_PAGE_PATH: self.results_page}

    def results_page(self, query):
        """GET /results.html?t=TOKEN&folder=F: the results page of the run folder F, where F is, string for string, a
        folder this page's own run lists (its "folders", as the page shows them), read at F/ordo/results.html when the
        run's own Page: line names F/ordo/results.html, else at F/results.html, as before, whatever else the run
        printed (page_parts, a constant never taken from the request); (bytes, content type,
        the results page's own CSP), or None for 404. The path read is the run's own folder, never one from the
        request: a request's folder only picks among the run's, so a traversal, an absolute path or any folder the
        page does not list is refused before the filesystem is touched, and read_results_page reads only from that
        folder pinned by the identity the run recorded (Runner.identity), refusing a folder or a results.html that is
        a link, a folder moved or linked into the run's place while the run's folder exists, and anything but a
        regular file inside it; a results.html is served only if, when asked after the read, the system reports that
        it still names that file and that the file has one name (named_alone, which a second name removed at the
        moment of the check can, rarely, get past). The pin names a folder, not its history, so a folder made after
        the run's folder was deleted can carry its identity wherever it was made, and is then served as the run's. A
        run's folder that cannot be resolved at this moment (placed: one being swapped for a link, and before Python
        3.13 a link loop) matches nothing: 404; on 3.13 a loop is matched and refused where the folder is opened
        without following a link, the same 404."""
        given = query.get("folder") or []
        with self.lock:
            runner = self.runner
        if runner is None or len(given) != 1:
            return None
        folder = next((f for f in runner.folders if placed(f) == given[0]), None)
        body = read_results_page(folder, runner.identity(folder), page_parts(runner, folder)) \
            if folder is not None else None
        return None if body is None else (body, "text/html; charset=utf-8", RESULTS_PAGE_CSP)

    def child_env(self):
        """The environment of a dry run or a run: this process's (or the tests'), with Python's output unbuffered so
        the page sees each line as it is printed."""
        return dict(os.environ if self.env is None else self.env, PYTHONUNBUFFERED="1")

    def command(self, request):
        return preview(request.get("form"), redactor=self.redactor, env=self.child_env())

    def memory(self, request):
        """The "Memory this run tests" block for the form (memory_view, in a child process with the run's
        environment): {"report": None} while the form has no command or no agent. It never waits on a Check or a
        Run and approves nothing."""
        form = request.get("form") if isinstance(request.get("form"), dict) else {}
        try:
            argv, _ = build_command(form)
        except FormRefused:
            return {"report": None}
        return memory_view(argv, self.child_env()) if text_of(form.get("agent")) else {"report": None}

    def check(self, request):
        """The real --dry-run of the form's command, in a subprocess in probatio's folder: its preflight and the totals,
        or probatio's message verbatim. Its roster snapshot is written first (write_snapshot) and must hold the same
        bytes after the dry run; then the plan is prepared in this process from the same argv and snapshot, and the
        approval binds the argv, those bytes and each run's plan fingerprint. Only the last Check can approve, and
        only if it passed. A form that still needs a field (required) is refused first, with nothing written and no
        dry run. A form build_command refuses, a command the reply's redactor would change (unshown) and a
        dry run that cannot start are refusals too, before anything is written or after nothing ran. A refusal whose
        advice the page cannot follow is in the page's words, probatio's own kept in the whole preflight (refusal).
        Default folders already held by incompatible runs are passed over by the command's own selector; the
        preflight names the sibling and Check approves the same plan there."""
        with self.gate:
            self.checked = None
            missing = form_needs(request.get("form"), env=self.child_env())
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
            if is_versus(argv):             # versus_run's own dry run and its folder line (check_versus)
                return self.check_versus(reply, argv)
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
                notices = resume_refusals(argv, plans, self.child_env())
            except (SnapshotRefused, ResumeUnread) as e:
                return dict(reply, ok=False, plan=told(proc.stdout, getattr(e, "detail", None)),
                            summary=None, error=str(e))
            except SystemExit as e:
                error, plan = refusal(exit_message(e.code), proc.stdout)
                return dict(reply, ok=False, plan=plan, summary=None, error=error)
            plan = told(proc.stdout, *(notice for notice in notices if notice not in proc.stdout))
            self.checked = Approval(tuple(argv), snapshot, tuple(fingerprint for _, fingerprint in plans), plan)
        return dict(reply, ok=True, plan=plan, summary=plan_summary(plan), notices=folder_notice_words(plan), error=None)

    def dry_run(self, argv):
        """argv's real --dry-run in a subprocess in probatio's folder, with the run's environment: (proc, None), or
        (None, the reply's error) when it took longer than CHECK_SECONDS or could not start."""
        try:
            return subprocess.run([*self.program, *argv, "--dry-run"], cwd=P.HERE, env=self.child_env(),
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=CHECK_SECONDS), None
        except subprocess.TimeoutExpired:
            return None, (f"the dry run took longer than {CHECK_SECONDS} s; run the command with --dry-run in a "
                          "terminal to see why")
        except (ValueError, OSError) as e:
            return None, not_started("dry run", e)

    def check_versus(self, reply, argv):
        """Check for a versus command (inside check's gate, after its form checks): versus_run's real --dry-run
        (dry_run), its refusal in the page's words where its advice names --out (versus_page_words) with its own line
        kept in the whole preflight; then its run prepared in this process (versus_prepared: the folder and the plan's
        versus fingerprint) and the dry run's own line on that folder (versus_folder): a folder the command would
        refuse is named in the page's words and approves nothing. On a pass the approval binds the argv and the
        fingerprint, and the totals come from the preflight (versus_summary)."""
        proc, error = self.dry_run(argv)
        if proc is None:
            return dict(reply, ok=False, plan="", summary=None, error=error)
        if proc.returncode != 0:
            error, plan = refusal_in_words(exit_message(None, proc.stderr), proc.stdout, versus_page_words)
            return dict(reply, ok=False, plan=plan, summary=None, error=error)
        try:
            folder, fingerprint = versus_prepared(argv, proc.stdout)
        except SystemExit as e:
            error, plan = refusal_in_words(exit_message(e.code), proc.stdout, versus_page_words)
            return dict(reply, ok=False, plan=plan, summary=None, error=error)
        state, why = versus_folder(proc.stdout, folder)
        if state in ("refused", "unread"):
            return dict(reply, ok=False, plan=proc.stdout, summary=None, error=versus_page_words(why) or why)
        self.checked = Approval(tuple(argv), None, (fingerprint,), proc.stdout)
        return dict(reply, ok=True, plan=proc.stdout, summary=versus_summary(proc.stdout),
                    notices=folder_notice_words(proc.stdout), error=None)

    def versus_refusal(self, argv, folder):
        """Select the actual versus folder again when a run already occupies the default: (folder, None), or
        (None, (page refusal, terminal line)). A new incompatible run since Check can move this run to a sibling."""
        VR = versus_command()
        a = VR.parse(argv)
        named = folder_named(a, VR.run_name(a, VR.versus_names(a.versus)))
        if not P.there(folder) and not P.link_there(named):
            return folder, None
        proc, error = self.dry_run(argv)
        if proc is None or proc.returncode != 0:
            said = error or exit_message(None, proc.stderr)
            return None, (versus_page_words(said) or said, said)
        try:
            selected = versus_prepared(argv, proc.stdout)[0]
        except SystemExit as e:
            said = exit_message(e.code)
            return None, (versus_page_words(said) or said, said)
        return selected, None

    def run_versus(self, argv):
        """Run for a versus command (inside run's gate, once the approval holds this argv): its run prepared again
        (versus_prepared), its fingerprint the approved one, the default sibling selected again just before the start
        (versus_refusal), then Runner with that actual root, reading a versus run's results line. Filesystem failures
        still refuse with the command's own line after the Check's preflight."""
        approved = self.checked
        try:
            folder, fingerprint = versus_prepared(argv)
        except SystemExit as e:
            return {"error": exit_message(e.code)}
        if (fingerprint,) != approved.fingerprints:
            return {"error": "the plan is no longer the one Check approved; check again"}
        folder, blocked = self.versus_refusal(argv, folder)
        if blocked:
            return {"error": blocked[0], "plan": told(approved.preflight, blocked[1])}
        with self.lock:
            try:
                self.runner = Runner(self.program, list(approved.argv), [folder], self.child_env(), VERSUS_READING)
            except (ValueError, OSError) as e:
                return {"ok": False, "error": not_started("run", e)}
            runner = self.runner
        runner.facts = versus_card_facts(list(approved.argv))           # outside the form's lock (F7): see run
        return {"run": runner.state()}

    def run(self, request):
        """Start the form's command exactly as shown and checked (--watch included unless chosen off), once it is
        confirmed, only when it is the command the last Check passed, while no run of the page's is going, and only
        after selecting its default folders again (resume_refusals), so a folder occupied since Check leads to
        the same sibling the command will choose and Runner accepts its results links."""
        if request.get("confirmed") is not True:
            return {"error": "confirm the run first"}
        missing = form_needs(request.get("form"), env=self.child_env())
        if missing:
            return {"error": needs_line(missing), "missing": missing, "needs": needs_line(missing)}
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
            if is_versus(argv):
                return self.run_versus(argv)
            try:
                if entities and snapshot_bytes(entities) != approved.entities:
                    raise SnapshotRefused(f"{entities_path(entities)} is not the roster Check approved; check again")
                plans = prepared(argv)
            except SnapshotRefused as e:
                return {"error": str(e)}
            except SystemExit as e:
                return {"error": P.capped(exit_message(e.code))}
            if tuple(fingerprint for _, fingerprint in plans) != approved.fingerprints:
                return {"error": "the plan is no longer the one Check approved; check again"}
            try:
                resume_refusals(argv, plans, self.child_env())
            except ResumeUnread as e:
                return dict(error=str(e), **({"plan": told(approved.preflight, e.detail)} if e.detail else {}))
            with self.lock:
                try:
                    self.runner = Runner(self.program, list(approved.argv), [folder for folder, _ in plans],
                                         self.child_env())
                except (ValueError, OSError) as e:
                    return {"ok": False, "error": not_started("run", e)}
                runner = self.runner
            # The card's facts (run_card_facts: the command parsed and planned again, about a second and a half) are
            # made once the run has started and the form's lock is released (the round-3 brief's F7), so /state and
            # Stop never wait on them: until they come, the card shows what it has. Run's own answer comes after
            # them; the gate keeps any other Check or Run out meanwhile. One assignment of a new list, which the
            # runner's card reads whole under its own lock.
            runner.facts = run_card_facts(list(approved.argv), len(plans))
            return {"run": runner.state()}

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
    server's redactor, the one every reply passes, and the one file it serves (the results page of its run)."""
    server = live.serve_page(PAGE, session.state, actions=session.actions(), files=session.files())
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
# name; choosing another CLI resets it to the default. The right column scrolls on its own when it is taller than the
# window: it keeps a stable gutter for a scrollbar that takes room (classic) and a right padding as wide as a scrollbar
# that does not (an overlay one, which a browser draws over the content), COLUMN_ROOM, so nothing in it, the run card
# first, ever sits under its scrollbar. While the form still needs a field (preview's "needs", from
# required), that one line shows, calm, and Check and Run wait; red is only probatio's own refusal of a complete form,
# word for word.
COLUMN_ROOM = 15                      # px: the widest scrollbar a browser draws over or beside the right column
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>probatio</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer">
<link rel="icon" href="data:,">
<style>
""" + live.PAGE_BASE + live.CARD_STYLE + """:root { color-scheme:light dark; }
[hidden] { display:none !important; }
main { max-width:1160px; margin:0 auto; padding:28px 24px 56px; }
header { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin:0 0 24px; }
h1 { font-size:20px; margin:0; font-weight:650; letter-spacing:-.01em; } header .sub { color:var(--muted); }
h2 { font-size:14px; font-weight:600; margin:0 0 6px; display:flex; gap:10px; align-items:baseline; }
h2 .aside { font-size:12.5px; font-weight:400; color:var(--muted); margin-left:auto; }
#how, #memsec { margin-top:28px; }
#memory { margin:2px 0 0; } #memory .kv code { color:var(--ink2); } #memory .cands { margin:10px 0 0; }
#memory .cands button.link { padding:2px 0; margin-right:6px; }
.pill { font-size:12.5px; color:var(--ink2); display:inline-flex; align-items:center; gap:6px; margin-left:auto; }
.pill::before { content:""; width:8px; height:8px; border-radius:50%; background:var(--muted); }
.pill.running::before, .pill.scored::before { background:var(--good); }
.pill.stopped::before, .pill.failed::before, .pill.lost::before, .pill.refused::before { background:var(--bad); }
.pill.finished::before { background:var(--ink2); }
.layout { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,420px); gap:56px; align-items:start; }
.layout > * { min-width:0; }
.answer { position:sticky; top:20px; max-height:calc(100vh - 40px); overflow:auto; scrollbar-gutter:stable;
  padding:0 """ + str(COLUMN_ROOM) + """px 8px 2px; }
.answer h2 { margin-top:28px; } .answer > h2:first-child { margin-top:0; }
.row { display:grid; grid-template-columns:104px minmax(0,1fr); column-gap:16px; row-gap:5px; align-items:baseline;
  padding:7px 0; }
.row > .k { color:var(--ink2); } .row > .k::first-letter { text-transform:uppercase; }
.row .def { grid-column:2; font-size:12.5px; color:var(--muted); }
#modelnote::first-letter { text-transform:uppercase; }
button { font:inherit; color:var(--ink); background:var(--track); border:0; border-radius:8px; padding:7px 14px;
  font-weight:500; cursor:pointer; }
button:hover:not(:disabled) { background:var(--line); }
button.primary { background:var(--accent); color:var(--accent-ink); font-weight:600; }
button.primary:hover:not(:disabled) { background:var(--accent-text); }
button:disabled, button.primary:disabled { background:var(--track); color:var(--muted); cursor:default; }
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
.answers { display:grid; gap:6px; }
button.answer { display:block; width:100%; text-align:left; padding:8px 12px; border-radius:10px; font-weight:400; }
button.answer b { display:block; font-weight:600; }
button.answer .line { display:block; font-size:12.5px; color:var(--ink2); margin-top:2px; }
button.answer[aria-checked=true], button.answer[aria-checked=true]:hover { background:var(--accent-soft);
  color:var(--accent-text); } button.answer[aria-checked=true] .line { color:var(--accent-text); }
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
.refusal { color:var(--bad); margin:6px 0 8px; overflow-wrap:anywhere; }
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
<div id="form"><section id="measure"></section><section id="against"></section><section id="how"></section>
<section id="memsec"></section></div>
<aside class="answer">
<h2>Command <span class="aside" id="cmdnote"></span></h2>
<div class="cmdbox"><pre class="cmd" id="cmd">…</pre>
<div class="cmdbar"><span id="where"></span><button type="button" class="quiet" id="copy">Copy</button></div></div>
<div id="labels" class="labels"></div><div id="error" class="err"></div><div id="files" class="files"></div>
<div class="actions" id="actions"><button type="button" id="check">Check</button>
<button type="button" class="primary" id="run">Run…</button><span class="why" id="why"></span></div>
<div id="confirm"></div>
<section id="runsec" hidden><h2>Run</h2><div id="runcard"></div>
<details class="fold" id="rundetails" hidden><summary>""" + html.escape(live.CARD_WORDS["details"]) + \
    """<span class="sum">""" + html.escape(live.CARD_WORDS["details_hint"]) + """</span></summary>
<div id="runlines"></div></details><div id="runbox"></div></section>
<h2>Plan <span class="aside">from the dry run</span></h2><div id="plan"></div>
</aside>
</div>
<footer id="foot"></footer>
</main>
<script>
"use strict";
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const RESULTS_PATH = """ + json.dumps(RESULTS_PAGE_PATH) + """;
""" + live.CARD_SCRIPT + """
const CLASS_WORDS = {I: "I · professed", II: "II · behavioural", III: "III · unobserved"};
const OTHER = "(other)";                                 // the model menu's last entry; no model has this name
const DEFAULT_MODEL = """ + agent_page_words("default_model_label") + """;
const TIME_NOTE = """ + agent_page_words("time_note") + """;
function agentAvailability() {             // absent for the shipped menu, capability facts when an extension adds one
  return D && D.availability && D.availability[F.agent];
}
function capabilityNote(field, value) {
  const spec = agentAvailability();
  return spec && !spec[field].includes(value) ? "not available for " + agentName(F.agent) : null;
}
function agentChoiceAvailability() {        // editing names can change the family without rebuilding the form
  const row = $("row-agent");
  if (!row || !D.availability) return;
  const family = versusMode() ? "versus" : F.types.length > 1 ? "compare" : "v4";
  row.querySelectorAll("button").forEach(button => {
    const unavailable = !D.availability[button.value].families.includes(family);
    button.title = unavailable ? "not available for this run" : "";
    button.classList.toggle("off", unavailable);
  });
}
let D = null, F = null, said = null, plan = null, run = null, asked = 0, timer = null, confirming = false;
let checking = false, marks = [], otherModel = false;
let mem = null, memAsked = 0, memKey = null;   // the memory block's answer (/memory), its request and its form
let edits = 0, answered = 0;      // the form's edits, and the latest one /command has answered; until then, Check
                                  // and Run wait (updating)
const updating = () => answered < edits;
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
          jobs: String(d.jobs), config_dir: "",     // watch's choices come default first; "" is the CLI's own folder
          versus: D.versus.answers[0], strangers: D.versus.default.strangers};    // the question's answer, its default
}
// ---- versus: the question, once the rows hold two or three names of different types (the server says so: said's
// "question"), and the form a versus run takes while its first answer is chosen
const versusMode = () => !!(said && said.question) && F.versus === D.versus.answers[0];
function question() {                     // the question's two answers, each with its line; built once per measure()
  const W = D.versus.words, list = el("div", "answers"), box = row(W.question, list);
  box.id = "question"; box.hidden = true;
  list.setAttribute("role", "radiogroup");
  for (const a of D.versus.answers) {
    const b = el("button", "answer");
    b.type = "button"; b.value = a; b.id = "answer-" + a; b.setAttribute("role", "radio");
    add(b, el("b", null, W[a]), el("span", "line", W[a + "_line"]));
    b.addEventListener("click", () => { if (F.versus !== a) { F.versus = a; changed(); } });
    list.appendChild(b);
  }
  return box;
}
function mode() {                         // show the question while the rows ask it; hide what a versus run does not take
  const q = $("question"), vs = versusMode();
  agentChoiceAvailability();
  if (q) {
    q.hidden = !(said && said.question);
    q.querySelectorAll("button.answer").forEach(b => b.setAttribute("aria-checked", String(F.versus === b.value)));
  }
  for (const [id, hide] of [["against", vs], ["row-classes", vs], ["row-auto", vs], ["row-strangers", !vs]]) {
    const e = $(id);
    if (e) e.hidden = hide;
  }
  const effort = $("row-effort"), spec = agentAvailability();
  if (effort) effort.hidden = !!spec && !spec.efforts.length;
  const tiers = $("tiers");
  if (tiers) tiers.querySelectorAll("button").forEach(b => { b.hidden = vs && !D.versus.tiers.includes(b.value); });
  const length = $("lengthnote");
  if (length) length.textContent = vs ? D.versus.words.length_line : "Check shows the calls and the time.";
  for (const id of ["namesnote", "quicknote"]) { const e = $(id); if (e && vs) e.hidden = true; }
  const role = $("namesnote");
  if (role && !vs) role.hidden = false;
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
  if (cross) {                                       // what a name does in a comparison, and at quick what it measures
    const role = el("p", "note", D.names_role), quick = el("p", "note");
    role.id = "namesnote"; quick.id = "quicknote"; quick.hidden = true;
    add(s, question(), role, quick);
  }
}
function coded(box, text) {               // text with `command` spans as code, the way the scorecard writes them
  String(text).split("`").forEach((part, i) => add(box, i % 2 ? el("code", null, part) : document.createTextNode(part)));
  return box;
}
function kvLine(k, v, after) {            // a key, a value in code, and an optional muted tail
  return add(el("div", "kv"), el("span", null, k + " "), el("code", null, v), after ? el("span", null, " " + after) :
    null);
}
function memsec() {                       // the memory this run tests: the config folder field and what it holds
  const s = $("memsec");
  s.replaceChildren(el("h2", null, "Memory this run tests"));
  const box = el("div");
  box.id = "memory";
  add(s, row("config folder", field(F, "config_dir", "the CLI's own (leave empty)"),
    "The folder your agent's launcher sets as CLAUDE_CONFIG_DIR (Claude Code) or CODEX_HOME (Codex); empty is " +
    "the one this shell gives the CLI."), box);
  renderMemory();
}
function renderMemory() {                 // /memory's answer: probatio's own memory report, read as the run reads it
  const box = $("memory");
  if (!box) return;
  box.replaceChildren();
  if (!mem) { box.appendChild(el("p", "note", F.agent ? "Looking at the memory files…" :
    "Choose an agent to see the memory it tests.")); return; }
  if (mem.error) { box.appendChild(el("p", "note", mem.error)); return; }
  const r = mem.report;
  if (!r) box.appendChild(el("p", "note", mem.missing ? "No folder at " + mem.missing + "; this run would stop " +
    "on it." : F.agent ? "The memory shows once the command is complete." : "Choose an agent to see the memory it " +
    "tests."));
  else {
    box.appendChild(kvLine("config folder", r.folder, "(" + r.source + ")"));
    if (mem.empty) box.appendChild(el("p", "hintline", "Memory files: " + mem.empty + "."));
    else for (const f of r.files) box.appendChild(kvLine("", f.shown, f.size + (f.title ? " · " + f.title : "")));
    if (r.total) box.appendChild(el("div", "hintline", r.total[0].toUpperCase() + r.total.slice(1) + "."));
    for (const n of r.notes) box.appendChild(el("p", "hintline", n[0].toUpperCase() + n.slice(1) + "."));
  }
  if (mem.candidates && mem.candidates.length) {     // a click fills the field with that folder
    const list = add(el("div", "cands"), el("div", "note", "Other Claude Code config folders on this machine:"));
    for (const c of mem.candidates) {
      const b = el("button", "link", c.folder), md = c.claude_md;
      b.type = "button"; b.title = c.path === null ? mem.escaped : c.path;
      if (c.path === null) b.disabled = true;           // a name shown escaped: typed by hand, never filled
      else b.addEventListener("click", () => { F.config_dir = c.path; memsec(); changed(); });
      add(list, add(el("div", "kv"), b, el("span", null, md ? "CLAUDE.md " + md.size + (md.title ? " · " + md.title : "")
        : "no CLAUDE.md")));
    }
    box.appendChild(list);
  }
}
async function refreshMemory() {          // asked again only when what the memory depends on changed
  const key = JSON.stringify([F.agent, F.config_dir, F.memory, F.auto_memory, F.types, F.targets.individual || "",
                              versusMode()]);
  if (key === memKey) return;
  memKey = key; const n = ++memAsked;
  if (!F.agent) { mem = null; renderMemory(); return; }
  try { const r = await post("/memory", {form: F}); if (n === memAsked) mem = r; }
  catch (err) { if (n === memAsked) mem = {error: "the page could not reach its server (" + err.message + ")"}; }
  renderMemory();
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
        counts[part].classList.toggle("bad", n > hi);           // too few is a missing piece (required), calm
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
  const defaultChoice = ["", DEFAULT_MODEL[F.agent] || "default"], spec = agentAvailability();
  if (spec) defaultChoice[1] = spec.default_model_label;
  menu.id = "model"; menu.disabled = !F.agent; menu.setAttribute("aria-label", "model");
  for (const [value, word] of [defaultChoice, ...list.map(m => [m, m]),
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
  const spec = agentAvailability();
  const words = spec ? spec.time_note : TIME_NOTE[F.agent || D.agents[0][0]];
  if (box) box.textContent = said && said.rates ? "time estimate: " + said.rates : words || "Time estimate unknown";
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
  const spec = agentAvailability();
  s.replaceChildren(el("h2", null, "How to run"));
  const agent = row("agent", choices("agent", D.agents, {after: () => {
    const chosen = agentAvailability();
    if (chosen && !chosen.efforts.length) F.effort = d.effort;
    F.model = ""; otherModel = false; how(); }}),
    F.agent ? "Runs on your own login and quota." : "Choose one; it runs on your own login and quota.");
  agent.id = "row-agent"; add(s, agent);
  const note = el("div", "def");
  note.id = "modelnote";
  add(s, add(row("model", modelChoice()), note)); modelNote();
  const tiers = choices("tier", D.tiers.map(t => [t, t]), {after: how, def: d.tier}), length = el("div", "def");
  tiers.id = "tiers"; length.id = "lengthnote";
  add(s, add(row("length", tiers), length));
  const [more, sum] = fold("more", "more", "More options");
  sum.id = "moresum";
  const classes = row("classes", choices("classes", D.classes.map(k => [k, CLASS_WORDS[k] || k,
      capabilityNote("classes", k) || (planned.includes(k) ? null : "not asked at " + F.tier)]), {multi: true}),
      planned.length < D.classes.length ? "At " + F.tier + ", only " + planned.join(" and ") + "." : null);
  const strangers = row(D.versus.words.strangers, choices("strangers", D.versus.strangers.map(v => [v, v]),
      {def: D.versus.default.strangers}), D.versus.words.strangers_line);
  classes.id = "row-classes"; strangers.id = "row-strangers"; strangers.hidden = true;
  const effort = row("effort", choices("effort", (spec && !spec.efforts.length ? [] : D.efforts).map(e =>
    [e, e, capabilityNote("efforts", e)]), {def: d.effort}));
  effort.id = "row-effort";
  add(more, classes,
    row("memory", choices("memory", D.memory.map(m => [m, m, capabilityNote("memory", m)]), {def: d.memory}),
      "Both asks each item with and without your memory files."), strangers,
    effort,
    row("watch", choices("watch", D.watch.map(w => [w, w]), {def: D.watch[0]}),
      "On opens a live view in your browser; off still shows the run's output here."),
    row("jobs", jobsField(), D.measured_jobs ? "Calls at once; more is faster but heavier on your rate limit; the " +
      "time estimate scales with it, from rates measured at " + D.measured_jobs + "." :
      "Calls at once; more is faster but heavier on your rate limit."),
    row("auto memory", choices("auto_memory", D.auto_memory.map(m => [m, m]), {def: d.auto_memory}),
      "Claude Code's own saved notes (Claude Code only): leave as configured to measure your agent as it is; " +
      "off, in both conditions, is the calibration's setting."));
  more.lastChild.id = "row-auto";
  s.appendChild(more); moreSummary(); mode();
}
function moreSummary() {                   // More options' closed line: what differs from the defaults
  const box = $("moresum");
  if (!box) return;
  const d = D.default, diff = [], vs = versusMode();        // a versus run shows only what it takes
  if (!vs && !same(F.classes, D.classes)) diff.push("classes " + F.classes.join(", "));
  for (const [key, word, base, shown] of [["memory", "memory", d.memory, true],
      ["strangers", D.versus.words.strangers, D.versus.default.strangers, vs], ["effort", "effort", d.effort, true],
      ["watch", "watch", D.watch[0], true], ["auto_memory", "auto memory", d.auto_memory, !vs]])
    if (shown && F[key] !== base) diff.push(word + " " + F[key]);
  if (String(F.jobs).trim() !== String(d.jobs)) diff.push("jobs " + String(F.jobs).trim());
  box.textContent = diff.length ? diff.join(" · ") : "defaults";
  box.classList.toggle("changed", diff.length > 0);
}
function build() { marks = []; measure(); against(); how(); memsec(); }
// ---- the answer
function changed() {                      // an edit: Check and Run wait for /command's answer to it (updating)
  confirming = false; edits++;
  clearTimeout(timer); timer = setTimeout(refresh, 150);
  render();
}
async function refresh() {
  const n = ++asked, edit = edits;
  try { const r = await post("/command", {form: F}); if (n === asked) { said = r; answered = edit; } }
  catch (err) { if (n === asked) { said = null; answered = edit; } }
  render(); refreshMemory();
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
  const role = $("namesnote");               // the role sentence in the roster's words, once the preview has them
  if (role && said && said.names_said && said.names_said.length) role.textContent = said.names_said[0];
  const quick = $("quicknote"), more = said && said.names_said ? said.names_said.slice(1) : [];
  if (quick) {                               // at quick, with a name typed: the scorecard's own sentence for it
    quick.replaceChildren();
    quick.hidden = !(more.length && F.types.some(t => lines(F.targets[t]).length));
    if (!quick.hidden) coded(quick, more.join(" "));
  }
  const error = $("error"), needs = said && said.needs;    // a field still empty: one calm line, never an error
  error.replaceChildren(); error.classList.toggle("calm", !!needs);
  if (needs) error.textContent = needs;
  else if (said && said.error) add(error, el("b", null, "probatio: "), document.createTextNode(said.error));
  const stale = plan && plan.ok && keyOf(plan) !== keyOf(said);
  $("check").disabled = !said || updating() || !!needs || !!said.error || checking;
  const ready = plan && plan.ok && !stale && !going() && !checking && !updating();   // not while either is in flight
  $("run").disabled = !ready;
  $("why").textContent = going() ? "a run is going" : updating() ? "updating" : needs ? said.missing[0] + " first" :
    !plan || !plan.ok ? "check first" : stale ?
    "the command changed: check it again" : "";
  $("actions").hidden = confirming && ready;                 // while confirming, Start the run is the one primary
  if (D) { mode(); moreSummary(); }
  renderConfirm(ready); renderPlan(stale);
}
function renderConfirm(ready) {
  const box = $("confirm"); box.replaceChildren();
  if (!confirming || !ready) return;
  const sm = plan.summary || {}, who = agentName(F.agent), spec = agentAvailability();
  const size = [sm.calls !== null && sm.calls !== undefined ? sm.calls + " calls" : null, sm.time].filter(Boolean);
  const p = el("p", null, "Run this command? " + (size.length ? size.join(", ") : "See the plan for its size") +
    ", on " + who + " with " + (F.model.trim() ? "the model " + F.model.trim() : "the CLI's default model") +
    (spec && !spec.efforts.length ? "" : " at effort " + F.effort) +
    ". It uses your own " + who + " login and quota.");
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
  if (!plan.ok && plan.needs) {          // a form that still needs a field: the calm line, never red
    box.appendChild(el("div", "err calm", plan.needs));
    return;
  }
  if (!plan.ok) {                         // a refusal: the page's words, and probatio's own in the whole preflight
    add(box, add(el("div", "err"), el("b", null, "probatio: "), document.createTextNode(plan.error)));
    if (plan.plan) box.appendChild(preflight(plan.plan));
    return;
  }
  const sm = plan.summary || {};
  const head = [sm.calls !== null && sm.calls !== undefined ? sm.calls + " calls" : null, sm.time].filter(Boolean);
  if (head.length) box.appendChild(el("div", "big", head.join(" · ")));
  if (sm.hint) box.appendChild(el("div", "hintline", sm.hint));
  (plan.notices || []).forEach(line => box.appendChild(el("div", "note", line)));
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
  "command resumes it", failed: "failed", refused: "refused: no page was written"};
// The run's box opens with the run card (live.py's results card, the --watch page's own), one frame through the run's
// states: while it goes, the live link as its secondary button; once done, "Open the results page"; with no page,
// why. It is drawn only when it changed, so focus and a selection in it survive the page's refreshes. Then the
// status, Stop, the command and its folders, and the output: open while the run goes and when it failed, stopped or
// was refused (they are the information then); under a finished card (with its page or without) folded into "Run
// details" (round 2, E3), a fold of the page's own markup, closed at first and kept as the reader leaves it across the
// refreshes, so the card's folder is not said three times; in the scored state the status and Stop stay outside it,
// since Stop is the one way to end the held live page.
const cardHands = {path: RESULTS_PATH, live: () => run && run.watch};
function renderRun() {
  const box = $("runbox"), lines = $("runlines"), out = $("runsec").querySelector("pre.out");
  const keep = !out || out.scrollHeight - out.scrollTop - out.clientHeight < 8;
  box.replaceChildren(); lines.replaceChildren();
  $("runsec").hidden = !run;
  cardDraw($("runcard"), run ? run.card || null : null, cardHands);
  const folded = !!(run && run.card && (run.status === "finished" || run.status === "scored"));
  $("rundetails").hidden = !folded;
  if (!run) return;
  const into = folded ? lines : box;              // the lines' place: the fold under a finished card, else the box
  const bar = el("div", "runbar");
  bar.appendChild(el("span", "pill " + run.status, (RUN_WORDS[run.status] || run.status) +
    (run.status === "failed" && run.code !== null ? " (exit " + run.code + ")" : "")));
  if (going()) {
    const stop = el("button", null, run.status === "stopping" ? "Stopping…" : "Stop");
    stop.type = "button"; stop.disabled = run.status === "stopping";
    stop.addEventListener("click", async () => { stop.disabled = true; try { await post("/stop", {}); } catch (e) {} });
    bar.appendChild(stop);
  }
  (folded && !going() ? into : box).appendChild(bar);     // Stop, when there is one, stays in sight
  if (run.refusal) into.appendChild(el("p", "refusal", run.refusal));    // the line a versus run ended with
  const kv = (k, v) => add(el("div", "kv"), el("span", null, k + " "), el("code", null, v));
  into.appendChild(kv("started " + run.started + ":", run.command));
  const pathLine = (k, path) => {                 // a path line breaks where a path breaks: the card's own rule
    const line = kv(k, "");                         // (cardPath: a <wbr> after each "/", F10; every path line, G2)
    cardPath(line.querySelector("code"), path);
    return line;
  };
  for (const f of run.folders) into.appendChild(pathLine("results folder", f));
  for (const [kind, path] of run.results) into.appendChild(pathLine(kind.toLowerCase(), path));
  const pre = lined(el("pre", "out"), run.lines.join("\\n") || "(no output yet)");
  into.appendChild(pre);
  if (keep) pre.scrollTop = pre.scrollHeight;
}
async function check() {
  if (updating()) return;                // never on a form /command has not answered
  checking = true; confirming = false; render();
  try { plan = await post("/check", {form: F}); }
  catch (err) { plan = {ok: false, error: "the page could not reach its server (" + err.message + ")"}; }
  checking = false; render();
}
async function start() {
  confirming = false; render();
  if (updating()) return;                // never on a form /command has not answered
  try {
    const r = await post("/run", {form: F, confirmed: true});
    if (r.error)                          // a refused Run may bring the preflight, probatio's own words after it, or
      plan = Object.assign({}, plan, {ok: false, error: r.error, needs: r.needs || null},     // what is still needed
                           r.plan === undefined ? {} : {plan: r.plan});
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
