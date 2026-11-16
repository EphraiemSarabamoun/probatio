#!/usr/bin/env python3
"""The account's quota ledger: one small state file on one host, which every calibration stream on every host reads
and writes, so pause and resume are decided once for the subscription they all draw on (two hosts share one
Max account).

The ledger is a directory on its home host (local.json's quota.home_dir, as the home names it):
  account.json   {"version", "limits", "meter", "hosts", "updated", "burn", "stages", "recovery", "study_stop",
                 "study_releases"}: the account's quota meter (meter.py: each window's high-water mark and reset,
                 rejections, unknown telemetry), folded from every reading any host has published, the pause limits
                 every host uses, when each host last published, the account's burn (meter.py: the chronological sum
                 of positive increments over every published reading), the calibration's stage book (per stage, the
                 burn when it opened, its caps and prediction, and its charged requests), and the two registered
                 quota states (CALIBRATION.md 7) the scheduler enforces:
                   recovery     set when the five-hour window reaches its limit; cleared only by a reading of a later
                                window below RESUME_FIVE_HOUR (0.40), so the study resumes only below 0.40 after a reset
                   study_stop   latched when the seven-day window reaches its limit; never cleared by a reading, a
                                reset or a resume, only by release-study-stop (study_releases keeps every release)
  account.lock   the flock every operation takes (exclusive to write, shared to read)
A host other than the home reaches the ledger over ssh by running this very file there with `python3 -`, its source on
stdin (with meter.py's prepended) and the operation in argv, so the home needs no copy of the harness. That is why
this file and meter.py import only the standard library and must stay runnable together on their own.

Operations (each prints, and call() returns, the ledger's state):
  read          the state, unchanged
  publish       fold a reading into the meter (meter.py's rules); note which host sent it
  init          set the limits if the ledger has none yet, else leave them (every host's check runs this)
  set-limits    replace the limits: an operator's decision, which every host follows at its next read
  stage-open    open a stage once: record the burn now as its baseline, with its caps and the prediction they came
                from. Opening it again changes nothing, so a resume keeps the baseline; other caps are refused
  reserve       count one charged request against a stage's cap, atomically under the lock: granted only while the
                count is below the cap, so concurrent callers can never overshoot it (the answer's "reserved")
  release-study-stop
                an operator's release of the weekly study stop after Ephraiem's go, with its reason: only from this
                file's command line on the home (--release-study-stop), never by a host's harness. It releases the
                stop for the seven-day window it was latched in; the 0.60 pause still holds until that window resets
                unless the limits are raised (--set-limits)

    python3 calibration/account.py --home-dir /var/tmp/probatio-cal/account --show
    python3 calibration/account.py --home-dir /var/tmp/probatio-cal/account --set-limits 0.6 0.6
    python3 calibration/account.py --home-dir /var/tmp/probatio-cal/account --release-study-stop "Ephraiem's go, <date>"
"""
import argparse
import base64
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

VERSION = 2
OPERATIONS = ("read", "publish", "init", "set-limits", "stage-open", "reserve")   # what call() may run over ssh
RESUME_FIVE_HOUR = 0.40         # CALIBRATION.md 7: after a five-hour pause, resume only on a fresh reading below this
try:                                    # shipped over ssh: meter.py's source was prepended to this file's
    _SHIPPED = _PROBATIO_SHIPPED       # noqa: F821
except NameError:
    _SHIPPED = False
if not _SHIPPED:
    SOURCE = Path(__file__).resolve()
    sys.path.insert(0, str(SOURCE.parent))
    from meter import (METER_SAME_WINDOW_S, meter_burn_add, meter_burn_clean, meter_empty,  # noqa: E402
                       meter_num, meter_observe)


class Unreachable(Exception):
    """The ledger could not be read or written: its home did not answer, or answered with an error."""


# ---- the ledger on its home -----------------------------------------------------------------------------------------
@contextlib.contextmanager
def locked(home_dir, exclusive):
    """Hold the ledger's flock for the body: exclusive to change the state, shared to read it."""
    home = Path(home_dir)
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(home / "account.lock", "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield home
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load(home):
    """The state in account.json, or an empty ledger. A version 1 ledger (one newest reading) is read as a meter
    folded from that reading; a ledger from before the stage book starts with no burn, no stages and no quota
    states."""
    try:
        state = json.loads((Path(home) / "account.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {"version": VERSION, "limits": None, "meter": meter_empty(), "hosts": {}, "updated": None}
    if isinstance(state, dict) and state.get("version") == 1:
        newest = state.pop("newest", None)
        state.update(version=VERSION, meter=meter_observe(meter_empty(), newest) if newest else meter_empty())
    if not isinstance(state, dict) or state.get("version") != VERSION:
        raise ValueError(f"{home}/account.json is not a version {VERSION} ledger")
    state["burn"] = meter_burn_clean(state.get("burn"))
    state.setdefault("stages", {})
    for key, empty in (("recovery", None), ("study_stop", None), ("study_releases", [])):
        state.setdefault(key, empty)
    return state


def save(home, state):
    """Write account.json whole, through a temporary file and a rename, so a reader never sees half of it."""
    tmp = Path(home) / f"account.json.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, Path(home) / "account.json")


def valid_limits(limits):
    """{"five_hour": x, "seven_day": y}, each a number in (0, 1], or ValueError."""
    if not isinstance(limits, dict) or set(limits) != {"five_hour", "seven_day"}:
        raise ValueError(f"limits must name five_hour and seven_day, not {limits!r}")
    for k, v in limits.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 1:
            raise ValueError(f"limit {k} must be a number in (0, 1], not {v!r}")
    return {k: float(v) for k, v in limits.items()}


def apply(home_dir, op, payload, now=None):
    """Run one operation on the ledger at home_dir (on this host) and return the state after it."""
    now = time.time() if now is None else now
    if op == "read":
        with locked(home_dir, exclusive=False) as home:
            return load(home)
    with locked(home_dir, exclusive=True) as home:
        state = load(home)
        answer = {}
        if op == "publish":
            row, host = payload["reading"], str(payload["host"])
            if not isinstance(row, dict) or not meter_num(row.get("t")):
                raise ValueError("a published reading needs a finite numeric t")
            row = dict(row, host=host)
            state["meter"] = meter_observe(state["meter"], row)
            state["burn"] = meter_burn_add(state["burn"], row)
            state["hosts"][host] = {"last_t": row["t"], "seen": round(now, 1)}
            quota_states(state, row)
        elif op == "init":
            if state["limits"] is None:
                state["limits"] = valid_limits(payload["limits"])
        elif op == "set-limits":
            state["limits"] = valid_limits(payload["limits"])
        elif op == "stage-open":
            stage_open(state, payload, now)
        elif op == "reserve":
            answer["reserved"] = reserve(state, payload)
        elif op == "release-study-stop":
            release_study_stop(state, payload, now)
        else:
            raise ValueError(f"unknown ledger operation {op!r}")
        state["updated"] = round(now, 1)
        save(home, state)
        return dict(state, **answer)


def valid_caps(caps):
    """A stage's caps: {name: a nonnegative finite number}, or ValueError."""
    if not isinstance(caps, dict) or not caps or any(not isinstance(k, str) or not meter_num(v) or v < 0
                                                     for k, v in caps.items()):
        raise ValueError(f"a stage's caps must map names to nonnegative numbers, not {caps!r}")
    return caps


def stage_open(state, payload, now):
    """Open payload["stage"] once (module doc): its baseline is the account's burn now. Reopening with the same caps
    changes nothing; reopening with other caps is refused, since a stage's caps are fixed when it starts."""
    name, caps = str(payload["stage"]), valid_caps(payload["caps"])
    entry = state["stages"].setdefault(name, {"charged_requests": 0})
    if entry.get("opened") is not None:
        if entry.get("caps") != caps:
            raise ValueError(f"stage {name} was opened with caps {entry.get('caps')}, not {caps}; a stage's caps "
                             "never change once it has started")
        return
    entry.update(opened=round(now, 1), baseline=dict(state["burn"]["total"]), caps=caps,
                 predicted=payload.get("predicted"))


def later_window(resets, t, paused):
    """Whether a reading (its reset time, its time) belongs to a window after the paused one {"window_resets",
    "since"}: a later reset, or, when the paused window had none, a reset named after the bounded no-reset hold."""
    if not meter_num(resets):
        return False
    if paused.get("window_resets") is None:
        return t > paused["since"]
    return resets > paused["window_resets"] + METER_SAME_WINDOW_S and t > paused["window_resets"]


def quota_states(state, row):
    """After a published reading, the two registered quota states (module doc): the five-hour recovery and the weekly
    study stop. Needs the ledger's limits; before a host has seeded them (check()), nothing is set."""
    limits = state.get("limits")
    if not limits:
        return
    t, host = row["t"], row.get("host")
    recovery = state["recovery"]
    u5 = row.get("five_hour")
    if recovery and meter_num(u5) and 0 <= u5 < RESUME_FIVE_HOUR \
            and later_window(row.get("five_hour_resets"), t, recovery):
        state["recovery"] = None                      # a fresh post-reset reading below 0.40: the study may resume
    five = state["meter"]["windows"].get("five_hour")
    if five and five["u"] >= limits["five_hour"]:
        recovery = state["recovery"]
        if not recovery or recovery.get("window_resets") != five["resets"]:
            state["recovery"] = {"window_resets": five["resets"], "since": t, "u": five["u"], "host": host}
    week = state["meter"]["windows"].get("seven_day")
    if week and week["u"] >= limits["seven_day"] and not state["study_stop"] and not released(state, week):
        state["study_stop"] = {"t": t, "u": week["u"], "limit": limits["seven_day"], "window_resets": week["resets"],
                               "host": host}


def released(state, week):
    """Whether the weekly study stop has been released for this seven-day window already."""
    return any(r.get("window_resets") == week["resets"] for r in state["study_releases"])


def release_study_stop(state, payload, now):
    """The operator's release of a latched weekly stop (module doc), recorded with its reason. Refused when nothing is
    latched or no reason is given."""
    reason = str(payload.get("reason") or "").strip()
    if not state["study_stop"]:
        raise ValueError("no weekly study stop is latched")
    if not reason:
        raise ValueError("a release needs its reason (Ephraiem's go, and when)")
    state["study_releases"].append(dict(state["study_stop"], released=round(now, 1), reason=reason[:300]))
    state["study_stop"] = None


def reserve(state, payload):
    """Count one charged request against payload["stage"]'s cap (payload["cap"]), atomically, since the caller holds
    the ledger's exclusive lock: True when it is granted, False at the cap (nothing counted)."""
    name, cap = str(payload["stage"]), payload["cap"]
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
        raise ValueError(f"a request cap must be a nonnegative integer, not {cap!r}")
    entry = state["stages"].setdefault(name, {"charged_requests": 0})
    if entry["charged_requests"] >= cap:
        return False
    entry["charged_requests"] += 1
    return True


def remote_main(argv):
    """Entry point on the home, when a host runs this file there as `python3 - <op> <base64 json payload>`."""
    op, blob = argv
    payload = json.loads(base64.b64decode(blob))
    try:
        print(json.dumps({"state": apply(payload["home_dir"], op, payload)}))
        return 0
    except (OSError, ValueError, KeyError) as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        return 2


# ---- reaching the ledger from any host ------------------------------------------------------------------------------
def call(conf, op, payload=None):
    """One operation on the account's ledger, wherever its home is: in this process when conf["home"] is "local",
    else over ssh to conf["home"] (user@host), running this file there. Raises Unreachable on any failure."""
    payload = dict(payload or {}, home_dir=conf["home_dir"])
    if conf["home"] == "local":
        try:
            return apply(conf["home_dir"], op, payload)
        except (OSError, ValueError) as e:
            raise Unreachable(f"local ledger at {conf['home_dir']}: {e}") from None
    opts = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={min(10, int(conf['ssh_timeout']))}"]
    if conf.get("control_dir"):
        opts += ["-o", "ControlMaster=auto", "-o", f"ControlPath={conf['control_dir']}/cm-%C",
                 "-o", "ControlPersist=120"]
    blob = base64.b64encode(json.dumps(payload).encode()).decode()
    try:
        p = subprocess.run([conf.get("ssh") or "ssh", *opts, conf["home"], "python3", "-", op, blob],
                           input=bundle(), capture_output=True, text=True, timeout=conf["ssh_timeout"])
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Unreachable(f"ledger home {conf['home']}: {e}") from None
    try:
        answer = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        from redact import Redactor                 # never shipped: call() runs only on the calling host
        tail = Redactor().clip(p.stderr.strip(), 200, tail=True)
        raise Unreachable(f"ledger home {conf['home']} exited {p.returncode}: {tail}") from None
    if "error" in answer:
        raise Unreachable(f"ledger home {conf['home']}: {answer['error']}")
    return answer["state"]


def bundle():
    """The program a remote home runs: meter.py, then this file, marked as shipped so it uses the prepended meter."""
    return ("_PROBATIO_SHIPPED = True\n" + (SOURCE.parent / "meter.py").read_text(encoding="utf-8") + "\n"
            + SOURCE.read_text(encoding="utf-8"))


def main(argv=None):
    if argv is None and len(sys.argv) == 3 and sys.argv[1] in OPERATIONS:
        return remote_main(sys.argv[1:])           # run on the home by call(), over ssh
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--home-dir", required=True, help="the ledger directory on this host (its home)")
    p.add_argument("--show", action="store_true", help="print the ledger's state")
    p.add_argument("--set-limits", nargs=2, type=float, metavar=("FIVE_HOUR", "SEVEN_DAY"),
                   help="replace the account's pause limits (every host follows at its next read)")
    p.add_argument("--release-study-stop", metavar="REASON",
                   help="release the latched weekly study stop after Ephraiem's go, recording why")
    a = p.parse_args(argv)
    if a.set_limits:
        state = apply(a.home_dir, "set-limits", {"limits": {"five_hour": a.set_limits[0],
                                                            "seven_day": a.set_limits[1]}})
    elif a.release_study_stop is not None:
        try:
            state = apply(a.home_dir, "release-study-stop", {"reason": a.release_study_stop})
        except ValueError as e:
            p.error(str(e))
    else:
        state = apply(a.home_dir, "read", {})
    print(json.dumps(state, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
