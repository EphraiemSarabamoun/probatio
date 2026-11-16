"""The subscription quota rules of the calibration harness (CALIBRATION.md section 7), on top of meter.py.

Claude Code's stream-json output carries, with every model call, a rate_limit_event whose rate_limit_info gives the
subscription's five-hour and seven-day utilization (0 to 1, in steps of 0.01) and when each window resets (epoch
seconds). Every host's shim (shim.py) turns each one into a reading here, appends it to its quota.jsonl and publishes
it to the account's shared ledger (account.py). The meter (meter.py) folds readings into per-window state, so a
partial, stale or reset-less reading never clears a hold, and the scheduler (scheduler.py) asks it before every call:
no new call starts while either window is at or above its limit, while the server says the limit is reached, or while
the telemetry is unknown. Everything here is a pure function of readings, the limits and the clock, apart from
append() and latest(), so the rules are tested without a model.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import meter  # noqa: E402

# The default limits leave the account's owner daily-use headroom, since his own seats draw on the same subscription.
# Measured 2026-11-07: a heavy day of his own work used about 0.07 of the seven-day window, so a calibration that stops
# at 0.60 leaves him 0.40, about five more heavy days, before the weekly cap could bind; the five-hour window resets
# every five hours, and stopping at 0.60 still leaves 0.40 of it for a burst of interactive work. local.json's quota
# block sets them per account (they live in the account ledger, so every host uses the same pair).
DEFAULT_LIMITS = {"five_hour": 0.60, "seven_day": 0.60}
WINDOWS = meter.METER_WINDOWS
RESUME_MARGIN_S = meter.METER_MARGIN_S
REJECTED_BACKOFF_S = meter.METER_REJECTED_BACKOFF_S
TAIL_BYTES = 262144             # the local fold reads only the end of quota.jsonl; the ledger keeps the full state


def reading(info, now, run_id=None):
    """One quota.jsonl line from a rate_limit_event's rate_limit_info: the time it was seen, the run that saw it, the
    server's status, each window's utilization and reset time (None where the event leaves one out), and whether it is
    usable telemetry (both windows complete)."""
    info = info if isinstance(info, dict) else {}
    windows = info.get("unifiedWindows") if isinstance(info.get("unifiedWindows"), dict) else {}

    def field(window, key):
        value = (windows.get(window) or {}).get(key) if isinstance(windows.get(window), dict) else None
        return value if meter.meter_num(value) else None

    row = {"t": round(now, 1), "run_id": run_id, "status": info.get("status"), "type": info.get("rateLimitType"),
           "resets": info.get("resetsAt") if meter.meter_num(info.get("resetsAt")) else None,
           "five_hour": field("five_hour", "utilization"), "five_hour_resets": field("five_hour", "resetsAt"),
           "seven_day": field("seven_day", "utilization"), "seven_day_resets": field("seven_day", "resetsAt")}
    row["valid"] = meter.meter_valid(row)
    return row


def rejected(now, run_id=None, resets=None):
    """A reading for a call refused by the server's limit when no rate_limit_event said so (a 429 or a usage-limit
    message in the result): the limit counts as reached until resets, or for REJECTED_BACKOFF_S."""
    return {"t": round(now, 1), "run_id": run_id, "status": "rejected", "type": None,
            "resets": resets if resets else round(now + REJECTED_BACKOFF_S, 1), "valid": False,
            "five_hour": None, "five_hour_resets": None, "seven_day": None, "seven_day_resets": None}


def unverified(now, run_id, why):
    """A marker for a call whose telemetry or protocol could not be verified: the meter treats it as unknown and holds
    (bounded), and too many in a row stop the runs."""
    return {"t": round(now, 1), "run_id": run_id, "status": "unverified", "why": why, "valid": False}


def limit_error(result):
    """Whether a Claude Code result event failed on the subscription's rate or usage limit."""
    if not result or not result.get("is_error"):
        return False
    text = str(result.get("result") or "").lower()
    return result.get("api_error_status") == 429 or any(s in text for s in ("rate limit", "usage limit",
                                                                              "hit your limit", "session limit"))


def check_limits(limits):
    """The two limits as floats in (0, 1], or ValueError: the one validation every config path uses."""
    out = {}
    for window in WINDOWS:
        value = (limits or {}).get(window, DEFAULT_LIMITS[window])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1:
            raise ValueError(f"quota limit {window} must be a number in (0, 1], not {value!r}")
        out[window] = float(value)
    return out


def newest(*rows):
    """The reading with the largest t among those given (None ignored), the later one on a tie, or None."""
    best = None
    for r in rows:
        if r and meter.meter_num(r.get("t")) and (best is None or r["t"] >= best["t"]):
            best = r
    return best


def fold(rows, state=None):
    """The meter state after the given readings, in order."""
    for row in rows:
        state = meter.meter_observe(state, row)
    return state or meter.meter_empty()


def pause_until(row, now, limits=None):
    """(epoch, reason) before which no new call may start after this one reading, or (None, None)."""
    until, why, _, _ = meter.meter_hold(fold([row] if row else []), now, check_limits(limits))
    return until, why


def append(path, row):
    """Add one reading to quota.jsonl. One write of one short line to a file opened for appending, so readings from
    calls running at once never interleave."""
    data = (json.dumps(row, sort_keys=True) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def rows(path):
    """The readings in the end of quota.jsonl, in file order; a line cut by the tail or half-written is skipped."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            tail = f.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return []
    out = []
    for line in tail.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and meter.meter_num(row.get("t")):          # NaN and Infinity parse as JSON
            out.append(row)
    return out


def latest(path):
    """The newest reading in quota.jsonl (largest t), or None."""
    return newest(*rows(path))


def pause_state(path, now=None, limits=None):
    """The hold from every reading in one quota.jsonl, folded through the meter (this host's alone; scheduler.py
    decides for the account)."""
    now = time.time() if now is None else now
    until, why, _, _ = meter.meter_hold(fold(rows(path)), now, check_limits(limits))
    return until, why


def when(epoch):
    """An epoch time as the harness prints it: UTC, to the second."""
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(epoch))
