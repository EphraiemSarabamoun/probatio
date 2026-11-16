"""The claude and codex commands a calibration run sees first on its PATH (bin/<run>/claude and codex, written by
isolate.py, which also writes the room.json this reads). probatio calls them exactly as it would call the real CLIs.

claude, for a print call (-p):
  1. refused, without calling the model, after a hard stop (the room's STOP file);
  2. the call's own context is checked against the room contract (guard.py): its directory and every parent, the
     config dir's contents, the memory file's exact digest or absence, its environment and its arguments (registered
     model and effort, no flag that adds instructions). Any problem writes STOP: the room is no longer what it was
     registered as;
  3. refused while the account's quota scheduler says pause, whichever host's reading put it there, and, in a room
     whose policy requires the ledger (isolate.ROOM_POLICIES), while the ledger cannot be read: such a room reads the
     ledger afresh immediately before the CLI starts, never from the cache, and one read decides the pause and the
     stage gate; a scheduler that
     reports too much unusable telemetry in a row writes STOP; in a staged run, refused with STOP once the stage has
     reached a cap (stages.call_gate, grid.cap_status, on the burn the account ledger has summed since it opened);
  4. auto memory forced off, CLAUDE.md memory forced off in an off run and absent in an on run, the registered effort
     set, the updater off;
  5. --output-format json becomes stream-json --verbose, and the stream is checked as a protocol: an init event must
     come before any reply, with apiKeySource "none" and exactly the pinned Claude Code build (else STOP) and the
     registered model; a later turn of a dialogue may bring init again, accepted only when it repeats the first's
     session, key source, build and model; every rate_limit_event is published to the scheduler, and at least one
     must be usable telemetry; the result's models must be exactly the registered model (plus any auxiliary model
     local.json registers). A result that fails any of these is not passed on: the call comes back as unverified
     (exit 76), the scheduler is told, and dispatch holds;
  6. the accepted result is printed as the one JSON object (or the text) the caller asked for. A caller that asked for
     stream-json gets each event as it comes, except a result: that is checked first (everything step 5 needs is
     already in the stream by then, since Claude Code sends the init and the rate_limit_event before it) and released
     only when it passes, so no unverified success is ever printed. A result is released at once, not at exit,
     because probatio's errand dialogue answers the first result on stdin. If reading the stream fails in any way, the
     child is ended and the call is published as unverified before the error leaves (or, when even that fails, STOP is
     written), so a fault in this code can never leave dispatch running on telemetry it never saw.
Every byte the child writes, stdout and stderr, and every message of the shim's own, passes the redactor (redact.py),
so the in-memory token cannot reach an output sink. Other claude calls (--version, auth status) run as a child with
their output redacted too.

codex: an exec call is checked against the room contract, the CODEX_HOME it actually receives and the exact Codex
command contract (guard.py: no -C, no added directory, profile or instruction-bearing -c key) and refused with STOP on
any problem; it is reserved against the Codex arm's cap of 1,000 charged requests in the account ledger, atomically,
before it runs (at the cap, or with the ledger out of reach: STOP), gets the registered effort (a different one is
refused) and, in an off room, project docs switched off. Codex runs without the Claude token in its environment, with
its output redacted.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard  # noqa: E402
import quota  # noqa: E402
import redact  # noqa: E402
import scheduler  # noqa: E402
import stages  # noqa: E402

PAUSED = 75                     # exit code for a call refused while the quota says pause (EX_TEMPFAIL)
STOPPED = 3                     # exit code for a call refused by a hard stop
UNVERIFIED = 76                 # exit code for a call whose protocol or telemetry could not be verified
INIT_FACTS = ("session_id", "apiKeySource", "claude_code_version", "model")   # what a later turn's init must repeat


def load_room():
    return json.loads(Path(os.environ["PROBATIO_CAL_ROOM"]).read_text(encoding="utf-8"))


def stop(room, reason, scrub):
    """Write the hard stop that halts every run on this host until a person removes it."""
    Path(room["stop_path"]).write_text(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {room['run_id']}: "
                                       f"{scrub(reason)}\n", encoding="utf-8")


def output_mode(args):
    """(mode, args for the real CLI): json and text calls are turned into stream-json; a caller already asking for
    stream-json gets its lines passed through."""
    args = list(args)
    if "--output-format" in args:
        i = args.index("--output-format")
        mode = args[i + 1] if i + 1 < len(args) else "text"
        if mode == "stream-json":
            return mode, args + ([] if "--verbose" in args else ["--verbose"])
        args[i + 1] = "stream-json"
    else:
        mode = "text"
        args += ["--output-format", "stream-json"]
    return mode, args + ([] if "--verbose" in args else ["--verbose"])


def fail(mode, message, code, scrub):
    """Refuse a print call the way a failed Claude Code call looks to its caller."""
    message = scrub(message)
    if mode == "text":
        print(message, file=sys.stderr)
    else:
        print(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                          "result": message}), flush=True)
    return code


def pump(stream, sink, scrub):
    """Copy a child's stream to sink line by line through the redactor (run in a thread)."""
    for line in stream:
        sink.write(scrub(line))
        sink.flush()
    stream.close()


def child(cmd, env, scrub, stdin=None):
    """Run a child with both its streams redacted on their way to ours; returns its exit code."""
    proc = subprocess.Popen(cmd, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True,
                            bufsize=1)
    threads = [threading.Thread(target=pump, args=(proc.stdout, sys.stdout, scrub)),
               threading.Thread(target=pump, args=(proc.stderr, sys.stderr, scrub))]
    for t in threads:
        t.start()
    code = proc.wait()
    for t in threads:
        t.join()
    return code


def claude_env(room):
    """The call's environment: the caller's, with the room's rules put back in force."""
    env = dict(os.environ)
    for k in guard.PROVIDER_VARS:
        env.pop(k, None)
    env.update({"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_EFFORT_LEVEL": room["effort"],
                "DISABLE_AUTOUPDATER": "1"})
    if room["memory"] == "off":
        env[guard.MEMORY_SWITCH] = "1"
    else:
        env.pop(guard.MEMORY_SWITCH, None)          # guard refuses a call that sets it; this keeps the child sure
    return env


class Stream:
    """What one Claude Code stream-json run said, checked as a protocol (module doc, step 5)."""

    def __init__(self, room):
        self.room, self.init, self.result = room, None, None
        self.meters, self.bad_meters, self.problems, self.refused = 0, 0, [], None
        self.rejected = self.early = False

    def event(self, event, sched):
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            self.init_event(event)
        elif kind == "rate_limit_event":
            row = quota.reading(event.get("rate_limit_info"), time.time(), self.room["run_id"])
            sched.publish(row)
            self.rejected = self.rejected or row["status"] == "rejected"
            self.meters += row["valid"]
            self.bad_meters += not row["valid"]
        elif kind in ("assistant", "user", "result"):
            if self.init is None and not self.early:
                self.early = True
                self.problems.append(f"a {kind} event came before init")
            if kind == "result":
                self.result = event

    def init_event(self, event):
        """Check an init event. Every init must name apiKeySource "none" and exactly the pinned build, or the call is
        refused (STOP), and a model other than the registered one is a problem. Claude Code 2.1.309 sends init again at
        the start of every later turn of a stream-json dialogue (an errand and its after-errand report), with the same
        session: found live in the ordo pilot's Stage 1 on 2026-11-08 (docs/ordo/PREREG.md, Amendment 4; real streams
        in tests/fixtures/dialogue-2.1.309/). A later init is accepted only when it repeats the first init's facts
        (INIT_FACTS) and names a session; any difference is a problem, which holds that turn's result. The first init
        stays the one on record."""
        if event.get("apiKeySource") != "none":
            self.refused = f"apiKeySource was {event.get('apiKeySource')!r}, not 'none'"
        elif str(event.get("claude_code_version", "")) != self.room["claude_version"]:
            self.refused = (f"the call ran Claude Code {event.get('claude_code_version')!r}, not the pinned "
                            f"{self.room['claude_version']}")
        elif self.room.get("model") and event.get("model") != self.room["model"]:
            self.problems.append(f"init names model {event.get('model')!r}, not the registered {self.room['model']}")
        if self.init is None:
            self.init = event
            return
        differ = [k for k in INIT_FACTS if event.get(k) != self.init.get(k)]
        if not event.get("session_id") and "session_id" not in differ:
            differ.append("session_id")
        if differ:
            self.problems.append(f"a later init event does not repeat the first ({', '.join(differ)})")

    def verdict(self):
        """The problems that make the result unverified (empty when it may be accepted)."""
        found = list(self.problems)
        if self.init is None:
            found.append("no init event")
        if not self.meters:
            found.append("no usable quota telemetry" + (f" ({self.bad_meters} malformed)" if self.bad_meters else ""))
        r = self.result
        if r is not None and not r.get("is_error") and self.room.get("model"):
            used = set(r.get("modelUsage") or {})
            allowed = {self.room["model"], *self.room.get("auxiliary_models", [])}
            if self.room["model"] not in used or not used <= allowed:
                found.append(f"the result's models {sorted(used)} are not the registered {self.room['model']}"
                             + (f" (with auxiliary {sorted(allowed - {self.room['model']})})"
                                if len(allowed) > 1 else ""))
        return found


def claude(room, args, scrub):
    if "-p" not in args and "--print" not in args:
        return child([room["claude_binary"], *args], claude_env(room), scrub)
    mode, real_args = output_mode(args)
    if Path(room["stop_path"]).exists():
        return fail(mode, f"probatio-cal: stopped: {Path(room['stop_path']).read_text().strip()}", STOPPED, scrub)
    broken = guard.claude_call_problems(room, os.environ, os.getcwd(), args)
    if broken:
        stop(room, "the call's context broke the room contract: " + "; ".join(broken), scrub)
        return fail(mode, "probatio-cal: refused: " + "; ".join(broken), STOPPED, scrub)
    sched = scheduler.Scheduler(room["quota"])
    required = (room.get("policy") or {}).get("ledger") == "required"     # a pilot room (isolate.ROOM_POLICIES)
    snapshot = sched.ledger(fresh=required)     # one read decides the pause and the stage gate below; a room that
    if required and snapshot[1]:                # requires the ledger reads it afresh, so no cached copy can stand in
        return fail(mode, "probatio-cal: paused: the quota ledger cannot be read, and this room's policy requires "
                          "it; nothing was launched", PAUSED, scrub)
    until, why, info = sched.pause(snapshot=snapshot)
    if info["stop"]:                                    # the weekly study stop, or telemetry failing too often
        stop(room, f"the quota scheduler stops the study ({why})", scrub)
        return fail(mode, f"probatio-cal: stopped: {why}", STOPPED, scrub)
    if info["degraded"]:
        print("probatio-cal: the quota ledger is out of reach; deciding from this host's readings", file=sys.stderr)
    if until:
        return fail(mode, f"probatio-cal: paused for quota until {quota.when(until)} ({why}, reading from "
                          f"{info['decided_by']})", PAUSED, scrub)
    capped = stages.call_gate(room.get("stage"), *snapshot)
    if capped:                                          # the stage is at a cap: it waits for Ephraiem's go
        stop(room, capped, scrub)
        return fail(mode, f"probatio-cal: stopped: {capped}", STOPPED, scrub)
    proc = subprocess.Popen([room["claude_binary"], *real_args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=claude_env(room), text=True, bufsize=1)
    errs = threading.Thread(target=pump, args=(proc.stderr, sys.stderr, scrub))
    errs.start()
    stream, held = Stream(room), None
    try:
        held = relay(proc, stream, sched, mode, scrub)
    except Exception as e:                              # a fault here must still hold dispatch (module doc, step 6)
        proc.kill()                                     # our own child
        hold_after_fault(room, sched, f"the shim failed reading the stream ({type(e).__name__})", scrub)
        raise
    proc.stdout.close()
    code = proc.wait()
    errs.join()
    if stream.refused:
        stop(room, stream.refused, scrub)
        return fail(mode, f"probatio-cal: refused: {stream.refused}", STOPPED, scrub)
    if quota.limit_error(stream.result) and not stream.rejected:
        sched.publish(quota.rejected(time.time(), room["run_id"]))
    problems = held or stream.verdict()
    if problems:                                        # the reasons carry child output: redacted before any sink
        sched.publish(quota.unverified(time.time(), room["run_id"], scrub("; ".join(problems))))
        return fail(mode, "probatio-cal: unverified: " + "; ".join(problems), UNVERIFIED, scrub)
    if stream.result is None:
        return fail(mode, f"probatio-cal: claude exited {code} without a result", code or 1, scrub)
    if mode == "json":
        print(scrub(json.dumps(stream.result)), flush=True)
    elif mode == "text":
        print(scrub(stream.result.get("result") or ""), flush=True)
    return code                                         # stream-json: every event, results included, went out above


def relay(proc, stream, sched, mode, scrub):
    """Read the child's stream-json to its end (or to a refusal), feeding every event to stream and, for a caller that
    asked for stream-json, passing each line on, except a successful result the shim cannot vouch for. Returns the
    problems that held such a result (None when none was held)."""
    for line in proc.stdout:
        try:
            event = json.loads(line)
        except ValueError:
            event = None
        if isinstance(event, dict):
            stream.event(event, sched)
        if stream.refused:
            proc.kill()                                 # our own child: no further turn may run on it
            return None
        if mode == "stream-json":
            if isinstance(event, dict) and event.get("type") == "result" and not event.get("is_error"):
                held = stream.verdict()
                if held:                                # never print a success the shim cannot vouch for
                    proc.kill()                         # our own child: its unverified turn ends here
                    return held
            sys.stdout.write(scrub(line))
            sys.stdout.flush()
    return None


def hold_after_fault(room, sched, why, scrub):
    """After a fault while reading a call's stream: publish the call as unverified, which the meter holds on (bounded,
    and a hard stop after too many in a row); if even that fails, write STOP, so dispatch never continues unheld."""
    try:
        sched.publish(quota.unverified(time.time(), room["run_id"], why))
    except Exception as e:                              # noqa: BLE001  the last resort is the hard stop
        stop(room, f"{why}; the unverified mark could not be recorded ({type(e).__name__})", scrub)


def codex_env():
    """Codex's environment: the caller's without any Claude, Anthropic or OpenAI credential."""
    env = dict(os.environ)
    for k in list(env):
        if k.startswith(("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC", "OPENAI")):
            env.pop(k)
    return env


def codex(room, args, scrub):
    env = codex_env()
    binary = _which(room["codex_binary"], env)
    if args[:1] != ["exec"]:
        return child([binary, *args], env, scrub, stdin=sys.stdin)
    if Path(room["stop_path"]).exists():
        print(scrub(f"probatio-cal: stopped: {Path(room['stop_path']).read_text().strip()}"), file=sys.stderr)
        return STOPPED
    broken = guard.codex_call_problems(room, os.environ, os.getcwd(), args)
    if broken:
        stop(room, "the Codex call's context broke the room contract: " + "; ".join(broken), scrub)
        print(scrub("probatio-cal: refused: " + "; ".join(broken)), file=sys.stderr)
        return STOPPED
    try:                                                # reserved before it runs, atomically, arm-wide
        granted, _ = stages.reserve_codex(room["quota"], room["codex_cap"])
    except stages.account.Unreachable as e:
        stop(room, f"the Codex arm's request count in the quota ledger cannot be reached ({e})", scrub)
        print(scrub(f"probatio-cal: stopped: the Codex arm's request count cannot be reached ({e})"), file=sys.stderr)
        return STOPPED
    if not granted:
        stop(room, f"the Codex arm reached its cap of {room['codex_cap']} charged requests", scrub)
        print(f"probatio-cal: the Codex cap of {room['codex_cap']} charged requests is reached", file=sys.stderr)
        return STOPPED
    extra = []
    if not guard.codex_config_values(args, "model_reasoning_effort"):
        extra += ["-c", f'model_reasoning_effort="{room["effort"]}"']
    if room["memory"] == "off" and not guard.codex_config_values(args, "project_doc_max_bytes"):
        extra += ["-c", "project_doc_max_bytes=0"]
    return child([binary, "exec", *extra, *args[1:]], env, scrub, stdin=sys.stdin)


def _which(name, env):
    """The executable the Codex setting names (guard.executable, the lookup the registration hashed)."""
    found = guard.executable(name, env.get("PATH", ""))
    if not found:
        raise SystemExit(f"probatio-cal: {name} not found on PATH")
    return found


def main(argv):
    scrub = redact.Redactor()
    try:
        cli, args = argv[1], argv[2:]
        room = load_room()
        return claude(room, args, scrub) if cli == "claude" else codex(room, args, scrub)
    except Exception as e:                              # a crash must not print an unredacted traceback
        print(scrub(f"probatio-cal: shim error: {type(e).__name__}: {e}"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
