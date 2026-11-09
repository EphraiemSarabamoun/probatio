"""The account's quota meter: what the calibration knows about the subscription's two usage windows, built from every
reading any host has seen, and the holds that follow from it.

Kept apart from quota.py because account.py ships this file, with itself, to the ledger's home host over ssh: it
imports only the standard library and every top-level name starts with meter_ or METER_, so nothing clashes there.

The state keeps each window (five_hour, seven_day) separately as {"u", "resets", "t", "host"}. A reading changes a
window only by these rules, so a partial, stale or reset-less reading can never clear a known hold:
  * a reading of a later window (its reset is more than METER_SAME_WINDOW_S after the known one, or it was taken after
    the known window ended) replaces the window: the only way a known high meter falls;
  * a reading of the same window raises the high-water mark and never lowers it, and fills in an unknown reset;
  * a reading of an earlier window (an older reset) changes nothing;
  * a reading that leaves a window out changes nothing for that window.
A reading without both windows' utilization and reset time is unusable telemetry: it still merges what it has, and it
marks the meter unknown, which holds for METER_UNKNOWN_S; METER_UNKNOWN_STOP unknown readings in a row are a hard stop.
A number is a number only when it is finite and no larger in size than METER_NUM_MAX: NaN, the infinities and an
integer too large for a float (10**1000 parses as JSON) count as missing wherever they appear (a utilization, a reset,
a reading's time, a rejection's reset, or a state merged from the ledger), so a malformed value can never stand in for
a reset that has already passed or a window that has none, and never raises: it makes the reading unusable telemetry,
which holds like any other.
A usable reading clears the unknown mark. A server rejection holds until its own reset time.

meter_hold() turns the state into a pause. A window at or over its limit holds until its reset time plus
METER_MARGIN_S, or, with no reset time, for METER_HIGH_NO_RESET_S after it was last seen, after which one call may
probe and its reading decides again.

The burn (CALIBRATION.md section 7's "summed positive increments", in points: utilization x 100) is kept apart from
that pause protection, because a high-water mark cannot see a fall and a rise again within one window. It is the sum,
over every accepted reading in time order, of each reading's positive increment over the one before it: the rise
within one window, a different window's whole reading (counted from zero, since the window restarted), nothing for the
first reading ever seen (no known start), and nothing for a fall. meter_burn_add places each reading by its time among
the last METER_BURN_HISTORY accepted readings of its window, so a reading that arrives late (concurrent calls publish
in any order) still adds exactly what its place in the sequence adds; an exact duplicate adds nothing. A reading too
late to place among them is bounded from above, by counting it as rising from zero and adding its step to the reading
after it, so the burn never undercounts. The account ledger keeps it (account.py), and a stage's burn is the ledger's
sum now less its sum when the stage opened: every seat's use in that time, an upper bound on the stage's own.
"""
import math

METER_WINDOWS = ("five_hour", "seven_day")
METER_SAME_WINDOW_S = 300       # two reset times this close name the same window
METER_MARGIN_S = 60             # resume a minute after a reset, so the first call lands in the new window
METER_HIGH_NO_RESET_S = 900     # a high meter with no reset time: hold this long after it was seen, then probe
METER_UNKNOWN_S = 300           # unusable telemetry: hold this long after it, then probe
METER_UNKNOWN_STOP = 3          # this many unusable readings in a row stop every run until a person looks
METER_REJECTED_BACKOFF_S = 900  # a rejection with no reset time holds this long
METER_BURN_HISTORY = 64         # accepted readings kept per window, to place a late reading in time order
METER_NUM_MAX = 1e11            # no utilization or epoch second comes near this (2026 is 1.8e9 s); a larger value is
                                # malformed telemetry, read as missing


def meter_empty():
    return {"windows": {}, "rejected": None, "unknown": None, "valid_t": None}


def meter_num(x):
    """Whether x is a usable real number: not a boolean, finite, and no larger in size than METER_NUM_MAX. Total over
    every JSON value: an integer too large for a float is simply not a number here, never an OverflowError."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    try:
        return math.isfinite(x) and abs(x) <= METER_NUM_MAX
    except OverflowError:
        return False


def meter_valid(row):
    """Whether a reading is usable telemetry: both windows with a utilization between 0 and 2 and a reset time."""
    if not isinstance(row, dict) or row.get("status") in ("unverified", "rejected") or not meter_num(row.get("t")):
        return False
    return all(meter_num(row.get(w)) and 0 <= row[w] <= 2 and meter_num(row.get(f"{w}_resets"))
               for w in METER_WINDOWS)


def meter_merge_window(windows, w, u, resets, t, host):
    """Fold one window observation into windows[w] by the module's rules (in place). An observation without a finite
    utilization in [0, 2] and a finite time changes nothing; a non-finite reset counts as none."""
    if not (meter_num(u) and 0 <= u <= 2 and meter_num(t)):
        return
    resets = resets if meter_num(resets) else None
    cur = windows.get(w)
    if cur is None:
        windows[w] = {"u": u, "resets": resets, "t": t, "host": host}
        return
    known = cur["resets"]
    later = known is not None and ((resets is not None and resets > known + METER_SAME_WINDOW_S) or t > known)
    # a reset-less high reading past its bounded recovery gives way to the next reading that names its reset
    later = later or (known is None and resets is not None and t > cur["t"] + METER_HIGH_NO_RESET_S)
    earlier = known is not None and resets is not None and resets < known - METER_SAME_WINDOW_S
    if later:
        windows[w] = {"u": u, "resets": resets, "t": t, "host": host}
    elif not earlier:
        if u > cur["u"]:
            cur["u"], cur["host"] = u, host
        if known is None and resets is not None:
            cur["resets"] = resets
        cur["t"] = max(cur["t"], t)


def meter_observe(state, row):
    """The state after one reading (a new dict; the old one is left alone)."""
    new = meter_clean(state)
    t = row.get("t") if isinstance(row, dict) else None
    if not meter_num(t):
        return new
    host = row.get("host")
    for w in METER_WINDOWS:
        if meter_num(row.get(w)) and 0 <= row[w] <= 2:
            resets = row.get(f"{w}_resets")
            meter_merge_window(new["windows"], w, row[w], resets if meter_num(resets) else None, t, host)
    if row.get("status") == "rejected":
        until = row["resets"] if meter_num(row.get("resets")) else t + METER_REJECTED_BACKOFF_S
        if not new["rejected"] or until > new["rejected"]["until"]:
            new["rejected"] = {"until": until, "t": t, "host": host}
    elif meter_valid(row):
        new["valid_t"] = max(t, new["valid_t"] or t)
        if new["unknown"] and new["unknown"]["t"] <= t:
            new["unknown"] = None
    elif not new["valid_t"] or t >= new["valid_t"]:
        streak = (new["unknown"] or {}).get("streak", 0) + 1
        new["unknown"] = {"t": max(t, (new["unknown"] or {}).get("t", t)), "streak": streak, "host": host,
                          "why": str(row.get("why") or "no usable quota telemetry")[:300]}
    return new


def meter_clean(state):
    """A copy of a state from elsewhere (the ledger, a cache) with every non-finite or malformed entry dropped: a
    window without a finite utilization and time, a rejection or unknown mark without a finite time, a non-finite
    valid_t. A dropped window is simply not known here; the reading that made it is still judged by its own host."""
    state = state if isinstance(state, dict) else {}
    out = meter_empty()
    for w, s in (state.get("windows") or {}).items():
        if w in METER_WINDOWS and isinstance(s, dict):
            meter_merge_window(out["windows"], w, s.get("u"), s.get("resets"), s.get("t"), s.get("host"))
    rej = state.get("rejected")
    if isinstance(rej, dict) and meter_num(rej.get("until")) and meter_num(rej.get("t")):
        out["rejected"] = {"until": rej["until"], "t": rej["t"], "host": rej.get("host")}
    unk = state.get("unknown")
    if isinstance(unk, dict) and meter_num(unk.get("t")) and meter_num(unk.get("streak")):
        out["unknown"] = {"t": unk["t"], "streak": unk["streak"], "host": unk.get("host"),
                          "why": str(unk.get("why") or "no usable quota telemetry")[:300]}
    out["valid_t"] = state.get("valid_t") if meter_num(state.get("valid_t")) else None
    return out


def meter_combine(a, b):
    """One state holding what either state knows (the ledger's and this host's own)."""
    out = meter_clean(a)
    b = meter_clean(b)
    for w, s in b["windows"].items():
        meter_merge_window(out["windows"], w, s["u"], s["resets"], s["t"], s.get("host"))
    if b["rejected"] and (not out["rejected"] or b["rejected"]["until"] > out["rejected"]["until"]):
        out["rejected"] = dict(b["rejected"])
    if b["valid_t"] and (not out["valid_t"] or b["valid_t"] > out["valid_t"]):
        out["valid_t"] = b["valid_t"]
    unknowns = [u for u in (out["unknown"], b["unknown"]) if u]
    unknown = max(unknowns, key=lambda u: (u["t"], u["streak"])) if unknowns else None
    out["unknown"] = unknown if unknown and (not out["valid_t"] or unknown["t"] >= out["valid_t"]) else None
    return out


def meter_same(a, b):
    """Whether two states say the same about the account (observation times aside)."""
    def key(s):
        s = s or meter_empty()
        return ({w: (v["u"], v["resets"]) for w, v in s["windows"].items()},
                (s["rejected"] or {}).get("until"), (s["unknown"] or {}).get("streak"))
    return key(a) == key(b)


def meter_hold(state, now, limits):
    """(until, why, stop, host): until is the epoch before which no call may start (None: may start now); stop asks for
    a hard stop; host is whose reading made the longest hold."""
    state = state or meter_empty()
    found = []
    for w in METER_WINDOWS:
        s = state["windows"].get(w)
        if s and s["u"] >= limits[w]:
            if s["resets"] is not None:
                end, why = s["resets"] + METER_MARGIN_S, f"{w} {s['u']:.2f} >= {limits[w]:.2f}"
            else:
                end = s["t"] + METER_HIGH_NO_RESET_S
                why = f"{w} {s['u']:.2f} >= {limits[w]:.2f} with no reset time (one probe after the hold)"
            if end > now:
                found.append((end, why, s.get("host")))
    rej = state["rejected"]
    if rej and rej["until"] + METER_MARGIN_S > now:
        found.append((rej["until"] + METER_MARGIN_S, "limit reached", rej.get("host")))
    unk = state["unknown"]
    stop = bool(unk and unk["streak"] >= METER_UNKNOWN_STOP)
    if unk and unk["t"] + METER_UNKNOWN_S > now:
        found.append((unk["t"] + METER_UNKNOWN_S, f"quota telemetry unknown ({unk['why']}; {unk['streak']} in a row)",
                      unk.get("host")))
    if not found:
        return None, None, stop, None
    end, _, host = max(found, key=lambda f: f[0])
    return end, "; ".join(why for _, why, _ in found), stop, host


# ---- the burn: CALIBRATION 7's summed positive increments, apart from the high-water pause protection ---------------
def meter_burn_empty():
    """A burn with nothing counted: per window, the total in points, the recent accepted readings in time order, and
    whether older readings have been dropped from them."""
    return {"total": {w: 0.0 for w in METER_WINDOWS}, "readings": {w: [] for w in METER_WINDOWS},
            "dropped": {w: False for w in METER_WINDOWS}}


def meter_burn_clean(burn):
    """A copy of a burn from elsewhere (the ledger) with every malformed entry dropped; a ledger from before the burn
    was kept per reading (a bare {window: total}) keeps its totals."""
    burn = burn if isinstance(burn, dict) else {}
    out = meter_burn_empty()
    totals = burn.get("total") if isinstance(burn.get("total"), dict) else burn
    for w in METER_WINDOWS:
        out["total"][w] = totals.get(w) if meter_num(totals.get(w)) and totals.get(w) >= 0 else 0.0
        kept = (burn.get("readings") or {}).get(w) if isinstance(burn.get("readings"), dict) else None
        out["readings"][w] = [r for r in (kept or []) if isinstance(r, dict) and meter_num(r.get("t"))
                              and meter_num(r.get("u")) and meter_num(r.get("resets"))]
        out["dropped"][w] = bool((burn.get("dropped") or {}).get(w)) if isinstance(burn.get("dropped"), dict) else False
    return out


def meter_step(before, after):
    """The positive increment, in points, from one accepted reading to the next in time: the rise within one window,
    the whole of a different window's reading, and nothing from no known start or for a fall."""
    if before is None:
        return 0.0
    if abs(after["resets"] - before["resets"]) <= METER_SAME_WINDOW_S:
        return max(0.0, 100 * (after["u"] - before["u"]))
    return 100 * after["u"]


def meter_burn_add(burn, row):
    """The burn after one reading (a new dict): each window whose utilization and reset the reading gives is placed by
    its time among that window's recent accepted readings, and the total grows by what the placement adds to the sum of
    positive increments (never less than nothing, since a reading placed between two others can only add a rise). An
    exact duplicate adds nothing. A reading older than every kept one, after older ones were dropped, has an unknown
    predecessor: it is counted as rising from zero, which bounds what it can add."""
    new = meter_burn_clean(burn)
    t = row.get("t") if isinstance(row, dict) else None
    if not meter_num(t):
        return new
    for w in METER_WINDOWS:
        u, resets = row.get(w), row.get(f"{w}_resets")
        if not (meter_num(u) and 0 <= u <= 2 and meter_num(resets)):
            continue
        reading = {"t": t, "u": u, "resets": resets, "host": row.get("host")}
        kept = new["readings"][w]
        if reading in kept:
            continue
        at = sum(1 for r in kept if r["t"] <= t)            # after every reading at or before its time
        before = kept[at - 1] if at else None
        after = kept[at] if at < len(kept) else None
        if before is None and new["dropped"][w]:            # too late to place: bound it from above
            added = 100 * u + (meter_step(reading, after) if after else 0.0)
        else:
            added = meter_step(before, reading) + (meter_step(reading, after) - meter_step(before, after)
                                                    if after else 0.0)
        new["total"][w] = round(new["total"][w] + max(0.0, added), 6)
        kept.insert(at, reading)
        if len(kept) > METER_BURN_HISTORY:
            del kept[0]
            new["dropped"][w] = True
    return new
