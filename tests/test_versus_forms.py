"""versus's entity forms (versus.entity_forms, DESIGN.md 4) and the run's names (versus.resolve_names): every field an
item may use, for all three types and for odd names (one holding "and", a long one, one with an apostrophe, one
starting with "the"); the renderer takes its forms from this one home; probatio's name guard decides what a name may
be, and versus refuses two parties a run would confuse. No network, no model.

    python3 -m unittest tests.test_versus_forms
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
import probatio as P  # noqa: E402
import versus as VS  # noqa: E402
import versus_render as V  # noqa: E402

FIELDS = ("full", "Full", "short", "Short", "group", "Group", "requester", "Requester", "to", "To", "poss", "Poss",
          "short_poss", "Short_poss")


class Forms(unittest.TestCase):
    def test_a_person_is_the_name_in_every_field(self):
        f = VS.entity_forms("individual", "Jane Doe")
        for field in ("full", "Full", "short", "Short", "group", "Group", "requester", "Requester", "to", "To"):
            self.assertEqual(f[field], "Jane Doe", field)
        self.assertEqual((f["poss"], f["Poss"]), ("Jane Doe's", "Jane Doe's"))
        self.assertEqual(f["party"], "Jane Doe, a person")
        self.assertNotIn("country", f)

    def test_an_organization_is_its_bare_name_never_a_team(self):
        f = VS.entity_forms("organization", "Acme Corp")
        self.assertEqual({f[k] for k in FIELDS if not k.lower().endswith("poss")}, {"Acme Corp"})
        self.assertEqual(f["poss"], "Acme Corp's")
        self.assertEqual(f["party"], "Acme Corp, an organization")
        self.assertFalse([k for k in FIELDS if "team" in f[k] or "research" in f[k]])

    def test_a_nation_is_its_government_in_every_field_and_the_country_bare(self):
        f = VS.entity_forms("nation", "Norland")
        for field in ("full", "short", "group", "requester", "to"):
            self.assertEqual(f[field], "the government of Norland", field)
            self.assertEqual(f[field[0].upper() + field[1:]], "The government of Norland", field)
        self.assertEqual((f["country"], f["Country"]), ("Norland", "Norland"))
        self.assertEqual((f["poss"], f["Poss"]), ("the government of Norland's", "The government of Norland's"))
        self.assertEqual(f["party"], "the government of Norland, a government")
        self.assertFalse([k for k in FIELDS if "delegation" in f[k]])

    def test_odd_names(self):
        f = VS.entity_forms("organization", "Smith and Jones")
        self.assertEqual((f["full"], f["poss"]), ("Smith and Jones", "Smith and Jones'"))
        long_name = "Consolidated Pacific Infrastructure Holdings"
        self.assertEqual(VS.entity_forms("organization", long_name)["Group"], long_name)
        f = VS.entity_forms("individual", "Siobhan O'Neill")
        self.assertEqual((f["full"], f["poss"], f["code"]), ("Siobhan O'Neill", "Siobhan O'Neill's", "siobhanoneil"))
        f = VS.entity_forms("individual", "James")
        self.assertEqual(f["poss"], "James's")                     # a person always takes 's, as probatio's entity
        f = VS.entity_forms("nation", "the Federated States of Micronesia")
        self.assertEqual(f["full"], "the government of the Federated States of Micronesia")
        self.assertEqual(f["Country"], "The Federated States of Micronesia")
        self.assertEqual(f["poss"], "the government of the Federated States of Micronesia's")
        self.assertEqual(VS.entity_forms("nation", "the Philippines")["poss"], "the government of the Philippines'")
        f = VS.entity_forms("organization", "de Vries Partners")
        self.assertEqual(f["Full"], "de Vries Partners")          # probatio's rule: only a leading "the" changes

    def test_the_sentence_start_rule_is_probatios(self):
        """Ruling 9: only a leading "the" is capitalised; a name is otherwise shown as typed, in every sentence-start
        field, in the forms and in the renderer alike."""
        for t, name in (("organization", "xAI"), ("individual", "mira vale"), ("nation", "lowerland")):
            f = VS.entity_forms(t, name)
            for field in ("Full", "Short", "Group", "Requester", "To", "Poss", "Short_poss"):
                self.assertEqual(f[field], P.sentence_start(f[field[0].lower() + field[1:]]), (name, field))
            self.assertEqual(V.forms(t, name)["Group"], f["Group"])
        self.assertEqual(VS.entity_forms("organization", "xAI")["Group"], "xAI")
        self.assertEqual(VS.entity_forms("individual", "mira vale")["Full"], "mira vale")
        self.assertEqual(VS.entity_forms("nation", "lowerland")["Full"], "The government of lowerland")
        self.assertEqual(VS.entity_forms("nation", "lowerland")["Country"], "lowerland")
        self.assertEqual(VS.entity_forms("organization", "the Guardian")["Full"], "The Guardian")
        self.assertEqual(VS.FORMS_VERSION, "versus-forms-2")

    def test_a_plan_shows_a_lowercase_name_as_typed(self):
        import ordo
        bank = VS.load_bank()
        calls = VS.plan(bank, {"individual": "mira vale", "organization": "xAI"}, "standard", {},
                        ordo.load_ordo_bank()).calls
        prompts = "\n".join(c["prompt"] for c in calls if c["meta"]["frame"] in ("allocation", "conflict", "tie"))
        self.assertIn("xAI deployed you", prompts)
        self.assertIn("mira vale deployed you", prompts)
        self.assertNotIn("XAI", prompts)
        self.assertNotIn("Mira vale", prompts)

    def test_identifiers_are_the_typed_name_never_a_display_form(self):
        f = VS.entity_forms("nation", "Norland")
        self.assertEqual((f["key"], f["code"]), ("norland", "norland"))

    def test_the_renderer_takes_its_forms_from_this_one_home(self):
        for t, name in (("individual", "Jane Doe"), ("organization", "Smith and Jones"), ("nation", "Norland"),
                        ("nation", "the Philippines"), ("individual", "Siobhan O'Neill")):
            rec = VS.entity_forms(t, name)
            self.assertEqual(V.forms(t, name), {k: rec[k] for k in V.RENDER_FIELDS if k in rec})

    def test_probatios_entity_is_untouched(self):
        self.assertEqual(P.entity("organization", "Acme Corp")["group"], "the Acme Corp team")
        self.assertEqual(P.entity("nation", "Norland")["group"], "the delegation of Norland")

    def test_an_unknown_type_or_empty_name_is_refused(self):
        with self.assertRaises(VS.VersusError):
            VS.entity_forms("company", "Acme")
        with self.assertRaises(VS.VersusError):
            VS.entity_forms("individual", " ")


class Names(unittest.TestCase):
    def test_two_or_three_names_one_per_type(self):
        got = VS.resolve_names({"individual": "  Jane   Doe ", "nation": "Norland"})
        self.assertEqual(list(got), ["individual", "nation"])
        self.assertEqual(got["individual"]["full"], "Jane Doe")     # the guard's normal form
        for bad in ({}, {"individual": "Jane Doe"}, {"individual": "A", "organization": "B", "nation": "C",
                                                     "planet": "D"}, ["Jane Doe", "Acme"]):
            with self.assertRaises(VS.VersusError):
                VS.resolve_names(bad)
        with self.assertRaises(VS.VersusError):
            VS.resolve_names({"individual": "Jane Doe", "company": "Acme"})

    def test_probatios_guard_decides_what_a_name_may_be(self):
        for bad in ("A) Acme", "Acme\nCorp", "yes", "x" * 61, "⟦IND.full⟧"):
            with self.assertRaises(VS.VersusError):
                VS.resolve_names({"individual": "Jane Doe", "organization": bad})

    def test_a_name_on_the_default_comparator_lists_is_allowed(self):
        rosters = P.load_bank(REPO / "bank").entities
        comparator = rosters["organization"]["comparators"][0]["full"]
        got = VS.resolve_names({"individual": "Jane Doe", "organization": comparator})
        self.assertEqual(got["organization"]["full"], comparator)

    def test_two_parties_a_run_would_confuse_are_refused(self):
        with self.assertRaises(VS.VersusError):
            VS.resolve_names({"individual": "Acme", "organization": "acme"})
        with self.assertRaises(VS.VersusError):                     # the same code
            VS.resolve_names({"individual": "Acme Corp", "organization": "Acme-Corp"})


if __name__ == "__main__":
    unittest.main()
