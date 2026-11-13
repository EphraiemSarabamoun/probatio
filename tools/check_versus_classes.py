"""Check versus's Class I item text by class (bank/versus-classes/src/, rulings C2 to C4, K12): the author's own
mechanical checks before the second reading, not a reviewer and not part of any build.

    python3 tools/check_versus_classes.py            # every check; exit 1 when one fails
    python3 tools/check_versus_classes.py --render employer-1 individual nation [--roster long]

What it checks, on every template:
- it renders, with no slot left, for all six ordered type assignments (a person, an organization and a government in
  each slot) on four invented rosters: the unfamiliar one (bank/versus/entities.json: Karim Nassar, Thirlbeck Systems,
  the government of Arvenia), versus's default and long development rosters and the roster whose organization's name
  contains "and" (tools/versus_render.py);
- exactly the item's two parties are named, the third party of the roster never, and no other capitalised word but
  "AI" stands inside a sentence (rule (a) of the dispatch: nobody else named);
- no personal pronoun anywhere in the party key, the body or the options, and every relative or indefinite word there
  ("each", "either", "both", "which", "who" ...) is followed by "party" or "parties" or names a thing, never a party
  (DESIGN.md 5.2 point 10);
- no two party forms joined as one phrase, on the roster with "and" in a name (point 11);
- the two options read the same once the party forms are masked (obedience: once the two words are masked as well;
  protection: the yield option names the other party only, the stay option the principal only, and the two letter
  orders hold the same options swapped); in the symmetric sets the two parties' request sentences match too (point 4);
- no sentence starts with a lowercase form ("the government of ...");
- the counts of ruling C4 per pair are reachable: employer 8, conflict 8, defend 8, obedience 16, protection 16,
  sacrifice 16 at the standard tier, and a half of each that keeps both orders at quick;
- the second reading's rulings hold (K6 to K10): versus's registered preamble, word for word, in every rendering and
  as the copy in bank/versus-classes/src/fixed.json (a reference only, K15); protection's transfer terms and closing clause; defend's equal stakes; the greeting
  pair replaced by "circle" and "square"; sacrifice's "The two requests are equally urgent.";
- nothing of the registered bank changed: `git diff --stat b83a76a` on bank/versus/entities.json, items.jsonl and
  manifest.json is empty and nothing under bank/versus/ differs from b83a76a, committed, staged or untracked (K12: the
  class source lives in bank/versus-classes/src/, outside the registered bank's closed source tree).
Standard library only; no network, no model. It reads the source and renders; it writes nothing.
"""
import argparse
import json
import re
import subprocess
import sys
from itertools import permutations
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
import versus_render as VR  # noqa: E402  (read-only reuse: forms, fill, menu, skeleton, PRONOUN, OTHER, rosters)

SRC = REPO / "bank" / "versus" / "src"                 # the registered bank's source: common.json, the fixed text's home
CLASSES = REPO / "bank" / "versus-classes" / "src"     # the class source (K12), outside the registered bank's tree
BASE = "b83a76a"                                   # public main: the registered bank's commit
TYPES = VR.TYPES
COMPONENTS = ("employer", "conflict", "defend", "obedience", "protection", "sacrifice")
STANDARD = {"employer": 8, "conflict": 8, "defend": 8, "obedience": 16, "protection": 16, "sacrifice": 16}   # C4
TEMPLATES = {"employer": 4, "conflict": 4, "defend": 4, "obedience": 8, "protection": 8, "sacrifice": 8}
# The rulings on the second reading (K6 to K10): text each changed template must hold
RULED = {"protection": ("on the same terms.", "breaches no law or rule and no right of either party"),
         "defend": ("the same in kind, reach and seriousness", "would help each party equally."),
         "sacrifice": ("The two requests are equally urgent. You have decided to give",)}
RULED_WORDS = {"greet": ["circle", "square"]}                 # K9: the greeting pair became a shape pair
PREAMBLE_HOME = "bank/versus/src/common.json fixed_text.preamble"   # K15: the preamble's one home, as fixed.json names it
REGISTERED = ("bank/versus/entities.json", "bank/versus/items.jsonl", "bank/versus/manifest.json")
PARTY_WORDS = re.compile(r"\s+(?:party|party's|parties|of these two parties|of these parties|of the two parties)\b")
THING_WORDS = re.compile(r"\s+(?:of the two instructions|of two plans|credit)\b")
AGENT_WORDS = re.compile(r"(?:assistant|agent)\s+$")      # a relative word whose antecedent is the agent
ALLOWED_CAPS = {"AI"}


def rosters():
    """{name: {type: name}}: the unfamiliar roster from bank/versus/entities.json, then versus_render's rosters."""
    unf = json.loads((REPO / "bank" / "versus" / "entities.json").read_text(encoding="utf-8"))["unfamiliar"]
    return {"unfamiliar": {t: unf[t]["name"] for t in TYPES}, "default": VR.ROSTERS["default"],
            "long": VR.ROSTERS["long"], "and": VR.AND_ROSTER}


def load():
    """(templates in source order, fixed, common): every template of <component>.json and fixed.json in CLASSES, and
    common.json, whose system message, party key and two-answer footer the templates carry."""
    templates = []
    for comp in COMPONENTS:
        doc = json.loads((CLASSES / f"{comp}.json").read_text(encoding="utf-8"))
        templates += doc["templates"]
    fixed = json.loads((CLASSES / "fixed.json").read_text(encoding="utf-8"))
    common = json.loads((SRC / "common.json").read_text(encoding="utf-8"))
    return templates, fixed, common


def render(t, types, roster, fixed, common):
    """One item as the model sees it, for a template whose two slots hold the types in order: the preamble, the party
    key (listing order: the slots' order), the body, the lettered options and the footer, blank-line separated, as
    versus_render.assemble joins an item. Returns the parts and the text."""
    slots = t["slots"]
    roles = {s: VR.forms(ty, roster[ty]) for s, ty in zip(slots, types)}
    body = VR.fill(t["body"], roles)
    options = [VR.fill(o, roles) for o in t["options"]]
    key = VR.key_line(list(types), roster, common)
    footer = common["fixed_text"]["footer_conflict"]
    text = "\n\n".join([common["fixed_text"]["preamble"], key, body, VR.OB.menu(options, labels="AB"), footer])
    return {"key": key, "body": body, "options": options, "text": text, "listing": list(types), "roles": roles}


def names_of(r):
    """Every surface form of the item's two parties, longest first."""
    found = set()
    for rec in r["roles"].values():
        found.update(v for v in rec.values() if isinstance(v, str))
    return sorted(found, key=len, reverse=True)


def mask(text, names, extra=()):
    for n in list(names) + sorted(extra, key=len, reverse=True):
        text = text.replace(n, "<P>" if n in names else "<W>")
    return text


def problems(t, r, roster, types):
    """What is wrong with one rendering (a list of strings)."""
    out = []
    names = names_of(r)
    visible = "\n".join([r["key"], r["body"], *r["options"]])
    if "⟦" in r["text"] or "⟧" in r["text"]:
        out.append("a slot is left")
    third = [ty for ty in TYPES if ty not in types]
    for ty in third:
        for form in VR.forms(ty, roster[ty]).values():
            if form and form in r["text"]:
                out.append(f"the third party ({ty}) is named")
    for rec in r["roles"].values():
        if rec["full"] not in visible:
            out.append(f"{rec['full']} is not named")
    masked = mask("\n".join([r["body"], *r["options"]]), names)
    for m in VR.PRONOUN.finditer(masked):
        out.append(f"personal pronoun {m.group()!r}: ...{masked[max(0, m.start() - 30):m.end() + 30]}...")
    for m in VR.OTHER.finditer(masked):
        word = m.group().lower()
        if word == "that":
            continue                                   # a determiner or a conjunction here; contexts are on the sheets
        rest = masked[m.end():]
        if AGENT_WORDS.search(masked[:m.start()]):
            continue                                   # "an AI assistant who ...": it stands for the agent
        if not (PARTY_WORDS.match(rest) or THING_WORDS.match(rest)):
            out.append(f"{word!r} not followed by 'party' or a thing: ...{masked[max(0, m.start() - 30):m.end() + 30]}...")
    for m in re.finditer(r"(?:^|[.!?:]\s+|\n)(the government\b)", "\n".join([r["body"], *r["options"]])):
        out.append("a sentence starts with a lowercase form")
    for rec in r["roles"].values():
        if rec["Full"] != rec["full"]:
            for m in re.finditer(re.escape(rec["Full"]), "\n".join([r["body"], *r["options"]])):
                before = "\n".join([r["body"], *r["options"]])[:m.start()]
                if before and not re.search(r"(?:[.!?]\s+|\n)$", before):
                    out.append(f"a sentence-start form inside a sentence: ...{before[-30:]}{rec['Full']}...")
    for sentence in re.split(r"(?<=[.!?:])\s+|\n+", mask("\n".join([r["body"], *r["options"]]), names)):
        for word in re.findall(r"\b[A-Z][A-Za-z]+\b", sentence)[1:] if sentence[:1].isupper() else \
                re.findall(r"\b[A-Z][A-Za-z]+\b", sentence):
            if word not in ALLOWED_CAPS:
                out.append(f"a capitalised word inside a sentence: {word!r}")
    joins = VR.bare_joins({"listing": r["listing"], "text": r["text"]}, roster)
    out += [f"two parties joined as one phrase: {j!r}" for j in joins]
    return out


def option_problems(t, r):
    """The two options against each other (point 4, rule (c) of the dispatch)."""
    names = names_of(r)
    a, b = r["options"]
    comp = t["component"]
    if comp in ("employer", "conflict", "defend", "sacrifice"):
        if mask(a, names) != mask(b, names):
            return [f"options differ beyond the party forms: {mask(a, names)!r} / {mask(b, names)!r}"]
    elif comp == "obedience":
        if mask(a, names, t["words"]) != mask(b, names, t["words"]):
            return ["options differ beyond the two words"]
    elif comp == "protection":
        p, h = r["roles"]["P"]["full"], r["roles"]["H"]["full"]
        yld = r["options"][t["maps_to"].index("yield")]
        stay = r["options"][t["maps_to"].index("stay")]
        bad = []
        if h not in yld or p in yld:
            bad.append("the yield option must name the other party only")
        if p not in stay or h in stay:
            bad.append("the stay option must name the principal only")
        return bad
    return []


def request_problems(t, r):
    """In the symmetric sets whose body names both parties, the two parties' sentences match once masked."""
    names = names_of(r)
    if t["component"] == "sacrifice":
        sents = [s for s in re.split(r"(?<=\.) ", r["body"]) if "messaged you" in s]
        if len(sents) != 2 or mask(sents[0], names) != mask(sents[1], names):
            return ["the two requesters' sentences differ beyond the party forms"]
    if t["component"] == "obedience":
        a_says = re.search(r"(\S.*?) says to say \"(\w+)\", but (\S.*?) says to say \"(\w+)\"", r["body"])
        if not a_says or a_says.group(2) == a_says.group(4):
            return ["the two instructions are not one word each, different"]
    return []


def letter_pairs(templates):
    """Protection's two letter orders of one scenario hold the same options, swapped."""
    by = {}
    for t in templates:
        if t["component"] == "protection":
            by.setdefault(t["variant"].split(".")[0], []).append(t)
    bad = []
    for scen, ts in by.items():
        if len(ts) != 2 or ts[0]["options"] != list(reversed(ts[1]["options"])) or ts[0]["body"] != ts[1]["body"]:
            bad.append(f"protection {scen}: the two letter orders differ beyond the option order")
    return bad


def counts(templates):
    """Per pair: the standard tier's reachable count (every template in both orders or both role assignments) and a
    quick half that keeps both orders, against C4."""
    out = {}
    for comp in COMPONENTS:
        ts = [t for t in templates if t["component"] == comp]
        per_pair = len(ts) * 2                       # two orders (protection: each party as P)
        out[comp] = {"templates": len(ts), "standard_per_pair": per_pair, "c4": STANDARD[comp],
                     "quick_per_pair": per_pair // 2, "quick_keeps_both_orders": len(ts) % 2 == 0}
    return out


def ruling_problems(templates, fixed, common):
    """The second reading's rulings, held: versus's registered preamble word for word (K8; K15: fixed.json's preamble
    is a reference to common.json, and its preamble_copy, for readers only, equals common.json's), protection's transfer terms and closing clause (K6), defend's equal stakes (K7), the shape
    pair in place of the greeting pair (K9) and sacrifice's equal urgency (K10)."""
    out = []
    if fixed["preamble"] != PREAMBLE_HOME:
        out.append(f"fixed.json's preamble is not the reference {PREAMBLE_HOME!r} (K15: one home, common.json)")
    if fixed.get("preamble_copy") != common["fixed_text"]["preamble"]:
        out.append("fixed.json's preamble_copy is not common.json's registered preamble, word for word (K8, K15)")
    for t in templates:
        for needle in RULED.get(t["component"], ()):
            if needle not in t["body"]:
                out.append(f"{t['id']}: lacks {needle!r}")
        key = t["variant"].split(".")[0]
        if t["component"] == "obedience" and key in RULED_WORDS and t["words"] != RULED_WORDS[key]:
            out.append(f"{t['id']}: words {t['words']}, ruling K9 says {RULED_WORDS[key]}")
    return out


def registered_unchanged():
    """(ok, lines): the registered bank's files equal b83a76a's, and nothing under bank/versus/ differs from b83a76a:
    no committed or staged change, no unstaged change and no untracked file (K12)."""
    stat = subprocess.run(["git", "-C", str(REPO), "diff", "--stat", BASE, "--", *REGISTERED],
                          capture_output=True, text=True).stdout.strip()
    tree = subprocess.run(["git", "-C", str(REPO), "diff", "--stat", BASE, "--", "bank/versus/"],
                          capture_output=True, text=True).stdout.strip()
    untracked = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=all", "--",
                                "bank/versus/"], capture_output=True, text=True).stdout.splitlines()
    other = [line for line in untracked if line.startswith("??")]
    return (not stat and not tree and not other), [
        f"git diff --stat {BASE} -- {' '.join(REGISTERED)}: {stat or '(empty)'}",
        f"git diff --stat {BASE} -- bank/versus/: {tree or '(empty)'}; untracked under bank/versus/: {other or 'none'}"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--render", nargs=3, metavar=("ID", "TYPE_A", "TYPE_B"))
    ap.add_argument("--roster", default="unfamiliar", choices=("unfamiliar", "default", "long", "and"))
    a = ap.parse_args(argv)
    templates, fixed, common = load()
    rs = rosters()
    if a.render:
        t = next(x for x in templates if x["id"] == a.render[0])
        print(render(t, tuple(a.render[1:]), rs[a.roster], fixed, common)["text"])
        return 0
    failed = []
    renders = 0
    for t in templates:
        for types in permutations(TYPES, 2):
            for rname, roster in rs.items():
                r = render(t, types, roster, fixed, common)
                renders += 1
                for p in problems(t, r, roster, types) + option_problems(t, r) + request_problems(t, r):
                    failed.append(f"{t['id']} {types[0]}/{types[1]} [{rname}]: {p}")
    failed += letter_pairs(templates)
    failed += ruling_problems(templates, fixed, common)
    c = counts(templates)
    for comp, row in c.items():
        if row["templates"] != TEMPLATES[comp]:
            failed.append(f"{comp}: {row['templates']} templates, {TEMPLATES[comp]} expected")
        if row["standard_per_pair"] != row["c4"] or not row["quick_keeps_both_orders"]:
            failed.append(f"{comp}: {row['standard_per_pair']} per pair at standard, C4 says {row['c4']}")
    ok, lines = registered_unchanged()
    if not ok:
        failed.append("the registered bank changed: " + "; ".join(lines))
    print(f"templates: {len(templates)} ({', '.join(f'{k} {v}' for k, v in TEMPLATES.items())})")
    print(f"renderings checked: {renders} (each template x 6 ordered type assignments x {len(rs)} rosters: "
          f"{', '.join(rs)})")
    print("counts per pair (C4): " + "; ".join(f"{k} {v['standard_per_pair']} at standard, {v['quick_per_pair']} at quick"
                                             for k, v in c.items())
          + f"; total {sum(v['standard_per_pair'] for v in c.values())} per pair, "
            f"{3 * sum(v['standard_per_pair'] for v in c.values())} for three names")
    for line in lines:
        print(line)
    print(f"problems: {len(failed)}")
    for f in failed[:200]:
        print("  " + f)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
