#!/usr/bin/env python3
"""live: watching a probatio run while it goes: the live line of a v4 run, and the one --watch page every kind of run
has (single-type, cross-type and versus, on Claude Code or Codex).

Two views read the run, and neither writes anything in its results folder:

    LiveLine       the default: one line on stderr with units done of total, valid, abstained, errors, elapsed, the
                   time left at this run's own pace and the quota; redrawn in place in a terminal, clipped by display
                   columns, or one plain checkpoint line per tenth of the run without one (no CR, no escape)
    Watch          --watch: the one page (VIEW_LIVE) on 127.0.0.1 behind a random token (serve_page), one tab per run
                   of the command, in one grammar: the run line and progress, the provisional scorecard, the link to
                   the results page once the run wrote one, the errands as they happen and the questions as they
                   land (feed.py's entries, each opened only when the page asks); it shows what the agent wrote,
                   which can quote what it knows
      RunView        the observer of one run: it reads replies.jsonl, and the shown and opened errands' record.jsonl,
                     through CommittedLines, and followup.json defensively; nothing else in the folder, and never the
                     memory files, quota.jsonl, the Codex homes or a transcript
      ViewRedactor   every free-text field passes it before it is serialized (calibration/redact.py's contract)
      Provisional    one coalescing worker per run scores the full plan on one snapshot with the final scorers; the
                     final card replaces it the moment the run is scored
    VersusWatch    --watch for a versus run (versus_run.py): the same page and server, with counts and no score while
                   the run goes, the same feed and errands, and the command's own answer lines once it ends
      VersusView     a RunView that reads the same files the same way, and counts each arm's and row's planned,
                     answered and valid calls and what each answer chose (versus_counts); it never scores

probatio.py imports this module for a v4 run (import_live), and versus_run.py through it for a versus run; this
module imports probatio's helpers and feed.py, as ordo.py imports probatio, so the dependency runs one way at import
time. It never imports versus itself (feed.VersusKind does, for a versus run, when versus_run starts its view), and it
imports ui.py only at call time, for the results page's pinned reader. It imports results_kit for the results pages'
own tokens (colours, type scale, spacing, button shape), which the results card is drawn with. Section numbers refer
to the design of the quick run and the run you can watch (revision 2, section 4); V-numbers to the live-feed ruling of
2026-11-14, C-numbers to the results-card ruling.

Standard library only, Python 3.9 or later.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import sys
import threading
import time
import traceback
import unicodedata
from collections import OrderedDict
from pathlib import Path

import feed
import results_kit
from probatio import (capped, display, plain_text, shown_view, terminal_text, AGENT_NAMES, CLASS_NAMES, CONDITION_NAMES, NOT_RUN_IN, REPLY_FIELDS,
                      TIER_CLASSES, V4_CLASSES, duration, episode_dir, episode_facts, key, parse_report, valid_line,
                      quick_card_line, whole)

# ---- thresholds, caps and words: one home ----------------------------------------------------------------------------
TICK_SECONDS = 1.0              # a terminal's line is redrawn each second and on every finished call (4.1)
MIN_COLUMNS = 30                # below this many columns the line falls back to checkpoints
CHECKPOINTS = 10                # without a terminal, a line at each tenth of the run, then one at the end
MEAN_AFTER = 3                  # a kind of call uses this launch's mean once 3 of it finished, the profile's before
PREFIX = "probatio: "           # every checkpoint line starts with it
CAPS = {"reply": 300, "args": 90, "filed": 200, "error": 200, "request": 300}     # characters, after redaction (4.3)
CALLS_SHOWN = 14                # the newest tool calls of a card
PROBLEMS_SHOWN = 5              # the newest observer failures, as fixed sentences
REFRESH_SECONDS = 0.5           # the observer reads the folder at most this often, however many tabs ask
PREFIX_BYTES = 256              # the committed bytes a reader keeps to check that what it read is still there
PROVISIONAL_SECONDS = 30        # at most one provisional scoring starts per 30 s (4.4)
CELL_WORDS = {"N0": "plain world", "U": "told unobserved", "O": "told observed"}
KIND_WORDS = {"single": "single-type", "cross": "cross-type", "versus": "head-to-head"}
BOUNDARY = ("Shows what the agent wrote, which can quote what it knows. Local to this machine; secret-shaped text is "
            "redacted.")
MOVES = "Every number moves until the last call; the result is the final scorecard."
MIDLINE_NOTE = "above 50 favours the first type named"
RECORD_FIELDS = {"kind": str, "seq": int}


# ---- display columns (4.1, finding 6) --------------------------------------------------------------------------------
def char_columns(ch):
    """The terminal columns one character takes: 0 for a combining or enclosing mark or a format character (Mn, Me,
    Cf: zero-width joiners and bidi controls among them), 2 for East Asian Wide and Fullwidth, 1 otherwise."""
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def columns(text):
    """The columns a text takes in a terminal."""
    return sum(char_columns(ch) for ch in text)


def clip(text, n):
    """text cut to at most n columns: whole characters only, the cut line ending in an ellipsis. A combining mark is
    kept only with its base, since the cut falls before the first character that does not fit and a mark (0 columns)
    always fits once its base did."""
    if columns(text) <= n:
        return text
    if n < 1:
        return ""
    out, used = [], 0
    for ch in text:
        w = char_columns(ch)
        if used + w > n - 1:            # one column is the ellipsis's
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def plain_label(text):
    """A run label safe to draw: probatio's plain_text (each white space character a space, every other unseen
    character, controls, bidi overrides, lone surrogates and invisible ones included, removed, runs of spaces made
    one), cut at DISPLAY_CHARS (display), so the tab and the live line show it on one line, as one token."""
    return display(plain_text(str(text)))


def clock_text(seconds):
    """Elapsed time as the line prints it: 51s, 8m 51s, 1h 02m."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {s % 3600 // 60:02d}m"


def terminal_width(stream):
    """The stream's terminal width minus one, read at every draw (a resize applies at the next one); 80 columns when
    the stream is not a terminal the OS can measure."""
    try:
        cols = os.get_terminal_size(stream.fileno()).columns
    except (AttributeError, ValueError, OSError):
        cols = shutil.get_terminal_size((80, 24)).columns
    return cols - 1


def quota_text(reading):
    """The quota part of the line, "5-hour 18%, 7-day 24%", from QuotaLog.latest's reading; None without one."""
    if not reading:
        return None
    parts = [f"{name} {round(100 * reading[w])}%" for w, name in (("five_hour", "5-hour"), ("seven_day", "7-day"))
             if isinstance(reading.get(w), (int, float)) and not isinstance(reading.get(w), bool)]
    return ", ".join(parts) or None


def call_kind(call):
    """A call's kind for the time left: a question, or an errand by its cell (N0, U, O)."""
    return (call["meta"].get("cell") or "errand") if call.get("episode") else "question"


# ---- the live line (4.1) ---------------------------------------------------------------------------------------------
class LiveLine:
    """The run's default view on stderr. run_calls hands it the plan and what is already done (begin), every call as
    it finishes (finished) and every message it prints (say), and closes it at the end (close). In a terminal at
    least MIN_COLUMNS wide the line is redrawn in place ("\\r", the clipped line, "\\x1b[K") each second by a ticker
    and on every finished call; otherwise a newline-terminated checkpoint prints at each tenth of the run and at the
    end, and nothing ever writes a CR or an escape. One lock serializes the ticker and the runner's threads, and each
    draw is one write, so no two lines interleave. The time left sums each pending call at its kind's mean in this
    launch (once MEAN_AFTER of the kind have finished) or the profile's rate (prior), divided by --jobs. snapshot() is
    what the page shows as progress."""

    def __init__(self, jobs, prior, quota=None, label=None, stream=None, tty=None, width=None, clock=time.monotonic,
                 tier=None):
        self.jobs, self.prior, self.quota, self.clock, self.tier = max(1, jobs), prior, quota, clock, tier
        self.label = plain_label(label) if label else None
        self.stream = stream if stream is not None else sys.stderr
        self.tty = (self.stream.isatty() if hasattr(self.stream, "isatty") else False) if tty is None else tty
        self.width = width or (lambda: terminal_width(self.stream))
        self.lock = threading.RLock()
        self.total = self.done = self.valid = self.abstained = self.errors = 0
        self.pending, self.seconds = {}, {}
        self.start, self.tenth, self.drawn, self.closed, self.ticker = clock(), 0, False, False, None
        self.stop = threading.Event()

    # -- what the runner tells it
    def begin(self, calls, done):
        """The plan and its finished records (read_replies's): resumed calls without an error count as done."""
        with self.lock:
            self.total, self.start = len(calls), self.clock()
            for c in calls:
                rec = done.get(key(c))
                if rec and not rec.get("error"):
                    self.count(c, rec)
                else:
                    prior = self.prior(c)
                    self.pending[key(c)] = (call_kind(c), None if prior is None else float(prior))
            self.tenth = self.done * CHECKPOINTS // self.total if self.total else 0
            if self.tty and not self.ticker:
                self.ticker = threading.Thread(target=self.tick, name="probatio-live-line", daemon=True)
                self.ticker.start()
            self.draw()

    def finished(self, call, rec):
        """One call came back (rec is its replies.jsonl record): count it, keep its seconds for its kind's mean."""
        with self.lock:
            kind, _ = self.pending.pop(key(call), (call_kind(call), 0.0))
            self.seconds.setdefault(kind, []).append(float(rec.get("seconds") or 0.0))
            self.count(call, rec)
            self.draw()

    def count(self, call, rec):
        self.done += 1
        if rec.get("error"):
            self.errors += 1
        elif not call.get("episode") and rec.get("outcome") is None:
            self.abstained += 1
        else:
            self.valid += 1

    # -- the numbers
    def left(self):
        """Seconds left at this run's own pace, or None with no pending calls or no estimate yet. A missing prior
        stays unknown until this launch has enough observations of every pending kind."""
        if not self.pending:
            return None
        mean = {k: sum(v) / len(v) for k, v in self.seconds.items() if len(v) >= MEAN_AFTER}
        estimates = [mean.get(kind, prior) for kind, prior in self.pending.values()]
        return None if any(value is None for value in estimates) else sum(estimates) / self.jobs

    def snapshot(self):
        """The progress numbers, as the page shows them and the line prints them."""
        with self.lock:
            left = self.left()
            reading = self.quota.latest() if self.quota is not None else None
            return {"done": self.done, "total": self.total, "valid": self.valid, "abstained": self.abstained,
                    "errors": self.errors, "elapsed_seconds": round(self.clock() - self.start, 1),
                    "left_seconds": None if left is None else round(left, 1), "quota": quota_text(reading),
                    "finished": self.closed}

    def text(self, width=None):
        """The line itself: quick · 212/360 · 205 valid · 4 abstained · 8m 51s · about 6 min left · 5-hour 18%,
        7-day 24%, led by the run's tier when it is given.
        A label (a several-run command's) comes first, clipped to the room the numbers leave within width, and is
        dropped below 8 columns of room, so the counts are what a narrow line keeps."""
        s = self.snapshot()
        parts = ([self.tier] if self.tier else []) + [f"{s['done']}/{s['total']}", f"{s['valid']} valid",
                                                       f"{s['abstained']} abstained"]
        if s["errors"]:
            parts.append(f"{s['errors']} errors")
        parts.append(clock_text(s["elapsed_seconds"]))
        if s["left_seconds"] is not None and s["done"] < s["total"]:
            parts.append(f"{duration(s['left_seconds'])} left")
        if s["quota"]:
            parts.append(s["quota"])
        numbers, label = " · ".join(parts), self.label
        if label and width is not None:
            room = width - columns(numbers) - 3
            label = clip(label, room) if room >= 8 else None
        return f"{label} · {numbers}" if label else numbers

    # -- drawing
    def in_place(self):
        """Whether this draw goes in place: a terminal at least MIN_COLUMNS wide (re-read every draw)."""
        return self.tty and self.width() >= MIN_COLUMNS

    def write(self, text, before="", after=""):
        """text on the stream in one write, with the line's own redraw codes (a carriage return before, an erase to
        the end of the line after): on probatio's TerminalFilter through its framed(), which lets only those fixed
        codes through raw and filters the text like any other output; on any other stream as they are."""
        framed = getattr(self.stream, "framed", None)
        if framed is None:
            self.stream.write(before + text + after)
        else:
            framed(text, before, after)
        self.stream.flush()

    def draw(self):
        """One draw: in place in a wide terminal; else a checkpoint when the run has passed another tenth."""
        with self.lock:
            if self.closed:
                return
            if self.in_place():
                width = self.width() - 2
                self.write("  " + clip(self.text(width), width), before="\r", after="\x1b[K")
                self.drawn = True
                return
            self.leave()
            tenth = self.done * CHECKPOINTS // self.total if self.total else 0
            if tenth > self.tenth:
                self.tenth = tenth
                self.write(PREFIX + self.text() + "\n")

    def leave(self):
        """End an in-place line with a newline before plain output (a terminal that became narrow), no CR."""
        if self.drawn:
            self.write("\n")
            self.drawn = False

    def say(self, message):
        """A message printed above the line, then the line again."""
        with self.lock:
            if self.in_place() and self.drawn:
                self.write(message + "\n", before="\r\x1b[K")
                self.drawn = False
            else:
                self.leave()
                self.write(message + "\n")
            self.draw()

    def tick(self):
        while not self.stop.wait(TICK_SECONDS):
            self.draw()

    def close(self):
        """The last draw and a final newline, once; the ticker stops."""
        with self.lock:
            if self.closed:
                return
            if self.in_place():
                self.draw()
                self.write("\n")
            else:
                self.leave()
                self.write(PREFIX + self.text() + "\n")
            self.drawn, self.closed = False, True
        self.stop.set()


# ---- redaction (4.3) -------------------------------------------------------------------------------------------------
# calibration/redact.py's contract, reimplemented since probatio and the harness never import each other: the exact
# values of every secret-named variable (8 characters or more) and of the given extras, longest first, then the key
# shapes, each replaced by <redacted>; a text that must be cut is redacted whole first. The page adds more shapes and
# removes control and format characters. tests/fixtures/redact_cases.json holds both to the same output on their
# common cases.
#
# Which variables are secret-named is the page's own, narrower rule. The harness only hides text, so a name matched by
# mistake costs it nothing; the --ui page also refuses any word of a command its redactor would change, so a variable
# matched by mistake refuses the user's own name (GIT_AUTHOR_NAME holds it; "AUTH" is in AUTHOR). The page reads a name
# as secret by its whole parts, split at "_", at other punctuation and between a lower-case letter and a capital
# (secret_named): a part ending in one of SECRET_SUFFIXES (GITHUB_TOKEN, DBPASSWORD), a part that is one of
# SECRET_PARTS, an AUTH part (GH_AUTH_HEADER, NPM_CONFIG__AUTH) unless SOCK follows it (SSH_AUTH_SOCK, an agent's
# socket), or one of SECRET_NAMES, the credentials probatio's own CLIs read, named outright. So GIT_AUTHOR_NAME,
# XAUTHORITY and SSH_AUTH_SOCK are not secret-named, and every case the shared file holds reads as before.
SECRET_SUFFIXES = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "PASSPHRASE", "APIKEY")
SECRET_PARTS = ("TOKENS", "SECRETS", "PASS", "API_KEY", "PRIVATE_KEY", "CREDENTIALS", "OAUTH")
SECRET_NAMES = frozenset({"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN_BACKUP", "ANTHROPIC_AUTH_TOKEN",
                          "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY"})
SECRET_NAME = re.compile(r"(?:^|_)[A-Z0-9]*(?:{})(?:_|$)|(?:^|_)(?:{})(?:_|$)|(?:^|_)AUTH(?:$|_(?!SOCK(?:_|$)))".format(
    "|".join(SECRET_SUFFIXES), "|".join(SECRET_PARTS)), re.I)     # read over name_parts(name), never the raw name
MIN_SECRET = 8
MARK = "<redacted>"
KEY_SHAPES = re.compile("|".join((
    r"sk-ant-[A-Za-z0-9_-]{8,}",                         # Anthropic (calibration/redact.py's own shape)
    r"sk-[A-Za-z0-9_-]{20,}",                            # OpenAI-style
    r"hf_[A-Za-z0-9]{30,}",                              # Hugging Face
    r"gh[pousr]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}",
    r"xox[abprs]-[A-Za-z0-9-]{10,}",                     # Slack
    r"AKIA[0-9A-Z]{16}",                                 # AWS access key id
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",       # a three-part JWT
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----|[\s\S]*)")))


def name_parts(name):
    """A variable's name with its parts split by "_": other punctuation becomes "_", and a capital after a lower-case
    letter or a digit starts a part (authToken reads as auth_Token)."""
    return re.sub(r"[^A-Za-z0-9]+", "_", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name))


def secret_named(name):
    """Whether a variable's name says it holds a secret (the page's rule above): one of SECRET_NAMES, or SECRET_NAME
    over its parts."""
    return isinstance(name, str) and (name.upper() in SECRET_NAMES or bool(SECRET_NAME.search(name_parts(name))))


def visible(text):
    """text without its control and format characters other than newline and tab, so none can split a secret."""
    return "".join(ch for ch in str(text) if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf"))


class ViewRedactor:
    """scrub(text): control and format characters other than newline and tab removed (visible), then every known
    value, then every key shape, each as <redacted>. Built from probatio's own environment and the page's token.
    matched_names(text) says which secret-named variables' values the scrub replaced, by name alone, so a page that
    refuses a word can say why without showing the value."""

    def __init__(self, environ=None, extra=()):
        environ = os.environ if environ is None else environ
        self.names = {}                                 # value -> the secret-named variables holding it
        for k, v in environ.items():
            if secret_named(k) and isinstance(v, str):
                self.names.setdefault(v, set()).add(k)
        values = set(self.names) | {v for v in extra if isinstance(v, str)}
        self.values = sorted((v for v in values if len(v) >= MIN_SECRET), key=len, reverse=True)

    def scrub(self, text):
        """(text without its known values, the values it held), visible first and the longest value first, as the
        redaction replaces them; the key shapes are not applied here."""
        text, held = visible(text), []
        for v in self.values:
            if v in text:
                held.append(v)
                text = text.replace(v, MARK)
        return text, held

    def __call__(self, text):
        if text is None:
            return text
        return KEY_SHAPES.sub(MARK, self.scrub(text)[0])

    def matched_names(self, text):
        """The names, sorted, of the secret-named variables whose values the redaction replaces in text (never a
        value); empty when text holds none, and for a value that is only one of the extras, such as the page's
        token."""
        if text is None:
            return []
        return sorted({name for v in self.scrub(text)[1] for name in self.names.get(v, ())})

    def clip(self, text, limit, tail=False):
        """text redacted whole, then cut to its first (or, with tail, its last) limit characters."""
        text = self(text) or ""
        return text[-limit:] if tail else text[:limit]

    def brief(self, text, cap):
        """A free-text field as the page shows it: redacted whole, then cut to cap characters with an ellipsis."""
        if text is None:
            return None
        full = self(text)
        return full if len(full) <= cap else full[:cap - 1] + "…"


# ---- reading what the run commits (4.5) ------------------------------------------------------------------------------
class CommittedLines:
    """The newline-committed records of one append-only JSON-lines file (replies.jsonl, an episode's record.jsonl),
    read incrementally without ever writing it. It keeps the file's (st_dev, st_ino), the offset just past its last
    committed newline and the last PREFIX_BYTES bytes before that offset. The unterminated rest is never kept or
    parsed, even when it is valid JSON: the next read starts again at the committed offset, so a record counts only
    once its newline lands, a multibyte character split across writes is whole when decoded, and the torn-tail cut
    read_replies makes at a new launch (back to the committed offset, then new lines appended) reads as the new lines,
    never as a stale prefix glued to them (Codex's re-check of revision 2, finding 6). It resets (records dropped,
    read again from 0) when the file was replaced, shrank below the offset, or no longer holds the kept bytes before
    the offset (a rewrite in place, truncated and grown again). A line counts only when it decodes as UTF-8 and
    json.loads gives a dict with its required keys and types; any other line is counted in unreadable, never shown."""

    def __init__(self, path, fields):
        self.path, self.fields = Path(path), fields
        self.identity, self.offset, self.kept, self.unreadable = None, 0, b"", 0

    def read(self):
        """(reset, records): whether the reader started over, and the records committed since the last read."""
        try:
            f = open(self.path, "rb")
        except FileNotFoundError:
            reset = self.identity is not None
            self.identity, self.offset, self.kept, self.unreadable = None, 0, b"", 0
            return reset, []
        with f:
            st = os.fstat(f.fileno())
            identity, reset = (st.st_dev, st.st_ino), False
            if self.identity is not None and (identity != self.identity or st.st_size < self.offset):
                reset = True
            elif self.offset:
                f.seek(self.offset - len(self.kept))
                reset = f.read(len(self.kept)) != self.kept
            if reset or self.identity is None:
                self.offset, self.kept, self.unreadable = 0, b"", 0
            self.identity = identity
            f.seek(self.offset)
            data = f.read()
        end = data.rfind(b"\n") + 1
        self.offset += end
        self.kept = (self.kept + data[:end])[-PREFIX_BYTES:]
        out = []
        for line in data[:end].split(b"\n")[:-1]:
            if not line.strip():
                continue
            try:
                rec = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError):
                rec = None
            if valid_line(rec, self.fields):
                out.append(rec)
            else:
                self.unreadable += 1
        return reset, out


def read_followup(folder):
    """An episode's followup.json, read defensively (Claude Code writes it during the dialogue and Codex only after
    the errand, neither atomically): ("none", None) when absent, ("not ready", None) when unreadable or not a dict,
    ("ready", reply) when it holds a reply string, ("not ready", None) until it does."""
    try:
        with open(Path(folder) / "followup.json", "rb") as f:
            data = json.loads(f.read().decode("utf-8"))
    except FileNotFoundError:
        return "none", None
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return "not ready", None
    reply = data.get("reply") if isinstance(data, dict) else None
    return ("ready", reply) if isinstance(reply, str) else ("not ready", None)


def folder_identity(folder):
    """An attempt's identity: the episode folder's and world.json's (st_dev, st_ino), and world.json's st_mtime_ns,
    or None before the world exists. start_episode recreates both for each attempt and writes world.json once, so
    its modification time tells two attempts apart even when the file system reuses the inode numbers (Linux often
    does, at once). world.json is only checked, never opened."""
    try:
        a, b = os.stat(folder), os.stat(Path(folder) / "world.json")
    except OSError:
        return None
    return a.st_dev, a.st_ino, b.st_dev, b.st_ino, b.st_mtime_ns


# ---- the provisional scorecard (4.4) ---------------------------------------------------------------------------------
class Provisional:
    """One scoring worker per run. trigger(snapshot) keeps only the newest pending snapshot (triggers coalesce), and
    at most one scoring starts per PROVISIONAL_SECONDS. A snapshot is frozen: {"done": the committed replies,
    "facts": each committed errand's episode_facts}, copied by the observer at one instant (Codex's re-check of
    revision 2, finding 4). score(snapshot) is the final scorer over the full plan with those alone, reading no file,
    and rows(card, final) turns a card into the page's rows. finalize(card) publishes the final card, handed over in
    memory, at once and stops the worker; a provisional result that finishes after it is discarded. latest() is the
    one published object: {snapshot_id, done, total, final, rows} from one snapshot, or a fixed sentence when scoring
    failed."""

    def __init__(self, score, rows, total, interval=PROVISIONAL_SECONDS, clock=time.monotonic):
        self.score, self.rows, self.total, self.interval, self.clock = score, rows, total, interval, clock
        self.cond = threading.Condition()
        self.pending, self.published, self.final, self.running = None, None, False, 0
        self.last_start, self.thread, self.scorings = None, None, 0

    def trigger(self, snapshot_id, snapshot, plan_keys):
        """Ask for a scoring of this frozen snapshot; a newer one replaces a pending one."""
        with self.cond:
            if self.final:
                return
            self.pending = (snapshot_id, snapshot, sum(1 for k in snapshot["done"] if k in plan_keys))
            if self.thread is None:
                self.thread = threading.Thread(target=self.work, name="probatio-provisional", daemon=True)
                self.thread.start()
            self.cond.notify_all()

    def work(self):
        while True:
            with self.cond:
                while not self.final and (self.pending is None or self.wait_left() > 0):
                    self.cond.wait(timeout=None if self.pending is None else self.wait_left())
                if self.final:
                    return
                snapshot_id, snapshot, n = self.pending
                self.pending, self.last_start, self.running = None, self.clock(), self.running + 1
                self.scorings += 1
            try:
                result = {"rows": self.rows(shown_view(self.score(snapshot)), False)}     # the card's text displayed
            except Exception as e:                      # shown as a fixed sentence, never the message
                result = {"rows": None, "problem": f"the provisional scorecard could not be computed yet "
                                                   f"({type(e).__name__})"}
            with self.cond:
                self.running -= 1
                if self.final:                          # the final card came first: this result is discarded
                    continue
                if not self.published or self.published["snapshot_id"] < snapshot_id:
                    self.published = dict(result, snapshot_id=snapshot_id, done=n, total=self.total, final=False)

    def wait_left(self):
        return 0 if self.last_start is None else self.interval - (self.clock() - self.last_start)

    def finalize(self, card):
        """Publish the final card now, whatever the throttle, and stop provisional scoring."""
        rows = self.rows(shown_view(card), True)                # the card's text displayed, as the result files show it
        with self.cond:
            self.final = True
            self.published = {"snapshot_id": None, "done": self.total, "total": self.total, "final": True,
                              "rows": rows}
            self.cond.notify_all()

    def latest(self):
        with self.cond:
            return dict(self.published) if self.published else None


def interval(ci):
    return [float(ci[0]), float(ci[1])] if ci else None


def v4_rows(card, final):
    """A v4 card's page rows: each class in each condition, its score (0 to 100) and 95% interval, or why there is
    none ("not run", "insufficient so far" while provisional). A tier that plans fewer classes (TIER_CLASSES: quick
    plans I and II) leads with the scorecard's own scope line (quick_card_line), and a class it does not plan reads
    as the scorecard's row does (NOT_RUN_IN: "not run in a quick run"), with no mark."""
    tier, conds = card.get("tier"), card["conditions"]
    planned = TIER_CLASSES.get(tier, V4_CLASSES)
    rows = [{"line": quick_card_line(card)}] if tier in TIER_CLASSES else []
    for cond, rec in conds.items():
        for k in V4_CLASSES:
            cls = rec["classes"][k]
            note = None
            if cls["status"] == "not_run" and cls.get("reason"):
                note = cls["reason"]
            elif k not in planned:
                note = NOT_RUN_IN.format(tier=tier)
            elif cls["status"] == "not_run":
                note = "not run"
            elif cls["score"] is None:
                note = "insufficient" if final else "insufficient so far"
            rows.append({"group": CONDITION_NAMES[cond] if len(conds) > 1 else None, "label": f"Class {k}",
                         "sublabel": CLASS_NAMES[k], "value": None if note else float(cls["score"]),
                         "shown": None if note else whole(cls["score"]), "ci": None if note else interval(cls["ci"]),
                         "note": note})
    return rows


def ordo_rows(ordo):
    """A cross-type card's page rows, for ordo's card: the names the user gave first (name_rows), then one strip per
    pair under each row that ran, its index around the 50 line; Class III only where ordo reads it as valid. A quick
    card has only its role-free rows and leads with ordo's own line naming the rest (ordo.QUICK_CARD_LINE), as its
    scorecard does."""
    def rows_of(card, final):
        rows, conds = ([{"line": ordo.QUICK_CARD_LINE}] if card.get("tier") in TIER_CLASSES else []), card["conditions"]
        rows += name_rows(ordo, card, final)
        for cond, rec in conds.items():
            for row in rec["rows"]:
                if row.get("ordering") == "not run":
                    continue
                for pn, p in row["pairs"].items():
                    note = None
                    if p.get("value") is None:
                        note = "insufficient" if final else "insufficient so far"
                    elif row["id"] == "role-free.III" and p.get("validity") != "valid":
                        note = ordo.iii_validity_text(p.get("validity"))
                    rows.append({"group": (CONDITION_NAMES[cond] + " · " if len(conds) > 1 else "") + row["label"],
                                 "label": " vs ".join(pn.split(":")), "sublabel": None,      # as the table heads it
                                 "value": None if note else float(p["value"]),
                                 "shown": None if note else whole(p["value"]),
                                 "ci": None if note else interval(p.get("ci")), "note": note, "midline": True})
        return rows
    return rows_of


def name_rows(ordo, card, final):
    """The page's rows for the names the user gave, from the card's named effects and with the scorecard's own words
    and cells (ordo's class_leads, opponent_lines, POSITION_GLOSS, quick_names_line, name_cells, roster_words): what
    each class's numbers are, what position-driven means when a name lost a part to it, and at quick what a name's
    Class I measures, as lines; then per condition one row per name and class, its dot at the name's index (no 50
    line), its twin's index beside the class, the difference with its interval as the value; an insufficient name
    reads "insufficient" (so far, while provisional) with no mark and, opponent type by opponent type, what passed
    and why the rest did not. None when the run scored no named target."""
    if not ordo.has_named(card):
        return []
    conds, words = card["conditions"], ordo.roster_words(card)
    lines = ordo.class_leads(card) + ([ordo.POSITION_GLOSS.format(top=ordo.POSITION_BAND[1])]
                                      if ordo.position_named(card) else [])
    rows = [{"line": line} for line in lines + [ordo.quick_names_line(card)] if line]
    for cond, rec in conds.items():
        for n, klass, s in ordo.named_rows(rec):
            name, twin, difference = ordo.name_cells(s)
            note = None
            if name == "insufficient":
                note = ("insufficient" if final else "insufficient so far") + ": " + \
                    "; ".join(ordo.opponent_lines(s, klass, words))
            rows.append({"group": (CONDITION_NAMES[cond] + " · " if len(conds) > 1 else "") + ordo.NAMES_HEADING,
                         "label": capped(f"{n['target']} ({n['type']})"),      # the whole label within the cap
                         "sublabel": f"Class {klass}" + ("" if note else
                                                         f": {name}, {words['one'].format(type=n['type'])} {twin}"),
                         "value": None if note else float(s["levels"]["target_index"]), "shown": difference or None,
                         "ci": None, "note": note, "midline": False})
    return rows


# ---- the observer of one run (4.3, 4.5) ------------------------------------------------------------------------------
def task_of(call):
    """The task an errand belongs to: every errand of one world task, whoever it is for (v4's target and control,
    ordo's three beneficiaries or an authority cell's group), shares it."""
    m = call["meta"]
    return (call["condition"], m.get("cell"), m.get("frame"), m.get("deployer"), m.get("task", m.get("seed")),
            m.get("set"), m.get("named"))


class Card:
    """One errand's card: its attempt (the folder's identity), its record read through CommittedLines, and its
    report's state."""

    def __init__(self, call, folder):
        self.call, self.folder = call, folder
        self.identity, self.attempt, self.calls, self.lines = None, 0, [], None
        self.report = ("none", None)

    def refresh(self):
        identity = folder_identity(self.folder)
        if identity is None:
            return
        if identity != self.identity:                                   # a new attempt: start the card over
            self.identity, self.attempt, self.calls = identity, self.attempt + 1, []
            self.lines = CommittedLines(self.folder / "record.jsonl", RECORD_FIELDS)
            self.report = ("none", None)
        reset, new = self.lines.read()
        if reset:
            self.calls = []
        self.calls += [x for x in new if x["kind"] == "call"]
        if self.report[0] != "ready":
            self.report = read_followup(self.folder)


class RunView:
    """What the page knows of one run, read from its folder (4.3's exhaustive list) and from memory. The plan, the
    run facts and the run's kind (feed.Kind: its words and its names) come from the caller; the progress from the
    live line; the committed replies from replies.jsonl; the newest task's errands, and an opened errand outside it,
    from their record.jsonl, followup.json and world.json's identity. Every read is caught and becomes a fixed
    sentence naming the call and the exception's class. state(query) returns only the whitelisted fields: the feed's
    entries (feed.SHOWN, newest first, or those before the page's ?before=), the newest task's errands as entries, and
    an entry's opened view only when the page asks for it (?open=), every free text redacted and capped (the live-feed
    ruling, V1 to V3, V10, V12)."""

    def __init__(self, name, out, calls, facts, kind, redactor, line=None, provisional=None,
                 clock=time.monotonic, now=time.localtime):
        self.name, self.out, self.calls, self.facts, self.kind = name, Path(out), calls, facts, kind
        self.redact, self.line, self.provisional = redactor, line, provisional
        self.clock, self.now = clock, now
        self.by_key = {key(c): c for c in calls}
        self.ids = feed.call_ids(calls)
        self.lock = threading.Lock()
        self.status = "waiting"
        self.replies = CommittedLines(self.out / "replies.jsonl", REPLY_FIELDS)
        self.done, self.seen, self.facts_of = {}, {}, {}
        self.landed, self.seq = OrderedDict(), 0    # call key -> its landing number, in landing order
        self.retry = {}                 # call key -> (record, attempt): committed errands whose facts await a reread
        self.cards, self.task, self.problems = {}, None, OrderedDict()
        self.opened = OrderedDict()     # call key -> Card of an opened errand outside the newest task
        self.results = None             # the results page's name while the run is done and its page can be served
        self.page = results_page_of(kind)   # where in its folder that page is: fixed by the run's kind, here
        self.identity = None            # the folder's (device, inode), recorded when the page starts watching it
        self.took = None                # the run's elapsed seconds when it was scored (the line counts on after)
        self.check = PageCheck()        # its page as the card offers it, and its text files, once it is done
        self.snapshot_id, self.triggered, self.last_refresh = 0, -1, None

    # -- the results card (the results-card ruling and its design)
    def scored(self):
        """The run is scored (Watch.finish, VersusWatch.finish, under the lock): it is done, its time is kept as the
        live line shows it now (the page's progress shows that time from now on, D9), and its results page is asked
        of the pinned reader at once."""
        self.status, self.last_refresh = "finished", None
        self.took = self.line.snapshot()["elapsed_seconds"] if self.line is not None else None
        self.check_page(force=True)

    def check_page(self, force=False):
        """Once the run is done, ask the pinned reader whether its results page can be served (page_state), through
        the run's PageCheck: at most every CARD_RECHECK_SECONDS by the view's clock (force: now), failing closed, the
        text files listed at the first, forced, ask. results is the page's subpath while it can be served, else None.
        So the card's button and the page's route follow the folder: a page taken away after the run is no longer
        offered, and one that can be read only a moment after the run was scored is offered then. Under the lock."""
        if self.status != "finished":
            return
        state = self.check.ask(self.clock(), lambda: page_state(self.out, self.identity, self.page),
                               lambda: text_files(self.out, self.page), force)
        self.results = "/".join(self.page) if state == "ready" else None

    @property
    def page_now(self):
        """The page's state as the card shows it (PageCheck.ask's last answer), None before the run is done."""
        return self.check.state

    def card_facts(self):
        """The facts the card is built from: the page's own (run_facts' list, 4.3's), and for a cross-type run the
        types it compares, which its title names as the results page does, read from the run's kind (feed.CrossKind's
        types, its rosters' own), never a new field of the page's facts."""
        types, title = getattr(self.kind, "types", None), getattr(self, "title", None)
        return dict(self.facts, **({"types": [display(t) for t in types]} if types else {}),
                    **({"title": title} if title else {}))

    def card_of(self, index, others=()):
        """(card, bar) of this run, the command's run number index, for /state: its results card (card_for: None
        until it is done) with the command's other runs (others, other_run's), and the bar for a reader further down
        the page (results_bar), only while its page can be served; each None until then."""
        with self.lock:
            done = self.status == "finished"
            link = {"run": index}
            files = (self.check.files or ()) if done else ()      # listed once, when the run was scored (F6)
            card = card_for(done, self.card_facts(), self.took, self.page_now, link,
                            display(os.path.abspath(self.out)), files, others)
            return card, (results_bar(link) if done and self.page_now == "ready" else None)

    def summary_of(self, index):
        """This run as another run's card lists it (other_run): its facts and time, its status in the page's words
        and its own link while its page can be served."""
        with self.lock:
            ready = self.status == "finished" and self.page_now == "ready"
            return other_run(self.card_facts(), self.took, other_words(self.status, self.page_now),
                             {"run": index} if ready else None)

    def shown_progress(self):
        """The progress for /state: the live line's, with the run's own time once it is scored (D9)."""
        progress = self.progress()
        return progress if self.took is None else dict(progress, elapsed_seconds=self.took)

    # -- reading
    def problem(self, what, error, slot="read"):
        """An observer failure as the fixed sentence of 4.3: no message, path or traceback. It is kept under (slot,
        what), the newest PROBLEMS_SHOWN of them, until the same read succeeds (resolved)."""
        self.problems.pop((slot, what), None)
        self.problems[(slot, what)] = f"could not read {what} yet ({type(error).__name__})"
        while len(self.problems) > PROBLEMS_SHOWN:
            self.problems.popitem(last=False)

    def resolved(self, what, slot="read"):
        """A read that failed before has succeeded: its sentence goes."""
        self.problems.pop((slot, what), None)

    def refresh(self):
        """Read what was committed since the last refresh (at most every REFRESH_SECONDS), and ask for a provisional
        scoring when the committed records changed."""
        with self.lock:
            if self.status == "waiting":
                return
            now = self.clock()
            if self.last_refresh is not None and now - self.last_refresh < REFRESH_SECONDS:
                return
            self.last_refresh = now
            try:
                self.read_replies()
                self.resolved("replies.jsonl")
            except Exception as e:
                self.problem("replies.jsonl", e)
            try:
                self.read_errands()
                self.resolved("the errands")
            except Exception as e:
                self.problem("the errands", e)
            if self.provisional is not None and self.snapshot_id != self.triggered:
                self.triggered = self.snapshot_id
                snapshot = {"done": dict(self.done), "facts": dict(self.facts_of)}
                self.provisional.trigger(self.snapshot_id, snapshot, self.by_key)

    def land(self, k):
        """A call's record has entered done: it takes the next landing number (a retried call its new one, so its one
        entry moves to the top of the feed)."""
        self.seq += 1
        self.landed.pop(k, None)
        self.landed[k] = self.seq

    def read_replies(self):
        reset, new = self.replies.read()
        if reset:
            self.done, self.seen, self.facts_of, self.retry = {}, {}, {}, {}
            self.landed.clear()
            self.snapshot_id += 1
        queued = list(self.retry.items())               # the failures of earlier refreshes, before this one's lines
        stamp = time.strftime("%H:%M:%S", self.now())
        for rec in new:
            k = key(rec)
            if k not in self.by_key:
                continue
            self.seen[k] = stamp
            if self.by_key[k].get("episode"):
                self.retry.pop(k, None)                 # a newer committed record replaces a queued one
                self.capture(self.by_key[k], rec, folder_identity(episode_dir(self.out, self.by_key[k])))
            else:
                self.done[k] = rec
                self.snapshot_id += 1
                self.land(k)
        for k, (rec, attempt) in queued:
            if self.retry.get(k) == (rec, attempt):
                self.retry_capture(self.by_key[k], rec, attempt)

    def capture(self, call, rec, attempt):
        """A committed errand's episode_facts, read once when its reply line lands: both CLIs have written its record
        and report by then (Codex's report leg runs inside the call), so they are final, and every later snapshot
        holds this copy. The record and its facts enter done and facts_of together, as one new snapshot, and the
        errand lands in the feed. A failed read keeps the errand out of both (a missing unit until then), says so,
        and queues it in retry under its call key with the committed record and the attempt (folder_identity) it
        belongs to, so a later refresh reads it again (Codex's final check of the round, finding 4)."""
        k = key(call)
        try:
            facts = episode_facts(call, rec, self.out)
        except Exception as e:
            self.done.pop(k, None)
            self.facts_of.pop(k, None)
            self.landed.pop(k, None)
            self.retry[k] = (rec, attempt)
            self.problem(call["id"], e, "facts")
            return
        self.retry.pop(k, None)
        self.done[k], self.facts_of[k] = rec, facts
        self.snapshot_id += 1
        self.land(k)
        self.resolved(call["id"], "facts")

    def retry_capture(self, call, rec, attempt):
        """One queued capture read again. If the errand's folder now holds another attempt than the one its record
        came from, the queued record is stale and dropped: that attempt's own record will be committed and captured
        when its line lands."""
        now = folder_identity(episode_dir(self.out, call))
        if attempt is not None and now is not None and now != attempt:
            self.retry.pop(key(call), None)
            self.resolved(call["id"], "facts")
            return
        self.capture(call, rec, attempt if attempt is not None else now)

    def read_errands(self):
        """The newest task: the one holding the most recently started errand that has not finished, else the one
        shown before, else (a run that has nothing running and showed nothing yet, such as a resumed finished one)
        the most recently started of all. Its errands' cards are refreshed; a card whose attempt changed starts
        over. When the newest task changes, the cards of every other task go: nothing refreshes them any more, so the
        page reads such an errand's committed outcome, or a card of its own while it is opened (log_of)."""
        candidates = [c for c in self.calls if c.get("episode") and key(c) not in self.done]
        if not candidates and self.task is None:        # nothing running and nothing shown yet: the newest finished
            candidates = [c for c in self.calls if c.get("episode")]
        newest, newest_t = None, -1
        for c in candidates:
            try:
                ident = folder_identity(episode_dir(self.out, c))
            except Exception as e:
                self.problem(c["id"], e, "folder")
                continue
            if ident is not None and ident[4] > newest_t:
                newest, newest_t = c, ident[4]
        if newest is not None:
            self.task = task_of(newest)
        if self.task is None:
            return
        for k in [k for k in self.cards if task_of(self.by_key[k]) != self.task]:
            del self.cards[k]
        for c in self.calls:
            if c.get("episode") and task_of(c) == self.task:
                card = self.cards.setdefault(key(c), Card(c, episode_dir(self.out, c)))
                try:
                    card.refresh()
                    self.resolved(c["id"], "card")
                except Exception as e:
                    self.problem(c["id"], e, "card")

    def log_of(self, call, opened):
        """An errand's record lines and report as its Card read them: the newest task's card; for an opened errand
        outside that task, a card of its own, kept while the page keeps it open (feed.OPEN_MAX of them, the least
        recently asked dropped) and read incrementally like the newest task's; else None (its compact line reads its
        committed record's outcome)."""
        k = key(call)
        card = self.cards.get(k)
        if card is None and opened:
            card = self.opened.pop(k, None) or Card(call, episode_dir(self.out, call))
            self.opened[k] = card
            while len(self.opened) > feed.OPEN_MAX:
                self.opened.popitem(last=False)
            try:
                card.refresh()
                self.resolved(call["id"], "card")
            except Exception as e:
                self.problem(call["id"], e, "card")
        return (card.calls, card.report) if card is not None else None

    # -- what the page shows
    def entry_of(self, k, opened, whole):
        """One call of the plan as the feed's entry (feed.entry), redacted and capped by this page's redactor."""
        call, cid = self.by_key[k], self.ids[k]
        is_open = cid in opened
        log = self.log_of(call, is_open) if call.get("episode") else None
        return feed.entry(self.kind, call, cid, self.done.get(k), log, is_open, self.landed.get(k), self.seen.get(k),
                          self.redact.brief, whole == cid)

    def feed_view(self, opened, before, whole):
        """The feed: one page of entries (feed.window over the landing numbers), newest first, every landed call
        counted, and the first landing number, below which there is nothing earlier to ask for; and kept, newest
        first, each landed entry the page has open (?open=) that this page does not hold, with its opened view, so an
        entry the reader opened stays on the page and keeps being refreshed however far the window has moved (V11).
        kept holds at most feed.OPEN_MAX entries, the bound on ?open=."""
        by_seq = {s: k for k, s in self.landed.items()}
        seqs = feed.window(list(by_seq), before)
        held, of_id = set(seqs), {i: k for k, i in self.ids.items()}
        kept = sorted((self.landed[of_id[i]] for i in opened if of_id.get(i) in self.landed
                       and self.landed[of_id[i]] not in held), reverse=True)
        return {"entries": [self.entry_of(by_seq[s], opened, whole) for s in seqs], "landed": len(self.landed),
                "first": next(iter(by_seq), None), "kept": [self.entry_of(by_seq[s], opened, whole) for s in kept]}

    def now_view(self, opened, whole):
        """Errands now: the newest task's errands as entries, with its heading (task, cell, frame, condition)."""
        if self.task is None:
            return None
        cond, cell, frame, deployer, task, _, named = self.task
        heading = [f"task {task}", CELL_WORDS.get(cell, cell or "")]
        if frame:
            heading.append(frame + (f", deployed by the {deployer}" if frame == "authority" and deployer else ""))
        if named:
            heading.append("named target")
        if len({c["condition"] for c in self.calls}) > 1:
            heading.append(CONDITION_NAMES.get(cond, cond))
        keys = [key(c) for c in self.calls if c.get("episode") and task_of(c) == self.task]
        return {"heading": " · ".join(h for h in heading if h), "entries": [self.entry_of(k, opened, whole)
                                                                           for k in keys]}

    def progress(self):
        if self.line is not None:
            return self.line.snapshot()
        valid = sum(1 for k, r in self.done.items() if not r.get("error") and
                    (self.by_key[k].get("episode") or r.get("outcome") is not None))
        errors = sum(1 for r in self.done.values() if r.get("error"))
        return {"done": len(self.done), "total": len(self.calls), "valid": valid,
                "abstained": len(self.done) - valid - errors, "errors": errors, "elapsed_seconds": None,
                "left_seconds": None, "quota": None, "finished": self.status != "running"}

    def unreadable(self):
        """The committed lines this view could not read: replies.jsonl's and every errand record's it reads."""
        return self.replies.unreadable + sum(c.lines.unreadable for c in [*self.cards.values(), *self.opened.values()]
                                             if c.lines)

    def entries(self, query):
        """(feed, now) for one /state request: the page's ?open=, ?before= and ?all= read by feed.query_of."""
        opened, before, whole = feed.query_of(query, len(self.calls))
        live_now = self.now_view(opened, whole) if self.status != "waiting" else None
        return self.feed_view(opened, before, whole), live_now

    def state(self, query=None):
        """The whitelisted state of this run for /state."""
        self.refresh()
        with self.lock:
            self.check_page()
            card = self.provisional.latest() if self.provisional is not None else None
            entries, live_now = self.entries(query)
            return {"name": self.redact.brief(display(self.name), 120), "status": self.status, "facts": self.facts,
                    "progress": self.shown_progress(), "scorecard": card, "results": self.results,
                    "now": live_now, "feed": entries, "unreadable": self.unreadable(),
                    "problems": list(self.problems.values())}


def args_line(args):
    """A tool call's arguments as one line, k=v and lists joined by commas (v3's short(), uncut: the cut comes after
    redaction)."""
    if not isinstance(args, dict):
        return "" if args is None else str(args)
    return ", ".join(f"{k}={', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in args.items())


# ---- serving the page (4.2, 4.3) -------------------------------------------------------------------------------------
def inline_hashes(page, tag):
    """The CSP source list for a page's inline <tag> blocks: 'sha256-…' of each block's exact text."""
    blocks = re.findall(rf"<{tag}>(.*?)</{tag}>", page, re.S)
    return " ".join("'sha256-" + base64.b64encode(hashlib.sha256(b.encode("utf-8")).digest()).decode() + "'"
                    for b in blocks)


def content_policy(page):
    """The page's Content-Security-Policy: nothing by default; its own inline script and style by hash; fetches to
    its own origin only; no frames, forms, base or plugins."""
    return (f"default-src 'none'; script-src {inline_hashes(page, 'script')}; "
            f"style-src {inline_hashes(page, 'style')}; "
            "connect-src 'self'; img-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


SECURITY_HEADERS = (("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer"),
                    ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"),
                    ("Cross-Origin-Resource-Policy", "same-origin"))
TOKEN_HEADER = "X-Probatio-Token"   # a POST carries the token here too: another origin cannot set it without CORS
MAX_BODY = 65536                    # bytes a POST may carry
DECIMAL = re.compile(r"[0-9]+")     # a Content-Length a POST may give: ASCII digits only (str.isdigit takes "²")
# The version a request line may end in: HTTP/ and two numbers of 1 to 10 plain ASCII digits, what Python 3.11 and
# later require of it, judged by the pages themselves so every Python reads a version alike (parse_request)
HTTP_VERSION = re.compile(r"HTTP/([0-9]{1,10})\.([0-9]{1,10})")
# How deep a POST's JSON text may nest its arrays and objects, counted on the text (json_too_deep), not on the value
# it parses to. The two depths are equal unless an object repeats a key: the parser keeps the last value, so a deeper
# earlier one is nesting in the text alone ({"x":[0],"x":0} nests 2 in its text, 1 once parsed), and the text's depth
# is never less than the value's. ui.py's page nests four at most (the request, its form, the rosters, one type's
# lists of names) and sends no repeated key; 16 leaves room for that to grow, and stays far below the depth at which
# json.loads (about a thousand on Python 3.9 to 3.12, far more on 3.13) or any walk of the request recurses too deep.
MAX_JSON_DEPTH = 16
JSON_MARKS = re.compile(rb'["\\\[\]{}]')     # the bytes json_too_deep reads: quotes, backslashes, brackets, braces
LAST_RESORT = object()               # what a request's attempt returns once it has answered a failure with 500


def json_too_deep(data, bound=MAX_JSON_DEPTH):
    """Whether data, a JSON text as bytes, nests more than bound arrays and objects in its text, read from its bytes
    before anything parses it, so the refusal holds on every Python whatever its parser accepts. Brackets inside a
    string are not nesting; a backslash in a string escapes the byte after it. UTF-8 puts no ASCII byte inside a
    multi-byte character, so the marks are found byte by byte. A text that is not JSON is the parser's to refuse.

    The depth is the text's, never less than the parsed value's, and the same unless an object repeats a key: the
    parser keeps the key's last value, so a deeper earlier one nests in the text alone ({"x":[0],"x":0} nests 2 in
    its text and 1 once parsed). Such a text past the bound is refused though its parsed value is within it; ui.py's
    page sends no repeated key."""
    depth, in_string, escaped = 0, False, -1
    for m in JSON_MARKS.finditer(data):
        at, mark = m.start(), data[m.start()]
        if at == escaped:
            continue
        if in_string:
            if mark == 0x5C:                                  # \ : the next byte is escaped
                escaped = at + 1
            elif mark == 0x22:                                # " : the string ends
                in_string = False
        elif mark == 0x22:
            in_string = True
        elif mark in (0x5B, 0x7B):                            # [ or {
            depth += 1
            if depth > bound:
                return True
        elif mark in (0x5D, 0x7D):                            # ] or }
            depth -= 1
    return False


# What a reply is made from leaves the request's attempt as plain built-in values only: the body plain bytes, each
# header plain str, each copied inside the guard (builtin_bytes, builtin_str) from a result taken apart once. A
# subclass of bytes, str or tuple can override its own methods (its length, its iteration, its formatting), and a
# reply made from it after the guard would run them there, outside it; plain built-ins' methods are the interpreter's
# own. So whatever the callback returned runs its own code, if any, inside the guard, where raising is the last
# resort's 500. (These are not probatio's plain_text, which makes text plain to display.)
def file_answer(served):
    """A file route's result turned into what its reply is made from: None (404), or the new tuple (body, content
    type, policy) of plain values, the body plain bytes and the other two plain str header values (text latin-1 can
    carry, with no CR or LF, as send_header writes them). The result must be None or a tuple of three, taken apart
    exactly once (a subclass's own iteration runs then); each part is then copied to its plain type, and only the
    copies are used. serve_page calls it inside the request's attempt, so a result that is not such a tuple, a part
    that is not bytes or text or does not copy to the plain type, and any part's own code that raises while it is
    taken apart or copied, all raise there and are the last resort's 500 with its traceback."""
    if served is None:
        return None
    if not isinstance(served, tuple):
        raise TypeError(f"a file route returns None or (body, content type, policy), not {type(served).__name__}")
    if type(served) is tuple and len(served) != 3:
        raise TypeError(f"a file route returns None or (body, content type, policy), not tuple of length "
                        f"{len(served)}")
    body, kind, own = served                       # once: a tuple subclass's own iteration runs here, in the guard
    body = builtin_bytes(body, "a file route's body")
    kind, own = builtin_str(kind, "a file route's content type"), builtin_str(own, "a file route's policy")
    for part, value in (("content type", kind), ("policy", own)):
        if not header_value(value):
            raise TypeError(f"a file route's {part} is a header value (latin-1 text, no CR or LF), not {value!r:.80}")
    return body, kind, own


def builtin_bytes(value, what):
    """value, bytes or a subclass of it, as plain bytes: bytes(value), where a subclass's own conversion runs, then
    checked, since that conversion may hand back a subclass again. Not bytes, or not plain once copied, is TypeError."""
    if not isinstance(value, bytes):
        raise TypeError(f"{what} is bytes, not {type(value).__name__}")
    copy = bytes(value)
    if type(copy) is not bytes:
        raise TypeError(f"{what} does not copy to plain bytes (bytes() gave {type(copy).__name__})")
    return copy


def builtin_str(value, what):
    """value, str or a subclass of it, as a plain str: str(value), where a subclass's own __str__ runs, then checked,
    since __str__ may hand back a subclass again. Not str, or not plain once copied, is TypeError."""
    if not isinstance(value, str):
        raise TypeError(f"{what} is text, not {type(value).__name__}")
    copy = str(value)
    if type(copy) is not str:
        raise TypeError(f"{what} does not copy to plain text (str() gave {type(copy).__name__})")
    return copy


def json_reply(value, redactor):
    """value as a JSON reply's body: redacted whole (redact_all), written as ASCII JSON and encoded to UTF-8, all
    inside the request's attempt, so anything in value whose own code runs while it is written runs in the guard;
    the body comes out as plain bytes (builtin_bytes), the one thing the reply is then made from."""
    return builtin_bytes(json.dumps(redact_all(value, redactor), ensure_ascii=True).encode("utf-8"), "a JSON reply")


def header_value(value):
    """Whether value is one a response header can carry as send_header writes it: text that encodes to latin-1,
    with no CR or LF (either would end the header and start another)."""
    if not isinstance(value, str) or "\r" in value or "\n" in value:
        return False
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return True


def serve_page(page, state, redactor=None, port=0, actions=None, files=None, query_state=False):
    """Serve one page and its state on 127.0.0.1 from a background thread for the life of the process; returns the
    server, with .url (holding the token) and .token. A GET, and a POST to a page with actions once its length has
    passed (below), must have an origin-form target, a path that begins with a slash, which is all the pages send (an
    absolute URL, an authority or an asterisk is refused before it is split, on every Python alike; a target beginning
    with two slashes or more is first read as one, as http.server itself reads it since gh-87389), and must carry the
    token (?t=, compared with hmac.compare_digest; a target urlsplit cannot split carries none) and a Host of
    127.0.0.1:PORT or localhost:PORT (a DNS-rebinding page has neither), else 403;
    GET serves "/" (the page) and "/state" (state() as JSON, redacted again whole as a last pass) and nothing else
    (404); PUT, DELETE, PATCH, OPTIONS, HEAD, TRACE and CONNECT are 405 whatever their target, before any check (a
    method http.server has no handler for, an unknown or lower-case one, is its own 501). None of these refusals
    reaches a route. Every response carries the security headers and the page's CSP, which allows its
    inline script and style by hash alone. No file is served and nothing is logged. .redactor (which knows the token)
    is there for a state that must redact a field before cutting it (v3's view_state).

    actions, only for a page that changes something (ui.py's), maps each further path to a function of the request's
    JSON object that returns a JSON reply, redacted as /state is. Those paths take POST alone (GET is 405 there, and
    POST anywhere else 404 or 405), and a POST must also carry the token in the TOKEN_HEADER header and no Origin but
    the page's own, else 403 (a cross-site page can send neither header nor body to it: a custom header needs a CORS
    preflight, and OPTIONS is 405), with a JSON object of at most MAX_BODY bytes as application/json (else 411 for a
    Content-Length that is not ASCII decimal digits, or 413, before the body is read; 415, or 400 for a body whose
    text nests deeper than MAX_JSON_DEPTH, read from its bytes before any parse, or that is not a JSON object, after). A
    refused request reaches no action. Without actions, POST is 405 like every other method but GET, before any check
    and whatever its target, as before.


    files, only for a page that serves a file it chose itself (ui.py's results page), maps each further path to a
    function of the request's query ({name: [values]}, the token's own checks passed first) that returns (body bytes,
    content type, Content-Security-Policy) or None for 404. Such a path takes GET alone; its response carries the
    security headers with its own policy in place of the page's. Without files, nothing else is served, as before.

    query_state, only for a page whose state depends on what it asks (the --watch page's opened entries and earlier
    page of the feed), makes /state call state(query) with the request's query ({name: [values]}), which that state
    reads by digits alone (feed.query_of); without it state() takes no argument, as before.

    The pages speak HTTP/1.x, and every reply they write carries a status line, by construction: the handler never
    frames a reply as HTTP/0.9 (send_response_only), the one condition under which http.server leaves out the status
    line and the headers. The version is the pages' own rule, judged before http.server reads the line or any header
    (parse_request, HTTP_VERSION): a line of two words, with no version, is refused 505, and a line of three words or
    more must end in HTTP/ and two numbers of 1 to 10 plain ASCII digits, else 400 (a sign, an underscore, more than
    10 digits, lower case), with a major number of 1, else 505 (HTTP/0.9, HTTP/0.5, HTTP/2.0), so that HTTP/01.1 and
    HTTP/1.01 go on as HTTP/1.x and no refusal reaches a route, with the token or without, or echoes the line.
    http.server's own refusals of what is left carry its codes: 400 for a line it cannot read (a method alone, more
    than three words), 414 for a line over 65,536 bytes, 431 for a header line over 65,536 bytes or more than 100
    headers, 501 for a method it has no handler for. Every Python measured (3.9.25, 3.10.12, 3.11.15, 3.12.13 and
    3.13.15) answers every line alike, but for one phrase of http.server's own: 3.13 calls its 414 "URI Too Long",
    3.9 to 3.12 "Request-URI Too Long". A line of whitespace only (empty, spaces, a tab, 0x1c to 0x1f, NEL, NBSP) gets
    no reply, and the connection is closed. Beyond that, a request is left without a reply only when the failure
    happens while the reply itself is being written to the socket (the client going away, for example), which no
    server can answer, or by what is as far out of a server's reach: a request not yet read whole (a client that goes
    away, or holds back the rest of its body or its headers, while it is being read; the server waits, with no
    timeout) and a process out of memory or threads.

    What a reply is made from is made inside the request's attempt and leaves it as plain bytes
    and plain str only (json_reply for the state and an action, file_answer for a file route; the page's own bytes
    and policy are made plain once, here), so nothing after the guard runs code a result brought with it, and an
    attempt sends its 500 even when stderr cannot take the traceback. An exception raised while a
    state, a file route or an action makes its answer, or while that answer is turned into plain values (a file
    route's result that is not None or those three parts, or whose parts raise or do not copy to plain bytes and
    str), is the last resort: its traceback is printed to stderr, as socketserver prints one, and the request is
    answered 500 with a short plain message (attempt)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlsplit
    token = secrets.token_urlsafe(24)
    redactor = redactor or ViewRedactor(extra=(token,))
    if token not in redactor.values:
        redactor = ViewRedactor(extra=(token, *redactor.values))
    policy = builtin_str(content_policy(page), "the page's policy")                # plain built-ins, as every
    body_page = builtin_bytes(page.encode("utf-8"), "the page")                     # reply's parts are
    actions, files = dict(actions or {}), dict(files or {})
    methods = "GET, POST" if actions else "GET"

    class Page(BaseHTTPRequestHandler):
        server_version, sys_version = "probatio", ""
        error_message_format, error_content_type = "%(code)d %(message)s\n", "text/plain; charset=utf-8"

        def end_headers(self):
            own = getattr(self, "file_policy", None) or policy           # a served file's own policy, else the page's
            for name, value in SECURITY_HEADERS + (("Content-Security-Policy", own),):
                self.send_header(name, value)
            super().end_headers()

        def send_response_only(self, code, message=None):
            """http.server's own status line, never framed as HTTP/0.9. Every reply this server writes starts here,
            whoever sends it (http.server's refusals at any stage, send_error, the handler's own replies), and on every
            Python measured (3.9.25, 3.10.12, 3.11.15, 3.12.13, 3.13.15) http.server leaves out the status line, the
            headers and the blank line for one condition only: a request_version of HTTP/0.9, which it stores from a
            line's last word, or keeps as its default, before it refuses the request. That version is read here as
            HTTP/1.0, so every reply carries a status line and its headers."""
            if self.request_version == "HTTP/0.9":
                self.request_version = "HTTP/1.0"
            super().send_response_only(code, message)

        def parse_request(self):
            """The pages' own reading of the request line, before http.server reads it, so that only HTTP/1.x reaches
            the handler and every Python reads a version alike. The words are read as http.server reads them
            (iso-8859-1, the line's CR LF stripped, split on whitespace). No word, or one: left to http.server (no
            reply for a line of whitespace only; its 400 for a method alone). Exactly two words, HTTP/0.9's own form
            (no version): refused 505. Three words or more: the last must be HTTP_VERSION (HTTP/ and two numbers of
            1 to 10 plain ASCII digits), else refused 400, and its major number must be 1, else refused 505; so
            HTTP/01.1 and HTTP/1.01 go on, and a version with a sign, an underscore or more than 10 digits, which
            3.9 and 3.10's http.server would read, is 400 there as on 3.11 and later. Each refusal is send_error with
            no message of the pages' own, so nothing of the line is echoed; it is sent before any header is read (a
            client that sends its line and waits is answered), reaches no route, with the token or without, and the
            connection is closed. Then http.server's own reading, whose refusals carry a status line too
            (send_response_only).

            Then the one rewrite http.server makes of the target since gh-87389 (2022): a target that begins with
            two slashes or more is reduced to one, so //x/state is the path /x/state, never the host x, and
            //results.html is /results.html. Every Python measured has that fix and has reduced the target already,
            so there this changes nothing: macOS's own 3.9.6 (Apple's build carries it, as measured on the Mac mini)
            and 3.9.25, 3.10.12 and 3.13.15 here. It is for a Python whose http.server does not reduce it (CPython
            before gh-87389: 3.9 before its 2022 security releases, 3.10 before 3.10.6), so that every Python reads
            such a target alike."""
            line = str(self.raw_requestline, "iso-8859-1").rstrip("\r\n")
            words = line.split()
            refused = None
            if len(words) == 2:                        # no version: HTTP/0.9's own form
                refused = 505
            elif len(words) >= 3:                      # the version is the last word, as http.server takes it
                version = HTTP_VERSION.fullmatch(words[-1])
                refused = 400 if version is None else 505 if int(version.group(1)) != 1 else None
            if refused:                                # before http.server reads the line or any header
                self.command, self.requestline = None, line
                self.request_version, self.close_connection = "HTTP/1.0", True
                self.send_error(refused)
                return False
            if not super().parse_request():
                return False
            if self.path.startswith("//"):
                self.path = "/" + self.path.lstrip("/")
            return True

        def allowed(self):
            """The request's own proofs: an origin-form target, a Host naming this server and the token as its one t.
            The pages send only origin-form targets, a path that begins with a slash (after parse_request, one
            slash), so any other target, an absolute URL whatever its host (http://127.0.0.1:PORT/, http://[/,
            a://[x]/), an authority (127.0.0.1:PORT) or an asterisk, fails here before anything splits it: one rule
            on every Python, not whichever targets a given Python's urlsplit happens to refuse (macOS's 3.9.6
            accepts a://[x]/ where later ones raise). A target urlsplit still cannot split carries no token it can
            read, and fails too rather than raising before any answer. do_GET answers a failure 403, and so does the
            form's do_POST, after the length checks every POST meets first (411, 413). PUT, DELETE, PATCH, OPTIONS,
            HEAD, TRACE and CONNECT, and every POST to a page without actions, are 405 before this is asked; a
            method http.server has no handler for is its own 501 and never reaches the handler. The handlers split
            the target again only after this has passed."""
            if not self.path.startswith("/"):
                return False
            port_ = self.server.server_address[1]
            if self.headers.get("Host") not in (f"127.0.0.1:{port_}", f"localhost:{port_}"):
                return False
            try:
                query = urlsplit(self.path).query
            except ValueError:
                return False
            given = parse_qs(query).get("t") or [""]
            return len(given) == 1 and hmac.compare_digest(given[0].encode("utf-8"), token.encode("utf-8"))

        def do_GET(self):
            self.file_policy = None
            if not self.allowed():
                self.send_error(403)
                return
            path = urlsplit(self.path).path
            if path == "/":
                body, kind = body_page, "text/html; charset=utf-8"
            elif path == "/state":
                body = self.attempt(lambda: json_reply(                             # plain bytes, made in the guard
                    state(parse_qs(urlsplit(self.path).query)) if query_state else state(), redactor))
                if body is LAST_RESORT:
                    return
                kind = "application/json"
            elif path in actions:
                self.refuse("POST")
                return
            elif path in files:
                served = self.attempt(lambda: file_answer(files[path](parse_qs(urlsplit(self.path).query))))
                if served is LAST_RESORT:
                    return
                if served is None:
                    self.send_error(404)
                    return
                body, kind, self.file_policy = served          # file_answer's new tuple of plain bytes and str
            else:
                self.send_error(404)
                return
            self.send(body, kind)

        def attempt(self, make):
            """make(), what this request's answer is made from, as plain built-in values (json_reply, file_answer);
            or, when it raises, the last resort: the traceback printed to stderr as socketserver prints one, a 500
            with a short plain message, and LAST_RESORT returned, so an error in a state, a file route or an action
            is seen and the request is still answered. SystemExit is caught too: in a request's thread it would only
            end the thread and leave the request unanswered. A stderr that cannot take the traceback (closed, or a
            pipe nobody reads) does not stop the 500. The answer's bytes are written after, outside it, so a failure
            to write is never answered twice."""
            try:
                return make()
            except (Exception, SystemExit):
                try:
                    self.server.handle_error(self.request, self.client_address)
                except Exception:                  # nowhere left to print it; the 500 is still owed
                    pass
                self.file_policy = None
                self.send_error(500)
                return LAST_RESORT

        def send(self, body, kind):
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def same_site(self):
            """The POST's own proofs: the token in TOKEN_HEADER, and no Origin but this page's."""
            port_ = self.server.server_address[1]
            given = self.headers.get(TOKEN_HEADER) or ""
            return (self.headers.get("Origin") in (None, f"http://127.0.0.1:{port_}", f"http://localhost:{port_}")
                    and hmac.compare_digest(given.encode("utf-8"), token.encode("utf-8")))

        def do_POST(self):
            if not actions:
                self.refuse()
                return
            length = self.headers.get("Content-Length") or ""
            try:
                size = int(length) if DECIMAL.fullmatch(length) else None
            except ValueError:
                size = None
            if size is None:
                self.send_error(411)
                return
            if size > MAX_BODY:
                self.send_error(413)
                return
            data = self.rfile.read(size)           # read before any verdict: a refusal never leaves it unread
            if not (self.allowed() and self.same_site()):
                self.send_error(403)
                return
            path = urlsplit(self.path).path
            if path in ("/", "/state") or path in files:
                self.refuse("GET")
                return
            if path not in actions:
                self.send_error(404)
                return
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                self.send_error(415)
                return
            if json_too_deep(data):                # refused on its bytes, whatever this Python's json would parse
                self.send_error(400)
                return
            try:
                request = json.loads(data.decode("utf-8"))
            except (ValueError, RecursionError):   # UnicodeDecodeError is a ValueError
                request = None
            if not isinstance(request, dict):
                self.send_error(400)
                return
            reply = self.attempt(lambda: json_reply(actions[path](request), redactor))   # plain bytes, in the guard
            if reply is LAST_RESORT:
                return
            self.send(reply, "application/json")

        def refuse(self, allow=None):
            self.send_response(405)
            self.send_header("Allow", allow or methods)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = do_TRACE = do_CONNECT = refuse

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Page)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="probatio-page", daemon=True).start()
    server.token, server.redactor = token, redactor
    server.url = f"http://127.0.0.1:{server.server_address[1]}/?t={token}"
    return server


def redact_all(value, redactor):
    """Every string in a state, redacted (the last pass; each field was redacted whole before it was cut)."""
    if isinstance(value, str):
        return redactor(value)
    if isinstance(value, dict):
        return {k: redact_all(v, redactor) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_all(v, redactor) for v in value]
    return value


# ---- the page of a command's runs (4.2, 4.6) -------------------------------------------------------------------------
def run_facts(meta, kind, roster=None):
    """The run facts the page shows (4.3's list, and nothing else of run.json): agent, CLI version, model, effort,
    tier, recipe, memory mode, the target and control names (single-type) or the comparison (cross-type). Every text
    value is displayed (probatio's display) here, where the page's facts are built: run.json and the CLI's version
    line are outside text, and the page shows each as one token."""
    facts = {"agent": AGENT_NAMES.get(meta.get("agent"), meta.get("agent")), "cli": version_of(meta.get("cli")),
             "model": meta.get("model") or "", "effort": meta.get("effort") or "", "tier": meta.get("tier"),
             "recipe": meta.get("recipe"), "memory": meta.get("memory"), "kind": KIND_WORDS[kind]}
    if kind == "single" and roster:
        facts.update(target=roster["T"]["full"], target_type=roster["type"], control=roster["X"]["full"])
    elif kind == "cross":
        facts.update(compare=meta.get("compare"))
    return shown_facts(facts)


def shown_facts(facts):
    """facts with every text value displayed (probatio's display: one token, every unseen character escaped, cut at
    DISPLAY_CHARS): the one rule for the facts a --watch page shows, v4's (run_facts) and a versus run's
    (VersusWatch.start), since each is read from a record or a CLI and is outside text."""
    return {k: display(v) if isinstance(v, str) else v for k, v in facts.items()}


def shown_rosters(rosters):
    """{roster: {type: name}} with every name displayed (display), as roster_names displays v4's: the names a versus
    page counts its answers by."""
    return {arm: {t: display(name) for t, name in parties.items()} for arm, parties in rosters.items()}


def version_of(cli):
    """The CLI's version as the page prints it after the agent's name: the first version-shaped word of its
    --version line ("2.1.309 (Claude Code)" gives 2.1.309, "codex-cli 0.159.2" gives 0.159.2), else the line."""
    m = re.search(r"\d+(?:\.\d+)+\S*", cli or "")
    return m.group(0) if m else (cli or "")


def roster_names(roster=None, rosters=None):
    """Every entity key a run can answer with, mapped to its full name: v4's target, control, comparators and the
    anonymous requester, or ordo's rosters of each type."""
    records = []
    if roster:
        records += [roster["T"], roster["X"], *roster["K"]] + ([roster["anon"]] if isinstance(roster.get("anon"), dict)
                                                               else [])
    for r in (rosters or {}).values():
        for group in r.values():
            records += [x for x in (group if isinstance(group, list) else []) if isinstance(x, dict)]
    return {x["key"]: display(x["full"]) for x in records          # the name displayed, the key as the answers give it
            if isinstance(x.get("key"), str) and isinstance(x.get("full"), str)}


def announce(stream, url, boundary):
    """The page's address on stderr, once, before the first call, with its boundary sentence: the line ui.py reads the
    link from (WATCH_LINK there), the same for every family's page."""
    stream.write(f"  watch it live: {url}\n  {boundary}\n")
    stream.flush()


class Watch:
    """--watch for one command: the page, with a tab for each run of the command (names, in order), each a RunView
    from the moment its run starts (start) and given its final card the moment it is scored (finish). The URL is
    printed once on stderr, before the first call, with the boundary sentence; the page serves for the life of the
    process. Once a run is scored and wrote a results page, the page links it (results_page, V3)."""

    def __init__(self, names, stream=None):
        self.names, self.views, self.lock = [plain_label(n) for n in names], [], threading.Lock()
        self.server = serve_page(VIEW_LIVE, self.state, query_state=True, files={RESULTS_PATH: self.results_page})
        self.redactor = ViewRedactor(extra=(self.server.token,))
        announce(stream or sys.stderr, self.server.url, BOUNDARY)

    def start(self, name, out, calls, facts, kind, line=None, score=None, rows=None):
        """The next run of the command starts: its view, running, its entries drawn by kind (feed.Kind).
        score(done) and rows(card, final) give its provisional card (Provisional); without them the page shows no
        scorecard."""
        provisional = Provisional(score, rows, len(calls)) if score and rows else None
        view = RunView(name, out, calls, facts, kind, self.redactor, line, provisional)
        view.status, view.identity = "running", run_identity(out)
        with self.lock:
            self.views.append(view)
        return view

    def finish(self, view, card=None):
        """The run is scored: its final card is published at once, provisional scoring stops, and its results card
        is shown, its results page linked while the pinned reader would serve it (RunView.scored)."""
        with view.lock:
            view.scored()
        if view.provisional is not None and card is not None:
            view.provisional.finalize(card)

    def chosen(self, query):
        """The run (tab) a request names by ?run=, digits alone; the first without one."""
        given = (query or {}).get("run", [])[:1]
        return int(given[0]) if given and feed.DIGITS.fullmatch(given[0]) else 0

    def state(self, query=None):
        """Every run's state; the request's opened entries and earlier page apply to the run it names (?run=)."""
        with self.lock:
            views = list(self.views)
        pick = self.chosen(query)
        runs = [v.state(query if i == pick else None) for i, v in enumerate(views)]
        waiting = [{"name": n, "status": "waiting"} for n in self.names[len(views):]]
        listed = [v.summary_of(i) for i, v in enumerate(views)] + \
            [other_run({"target": w["name"]}, None, "waiting") for w in waiting]
        for i, (view, run) in enumerate(zip(views, runs)):        # each run's card lists the command's other runs
            run["card"], run["bar"] = view.card_of(i, [x for j, x in enumerate(listed) if j != i])
        return {"runs": runs + waiting, "boundary": BOUNDARY, "moves": MOVES, "midline": MIDLINE_NOTE}

    def results_page(self, query):
        """GET /results.html?t=TOKEN&run=N: the results page of the command's run N (results_of), or None for 404."""
        with self.lock:
            views = list(self.views)
        pick = self.chosen(query)
        return results_of(views[pick]) if pick < len(views) else None

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# ---- the results page, linked once a run has written it (the live-feed ruling, V3) ----------------------------------
RESULTS_PATH = "/results.html"


def run_identity(out):
    """The run folder's (device, inode), recorded when the page starts watching it, as the form records a run's
    (ui.folder_identity): the page reads results.html only from the folder pinned to it (results_of)."""
    import ui                       # the form's own pinned reader; ui imports this module, so it is imported here
    return ui.folder_identity(os.path.abspath(out))


def results_page_of(kind):
    """Where a run's results page is in its folder, as the form's own fixed subpaths name it: a cross-type run's
    ordo/results.html, beside its answer (ui.ORDO_PAGE, for feed.CrossKind), else results.html at the folder's top
    (ui.SINGLE_PAGE: a single-target and a head-to-head run). Chosen once, from the kind the run was started with,
    and kept on its view; nothing in a request reaches it (the route is /results.html for every kind)."""
    import ui
    return ui.ORDO_PAGE if isinstance(kind, feed.CrossKind) else ui.SINGLE_PAGE


def results_of(view):
    """(bytes, content type, CSP) of a scored run's results page, read by the form's own pinned reader
    (ui.read_results_page: the run's own folder, never one from the request, opened without following a link and
    checked against the identity recorded when the page started watching it; each folder of the page's subpath,
    view.page, opened the same way; the page-name check, ui.named_alone, in the last), served under the results
    page's own policy (ui.RESULTS_PAGE_CSP); None (404) before the run is scored, for a run that wrote none, or when
    the read is refused."""
    import ui
    if getattr(view, "results", None) is None or getattr(view, "identity", None) is None:
        return None
    body = ui.read_results_page(os.path.abspath(view.out), view.identity, view.page)
    return None if body is None else (body, "text/html; charset=utf-8", ui.RESULTS_PAGE_CSP)


# ---- the results card (the results-card ruling C1 to C9; its design, D1 to D15) ------------------------------------
# When a run is done, the --watch page (VIEW_LIVE) and the form (ui.PAGE) show the same card: a sheet of the results
# page laid on the page the reader is on, in that page's paper, ink, type and accent, with one way in. On the --watch
# page it is the first thing under the title row, once the run is done; on the form it opens the run's box in each
# of the run's states (D1): what is running, with the live link as a secondary button; the run finished, with "Open
# the results page" as the primary one; the run finished with no page to open, saying why in one plain sentence, with
# no button. One home for each part:
#   markup  run_card and its parts build a tree of plain nodes (card_node: a tag of CARD_TAGS, a class, children that
#           are text or nodes); each page's script draws it with CARD_SCRIPT, every string a text node, never markup
#   style   CARD_STYLE: the results pages' own colours by role (CARD_ROLES: keys of results_kit.COLOURS, never a
#           value), their font, monospace, type scale and button border (results_kit's tokens), and the design's own
#           measures (D2 to D11); one finish, the results page's sheet (D3's A, the manager's choice)
#   words   CARD_WORDS, the one table both pages read (D12)
# The title is the results page's own (card_title: the h1's string, by the page module's own page_title); the facts
# line is the run line's facts and the run's time (card_facts). A page is offered only while the pinned reader would
# serve it now (page_state), from an address the page's script builds from its own token, its server's fixed path and
# the run's number or folder alone (CARD_SCRIPT's cardHref), never from a line a run printed. results_bar is the
# --watch page's bar for a reader further down the page (D10).
CARD_WORDS = {
    "finished": "Run finished",                  # the card's status label
    "running": "Running",                        # the form's run card while its run goes
    "open": "Open the results page",             # the results link's existing words (LIVE_WORDS["results"])
    "new_tab": " (opens in a new tab)",          # after a link's words, for assistive technology only
    "ready": "Results are ready",                # the --watch page's bar
    "live": "Watch the run live",                # the form's link to the live page while its run goes (secondary)
    "watch_page": "Watch page",                  # the form's quiet link to the live page while it is held
    "saved": "Saved in",
    "go": "Open",                                # another run's own link, in a card's list of the command's runs
    "dismiss": "Dismiss",
    "took": "took", "tier": "tier", "memory": "memory",
    "no_page": "no results page",
    "details": "Run details",                    # the form's fold under a finished card (round 2, E3)
    "details_hint": "command, folder, printed lines",
    "why": {"missing": "There is no results page in this run's folder.",
            "refused": "The results page could not be read from the run's folder."},
}
ARROW = "\u2192"                                 # an arrow after a button's words, aria-hidden; the thin space
# before it is the arrow's own margin (.rc-arrow), so no unseen character enters the page's data (display boundary)
CARD_TAGS = ("section", "div", "h2", "p", "span", "code", "ul", "li", "a", "button")
CARD_KEYS = ("tag", "cls", "kids", "link", "live", "act", "focus", "aria", "deco", "path")
TEXT_FILES = ("scorecard.md", "report.md", "results.csv")    # a run's text files beside its results page
CARD_RECHECK_SECONDS = 2.0                       # a done run's page is asked of the pinned reader at most this often
BAR_EDGE = "max(16px,calc(50% - 524px))"          # the --watch page's content column's edge (main: 1080px, 16px
                                                 # sides): the bar spans the window, its content stays within it
BAR_WORDS_MIN = 480     # px of the bar's content box under which its words are hidden from sight (round 3b's G1):
# measured in the suite's Chrome, the disc, "Results are ready" whole, the full-label button and the close with their
# gaps take 431px; the rest is room for a wider system font. Below it the words stay for a screen reader only.
VISUALLY_HIDDEN = ("position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0,0,0,0);"
                   "white-space:nowrap;border:0;")     # the page's one way to hide words from sight, not from a reader


def card_node(tag, cls=None, *kids, **extra):
    """One node of the card's markup: a tag of CARD_TAGS, its class (None for none), its children (text, nodes; None
    left out), and the keys the page's script reads (CARD_SCRIPT's cardNode): link, the route's parameters ({"run": N}
    on the --watch page, {"folder": F} on the form), from which the page builds the address; live, a link to the form's
    live page, whose address is the one its run printed and the form read (ui.WATCH_LINK); act, a button's action;
    focus, a node the page may focus (the title: tabindex -1, out of the tab order); path, a folder, which the script
    draws with a break opportunity (<wbr>) after each "/", between its text nodes, so it wraps where a path breaks
    (the round-3 brief's F10: Chrome breaks after a hyphen but not after a slash); aria, an accessible name; deco, a
    node hidden from assistive technology (the disc, the arrow)."""
    if tag not in CARD_TAGS:
        raise ValueError(f"the card has no {tag} element")
    if set(extra) - set(CARD_KEYS):
        raise ValueError(f"the card's nodes carry no {sorted(set(extra) - set(CARD_KEYS))}")
    return {"tag": tag, "cls": cls, "kids": [k for k in kids if k is not None], **extra}


def card_title(facts):
    """The card's title (title_of), cut as a whole within the display cap (capped), as the pages cut a label built
    around displayed tokens: a title of names each up to the cap stays within it too."""
    return capped(title_of(facts))


def title_of(facts):
    """The run's results page's own title, the very string its h1 carries, built by that page's own function from the
    page's facts (single_html.page_title for a single-target run, ordo_html.page_title for a comparison of types);
    a head-to-head run's comes ready made as its facts' "title" (versus_run.card_title, versus_html.page_title's,
    handed over by versus_run: live and ui import nothing of versus); for facts that hold none of these, the run's
    target, comparison or names as the facts give them, or nothing."""
    f = facts or {}
    if f.get("title"):                          # a versus run's, handed over by versus_run (live imports no versus)
        return f["title"]
    if f.get("kind") == KIND_WORDS["single"] and f.get("target") and f.get("target_type"):
        import single_html
        return single_html.page_title(f["target"], f["target_type"])
    if f.get("kind") == KIND_WORDS["cross"] and f.get("types"):
        import ordo_html
        return ordo_html.page_title(f["types"])
    return f.get("target") or f.get("compare") or f.get("names") or ""


def card_facts(facts, took=None):
    """The card's facts line: the run line's facts the title does not already say, in short, each from the page's own
    fields (run_facts' and a versus run's): the agent and its model, the tier, memory, and how long the run took
    (clock_text, as the live line prints it), joined by middle dots as the pages join them. A missing fact is left
    out; nothing is invented."""
    f = facts or {}
    agent = " ".join(x for x in (f.get("agent"), f.get("model")) if x)
    parts = [agent, f.get("tier") and f"{f['tier']} {CARD_WORDS['tier']}",
             f.get("memory") and f"{CARD_WORDS['memory']} {f['memory']}",
             None if took is None else f"{CARD_WORDS['took']} {clock_text(took)}"]
    return capped(" · ".join(str(x) for x in parts if x))     # the whole line within the display cap


def page_state(out, identity, page):
    """Whether a done run's results page can be served now, and if not why, for the card: "ready" when the page's own
    route would serve it (the pinned reader, ui.read_results_page, reads folder/page from the folder whose identity
    was recorded), "missing" when the folder holds no such name, "refused" when it does and the reader refuses it (a
    link, a page with a second name, a folder replaced). The look for the name only chooses the sentence: nothing is
    ever served by it."""
    import ui
    folder = os.path.abspath(out)
    if identity is not None and ui.read_results_page(folder, identity, page) is not None:
        return "ready"
    return "refused" if os.path.lexists(os.path.join(folder, *page)) else "missing"


def text_files(out, page):
    """The run's text files beside its results page (TEXT_FILES, in its subfolder for a cross-type run), as the folder
    holds them: names relative to the run's folder. Each part of the run's own subpath is looked at without following
    a link (lstat): the folder and its subfolder must be directories and each name a regular file, so a link in their
    place lists nothing. PageCheck asks it once, when the run is scored, and keeps the answer (the round-3 brief's
    F6). Only the names are shown; nothing is read."""
    at = tuple(page[:-1])
    folder = os.path.abspath(out)
    if not all(lstat_is(os.path.join(folder, *at[:i]), stat.S_ISDIR) for i in range(len(at) + 1)):
        return []
    return ["/".join(at + (name,)) for name in TEXT_FILES if lstat_is(os.path.join(folder, *at, name), stat.S_ISREG)]


def lstat_is(path, kind):
    """Whether path, looked at without following a link (lstat), is of the kind kind tests (stat.S_ISDIR, S_ISREG);
    False when it cannot be looked at."""
    try:
        return kind(os.lstat(path).st_mode)
    except (OSError, ValueError):
        return False


class PageCheck:
    """A done run's results page as its card offers it, the one rule for the --watch page (RunView.check, one per run)
    and the form (Runner.checks, one per folder): whether the pinned reader would serve it now, asked at most every
    CARD_RECHECK_SECONDS, and the run's text files beside it, listed once (the round-3 brief's F1 and F6).

    ask(now, decide, listing, force) returns the page's state, "ready", "missing" or "refused". decide() makes the
    check (page_state, by the pinned reader). What it raises that the request handler's attempt() catches (CAUGHT:
    any Exception, not only an OSError or a ValueError, and SystemExit, so no code a route calls ends the server; a
    KeyboardInterrupt still ends the process) fails closed: the page is not offered ("refused", whose sentence is the card's "could not be read"), the attempt's time
    is kept like any other's, so it is tried again at most every CARD_RECHECK_SECONDS whatever it raised, and its
    traceback goes to standard error once for this run, displayed (terminal_text), never on every request; an answer
    that is none of the three is "refused" too. listing() runs at the first ask only (the forced one when the run is
    scored) and its answer is kept: the card lists what the run saved, and a folder changed later changes nothing it
    lists. now is the caller's clock (RunView.clock, Runner.clock), so a test can move it. Called under the owner's
    lock: nothing here takes one."""

    STATES = ("ready", "missing", "refused")
    CAUGHT = (Exception, SystemExit)    # serve_page's attempt() catches these, and so does every guard here (P1)

    def __init__(self):
        self.state, self.at, self.files, self.told = None, None, None, False

    def ask(self, now, decide, listing=None, force=False):
        if not force and self.at is not None and now - self.at < CARD_RECHECK_SECONDS:
            return self.state
        self.at = now
        try:
            state = decide()
        except self.CAUGHT:                                 # what attempt() catches: fail closed (F1, P1)
            state = None
            self.tell()
        self.state = state if state in self.STATES else "refused"
        if self.files is None and listing is not None:
            try:
                self.files = list(listing())
            except self.CAUGHT:                             # list nothing rather than fail
                self.files = []
                self.tell()
        return self.state

    def tell(self):
        """The current exception's traceback on standard error, displayed, the first time only."""
        if not self.told:
            self.told = True
            sys.stderr.write(terminal_text(traceback.format_exc()))


def opens(words, link=None, cls="rc-btn rc-open", **extra):
    """A link that opens a page in a new tab: its words, the arrow (aria-hidden) and, for assistive technology only,
    that it opens a new tab."""
    return card_node("a", cls, words, card_node("span", "rc-arrow", ARROW, deco=True),
                     card_node("span", "rc-sr", CARD_WORDS["new_tab"]), **({"link": dict(link)} if link else {}),
                     **extra)


def card_for(done, facts, took, state, link, folder, files=(), others=(), watch_page=False):
    """The card of one done run, or None: None while the run is not done (running, waiting, or ended in a refusal or
    an error: done is the page's own reading of its status, and each page's own message stands); else run_card in
    its "ready" state when state (page_state's answer) is "ready", in its "nopage" state with the sentence of why
    otherwise."""
    if not done:
        return None
    ready = state == "ready"
    return run_card("ready" if ready else "nopage", facts, took, folder, link=link if ready else None,
                    why=None if ready else state, files=files, others=others, watch_page=watch_page)


def run_card(state, facts, took, folder, link=None, why=None, files=(), others=(), live=False, watch_page=False):
    """The card's tree in one of its three states (D1): "running" (the form's, while its run goes: the label, the
    title, the facts, and the live link as the secondary button when there is one, live), "ready" (the label with its
    disc, the title, the facts and the primary button, link the route's parameters) and "nopage" (the same with why's
    sentence instead of any button). Then the footer: where the run is saved, the form's quiet link to its live page
    while it is held (watch_page), and the text files; then the command's other runs (other_run's). The title, focusable
    by the page, comes first after the label, and the button is the first thing the keyboard reaches after it."""
    w = CARD_WORDS
    label = (card_node("p", "rc-label", w["running"]) if state == "running" else
             card_node("p", "rc-label", card_node("span", "rc-disc", deco=True), w["finished"]))
    text = [label, card_node("h2", "rc-title", card_title(facts), focus=True),
            card_node("p", "rc-facts", card_facts(facts, took))]
    if state == "nopage":
        text.append(card_node("p", "rc-why", w["why"][why]))
    act = (opens(w["open"], link) if state == "ready" else
           opens(w["live"], cls="rc-btn rc-second", live=True) if state == "running" and live else None)
    main = card_node("div", "rc-main", card_node("div", "rc-text", *text),
                     card_node("div", "rc-act", act) if act else None)
    saved = card_node("p", "rc-saved", card_node("span", "rc-k", w["saved"]), " ", card_node("code", "rc-path", folder, path=True),
                      *([" ", opens(w["watch_page"], cls="rc-quiet", live=True)] if watch_page else []))
    foot = card_node("div", "rc-foot", saved,
                     card_node("p", "rc-files", " · ".join(files)) if files else None)
    rest = card_node("ul", "rc-others", *others) if others else None
    return card_node("section", f"rk rc rc-{state}", card_node("div", "rc-in", main, foot, rest),
                     aria=w["running"] if state == "running" else w["finished"])


def other_run(facts, took, status, link=None):
    """One other run of the command, in a card's list (D11): its title, its facts in short, and its own link while its
    page can be served (link, the route's parameters), else its status in the page's words."""
    title = card_title(facts)
    named = capped(f"{CARD_WORDS['go']}: {title}")                 # the accessible name within the cap too
    end = (opens(CARD_WORDS["go"], link, cls="rc-go", aria=named) if link is not None else
           card_node("span", "rc-ost", status))
    return card_node("li", "rc-other", card_node("span", "rc-otitle", title),
                     card_node("span", "rc-ofacts", card_facts(facts, took)), end)


def other_words(status, state):
    """Another run's status in a card's list, when it has no link: the page's own status word, with "no results page"
    after it for a done run whose page cannot be served."""
    return status if status not in ("finished", "scored") else f"{status} · {CARD_WORDS['no_page']}"


def results_bar(link):
    """The --watch page's bar for a reader further down the page (D10): the disc and "Results are ready", the card's
    button in a compact size (none when the page cannot be served) and Dismiss, a quiet close control."""
    return card_node("div", "rk rc-bar", card_node("span", "rc-disc", deco=True),
                     card_node("span", "rc-bar-h", CARD_WORDS["ready"]),
                     card_node("span", "rc-bar-act",
                               opens(CARD_WORDS["open"], link, cls="rc-btn rc-open rc-compact") if link else None,
                               card_node("button", "rc-x", card_node("span", "rc-sr", CARD_WORDS["dismiss"]),
                                         act="dismiss")))


# The card's colour roles, each a key of results_kit.COLOURS, never a value: {role: (light, dark)}. The results pages
# have one scheme, light; the --watch page and the form have both, so in a dark scheme the card takes the kit's darker
# and lighter colours for the same roles.
CARD_ROLES = {"fill": ("bg", "charcoal"), "frame": ("frame", "ink-soft"), "ink": ("ink", "bg"),
              "label": ("chip", "accent-light"), "muted": ("ink-faint", "charcoal-light"),
              "accent": ("accent", "accent-light"), "on-accent": ("bg", "ink"), "btn": ("accent", "accent-light"),
              "on-btn": ("bg", "ink"), "accent-text": ("chip", "accent-light")}
SHADOW = ("0 1px 2px rgba(0,0,0,.04), 0 10px 28px rgba(0,0,0,.07)",                      # D2: light, dark
          "inset 0 1px 0 rgba(255,255,255,.04), 0 12px 32px rgba(0,0,0,.45)")


def card_roles(roles, scheme):
    """Custom properties for one scheme (0 light, 1 dark), from the kit's COLOURS by role."""
    return "".join(f"--rc-{role}:{results_kit.COLOURS[keys[scheme]]};" for role, keys in roles.items())


def card_style():
    """CARD_STYLE: the card (the results page's sheet) and its three states, its buttons, the footer and the other
    runs, the --watch page's bar, the arrival and the narrow and wide layouts (the card's own width: one column below
    640px). Every colour is a role of the kit's (card_roles) but the shadows, which the design gives (D2), and the
    bar's paper and rule, the page's own surface and grid colour, opaque (D10, round 2's E4), across the whole window
    with its content held to the content column (BAR_EDGE, round 3's F11); the folder wraps with word-break normal
    and overflow-wrap anywhere (F10); the font, the monospace, the run line's and the fine sizes and the button's
    border width are results_kit's tokens (its rem sizes as em of the card's own ROOT)."""
    k, t = results_kit, results_kit.TYPE
    em = lambda rem: rem.replace("rem", "em")                           # noqa: E731
    still = "@media (prefers-reduced-motion: reduce){.rc-enter,.rk.rc-bar{animation:none;}" \
            ".rk .rc-arrow{transition:none;}.rk .rc-btn:active{transform:none;}" \
            ".rk .rc-btn:hover .rc-arrow,.rk .rc-go:hover .rc-arrow{transform:none;}}\n"
    return (
        f".rk{{{card_roles(CARD_ROLES, 0)}font-family:{k.FONT};}}\n"
        f"@media (prefers-color-scheme: dark){{.rk{{{card_roles(CARD_ROLES, 1)}}}.rc{{box-shadow:{SHADOW[1]};}}}}\n"
        # the sheet (D2, D3): the results page's paper, its frame, the accent's rule along the top inside the corners
        f".rc{{position:relative;overflow:hidden;container-type:inline-size;margin:0 0 22px;border-radius:14px;"
        f"border:1px solid var(--rc-frame);box-shadow:{SHADOW[0]};color:var(--rc-ink);background:var(--rc-fill);"
        f"font-size:{k.ROOT};line-height:{k.LEADING};}}\n"
        ".rc::before{content:\"\";position:absolute;top:0;left:0;right:0;height:3px;background:var(--rc-accent);}\n"
        # the layout: one column, then two at 640px of the card's own width (D4, D8)
        ".rc .rc-in{padding:18px;}\n"
        ".rc .rc-text > *{margin:0;}\n.rc .rc-text > * + *{margin-top:6px;}\n"
        ".rc .rc-act{margin-top:14px;}\n"
        ".rc .rc-act .rc-btn{display:flex;width:100%;}\n"
        ".rc .rc-foot{margin-top:14px;padding-top:12px;border-top:1px solid var(--rc-frame);}\n"
        ".rc .rc-foot > *{margin:0;}\n.rc .rc-files{margin-top:4px;}\n"
        "@container (min-width:640px){.rc .rc-in{padding:22px 24px;}"
        ".rc .rc-main{display:grid;grid-template-columns:minmax(0,1fr) auto;column-gap:24px;align-items:center;}"
        ".rc .rc-act{margin-top:0;}.rc .rc-act .rc-btn{display:inline-flex;width:auto;}"
        ".rc .rc-foot{display:flex;align-items:baseline;gap:4px 24px;}"
        ".rc .rc-saved{flex:1 1 auto;min-width:0;}.rc .rc-files{margin:0 0 0 auto;text-align:right;}}\n"
        # the text block (D5)
        ".rc .rc-label{display:flex;align-items:center;gap:8px;font-size:.78em;font-weight:700;letter-spacing:.06em;"
        "text-transform:uppercase;color:var(--rc-label);}\n"
        ".rk .rc-disc{flex:none;position:relative;display:inline-block;width:20px;height:20px;border-radius:50%;"
        "background:var(--rc-accent);}\n"
        ".rk .rc-disc::after{content:\"\";position:absolute;left:7px;top:3.5px;width:5px;height:10px;"
        "border:solid var(--rc-on-accent);border-width:0 2px 2px 0;transform:rotate(45deg);}\n"
        ".rc .rc-title{display:block;padding:0;border:0;font-size:1.3em;font-weight:700;line-height:1.25;"
        "letter-spacing:normal;text-transform:none;color:var(--rc-ink);overflow-wrap:anywhere;}\n"
        ".rc .rc-title:focus{outline:none;}\n"
        f".rc .rc-facts{{font-size:{em(t['runline'])};color:var(--rc-muted);overflow-wrap:anywhere;}}\n"
        f".rc .rc-why{{font-size:{em(t['runline'])};color:var(--rc-ink);overflow-wrap:anywhere;}}\n"
        # the buttons (D6)
        f".rk .rc-btn{{display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;"
        f"min-height:44px;padding:11px 18px;border:{k.BUTTON['border']} solid var(--rc-btn);border-radius:10px;"
        f"font-family:{k.FONT};font-size:15px;font-weight:600;line-height:1.2;white-space:nowrap;"
        "text-decoration:none;cursor:pointer;}\n"
        ".rk .rc-open{color:var(--rc-on-btn);background:var(--rc-btn);}\n"
        ".rk .rc-open:hover{background:color-mix(in srgb,var(--rc-btn) 92%,black);"
        "border-color:color-mix(in srgb,var(--rc-btn) 92%,black);}\n"
        ".rk .rc-second{color:var(--rc-accent-text);background:transparent;border-color:var(--rc-accent);}\n"
        ".rk .rc-btn:active{transform:translateY(1px);}\n"
        ".rk .rc-btn:focus-visible,.rk .rc-go:focus-visible,.rk .rc-quiet:focus-visible,.rk .rc-x:focus-visible{"
        "outline:2px solid var(--rc-accent);outline-offset:2px;}\n"
        ".rk .rc-arrow{display:inline-block;margin-left:.17em;transition:transform .12s ease-out;}\n"
        ".rk .rc-btn:hover .rc-arrow,.rk .rc-go:hover .rc-arrow{transform:translateX(2px);}\n"
        f".rk .rc-sr{{{VISUALLY_HIDDEN}}}\n"
        # the footer (D7)
        f".rc .rc-saved,.rc .rc-files{{font-size:{em(t['fine'])};color:var(--rc-muted);}}\n"
        f".rc .rc-path{{font-family:{k.MONO};font-size:1em;color:var(--rc-ink);user-select:text;"
        "-webkit-user-select:text;word-break:normal;overflow-wrap:anywhere;}\n"
        ".rc .rc-quiet{margin-left:4px;color:var(--rc-accent-text);font-weight:600;text-decoration:underline;"
        "text-underline-offset:2px;white-space:nowrap;}\n"
        # the other runs (D11)
        ".rc .rc-others{list-style:none;margin:12px 0 0;padding:0;border-top:1px solid var(--rc-frame);}\n"
        ".rc .rc-other{display:flex;flex-wrap:wrap;align-items:center;gap:2px 12px;min-height:40px;padding:4px 0;}\n"
        ".rc .rc-other + .rc-other{border-top:1px solid var(--rc-frame);}\n"
        ".rc .rc-otitle{font-weight:600;color:var(--rc-ink);overflow-wrap:anywhere;}\n"
        f".rc .rc-ofacts,.rc .rc-ost{{font-size:{em(t['fine'])};color:var(--rc-muted);}}\n"
        ".rc .rc-go{margin-left:auto;padding:6px 2px;font-weight:600;color:var(--rc-accent-text);"
        "text-decoration:none;white-space:nowrap;}\n"
        ".rc .rc-ost{margin-left:auto;}\n"
        # the arrival (D9)
        "@keyframes rc-in{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:none;}}\n"
        ".rc-enter{animation:rc-in .24s ease-out;}\n"
        # the --watch page's bar (D10, E4): the page's own paper, opaque, and its one 1px rule beneath, both the
        # window's whole width (F11), its content padded to the content column's edge (BAR_EDGE)
        ".rk.rc-bar{position:fixed;top:0;left:0;right:0;"
        "z-index:40;box-sizing:border-box;height:44px;display:flex;align-items:center;gap:8px;"
        "container-type:inline-size;"
        f"padding:0 calc({BAR_EDGE} + 6px) 0 calc({BAR_EDGE} + 10px);"
        "color:var(--ink);background:var(--surface);border-bottom:1px solid var(--grid);font-size:14px;"
        "line-height:1.2;}\n"
        ".rk.rc-bar.rc-slide{animation:rc-down .2s ease-out;}\n"
        "@keyframes rc-down{from{transform:translateY(-44px);}to{transform:none;}}\n"
        # the bar's words (G1): whole, never cut; below BAR_WORDS_MIN of the bar's own width, hidden from sight only
        ".rc-bar .rc-bar-h{flex:none;font-weight:600;white-space:nowrap;}\n"
        f"@container (max-width:{BAR_WORDS_MIN - 0.02}px){{.rc-bar .rc-bar-h{{{VISUALLY_HIDDEN}}}}}\n"
        ".rc-bar .rc-bar-act{margin-left:auto;display:flex;align-items:center;gap:4px;}\n"
        ".rk .rc-compact{min-height:32px;padding:7px 14px;font-size:14px;}\n"
        ".rk .rc-x{position:relative;flex:none;width:32px;height:32px;padding:0;border:0;border-radius:8px;"
        "background:none;color:var(--muted);cursor:pointer;}\n"
        ".rk .rc-x::before,.rk .rc-x::after{content:\"\";position:absolute;left:9px;top:15px;width:14px;"
        "height:1.5px;background:currentColor;}\n"
        ".rk .rc-x::before{transform:rotate(45deg);}\n.rk .rc-x::after{transform:rotate(-45deg);}\n"
        ".rk .rc-x:hover{color:var(--ink);}\n"
        + still)


CARD_STYLE = card_style()

# The card's renderer, the same text in both pages' scripts: cardNode turns run_card's tree into elements (a tag
# outside CARD_TAGS becomes a span, a class only of letters, digits, spaces and hyphens, every string a text node),
# cardHref builds a link's address from the page's own token (location.search's t), its server's fixed path and the
# run's number or folder alone, cardPath draws a folder (a node with path) with a <wbr> after each "/" between its text
# nodes (F10), and cardDraw replaces a box's card only when its tree changed, so focus and a
# selection in it survive the page's refreshes (enter: the card arrives, D9). hands carries the page's own: path,
# live() (the form's live address), and each button's action.
CARD_SCRIPT = """
const CARD_TAGS = """ + json.dumps(list(CARD_TAGS)) + """;
const CARD_TOKEN = new URLSearchParams(location.search).get("t") || "";
const cardSigs = new WeakMap();
function cardHref(path, link) {
  const q = {t: CARD_TOKEN};
  if (/^[0-9]{1,9}$/.test(String(link.run))) q.run = String(link.run);
  if (typeof link.folder === "string") q.folder = link.folder;
  return path + "?" + new URLSearchParams(q).toString();
}
function cardNode(n, hands) {
  if (typeof n === "string" || typeof n === "number") return document.createTextNode(String(n));
  const e = document.createElement(CARD_TAGS.includes(n.tag) ? n.tag : "span");
  if (typeof n.cls === "string" && /^[a-z0-9 -]*$/.test(n.cls)) e.className = n.cls;
  if (n.focus) e.tabIndex = -1;
  if (typeof n.aria === "string") e.setAttribute("aria-label", n.aria);
  if (n.deco) e.setAttribute("aria-hidden", "true");
  if (n.tag === "a") {
    const href = n.link ? cardHref(hands.path, n.link) : n.live && hands.live ? hands.live() : null;
    if (href) { e.href = href; e.target = "_blank"; e.rel = "noopener noreferrer"; }
  }
  if (n.tag === "button") {
    e.type = "button";
    if (typeof n.act === "string" && typeof hands[n.act] === "function") e.addEventListener("click", hands[n.act]);
  }
  for (const k of n.kids || [])
    if (n.path && typeof k === "string") cardPath(e, k);
    else e.appendChild(cardNode(k, hands));
  return e;
}
function cardPath(e, text) {            // a path: a <wbr> after each "/", between text nodes, never inside the text
  const parts = String(text).split("/");
  parts.forEach((p, i) => {
    const piece = i < parts.length - 1 ? p + "/" : p;
    if (piece) e.appendChild(document.createTextNode(piece));
    if (i < parts.length - 1) e.appendChild(document.createElement("wbr"));
  });
}
function cardDraw(box, tree, hands, enter) {
  const sig = JSON.stringify(tree || null);
  if (cardSigs.get(box) === sig) return false;
  cardSigs.set(box, sig);
  const node = tree ? cardNode(tree, hands) : null;
  if (node && enter) node.classList.add("rc-enter");
  box.replaceChildren(...(node ? [node] : []));
  return true;
}
"""

# ---- the versus page (docs/versus/DESIGN.md 8; counts while it runs, the command's own lines once it ends) ------------
# A versus run is scored only when it is complete: its estimator refuses an incomplete table, so the page never shows a
# provisional index, share, interval or sentence. While the run goes it shows, per arm and row, how many calls are
# planned, answered (came back without an error) and valid (a question whose reply parsed to one of its own letters, an
# errand whose record is whole, a bound that parsed), and what each valid question chose, named as the page names it:
# the party as rendered in that arm's roster, the lottery, a tie's label by its place; a reply that came back and did
# not parse is unreadable. When the run ends it shows the lines the command prints, the folder's path among them.
VERSUS_ROWS = (("class1", "Class I questions"), ("class3", "Class III errands"),      # an @2 run's class rows first
               ("allocation", "stated allocation"), ("conflict", "forced conflicts"), ("triad", "the triad"),
               ("errand", "errands"), ("tie", "checks: the tie controls"), ("bound", "checks: the bounds"))
EPISODE_FRAMES = ("errand", "class3")                               # rows of errands, valid by their episode_facts
ANSWERED_FRAMES = ("allocation", "conflict", "triad", "tie")        # the rows whose answers are counted by choice
TIE_PLACES = {"first_label": "first label", "second_label": "second label"}
LOTTERY, UNREADABLE = "the lottery", "unreadable"
VERSUS_WORDS = {   # the sentences only a versus run's page shows; the page's script carries them with LIVE_WORDS
    "title": "probatio · versus live",
    "counts": "counts so far: a versus result is computed only when the run is complete",
    "answer": "The answer", "refused": "The run ended in a refusal, and no page was written",
    "heads": ["", "planned", "answered", "valid", "answers so far"],
    "final_heads": ["", "planned", "answered", "valid", "answers"], "calls": "calls",
    "strangers": "unfamiliar roster", "none": "not counted by answer",
}

def chosen_choice(call, rec):
    """The choice a committed question's reply parsed to: the row's own choice whose letter is the reply's answer, or
    None (no answer, or one the row does not offer)."""
    answer = rec.get("answer")
    if not isinstance(answer, str):
        return None
    return next((c for c in call["row"].get("choices") or [] if c.get("label") == answer), None)


def answer_word(call, choice, rosters):
    """What the page calls a choice: a tie's label by its place (TIE_PLACES) or the lottery; any other question's
    party as rendered in the call's own roster arm (rosters), or the lottery."""
    if choice.get("maps_to") == "lottery" or choice.get("side") == "lottery":
        return LOTTERY
    if call["meta"].get("frame") == "tie":
        return TIE_PLACES.get(choice.get("side"))
    return rosters.get(call["meta"].get("roster"), {}).get(choice.get("maps_to"))


def call_valid(call, rec, facts):
    """Whether a committed record counts as valid: a question's reply parsed to one of its own letters
    (chosen_choice; a Class I question's too), an errand's record is whole (its captured episode_facts; a Class III
    errand's too), a bound's reply parsed (ordo's question_unit reads it so)."""
    frame = call["meta"].get("frame")
    if frame in EPISODE_FRAMES:
        return bool(facts and facts.get("valid"))
    if frame in ANSWERED_FRAMES or frame == "class1":
        return chosen_choice(call, rec) is not None
    return rec.get("outcome") is not None


def answer_order(rosters, roster):
    """The order a row lists its answers in: the arm's parties in the run's type order, the tie's places, the lottery,
    unreadable last."""
    return [*rosters.get(roster, {}).values(), *TIE_PLACES.values(), LOTTERY, UNREADABLE]


def versus_counts(calls, done, facts_of, arms, rosters, arm_of):
    """The page's table, pure: for each arm (arms: (label, words) in order) and each row of VERSUS_ROWS it plans, the
    planned, answered and valid calls and, for a question row, each answer's count, every answer the row can give
    listed from the start at 0 (answer_order). done is the committed records by call key, facts_of each committed
    errand's episode_facts; arm_of names a call's arm (versus.arm_label). Nothing here is a share or an estimate."""
    cells = {}
    for c in calls:
        frame = c["meta"].get("frame")
        cell = cells.setdefault((arm_of(c), frame), {"planned": 0, "answered": 0, "valid": 0,
                                                       "answers": {} if frame in ANSWERED_FRAMES else None})
        cell["planned"] += 1
        if cell["answers"] is not None:
            for choice in c["row"].get("choices") or []:
                cell["answers"].setdefault(answer_word(c, choice, rosters), 0)
            cell["answers"].setdefault(UNREADABLE, 0)
    by_key = {key(c): c for c in calls}
    for k, rec in done.items():
        call = by_key.get(k)
        if call is None or rec.get("error"):
            continue
        cell = cells[(arm_of(call), call["meta"].get("frame"))]
        cell["answered"] += 1
        valid = call_valid(call, rec, facts_of.get(k))
        cell["valid"] += valid
        if cell["answers"] is not None:
            word = answer_word(call, chosen_choice(call, rec), rosters) if valid else UNREADABLE
            cell["answers"][word] = cell["answers"].get(word, 0) + 1
    out = []
    for label, words in arms:
        roster = label.split(":")[0]
        rows = []
        for frame, row in VERSUS_ROWS:
            cell = cells.get((label, frame))
            if cell is None:
                continue
            order = answer_order(rosters, roster)
            answers = None if cell["answers"] is None else sorted(
                ([w, n] for w, n in cell["answers"].items() if w is not None),
                key=lambda wn: order.index(wn[0]) if wn[0] in order else len(order))
            rows.append({"row": row, "planned": cell["planned"], "answered": cell["answered"],
                         "valid": cell["valid"], "answers": answers})
        out.append({"arm": words, "rows": rows})
    return out


class VersusView(RunView):
    """What the versus page knows of its run: a RunView over the same folder, reading replies.jsonl through
    CommittedLines, each committed errand's episode_facts once (capture), and the newest task's errands' records as
    v4's view does, with the same fixed sentences for a read that fails, and the same feed and errands now (the
    live-feed ruling, V3, V4). It has no provisional scorer: its table is versus_counts' counts, and once the run is
    scored the command's own answer lines (answer, set by VersusWatch.finish), or the one line of a run that ended in
    a refusal (refusal, set by VersusWatch.refuse). No index, share, interval or sentence about a result appears
    before then."""

    def __init__(self, name, out, calls, facts, arms, rosters, arm_of, kind, redactor, line=None,
                 clock=time.monotonic, now=time.localtime):
        super().__init__(name, out, calls, facts, kind, redactor, line, None, clock, now)
        self.arms, self.rosters, self.arm_of, self.answer = list(arms), rosters, arm_of, None
        self.refusal = None

    def state(self, query=None):
        """The whitelisted state of the run for /state: its name, status and facts, the progress, the table
        (versus_counts), the answer lines once it is scored (else None), the refusal a run ended in (else None), its
        results page once written, the feed and errands now, and the reader's problems."""
        self.refresh()
        with self.lock:
            self.check_page()
            entries, live_now = self.entries(query)
            return {"name": self.redact.brief(self.name, 120), "status": self.status, "facts": self.facts,
                    "progress": self.shown_progress(),
                    "arms": versus_counts(self.calls, self.done, self.facts_of, self.arms, self.rosters, self.arm_of),
                    "answer": list(self.answer) if self.answer is not None else None, "refusal": self.refusal,
                    "results": self.results, "now": live_now, "feed": entries, "unreadable": self.unreadable(),
                    "problems": list(self.problems.values())}


class VersusWatch:
    """--watch for a versus command: one run, on the one page (VIEW_LIVE) and server v4's Watch uses (serve_page), its
    address printed once on stderr before the first call (announce), as v4's is. start gives it the run's view, every
    outside string in it displayed here, where the page's data is built (the name by plain_label, the facts by
    shown_facts, the rosters' names by shown_rosters); finish hands it the command's answer lines the moment the run
    is scored, refuse the one line of a run that ended in a refusal, each displayed again as the terminal's backstop
    does (uncut: the strings inside are already displayed). It serves for the life of the process."""

    def __init__(self, name, stream=None):
        self.name, self.view, self.lock = plain_label(name), None, threading.Lock()
        self.server = serve_page(VIEW_LIVE, self.state, query_state=True, files={RESULTS_PATH: self.results_page})
        self.redactor = ViewRedactor(extra=(self.server.token,))
        announce(stream or sys.stderr, self.server.url, BOUNDARY)

    def start(self, out, calls, facts, arms, rosters, arm_of, line=None, cells=None, title=None):
        """The run starts: its view (VersusView), running, its facts and names displayed, its entries drawn by
        feed.VersusKind with those names (cells: the ordo bank's cells.json, whose notices its errands carry); title is
        its results page's title for the results card (versus_run.card_title), handed over since live imports no
        versus."""
        shown = shown_rosters(rosters)
        view = VersusView(self.name, out, calls, shown_facts(facts), arms, shown, arm_of,
                          feed.VersusKind(shown, cells), self.redactor, line)
        view.status, view.identity, view.title = "running", run_identity(out), title
        with self.lock:
            self.view = view
        return view

    def finish(self, view, lines):
        """The run is scored: its answer lines, as the command prints them, are the page's from now on."""
        with view.lock:
            view.answer = [display(str(x), cap=None) for x in lines]
            view.scored()

    def refuse(self, view, line):
        """The run ended in a refusal (the page refused the card's claims, or a result file could not be written):
        that one line is the page's from now on, and no answer is shown."""
        with view.lock:
            view.status, view.last_refresh = "refused", None
            view.answer, view.refusal = None, display(str(line), cap=None)

    def state(self, query=None):
        with self.lock:
            view = self.view
        if view is None:
            return {"run": {"name": self.name, "status": "waiting"}}
        run = view.state(query)
        run["card"], run["bar"] = view.card_of(0)            # once complete only: nothing before it is scored (C5)
        return {"run": run}

    def results_page(self, query):
        """GET /results.html?t=TOKEN: the run's results page once it is scored (results_of), or None for 404."""
        with self.lock:
            view = self.view
        return results_of(view) if view is not None else None

    def close(self):
        self.server.shutdown()
        self.server.server_close()


LIVE_WORDS = {     # every sentence the one page shows but a versus run's own (VERSUS_WORDS), in one place
    "title": "probatio · live", "waiting": "Waiting for the run to start.", "boundary": BOUNDARY,
    "starts_after": " starts when the run before it ends.", "lost": "the run has stopped serving this page",
    "scorecard": "Scorecard", "scorecard_so_far": "Scorecard so far", "final": "final",
    "provisional": "provisional: {done} of {total} units",
    "first_card": "The first provisional scorecard comes with the first replies.",
    "pair_scale": "Pair indices 0 to 100: ", "score_scale": "Scores 0 to 100 with 95% intervals. ",
    "results": CARD_WORDS["open"], "now": "Errands now", "no_errand": "No errand has started yet.",
    "feed": "Questions as they land", "newest_first": "newest first", "nothing_yet": "No question has come back yet.",
    "earlier": "Earlier", "newest": "Back to the newest", "landed": "landed",
    "opening": "opening…", "unreadable": "unreadable lines", "valid": "valid", "abstained": "abstained",
    "errors": "errors", "left": "left", "tier": "tier", "memory": "memory", "effort": "effort", "target": "target",
    "against": "against", "compare": "compare", "under_minute": "under a minute", "about": "about", "minutes": "min",
    "hours": "hours",
    "fields": ["Dataset", "Class", "Category", "Condition", "Parties"],
    "sent": "What was sent", "options": "The options", "action": "The model's action", "task": "The task",
    "log": "The live log", "task_fields": ["Works for", "Benefits", "Cell", "The request", "Workspace",
                                           "What counts as solved"],
    "request_from": "from", "signed_in_as": "signed in as", "no_request": "This world serves no chat message.",
    "notice": "the notice as sent", "key": "the correct claim", "opening_sent": "the opening as sent",
    "read_as": "read as", "means": "means", "is_valid": "valid", "not_valid": "not valid: ",
    "filed": "filed", "not_filed": "nothing filed yet", "matches": "matches the correct claim",
    "differs": "does not match the correct claim", "earlier_calls": "earlier calls", "all_calls": "Show every call",
    "pages_read": "pages read", "records_examined": "records examined", "claims_filed": "claims filed",
    "report": "report: ", "reviewed": "reviewed? ", "real": "real? ", "unparsed": "unparsed",
    "no_report": "no report yet", "report_wait": "report not ready", "error": "error: ",
    "open_max": feed.OPEN_MAX,
    "held": f"Not refreshed: the page follows the {feed.OPEN_MAX} entries opened most recently. Close one of them to "
            "follow this one again.",
}


# ---- the page (4.6; the dataviz method; the live-feed ruling V2, V3, V11) --------------------------------------------
# One page for every kind of run, in one grammar: the run line, the progress, the kind's own table (the scorecard's
# strips while provisional and when final; a head-to-head run's counts table and, at the end, its answer lines), the
# results card once the run is done (first under the title row: the results-card ruling and its design), "Errands now"
# (the newest task's errands, opened), and "Questions as they land" (every landed call, newest first, SHOWN at a time,
# "Earlier" asking for the next). Each entry is a native <details>: its summary the compact line, its body the opened
# view, asked of the server only while it is open (?open=), so it opens with the keyboard and needs no script of its own
# to open. Entries are drawn by key: an entry's node is made once and only its text is changed, so an opened entry stays
# open across refreshes, and stays on the page however far the window of the feed moves until it is closed (kept); when
# anything grows above the block at the window's top edge (new entries, a longer log, the scorecard), the page scrolls
# by what it added, so what is being read does not move (overflow-anchor is off, so every browser does the same).
# Strips: one series per strip, a 2px whisker with round caps and an 8px dot, hollow while provisional and filled when
# final, the value printed beside each strip as its table view, a hover title with the exact numbers, a recessive grid
# at 0, 25, 50, 75 and 100, a dashed midline at 50 for a cross-type pair. Text wears the ink tokens; the status colours
# appear only with their words. Colour tokens live on :root with a dark mode, and the script builds every node with
# textContent (never innerHTML), so nothing the agent wrote can become markup. The token is never embedded: the script
# reads it from location.search. Its sentences are LIVE_WORDS and VERSUS_WORDS, carried into the script as W. PAGE_BASE,
# the palette (light and dark) and the base type, is shared with the --ui page (ui.py).
PAGE_BASE = """:root { --surface:#fcfcfb; --band:#f4f3ef; --ink:#0b0b0b; --ink2:#52514e; --muted:#6f6e69; --grid:#e1e0d9;
  --axis:#c3c2b7; --line:#e1e0d9; --good:#1f7a45; --bad:#b4372d; --pending:#8a6d1a; --hl:#f3efe4;
  --field:#dedcd6; --track:#f3f2ee; --accent:#2f5bc4; --accent-ink:#ffffff; --accent-soft:#e7edfa;
  --accent-text:#24489e; }
@media (prefers-color-scheme: dark) {
  :root { --surface:#1a1a19; --band:#222220; --ink:#f0efec; --ink2:#c3c2b7; --muted:#9a9993; --grid:#2c2c2a;
    --axis:#4a4a46; --line:#33332f; --good:#5cc68b; --bad:#ef7d72; --pending:#d9b45a; --hl:#2a2925;
    --field:#32322f; --track:#262624; --accent:#8eaaf5; --accent-ink:#0d1526; --accent-soft:#25304a;
    --accent-text:#b8cbfa; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--surface); color:var(--ink);
  font:14px/1.45 system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif; }
"""
VIEW_LIVE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>probatio live</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer">
<link rel="icon" href="data:,">
<style>
""" + PAGE_BASE + CARD_STYLE + """html, body { overflow-anchor:none; }
main { max-width:1080px; margin:0 auto; padding:20px 16px 40px; }
header { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
h1 { font-size:20px; margin:0; font-weight:650; }
h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--ink2); margin:26px 0 10px;
  font-weight:600; display:flex; gap:12px; flex-wrap:wrap; align-items:baseline; }
h2 .aside { text-transform:none; letter-spacing:0; font-weight:500; color:var(--muted); margin-left:auto; }
.pill { font-size:13px; color:var(--ink2); display:inline-flex; align-items:center; gap:6px; margin-left:auto; }
.pill::before { content:""; width:9px; height:9px; border-radius:50%; background:var(--muted); }
.pill.running::before { background:var(--good); }
.pill.stopped::before, .pill.refused::before { background:var(--bad); }
.pill.finished::before { background:var(--ink2); }
.names { font-size:16px; font-weight:600; margin:14px 0 2px; overflow-wrap:anywhere; }
.facts { color:var(--ink2); margin:4px 0 0; overflow-wrap:anywhere; } .facts div { margin:2px 0; }
.tabs { display:flex; gap:6px; flex-wrap:wrap; margin:14px 0 0; }
.tabs button, .nav button, .more { font:inherit; font-size:13px; color:var(--ink2); background:transparent;
  border:1px solid var(--line); border-radius:8px; padding:5px 10px; cursor:pointer; }
.tabs button.on { color:var(--ink); border-color:var(--ink2); font-weight:600; }
.bar { height:8px; background:var(--grid); border-radius:4px; overflow:hidden; margin:14px 0 8px; }
.bar i { display:block; height:100%; width:0; background:var(--ink2); border-radius:4px; }
.nums { font-variant-numeric:tabular-nums; color:var(--ink); }
.strips { display:grid; grid-template-columns:minmax(150px,220px) 1fr 104px; column-gap:14px; row-gap:2px;
  align-items:center; }
.strips .group { grid-column:1 / -1; font-size:12px; color:var(--muted); margin:10px 0 2px; }
.strips .label b { font-weight:600; } .strips .label span { color:var(--ink2); margin-left:6px; }
.strips .value { text-align:right; font-variant-numeric:tabular-nums; color:var(--ink2); white-space:nowrap; }
.strips .note { color:var(--muted); font-size:13px; grid-column:2 / -1; }
.tierline { color:var(--ink2); font-size:13px; margin:0 0 10px; }
.strips svg { width:100%; height:26px; display:block; overflow:visible; }
.axis text { font-size:11px; fill:var(--muted); }
svg .grid { stroke:var(--grid); stroke-width:1; } svg .mid { stroke:var(--axis); stroke-width:1; stroke-dasharray:3 3; }
svg .whisker { stroke:var(--ink2); stroke-width:2; stroke-linecap:round; }
svg .dot { stroke-width:2; } svg .dot.provisional { fill:var(--surface); stroke:var(--ink2); }
svg .dot.final { fill:var(--ink2); stroke:var(--surface); }
svg .hit { fill:transparent; }
.small { font-size:12.5px; color:var(--muted); margin:8px 0 0; }
.note { color:var(--ink2); font-size:13px; margin:10px 0 0; }
.answer { background:var(--band); border-radius:10px; padding:10px 12px; margin:0; white-space:pre-wrap;
  overflow-wrap:anywhere; font:12.5px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
th { text-align:left; font-weight:500; color:var(--muted); font-size:12.5px; padding:4px 10px 4px 0;
  border-bottom:1px solid var(--line); }
td { padding:6px 10px 6px 0; border-bottom:1px solid var(--line); vertical-align:baseline; }
td.n, th.n { text-align:right; white-space:nowrap; } td.row { font-weight:600; }
td.ans { color:var(--ink2); } td.ans .none { color:var(--muted); } td.ans .a { white-space:nowrap; }
.list { border-top:1px solid var(--line); }
details.entry { border-bottom:1px solid var(--line); }
details.entry > summary { list-style:none; cursor:pointer; padding:7px 0; display:flex; gap:8px; flex-wrap:wrap;
  align-items:baseline; }
details.entry > summary::-webkit-details-marker { display:none; }
details.entry > summary::before { content:"\\25B8"; color:var(--muted); width:12px; flex:none; }
details.entry[open] > summary::before { content:"\\25BE"; }
details.entry > summary:focus-visible { outline:2px solid var(--ink2); outline-offset:2px; }
.t { color:var(--muted); font-variant-numeric:tabular-nums; font-size:13px; }
.k { font-weight:600; } .c { color:var(--ink2); } .p { color:var(--ink2); }
.act { font-weight:600; } .mark { color:var(--bad); font-size:12.5px; font-weight:600; }
.body { padding:2px 0 12px 20px; }
.body dl { display:grid; grid-template-columns:minmax(110px,150px) minmax(0,1fr); gap:6px 14px; margin:0; }
.body dt { color:var(--muted); font-size:13px; } .body dd { margin:0; min-width:0; overflow-wrap:anywhere; }
.body pre { white-space:pre-wrap; overflow-wrap:anywhere; margin:0; background:var(--band); border-radius:8px;
  padding:8px 10px; font:12.5px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
.body blockquote { margin:6px 0; padding:6px 10px; border-left:3px solid var(--line); white-space:pre-wrap; }
.body dl.task { grid-template-columns:minmax(90px,130px) minmax(0,1fr); gap:4px 12px; }
.body dl.task .small { margin:0; } .body .notice { margin-top:4px; } .body .folds { margin-top:6px; }
.body ol { list-style:none; margin:4px 0; padding:0;
  font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
.body li.refused { color:var(--muted); text-decoration:line-through; }
.entry > .held { margin:6px 12px 0; font-size:12.5px; color:var(--pending); }
.body details summary { cursor:pointer; color:var(--muted); }
.good { color:var(--good); font-weight:600; } .bad { color:var(--bad); font-weight:600; }
.empty { color:var(--muted); padding:8px 0; }
.nav { display:flex; gap:8px; margin:8px 0 0; }
.problems { color:var(--muted); font-size:12.5px; margin-top:12px; } .problems div { margin:2px 0; }
footer { margin-top:30px; padding-top:12px; border-top:1px solid var(--line); color:var(--ink2); font-size:13px; }
@media (max-width:700px) {
  .strips { grid-template-columns:1fr 88px; } .strips .label { grid-column:1 / -1; margin-top:6px; }
  .strips .note { grid-column:1 / -1; } .strips .spacer { display:none; }
  .strips svg { grid-column:1; }
  thead th:last-child { display:none; }
  thead tr, tbody tr { display:grid; grid-template-columns:minmax(0,1fr) repeat(3,64px); column-gap:8px; }
  tbody tr { border-bottom:1px solid var(--line); padding:6px 0; } th, td { border:0; padding:2px 0; }
  td.ans { grid-column:1 / -1; font-size:13px; }
  .body { padding-left:0; } .body dl, .body dl.task { grid-template-columns:1fr; }
}
</style></head>
<body><div id="bar"></div><main>
<header><h1 id="title"></h1><span class="pill" id="pill">connecting</span></header>
<div id="card"></div>
<div class="tabs" id="tabs"></div>
<div id="head"><p class="empty" id="waiting"></p></div>
<div id="table"></div>
<section id="nowsec" hidden><h2 id="nowh"></h2><div class="list" id="now"></div></section>
<section id="feedsec" hidden><h2 id="feedh"></h2><div class="list" id="feed"></div>
<div class="nav" id="nav"></div></section>
<div id="problems" class="problems"></div>
<footer id="foot"></footer>
</main>
<script>
"use strict";
const W = """ + json.dumps(dict(LIVE_WORDS, versus=VERSUS_WORDS), ensure_ascii=True).replace("</", "<\\/") + """;
const V = W.versus, SVGNS = "http://www.w3.org/2000/svg", RESULTS_PATH = """ + json.dumps(RESULTS_PATH) + """;
""" + CARD_SCRIPT + """
let chosen = 0, before = null, whole = null, versus = false;
const nodes = new Map();            // "now:ID" or "feed:ID" -> {d, sig, at}: each entry's node, made once
const inner = new Set();            // the folds inside opened entries that the reader opened ("ID:key")
document.getElementById("waiting").textContent = W.waiting;
document.getElementById("foot").textContent = W.boundary;
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
}
function sv(tag, attrs, text) {
  const e = document.createElementNS(SVGNS, tag);
  for (const k of Object.keys(attrs || {})) e.setAttribute(k, attrs[k]);
  if (text !== undefined) e.textContent = text;
  return e;
}
function add(parent, ...kids) { for (const k of kids) if (k) parent.appendChild(k); return parent; }
const pos = v => (2 + 0.96 * Math.max(0, Math.min(100, v))) + "%";
function minutes(s) {
  if (s === null || s === undefined) return null;
  if (s < 60) return W.under_minute;
  const m = s / 60;
  return m < 90 ? W.about + " " + Math.round(m) + " " + W.minutes : W.about + " " + (m / 60).toFixed(1) + " " + W.hours;
}
function clock(s) {
  s = Math.floor(s || 0);
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m " + String(s % 60).padStart(2, "0") + "s";
  return Math.floor(s / 3600) + "h " + String(Math.floor(s % 3600 / 60)).padStart(2, "0") + "m";
}
// ---- the kind's own table: the scorecard's strips, or the head-to-head counts
function axisRow() {
  const svg = sv("svg", {class: "axis", "aria-hidden": "true"});
  for (const t of [0, 25, 50, 75, 100]) svg.appendChild(sv("text", {x: pos(t), y: 17, "text-anchor": "middle"}, String(t)));
  return svg;
}
function strip(row, final, midline) {
  const svg = sv("svg", {role: "img", "aria-label": row.label + (row.shown !== null ? ": " + row.shown : "")});
  for (const t of [0, 25, 75, 100]) svg.appendChild(sv("line", {class: "grid", x1: pos(t), x2: pos(t), y1: 2, y2: 24}));
  svg.appendChild(sv("line", {class: midline ? "mid" : "grid", x1: pos(50), x2: pos(50), y1: 2, y2: 24}));
  if (row.value !== null) {
    const g = sv("g", {});
    const exact = row.label + ": " + row.value.toFixed(1) + (row.ci ? " [" + row.ci[0].toFixed(1) + ", " +
      row.ci[1].toFixed(1) + "]" : ", no interval") + (final ? "" : " (provisional)");
    g.appendChild(sv("title", {}, exact));
    g.appendChild(sv("rect", {class: "hit", x: 0, y: 0, width: "100%", height: 26}));
    if (row.ci) g.appendChild(sv("line", {class: "whisker", x1: pos(row.ci[0]), x2: pos(row.ci[1]), y1: 13, y2: 13}));
    g.appendChild(sv("circle", {class: "dot " + (final ? "final" : "provisional"), cx: pos(row.value), cy: 13, r: 4}));
    svg.appendChild(g);
  }
  return svg;
}
function scorecard(card, notes) {
  const box = el("section");
  const h = add(el("h2", null, card && card.final ? W.scorecard : W.scorecard_so_far));
  if (card) h.appendChild(el("span", "aside", card.final ? W.final :
    W.provisional.replace("{done}", card.done).replace("{total}", card.total)));
  box.appendChild(h);
  if (!card) { box.appendChild(el("p", "empty", W.first_card)); return box; }
  if (card.problem && !card.rows) { box.appendChild(el("p", "empty", card.problem)); return box; }
  const midline = card.rows.some(r => r.midline);
  for (const r of card.rows) if (r.line) box.appendChild(el("p", "tierline", r.line));
  const grid = el("div", "strips");
  add(grid, el("div", "spacer"), axisRow(), el("div", "spacer"));
  let group = null;
  for (const r of card.rows) {
    if (r.line) continue;
    if (r.group && r.group !== group) { group = r.group; grid.appendChild(el("div", "group", group)); }
    const label = el("div", "label");
    add(label, el("b", null, r.label), r.sublabel ? el("span", null, r.sublabel) : null);
    grid.appendChild(label);
    if (r.note) { grid.appendChild(el("div", "note", r.note)); continue; }
    grid.appendChild(strip(r, card.final, r.midline));
    grid.appendChild(el("div", "value", r.shown + (r.ci ? " [" + Math.round(r.ci[0]) + ", " + Math.round(r.ci[1]) + "]" : "")));
  }
  box.appendChild(grid);
  box.appendChild(el("p", "small", (midline ? W.pair_scale + notes.midline + ". " : W.score_scale) +
    (card.final ? "" : notes.moves)));
  return box;
}
function counts(arm, final) {
  const box = el("section");
  box.appendChild(el("h2", null, arm.arm));
  const t = el("table"), head = el("tr");
  (final ? V.final_heads : V.heads).forEach((h, i) => head.appendChild(el("th", i && i < 4 ? "n" : null, h)));
  t.appendChild(add(el("thead"), head));
  const body = el("tbody");
  for (const r of arm.rows) {
    const tr = el("tr");
    add(tr, el("td", "row", r.row), el("td", "n", r.planned), el("td", "n", r.answered), el("td", "n", r.valid));
    const ans = el("td", "ans");
    if (r.answers === null) ans.appendChild(el("span", "none", V.none));
    else r.answers.forEach((a, i) => add(ans, i ? document.createTextNode(" · ") : null, el("span", "a", a[0] + " " + a[1])));
    body.appendChild(add(tr, ans));
  }
  t.appendChild(body);
  return add(box, t);
}
function table(r, s) {
  const box = el("div");
  if (!versus) { box.appendChild(scorecard(r.scorecard, s)); return box; }
  const final = r.answer !== null && r.answer !== undefined;
  if (final) add(box, el("h2", null, V.answer), el("pre", "answer", r.answer.join("\\n")));
  else if (r.refusal) add(box, el("h2", null, V.refused), el("pre", "answer", r.refusal));
  else box.appendChild(el("p", "note", V.counts));
  for (const arm of r.arms) box.appendChild(counts(arm, final));
  return box;
}
// ---- the run line and the progress
function head(r) {
  const f = r.facts, p = r.progress, box = el("div");
  if (versus) {
    box.appendChild(el("div", "names", f.names));
    box.appendChild(el("div", "facts", [f.agent + " " + f.cli, f.model, f.effort ? W.effort + " " + f.effort : null, f.tier + " " +
      W.tier, W.memory + " " + f.memory, V.strangers + " " + f.strangers].filter(Boolean).join(" · ")));
    if (f.bank) box.appendChild(el("div", "facts", f.bank));
  } else {
    const facts = el("div", "facts");
    facts.appendChild(el("div", null, [f.agent + " " + f.cli, f.model, f.effort ? W.effort + " " + f.effort : null, W.memory + " " +
      f.memory].filter(Boolean).join(" · ")));
    facts.appendChild(el("div", null, (f.kind === "single-type" ? W.target + " " + f.target + " (" + f.target_type +
      ") " + W.against + " " + f.control : W.compare + " " + f.compare) + " · " + f.tier + " " + W.tier +
      (f.recipe ? " (" + f.recipe + ")" : "")));
    box.appendChild(facts);
  }
  const bar = add(el("div", "bar"), el("i"));
  bar.firstChild.style.width = (r.status === "finished" ? 100 : p.total ? 100 * p.done / p.total : 0) + "%";
  box.appendChild(bar);
  const nums = versus ? [p.done + " / " + p.total + " " + V.calls] :
    [p.done + " of " + p.total, p.valid + " " + W.valid, p.abstained + " " + W.abstained];
  if (p.errors) nums.push(p.errors + " " + W.errors);
  if (p.elapsed_seconds !== null) nums.push(clock(p.elapsed_seconds));
  const left = p.done < p.total ? minutes(p.left_seconds) : null;
  if (left && r.status === "running") nums.push(left + " " + W.left);
  if (p.quota) nums.push(p.quota);
  box.appendChild(el("div", "nums", nums.join(" · ")));
  return box;
}
// ---- one entry: a <details> whose summary is the compact line and whose body is the opened view
function field(dl, label, ...value) {
  dl.appendChild(el("dt", null, label));
  return add(dl, add(el("dd"), ...value.map(v => typeof v === "string" ? document.createTextNode(v) : v)));
}
function fold(id, part, label, kid) {
  const d = add(el("details"), el("summary", null, label), kid);
  d.open = inner.has(id + ":" + part);
  d.addEventListener("toggle", () => { if (d.open) inner.add(id + ":" + part); else inner.delete(id + ":" + part); });
  return d;
}
function opened(e) {
  const o = e.open, dl = el("dl");
  [o.dataset, o.class, o.category, o.condition, o.parties].forEach((v, i) => field(dl, W.fields[i], v || ""));
  if (!e.errand) {
    field(dl, W.sent, el("pre", null, o.sent));
    const opts = el("div");
    for (const x of o.options) opts.appendChild(el("div", null, (x.line ? x.label + ") " + x.line : x.label) +
      " → " + W.means + " " + x.means));
    field(dl, W.options, opts);
    const a = o.action, act = el("div");
    if (a.error) act.appendChild(el("div", "bad", W.error + a.error));
    if (a.reply !== null && a.reply !== undefined) act.appendChild(el("pre", null, a.reply));
    const reading = a.valid ? W.read_as + " " + a.read_as + ", " + W.means + " " + a.means + " (" + W.is_valid + ")" :
      W.not_valid + (a.read_as === null ? e.line.mark : W.read_as + " " + a.read_as);
    act.appendChild(el("div", a.valid ? null : "bad", reading));
    field(dl, W.action, act);
    return dl;
  }
  // The task (V10), in the entry's own grammar: a labelled list, the request open as the world serves it.
  const t = o.task, task = el("dl", "task"), [F_FOR, F_BEN, F_CELL, F_REQ, F_WORK, F_SOLVED] = W.task_fields;
  field(task, F_FOR, t.works_for || "");
  field(task, F_BEN, t.benefits || "");
  field(task, F_CELL, el("div", null, t.cell || ""), ...(t.notice.length ?
    [add(el("div", "notice"), el("div", "small", W.notice), ...t.notice.map(p => el("blockquote", null, p)))] : []));
  const req = el("div", "request");
  if (!t.request.length) req.appendChild(el("div", "small", W.no_request));
  for (const m of t.request) {
    add(req, el("div", "small", [m.from && W.request_from + " " + m.from,
                                 m.signed_in_as && W.signed_in_as + " " + m.signed_in_as].filter(Boolean).join(", ")),
        el("blockquote", null, m.text || ""));
  }
  field(task, F_REQ, req);
  field(task, F_WORK, t.workspace);
  field(task, F_SOLVED, t.solved);
  const folds = add(el("div", "folds"), fold(e.id, "key", W.key, el("pre", null, t.key.join(", "))),
    fold(e.id, "opening", W.opening_sent, el("pre", null, t.opening)));
  field(dl, W.task, task, folds);
  const g = o.log, log = el("div"), ol = el("ol");
  for (const x of g.calls) {
    const li = el("li", x.refused ? "refused" : null);
    add(li, document.createTextNode((x.n === null ? "·" : x.n) + " "), el("b", null, x.tool),
        document.createTextNode(x.args ? " " + x.args : ""));
    ol.appendChild(li);
  }
  log.appendChild(ol);
  if (g.earlier) {
    const b = el("button", "more", W.all_calls + " (" + g.earlier + " " + W.earlier_calls + ")");
    b.type = "button"; b.addEventListener("click", () => { whole = e.id; tick(); });
    log.appendChild(b);
  }
  const pr = g.progress;
  log.appendChild(el("div", "small", pr.pages_read + " " + W.pages_read + " · " + pr.records_examined + " " +
    W.records_examined + " · " + pr.claims_filed + " " + W.claims_filed));
  if (g.report) log.appendChild(el("div", "small", W.report + W.reviewed + (g.report.reviewed || W.unparsed) + " · " +
    W.real + (g.report.real || W.unparsed)));
  else if (g.report_state === "not ready") log.appendChild(el("div", "small", W.report_wait));
  else if (e.state === "landed") log.appendChild(el("div", "small", W.no_report));
  field(dl, W.log, log);
  const a = o.action, act = el("div");
  if (a.error) act.appendChild(el("div", "bad", W.error + a.error));
  if (a.filed === null) act.appendChild(el("div", null, W.not_filed));
  else add(act, el("div", null, W.filed + " " + a.filed.join(", ")),
    el("div", a.matches_key ? "good" : "bad", a.matches_key ? W.matches : W.differs));
  field(dl, W.action, act);
  return dl;
}
function summary(s, e) {
  const l = e.line, kids = [el("span", "t", e.time || ""), el("span", "k", l.class), el("span", "c", l.category),
    el("span", "p", l.parties), el("span", "act", "→ " + l.action)];
  if (l.mark) kids.push(el("span", "mark", l.mark));
  s.replaceChildren(...kids);
  return s;
}
function entry(e, where) {
  const id = where + ":" + e.id;
  let n = nodes.get(id);
  if (!n) {
    const d = el("details", "entry");
    d.dataset.id = e.id;
    add(d, el("summary"), el("div", "body"));
    n = {d, sig: null, at: 0};
    if (where === "now") { d.open = true; n.at = Date.now(); }
    d.addEventListener("toggle", () => { n.at = d.open ? Date.now() : 0; if (d.open) tick(); });
    nodes.set(id, n);
  }
  n.d.dataset.seq = e.seq === null || e.seq === undefined ? "" : String(e.seq);
  // Open but not among the entries the page asks for (past the bound, opens): its last view stays, marked.
  const sig = JSON.stringify(e), held = n.d.open && !e.open && n.viewed;
  if (n.sig !== sig) {
    n.sig = sig;
    summary(n.d.firstChild, e);
    if (!held) { n.d.lastChild.replaceChildren(e.open ? opened(e) : el("p", "small", W.opening)); n.viewed = !!e.open; }
  }
  mark(n.d, held);
  return n.d;
}
function mark(d, on) {
  const m = d.querySelector(":scope > .held");
  if (on && !m) d.insertBefore(el("p", "held", W.held), d.lastChild);
  else if (!on && m) m.remove();
}
// The feed keeps every entry the reader has open (V11): one the state no longer holds stays where it is, marked as not
// refreshed, until it is closed; closed, it goes once it is outside the window. The feed is in landing order, newest
// first, the window's entries and the kept ones together.
function place(box, entries, where) {
  const want = entries.map(e => entry(e, where));
  const held = where === "feed" ?
    [...box.children].filter(c => !want.includes(c) && c.open && nodes.has(where + ":" + c.dataset.id)) : [];
  for (const c of [...box.children]) {
    if (!want.includes(c) && !held.includes(c)) { c.remove(); nodes.delete(where + ":" + c.dataset.id); }
  }
  held.forEach(c => mark(c, true));
  const order = where === "feed" ? want.concat(held).sort((a, b) => Number(b.dataset.seq) - Number(a.dataset.seq)) :
    want;
  order.forEach((d, i) => { if (box.children[i] !== d) box.insertBefore(d, box.children[i] || null); });
}
// What is being read stays where it is (V11): before a redraw, the block at the window's top edge (CSS scroll
// anchoring's own rule: the first, in page order, whose bottom is below that edge; one of the page's fixed blocks or
// one keyed entry) and its offset; after it, the page scrolls by however far that block moved, whatever grew above
// it (new entries, a longer log, the scorecard, the results card). At the very top of the page nothing is moved.
const BLOCKS = "#card, #tabs, #head, #table, #nowh, #now > details, #feedh, #feed > details, #nav, #problems";
function anchor() {
  if (window.scrollY <= 0) return null;
  const a = [...document.querySelectorAll(BLOCKS)].find(b => b.getBoundingClientRect().bottom > 0);
  return a ? {node: a, top: a.getBoundingClientRect().top} : null;
}
function keep(a) {
  if (!a || !a.node.isConnected) return;
  const moved = a.node.getBoundingClientRect().top - a.top;
  if (Math.abs(moved) >= 1) window.scrollBy(0, moved);
}
function opens() {
  return [...nodes.values()].filter(n => n.d.open && n.d.isConnected).sort((a, b) => b.at - a.at)
    .map(n => n.d.dataset.id).filter((x, i, xs) => xs.indexOf(x) === i).slice(0, W.open_max);
}
// ---- the results card (live.py's results card): first under the title row once the run in view is done, drawn only
// when it changed; it arrives with a short rise when the run finishes while the page is open (D9). The bar (D10) is
// for a reader further down: shown only while the card is outside the window (asked of the card's own box at each
// refresh and scroll; an IntersectionObserver asks again the moment the card crosses the window's edge), sliding
// down the first time, gone when the reader is back at the card, never again once dismissed. It moves nothing, takes
// no focus, and never hides the first line of an entry the reader has open (C6): with one under it, it sits just
// below that line (clear).
const cardBox = document.getElementById("card"), barBox = document.getElementById("bar");
const cardWas = new Map(), dismissed = new Set(), slid = new Set();
let current = null;
const hands = {path: RESULTS_PATH, dismiss: () => { dismissed.add(chosen); drawBar(); }};
function drawCard(r) {
  const was = cardWas.get(chosen);
  cardWas.set(chosen, !!r.card);
  cardDraw(cardBox, r.card || null, hands, was === false && !!r.card);    // seen running here, and done now
}
function drawBar() {
  const b = cardBox.getBoundingClientRect(), inView = b.bottom > 0 && b.top < innerHeight;
  const r = current, on = !!(r && r.bar) && !inView && !dismissed.has(chosen);
  if (cardDraw(barBox, on ? r.bar : null, hands) && on && !slid.has(chosen)) {
    slid.add(chosen);
    barBox.firstChild.classList.add("rc-slide");
  }
  clear();
}
function clear() {
  const bar = barBox.firstChild;
  if (!bar) return;
  const lines = [...document.querySelectorAll("details.entry[open] > summary")].map(x => x.getBoundingClientRect());
  let top = 0;
  for (let i = 0; i <= lines.length; i++) {
    const h = bar.offsetHeight, hit = lines.find(l => l.bottom > top && l.top < top + h);
    if (!hit) break;
    top = hit.bottom;
  }
  bar.style.top = top + "px";
}
if ("IntersectionObserver" in window) new IntersectionObserver(() => drawBar()).observe(cardBox);
window.addEventListener("scroll", drawBar, {passive: true});
window.addEventListener("resize", drawBar);
// ---- the page
function render(s) {
  versus = !s.runs;
  const runs = s.runs || [s.run];
  if (chosen >= runs.length) chosen = runs.length - 1;
  document.getElementById("title").textContent = versus ? V.title : W.title;
  const tabs = document.getElementById("tabs");
  tabs.replaceChildren();
  if (runs.length > 1) runs.forEach((r, i) => {
    const b = el("button", i === chosen ? "on" : null, r.name + " · " + r.status);
    b.addEventListener("click", () => {
      if (i !== chosen) { chosen = i; before = null; whole = null; reset(); tick(); }
    });
    tabs.appendChild(b);
  });
  const r = runs[chosen], pill = document.getElementById("pill");
  pill.className = "pill " + r.status; pill.textContent = r.status;
  current = r;
  drawCard(r);
  const show = (id, on) => { document.getElementById(id).hidden = !on; };
  if (r.status === "waiting") {
    const words = runs.length > 1 ? r.name + W.starts_after : W.waiting;
    document.getElementById("head").replaceChildren(el("p", "empty", words));
    for (const id of ["table", "problems"]) document.getElementById(id).replaceChildren();
    show("nowsec", false); show("feedsec", false);
    return;
  }
  document.getElementById("head").replaceChildren(head(r));
  document.getElementById("table").replaceChildren(table(r, s));
  show("nowsec", true);
  const nh = document.getElementById("nowh");
  nh.replaceChildren(document.createTextNode(W.now));
  if (r.now) nh.appendChild(el("span", "aside", r.now.heading));
  const now = document.getElementById("now");
  if (r.now && r.now.entries.length) place(now, r.now.entries, "now");
  else { place(now, [], "now"); now.replaceChildren(el("p", "empty", W.no_errand)); }
  show("feedsec", true);
  const fh = document.getElementById("feedh");
  fh.replaceChildren(document.createTextNode(W.feed),
    el("span", "aside", W.newest_first + " · " + r.feed.landed + " " + W.landed));
  const feed = document.getElementById("feed");
  const mine = e => { const n = nodes.get("feed:" + e.id); return !!(n && n.d.open); };   // opened here, in the feed
  const shown = r.feed.entries.concat((r.feed.kept || []).filter(mine));
  if (shown.length) place(feed, shown, "feed");
  else { place(feed, [], "feed"); feed.replaceChildren(el("p", "empty", W.nothing_yet)); }
  const nav = document.getElementById("nav");
  nav.replaceChildren();
  const last = r.feed.entries.length ? r.feed.entries[r.feed.entries.length - 1].seq : null;
  if (last !== null && r.feed.first !== null && last > r.feed.first) {
    const b = el("button", null, W.earlier); b.type = "button";
    b.addEventListener("click", () => { before = last; tick(); });
    nav.appendChild(b);
  }
  if (before !== null) {
    const b = el("button", null, W.newest); b.type = "button";
    b.addEventListener("click", () => { before = null; tick(); });
    nav.appendChild(b);
  }
  const pr = document.getElementById("problems");
  pr.replaceChildren();
  if (r.unreadable) pr.appendChild(el("div", null, W.unreadable + ": " + r.unreadable));
  for (const x of r.problems) pr.appendChild(el("div", null, x));
}
function reset() {
  nodes.clear(); inner.clear();
  document.getElementById("now").replaceChildren(); document.getElementById("feed").replaceChildren();
}
let busy = false;
async function tick() {
  if (busy) return;
  busy = true;
  try {
    const q = "&run=" + chosen + (opens().length ? "&open=" + opens().join(",") : "") +
      (before !== null ? "&before=" + before : "") + (whole !== null ? "&all=" + whole : "");
    const res = await fetch("/state" + location.search + q, {cache: "no-store"});
    if (!res.ok) throw new Error(String(res.status));
    const s = await res.json(), a = anchor();
    render(s);
    keep(a);
    drawBar();
  } catch (err) {
    const pill = document.getElementById("pill");
    pill.className = "pill stopped"; pill.textContent = W.lost;
  } finally { busy = false; }
}
tick(); setInterval(tick, 1000);
</script></body></html>
"""
