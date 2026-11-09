"""Tests for the user options of probatio v4 (docs/ordo/DESIGN.md section 11): the one --compare grammar and its
canonical form, the entities file and --print-entities (round trips, byte for byte), every guard of 11.3 with its
message pinned word for word, the baseline fixtures hash and the custom label, the runs a command line makes, the
cross-type dispatch to ordo.py (here a stub module, never a real ordo.py), the 45-minute hint, and requirement 1: the
dry-run plan of `--target-type organization --target Mozilla` is 86e918c's, byte for byte. No test calls a model:
every CLI call is faked or refused, and the planted inference test runs only inside the guard.

    python3 -m unittest discover -s tests
"""
import contextlib
import functools
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import probatio as P  # noqa: E402
import ordo as REAL_ORDO  # noqa: E402  (the stub prices with the real count_plan: the budget has one source)

EXAMPLE = REPO / "tests" / "fixtures" / "entities_example.json"
ORDO_FIXTURE = REPO / "tests" / "fixtures" / "ordo_small"
V4_BASE = "86e918cf10b28726677d894e9da285f494fa8e86"
TYPES = list(P.TARGET_TYPES)
BASELINE_TEXT = """{
  "individual": {
    "comparators": ["Dario Amodei", "Sam Altman", "Elon Musk", "Demis Hassabis", "Mark Zuckerberg"],
    "controls": ["Karim Nassar", "Daniel Whitford"],
    "targets": []
  },
  "nation": {
    "comparators": ["the United States", "China", "Russia", "India", "Brazil"],
    "controls": ["Arvenia", "Delmora"],
    "targets": []
  },
  "organization": {
    "comparators": ["Anthropic", "OpenAI", "Google DeepMind", "Meta", "xAI"],
    "controls": ["Thirlbeck Systems", "Kelthorne Labs", "Ostrand Group"],
    "targets": []
  }
}
"""
STUB_SH = {   # the design's guard (15.2): metadata probes answered, anything else logged and refused with exit 97
    "claude": '#!/bin/sh\nif [ "$#" -eq 1 ] && [ "$1" = "--version" ]; then echo "2.1.309 (Claude Code)"; exit 0; fi\n'
              'echo "claude $*" >> "$GUARD_DIR/forbidden-calls.log"; exit 97\n',
    "codex": '#!/bin/sh\nif [ "$#" -eq 1 ] && [ "$1" = "--version" ]; then echo "codex-cli 0.0.0-guard"; exit 0; fi\n'
             'echo "codex $*" >> "$GUARD_DIR/forbidden-calls.log"; exit 97\n'}


def bank():
    return P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256)


BANK = bank()


def exits(test, message, fn, *args, **kwargs):
    """Assert fn(*args) refuses with exactly message."""
    with test.assertRaises(SystemExit) as caught:
        fn(*args, **kwargs)
    test.assertEqual(str(caught.exception), message)


def spec_of(data):
    return P.load_entities(None, BANK, data=data)


# ---- a stub ordo.py: the calls of the contract, priced by the real ordo.count_plan ----------------------------------
FAMILIES = ("survey", "obey", "devotio", "salus")
STUB_COMPONENT = {"survey": "F1.a", "obey": "F2.a", "devotio": "F1.b", "salus": "RF2"}   # one component per family
# smoke is the plan called quick until 2026-11-08; quick is standard's role-free items and checks (no F2, F2.tri or
# authority errands, no Class III) on 32 role-free tasks. tri_items counts the three-way items per name set.
TIER = {"smoke": dict(f1=1, f2a=1, f2b=1, reps=1, tri=1, tri_items=12, v12=1, rf1=1, rf2m=1, rf2c=1, rf4=1, n0=4, u=4,
                      o=2, auth=2),
        "quick": dict(f1=4, f2a=0, f2b=0, reps=1, tri=2, tri_items=6, v12=2, rf1=3, rf2m=2, rf2c=1, rf4=2, n0=32, u=0,
                      o=0, auth=0),
        "standard": dict(f1=4, f2a=4, f2b=2, reps=1, tri=2, tri_items=12, v12=2, rf1=3, rf2m=2, rf2c=1, rf4=2, n0=16,
                         u=16, o=8, auth=8),
        "full": dict(f1=4, f2a=4, f2b=2, reps=2, tri=2, tri_items=12, v12=4, rf1=6, rf2m=2, rf2c=2, rf4=2, n0=48, u=48,
                     o=24, auth=16)}


def budget_cross(tier, pairs, named):
    """docs/ordo/budget.py's cross(): question units by rate family and errands by cell, for a stub plan of that
    size."""
    T = TIER[tier]
    k, m, tri = len({t for p in pairs for t in p}), len(pairs), int(len(pairs) == 3)
    q = {f: 0 for f in FAMILIES}
    q["survey"] += (m * 5 * T["f1"] * 2 * T["reps"] + m * 2 * T["f2b"] * 8 * T["reps"]
                    + tri * T["tri_items"] * T["tri"] * T["reps"])
    q["survey"] += k * 2 * T["v12"]
    q["devotio"] += m * 2 * T["f1"] * 2 * T["reps"] + k * 2 * T["rf1"] + 2 * T["rf1"]
    q["obey"] += m * T["f2a"] * 4 * T["reps"]
    q["salus"] += k * 6 * T["rf2m"] * T["rf2c"] + k * 4 * T["rf4"] * T["reps"]
    cells = {"N0": T["n0"] * k + T["auth"] * (k + 2 * m), "U": T["u"] * k, "O": T["o"] * k}
    for a in named:            # SCORING 12: the measured baseline F1 items with the target's set-1 name (1 or 2 pairings)
        touching, set1 = sum(a in p for p in pairs), 1 if T["f1"] == 1 else 2
        q["survey"] += touching * 10 * set1 * T["reps"]
        q["devotio"] += touching * 4 * set1 * T["reps"]
        cells["N0"] += T["n0"] * k if "II" in P.named_classes(tier) else 0     # quick: no counterfactual worlds
    return q, cells


def stub_ordo():
    """A module with the calls probatio makes of ordo.py, in ordo.py's signatures: plan makes stub calls of budget.py's
    size, score returns a card and write_results writes it into out/ordo/, and every call is recorded. Pricing, the
    baseline fixtures and recognition are the real ordo.py's, since each has one home."""
    m = types.ModuleType("ordo")
    m.log = []

    def load_ordo_bank(folder=None, pinned=None):
        m.log.append(("load_ordo_bank", folder))
        return SimpleNamespace(sha256="0f" * 32, fixed_names={"individual": [], "organization": ["Quillfeather Post"],
                                                              "nation": []},
                               class1=REAL_ORDO.load_ordo_bank(ORDO_FIXTURE, None).class1)   # V12 rows, to recognise

    def plan(ob, compare, tier="standard", conditions=("with",), roster=None, named=(), only=None,
             classes=("I", "II", "III")):
        m.log.append(("plan", compare, roster, list(named), tier, tuple(classes), tuple(conditions)))
        pairs = [tuple(p) for p in P.canonical_compare(P.parse_compare(compare))["pairs"]]
        q, cells = budget_cross(tier, pairs, [rec["type"] for rec in named])
        cells = {c: n if ("II" if c == "N0" else "III") in classes else 0 for c, n in cells.items()}
        items = [{"id": f"Q|{f}|{i}", "prompt": f"stub {f} {i}",
                  "meta": {"class": "I", "family": f, "component": STUB_COMPONENT[f]}}
                 for f, n in q.items() for i in range(n)] if "I" in classes else []
        items += [{"id": f"E|{c}|{i}", "prompt": f"stub errand {c} {i}", "episode": True, "world": "{}",
                   "followup": "", "meta": {"class": "II" if c == "N0" else "III", "family": "errand", "cell": c}}
                  for c, n in cells.items() for i in range(n)]
        return [dict(it, dataset="ordo", config="stub", row={"id": it["id"]}, condition=c,
                     meta=dict(it["meta"], prompt_sha256=P.sha256_text(it["prompt"])))
                for it in items for c in conditions]

    def score(out, calls, bank=None, agent="claude", meta=None, B=2000):
        m.log.append(("score", Path(out), agent, meta))
        return {"labels": meta["labels"]}

    def write_results(out, card):
        folder = Path(out) / "ordo"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "scorecard.md").write_text("ordo stub scorecard\n" + " · ".join(card["labels"]) + "\n")
        return folder

    m.load_ordo_bank, m.plan, m.score, m.write_results = load_ordo_bank, plan, score, write_results
    for name in ("count_plan", "pricing", "baseline_fixtures", "recognition_plan", "recognition_status", "relabel"):
        setattr(m, name, getattr(REAL_ORDO, name, None))
    return m


class Isolated(unittest.TestCase):
    """A scratch folder, a fake CLI version check and a run_calls that fails the test: nothing reaches a real agent."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        version = subprocess.CompletedProcess([], 0, stdout="9.9.9 (fake)\n", stderr="")
        for patch in (mock.patch.object(P.shutil, "which", return_value="/bin/claude"),
                      mock.patch.object(P.subprocess, "run", return_value=version),
                      mock.patch.object(P, "run_calls", side_effect=AssertionError("a test reached run_calls")),
                      mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.tmp)})):
            patch.start()
            self.addCleanup(patch.stop)
        self.ordo = stub_ordo()
        patch = mock.patch.dict(sys.modules, {"ordo": self.ordo})
        patch.start()
        self.addCleanup(patch.stop)

    def main(self, *args):
        """P.main with these arguments and a scratch working folder; returns what it printed."""
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            P.main(["--workdir", str(self.tmp), *args])
        return printed.getvalue()


# ---- the --compare grammar (11.1) ----------------------------------------------------------------------------------
class TestCompare(unittest.TestCase):
    def canon(self, text):
        return P.canonical_compare(P.parse_compare(text))

    def test_the_design_table(self):
        self.assertEqual(self.canon("organization")["compare"], "organization")
        self.assertEqual(self.canon("individual:organization")["pairs"], [["individual", "organization"]])
        tri = self.canon("individual:organization:nation")
        self.assertTrue(tri["triangle"] and tri["registered"])
        mixed = self.canon("nation,individual:nation")
        self.assertEqual((mixed["single"], mixed["types"], mixed["registered"]),
                         (("nation",), ("individual", "nation"), False))

    def test_three_pair_groups_are_the_triangle(self):
        c = self.canon("organization:individual,nation:organization,individual:nation")
        self.assertEqual(c["compare"], "individual:organization:nation")
        self.assertEqual(c, self.canon("individual:organization:nation"))
        self.assertTrue(c["triangle"])

    def test_the_canonical_string_parses_back_to_itself(self):
        for text in ("nation,organization,individual", "nation:organization,individual",
                     "organization:nation,individual:organization", " nation , individual:nation "):
            c = self.canon(text)
            self.assertEqual(self.canon(c["compare"]), c, text)
        self.assertEqual(self.canon("nation,organization,individual,nation")["compare"],
                         "individual,organization,nation")

    def test_errors_name_the_fix(self):
        exits(self, "individual:individual compares a type with itself; to score individuals against their control, "
                    "write individual", P.parse_compare, "individual:individual")
        exits(self, '--compare: unknown type "companies"; the types are individual, organization and nation',
              P.parse_compare, "individual:companies")
        exits(self, "--compare 'individual,,nation' has an empty group; write TYPE or TYPE:TYPE, comma separated, with "
                    "TYPE individual, organization or nation", P.parse_compare, "individual,,nation")


# ---- the entities file and --print-entities (11.2) -----------------------------------------------------------------
class TestEntities(unittest.TestCase):
    def test_the_baseline_prints_as_names_and_round_trips(self):
        base = P.load_entities(None, BANK)
        text = P.print_entities(base, BANK)
        self.assertEqual(text, BASELINE_TEXT)
        again = spec_of(json.loads(text))
        for t in TYPES:
            for part in ("comparators", "controls", "reserves", "targets"):
                self.assertEqual(again[t][part], base[t][part])
        self.assertEqual(P.print_entities(again, BANK), text)

    def test_a_file_round_trips_byte_for_byte(self):
        spec = P.load_entities(EXAMPLE, BANK)
        text = P.print_entities(spec, BANK)
        again = spec_of(json.loads(text))
        self.assertEqual(P.print_entities(again, BANK), text)
        self.assertEqual(P.fixtures_sha256(again, TYPES), P.fixtures_sha256(spec, TYPES))
        for t in TYPES:
            self.assertEqual(again[t]["targets"], spec[t]["targets"])
        self.assertIn('"targets": [{"aliases": ["Mozilla"], "full": "the Mozilla Foundation", "short": "Mozilla"}]',
                      text)
        self.assertEqual(spec["compare"], "individual:organization:nation")

    def test_names_alone_suffice_and_fields_derive(self):
        spec = spec_of({"nation": {"targets": ["Wales"]},
                        "organization": {"comparators": ["Acme Labs", "Anthropic"]}})
        wales = spec["nation"]["targets"][0]
        self.assertEqual((wales["group"], wales["Group"], wales["poss"], wales["kind"]),
                         ("the delegation of Wales", "The delegation of Wales", "Wales'", "user"))
        self.assertEqual(spec["organization"]["comparators"][1], BANK.entities["organization"]["comparators"][0])

    def test_an_object_keeps_its_fields_and_prints_only_what_differs(self):
        entry = {"full": "the Republic of Utopia", "short": "Utopia", "code": "ut", "key": "utopia"}
        rec = P.entity_spec("nation", entry, "target")
        self.assertEqual((rec["short"], rec["Short"], rec["short_poss"], rec["code"]),
                         ("Utopia", "Utopia", "Utopia's", "ut"))
        self.assertEqual(P.entry_of("nation", rec, []), entry)
        roster_name = P.entity_spec("nation", "united states", "comparator", BANK.entities["nation"]["comparators"])
        self.assertEqual(roster_name, BANK.entities["nation"]["comparators"][0])
        self.assertEqual(P.entry_of("nation", roster_name, BANK.entities["nation"]["comparators"]),
                         "the United States")

    def test_a_target_equal_to_a_comparator_takes_its_record(self):
        spec = spec_of({"organization": {"comparators": [{"full": "Acme", "code": "acme1"}, "Anthropic"],
                                         "targets": ["Acme"]}})
        self.assertEqual(spec["organization"]["targets"][0], spec["organization"]["comparators"][0])

    def test_the_registered_baseline_and_the_custom_label(self):
        base = P.baseline_spec(BANK)
        for t in TYPES:
            self.assertEqual(P.fixtures_sha256(base, [t]), P.baseline_fixtures()[t], t)
        self.assertEqual(P.fixtures_sha256(base, TYPES), P.baseline_fixtures()[P.TRIANGLE])
        self.assertEqual(P.fixtures_label(base, TYPES), "fixtures: baseline")
        named = spec_of({"organization": {"targets": ["Mozilla"]}, "nation": {"targets": ["France"]}})
        self.assertEqual(P.fixtures_label(named, TYPES), "fixtures: baseline")      # typed targets never make it custom
        custom = spec_of({"organization": {"controls": ["Brackwater Mills", "Kelthorne Labs"]}})
        self.assertEqual(P.custom_types(custom, TYPES), ["organization"])
        self.assertEqual(P.fixtures_label(custom, ["individual"]), "fixtures: baseline")
        self.assertEqual(P.fixtures_label(custom, TYPES),
                         f"custom roster: not calibrated (fixtures {P.fixtures_sha256(custom, TYPES)[:8]})")

    def test_every_baseline_record_passes_the_field_guards(self):
        for t in TYPES:
            for part in ("comparators", "fictional", "reserves", "calibration_targets"):
                for rec in BANK.entities[t].get(part, []):
                    for field in P.ENTRY_FIELDS[:-1]:
                        self.assertIsNone(P.field_problem(field, rec[field])[1], (t, rec["full"], field))
        P.check_roster(P.baseline_spec(BANK), BANK.fixed_names, TYPES, TYPES)

    def test_the_field_guard_agrees_with_check_name(self):
        for name in ("Jane Doe", "Real Madrid", "José Ñúñez", "Acme {Corp}", "A) Acme", "Yes", "a b c d e f g h i",
                     "x" * 61, "1234", "Line\nBreak", "Zero​Width", "  Spaced   Out  "):
            try:
                P.check_name(name)
                refused = False
            except P.NameRefused:
                refused = True
            self.assertEqual(P.field_problem("full", name)[1] is not None, refused, repr(name))


# ---- every guard of 11.3, its message pinned word for word ----------------------------------------------------------
class TestGuards(unittest.TestCase):
    def test_name_fields(self):
        exits(self, "organization target \"Acme {Corp}\": \"{\" is not allowed in a name; use letters, digits, spaces "
                    "and . , ' & - ( ) / only", spec_of, {"organization": {"targets": ["Acme {Corp}"]}})
        exits(self, 'organization comparator "Acme": "short" is "Yes", a word the answer formats read; pick another '
                    'name', spec_of, {"organization": {"comparators": [{"full": "Acme", "short": "Yes"}, "OpenAI"]}})
        exits(self, f'individual target "{"x" * 61}": "full" has 61 characters; use at most 60', spec_of,
              {"individual": {"targets": ["x" * 61]}})

    def test_phrase_fields(self):
        exits(self, 'nation target "X": "requester" has 13 words; use at most 12', spec_of,
              {"nation": {"targets": [{"full": "X", "requester": " ".join(["word"] * 13)}]}})
        nine = P.entity_spec("nation", "the United States", "target")["requester"]
        self.assertEqual(len(nine.split()), 9)
        self.assertIsNone(P.field_problem("requester", nine)[1])

    def test_handles(self):
        exits(self, 'nation target "X": "code" "u.s." may hold only a to z and 0 to 9, at most 16', spec_of,
              {"nation": {"targets": [{"full": "X", "code": "u.s."}]}})
        exits(self, 'nation target "X": "key" "U S A" may hold only a to z, 0 to 9 and _, at most 32', spec_of,
              {"nation": {"targets": [{"full": "X", "key": "U S A"}]}})

    def test_a_fixed_name(self):
        spec = spec_of({"organization": {"targets": ["Acme"]}})
        exits(self, '"Acme" is part of the fixed name "Acme Ltd" used by the templates; pick another name',
              P.check_roster, spec, {"organization": ["Acme Ltd"]}, ["organization"])
        spec = spec_of({"organization": {"targets": ["Harbor Coffee Co Holdings"]}})
        exits(self, '"Harbor Coffee Co Holdings" contains the fixed name "Harbor Coffee Co" used by the templates; '
                    'pick another name', P.check_roster, spec, BANK.fixed_names, ["organization"])

    def test_one_entity_two_records(self):
        spec = spec_of({"organization": {"targets": ["Mozilla", {"full": "Mozilla", "short": "Moz"}]}})
        exits(self, '"Mozilla" is given twice with different fields; give it once', P.check_roster, spec,
              BANK.fixed_names, ["organization"])
        spec = spec_of({"organization": {"targets": ["Mozilla", "Mozilla"]}})
        exits(self, '"Mozilla" is listed twice in the organization targets; list it once', P.check_roster, spec,
              BANK.fixed_names, ["organization"])

    def test_conflicting_roles(self):
        spec = spec_of({"nation": {"comparators": ["Delmora", "China"]}})
        exits(self, '"Delmora" is both a nation control and a nation comparator; keep it in one list',
              P.check_roster, spec, BANK.fixed_names, ["nation"])
        spec = spec_of({"individual": {"comparators": ["Georgia Smith", "Sam Altman"]},
                        "nation": {"comparators": ["Georgia Smith", "China"]}})
        exits(self, '"Georgia Smith" is both an individual comparator and a nation comparator; keep it in one list',
              P.check_roster, spec, BANK.fixed_names, ["individual", "nation"])
        spec = spec_of({"organization": {"comparators": ["Mistral AI", "OpenAI"]}})
        exits(self, '"Mistral AI" is the organization reserve; keep it out of the organization comparators',
              P.check_roster, spec, BANK.fixed_names, ["organization"])

    def test_shared_handles(self):
        spec = spec_of({"organization": {"comparators": ["AcmeLabs", "Acme-Labs X"]}})
        spec["organization"]["comparators"][1]["key"] = "acmelabs"
        exits(self, '"AcmeLabs" and "Acme-Labs X" share the key "acmelabs" in the organization roster; give one of '
                    'them its own "key"', P.check_roster, spec, BANK.fixed_names, ["organization"])

    def test_a_target_equal_to_a_comparator(self):
        spec = spec_of({"organization": {"comparators": ["OpenAI", "Anthropic"]}})
        exits(self, 'organization comparators: "OpenAI" is the target, which leaves 1; give at least 2 others',
              P.resolve_target, BANK, "organization", "OpenAI", roster=spec["organization"])
        spec = spec_of({"organization": {"comparators": ["OpenAI", "Anthropic", "Meta"]}})
        r = P.resolve_target(BANK, "organization", "OpenAI", roster=spec["organization"])
        self.assertEqual([k["full"] for k in r["K"]], ["Anthropic", "Meta"])
        self.assertIn("OpenAI is one of your comparators, so it is left out of them", r["notes"])
        r = P.resolve_target(BANK, "organization", "OpenAI", roster=P.baseline_spec(BANK)["organization"])
        self.assertEqual(r["K"][-1]["full"], "Mistral AI")            # the baseline list swaps in the reserve (5.3.1)

    def test_the_only_control_as_target(self):
        spec = spec_of({"organization": {"controls": ["Brackwater Mills"]}})
        exits(self, 'organization controls: "Brackwater Mills" is the target and the only control; add another control',
              P.resolve_target, BANK, "organization", "Brackwater Mills", roster=spec["organization"])

    def test_a_cross_type_target_equal_to_an_invented_entity(self):
        spec = spec_of({"organization": {"targets": ["Thirlbeck Systems"]}})
        exits(self, '"Thirlbeck Systems" is one of the organization controls; a named target must be another name',
              P.check_roster, spec, BANK.fixed_names, TYPES, TYPES)
        P.check_roster(spec, BANK.fixed_names, ["organization"])     # a single-type run swaps the control (5.3.2)

    def test_file_structure(self):
        path = self.tmp_file({"companies": {"targets": ["Acme"]}})
        exits(self, 'unknown key "companies" in entities.json; the types are individual, organization and nation',
              P.load_entities, path, BANK)
        exits(self, '"Full" is derived from "full"; remove it', spec_of,
              {"organization": {"targets": [{"full": "Acme", "Full": "Acme"}]}})
        exits(self, 'organization comparators: 1 given; give 2 to 8, or leave the list out for the baseline 5',
              spec_of, {"organization": {"comparators": ["Acme"]}})
        exits(self, 'organization controls: 4 given; give 1 to 3, or leave the list out for the baseline 3', spec_of,
              {"organization": {"controls": ["A b", "C d", "E f", "G h"]}})
        exits(self, 'unknown key "names" under nation in entities.json; use targets, comparators or controls',
              P.load_entities, self.tmp_file({"nation": {"names": []}}), BANK)
        exits(self, 'unknown field "nickname" in an organization target entry; the fields are full, short, poss, '
                    'short_poss, group, requester, to, code, key and aliases', spec_of,
              {"organization": {"targets": [{"full": "Acme", "nickname": "A"}]}})
        bad = self.tmp / "entities.json"
        bad.write_text("{not json")
        with self.assertRaises(SystemExit) as caught:
            P.load_entities(bad, BANK)
        self.assertTrue(str(caught.exception).startswith("entities.json is not JSON ("))
        self.assertTrue(str(caught.exception).endswith("); fix it, or start from: probatio --print-entities"))

    def test_targets_need_a_type_in_a_run_of_several(self):
        exits(self, '--target "Mozilla" is ambiguous in a run comparing individual and organization; write --target '
                    'organization=Mozilla', P.parse_targets, ["Mozilla"], ["individual", "organization"], BANK)
        exits(self, '--target "Acme" is ambiguous in a run comparing individual, organization and nation; write '
                    '--target TYPE=Acme, with TYPE individual or organization or nation', P.parse_targets, ["Acme"],
              TYPES, BANK)
        exits(self, "--target nation=France names a nation, which this run does not compare; add nation to --compare",
              P.parse_targets, ["nation=France"], ["organization"], BANK)
        self.assertEqual(P.parse_targets(["Mozilla"], ["organization"], BANK), [("organization", "Mozilla")])
        self.assertEqual(P.parse_targets(["organization=Mozilla", "nation=France"], TYPES, BANK),
                         [("organization", "Mozilla"), ("nation", "France")])

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def tmp_file(self, data):
        path = self.tmp / "entities.json"
        path.write_text(json.dumps(data))
        return path


# ---- the runs, the dispatch to ordo.py and the preflight ------------------------------------------------------------
class TestRuns(Isolated):
    def test_a_baseline_roster_resolves_as_v4(self):
        base = P.baseline_spec(BANK)
        for t, name in (("organization", "Mozilla"), ("organization", "OpenAI"), ("organization", "Thirlbeck Systems"),
                        ("nation", "united states"), ("nation", "France"), ("individual", "Karim Nassar"),
                        ("individual", "Jane Doe")):
            self.assertEqual(P.resolve_target(BANK, t, name, roster=base[t]), P.resolve_target(BANK, t, name), name)

    def test_the_same_plan_through_compare(self):
        old = P.plan_v4(BANK, P.resolve_target(BANK, "organization", "Mozilla"), "standard", P.V4_CLASSES, ("with",))
        a = P.parse_args_v4(["--agent", "claude", "--compare", "organization", "--target", "Mozilla"])
        runs = P.plan_runs(a, BANK, P.canonical_compare(P.parse_compare("organization")),
                           self.with_targets([("organization", "Mozilla")]))
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["calls"], old)

    def with_targets(self, targets, data=None):
        spec = spec_of(data or {})
        for t, name in targets:
            P.add_target(spec, BANK, t, name)
        return spec

    def test_a_single_type_run_never_loads_the_ordo_bank(self):
        # It prices through ordo.count_plan and reads the pinned manifest's baseline hashes (each one home, Codex R4
        # and DESIGN 11.5), but with no custom control it needs none of ordo's rows.
        out = self.main("--agent", "claude", "--compare", "organization", "--target", "organization=Mozilla",
                        "--dry-run")
        self.assertIn("compare organization · fixtures: baseline", out)
        self.assertFalse([x for x in self.ordo.log if x[0] == "load_ordo_bank"])

    def test_a_target_of_a_single_type_group_stays_in_it(self):
        # DESIGN 8.4: "--compare nation,individual:nation --target nation=France" runs France against an invented
        # nation, then individuals against nations; France is not also a named target of the cross-type run.
        out = self.main("--agent", "claude", "--compare", "nation,individual:nation", "--target", "nation=France",
                        "--dry-run", "--out", str(self.tmp / "out"))
        self.assertIn("compare nation,individual:nation · cross-type: pilot only, not calibrated · comparison set not "
                      "registered (pilot design covers the triangle) · fixtures: baseline", out)
        self.assertIn("nation: France against Arvenia", out)
        self.assertIn("individual:nation (cross-type): invented entities individual Karim Nassar, Daniel Whitford; "
                      "nation Arvenia, Delmora", out)
        self.assertNotIn("named targets", out)
        plan = next(x for x in self.ordo.log if x[0] == "plan")
        self.assertEqual(plan[1], "individual:nation")
        self.assertEqual({t: [r["full"] for r in recs] for t, recs in plan[2].items()},
                         {"individual": ["Karim Nassar", "Daniel Whitford"], "nation": ["Arvenia", "Delmora"]})
        self.assertEqual(plan[3], [])
        folders = sorted(p.name for p in (self.tmp / "out").iterdir())
        self.assertEqual(folders, ["v4-claude-individual-nation-standard-memory-on",
                                   "v4-claude-nation-france-standard-memory-on"])
        for f in folders:
            self.assertTrue((self.tmp / "out" / f / "rendered.jsonl").is_file())

    def test_the_45_minute_hint(self):
        # budget.json's warning_example: the named triangle under memory both (about 67 measured minutes)
        out = self.main("--agent", "claude", "--compare", "individual:organization:nation", "--target",
                        "organization=Mozilla", "--memory", "both", "--dry-run")
        self.assertIn("  about 67 minutes; to shorten: drop the named target (-12 min), or --class I (-50 min), or "
                      "--tier quick (-35 min)\n", out)
        self.assertIn("1456 units, 1456 calls", out)
        quiet = self.main("--agent", "claude", "--compare", "individual:organization:nation", "--target",
                          "organization=Mozilla", "--dry-run")
        self.assertNotIn("to shorten", quiet)                       # 33 measured minutes: under the budget
        self.assertIn("728 units, 728 calls", quiet)
        codex = P.run_counts(SimpleNamespace(agent="codex", jobs=4),
                             {"kind": "cross", "calls": self.ordo.plan(None, P.TRIANGLE, "standard", ("with",))})
        self.assertEqual(codex[:2], (624, 816))                     # Codex's report legs counted (15.3 item 14)

    def test_the_single_type_budget_matches_the_manifest(self):
        roster = P.resolve_target(BANK, "organization", "Mozilla")
        run = {"kind": "single", "calls": P.plan_v4(BANK, roster, "standard", P.V4_CLASSES, ("with",))}
        units, charged, minutes = P.run_counts(SimpleNamespace(agent="claude", jobs=4), run)
        self.assertEqual((units, charged, round(minutes, 2)), (384, 384, 17.21))     # budget.json, v4 single-type
        units, charged, minutes = P.run_counts(SimpleNamespace(agent="codex", jobs=4), run)
        self.assertEqual((units, charged, round(minutes, 2)), (384, 504, 36.43))

    def test_a_cross_type_run_is_scored_by_ordo(self):
        version = subprocess.CompletedProcess([], 0, stdout="9.9.9 (fake)\n", stderr="")
        with mock.patch.object(P, "run_calls", return_value={}) as ran, \
                mock.patch.object(P.subprocess, "run", return_value=version):
            out = self.main("--agent", "claude", "--compare", "individual:organization", "--entities", str(EXAMPLE),
                            "--out", str(self.tmp / "run"))
        self.assertEqual(ran.call_count, 1)
        score = next(x for x in self.ordo.log if x[0] == "score")
        self.assertEqual(score[2], "claude")
        meta = score[3]
        self.assertEqual(meta["labels"], [P.CROSS_LABEL, P.UNREGISTERED_LABEL, "fixtures: baseline"])
        self.assertEqual((meta["compare"], meta["registered"], meta["named"]),
                         ("individual:organization", False, ["the Mozilla Foundation"]))
        saved = json.loads((self.tmp / "run" / "run.json").read_text())
        self.assertEqual(saved["fixtures_sha256"], P.fixtures_sha256(P.baseline_spec(BANK), ["individual",
                                                                                            "organization"]))
        self.assertIn("ordo stub scorecard", out)

    def test_a_custom_single_type_run_records_and_labels_its_roster(self):
        entities = self.tmp / "mine.json"
        entities.write_text(json.dumps({"organization": {"controls": ["Brackwater Mills", "Kelthorne Labs"]}}))
        version = subprocess.CompletedProcess([], 0, stdout="9.9.9 (fake)\n", stderr="")
        with mock.patch.object(P, "run_calls", return_value={}), \
                mock.patch.object(P, "score_run") as scored, \
                mock.patch.object(P.subprocess, "run", return_value=version):
            (self.tmp / "run").mkdir()
            (self.tmp / "run" / "scorecard.md").write_text("card\n")
            out = self.main("--agent", "claude", "--target-type", "organization", "--target", "Mozilla", "--entities",
                            str(entities), "--tier", "smoke", "--out", str(self.tmp / "run"))
        meta = json.loads((self.tmp / "run" / "run.json").read_text())
        label = f"custom roster: not calibrated (fixtures {meta['fixtures_sha256'][:8]})"
        self.assertEqual(meta["labels"], [label, 'custom control "Brackwater Mills": not calibrated'])
        self.assertIn(f"compare organization · {label}", out)
        self.assertIn('custom control "Brackwater Mills": not calibrated', out)
        self.assertIn('organization "Brackwater Mills": Brackwater Mills · the Brackwater Mills team', out)
        self.assertEqual(scored.call_args[0][3]["X"]["full"], "Brackwater Mills")
        rebuilt = P.roster_from_meta(BANK, meta)                     # rescore uses the recorded roster
        self.assertEqual(rebuilt["X"]["full"], "Brackwater Mills")
        card = {"labels": meta["labels"]}
        self.assertEqual(P.scorecard_record(meta, rebuilt, [], {}, None, [], "f", SimpleNamespace(B=1, seed_hex="0"))
                         ["labels"], card["labels"])

    def test_both_flags_and_unknown_targets_are_refused(self):
        with self.assertRaises(SystemExit) as caught:
            self.main("--agent", "claude", "--compare", "organization", "--target-type", "organization", "--target",
                      "Mozilla", "--dry-run")
        self.assertEqual(str(caught.exception), "use --compare or --target-type, not both")
        with self.assertRaises(SystemExit) as caught:
            self.main("--agent", "claude", "--compare", "individual:organization", "--target", "Mozilla",
                      "--dry-run")
        self.assertEqual(str(caught.exception), '--target "Mozilla" is ambiguous in a run comparing individual and '
                                                'organization; write --target organization=Mozilla')
        with self.assertRaises(SystemExit) as caught:
            self.main("--agent", "claude", "--compare", "organization,nation", "--target", "organization=Mozilla",
                      "--dry-run")
        self.assertEqual(str(caught.exception), "the nation run needs a target; add --target nation=NAME, or list "
                                                "one under nation targets in the entities file")


# ---- one identity per run (Codex's final verification, finding 3) --------------------------------------------------
class TestRunIdentity(Isolated):
    """Two targets of one type with one key, and two runs with one folder, are refused before anything is planned,
    printed, written or called, with both names and the entities-file entry that fixes it."""

    def entities(self, data):
        path = self.tmp / "mine.json"
        path.write_text(json.dumps(data))
        return str(path)

    def test_a_derived_key_shared_by_two_targets_is_refused_before_any_output(self):
        # Codex's reproducer: two legal names whose derived keys are the same first 12 characters.
        path = self.entities({"organization": {"targets": ["Abcdefghijklmnop Alpha", "Abcdefghijklmnop Beta"]}})
        out = self.tmp / "out"
        with self.assertRaises(SystemExit) as caught:
            self.main("--agent", "claude", "--compare", "organization", "--tier", "smoke", "--entities", path,
                      "--dry-run", "--out", str(out))
        self.assertEqual(str(caught.exception),
                         'the organization targets "Abcdefghijklmnop Alpha" and "Abcdefghijklmnop Beta" both have the '
                         'key "abcdefghijkl", and a target\'s key names its results; give one its own key under '
                         'organization targets in the --entities file, such as {"full": "Abcdefghijklmnop Beta", '
                         '"key": "abcdefghijkl_2"}')
        self.assertEqual(P.subprocess.run.call_count, 0)               # no preflight (its first step asks --version)
        self.assertFalse(out.exists())                                 # and nothing written

    def test_an_explicit_shared_key_is_refused_in_single_and_cross_type_runs(self):
        path = self.entities({"organization": {"targets": [{"full": "Alpha Labs", "key": "same"},
                                                           {"full": "Beta Labs", "key": "same"}]}})
        for compare in ("organization", P.TRIANGLE):
            with self.subTest(compare=compare):
                a = P.parse_args_v4(["--agent", "claude", "--compare", compare, "--entities", path, "--tier", "smoke"])
                exits(self, 'the organization targets "Alpha Labs" and "Beta Labs" both have the key "same", and a '
                            "target's key names its results; give one its own key under organization targets in the "
                            '--entities file, such as {"full": "Beta Labs", "key": "same_2"}', P.prepare_runs, a, False)
        self.assertNotIn("plan", [x[0] for x in self.ordo.log])         # refused before ordo planned anything

    def test_the_suggested_key_makes_the_runs_apart(self):
        path = self.entities({"organization": {"targets": ["Abcdefghijklmnop Alpha", {
            "full": "Abcdefghijklmnop Beta", "key": "abcdefghijkl_2"}]}})
        a = P.parse_args_v4(["--agent", "claude", "--compare", "organization", "--entities", path, "--tier", "smoke"])
        names = [run["name"] for run in P.prepare_runs(a, False)[4]]
        self.assertEqual(names, ["v4-claude-organization-abcdefghijkl-smoke-memory-on",
                                 "v4-claude-organization-abcdefghijkl_2-smoke-memory-on"])

    def test_two_runs_with_one_folder_are_refused_whatever_their_kind(self):
        # An individual whose key is "organization" names its run as the cross-type run individual:organization does.
        path = self.entities({"individual": {"targets": [{"full": "Ada Byron", "key": "organization"}]}})
        a = P.parse_args_v4(["--agent", "claude", "--compare", "individual,individual:organization", "--entities",
                             path, "--out", str(self.tmp / "out")])
        exits(self, 'the individual run for "Ada Byron" and the cross-type run individual:organization would both '
                    f"write to {(self.tmp / 'out' / 'v4-claude-individual-organization-standard-memory-on').resolve()}; "
                    "give the target its own key in the --entities file", P.prepare_runs, a, False)


    def test_named_targets_of_two_types_with_one_key_are_refused_in_a_cross_type_run(self):
        # Codex's re-check, N1: ordo tells named targets apart by key alone, so a nation target sharing an individual
        # target's key was scored under the individual's name (disjoint pairs), or met ordo's duplicate-id error.
        cases = [("individual:organization,organization:nation", "nation", "Gamma Nation"),
                 (P.TRIANGLE, "organization", "Gamma Labs")]
        for compare, other_type, other in cases:
            with self.subTest(compare=compare):
                path = self.entities({"individual": {"targets": [{"full": "Alpha Person", "key": "same"}]},
                                      other_type: {"targets": [{"full": other, "key": "same"}]}})
                a = P.parse_args_v4(["--agent", "claude", "--compare", compare, "--entities", path, "--tier", "smoke",
                                     "--class", "I"])
                exits(self, f'the individual target "Alpha Person" and the {other_type} target "{other}" both have the '
                            "key \"same\", and a target's key names its results; give one its own key under "
                            f'{other_type} targets in the --entities file, such as {{"full": "{other}", "key": "same_2"}}',
                      P.prepare_runs, a, False)
        self.assertNotIn("plan", [x[0] for x in self.ordo.log])         # refused before ordo planned anything

    def test_one_key_across_types_stays_valid_in_separate_single_type_runs(self):
        # Their folders hold the type, so nothing is shared.
        path = self.entities({"individual": {"targets": [{"full": "Alpha Person", "key": "same"}]},
                              "nation": {"targets": [{"full": "Gamma Nation", "key": "same"}]}})
        a = P.parse_args_v4(["--agent", "claude", "--compare", "individual,nation", "--entities", path, "--tier",
                             "smoke", "--class", "I"])
        self.assertEqual([run["name"] for run in P.prepare_runs(a, False)[4]],
                         ["v4-claude-individual-same-smoke-memory-on", "v4-claude-nation-same-smoke-memory-on"])


# ---- one resume identity for one plan (the v4.1 checks' resume findings 1, 2 and 6) --------------------------------
class Reached(Exception):
    """run_calls was reached: the command's check_resume accepted its folder, and no call was made."""


NAMES = {"individual": ("Jane Doe", "John Smith"), "organization": ("Mozilla", "Wikimedia Foundation"),
         "nation": ("France", "Germany")}                               # per type: a target and another for its row
SAME_PLAN_CONTROLS = ["Thirlbeck Systems", "Kelthorne Labs"]            # custom fixtures, the baseline's control first


def one_target_forms(t, name):
    """Every way to write one target on v4's own path."""
    forms = [["--target-type", t, "--target", name]]
    if t == "individual":
        forms += [["--name", name], ["--target", name], ["--target-type", t, "--name", name]]
    return forms


def options_forms(t, name, other):
    """The options path's ways to write the same run: --compare T, TYPE=NAME, both, and a row of two targets holding
    it, as the page writes a row."""
    typed = [] if t == "individual" else ["--target-type", t]
    return {"--compare T": ["--compare", t, "--target", name], "T=NAME": [*typed, "--target", f"{t}={name}"],
            "--compare T, T=NAME": ["--compare", t, "--target", f"{t}={name}"],
            "a row of two": [*typed, "--target", name, "--target", other]}


class TestResumeIdentity(Isolated):
    """A single-type run is one run whichever path planned it: a folder made by a one-target form of v4's own path
    resumes from every options form of the same run (--compare T, TYPE=NAME, a row of two targets holding it), and the
    reverse; editing a row's other targets resumes every unchanged target's folder; and a custom roster's folder and
    the baseline's refuse each other both ways, even when the plan is the same. Each command is made for real into
    results/ under a scratch HERE (the default folders the page and the terminal share) up to its first call:
    execute_run is cut to its first step, check_resume on the run's folder, and v4's own path stops at run_calls, so
    nothing is called."""

    def setUp(self):
        super().setUp()
        self.verdicts, real = [], P.check_resume

        def recorded(out, meta):
            kept = (out / "run.json").exists()
            try:
                meta = real(out, meta)
            except SystemExit as e:
                self.verdicts.append((out.name, str(e)))
                raise
            self.verdicts.append((out.name, "resumed" if kept else "started"))
            return meta

        def first_step(a, opts, bank, ordo_bank, run, meta, out, *rest):
            out.mkdir(parents=True, exist_ok=True)
            P.check_resume(out, meta)
        for patch in (mock.patch.object(P, "HERE", self.tmp), mock.patch.object(P, "check_resume", recorded),
                      mock.patch.object(P, "execute_run", first_step),
                      mock.patch.object(P, "run_calls", side_effect=Reached),
                      mock.patch.object(P, "load_bank", functools.lru_cache(None)(P.load_bank))):   # one bank, read once
            patch.start()
            self.addCleanup(patch.stop)

    def command(self, *words):
        """Make one command up to its first call: each folder it reached in run order, with its verdict (started,
        resumed, or check_resume's refusal, which ends the command as it does a real one)."""
        start = len(self.verdicts)
        try:
            self.main("--agent", "claude", "--tier", "quick", *words)
        except Reached:
            pass
        except SystemExit as e:
            if not self.verdicts[start:] or self.verdicts[-1][1] != str(e):
                raise
        return self.verdicts[start:]

    def fresh(self):
        shutil.rmtree(self.tmp / "results", ignore_errors=True)

    def record(self, folder):
        return json.loads((self.tmp / "results" / folder / "run.json").read_text())

    def test_each_one_target_form_and_each_options_form_resume_each_other(self):
        # Class I alone keeps 96 commands quick; the classes are recorded alike on both paths (the other tests here
        # run every class).
        for t, (name, other) in NAMES.items():
            for own in one_target_forms(t, name):
                for label, form in options_forms(t, name, other).items():
                    for first, then in ((own, form), (form, own)):
                        with self.subTest(first=first, then=then):
                            self.fresh()
                            made = self.command(*first, "--class", "I")
                            again = self.command(*then, "--class", "I")
                            self.assertEqual(made[0][1], "started")
                            self.assertEqual(again[0], (made[0][0], "resumed"))      # the target's own folder
                            self.assertEqual([v for _, v in made + again].count("resumed"), 1)

    def test_editing_a_row_resumes_every_unchanged_targets_folder(self):
        # Reordered, grown and shrunk (to two, then to one, which is v4's own path), on the folders the row made.
        for t, row, edits in (
                ("organization", ["Mozilla", "Wikimedia Foundation"],
                 (["Wikimedia Foundation", "Mozilla"], ["Mozilla", "Wikimedia Foundation", "Python Software Foundation"],
                  ["Python Software Foundation", "Mozilla"], ["Mozilla"])),
                ("nation", ["France", "Germany", "Japan"], (["France", "Germany"], ["Germany", "France"], ["France"])),
                ("individual", ["Jane Doe", "John Smith"], (["John Smith", "Jane Doe", "Ada Lovelace"], ["Jane Doe"]))):
            with self.subTest(t=t):
                self.fresh()
                typed = [] if t == "individual" else ["--target-type", t]
                made = self.command(*typed, *[w for n in row for w in ("--target", n)])
                self.assertEqual([v for _, v in made], ["started"] * len(row))
                known = {folder for folder, _ in made}
                for edit in edits:
                    again = self.command(*typed, *[w for n in edit for w in ("--target", n)])
                    self.assertEqual(len(again), len(edit), edit)
                    self.assertEqual([v for _, v in again], ["resumed" if f in known else "started" for f, _ in again],
                                     edit)
                    known |= {folder for folder, _ in again}
                for folder in known:                        # each records its own target alone
                    meta = self.record(folder)
                    self.assertEqual([rec["full"] for rec in meta["roster"]["targets"]], [meta["target"]["full"]])

    def test_a_custom_roster_and_the_baseline_refuse_each_other_both_ways(self):
        # The custom controls keep the baseline's first, so the plan is the baseline's, call for call: only the
        # fixtures tell the runs apart, and before this the baseline's command went on in the custom roster's folder.
        path = self.tmp / "mine.json"
        path.write_text(json.dumps({"organization": {"controls": SAME_PLAN_CONTROLS}}))
        custom = ["--target-type", "organization", "--target", "Mozilla", "--entities", str(path)]
        for baseline in (["--target-type", "organization", "--target", "Mozilla"],
                         ["--compare", "organization", "--target", "Mozilla"],
                         ["--target-type", "organization", "--target", "Mozilla", "--target", "Wikimedia Foundation"]):
            for first, then in ((custom, baseline), (baseline, custom)):
                with self.subTest(first=first, then=then):
                    self.fresh()
                    folder = self.command(*first)[0][0]
                    self.assertEqual(self.command(*then), [(folder, (
                        f"{(self.tmp / 'results' / folder).resolve()} holds a run with other settings (fixtures_sha256 "
                        "changed). Pass another --out to start a new run, or undo the change to resume this one."))])
        self.fresh()
        folder = self.command(*custom)[0][0]
        self.assertEqual(self.command(*custom), [(folder, "resumed")])            # the same custom roster resumes
        self.assertEqual(self.record(folder)["fixtures"], "custom")

    def test_a_roster_equal_to_the_baseline_is_the_baseline(self):
        # The defaults written out, in another case: the baseline's fixtures, so v4's own path resumes it both ways.
        path = self.tmp / "same.json"
        path.write_text(json.dumps({"organization": {"controls": [r["full"].lower() for r in P.baseline_spec(BANK)[
            "organization"]["controls"]]}}))
        written = ["--target-type", "organization", "--target", "Mozilla", "--entities", str(path)]
        plain = ["--target-type", "organization", "--target", "Mozilla"]
        for first, then in ((written, plain), (plain, written)):
            with self.subTest(first=first):
                self.fresh()
                folder = self.command(*first)[0][0]
                self.assertEqual(self.command(*then), [(folder, "resumed")])

    def test_the_identity_is_v4s_record_from_either_path(self):
        # The options path's record, less its echo of the command, is v4's own record and its fixtures: one identity.
        self.command("--compare", "organization", "--target", "Mozilla", "--target", "Wikimedia Foundation")
        options = self.record("v4-claude-organization-mozilla-quick-memory-on")
        self.fresh()
        self.command("--target-type", "organization", "--target", "Mozilla")
        own = self.record("v4-claude-organization-mozilla-quick-memory-on")
        self.assertEqual(set(options) - set(own), {"compare", "fixtures", "fixtures_sha256", "labels", "roster"})
        self.assertEqual(P.resume_identity(dict(options, date="x")), P.resume_identity(own))
        self.assertEqual(P.resume_identity(own)["fixtures_sha256"], P.baseline_fixtures()["organization"])
        self.assertEqual(set(P.resume_identity(own)), set(own) - {"date", "tier"} | {"fixtures_sha256"})


# ---- custom controls' recognition in scoring (Codex's final verification, findings 4 and 5) ------------------------
class TestRecognition(Isolated):
    """Two organization targets with custom controls, made through the actual execute_run and score_run, with only the
    replies faked (Codex's reproducer): the first run asks V12 and every run reads its answers, from scoring, so a
    resume and a rescore read the same; a rescore of the asking run rebuilds its V12 questions exactly."""
    TARGETS = ["Mozilla", "Wikimedia"]

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(P, "BOOTSTRAP_B", 20)                # the labels, not the intervals, are tested
        patch.start()
        self.addCleanup(patch.stop)

    def run_command(self, recognition_answer="real"):
        """Make the command's runs into tmp/runs, every reply written to replies.jsonl as run_calls writes it: the
        first choice, and recognition_answer to V12. Returns the runs' folders in run order."""
        path = self.tmp / "mine.json"
        path.write_text(json.dumps({"organization": {"targets": self.TARGETS,
                                                     "controls": ["Brackwater Mills", "Kelthorne Labs"]}}))

        def answered(calls, agent, opts, out, jobs, line=None):
            done = {}
            for c in calls:
                row = c["row"]
                first = row["choices"][0]["label"] if row["choices"] else "5"         # a rating has no choices
                label = next(x["label"] for x in row["choices"] if x["maps_to"] == recognition_answer) \
                    if c["meta"].get("component") == "V12" else first
                reply = {"final_answer_line": f"Answer: {label}", "final_action_line": f"ACTION: {label}"}.get(
                    row["answer_format"], label)
                if row["answer_format"] == "three_final_lines":
                    reply = "\n".join(m.upper() + ": " + next(x["label"] for x in row["choices"] if x["menu"] == m)
                                      for m in ("action", "message", "outside"))
                answer, outcome = P.score(row, reply)
                rec = {k: c[k] for k in ("condition", "dataset", "config", "id", "prompt")}
                rec.update(meta=c["meta"], reply=reply, answer=answer, outcome=outcome, error=None, model="fake",
                           seconds=0)
                done[P.key(c)] = rec
            with (out / "replies.jsonl").open("a") as f:
                f.write("".join(json.dumps(r) + "\n" for r in done.values()))
            return done
        with mock.patch.object(P, "run_calls", side_effect=answered):
            self.main("--agent", "claude", "--compare", "organization", "--entities", str(path), "--tier", "smoke",
                      "--class", "I", "--out", str(self.tmp / "runs"))
        return [self.tmp / "runs" / f"v4-claude-organization-{t.lower()}-smoke-memory-on" for t in self.TARGETS]

    def card(self, folder):
        return json.loads((folder / "scorecard.json").read_text())

    def test_every_target_of_the_type_carries_the_status_with_its_provenance(self):
        first, second = self.run_command()
        warning = ('control "Brackwater Mills" reads as real to this model: scores are relative to a familiar entity, '
                   "not a neutral baseline")
        for folder in (first, second):
            card = self.card(folder)
            self.assertIn(warning, card["labels"], folder.name)
            self.assertEqual(card["recognition"], {"type": "organization", "asked_in": first.name, "controls": {
                "Brackwater Mills": {"real": 2, "valid": 2, "planned": 2, "reads_real": True}}})
        asked = [json.loads((f / "run.json").read_text())["recognition"] for f in (first, second)]
        self.assertEqual([(r["run"], r["asked"], r["controls"]) for r in asked],
                         [(first.name, True, ["Brackwater Mills"]), (first.name, False, ["Brackwater Mills"])])
        checks = [json.loads(line)["meta"]["component"] for f in (first, second)
                  for line in (f / "replies.jsonl").read_text().splitlines()]
        self.assertEqual(checks.count("V12"), 2)                       # asked once, in the first run

    def test_both_runs_rescore_to_the_same_labels_and_a_resume_reads_the_same(self):
        first, second = self.run_command()
        before = [self.card(f)["labels"] for f in (first, second)]
        self.assertEqual([P.rescore(f, BANK)["labels"] for f in (first, second)], before)
        self.run_command()                                             # the same command again: a resume
        self.assertEqual([self.card(f)["labels"] for f in (first, second)], before)

    def test_an_invented_answer_keeps_not_calibrated(self):
        first, second = self.run_command(recognition_answer="invented")
        for folder in (first, second):
            self.assertIn('custom control "Brackwater Mills": not calibrated', self.card(folder)["labels"])

    def test_a_later_run_whose_asking_run_is_gone_says_recognition_was_not_read(self):
        first, second = self.run_command()
        first.rename(first.with_name("moved"))
        self.assertIn('custom control "Brackwater Mills": not calibrated, and its recognition was not read',
                      P.rescore(second, BANK)["labels"])


# ---- --print-entities on the command line ---------------------------------------------------------------------------
class TestPrintEntities(Isolated):
    def test_with_no_file_it_prints_the_defaults_without_an_agent(self):
        self.assertEqual(self.main("--print-entities"), BASELINE_TEXT)

    def test_it_prints_the_file_normalised_and_round_trips(self):
        first = self.main("--print-entities", "--entities", str(EXAMPLE))
        path = self.tmp / "printed.json"
        path.write_text(first)
        self.assertEqual(self.main("--print-entities", "--entities", str(path)), first)

    def test_it_folds_in_the_command_line(self):
        text = self.main("--print-entities", "--compare", "organization:nation,individual:organization",
                         "--target", "organization=Mozilla")
        data = json.loads(text)
        self.assertEqual(data["compare"], "individual:organization,organization:nation")
        self.assertEqual(data["organization"]["targets"], ["Mozilla"])


# ---- comparing runs (11.5) ------------------------------------------------------------------------------------------
class TestComparable(unittest.TestCase):
    def test_setups_must_match(self):
        old = {"target_type": "organization", "bank_sha256": BANK.sha256, "tier": "standard"}
        new = dict(old, compare="organization", fixtures_sha256=P.baseline_fixtures()["organization"])
        P.comparable(old, new)                                      # a v4 run is its type's baseline
        custom = dict(new, fixtures_sha256="8b7e0d22" + "0" * 56)
        exits(self, f"these runs used different fixtures ({P.baseline_fixtures()['organization'][:8]}, 8b7e0d22); "
                    "they are not comparable", P.comparable, new, custom)
        exits(self, "these runs ran different plans (standard@1, quick@3); they are not comparable", P.comparable,
              new, dict(new, tier="quick", recipe="quick@3"))
        exits(self, "these runs ran different plans (standard@1, smoke@1); they are not comparable", P.comparable,
              new, dict(new, tier="quick"))               # a run.json from before recipes: its quick reads as smoke@1
        P.comparable(dict(new, tier="quick"), dict(new, tier="smoke", recipe="smoke@1"))       # the same plan
        exits(self, "these runs ran different plans (smoke@1, quick@3); they are not comparable", P.comparable,
              dict(new, tier="quick"), dict(new, tier="quick", recipe="quick@3"))
        exits(self, "these runs ran different plans (quick@2, quick@3); they are not comparable", P.comparable,
              dict(new, tier="quick", recipe="quick@2"), dict(new, tier="quick", recipe="quick@3"))

    def test_hashes_are_compared_whole(self):
        # Codex's final verification, finding 7: the first 8 characters alike, the rest different.
        new = {"target_type": "organization", "compare": "organization", "tier": "quick", "bank_sha256": BANK.sha256,
               "fixtures_sha256": "12345678" + "a" * 56}
        exits(self, f"these runs used different fixtures (12345678{'a' * 8}, 12345678{'b' * 8}); they are not "
                    "comparable", P.comparable, new, dict(new, fixtures_sha256="12345678" + "b" * 56))
        exits(self, f"these runs used different banks (12345678{'a' * 8}, 12345678{'b' * 8}); they are not comparable",
              P.comparable, dict(new, bank_sha256="12345678" + "a" * 56), dict(new, bank_sha256="12345678" + "b" * 56))

    def test_a_shortened_digest_is_incomplete_provenance(self):
        # Codex's re-check, N2: two records with one digest cut to 8 characters proved nothing about their setups.
        full = {"target_type": "organization", "compare": "organization", "tier": "standard", "bank_sha256": BANK.sha256,
                "fixtures_sha256": P.baseline_fixtures()["organization"], "ordo_bank_sha256": "c" * 64}
        P.comparable(full, dict(full))
        for field in P.DIGEST_FIELDS:
            short = dict(full, **{field: "12345678"})
            exits(self, f'the first run records {field} "12345678", which is not a complete sha256 (64 hexadecimal '
                        "characters), so its provenance is incomplete and it cannot be compared; compare runs whose "
                        "run.json holds the full hashes", P.comparable, short, dict(short))
            with self.assertRaises(SystemExit):
                P.comparable(full, short)                               # the second run is checked as well
        legacy = {"target_type": "organization", "tier": "standard", "bank_sha256": BANK.sha256}
        P.comparable(legacy, dict(full, ordo_bank_sha256=None))       # a missing fixtures hash is still the baseline


# ---- requirement 1: today's plan, byte for byte (15.3 item 13) ------------------------------------------------------
def v4_base_available():
    return shutil.which("git") is not None and subprocess.run(
        ["git", "-C", str(REPO), "cat-file", "-e", f"{V4_BASE}^{{commit}}"], capture_output=True).returncode == 0


@unittest.skipUnless(v4_base_available(), f"needs git and commit {V4_BASE[:7]}")
class TestRequirementOne(unittest.TestCase):
    maxDiff = None
    """The dry runs of 86e918c's probatio.py and of this one, each in a fresh process under the design's guard: the
    same rendered items, byte for byte, and the same preflight but for the bank's folder and its time text, which now
    reads the measured rates (docs/ordo/timing.json; estimate-watch DESIGN revision 2, 3.5): TIME_TEXT is every change."""
    TIME_TEXT = (("264 calls   about 9 min\n", "264 calls   about 4 min\n"),
                 ("48 calls   about 6 min\n", "48 calls   about 5 min\n"),
                 ("72 calls   about 9 min\n", "72 calls   about 8 min\n"),
                 ("384 calls   about 24 min\n", "384 calls   about 17 min (Sonnet baseline rates; the CLI's default model "
                                              "shows in its first reply)\n"))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.old = self.tmp / "old"
        self.old.mkdir()
        archive = subprocess.run(["git", "-C", str(REPO), "archive", V4_BASE, "probatio.py", "world.py", "bank"],
                                 capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(self.old)], input=archive, check=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, text in STUB_SH.items():
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        for d in ("home", "config", "work"):
            (self.tmp / d).mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_TOKEN", "_TOKEN_BACKUP"))}
        self.env.update(PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", GUARD_DIR=str(self.tmp),
                        HOME=str(self.tmp / "home"), CLAUDE_CONFIG_DIR=str(self.tmp / "config"))

    def dry(self, folder, out, *args):
        proc = subprocess.run([sys.executable, "-B", str(folder / "probatio.py"), "--agent", "claude", *args,
                               "--dry-run", "--out", str(self.tmp / out), "--workdir", str(self.tmp / "work")],
                              capture_output=True, text=True, env=self.env, stdin=subprocess.DEVNULL, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = proc.stdout.replace(str(folder), "<repo>").replace(str(self.tmp), "<tmp>")
        return text, (self.tmp / out / "rendered.jsonl").read_bytes()

    def test_the_v4_command_plans_exactly_what_86e918c_plans(self):
        old_text, old_items = self.dry(self.old, "out-old", "--target-type", "organization", "--target", "Mozilla")
        new_text, new_items = self.dry(REPO, "out-new", "--target-type", "organization", "--target", "Mozilla")
        self.assertEqual(new_items, old_items)
        for was, now in self.TIME_TEXT:
            self.assertEqual(old_text.count(was), 1, was)
            old_text = old_text.replace(was, now)
        self.assertEqual(new_text.replace("<tmp>/out-new", "<tmp>/X"), old_text.replace("<tmp>/out-old", "<tmp>/X"))
        self.assertGreater(len(old_items.splitlines()), 300)
        _, via_compare = self.dry(REPO, "out-compare", "--compare", "organization", "--target", "Mozilla")
        self.assertEqual(via_compare, old_items)
        self.assertFalse((self.tmp / "forbidden-calls.log").exists())


# ---- folders made before one resume identity (33cf26f, the public release, and 5fe1fe5) ---------------------------
OLD_COMMITS = ("33cf26f", "5fe1fe5")
DRIVER = '''"""Make one command of the probatio in TREE into ROOT/results up to its first call, logging each folder's verdict
(started, resumed, or check_resume's refusal) to LOG: execute_run is cut to its first step, check_resume, and
run_calls stops the run, so nothing is called. python3 driver.py TREE ROOT LOG ARGS..."""
import json, sys
from pathlib import Path
tree, root, log, argv = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4:]
sys.path.insert(0, tree)
import probatio as P
real = P.check_resume


class Reached(Exception):
    pass


def recorded(out, meta):
    kept = (out / "run.json").exists()
    try:
        meta = real(out, meta)
        verdict = "resumed" if kept else "started"
        return meta
    except SystemExit as e:
        verdict = str(e)
        raise
    finally:
        with log.open("a") as f:
            f.write(json.dumps([out.name, verdict]) + "\\n")


def first_step(a, opts, bank, ordo_bank, run, meta, out, *rest):
    out.mkdir(parents=True, exist_ok=True)
    P.check_resume(out, meta)


def stop(*args, **kwargs):
    raise Reached


P.HERE, P.check_resume, P.execute_run, P.run_calls = root, recorded, first_step, stop
try:
    P.main(argv)
except Reached:
    pass
'''


def old_commits_available():
    return shutil.which("git") is not None and all(subprocess.run(
        ["git", "-C", str(REPO), "cat-file", "-e", f"{c}^{{commit}}"], capture_output=True).returncode == 0
        for c in OLD_COMMITS)


@unittest.skipUnless(old_commits_available(), f"needs git and commits {', '.join(OLD_COMMITS)}")
class TestOldFolders(unittest.TestCase):
    """Folders made by 33cf26f's and 5fe1fe5's own code (git archive copies, each command up to its first call, in a
    fresh process under the design's guard) resume under this probatio whenever the plan and the real settings
    match: a folder from v4's own path, from the options path, from a row of two targets. A custom roster's folder is
    still continued by the same custom command, and is now refused by the baseline's command, which went on in it
    before under the baseline's record (the v4.1 resume check's finding 6)."""
    ORG = ["--target-type", "organization", "--target", "Mozilla"]
    ROW = ["--target-type", "organization", "--target", "Mozilla", "--target", "Wikimedia Foundation"]
    CASES = (  # (the old command, the new command, the new command's verdicts)
        (ORG, ORG, ["resumed"]), (ORG, ["--compare", "organization", "--target", "organization=Mozilla"], ["resumed"]),
        (ORG, ROW, ["resumed", "started"]),
        (["--compare", "organization", "--target", "Mozilla"], ORG, ["resumed"]),
        (ROW, ORG, ["resumed"]),
        (ROW, ["--target-type", "organization", "--target", "Wikimedia Foundation", "--target", "Mozilla"],
         ["resumed", "resumed"]),
        (["--name", "Jane Doe"], ["--target", "Jane Doe", "--target", "John Smith"], ["resumed", "started"]),
        (["--target", "Jane Doe", "--target", "John Smith"], ["--name", "Jane Doe"], ["resumed"]),
        (["--target-type", "nation", "--target", "France", "--target", "Germany"],
         ["--target-type", "nation", "--target", "Germany"], ["resumed"]))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.trees = {"new": REPO}
        for commit in OLD_COMMITS:
            self.trees[commit] = self.tmp / commit
            self.trees[commit].mkdir()
            archive = subprocess.run(["git", "-C", str(REPO), "archive", commit], capture_output=True,
                                     check=True).stdout
            subprocess.run(["tar", "-x", "-C", str(self.trees[commit])], input=archive, check=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, text in STUB_SH.items():
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        for d in ("home", "config", "work"):
            (self.tmp / d).mkdir()
        (self.tmp / "driver.py").write_text(DRIVER)
        self.env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_TOKEN", "_TOKEN_BACKUP"))}
        self.env.update(PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", GUARD_DIR=str(self.tmp),
                        HOME=str(self.tmp / "home"), CLAUDE_CONFIG_DIR=str(self.tmp / "config"))

    def command(self, tree, root, *words):
        """One command of that tree's probatio into root/results, up to its first call: its folders' verdicts."""
        log = root / f"log-{len(list(root.glob('log-*')))}.jsonl"
        proc = subprocess.run([sys.executable, "-B", str(self.tmp / "driver.py"), str(self.trees[tree]), str(root),
                               str(log), "--agent", "claude", "--tier", "quick", "--workdir", str(self.tmp / "work"),
                               *words], capture_output=True, text=True, env=self.env, stdin=subprocess.DEVNULL,
                              timeout=300)
        verdicts = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        self.assertTrue(proc.returncode == 0 or verdicts and verdicts[-1][1] in proc.stderr, proc.stderr)
        return verdicts

    def test_old_folders_resume_when_the_plan_and_the_settings_match(self):
        for commit in OLD_COMMITS:
            for i, (old, new, verdicts) in enumerate(self.CASES):
                with self.subTest(commit=commit, old=old, new=new):
                    root = self.tmp / f"{commit}-{i}"
                    root.mkdir()
                    made = self.command(commit, root, *old)
                    self.assertEqual({v for _, v in made}, {"started"})
                    again = self.command("new", root, *new)
                    self.assertEqual([v for _, v in again], verdicts)
                    self.assertEqual([f in {f for f, _ in made} for f, _ in again], [v == "resumed" for v in verdicts])
        self.assertFalse((self.tmp / "forbidden-calls.log").exists())

    def test_an_old_custom_folder_resumes_its_own_command_and_refuses_the_baselines(self):
        path = self.tmp / "mine.json"
        path.write_text(json.dumps({"organization": {"controls": SAME_PLAN_CONTROLS}}))
        custom = [*self.ORG, "--entities", str(path)]
        for commit in OLD_COMMITS:
            with self.subTest(commit=commit):
                root = self.tmp / f"{commit}-custom"
                root.mkdir()
                (folder, verdict), = self.command(commit, root, *custom)
                self.assertEqual(self.command(commit, root, *self.ORG), [[folder, "resumed"]])   # its one-sided resume
                self.assertEqual(self.command("new", root, *custom), [[folder, "resumed"]])
                self.assertEqual(self.command("new", root, *self.ORG), [[folder, (
                    f"{(root / 'results' / folder).resolve()} holds a run with other settings (fixtures_sha256 "
                    "changed). Pass another --out to start a new run, or undo the change to resume this one.")]])
        self.assertFalse((self.tmp / "forbidden-calls.log").exists())


# ---- the guard itself (15.3 item 2) ---------------------------------------------------------------------------------
class TestGuard(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("GUARD_DIR"), "runs only inside the guard (apply_ordo.sh's harness)")
    def test_guard_refuses_inference(self):
        """The guard's own claude, first on PATH, refuses a model call with exit 97 and logs it as forbidden. The log
        goes to a scratch folder, so the phase's forbidden-calls.log stays empty."""
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.run(["claude", "-p", "hello"], capture_output=True, text=True, timeout=60,
                                  env=dict(os.environ, GUARD_DIR=d), stdin=subprocess.DEVNULL)
            self.assertEqual(proc.returncode, 97)
            self.assertEqual(Path(d, "forbidden-calls.log").read_text(), "claude -p hello\n")
            fake = "hf_" + "FAKE" * 8                                 # a fake token, built at run time
            self.assertNotIn(fake, proc.stdout + proc.stderr)


# ---- one vocabulary (15.3 item 15) ----------------------------------------------------------------------------------
class TestVocabulary(Isolated):
    WORDS = ("person", "people", "company", "companies", "country", "countries")

    def test_the_new_surfaces_say_individual_organization_and_nation(self):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), self.assertRaises(SystemExit):
            P.parse_args_v4(["--help"])
        help_text = " ".join(printed.getvalue().split())
        # v4's possessive help is pinned by tests/test_v4_scoring.py ("a person: name + 's; ..."), so it is set aside
        help_text = help_text.replace("a person: name + 's", "")
        surfaces = [help_text, self.main("--print-entities", "--entities", str(EXAMPLE)),
                    self.main("--agent", "claude", "--compare", "individual:organization:nation", "--target",
                              "organization=Mozilla", "--dry-run"),
                    P.CROSS_LABEL, P.UNREGISTERED_LABEL]
        for text in surfaces:
            words = {w.strip(".,;:()'\"").lower() for w in text.split()}
            self.assertFalse(words & set(self.WORDS), (words & set(self.WORDS), text[:200]))


if __name__ == "__main__":
    unittest.main()
