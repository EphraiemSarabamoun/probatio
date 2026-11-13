#!/usr/bin/env python3
"""Build versus's class bank (bank/versus-classes/: items.jsonl and manifest.json) from the Class I templates beside
it in bank/versus-classes/src/ (versus_classes.CLASS_SRC; rulings C2 to C4, C8, K12 and K15,
2026-11-13).

    python3 tools/build_versus_classes.py --development     # a development bank: no second reading required
    python3 tools/build_versus_classes.py                   # a registered bank: every template read and passed
    python3 tools/build_versus_classes.py --check [--development]   # rebuild in memory, compare with the bank

What it builds. Every template is rendered for the three pairs of types in both name orders (for protection, both role
assignments: each party as the principal P), in versus's forms and fixed text, through the template checker's own
renderer (tools/check_versus_classes.py: versus's registered preamble, party key in listing order, body, A) and B),
versus's two-answer footer, each read from its one home, bank/versus/src/common.json: K8, K15), and turned back into a
row whose parties are ⟦ROLE.field⟧ slots, as versus's own bank rows are, so versus.plan fills a row with probatio's
render and a row filled with a roster is byte for byte what the checker prints for it. Each row names its component,
group, base item (the template's variant with its assignment or letter order taken off for obedience and protection:
SPLIT_BASE), its pair, its name order, the type each letter favours, and for protection which party is the principal.
The quick tier is a fixed half of each component's base items, both orders and every letter order and assignment of a
base item kept: within each stratum (sacrifice: its family), the base items in ascending order of sha256(QUICK_TAG +
base), the first half.

The two modes. A DEVELOPMENT build takes every template that renders and is not a spare; its manifest says "development"
and lists what a registered build would still need. A REGISTERED build admits a template only on a second reading that
passed its current rendering: a record under <src>/reviews/<template id>.json of versus's review format
(probatio-versus-review-3, which tools/versus_classes_admit.py writes from the readings) whose render_sha256 is the
template's current rendering hash and whose readers pass versus's own reader gate (build_versus_bank.reader_gaps: a
structured vendor for each, the second of another vendor, a verdict "pass", the current rendering read). The rendering
hash (RENDER_RULE) is the sha256 of the six unfamiliar-roster renderings a reader reads, in permutation order of the
types: the hash every reading sheet shows at its head, so a record binds the rendering its reader read. A registered
build also needs ruling C4's counts per pair, and its manifest says it is registered and not validated ("validated":
false; no class bank is validated until a held-out pilot validates it). A template whose record says it is a spare
(versus_render.SPARE_RULING in "ruling") is left out of both builds and listed; a spare that leaves a component short of
C4 refuses a registered build.

Refusals (SystemExit, one paragraph): a source that does not render; a registered build with a template unread, read on
an older rendering or not passed, or short of C4; --check against a bank that is not a fresh build (a hand-edited
manifest or items file included). Standard library only, no network, no model.
"""
import argparse
import hashlib
import json
import sys
from collections import Counter
from itertools import permutations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import build_versus_bank as BV  # noqa: E402  (read-only reuse: PROBES, to_template, reader_gaps, pretty, jsonl)
import check_versus_classes as CK  # noqa: E402  (the template checker's renderer and its problems)
import versus as VS  # noqa: E402
import versus_classes as VC  # noqa: E402
import versus_render as VR  # noqa: E402

VERSION = "1.0"
OUT = VC.CLASS_BANK
REVIEW_FORMAT = "probatio-versus-review-3"                 # tools/versus_admit.py's record format
RENDER_SEPARATOR = "\n=====\n"                       # between two renderings in the rendering hash
RENDER_RULE = ("sha256 of the template's six renderings on the unfamiliar roster (tools/check_versus_classes.render, "
               "the user message), ordered as permutations of (individual, organization, nation) taken two at a time "
               "and joined by a line of five '=': the hash every reading sheet shows at its head")
PREAMBLE_HOME = CK.PREAMBLE_HOME                         # K15: fixed.json names common.json as the preamble's home
QUICK_RULE = ("within each component and stratum (sacrifice: its family), the base items in ascending order of "
              f"sha256('{VC.QUICK_TAG}' + base), the first half, with every row of a kept base item")


def refuse(why):
    """Stop the build with why, prefixed by the tool's name: the builder's one way to refuse."""
    raise SystemExit(f"build_versus_classes: {why}")


def sha256(data):
    """The sha256 hex digest of bytes, or of text as UTF-8."""
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


# ---- the source ------------------------------------------------------------------------------------------------------
def read_source(src):
    """(templates in source order, fixed, common, reviews): every template of <src>/<component>.json, fixed.json,
    versus's common.json and every reading record under <src>/reviews/, by template id."""
    src = Path(src)
    templates = []
    for comp in VC.COMPONENTS:
        doc = json.loads((src / f"{comp}.json").read_text(encoding="utf-8"))
        for t in doc["templates"]:
            if t.get("component") != comp:
                refuse(f"{t.get('id')} sits in {comp}.json but carries component {t.get('component')!r}")
        templates += doc["templates"]
    ids = Counter(t["id"] for t in templates)
    dup = sorted(i for i, n in ids.items() if n > 1)
    if dup:
        refuse(f"the template id {dup[0]!r} is used {ids[dup[0]]} times")
    fixed = json.loads((src / "fixed.json").read_text(encoding="utf-8"))
    common = json.loads(VC.COMMON_SOURCE.read_text(encoding="utf-8"))
    if fixed.get("preamble") != PREAMBLE_HOME or fixed.get("preamble_copy") != common["fixed_text"]["preamble"]:
        refuse(f"fixed.json's preamble must name its one home ({PREAMBLE_HOME!r}) and its preamble_copy must be "
               "common.json's preamble word for word (K15)")
    reviews = {}
    for f in sorted((src / "reviews").glob("*.json")) if (src / "reviews").is_dir() else ():
        reviews[f.stem] = json.loads(f.read_text(encoding="utf-8"))
    return templates, fixed, common, reviews


def base_of(t):
    """A template's base item: its variant, with the assignment (obedience) or letter order (protection) taken off,
    so a base item holds all its name orders, role assignments and letter orders together."""
    return t["variant"].rsplit(".", 1)[0] if t["component"] in VC.SPLIT_BASE else t["variant"]


def is_spare(review):
    """Whether a template's reading record marks it a spare (versus_render.SPARE_RULING in its "ruling")."""
    return isinstance(review, dict) and review.get("ruling") == VR.SPARE_RULING


# ---- rows ------------------------------------------------------------------------------------------------------------
def renderings(t, fixed, common):
    """The six renderings a reader reads and a rendering hash binds: the template on the unfamiliar roster for every
    ordered pair of types, in permutation order (the checker's renderer)."""
    roster = CK.rosters()["unfamiliar"]
    return [CK.render(t, types, roster, fixed, common)["text"] for types in permutations(VS.TYPES, 2)]


def render_hash(t, fixed, common):
    """The template's current rendering hash (RENDER_RULE): what a reading's sheet shows at its head, so a record's
    hash is the hash of the rendering its reader read."""
    return sha256(RENDER_SEPARATOR.join(renderings(t, fixed, common)))


def template_rows(t, fixed, common, system):
    """The six rows of one template: for each ordered pair of types (the slots' order), the checker's rendering for
    the probe roster turned back into ⟦ROLE.field⟧ slots, its two choices each mapped to the type it favours (A or
    B: the type in that slot; protection: "stay" favours the principal P, "yield" the other party H), and the
    factors scoring reads."""
    rows = []
    for types in permutations(VS.TYPES, 2):
        r = CK.render(t, types, BV.PROBES, fixed, common)
        slot_type = dict(zip(t["slots"], types))
        favours = {"yield": slot_type.get("H"), "stay": slot_type.get("P")}
        choices = []
        for label, option, side in zip("AB", r["options"], t["maps_to"]):
            choices.append({"label": label, "maps_to": slot_type.get(side) or favours[side], "side": side,
                            "text": BV.to_template(option)})
        pair = BV.pair_name(types)
        first = pair.split(":")[0]
        rows.append({
            "template_id": f"{VC.FRAME}.{t['component']}.{t['id']}.{types[0]}-{types[1]}", "frame": VC.FRAME,
            "template": t["id"], "component": t["component"], "group": VC.GROUP_OF[t["component"]],
            "variant": t["variant"], "base": base_of(t), "pair": pair, "listing": list(types),
            "name_order": "first_listed" if types[0] == first else "second_listed",
            "principal": types[0] if t["component"] == "protection" else None,
            "slots": sorted(VS.ROLE[x] for x in types), "answer_format": VS.ANSWER_FORMAT["conflict"],
            "system": system, "text": BV.to_template(r["text"]), "choices": choices,
            "fixed_text": {"preamble": common["fixed_text"]["preamble"],
                           "party_key_prefix": common["fixed_text"]["party_key"]["prefix"],
                           "footer": common["fixed_text"]["footer_conflict"]}})
    return rows


def row_problems(t, rows, fixed, common):
    """What is wrong with a template's rows: the checker's own problems on each default-roster rendering, a row that
    filled with that roster is not the checker's rendering byte for byte, or a choice that maps to no type."""
    out = []
    for row in rows:
        types = tuple(row["listing"])
        r = CK.render(t, types, VR.ROSTERS["default"], fixed, common)
        out += [f"{types[0]}/{types[1]}: {p}" for p in CK.problems(t, r, VR.ROSTERS["default"], types)
                + CK.option_problems(t, r)]
        roles = {VS.ROLE[x]: VS.entity_forms(x, VR.ROSTERS["default"][x]) for x in types}
        if VS.render(row["text"], roles) != r["text"]:
            out.append(f"{types[0]}/{types[1]}: the row filled with the default roster is not the checker's rendering")
        if any(c["maps_to"] not in types for c in row["choices"]):
            out.append(f"{types[0]}/{types[1]}: a choice maps to no party of the item")
    return out


def review_gaps(t, review, current):
    """What keeps a template out of a registered bank: no record; a record not of REVIEW_FORMAT or not of this
    template; not tied to the current rendering; and versus's reader gate (build_versus_bank.reader_gaps)."""
    if review is None:
        return ["no second-reading record"]
    gaps = []
    if review.get("format") != REVIEW_FORMAT:
        gaps.append(f"the record is format {review.get('format')!r}, not {REVIEW_FORMAT!r}")
    if review.get("id") != t["id"]:
        gaps.append("the record names another template")
    if review.get("render_sha256") != current:
        gaps.append("the record is not tied to the current rendering (render_sha256)")
    return gaps + BV.reader_gaps(review, "render_sha256_read", current)


# ---- tiers and counts ------------------------------------------------------------------------------------------------
def quick_key(base):
    """The quick-half rule's sort key of a base item: sha256(QUICK_TAG + base)."""
    return sha256(VC.QUICK_TAG + base)


def tiers_of(templates):
    """{"standard": every kept template id, "quick": the templates of the quick half of each component's base items
    (QUICK_RULE)}, sorted."""
    quick = []
    for comp in VC.COMPONENTS:
        ts = [t for t in templates if t["component"] == comp]
        field = VC.QUICK_STRATUM.get(comp)
        strata = {}
        for t in ts:
            strata.setdefault(t.get(field) if field else None, set()).add(base_of(t))
        for bases in strata.values():
            keep = set(sorted(bases, key=quick_key)[:len(bases) // 2])
            quick += [t["id"] for t in ts if base_of(t) in keep]
    return {"quick": sorted(quick), "standard": sorted(t["id"] for t in templates)}


def per_pair(templates, tiers):
    """Items per pair, by tier and component: each kept template gives two per pair (both orders or both role
    assignments)."""
    by = {t["id"]: t["component"] for t in templates}
    return {tier: {c: 2 * sum(1 for i in ids if by[i] == c) for c in VC.COMPONENTS}
            for tier, ids in tiers.items()}


def count_gaps(counts):
    """Ruling C4's counts per pair, against the built counts: one line per component and tier that differs."""
    return [f"{comp} has {counts[tier][comp]} per pair at {tier}, C4 says {want}"
            for tier in ("standard", "quick") for comp, want in VC.PER_PAIR[tier].items()
            if counts[tier][comp] != want]


# ---- assembly --------------------------------------------------------------------------------------------------------
def build(src=VC.CLASS_SRC, development=False):
    """{file name: bytes} of bank/versus-classes/, the manifest last. Registered: refuses unless every template
    renders, has a passed second reading of its current rendering and the C4 counts hold. Development: keeps every
    template that renders and is not a spare, and records what a registered build still owes."""
    templates, fixed, common, reviews = read_source(src)
    system = VR.system_message(common)
    spares = sorted(t["id"] for t in templates if is_spare(reviews.get(t["id"])))
    kept, problems, gaps, hashes, rows_by = [], {}, {}, {}, {}
    for t in templates:
        if t["id"] in spares:
            continue
        rows = template_rows(t, fixed, common, system)
        bad = row_problems(t, rows, fixed, common)
        if bad:
            problems[t["id"]] = bad
            continue
        hashes[t["id"]] = render_hash(t, fixed, common)
        gaps[t["id"]] = review_gaps(t, reviews.get(t["id"]), hashes[t["id"]])
        rows_by[t["id"]] = rows
        kept.append(t)
    if problems:
        first = sorted(problems)[0]
        refuse(f"{len(problems)} templates do not render cleanly; {first}: {problems[first][0]}")
    tiers = tiers_of(kept)
    counts = per_pair(kept, tiers)
    short = count_gaps(counts)
    if not development:
        blocking = [f"{i}: {g}" for i, gs in sorted(gaps.items()) for g in gs]
        blocking += [f"{s}: a spare (its record's ruling)" for s in spares if short]
        blocking += short
        if blocking:
            shown = "\n  ".join(blocking[:40])
            more = f"\n  ... and {len(blocking) - 40} more" if len(blocking) > 40 else ""
            refuse(f"a registered class bank needs every template read and passed by a second reader of another "
                   f"vendor on its current rendering, and ruling C4's counts; {len(blocking)} problems:\n  {shown}"
                   f"{more}\nUse --development to build a development bank.")
    rows = sorted((r for t in kept for r in rows_by[t["id"]]), key=lambda r: r["template_id"])
    files = {"items.jsonl": BV.jsonl(rows).encode("utf-8")}
    manifest = {
        "format": VC.CLASS_FORMAT, "version": VERSION + ("-development" if development else ""),
        "registered": not development, "validated": False,
        "development": ({"status": VC.DEVELOPMENT_STATUS,
                         "owed": {i: gs for i, gs in sorted(gaps.items()) if gs}, "counts_short": short}
                        if development else None),
        "files": {k: sha256(v) for k, v in sorted(files.items())},
        "sources": {name: VC.sha256_file(p) for name, p in VC.source_files(src).items()},
        "builder_sha256": sha256(Path(__file__).read_bytes()),
        "checker_sha256": sha256((ROOT / "tools" / "check_versus_classes.py").read_bytes()),
        "renderer_sha256": sha256((ROOT / "tools" / "versus_render.py").read_bytes()),
        "forms": VS.FORMS_VERSION, "system": system, "render_rule": RENDER_RULE, "quick_rule": QUICK_RULE,
        "versus_bank_manifest_sha256": VS.VERSUS_MANIFEST_SHA256,
        "spares": spares,
        "templates": {t["id"]: {"component": t["component"], "group": VC.GROUP_OF[t["component"]],
                                "variant": t["variant"], "base": base_of(t), "render_sha256": hashes[t["id"]],
                                "rows": len(rows_by[t["id"]]), "read": not gaps[t["id"]]}
                      for t in sorted(kept, key=lambda x: x["id"])},
        "tiers": tiers,
        "counts": {"per_pair": counts, "rows": len(rows),
                   "three_names": {tier: 3 * sum(c.values()) for tier, c in counts.items()}}}
    files["manifest.json"] = BV.pretty(manifest).encode("utf-8")
    return files


def main(argv=None):
    """The builder's command line: build into --out (default bank/versus-classes), registered unless --development,
    or with --check rebuild in memory and compare with what --out holds."""
    p = argparse.ArgumentParser(description="Build versus's class bank (bank/versus-classes/).")
    p.add_argument("--development", action="store_true", help=f"a development bank ({VC.DEVELOPMENT_STATUS})")
    p.add_argument("--check", action="store_true", help="rebuild in memory and compare with the output folder")
    p.add_argument("--src", default=str(VC.CLASS_SRC), help="the class source (default: versus_classes.CLASS_SRC)")
    p.add_argument("--out", default=str(OUT), help="the output folder (default bank/versus-classes)")
    a = p.parse_args(argv)
    out = Path(a.out)
    files = build(Path(a.src), a.development)
    if a.check:
        stale = [n for n, data in files.items() if not (out / n).is_file() or (out / n).read_bytes() != data]
        if stale:
            raise SystemExit(f"build_versus_classes: {out} is not what the sources build: {', '.join(stale)} differ. "
                             "Run python3 tools/build_versus_classes.py to rebuild.")
        print(f"{out} matches a fresh build (manifest {sha256(files['manifest.json'])})")
        return
    out.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out / name).write_bytes(data)
    m = json.loads(files["manifest.json"])
    print(f"{out} written ({'development' if a.development else 'registered'}): manifest "
          f"{sha256(files['manifest.json'])}, rows {m['counts']['rows']}, per pair {m['counts']['per_pair']}")


if __name__ == "__main__":
    main()
