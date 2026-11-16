"""Build ordo's bank (bank/ordo/): the cross-type loyalty templates of docs/ordo/DESIGN.md, written deterministically
from ordo's own sources and the pinned v4 bank. Standard library only, Python 3.9 or later. No network, no model.

Inputs
  bank/                  the v4 bank, read through probatio.load_bank with BANK_MANIFEST_SHA256, so ordo is tied to the
                         bank the published trio (persona, collegium, natio) was built from. Nothing in it is written.
  bank/ordo/src/         ordo's hand-written sources: templates.json (every new template), entities.json (the name sets
                         and the joint secretariat), cells.json (the U and O overlays), followup.json (the report change)

Output: bank/ordo/, the format of docs/ordo/BANK_FORMAT.md (the contract with ordo.py)
  class1.jsonl    201 Class I and check templates and 90 red-flag templates, one row per ordered type assignment
  errands.jsonl   48 N0 worlds, one per refund task, derived from collegium's world of the same seed
  cells.json      the U and O overlays, applied at load by probatio.derive_cell (type "ordo")
  followup.json   the five-question report
  entities.json   v4's rosters unchanged, the two name sets and the joint secretariat
  fixed_names.json  every proper name the templates hold outside their slots
  manifest.json   every file's sha256, the sources' sha256, the builder's sha256, the v4 bank pin, the task pools

Data flow: load_v4 and load_sources read the inputs; the row builders (pair_rows to red_flag_rows, ordo_errands)
expand them; validate checks every row by rendering it; build assembles the files as bytes; main writes or compares.

Refusals, each fatal before anything is written: a v4 bank that does not match its pin; a source paragraph, message
or rule that is not the one this builder was written against; a derived world that differs from its collegium source
outside the declared fields (prompt paragraph one and the result's recipient, the chat message's sender, sign-in and
last sentence, the world's roles); any slot of an unknown role or field; any row that does not render.

    python3 tools/build_ordo_bank.py           # build and write bank/ordo/
    python3 tools/build_ordo_bank.py --check   # rebuild in memory and compare with bank/ordo/ byte for byte
"""
import argparse
import hashlib
import json
import re
import sys
from itertools import permutations, product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import probatio as P  # noqa: E402
import build_bank as B  # noqa: E402  (read-only use: proper_names, strings)

BANK = ROOT / "bank"
ORDO = BANK / "ordo"
SRC = ORDO / "src"
SOURCES = ("templates.json", "entities.json", "cells.json", "followup.json")
OUTPUTS = ("class1.jsonl", "errands.jsonl", "cells.json", "followup.json", "entities.json", "fixed_names.json")
ORDO_FORMAT = "probatio-ordo-1"
ORDO_VERSION = "1.0"
ORDO_TYPE = "ordo"                                     # the type key of ordo's rows, cells and report

# ---- the vocabulary: types, roles, pairs, fields --------------------------------------------------------------------
TYPES = ("individual", "organization", "nation")      # canonical order: a pair index above 50 favours the first
TYPE_ROLE = {"individual": "IND", "organization": "ORG", "nation": "NAT"}
PAIRS = tuple((a, b) for i, a in enumerate(TYPES) for b in TYPES[i + 1:])
ERRAND_ROLES = ("D", "B", "P1", "P2", "P3")
FIELDS = frozenset(P.ENTITY_FIELDS) | {"code", "key"}
SLOT = re.compile(r"⟦([A-Za-z][A-Za-z0-9]*)\.([A-Za-z_]+)⟧")
TAIL_STARTS = {"forced": "You must choose one of the two.", "action": "Choose one action."}
FINISHED = "When you have finished"                  # the result paragraph, which the cells' notices follow
NAME_SET_COUNT = 2

# ---- the fraction of F2.b (DESIGN.md 2.2): factors coded 0/1, level 0 read as +1 ------------------------------------
FACTORS = ("T", "L", "N", "W")      # type assignment, letter, narrative, wording


def half_of(levels):
    """'+1' when the product of the four factors, each level 0 read as +1 and level 1 as -1, is +1 (an even number of
    second levels), else '-1'. The standard tier runs '+1'; the full tier's second repetition runs '-1'."""
    return "+1" if sum(levels) % 2 == 0 else "-1"


# ---- small helpers -------------------------------------------------------------------------------------------------
def sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def pretty(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def jsonl(rows):
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def dump_world(world):
    """A world as the v4 bank writes it (sorted keys, UTF-8 kept), so an unchanged part stays byte-identical."""
    return json.dumps(world, ensure_ascii=False, sort_keys=True)


def reslot(value, mapping):
    """value (a string, list or dict of strings) with every slot's role renamed through mapping (positional role ->
    type role). A role missing from mapping is a builder error, so a template cannot leak a positional role."""
    if isinstance(value, list):
        return [reslot(v, mapping) for v in value]
    if isinstance(value, dict):
        return {reslot(k, mapping): reslot(v, mapping) for k, v in value.items()}
    if not isinstance(value, str):
        return value

    def rename(m):
        role, field = m.groups()
        if role not in mapping:
            raise SystemExit(f"build_ordo_bank: slot {m.group()} has no role mapping ({sorted(mapping)})")
        return f"⟦{mapping[role]}.{field}⟧"
    return SLOT.sub(rename, value)


def slots_in(*values):
    """The sorted roles of every slot in values (strings, lists, dicts, nested)."""
    found = set()
    for v in values:
        for s in B.strings(v) if not isinstance(v, str) else [v]:
            found.update(role for role, _ in SLOT.findall(s))
    return sorted(found)


def menu(options, labels="ABC"):
    return "\n".join(f"{labels[i]}) {text}" for i, text in enumerate(options))


def pair_name(types):
    """The pair column: the types in canonical order joined as --compare writes them ("individual:organization")."""
    return ":".join(sorted(types, key=TYPES.index))


# ---- inputs ---------------------------------------------------------------------------------------------------------
def load_v4():
    """The v4 bank through probatio.load_bank (pinned), plus its cells.json, which load_bank applies but does not keep;
    load_bank has already checked that file's sha256 against the pinned manifest."""
    bank = P.load_bank(BANK)
    bank.cells = json.loads((BANK / "cells.json").read_text(encoding="utf-8"))
    if sha256((BANK / "cells.json").read_bytes()) != bank.manifest["files"]["cells.json"]:
        raise SystemExit("build_ordo_bank: bank/cells.json does not match the v4 manifest")
    bank.rows = {r["template_id"]: r for r in bank.class1}
    return bank


def load_sources(src=SRC):
    """ordo's four source files, as parsed JSON with their sha256."""
    out, hashes = {}, {}
    for name in SOURCES:
        raw = (src / name).read_bytes()
        out[name[:-5]] = json.loads(raw)
        hashes[f"src/{name}"] = sha256(raw)
    return out, hashes


def v4_row(bank, template_id):
    if template_id not in bank.rows:
        raise SystemExit(f"build_ordo_bank: the v4 bank has no row {template_id}")
    return bank.rows[template_id]


def tails(bank, common):
    """The two-option closing paragraphs, taken from the v4 rows that publish them (sacramentum's forced instruction,
    devotio's action instruction), so they are never retyped, and the three-option variant from the source."""
    out = {"three": common["forced_tail_three"]}
    for kind, tid in (("forced", common["forced_tail_two_from_v4"]), ("action", common["action_tail_two_from_v4"])):
        last = v4_row(bank, tid)["text"].split("\n\n")[-1]
        if not last.startswith(TAIL_STARTS[kind]):
            raise SystemExit(f"build_ordo_bank: {tid}'s last paragraph is not the {kind} instruction")
        out[kind] = last
    return out


# ---- Class I rows ---------------------------------------------------------------------------------------------------
def base_row(tid, frame, component, plan, role, variant, types, answer_format, system, text, choices, source):
    """One class1.jsonl row in ordo's schema (docs/ordo/BANK_FORMAT.md)."""
    return {"template_id": tid, "type": ORDO_TYPE, "frame": frame, "component": component, "plan": plan, "role": role,
            "variant": variant, "pair": pair_name(types), "order": list(types), "slots": slots_in(text, choices),
            "answer_format": answer_format, "system": system, "text": text, "choices": choices, "source": source}


def pair_rows(src, tl):
    """The role-free two-option items (F1.a allocation, F1.b sacrifice, F1.c advocacy): one row per item and ordered
    pair of types, option A for the first type of the order. A reply favouring a type maps to that type's name."""
    common, rows = src["templates"]["common"], []
    for item in src["templates"]["pair_items"]:
        for a, b in PAIRS:
            for order in ((a, b), (b, a)):
                mapping = {"A": TYPE_ROLE[order[0]], "B": TYPE_ROLE[order[1]]}
                parts = ([common["survey_preamble"]] if item["preamble"] else []) + [item["body"],
                                                                                    menu(item["options"]),
                                                                                    tl[item["tail"]]]
                text = reslot("\n\n".join(parts), mapping)
                choices = [{"label": "AB"[i], "maps_to": order[i], "menu": None,
                            "text": reslot(item["options"][i], mapping)} for i in range(2)]
                source = dict(item["source"])
                if item["preamble"]:
                    source["preamble_change"] = common["survey_preamble_change"]
                row = base_row(f"{item['component']}.{item['variant']}.{order[0]}-{order[1]}", "role-free",
                               item["component"], item["plan"], "score", item["variant"], order,
                               item["answer_format"], common["system"], text, choices, source)
                for key in ("stake", "price"):
                    if key in item:
                        row[key] = item[key]
                rows.append(row)
    return rows


def triple_rows(src, tl):
    """The descriptive three-way items, each in all six orders: F1.tri (positional claimants A to C) and F2.tri (the
    canonical triad, whose roles are fixed by type and whose jobs and options rotate together)."""
    common, rows = src["templates"]["common"], []
    for item in src["templates"]["triple_items"]:
        for order in permutations(TYPES):
            if item["fixed_roles"]:
                jobs = ", ".join(item["jobs"][t] for t in order[:2]) + " and " + item["jobs"][order[2]]
                options = [item["option_by_type"][t] for t in order]
                body, mapping = item["body"].replace("{jobs}", jobs), {r: r for r in TYPE_ROLE.values()}
            else:
                options, body = item["options"], item["body"]
                mapping = {"ABC"[i]: TYPE_ROLE[t] for i, t in enumerate(order)}
            text = reslot("\n\n".join([common["survey_preamble"], body, menu(options), tl["three"]]), mapping)
            choices = [{"label": "ABC"[i], "maps_to": order[i], "menu": None, "text": reslot(options[i], mapping)}
                       for i in range(3)]
            rows.append(base_row(f"{item['component']}.{item['variant']}.{'-'.join(order)}", item["frame"],
                                 item["component"], item["plan"], "descriptive", item["variant"], order,
                                 item["answer_format"], common["system"], text, choices, item["source"]))
    return rows


def obedience_rows(src, bank):
    """F2.a, par-potestas under equal authority: v4's individual conflict rows (the published wording) for every word
    pair and assignment, with A and B given the types of each ordered pair."""
    spec, rows = src["templates"]["obedience"], []
    for words, assign in product(spec["words"], (0, 1)):
        v4 = v4_row(bank, spec["from_v4"].format(words=words, assign=assign))
        for a, b in PAIRS:
            for order in ((a, b), (b, a)):
                mapping = {"A": TYPE_ROLE[order[0]], "B": TYPE_ROLE[order[1]]}
                choices = [dict(c, maps_to=order["AB".index(c["maps_to"])]) for c in v4["choices"]]
                source = dict(spec["source"], v4_template_id=v4["template_id"], v4_source=v4["source"])
                row = base_row(f"F2.a.{words}.w{assign}.{order[0]}-{order[1]}", "authority", "F2.a", spec["plan"],
                               "score", words, order, v4["answer_format"], v4["system"],
                               reslot(v4["text"], mapping), choices, source)
                row.update(words=words, assign=assign)
                rows.append(row)
    return rows


def ranked_text(item, deployer_first, deployer_wording, deployer_letter, common, tl):
    """The F2.b text and options for one run of the fraction, with DEP and CLI still positional. N sets who is
    introduced first and whose job and request come first; W which job wording the deployer gets; L which letter the
    deployer's option takes. Both requests use the same verb, so only the role sentences say who holds authority."""
    w = item["wordings"]
    sides = {"DEP": {"sentence": item["deployer_sentence"], "wording": w[deployer_wording]},
             "CLI": {"sentence": item["client_sentence"], "wording": w[1 - deployer_wording]}}
    first, second = ("DEP", "CLI") if deployer_first else ("CLI", "DEP")
    group = lambda s: f"⟦{s}.group⟧"         # noqa: E731
    Group = lambda s: f"⟦{s}.Group⟧"         # noqa: E731
    items = " and ".join(item["item"].format(a=sides[s]["wording"]["a"], group=group(s)) for s in (first, second))
    requests = " ".join(item["request"].format(Group=Group(s), the=sides[s]["wording"]["the"]) for s in (first, second))
    paragraph = " ".join([item["setting"], sides[first]["sentence"], sides[second]["sentence"],
                          f"{item['lead']} {items}.", requests, item["closing"]])
    lettered = ("DEP", "CLI") if deployer_letter == 0 else ("CLI", "DEP")
    options = [item["option"].format(the=sides[s]["wording"]["the"], group=group(s)) for s in lettered]
    text = "\n\n".join([common["survey_preamble"], paragraph, menu(options), tl["forced"]])
    return text, options, lettered


def ranked_rows(src, tl):
    """F2.b, deployer against client: for each scenario, pair, type assignment (T), letter (L), narrative (N) and
    wording (W), one row: 2 x 3 x 2 x 2 x 2 x 2 = 96. Each row's design records its four levels and its half of the
    registered fraction T.L.N.W = +1; maps_to names the side's type, and side names its role."""
    common, rows = src["templates"]["common"], []
    for item in src["templates"]["ranked_items"]:
        for a, b in PAIRS:
            for T, L, N, W in product((0, 1), repeat=4):
                deployer, client = (a, b) if T == 0 else (b, a)
                text, options, lettered = ranked_text(item, N == 0, W, L, common, tl)
                mapping = {"DEP": TYPE_ROLE[deployer], "CLI": TYPE_ROLE[client]}
                side_type = {"DEP": deployer, "CLI": client}
                order = [side_type[s] for s in lettered]
                choices = [{"label": "AB"[i], "maps_to": side_type[s], "side": "deployer" if s == "DEP" else "client",
                            "menu": None, "text": reslot(options[i], mapping)} for i, s in enumerate(lettered)]
                row = base_row(f"F2.b.{item['variant']}.D-{deployer}.C-{client}.L{L}.N{N}.W{W}", "authority", "F2.b",
                               item["plan"], "score", item["variant"], order, item["answer_format"],
                               common["system"], reslot(text, mapping), choices, item["source"])
                row["pair"] = pair_name((a, b))
                row["design"] = {"T": T, "L": L, "N": N, "W": W, "half": half_of((T, L, N, W)),
                                 "deployer": deployer, "client": client}
                rows.append(row)
    return rows


def check_rows(src):
    """V12, the recognition check: one row per type, its slot filled by each invented entity in turn."""
    spec, common, rows = src["templates"]["checks"], src["templates"]["common"], []
    for t in TYPES:
        mapping = {"X": TYPE_ROLE[t]}
        choices = [{"label": label, "maps_to": meaning, "menu": None, "text": None} for label, meaning in spec["choices"]]
        rows.append(base_row(f"V12.recognize.{t}", "check", "V12", spec["plan"], "check", "recognize", (t,),
                             spec["answer_format"], common["system"], reslot(spec["text_by_type"][t], mapping),
                             choices, spec["source"]))
    return rows


def red_flag_rows(src, bank, tl):
    """RF1 (honesty) and RF2 (harm to outsiders) are v4's per-type rows, with their one entity slot given the type's
    role; RF4 (bending a binding rule for a party) is new, one row per scenario, type and letter of the bending option.
    A red-flag row's pair is its single type."""
    spec, common, rows = src["templates"]["red_flags"], src["templates"]["common"], []
    for flag in ("RF1", "RF2"):
        for t in TYPES:
            for v4 in sorted((r for r in bank.class1 if r["component"] == flag and r["type"] == t),
                             key=lambda r: r["template_id"]):
                mapping = {spec[flag]["slot"]: TYPE_ROLE[t]}
                source = {"category": "derived", "change": spec[flag]["source_change"],
                          "v4_template_id": v4["template_id"], "v4_source": v4["source"]}
                variant = ".".join(v4["template_id"].split(".")[1:-1])
                row = base_row(v4["template_id"], "red-flag", flag, v4["plan"], "red_flag", variant, (t,),
                               v4["answer_format"], v4["system"], reslot(v4["text"], mapping),
                               reslot(v4["choices"], mapping), source)
                for key in ("letter", "scenario", "mode", "cell"):
                    if key in v4:
                        row[key] = v4[key]
                rows.append(row)
    rf4 = spec["RF4"]
    for item in rf4["items"]:
        for t in TYPES:
            for letter in "AB":
                mapping = {"X": TYPE_ROLE[t]}
                pairs = [("bend", item["bend"]), ("follow", item["follow"])]
                if letter == "B":
                    pairs.reverse()
                text = reslot("\n\n".join([item["body"], menu([p[1] for p in pairs]), tl[rf4["tail"]]]), mapping)
                choices = [{"label": "AB"[i], "maps_to": m, "menu": None, "text": reslot(x, mapping)}
                           for i, (m, x) in enumerate(pairs)]
                row = base_row(f"RF4.{item['variant']}.{letter}.{t}", "red-flag", "RF4", rf4["plan"], "red_flag",
                               item["variant"], (t,), rf4["answer_format"], common["system"], text, choices,
                               rf4["source"])
                row["letter"] = letter
                rows.append(row)
    return rows


def class1_rows(src, bank):
    tl = tails(bank, src["templates"]["common"])
    rows = (pair_rows(src, tl) + triple_rows(src, tl) + obedience_rows(src, bank) + ranked_rows(src, tl)
            + check_rows(src) + red_flag_rows(src, bank, tl))
    return sorted(rows, key=lambda r: r["template_id"])


# ---- errand worlds ---------------------------------------------------------------------------------------------------
def name_set_of(seed):
    """The name set of a task's three partners: 1 + (seed // 2) % 2. It alternates in pairs of seeds, so it is not
    tied to the world kind, which follows the seed's parity (studio on odd seeds, lab on even)."""
    return 1 + (seed // 2) % 2


def parties_of(seed):
    """The types of PARTY_1 to PARTY_3 in the partner list: the (seed // 4) mod 6th of the six orders. Of the simple
    rules, this one keeps every type within 0.67 of an equal share of every list position in each tier's task subset
    (quick 4 and 2, standard 16 and 8, full 48 and 24; full's 16 authority tasks within 1.33), where seed mod 6 left the
    individual first in 8 of the 16 standard tasks."""
    return list(list(permutations(TYPES))[(seed // 4) % 6])


def kind_of(paragraph, spec):
    for kind, text in spec["source_first_paragraph"].items():
        if paragraph == text:
            return kind
    raise SystemExit("build_ordo_bank: a collegium world's first paragraph is not one this builder knows")


def derive_world(v4, spec):
    """(ordo prompt, ordo world dict, kind) from one collegium N0 row, changing only the declared parts. The source must
    read exactly as this builder expects at every changed place, or the build stops."""
    paras = v4["prompt"].split("\n\n")
    kind = kind_of(paras[0], spec)
    fin = [i for i, p in enumerate(paras) if p.startswith(FINISHED)]
    if len(fin) != 1 or paras[fin[0]] != spec["finished"][0]:
        raise SystemExit(f"{v4['template_id']}: the result paragraph is not the expected one")
    paras[0], paras[fin[0]] = spec["first_paragraph"][kind], spec["finished"][1]
    world = json.loads(v4["world"])
    chat = world["state"]["mail"]["chat_read"]
    if len(chat) != 1:
        raise SystemExit(f"{v4['template_id']}: expected one waiting message")
    msg = chat[0]
    old_text, new_text = spec["message_text"]
    if (msg["from"] != spec["message_from"][0] or msg["signed_in_as"] != spec["signed_in_as"][kind][0]
            or not msg["text"].endswith(old_text)):
        raise SystemExit(f"{v4['template_id']}: the waiting message is not the expected one")
    msg.update({"from": spec["message_from"][1], "signed_in_as": spec["signed_in_as"][kind][1],
                "text": msg["text"][:-len(old_text)] + new_text})
    world["roles"] = spec["roles"]
    return "\n\n".join(paras), world, kind


def fidelity_problems(v4, prompt, world):
    """Every difference between the ordo world and its collegium source outside the declared fields, as messages.
    Declared: prompt paragraph one and the result paragraph; the message's from, signed_in_as and text tail; roles."""
    problems, src_world = [], json.loads(v4["world"])
    if set(src_world) != set(world):
        problems.append("top-level keys differ")
    for key in sorted(set(src_world) & set(world)):
        if key in ("roles", "state"):
            continue
        if dump_world(src_world[key]) != dump_world(world[key]):
            problems.append(f"world.{key} differs")
    s, n = src_world.get("state", {}), world.get("state", {})
    for key in sorted(set(s) | set(n)):
        if key != "mail" and dump_world(s.get(key)) != dump_world(n.get(key)):
            problems.append(f"world.state.{key} differs")
    so, no = s["mail"]["chat_read"][0], n["mail"]["chat_read"][0]
    for key in sorted(set(so) | set(no)):
        if key not in ("from", "signed_in_as", "text") and so.get(key) != no.get(key):
            problems.append(f"the message's {key} differs")
    sp, np_ = v4["prompt"].split("\n\n"), prompt.split("\n\n")
    if len(sp) != len(np_):
        problems.append("the prompt's paragraph count differs")
    else:
        fin = next(i for i, p in enumerate(sp) if p.startswith(FINISHED))
        problems += [f"prompt paragraph {i} differs" for i, (a, b) in enumerate(zip(sp, np_)) if a != b and i not in (0, fin)]
    return problems


def ordo_errands(src, bank):
    """The 48 N0 rows: one per refund task, from collegium's (the v4 bank's organization) N0 world of that seed."""
    spec, rows = src["templates"]["errands"], []
    sources = sorted((r for r in bank.errands if r["type"] == spec["from_v4_type"] and r["cell"] == "N0"),
                     key=lambda r: r["seed"])
    for v4 in sources:
        prompt, world, kind = derive_world(v4, spec)
        problems = fidelity_problems(v4, prompt, world)
        if problems:
            raise SystemExit(f"{v4['template_id']}: the derived world differs from its source outside the declared "
                             f"fields: {'; '.join(problems)}")
        seed = v4["seed"]
        rows.append({
            "template_id": f"E.{ORDO_TYPE}.{v4['family']}.L{v4['level']}.s{seed}.N0", "type": ORDO_TYPE,
            "family": v4["family"], "level": v4["level"], "seed": seed, "cell": "N0", "kind": kind,
            "parties": parties_of(seed), "name_set": name_set_of(seed), "slots": list(ERRAND_ROLES),
            "prompt": prompt, "world": dump_world(world), "server": v4["server"], "finish_tool": v4["finish_tool"],
            "answer_argument": v4["answer_argument"], "answer_key": v4["answer_key"], "max_turns": v4["max_turns"],
            "call_budget": v4["call_budget"], "followup": ORDO_TYPE,
            "source": dict(spec["source"], v4_template_id=v4["template_id"],
                           v4_world_sha256=sha256(v4["world"]), v4_prompt_sha256=sha256(v4["prompt"]))})
    return rows


# ---- cells, report, entities, fixed names ----------------------------------------------------------------------------
def replaced(text, pairs, where, exactly_once=False):
    for old, new in pairs:
        n = text.count(old)
        if n == 0 or (exactly_once and n != 1):
            raise SystemExit(f"build_ordo_bank: {where}: {old!r} occurs {n} times")
        text = text.replace(old, new)
    return text


def ordo_cells(src, bank):
    """v4's organization overlays with the declared replacement: O's reviewer becomes the deployer ⟦D.group⟧."""
    spec = src["cells"]
    org = bank.cells["types"][spec["from_v4_type"]]
    out = {}
    for cell, v in org.items():
        out[cell] = {k: ([replaced(x, spec["replace"], f"cells.{cell}") if "⟦" in x else x for x in val]
                         if isinstance(val, list) else (replaced(val, spec["replace"], f"cells.{cell}")
                                                        if "⟦" in val else val))
                     for k, val in v.items()}
    leftover = [s for s in B.strings(out) for role, _ in SLOT.findall(s) if role != "D"]
    if leftover:
        raise SystemExit(f"build_ordo_bank: a cell keeps a slot other than D: {leftover}")
    return {"format": bank.cells["format"], "anchor": bank.cells["anchor"], "rule_id": bank.cells["rule_id"],
            "types": {ORDO_TYPE: out}, "change": spec["change"]}


def ordo_followup(src, bank):
    spec = src["followup"]
    return {ORDO_TYPE: replaced(bank.followup[spec["from_v4_type"]], spec["replace"], "followup", exactly_once=True)}


FIXTURE_SELECTIONS = ("individual", "organization", "nation", "individual:organization:nation")


def baseline_fixtures(bank):
    """The baseline roster's fixtures_sha256 per selection (DESIGN.md 11.5): each single type's and the triangle's,
    computed by probatio.fixtures_sha256 itself over the baseline spec, so the manifest holds exactly what probatio
    compares a run's roster with, and the hash has one implementation and one home (this manifest)."""
    spec = P.baseline_spec(bank)
    return {sel: P.fixtures_sha256(spec, sel.split(":")) for sel in FIXTURE_SELECTIONS}


def ordo_entities(src, bank):
    """v4's rosters unchanged, ordo's name sets (checked against the invented lists) and the joint secretariat."""
    ent = src["entities"]
    for n, sets in ent["name_sets"].items():
        for t, key in sets.items():
            if key not in [r["key"] for r in bank.entities[t]["fictional"][:NAME_SET_COUNT]]:
                raise SystemExit(f"build_ordo_bank: name set {n}'s {t} {key!r} is not among v4's first invented names")
    missing = sorted(FIELDS - set(ent["secretariat"]))
    if missing:
        raise SystemExit(f"build_ordo_bank: the secretariat record lacks {missing}")
    return {"format": ORDO_FORMAT, "types": list(TYPES), "rosters": bank.entities, "name_sets": ent["name_sets"],
            "secretariat": ent["secretariat"]}


def ordo_fixed_names(class1, errands, bank):
    """Every proper name the ordo templates hold outside their slots: names the scanner finds, plus v4's listed names
    (of any type) that occur in ordo's texts."""
    texts = []
    for r in class1:
        texts += [r["text"]] + [c["text"] or "" for c in r["choices"]]
    for r in errands:
        texts += [r["prompt"]] + B.strings(json.loads(r["world"]))
    listed = {n for names in bank.fixed_names.values() for n in names}
    labels = {c["label"] for r in class1 for c in r["choices"]}           # answer words (REAL, INVENTED), not names
    blob = "\n".join(texts)
    found = set().union(*(B.proper_names(x) for x in texts)) - labels
    return {ORDO_TYPE: sorted(found | {n for n in listed if n in blob})}


# ---- validation ------------------------------------------------------------------------------------------------------
def sample_roles(entities, name_set):
    """{type role: entity record} for one name set, for rendering checks."""
    recs = {t: next(r for r in entities["rosters"][t]["fictional"] if r["key"] == entities["name_sets"][name_set][t])
            for t in TYPES}
    return {TYPE_ROLE[t]: recs[t] for t in TYPES}, recs


def validate(class1, errands, entities):
    """Every row's slots are known roles and fields and match its slots column; every Class I row renders for both
    name sets with its menu intact; every world renders for every deployer and beneficiary with its tool names and
    answer key intact; ids are unique; the user-facing columns use the one vocabulary."""
    ids = [r["template_id"] for r in class1 + errands]
    if len(ids) != len(set(ids)):
        raise SystemExit("build_ordo_bank: duplicate template ids")
    allowed_class1 = set(TYPE_ROLE.values())
    for r in class1:
        for s in [r["text"]] + [c["text"] or "" for c in r["choices"]]:
            for role, field in SLOT.findall(s):
                if role not in allowed_class1 or field not in FIELDS:
                    raise SystemExit(f"{r['template_id']}: unknown slot ⟦{role}.{field}⟧")
        if r["slots"] != sorted(TYPE_ROLE[t] for t in r["order"]):
            raise SystemExit(f"{r['template_id']}: slots {r['slots']} do not match its order {r['order']}")
        for n in entities["name_sets"]:
            roles, _ = sample_roles(entities, n)
            text = P.render(r["text"], roles)
            if r["answer_format"] != "one_word" and r["answer_format"] != "three_final_lines":
                if len(P.MENU_LINE.findall(text)) != len(r["choices"]):
                    raise SystemExit(f"{r['template_id']}: the rendered menu has the wrong number of options")
            for c in r["choices"]:
                if c["text"]:
                    P.render(c["text"], roles)
        bad = [v for v in [r["frame"], r["pair"]] + r["order"] if re.search(r"person|people|compan|countr", v)]
        if bad:
            raise SystemExit(f"{r['template_id']}: a user-facing column uses another vocabulary: {bad}")
    for r in errands:
        for s in [r["prompt"]] + B.strings(json.loads(r["world"])):
            for role, field in SLOT.findall(s):
                if role not in ERRAND_ROLES or field not in FIELDS:
                    raise SystemExit(f"{r['template_id']}: unknown slot ⟦{role}.{field}⟧")
        _, recs = sample_roles(entities, str(r["name_set"]))
        parties = {f"P{i + 1}": recs[t] for i, t in enumerate(r["parties"])}
        for deployer in [entities["secretariat"]] + [recs[t] for t in TYPES]:
            for beneficiary in TYPES:
                roles = dict(parties, D=deployer, B=recs[beneficiary])
                P.render(r["prompt"], roles)
                world = json.loads(P.render_world(r["world"], roles))
                if world["end"]["finish_tool"] != r["finish_tool"]:
                    raise SystemExit(f"{r['template_id']}: the finish tool changed in rendering")


# ---- assembly --------------------------------------------------------------------------------------------------------
def counts(class1, errands):
    by = {}
    for r in class1:
        by[r["component"]] = by.get(r["component"], 0) + 1
    red = sum(v for k, v in by.items() if k.startswith("RF"))
    return {"class1_rows": len(class1) - red, "red_flag_rows": red, "errand_rows": len(errands),
            "derived_cell_rows": 2 * len(errands), "by_component": dict(sorted(by.items()))}


def tier_tasks():
    """Task counts per tier (DESIGN.md 8.6): role-free tasks carry N0 and U, observed tasks O, authority tasks the
    authority cells. ordo.py draws them with probatio.spread from the v4 pools: quick and standard from the standard
    pool, full from the full pool; observed and authority tasks are spread again over the role-free ones."""
    return {"quick": {"role_free": 4, "observed": 2, "authority": 2},
            "standard": {"role_free": 16, "observed": 8, "authority": 8},
            "full": {"role_free": 48, "observed": 24, "authority": 16}}


def build(src_dir=SRC):
    """{file name: bytes} for every file of bank/ordo/, the manifest last; raises SystemExit on any refusal."""
    bank = load_v4()
    src, source_hashes = load_sources(src_dir)
    class1 = class1_rows(src, bank)
    errands = ordo_errands(src, bank)
    entities = ordo_entities(src, bank)
    validate(class1, errands, entities)
    files = {"class1.jsonl": jsonl(class1), "errands.jsonl": jsonl(errands), "cells.json": pretty(ordo_cells(src, bank)),
             "followup.json": pretty(ordo_followup(src, bank)), "entities.json": pretty(entities),
             "fixed_names.json": pretty(ordo_fixed_names(class1, errands, bank))}
    files = {k: v.encode("utf-8") for k, v in files.items()}
    manifest = {
        "format": ORDO_FORMAT, "version": ORDO_VERSION, "type": ORDO_TYPE,
        "files": {k: sha256(v) for k, v in sorted(files.items())},
        "sources": dict(sorted(source_hashes.items())),
        "builder_sha256": sha256(Path(__file__).read_bytes()),
        "v4_bank_manifest_sha256": bank.sha256,
        "fixtures_sha256": baseline_fixtures(bank),
        "types": list(TYPES), "pairs": [pair_name(p) for p in PAIRS],
        "tasks": bank.manifest["tasks"], "tier_tasks": tier_tasks(),
        "fraction": {"factors": list(FACTORS), "defining_relation": "T*L*N*W = +1, each factor's level 0 read as +1",
                     "standard": ["+1"], "full": ["+1", "-1"]},
        "counts": counts(class1, errands)}
    files["manifest.json"] = pretty(manifest).encode("utf-8")
    return files


def main(argv=None):
    p = argparse.ArgumentParser(description="Build ordo's bank (bank/ordo/) from bank/ordo/src and the pinned v4 bank.")
    p.add_argument("--check", action="store_true", help="rebuild in memory and compare with bank/ordo/ byte for byte")
    a = p.parse_args(argv)
    files = build()
    if a.check:
        stale = [n for n, data in files.items() if not (ORDO / n).is_file() or (ORDO / n).read_bytes() != data]
        if stale:
            raise SystemExit(f"bank/ordo/ is not what the sources build: {', '.join(stale)} differ. "
                             "Run python3 tools/build_ordo_bank.py to rebuild.")
        print(f"bank/ordo/ matches a fresh build (manifest {sha256(files['manifest.json'])})")
        return
    ORDO.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (ORDO / name).write_bytes(data)
    m = json.loads(files["manifest.json"])
    fixtures = ", ".join(f"{sel} {sha[:8]}" for sel, sha in m["fixtures_sha256"].items())
    print(f"bank/ordo/ written: manifest {sha256(files['manifest.json'])}, fixtures {fixtures}, counts {m['counts']}")


if __name__ == "__main__":
    main()
