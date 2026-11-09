#!/usr/bin/env python3
"""live: watching a probatio v4 run while it goes, for single-type and cross-type runs on Claude Code or Codex.

Two views read the run, and neither writes anything in its results folder:

    LiveLine       the default: one line on stderr with units done of total, valid, abstained, errors, elapsed, the
                   time left at this run's own pace and the quota; redrawn in place in a terminal, clipped by display
                   columns, or one plain checkpoint line per tenth of the run without one (no CR, no escape)
    Watch          --watch: a page on 127.0.0.1 behind a random token (serve_page), one tab per run of the command,
                   with the progress, the errands as they happen, the questions as they land and a provisional
                   scorecard; it shows what the agent wrote, which can quote what it knows
      RunView        the observer of one run: it reads replies.jsonl and the shown errands' record.jsonl through
                     CommittedLines, and followup.json defensively; nothing else in the folder, and never the memory
                     files, quota.jsonl, the Codex homes or a transcript
      ViewRedactor   every free-text field passes it before it is serialized (calibration/redact.py's contract)
      Provisional    one coalescing worker per run scores the full plan on one snapshot with the final scorers; the
                     final card replaces it the moment the run is scored

probatio.py imports this module for a v4 run (import_live), and this module imports probatio's helpers, as ordo.py
does, so the dependency runs one way at import time. Section numbers refer to the design of the quick run and the
run you can watch (revision 2, section 4).

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
import sys
import threading
import time
import unicodedata
from collections import OrderedDict, deque
from pathlib import Path

from probatio import (AGENT_NAMES, CLASS_NAMES, CONDITION_NAMES, NOT_RUN_IN, QUICK_CARD_LINE, REFUSED, TIER_CLASSES,
                      V4_CLASSES, duration, episode_dir, episode_facts, key, parse_report, whole)

# ---- thresholds, caps and words: one home ----------------------------------------------------------------------------
TICK_SECONDS = 1.0              # a terminal's line is redrawn each second and on every finished call (4.1)
MIN_COLUMNS = 30                # below this many columns the line falls back to checkpoints
CHECKPOINTS = 10                # without a terminal, a line at each tenth of the run, then one at the end
MEAN_AFTER = 3                  # a kind of call uses this launch's mean once 3 of it finished, the profile's before
PREFIX = "probatio: "           # every checkpoint line starts with it
CAPS = {"reply": 300, "args": 90, "filed": 200, "error": 200, "request": 300}     # characters, after redaction (4.3)
QUESTIONS_SHOWN = 30            # the newest questions on the page
CALLS_SHOWN = 14                # the newest tool calls of a card
PROBLEMS_SHOWN = 5              # the newest observer failures, as fixed sentences
REFRESH_SECONDS = 0.5           # the observer reads the folder at most this often, however many tabs ask
PREFIX_BYTES = 256              # the committed bytes a reader keeps to check that what it read is still there
PROVISIONAL_SECONDS = 30        # at most one provisional scoring starts per 30 s (4.4)
CELL_WORDS = {"N0": "plain world", "U": "told unobserved", "O": "told observed"}
KIND_WORDS = {"single": "single-type", "cross": "cross-type"}
BOUNDARY = ("Shows what the agent wrote, which can quote what it knows. Local to this machine; secret-shaped text is "
            "redacted.")
MOVES = "Every number moves until the last call; the result is the final scorecard."
MIDLINE_NOTE = "above 50 favours the first type named"
REPLY_FIELDS = {"condition": str, "dataset": str, "id": str, "error": (str, type(None)), "seconds": (int, float)}
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
    """A run label safe to draw: control and format characters (bidi overrides included) removed, and runs of
    whitespace made one space."""
    kept = "".join(ch for ch in str(text) if unicodedata.category(ch) not in ("Cc", "Cf"))
    return " ".join(kept.split())


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
                    self.pending[key(c)] = (call_kind(c), float(self.prior(c)))
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
        """Seconds left at this run's own pace, or None when nothing is pending."""
        if not self.pending:
            return None
        mean = {k: sum(v) / len(v) for k, v in self.seconds.items() if len(v) >= MEAN_AFTER}
        return sum(mean.get(kind, prior) for kind, prior in self.pending.values()) / self.jobs

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

    def write(self, text):
        self.stream.write(text)
        self.stream.flush()

    def draw(self):
        """One draw: in place in a wide terminal; else a checkpoint when the run has passed another tenth."""
        with self.lock:
            if self.closed:
                return
            if self.in_place():
                width = self.width() - 2
                self.write("\r  " + clip(self.text(width), width) + "\x1b[K")
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
                self.write("\r\x1b[K" + message + "\n")
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
SECRET_NAME = re.compile(r"TOKEN|API_KEY|SECRET|PASSWORD|AUTH", re.I)
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


class ViewRedactor:
    """scrub(text): control and format characters other than newline and tab removed (so none can split a secret),
    then every known value, then every key shape, each as <redacted>. Built from probatio's own environment and the
    page's token."""

    def __init__(self, environ=None, extra=()):
        environ = os.environ if environ is None else environ
        values = {v for k, v in environ.items() if SECRET_NAME.search(k) and isinstance(v, str)}
        values |= {v for v in extra if isinstance(v, str)}
        self.values = sorted((v for v in values if len(v) >= MIN_SECRET), key=len, reverse=True)

    def __call__(self, text):
        if text is None:
            return text
        text = "".join(ch for ch in str(text) if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf"))
        for v in self.values:
            text = text.replace(v, MARK)
        return KEY_SHAPES.sub(MARK, text)

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
def valid_line(rec, fields):
    """Whether a decoded line is a dict with its required keys of their types (never a bool for a number)."""
    if not isinstance(rec, dict):
        return False
    for name, kinds in fields.items():
        value = rec.get(name, ...)
        if value is ... or not isinstance(value, kinds) or (isinstance(value, bool) and bool not in
                                                             (kinds if isinstance(kinds, tuple) else (kinds,))):
            return False
    return True


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
                result = {"rows": self.rows(self.score(snapshot), False)}
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
        rows = self.rows(card, True)
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
    plans I and II) leads with the scorecard's own line for it (QUICK_CARD_LINE), and a class it does not plan reads
    as the scorecard's row does (NOT_RUN_IN: "not run in a quick run"), with no mark."""
    tier, conds = card.get("tier"), card["conditions"]
    planned = TIER_CLASSES.get(tier, V4_CLASSES)
    rows = [{"line": QUICK_CARD_LINE}] if tier in TIER_CLASSES else []
    for cond, rec in conds.items():
        for k in V4_CLASSES:
            cls = rec["classes"][k]
            note = None
            if k not in planned:
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
    """A cross-type card's page rows, for ordo's card: one strip per pair under each row that ran, its index around
    the 50 line; Class III only where ordo reads it as valid. A quick card has only its role-free rows and leads with
    ordo's own line naming the rest (ordo.QUICK_CARD_LINE), as its scorecard does."""
    def rows_of(card, final):
        rows, conds = ([{"line": ordo.QUICK_CARD_LINE}] if card.get("tier") in TIER_CLASSES else []), card["conditions"]
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
                                 "label": pn, "sublabel": None, "value": None if note else float(p["value"]),
                                 "shown": None if note else whole(p["value"]),
                                 "ci": None if note else interval(p.get("ci")), "note": note, "midline": True})
        return rows
    return rows_of


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
    run facts and the entity names come from the caller; the progress from the live line; the committed replies
    from replies.jsonl; the newest task's errands from their record.jsonl, followup.json and world.json's identity.
    Every read is caught and becomes a fixed sentence naming the call and the exception's class. state() returns only
    the whitelisted fields, every free text redacted and capped."""

    def __init__(self, name, out, calls, facts, names, components, redactor, line=None, provisional=None,
                 clock=time.monotonic, now=time.localtime):
        self.name, self.out, self.calls, self.facts, self.names = name, Path(out), calls, facts, names
        self.components, self.redact, self.line, self.provisional = components, redactor, line, provisional
        self.clock, self.now = clock, now
        self.by_key = {key(c): c for c in calls}
        self.lock = threading.Lock()
        self.status = "waiting"
        self.replies = CommittedLines(self.out / "replies.jsonl", REPLY_FIELDS)
        self.done, self.seen, self.facts_of, self.questions = {}, {}, {}, deque(maxlen=QUESTIONS_SHOWN)
        self.retry = {}                 # call key -> (record, attempt): committed errands whose facts await a reread
        self.cards, self.task, self.problems = {}, None, OrderedDict()
        self.snapshot_id, self.triggered, self.last_refresh = 0, -1, None

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

    def read_replies(self):
        reset, new = self.replies.read()
        if reset:
            self.done, self.seen, self.facts_of, self.retry = {}, {}, {}, {}
            self.questions.clear()
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
                if k in self.questions:
                    self.questions.remove(k)
                self.questions.append(k)
        for k, (rec, attempt) in queued:
            if self.retry.get(k) == (rec, attempt):
                self.retry_capture(self.by_key[k], rec, attempt)

    def capture(self, call, rec, attempt):
        """A committed errand's episode_facts, read once when its reply line lands: both CLIs have written its record
        and report by then (Codex's report leg runs inside the call), so they are final, and every later snapshot
        holds this copy. The record and its facts enter done and facts_of together, as one new snapshot. A failed
        read keeps the errand out of both (a missing unit until then), says so, and queues it in retry under its call
        key with the committed record and the attempt (folder_identity) it belongs to, so a later refresh reads it
        again (Codex's final check of the round, finding 4)."""
        k = key(call)
        try:
            facts = episode_facts(call, rec, self.out)
        except Exception as e:
            self.done.pop(k, None)
            self.facts_of.pop(k, None)
            self.retry[k] = (rec, attempt)
            self.problem(call["id"], e, "facts")
            return
        self.retry.pop(k, None)
        self.done[k], self.facts_of[k] = rec, facts
        self.snapshot_id += 1
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
        over."""
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
        for c in self.calls:
            if c.get("episode") and task_of(c) == self.task:
                card = self.cards.setdefault(key(c), Card(c, episode_dir(self.out, c)))
                try:
                    card.refresh()
                    self.resolved(c["id"], "card")
                except Exception as e:
                    self.problem(c["id"], e, "card")

    # -- what the page shows
    def name_of(self, value):
        return self.names.get(value, value) if isinstance(value, str) else value

    def answer_text(self, rec):
        if rec.get("error"):
            return "error"
        outcome = rec.get("outcome")
        if outcome is None:
            return "abstained"
        if isinstance(outcome, dict):
            return ", ".join(f"{k} {self.name_of(v)}" for k, v in outcome.items())
        return str(self.name_of(outcome))

    def question_row(self, k):
        call, rec = self.by_key[k], self.done[k]
        comp = call["meta"].get("component") or ""
        options = [self.name_of(c.get("maps_to")) for c in call["row"].get("choices") or []]
        return {"time": self.seen.get(k), "component": " ".join(x for x in (comp, self.components.get(comp)) if x),
                "options": self.redact.brief(" or ".join(map(str, options)) if options else "a rating 0 to 10",
                                             CAPS["request"]),
                "answer": self.redact.brief(self.answer_text(rec), CAPS["reply"]),
                "reply": self.redact.brief(rec.get("reply") or "", CAPS["reply"]),
                "error": self.redact.brief(rec.get("error"), CAPS["error"])}

    def card_view(self, call):
        card, rec = self.cards.get(key(call)), self.done.get(key(call))
        meta, row = call["meta"], call["row"]
        calls = card.calls if card else []
        filed = [x for x in calls if x.get("tool") == row.get("finish_tool")]
        status = ("error" if rec.get("error") else "done") if rec else \
            ("working" if card and card.identity else "waiting")
        role = meta.get("side") if meta.get("side") in ("target", "control") else meta.get("beneficiary")
        report_state, report = card.report if card else ("none", None)
        words = None
        if report_state == "ready":
            parsed = parse_report(report)
            words = {"reviewed": parsed.get(1), "real": parsed.get(2)}
        outcome = (rec or {}).get("outcome") if rec and not rec.get("error") else None
        return {"for": self.redact.brief(call.get("for") or "", CAPS["request"]), "role": role, "status": status,
                "attempt": card.attempt if card else 0, "tool_calls": len(calls),
                "calls": [{"n": x.get("n"), "tool": self.redact.brief(str(x.get("tool")), 40),
                           "args": self.redact.brief(args_line(x.get("args")), CAPS["args"]),
                           "refused": any(x.get(m) for m in REFUSED) or x.get("n") is None}
                          for x in calls[-CALLS_SHOWN:]],
                "earlier_calls": max(0, len(calls) - CALLS_SHOWN),
                "filed": self.redact.brief(args_line(filed[-1].get("args")), CAPS["filed"]) if filed else None,
                "solved": None if not isinstance(outcome, dict) else bool(outcome.get("solved")),
                "report": words, "report_state": report_state if rec or report_state == "ready" else None,
                "error": self.redact.brief((rec or {}).get("error"), CAPS["error"]),
                "request": self.redact.brief(call.get("prompt") or "", CAPS["request"])}

    def errands_view(self):
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
        cards = [self.card_view(c) for c in self.calls if c.get("episode") and task_of(c) == self.task]
        return {"heading": " · ".join(h for h in heading if h), "cards": cards}

    def progress(self):
        if self.line is not None:
            return self.line.snapshot()
        valid = sum(1 for k, r in self.done.items() if not r.get("error") and
                    (self.by_key[k].get("episode") or r.get("outcome") is not None))
        errors = sum(1 for r in self.done.values() if r.get("error"))
        return {"done": len(self.done), "total": len(self.calls), "valid": valid,
                "abstained": len(self.done) - valid - errors, "errors": errors, "elapsed_seconds": None,
                "left_seconds": None, "quota": None, "finished": self.status != "running"}

    def state(self):
        """The whitelisted state of this run for /state."""
        self.refresh()
        with self.lock:
            card = self.provisional.latest() if self.provisional is not None else None
            return {"name": self.redact.brief(self.name, 120), "status": self.status, "facts": self.facts,
                    "progress": self.progress(), "scorecard": card,
                    "errands": self.errands_view() if self.status != "waiting" else None,
                    "questions": [self.question_row(k) for k in reversed(self.questions)],
                    "unreadable": self.replies.unreadable + sum(c.lines.unreadable for c in self.cards.values()
                                                                 if c.lines),
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


def serve_page(page, state, redactor=None, port=0):
    """Serve one page and its state on 127.0.0.1 from a background thread for the life of the process; returns the
    server, with .url (holding the token) and .token. Every request must carry the token (?t=, compared with
    hmac.compare_digest) and a Host of 127.0.0.1:PORT or localhost:PORT (a DNS-rebinding page has neither), else 403;
    GET serves "/" (the page) and "/state" (state() as JSON, redacted again whole as a last pass) and nothing else
    (404); any other method is 405. Every response carries the security headers and the page's CSP, which allows its
    inline script and style by hash alone. No file is served and nothing is logged. .redactor (which knows the token)
    is there for a state that must redact a field before cutting it (v3's view_state)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlsplit
    token = secrets.token_urlsafe(24)
    redactor = redactor or ViewRedactor(extra=(token,))
    if token not in redactor.values:
        redactor = ViewRedactor(extra=(token, *redactor.values))
    policy, body_page = content_policy(page), page.encode("utf-8")

    class Page(BaseHTTPRequestHandler):
        server_version, sys_version = "probatio", ""
        error_message_format, error_content_type = "%(code)d %(message)s\n", "text/plain; charset=utf-8"

        def end_headers(self):
            for name, value in SECURITY_HEADERS + (("Content-Security-Policy", policy),):
                self.send_header(name, value)
            super().end_headers()

        def allowed(self):
            port_ = self.server.server_address[1]
            if self.headers.get("Host") not in (f"127.0.0.1:{port_}", f"localhost:{port_}"):
                return False
            given = parse_qs(urlsplit(self.path).query).get("t") or [""]
            return len(given) == 1 and hmac.compare_digest(given[0].encode("utf-8"), token.encode("utf-8"))

        def do_GET(self):
            if not self.allowed():
                self.send_error(403)
                return
            path = urlsplit(self.path).path
            if path == "/":
                body, kind = body_page, "text/html; charset=utf-8"
            elif path == "/state":
                body = json.dumps(redact_all(state(), redactor), ensure_ascii=True).encode("utf-8")
                kind = "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def refuse(self):
            self.send_response(405)
            self.send_header("Allow", "GET")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = do_TRACE = do_CONNECT = refuse

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
    tier, recipe, memory mode, the target and control names (single-type) or the comparison (cross-type)."""
    facts = {"agent": AGENT_NAMES.get(meta.get("agent"), meta.get("agent")), "cli": version_of(meta.get("cli")),
             "model": meta.get("model") or "", "effort": meta.get("effort") or "", "tier": meta.get("tier"),
             "recipe": meta.get("recipe"), "memory": meta.get("memory"), "kind": KIND_WORDS[kind]}
    if kind == "single" and roster:
        facts.update(target=roster["T"]["full"], target_type=roster["type"], control=roster["X"]["full"])
    elif kind == "cross":
        facts.update(compare=meta.get("compare"))
    return facts


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
    return {x["key"]: x["full"] for x in records if isinstance(x.get("key"), str) and isinstance(x.get("full"), str)}


class Watch:
    """--watch for one command: the page, with a tab for each run of the command (names, in order), each a RunView
    from the moment its run starts (start) and given its final card the moment it is scored (finish). The URL is
    printed once on stderr, before the first call, with the boundary sentence; the page serves for the life of the
    process."""

    def __init__(self, names, stream=None):
        self.names, self.views, self.lock = [plain_label(n) for n in names], [], threading.Lock()
        self.server = serve_page(VIEW_V4, self.state)
        self.redactor = ViewRedactor(extra=(self.server.token,))
        stream = stream or sys.stderr
        stream.write(f"  watch it live: {self.server.url}\n  {BOUNDARY}\n")
        stream.flush()

    def start(self, name, out, calls, facts, names, components, line=None, score=None, rows=None):
        """The next run of the command starts: its view, running. score(done) and rows(card, final) give its
        provisional card (Provisional); without them the page shows no scorecard."""
        provisional = Provisional(score, rows, len(calls)) if score and rows else None
        view = RunView(name, out, calls, facts, names, components, self.redactor, line, provisional)
        view.status = "running"
        with self.lock:
            self.views.append(view)
        return view

    def finish(self, view, card=None):
        """The run is scored: its final card is published at once and provisional scoring stops."""
        with view.lock:
            view.status, view.last_refresh = "finished", None
        if view.provisional is not None and card is not None:
            view.provisional.finalize(card)

    def state(self):
        with self.lock:
            views = list(self.views)
        waiting = [{"name": n, "status": "waiting"} for n in self.names[len(views):]]
        return {"runs": [v.state() for v in views] + waiting, "boundary": BOUNDARY, "moves": MOVES,
                "midline": MIDLINE_NOTE}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# ---- the page (4.6; the dataviz method) ------------------------------------------------------------------------------
# One series per strip: a 2px whisker with round caps and an 8px dot, hollow while provisional and filled (with a 2px
# surface ring) when final; the value printed beside each strip as its table view, and a hover title with the exact
# numbers; a recessive grid at 0, 25, 50, 75 and 100, with a dashed midline at 50 for a cross-type pair. Text wears the
# ink tokens; the status colours appear only with their words. Colour tokens live on :root with a dark mode, the
# cards stack under 700 px, and the script builds every node with textContent (never innerHTML), so nothing the
# agent wrote can become markup. The token is never embedded: the script reads it from location.search.
VIEW_V4 = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>probatio live</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer">
<link rel="icon" href="data:,">
<style>
:root { --surface:#fcfcfb; --band:#f4f3ef; --ink:#0b0b0b; --ink2:#52514e; --muted:#6f6e69; --grid:#e1e0d9;
  --axis:#c3c2b7; --line:#e1e0d9; --good:#1f7a45; --bad:#b4372d; --pending:#8a6d1a; --hl:#f3efe4; }
@media (prefers-color-scheme: dark) {
  :root { --surface:#1a1a19; --band:#222220; --ink:#f0efec; --ink2:#c3c2b7; --muted:#9a9993; --grid:#2c2c2a;
    --axis:#4a4a46; --line:#33332f; --good:#5cc68b; --bad:#ef7d72; --pending:#d9b45a; --hl:#2a2925; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--surface); color:var(--ink);
  font:14px/1.45 system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif; }
main { max-width:1080px; margin:0 auto; padding:20px 16px 40px; }
header { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
h1 { font-size:20px; margin:0; font-weight:650; }
h2 { font-size:13px; text-transform:uppercase; letter-spacing:.04em; color:var(--ink2); margin:26px 0 10px;
  font-weight:600; display:flex; gap:12px; flex-wrap:wrap; align-items:baseline; }
h2 .aside { text-transform:none; letter-spacing:0; font-weight:500; color:var(--muted); margin-left:auto; }
.pill { font-size:13px; color:var(--ink2); display:inline-flex; align-items:center; gap:6px; margin-left:auto; }
.pill::before { content:""; width:9px; height:9px; border-radius:50%; background:var(--muted); }
.pill.running::before { background:var(--good); } .pill.stopped::before { background:var(--bad); }
.pill.finished::before { background:var(--ink2); }
.facts { color:var(--ink2); margin:4px 0 0; } .facts div { margin:2px 0; }
.tabs { display:flex; gap:6px; flex-wrap:wrap; margin:14px 0 0; }
.tabs button { font:inherit; font-size:13px; color:var(--ink2); background:transparent; border:1px solid var(--line);
  border-radius:8px; padding:5px 10px; cursor:pointer; }
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
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; }
.card { border:1px solid var(--line); border-radius:10px; overflow:hidden; background:var(--surface);
  display:flex; flex-direction:column; min-width:0; }
.card .top { padding:10px 12px; background:var(--band); }
.card .who { font-weight:600; } .card .role { color:var(--muted); margin-left:6px; }
.card .status { font-size:13px; color:var(--ink2); margin-top:2px; }
.status .good { color:var(--good); font-weight:600; } .status .bad { color:var(--bad); font-weight:600; }
.status .pending { color:var(--pending); font-weight:600; }
.card ol { list-style:none; margin:0; padding:6px 0; font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  max-height:260px; overflow:auto; }
.card li { padding:0 12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.card li b { font-weight:600; } .card li.refused { color:var(--muted); text-decoration:line-through; }
.card li.more { color:var(--muted); }
.card .foot { border-top:1px solid var(--line); padding:8px 12px; font-size:13px; color:var(--ink2); }
.card .foot div + div { margin-top:4px; } .card .foot .k { color:var(--muted); }
.card details { padding:0 12px 8px; font-size:12.5px; color:var(--ink2); } .card summary { cursor:pointer; color:var(--muted); }
.card details p { white-space:pre-wrap; margin:6px 0 0; }
.qs { border-top:1px solid var(--line); }
.q { display:grid; grid-template-columns:70px minmax(0,1.3fr) minmax(0,1fr); gap:10px; padding:7px 0;
  border-bottom:1px solid var(--line); }
.q .t { color:var(--muted); font-variant-numeric:tabular-nums; font-size:13px; }
.q .c { font-weight:600; } .q .o { color:var(--ink2); }
.q .a { font-weight:600; } .q .a.abst { color:var(--muted); font-weight:500; } .q .a.err { color:var(--bad); }
.q .r { grid-column:2 / -1; color:var(--muted); font-size:12.5px; white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis; }
.empty { color:var(--muted); padding:8px 0; }
.problems { color:var(--muted); font-size:12.5px; } .problems div { margin:2px 0; }
footer { margin-top:30px; padding-top:12px; border-top:1px solid var(--line); color:var(--ink2); font-size:13px; }
@media (max-width:700px) {
  .cards { grid-template-columns:1fr; }
  .strips { grid-template-columns:1fr 88px; } .strips .label { grid-column:1 / -1; margin-top:6px; }
  .strips .note { grid-column:1 / -1; } .strips .spacer { display:none; }
  .strips svg { grid-column:1; } .q { grid-template-columns:62px 1fr; } .q .a { grid-column:2; }
}
</style></head>
<body><main>
<header><h1>probatio · live</h1><span class="pill" id="pill">connecting</span></header>
<div class="tabs" id="tabs"></div>
<div id="run"><p class="empty">Waiting for the run to start.</p></div>
<footer id="foot"></footer>
</main>
<script>
"use strict";
const SVGNS = "http://www.w3.org/2000/svg";
let chosen = 0;
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
  if (s < 60) return "under a minute";
  const m = s / 60;
  return m < 90 ? "about " + Math.round(m) + " min" : "about " + (m / 60).toFixed(1) + " hours";
}
function clock(s) {
  s = Math.floor(s || 0);
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m " + String(s % 60).padStart(2, "0") + "s";
  return Math.floor(s / 3600) + "h " + String(Math.floor(s % 3600 / 60)).padStart(2, "0") + "m";
}
function axisRow(midline) {
  const svg = sv("svg", {class: "axis", "aria-hidden": "true"});
  for (const t of [0, 25, 50, 75, 100]) {
    svg.appendChild(sv("text", {x: pos(t), y: 17, "text-anchor": "middle"}, String(t)));
  }
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
function scorecard(s, card, notes) {
  const box = el("section");
  const h = add(el("h2", null, card && card.final ? "Scorecard" : "Scorecard so far"));
  if (card) h.appendChild(el("span", "aside", card.final ? "final" :
    "provisional: " + card.done + " of " + card.total + " units"));
  box.appendChild(h);
  if (!card) { box.appendChild(el("p", "empty", "The first provisional scorecard comes with the first replies.")); return box; }
  if (card.problem && !card.rows) { box.appendChild(el("p", "empty", card.problem)); return box; }
  const midline = card.rows.some(r => r.midline);
  for (const r of card.rows) if (r.line) box.appendChild(el("p", "tierline", r.line));
  const grid = el("div", "strips");
  add(grid, el("div", "spacer"), axisRow(midline), el("div", "spacer"));
  let group = null;
  for (const r of card.rows) {
    if (r.line) continue;                      // the tier's own line, printed above the chart
    if (r.group && r.group !== group) { group = r.group; grid.appendChild(el("div", "group", group)); }
    const label = el("div", "label");
    add(label, el("b", null, r.label), r.sublabel ? el("span", null, r.sublabel) : null);
    grid.appendChild(label);
    if (r.note) { grid.appendChild(el("div", "note", r.note)); continue; }
    grid.appendChild(strip(r, card.final, r.midline));
    grid.appendChild(el("div", "value", r.shown + (r.ci ? " [" + Math.round(r.ci[0]) + ", " + Math.round(r.ci[1]) + "]" : "")));
  }
  box.appendChild(grid);
  box.appendChild(el("p", "small", (midline ? "Pair indices 0 to 100: " + notes.midline + ". " : "Scores 0 to 100 with 95% intervals. ") +
    (card.final ? "" : notes.moves)));
  return box;
}
function verdict(c) {
  const s = el("div", "status");
  const say = (cls, text) => s.appendChild(el("span", cls, text));
  if (c.status === "waiting") s.textContent = "waiting to start";
  else if (c.status === "working") s.textContent = "working · attempt " + c.attempt + " · " + c.tool_calls + " tool calls";
  else if (c.status === "error") { say("bad", "error"); s.appendChild(document.createTextNode(" " + (c.error || ""))); }
  else {
    if (c.solved === true) say("good", "filed the right claim");
    else if (c.filed) say("bad", "filed a wrong claim");
    else say("bad", "filed nothing");
    s.appendChild(document.createTextNode(" · " + c.tool_calls + " tool calls"));
  }
  return s;
}
function errandCard(c) {
  const card = el("div", "card");
  const top = el("div", "top");
  add(top, add(el("div"), el("span", "who", "for " + c.for), el("span", "role", c.role || "")), verdict(c));
  card.appendChild(top);
  const ol = el("ol");
  if (c.earlier_calls) ol.appendChild(el("li", "more", "… " + c.earlier_calls + " earlier calls"));
  for (const x of c.calls) {
    const li = el("li", x.refused ? "refused" : null);
    add(li, document.createTextNode((x.n === null ? "·" : x.n) + " "), el("b", null, x.tool),
        document.createTextNode(x.args ? " " + x.args : ""));
    li.title = x.tool + (x.args ? " " + x.args : "");
    ol.appendChild(li);
  }
  if (!c.calls.length) ol.appendChild(el("li", "more", c.status === "waiting" ? "not started" : "no tool calls yet"));
  card.appendChild(ol);
  const foot = el("div", "foot");
  if (c.filed) add(foot, add(el("div"), el("span", "k", "filed "), document.createTextNode(c.filed)));
  if (c.report) add(foot, add(el("div"), el("span", "k", "report: "),
    document.createTextNode("reviewed? " + (c.report.reviewed || "unparsed") + " · real? " + (c.report.real || "unparsed"))));
  else if (c.report_state === "none" && c.status === "done") add(foot, el("div", "k", "no report yet"));
  else if (c.report_state === "not ready") add(foot, el("div", "k", "report not ready"));
  if (foot.childNodes.length) card.appendChild(foot);
  const d = el("details");
  add(d, el("summary", null, "the request"), el("p", null, c.request));
  card.appendChild(d);
  return card;
}
function questions(qs) {
  const box = el("section");
  add(box, add(el("h2", null, "Questions as they land"), el("span", "aside", "newest first")));
  if (!qs.length) { box.appendChild(el("p", "empty", "No question has come back yet.")); return box; }
  const list = el("div", "qs");
  for (const q of qs) {
    const row = el("div", "q");
    const cls = q.answer === "abstained" ? "a abst" : q.answer === "error" ? "a err" : "a";
    add(row, el("div", "t", q.time), add(el("div"), el("span", "c", q.component), el("span", "o", " · " + q.options)),
        el("div", cls, "→ " + q.answer), el("div", "r", q.error ? q.error : q.reply));
    list.appendChild(row);
  }
  box.appendChild(list);
  return box;
}
function render(s) {
  const runs = s.runs;
  if (!runs.length) return;
  if (chosen >= runs.length) chosen = runs.length - 1;
  const tabs = document.getElementById("tabs");
  tabs.replaceChildren();
  if (runs.length > 1) runs.forEach((r, i) => {
    const b = el("button", i === chosen ? "on" : null, r.name + " · " + r.status);
    b.addEventListener("click", () => { chosen = i; render(s); });
    tabs.appendChild(b);
  });
  const r = runs[chosen], f = r.facts, p = r.progress;
  const pill = document.getElementById("pill");
  pill.className = "pill " + r.status; pill.textContent = r.status;
  document.getElementById("foot").textContent = s.boundary;
  if (r.status === "waiting") {
    document.getElementById("run").replaceChildren(el("p", "empty", r.name + " starts when the run before it ends."));
    return;
  }
  const box = el("div");
  const facts = el("div", "facts");
  facts.appendChild(el("div", null, [f.agent + " " + f.cli, f.model, "effort " + f.effort, "memory " + f.memory]
    .filter(Boolean).join(" · ")));
  facts.appendChild(el("div", null, (f.kind === "single-type" ? "target " + f.target + " (" + f.target_type +
    ") against " + f.control : "compare " + f.compare) + " · " + f.tier + " tier" + (f.recipe ? " (" + f.recipe + ")" : "")));
  box.appendChild(facts);
  const bar = add(el("div", "bar"), el("i"));
  bar.firstChild.style.width = (p.total ? 100 * p.done / p.total : 0) + "%";
  box.appendChild(bar);
  const nums = [p.done + " of " + p.total, p.valid + " valid", p.abstained + " abstained"];
  if (p.errors) nums.push(p.errors + " errors");
  if (p.elapsed_seconds !== null) nums.push(clock(p.elapsed_seconds));
  const left = p.done < p.total ? minutes(p.left_seconds) : null;
  if (left && r.status === "running") nums.push(left + " left");
  if (p.quota) nums.push(p.quota);
  box.appendChild(el("div", "nums", nums.join(" · ")));
  box.appendChild(scorecard(s, r.scorecard, s));
  const e = el("section");
  const eh = el("h2", null, "Errands now");
  if (r.errands) eh.appendChild(el("span", "aside", r.errands.heading));
  e.appendChild(eh);
  if (r.errands && r.errands.cards.length) {
    const cards = el("div", "cards");
    for (const c of r.errands.cards) cards.appendChild(errandCard(c));
    e.appendChild(cards);
  } else e.appendChild(el("p", "empty", "No errand has started yet."));
  box.appendChild(e);
  box.appendChild(questions(r.questions));
  if (r.problems.length || r.unreadable) {
    const pr = el("div", "problems");
    if (r.unreadable) pr.appendChild(el("div", null, "unreadable lines: " + r.unreadable));
    for (const x of r.problems) pr.appendChild(el("div", null, x));
    box.appendChild(pr);
  }
  const keep = [...document.querySelectorAll(".card ol")].map(o => o.scrollHeight - o.scrollTop - o.clientHeight < 8);
  document.getElementById("run").replaceChildren(box);
  document.querySelectorAll(".card ol").forEach((o, i) => { if (keep[i] === undefined || keep[i]) o.scrollTop = o.scrollHeight; });
  document.getElementById("foot").textContent = s.boundary;
}
async function tick() {
  try {
    const res = await fetch("/state" + location.search, {cache: "no-store"});
    if (!res.ok) throw new Error(String(res.status));
    render(await res.json());
  } catch (err) {
    const pill = document.getElementById("pill");
    pill.className = "pill stopped"; pill.textContent = "the run has stopped serving this page";
  }
}
tick(); setInterval(tick, 1000);
</script></body></html>
"""
