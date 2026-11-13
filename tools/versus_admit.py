#!/usr/bin/env python3
"""Admit versus's second reading into its source: mark the spares the manager's selection names, turn every review
record into format probatio-versus-review-3 with the second reader's verdict on the current text, write the fixed
text's record, and report, per list, which of the bank's scenarios the builder's gate will take and what is owed.

Standard library only, Python 3.9 or later, no network, no model. Everything it writes is derived from its inputs, so
running it again on the same inputs changes nothing (--check says whether a run would change a file).

Inputs (paths given on the command line; none is written into the repo)
  --selection FILE   the manager's choice of six (format manager-selection-1): per list ("allocation" and "conflict" by
                     family, "triad", "tie") its "bank" ids and its "spare" ids. An id written as <...> is a
                     placeholder: it is listed as a gap and nothing is written for it.
  --readings DIR...  the second readers' folders, OLDEST FIRST: each holds reading.json (a list with one entry per
                     scenario read: id, list, family, points "1" to "12" each {verdict, why}, overall, fix, quotes,
                     type_argument, and usually the rendering hash read), and may hold lists.json (the reader's form
                     groups per list) and fixed.json (the fixed sheet's reading). A folder without reading.json
                     stands for every sub-folder that has one, in name order. A folder's own packet/ is a packet.
  --packets DIR...   the packets the readers read: index.json and sheets/<id>.txt, each sheet's head carrying
                     "rendering hash (default roster): <sha256>", and fixed.txt; a packet without index.json is
                     read from its sheets/ and list-sheets/ alone (packet_entries).
  --src DIR          the source (default bank/versus/src), which is rewritten in place.

What the gate decides, per scenario (admit_state). A reading counts only when a packet sheet of that id confirms the
hash it read (confirmed_hash): the hash the entry records must be on a sheet's head (and its sheet_sha256, when it
records one, that sheet's own sha256); an entry that records none takes its own folder's packet, or the one hash
every packet shows for the id. Of the readings of the CURRENT rendering (versus_render.render_hash, default roster),
the newest decides:
  - overall "pass" with no point failed: verdict "pass".
  - ruling R10 (r10_only): overall "fail" on point 8 alone, for the list's form group (the reader's point-8 words
    cite R4 and the reader's lists.json puts the id in a request-form group of more than three), every other point
    not failed: verdict "pass", with a note naming R10 and the reader's own words. The limit of three is a rule
    about the list, settled by the selection.
  - anything else (a fail, a pass after fix, a pass beside a failed point): not passed, with what is owed.
With no reading of the current rendering, the newest reading of an older one stands, not passed: "a re-reading of
the changed text" is owed; with no reading at all, a second reading is owed.

The records (review3). first_reader keeps what it says and gains vendor "anthropic". second_reader is rebuilt from
the deciding reading: vendor (--vendor, default "openai"), the reader's identity without a seat name ("an
independent reader of another vendor, second reading DATE", or "re-reading DATE" for a later reading of the same
id; DATE is reading.json's modification date), the verdict, the hash read, the per-point verdicts, the quotes, the
fix and the type argument, and, when not passed, "owed"; its verdict is "pass" exactly when the gate passed
(verdict_of). A review-2 record's earlier second reader, when it gave a verdict, is kept as "earlier_second_reading"
and its status as "status_before_second_reading"; "status" says the gate state. A record with no "near" gets an
empty one (with_near: the builder's ruling 14, a list possibly empty).

The fixed text's record, reviews/fixed-text.json (fixed_record): passed only if the reader passed every piece and
the sheet it read (fixed.json's fixed_sha256) is the current fixed sheet byte for byte, in which case the version
read is the current fixed_text_version; its pieces are matched to the fixed sheet's by FIXED_PIECES.

The report (build_report) asks the builder itself (tools/build_versus_bank.py): a development build in memory for
every gap it records, and a registered build for whether it would be accepted.

    python3 tools/versus_admit.py --selection SEL.json --readings R1 R2 ... --packets P1 ... [--report OUT.json]
    python3 tools/versus_admit.py ... --check      # exit 1 if a run would change a file; nothing is written
"""
import argparse
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import versus_render as V  # noqa: E402

SRC = ROOT / "bank" / "versus" / "src"
REVIEW_FORMAT = "probatio-versus-review-3"
FIRST_VENDOR, SECOND_VENDOR = "anthropic", "openai"
IDENTITY = "an independent reader of another vendor, {kind} {date}"
READ_HASH_KEYS = ("render_sha256_read", "render_sha256", "packet_render_sha256")   # where readers record the hash
COMMIT_KEYS = ("commit_read", "source_commit", "based_on_commit", "reviewed_commit")
SHEET_HASH = re.compile(r"^rendering hash \(default roster\): ([0-9a-f]{64})$", re.M)
PLACEHOLDER = re.compile(r"^<.*>$")
POINTS = tuple(str(i) for i in range(1, 13))
NOT_FAILED = ("pass", "not applicable", "awaiting the pilot", "recorded")   # a point verdict that is not a fail
FORM_LIMIT = 3                                            # R4: at most three of a list share a form
RENDER_RULE = ("tools/versus_render.py --hashes, default roster: sha256 of the scenario's renderings, in canonical "
               "order, joined by a line of five '=' between blank lines")
R10_NOTE = ("admitted under ruling R10: R4 is a rule about a list, settled by the manager's choice of six; the reader "
            "failed this scenario on point 8 alone, for its list's form group, and passed every other point, on the "
            "text it still has. The reader's words on point 8: ")
OWED_REREAD = "a re-reading of the changed text"
FIXED_ID = V.FIXED_TEXT_ID
# the fixed reader's piece ids, each the fixed sheet's piece name (versus_render.fixed_pieces) it judged
FIXED_PIECES = {"system-message": "system message", "preamble": "preamble",
                "party-key": "party key, in an item's listing order (here person, organization, government)",
                "third-option": "allocation and tie: the third answer",
                "allocation-tie-footer": "allocation and tie: the footer", "conflict-footer": "conflict: the footer",
                "triad-footer": "triad: the footer", "deployer-role": "conflict: the deployer's role sentence",
                "client-role": "conflict: the client's role sentence", "triad-roles": "triad: the role sentences",
                "person-forms": "entity forms: a person", "organization-forms": "entity forms: an organization",
                "government-forms": "entity forms: a government"}
FIXED_FIRST_READER = {"reader": "the fixed text's authors", "vendor": FIRST_VENDOR,
                      "read": "written and checked by its authors with the builder and the renderer; no separate "
                              "first reading of the fixed sheet is recorded"}


# ---- files -----------------------------------------------------------------------------------------------------------
def sha256(data):
    """The sha256 hex digest of bytes, or of text as UTF-8."""
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def load(path):
    """A JSON file's value."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(obj):
    """obj as the source's JSON files are written: one-space indent, unescaped text, keys in their order, a newline."""
    return json.dumps(obj, indent=1, ensure_ascii=False) + "\n"


# ---- the selection ---------------------------------------------------------------------------------------------------
def is_placeholder(sid):
    """Whether a selection id is a placeholder (<...>), not yet a scenario."""
    return bool(PLACEHOLDER.match(str(sid)))


def selection_lists(sel):
    """The selection's lists as [(list, family or None, bank ids, spare ids)]: allocation and conflict by family in
    the selection's order, then the triads and the ties."""
    out = []
    for lst in ("allocation", "conflict"):
        for fam, spec in sel[lst].items():
            out.append((lst, fam, list(spec["bank"]), list(spec.get("spare", []))))
    for lst in ("triad", "tie"):
        out.append((lst, None, list(sel[lst]["bank"]), list(sel[lst].get("spare", []))))
    return out


def selection_gaps(lists, source):
    """What is wrong with the selection against the source: a placeholder, an id the source does not hold in the
    list (and family) the selection names, an id in two places, a scenario in neither bank nor spare."""
    where = {i["id"]: (lst, i["family"]) for lst, i in V.scenarios(source)}
    gaps, placed = [], {}
    for lst, fam, bank, spare in lists:
        name = f"{lst}/{fam}" if fam else lst
        for sid in bank + spare:
            if is_placeholder(sid):
                gaps.append(f"{name}: the placeholder {sid} is not resolved to a scenario")
                continue
            if sid in placed:
                gaps.append(f"{sid} is placed twice ({placed[sid]} and {name})")
            placed[sid] = name
            have = where.get(sid)
            if have is None or have[0] != lst or (fam and have[1] != fam):
                gaps.append(f"{name}: {sid} is not a {lst} scenario{' of ' + fam if fam else ''} in the source")
    gaps += [f"{sid} ({lst}/{fam}) is in neither bank nor spare" for sid, (lst, fam) in where.items()
             if sid not in placed]
    gaps += [f"tie: {sid} is listed as a spare, but the four ties are registered and take none"
             for lst, _, _, spare in lists if lst == "tie" for sid in spare]
    return gaps


def spare_lists(lists, common):
    """{source file under src/: spare ids}: each family file's allocation then conflict spares, triads.json's
    spares; placeholders left out. ties.json takes none (the four ties are registered)."""
    out = {f"families/{fam}.json": [] for fam in common["families"]}
    out["triads.json"] = []
    for lst, fam, _, spare in lists:
        key = f"families/{fam}.json" if fam else ("triads.json" if lst == "triad" else None)
        if key is not None:
            out[key] += [s for s in spare if not is_placeholder(s)]
    return out


def with_spares(data, spares):
    """A family file's or triads.json's data with its "spares" list set (after the scenario lists), or removed when
    there is none."""
    out = {k: v for k, v in data.items() if k != "spares"}
    if spares:
        out["spares"] = list(spares)
    return out


# ---- packets and readings --------------------------------------------------------------------------------------------
def packet_entries(folder):
    """(entries, problems): a packet's sheets as index entries ({"id", "sheet", "render_sha256"}): its index.json's,
    or, for a packet without one, every sheet in sheets/ and list-sheets/, its id the first field of the sheet's first
    line ("<id> | <list> | <family>"), which must be its file name, and no index hash."""
    folder = Path(folder)
    if (folder / "index.json").is_file():
        return load(folder / "index.json"), []
    out, problems = [], []
    for sub in ("sheets", "list-sheets"):
        for path in sorted((folder / sub).glob("*.txt")):
            sid = path.read_text(encoding="utf-8").split("\n", 1)[0].split(" | ", 1)[0].strip()
            if sid != path.stem:
                problems.append(f"{folder.name}: {sub}/{path.name} heads another id ({sid!r}); it is not read")
                continue
            out.append({"id": sid, "sheet": f"{sub}/{path.name}", "render_sha256": None})
    if not out:
        raise SystemExit(f"versus_admit: {folder} holds no index.json and no sheets")
    return out, problems


def load_packet(folder):
    """{"folder", "sheets": {id: [(render hash, sheet sha256)]}, "fixed": sha256 of fixed.txt or None, "problems"}:
    what a packet showed (packet_entries). A sheet's head is what its reader read, so the head's hash is taken; an
    index entry whose hash is not its sheet's head (a sheet rewritten after the index was made), or a sheet with no
    head, is listed among the problems."""
    folder = Path(folder)
    sheets = {}
    entries, problems = packet_entries(folder)
    for e in entries:
        path = folder / e["sheet"]
        if not path.is_file():
            problems.append(f"{folder.name}: {e['sheet']} is in index.json but not in the packet")
            continue
        data = path.read_bytes()
        head = SHEET_HASH.search(data.decode("utf-8"))
        if not head:
            problems.append(f"{folder.name}: {e['sheet']} carries no rendering hash on its head")
            continue
        if e.get("render_sha256") is not None and head.group(1) != e["render_sha256"]:
            problems.append(f"{folder.name}: {e['sheet']}'s head carries {head.group(1)[:12]}, its index entry "
                            f"{str(e.get('render_sha256'))[:12]}; the head, what the reader read, is taken")
        sheets.setdefault(e["id"], []).append((head.group(1), sha256(data)))
    fixed = folder / "fixed.txt"
    return {"folder": folder, "sheets": sheets, "fixed": sha256(fixed.read_bytes()) if fixed.is_file() else None,
            "problems": problems}


def reading_folders(dirs):
    """Each folder holding reading.json, in the order given; a folder without one stands for its sub-folders that
    have one, in name order."""
    out = []
    for d in map(Path, dirs):
        if (d / "reading.json").is_file():
            out.append(d)
        else:
            subs = sorted(p.parent for p in d.glob("*/reading.json"))
            if not subs:
                raise SystemExit(f"versus_admit: {d} holds no reading.json, nor does any folder in it")
            out += subs
    return out


def load_reading(folder, order):
    """One reader's folder: its entries, its lists (lists.json, if any), its fixed reading (fixed.json, if any), its
    own packet (packet/, if any), its order among the readings and its date (reading.json's modification date)."""
    folder = Path(folder)
    path = folder / "reading.json"
    date = datetime.date.fromtimestamp(path.stat().st_mtime).isoformat()
    opt = {name: load(folder / f"{name}.json") if (folder / f"{name}.json").is_file() else None
           for name in ("lists", "fixed")}
    own = load_packet(folder / "packet") if (folder / "packet").is_dir() else None
    return {"folder": folder, "order": order, "date": date, "entries": load(path), "lists": opt["lists"] or [],
            "fixed": opt["fixed"], "packet": own}


def recorded_hash(entry):
    """The rendering hash an entry says it read, under whichever key its reader used (READ_HASH_KEYS), or None."""
    return next((entry[k] for k in READ_HASH_KEYS if isinstance(entry.get(k), str)), None)


def confirmed_hash(entry, reading, packets):
    """(hash, None) when a packet sheet of the entry's id confirms what it read, else (None, why)."""
    sid = entry.get("id")
    pool = packets + ([reading["packet"]] if reading["packet"] else [])
    shown = [s for p in pool for s in p["sheets"].get(sid, [])]
    claimed, sheet = recorded_hash(entry), entry.get("sheet_sha256")
    if sheet:
        hit = [h for h, s in shown if s == sheet]
        if not hit:
            return None, "no packet sheet has the sha256 the reader recorded"
        if claimed and claimed != hit[0]:
            return None, "the hash the reader recorded is not the one on the sheet it read"
        return hit[0], None
    if claimed:
        return (claimed, None) if any(h == claimed for h, _ in shown) else (
            None, "no packet sheet of this id carries the hash the reader recorded")
    if reading["packet"] and reading["packet"]["sheets"].get(sid):
        return reading["packet"]["sheets"][sid][0][0], None
    hashes = {h for h, _ in shown}
    if len(hashes) == 1:
        return hashes.pop(), None
    return None, "the reader recorded no hash and the packets do not settle which sheet it read"


def readings_by_id(readings, packets):
    """({id: [read]}, problems): every entry of every reading as a read ({"reading", "entry", "hash", "why"}),
    oldest first; an entry whose hash no packet confirms keeps hash None and its why, and is listed."""
    out, problems = {}, []
    for r in readings:
        for e in r["entries"]:
            h, why = confirmed_hash(e, r, packets)
            out.setdefault(e.get("id"), []).append({"reading": r, "entry": e, "hash": h, "why": why})
            if why:
                problems.append(f"{e.get('id')} in {r['folder'].name}: {why}")
    return out, problems


# ---- the gate --------------------------------------------------------------------------------------------------------
def failed_points(entry):
    """The points an entry failed, in order."""
    points = entry.get("points") or {}
    return [k for k in POINTS if (points.get(k) or {}).get("verdict") not in NOT_FAILED]


def form_group_over_limit(sid, lst, family, lists):
    """Whether the reader's lists.json puts sid, in its list and family, in a request-form group of more than
    FORM_LIMIT ids."""
    for e in lists:
        if e.get("list") == lst and e.get("family") == family:
            for g in e.get("request_form_groups") or []:
                if isinstance(g, dict) and sid in (g.get("ids") or []) and len(g["ids"]) > FORM_LIMIT:
                    return True
    return False


def r10_only(read, lst, family):
    """Ruling R10: the reader failed the scenario on point 8 alone and only for its list's form group (its point-8
    words cite R4, and its lists.json puts the id in a request-form group over the limit); every other point not
    failed."""
    e = read["entry"]
    why8 = ((e.get("points") or {}).get("8") or {}).get("why", "")
    return (e.get("overall") == "fail" and failed_points(e) == ["8"] and bool(re.search(r"\bR4\b", why8))
            and form_group_over_limit(e.get("id"), lst, family, read["reading"]["lists"]))


def judge(read, lst, family):
    """(passed, note or None, owed or None) of one reading of the current rendering: only an overall "pass" with no
    point failed, or ruling R10's case, passes."""
    e = read["entry"]
    fails, overall = failed_points(e), e.get("overall")
    if overall == "pass" and not fails:
        return True, None, None
    if r10_only(read, lst, family):
        return True, R10_NOTE + e["points"]["8"]["why"], None
    if overall == "pass after fix":
        return False, None, "the reader's fix applied, then " + OWED_REREAD
    if overall == "pass":
        return False, None, f"a re-reading: the reader passed it overall but failed point(s) {', '.join(fails)}"
    if overall == "fail":
        return False, None, f"a change meeting point(s) {', '.join(fails) or 'the reader named'}, then " + OWED_REREAD
    return False, None, f"a reading with a verdict the gate knows (the reader's is {overall!r})"


def admit_state(sid, lst, family, current, reads):
    """The gate state of one scenario: {"passed", "read" (the deciding reading or None), "note", "owed"}."""
    good = [r for r in reads if r["hash"]]
    now = [r for r in good if r["hash"] == current]
    if now:
        passed, note, owed = judge(now[-1], lst, family)
        return {"passed": passed, "read": now[-1], "note": note, "owed": owed}
    if good:
        return {"passed": False, "read": good[-1], "note": None, "owed": OWED_REREAD}
    if reads:
        return {"passed": False, "read": None, "note": None,
                "owed": "a reading whose rendering hash a packet sheet confirms (" + reads[-1]["why"] + ")"}
    return {"passed": False, "read": None, "note": None, "owed": "a second reading by a reader of another vendor"}


def verdict_of(passed, overall):
    """The verdict a record carries: "pass" exactly when the gate passed; otherwise the reader's fail or pass after
    fix, or "owed" (a reader's pass of another text, or beside a failed point or piece, is never written as a pass)."""
    if passed:
        return "pass"
    return overall if overall in ("fail", "pass after fix") else "owed"


def identity(read, reads):
    """The deciding reader's identity, without a seat name: its second reading or a later re-reading, and its date."""
    first = reads[0]["reading"]["order"] == read["reading"]["order"]
    return IDENTITY.format(kind="second reading" if first else "re-reading", date=read["reading"]["date"])


def second_reader(state, reads, vendor):
    """A record's second_reader from its gate state: the deciding reading's verdict, hash, points and quotes."""
    read = state["read"]
    if read is None:
        return {"reader": None, "vendor": None, "verdict": "not read", "render_sha256_read": None,
                "owed": state["owed"]}
    e = read["entry"]
    points = e.get("points") or {}
    out = {"reader": identity(read, reads), "vendor": vendor,
           "verdict": verdict_of(state["passed"], e.get("overall")),
           "render_sha256_read": read["hash"], "render_rule_read": RENDER_RULE,
           "commit_read": next((e[k] for k in COMMIT_KEYS if e.get(k)), None),
           "overall_read": e.get("overall"),
           "points": {k: {"verdict": points[k].get("verdict"), "why": points[k].get("why")}
                      for k in POINTS if isinstance(points.get(k), dict)},
           "quotes": list(e.get("quotes") or []), "fix": e.get("fix"), "type_argument": e.get("type_argument")}
    if state["note"]:
        out["note"] = state["note"]
    if not state["passed"]:
        out["owed"] = state["owed"]
    return out


def status_line(state, date=None):
    """A record's status, from its gate state."""
    if state["passed"]:
        how = " under ruling R10" if state["note"] else ""
        return f"admitted: the second reader passed the current text{how} ({date})"
    return f"owed: {state['owed']}"


def was_read(reader):
    """Whether a review-2 record's second_reader is a reading that happened (a verdict), not the "pending" slot."""
    return isinstance(reader, dict) and reader.get("verdict") not in (None, "pending")


def review3(rec, second, status):
    """A review record in format REVIEW_FORMAT: its keys in their order, first_reader with its vendor, second_reader
    and status as given; a review-2 record's second reader (when it gave a verdict: was_read) and status kept
    beside them, once."""
    converting = rec.get("format") != REVIEW_FORMAT
    out = {}
    for k, v in rec.items():
        if k == "format":
            out[k] = REVIEW_FORMAT
        elif k == "status":
            out[k] = status
            if converting and v is not None and "status_before_second_reading" not in rec:
                out["status_before_second_reading"] = v
        elif k == "first_reader":
            out[k] = dict(v or {}, vendor=FIRST_VENDOR)
        elif k == "second_reader":
            out[k] = second
            if converting and was_read(v) and "earlier_second_reading" not in rec:
                out["earlier_second_reading"] = v
        else:
            out[k] = v
    out.setdefault("status", status)
    out.setdefault("first_reader", {"vendor": FIRST_VENDOR})
    out.setdefault("second_reader", second)
    return out


def with_near(rec):
    """A scenario's record with "near" an empty list when it has none (builder's ruling 14: "near" is a list of {id,
    why}, possibly empty, nothing near found; the reader's sheet showed such a record's near as "none"), placed after
    its loss_kind (or object). A near the record has, well formed or not, is left as it is for the gate to judge."""
    if "near" in rec:
        return rec
    after = "loss_kind" if "loss_kind" in rec else ("object" if "object" in rec else None)
    out = {}
    for k, v in rec.items():
        out[k] = v
        if k == after:
            out["near"] = []
    out.setdefault("near", [])
    return out


# ---- the fixed text --------------------------------------------------------------------------------------------------
def fixed_state(readings, common, packets):
    """The fixed text's gate state: the newest fixed reading of the current fixed sheet decides; passed only if its
    overall and every piece passed and its pieces are the sheet's (FIXED_PIECES)."""
    sheet = sha256(V.fixed_sheet(common))
    shown = {p["fixed"] for p in packets} | {r["packet"]["fixed"] for r in readings if r["packet"]}
    fixed = [r for r in readings if r["fixed"]]
    now = [r for r in fixed if r["fixed"].get("fixed_sha256") == sheet and sheet in shown]
    if not now:
        owed = (OWED_REREAD + " (the fixed sheet read is not the current one, or no packet shows it)" if fixed
                else "a second reading of the fixed sheet")
        return {"passed": False, "read": fixed[-1] if fixed else None, "owed": owed, "sheet": sheet}
    r = now[-1]
    pieces = r["fixed"].get("pieces") or []
    names = [FIXED_PIECES.get(p.get("id")) for p in pieces]
    if sorted(n or "" for n in names) != sorted(n for n, _ in V.fixed_pieces(common)):
        return {"passed": False, "read": r, "owed": "a reading whose pieces are the fixed sheet's", "sheet": sheet}
    bad = [p.get("id") for p in pieces if p.get("overall") != "pass"]
    if r["fixed"].get("overall") != "pass" or bad:
        return {"passed": False, "read": r, "sheet": sheet,
                "owed": f"a change to the piece(s) {', '.join(bad) or 'read'}, then " + OWED_REREAD}
    return {"passed": True, "read": r, "owed": None, "sheet": sheet}


def fixed_record(rec, state, readings, common, vendor):
    """reviews/fixed-text.json from its gate state, in the builder's shape: id and list FIXED_ID, the current
    fixed_text_version, a first reader of the first vendor, the second reader (the version it read is the current
    one only when the sheet it read is the current sheet), and each piece's verdict and notes."""
    r, version = state["read"], V.fixed_text_version(common)
    f = r["fixed"] if r else {}
    by_name = {FIXED_PIECES.get(p.get("id")): p for p in f.get("pieces") or []}
    order = [x for x in readings if x["fixed"]]
    second = {"reader": None, "vendor": None, "verdict": "not read", "fixed_text_version_read": None,
              "owed": state["owed"]}
    if r:
        kind = "second reading" if order[0]["order"] == r["order"] else "re-reading"
        read_version = version if f.get("fixed_sha256") == state["sheet"] else None
        second = {"reader": IDENTITY.format(kind=kind, date=r["date"]), "vendor": vendor,
                  "verdict": verdict_of(state["passed"], f.get("overall")),
                  "fixed_text_version_read": read_version, "fixed_sheet_sha256_read": f.get("fixed_sha256"),
                  "commit_read": next((f[k] for k in COMMIT_KEYS if f.get(k)), None), "overall_read": f.get("overall")}
        if not state["passed"]:
            second["owed"] = state["owed"]
    pieces = []
    for name, _ in V.fixed_pieces(common):
        p = by_name.get(name)
        if p is None:
            pieces.append({"name": name, "status": "not read", "notes": []})
            continue
        notes = [p.get("why")] + list(p.get("limits") or []) + ([f"fix: {p['fix']}"] if p.get("fix") else [])
        pieces.append({"name": name, "status": p.get("overall"), "notes": [n for n in notes if n],
                       "quotes": list(p.get("quotes") or [])})
    out = review3(dict(rec or {}, id=FIXED_ID, list=FIXED_ID), second,
                  status_line(dict(state, note=None), r and r["date"]))
    out["fixed_text_version"] = version
    out["first_reader"] = dict(FIXED_FIRST_READER, **{k: v for k, v in (rec or {}).get("first_reader", {}).items()
                                                       if k != "vendor"}, vendor=FIRST_VENDOR)
    out["pieces"] = pieces
    return out


# ---- the run ---------------------------------------------------------------------------------------------------------
def admit(src, selection, reading_dirs, packet_dirs, vendor=SECOND_VENDOR):
    """({path under src: new text}, states, context): every file the admission writes, with what it decided. Reads
    the source, the selection, the readings and the packets; writes nothing."""
    src = Path(src)
    source = V.load_source(src)
    common = source["common"]
    sel = load(selection)
    lists = selection_lists(sel)
    packets = [load_packet(p) for p in packet_dirs]
    readings = [load_reading(d, n) for n, d in enumerate(reading_folders(reading_dirs))]
    reads, problems = readings_by_id(readings, packets)
    problems = [x for p in packets + [r["packet"] for r in readings if r["packet"]] for x in p["problems"]] + problems
    files = {}
    for rel, spares in spare_lists(lists, common).items():
        data = load(src / rel)
        files[rel] = dump(with_spares(data, spares))
    states = {}
    for lst, item in V.scenarios(source):
        sid = item["id"]
        current = V.render_hash(V.render(lst, item, V.ROSTERS["default"], common))
        mine = reads.get(sid, [])
        state = admit_state(sid, lst, item["family"], current, mine)
        state.update(list=lst, family=item["family"], current=current)
        states[sid] = state
        path = src / "reviews" / f"{sid}.json"
        if not path.is_file():                 # a record is the writers' to make; the gate reports its absence
            problems.append(f"{sid}: no review record (reviews/{sid}.json); none is written")
            continue
        date = state["read"]["reading"]["date"] if state["read"] else None
        files[f"reviews/{sid}.json"] = dump(review3(with_near(load(path)), second_reader(state, mine, vendor),
                                                    status_line(state, date)))
    fpath = src / "reviews" / f"{FIXED_ID}.json"
    fstate = fixed_state(readings, common, packets)
    files[f"reviews/{FIXED_ID}.json"] = dump(fixed_record(load(fpath) if fpath.is_file() else None, fstate, readings,
                                                          common, vendor))
    unknown = sorted(k for k in reads if k not in states)
    problems += [f"{sid}: read, but no scenario of the source has this id" for sid in unknown]
    ctx = {"lists": lists, "selection_gaps": selection_gaps(lists, source), "reading_problems": problems,
           "fixed": fstate, "readings": [str(r["folder"]) for r in readings]}
    return files, states, ctx


def changed_files(src, files):
    """The paths (under src) whose text on disk differs from what the admission writes."""
    return sorted(rel for rel, text in files.items()
                  if not (Path(src) / rel).is_file() or (Path(src) / rel).read_text(encoding="utf-8") != text)


def write_files(src, files):
    """Write every changed file of the admission into src; the paths written."""
    changed = changed_files(src, files)
    for rel in changed:
        (Path(src) / rel).write_text(files[rel], encoding="utf-8")
    return changed


# ---- the report ------------------------------------------------------------------------------------------------------
def builder_view(src):
    """What tools/build_versus_bank.py makes of the source: its development manifest's gaps (left out by a check,
    past the axes, review gaps, counts owed) and whether a registered build is accepted, with its refusal."""
    import build_versus_bank as B
    dev = json.loads(B.build(src, development=True)["manifest.json"])["development"]
    try:
        B.build(src, development=False)
        refusal = None
    except SystemExit as exc:
        refusal = str(exc)
    return {"registered_accepted": refusal is None, "refusal": refusal, "left_out": dev["left_out"],
            "past_the_axes": dev["past_the_axes"], "review_gaps": dev["review_gaps"], "owed": dev["owed"]}


def state_word(state):
    """A scenario's gate state in a few words."""
    if state is None:
        return "not in the source"
    if state["passed"]:
        return "passed (R10)" if state["note"] else "passed"
    return "owed: " + state["owed"]


def build_report(states, ctx, builder):
    """The JSON report: per list its six bank ids with each one's gate state, its spares; the fixed text; the
    selection's and the readings' problems; and the builder's view."""
    lists = []
    for lst, fam, bank, spare in ctx["lists"]:
        rows = [{"id": sid, "state": state_word(states.get(sid)) if not is_placeholder(sid) else "a placeholder"}
                for sid in bank]
        lists.append({"list": lst, "family": fam, "bank": rows, "spares": spare,
                      "passed": sum(1 for r in rows if r["state"].startswith("passed"))})
    f = ctx["fixed"]
    return {"format": "versus-admit-report-1", "readings": ctx["readings"], "lists": lists,
            "fixed_text": "passed" if f["passed"] else "owed: " + f["owed"],
            "selection_gaps": ctx["selection_gaps"], "reading_problems": ctx["reading_problems"],
            "builder": builder}


def report_text(rep):
    """The report on one screen: one line per list, the fixed text, then the builder's verdict and gap counts."""
    lines = []
    for e in rep["lists"]:
        name = f"{e['list']}/{e['family']}" if e["family"] else e["list"]
        owed = [f"{r['id']} ({r['state'][len('owed: '):] if r['state'].startswith('owed: ') else r['state']})"
                for r in e["bank"] if not r["state"].startswith("passed")]
        lines.append(f"{name:24s} {e['passed']}/{len(e['bank'])} passed; spares {', '.join(e['spares']) or 'none'}"
                     + (f"\n{'':24s} owed: " + "; ".join(owed) if owed else ""))
    lines.append(f"fixed text: {rep['fixed_text']}")
    for key in ("selection_gaps", "reading_problems"):
        if rep[key]:
            lines.append(f"{key.replace('_', ' ')}: " + "; ".join(rep[key]))
    b = rep["builder"]
    gaps = {"left out by a check": len(b["left_out"]), "past the axes": len(b["past_the_axes"]),
            "with review gaps": len(b["review_gaps"]), "counts owed": len(b["owed"])}
    lines.append("registered build: " + ("ACCEPTED" if b["registered_accepted"] else "refused; "
                                         + ", ".join(f"{n} {k}" for k, n in gaps.items())))
    for o in b["owed"]:
        lines.append(f"  owed: {o}")
    for sid, why in sorted(b["past_the_axes"].items()):
        lines.append(f"  past the axes: {sid}: {why}")
    return "\n".join(lines)


def main(argv=None):
    """The command line: admit the readings into --src (or, with --check, only say whether that would change a
    file), then print the report and write it as JSON to --report."""
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--selection", required=True, help="the manager's choice of six (manager-selection-1)")
    p.add_argument("--readings", nargs="+", required=True, help="the readers' folders, oldest first")
    p.add_argument("--packets", nargs="*", default=[], help="the packets the readers read")
    p.add_argument("--src", default=str(SRC), help="the source folder (default bank/versus/src)")
    p.add_argument("--vendor", default=SECOND_VENDOR, help="the readers' vendor (default openai)")
    p.add_argument("--report", help="write the JSON report to this file")
    p.add_argument("--check", action="store_true", help="write nothing; exit 1 if a run would change a file")
    a = p.parse_args(argv)
    files, states, ctx = admit(a.src, a.selection, a.readings, a.packets, a.vendor)
    if a.check:
        changed = changed_files(a.src, files)
        print(f"versus_admit: {len(changed)} file(s) would change" + (": " + ", ".join(changed) if changed else ""))
        return 1 if changed else 0
    changed = write_files(a.src, files)
    rep = build_report(states, ctx, builder_view(a.src))
    rep["written"] = changed
    if a.report:
        Path(a.report).write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"versus_admit: {len(changed)} file(s) written")
    print(report_text(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
