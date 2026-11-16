"""versus.plan and versus.count_plan: the calls of a run for two and three names at both tiers and every arm, on the
complete synthetic bank (the registered counts) and on the shipped, registered bank; the balance of every factor within
every conflict scenario; ids that carry the arm, frame, family, scenario, pair and factors, and twins that pair them
across arms; stable fingerprints; the errands with the names as partners (and a two-name run's fixed third partner);
the bounds on the run's own entities; and the runner's own pieces taking the calls unchanged. No network, no model.

    python3 -m unittest tests.test_versus_plan
"""
import json
import sys
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import live  # noqa: E402
import ordo  # noqa: E402
import probatio as P  # noqa: E402
import versus as VS  # noqa: E402
from test_versus_fixture import full_bank  # noqa: E402

TWO = {"individual": "Jane Doe", "organization": "Acme Corp"}
THREE = dict(TWO, nation="Norland")
OB = ordo.load_ordo_bank()
# questions per arm on the complete bank: PREREG.md 2's pairwise counts, the triads, two label orders of four ties for
# each named type (ruling 3: 16 for two names, 24 for three), the 32 errands per named type, and the bounds: ordo's
# red-flag rows for the named types (46 for two, 66 for three) and V12 twice for each name (ruling 7)
EXPECTED = {
    ("standard", 2): {"allocation": 96, "conflict": 384, "tie": 16, "errand": 64, "bound": 54},
    ("quick", 2): {"allocation": 32, "conflict": 128, "tie": 16, "errand": 64, "bound": 54},
    ("standard", 3): {"allocation": 288, "conflict": 1152, "triad": 72, "tie": 24, "errand": 96, "bound": 78},
    ("quick", 3): {"allocation": 96, "conflict": 384, "triad": 72, "tie": 24, "errand": 96, "bound": 78},
}
ARMS = ({"memory": "on"}, {"memory": "off"}, {"memory": "both"}, {"memory": "on", "strangers": True},
        {"memory": "both", "strangers": True})
ARM_NAMES = {"on": ["on"], "off": ["off"], "both": ["off", "on"]}


def plan(names=THREE, tier="standard", arms=None, bank=None, recipe=None):
    return VS.plan(bank or full_bank(), names, tier, arms or {"memory": "on"}, OB, recipe=recipe)


class Counts(unittest.TestCase):
    def test_every_tier_name_count_and_arm(self):
        bank = full_bank()
        for (tier, n), want in EXPECTED.items():
            names = THREE if n == 3 else TWO
            for arms in ARMS:
                c = VS.count_plan(plan(names, tier, arms, bank, recipe=VS.RECIPES[tier]).calls)   # @1's rows
                rosters = ["names", "unfamiliar"] if arms.get("strangers") else ["names"]
                labels = sorted(f"{r}:{m}" for r in rosters for m in ARM_NAMES[arms["memory"]])
                self.assertEqual(sorted(c["by_arm"]), labels, (tier, n, arms))
                for label in labels:
                    self.assertEqual(c["by_arm"][label], dict(want, total=sum(want.values())), (tier, n, label))
                self.assertEqual(c["episodes"], want["errand"] * len(labels))
                self.assertEqual(c["questions"] + c["episodes"], c["calls"])
                self.assertTrue(all(v > 0 for v in c["minutes"].values()))
                self.assertIn("an estimate from ordo's rate", c["minutes_basis"])          # ruling 6

    def test_two_names_one_pair_and_no_triad(self):
        calls = plan(TWO).calls
        self.assertEqual({c["meta"]["pair"] for c in calls if c["meta"]["frame"] in ("allocation", "conflict")},
                         {"individual:organization"})
        self.assertFalse([c for c in calls if c["meta"]["frame"] == "triad"])
        self.assertEqual({c["meta"]["listing"][0] for c in calls if c["meta"]["frame"] == "tie"},
                         {"individual", "organization"})

    def test_the_shipped_bank(self):
        """The registered bank/versus/ plans the preregistration's counts (PREREG.md 2): for three names, standard
        1,440 pairwise and 72 triad replies, 24 ties, 96 episodes; quick 480 pairwise and 72 triad."""
        bank = VS.load_bank()
        self.assertFalse(bank.development)
        for tier, pairwise in (("standard", 1440), ("quick", 480)):
            got = VS.count_plan(VS.plan(bank, THREE, tier, {"memory": "on"}, OB).calls)["by_arm"]["names:on"]
            self.assertEqual((got["allocation"] + got["conflict"], got["triad"], got["tie"], got["errand"]),
                             (pairwise, 72, 24, 96), tier)

    def test_an_unknown_tier_or_arm_is_refused(self):
        for tier, arms in (("full", {}), ("smoke", {}), ("standard", {"memory": "sometimes"}),
                           ("standard", {"strangers": "yes"}), ("standard", {"roster": "names"})):
            with self.assertRaises(VS.VersusError):
                plan(THREE, tier, arms)


class Balance(unittest.TestCase):
    def test_every_factor_balanced_within_every_conflict_scenario(self):
        groups = defaultdict(list)
        for c in plan().calls:
            m = c["meta"]
            if m["frame"] == "conflict":
                groups[(m["scenario"], m["pair"])].append(m["design"]["est"])
        self.assertEqual(len(groups), 48 * 3)
        for key, runs in groups.items():
            self.assertEqual(len(runs), 8, key)
            self.assertEqual(len({(d["T"], d["L"], d["N"]) for d in runs}), 8, key)
            for f in "TLNW":
                self.assertEqual(sum(d[f] for d in runs), 0, (key, f))        # four at each level
                for g in "TLNW":
                    if f < g:
                        self.assertEqual(sum(d[f] * d[g] for d in runs), 0, (key, f, g))   # orthogonal
            self.assertTrue(all(d["W"] == d["T"] * d["L"] * d["N"] for d in runs))

    def test_allocation_both_listings_and_triad_six(self):
        alloc, tri = Counter(), Counter()
        for c in plan().calls:
            m = c["meta"]
            if m["frame"] == "allocation":
                alloc[(m["scenario"], m["pair"])] += 1
            if m["frame"] == "triad":
                tri[m["scenario"]] += 1
        self.assertEqual(set(alloc.values()), {2})
        self.assertEqual(set(tri.values()), {6})


class Ids(unittest.TestCase):
    def test_ids_carry_the_arm_frame_family_scenario_pair_and_factors(self):
        calls = plan(THREE, arms={"memory": "both", "strangers": True}).calls
        keys = [P.key(c) for c in calls]
        self.assertEqual(len(keys), len(set(keys)))
        c = next(c for c in calls if c["meta"]["frame"] == "conflict" and c["meta"]["roster"] == "unfamiliar")
        m = c["meta"]
        self.assertTrue(c["id"].startswith("versus|unfamiliar|conflict." + m["scenario_family"] + "." + m["scenario"]
                                           + "." + m["pair"] + ".T"))
        self.assertEqual(c["id"], "versus|unfamiliar|" + m["template_id"])
        self.assertEqual(c["row"]["id"], c["id"])

    def test_twins_pair_each_item_across_arms(self):
        calls = plan(THREE, arms={"memory": "both", "strangers": True}).calls
        by = defaultdict(set)
        for c in calls:
            by[c["meta"]["twin"]].add((c["meta"]["roster"], c["condition"]))
        self.assertEqual({frozenset(v) for v in by.values()},
                         {frozenset((r, m) for r in ("names", "unfamiliar") for m in ("with", "without"))})
        # a twin's calls sit side by side
        order = [c["meta"]["twin"] for c in calls]
        runs = [t for i, t in enumerate(order) if i == 0 or order[i - 1] != t]
        self.assertEqual(len(runs), len(by))

    def test_the_unfamiliar_arm_has_the_unfamiliar_names_in_versus_forms(self):
        calls = plan(THREE, arms={"memory": "on", "strangers": True}).calls
        p = next(c["prompt"] for c in calls if c["meta"]["roster"] == "unfamiliar" and c["meta"]["frame"] == "triad")
        for name in ("Karim Nassar", "Thirlbeck Systems", "the government of Arvenia"):
            self.assertIn(name, p)
        for word in ("Jane Doe", "Acme Corp", "Norland", "delegation", "team"):
            self.assertNotIn(word, p)


class Fingerprints(unittest.TestCase):
    def test_stable_and_sensitive(self):
        a, b = plan(TWO), plan(TWO)
        self.assertEqual(a.identity["plan_fingerprint"], b.identity["plan_fingerprint"])
        self.assertEqual(a.identity["plan_fingerprint"], P.plan_fingerprint(a.calls))
        self.assertEqual([P.key(c) for c in a.calls], [P.key(c) for c in b.calls])       # the order too
        other = plan({"individual": "Jane Roe", "organization": "Acme Corp"})
        self.assertNotEqual(a.identity["plan_fingerprint"], other.identity["plan_fingerprint"])
        self.assertNotEqual(a.identity["plan_fingerprint"], plan(TWO, "quick").identity["plan_fingerprint"])
        self.assertEqual(a.identity["versus_fingerprint"], b.identity["versus_fingerprint"])
        self.assertEqual(a.identity["versus_fingerprint"], VS.identity_hash(a.calls))
        self.assertNotEqual(a.identity["versus_fingerprint"], other.identity["versus_fingerprint"])

    def test_identity(self):
        ident = plan(TWO, "quick", {"memory": "both", "strangers": True}).identity
        self.assertEqual((ident["recipe"], ident["tier"]), ("versus-quick@2", "quick"))     # new runs plan @2
        self.assertEqual(ident["arms"], {"memory": "both", "strangers": True})
        self.assertEqual(ident["partner"]["rendered"], "the government of Arvenia")
        self.assertEqual(ident["unfamiliar"]["organization"]["rendered"], "Thirlbeck Systems")
        self.assertEqual(ident["participants"]["organization"]["party"], "Acme Corp, an organization")
        self.assertEqual((ident["forms"], ident["estimator"]), (VS.FORMS_VERSION, VS.E.VERSION))
        self.assertEqual(ident["scorer"], VS.SCORER_VERSION)
        self.assertEqual(ident["fixed_text"], full_bank().manifest["fixed_text"]["version"])
        self.assertEqual(ident["bank"]["sha256"], full_bank().sha256)
        self.assertTrue(VS.RECIPES["standard"] == "versus-standard@1")


class Errands(unittest.TestCase):
    def test_the_registered_tasks_with_the_names_as_partners(self):
        calls = [c for c in plan(TWO).calls if c["meta"]["frame"] == "errand"]
        seeds = sorted({c["meta"]["seed"] for c in calls})
        self.assertEqual(seeds, sorted(list(range(625, 649)) + list(range(301, 309))))
        self.assertEqual({c["meta"]["side"] for c in calls}, {"individual", "organization"})
        c = calls[0]
        self.assertTrue(c["episode"] and c["world"] and c["followup"])
        world = json.loads(c["world"])
        self.assertEqual(world["end"]["finish_tool"], c["row"]["finish_tool"])
        self.assertIn("the government of Arvenia", c["prompt"])        # the fixed third partner
        self.assertIn("Jane Doe", c["prompt"])
        self.assertIn("the joint secretariat", c["prompt"])
        self.assertEqual(c["meta"]["deployer"], VS.SECRETARIAT)
        self.assertEqual(c["meta"]["axis"], "ve-%02d" % (VS.versus_axes.ERRAND_SEEDS.index(c["meta"]["seed"]) + 1))

    def test_a_name_that_is_the_fixed_partner_is_refused_in_plain_words(self):
        """Ruling 10: refused, with no fallback entity, whichever type the name is given for."""
        for names in ({"individual": "Arvenia", "organization": "Acme Corp"},
                      {"individual": "Jane Doe", "organization": "arvenia"}):
            with self.assertRaises(VS.VersusError) as cm:
                plan(names)
            self.assertIn("is this two-name run's fixed third partner, the government of Arvenia, which every errand "
                          "world of the run includes; give another name, or all three. versus has no fallback "
                          "entity.", str(cm.exception))
        plan({"individual": "Arvenia", "organization": "Acme Corp", "nation": "Norland"})   # three names: no partner

    def test_a_name_that_is_its_unfamiliar_entity_is_refused_with_strangers(self):
        with self.assertRaises(VS.VersusError) as cm:
            plan({"individual": "Karim Nassar", "organization": "Acme Corp", "nation": "Norland"},
                 arms={"strangers": True})
        self.assertIn("'Karim Nassar' is the unfamiliar roster's individual, Karim Nassar, so the strangers arm would "
                      "compare it with itself; run without strangers or give another name. versus has no fallback "
                      "entity.", str(cm.exception))
        plan({"individual": "Karim Nassar", "organization": "Acme Corp", "nation": "Norland"})   # alone it is fine
        plan({"individual": "Jane Doe", "organization": "Karim Nassar", "nation": "Norland"},
             arms={"strangers": True})                          # another type's unfamiliar entity is not a collision

    def test_a_name_that_is_the_secretariat_is_refused(self):
        for name in ("the joint secretariat", "Joint Secretariat", "Secretariat"):
            with self.assertRaises(VS.VersusError) as cm:
                plan({"individual": "Jane Doe", "organization": name})
            self.assertIn("is the joint secretariat, the deployer of every errand world", str(cm.exception))

    def test_the_ordo_bank_must_be_the_one_the_bank_was_built_against(self):
        other = type(OB)(**dict(vars(OB), sha256="0" * 64))
        with self.assertRaises(VS.VersusError):
            VS.plan(full_bank(), TWO, "standard", {}, other)


class Bounds(unittest.TestCase):
    def test_ordos_red_flag_rows_and_v12_on_the_runs_own_entities(self):
        calls = [c for c in plan(TWO).calls if c["meta"]["frame"] == "bound"]
        self.assertEqual({c["meta"]["component"] for c in calls}, {"RF1", "RF2", "RF4", "V12"})
        v12 = [c for c in calls if c["meta"]["component"] == "V12"]
        self.assertEqual(sorted(c["meta"]["full"] for c in v12), ["Acme Corp"] * 2 + ["Jane Doe"] * 2)
        self.assertEqual({c["meta"]["type"] for c in calls}, {"individual", "organization"})
        prompts = "\n".join(c["prompt"] for c in calls)
        self.assertIn("Jane Doe", prompts)
        self.assertIn("Acme Corp", prompts)
        self.assertNotIn("the research team at Acme Corp", prompts)
        same = ordo.plan_checks(OB, {"individual": [VS.entity_forms("individual", "Jane Doe")],
                                     "organization": [VS.entity_forms("organization", "Acme Corp")]},
                                ["individual", "organization"], "standard")
        self.assertEqual(sorted(c["prompt"] for c in calls), sorted(i["prompt"] for i in same))

    def test_the_strangers_arm_asks_v12_of_the_unfamiliar_entities(self):
        calls = [c for c in plan(TWO, arms={"strangers": True}).calls
                 if c["meta"]["frame"] == "bound" and c["meta"]["component"] == "V12"]
        self.assertEqual(sorted({(c["meta"]["roster"], c["meta"]["full"]) for c in calls}),
                         [("names", "Acme Corp"), ("names", "Jane Doe"), ("unfamiliar", "Karim Nassar"),
                          ("unfamiliar", "Thirlbeck Systems")])


class Runner(unittest.TestCase):
    """The calls are in the shape probatio's runner takes: key, the live line's pricing, resume and the lock."""

    def test_every_call_is_priced_and_keyed(self):
        calls = plan(THREE, arms={"memory": "both"}).calls
        price = P.pricing()
        for profile in ("sonnet", "opus", "codex"):
            self.assertTrue(all(P.expected_seconds(c, profile, price) > 0 for c in calls))
        with open("/dev/null", "w") as sink:
            line = live.LiveLine(2, lambda c: P.expected_seconds(c, "sonnet", price), stream=sink, tty=False)
            line.begin(calls, {})
            line.close()
        for c in calls:
            self.assertEqual(P.key(c), (c["condition"], c["dataset"], c["id"]))
            self.assertEqual(P.is_episode(c), c["meta"]["frame"] in ("errand", "class3"))   # class3: U and O

    def test_the_lock(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "run"
            out.mkdir()
            lock = P.claim(out)                         # held while the file stays open, as a run holds it
            self.addCleanup(lock.close)
            with self.assertRaises(SystemExit):
                P.claim(out)


if __name__ == "__main__":
    unittest.main()
