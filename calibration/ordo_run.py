"""One arm-run of the ordo pilot, from the outside: its registered room, its preflight and its launches, with the quota
pauses, the hard stop and the resume between them (docs/ordo/PILOT_RUNBOOK.md). calibration/run.py's loop, for a run
that probatio's public command line cannot express: an arm's items come from the frozen plan's private item filter
(DESIGN 8.7), so the child it launches is ordo_arm.py, which runs probatio's own cross-type path with those items.

The flow of run_unit, for one arm-run (a stage's unit of work, such as Stage 2's g_wish_nation arm):
  1. its registration: the stage, arm, tier and subject recipe; the quota policy and ledger identity (limits, the
     ledger's home and directory, this host's label and log, the rooms' policy) and the token's source (its variable's
     name and the secrets file's canonical path, never its value); and the identity (real path and sha256) of every file
     that decides what it asks and how: the pinned Claude Code build, probatio.py, ordo.py, world.py, the pilot's own
     code, the frozen plan, the arm's memory file, the ordo bank's manifest, the shim and the guard. A resume whose
     registration differs is refused before anything is touched;
  2. its room (isolate.build, memory on, the arm's file as CLAUDE.md, the registered model and effort, and the pilot's
     room policy, under which the shim refuses a call when the quota ledger cannot be read), refused unless
     ordo_pilot.audit_room finds exactly that file at its registered sha256;
  3. its preflight: the room's checks (isolate.check), then the child's dry run, which must list exactly the room's
     memory file, report the registered model, effort and jobs, and plan exactly the calls the parent planned;
  4. its launches. Before each: no errand of an earlier launch still running (probatio starts each errand's CLI in
     its own session, so one can outlive a launch that died, and its world would write into an episode a resume
     restarts), no hard stop, no quota hold (a quota ledger it cannot read counts as a hold, never as
     clear), the registration unchanged and the room clean. After each, the child's exit says what happened: done,
     held for quota (wait, then launch again, which resumes), stopped (STOP stays for a person), or stopped by
     probatio's own rule (eight failed calls in a row, or a reply from another model: PREREG 8).
Everything is recorded in the run folder's pilot-run.json: the registration, the planned count, each attempt, each
pause, and the outcome. The child's output reaches harness.log only through the redactor, and its progress lines (one
per finished call) are echoed to the pilot's log.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import isolate  # noqa: E402
import ordo_arm  # noqa: E402
import ordo_pilot  # noqa: E402
import probatio  # noqa: E402
import quota  # noqa: E402
import redact  # noqa: E402
import run as harness  # noqa: E402  calibration/run.py: the identities and the preflight's parsers
import scheduler  # noqa: E402

ARM = HERE / "ordo_arm.py"
RECORD = "pilot-run.json"
UNREADABLE_WAIT_S = 60          # a quota ledger that cannot be read holds the next launch this long, then is read again
ORPHAN_POLL_S = 5               # how often a launch looks again for an earlier launch's errands still running
STALL_LIMIT = 3                 # launches in a row that recorded nothing and ended held with no hold in force: stop
REFUSAL_TAIL = 400              # characters of a failed child's output a refusal quotes
REGISTERED_FILES = (("probatio", ROOT / "probatio.py"), ("ordo", ROOT / "ordo.py"), ("world", ROOT / "world.py"),
                    ("ordo_arm", ARM), ("ordo_run", Path(__file__).resolve()), ("ordo_pilot", HERE / "ordo_pilot.py"),
                    ("shim", HERE / "shim.py"), ("guard", HERE / "guard.py"),
                    ("ordo_bank", ROOT / "bank" / "ordo" / "manifest.json"))


class Refusal(isolate.Refusal):
    pass


# ---- the record -----------------------------------------------------------------------------------------------------
def save_json(path, data):
    """Write a JSON record atomically, so a crash never leaves half a record."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def out_dir(pilot, unit):
    """An arm-run's folder, <results>/runs/<run id>, checked as an output (ordo_pilot.output_path: no symlink on the
    way, in no git tree)."""
    return ordo_pilot.output_path(pilot.results, pilot.results / "runs" / unit["run_id"])


def registration(pilot, unit):
    """(digest, canonical): everything that makes an arm-run the run it was registered as (module doc, step 1)."""
    s = pilot.plan["subject"]
    canonical = {"stage": unit["stage"], "arm": unit["arm"], "tier": ordo_pilot.stage_tier(pilot.plan, unit["stage"],
                                                                                           unit["arm"]),
                 "agent": s["agent"], "model": s["model"], "effort": s["effort"], "jobs": s["jobs"],
                 "claude": harness.binary_identity(pilot.cfg["claude_binary"], pilot.cfg["claude_version"]),
                 "quota": ordo_pilot.quota_identity(pilot.cfg), "token": ordo_pilot.token_source(pilot.cfg),
                 "plan": harness.file_identity(pilot.plan_path),
                 "arm_file": harness.file_identity(ordo_pilot.PILOT / pilot.plan["arms"][unit["arm"]]["file"])}
    canonical.update({name: harness.file_identity(path) for name, path in REGISTERED_FILES})
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest(), canonical


def registered(out, digest, canonical, now):
    """The arm-run's earlier record on a resume of the same registration, None for a new one, or Refusal (logged in
    rejected-resumes.jsonl, the record left as it was) when anything registered has changed."""
    old = load_json(out / RECORD)
    if old is None:
        return None
    reg = old.get("registration") or {}
    if reg.get("fingerprint") == digest:
        return old
    changed = harness.changed_keys(canonical, reg.get("canonical")) or ["(no registration on record)"]
    with open(out / "rejected-resumes.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": now, "fingerprint": digest, "changed": changed}) + "\n")
    raise Refusal(f"{out.name} was registered with another {', '.join(changed)}; an arm-run's registration never "
                  "changes, and a changed file waits for a decision about the pilot")


def recorded(out, calls):
    """(recorded, errors) over the planned calls, read without changing replies.jsonl."""
    done = probatio.saved_replies(out / "replies.jsonl")
    recs = [done[probatio.key(c)] for c in calls if probatio.key(c) in done]
    return len(recs), sum(1 for r in recs if r.get("error"))


# ---- the room and its preflight ---------------------------------------------------------------------------------------
def open_room(pilot, unit, out):
    """The arm-run's room, built (or reopened) with the arm's bytes as its memory file, refused unless the audit of
    PREREG Amendment 1 finds exactly that file at its registered sha256."""
    s, spec = pilot.plan["subject"], pilot.plan["arms"][unit["arm"]]
    room = isolate.build(pilot.cfg, unit["run_id"], s["agent"], s["memory"], s["effort"],
                         (ordo_pilot.PILOT / spec["file"]).read_bytes(), model=s["model"], out_dir=out,
                         policy=ordo_pilot.ROOM_POLICY)
    problems = ordo_pilot.audit_room(room.work_dir, pilot.plan, unit["arm"])
    if problems:
        raise Refusal("; ".join(problems))
    return room


def child_command(pilot, unit, out, dry_run=False):
    """The child's command: this interpreter (the one the room registered for the world server) running ordo_arm.py."""
    s = pilot.plan["subject"]
    return ([sys.executable, "-B", str(ARM), "--plan", str(pilot.plan_path), "--stage", str(unit["stage"]),
             "--arm", unit["arm"], "--out", str(out), "--jobs", str(s["jobs"]), "--model", s["model"],
             "--effort", s["effort"]] + (["--dry-run"] if dry_run else []))


def planned_count(text):
    """The calls the child's dry run says it plans (ordo_arm.PLANNED), or None."""
    for line in text.splitlines():
        if line.startswith(ordo_arm.PLANNED.split("{")[0]):
            return int(line.split()[-2])
    return None


def preflight(pilot, unit, room, env, scrub, out, calls):
    """The room's checks, then the child's dry run (module doc, step 3). Returns the dry run's output."""
    isolate.check(room, pilot.cfg, env)
    dry = subprocess.run(child_command(pilot, unit, out, dry_run=True), capture_output=True, text=True,
                         cwd=room.work_dir, env=env)
    if dry.returncode:
        raise Refusal(f"the arm's dry run failed (exit {dry.returncode}): "
                      f"{scrub.clip((dry.stderr or dry.stdout).strip(), REFUSAL_TAIL, tail=True)}")
    s, settings = pilot.plan["subject"], harness.preflight_settings(dry.stdout)
    problems = harness.preflight_problems([room.memory_path], harness.listed_memory(dry.stdout))
    for name in ("model", "effort", "jobs"):
        if settings[name] != s[name]:
            problems.append(f"the dry run reports {name} {settings[name]!r}, not the registered {s[name]!r}")
    planned = planned_count(dry.stdout)
    if planned != len(calls):
        problems.append(f"the dry run plans {planned} calls; the frozen plan holds {len(calls)}")
    if problems:
        raise Refusal("; ".join(scrub(p) for p in problems))
    return dry.stdout


# ---- an earlier launch's errands --------------------------------------------------------------------------------------
def orphans(out):
    """The pids of processes still serving one of this arm-run's episodes: a world.py whose command line names
    <out>/episodes. Refused when pgrep is missing, since then they cannot be looked for."""
    try:
        p = subprocess.run(["pgrep", "-f", re.escape(str(Path(out).resolve() / "episodes") + os.sep)],
                           capture_output=True, text=True)
    except FileNotFoundError:
        raise Refusal("pgrep is missing, so an earlier launch's errands cannot be looked for") from None
    return [int(x) for x in p.stdout.split()]


def wait_orphans(pilot, out, stop_path, label):
    """Wait, checking for a hard stop every ORPHAN_POLL_S, until no earlier launch's errand is running. An orphan ends
    on its own: its runner is gone, so its CLI's input is closed and it stops after its current turn."""
    found, logged = orphans(out), False
    while found and not stop_path.exists():
        if not logged:
            pilot.log(f"{label}: waiting for {len(found)} errand process(es) an earlier launch left running ({found})")
            logged = True
        pilot.sleep(ORPHAN_POLL_S)
        found = orphans(out)


# ---- quota and the hard stop ----------------------------------------------------------------------------------------
def quota_hold(sched, now):
    """(until, why, stop) before a launch or a canary: the scheduler's pause and its weekly study stop, with a quota
    ledger that cannot be read held as a pause (it never means proceed)."""
    _, degraded = sched.ledger(now, fresh=True)
    if degraded:
        return now + UNREADABLE_WAIT_S, "the quota ledger cannot be read", False
    until, why, info = sched.pause(now)
    return until, why, info["stop"]


def wait_clear(pilot, stop_path, sched, pauses, label):
    """Wait until no quota hold is in force, sleeping in steps no longer than run.STOP_POLL_S and checking for a hard
    stop at every step; each pause is appended to pauses. Returns None when a call may start, else why it may not
    (the hard stop's text; the weekly study stop is written to STOP first, as run.py does)."""
    while True:
        if stop_path.exists():
            return f"a hard stop is in force: {stop_path.read_text().strip()}"
        until, why, stop = quota_hold(sched, pilot.now())
        if stop:
            stop_path.write_text(f"{quota.when(pilot.now())} {label}: the quota scheduler stops the study ({why})\n",
                                 encoding="utf-8")
            continue
        if not until:
            return None
        pauses.append({"from": pilot.now(), "until": until, "why": why})
        pilot.log(f"{label}: paused for quota until {quota.when(until)} ({why})")
        while until and until > pilot.now() and not stop_path.exists():
            pilot.sleep(min(until - pilot.now(), harness.STOP_POLL_S))
            until, why, stop = quota_hold(sched, pilot.now())
            if stop:
                break


# ---- one launch ---------------------------------------------------------------------------------------------------------
def launch(pilot, unit, room, env, scrub, out, number):
    """One child run, its output appended to harness.log through the redactor and its progress lines echoed to the
    pilot's log. Returns (exit code, the child's last progress line)."""
    last = None
    with open(out / "harness.log", "a", encoding="utf-8") as logf:
        logf.write(f"\n=== launch {number} at {quota.when(pilot.now())} ===\n")
        logf.flush()
        proc = subprocess.Popen(child_command(pilot, unit, out), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                cwd=room.work_dir, env=env, text=True, bufsize=1)
        for line in proc.stdout:
            clean = scrub(line)
            logf.write(clean)
            if clean.startswith(ordo_arm.PROGRESS):
                last = clean.rstrip()
                pilot.log(last)
        proc.stdout.close()
        return proc.wait(), last


def step_after(rc, complete, stopped):
    """What a launch's exit means: "done", "pause" (wait, launch again), "stop", "failures" (PREREG 8) or "fail"."""
    if stopped or rc == ordo_arm.HELD_STOP:
        return "stop"
    if rc == ordo_arm.DONE and complete:
        return "done"
    if rc == ordo_arm.HELD_PAUSE:
        return "pause"
    return "failures" if rc == ordo_arm.FAILURES else "fail"


# ---- the arm-run --------------------------------------------------------------------------------------------------------
def run_unit(pilot, unit, calls):
    """Run one arm-run to its end, resuming it when it was begun before; returns its record (module doc)."""
    out = out_dir(pilot, unit)
    out.mkdir(parents=True, exist_ok=True)
    ordo_pilot.check_tree(pilot.results, out)       # a resumed folder: no symlink, no git tree anywhere beneath it
    digest, canonical = registration(pilot, unit)
    old = registered(out, digest, canonical, pilot.now())
    room = open_room(pilot, unit, out)
    env = isolate.environment(room, pilot.cfg)
    scrub = redact.Redactor(env)
    try:
        preflight(pilot, unit, room, env, scrub, out, calls)
    except isolate.Refusal as e:
        raise Refusal(scrub(str(e))) from None
    record = {"run_id": unit["run_id"], "stage": unit["stage"], "arm": unit["arm"],
              "registration": {"fingerprint": digest, "canonical": canonical}, "room": str(room.bin_dir / "room.json"),
              "memory_file": str(room.memory_path), "memory_sha256": room.memory_sha256, "planned": len(calls),
              "started": pilot.now(), "attempts": [], "pauses": [], "outcome": "running"}
    if old:                                     # a resume keeps its history; a launch a crash cut short says so
        record.update(started=old.get("started", record["started"]), attempts=old.get("attempts", []),
                      pauses=old.get("pauses", []))
        for a in record["attempts"]:
            if a.get("ended") is None:
                a.update(step="interrupted", note="the process ended without recording this launch (a crash or a kill)")
    save_json(out / RECORD, record)

    def finish(outcome):
        record.update(outcome=outcome, ended=pilot.now())
        save_json(out / RECORD, record)
        pilot.log(f"{unit['run_id']}: {outcome}")
        return record

    sched, stalls = scheduler.Scheduler(scheduler.conf_from(pilot.cfg, room.root), clock=pilot.now), 0
    while True:
        wait_orphans(pilot, out, room.stop_path, unit["run_id"])
        blocked = wait_clear(pilot, room.stop_path, sched, record["pauses"], unit["run_id"])
        save_json(out / RECORD, record)
        if blocked:
            return finish(f"stopped: {scrub(blocked)}")
        if registration(pilot, unit)[0] != digest:
            changed = harness.changed_keys(registration(pilot, unit)[1], canonical)
            return finish(f"refused: the registered {', '.join(changed)} changed on disk since the run was registered")
        try:
            isolate.check(room, pilot.cfg, env)
            ordo_pilot.check_tree(pilot.results, out)
        except (isolate.Refusal, ordo_pilot.Refusal) as e:
            return finish(f"refused: {scrub(str(e))}")
        before, _ = recorded(out, calls)
        attempt = {"started": pilot.now(), "ended": None, "recorded_before": before}
        record["attempts"].append(attempt)
        save_json(out / RECORD, record)
        rc, last = launch(pilot, unit, room, env, scrub, out, len(record["attempts"]))
        after, errors = recorded(out, calls)
        step = step_after(rc, after == len(calls), room.stop_path.exists())
        attempt.update(ended=pilot.now(), rc=rc, recorded=after, errors=errors, step=step)
        save_json(out / RECORD, record)
        pilot.log(f"{unit['run_id']}: launch {len(record['attempts'])}: exit {rc}, {after} of {len(calls)} recorded, "
                  f"{errors} errors: {step}")
        if step == "done":
            return finish("done")
        if step == "failures":                  # the child's last line: "<prefix> <run id>: stopped: <why>"
            why = (last or "probatio stopped the run").split(": ", 1)[-1].removeprefix("stopped: ")
            return finish(f"stopped: {why} (PREREG 8)")
        if step == "fail":
            tail = (out / "harness.log").read_text(encoding="utf-8", errors="replace")[-REFUSAL_TAIL:]
            return finish(f"failed: the arm-run exited {rc}: {' '.join(tail.split())}")
        if step == "stop" and room.stop_path.exists():
            return finish(f"stopped: {scrub(room.stop_path.read_text().strip())}")
        stalls = stalls + 1 if after == before and quota_hold(sched, pilot.now())[0] is None else 0
        if stalls >= STALL_LIMIT:
            return finish(f"stopped: {STALL_LIMIT} launches in a row recorded nothing and no quota hold explains it")
