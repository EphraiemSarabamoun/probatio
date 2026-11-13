#!/usr/bin/env python3
"""Admit versus by class's second readings into its source, as tools/versus_admit.py admits versus's: one review record
per Class I template under bank/versus-classes/src/reviews/<template id>.json, format probatio-versus-review-3, which
tools/build_versus_classes.py's registered gate reads.

Standard library only, Python 3.9 or later, no network, no model. Everything it writes is derived from its inputs, so a
second run on the same inputs changes nothing (--check says whether a run would change a file).

    python3 tools/versus_classes_admit.py --readings R1 R2 ... --packets P1 P2 ... [--changes FIXES.json]
                                          [--report OUT.json] [--check]

Inputs (paths given on the command line; none is written into a record):
  --readings DIR...  the readers' folders, OLDEST FIRST, each holding reading.json: a list with one entry per template
                     read (id, set, points "1" to "n" each {verdict, why}, overall, fix, quotes, type_argument, and the
                     hash read: render_sha256, rendering_hash or packet_render_sha256, and/or sheet_sha256).
  --packets DIR...   the packets the readers read: sheets/<id>.txt, each sheet's head carrying "rendering hash
                     (unfamiliar roster, the six assignments in order): <sha256>" and the six complete renderings.
  --changes FILE     a list of {id, ruling, before, after}: what changed in a template after a reading, and under which
                     ruling (recorded with the earlier reading it answers).

The proof, per template (prove): today's rendering hash (build_versus_classes.render_hash, recomputed from the source),
the hash at the head of a sheet of that id, and the hash recomputed from that sheet's own six renderings must be the
same; the deciding reading must have read that hash (confirmed_hash). Then the gate (decide): of the readings whose
confirmed hash is today's, the newest decides, and it admits the template only when its overall verdict is "pass" and
no point failed. If any template is not admitted, nothing is written and every reason is printed: a template whose
hash does not match is never admitted.

The record (record): the template's id, list (component) and set, today's render_sha256 and its rule; the first reader
(the template's author, vendor anthropic); the second reader named by vendor alone, "an independent reader, vendor
openai", never a reader's own label, a person or a path, with the verdict, the hash read, the per-point verdicts and
reasons, the quotes, the fix and the type argument; and, where an earlier reading failed or was fixed, that reading as
history (earlier_second_readings: its verdict, the hash it read, its failed points, its fix, and what changed after
it and under which ruling, from --changes).
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import build_versus_bank as BV  # noqa: E402  (read-only reuse: the reader gate's vendors and passing verdicts)
import build_versus_classes as BC  # noqa: E402
import versus_classes as VC  # noqa: E402

REVIEW_FORMAT = BC.REVIEW_FORMAT
FIRST_VENDOR, SECOND_VENDOR = "anthropic", "openai"
FIRST_READER = "the author, vendor {vendor}"
SECOND_READER = "an independent reader, vendor {vendor}"
SHEET_HASH = re.compile(r"^rendering hash \(unfamiliar roster, the six assignments in order\): ([0-9a-f]{64})$", re.M)
RENDERING_HEAD = re.compile(r"^\d of 6: unfamiliar roster")
READ_HASH_KEYS = ("render_sha256", "rendering_hash", "packet_render_sha256")     # where readers record the hash read
NOT_FAILED = ("pass", "not applicable", "awaiting the pilot", "recorded")        # a point verdict that is not a fail
KEPT = ("quotes", "fix", "type_argument")                       # the reading's own words a record keeps
ADMITTED = "admitted: an independent reader of another vendor passed the current rendering"


def sha256(text):
    """The sha256 hex digest of text as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---- the sheets ------------------------------------------------------------------------------------------------------
def sheet_renderings(text):
    """The six unfamiliar-roster user messages a sheet prints, in its order: each "complete rendering n of 6:
    unfamiliar roster" section's text after its "--- user message" line, up to the next section."""
    out = []
    for part in text.split("\n---- complete rendering ")[1:]:
        if RENDERING_HEAD.match(part):
            body = part.split("\n--- user message\n", 1)[1]
            out.append(body[:-1] if body.endswith("\n") else body)
    return out


def read_sheet(path):
    """One sheet as {sheet_sha256, head, recomputed}: the sha256 of the file, the hash its head shows, and the hash
    recomputed from its own six renderings by the class bank's rule (build_versus_classes.RENDER_SEPARATOR)."""
    text = Path(path).read_text(encoding="utf-8")
    head = SHEET_HASH.search(text)
    rendered = sheet_renderings(text)
    return {"sheet_sha256": sha256(text), "head": head.group(1) if head else None,
            "recomputed": sha256(BC.RENDER_SEPARATOR.join(rendered)) if len(rendered) == 6 else None}


def packet_sheets(packets):
    """{template id: [sheet]} of every packet's sheets/<id>.txt, in the order the packets are given."""
    out = {}
    for packet in packets:
        for f in sorted((Path(packet) / "sheets").glob("*.txt")):
            out.setdefault(f.stem, []).append(read_sheet(f))
    return out


# ---- the readings ----------------------------------------------------------------------------------------------------
def load_readings(folders):
    """{template id: [entry]} of every reading.json, oldest first (the folders' order, then the file's)."""
    out = {}
    for folder in folders:
        for entry in json.loads((Path(folder) / "reading.json").read_text(encoding="utf-8")):
            out.setdefault(entry["id"], []).append(entry)
    return out


def recorded_hash(entry):
    """The rendering hash a reading records it read, or None."""
    for k in READ_HASH_KEYS:
        if isinstance(entry.get(k), str) and re.fullmatch(r"[0-9a-f]{64}", entry[k]):
            return entry[k]
    return None


def confirmed_hash(entry, sheets):
    """The rendering hash a reading read, confirmed by a sheet of its template: the hash it records, when a sheet's
    head shows it (and, when it also records a sheet_sha256, that very sheet does); else the head of the sheet whose
    sha256 it records; else the one head every sheet of the template shows. None when no sheet confirms it."""
    mine, sheet_sha = recorded_hash(entry), entry.get("sheet_sha256")
    if mine:
        ok = [s for s in sheets if s["head"] == mine and (not sheet_sha or s["sheet_sha256"] == sheet_sha)]
        return mine if ok else None
    if sheet_sha:
        heads = {s["head"] for s in sheets if s["sheet_sha256"] == sheet_sha}
        return heads.pop() if len(heads) == 1 else None
    heads = {s["head"] for s in sheets}
    return heads.pop() if len(heads) == 1 else None


def failed_points(entry):
    """{point: why} of every point the reading did not pass."""
    return {k: p.get("why") for k, p in sorted((entry.get("points") or {}).items(), key=lambda kv: int(kv[0]))
            if p.get("verdict") not in NOT_FAILED}


def passed(entry):
    """Whether a reading passes its template: overall "pass" and no point failed."""
    return entry.get("overall") == "pass" and not failed_points(entry)


def vendor_of(entry, default):
    """The reading's vendor: openai for a Codex or GPT reader, else the folders' default; never a name."""
    who = str(entry.get("reviewer") or "")
    return SECOND_VENDOR if re.search(r"\b(Codex|GPT)", who) else default


# ---- the proof and the gate ------------------------------------------------------------------------------------------
def prove(current, sheets, decisive_hash):
    """The hash proof of one template: (ok, proof). ok when a sheet's head is today's hash, that sheet's own
    renderings recompute to it, and the deciding reading read it."""
    matching = [s for s in sheets if s["head"] == current]
    proof = {"today": current, "sheet_head": current if matching else None,
             "sheet_recomputed": matching[0]["recomputed"] if matching else None,
             "sheet_sha256": matching[0]["sheet_sha256"] if matching else None, "reading_read": decisive_hash}
    ok = bool(matching) and matching[0]["recomputed"] == current and decisive_hash == current
    return ok, proof


def decide(tid, current, entries, sheets):
    """(decisive reading or None, its confirmed hash, history, reasons): of the readings whose confirmed hash is
    today's, the newest decides; history is every other reading, oldest first."""
    confirmed = [(e, confirmed_hash(e, sheets)) for e in entries]
    of_current = [(e, h) for e, h in confirmed if h == current]
    if not of_current:
        read = sorted({str(h) for _, h in confirmed})
        return None, None, confirmed, [f"no reading of today's rendering {current[:12]} "
                                       f"(read: {', '.join(read) or 'none'})"]
    decisive, h = of_current[-1]
    history = [(e, x) for e, x in confirmed if e is not decisive]
    reasons = [] if passed(decisive) else [
        f"the reading of today's rendering does not pass: overall {decisive.get('overall')!r}, failed points "
        f"{sorted(failed_points(decisive))}"]
    return decisive, h, history, reasons


# ---- the record ------------------------------------------------------------------------------------------------------
def point_record(entry):
    """The reading's per-point verdicts and reasons."""
    return {k: {"verdict": p.get("verdict"), "why": p.get("why")}
            for k, p in sorted((entry.get("points") or {}).items(), key=lambda kv: int(kv[0]))}


def earlier_record(entry, read_hash, changes):
    """One earlier reading that failed or was fixed, as history: its verdict, the hash it read, its failed points,
    its fix, and what changed after it and under which ruling."""
    change = changes.get(entry["id"])
    return {"overall": entry.get("overall"), "render_sha256_read": read_hash, "failed_points": failed_points(entry),
            "fix": entry.get("fix"),
            "changed": ({"ruling": change.get("ruling"), "before": change.get("before"), "after": change.get("after")}
                        if change else None)}


def record(t, current, decisive, read_hash, history, changes, default_vendor):
    """A template's review record (format probatio-versus-review-3), the reader named by vendor alone."""
    vendor = vendor_of(decisive, default_vendor)
    second = {"reader": SECOND_READER.format(vendor=vendor), "vendor": vendor, "verdict": "pass",
              "render_sha256_read": read_hash, "overall": decisive.get("overall"), "points": point_record(decisive)}
    second.update({k: decisive.get(k) for k in KEPT})
    earlier = [earlier_record(e, h, changes) for e, h in history if not passed(e)]
    return {"format": REVIEW_FORMAT, "id": t["id"], "list": t["component"], "set": decisive.get("set"),
            "render_sha256": current, "render_rule": BC.RENDER_RULE, "status": ADMITTED,
            "first_reader": {"reader": FIRST_READER.format(vendor=FIRST_VENDOR), "vendor": FIRST_VENDOR},
            "second_reader": second, "earlier_second_readings": earlier}


def admit(src, folders, packets, changes, default_vendor=SECOND_VENDOR):
    """({template id: record}, report, problems) for every template of the class source."""
    templates, fixed, common, _ = BC.read_source(src)
    readings, sheets = load_readings(folders), packet_sheets(packets)
    records, report, problems = {}, {}, []
    for t in templates:
        current = BC.render_hash(t, fixed, common)
        mine = sheets.get(t["id"], [])
        decisive, read_hash, history, reasons = decide(t["id"], current, readings.get(t["id"], []), mine)
        ok, proof = prove(current, mine, read_hash)
        if not ok:
            reasons.append("the hash proof fails: " + json.dumps(proof))
        if decisive is not None and vendor_of(decisive, default_vendor) == FIRST_VENDOR:
            reasons.append("the deciding reader is of the first reader's vendor")
        report[t["id"]] = {"proof": proof, "admitted": not reasons, "reasons": reasons,
                           "earlier": [e.get("overall") for e, _ in history]}
        if reasons:
            problems += [f"{t['id']}: {r}" for r in reasons]
            continue
        records[t["id"]] = record(t, current, decisive, read_hash, history, changes, default_vendor)
    return records, report, problems


def record_text(rec):
    """A record as written: sorted keys, one-space indent, unescaped text, a final newline (the bank's JSON style)."""
    return json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def main(argv=None):
    """The command line: admit, refuse to write anything when a template is not admitted, else write (or --check)."""
    p = argparse.ArgumentParser(description="Admit versus by class's second readings "
                                            "(bank/versus-classes/src/reviews/).")
    p.add_argument("--readings", nargs="+", required=True)
    p.add_argument("--packets", nargs="+", required=True)
    p.add_argument("--changes", default=None)
    p.add_argument("--src", default=str(VC.CLASS_SRC))
    p.add_argument("--vendor", default=SECOND_VENDOR, help="the readers' vendor when a reading does not say")
    p.add_argument("--report", default=None)
    p.add_argument("--check", action="store_true", help="exit 1 if a run would change a file; nothing is written")
    a = p.parse_args(argv)
    changes = {c["id"]: c for c in json.loads(Path(a.changes).read_text(encoding="utf-8"))} if a.changes else {}
    records, report, problems = admit(Path(a.src), a.readings, a.packets, changes, a.vendor)
    if a.report:
        Path(a.report).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if problems:
        raise SystemExit("versus_classes_admit: not admitted, nothing written:\n  " + "\n  ".join(problems))
    out = Path(a.src) / "reviews"
    stale = [i for i, r in records.items() if not (out / f"{i}.json").is_file()
             or (out / f"{i}.json").read_text(encoding="utf-8") != record_text(r)]
    if a.check:
        if stale:
            raise SystemExit(f"versus_classes_admit: {len(stale)} records would change: {', '.join(sorted(stale))}")
        print(f"versus_classes_admit: {len(records)} records, none would change")
        return
    out.mkdir(exist_ok=True)
    for i, r in records.items():
        (out / f"{i}.json").write_text(record_text(r), encoding="utf-8")
    print(f"versus_classes_admit: {len(records)} templates admitted, {len(stale)} records written")


if __name__ == "__main__":
    main()
