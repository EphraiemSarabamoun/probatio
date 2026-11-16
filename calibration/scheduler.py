"""The account-level quota scheduler that every calibration call (shim.py) and every run (run.py) on every host asks.

The calibration's hosts draw on one subscription, so pause and resume are one decision for the account. pause() makes
it from the account's quota meter (meter.py): the ledger's (account.py, on the home host, where every host publishes)
combined with this host's own readings folded from its quota.jsonl, so a reading this host has seen but not yet
published still counts. The meter keeps each window apart, so no partial, stale or reset-less reading clears a hold,
and unusable telemetry holds too; enough of it in a row asks for a hard stop. The limits are the ledger's, so every
host uses one pair; local.json's quota block seeds them when the ledger has none.

Two registered rules (CALIBRATION.md 7) live in the ledger's state and are enforced here, apart from the meter's
holds:
  * after a five-hour pause, the study resumes only on a fresh post-reset reading below 0.40 (account.RESUME_FIVE_HOUR):
    while the ledger's recovery mark stands, a later window's reading at or above 0.40 holds until that window resets;
  * when the seven-day window reaches its limit, the study stops: pause() asks for a hard stop (callers write STOP) as
    soon as this host sees it, and the ledger latches it, so no reset, resume or new run lifts it. Only an operator's
    release after Ephraiem's go does (account.py --release-study-stop), and check() refuses new runs until then.

A non-home host reaches the ledger over ssh. A local cache of the ledger's state is reused for cache_seconds for
reads. Every reading is published, because the stage burn (meter.py) sums each reading's increment in time order, so
a reading the meter's high-water mark would ignore can still count.

If the home cannot be reached, the scheduler decides from this host's readings and the last cached ledger, and reports
itself degraded. That stays sound, because every call returns a fresh reading of the whole account: a host that loses
the ledger still pauses on the account's state, only after its own next call instead of after another host's. publish()
always writes the reading to quota.jsonl before it tries the ledger, so nothing is lost while the home is away, and the
next successful publish brings the ledger up to date.
"""
import json
import os
import socket
import time
from pathlib import Path

import account
import meter
import quota


def conf_from(cfg, root):
    """The scheduler's settings for one host, from local.json's quota block (already merged with the defaults) and the
    host's root: the one place the block's keys become paths. Holds no secret; isolate.py writes it into room.json."""
    q = cfg["quota"]
    home_dir = q.get("home_dir") or (str(Path(root) / "account") if q["home"] == "local" else None)
    if not home_dir:
        raise ValueError("quota.home_dir must name the ledger's directory on its home host")
    return {"home": q["home"], "home_dir": home_dir,
            "host": q.get("host") or socket.gethostname().split(".")[0],
            "limits": quota.check_limits({"five_hour": q["five_hour"], "seven_day": q["seven_day"]}),
            "log_path": str(Path(root) / "quota.jsonl"), "cache_path": str(Path(root) / "account.cache.json"),
            "control_dir": str(Path(root)), "cache_seconds": q["cache_seconds"], "ssh_timeout": q["ssh_timeout"],
            "ssh": q.get("ssh") or "ssh"}


class Scheduler:
    """One host's view of the account's quota (see the module doc). conf is conf_from()'s dict; clock is for tests."""

    def __init__(self, conf, clock=time.time):
        self.conf, self.clock = conf, clock

    # ---- the cached ledger --------------------------------------------------------------------------------------------
    def _read_cache(self):
        try:
            return json.loads(Path(self.conf["cache_path"]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_cache(self, state, now):
        path = Path(self.conf["cache_path"])
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"fetched": now, "state": state}), encoding="utf-8")
        os.replace(tmp, path)

    def _fresh(self, cache, now):
        return bool(cache) and 0 <= now - cache["fetched"] <= self.conf["cache_seconds"]

    def ledger(self, now=None, fresh=False):
        """(the ledger's state or None, degraded): the cache when it is fresh (and fresh=False), else the ledger,
        else (degraded) whatever cache there is."""
        now = self.clock() if now is None else now
        cache = self._read_cache()
        if not fresh and self._fresh(cache, now):
            return cache["state"], False
        try:
            state = account.call(self.conf, "read")
        except account.Unreachable:
            return (cache or {}).get("state"), True
        self._write_cache(state, now)
        return state, False

    # ---- the two questions --------------------------------------------------------------------------------------------
    def limits(self, state):
        """The ledger's limits when it has them (one pair for the account), else this host's configured pair."""
        return quota.check_limits(state["limits"]) if state and state.get("limits") else self.conf["limits"]

    def pause(self, now=None, fresh=False, snapshot=None):
        """(until, why, info): until is the epoch before which no call may start, or None. info says whose reading made
        the hold, with which limits, whether the ledger was out of reach (degraded), whether the study is recovering
        from a five-hour pause, and whether a hard stop is asked for (the weekly study stop, or too much unusable
        telemetry in a row). The decision is made from one ledger snapshot: snapshot, a (state, degraded) pair the
        caller already read (so its other decisions use the same read), else ledger(now, fresh), where fresh skips the
        cache (a room whose policy requires the ledger reads it afresh before every call)."""
        now = self.clock() if now is None else now
        state, degraded = snapshot if snapshot is not None else self.ledger(now, fresh=fresh)
        combined = meter.meter_combine((state or {}).get("meter"), quota.fold(quota.rows(self.conf["log_path"])))
        limits = self.limits(state)
        until, why, stop, host = meter.meter_hold(combined, now, limits)
        whys = [why] if why else []
        recovering = recovery_hold(state, combined, now)
        if recovering and recovering[0] > now:
            whys.append(recovering[1])
            if until is None or recovering[0] > until:
                until, host = recovering[0], recovering[2]
        weekly = study_stop(state, combined, limits)
        if weekly:
            stop = True
            whys.append(weekly)
        return until, "; ".join(whys) or None, {"degraded": degraded, "limits": limits, "stop": stop,
                                               "recovering": bool((state or {}).get("recovery")),
                                               "decided_by": host if until else None}

    def publish(self, row, now=None):
        """Record one reading (or an unverified-call marker): in this host's quota.jsonl first, then in the ledger,
        always (the module doc says why). Returns True when the ledger could not be reached."""
        now = self.clock() if now is None else now
        row = dict(row, host=self.conf["host"])
        quota.append(self.conf["log_path"], row)
        try:
            state = account.call(self.conf, "publish", {"reading": row, "host": self.conf["host"]})
        except account.Unreachable:
            return True
        self._write_cache(state, now)
        return False

    def check(self):
        """What would stop the account from being scheduled as one (empty when nothing would): an unreachable ledger,
        or a ledger whose limits differ from this host's local.json. Seeds the ledger's limits when it has none."""
        try:
            state = account.call(self.conf, "init", {"limits": self.conf["limits"]})
        except account.Unreachable as e:
            return [f"the quota ledger cannot be reached ({e}); every host must reach one ledger to share the account"]
        self._write_cache(state, self.clock())
        found = []
        stored = quota.check_limits(state["limits"])
        if stored != self.conf["limits"]:
            found.append(f"the quota ledger's limits are {stored}, this host's local.json says {self.conf['limits']}; "
                         "keep one pair (account.py --set-limits changes the ledger's)")
        if state.get("study_stop"):
            found.append(study_stop(state, None, stored) + f"; after his go, release it on the ledger's home with "
                         f"account.py --home-dir {self.conf['home_dir']} --release-study-stop REASON")
        return found


def recovery_hold(state, combined, now):
    """(until, why, host) while the study recovers from a five-hour pause (the ledger's recovery mark) and the latest
    five-hour reading is of a later window at or above account.RESUME_FIVE_HOUR; else None. While the paused window
    itself is current, the meter's own hold covers it; once it has reset, calls may probe, and the first later-window
    reading decides: below 0.40 the ledger clears the mark, at 0.40 or above this holds until that window resets."""
    recovery = (state or {}).get("recovery")
    five = (combined or {}).get("windows", {}).get("five_hour")
    if not recovery or not five or five["u"] < account.RESUME_FIVE_HOUR:
        return None
    if not account.later_window(five["resets"], five["t"], recovery):
        return None
    end = (five["resets"] + meter.METER_MARGIN_S if five["resets"] is not None
           else five["t"] + meter.METER_HIGH_NO_RESET_S)
    return end, (f"five_hour {five['u']:.2f} >= {account.RESUME_FIVE_HOUR:.2f} after a five-hour pause (the study "
                 "resumes only on a post-reset reading below it)"), five.get("host")


def study_stop(state, combined, limits):
    """Why the study is stopped at the weekly threshold, or None: the ledger's latched stop, or the seven-day window
    seen here at or above its limit and not yet released for that window (the ledger latches it at its next publish).
    It never lapses on its own: only an operator's release does (account.py --release-study-stop)."""
    latched = (state or {}).get("study_stop")
    if latched:
        return (f"the study is stopped at the weekly threshold: seven_day reached {latched['u']:.2f} >= "
                f"{latched['limit']:.2f} at {quota.when(latched['t'])}; it waits for Ephraiem's go")
    week = (combined or {}).get("windows", {}).get("seven_day")
    releases = (state or {}).get("study_releases") or []
    if week and week["u"] >= limits["seven_day"] and not any(r.get("window_resets") == week["resets"]
                                                             for r in releases):
        return (f"the study is stopped at the weekly threshold: seven_day {week['u']:.2f} >= "
                f"{limits['seven_day']:.2f}; it waits for Ephraiem's go")
    return None
