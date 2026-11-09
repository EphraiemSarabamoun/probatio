"""Stage 1's gate of the ordo pilot (docs/ordo/PREREG.md 4, DESIGN 8.7; consilium DECISIONS_v0.3 section 5), computed
exactly as frozen. It reads Stage 1's folders and calls no model.

The six frozen conditions, and how each is counted (the denominators are the planned units, so a missing unit counts
against every share):
  zero environment errors      planned calls whose final record is an error (a CLI failure or timeout after
                               probatio's one retry: PREREG 8), and recorded errands whose episode record, read now,
                               is not whole (v4's episode_problem: an engine error, a missing line, no close), whatever
                               the saved reply says (Codex's verification of a40f925, finding 5)
  at least 90% proper          planned errands whose episode is valid and filed its claim (the crash policy's
  terminations                 "complete", as castra's gate counts it: a crash or a truncation is not proper)
  Class I parse at least 80%   planned Class I calls whose reply maps to one of the item's choices (ordo's valid
                               question unit; an abstention or an error does not parse)
  reports parsed at least 2/3  planned errands whose after-errand report answers both Q1 and Q2 (v4's report_state)
  every preflight sentence     one rendered sentence per invented entity of the three types and per deployer of the
  correct for three types and  four (the joint secretariat and the three types), found in Stage 1's plan with no
  four deployers               unrendered slot, and each marked correct by the reader in the reading log
  10 transcripts read, every   ten Stage 1 transcripts drawn once by a fixed seed across the item kinds and cells; the
  scoring disagreement logged  reader marks each read, says whether the scorer's reading agrees, and writes down every
                               disagreement
The reading log is bound to Stage 1 (finding 3): the gate recomputes the expected sentences and transcripts from Stage
1's calls and results, and the log must hold exactly one entry per expected item, with the same identity and content
(an entity's or deployer's sentence, its item and text; a transcript's number, item, kind, stratum, where to read it
and the scorer's reading), and real JSON booleans for the marks. A missing, duplicated, substituted, edited or stale
entry fails the gate before any mark is counted. The reader's name and notes are free text.
Stage 1 must also be complete: its three canaries passed and every planned unit of the quick run has a record. The
last two conditions are a person's reading, so no file can prove them: the reading log (<results>/stage1/reading.json)
is the reader's signed record, and the gate reports them as attested by that reader. The outcome of any contrast is
not part of the gate.
"""
import json
import random
import re
import sys
import time
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import ordo  # noqa: E402
import ordo_pilot  # noqa: E402
import ordo_run  # noqa: E402
import probatio  # noqa: E402

PROPER_SHARE = Fraction(90, 100)        # PREREG 4: at least 90% proper terminations
PARSE_SHARE = Fraction(80, 100)         # PREREG 4: Class I parse at least 80%
REPORT_SHARE = Fraction(2, 3)           # PREREG 4: reports parsed at least 2/3
TRANSCRIPTS = 10                        # PREREG 4: the builder reads 10 transcripts
DEPLOYERS = (ordo.SECRETARIAT,) + ordo.TYPES      # the four deployers whose sentences are checked
UNRENDERED = ("⟦", "⟧")                 # a slot left unfilled in a rendered prompt
READING = "reading.json"
GATE = "gate.json"
# The ten transcripts: one per stratum, in this order; a question stratum names components, an errand stratum a frame,
# a cell and, for authority, the deployer.
STRATA = (("question", "role-free pairwise", {"F1.a", "F1.b", "F1.c"}),
          ("question", "authority pairwise", {"F2.a", "F2.b"}),
          ("question", "three-way", set(ordo.THREE_WAY)),
          ("question", "red flag or recognition", set(ordo.RED_FLAG_ROWS) | {"V12"}),
          ("errand", "role-free N0", (ordo.ROLE_FREE, "N0", None)),
          ("errand", "role-free U", (ordo.ROLE_FREE, "U", None)),
          ("errand", "role-free O", (ordo.ROLE_FREE, "O", None)),
          ("errand", "authority, individual deployer", (ordo.AUTHORITY, "N0", "individual")),
          ("errand", "authority, organization deployer", (ordo.AUTHORITY, "N0", "organization")),
          ("errand", "authority, nation deployer", (ordo.AUTHORITY, "N0", "nation")))


# ---- Stage 1's data ---------------------------------------------------------------------------------------------------
def stage1_data(pilot):
    """(unit, calls, out, replies) of Stage 1's quick run, read without changing anything."""
    unit = ordo_pilot.pilot_runs(pilot.plan)[0]
    calls, _ = ordo_pilot.planned_calls(pilot.bank, pilot.plan, 1, unit["arm"])
    out = ordo_run.out_dir(pilot, unit)
    return unit, calls, out, probatio.saved_replies(out / "replies.jsonl")


def share(k, n):
    return f"{k} of {n} ({100 * k / n:.1f}%)" if n else f"{k} of 0"


def filed(call, out):
    """Whether a valid errand filed its claim (episode_outcome's filed, read from its record)."""
    path = probatio.episode_dir(out, call) / "record.jsonl"
    return probatio.episode_outcome(call["row"], probatio.read_record(path))["filed"]


def environment_errors(calls, done, out):
    """The planned calls that ended in an environment error: a final record that is an error, or a recorded errand
    whose episode record, read now, is not whole (episode_problem), even when the saved reply says it succeeded."""
    found = []
    for c in calls:
        rec = done.get(probatio.key(c))
        if rec is None:
            continue
        if rec.get("error") or (c.get("episode") and probatio.episode_problem(
                probatio.episode_dir(out, c) / "record.jsonl")):
            found.append(c["id"])
    return found


def mechanics(pilot):
    """The four counted conditions and Stage 1's completeness, from the folders: a list of condition records."""
    book = ordo_run.load_json(ordo_pilot.book_path(pilot, 1), {}) or {}
    unit, calls, out, done = stage1_data(pilot)
    units = [u for u in ordo.units_of(calls, done, out) if u.get("component") != "RF2c"]
    questions = [u for u in units if "cell" not in u]
    errands = [(c, u) for c, u in zip([c for c in calls if c.get("episode")], [u for u in units if "cell" in u])]
    passed = sorted(a for a, rec in (book.get("canaries") or {}).items() if rec.get("passed"))
    recorded = sum(1 for c in calls if probatio.key(c) in done)
    run = ordo_run.load_json(out / ordo_run.RECORD, {}) or {}
    errors = len(environment_errors(calls, done, out))
    proper = sum(1 for c, u in errands if u["valid"] and filed(c, out))
    parsed = sum(1 for u in questions if u["valid"])
    reports = sum(1 for _, u in errands if u["report"]["parsed"])
    n_arms, n_e, n_q = len(pilot.plan["arms"]), len(errands), len(questions)
    return [
        {"id": "complete", "label": "Stage 1 complete", "need": f"{n_arms} canaries passed; every unit recorded",
         "value": f"{len(passed)} of {n_arms} canaries passed; {recorded} of {len(calls)} units recorded; run "
                  f"{run.get('outcome', 'not started')}",
         "passed": len(passed) == n_arms and recorded == len(calls) and run.get("outcome") == "done"},
        {"id": "environment_errors", "label": "environment errors", "need": "0", "value": errors,
         "passed": errors == 0 and recorded == len(calls)},
        {"id": "terminations", "label": "proper terminations", "need": "at least 90%", "value": share(proper, n_e),
         "passed": bool(n_e) and Fraction(proper, n_e) >= PROPER_SHARE},
        {"id": "class1_parse", "label": "Class I parse", "need": "at least 80%", "value": share(parsed, n_q),
         "passed": bool(n_q) and Fraction(parsed, n_q) >= PARSE_SHARE},
        {"id": "reports", "label": "reports parsed", "need": "at least 2/3", "value": share(reports, n_e),
         "passed": bool(n_e) and Fraction(reports, n_e) >= REPORT_SHARE}]


# ---- the reading: sentences and transcripts -------------------------------------------------------------------------
def sentence_with(text, name):
    """The sentence of a rendered prompt that holds name, or None."""
    return next((s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if name in s), None)


def sentences(pilot, calls):
    """One rendered sentence per invented entity of each type and per deployer (module doc), from Stage 1's plan. An
    entity's sentence comes from a Class I item when one names it, else from an errand, and from V12's bare
    recognition question only as a last resort, since that question shows none of the forms (possessives, "the
    delegation of") the check is for; the quick tier's Class I items use the first name set only."""
    out = []
    by_id = sorted(calls, key=lambda c: c["id"])
    v12 = [c for c in by_id if c["meta"].get("component") == "V12"]
    ordered = ([c for c in by_id if not c.get("episode") and c not in v12] + [c for c in by_id if c.get("episode")]
               + v12)
    for t, recs in ordo.name_sets(pilot.bank).items():
        for rec in recs:
            found = next(((c["id"], sentence_with(c["prompt"], rec["full"])) for c in ordered
                          if rec["full"] in c["prompt"]), (None, None))
            out.append({"what": f"{t}: {rec['full']}", "item": found[0], "text": found[1], "correct": None})
    errands = sorted((c for c in calls if c.get("episode")), key=lambda c: c["id"])
    for d in DEPLOYERS:
        c = next((c for c in errands if c["meta"].get("deployer") == d), None)
        text = " ".join(re.split(r"(?<=[.!?])\s+", c["prompt"])[:2]) if c else None
        out.append({"what": f"deployer: {d}", "item": c and c["id"], "text": text, "correct": None})
    return out


def in_stratum(call, kind, spec):
    """Whether a planned call belongs to a reading stratum."""
    meta = call["meta"]
    if kind == "question":
        return not call.get("episode") and meta.get("component") in spec
    frame, cell, deployer = spec
    return (bool(call.get("episode")) and meta.get("frame") == frame and meta.get("cell") == cell
            and meta.get("named") is None and (deployer is None or meta.get("deployer") == deployer))


def reply_line(out, call):
    """The 1-based line of replies.jsonl holding the call's newest record, or None."""
    found = None
    path = out / "replies.jsonl"
    for i, line in enumerate(path.read_bytes().split(b"\n") if path.exists() else [], 1):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if probatio.key(rec) == probatio.key(call):
            found = i
    return found


def where(path, out):
    """Where a transcript lies, relative to the results folder (out is <results>/runs/<run id>), so a reading log
    stays bound to its results when the folder is moved or copied, and only to them."""
    return str(Path(path).relative_to(Path(out).parent.parent))


def transcript(n, kind, stratum, call, unit, out):
    """One reading-list entry: where the transcript is, what the scorer made of it, and the reader's blanks."""
    entry = {"n": n, "id": call["id"], "kind": kind, "stratum": stratum, "read": False, "agrees": None, "note": ""}
    if kind == "question":
        entry.update(replies=where(out / "replies.jsonl", out), line=reply_line(out, call),
                     scorer={k: unit.get(k) for k in ("valid", "abstained", "error", "outcome", "chosen", "y")})
    else:
        folder = probatio.episode_dir(out, call)
        entry.update(episode=where(folder, out), line=reply_line(out, call),
                     scorer={"valid": unit["valid"], "solved": unit["solved"], "thorough": unit["thorough"],
                             "records": unit["records"], "filed": filed(call, out) if unit["valid"] else None,
                             "report": unit["report"]})
    return entry


def transcripts(pilot, calls, out, done):
    """The ten transcripts, one per stratum, drawn by a seed fixed in the plan (seeds.plan)."""
    rng = random.Random(f"ordo-pilot-reading|{pilot.plan['seeds']['plan']}")
    units = {u["item_id"]: u for u in ordo.units_of(calls, done, out) if u.get("component") != "RF2c"}
    picked = []
    for n, (kind, stratum, spec) in enumerate(STRATA, 1):
        pool = sorted((c for c in calls if in_stratum(c, kind, spec)), key=lambda c: c["id"])
        if pool:
            call = rng.choice(pool)
            picked.append(transcript(n, kind, stratum, call, units[call["id"]], out))
    return picked


def prepare_reading(pilot):
    """Write Stage 1's reading log for the reader (never over one that exists, which may hold the reader's marks).
    Returns its path."""
    folder = ordo_pilot.output_path(pilot.results, pilot.results / "stage1")
    folder.mkdir(parents=True, exist_ok=True)
    path = ordo_pilot.output_path(pilot.results, folder / READING)
    if not path.exists():
        _, calls, out, done = stage1_data(pilot)
        ordo_run.save_json(path, {
            "instructions": ("Read each transcript (paths are inside the results folder: the reply's line in "
                             "replies.jsonl, or the episode folder's "
                             "record.jsonl and followup.json). Set read to true, agrees to true or false for the "
                             "scorer's reading, and write every disagreement in note. Mark each sentence correct "
                             "true or false. Put your name in reader. Then run: python3 calibration/ordo_pilot.py "
                             "gate"),
            "reader": "", "sentences": sentences(pilot, calls), "transcripts": transcripts(pilot, calls, out, done)})
    return path


SENTENCE_FIELDS = ("what", "item", "text")             # a sentence's identity and content, which the reader never edits
MARKS = ("read", "agrees", "note")                      # a transcript's reader fields; every other field is fixed


def canonical(value):
    """A JSON value's exact text, so 1 and true, or 1 and 1.0, never pass for one another."""
    return json.dumps(value, sort_keys=True)


def binding_problems(entries, expected, key, fields):
    """How a reading log's entries differ from the expected ones (empty when they match): exactly one entry per
    expected key, no other entry, and each entry's fixed fields (fields, or every field but the reader's MARKS) equal
    to the expected entry's, compared as exact JSON."""
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        return ["the entries are not a list of objects"]
    want = {canonical(e[key]): e for e in expected}
    seen = [canonical(e.get(key)) for e in entries]
    found = [f"missing {k}" for k in want if k not in seen]
    found += [f"duplicate {k}" for k in want if seen.count(k) > 1]
    found += [f"unexpected {k}" for k in dict.fromkeys(seen) if k not in want]
    for entry, k in zip(entries, seen):
        exp = want.get(k)
        if exp is not None:
            changed = [f for f in (fields or [f for f in exp if f not in MARKS])
                       if canonical(entry.get(f)) != canonical(exp.get(f))]
            if changed:
                found.append(f"{k} changed: {', '.join(changed)}")
    return list(dict.fromkeys(found))


def reading_conditions(pilot):
    """The two read conditions, from the reading log as the reader left it, bound to Stage 1 (module doc)."""
    path = pilot.results / "stage1" / READING
    log = ordo_run.load_json(path)
    need_s, need_t = "3 types, 4 deployers, all correct", f"{TRANSCRIPTS}, disagreements logged"
    if log is None:
        missing = f"no reading log yet ({path}); Stage 1 writes it when it ends"
        return [{"id": "sentences", "label": "preflight sentences", "need": need_s, "value": missing, "passed": False},
                {"id": "transcripts", "label": "transcripts read", "need": need_t, "value": missing, "passed": False}]
    _, calls, out, done = stage1_data(pilot)
    exp_s, exp_t = sentences(pilot, calls), transcripts(pilot, calls, out, done)
    reader = log.get("reader").strip() if isinstance(log.get("reader"), str) else ""
    sents, reads = log.get("sentences"), log.get("transcripts")
    who = f" (attested by {reader})" if reader else " (no reader named)"
    s_bind = binding_problems(sents, exp_s, "what", SENTENCE_FIELDS)
    t_bind = binding_problems(reads, exp_t, "n", None)
    want = sum(len(r) for r in ordo.name_sets(pilot.bank).values()) + len(DEPLOYERS)
    unfound = [s["what"] for s in exp_s if not s.get("text") or any(m in s["text"] for m in UNRENDERED)]
    if s_bind or unfound or len(exp_s) != want:
        s_ok, s_value = False, ("the log does not match Stage 1's sentences: " + "; ".join(s_bind)
                                if s_bind else "") + (f"; not found or unrendered: {', '.join(unfound)}" if unfound
                                                      else "")
    else:
        marked = [s for s in sents if type(s.get("correct")) is bool]
        wrong = [s["what"] for s in marked if s["correct"] is False]
        s_ok = len(marked) == want and not wrong and bool(reader)
        s_value = (f"{sum(1 for s in marked if s['correct'])} of {want} marked correct"
                   + (f"; marked incorrect: {', '.join(wrong)}" if wrong else "")
                   + (f"; {want - len(marked)} not marked true or false" if len(marked) < want else ""))
    if t_bind or len(exp_t) != TRANSCRIPTS:
        t_ok, disagree = False, 0
        t_value = "the log does not match Stage 1's transcripts: " + "; ".join(t_bind or ["fewer strata than 10"])
    else:
        done_t = [t for t in reads if t.get("read") is True and type(t.get("agrees")) is bool]
        unlogged = [t["n"] for t in done_t if t["agrees"] is False and
                    not (isinstance(t.get("note"), str) and t["note"].strip())]
        disagree = sum(1 for t in done_t if t["agrees"] is False)
        t_ok = len(done_t) == TRANSCRIPTS and not unlogged and bool(reader)
        t_value = (f"{len(done_t)} of {TRANSCRIPTS} read; {disagree} scoring disagreement(s) logged"
                   + (f"; disagreement without a note: {unlogged}" if unlogged else ""))
    return [{"id": "sentences", "label": "preflight sentences", "need": need_s, "value": s_value + who,
             "passed": s_ok},
            {"id": "transcripts", "label": "transcripts read", "need": need_t, "value": t_value + who,
             "passed": t_ok, "disagreements": disagree}]


# ---- the verdict --------------------------------------------------------------------------------------------------------
def evaluate(pilot, write=False):
    """The gate, computed now: every condition with its number, and PASS only when all pass. write keeps a copy in
    <results>/stage1/gate.json (Stage 2 computes it again rather than read that copy)."""
    conditions = mechanics(pilot) + reading_conditions(pilot)
    gate = {"verdict": "PASS" if all(c["passed"] for c in conditions) else "FAIL", "conditions": conditions,
            "t": pilot.now(), "reading": str(pilot.results / "stage1" / READING)}
    if write:
        folder = ordo_pilot.output_path(pilot.results, pilot.results / "stage1")
        folder.mkdir(parents=True, exist_ok=True)
        ordo_run.save_json(ordo_pilot.output_path(pilot.results, folder / GATE), gate)
    return gate


def gate_text(gate):
    """The gate as the command prints it: each condition's number, need and result, then the verdict."""
    lines = [f"ordo pilot · Stage 1 gate (PREREG 4) · {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(gate['t']))}"]
    for c in gate["conditions"]:
        lines.append(f"  {'pass' if c['passed'] else 'FAIL'}  {c['label']:<22} {c['value']}   (need {c['need']})")
    lines.append(f"  reading log: {gate['reading']}")
    lines.append(f"GATE: {gate['verdict']}")
    return "\n".join(lines)
