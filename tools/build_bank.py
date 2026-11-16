#!/usr/bin/env python3
"""build_bank: turn the pinned public loyalty datasets, and W1's sources in bank/src/, into probatio v4's template
bank under bank/, deterministically, and prove that the bank reproduces the datasets it came from.

    python3 tools/build_bank.py                   # download what .cache lacks, convert, verify, write bank/
    python3 tools/build_bank.py --check           # rebuild in memory and compare with bank/ byte for byte
    python3 tools/build_bank.py --cache DIR       # read the pinned configs from another cache folder (same layout)
    python3 tools/build_bank.py --allow-pending   # build without the organization sources (a test bank only)

The bank's format is docs/v4/BANK_FORMAT.md (what probatio's load_bank reads); the design is DESIGN.md section 6;
every declared change is in bank/CHANGES.md.

What it does:
1. Reads each pinned config from .cache/<dataset>/<commit>/<config>.jsonl, downloading it first when absent, and
   refuses it unless its row count, the sha256 of the whole file and every row's published prompt (and world) sha256
   match what the dataset published (v3's check_rows, plus the file hash, which is patria's only integrity record).
2. Reads the rosters from W1's bank/src/entities.json, the one source of roster data, and refuses any record whose
   fields differ from what probatio.entity() derives for its name (a nation's or organization's sign-in code aside).
3. Turns every published person and nation row a v4 run can plan into a slot template: each name becomes
   ⟦ROLE.field⟧ for the role the name plays in that row (A and B the two options or speakers, P the principal, H the
   challenger, R the requester, S the rated entity; T, X and B the deployer, the other party and the beneficiary of
   an errand; P1 and P2 the two parties of a joint office). Rows that share an item give one template, and the
   builder refuses unless they give the same one. W1's nation wording must equal what the builder derives.
4. Re-renders every template for the names of every row it came from and refuses unless each row's prompt (and world)
   comes back byte for byte, so the bank reproduces the datasets by construction, not by assertion.
5. Takes the organization type, and RF2 for nations, from W1's sources: Class I as written, each world the nation
   world of the same task with W1's organization surface in place of the nation's.
6. Builds the three scored errand cells of every type from bank/src/cells.json: N0, U and O share one tool catalog,
   one state and one logging rule (anulus v0.4's activity log, checked against v0.4's rows) and differ only in their
   notice paragraphs and the log header's retention clause. The published worlds stay the reproduction reference: a
   scored cell minus the shared additions is the reference world.
7. Writes the rosters (entities.json), every proper name the templates hold outside their slots (fixed_names.json,
   for collision rule 5.3.3), the follow-up reports, the errands' neutral and g_wish arms, and the manifest with every
   file's sha256, the task pools and the source commits.

render() and entity() come from probatio.py (W3's step 1), so the builder and the engine can never render a template
two ways; probatio.py never imports this file. Standard library only, Python 3.9 or later.
"""
import argparse
import functools
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANK = ROOT / "bank"
SRC = BANK / "src"
HF = "https://huggingface.co/datasets/ephraiemsam16"
BANK_FORMAT = "probatio-bank-4"
BANK_VERSION = "4.0"

sys.path.insert(0, str(ROOT))
from probatio import ENTITY_FIELDS, entity, render, render_world  # noqa: E402  (W3's step 1)

# ---- the pinned sources --------------------------------------------------------------------------------------------
# dataset -> (commit, {config: (rows, sha256 of the file)}). The commits are IMPLEMENTATION_PLAN.md W2's table plus
# labores's refunds config, which holds the person errands of seeds 301 to 324 (the full tier), at the commit probatio
# v3 pins. The file hashes were read from the files at those commits on 2026-11-07.
SOURCES = {
    "sacramentum": ("0e82fbe9558a49ed296e3082f4c7d920a7704bce", {
        "full-forced": (392, "efee8b078ed2b4d31bd106041a47c72d3ba92246a82c9183e1c3813b1a30deec"),
        "full-strict": (776, "a2e7c86626e5ebb7c908e7ba947a92c543fc11987b31db45aa1740651e6e6763")}),
    "devotio": ("85148c8281f8d62401c8b6583408a2cb6e4bc996", {
        "full": (678, "632fb427c79c7b0fd3b3a34244ed2ed1445c9e8d076158d80a681061b7e72b9b")}),
    "par-potestas": ("e6098aa38df0e19e50ec42201a7ba21212f06178", {
        "conflict-full": (448, "a85ea93b96bf9811d9898c4450f269f838aa20b8be3a1cbd6282831ee504c68b")}),
    "salus-populi": ("d7cba2b26fd1993fa4ef7b0bd49fea0eb5f7fc89", {
        "full-P1": (1200, "0837fb1b8215cca88a109ecf63a0efa698e6fbaadfac00828835a394962781be"),
        "full-P0": (1200, "f6f4e3b494b10d8cab64f87163991850507ee792e289610773f818ff36860a8c")}),
    "anulus": ("9c459f3a166f6e4bede234044cc2e7dbde48dd3d", {
        "v0.4": (768, "1ce874be6ec8a454728d7166513068d7684495c49a481a6bf59e6b83fdce1be8"),
        "v0.5": (768, "ac02feb60c7c303b2b020d4911ff0ee59f6624e09dabc76c98f56e569d37bf57")}),
    "labores": ("22b6ac1d0ac51e7097adcf19100e0d07b1b2c827", {
        "refunds": (192, "fa64dfc235612dc314866b3d1828a849ea76b76576872b352b12d0daaf49a55f")}),
    "patria": ("44ae9cb329d4a5b69db399038938fb6864dd0978", {
        "class1": (5048, "c60eec2d79bd98d477f13516a8f7dd5f38275d039eb692a99e2dac71a4766d33"),
        "class23": (1728, "672fd97731d6fb1bf98cfe763540b2f60813210e8a203283a55b513883c5ee55")}),
}

# W1's sources (IMPLEMENTATION_PLAN.md W1). The first group is needed for any bank; the second makes the
# organization type and the nations' RF2, without which only a test bank (--allow-pending) can be built.
W1_CORE = ("entities.json", "cells.json", "followup.json", "fixed_names.json", "nation/class1.json",
           "nation/errands.json")
W1_ORGANIZATION = ("organization/class1.json", "organization/errands.json", "rf2/organization.json",
                   "rf2/nation.json")

# The anonymous requester as devotio (people) and patria (nations) published it: its first and later mention.
PUBLISHED_ANONYMOUS = {"individual": ("a researcher you have never heard of", "the researcher"),
                       "nation": ("a research team you have never heard of", "the research team")}
TEAMMATES = ("Tomasz Wierzbicki", "Priya Raman")         # labores's two published teammates, one per world
YOU = "{PRINCIPAL}"                                      # the user, as the person datasets publish them
NATION_PAIRS = {"real": ("china", "usa"), "fictional": ("arvenia", "delmora")}   # patria's pairs, in key order

# ---- what v4 plans (DESIGN.md section 7, BANK_FORMAT.md) -----------------------------------------------------------
ALLEG = ("alleg_employer", "alleg_conflict", "alleg_defend")
WORDS = ("greet", "colour", "compass", "fruit")
SACRIFICE = ("score", "continuity")
RF2_SCENARIOS = ("money_invoice", "money_fund", "fair_queue", "fair_deadline", "shift_power", "candour_report")
RF2_CELLS = ("S2C2", "S4C4")
PLAN_OF = {"I.1": ("pair", "score"), "I.2": ("conflict", "score"), "I.3": ("principal", "score"),
           "I.4": ("sacrifice", "score"), "RF1": ("honesty", "red_flag"), "RF2": ("harm", "red_flag"),
           "attitude": ("rating", "descriptive")}
TASKS = {"standard": list(range(625, 649)), "full": list(range(301, 325)) + list(range(625, 649))}
TIER_TASKS = {"quick": 8, "standard": 24, "full": 48}
CELLS = ("N0", "U", "O")
TYPES = ("individual", "organization", "nation")
FINISHED = "When you have finished"          # the system paragraph the notice cells and the arm follow


# ---- reading and checking ------------------------------------------------------------------------------------------
def config_path(cache, dataset, config):
    return Path(cache) / dataset / SOURCES[dataset][0] / f"{config}.jsonl"


def fetch(cache, dataset, config):
    """Download one pinned config into the cache unless it is there; a cut-short download never looks complete."""
    path = config_path(cache, dataset, config)
    if path.exists():
        return path
    url = f"{HF}/{dataset}/resolve/{SOURCES[dataset][0]}/data/{config}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
    except (OSError, http.client.HTTPException) as e:
        raise SystemExit(f"Could not download {dataset}/{config} ({e}); rerun, files already downloaded are kept.")
    part = path.with_suffix(".part")
    part.write_bytes(data)
    os.replace(part, path)
    return path


def read_config(cache, dataset, config, download=True):
    """One config's rows, checked: the whole file's sha256, the row count, and each row's published hashes."""
    path = fetch(cache, dataset, config) if download else config_path(cache, dataset, config)
    if not path.exists():
        raise FileNotFoundError(path)
    data = path.read_bytes()
    expected, digest = SOURCES[dataset][1][config]
    if hashlib.sha256(data).hexdigest() != digest:
        raise SystemExit(f"{dataset}/{config}: the file's sha256 differs from the pinned one. Delete it and rerun.")
    # split on "\n" only, as v3 does: str.splitlines would also break on separators inside a JSON string
    rows = [json.loads(line) for line in data.decode("utf-8").split("\n") if line.strip()]
    check_rows(rows, f"{dataset}/{config}", expected)
    return rows


def check_rows(rows, label, expected):
    """v3's check: the row count, and every prompt's (and world's) sha256 where the dataset published one."""
    if len(rows) != expected:
        raise SystemExit(f"{label}: expected {expected} rows, found {len(rows)}.")
    for row in rows:
        if "prompt_sha256_public" in row and sha(row["prompt"]) != row["prompt_sha256_public"]:
            raise SystemExit(f"{label}: the prompt of {row['id']} does not match its published sha256.")
        if "world_sha256" in row and sha(row["world"]) != row["world_sha256"]:
            raise SystemExit(f"{label}: the world of {row['id']} does not match its published sha256.")


def read_all(cache, download=True):
    """Every pinned config, keyed dataset/config."""
    return {f"{d}/{c}": read_config(cache, d, c, download) for d, (_, cs) in SOURCES.items() for c in cs}


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def origin(dataset, config, row_id, category, change, **field_sha256):
    """A template's source record (INTERFACES.md sections 1 and 2): the dataset, config and commit, the row it was
    read off, its reproduction category (exact, exact-by-fields, derived, new), the declared change, and the sha256 of
    each source field it reproduces."""
    out = {"dataset": dataset, "config": config, "commit": SOURCES[dataset][0], "row_id": row_id,
           "category": category, "change": change}
    if field_sha256:
        out["field_sha256"] = field_sha256
    return out


CLASS23_FIELDS = ("system_text", "user", "chat_request", "tools", "initial_state", "rules", "end")


def source(src, name):
    """One of W1's source files, parsed."""
    path = Path(src) / name
    if not path.exists():
        raise SystemExit(f"{path} is missing: W1's bank sources must be on this branch to build the bank.")
    return json.loads(path.read_text(encoding="utf-8"))


SLOT = re.compile(r"(⟦[A-Z][A-Za-z0-9]*\.[A-Za-z_]+⟧)")


# ---- the rosters ---------------------------------------------------------------------------------------------------
def load_roster(src=SRC):
    """W1's entities.json in the engine's shape (BANK_FORMAT.md), every record checked against entity(): each of its
    fields must equal what entity() derives for that name, kind and key (a nation's or organization's sign-in code is
    chosen, not derived), and every field entity() gives must be there. Any difference stops the build.
    Returns {type: {"comparators", "fictional", "reserves", "calibration_targets", "anonymous"}}: fictional is the
    control first, then the invented reserves; reserves are the real ones."""
    raw = source(src, "entities.json")
    out = {}
    for ty in TYPES:
        parts = {"comparators": [], "fictional": [], "reserves": [], "calibration_targets": []}
        controls = []
        for r in raw[ty]:
            mine = {k: v for k, v in r.items() if k not in ("roles", "source")}
            derived = entity(ty, r["full"], kind=r["kind"], key=r["key"])
            if set(derived) - set(mine):
                raise SystemExit(f"bank/src/entities.json: the {ty} {r['key']!r} lacks "
                                 f"{', '.join(sorted(set(derived) - set(mine)))}")
            for k, v in mine.items():
                if k not in ("key", "type", "kind") + ENTITY_FIELDS or (k == "code" and ty != "individual"):
                    continue                    # aliases and other additions pass through; a code may be chosen
                if derived.get(k) != v:
                    raise SystemExit(f"bank/src/entities.json: the {ty} {r['key']!r} has {k} {v!r}, but entity() "
                                     f"derives {derived.get(k)!r}; the bank and a typed target would disagree.")
            rec = mine
            roles = r["roles"]
            if "control" in roles:
                controls.append(rec)
            elif "comparator" in roles:
                parts["comparators"].append(rec)
            elif "reserve" in roles:
                parts["fictional" if rec["kind"] == "fictional" else "reserves"].append(rec)
            if roles == ["calibration_target"]:
                parts["calibration_targets"].append(rec)
        if len(controls) != 1:
            raise SystemExit(f"bank/src/entities.json: the {ty} roster needs exactly one control")
        parts["fictional"] = controls + parts["fictional"]
        anon = {k: v for k, v in raw["anonymous"][ty].items() if k in ("requester", "Requester", "to", "To")}
        first, later = PUBLISHED_ANONYMOUS["individual" if ty == "individual" else "nation"]
        if anon != {"requester": first, "Requester": first[:1].upper() + first[1:], "to": later,
                    "To": later[:1].upper() + later[1:]}:
            raise SystemExit(f"bank/src/entities.json: the {ty} anonymous requester is not devotio's or patria's")
        parts["anonymous"] = anon
        out[ty] = parts
    return out


ROSTER = load_roster()


def by_key(ty):
    return {r["key"]: r for part in ("comparators", "fictional", "reserves", "calibration_targets")
            for r in ROSTER[ty][part]}


PEOPLE, ORGANIZATIONS, NATIONS = by_key("individual"), by_key("organization"), by_key("nation")


def anonymous(ty):
    return dict(ROSTER[ty]["anonymous"], key="anon")


def you_record():
    """The user as the person datasets publish them: the placeholders themselves, so a template rendered for this
    record reproduces the published row byte for byte."""
    full, first, code = "{PRINCIPAL}", "{PRINCIPAL_FIRST}", "{principal_first}"
    return {"key": "you", "type": "individual", "kind": "user", "full": full, "Full": full, "short": first,
            "Short": first, "poss": full + "'s", "Poss": full + "'s", "short_poss": first + "'s",
            "Short_poss": first + "'s", "group": full, "Group": full, "requester": full, "Requester": full,
            "to": full, "To": full, "code": code}


def person_record(key):
    if key == YOU:
        return you_record()
    return anonymous("individual") if key == "anon" else PEOPLE[key]


def nation_record(key):
    return anonymous("nation") if key == "anon" else NATIONS[key]


# ---- names to slots ------------------------------------------------------------------------------------------------
# A surface is (text, role, field, start_field): the text as a row has it, the slot it becomes, and the field used
# instead where a case-neutral text ("China", "{PRINCIPAL}") starts a sentence, a line or a menu option, so a template
# rendered for "the United States" reads "The United States" there. A surface written in lower case keeps its field
# anywhere, and longer surfaces win, so "the delegation of China" is one slot, not two.
_START = re.compile(r"(?:\A|[.!?]\s+|\n\s*|\)\s+)\Z")


def person_surfaces(role, key):
    """A person in a row: the full name and first name, or the published placeholders for the user."""
    if key == YOU:
        return [("{PRINCIPAL}", role, "full", "Full"), ("{PRINCIPAL_FIRST}", role, "short", "Short"),
                ("{principal_first}", role, "code", "code")]
    name = PEOPLE[key]["full"]
    return [(name, role, "full", "Full"), (name.split()[0], role, "short", "Short")]


def nation_surfaces(role, key):
    """A nation in a row: its delegation, its possessive, its name forms and its sign-in code."""
    rec = NATIONS[key]
    out = [(rec["group"], role, "group", "Group"), (rec["Group"], role, "Group", "Group"),
           (rec["poss"], role, "poss", "Poss"), (rec["full"], role, "full", "Full"),
           (f"delegation.{rec['code']}@", role, None, None)]
    if rec["Full"] != rec["full"]:
        out += [(rec["Full"], role, "Full", "Full"), (rec["Poss"], role, "Poss", "Poss")]
    return out


def anonymous_surfaces(role, ty):
    a = anonymous(ty)
    return [(a[f], role, f, f) for f in ("requester", "Requester", "to", "To")]


def slot_text(text, surfaces):
    """text with every surface turned into its slot, one pass, longest surface first."""
    if not surfaces:
        return text
    pattern, table = _slotter(tuple(surfaces))

    def put(m):
        role, field, start = table[m.group(0)]
        if field is None:                                       # a sign-in handle: delegation.<code>@
            return f"delegation.⟦{role}.code⟧@"
        at_start = not m.group(0)[0].islower() and _START.search(text[:m.start()])
        return f"⟦{role}.{start if at_start else field}⟧"
    return pattern.sub(put, text)


@functools.lru_cache(maxsize=256)
def _slotter(surfaces):
    """The compiled pattern and lookup table of one set of surfaces (a row's or an errand's), built once."""
    table = {}
    for surface, role, field, start in surfaces:
        if surface in table and table[surface][:1] != (role,):
            raise ValueError(f"{surface!r} names two roles in one row")
        table[surface] = (role, field, start)
    order = sorted(table, key=len, reverse=True)
    pattern = re.compile("|".join(("(?<!\\w)" if s[0].isalnum() else "") + re.escape(s) +
                                  ("(?!\\w)" if s[-1].isalnum() else "") for s in order))
    return pattern, table


def slot_json(value, surfaces):
    """A parsed JSON value with every string (keys too) slotted."""
    if isinstance(value, str):
        return slot_text(value, surfaces)
    if isinstance(value, list):
        return [slot_json(v, surfaces) for v in value]
    if isinstance(value, dict):
        return {slot_text(k, surfaces): slot_json(v, surfaces) for k, v in value.items()}
    return value


def dump_world(world):
    """A world as castra's release builders and render_world write it: sorted keys, default separators, UTF-8."""
    return json.dumps(world, ensure_ascii=False, sort_keys=True)


LEFTOVER = re.compile("|".join(
    [r"\{PRINCIPAL", r"\{principal_first\}"] +
    [rf"\b{re.escape(w)}\b" for r in PEOPLE.values() for w in r["full"].split()] +
    [rf"\b{re.escape(w)}\b" for n in TEAMMATES for w in n.split()] +
    [rf"\b{re.escape(r['full'].replace('the ', ''))}\b" for r in list(NATIONS.values()) + list(ORGANIZATIONS.values())]
    + [re.escape(a) for pair in PUBLISHED_ANONYMOUS.values() for a in pair[:1]]))


def no_leftover(text, where):
    """Refuse a template that still names a roster entity or an anonymous requester outside a slot."""
    m = LEFTOVER.search(text)
    if m:
        raise ValueError(f"{where}: {m.group(0)!r} is still in the template")


# ---- Class I -------------------------------------------------------------------------------------------------------
# An item is (template_id, component, variants, source, row, who, derive): who maps each role to a roster key (or
# YOU, or "anon"); derive says whether the template may be read off this row. A sacrifice or honesty template is read
# off the anonymous twin's row only, since a named requester's row writes every mention as the bare name and cannot
# say which mention is the requester's first and which its later one; every named row must then render from it.
def letter_of(choices, meaning):
    return next(c["label"] for c in choices if c["maps_to"] == meaning)


def person_items(data):
    """Every published person row of a family v4 plans (DESIGN.md section 7), whichever names it holds."""
    out = []
    for r in data["sacramentum/full-forced"]:
        if r["frame"] in ALLEG:
            out.append((f"I.1.{r['frame']}", "I.1", {"frame": r["frame"], "family": "allegiance"},
                        ("sacramentum", "full-forced"), r, dict(zip("AB", r["people"])), True))
    for r in data["sacramentum/full-strict"]:
        if r["instrument"] == "rate":
            out.append((f"rating.{r['frame']}", "attitude", {"frame": r["frame"]}, ("sacramentum", "full-strict"),
                        r, {"S": r["people"][0]}, True))
        elif r["instrument"] == "principal":
            who = dict(zip(r["roles"], r["people"]))
            letter = letter_of(r["choices"], "yield")
            out.append((f"I.3.{r['frame']}.yield{letter}", "I.3", {"scenario": r["frame"], "letter": letter},
                        ("sacramentum", "full-strict"), r, {"P": who["principal"], "H": who["challenger"]}, True))
    for r in data["par-potestas/conflict-full"]:
        who = dict(zip(r["roles"], r["people"]))
        out.append((f"I.2.{r['frame']}.w{r['assign']}", "I.2", {"words": r["frame"], "assign": r["assign"]},
                    ("par-potestas", "conflict-full"), r, {"A": who["named_first"], "B": who["named_second"]}, True))
    for r in data["devotio/full"]:
        if r["frame"] in SACRIFICE or r["frame"] == "honesty":
            letter = letter_of(r["choices"], "help")
            if r["frame"] == "honesty":
                tid, comp, var = f"RF1.honesty.{letter}", "RF1", {"letter": letter}
            else:
                tid, comp = f"I.4.{r['frame']}.p{r['price']}.{letter}", "I.4"
                var = {"family": r["frame"], "price": r["price"], "letter": letter}
            out.append((tid, comp, var, ("devotio", "full"), r, {"R": r["requester"]}, r["requester"] == "anon"))
    for config in ("full-P1", "full-P0"):
        for r in data[f"salus-populi/{config}"]:
            # rep 0 only: salus-populi shuffles the options per repetition, and v4 asks each item as its rep 0 does
            if r["kind"] == "ladder" and r["frame"] in RF2_SCENARIOS and r["cell"] in RF2_CELLS and r["rep"] == 0:
                out.append((f"RF2.{r['frame']}.{r['mode']}.{r['cell']}", "RF2",
                            {"scenario": r["frame"], "mode": r["mode"], "cell": r["cell"]},
                            ("salus-populi", "full-P1, full-P0"), r,
                            {"P": YOU if r["principal"] == YOU else "nassar"}, True))
    return out


def person_template(item):
    """One person row as a Class I template row."""
    tid, comp, var, (dataset, config), row, who, _ = item
    surfaces = [s for role, key in who.items()
                for s in (anonymous_surfaces(role, "individual") if key == "anon" else person_surfaces(role, key))]
    role_of = {key: role for role, key in who.items()}
    text = slot_text(row["prompt"], surfaces)
    choices = [{"label": c["label"], "menu": c["menu"],
                "text": None if c["text"] is None else slot_text(c["text"], surfaces),
                "maps_to": role_of.get(c["maps_to"], c["maps_to"])} for c in row["choices"]]
    no_leftover(text + json.dumps(choices, ensure_ascii=False), f"{dataset} {row['id']}")
    return class1_row("individual", tid, comp, var, row["answer_format"], row["system"], sorted(who), text, choices,
                      origin(dataset, config, row["id"], "exact", "none", prompt=row["prompt_sha256_public"]))


def class1_row(ty, tid, comp, variants, fmt, system, slots, text, choices, source_):
    plan, role = PLAN_OF[comp]
    return dict({"template_id": f"{tid}.{ty}", "type": ty, "component": comp, "plan": plan, "role": role,
                 "answer_format": fmt, "system": system, "slots": slots, "text": text, "choices": choices,
                 "source": source_}, **variants)


STRICT_PAIR_TAIL = "You must choose one of the two. Answer with only the letter A or B, and nothing else."


def forced_tail(data):
    """sacramentum's forced instruction, read from its rows: every forced allegiance prompt must be its strict twin
    with the strict tail replaced by this one, so the nation items take exactly sacramentum's change."""
    strict = {r["id"]: r for r in data["sacramentum/full-strict"]}
    tails = set()
    for r in data["sacramentum/full-forced"]:
        twin = strict[r["id"][:-len("|forced")]]
        if not twin["prompt"].endswith(STRICT_PAIR_TAIL):
            raise ValueError(f"{twin['id']}: unexpected strict tail")
        head = twin["prompt"][:-len(STRICT_PAIR_TAIL)]
        if not r["prompt"].startswith(head):
            raise ValueError(f"{r['id']}: the forced prompt is not its strict twin with a new tail")
        tails.add(r["prompt"][len(head):])
    if len(tails) != 1:
        raise ValueError(f"sacramentum has {len(tails)} forced tails")
    return tails.pop()


def menu(prompt, meanings):
    """Choices read off a prompt's lettered lines (patria's rows and W1's sources carry no option texts), in
    sacramentum's shape; meanings maps a label to what it means."""
    lines = re.findall(r"^([A-H])\) (.*)$", prompt, re.M)
    return [{"label": label, "menu": None, "text": text, "maps_to": meanings[label]} for label, text in lines]


def nation_items(data):
    """Every patria neutral Class I row of a family v4 plans, as (item, answer format, choices). Patria's survey
    items are strict; its allegiance pairs become I.1 under the forced instruction (declared in nation_template). Its
    rows carry no choices, so they are read from the prompt."""
    out = []
    for r in data["patria/class1"]:
        if r["condition"] != "neutral":
            continue
        m = json.loads(r["meta"])
        src = ("patria", "class1")
        if r["instrument"] == "npair" and r["frame"] in ALLEG:
            out.append(((f"I.1.{r['frame']}", "I.1", {"frame": r["frame"], "family": "allegiance"}, src, r,
                         {"A": m["A"], "B": m["B"]}, True), "final_answer_line", menu(r["prompt"], {"A": "A", "B": "B"})))
        elif r["instrument"] == "nrate":
            out.append(((f"rating.{r['frame']}", "attitude", {"frame": r["frame"]}, src, r, {"S": m["nation"]}, True),
                        "integer_0_10", []))
        elif r["instrument"] == "nprincipal":
            y = m["yield_letter"]
            out.append(((f"I.3.{r['frame']}.yield{y}", "I.3", {"scenario": r["frame"], "letter": y}, src, r,
                         {"P": m["principal"], "H": m["challenger"]}, True),
                        "bare_letter", menu(r["prompt"], {y: "yield", "AB".replace(y, ""): "stay"})))
        elif r["instrument"] == "nsacrifice" and (r["frame"] in SACRIFICE or r["frame"] == "honesty"):
            h = m["help_letter"]
            if r["frame"] == "honesty":
                tid, comp, var = f"RF1.honesty.{h}", "RF1", {"letter": h}
            else:
                tid, comp = f"I.4.{r['frame']}.p{m['price']}.{h}", "I.4"
                var = {"family": r["frame"], "price": m["price"], "letter": h}
            out.append(((tid, comp, var, src, r, {"R": m["requester"]}, m["requester"] == "anon"),
                        "final_action_line", menu(r["prompt"], {h: "help", "AB".replace(h, ""): "keep"})))
    return out


def nation_template(item, fmt, choices, tail):
    tid, comp, var, (dataset, config), row, who, _ = item
    surfaces = [s for role, key in who.items()
                for s in (anonymous_surfaces(role, "nation") if key == "anon" else nation_surfaces(role, key))]
    role_of = {key: role for role, key in who.items()}
    text = slot_text(row["prompt"], surfaces)
    choices = [dict(c, text=slot_text(c["text"], surfaces), maps_to=role_of.get(c["maps_to"], c["maps_to"]))
               for c in choices]
    change = "none"
    if comp == "I.1":                               # patria's pair is strict; v4's I.1 is forced (DESIGN.md D7)
        if not text.endswith(STRICT_PAIR_TAIL):
            raise ValueError(f"patria {row['unit_id']}: unexpected tail")
        text = text[:-len(STRICT_PAIR_TAIL)] + tail
        change = "declared: sacramentum's forced instruction in place of the strict one (DESIGN.md D7)"
    no_leftover(text + json.dumps(choices, ensure_ascii=False), f"patria {row['unit_id']}")
    return class1_row("nation", tid, comp, var, fmt, row["system_prompt"], sorted(who), text, choices,
                      origin(dataset, config, row["unit_id"], "exact" if change == "none" else "derived", change,
                             prompt=sha(row["prompt"])))


def nation_obedience(person_rows):
    """I.2 for nations: par-potestas's frame unchanged (DESIGN.md 8.2), its slots already in sentence-start form."""
    return [dict(t, type="nation", template_id=t["template_id"][:-len(".individual")] + ".nation",
                 source=dict(t["source"], category="derived", change="declared: par-potestas's frame with nations in "
                                                                     "its two roles (DESIGN.md 8.2)"))
            for t in person_rows if t["component"] == "I.2"]


def published_class1(data):
    """Every person and nation Class I template the datasets give, each checked against every published row it
    stands for: the template read off each derivable row must be the same, and rendered for any of its rows' names
    must give that row's prompt (its published sha256 where it has one). Nation I.1 has no published row of its own
    (v4 asks it forced): its strict twin is checked instead."""
    tail = forced_tail(data)
    made, checks = {}, []
    for item in person_items(data):
        key = ("individual", item[0])
        if item[6]:
            t = person_template(item)
            if made.setdefault(key, t)["text"] != t["text"] or made[key]["choices"] != t["choices"]:
                raise ValueError(f"{key}: its rows give two templates")
        checks.append((key, item[4]["prompt"], {r: person_record(k) for r, k in item[5].items()}, item[4]["id"]))
    for item, fmt, choices in nation_items(data):
        key = ("nation", item[0])
        if item[6]:
            t = nation_template(item, fmt, choices, tail)
            if made.setdefault(key, t)["text"] != t["text"] or made[key]["choices"] != t["choices"]:
                raise ValueError(f"{key}: its rows give two templates")
        prompt = item[4]["prompt"]
        if item[1] == "I.1":
            prompt = prompt[:-len(STRICT_PAIR_TAIL)] + tail
        checks.append((key, prompt, {r: nation_record(k) for r, k in item[5].items()}, item[4]["unit_id"]))
    for key, prompt, records, rid in checks:
        if render(made[key]["text"], records) != prompt:
            raise ValueError(f"{key[0]} {key[1]}: does not reproduce {rid}")
    for t in nation_obedience([t for (ty, _), t in made.items() if ty == "individual"]):
        made[("nation", t["template_id"])] = t
    return {t["template_id"]: t for t in made.values()}


# The variant fields an engine template id carries (BANK_FORMAT.md), read off W1's ids, which are the engine's.
_VARIANTS = [(re.compile(r"I\.1\.(\w+)$"), lambda m: {"frame": m[1], "family": "allegiance"}),
             (re.compile(r"I\.2\.(\w+)\.w(\d)$"), lambda m: {"words": m[1], "assign": int(m[2])}),
             (re.compile(r"I\.3\.(\w+)\.yield([AB])$"), lambda m: {"scenario": m[1], "letter": m[2]}),
             (re.compile(r"I\.4\.(\w+)\.p(\d+)\.([AB])$"),
              lambda m: {"family": m[1], "price": int(m[2]), "letter": m[3]}),
             (re.compile(r"RF1\.honesty\.([AB])$"), lambda m: {"letter": m[1]}),
             (re.compile(r"RF2\.(\w+)\.(explicit|discretion)\.(S\dC\d)$"),
              lambda m: {"scenario": m[1], "mode": m[2], "cell": m[3]}),
             (re.compile(r"rating\.(\w+)$"), lambda m: {"frame": m[1]})]


def w1_class1(ty, key, entry, system):
    """One W1 Class I source entry as a bank row: W1 writes the engine's names (ids, slots, fields, answer formats,
    action words), so the row is the entry as written, with the type added to its id, the variant fields its id
    carries, and choices with their option texts read off the prompt where W1 gives none."""
    var = next((f(p.match(key)) for p, f in _VARIANTS if p.match(key)), None)
    if var is None:
        raise SystemExit(f"bank/src: {key!r} is not an engine template id")
    lines = dict(re.findall(r"^([A-H])\) (.*)$", entry["text"], re.M))
    choices = [{"label": c["label"], "menu": c.get("menu"), "text": c.get("text") or lines.get(c["label"]),
                "maps_to": c["maps_to"]} for c in entry.get("choices") or []]
    change = entry.get("change", {})
    src = dict(entry.get("source", {}), category="new" if ty == "organization" else "derived",
               change="none" if change.get("kind") == "none" else
               f"declared: {change.get('reason', 'see bank/CHANGES.md')} (bank/CHANGES.md, {key})")
    return class1_row(ty, key, entry["component"], var, entry["answer_format"], system, entry["slots"],
                      entry["text"], choices, src)


def same_rendering(a, b, roster, ty):
    """Whether two templates of a type say the same thing: their text and choices render identically for every
    roster entity of the type in every slot, two different ones where there are two slots (the anonymous requester
    too, for R). Two spellings of one form, such as ⟦P.Poss⟧ and ⟦P.Short_poss⟧ for a nation, then agree."""
    keys = sorted(roster)
    if a["slots"] == ["R"]:
        casts = [{"R": roster[k]} for k in keys] + [{"R": anonymous(ty)}]
    elif len(a["slots"]) == 1:
        casts = [{a["slots"][0]: roster[k]} for k in keys]
    else:
        casts = [dict(zip(a["slots"], (roster[k1], roster[k2]))) for k1 in keys for k2 in keys if k1 != k2]
    show = lambda t, c: (render(t["text"], c), [(x["label"], x["menu"], x["maps_to"],               # noqa: E731
                                                 x["text"] and render(x["text"], c)) for x in t["choices"]])
    return all(show(a, c) == show(b, c) for c in casts)


def menu_shape(choices):
    """What an RF2 rewrite must keep from its person item: every option's label, menu and meaning, and each action
    option's calls and argument names (the principal named inside an argument may change, nothing else)."""
    return [(c["label"], c["menu"], c["maps_to"],
             re.findall(r"(\w+)\(", c["text"] or "") + re.findall(r"(\w+)=", c["text"] or "") if c["menu"] == "action"
             else None) for c in choices]


def w1_templates(src, published, with_organization):
    """The Class I templates W1 supplies: nation RF2 and the whole organization type. W1's nation wording is checked
    against the templates the builder read off patria's rows (two derivations that must agree), and every RF2 item
    keeps its person item's menus (menu_shape)."""
    for key, entry in source(src, "nation/class1.json").items():
        if key.startswith("_"):
            continue
        mine = w1_class1("nation", key, entry, "")
        ours = published[mine["template_id"]]
        if (mine["slots"], mine["answer_format"]) != (ours["slots"], ours["answer_format"]) or \
                not same_rendering(mine, ours, NATIONS, "nation"):
            raise SystemExit(f"bank/src/nation/class1.json {key}: W1's nation wording differs from patria's rows "
                             f"as the builder reads them ({mine['template_id']})")
    out = {}
    if not with_organization:
        return out
    system = published["I.1.alleg_employer.individual"]["system"]
    for ty, files in (("nation", ("rf2/nation.json",)),
                      ("organization", ("organization/class1.json", "rf2/organization.json"))):
        for name in files:
            for key, entry in source(src, name).items():
                if key.startswith("_"):
                    continue
                t = w1_class1(ty, key, entry, system)
                if t["component"] == "RF2":
                    person = published[t["template_id"][:-len(ty)] + "individual"]
                    if menu_shape(t["choices"]) != menu_shape(person["choices"]):
                        raise SystemExit(f"bank/src/{name} {key}: its menus are not the person item's")
                no_leftover(t["text"] + json.dumps(t["choices"], ensure_ascii=False), f"{name} {key}")
                out[t["template_id"]] = t
    return out


# ---- errands: the published worlds (the reproduction reference) ----------------------------------------------------
def merge_beneficiary(a, b, where):
    """One errand template from its two beneficiaries' templates: a has the target as beneficiary (its T slots where
    the beneficiary is named), b the other party (X slots there). Where they differ the slot becomes B; anything else
    that differs is refused."""
    if isinstance(a, str):
        ta, tb = SLOT.split(a), SLOT.split(b)
        if len(ta) != len(tb) or ta[::2] != tb[::2]:
            raise ValueError(f"{where}: the two beneficiaries' texts differ beyond a name")
        out = []
        for i, (x, y) in enumerate(zip(ta, tb)):
            if x == y:
                out.append(x)
            elif i % 2 and x.startswith("⟦T.") and y.startswith("⟦X.") and x[3:] == y[3:]:
                out.append("⟦B." + x[3:])
            else:
                raise ValueError(f"{where}: {x!r} against {y!r}")
        return "".join(out)
    if isinstance(a, list):
        if len(a) != len(b):
            raise ValueError(f"{where}: lists differ in length")
        return [merge_beneficiary(x, y, where) for x, y in zip(a, b)]
    if isinstance(a, dict):
        if list(a) != list(b):
            raise ValueError(f"{where}: keys differ")
        return {k: merge_beneficiary(a[k], b[k], where) for k in a}
    if a != b:
        raise ValueError(f"{where}: {a!r} against {b!r}")
    return a


def errand_row(ty, seed, cell, slots, world, prompt, base, source_):
    """An errand template row (BANK_FORMAT.md). The answer key, tools and budgets are the published row's."""
    w = json.loads(world)
    return {"template_id": f"E.{ty}.refunds.L3.s{seed}.{cell}", "type": ty, "family": "refunds", "level": 3,
            "seed": seed, "cell": cell, "slots": slots, "world": world, "prompt": prompt, "server": w["server"],
            "finish_tool": base["finish_tool"], "answer_argument": base["answer_argument"],
            "answer_key": sorted(base["answer_key"]), "max_turns": w["end"]["max_turns"],
            "call_budget": w["end"]["call_budget"], "followup": ty, "source": source_}


def merged_person(p, t, mate, where):
    """A person errand's world (parsed), system text and opening turn as templates, from its two beneficiaries' rows:
    the user is T, the published teammate X, and the names that differ between the two rows B."""
    surfaces = person_surfaces("T", YOU) + [(mate, "X", "full", "Full"), (mate.split()[0], "X", "short", "Short")]
    parts = [merge_beneficiary(slot_json(x, surfaces), slot_json(y, surfaces), where)
             for x, y in ((json.loads(p["world"]), json.loads(t["world"])), (p["system_text"], t["system_text"]),
                          (p["user"], t["user"]))]
    for txt in (dump_world(parts[0]), parts[1], parts[2]):
        no_leftover(txt, where)
    return parts


def mate_record(row):
    """The published teammate of a person row, as an entity record (X in the reproduction)."""
    return entity("individual", json.loads(row["world"])["roles"]["teammate"]["name"])


def person_errands(data):
    """The reference worlds of the level-3 tasks anulus v0.5 published (seeds 625 to 648): its realistic neutral
    rows, the user deploying, for both beneficiaries. Each must reproduce both rows' prompt and world sha256."""
    mine = {}
    for r in data["anulus/v0.5"]:
        if r["arm"] == "neutral" and r["realism"] == "realistic" and r["principal_slot"] == "you" and r["level"] == 3:
            mine.setdefault(r["seed"], {})[r["beneficiary"]] = r
    out = []
    for seed, pair in sorted(mine.items()):
        p, t = pair["principal"], pair["teammate"]
        world, system, user = merged_person(p, t, json.loads(p["world"])["roles"]["teammate"]["name"],
                                            f"anulus s{seed}")
        if p["answer_key"] != t["answer_key"] or p["followup_user"] != t["followup_user"]:
            raise ValueError(f"anulus s{seed}: the beneficiaries' keys or reports differ")
        tpl = errand_row("individual", seed, "N0", ["T", "X", "B"], dump_world(world), system + "\n\n" + user, p,
                         origin("anulus", "v0.5", p["id"].replace(".principal.", ".<beneficiary>."), "exact", "none",
                                prompt=p["prompt_sha256_public"], world=p["world_sha256"]))
        for row in (p, t):
            x = mate_record(row)
            rec = {"T": you_record(), "X": x, "B": you_record() if row is p else x}
            if sha(render_world(tpl["world"], rec)) != row["world_sha256"] or \
                    sha(render(tpl["prompt"], rec)) != row["prompt_sha256_public"]:
                raise ValueError(f"individual s{seed}: does not reproduce {row['id']}")
        out.append(tpl)
    return out


def world_kinds(person):
    """{seed parity: (server, channels, roles, chat message)}: labores's two worlds as the v0.5 templates have them.
    Every template of a parity must agree, or the builder refuses."""
    kinds = {}
    for tpl in person:
        w = json.loads(tpl["world"])
        got = (w["server"], w["channels"], w["roles"], w["state"]["mail"]["chat_read"])
        if kinds.setdefault(tpl["seed"] % 2, got) != got:
            raise ValueError(f"individual s{tpl['seed']}: its world kind differs from its parity's")
    return kinds


def nation_errands(data, person, slices=False):
    """The reference worlds of every level-3 task from patria's class23 realistic neutral rows: both pairs, each
    nation deploying, both beneficiaries (the joint secretariat's rows are not v4's). A task missing a whole pair is
    refused, except with slices (data from tests/fixtures/sources, whose tasks hold one pair). A row holds the
    engine's tools,
    state, rules and end; the world adds the roles patria gave (the two delegations) and labores's channel and server
    for that world kind. Each template must give every row's system text and opening turn byte for byte and its
    engine fields exactly; where anulus v0.5 has the task, the world must be its person world with only the roles and
    the chat message changed (patria's check_derived)."""
    base = {t["seed"]: t for t in person}
    kinds = world_kinds(person)
    by = {}
    for r in data["patria/class23"]:
        if r["condition"] == "neutral" and r["realism"] == "realistic" and r["beneficiary"] in ("principal", "partner"):
            by.setdefault(r["seed"], []).append(r)
    out = []
    for seed, rows in sorted(by.items()):
        server, channels = kinds[seed % 2][:2]
        made, checks = None, []
        for pair, (k1, k2) in NATION_PAIRS.items():
            # each pair must be whole (each nation deploying, both beneficiaries); only a build from the slices may
            # meet a task without one of its pairs, and the published dataset holds both for every task
            if not any(r["pair"] == pair for r in rows):
                if slices:
                    continue
                raise ValueError(f"patria s{seed}: the {pair} pair is missing")
            if sum(r["pair"] == pair for r in rows) != 4:
                raise ValueError(f"patria s{seed}: the {pair} pair is not whole")
            first, second = (k1, k2) if seed % 4 < 2 else (k2, k1)       # patria's order: key order, then the seed
            for deployer, other in ((k1, k2), (k2, k1)):
                surfaces = nation_surfaces("T", deployer) + nation_surfaces("X", other)
                halves = []
                for ben in ("principal", "partner"):
                    r = next(x for x in rows if x["pair"] == pair and x["deploying_nation"] == deployer
                             and x["beneficiary"] == ben)
                    world = {"channels": channels, "end": json.loads(r["end"]),
                             "roles": {"principal": {"name": NATIONS[deployer]["group"], "relation": "principal"},
                                       "teammate": {"name": NATIONS[other]["group"], "relation": "teammate"}},
                             "rules": json.loads(r["rules"]), "server": server,
                             "state": json.loads(r["initial_state"]), "tools": json.loads(r["tools"])}
                    halves.append([slot_json(world, surfaces), slot_text(r["system_text"], surfaces),
                                   slot_text(r["user"], surfaces)])
                    checks.append((r, {"T": NATIONS[deployer], "X": NATIONS[other],
                                       "B": NATIONS[deployer if ben == "principal" else other],
                                       "P1": NATIONS[first], "P2": NATIONS[second]}))
                parts = merge_beneficiary(halves[0], halves[1], f"patria s{seed} {pair} {deployer}")
                parts[1] = re.sub(r"between ⟦[TX]\.full⟧ and ⟦[TX]\.full⟧", "between ⟦P1.full⟧ and ⟦P2.full⟧",
                                  parts[1], count=1)
                if made is None:
                    made = parts
                elif parts != made:
                    raise ValueError(f"patria s{seed}: {pair} {deployer} gives another template")
        world = dump_world(made[0])
        for txt in (world, made[1], made[2]):
            no_leftover(txt, f"patria s{seed}")
        if seed in base:
            derived_from_person(json.loads(world), json.loads(base[seed]["world"]), f"patria s{seed}")
        first_row = rows[0]
        tpl = errand_row("nation", seed, "N0", ["T", "X", "B", "P1", "P2"], world, made[1] + "\n\n" + made[2],
                         first_row, origin("patria", "class23", f"patria.labores.nat.refunds.L3.s{seed}.<pair>."
                                           "<beneficiary>.<nation>.<deployer>.neutral.realistic", "exact-by-fields",
                                           "none", **{f: sha(first_row[f]) for f in CLASS23_FIELDS}))
        for r, rec in checks:
            w = json.loads(render_world(tpl["world"], rec))
            fields = {"tools": "tools", "state": "initial_state", "rules": "rules", "end": "end"}
            # the row's engine fields, parsed, are the world's (the world sorts keys; the row kept the spec's order),
            # and the row's text is the plain dump of its parse, so the parse lost nothing
            if render(tpl["prompt"], rec) != r["system_text"] + "\n\n" + r["user"] or \
                    any(json.loads(r[f]) != w[k] or json.dumps(json.loads(r[f])) != r[f] for k, f in fields.items()) \
                    or sorted(r["answer_key"]) != tpl["answer_key"]:
                raise ValueError(f"nation s{seed}: does not reproduce {r['episode_id']}")
        out.append(tpl)
    return out


def derived_from_person(nation_world, person_world, where):
    """patria's check_derived: a nation world is the labores world of its task with only the roles and the chat
    message changed; tools, rules, budgets, channel, server and the rest of the state are byte-identical."""
    a, b = dict(nation_world), dict(person_world)
    for w in (a, b):
        w.pop("roles")
        w["state"] = dict(w["state"], mail=dict(w["state"]["mail"]))
        w["state"]["mail"].pop("chat_read")
    if a != b:
        raise ValueError(f"{where}: the nation world differs from its task's person world beyond roles and the chat")


def person_from_nation(nation_tpl, kind):
    """A person world from the nation world of the same task: labores's roles and chat message for that world kind
    put back where patria put the delegations'. On the tasks anulus v0.5 published this must give v0.5's world byte
    for byte, which is what licenses it for the tasks v0.5 did not publish (301 to 324)."""
    w = json.loads(nation_tpl["world"])
    w["roles"] = kind[2]
    w["state"] = dict(w["state"], mail=dict(w["state"]["mail"], chat_read=kind[3]))
    return dump_world(w)


def labores_errands(data, person, nations):
    """The reference worlds of the level-3 tasks only labores published (seeds 301 to 324, the full tier). labores's
    prereg rows use an older world layout (the chat in state.chat with no message id, no channel, an empty score
    accumulator), so the world is built from patria's world of the same task (person_from_nation), once that
    construction has reproduced every v0.5 world; the prompt is labores's own, byte for byte, and its chat message,
    roles and task data (statement, details, allowance) must be the built world's."""
    kinds = world_kinds(person)
    by_seed = {t["seed"]: t for t in nations}
    for tpl in person:
        if person_from_nation(by_seed[tpl["seed"]], kinds[tpl["seed"] % 2]) != tpl["world"]:
            raise ValueError(f"individual s{tpl['seed']}: patria's world with labores's roles is not v0.5's")
    mine = {}
    for r in data["labores/refunds"]:
        if r["arm"] == "neutral" and r["principal_slot"] == "you" and r["level"] == 3:
            mine.setdefault(r["seed"], {})[r["beneficiary"]] = r
    out = []
    for seed, pair in sorted(mine.items()):
        p, t = pair["principal"], pair["teammate"]
        kind = kinds[seed % 2]
        old, system, user = merged_person(p, t, json.loads(p["world"])["roles"]["teammate"]["name"],
                                          f"labores s{seed}")
        world = person_from_nation(by_seed[seed], kind)
        new = json.loads(world)
        msg = {k: v for k, v in kind[3][0].items() if k != "id"}
        if (old["roles"] != kind[2] or old["state"]["chat"] != [msg] or old["server"] != kind[0]
                or any(old["state"][k] != new["state"][k] for k in ("statement", "detail", "agent"))):
            raise ValueError(f"labores s{seed}: its roles, chat message or task data differ from the built world's")
        tpl = errand_row("individual", seed, "N0", ["T", "X", "B"], world, system + "\n\n" + user, p,
                         origin("labores", "refunds", p["id"].replace(".principal.", ".<beneficiary>."), "derived",
                                "declared: labores's prereg world rebuilt in anulus v0.5's layout from patria's world "
                                "of the same task (the chat as mail with an id, the authenticated channel)",
                                prompt=p["prompt_sha256_public"]))
        for row in (p, t):
            x = mate_record(row)
            if sha(render(tpl["prompt"], {"T": you_record(), "X": x, "B": you_record() if row is p else x})) != \
                    row["prompt_sha256_public"]:
                raise ValueError(f"individual s{seed}: does not reproduce {row['id']}'s prompt")
        out.append(tpl)
    return out


# ---- errands: the organization worlds ------------------------------------------------------------------------------
def surface(errands_src, kind):
    """The pieces of a type's errand surface for one world kind (W1's nation/ or organization/errands.json)."""
    e = {k: v["text"] for k, v in errands_src.items() if not k.startswith("_")}
    return {"first": e[f"E.system.first.{kind}"], "last": e["E.system.last"], "user": e[f"E.user.{kind}"],
            "from": e["E.message.from"], "signed_in_as": e[f"E.message.signed_in_as.{kind}"],
            "text": e["E.request"] + "\n\n" + e["E.message.signature"],
            "principal": e["E.roles.principal"], "teammate": e["E.roles.teammate"]}


def surface_of(tpl):
    """The same pieces as a built errand template has them: the first and last system paragraphs, the opening turn,
    the chat message's sender, handle and text, and the two roles' names."""
    paras, w = tpl["prompt"].split("\n\n"), json.loads(tpl["world"])
    msg = w["state"]["mail"]["chat_read"][0]
    last = next(p for p in paras if p.startswith(FINISHED))
    return {"first": paras[0], "last": last, "user": paras[-1], "from": msg["from"],
            "signed_in_as": msg["signed_in_as"], "text": msg["text"], "principal": w["roles"]["principal"]["name"],
            "teammate": w["roles"]["teammate"]["name"]}


def with_surface(tpl, pieces):
    """An errand template with its surface pieces replaced, and nothing else: the prompt's first and last system
    paragraphs and opening turn, the chat message's sender, handle and text, and the roles' names."""
    paras = tpl["prompt"].split("\n\n")
    at = next(i for i, p in enumerate(paras) if p.startswith(FINISHED))
    paras[0], paras[at], paras[-1] = pieces["first"], pieces["last"], pieces["user"]
    w = json.loads(tpl["world"])
    msg = dict(w["state"]["mail"]["chat_read"][0], **{k: pieces[k] for k in ("from", "signed_in_as", "text")})
    w["state"] = dict(w["state"], mail=dict(w["state"]["mail"], chat_read=[msg]))
    w["roles"] = {"principal": dict(w["roles"]["principal"], name=pieces["principal"]),
                  "teammate": dict(w["roles"]["teammate"], name=pieces["teammate"])}
    return dump_world(w), "\n\n".join(paras)


def organization_errands(src, nations):
    """The organization worlds: each nation world of the same task with W1's organization surface (DESIGN.md 8.5)
    in place of the nation's. W1's nation surface must be exactly what the builder read off patria's rows, so the
    swap changes the surface and only the surface."""
    nat, org = source(src, "nation/errands.json"), source(src, "organization/errands.json")
    out = []
    for tpl in nations:
        have = surface_of(tpl)
        kind = next((k for k in ("lab", "studio") if surface(nat, k) == have), None)
        if kind is None:
            raise SystemExit(f"bank/src/nation/errands.json: no world kind matches patria's task {tpl['seed']} as the "
                             "builder reads it")
        world, prompt = with_surface(tpl, surface(org, kind))
        no_leftover(world + prompt, f"organization s{tpl['seed']}")
        out.append(errand_row("organization", tpl["seed"], "N0", tpl["slots"], world, prompt, tpl,
                              dict(tpl["source"], category="new", change=f"declared: patria's world with W1's "
                                   f"organization surface for the {kind} world (bank/CHANGES.md, Errands)")))
    return out


# ---- the cells -----------------------------------------------------------------------------------------------------
def overlay(data):
    """anulus v0.4's notice cells against v0.5, read from the rows: for the unaudited-realistic cell (U) and the
    audited-disclosed cell (O), the notice paragraphs, the activity_log tool, the two logging rules and the log's
    state key. Refuses unless every v0.4 row differs from its v0.5 twin in exactly these, the opening turn's copied
    request aside. It is the evidence cells.json is checked against."""
    v5 = {(r["seed"], r["beneficiary"], r["principal_slot"]): r for r in data["anulus/v0.5"]
          if r["arm"] == "neutral" and r["realism"] == "realistic"}
    cells = {("unaudited", "realistic"): "U", ("audited", "disclosed"): "O"}
    found = {}
    for r in data["anulus/v0.4"]:
        cell = cells.get((r["observation"], r["realism"]))
        if cell is None or r["principal_slot"] != "you":
            continue
        b = v5[(r["seed"], r["beneficiary"], r["principal_slot"])]
        surfaces = person_surfaces("T", YOU)
        w4, w5 = slot_json(json.loads(r["world"]), surfaces), slot_json(json.loads(b["world"]), surfaces)
        tool = w4["tools"][len(w5["tools"]):]
        if (w4["tools"][:len(w5["tools"])] != w5["tools"] or len(tool) != 1 or w4["rules"][1:-1] != w5["rules"]
                or w4["state"] != dict(w5["state"], _anulus_log=[])
                or {k: v for k, v in w4.items() if k not in ("tools", "rules", "state")} !=
                {k: v for k, v in w5.items() if k not in ("tools", "rules", "state")}):
            raise ValueError(f"anulus v0.4 {r['id']}: differs from v0.5 beyond the overlay")
        p4, p5 = (slot_text(x["system_text"], surfaces).split("\n\n") for x in (r, b))
        if p4[:3] != p5[:3] or p4[-1] != p5[-1] or not p5[2].startswith(FINISHED):
            raise ValueError(f"anulus v0.4 {r['id']}: system text differs beyond the notices")
        got = {"notices": p4[3:-1], "tool": tool[0], "rules": [w4["rules"][0], w4["rules"][-1]]}
        if found.setdefault(cell, got) != got:
            raise ValueError(f"anulus v0.4 {r['id']}: another overlay than its cell's other rows")
    if set(found) != {"U", "O"}:
        raise ValueError("anulus v0.4: a cell is missing")
    return found


def cells(src, data):
    """{type: {cell: {"notices": [...], "header": ...}}} and the shared additions (tool, the two rules, the state),
    from W1's cells.json and each type's audited notice. The shared additions and the individual notices and headers
    must be anulus v0.4's, as overlay() reads them off its rows; N0's header is v0.4's without the retention clause."""
    c = source(src, "cells.json")
    shared, layout = c["_structure"]["shared"], c["_structure"]["cells"]
    errands = {"nation": source(src, "nation/errands.json")}
    if (Path(src) / "organization/errands.json").exists():
        errands["organization"] = source(src, "organization/errands.json")

    def text(ref, ty):
        if ref.startswith("<type>:"):
            name = ref[len("<type>:"):]
            if ty == "individual":
                return c[{"E.notice.audited": "cell.individual.notice.audited",
                          "E.log_header.O": "cell.individual.log_header.O"}[name]]["text"]
            return errands[ty][name]["text"]
        return c[ref]["text"]
    spec = {ty: {cell: {"notices": [text(n, ty) for n in layout[cell]["notice"]],
                        "header": text(layout[cell]["log_header"], ty)} for cell in CELLS}
            for ty in ("individual",) + tuple(errands)}
    ov = overlay(data)
    for cell in ("U", "O"):
        header = ov[cell]["rules"][1]["then"]["respond"]["header"]
        if ov[cell]["notices"] != spec["individual"][cell]["notices"] or header != spec["individual"][cell]["header"] \
                or [shared["tools_added"][0]] != [ov[cell]["tool"]] or shared["rules_added"][0] != ov[cell]["rules"][0] \
                or dict(shared["rules_added"][1], then={"respond": dict(shared["rules_added"][1]["then"]["respond"],
                                                                         header=header)}) != ov[cell]["rules"][1] \
                or shared["state_added"] != {"_anulus_log": []}:
            raise SystemExit(f"bank/src/cells.json: the {cell} cell is not anulus v0.4's as its rows have it")
    if not spec["individual"]["U"]["header"].startswith(spec["individual"]["N0"]["header"]):
        raise SystemExit("bank/src/cells.json: N0's log header is not v0.4's without its retention clause")
    return spec, shared


def scored_cell(ref, cell, spec, shared):
    """A scored errand cell from its task's reference world: the shared activity log (its tool after the others, the
    call-logging rule first and the log's own rule last, the log's empty state) in every cell, the cell's log header,
    and the cell's notice paragraphs after the "When you have finished" paragraph."""
    w = json.loads(ref["world"])
    log_rule = dict(shared["rules_added"][1], then={"respond": dict(shared["rules_added"][1]["then"]["respond"],
                                                                    header=spec[cell]["header"])})
    w["tools"] = w["tools"] + shared["tools_added"]
    w["rules"] = [shared["rules_added"][0]] + w["rules"] + [log_rule]
    w["state"] = dict(w["state"], **shared["state_added"])
    paras = ref["prompt"].split("\n\n")
    at = next(i for i, p in enumerate(paras) if p.startswith(FINISHED)) + 1
    prompt = "\n\n".join(paras[:at] + spec[cell]["notices"] + paras[at:])
    what = {"N0": "no notice", "U": "the unaudited notice", "O": "the audited and disclosed notices"}[cell]
    change = (f"declared: cell {cell}, {what}, on the shared activity-log environment (bank/src/cells.json); the "
              "source world is its reproduction reference R0")
    if ref["source"]["change"] != "none":
        change += "; R0 " + ref["source"]["change"][len("declared: "):]
    return errand_row(ref["type"], ref["seed"], cell, ref["slots"], dump_world(w), prompt, ref,
                      dict(ref["source"], category="new" if ref["type"] == "organization" else "derived",
                           reference_category=ref["source"]["category"], change=change))


def strip_cell(t, shared):
    """A scored cell with the shared additions and its notices taken out: what must equal its reference world."""
    w = json.loads(t["world"])
    w["tools"] = w["tools"][:-len(shared["tools_added"])]
    w["rules"] = w["rules"][1:-1]
    w["state"] = {k: v for k, v in w["state"].items() if k not in shared["state_added"]}
    return dump_world(w)


def check_cells(trio, ref, shared):
    """The cells of one task: each is its reference world plus the shared additions; the three worlds are equal but
    for the log header; the three prompts are the reference prompt plus each cell's notices, and nothing else;
    answer key, budgets and server are the reference's."""
    worlds = []
    for t in trio.values():
        if strip_cell(t, shared) != ref["world"]:
            raise ValueError(f"{t['template_id']}: not its reference world plus the shared activity log")
        w = json.loads(t["world"])
        w["rules"][-1] = dict(w["rules"][-1], then="<header>")
        worlds.append(w)
        if any(t[k] != ref[k] for k in ("answer_key", "finish_tool", "answer_argument", "max_turns", "call_budget",
                                        "server", "slots")):
            raise ValueError(f"{t['template_id']}: its key or budgets differ from the reference's")
    if worlds[1:] != worlds[:-1]:
        raise ValueError(f"{ref['type']} s{ref['seed']}: the cells' worlds differ beyond the log header")
    if trio["N0"]["prompt"] != ref["prompt"]:
        raise ValueError(f"{ref['type']} s{ref['seed']}: N0's prompt is not the reference's")


def bank_cells(spec, shared):
    """The bank's cells.json (BANK_FORMAT.md, "cells.json: U and O as overlays applied at load"): errands.jsonl holds
    the N0 rows only, and the engine derives U and O from each by inserting the cell's notices after the anchor
    paragraph and swapping the activity-log rule's header. About a third of the size of storing all three cells."""
    return {"format": 1, "anchor": FINISHED, "rule_id": shared["rules_added"][1]["id"],
            "types": {ty: {"N0": {"log_header": c["N0"]["header"]},
                           **{cell: {"notice": c[cell]["notices"], "log_header": c[cell]["header"]}
                              for cell in ("U", "O")}} for ty, c in spec.items()}}


def derive_cell(row, doc, cell):
    """The engine's rule for a U or O row from an N0 row (BANK_FORMAT.md; probatio's derive_cell on branch v4-w3):
    the notices after the one anchor paragraph, the one activity-log rule's N0 header replaced, the id's cell suffix
    swapped. The builder applies it to every N0 row and refuses unless it gives back the cell it built in full."""
    spec = doc["types"][row["type"]]
    paras = row["prompt"].split("\n\n")
    at = [i for i, para in enumerate(paras) if para.startswith(doc["anchor"])]
    world = json.loads(row["world"])
    rules = [r for r in world["rules"] if r.get("id") == doc["rule_id"]]
    if len(at) != 1 or len(rules) != 1 or rules[0]["then"]["respond"]["header"] != spec["N0"]["log_header"]:
        raise ValueError(f"{row['template_id']}: no single anchor paragraph or N0 log rule")
    rules[0]["then"]["respond"]["header"] = spec[cell]["log_header"]
    return dict(row, cell=cell, template_id=row["template_id"][:-len("N0")] + cell, world=dump_world(world),
                prompt="\n\n".join(paras[:at[0] + 1] + spec[cell]["notice"] + paras[at[0] + 1:]))


# ---- arms and the follow-up report ---------------------------------------------------------------------------------
def arm_of(system_text):
    """The arm paragraph of a system text: what follows the "When you have finished" paragraph (labores's layout)."""
    paras = system_text.split("\n\n")
    at = next(i for i, p in enumerate(paras) if p.startswith(FINISHED)) + 1
    if len(paras) != at + 1:
        raise ValueError("a system text with more than one paragraph after its finish paragraph")
    return paras[at]


def arms(data, errands, src, with_organization):
    """{type: {neutral, g_wish}}: the neutral sentence every prompt holds exactly once, and labores's g_wish policy
    for the type with the deployer as ⟦T⟧ (anulus v0.5's g_wish rows for people, patria's for nations, which must be
    W1's nation g_wish; W1's for organizations). Every source row of an arm must give the same text."""
    got = {}
    for r in data["anulus/v0.5"]:
        if r["principal_slot"] == "you" and r["realism"] == "realistic":
            text = slot_text(arm_of(r["system_text"]), person_surfaces("T", YOU))
            if got.setdefault(("individual", r["arm"]), text) != text:
                raise ValueError(f"anulus {r['id']}: another {r['arm']} arm")
    for r in data["patria/class23"]:
        if r["realism"] == "realistic" and r["deploying_nation"] in NATIONS:
            text = slot_text(arm_of(r["system_text"]), nation_surfaces("T", r["deploying_nation"]))
            if got.setdefault(("nation", r["condition"]), text) != text:
                raise ValueError(f"patria {r['episode_id']}: another {r['condition']} arm")
    out = {ty: {arm: got[(ty, arm)] for arm in ("neutral", "g_wish")} for ty in ("individual", "nation")}
    if source(src, "nation/errands.json")["E.arm.g_wish"]["text"] != out["nation"]["g_wish"]:
        raise SystemExit("bank/src/nation/errands.json: E.arm.g_wish is not patria's g_wish as its rows have it")
    if with_organization:
        out["organization"] = {"neutral": out["nation"]["neutral"],
                               "g_wish": source(src, "organization/errands.json")["E.arm.g_wish"]["text"]}
    for t in errands.values():
        if t["prompt"].count(out[t["type"]]["neutral"]) != 1:
            raise ValueError(f"{t['template_id']}: the neutral sentence is not in its prompt exactly once")
    return out


def followups(data, src, with_organization):
    """The five-question report per type: anulus's for individuals and patria's for nations (W1's copies must equal
    them), W1's for organizations."""
    person = {r["followup_user"] for r in data["anulus/v0.5"]}
    nation = {r["followup_questions"] for r in data["patria/class23"]}
    if len(person) != 1 or len(nation) != 1:
        raise ValueError("a dataset has more than one follow-up report")
    out = {"individual": person.pop(), "nation": nation.pop()}
    w1 = source(src, "followup.json")
    for ty in out:
        if w1[f"followup.{ty}"]["text"] != out[ty]:
            raise SystemExit(f"bank/src/followup.json: followup.{ty} is not the published report")
    if with_organization:
        out["organization"] = w1["followup.organization"]["text"]
    return out


# ---- fixed names (collision rule 5.3.3) ----------------------------------------------------------------------------
# A proper name is a run of capitalised words (joined inside the run by "&", "of", "for", "at", "the" or "and"),
# except a single word that starts a sentence, a line, a quotation or a menu option. Slots, transaction ids and words
# capitalised for other reasons (answer keywords, months, days) break a run and are never names.
NOT_NAMES = {"A", "B", "C", "D", "E", "F", "G", "H", "I", "AI", "ACTION", "ANSWER", "MESSAGE", "OUTSIDE", "YES", "NO",
             "REAL", "TEST", "NOBODY", "Answer", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
             "Sunday", "January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December", "GPU", "CPU", "ID", "PDF", "OK"}
_WORD = r"[A-Z][\w'’]*"
_NAME = re.compile(rf"(?<![\w\x00]){_WORD}(?:(?:[ \t]+(?:&|of|for|at|the|and)[ \t]+|[ \t]+){_WORD})*")
_OPENS = re.compile(r"(?:\A|[.!?][\"”’)]*\s+|\n\s*|\)\s+|[\"“‘(]|:\s+)\Z")
_BREAK = re.compile(r"⟦[^⟧]*⟧|\b[A-Z]{1,4}-\d+\b")


def proper_names(text):
    """The proper names in one string, outside its slots."""
    plain = _BREAK.sub("\x00", text)
    names = set()
    for m in _NAME.finditer(plain):
        words = m.group(0).split()
        first_opens = bool(_OPENS.search(plain[:m.start()]))
        while words and (words[0] in NOT_NAMES or not words[0][:1].isupper()):
            words.pop(0)
            first_opens = False
        while words and (words[-1] in NOT_NAMES or not words[-1][:1].isupper()):
            words.pop()
        if not words or (len(words) == 1 and first_opens):
            continue
        names.add(re.sub(r"['’]s$", "", " ".join(words)).rstrip("'’"))      # "Kestrel Travel's" -> the name
    return names


def strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [k] + strings(v)]
    return []


def fixed_names(templates, src):
    """{type: [names]}: every proper name the type's templates hold outside their slots, the names the builder finds
    and those W1 lists (bank/src/fixed_names.json) that occur in the type's templates."""
    w1 = source(src, "fixed_names.json")
    listed = {n for k, v in w1.items() if not k.startswith("_") and k != "source" for n in v}
    texts, out = {}, {}
    for t in templates:
        mine = [t.get("text") or t.get("prompt") or ""] + [c["text"] or "" for c in t.get("choices", [])]
        if "world" in t:
            mine += strings(json.loads(t["world"]))
        texts.setdefault(t["type"], []).extend(mine)
    for ty, all_texts in texts.items():
        found = set().union(*(proper_names(x) for x in all_texts))
        blob = "\n".join(all_texts)
        out[ty] = sorted(found | {n for n in listed if n in blob})
    return out


# ---- assembling and writing ----------------------------------------------------------------------------------------
def entities():
    """entities.json: W1's rosters in the engine's shape (load_roster), without the source notes."""
    return ROSTER


def pending(src):
    return [p for p in W1_ORGANIZATION if not (Path(src) / p).exists()]


def build(data, src=SRC, allow_pending=False, slices=False):
    """Every bank file's text, keyed by name, and a summary. Refuses on any reproduction, derivation or cross-check
    failure, and without the organization sources unless allow_pending. slices says data is the source slices
    (tests/fixtures/make_sources.read()), whose tasks may lack one patria pair; a real build never passes it."""
    for name in W1_CORE:
        source(src, name)
    missing = pending(src)
    if missing and not allow_pending:
        raise SystemExit("W1's organization sources are not in bank/src/ (" + ", ".join(missing) +
                         "); pass --allow-pending to build a test bank without them.")
    with_org = not missing
    published = published_class1(data)
    class1 = dict(published)
    class1.update(w1_templates(src, published, with_org))
    person = person_errands(data)
    nations = nation_errands(data, person, slices)
    refs = person + labores_errands(data, person, nations) + nations
    if with_org:
        refs += organization_errands(src, nations)
    spec, shared = cells(src, data)
    doc = bank_cells(spec, shared)
    errands, n0 = {}, {}
    for ref in refs:
        trio = {cell: scored_cell(ref, cell, spec[ref["type"]], shared) for cell in CELLS}
        check_cells(trio, ref, shared)
        for cell in ("U", "O"):
            got = derive_cell(trio["N0"], doc, cell)
            if (got["prompt"], got["world"], got["template_id"]) != \
                    (trio[cell]["prompt"], trio[cell]["world"], trio[cell]["template_id"]):
                raise ValueError(f"{trio[cell]['template_id']}: the overlay at load does not give the cell built")
        errands.update({t["template_id"]: t for t in trio.values()})
        n0[trio["N0"]["template_id"]] = trio["N0"]
    order = lambda t: (TYPES.index(t["type"]), t["template_id"])           # noqa: E731
    errand_order = lambda t: (TYPES.index(t["type"]), t["seed"], CELLS.index(t["cell"]))  # noqa: E731
    files = {
        "entities.json": pretty(entities()),
        "fixed_names.json": pretty(fixed_names(list(class1.values()) + list(errands.values()), src)),
        "class1.jsonl": jsonl(sorted(class1.values(), key=order)),
        "errands.jsonl": jsonl(sorted(n0.values(), key=errand_order)),          # U and O: cells.json at load
        "cells.json": pretty(doc),
        "followup.json": pretty(followups(data, src, with_org)),
        "arms.json": pretty(arms(data, errands, src, with_org)),
    }
    files["manifest.json"] = pretty(manifest(files, missing, src))
    summary = {"class1": count(class1.values()), "errands": count(errands.values()), "pending": missing}
    return files, summary


def count(rows):
    out = {}
    for r in rows:
        out[r["type"]] = out.get(r["type"], 0) + 1
    return out


def jsonl(rows):
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def pretty(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def manifest(files, missing, src=SRC):
    """BANK_FORMAT.md's manifest (format, version, every file's sha256, the task pools and tier counts, the source
    commits, the builder's commit), plus each source file's row count and sha256, the sha256 of the builder and of
    every W1 source file it read, and what is still pending."""
    me = Path(__file__).resolve()
    try:
        commit = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%H", "--", "tools/build_bank.py"],
                                capture_output=True, text=True, timeout=30).stdout.strip() or "uncommitted"
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
    w1 = {p: hashlib.sha256((Path(src) / p).read_bytes()).hexdigest() for p in W1_CORE + W1_ORGANIZATION
          if (Path(src) / p).exists()}
    return {"format": BANK_FORMAT, "version": BANK_VERSION,
            "files": {name: sha(text) for name, text in sorted(files.items())},
            "tasks": TASKS, "tier_tasks": TIER_TASKS,
            "sources": {d: c for d, (c, _) in SOURCES.items()},
            "source_files": {f"{d}/{k}": {"rows": n, "sha256": h} for d, (_, cs) in SOURCES.items()
                             for k, (n, h) in cs.items()},
            "bank_sources": w1, "built_by": commit, "builder_sha256": hashlib.sha256(me.read_bytes()).hexdigest(),
            "pending": missing}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", default=str(ROOT / ".cache"), help="folder of <dataset>/<commit>/<config>.jsonl")
    ap.add_argument("--out", default=str(BANK), help="where to write the bank (default bank/)")
    ap.add_argument("--check", action="store_true", help="rebuild in memory and compare with --out, write nothing")
    ap.add_argument("--allow-pending", action="store_true", help="build without the organization sources")
    a = ap.parse_args(argv)
    files, summary = build(read_all(a.cache), allow_pending=a.allow_pending)
    out = Path(a.out)
    if a.check:
        bad = [n for n, t in files.items() if not (out / n).exists() or (out / n).read_text(encoding="utf-8") != t]
        print("bank matches a fresh build" if not bad else f"bank differs from a fresh build in: {', '.join(bad)}")
        return 1 if bad else 0
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    print(f"bank written to {out}: Class I templates {summary['class1']}, errand templates {summary['errands']}"
          + (f"; pending from W1: {', '.join(summary['pending'])}" if summary["pending"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
