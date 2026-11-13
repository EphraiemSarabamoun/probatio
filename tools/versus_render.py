#!/usr/bin/env python3
"""Render versus's scenarios exactly as a model will see them, so every writer and reviewer reads the same text.

versus (docs/versus/DESIGN.md) asks a model questions about the user's own person, organization and government. Its
source is bank/versus/src/: common.json holds the text every item shares (the preamble, the party key, the lottery
option, the footers, the role sentences), and families/<family>.json, triads.json and ties.json hold the scenarios.
This tool fills each scenario's slots with a roster of three names and writes every rendering the design asks for:

    allocation item   every ordered pair of types in slots A and B (6)
    conflict item     every pair of types and the eight runs of the design: T (which name deploys), L (which letter
                      the deployer's option takes), N (who is introduced first) and the job wording W = T x L x N,
                      the registered half of ordo's fraction (3 x 8 = 24)
    triad             the fixed roles in all six listing orders (6)
    tie control       each type in slot A with both label orders (6)

Conflicts are assembled by ordo's own ranked_text (tools/build_ordo_bank.py, read-only), so their paragraph, request
and option order is ordo's builder's; only versus's forms and fixed text differ.

    python3 tools/versus_render.py                        every rendering, default roster, to stdout
    python3 tools/versus_render.py --roster long --out DIR   one file per list
    python3 tools/versus_render.py --skeletons | --counts | --checks | --hashes
    python3 tools/versus_render.py --fixed                one sheet of every piece of fixed text
    python3 tools/versus_render.py --sheets DIR [--writers FILE]   the second reader's packet

Every full rendering is headed by the system message (common.json's one copy, which must be exactly SYSTEM_MESSAGE)
and then the user message; the rendering hash a review record cites covers the user message, as it always has, and the
system message is held, with the rest of the fixed text, by reviews/fixed-text.json (check_fixed_review). The system
message is recorded, not sent in an agent run (ruling R8): probatio tests the user's own agent, whose own system prompt
and memory are what is measured; a harness that calls a model directly may send it. SYSTEM_NOTE says so wherever it
is printed.

The default roster is "Jane Doe", "Acme Corp", "Norland"; --roster long is a length test, invented and with as few
cues of wealth, size or sector as a long name allows (a long hyphenated personal name, a long company name that
contains "and", a nation whose name begins with "the" and contains "and"); --names PERSON ORG NATION gives any other.
Python 3.9, standard library only; no network, no model.
"""
import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from itertools import permutations, product
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
import build_ordo_bank as OB  # noqa: E402  (read-only reuse: ranked_text, menu, PAIRS, half_of)
import versus as VS  # noqa: E402  (the entity forms' one home)

SRC = REPO / "bank" / "versus" / "src"
TYPES = ("individual", "organization", "nation")
ROLE = {"individual": "IND", "organization": "ORG", "nation": "NAT"}
ROSTERS = {
    "default": {"individual": "Jane Doe", "organization": "Acme Corp", "nation": "Norland"},
    # invented: no real person, company or country; the organization's "and" is deliberate (a bare-join test)
    "long": {"individual": "Marguerite Vandersteen-Albrightson",
             "organization": "Harrowgate Northfield and Pellbrook Company",
             "nation": "the United Provinces of Corvania and Saint Almery"},
}
# 5.2 point 11: every body is tested with a name that contains "and"
AND_ROSTER = {"individual": "Jane Doe", "organization": "Smith and Jones", "nation": "Norland"}
SLOT = re.compile(r"⟦([A-Za-z][A-Za-z0-9]*)\.([A-Za-z_]+)⟧")
LISTS = ("allocation", "conflict", "triad", "tie")
SYSTEM_MESSAGE = "You are a helpful assistant."      # design 5: the same as ordo's
SYSTEM_NOTE = "recorded in the bank; not sent in an agent run"   # ruling R8: how sheets and renderings describe it
FIXED_TEXT_ID = "fixed-text"     # reviews/fixed-text.json: the fixed text's review record, in the bank builder's
#                                  shape (tools/build_versus_bank.py on the build branch); no scenario takes this id
FIXED_STATUS = "awaiting second reading"
FIXED_RECORD_FORMAT = "probatio-versus-review-2"      # the format every review record of this source has
# the second reader's packet (--sheets): which reading list holds which scenarios; one reader sees all of a family
READINGS = {1: {"families": ("scheduling", "queues"), "lists": ("tie",), "fixed": True},
            2: {"families": ("budget-lines", "travel-funds"), "lists": (), "fixed": False},
            3: {"families": ("quotas", "review-slots"), "lists": (), "fixed": False},
            4: {"families": ("document-order", "data-access"), "lists": (), "fixed": False},
            5: {"families": (), "lists": ("triad",), "fixed": False}}
# a sheet's complete renderings: each type once in the first seat and once in the second (an allocation's slot A,
# a conflict's deployer, a tie's one party); then one on the long roster; the rest as the lines that carry the forms
SHEET_SEATS = (("individual", "organization"), ("organization", "nation"), ("nation", "individual"))
SHEET_LONG_SEAT = ("nation", "organization")
SHEET_TIE_FULL = ((0, 0), (1, 1), (2, 0))       # a tie's complete renderings: (type index, label order index)
SHEET_TIE_LONG = (2, 1)
SHEET_TRIAD_FULL = (0, 5)                        # a triad's two complete listings, as indices of permutations(TYPES)
SPARE_RULING = "spare by the manager's ruling"   # how a record's "ruling" field marks a spare
SHEET_NOTE = {           # what a sheet shows, in its head (sheet_selection chooses the renderings)
    "allocation": "3 complete renderings, default roster (each type once in slot A, once in slot B); 1 on the long "
                  "roster; the other 3 assignments as their lines with a party's form",
    "conflict": "3 complete renderings, default roster (each type once as deployer, once as client; L, N and W each "
                "at both levels); 1 on the long roster; the other 3 assignments as their lines with a party's form",
    "triad": "2 complete listings, default roster; the other 4 listings as the lines that differ from the first (the "
             "long roster's forms are on the fixed sheet)",
    "tie": "3 complete renderings, default roster (each type once, both label orders); 1 on the long roster; the "
           "other 3 as their lines with the party's form or a label",
}


# ---- source --------------------------------------------------------------------------------------------------------
def load_source(src=SRC):
    """The whole source as {"common": ..., "allocation": [...], "conflict": [...], "triad": [...], "tie": [...],
    "spares": [...]}. Family files are read in the order common.json lists the families, so renders and hashes are
    deterministic. "spares" gathers the ids the family files and triads.json list as spares: they are rendered and
    reviewed like any scenario, and the bank builder leaves them out of the bank."""
    src = Path(src)
    common = json.loads((src / "common.json").read_text(encoding="utf-8"))
    out = {"common": common, "allocation": [], "conflict": [], "triad": [], "tie": [], "spares": []}
    for fam in common["families"]:
        data = json.loads((src / "families" / f"{fam}.json").read_text(encoding="utf-8"))
        out["allocation"] += data["allocation_items"]
        out["conflict"] += data["conflict_items"]
        out["spares"] += data.get("spares", [])
    triads = json.loads((src / "triads.json").read_text(encoding="utf-8"))
    out["triad"] = triads["triad_items"]
    out["spares"] += triads.get("spares", [])
    out["tie"] = json.loads((src / "ties.json").read_text(encoding="utf-8"))["tie_controls"]
    return out


def scenarios(source):
    """Every (list, item) pair in render order."""
    return [(lst, item) for lst in LISTS for item in source[lst]]


# ---- forms, slots and the fixed text -------------------------------------------------------------------------------
RENDER_FIELDS = ("full", "Full", "group", "Group", "country")       # the fields a source scenario may use


def forms(type_, name):
    """versus's forms (DESIGN.md 4), from their one home, versus.entity_forms: the fields a source scenario may use,
    one form in every field, .Group/.Full the sentence-start version; .country, the bare country name, exists for a
    nation only."""
    rec = VS.entity_forms(type_, name)
    return {k: rec[k] for k in RENDER_FIELDS if k in rec}


def fill(text, roles):
    """Replace every ⟦ROLE.field⟧ slot; a slot left over is an error, never a silent blank."""
    out = SLOT.sub(lambda m: roles[m.group(1)][m.group(2)], text)
    if "⟦" in out or "⟧" in out:
        raise ValueError(f"unfilled slot in: {out}")
    return out


def key_line(listing, roster, common):
    """The party key (DESIGN.md 4): each party in the form the item uses, with its kind, in listing order."""
    k = common["fixed_text"]["party_key"]
    entries = [k["entry"].format(name=forms(t, roster[t])["group"], kind=k["kind"][t]) for t in listing]
    return k["prefix"] + k["separator"].join(entries) + k["end"]


def assemble(common, listing, roster, body, options, footer):
    """One item as the model sees it: preamble, party key, body, lettered options, footer, blank-line separated."""
    return "\n\n".join([common["fixed_text"]["preamble"], key_line(listing, roster, common), body,
                        OB.menu(options, labels="ABC"), footer])


def system_message(common, source=None):
    """The system message, after checking that common.json holds it once and exactly as SYSTEM_MESSAGE (and, given
    the whole source, that no scenario carries a copy of its own). A ValueError names what is wrong."""
    text = common.get("fixed_text", {}).get("system_message")
    if text != SYSTEM_MESSAGE:
        raise ValueError(f"common.json's fixed_text.system_message is {text!r}, not {SYSTEM_MESSAGE!r}")
    copies = json.dumps(common, ensure_ascii=False).count(json.dumps(SYSTEM_MESSAGE)[1:-1])
    if source is not None:
        copies += sum(json.dumps(item, ensure_ascii=False).count(SYSTEM_MESSAGE) for _, item in scenarios(source))
    if copies != 1:
        raise ValueError(f"the source holds {copies} copies of the system message, where one is expected")
    return text


def with_system(text, common):
    """A rendering as a model receives it: the system message (SYSTEM_NOTE: recorded, not sent in an agent run),
    then the user message, each under a heading line the model never sees."""
    return f"--- system message ({SYSTEM_NOTE})\n{common['fixed_text']['system_message']}\n--- user message\n{text}"


# ---- one function per list -----------------------------------------------------------------------------------------
def render_allocation(item, roster, common):
    """Six renderings, one per ordered type pair; the builder appends option C and the lottery footer."""
    ft, out = common["fixed_text"], []
    for a, b in permutations(TYPES, 2):
        roles = {"A": forms(a, roster[a]), "B": forms(b, roster[b])}
        body = fill(item["body"], roles)
        options = [fill(o, roles) for o in item["options"]] + [ft["option_c"]]
        out.append({"run": f"A={a} B={b}", "listing": (a, b), "body": body, "options": options, "jobs": [],
                    "labels": [], "text": assemble(common, (a, b), roster, body, options, ft["footer_lottery"])})
    return out


def conflict_runs():
    """The eight runs of a conflict per pair: every T, L, N with W = T xor L xor N (ordo's registered half)."""
    return [(T, L, N, T ^ L ^ N) for T, L, N in product((0, 1), repeat=3)]


def render_conflict(item, roster, common):
    """24 renderings: each pair of types and its eight runs, assembled by ordo's ranked_text with versus's role
    sentences, preamble and footer, then the party key inserted after the preamble."""
    ft, rs = common["fixed_text"], common["role_sentences"]
    grammar = dict(item, deployer_sentence=rs["deployer"], client_sentence=rs["client"])
    out = []
    for a, b in OB.PAIRS:
        for T, L, N, W in conflict_runs():
            dep, cli = (a, b) if T == 0 else (b, a)
            text, options, lettered = OB.ranked_text(grammar, N == 0, W, L, {"survey_preamble": ft["preamble"]},
                                                     {"forced": ft["footer_conflict"]})
            roles = {"DEP": forms(dep, roster[dep]), "CLI": forms(cli, roster[cli])}
            body = fill(text.split("\n\n")[1], roles)
            options = [fill(o, roles) for o in options]
            listing = (dep, cli) if N == 0 else (cli, dep)
            jobs = [w[k] for w in item["wordings"] for k in ("a", "the")]
            out.append({"run": f"pair={a}:{b} dep={dep} cli={cli} T{T} L{L} N{N} W{W}", "listing": listing,
                        "design": {"T": T, "L": L, "N": N, "W": W}, "body": body, "options": options, "jobs": jobs,
                        "labels": [], "text": assemble(common, listing, roster, body, options, ft["footer_conflict"])})
    return out


def render_triad(item, roster, common):
    """Six renderings: the fixed roles (ORG deployed, IND the user, NAT the jurisdiction's government) with the jobs
    and the options rotating together through every listing order (ordo F2.tri); the key follows that order."""
    roles = {ROLE[t]: forms(t, roster[t]) for t in TYPES}
    out = []
    for order in permutations(TYPES):
        jobs = ", ".join(item["jobs"][t] for t in order[:2]) + " and " + item["jobs"][order[2]]
        body = fill(" ".join([item["setting"], common["role_sentences"]["triad"],
                              item["lead"].replace("{jobs}", jobs), item["closing"]]), roles)
        options = [fill(item["option_by_type"][t], roles) for t in order]
        out.append({"run": "order=" + "-".join(order), "listing": order, "body": body, "options": options,
                    "jobs": [], "labels": [],
                    "text": assemble(common, order, roster, body, options, common["fixed_text"]["footer_triad"])})
    return out


def render_tie(item, roster, common):
    """Six renderings: each type in slot A, the two labels in both orders; option C and the lottery footer as for an
    allocation item."""
    ft, out = common["fixed_text"], []
    for t in TYPES:
        for first, second in (item["labels"], item["labels"][::-1]):
            roles = {"A": forms(t, roster[t])}

            def sub(s, first=first, second=second):
                """s with its {first} and {second} label slots filled in this rendering's order."""
                return s.replace("{first}", first).replace("{second}", second)
            body = fill(sub(item["body"]), roles)
            options = [fill(sub(o), roles) for o in item["options"]] + [ft["option_c"]]
            out.append({"run": f"A={t} labels={first},{second}", "listing": (t,), "body": body, "options": options,
                        "jobs": [], "labels": [first, second],
                        "text": assemble(common, (t,), roster, body, options, ft["footer_lottery"])})
    return out


RENDER = {"allocation": render_allocation, "conflict": render_conflict, "triad": render_triad, "tie": render_tie}


def render(lst, item, roster, common):
    """Every rendering of one scenario, in the canonical order the hash and the files use."""
    return RENDER[lst](item, roster, common)


# ---- what a reviewer asks of the renders ---------------------------------------------------------------------------
def render_hash(renders):
    """sha256 of a scenario's renderings: each rendering's text, in canonical order, joined by a line of five '='
    between blank lines. This is the hash a review record cites (DESIGN.md 5.2)."""
    joined = "\n\n=====\n\n".join(r["text"] for r in renders)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def party_forms(listing, roster):
    """Every surface form of the parties in a rendering, longest first, for name removal."""
    found = set()
    for t in listing:
        found.update(forms(t, roster[t]).values())
    return sorted(found, key=len, reverse=True)


def skeleton(text, names=(), jobs=(), labels=()):
    """An option or request with party forms replaced by <P>, registered job wordings by <J> and labels by <L>."""
    for n in names:
        text = text.replace(n, "<P>")
    for j in sorted(jobs, key=len, reverse=True):
        text = text.replace(j, "<J>")
    for lab in labels:
        text = text.replace(lab, "<L>")
    return text


def option_skeletons(lst, r, roster):
    """The action skeletons of a rendering's scenario options (not option C)."""
    n = 3 if lst == "triad" else 2
    names = party_forms(r["listing"], roster)
    return [skeleton(o, names, r["jobs"], r["labels"]) for o in r["options"][:n]]


def request_skeletons(lst, item, r, roster):
    """The skeletons of the requests in a rendering (5.2 point 4: the same verb for every request): a conflict's two
    request sentences, an allocation item's two sentences that open with a party's name, a triad's three job phrases.
    A tie has one party and no competing request."""
    names = party_forms(r["listing"], roster)
    if lst == "conflict":
        sides = [forms(t, roster[t])["Group"] for t in r["listing"]]
        texts = [s for s in re.split(r"(?<=\.) ", r["body"]) if any(s.startswith(g + " asks you") for g in sides)]
    elif lst == "allocation":
        starts = tuple(forms(t, roster[t])["Group"] for t in r["listing"])
        texts = [s for s in re.split(r"(?<=\.) ", r["body"]) if s.startswith(starts)]
    elif lst == "triad":
        texts = [fill(item["jobs"][t], {ROLE[u]: forms(u, roster[u]) for u in TYPES}) for t in TYPES]
    else:
        texts = []
    return [skeleton(t, names, r["jobs"], r["labels"]) for t in texts]


PRONOUN = re.compile(r"\b(they|their|theirs|them|themselves|themself|it|its|itself|he|him|his|himself|she|her|hers|"
                     r"herself)\b", re.I)
# relative, demonstrative and indefinite words a reader must classify, never failures; the person-coded indefinites
# (anyone ... everybody) are among them, since one can read as standing for a party
OTHER = re.compile(r"\b(who|whom|whose|which|that|each|either|neither|both|whoever|whichever|anyone|anybody|someone|"
                   r"somebody|everyone|everybody)\b", re.I)


def pronoun_hits(r, roster):
    """Every pronoun-like word in a rendering's key, body and options, with its context and party names as <P>.
    Personal pronouns are reported apart from relative, demonstrative and indefinite ones, which a reader must
    classify (point 10 forbids only those that stand for a party)."""
    names = party_forms(r["listing"], roster)
    parts = r["text"].split("\n\n")
    hits = []
    for part_name, part in zip(("preamble", "key", "body", "options", "footer"), parts):
        templ = skeleton(part, names).replace("\n", " / ")
        for kind, rx in (("personal", PRONOUN), ("other", OTHER)):
            for m in rx.finditer(templ):
                hits.append((kind, part_name, m.group().lower(), templ[max(0, m.start() - 35):m.end() + 35]))
    return hits


def bare_joins(r, roster):
    """Two party forms joined as one phrase ("X and Y", "X, Y", "X or Y", "both X and Y"): point 11 forbids them."""
    names, found = party_forms(r["listing"], roster), []
    for p in names:
        for q in names:
            if p.lower() == q.lower() or p in q or q in p:
                continue
            for m in re.finditer(re.escape(p) + r"(?:,| and| or| &)(?: both)? " + re.escape(q), r["text"]):
                found.append(m.group())
    return found


def family_counts(source):
    """Scenarios per family in each list, spares not counted, against the design's targets (six allocation and six
    conflict per family, twelve triads, four ties), and what each family still owes."""
    out, spare = {}, set(source.get("spares", ()))
    for fam in source["common"]["families"]:
        a = sum(1 for i in source["allocation"] if i["family"] == fam and i["id"] not in spare)
        c = sum(1 for i in source["conflict"] if i["family"] == fam and i["id"] not in spare)
        out[fam] = {"allocation": a, "conflict": c, "owed_allocation": max(0, 6 - a), "owed_conflict": max(0, 6 - c)}
    triads = [i for i in source["triad"] if i["id"] not in spare]
    out["triads"] = {"have": len(triads), "owed": max(0, 12 - len(triads))}
    out["ties"] = {"have": len(source["tie"]), "owed": max(0, 4 - len(source["tie"]))}
    return out


# ---- reports -------------------------------------------------------------------------------------------------------
def report_renders(source, roster, lists, only):
    """Every rendering as text, one block per rendering, headed by a line the model never sees, then the system
    message and the user message (with_system)."""
    blocks = []
    common = source["common"]
    system_message(common, source)
    for lst, item in scenarios(source):
        if lst in lists and (not only or item["id"] == only):
            for r in render(lst, item, roster, common):
                blocks.append(f"===== {item['id']} | {lst} | {item['family']} | {r['run']}\n"
                              f"{with_system(r['text'], common)}\n")
    return "\n".join(blocks)


def report_skeletons(source, roster):
    """One line per scenario: IDENTICAL when every option (and, in a conflict, every request) of every rendering
    reduces to one skeleton."""
    lines = []
    for lst, item in scenarios(source):
        sks, rqs, ok = set(), set(), True
        for r in render(lst, item, roster, source["common"]):
            sks.update(option_skeletons(lst, r, roster))
            rq = request_skeletons(lst, item, r, roster)
            ok &= lst == "tie" or len(rq) == {"conflict": 2, "allocation": 2, "triad": 3}[lst]
            rqs.update(rq)
        ok &= len(sks) == 1 and (lst == "tie" or len(rqs) == 1)
        lines.append(f"{lst:10s} {item['id']:34s} {'IDENTICAL' if ok else 'DIFFERENT'}  options: "
                     + " | ".join(sorted(sks)) + ("  requests: " + " | ".join(sorted(rqs)) if rqs else ""))
    return "\n".join(lines)


def report_counts(source):
    """--counts: scenarios per family in each list and what each family still owes, as a table."""
    counts = family_counts(source)
    lines = [f"{'family':16s} allocation conflict  owed: allocation conflict"]
    for fam in source["common"]["families"]:
        c = counts[fam]
        lines.append(f"{fam:16s} {c['allocation']:10d} {c['conflict']:8d}  {c['owed_allocation']:16d} "
                     f"{c['owed_conflict']:8d}")
    lines.append(f"triads {counts['triads']['have']} (owed {counts['triads']['owed']}); "
                 f"ties {counts['ties']['have']} (owed {counts['ties']['owed']})")
    return "\n".join(lines)


def report_checks(source, roster):
    """Personal pronouns anywhere outside the fixed text, the other pronoun words with context, and bare name joins
    for this roster and for a roster whose organization contains "and"."""
    personal, other, joins = Counter(), Counter(), []
    for lst, item in scenarios(source):
        for ros in (roster, AND_ROSTER):
            for r in render(lst, item, ros, source["common"]):
                joins += [f"{item['id']} {r['run']}: {j}" for j in bare_joins(r, ros)]
                if ros is roster:
                    for kind, part, word, ctx in pronoun_hits(r, ros):
                        (personal if kind == "personal" else other)[(part, word, ctx)] += 1
    lines = ["personal pronouns (part, word, context, count):"]
    lines += [f"  [{p}] {w!r} x{n}: ...{c}..." for (p, w, c), n in sorted(personal.items())] or ["  none"]
    in_items = sum(n for (p, _, _), n in personal.items() if p in ("key", "body", "options"))
    lines.append(f"personal pronouns in a party key, body or option: {in_items}")
    lines.append("relative, demonstrative and indefinite pronouns (a reader classifies each):")
    lines += [f"  [{p}] {w!r} x{n}: ...{c}..." for (p, w, c), n in sorted(other.items())] or ["  none"]
    lines.append("bare joins of two parties (this roster and one with 'Smith and Jones'):")
    lines += [f"  {j}" for j in joins] or ["  none"]
    return "\n".join(lines)


def report_hashes(source, roster):
    """--hashes: one line per scenario, its id and the render hash a review record cites (render_hash)."""
    return "\n".join(f"{item['id']} {render_hash(render(lst, item, roster, source['common']))}"
                     for lst, item in scenarios(source))


# ---- the fixed text: one sheet, one record ---------------------------------------------------------------------------
def role_lines(template, slots, common):
    """A role sentence with its slots visible, then filled for each type that can take each slot, on both rosters;
    each filled line is prefixed by a bracketed label the model never sees."""
    lines = [f"[template] {template}"]
    for rname in ("default", "long"):
        roster = ROSTERS[rname]
        if slots == "triad":
            lines.append(f"[{rname} roster] " + fill(template, {ROLE[t]: forms(t, roster[t]) for t in TYPES}))
            continue
        for t in TYPES:
            lines.append(f"[{rname} roster, {common['fixed_text']['party_key']['kind'][t]}] "
                         + fill(template, {slots: forms(t, roster[t])}))
    return "\n".join(lines)


def entity_lines(type_, common):
    """One type's forms on both rosters: its party-key entry, its form in a field, at a sentence start, and (a
    nation) the bare country name a triad's jurisdiction sentence uses."""
    k, lines = common["fixed_text"]["party_key"], []
    for rname in ("default", "long"):
        f = forms(type_, ROSTERS[rname][type_])
        lines.append(f"[{rname} roster] in the party key: {k['entry'].format(name=f['group'], kind=k['kind'][type_])}")
        lines.append(f"[{rname} roster] in a field: {f['group']}")
        lines.append(f"[{rname} roster] at a sentence start: {f['Group']}")
        if "country" in f:
            lines.append(f"[{rname} roster] the country, where a deployment runs: {f['country']}")
    return "\n".join(lines)


def fixed_pieces(common):
    """Every piece of fixed text as [(name, text)], in sheet order, each exactly as a model sees it (a role sentence or
    an entity form also with its slots, under bracketed labels the model never sees)."""
    ft, rs = common["fixed_text"], common["role_sentences"]
    key = "\n".join(f"[{r} roster] " + key_line(TYPES, ROSTERS[r], common) for r in ("default", "long"))
    system = f"{ft['system_message']}\n[{SYSTEM_NOTE}; a harness that calls a model directly may send it]"
    pieces = [("system message", system), ("preamble", ft["preamble"]),
              ("party key, in an item's listing order (here person, organization, government)", key),
              ("allocation and tie: the third answer", OB.menu(["", "", ft["option_c"]], labels="ABC").split("\n")[2]),
              ("allocation and tie: the footer", ft["footer_lottery"]),
              ("conflict: the footer", ft["footer_conflict"]),
              ("triad: the footer", ft["footer_triad"]),
              ("conflict: the deployer's role sentence", role_lines(rs["deployer"], "DEP", common)),
              ("conflict: the client's role sentence", role_lines(rs["client"], "CLI", common)),
              ("triad: the role sentences", role_lines(rs["triad"], "triad", common))]
    pieces += [(f"entity forms: {common['fixed_text']['party_key']['kind'][t]}", entity_lines(t, common))
               for t in TYPES]
    return pieces


def fixed_sheet(common):
    """The fixed sheet (--fixed): every piece under a heading line, blank-line separated."""
    head = ("versus: every piece of fixed text, exactly as a model sees it. Heading lines (==) and bracketed labels "
            "are the sheet's own; everything else is the model's text.")
    return "\n\n".join([head] + [f"== {name}\n{text}" for name, text in fixed_pieces(common)]) + "\n"


def fixed_text_version(common):
    """The fixed text's version, as the bank builder computes it (its fixed_version): the sha256 of the fixed text,
    the role sentences and the system message as canonical JSON (keys sorted, no ASCII escaping)."""
    blob = json.dumps({"fixed_text": common["fixed_text"], "role_sentences": common["role_sentences"],
                       "system": system_message(common)}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def fixed_review_record(common):
    """A fresh fixed-text record in the builder's shape (id and list FIXED_TEXT_ID, tied to its text by
    fixed_text_version), awaiting a second reading, with each piece of the fixed sheet named so that a reader can
    note on it; the builder's shape has one hash for the whole, so no piece carries its own."""
    return {"format": FIXED_RECORD_FORMAT, "id": FIXED_TEXT_ID, "list": FIXED_TEXT_ID,
            "fixed_text_version": fixed_text_version(common),
            "fixed_text_version_rule": "sha256 of the canonical JSON (keys sorted, no ASCII escaping) of common.json's "
                                       "fixed_text and role_sentences and the system message: tools/versus_render.py "
                                       "fixed_text_version, the bank builder's fixed_version",
            "status": FIXED_STATUS,
            "pieces": [{"name": n, "status": FIXED_STATUS, "notes": []} for n, _ in fixed_pieces(common)]}


def check_fixed_review(common, src=SRC):
    """What is wrong with reviews/fixed-text.json against the fixed text now: no record; another id or list; a
    fixed_text_version that is not the current text's (so a later edit to fixed text shows); or pieces that are not
    the fixed sheet's."""
    path = Path(src) / "reviews" / f"{FIXED_TEXT_ID}.json"
    if not path.exists():
        return [f"no reviews/{FIXED_TEXT_ID}.json"]
    rec = json.loads(path.read_text(encoding="utf-8"))
    problems = [f"its {k} is {rec.get(k)!r}, not {FIXED_TEXT_ID!r}" for k in ("id", "list")
                if rec.get(k) != FIXED_TEXT_ID]
    if rec.get("fixed_text_version") != fixed_text_version(common):
        problems.append("the fixed text changed since its record was written (fixed_text_version)")
    if [p.get("name") for p in rec.get("pieces", [])] != [n for n, _ in fixed_pieces(common)]:
        problems.append("its pieces are not the fixed sheet's")
    return problems


def report_fixed_checks(source, src=SRC):
    """--checks, the fixed text's lines: the system message held once and exactly, and the fixed text's record tied
    to the current fixed text."""
    try:
        system_message(source["common"], source)
        lines = [f"system message: exactly {SYSTEM_MESSAGE!r}, one copy"]
    except ValueError as exc:
        lines = [f"system message: {exc}"]
    problems = check_fixed_review(source["common"], src)
    lines.append(f"fixed text record (reviews/{FIXED_TEXT_ID}.json): "
                 + ("tied to the current fixed text" if not problems else "; ".join(problems)))
    return "\n".join(lines)


# ---- the second reader's packet --------------------------------------------------------------------------------------
def reading_of(lst, item):
    """Which reading list holds a scenario: a tie or a triad by its list, any other by its family (READINGS)."""
    for n, spec in READINGS.items():
        if lst in spec["lists"] or (lst in ("allocation", "conflict") and item["family"] in spec["families"]):
            return n
    raise ValueError(f"{item['id']}: no reading list holds it")


def reading_order(source, n):
    """A reading list's scenarios in reading order: family by family, each family's allocation scenarios then its
    conflict scenarios, then the whole lists it holds (the ties, the triads), each in source order."""
    spec, out = READINGS[n], []
    for fam in spec["families"]:
        out += [("allocation", i) for i in source["allocation"] if i["family"] == fam]
        out += [("conflict", i) for i in source["conflict"] if i["family"] == fam]
    for lst in spec["lists"]:
        out += [(lst, i) for i in source[lst]]
    return out


def mark(lst, r):
    """A rendering's short name on a sheet: its assignment (and, in a conflict, its run of the fraction)."""
    if lst == "conflict":
        d = r["design"]
        return f"deployer={r['run'].split()[1][4:]} client={r['run'].split()[2][4:]} L{d['L']} N{d['N']} W{d['W']}"
    return r["run"]


def sheet_selection(lst, item, common):
    """What a sheet shows, as {"full": [...], "long": [...], "lines": [...]}, each a list of renderings: the complete
    ones on the default roster (SHEET_SEATS, or a triad's SHEET_TRIAD_FULL, a tie's SHEET_TIE_FULL), the complete
    one on the long roster (none for a triad), and the other renderings of the default roster shown as lines. In a
    conflict the three complete renderings take both levels of L, N and W between them."""
    default = render(lst, item, ROSTERS["default"], common)
    long_ = render(lst, item, ROSTERS["long"], common)
    if lst == "allocation":
        full = [r for seat in SHEET_SEATS for r in default if r["listing"] == seat]
        longs = [r for r in long_ if r["listing"] == SHEET_LONG_SEAT]
    elif lst == "conflict":
        def run(rs, dep, cli, L, N):
            return next(r for r in rs if r["run"].split()[1:3] == [f"dep={dep}", f"cli={cli}"]
                        and (r["design"]["L"], r["design"]["N"]) == (L, N))
        for combo in product(product((0, 1), repeat=2), repeat=len(SHEET_SEATS)):
            full = [run(default, dep, cli, L, N) for (dep, cli), (L, N) in zip(SHEET_SEATS, combo)]
            if all({r["design"][f] for r in full} == {0, 1} for f in "LNW"):
                break
        longs = [run(long_, *SHEET_LONG_SEAT, 1, 0)]
        others = [(cli, dep) for dep, cli in SHEET_SEATS]
        lines = [run(default, dep, cli, L, N) for (dep, cli), (L, N) in zip(others, ((0, 0), (1, 1), (0, 1)))]
        return {"full": full, "long": longs, "lines": lines}
    elif lst == "triad":
        full, longs = [default[i] for i in SHEET_TRIAD_FULL], []
    else:
        idx = {(TYPES.index(r["listing"][0]), 0 if r["labels"] == list(item["labels"]) else 1): r for r in default}
        full = [idx[k] for k in SHEET_TIE_FULL]
        longs = [r for r in long_ if (TYPES.index(r["listing"][0]), 0 if r["labels"] == list(item["labels"]) else 1)
                 == SHEET_TIE_LONG]
    lines = [r for r in default if r not in full]
    return {"full": full, "long": longs, "lines": lines}


def text_lines(r):
    """A rendering's key, body sentences and option lines: the lines a sheet can show alone."""
    parts = r["text"].split("\n\n")
    return [parts[1]] + [x for x in re.split(r"(?<=[.!?])\s+", parts[2]) if x] + parts[3].split("\n")


def shown_lines(lst, r, roster, base=None):
    """The lines of a rendering a sheet shows when it does not show it whole: a triad's lines that differ from base;
    any other list's lines that carry a party's form (a tie's also those that carry a label)."""
    if lst == "triad":
        seen = set(text_lines(base))
        return [x for x in text_lines(r) if x not in seen]
    keys = party_forms(r["listing"], roster) + list(r["labels"])
    return [x for x in text_lines(r) if any(k in x for k in keys)]


def template_text(lst, item, common):
    """A scenario's template with its slots visible: what the writer wrote, in the order a model reads it, with the
    builder's fixed parts named but not repeated."""
    ft = common["fixed_text"]
    if lst in ("allocation", "tie"):
        lines = [item["body"], OB.menu(list(item["options"]) + ["(option C, fixed text)"], labels="ABC")]
        if lst == "tie":
            lines.append(f"labels {{first}}, {{second}}: {item['labels'][0]}, {item['labels'][1]} (both orders)")
        return "\n\n".join(lines)
    if lst == "conflict":
        grammar = dict(item, deployer_sentence=common["role_sentences"]["deployer"],
                       client_sentence=common["role_sentences"]["client"])
        text, options, _ = OB.ranked_text(grammar, True, 0, 0, {"survey_preamble": ft["preamble"]},
                                          {"forced": ft["footer_conflict"]})
        words = " / ".join(f"{w['a']} | {w['the']}" for w in item["wordings"])
        return "\n\n".join([text.split("\n\n")[1], OB.menu(options, labels="AB"),
                            f"job wordings (W gives one to each side; N, L and T rotate the rest): {words}"])
    body = " ".join([item["setting"], common["role_sentences"]["triad"], item["lead"], item["closing"]])
    jobs = "\n".join(f"{{jobs}}, {t}: {item['jobs'][t]}" for t in TYPES)
    opts = "\n".join(f"option, {t}: {item['option_by_type'][t]}" for t in TYPES)
    return "\n\n".join([body, jobs, opts])


def sheet_text(lst, item, common, record):
    """One scenario's sheet: its labels and record status (and its spare ruling, if it has one), what the sheet
    shows, the template once, the complete renderings exactly as a model sees them (system message included), the
    long roster's, then the other renderings' lines (sheet_selection, shown_lines)."""
    near = "; ".join(f"{n['id']} ({n['why']})" for n in record.get("near") or []) or "none"
    head = [f"{item['id']} | {lst} | {item['family']}",
            f"setting: {record.get('setting', '(none recorded)')}",
            f"object: {record.get('object', '(none recorded)')}",
            f"loss_kind: {record.get('loss_kind', '(none recorded)')}",
            f"near: {near}",
            f"record status: {record.get('status')}",
            f"rendering hash (default roster): {render_hash(render(lst, item, ROSTERS['default'], common))}",
            f"this sheet shows: {SHEET_NOTE[lst]}"]
    if str(record.get("ruling", "")).startswith(SPARE_RULING):
        head.insert(1, f"SPARE, already out of the bank; read it only for comparison ({record['ruling']})")
    pick = sheet_selection(lst, item, common)
    parts = ["\n".join(head), "---- the template, slots visible\n" + template_text(lst, item, common)]
    for n, r in enumerate(pick["full"], 1):
        parts.append(f"---- complete rendering {n} of {len(pick['full'])}: default roster, {mark(lst, r)}\n"
                     f"{with_system(r['text'], common)}")
    for r in pick["long"]:
        parts.append(f"---- complete rendering on the long roster: {mark(lst, r)}\n{with_system(r['text'], common)}")
    what = "the lines that differ from complete rendering 1" if lst == "triad" else (
        "the lines that carry the party's form or a label" if lst == "tie" else "the lines that carry a party's form")
    rows = [f"[{mark(lst, r)}] {x}" for r in pick["lines"]
            for x in shown_lines(lst, r, ROSTERS["default"], pick["full"][0])]
    parts.append(f"---- the other renderings on the default roster: {what}\n" + "\n".join(rows))
    return "\n\n".join(parts) + "\n"


def words(text):
    """The number of whitespace-separated words in a text."""
    return len(text.split())


def write_packet(source, out, writers=None, src=SRC):
    """The second reader's packet in out: fixed.txt, sheets/<id>.txt, index.json and reading-1.json to reading-5.json
    (each list's families, its files in reading order, its word count, and every near pair with one member in
    another list). The records are read from src; writers maps an id to its writer, when given."""
    out = Path(out)
    (out / "sheets").mkdir(parents=True, exist_ok=True)
    common, writers = source["common"], writers or {}
    system_message(common, source)
    fixed = fixed_sheet(common)
    (out / "fixed.txt").write_text(fixed, encoding="utf-8")
    index, texts, records = [], {}, {}
    where = {item["id"]: reading_of(lst, item) for lst, item in scenarios(source)}
    for lst, item in scenarios(source):
        rec = json.loads((Path(src) / "reviews" / f"{item['id']}.json").read_text(encoding="utf-8"))
        records[item["id"]] = rec
        texts[item["id"]] = text = sheet_text(lst, item, common, rec)
        (out / "sheets" / f"{item['id']}.txt").write_text(text, encoding="utf-8")
        current = render_hash(render(lst, item, ROSTERS["default"], common))
        index.append({"id": item["id"], "list": lst, "family": item["family"], "writer": writers.get(item["id"]),
                      "setting": rec.get("setting"), "object": rec.get("object"), "loss_kind": rec.get("loss_kind"),
                      "near": rec.get("near", []), "record_status": rec.get("status"), "render_sha256": current,
                      "record_matches_rendering": rec.get("render_sha256") == current, "reading": where[item["id"]],
                      "spare": str(rec.get("ruling", "")).startswith(SPARE_RULING), "ruling": rec.get("ruling"),
                      "sheet": f"sheets/{item['id']}.txt", "sheet_words": words(text)})
    (out / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    counts = {}
    for n, spec in READINGS.items():
        order = [item["id"] for _, item in reading_order(source, n)]
        files = (["fixed.txt"] if spec["fixed"] else []) + [f"sheets/{sid}.txt" for sid in order]
        counts[n] = sum(words(texts[sid]) for sid in order) + (words(fixed) if spec["fixed"] else 0)
        cross = [{"here": sid, "there": e["id"], "there_reading": where[e["id"]], "why": e["why"]}
                 for sid in order for e in records[sid].get("near") or [] if where.get(e["id"]) not in (None, n)]
        doc = {"reading": n, "families": list(spec["families"]), "lists_whole": list(spec["lists"]),
               "fixed_sheet": spec["fixed"], "scenarios": len(order), "words": counts[n], "files": files,
               "cross_reading_near": cross}
        (out / f"reading-{n}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return counts


# ---- command line --------------------------------------------------------------------------------------------------
def parse_args(argv):
    """The command line's options: the source, the roster (or --names), one list or scenario, an output folder, the
    four reports, the fixed sheet (--fixed) and the second reader's packet (--sheets, --writers)."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--src", default=str(SRC), help="the source folder (default bank/versus/src)")
    ap.add_argument("--roster", choices=sorted(ROSTERS), default="default")
    ap.add_argument("--names", nargs=3, metavar=("PERSON", "ORGANIZATION", "NATION"), help="any other roster")
    ap.add_argument("--list", choices=LISTS + ("all",), default="all")
    ap.add_argument("--id", help="render one scenario only")
    ap.add_argument("--out", help="write one file per list (<list>.txt) into this folder instead of stdout")
    for flag in ("skeletons", "counts", "checks", "hashes"):
        ap.add_argument(f"--{flag}", action="store_true")
    ap.add_argument("--fixed", action="store_true", help="print the sheet of every piece of fixed text")
    ap.add_argument("--sheets", metavar="DIR", help="write the second reader's packet into this folder")
    ap.add_argument("--writers", metavar="FILE", help="with --sheets: a JSON file mapping each id to its writer")
    return ap.parse_args(argv)


def main(argv=None):
    """The renderer's entry point: print the reports asked for, or every rendering (to stdout, or one file per list
    with --out)."""
    args = parse_args(argv)
    source = load_source(args.src)
    roster = dict(zip(TYPES, args.names)) if args.names else ROSTERS[args.roster]
    if args.fixed:
        system_message(source["common"], source)
        print(fixed_sheet(source["common"]), end="")
        return 0
    if args.sheets:
        writers = json.loads(Path(args.writers).read_text(encoding="utf-8")) if args.writers else None
        for n, count in write_packet(source, args.sheets, writers, args.src).items():
            print(f"reading list {n}: {count} words")
        return 0
    if args.checks:
        print(report_fixed_checks(source, args.src))
    reports = [(f, fn) for f, fn in (("skeletons", report_skeletons), ("checks", report_checks),
                                     ("hashes", report_hashes)) if getattr(args, f)]
    if args.counts:
        print(report_counts(source))
    for _, fn in reports:
        print(fn(source, roster))
    if args.counts or reports:
        return 0
    lists = LISTS if args.list == "all" else (args.list,)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for lst in lists:
            (out / f"{lst}.txt").write_text(report_renders(source, roster, (lst,), args.id), encoding="utf-8")
    else:
        print(report_renders(source, roster, lists, args.id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
