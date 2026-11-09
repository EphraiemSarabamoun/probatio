#!/usr/bin/env python3
"""One arm-run of the ordo pilot inside its clean room: the child process ordo_run.py launches with the room's
environment, from the room's working directory, so every subject call goes through the room's own claude (shim.py)
exactly as probatio's own calls would (docs/ordo/PILOT_RUNBOOK.md).

It plans the arm's calls from the frozen plan (ordo_pilot.planned_calls: ordo.plan with the private item filter of
DESIGN 8.7), checks that they are probatio's own cross-type items with their words, worlds and metadata unchanged, and
then runs probatio's own path for a cross-type run: the preflight (preflight_runs, whose memory list the parent checks),
the run folder's lock and resume check (claim, check_resume), and run_calls. It never scores: ordo_gate.py and
ordo_analysis.py read the folder afterwards, so no number is ever computed from a run still in progress.

Two things are added around probatio's ask, and nothing in probatio changes:
  the call boundary   before each call, the room's hard stop (STOP), a quota pause, the weekly study stop or a quota
                      ledger it cannot read ends the run cleanly: no new call starts, the calls already running finish
                      and are recorded, and the process exits with HELD_PAUSE or HELD_STOP for the parent to wait or
                      stop. A call the room's shim refused while it ran (a stop, a pause, unverified telemetry) is not
                      recorded either, so a resume makes it again;
  the progress line   one line per finished call: the count, the valid replies so far, the time since this launch
                      began and the newest quota reading.
Errors are final (PREREG 8): probatio's ask retries a failed call once, and a call that still fails is recorded as an
error and never made again. A resume makes only the calls that have no record.

    (launched by ordo_run.py; never run it by hand)
    python3 -B calibration/ordo_arm.py --plan PLAN --stage 1|2 --arm ARM --out OUT --jobs N --model M --effort E
                                       [--dry-run]
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import ordo  # noqa: E402
import ordo_pilot  # noqa: E402
import probatio  # noqa: E402
import quota  # noqa: E402
import scheduler  # noqa: E402

# ---- exit codes the parent reads (ordo_run.py) ------------------------------------------------------------------------
DONE = 0                # every planned call has a record
HELD_PAUSE = 75         # a quota pause, or a quota ledger this process could not read: wait, then resume
HELD_STOP = 3           # the room's STOP, or the weekly study stop: a person decides
FAILURES = 4            # probatio stopped the run: eight failed calls in a row, or a reply from another model (PREREG 8)
PROGRESS = "[ordo-pilot]"           # the prefix of every line the parent echoes to its own log
PLANNED = PROGRESS + " planned {} calls"
SHIM_REFUSALS = ("probatio-cal: stopped", "probatio-cal: refused", "probatio-cal: paused",     # how the room's
                 "probatio-cal: unverified")                                                    # shim words one


class Hold(Exception):
    """The run must end at this call boundary: kind is "pause" or "stop", why says what holds it."""

    def __init__(self, kind, why):
        super().__init__(why)
        self.kind, self.why = kind, why


# ---- the plan, checked against probatio's own ---------------------------------------------------------------------------
def signature(call):
    """What makes two planned calls the same item: its id and condition, its words, its world and its metadata."""
    return (call["id"], call["condition"], call["prompt"], call.get("world"), call.get("followup"),
            json.dumps(call["meta"], sort_keys=True))


def probatio_args(plan, tier, workdir, out, jobs):
    """probatio's own command line for this arm's cross-type run: the pilot's comparison and tier, memory on, auto
    memory off and the registered model and effort (PREREG 2). The item filter is the only thing it cannot say."""
    s = plan["subject"]
    return ["--agent", s["agent"], "--model", s["model"], "--compare", plan["compare"], "--tier", tier,
            "--memory", s["memory"], "--auto-memory", s["auto_memory"], "--effort", s["effort"],
            "--workdir", str(workdir), "--out", str(out), "--jobs", str(jobs)]


def checked_run(a, calls):
    """probatio's cross-type run for this command line, with its calls replaced by the arm's: refused unless probatio
    plans exactly one cross-type run and every one of the arm's calls is one of its items, unchanged."""
    bank, ordo_bank, selection, spec, runs = probatio.prepare_runs(a, False)
    if len(runs) != 1 or runs[0]["kind"] != "cross":
        raise SystemExit("ordo pilot: probatio did not plan one cross-type run for the pilot's comparison")
    own = {signature(c) for c in runs[0]["calls"]}
    stray = [c["id"] for c in calls if signature(c) not in own]
    if stray:
        raise SystemExit(f"ordo pilot: {len(stray)} of the arm's calls are not probatio's own items, such as "
                         f"{stray[0]!r}; the plan and the bank disagree")
    return bank, ordo_bank, selection, spec, dict(runs[0], calls=calls)


# ---- the call boundary ------------------------------------------------------------------------------------------------
class Boundary:
    """The checks before each call and after a refused one (module doc), from the room's own room.json."""

    def __init__(self, room):
        self.stop_path = Path(room["stop_path"])
        self.sched = scheduler.Scheduler(room["quota"])

    def before(self):
        """Raise Hold when no new call may start."""
        if self.stop_path.exists():
            raise Hold("stop", f"a hard stop is in force: {self.stop_path.read_text().strip()}")
        _, degraded = self.sched.ledger()
        if degraded:
            raise Hold("pause", "the quota ledger cannot be read, so no new call starts until it can")
        until, why, info = self.sched.pause()
        if info["stop"]:
            raise Hold("stop", f"the quota scheduler stops the study ({why})")
        if until:
            raise Hold("pause", f"paused for quota until {quota.when(until)} ({why})")

    def refused(self, error):
        """The Hold a failed call stands for when the room refused it rather than the CLI or the model failing, else
        None: a stop or pause now in force (a long refusal's mark can fall outside probatio's tail of the error), or
        one of the shim's refusal marks with nothing in force any more, which a resume makes again like a pause. A
        CLI that exits without a result is a failure, not a refusal, and is recorded."""
        try:
            self.before()
        except Hold as h:
            return h
        marked = any(mark in (error or "") for mark in SHIM_REFUSALS)
        return Hold("pause", f"the room refused the call ({error[-200:]})") if marked else None


# ---- the progress line ------------------------------------------------------------------------------------------------
def is_valid(call, reply, error):
    """A finished call is valid when it came back without an error and, for a question, its reply maps to a choice."""
    if error is not None:
        return False
    return bool(call.get("episode")) or probatio.score(call["row"], reply)[1] is not None


class Progress:
    """The running counts behind the progress line, shared by probatio's worker threads."""

    def __init__(self, run_id, planned, done, valid, quota_path, print_line=print):
        self.run_id, self.planned, self.done, self.valid = run_id, planned, done, valid
        self.quota_path, self.print_line = quota_path, print_line
        self.errors, self.started, self.lock = 0, time.monotonic(), threading.Lock()

    def note(self, call, reply, error):
        with self.lock:
            self.done += 1
            self.valid += is_valid(call, reply, error)
            self.errors += error is not None
            self.print_line(self.line(), flush=True)

    def line(self):
        """count, valid, errors, elapsed and the newest quota reading on this host."""
        newest = quota.latest(self.quota_path) or {}
        meter = " · ".join(f"{w} {newest[w]:.2f}" for w in quota.WINDOWS
                           if isinstance(newest.get(w), (int, float))) or "no quota reading yet"
        elapsed = int(time.monotonic() - self.started)
        return (f"{PROGRESS} {self.run_id} {self.done}/{self.planned} · {self.valid} valid · {self.errors} errors this "
                f"launch · {elapsed // 60}m {elapsed % 60:02d}s · {meter}")


def gated(real_ask, boundary, progress):
    """probatio's ask with the call boundary before it and the progress line after it. A Hold raised here leaves
    run_calls through its pool: running calls finish and are recorded, no queued call starts."""
    def ask(agent, call, opts, folder=None):
        boundary.before()
        reply, error, model, seconds = real_ask(agent, call, opts, folder)
        held = boundary.refused(error) if error is not None else None
        if held:                                # not recorded: a resume makes the call again
            raise held
        progress.note(call, reply, error)
        return reply, error, model, seconds
    return ask


# ---- the run ----------------------------------------------------------------------------------------------------------
def pending(calls, done):
    """The calls a launch makes: those with no record. An error recorded after probatio's one retry is final."""
    return [c for c in calls if probatio.key(c) not in done]


def counts(calls, done):
    """(recorded, valid) over the planned calls."""
    recorded = [c for c in calls if probatio.key(c) in done]
    return len(recorded), sum(is_valid(c, done[probatio.key(c)].get("reply"), done[probatio.key(c)].get("error"))
                              for c in recorded)


def pilot_meta(meta, plan_path, stage, arm, ids):
    """run.json's record of a pilot arm-run: probatio's own meta, plus which stage, arm and frozen items it is."""
    return dict(meta, pilot={"stage": stage, "arm": arm, "items_sha256": ordo_pilot.ids_sha256(ids),
                             "plan_sha256": hashlib.sha256(Path(plan_path).read_bytes()).hexdigest()})


def execute(a, opts, run, meta, room, out):
    """Make the run's calls that have no record yet, behind the call boundary. Returns an exit code."""
    out.mkdir(parents=True, exist_ok=True)
    lock = probatio.claim(out)
    try:
        probatio.check_resume(out, meta)
        opts.quota = probatio.QuotaLog(out / "quota.jsonl")
        done = probatio.read_replies(out / "replies.jsonl")
        recorded, valid = counts(run["calls"], done)
        todo = pending(run["calls"], done)
        progress = Progress(room["run_id"], len(run["calls"]), recorded, valid, room["quota_path"])
        print(f"{PROGRESS} {room['run_id']}: {recorded} of {len(run['calls'])} calls recorded; making {len(todo)}",
              flush=True)
        real_ask = probatio.ask
        probatio.ask = gated(real_ask, Boundary(room), progress)
        try:
            probatio.run_calls(todo, a.agent, opts, out, a.jobs)
        finally:
            probatio.ask = real_ask
    except Hold as h:
        print(f"{PROGRESS} {room['run_id']}: held ({h.kind}): {h.why}", flush=True)
        return HELD_STOP if h.kind == "stop" else HELD_PAUSE
    except SystemExit as e:                     # probatio's own stop: eight failures in a row, or another model
        if isinstance(e.code, str) and e.code.lstrip().startswith("stopped:"):
            print(f"{PROGRESS} {room['run_id']}: {e.code.strip()}", flush=True)
            return FAILURES
        raise
    finally:
        lock.close()
    return DONE


def room_or_refuse():
    """The room this process runs in (room.json), refused unless it is launched in one: PROBATIO_CAL_ROOM names its
    room.json, and the claude first on PATH is that room's shim wrapper, so no call can reach a CLI outside it."""
    if "PROBATIO_CAL_ROOM" not in os.environ:
        raise SystemExit("ordo pilot: ordo_arm.py runs only inside a calibration room (ordo_pilot.py launches it)")
    spec = Path(os.environ["PROBATIO_CAL_ROOM"])
    found = shutil.which("claude")
    if not found or Path(found).resolve() != (spec.parent / "claude").resolve():
        raise SystemExit(f"ordo pilot: the claude on PATH is {found}, not this room's {spec.parent / 'claude'}")
    return json.loads(spec.read_text(encoding="utf-8"))


def main(argv=None):
    p = argparse.ArgumentParser(description="One arm-run of the ordo pilot in its room (launched by ordo_run.py).")
    p.add_argument("--plan", required=True)
    p.add_argument("--stage", type=int, choices=(1, 2), required=True)
    p.add_argument("--arm", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--jobs", type=int, required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--effort", required=True)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    room = room_or_refuse()
    plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    s = plan["subject"]
    if (a.model, a.effort, a.jobs) != (s["model"], s["effort"], s["jobs"]) or room.get("model") != s["model"]:
        raise SystemExit("ordo pilot: the launch's model, effort or jobs is not the plan's subject recipe")
    calls, ids = ordo_pilot.planned_calls(ordo.load_ordo_bank(), plan, a.stage, a.arm)
    tier = ordo_pilot.plan_word(ordo_pilot.stage_tier(plan, a.stage, a.arm))      # PREREG Amendment 5: quick -> smoke
    pa = probatio.parse_args_v4(probatio_args(plan, tier, room["work_dir"], a.out, a.jobs))
    bank, ordo_bank, selection, spec, run = checked_run(pa, calls)
    opts = probatio.agent_options(pa)
    opts.effort, opts.auto_memory = pa.effort, pa.auto_memory
    meta = probatio.preflight_runs(pa, opts, bank, ordo_bank, selection, spec, [run])[0]
    print(PLANNED.format(len(calls)), flush=True)
    if a.dry_run:
        return DONE
    return execute(pa, opts, run, pilot_meta(meta, a.plan, a.stage, a.arm, ids), room, Path(a.out))


if __name__ == "__main__":
    sys.exit(main())
