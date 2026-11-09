"""Tests for the v4 bank's source files (bank/src/, the W1 content the bank builder reads): every file parses; every
slot names a known role and field; every organization text differs from its nation counterpart only by the
substitutions its change field declares, and CHANGES.md covers every template; every entity passes the structural
guard, no handle or name is duplicated or ambiguous, and no entity name collides with a fixed name; the cells share one
tool catalog and differ only in notice text; the hypothetical framing and the provenance of every item are kept; and no
text holds an em dash, a double hyphen or a curly quote. Standard library only; nothing here calls a model.

    python3 -m unittest discover -s tests
"""
import difflib
import json
import re
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "bank" / "src"
CHANGES = ROOT / "bank" / "CHANGES.md"
SLOT = re.compile(r"⟦([A-Z][A-Z0-9]*)\.([A-Za-z_]+)⟧")
# organization file -> its nation counterpart
PAIRS = {"organization/class1.json": "nation/class1.json", "organization/errands.json": "nation/errands.json",
         "rf2/organization.json": "rf2/nation.json"}
TYPES = ("individual", "organization", "nation")


def load(rel):
    return json.loads((SRC / rel).read_text(encoding="utf-8"))


def templates(rel):
    """A source file's templates: every entry but the "_" metadata keys."""
    return {k: v for k, v in load(rel).items() if not k.startswith("_")}


def all_templates():
    """(file, template id, entry) for every template in bank/src."""
    out = []
    for path in sorted(SRC.rglob("*.json")):
        rel = str(path.relative_to(SRC))
        if rel in ("schema.json", "entities.json", "fixed_names.json"):
            continue
        out += [(rel, tid, e) for tid, e in templates(rel).items()]
    return out


def word_diff(a, b):
    """The (from, to) word spans where b differs from a, as the change fields list them."""
    ta, tb = a.split(), b.split()
    return [[" ".join(ta[i1:i2]), " ".join(tb[j1:j2])]
            for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes()
            if op != "equal"]


def normalise(name):
    """DESIGN.md 5.3: NFKC, casefold, a leading "the " removed, punctuation and whitespace runs to one space."""
    s = unicodedata.normalize("NFKC", name).casefold().strip()
    s = re.sub(r"^the\s+", "", s)
    return re.sub(r"[\W_]+", " ", s).strip()


def contains(a, b):
    """Whether one normalised name holds the other as a whole run of words."""
    wa, wb = f" {a} ", f" {b} "
    return wa in wb or wb in wa


def guard_problems(text, guard):
    """The structural guard of DESIGN.md section 9 (bank/src/schema.json), as the renderer applies it to every
    user-supplied field: what is wrong with this text, or an empty list."""
    found = []
    if any(not (c.isalpha() or c.isdigit() or c in guard["allowed_punctuation"]) for c in text):
        found.append("a character outside letters, digits and the allowed punctuation")
    if len(text.strip()) > guard["max_chars"] or len(text.split()) > guard["max_words"]:
        found.append("too long")
    if not any(c.isalpha() for c in text):
        found.append("no letter")
    words = re.findall(r"[^\W\d_]+", text)
    if any(w.upper() in guard["reserved_words"] for w in words):
        found.append("an answer keyword")
    if any(re.match(guard["forbidden_token_pattern"], tok) for tok in text.split()):
        found.append("an option-letter token")
    return found


class Files(unittest.TestCase):
    def test_every_file_parses_and_every_template_is_complete(self):
        files = sorted(SRC.rglob("*.json"))
        self.assertGreaterEqual(len(files), 11)
        for path in files:
            json.loads(path.read_text(encoding="utf-8"))
        for rel, tid, e in all_templates():
            with self.subTest(template=f"{rel}:{tid}"):
                self.assertIsInstance(e.get("text"), str)
                self.assertTrue(e["text"].strip())
                self.assertTrue(e.get("source"), "every template keeps its provenance")
                self.assertIn(e.get("change", {}).get("kind"), ("none", "declared"))

    def test_slots_name_known_roles_and_fields(self):
        schema = load("schema.json")
        for rel, tid, e in all_templates():
            with self.subTest(template=f"{rel}:{tid}"):
                found = SLOT.findall(e["text"])
                for c in e.get("choices", []):
                    found += SLOT.findall(c.get("text", ""))
                for role, field in found:
                    self.assertIn(role, schema["roles"])
                    self.assertIn(field, schema["fields"])
                self.assertEqual(sorted({r for r, _ in SLOT.findall(e["text"])}), e["slots"])
                left = SLOT.sub("", e["text"])
                self.assertNotIn("⟦", left)
                self.assertNotIn("⟧", left)
                self.assertNotRegex(e["text"], r"[{}]", "a published placeholder or brace survived")

    def test_typography(self):
        texts = [(f"{rel}:{tid}", e["text"]) for rel, tid, e in all_templates()]
        texts += [(f"{rel}:{tid}:choice", c["text"]) for rel, tid, e in all_templates() for c in e.get("choices", [])
                  if "text" in c]
        ents = load("entities.json")
        texts += [(f"entity:{r['key']}", v) for t in TYPES for r in ents[t] for v in r.values() if isinstance(v, str)]
        texts.append(("CHANGES.md", CHANGES.read_text(encoding="utf-8")))
        for where, text in texts:
            with self.subTest(where=where):
                self.assertNotIn("—", text, "em dash")
                self.assertNotIn("--", text, "double hyphen")
                self.assertFalse(set(text) & set("“”‘’"), "curly quote")
                self.assertEqual(text.count('"') % 2, 0, "unbalanced straight double quotes")


class Derivation(unittest.TestCase):
    def test_organization_texts_differ_from_nation_only_by_declared_substitutions(self):
        pairs = [(o, n, tid) for o, n in PAIRS.items() for tid in templates(o)]
        pairs.append(("followup.json", "followup.json", "followup.organization"))
        for org_file, nat_file, tid in pairs:
            with self.subTest(template=tid):
                org = templates(org_file)[tid]
                nat_tid = "followup.nation" if tid == "followup.organization" else tid
                nat = templates(nat_file)[nat_tid]
                actual = word_diff(nat["text"], org["text"])
                declared = org["change"].get("substitutions", [])
                self.assertEqual(actual, declared, "a difference from the nation text that change does not declare")
                self.assertEqual(org["change"]["kind"], "declared" if actual else "none")
                self.assertEqual(nat["text"].count("\n\n"), org["text"].count("\n\n"), "paragraphs differ")

    def test_every_organization_template_has_a_nation_counterpart_and_back(self):
        for org_file, nat_file in PAIRS.items():
            self.assertEqual(set(templates(org_file)), set(templates(nat_file)), org_file)

    def test_changes_md_covers_every_template(self):
        text = CHANGES.read_text(encoding="utf-8")
        for rel, tid, e in all_templates():
            with self.subTest(template=f"{rel}:{tid}"):
                self.assertIn(f"`{tid}`", text)

    def test_every_invented_organization_has_a_recorded_web_and_domain_check(self):
        # the check W1 first skipped: an exact-name search and the likely domains, name.com and name.io
        text = CHANGES.read_text(encoding="utf-8")
        invented = [r["full"] for r in load("entities.json")["organization"] if r["kind"] == "fictional"]
        self.assertGreaterEqual(len(invented), 3)
        for name in invented:
            slug = re.sub(r"[^a-z0-9]", "", name.lower())
            with self.subTest(name=name):
                self.assertIn(f'search "{name}"', text, "no exact-name search recorded")
                self.assertIn(f"`{slug}.com`", text, "no .com domain check recorded")
                self.assertIn(f"`{slug}.io`", text, "no .io domain check recorded")

    def test_provenance_claims_match_their_sources(self):
        # a template may claim to be patria's own text only if its source is patria
        for rel, tid, e in all_templates():
            against = e["change"].get("against", "")
            with self.subTest(template=f"{rel}:{tid}"):
                if against == "patria":
                    self.assertEqual(e["source"].get("repo"), "patria", "claims patria but cites another source")
                if against.startswith("anulus:"):
                    self.assertEqual(e["source"].get("dataset"), "anulus")

    def test_hypothetical_framing_kept(self):
        for kind in ("nation", "organization"):
            for tid, e in templates(f"{kind}/class1.json").items():
                if tid.startswith(("I.1", "I.3", "rating")):
                    with self.subTest(template=f"{kind}:{tid}"):
                        self.assertIn("It is hypothetical, nothing you say will be acted on", e["text"])
            for tid, e in templates(f"rf2/{kind}.json").items():
                with self.subTest(template=f"rf2/{kind}:{tid}"):
                    self.assertTrue(e["text"].startswith("This scenario is fictional, and every circumstance in it is "
                                                         "stipulated."))

    def test_answer_menus_are_in_the_text(self):
        for rel, tid, e in all_templates():
            for c in e.get("choices", []):
                if "text" in c:
                    with self.subTest(template=f"{rel}:{tid}", label=c["label"]):
                        self.assertIn(f"{c['label']}) {c['text']}", e["text"])
                elif len(c["label"]) == 1:
                    self.assertIn(f"{c['label']}) ", e["text"])

    def test_rf2_covers_six_scenarios_two_cells_two_modes(self):
        for kind in ("nation", "organization"):
            ids = set(templates(f"rf2/{kind}.json"))
            want = {f"RF2.{s}.{m}.{c}" for s in ("money_invoice", "money_fund", "fair_queue", "fair_deadline",
                                                  "shift_power", "candour_report")
                    for m in ("explicit", "discretion") for c in ("S2C2", "S4C4")}
            self.assertEqual(ids, want)


class Cells(unittest.TestCase):
    def test_cells_share_one_catalog_and_differ_only_in_notices(self):
        cells = load("cells.json")
        structure = cells["_structure"]
        self.assertEqual(set(structure["cells"]), {"N0", "U", "O"})
        self.assertEqual([t["name"] for t in structure["shared"]["tools_added"]], ["activity_log"])
        for cell, spec in structure["cells"].items():
            with self.subTest(cell=cell):
                self.assertEqual(set(spec), {"notice", "log_header"}, "a cell may vary its notice text only")
        self.assertEqual(structure["cells"]["N0"]["notice"], [])

    def test_every_notice_reference_resolves_for_every_type(self):
        cells = load("cells.json")
        own = {"organization": templates("organization/errands.json"), "nation": templates("nation/errands.json")}
        for kind in TYPES:
            for cell, spec in cells["_structure"]["cells"].items():
                for ref in spec["notice"] + [spec["log_header"]]:
                    with self.subTest(type=kind, cell=cell, ref=ref):
                        if ref.startswith("<type>:"):
                            tid = ref.split(":", 1)[1]
                            if kind == "individual":        # E.notice.audited -> cell.individual.notice.audited
                                self.assertIn("cell.individual." + tid.split(".", 1)[1], cells)
                            else:
                                self.assertIn(tid, own[kind])
                        else:
                            self.assertIn(ref, cells)


class Entities(unittest.TestCase):
    def setUp(self):
        self.schema = load("schema.json")
        self.ents = load("entities.json")

    def test_records_are_complete_and_rosters_balanced(self):
        for kind in TYPES:
            recs = self.ents[kind]
            with self.subTest(type=kind):
                for r in recs:
                    self.assertEqual(set(self.schema["fields"]) - set(r), set(), r["key"])
                    self.assertEqual(r["type"], kind)
                    self.assertTrue(r.get("source"))
                roles = [role for r in recs for role in r["roles"]]
                self.assertEqual(roles.count("comparator"), 5)
                self.assertEqual(roles.count("control"), 1)
                self.assertTrue(all(r["kind"] != "fictional" for r in recs if "comparator" in r["roles"]))
                anon = self.ents["anonymous"][kind]
                self.assertTrue({"requester", "Requester", "to", "To"} <= set(anon))

    def test_every_name_field_passes_the_guard(self):
        guard = self.schema["guard"]
        for kind in TYPES:
            for r in self.ents[kind] + [self.ents["anonymous"][kind]]:
                for field in self.schema["name_fields"]:
                    if field in r:
                        with self.subTest(entity=r["key"], field=field):
                            self.assertEqual(guard_problems(r[field], guard), [])
                for field in self.schema["derived_fields"]:      # the type's fixed words around the name
                    if field in r:
                        with self.subTest(entity=r["key"], field=field):
                            self.assertEqual([p for p in guard_problems(r[field], guard) if p != "too long"], [])
                            if "full" in r:
                                self.assertIn(normalise(r["full"]), normalise(r[field]))
                if "code" in r:
                    self.assertRegex(r["code"], guard["code_pattern"])

    def test_the_guard_refuses_what_it_should(self):
        guard = self.schema["guard"]
        for bad in ("Acme {Corp}", "Acme⟦T.full⟧", 'Acme "Labs"', "ANSWER Labs", "A) Acme", "Acme\nLabs",
                    "Acme: Labs", "1234", "x" * 61, "one two three four five six seven eight nine", "No Labs"):
            with self.subTest(name=bad):
                self.assertTrue(guard_problems(bad, guard))
        for good in ("Mozilla", "Fenwick & Ash", "the United States", "Côte d'Ivoire", "AT&T (US)", "Mistral AI"):
            with self.subTest(name=good):
                self.assertEqual(guard_problems(good, guard), [])

    def test_no_duplicate_or_ambiguous_keys_handles_or_names(self):
        allnames = []
        for kind in TYPES:
            recs = self.ents[kind]
            with self.subTest(type=kind):
                self.assertEqual(len({r["key"] for r in recs}), len(recs), "duplicate key")
                self.assertEqual(len({r["code"] for r in recs}), len(recs), "duplicate handle")
                names = [normalise(r["full"]) for r in recs]
                for i, a in enumerate(names):
                    for b in names[i + 1:]:
                        self.assertFalse(contains(a, b), f"{a!r} and {b!r} are ambiguous")
            allnames += [normalise(r["full"]) for r in recs]
        self.assertEqual(len(allnames), len(set(allnames)), "a name used in two types")

    def test_no_entity_collides_with_a_fixed_name(self):
        fixed = load("fixed_names.json")
        names = [normalise(n) for k, v in fixed.items() if isinstance(v, list) for n in v]
        for kind in TYPES:
            for r in self.ents[kind]:
                for n in names:
                    with self.subTest(entity=r["full"], fixed=n):
                        self.assertFalse(contains(normalise(r["full"]), n))


if __name__ == "__main__":
    unittest.main()
