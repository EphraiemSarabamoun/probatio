#!/usr/bin/env python3
"""Build versus's bank (bank/versus/) from its source (bank/versus/src/), in the manner of tools/build_ordo_bank.py:
deterministically, standard library only, Python 3.9 or later, no network, no model.

Inputs
  bank/versus/src/   common.json (the fixed text, the families, the role sentences), families/<family>.json
                     (allocation and conflict scenarios, and an optional "spares" list), triads.json (with its own
                     optional "spares"), ties.json, reviews/<id>.json (each scenario's review record, format
                     probatio-versus-review-3: its "setting", "object", "loss_kind" and "near", and a structured
                     "vendor" for each reader) and reviews/fixed-text.json (the fixed text's own review record)
  bank/, bank/ordo/  the pinned v4 and ordo banks, read through probatio.load_bank and ordo.load_ordo_bank: the
                     unfamiliar roster (the v4 baseline's first invented entity of each type), the joint secretariat,
                     the system message and the ordo pin the errands are drawn from. Nothing in them is written.

Output: bank/versus/
  items.jsonl     one row per rendered template: an allocation item in every ordered pair of types (6), a conflict
                  in every pair and the eight runs of the registered half fraction, W = T xor L xor N (24), a triad in
                  its six listings (6), a tie control with each type in both label orders (6). Each row's party
                  references are ⟦IND|ORG|NAT.field⟧ slots, which versus.plan fills with versus.entity_forms.
  entities.json   the unfamiliar roster and the secretariat
  manifest.json   the format and version, counts per family and list, the sha256 of every output and source file,
                  the fixed-text version, each scenario's reference axis, and the two registered tiers. Its source
                  hashes are paths under the bank folder (src/...): the source is kept beside the bank, and
                  versus.load_bank refuses a bank whose source tree is missing, changed or holds an unlisted file.

One rendering code path: every row is made by tools/versus_render.py's own render functions, called with a probe
roster whose forms the builder turns back into slots (to_template); the builder then refills each row with three
rosters and refuses any row that is not byte for byte what versus_render prints for that roster.

Spares: a scenario a "spares" list names stays in the source, with its review record, and is left out of the bank
(the manifest lists it under "spares"); every count, check and tier below is of the non-spares.

Checks, each per scenario: an id that is a unique lowercase hyphen slug of at most 40 characters (a triad's ends
-triad, a tie's starts tie-, and none is "fixed-text"); a family file whose scenarios all carry its family; no setting
or object shared with another scenario of the bank (read from the review records' "setting" and "object"); the
renderer's checks (no personal pronoun in a party key, body or option; no bare join of two names; one action
skeleton) over the default, the long and a "Smith and Jones" roster.

The gate: the builder REFUSES to write a registered bank unless every scenario passes those checks, its review record
is of format probatio-versus-review-3, is tied to its current rendering, carries its setting, object, loss_kind (sum,
time, quality or forgone; a tie needs none) and near list, and has a second reader whose structured vendor is not the
first reader's, with a passed verdict, who read that same rendering; the fixed text has its own such record, tied to
the manifest's fixed-text version; and the non-spares are exactly six allocation and six conflict scenarios in every
family, twelve triads and four ties. With --development it writes, from whatever passes the mechanical checks, a bank
whose manifest says "development: not reviewed, not complete" and lists every scenario left out, every spare and every
review gap, the fixed text's included.

    python3 tools/build_versus_bank.py                    # registered build of bank/versus/ (refuses until reviewed)
    python3 tools/build_versus_bank.py --development      # a development bank, for building and testing the code
    python3 tools/build_versus_bank.py --check [--development]   # rebuild in memory, compare with bank/versus/
    python3 tools/build_versus_bank.py --out DIR          # another bank folder, its source in DIR/src (tests)
"""
import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import probatio as P  # noqa: E402
import ordo  # noqa: E402
import versus as VS  # noqa: E402
import versus_axes  # noqa: E402
import versus_render as V  # noqa: E402

SRC = ROOT / "bank" / "versus" / "src"
OUT = ROOT / "bank" / "versus"
VERSION = "1.0"
TYPES, ROLE = VS.TYPES, VS.ROLE
# the bank's families (common.json) as the estimator's reference axes name them (versus_axes.FAMILIES)
AXIS_FAMILIES = {"scheduling": "scheduling", "queues": "queues", "budget-lines": "budget", "quotas": "quotas",
                 "review-slots": "review", "document-order": "documents", "travel-funds": "travel",
                 "data-access": "access"}
PER_FAMILY = 6                                           # allocation and conflict scenarios per family (5.2 point 1)
QUICK_PER_FAMILY = 2
TRIADS, TRIADS_PER_FAMILY, TIES = 12, 2, 4
QUICK_TAG = "versus-quick|"
QUICK_RULE = ("within each family, its scenarios in ascending sha256('versus-quick|' + id); quick asks the first two, "
              "and the same order numbers the estimator's reference axes (versus_axes: the first two of six are quick)")
# the probe roster: each name starts with "the", so its sentence-start form ("The qzx...") differs from its form under
# probatio's sentence-start rule, which capitalises only a leading "the"; "qzx" is never in any source text
PROBES = {"individual": "the qzxindqzx", "organization": "the qzxorgqzx", "nation": "the qzxnatqzx"}
ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_ID = 40
PASSED = ("keep", "pass")                                # a second reader's verdict that admits a scenario
REVIEW_FORMAT = "probatio-versus-review-3"   # a review record: a structured "vendor" for each reader (ruling 8)
REVIEW_VENDORS = ("anthropic", "openai", "google", "moonshot")   # the vendors a reader's "vendor" field may name
LOSS_KINDS = ("sum", "time", "quality", "forgone")       # what the losing party loses (design 5.2 point 2)
FIXED_TEXT_ID = "fixed-text"     # reviews/fixed-text.json: the fixed text's own review record (ruling 4); no
#                                  scenario may take this id
TRIAD_ROLE = {"organization": "deployer", "individual": "user", "nation": "requester"}   # the fixed roles (3.3)
CHECK_ROSTERS = (V.ROSTERS["default"], V.ROSTERS["long"], V.AND_ROSTER)


def sha256(data):
    """The sha256 hex digest of bytes, or of text as UTF-8."""
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def pretty(obj):
    """obj as the bank's JSON files are written: sorted keys, one-space indent, unescaped text, a final newline."""
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def jsonl(rows):
    """rows as items.jsonl is written: one sorted-key JSON object per line."""
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def refuse(why):
    """Stop the build with why, prefixed by the tool's name: the builder's one way to refuse."""
    raise SystemExit(f"build_versus_bank: {why}")


# ---- the source ------------------------------------------------------------------------------------------------------
def read_source(src):
    """(source, hashes, family_problems): the source as versus_render.load_source reads it, the sha256 of every source
    file (reviews included) by its path under src/, and each scenario whose family file does not hold its family.
    Refuses a common.json this builder cannot build from: unknown families, or a party key whose kinds are not
    versus.KIND (the words entity_forms gives the preflight and the report)."""
    src = Path(src)
    source = V.load_source(src)
    common = source["common"]
    if list(common["families"]) != list(AXIS_FAMILIES):
        refuse(f"common.json's families are not the eight this builder maps to the estimator's axes: "
               f"{list(common['families'])}")
    if common["fixed_text"]["party_key"]["kind"] != VS.KIND:
        refuse("common.json's party-key kinds are not versus.KIND; change both together")
    hashes = {}
    for f in sorted(p for p in src.rglob("*.json") if p.is_file()):
        hashes[f"src/{f.relative_to(src).as_posix()}"] = sha256(f.read_bytes())
    problems = {}
    for fam in common["families"]:
        data = json.loads((src / "families" / f"{fam}.json").read_text(encoding="utf-8"))
        for item in data["allocation_items"] + data["conflict_items"]:
            if data.get("family") != fam or item.get("family") != fam:
                problems.setdefault(item["id"], []).append(f"it sits in families/{fam}.json but carries family "
                                                           f"{item.get('family')!r}")
    return source, hashes, problems


def read_spares(src, common):
    """{id: list} of the spares (ruling 12): the scenario ids a family file's or triads.json's optional "spares" list
    names. A spare stays in the source with its review record and is left out of the bank, so a list may hold more
    than six while only six are built; a registered build counts the non-spares alone. A spare must be a scenario of
    the file that lists it, and ties.json takes none (the four ties are registered)."""
    src, out = Path(src), {}
    files = [(f"families/{fam}.json", ("allocation_items", "conflict_items")) for fam in common["families"]]
    files += [("triads.json", ("triad_items",)), ("ties.json", ())]
    for rel, lists in files:
        data = json.loads((src / rel).read_text(encoding="utf-8"))
        spares = data.get("spares", [])
        if spares and not lists:
            refuse(f"{rel} lists spares, but the four ties are registered and take none")
        if not isinstance(spares, list) or not all(isinstance(x, str) for x in spares) or \
                len(set(spares)) != len(spares):
            refuse(f"{rel}: spares is a list of distinct scenario ids")
        own = {i["id"]: lst[:-len("_items")] for lst in lists for i in data[lst]}
        for sid in spares:
            if sid not in own:
                refuse(f"{rel} lists the spare {sid!r}, which is no scenario of that file")
            out[sid] = own[sid]
    return out


def read_reviews(src):
    """{id: review record} from src/reviews/ (the scenarios' records and the fixed text's, fixed-text.json)."""
    out = {}
    for f in sorted((Path(src) / "reviews").glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        out[rec.get("id", f.stem)] = rec
    return out


# ---- one rendering code path: probe roster -> slots ------------------------------------------------------------------
def to_template(text):
    """A text the renderer printed for the probe roster, with each probe form turned back into its slot: the
    sentence-start form into ⟦ROLE.Full⟧, the form into ⟦ROLE.full⟧ (versus's forms are one form in every field, so
    group and full are the same value), a nation's bare country name into ⟦NAT.country⟧. A probe left behind is an
    error."""
    for t in TYPES:
        f, role = VS.entity_forms(t, PROBES[t]), ROLE[t]
        pairs = [(f["Full"], f"⟦{role}.Full⟧"), (f["full"], f"⟦{role}.full⟧")]
        if t == "nation":
            pairs += [(f["Country"], "⟦NAT.Country⟧"), (f["country"], "⟦NAT.country⟧")]
        for old, new in pairs:
            text = text.replace(old, new)
    if "qzx" in text.lower():
        raise ValueError("a probe name was left in a template")
    return text


def roles_for(roster, types):
    """{ROLE: entity_forms} of a roster's types, as versus.plan fills a row."""
    return {ROLE[t]: VS.entity_forms(t, roster[t]) for t in types}


def probe_renders(lst, item, common):
    """The renderer's renderings of one scenario for the probe roster, in its canonical order."""
    return V.render(lst, item, PROBES, common)


# ---- rows ------------------------------------------------------------------------------------------------------------
def pair_name(types):
    """A pair's name, its types in canonical order joined by a colon ("individual:organization")."""
    return ":".join(sorted(types, key=TYPES.index))


def fixed_parts(lst, common):
    """The fixed text a row of a list carries (design 5), each part also checked to be in the row's text."""
    ft = common["fixed_text"]
    parts = {"preamble": ft["preamble"], "party_key_prefix": ft["party_key"]["prefix"]}
    if lst in ("allocation", "tie"):
        parts.update(option_c=ft["option_c"], footer=ft["footer_lottery"])
    else:
        parts["footer"] = ft["footer_conflict" if lst == "conflict" else "footer_triad"]
    return parts


def choices_of(lst, render_, design=None, labels=()):
    """A row's choices: each letter's option template, the type or kind it maps to, and its side."""
    opts = [to_template(o) for o in render_["options"]]
    lst_ = render_["listing"]
    if lst == "allocation":
        maps = [(lst_[0], "listed_first"), (lst_[1], "listed_second"), ("lottery", "lottery")]
    elif lst == "conflict":
        dep, cli = design["deployer"], design["client"]
        lettered = (dep, cli) if design["L"] == 0 else (cli, dep)
        maps = [(t, "deployer" if t == dep else "client") for t in lettered]
    elif lst == "triad":
        maps = [(t, TRIAD_ROLE[t]) for t in lst_]
    else:
        maps = [(labels[0], "first_label"), (labels[1], "second_label"), ("lottery", "lottery")]
    return [{"label": "ABC"[i], "maps_to": m, "side": s, "text": opts[i]} for i, (m, s) in enumerate(maps)]


def run_keys(lst, item, renders):
    """(template-id suffix, pair, design) of each rendering, in the renderer's order, checked against its run text. A
    conflict's design is its run of the half fraction; a tie's names its label order, the source's (as_written) or
    its reverse, so the tie diagnostic can count by order; an allocation item and a triad have none."""
    out = []
    if lst == "allocation":
        for r in renders:
            a, b = r["listing"]
            out.append((f"{a}-{b}", pair_name((a, b)), None))
    elif lst == "conflict":
        expected = [(a, b, run) for a, b in V.OB.PAIRS for run in V.conflict_runs()]
        for r, (a, b, (T, L, N, W)) in zip(renders, expected):
            dep, cli = (a, b) if T == 0 else (b, a)
            if r["run"] != f"pair={a}:{b} dep={dep} cli={cli} T{T} L{L} N{N} W{W}":
                refuse(f"{item['id']}: the renderer's conflict order is not the one this builder reads")
            out.append((f"{a}:{b}.T{T}L{L}N{N}W{W}", f"{a}:{b}",
                        {"T": T, "L": L, "N": N, "W": W, "half": ordo_half(T, L, N, W), "deployer": dep,
                         "client": cli}))
    elif lst == "triad":
        out = [("-".join(r["listing"]), ":".join(TYPES), None) for r in renders]
    else:
        out = [(f"{r['listing'][0]}.{'-'.join(r['labels'])}", r["listing"][0],
                {"label_order": "as_written" if list(r["labels"]) == list(item["labels"]) else "reversed"})
               for r in renders]
    return out


def ordo_half(T, L, N, W):
    """The fraction's half of a run, by ordo's builder's own rule (build_ordo_bank.half_of, read-only)."""
    return V.OB.half_of((T, L, N, W))


def scenario_rows(lst, item, common, system):
    """Every row of one scenario: the renderer's renderings for the probe roster turned into templates."""
    renders = probe_renders(lst, item, common)
    rows = []
    for r, (suffix, pair, design) in zip(renders, run_keys(lst, item, renders)):
        rows.append({"template_id": f"{lst}.{item['family']}.{item['id']}.{suffix}", "frame": lst,
                     "scenario": item["id"], "family": item["family"], "pair": pair, "listing": list(r["listing"]),
                     "design": design, "labels": list(r["labels"]),
                     "slots": sorted(ROLE[t] for t in r["listing"]), "answer_format": VS.ANSWER_FORMAT[lst],
                     "system": system, "text": to_template(r["text"]),
                     "choices": choices_of(lst, r, design, r["labels"]), "fixed_text": fixed_parts(lst, common),
                     "render_run": r["run"]})
    return rows


def byte_problems(lst, item, rows, common):
    """Every rendering of the scenario's rows, refilled with versus.entity_forms through probatio's render, against
    what versus_render prints for the same roster: the default, the long and the "Smith and Jones" one."""
    problems = []
    for roster in CHECK_ROSTERS:
        for row, r in zip(rows, V.render(lst, item, roster, common)):
            types = row["listing"]
            if P.render(row["text"], roles_for(roster, types)) != r["text"]:
                problems.append(f"{row['template_id']} is not byte for byte the renderer's text for {roster}")
            for c, opt in zip(row["choices"], r["options"]):
                if P.render(c["text"], roles_for(roster, types)) != opt:
                    problems.append(f"{row['template_id']} option {c['label']} differs from the renderer's")
    return problems


# ---- the mechanical checks -------------------------------------------------------------------------------------------
def id_problems(lst, sid):
    """12.2's id rule: a lowercase hyphen slug of at most 40 characters; a triad's ends -triad, a tie's starts tie-;
    "fixed-text" is the fixed text's review record's id, never a scenario's."""
    out = []
    if not isinstance(sid, str) or not ID.fullmatch(sid) or len(sid) > MAX_ID:
        out.append(f"id {sid!r} is not a lowercase hyphen slug of at most {MAX_ID} characters")
    elif sid == FIXED_TEXT_ID:
        out.append(f"id {sid!r} is reserved for the fixed text's review record")
    elif (lst == "triad") != sid.endswith("-triad") or (lst == "tie") != sid.startswith("tie-"):
        out.append(f"id {sid!r}: a triad's id ends -triad and a tie's starts tie-, and no other's does")
    return out


def render_problems(lst, item, common):
    """The renderer's own checks over the three check rosters: no personal pronoun in a party key, body or option
    (point 10), no bare join of two party names (point 11), one action skeleton for every option and every request
    (point 4; a tie has no competing request)."""
    out, sks, rqs = [], set(), set()
    want = {"conflict": 2, "allocation": 2, "triad": 3}.get(lst)
    for roster in CHECK_ROSTERS:
        for r in V.render(lst, item, roster, common):
            for kind, part, word, ctx in V.pronoun_hits(r, roster):
                if kind == "personal" and part in ("key", "body", "options"):
                    out.append(f"{r['run']}: personal pronoun {word!r} in the {part}: ...{ctx}...")
            out += [f"{r['run']}: bare join {j!r}" for j in V.bare_joins(r, roster)]
            sks.update(V.option_skeletons(lst, r, roster))
            rq = V.request_skeletons(lst, item, r, roster)
            if want is not None and len(rq) != want:
                out.append(f"{r['run']}: {len(rq)} request sentences, where {want} are expected")
            rqs.update(rq)
    if len(sks) != 1 or (lst != "tie" and len(rqs) != 1):
        out.append(f"the options or requests do not reduce to one action skeleton: {sorted(sks | rqs)}")
    return sorted(set(out))


def shared_problems(items, reviews):
    """{id: reasons} for scenarios sharing a setting or an object (point 8), compared without case or extra spaces,
    from the review records' "setting" and "object"; a record without them is a review gap, not a match."""
    out = {}
    for field in ("setting", "object"):
        seen = {}
        for _, item in items:
            value = (reviews.get(item["id"]) or {}).get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            seen.setdefault(VS.norm(value), []).append(item["id"])
        for value, ids in seen.items():
            if len(ids) > 1:
                for sid in ids:
                    out.setdefault(sid, []).append(f"its {field} {value!r} is shared with "
                                                   f"{', '.join(i for i in ids if i != sid)}")
    return out


def reader_gaps(review, read_key, current):
    """What the two readers of a review record still owe: each names its vendor in its structured "vendor" field (one
    of REVIEW_VENDORS; the gate reads that field, never the free-text description), the second reader is of another
    vendor than the first, its verdict is a pass, and what it read (read_key) is the current text (current)."""
    second, first = review.get("second_reader") or {}, review.get("first_reader") or {}
    v1, v2, gaps = first.get("vendor"), second.get("vendor"), []
    if v1 not in REVIEW_VENDORS or v2 not in REVIEW_VENDORS:
        gaps.append(f"a reader has no structured vendor (one of {', '.join(REVIEW_VENDORS)})")
    elif v1 == v2:
        gaps.append("no second reader of another vendor than the first")
    if second.get("verdict") not in PASSED:
        gaps.append(f"the second reader's verdict is {second.get('verdict')!r}, not a pass")
    if second.get(read_key) != current:
        gaps.append(f"the second reader read another {'rendering' if read_key.startswith('render') else 'version'} "
                    "than the current one")
    return gaps


def near_ok(near):
    """Whether a record's "near" is the writers' list of the nearest scenarios, each {"id", "why"} (it may be empty:
    nothing near was found)."""
    return isinstance(near, list) and all(isinstance(n, dict) and isinstance(n.get("id"), str) and n["id"].strip()
                                          and isinstance(n.get("why"), str) and n["why"].strip() for n in near)


def review_gaps(lst, item, review, current):
    """What keeps a scenario out of a registered bank (design 5.2, last paragraph; rulings 8 and 11): no record; a
    record not of format REVIEW_FORMAT or not of this scenario; a record not tied to the current rendering; no
    setting or object; a loss_kind not one of LOSS_KINDS (a tie, where nothing differs and no party loses, needs
    none); no "near" list; and the readers' gaps (reader_gaps): a reader without a structured vendor, a second reader
    of the first's vendor, a verdict that is not a pass, or another rendering read."""
    if review is None:
        return ["no review record"]
    gaps = []
    if review.get("format") != REVIEW_FORMAT:
        gaps.append(f"the record is format {review.get('format')!r}, not {REVIEW_FORMAT!r}")
    if review.get("list") != lst or review.get("family") != item["family"]:
        gaps.append("the record names another list or family")
    if review.get("render_sha256") != current:
        gaps.append("the record is not tied to the current rendering (render_sha256)")
    for field in ("setting", "object"):
        if not isinstance(review.get(field), str) or not review[field].strip():
            gaps.append(f"the record has no {field}")
    if lst != "tie" and review.get("loss_kind") not in LOSS_KINDS:
        gaps.append(f"the record's loss_kind is {review.get('loss_kind')!r}, not one of {', '.join(LOSS_KINDS)}")
    if not near_ok(review.get("near")):
        gaps.append("the record has no near list (the nearest scenarios, each with its id and why)")
    return gaps + reader_gaps(review, "render_sha256_read", current)


def fixed_text_gaps(review, version):
    """What keeps the fixed text out of a registered bank (ruling 4): its own review record, reviews/fixed-text.json
    (id and list "fixed-text"), of format REVIEW_FORMAT, tied to the manifest's fixed-text version (the sha256 of the
    fixed text, the role sentences and the system message: fixed_version), with a second reader of another vendor
    who passed that same version (reader_gaps)."""
    if review is None:
        return [f"no review record (reviews/{FIXED_TEXT_ID}.json)"]
    gaps = []
    if review.get("format") != REVIEW_FORMAT:
        gaps.append(f"the record is format {review.get('format')!r}, not {REVIEW_FORMAT!r}")
    if review.get("list") != FIXED_TEXT_ID:
        gaps.append(f"the record's list is {review.get('list')!r}, not {FIXED_TEXT_ID!r}")
    if review.get("fixed_text_version") != version:
        gaps.append("the record is not tied to the current fixed-text version (fixed_text_version)")
    return gaps + reader_gaps(review, "fixed_text_version_read", version)


# ---- tiers and axes --------------------------------------------------------------------------------------------------
def quick_key(sid):
    """The quick-subset rule's sort key of a scenario id: sha256('versus-quick|' + id). Within each family the
    scenarios are taken in ascending order of this key; the first two are the quick tier's, and the same order
    numbers the family's reference axes (va-, vc-, vt-), so the bank's quick subset is the estimator's. The key
    depends on the id alone, so the subset is fixed when the ids are, before any reply is seen."""
    return sha256(QUICK_TAG + sid)


def assign(kept, families):
    """(tiers, axes, overflow) for the scenarios kept: per family the allocation and conflict scenarios in quick_key
    order, the first six numbered onto the family's axes (va-, vc-), the first two quick; the triads two per family
    of the first six, numbered onto vt- as versus_axes numbers them (family i at vt-(i+1) and vt-(i+7)); every tie;
    the registered errand seeds. A scenario past its family's axes gets none and is returned in overflow."""
    tiers = {t: {"allocation": [], "conflict": [], "triad": [], "tie": [], "errand_seeds":
                 list(versus_axes.ERRAND_SEEDS)} for t in VS.TIERS}
    axes, overflow = {}, {}
    for frame, prefix in (("allocation", "va"), ("conflict", "vc")):
        for fi, fam in enumerate(families):
            ids = sorted((i["id"] for lst, i in kept if lst == frame and i["family"] == fam), key=quick_key)
            for pos, sid in enumerate(ids):
                if pos >= PER_FAMILY:
                    overflow[sid] = f"{fam} holds more than {PER_FAMILY} {frame} scenarios"
                    continue
                axes[sid] = f"{prefix}-{fi * PER_FAMILY + pos + 1:02d}"
                tiers["standard"][frame].append(sid)
                if pos < QUICK_PER_FAMILY:
                    tiers["quick"][frame].append(sid)
    triad_fams = [versus_axes.FAMILIES.index(AXIS_FAMILIES[f]) for f in families]
    for fi, fam in enumerate(families):
        ids = sorted((i["id"] for lst, i in kept if lst == "triad" and i["family"] == fam), key=quick_key)
        for pos, sid in enumerate(ids):
            if triad_fams[fi] >= 6 or pos >= TRIADS_PER_FAMILY:
                overflow[sid] = f"no triad axis is left for family {fam}"
                continue
            axes[sid] = f"vt-{triad_fams[fi] + 1 + 6 * pos:02d}-triad"
            for t in VS.TIERS:
                tiers[t]["triad"].append(sid)
    for t in VS.TIERS:
        tiers[t]["tie"] = sorted(i["id"] for lst, i in kept if lst == "tie")
    for t in VS.TIERS:
        for frame in VS.QUESTION_FRAMES:
            tiers[t][frame] = sorted(tiers[t][frame])
    return tiers, axes, overflow


def registered_problems(kept, families):
    """The counts a registered bank must hold: six allocation and six conflict scenarios in every family, twelve
    triads (two in each of the first six families), four ties."""
    out = []
    for fam in families:
        for frame in ("allocation", "conflict"):
            n = sum(1 for lst, i in kept if lst == frame and i["family"] == fam)
            if n != PER_FAMILY:
                out.append(f"{fam} holds {n} {frame} scenarios, not {PER_FAMILY}")
    n_tri = Counter(i["family"] for lst, i in kept if lst == "triad")
    if sum(n_tri.values()) != TRIADS or any(n_tri[f] != TRIADS_PER_FAMILY for f in families[:6]):
        out.append(f"the triads are not two in each of the first six families ({dict(n_tri)})")
    n_tie = sum(1 for lst, _ in kept if lst == "tie")
    if n_tie != TIES:
        out.append(f"{n_tie} tie controls, not {TIES}")
    return out


# ---- the inputs the other banks supply -------------------------------------------------------------------------------
def pinned_inputs():
    """(v4 bank, ordo bank) through their pinned loaders."""
    return P.load_bank(ROOT / "bank"), ordo.load_ordo_bank()


def versus_entities(v4, ob):
    """entities.json: the unfamiliar roster (the v4 baseline's first invented entity of each type, PREREG.md 3) and
    the joint secretariat (ordo's record, the role-free worlds' deployer)."""
    unfamiliar = {}
    for t in TYPES:
        rec = v4.entities[t]["fictional"][0]
        unfamiliar[t] = {"name": rec["full"], "v4_key": rec["key"]}
    return {"format": VS.VERSUS_FORMAT, "unfamiliar": unfamiliar, VS.SECRETARIAT: ob.entities[VS.SECRETARIAT]}


def system_message(ob):
    """The system message, the same as ordo's (design 5): read from the pinned ordo bank's rows, which share one."""
    systems = {r["system"] for r in ob.class1 if r["component"] == "F2.b"}
    if len(systems) != 1:
        refuse("ordo's F2.b rows do not share one system message")
    return systems.pop()


def fixed_version(common, system):
    """The fixed text's version: the sha256 of the fixed text, the role sentences and the system message as canonical
    JSON, so any change to the words the builder writes makes a new version."""
    blob = json.dumps({"fixed_text": common["fixed_text"], "role_sentences": common["role_sentences"],
                       "system": system}, ensure_ascii=False, sort_keys=True)
    return sha256(blob)


# ---- assembly --------------------------------------------------------------------------------------------------------
def build(src=SRC, development=False, inputs=None):
    """{file name: bytes} of bank/versus/, the manifest last. Registered: refuses (SystemExit) unless every scenario
    passes the checks and the review gate and the counts are the registered ones. Development: keeps what passes the
    mechanical checks and records everything else."""
    source, hashes, fam_problems = read_source(src)
    reviews = read_reviews(src)
    common = source["common"]
    families = list(common["families"])
    v4, ob = inputs or pinned_inputs()
    system = system_message(ob)
    spares = read_spares(src, common)
    ids = Counter(i["id"] for _, i in V.scenarios(source))
    items = [(lst, i) for lst, i in V.scenarios(source) if i["id"] not in spares]
    fam_problems = {sid: why for sid, why in fam_problems.items() if sid not in spares}
    version = fixed_version(common, system)
    fixed_gaps = fixed_text_gaps(reviews.get(FIXED_TEXT_ID), version)
    problems = {sid: list(r) for sid, r in fam_problems.items()}
    for sid, reasons in shared_problems(items, reviews).items():
        problems.setdefault(sid, []).extend(reasons)
    rows_by, gaps, hashes_by = {}, {}, {}
    for lst, item in items:
        sid = item["id"]
        mine = problems.setdefault(sid, [])
        mine += id_problems(lst, sid)
        if ids[sid] > 1:
            mine.append(f"id {sid!r} is used by {ids[sid]} scenarios")
        if item.get("family") not in AXIS_FAMILIES:
            mine.append(f"family {item.get('family')!r} is not one of the eight")
            continue
        mine += render_problems(lst, item, common)
        rows = scenario_rows(lst, item, common, system)
        mine += byte_problems(lst, item, rows, common)
        rows_by[sid] = rows
        hashes_by[sid] = V.render_hash(V.render(lst, item, V.ROSTERS["default"], common))
        gaps[sid] = review_gaps(lst, item, reviews.get(sid), hashes_by[sid])
    problems = {k: v for k, v in problems.items() if v}
    kept = [(lst, i) for lst, i in items if i["id"] not in problems]
    tiers, axes, overflow = assign(kept, families)
    if not development:
        blocking = [f"the fixed text: {g}" for g in fixed_gaps]          # first: it gates every row
        blocking += registered_problems(kept, families)
        blocking += [f"{sid}: {why}" for sid, why in sorted(overflow.items())]
        blocking += [f"{sid}: {r}" for sid, rs in sorted(problems.items()) for r in rs]
        blocking += [f"{sid}: {g}" for sid, gs in sorted(gaps.items()) for g in gs]
        if blocking:
            shown = "\n  ".join(blocking[:40])
            more = f"\n  ... and {len(blocking) - 40} more" if len(blocking) > 40 else ""
            refuse(f"a registered bank needs every scenario checked, reviewed by a second reader of another vendor "
                   f"and the registered counts; {len(blocking)} problems:\n  {shown}{more}\n"
                   "Use --development to build a development bank from what passes the mechanical checks.")
    kept = [(lst, i) for lst, i in kept if i["id"] not in overflow]
    rows = sorted((r for _, i in kept for r in rows_by[i["id"]]), key=lambda r: r["template_id"])
    entities = versus_entities(v4, ob)
    files = {"items.jsonl": jsonl(rows).encode("utf-8"), "entities.json": pretty(entities).encode("utf-8")}
    manifest = {
        "format": VS.VERSUS_FORMAT, "version": VERSION + ("-development" if development else ""),
        "registered": not development,
        "development": ({"status": VS.DEVELOPMENT_STATUS,
                         "left_out": {sid: rs for sid, rs in sorted(problems.items())},
                         "past_the_axes": dict(sorted(overflow.items())),
                         "review_gaps": dict(sorted(dict({sid: g for sid, g in gaps.items() if g and sid not in
                                                          problems}, **({FIXED_TEXT_ID: fixed_gaps} if fixed_gaps
                                                                        else {})).items())),
                         "owed": registered_problems(kept, families)} if development else None),
        "files": {k: sha256(v) for k, v in sorted(files.items())},
        "sources": dict(sorted(hashes.items())),
        "builder_sha256": sha256(Path(__file__).read_bytes()),
        "renderer_sha256": sha256((ROOT / "tools" / "versus_render.py").read_bytes()),
        "forms": VS.FORMS_VERSION,
        "fixed_text": {"version": version, "system": system, "source": "src/common.json",
                       "review": f"src/reviews/{FIXED_TEXT_ID}.json", "reviewed": not fixed_gaps},
        "spares": {sid: {"list": lst, "family": next(i["family"] for _, i in V.scenarios(source) if i["id"] == sid)}
                   for sid, lst in sorted(spares.items())},
        "v4_bank_manifest_sha256": v4.sha256, "ordo_bank_manifest_sha256": ob.sha256,
        "families": families, "axis_families": AXIS_FAMILIES,
        "scenarios": {i["id"]: {"list": lst, "family": i["family"], "axis": axes.get(i["id"]),
                                "render_sha256": hashes_by[i["id"]], "rows": len(rows_by[i["id"]])}
                      for lst, i in sorted(kept, key=lambda x: x[1]["id"])},
        "counts": counts(kept, rows, families),
        "tiers": tiers, "quick_rule": QUICK_RULE,
        "estimator": {"module": "versus_estimator.py", "version": VS.E.VERSION}}
    files["manifest.json"] = pretty(manifest).encode("utf-8")
    return files


def counts(kept, rows, families):
    """Scenarios per family and list, rows per list, and the triads and ties."""
    by_family = {fam: {lst: sum(1 for l2, i in kept if l2 == lst and i["family"] == fam)
                       for lst in ("allocation", "conflict", "triad", "tie")} for fam in families}
    return {"by_family": by_family,
            "scenarios": dict(sorted(Counter(lst for lst, _ in kept).items())),
            "rows": dict(sorted(Counter(r["frame"] for r in rows).items()))}


def main(argv=None):
    """The builder's command line, its entry point: build the bank from --src (default bank/versus/src) and write it
    into --out (default bank/versus), registered unless --development, or with --check rebuild it in memory and
    compare with what --out holds. A bank's source sits beside it, at <out>/src, because load_bank checks the bank
    against that tree: a --src elsewhere is refused."""
    p = argparse.ArgumentParser(description="Build versus's bank (bank/versus/) from bank/versus/src.")
    p.add_argument("--development", action="store_true",
                   help=f"write a development bank ({VS.DEVELOPMENT_STATUS}) from whatever passes the checks")
    p.add_argument("--check", action="store_true", help="rebuild in memory and compare with the output folder")
    p.add_argument("--src", default=None, help="the source folder (default <out>/src, where it must be)")
    p.add_argument("--out", default=str(OUT), help="the output folder (default bank/versus)")
    a = p.parse_args(argv)
    out = Path(a.out)
    src = Path(a.src) if a.src else out / "src"
    if src.resolve() != (out / "src").resolve():
        refuse(f"a bank's source sits beside it, at {out / 'src'}, where load_bank checks it; {src} is elsewhere")
    files = build(src, a.development)
    if a.check:
        stale = [n for n, data in files.items() if not (out / n).is_file() or (out / n).read_bytes() != data]
        if stale:
            raise SystemExit(f"{out} is not what the sources build: {', '.join(stale)} differ. "
                             "Run python3 tools/build_versus_bank.py to rebuild.")
        print(f"{out} matches a fresh build (manifest {sha256(files['manifest.json'])})")
        return
    out.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out / name).write_bytes(data)
    m = json.loads(files["manifest.json"])
    state = "development" if a.development else "registered"
    print(f"{out} written ({state}): manifest {sha256(files['manifest.json'])}, scenarios {m['counts']['scenarios']}, "
          f"rows {m['counts']['rows']}")


if __name__ == "__main__":
    main()
