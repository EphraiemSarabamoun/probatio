#!/usr/bin/env python3
"""One scheduled calibration run, end to end (CALIBRATION.md sections 5 and 6).

Given a run spec (a JSON file; grid.py will write them), it:
  1. checks the spec, and every forwarded probatio argument against an allowlist of exact flag names, so no
     abbreviation, alias, short option, duplicate or harness-owned flag can change the subject;
  2. computes the run's fingerprint (agent, model, effort, memory condition and digest, CLI contract, jobs, normalised
     arguments, and the pinned Claude Code, Codex and probatio identities: each executable as it resolves, a command
     name looked up on PATH, with its sha256). On a resume, any change is refused before the room or the record is
     touched; the refused attempt is logged in rejected-resumes.jsonl and the registration stays as it was. The
     fingerprint is computed again before every launch, and a change ends the run as refused;
  3. builds the run's clean room (isolate.py), whose contract fixes the memory file's exact digest or absence;
  4. checks the room, then runs probatio.py --dry-run and refuses unless it lists exactly the run's memory (none for
     an off run), reports the registered model and jobs, and states how many calls it plans;
  5. runs probatio.py in the room. Before every launch, the first included, it checks for a hard stop and checks the
     room again. While the account's quota scheduler (scheduler.py) says pause, it sleeps in steps of STOP_POLL_S and
     checks for a hard stop at every step. The room's shim refuses calls while paused, so probatio stops after its
     error streak; this runner then reruns the same command, which resumes and retries the failed calls. probatio's
     output reaches harness.log only through the redactor;
  6. calls the run done only when every planned call has an answer recorded and none failed.
A staged spec (one grid.py wrote, with a "stage") is run from its plan folder and passes the study's gates (stages.py):
before anything else, grid.eligibility must find it ready and its stage is opened in the account ledger with the caps
grid.stage_caps fixes; before every launch, and in the shim before every charged call, the stage must be under its
caps (grid.cap_status), or the run stops with STOP written and waits for Ephraiem's go.
The record, calibration.json in the run's folder, keeps the registration (fingerprint and the canonical spec behind
it), the memory file's sha256, the planned call count, the stage gate's decision, every attempt and pause, and the
outcome.

A spec:
    {"run_id": "sonnet-L4-organization-kelthorne", "agent": "claude", "model": "claude-sonnet-5-5",
     "memory": "on", "memory_file": "rendered/L4.organization.kelthorne.md", "jobs": 4,
     "cli": "v4", "probatio_args": ["--target-type", "organization", "--target", "Kelthorne Labs", "--tier", "standard"]}
memory_file is relative to the spec's folder, or null for none. cli "v3" runs probatio v3, which has no --memory and
asks both conditions (smoke tests only).

    python3 calibration/run.py spec.json [--local calibration/local.json] [--dry-run]
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard  # noqa: E402
import isolate  # noqa: E402
import quota  # noqa: E402
import redact  # noqa: E402
import scheduler  # noqa: E402
import stages  # noqa: E402

PROBATIO_V4_EFFORTS = ("low", "medium", "high", "xhigh", "max")    # probatio.py's EFFORTS (v4 has no "auto")
MAX_ATTEMPTS = 6                # reruns for failures that are not a quota pause, before the run is marked failed
RETRY_WAIT_S = 120              # times the attempt number, between such reruns
STOP_POLL_S = 60                # a pause sleeps in steps no longer than this, checking for a hard stop each time
# The probatio flags a spec may forward, by exact name, each with exactly one value. Everything else (the harness's
# own --agent, --model, --workdir, --out, --jobs, --memory, --auto-memory, --effort, --dry-run, any abbreviation
# argparse would accept, short options) is refused.
ALLOWED_ARGS = ("--target-type", "--target", "--name", "--target-short", "--target-possessive", "--tier", "--arm",
                "--class", "--dataset", "--seed", "--local")


class Refusal(isolate.Refusal):
    pass


# ---- the spec -------------------------------------------------------------------------------------------------------
def normalize_args(args, where="spec"):
    """The forwarded arguments as [--flag, value, ...], or Refusal: exact allowed names only, once each, each with one
    value that does not itself start with a dash."""
    if not isinstance(args, list):
        raise Refusal(f"{where}: probatio_args must be a list")
    out, seen, i = [], set(), 0
    while i < len(args):
        a = args[i]
        if not isinstance(a, str) or not a.startswith("--"):
            raise Refusal(f"{where}: probatio_args item {a!r} is not an allowed --flag")
        name, eq, value = a.partition("=")
        if name not in ALLOWED_ARGS:
            raise Refusal(f"{where}: {name} is not a flag a spec may forward (exact names only: "
                          f"{', '.join(ALLOWED_ARGS)})")
        if name in seen:
            raise Refusal(f"{where}: {name} is given twice")
        if not eq:
            i += 1
            if i >= len(args):
                raise Refusal(f"{where}: {name} has no value")
            value = args[i]
        if not isinstance(value, str) or not value or value.startswith("-"):
            raise Refusal(f"{where}: the value of {name} must be text that does not start with a dash")
        out += [name, value]
        seen.add(name)
        i += 1
    return out


def load_spec(path):
    """The run spec, checked: required fields, known values, the forwarded arguments normalised, and the memory file
    read as bytes (relative to the spec's folder)."""
    path = Path(path)
    spec = json.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in ("run_id", "agent", "model", "memory") if not spec.get(k)]
    if missing:
        raise Refusal(f"{path}: missing {', '.join(missing)}")
    spec.setdefault("jobs", 4)
    spec.setdefault("cli", "v4")
    spec.setdefault("effort", isolate.EFFORT)
    spec.setdefault("probatio_args", [])
    if spec["cli"] not in ("v3", "v4") or isinstance(spec["jobs"], bool) or not isinstance(spec["jobs"], int) \
            or spec["jobs"] < 1:
        raise Refusal(f"{path}: cli must be v3 or v4 and jobs a positive integer")
    if spec["cli"] == "v4" and spec["effort"] not in PROBATIO_V4_EFFORTS:
        raise Refusal(f"{path}: probatio v4 takes --effort {', '.join(PROBATIO_V4_EFFORTS)}, not {spec['effort']!r}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", str(spec["model"])):
        raise Refusal(f"{path}: model {spec['model']!r} is not a model name")
    spec["probatio_args"] = normalize_args(spec["probatio_args"], str(path))
    spec["memory_bytes"] = (path.parent / spec["memory_file"]).read_bytes() if spec.get("memory_file") else None
    if spec.get("stage") is not None:               # a staged spec is gated by its plan (stages.py)
        if not (path.parent / "manifest.json").is_file():
            raise Refusal(f"{path}: a staged spec is run from the plan folder grid.py wrote, beside its manifest.json")
        spec["plan_dir"] = str(path.parent.resolve())
    return spec


def command(spec, room, cfg, out, dry_run=False):
    """The probatio command for this run: the harness's own flags, then the normalised forwarded ones. A v4 run gets
    its memory condition, auto memory off and the registered effort as flags, so probatio's own run.json and
    scorecard record them; the shim still enforces both through the environment, whatever the flags say. A v3 run
    (smoke tests) names --dataset, since probatio takes a command line without a v3-only flag as v4's."""
    cmd = [sys.executable, cfg["probatio"], "--agent", spec["agent"], "--model", spec["model"],
           "--workdir", str(room.work_dir), "--out", str(out), "--jobs", str(spec["jobs"])]
    forwarded = normalize_args(list(spec["probatio_args"]))
    if spec["cli"] == "v4":
        cmd += ["--memory", spec["memory"], "--auto-memory", "off", "--effort", spec["effort"]]
    elif "--dataset" not in forwarded:
        forwarded += ["--dataset", "all"]
    return cmd + forwarded + (["--dry-run"] if dry_run else [])


# ---- the registration -----------------------------------------------------------------------------------------------
def file_identity(path):
    """A pinned file's real path and sha256 (None when it is not a file on this host)."""
    real = Path(path).expanduser().resolve()
    if not real.is_file():
        return {"path": str(real), "sha256": None}
    h = hashlib.sha256()
    with open(real, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return {"path": str(real), "sha256": h.hexdigest()}


def binary_identity(binary, version):
    """A pinned CLI as the run will execute it: the file the setting resolves to (a command name is looked up on this
    process's PATH, as the shim will look it up, guard.executable) with that file's sha256, the setting itself and the
    pinned version. Same name and version with other bytes is another identity."""
    resolved = guard.executable(binary, os.environ.get("PATH", ""))
    found = file_identity(resolved) if resolved else {"path": None, "sha256": None}
    return dict(found, binary=binary, version=version)


def fingerprint(spec, cfg):
    """(digest, canonical): everything that makes a run the run it was registered as."""
    canonical = {"agent": spec["agent"], "model": spec["model"], "effort": spec["effort"], "memory": spec["memory"],
                 "stage": spec.get("stage"),
                 "memory_sha256": hashlib.sha256(spec["memory_bytes"]).hexdigest()
                 if spec.get("memory_bytes") is not None else None,
                 "cli": spec["cli"], "jobs": spec["jobs"], "probatio_args": normalize_args(list(spec["probatio_args"])),
                 "claude": binary_identity(cfg["claude_binary"], cfg["claude_version"]),
                 "codex": binary_identity(cfg["codex_binary"], cfg["codex_version"]),
                 "probatio": file_identity(cfg["probatio"]), "world": file_identity(isolate.world_script(cfg))}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest(), canonical


def changed_keys(canonical, old):
    """The registration's top-level entries that differ between two canonical forms."""
    return sorted(k for k in set(canonical) | set(old or {}) if canonical.get(k) != (old or {}).get(k))


def registered(out, digest, canonical, now):
    """The run's earlier record when this is a resume of the same registration, None for a new run, or Refusal (logged
    in rejected-resumes.jsonl, the registration left as it was) when anything registered has changed."""
    path = Path(out) / "calibration.json"
    if not path.is_file():
        return None
    old = json.loads(path.read_text(encoding="utf-8"))
    reg = old.get("registration") or {}
    if reg.get("fingerprint") == digest:
        return old
    changed = changed_keys(canonical, reg.get("canonical")) or ["(no registration on record)"]
    with open(Path(out) / "rejected-resumes.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": now, "fingerprint": digest, "changed": changed, "canonical": canonical}) + "\n")
    raise Refusal(f"{out} was registered with another {', '.join(changed)}; a run's registration never changes "
                  "(start a new run id)")


# ---- the preflight --------------------------------------------------------------------------------------------------
def expected_memory(spec, room):
    """The memory files the dry run must list: the run's file for a memory-on run (v3 lists the with-memory files of
    every run, since it asks both conditions), else none."""
    if room.contract["memory"].get("absent"):
        return []
    return [room.memory_path] if spec["cli"] == "v3" or spec["memory"] == "on" else []


def listed_memory(text):
    """The memory files a probatio preflight lists: the indented lines after its "memory files" line, with any
    trailing note in two spaces and parentheses dropped. "none found" on that line means none."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "memory files" in line.lower():
            if "none found" in line.lower():
                return []
            indent = len(line) - len(line.lstrip())
            files = []
            for nxt in lines[i + 1:]:
                if not nxt.strip() or len(nxt) - len(nxt.lstrip()) <= indent:
                    break
                files.append(Path(re.sub(r"\s{2,}\(.*\)$", "", nxt.strip())))
            return files
    raise Refusal("the dry run printed no memory-file list")


def preflight_problems(expected, listed):
    """How the dry run's memory list differs from what the run should load (empty when they agree)."""
    want = sorted(str(Path(p).resolve()) for p in expected)
    got = sorted(str(Path(p).expanduser().resolve()) for p in listed)
    if want == got:
        return []
    return [f"the dry run expects memory {got or 'none'}, the run should load {want or 'none'}"]


def preflight_settings(text):
    """What the dry run says it will do: {"model", "effort", "jobs", "planned"} (None where it does not say; v3 states
    no effort). The planned count is the "all" line's when there is one, else the sum of the per-dataset (or
    per-class) lines."""
    model = re.search(r", model ([^,\n]+?)(?:, effort [^\n]*)?$", text, re.M)
    effort = re.search(r", model [^\n]*, effort (\S+)\s*$", text, re.M)
    jobs = re.search(r"--jobs (\d+)", text)
    rows = re.findall(r"^\s+(\S+)\s+\d+ items\s+(\d+) calls", text, re.M)
    total = [int(n) for name, n in rows if name == "all"]
    return {"model": model.group(1).strip() if model else None, "effort": effort.group(1) if effort else None,
            "jobs": int(jobs.group(1)) if jobs else None,
            "planned": total[0] if total else (sum(int(n) for _, n in rows) if rows else None)}


def settings_problems(spec, settings):
    found = []
    if settings["model"] != spec["model"]:
        found.append(f"the dry run reports model {settings['model']!r}, not the registered {spec['model']}")
    if settings["effort"] is not None and settings["effort"] != spec["effort"]:
        found.append(f"the dry run reports effort {settings['effort']!r}, not the registered {spec['effort']}")
    if settings["jobs"] != spec["jobs"]:
        found.append(f"the dry run reports --jobs {settings['jobs']}, not the registered {spec['jobs']}")
    if not settings["planned"]:
        found.append("the dry run states no planned calls")
    return found


# ---- the outcome of one attempt -------------------------------------------------------------------------------------
def call_errors(out):
    """(calls answered, calls whose latest record is an error) from probatio's replies.jsonl. A call is keyed as v3
    keys it (condition, dataset or class, id), and a later line for the same call replaces an earlier one."""
    path = Path(out) / "replies.jsonl"
    latest = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                latest[(rec.get("condition"), rec.get("dataset") or rec.get("class"), rec.get("id"))] = rec
    errors = sum(1 for r in latest.values() if r.get("error"))
    return len(latest) - errors, errors


def next_step(rc, errors, stopped, pause, failures, complete=True):
    """What to do after an attempt: "done" (every planned call answered, none failed), "stop" (a hard stop), "pause"
    (sleep until the quota clears; not counted as a failure), "retry" (rerun, which retries failed or missing calls)
    or "fail". `failures` counts the failed attempts so far, this one included. A nonzero exit with no failed call
    means probatio refused to start (a resume with other settings, a bad flag): rerunning cannot help."""
    if stopped:
        return "stop"
    if rc == 0 and errors == 0 and complete:
        return "done"
    if pause:
        return "pause"
    if rc != 0 and errors == 0:
        return "fail"
    return "retry" if failures < MAX_ATTEMPTS else "fail"


# ---- the run --------------------------------------------------------------------------------------------------------
def write_record(out, record):
    path = Path(out) / "calibration.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def launch(cmd, room, env, out, scrub, number, started):
    """One probatio attempt, its output appended to harness.log through the redactor; returns its exit code."""
    with open(out / "harness.log", "a", encoding="utf-8") as logf:
        logf.write(f"\n=== attempt {number} at {quota.when(started)} ===\n")
        logf.flush()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=room.work_dir, env=env,
                                text=True, bufsize=1)
        for line in proc.stdout:
            logf.write(scrub(line))
        proc.stdout.close()
        return proc.wait()


def stage_dispatch(spec, cfg, now, dry_run=False):
    """A staged run's gate before it starts (stages.dispatch; a dry run opens no stage), or None for a run outside the
    staged study. Raises Refusal with the reasons."""
    if spec.get("stage") is None:
        return None
    try:
        return stages.dispatch(spec, cfg, now, scheduler.conf_from(cfg, Path(cfg["root"])), open_stage=not dry_run)
    except (ValueError, OSError, stages.account.Unreachable) as e:
        raise Refusal(f"{spec['run_id']} may not start: {e}") from None


def run(spec, cfg, dry_run=False, sleep=time.sleep, now=time.time, log=print):
    """Run one spec to its end; returns the record. sleep, now and log are for tests."""
    gate = stage_dispatch(spec, cfg, now(), dry_run)    # before anything is created or registered
    out = Path(cfg["results_dir"]) / spec["run_id"]
    out.mkdir(parents=True, exist_ok=True)
    digest, canonical = fingerprint(spec, cfg)
    old = registered(out, digest, canonical, now())
    room = isolate.build(cfg, spec["run_id"], spec["agent"], spec["memory"], spec["effort"], spec["memory_bytes"],
                         model=spec["model"], out_dir=out, stage=spec.get("stage"))
    env = isolate.environment(room, cfg)
    scrub = redact.Redactor(env)
    try:                                            # a refusal leaves only through the token-aware redactor
        isolate.check(room, cfg, env)
        dry = subprocess.run(command(spec, room, cfg, out, dry_run=True), capture_output=True, text=True,
                             cwd=room.work_dir, env=env)
        if dry.returncode:
            raise Refusal(f"probatio --dry-run failed (exit {dry.returncode}): "
                          f"{scrub.clip(dry.stderr.strip(), 400, tail=True)}")
        settings = preflight_settings(dry.stdout)
        problems = preflight_problems(expected_memory(spec, room), listed_memory(dry.stdout))
        problems += settings_problems(spec, settings)
        if problems:
            raise Refusal("; ".join(problems))
    except isolate.Refusal as e:
        raise Refusal(scrub(str(e))) from None
    record = {"run_id": spec["run_id"], "registration": {"fingerprint": digest, "canonical": canonical},
              "spec": {k: v for k, v in spec.items() if k != "memory_bytes"},
              "memory_file": str(room.memory_path) if room.memory_sha256 else None,
              "memory_sha256": room.memory_sha256, "effort": spec["effort"], "claude_version": cfg["claude_version"],
              "room": str(room.bin_dir / "room.json"), "planned": settings["planned"], "started": now(),
              "stage_gate": gate, "attempts": [], "pauses": [], "outcome": "dry run" if dry_run else "running"}
    if old:                                         # a resumed run keeps its registration and its history
        record.update(started=old.get("started", record["started"]), attempts=old.get("attempts", []),
                      pauses=old.get("pauses", []))
    write_record(out, record)
    if dry_run:
        log(scrub(dry.stdout.rstrip()))
        return record

    def finish(outcome):
        record.update(outcome=outcome, ended=now())
        write_record(out, record)
        return record

    def stopped():
        return scrub(room.stop_path.read_text().strip()) if room.stop_path.exists() else None

    failures = sum(1 for a in record["attempts"] if a.get("step") in ("retry", "fail"))
    sched = scheduler.Scheduler(scheduler.conf_from(cfg, room.root), clock=now)
    while True:
        if stopped():
            return finish(f"stopped: {stopped()}")
        until, why, info = sched.pause()
        if info["stop"]:                            # the weekly study stop, or telemetry failing too often
            room.stop_path.write_text(f"{quota.when(now())} {spec['run_id']}: the quota scheduler stops the study "
                                      f"({why})\n", encoding="utf-8")
            continue
        if until:
            record["pauses"].append({"from": now(), "until": until, "why": why, "decided_by": info["decided_by"],
                                     "limits": info["limits"], "degraded": info["degraded"]})
            write_record(out, record)
            log(f"{spec['run_id']}: paused for quota until {quota.when(until)} ({why}, reading from "
                f"{info['decided_by']}" + (", ledger out of reach" if info["degraded"] else "") + ")")
            while until and until > now() and not stopped():
                sleep(min(until - now(), STOP_POLL_S))
                until, why, info = sched.pause()
            continue
        now_digest, now_canonical = fingerprint(spec, cfg)
        if now_digest != digest:                    # a CLI or probatio replaced on disk since the registration
            return finish(f"refused: the registered {', '.join(changed_keys(now_canonical, canonical))} changed on "
                          "disk since the run was registered")
        try:                                        # before every launch, the first included
            isolate.check(room, cfg, env)
        except isolate.Refusal as e:
            return finish(f"stopped: {stopped()}" if stopped() else f"refused: {scrub(e)}")
        capped = stages.call_gate(spec.get("stage"), *sched.ledger())
        if capped:                                  # the stage is at a cap: it waits for Ephraiem's go
            room.stop_path.write_text(f"{quota.when(now())} {spec['run_id']}: {capped}\n", encoding="utf-8")
            return finish(f"stopped: {scrub(capped)}")
        started = now()
        rc = launch(command(spec, room, cfg, out), room, env, out, scrub, len(record["attempts"]) + 1, started)
        answered, errors = call_errors(out)
        complete = answered >= record["planned"] and errors == 0
        pause, _, _ = sched.pause()
        step = next_step(rc, errors, bool(stopped()), pause, failures + 1, complete)
        record["attempts"].append({"started": started, "ended": now(), "rc": rc, "answered": answered,
                                   "planned": record["planned"], "errors": errors, "step": step})
        log(f"{spec['run_id']}: attempt {len(record['attempts'])}: exit {rc}, {answered} of {record['planned']} "
            f"answered, {errors} failed: {step}")
        if step in ("retry", "fail"):
            failures += 1
        if step == "done":
            return finish("done")
        if step == "stop":
            return finish(f"stopped: {stopped()}")
        if step == "fail":
            return finish("failed")
        write_record(out, record)
        if step == "retry":
            sleep(RETRY_WAIT_S * failures)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("spec", help="the run spec (JSON)")
    p.add_argument("--local", default=str(isolate.LOCAL), help="this host's settings (default calibration/local.json)")
    p.add_argument("--dry-run", action="store_true", help="build and check the room and run the preflight only")
    a = p.parse_args(argv)
    scrub = redact.Redactor()
    try:
        record = run(load_spec(a.spec), isolate.load_local(a.local), dry_run=a.dry_run)
    except isolate.Refusal as e:
        print(f"refused: {scrub(e)}", file=sys.stderr)
        return 2
    print(f"{record['run_id']}: {scrub(record['outcome'])}")
    return 0 if record["outcome"] in ("done", "dry run") else 1


if __name__ == "__main__":
    sys.exit(main())
